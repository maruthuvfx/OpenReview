"""
Annotation Manager Dialog for OpenReview.
Provides studio-grade inspection, review notes management, keyframe navigation,
and comprehensive export / import of frame-keyed vector annotations and dailies reports.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional, List, Callable
import numpy as np

from PySide6.QtCore import Qt, Signal, QThread, QEventLoop, QObject
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QDialog,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QPushButton,
    QLabel,
    QTextEdit,
    QGroupBox,
    QFileDialog,
    QMessageBox,
    QSplitter,
    QComboBox,
    QLineEdit,
    QGridLayout,
    QSizePolicy,
    QProgressDialog,
    QApplication,
)

from openreview.models.annotations import AnnotationStore, frame_to_timecode
from openreview.core.clock import PlaybackClock
from openreview.core.exporter import MediaExporter


class AnnotationExportWorker(QThread):
    """
    Background worker thread for exporting PDF review sheets, rendered frame sequences,
    and burned-in PNG images without freezing the main application UI.
    """
    progress_updated = Signal(int, int, str)

    def __init__(
        self,
        export_mode: str,  # "pdf", "sequence", "burned_in"
        target_path: Path,
        frames: List[int],
        store: AnnotationStore,
        fps: float,
        render_frame_callback: Optional[Callable[[int], np.ndarray]],
        export_png_frames: bool = False,
        seq_start_frame: int = 1001,
        seq_end_frame: int = 1100,
        source_path: str = "",
        export_image_frames: Optional[bool] = None,
        image_format: Optional[str] = None,
        parent: Optional[QObject] = None,
    ):
        super().__init__(parent)
        self.export_mode = export_mode
        self.target_path = target_path
        self.frames = frames
        self.store = store
        self.fps = fps
        self.render_frame_callback = render_frame_callback
        self.export_png_frames = export_png_frames if export_image_frames is None else export_image_frames
        self.export_image_frames = self.export_png_frames
        if image_format is not None:
            self.image_format = image_format.lower().lstrip(".")
        elif export_png_frames and export_image_frames is None:
            self.image_format = "png"
        else:
            self.image_format = "jpg"
        self.seq_start_frame = seq_start_frame
        self.seq_end_frame = seq_end_frame
        self.source_path = source_path
        self._is_cancelled = False
        self.result_success = False
        self.result_title = ""
        self.result_message = ""

    def cancel(self) -> None:
        self._is_cancelled = True

    def run(self) -> None:
        try:
            if self.export_mode == "pdf":
                self._run_pdf_export()
            elif self.export_mode == "sequence":
                self._run_sequence_export()
            elif self.export_mode == "burned_in":
                self._run_burned_in_export()
        except Exception as e:
            self.result_success = False
            self.result_title = "Export Error"
            self.result_message = f"Failed to export annotations:\n{e}"

    def _run_pdf_export(self) -> None:
        p = self.target_path
        if not p.suffix:
            p = p.with_suffix(".pdf")

        from openreview.core.preferences import PreferencesManager
        prefs = PreferencesManager.instance().prefs
        fmt = (self.image_format or getattr(prefs, "default_export_image_format", "jpg")).lower()
        downscale_gt_fhd = getattr(prefs, "export_downscale_greater_than_fhd", True)
        jpg_quality = getattr(prefs, "export_jpg_quality", 85)
        png_mode = getattr(prefs, "export_png_mode", "png8")
        png_dither = getattr(prefs, "export_png_dither", True)
        png_comp = getattr(prefs, "export_png_compression", 4)

        entries = []
        render_steps = len(self.frames)
        total_steps = render_steps * 2 + 1
        img_dir = None
        saved_img_count = 0
        if self.export_image_frames:
            img_dir = p.parent / f"{p.stem}_frames"
            img_dir.mkdir(parents=True, exist_ok=True)

        for idx, f in enumerate(self.frames):
            if self._is_cancelled:
                return
            self.progress_updated.emit(idx, total_steps, f"Rendering frame {f} ({idx + 1} of {len(self.frames)})...")

            img = self.render_frame_callback(f) if self.render_frame_callback else None
            if isinstance(img, (tuple, list)):
                img = next((x for x in img if isinstance(x, np.ndarray)), None)

            if self.export_image_frames and img_dir and img is not None:
                save_img = img
                if downscale_gt_fhd and (save_img.shape[1] > 1920 or save_img.shape[0] > 1080):
                    half_w = max(1, save_img.shape[1] // 2)
                    half_h = max(1, save_img.shape[0] // 2)
                    save_img = MediaExporter.resize_frame(save_img, half_w, half_h)

                frame_file = img_dir / f"frame_{f:04d}.{fmt}"
                MediaExporter.save_image_file(
                    save_img,
                    frame_file,
                    quality=jpg_quality,
                    png_mode=png_mode,
                    dither=png_dither,
                    compression=str(png_comp),
                )
                saved_img_count += 1

            note = self.store.get_frame_note(f)
            tc = frame_to_timecode(f, self.fps)
            st_cnt = self.store.get_stroke_count(f)
            entries.append({
                "frame": f,
                "timecode": tc,
                "note": note,
                "strokes_count": st_cnt,
                "image": img,
                "source_path": self.source_path,
            })

        if self._is_cancelled:
            return

        def _on_pdf_progress(cur: int, tot: int, msg: str):
            if self._is_cancelled:
                return
            overall = min(render_steps + cur, total_steps)
            self.progress_updated.emit(overall, total_steps, msg)

        pdf_path = MediaExporter.export_pdf_review_sheet(
            output_pdf_path=p,
            entries=entries,
            fps=self.fps,
            progress_callback=_on_pdf_progress,
        )
        self.progress_updated.emit(total_steps, total_steps, "Done")

        exported_img_msg = ""
        if self.export_image_frames and img_dir:
            exported_img_msg = f"\n\nAlso exported {saved_img_count} annotated {fmt.upper()} frames to:\n{img_dir}"

        self.result_success = True
        self.result_title = "Export Successful"
        self.result_message = f"Exported structured PDF review sheet to:\n{pdf_path}{exported_img_msg}"

    def _run_sequence_export(self) -> None:
        if not self.render_frame_callback:
            self.result_success = False
            self.result_title = "Renderer Unavailable"
            self.result_message = "Direct frame render callback not available."
            return

        from openreview.core.preferences import PreferencesManager
        prefs = PreferencesManager.instance().prefs
        downscale_gt_fhd = getattr(prefs, "export_downscale_greater_than_fhd", True)
        jpg_quality = getattr(prefs, "export_jpg_quality", 85)
        png_mode = getattr(prefs, "export_png_mode", "png8")
        png_dither = getattr(prefs, "export_png_dither", True)
        png_comp = getattr(prefs, "export_png_compression", 4)

        p = self.target_path
        out_dir = p.parent / (p.stem + "_sequence")
        out_dir.mkdir(parents=True, exist_ok=True)
        seq_frames = list(range(self.seq_start_frame, self.seq_end_frame + 1))
        total = len(seq_frames)
        count = 0
        ext = p.suffix.lstrip(".").lower() or self.image_format or "png"

        for idx, f in enumerate(seq_frames):
            if self._is_cancelled:
                return
            self.progress_updated.emit(idx, total, f"Rendering frame {f} ({idx + 1} of {total})...")
            img = self.render_frame_callback(f)
            if isinstance(img, (tuple, list)):
                img = next((x for x in img if isinstance(x, np.ndarray)), None)

            if img is not None:
                if downscale_gt_fhd and (img.shape[1] > 1920 or img.shape[0] > 1080):
                    img = MediaExporter.resize_frame(img, max(1, img.shape[1] // 2), max(1, img.shape[0] // 2))
                out_file = out_dir / f"{p.stem}.{f:04d}.{ext}"
                MediaExporter.save_image_file(
                    img,
                    out_file,
                    quality=jpg_quality,
                    png_mode=png_mode,
                    dither=png_dither,
                    compression=str(png_comp),
                )
                count += 1

        self.progress_updated.emit(total, total, "Done")
        self.result_success = True
        self.result_title = "Export Successful"
        self.result_message = f"Exported {count} frames of annotated sequence to:\n{out_dir}"

    def _run_burned_in_export(self) -> None:
        if not self.render_frame_callback:
            self.result_success = False
            self.result_title = "Renderer Unavailable"
            self.result_message = "Direct viewport render callback not available."
            return

        from openreview.core.preferences import PreferencesManager
        prefs = PreferencesManager.instance().prefs
        downscale_gt_fhd = getattr(prefs, "export_downscale_greater_than_fhd", True)
        jpg_quality = getattr(prefs, "export_jpg_quality", 85)
        png_mode = getattr(prefs, "export_png_mode", "png8")
        png_dither = getattr(prefs, "export_png_dither", True)
        png_comp = getattr(prefs, "export_png_compression", 4)

        p = self.target_path
        ext = p.suffix.lstrip(".").lower() or self.image_format or "png"

        if len(self.frames) == 1:
            f = self.frames[0]
            self.progress_updated.emit(0, 1, f"Rendering frame {f}...")
            img = self.render_frame_callback(f)
            if isinstance(img, (tuple, list)):
                img = next((x for x in img if isinstance(x, np.ndarray)), None)
            if img is not None and downscale_gt_fhd and (img.shape[1] > 1920 or img.shape[0] > 1080):
                img = MediaExporter.resize_frame(img, max(1, img.shape[1] // 2), max(1, img.shape[0] // 2))

            out_file = p if p.suffix else p.with_suffix(f".{ext}")
            MediaExporter.save_image_file(
                img,
                out_file,
                quality=jpg_quality,
                png_mode=png_mode,
                dither=png_dither,
                compression=str(png_comp),
            )
            self.progress_updated.emit(1, 1, "Done")
            self.result_success = True
            self.result_title = "Export Successful"
            self.result_message = f"Exported annotated frame {f} to:\n{out_file}"
        else:
            out_dir = p.parent / (p.stem + "_frames")
            out_dir.mkdir(parents=True, exist_ok=True)
            total = len(self.frames)
            count = 0
            for idx, f in enumerate(self.frames):
                if self._is_cancelled:
                    return
                self.progress_updated.emit(idx, total, f"Rendering frame {f} ({idx + 1} of {total})...")
                img = self.render_frame_callback(f)
                if isinstance(img, (tuple, list)):
                    img = next((x for x in img if isinstance(x, np.ndarray)), None)
                if img is not None and downscale_gt_fhd and (img.shape[1] > 1920 or img.shape[0] > 1080):
                    img = MediaExporter.resize_frame(img, max(1, img.shape[1] // 2), max(1, img.shape[0] // 2))
                out_file = out_dir / f"annotated.{f:04d}.{ext}"
                MediaExporter.save_image_file(
                    img,
                    out_file,
                    quality=jpg_quality,
                    png_mode=png_mode,
                    dither=png_dither,
                    compression=str(png_comp),
                )
                count += 1

            self.progress_updated.emit(total, total, "Done")
            self.result_success = True
            self.result_title = "Export Successful"
            self.result_message = f"Exported {count} burned-in frames to:\n{out_dir}"


class AnnotationManagerWidget(QWidget):
    """
    Studio-grade Annotation & Markup Manager Widget.
    Lists all annotated keyframes, provides live frame jumping, per-frame notes,
    and multi-format export/import.
    """

    frame_jump_requested = Signal(int)
    annotations_cleared = Signal()

    def __init__(
        self,
        annotation_store: AnnotationStore,
        clock: PlaybackClock,
        render_frame_callback: Optional[Callable[[int], np.ndarray]] = None,
        parent: Optional[QWidget] = None,
        source_path: str = "",
    ):
        if isinstance(parent, str) and not source_path:
            source_path, parent = parent, None
        super().__init__(parent)
        self.setMinimumWidth(260)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        self.store = annotation_store
        self.clock = clock
        self.render_frame_callback = render_frame_callback
        self.source_path = str(source_path) if isinstance(source_path, (str, Path)) else ""
        self._updating_table = False

        self._setup_ui()
        self._connect_signals()
        self.refresh_table()

    def _setup_ui(self) -> None:
        self.setStyleSheet("""
            QDialog {
                background-color: #1c1c1c;
                color: #e0e0e0;
            }
            QGroupBox {
                border: 1px solid #333333;
                margin-top: 6px;
                padding-top: 10px;
                color: #e5a823;
                font-weight: bold;
            }
            QLabel {
                color: #cccccc;
            }
            QTableWidget {
                background-color: #141414;
                border: 1px solid #2e2e2e;
                gridline-color: #262626;
                color: #e0e0e0;
                selection-background-color: #e5a823;
                selection-color: #121212;
            }
            QTableWidget::item {
                padding: 4px;
            }
            QHeaderView::section {
                background-color: #242424;
                color: #b0b0b0;
                padding: 4px;
                border: 1px solid #2e2e2e;
                font-weight: bold;
            }
            QTextEdit {
                background-color: #121212;
                border: 1px solid #333333;
                color: #ffffff;
                padding: 6px;
                font-family: Consolas, monospace;
                font-size: 12px;
            }
            QTextEdit:focus {
                border: 1px solid #e5a823;
            }
            QPushButton {
                background-color: #2a2a2a;
                border: 1px solid #3d3d3d;
                color: #ffffff;
                padding: 6px 12px;
                border-radius: 3px;
                font-weight: 500;
            }
            QPushButton:hover {
                background-color: #383838;
                border-color: #e5a823;
            }
            QPushButton:pressed {
                background-color: #e5a823;
                color: #121212;
            }
            QPushButton#danger_btn:hover {
                border-color: #e74c3c;
                color: #ff6b6b;
            }
            QComboBox {
                background-color: #141414;
                border: 1px solid #333333;
                color: #ffffff;
                padding: 4px;
            }
        """)

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(12, 12, 12, 12)
        main_layout.setSpacing(10)

        # Header Info Banner
        header_layout = QHBoxLayout()
        title_box = QVBoxLayout()
        self.lbl_title = QLabel("Annotation & Markup Manager", self)
        font = QFont()
        font.setPointSize(13)
        font.setBold(True)
        self.lbl_title.setFont(font)
        self.lbl_title.setStyleSheet("color: #e5a823;")

        self.lbl_summary = QLabel("Loading annotated frames...", self)
        self.lbl_summary.setStyleSheet("color: #888888; font-size: 11px;")
        title_box.addWidget(self.lbl_title)
        title_box.addWidget(self.lbl_summary)
        header_layout.addLayout(title_box)
        header_layout.addStretch()

        from openreview.resources import get_vfx_icon

        self.btn_refresh = QPushButton("Refresh", self)
        self.btn_refresh.setIcon(get_vfx_icon("refresh"))
        self.btn_refresh.clicked.connect(self.refresh_table)
        header_layout.addWidget(self.btn_refresh)
        main_layout.addLayout(header_layout)

        # Splitter: Table on top/left, Frame Notes & Details on bottom/right
        splitter = QSplitter(Qt.Orientation.Vertical, self)

        # Top: Table of Keyframes
        self.table = QTableWidget(self)
        self.table.setColumnCount(5)
        self.table.setHorizontalHeaderLabels(["Frame", "Timecode", "Strokes", "Breakdown", "Review Note"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.itemSelectionChanged.connect(self._on_table_selection_changed)
        self.table.itemDoubleClicked.connect(self._on_table_double_clicked)
        splitter.addWidget(self.table)

        # Bottom: Note Editor & Details
        bottom_widget = QWidget(self)
        bottom_layout = QVBoxLayout(bottom_widget)
        bottom_layout.setContentsMargins(0, 6, 0, 0)
        bottom_layout.setSpacing(6)

        note_header = QHBoxLayout()
        self.lbl_selected_frame = QLabel("Selected Frame: None", bottom_widget)
        self.lbl_selected_frame.setStyleSheet("font-weight: bold; color: #e5a823;")
        note_header.addWidget(self.lbl_selected_frame)
        note_header.addStretch()

        self.btn_save_note = QPushButton("Update Note", bottom_widget)
        self.btn_save_note.setIcon(get_vfx_icon("save"))
        self.btn_save_note.clicked.connect(self._on_save_current_note)
        note_header.addWidget(self.btn_save_note)
        bottom_layout.addLayout(note_header)

        self.edit_note = QTextEdit(bottom_widget)
        self.edit_note.setPlaceholderText("Type director review notes or dailies feedback for the selected frame here...")
        self.edit_note.textChanged.connect(self._on_note_text_changed)
        bottom_layout.addWidget(self.edit_note)

        # Dedicated Text Annotations on Canvas Management Section
        text_grp = QGroupBox("Text Annotations (On Canvas)", bottom_widget)
        text_layout = QVBoxLayout(text_grp)
        text_layout.setContentsMargins(6, 6, 6, 6)
        text_layout.setSpacing(4)

        t_row1 = QHBoxLayout()
        self.combo_text_annots = QComboBox(text_grp)
        self.combo_text_annots.addItem("No text annotations on frame", -1)
        self.combo_text_annots.currentIndexChanged.connect(self._on_text_annot_selected)
        t_row1.addWidget(self.combo_text_annots, 1)

        self.btn_delete_text_annot = QPushButton("Delete", text_grp)
        self.btn_delete_text_annot.setObjectName("danger_btn")
        self.btn_delete_text_annot.setToolTip("Delete selected text annotation from canvas")
        self.btn_delete_text_annot.setEnabled(False)
        self.btn_delete_text_annot.clicked.connect(self._on_delete_text_annot_clicked)
        t_row1.addWidget(self.btn_delete_text_annot)
        text_layout.addLayout(t_row1)

        t_row2 = QHBoxLayout()
        self.edit_text_annot = QLineEdit(text_grp)
        self.edit_text_annot.setPlaceholderText("Edit annotation text...")
        self.edit_text_annot.setEnabled(False)
        self.edit_text_annot.returnPressed.connect(self._on_update_text_annot_clicked)
        t_row2.addWidget(self.edit_text_annot, 1)

        self.btn_update_text_annot = QPushButton("Update", text_grp)
        self.btn_update_text_annot.setToolTip("Update text on canvas for this annotation")
        self.btn_update_text_annot.setEnabled(False)
        self.btn_update_text_annot.clicked.connect(self._on_update_text_annot_clicked)
        t_row2.addWidget(self.btn_update_text_annot)
        text_layout.addLayout(t_row2)

        bottom_layout.addWidget(text_grp)

        splitter.addWidget(bottom_widget)
        splitter.setSizes([320, 220])
        main_layout.addWidget(splitter)

        # Bottom Action Bar (Clean 2-Row Responsive Grid)
        btn_grid = QGridLayout()
        btn_grid.setSpacing(6)
        btn_grid.setContentsMargins(0, 4, 0, 0)

        self.btn_jump = QPushButton("Jump to Frame", self)
        self.btn_jump.setToolTip("Seek timeline to selected annotated frame")
        self.btn_jump.clicked.connect(self._on_jump_clicked)
        btn_grid.addWidget(self.btn_jump, 0, 0)

        self.btn_clear_frame = QPushButton("Clear Frame", self)
        self.btn_clear_frame.setIcon(get_vfx_icon("delete"))
        self.btn_clear_frame.setObjectName("danger_btn")
        self.btn_clear_frame.setToolTip("Clear annotations on the selected frame")
        self.btn_clear_frame.clicked.connect(self._on_clear_selected_frame)
        btn_grid.addWidget(self.btn_clear_frame, 0, 1)

        self.btn_clear_all = QPushButton("Clear All", self)
        self.btn_clear_all.setIcon(get_vfx_icon("delete"))
        self.btn_clear_all.setObjectName("danger_btn")
        self.btn_clear_all.setToolTip("Purge all annotations across the entire clip")
        self.btn_clear_all.clicked.connect(self._on_clear_all_clicked)
        btn_grid.addWidget(self.btn_clear_all, 0, 2)

        self.btn_export = QPushButton("Export...", self)
        self.btn_export.setIcon(get_vfx_icon("export"))
        self.btn_export.setToolTip("Export annotations to JSON, Report, or Burned-in Images")
        self.btn_export.clicked.connect(self._on_export_clicked)
        btn_grid.addWidget(self.btn_export, 1, 0)

        self.btn_import = QPushButton("Import...", self)
        self.btn_import.setIcon(get_vfx_icon("import"))
        self.btn_import.setToolTip("Import vector annotations from JSON")
        self.btn_import.clicked.connect(self._on_import_clicked)
        btn_grid.addWidget(self.btn_import, 1, 1)

        self.btn_close = QPushButton("Close", self)
        self.btn_close.clicked.connect(self._on_close_clicked)
        btn_grid.addWidget(self.btn_close, 1, 2)

        main_layout.addLayout(btn_grid)

    def _on_close_clicked(self) -> None:
        p = self.parent()
        if p and hasattr(p, "close_panel"):
            p.close_panel()
        elif p and hasattr(p, "accept"):
            p.accept()
        else:
            self.close()

    def _connect_signals(self) -> None:
        self.store.annotations_changed.connect(self._on_store_changed)
        self.clock.frame_changed.connect(self._on_clock_frame_changed)

    def _on_store_changed(self, frame: int) -> None:
        if not self._updating_table:
            self.refresh_table()

    def _on_clock_frame_changed(self, frame: int) -> None:
        # Highlight current frame in table if present
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            if item and item.text() == str(frame):
                self.table.selectRow(row)
                break

    def refresh_table(self) -> None:
        """Populate keyframe table with all annotated frames."""
        self._updating_table = True
        try:
            frames = self.store.get_annotated_frames()
            self.table.setRowCount(len(frames))

            total_strokes = 0
            for row, f in enumerate(frames):
                st_count = self.store.get_stroke_count(f)
                total_strokes += st_count
                shapes = self.store.get_shape_counts(f)
                shapes_str = ", ".join(f"{k.capitalize()}: {v}" for k, v in shapes.items()) if shapes else "Note only"
                note = self.store.get_frame_note(f)
                tc = frame_to_timecode(f, self.clock.fps)

                # Col 0: Frame
                item_frame = QTableWidgetItem(str(f))
                item_frame.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.table.setItem(row, 0, item_frame)

                # Col 1: Timecode
                item_tc = QTableWidgetItem(tc)
                item_tc.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.table.setItem(row, 1, item_tc)

                # Col 2: Strokes
                item_cnt = QTableWidgetItem(str(st_count))
                item_cnt.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.table.setItem(row, 2, item_cnt)

                # Col 3: Breakdown
                item_shapes = QTableWidgetItem(shapes_str)
                self.table.setItem(row, 3, item_shapes)

                # Col 4: Review Note
                item_note = QTableWidgetItem(note)
                self.table.setItem(row, 4, item_note)

            self.lbl_summary.setText(
                f"{len(frames)} annotated keyframe(s) | {total_strokes} total stroke(s) | FPS: {self.clock.fps:.2f}"
            )

            # Re-select row if matching clock
            current_f = self.clock.current_frame
            selected = False
            for row in range(self.table.rowCount()):
                if self.table.item(row, 0).text() == str(current_f):
                    self.table.selectRow(row)
                    selected = True
                    break
            if not selected and self.table.rowCount() > 0:
                self.table.selectRow(0)
            elif self.table.rowCount() == 0:
                self.lbl_selected_frame.setText("Selected Frame: None")
                self.edit_note.clear()
        finally:
            self._updating_table = False

    def get_selected_frame(self) -> Optional[int]:
        selected = self.table.selectedItems()
        if not selected:
            return None
        row = selected[0].row()
        item = self.table.item(row, 0)
        return int(item.text()) if item else None

    def _on_table_selection_changed(self) -> None:
        frame = self.get_selected_frame()
        if frame is not None:
            self.lbl_selected_frame.setText(f"Selected Frame: {frame} (Timecode: {frame_to_timecode(frame, self.clock.fps)})")
            note = self.store.get_frame_note(frame)
            self.edit_note.blockSignals(True)
            self.edit_note.setPlainText(note)
            self.edit_note.blockSignals(False)
            self._refresh_text_annotations(frame)
        else:
            self.lbl_selected_frame.setText("Selected Frame: None")
            self.edit_note.blockSignals(True)
            self.edit_note.clear()
            self.edit_note.blockSignals(False)
            self._refresh_text_annotations(None)

    def _refresh_text_annotations(self, frame: Optional[int] = None) -> None:
        """Update the text annotations dropdown and editing input for the selected frame."""
        if frame is None:
            frame = self.get_selected_frame()
        self.combo_text_annots.blockSignals(True)
        self.combo_text_annots.clear()
        if frame is None:
            self.combo_text_annots.addItem("No frame selected", -1)
            self.combo_text_annots.setEnabled(False)
            self.edit_text_annot.clear()
            self.edit_text_annot.setEnabled(False)
            self.btn_update_text_annot.setEnabled(False)
            self.btn_delete_text_annot.setEnabled(False)
            self.combo_text_annots.blockSignals(False)
            return

        text_strokes = self.store.get_text_strokes(frame)
        if not text_strokes:
            self.combo_text_annots.addItem("No text annotations on frame", -1)
            self.combo_text_annots.setEnabled(False)
            self.edit_text_annot.clear()
            self.edit_text_annot.setEnabled(False)
            self.btn_update_text_annot.setEnabled(False)
            self.btn_delete_text_annot.setEnabled(False)
            self.combo_text_annots.blockSignals(False)
            return

        for num, (orig_idx, stroke) in enumerate(text_strokes, start=1):
            snippet = stroke.text.replace("\n", " ").strip()
            if len(snippet) > 28:
                snippet = snippet[:25] + "..."
            label = f"#{num}: \"{snippet}\""
            self.combo_text_annots.addItem(label, orig_idx)

        self.combo_text_annots.setEnabled(True)
        self.edit_text_annot.setEnabled(True)
        self.btn_update_text_annot.setEnabled(True)
        self.btn_delete_text_annot.setEnabled(True)
        self.combo_text_annots.blockSignals(False)

        # Set text input from currently selected combo item
        self._on_text_annot_selected(self.combo_text_annots.currentIndex())

    def _on_text_annot_selected(self, idx: int) -> None:
        frame = self.get_selected_frame()
        orig_idx = self.combo_text_annots.currentData()
        if frame is None or orig_idx is None or orig_idx < 0:
            self.edit_text_annot.clear()
            self.edit_text_annot.setEnabled(False)
            self.btn_update_text_annot.setEnabled(False)
            self.btn_delete_text_annot.setEnabled(False)
            return

        strokes = self.store.get_strokes(frame)
        if 0 <= orig_idx < len(strokes):
            self.edit_text_annot.setEnabled(True)
            self.btn_update_text_annot.setEnabled(True)
            self.btn_delete_text_annot.setEnabled(True)
            self.edit_text_annot.setText(strokes[orig_idx].text)

    def _on_update_text_annot_clicked(self) -> None:
        frame = self.get_selected_frame()
        orig_idx = self.combo_text_annots.currentData()
        if frame is None or orig_idx is None or orig_idx < 0:
            return
        new_text = self.edit_text_annot.text().strip()
        if new_text:
            self.store.update_stroke_text(frame, orig_idx, new_text)
        else:
            self.store.remove_stroke(frame, orig_idx)
        self._refresh_text_annotations(frame)

    def _on_delete_text_annot_clicked(self) -> None:
        frame = self.get_selected_frame()
        orig_idx = self.combo_text_annots.currentData()
        if frame is None or orig_idx is None or orig_idx < 0:
            return
        self.store.remove_stroke(frame, orig_idx)
        self._refresh_text_annotations(frame)

    def _on_table_double_clicked(self, item: QTableWidgetItem) -> None:
        self._on_jump_clicked()

    def _on_jump_clicked(self) -> None:
        frame = self.get_selected_frame()
        if frame is not None:
            self.clock.set_frame(frame)
            self.frame_jump_requested.emit(frame)

    def _on_note_text_changed(self) -> None:
        frame = self.get_selected_frame()
        if frame is not None and not self._updating_table:
            text = self.edit_note.toPlainText()
            self.store.set_frame_note(frame, text)
            # Update row in table directly without full refresh
            selected = self.table.selectedItems()
            if selected:
                row = selected[0].row()
                self.table.item(row, 4).setText(text)

    def _on_save_current_note(self) -> None:
        frame = self.get_selected_frame()
        if frame is not None:
            text = self.edit_note.toPlainText()
            self.store.set_frame_note(frame, text)
            self.refresh_table()

    def _on_clear_selected_frame(self) -> None:
        frame = self.get_selected_frame()
        if frame is None:
            return
        reply = QMessageBox.question(
            self,
            "Clear Frame Annotations",
            f"Are you sure you want to clear annotations on frame {frame}?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self.store.clear_frame(frame, clear_note=True)
            self.refresh_table()

    def _on_clear_all_clicked(self) -> None:
        reply = QMessageBox.question(
            self,
            "Clear All Annotations",
            "Are you sure you want to permanently clear all annotations and review notes across all frames?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self.store.clear_all()
            self.annotations_cleared.emit()
            self.refresh_table()

    def _on_export_clicked(self) -> None:
        """Handle comprehensive export (JPEG/PNG frames default, structured PDF report, sequences, JSON, Markdown, CSV)."""
        frames = self.store.get_annotated_frames()
        if not frames:
            QMessageBox.information(self, "No Annotations", "There are no annotations or notes to export.")
            return

        from openreview.core.preferences import PreferencesManager
        prefs = PreferencesManager.instance().prefs
        default_fmt = getattr(prefs, "default_export_image_format", "jpg").lower()
        fmt_upper = default_fmt.upper()

        file_types = (
            f"Burned-in {fmt_upper} Frames (*.{default_fmt});;"
            "PDF Review Sheet (*.pdf);;"
            f"Annotated Image Sequence (*.{default_fmt});;"
            "Burned-in JPEG Frames (*.jpg);;"
            "Burned-in PNG Frames (*.png);;"
            "JSON Vector Annotations (*.json);;"
            "Markdown Review Report (*.md);;"
            "CSV Review Notes (*.csv)"
        )
        path, selected_filter = QFileDialog.getSaveFileName(
            self,
            "Export Annotations & Review Notes",
            f"annotated_frame.{default_fmt}",
            file_types,
        )
        if not path:
            return

        try:
            p = Path(path)
            if "PDF" in selected_filter or p.suffix.lower() == ".pdf":
                if not p.suffix:
                    p = p.with_suffix(".pdf")
                reply = QMessageBox.question(
                    self,
                    f"Export {fmt_upper} Frames?",
                    f"Do you also want to export the individual annotated frames as {fmt_upper} images?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.Yes,
                )
                export_images = (reply == QMessageBox.StandardButton.Yes)
                active_src = self.source_path
                if not active_src:
                    win = self.window()
                    if hasattr(win, "_current_media_path"):
                        active_src = str(getattr(win, "_current_media_path", ""))

                worker = AnnotationExportWorker(
                    export_mode="pdf",
                    target_path=p,
                    frames=frames,
                    store=self.store,
                    fps=self.clock.fps,
                    render_frame_callback=self.render_frame_callback,
                    export_png_frames=export_images,
                    export_image_frames=export_images,
                    image_format=default_fmt,
                    source_path=active_src,
                    parent=self,
                )
                self._run_export_worker(worker, "Exporting PDF Review Sheet", "Rendering annotated frames...", len(frames) + 1)

            elif "Sequence" in selected_filter:
                if not self.render_frame_callback:
                    QMessageBox.warning(self, "Renderer Unavailable", "Direct frame render callback not available.")
                    return
                seq_frames_count = max(1, self.clock.end_frame - self.clock.start_frame + 1)
                worker = AnnotationExportWorker(
                    export_mode="sequence",
                    target_path=p,
                    frames=frames,
                    store=self.store,
                    fps=self.clock.fps,
                    render_frame_callback=self.render_frame_callback,
                    image_format=p.suffix.lstrip(".").lower() or default_fmt,
                    seq_start_frame=self.clock.start_frame,
                    seq_end_frame=self.clock.end_frame,
                    parent=self,
                )
                self._run_export_worker(worker, "Exporting Image Sequence", "Rendering sequence frames...", seq_frames_count)

            elif "Burned-in" in selected_filter or p.suffix.lower() in (".png", ".jpg", ".jpeg"):
                if not self.render_frame_callback:
                    QMessageBox.warning(self, "Renderer Unavailable", "Direct viewport render callback not available.")
                    return
                worker = AnnotationExportWorker(
                    export_mode="burned_in",
                    target_path=p,
                    frames=frames,
                    store=self.store,
                    fps=self.clock.fps,
                    render_frame_callback=self.render_frame_callback,
                    image_format=p.suffix.lstrip(".").lower() or default_fmt,
                    parent=self,
                )
                target_fmt_str = p.suffix.lstrip(".").upper() or fmt_upper
                self._run_export_worker(worker, f"Exporting {target_fmt_str} Frames", "Rendering frames...", len(frames))

            elif "JSON" in selected_filter or p.suffix.lower() == ".json":
                self.store.export_to_json(p)
                QMessageBox.information(self, "Export Successful", f"Exported vector annotations to:\n{p}")
            elif "Markdown" in selected_filter or p.suffix.lower() == ".md":
                md_content = self.store.generate_report_markdown(self.clock.fps)
                p.write_text(md_content, encoding="utf-8")
                QMessageBox.information(self, "Export Successful", f"Exported Markdown review report to:\n{p}")
            elif "CSV" in selected_filter or p.suffix.lower() == ".csv":
                csv_content = self.store.generate_report_csv(self.clock.fps)
                p.write_text(csv_content, encoding="utf-8")
                QMessageBox.information(self, "Export Successful", f"Exported CSV review notes to:\n{p}")
        except Exception as e:
            QMessageBox.critical(self, "Export Error", f"Failed to export annotations:\n{e}")

    def _run_export_worker(
        self, worker: AnnotationExportWorker, title: str, initial_msg: str, total_steps: int
    ) -> None:
        """Run an export worker asynchronously while pumping Qt events smoothly."""
        progress = QProgressDialog(initial_msg, "Cancel", 0, total_steps, self)
        progress.setWindowTitle(title)
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(0)
        progress.setValue(0)

        def on_progress(cur: int, tot: int, msg: str):
            if progress.maximum() != tot:
                progress.setMaximum(tot)
            progress.setValue(cur)
            progress.setLabelText(msg)

        user_cancelled = False
        def on_user_canceled():
            nonlocal user_cancelled
            user_cancelled = True
            worker.cancel()

        worker.progress_updated.connect(on_progress)
        progress.canceled.connect(on_user_canceled)

        loop = QEventLoop()
        worker.finished.connect(loop.quit)
        worker.start()
        loop.exec()

        # Disconnect progress canceled so closing the dialog never triggers cancellation
        try:
            progress.canceled.disconnect(on_user_canceled)
        except Exception:
            pass
        progress.close()

        # Prioritize explicit errors over cancellation
        if worker.result_title and not worker.result_success:
            QMessageBox.critical(self, worker.result_title, worker.result_message)
        elif user_cancelled:
            QMessageBox.information(self, "Export Cancelled", "Export operation was cancelled.")
        elif worker.result_title:
            if worker.result_success:
                QMessageBox.information(self, worker.result_title, worker.result_message)
            else:
                QMessageBox.critical(self, worker.result_title, worker.result_message)

    def _on_import_clicked(self) -> None:
        """Handle importing vector annotations and notes from JSON."""
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Import Annotations",
            "",
            "JSON Vector Annotations (*.json);;All Files (*.*)",
        )
        if not path:
            return

        try:
            count = self.store.import_from_json(path, merge=True)
            self.refresh_table()
            QMessageBox.information(
                self, "Import Successful", f"Successfully imported annotations for {count} frame(s)."
            )
        except Exception as e:
            QMessageBox.critical(self, "Import Error", f"Failed to import annotations:\n{e}")


class AnnotationManagerDialog(QDialog):
    """Studio Annotation Manager Dialog wrapper for standalone window usage."""

    frame_jump_requested = Signal(int)
    annotations_cleared = Signal()

    def __init__(
        self,
        annotation_store: AnnotationStore,
        clock: PlaybackClock,
        render_frame_callback: Optional[Callable[[int], np.ndarray]] = None,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.setWindowTitle("Annotation Manager - OpenReview")
        self.resize(760, 540)
        self.setStyleSheet("background-color: #1c1c1c;")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        self.widget = AnnotationManagerWidget(
            annotation_store, clock, render_frame_callback, self
        )
        self.widget.frame_jump_requested.connect(self.frame_jump_requested.emit)
        self.widget.annotations_cleared.connect(self.annotations_cleared.emit)
        layout.addWidget(self.widget)

    def __getattr__(self, name):
        if name != "widget" and hasattr(self, "widget"):
            return getattr(self.widget, name)
        raise AttributeError(f"'{type(self).__name__}' object has no attribute '{name}'")

    def refresh_table(self) -> None:
        self.widget.refresh_table()


