"""
Multi-Track Editorial Timeline Widget.
Provides:
- Multi-lane Video and Audio track visualization
- Track Headers with Mute, Solo, Lock, Visibility, and Editorial Offline Reference (REF) toggles
- Clip items with shot name, version badge, colorspace, and soft effect ribbons
- Trimming handles (Head & Tail Edge Hover brackets, Ripple Trim)
- Slip Tool (Y) and Slide Tool (U) for non-linear timing auditioning
- In / Out (I / O) Range Marking with shaded ruler highlight and playback loop constraint
- Color-coded Review Markers (M) with category tags (Approved, WIP, Retake, Client Note)
- Real-time Zooming (Zoom Slider, Mouse Wheel, +/-/F shortcuts), Horizontal Scrollbar
- Drag-and-Drop media ingestion from Sources Dock or OS File Explorer
- Lift & Ripple Clip Deletion
"""
from __future__ import annotations

import logging
import os
import time
from typing import Optional, List, Dict, Tuple, Any

from PySide6.QtCore import Qt, Signal, QRectF, QPointF, QSize, QRect, QTimer
from PySide6.QtGui import (
    QPainter,
    QColor,
    QPen,
    QBrush,
    QFont,
    QMouseEvent,
    QWheelEvent,
    QPaintEvent,
    QKeyEvent,
    QDragEnterEvent,
    QDropEvent,
    QDragMoveEvent,
    QPolygonF,
    QCursor,
    QPixmap,
    QImage,
    QLinearGradient,
)
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QSplitter,
    QPushButton,
    QToolButton,
    QLabel,
    QFrame,
    QScrollBar,
    QSlider,
    QMenu,
    QDialog,
    QFormLayout,
    QLineEdit,
    QTextEdit,
    QComboBox,
    QDialogButtonBox,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QFileDialog,
    QMessageBox,
    QProgressBar,
    QSpinBox,
    QGroupBox,
    QInputDialog,
)

from openreview.models.timeline_sequence import (
    TimelineSequence,
    Track,
    ClipItem,
    TrackType,
    SoftEffectType,
    TimelineMarker,
)
from openreview.core.timecode import frame_to_timecode, timecode_to_frame
from openreview.ui.svg_icons import get_svg_icon
from openreview.core.undo_manager import (
    UndoManager,
    ClipMoveCommand,
    ClipTrimCommand,
    ClipSlipCommand,
    ClipSlideCommand,
    ClipSplitCommand,
    ClipDeleteCommand,
    ClipAddCommand,
    MarkerAddCommand,
    MarkerDeleteCommand,
    MarkerEditCommand,
)

logger = logging.getLogger(__name__)


class MarkerEditDialog(QDialog):
    """Modal dialog to view, edit, or delete an existing review marker."""

    def __init__(self, marker: TimelineMarker, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.marker = marker
        self.action = "cancel"  # "save", "delete", "cancel"
        self.setWindowTitle(f"Edit Review Marker @ Frame {marker.frame}")
        self.setMinimumWidth(380)
        self.setStyleSheet("""
            QDialog {
                background-color: #202020;
                color: #dddddd;
            }
            QLabel {
                color: #cccccc;
                font-size: 11px;
            }
            QLineEdit, QTextEdit, QComboBox, QSpinBox {
                background-color: #161616;
                color: #ffffff;
                border: 1px solid #3a3a3a;
                border-radius: 3px;
                padding: 4px;
                font-size: 11px;
            }
            QPushButton {
                background-color: #2b2b2b;
                color: #dddddd;
                border: 1px solid #3d3d3d;
                border-radius: 3px;
                padding: 5px 12px;
                font-size: 11px;
            }
            QPushButton:hover {
                background-color: #383838;
                color: #ffffff;
            }
            QPushButton#btn_delete {
                background-color: #5c1d1d;
                border-color: #7a2828;
                color: #ffcccc;
            }
            QPushButton#btn_delete:hover {
                background-color: #7a2828;
            }
            QPushButton#btn_save {
                background-color: #e5a823;
                color: #000000;
                font-weight: bold;
                border-color: #f5b833;
            }
        """)

        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.title_edit = QLineEdit(marker.name, self)
        form.addRow("Title:", self.title_edit)

        self.cat_combo = QComboBox(self)
        self.cat_combo.addItems(["Note (Yellow)", "Approved (Green)", "Retake (Red)", "Client Note (Blue)"])
        if "Approved" in marker.category or marker.color == "#2ecc71":
            self.cat_combo.setCurrentIndex(1)
        elif "Retake" in marker.category or marker.color == "#e74c3c":
            self.cat_combo.setCurrentIndex(2)
        elif "Client" in marker.category or marker.color == "#3498db":
            self.cat_combo.setCurrentIndex(3)
        else:
            self.cat_combo.setCurrentIndex(0)
        form.addRow("Category:", self.cat_combo)

        self.comment_edit = QTextEdit(self)
        self.comment_edit.setPlainText(marker.comment)
        self.comment_edit.setMaximumHeight(80)
        form.addRow("Comment:", self.comment_edit)

        layout.addLayout(form)

        btn_layout = QHBoxLayout()
        self.btn_del = QPushButton("Delete Marker", self)
        self.btn_del.setObjectName("btn_delete")
        self.btn_del.clicked.connect(self._on_delete)
        btn_layout.addWidget(self.btn_del)

        btn_layout.addStretch()

        self.btn_cancel = QPushButton("Cancel", self)
        self.btn_cancel.clicked.connect(self.reject)
        btn_layout.addWidget(self.btn_cancel)

        self.btn_save = QPushButton("Save", self)
        self.btn_save.setObjectName("btn_save")
        self.btn_save.clicked.connect(self._on_save)
        btn_layout.addWidget(self.btn_save)

        layout.addLayout(btn_layout)

    @property
    def selected_color(self) -> str:
        idx = self.cat_combo.currentIndex()
        if idx == 1:
            return "#2ecc71"
        elif idx == 2:
            return "#e74c3c"
        elif idx == 3:
            return "#3498db"
        return "#f1c40f"

    @property
    def selected_category(self) -> str:
        idx = self.cat_combo.currentIndex()
        if idx == 1:
            return "Approved"
        elif idx == 2:
            return "Retake"
        elif idx == 3:
            return "Client"
        return "Note"

    def _on_delete(self) -> None:
        self.action = "delete"
        self.accept()

    def _on_save(self) -> None:
        self.action = "save"
        self.accept()


class TimecodeLineEdit(QLineEdit):
    """
    Industry-standard NLE numeric timecode & frame number input.
    - In Timecode (TC) mode: accepts numbers, auto-populates time separators (colons)
      by shifting digits right-to-left (e.g. typing 1000000 becomes 01:00:00:00).
    - In Frame (FR) mode: accepts pure numbers (e.g. 250, +50, -10) with zero colons.
    - Pressing Enter or Escape commits/cancels and transfers focus back to the canvas.
    """
    def __init__(self, parent: Optional[QWidget] = None, canvas: Optional[QWidget] = None):
        super().__init__(parent)
        self._canvas = canvas
        self._timecode_mode: bool = True
        self._raw_digits: str = ""

    def set_canvas(self, canvas: QWidget) -> None:
        self._canvas = canvas

    def set_timecode_mode(self, is_tc: bool) -> None:
        self._timecode_mode = bool(is_tc)
        self._raw_digits = ""

    def set_formatted_timecode(self, tc_text: str) -> None:
        if not self.hasFocus():
            if self._timecode_mode:
                self._raw_digits = "".join(c for c in tc_text if c.isdigit())[-8:]
            else:
                self._raw_digits = tc_text.strip()
            self.setText(tc_text)

    def focusInEvent(self, event) -> None:
        super().focusInEvent(event)
        if self._timecode_mode:
            cur = "".join(c for c in self.text() if c.isdigit())
            self._raw_digits = cur[-8:] if cur else ""
        else:
            self._raw_digits = self.text().strip()
        self.selectAll()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        key = event.key()
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.returnPressed.emit()
            self.clearFocus()
            if self._canvas:
                self._canvas.setFocus()
            event.accept()
            return
        elif key == Qt.Key.Key_Escape:
            self.clearFocus()
            if self._canvas:
                self._canvas.setFocus()
            event.accept()
            return

        # Frame (FR) mode: direct numeric entry without colons
        if not self._timecode_mode:
            if key == Qt.Key.Key_Backspace:
                super().keyPressEvent(event)
                return
            text = event.text()
            if text and (text.isdigit() or text in ("+", "-")):
                super().keyPressEvent(event)
                return
            event.ignore()
            return

        # Timecode (TC) mode: auto-colon formatting
        if key == Qt.Key.Key_Backspace:
            if self.hasSelectedText():
                self._raw_digits = ""
            elif self._raw_digits:
                self._raw_digits = self._raw_digits[:-1]
            padded = self._raw_digits.zfill(8)
            self.setText(f"{padded[0:2]}:{padded[2:4]}:{padded[4:6]}:{padded[6:8]}")
            event.accept()
            return

        text = event.text()
        if text and text.isdigit():
            if self.hasSelectedText():
                self._raw_digits = ""
            self._raw_digits = (self._raw_digits + text)[-8:]
            padded = self._raw_digits.zfill(8)
            self.setText(f"{padded[0:2]}:{padded[2:4]}:{padded[4:6]}:{padded[6:8]}")
            event.accept()
            return
        elif text in ("+", "-"):
            super().keyPressEvent(event)
            return

        event.ignore()


def file_has_audio(path: str) -> bool:
    """Check if media file contains an audio stream."""
    if not path or not os.path.exists(path):
        return False
    _, ext = os.path.splitext(path.lower())
    if ext in {".wav", ".mp3", ".aac", ".flac", ".ogg", ".aif", ".aiff", ".m4a"}:
        return True
    if ext in {".mp4", ".mov", ".mkv", ".avi", ".m4v", ".webm"}:
        try:
            import av
            with av.open(path) as container:
                return len(container.streams.audio) > 0
        except Exception:
            try:
                import soundfile as sf
                sf.info(path)
                return True
            except Exception:
                return False
    return False


class BatchRenameDialog(QDialog):
    """
    Dialog to batch rename selected clips with auto-incrementation and live preview table.
    Supports tokens:
      {###} -> 3-digit padded number (010, 020)
      {##}  -> 2-digit padded number (01, 02)
      {#}   -> unpadded number (1, 2)
      {name}-> original clip name
      {track}-> parent track name
    """

    def __init__(self, clips_with_tracks: List[Tuple[Track, ClipItem]], parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.clips_with_tracks = clips_with_tracks
        self.renamed_map: Dict[str, str] = {}
        self.setWindowTitle(f"Batch Rename Clips ({len(clips_with_tracks)} items)")
        self.setMinimumSize(620, 460)
        self.setStyleSheet("""
            QDialog {
                background-color: #202020;
                color: #e0e0e0;
            }
            QLabel {
                color: #cccccc;
                font-size: 11px;
            }
            QLineEdit, QSpinBox {
                background-color: #161616;
                color: #ffffff;
                border: 1px solid #3a3a3a;
                border-radius: 3px;
                padding: 4px;
                font-size: 11px;
            }
            QLineEdit:focus, QSpinBox:focus {
                border-color: #e5a823;
            }
            QTableWidget {
                background-color: #171717;
                border: 1px solid #333333;
                color: #dddddd;
                gridline-color: #262626;
                font-size: 11px;
            }
            QHeaderView::section {
                background-color: #252525;
                color: #e5a823;
                border: 1px solid #333333;
                font-weight: bold;
                padding: 4px;
            }
            QPushButton {
                background-color: #2b2b2b;
                color: #dddddd;
                border: 1px solid #3d3d3d;
                border-radius: 3px;
                padding: 5px 12px;
                font-size: 11px;
            }
            QPushButton:hover {
                background-color: #383838;
                color: #ffffff;
            }
            QPushButton#btn_apply {
                background-color: #e5a823;
                color: #000000;
                font-weight: bold;
                border-color: #f5b833;
            }
        """)

        layout = QVBoxLayout(self)

        grp = QGroupBox("Pattern & Increment Settings", self)
        grp.setStyleSheet("QGroupBox { font-weight: bold; color: #e5a823; border: 1px solid #333333; margin-top: 6px; padding-top: 10px; }")
        grp_layout = QFormLayout(grp)

        self.pattern_edit = QLineEdit("SH_{###}", grp)
        self.pattern_edit.setToolTip("Tokens: {###}=010, {##}=01, {#}=1, {name}=Original Name, {track}=Track Name")
        grp_layout.addRow("Rename Pattern:", self.pattern_edit)

        inc_layout = QHBoxLayout()
        lbl_start = QLabel("Start Index:", grp)
        self.spin_start = QSpinBox(grp)
        self.spin_start.setRange(0, 99999)
        self.spin_start.setValue(10)

        lbl_step = QLabel("Increment Step:", grp)
        self.spin_step = QSpinBox(grp)
        self.spin_step.setRange(1, 1000)
        self.spin_step.setValue(10)

        inc_layout.addWidget(lbl_start)
        inc_layout.addWidget(self.spin_start)
        inc_layout.addSpacing(15)
        inc_layout.addWidget(lbl_step)
        inc_layout.addWidget(self.spin_step)
        inc_layout.addStretch()
        grp_layout.addRow("Numbering:", inc_layout)

        sr_layout = QHBoxLayout()
        self.find_edit = QLineEdit(grp)
        self.find_edit.setPlaceholderText("Find text...")
        self.replace_edit = QLineEdit(grp)
        self.replace_edit.setPlaceholderText("Replace with...")
        sr_layout.addWidget(QLabel("Find:"))
        sr_layout.addWidget(self.find_edit)
        sr_layout.addWidget(QLabel("Replace:"))
        sr_layout.addWidget(self.replace_edit)
        grp_layout.addRow("Search / Replace:", sr_layout)

        layout.addWidget(grp)

        lbl_preview = QLabel("Live Preview:", self)
        lbl_preview.setStyleSheet("font-weight: bold; color: #cccccc; margin-top: 6px;")
        layout.addWidget(lbl_preview)

        self.table = QTableWidget(len(clips_with_tracks), 3, self)
        self.table.setHorizontalHeaderLabels(["Track", "Original Name", "New Name Preview"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().setVisible(False)
        layout.addWidget(self.table)

        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        self.btn_cancel = QPushButton("Cancel", self)
        self.btn_cancel.clicked.connect(self.reject)
        btn_layout.addWidget(self.btn_cancel)

        self.btn_apply = QPushButton("Apply Batch Rename", self)
        self.btn_apply.setObjectName("btn_apply")
        self.btn_apply.clicked.connect(self._on_apply)
        btn_layout.addWidget(self.btn_apply)

        layout.addLayout(btn_layout)

        self.pattern_edit.textChanged.connect(self._update_preview)
        self.spin_start.valueChanged.connect(self._update_preview)
        self.spin_step.valueChanged.connect(self._update_preview)
        self.find_edit.textChanged.connect(self._update_preview)
        self.replace_edit.textChanged.connect(self._update_preview)

        self._update_preview()

    def _generate_name(self, index: int, original_name: str, track_name: str) -> str:
        pattern = self.pattern_edit.text()
        start = self.spin_start.value()
        step = self.spin_step.value()
        val = start + index * step

        res = pattern
        res = res.replace("{####}", f"{val:04d}")
        res = res.replace("{###}", f"{val:03d}")
        res = res.replace("{##}", f"{val:02d}")
        res = res.replace("{#}", str(val))
        res = res.replace("{name}", original_name)
        res = res.replace("{track}", track_name)

        find_str = self.find_edit.text()
        if find_str:
            res = res.replace(find_str, self.replace_edit.text())

        return res

    def _update_preview(self) -> None:
        self.renamed_map.clear()
        for idx, (track, clip) in enumerate(self.clips_with_tracks):
            new_name = self._generate_name(idx, clip.name, track.name)
            self.renamed_map[clip.id] = new_name

            item_trk = QTableWidgetItem(track.name)
            item_orig = QTableWidgetItem(clip.name)
            item_new = QTableWidgetItem(new_name)
            item_new.setForeground(QBrush(QColor("#2ecc71")))

            self.table.setItem(idx, 0, item_trk)
            self.table.setItem(idx, 1, item_orig)
            self.table.setItem(idx, 2, item_new)

    def _on_apply(self) -> None:
        for track, clip in self.clips_with_tracks:
            if clip.id in self.renamed_map:
                clip.name = self.renamed_map[clip.id]
        self.accept()


class ClipExportDialog(QDialog):
    """
    Modal dialog to export selected clips individually or as editorial turnover.
    Supports:
    - Editorial Turnover: OpenTimelineIO (.otio), CMX 3600 EDL (.edl), CSV Shot List (.csv)
    - Media Render / Extraction: Individual MP4, MOV, or EXR/PNG sequences
    """

    def __init__(
        self,
        clips_with_tracks: List[Tuple[Track, ClipItem]],
        sequence: TimelineSequence,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.clips_with_tracks = clips_with_tracks
        self.sequence = sequence
        self.setWindowTitle(f"Export Clips ({len(clips_with_tracks)} items)")
        self.setMinimumSize(640, 520)
        self.setStyleSheet("""
            QDialog {
                background-color: #202020;
                color: #e0e0e0;
            }
            QLabel {
                color: #cccccc;
                font-size: 11px;
            }
            QLineEdit, QComboBox {
                background-color: #161616;
                color: #ffffff;
                border: 1px solid #3a3a3a;
                border-radius: 3px;
                padding: 4px;
                font-size: 11px;
            }
            QLineEdit:focus, QComboBox:focus {
                border-color: #e5a823;
            }
            QGroupBox {
                font-weight: bold;
                color: #e5a823;
                border: 1px solid #333333;
                margin-top: 6px;
                padding-top: 10px;
            }
            QPushButton {
                background-color: #2b2b2b;
                color: #dddddd;
                border: 1px solid #3d3d3d;
                border-radius: 3px;
                padding: 5px 12px;
                font-size: 11px;
            }
            QPushButton:hover {
                background-color: #383838;
                color: #ffffff;
            }
            QPushButton#btn_export {
                background-color: #38d9c6;
                color: #000000;
                font-weight: bold;
                border-color: #4fe0d0;
            }
            QProgressBar {
                background-color: #181818;
                border: 1px solid #333333;
                border-radius: 3px;
                text-align: center;
                color: #ffffff;
                font-size: 10px;
            }
            QProgressBar::chunk {
                background-color: #38d9c6;
            }
        """)

        layout = QVBoxLayout(self)

        grp_opt = QGroupBox("Export Settings", self)
        f_layout = QFormLayout(grp_opt)

        self.combo_format = QComboBox(grp_opt)
        self.combo_format.addItems([
            "Editorial Turnover: CSV Shot List (.csv)",
            "Editorial Turnover: OpenTimelineIO (.otio)",
            "Editorial Turnover: CMX 3600 EDL (.edl)",
            "Media Render: Individual MP4 (H.264)",
            "Media Render: Individual MOV (Apple ProRes)",
            "Media Render: Individual EXR Sequence (.exr)",
        ])
        f_layout.addRow("Export Format:", self.combo_format)

        dir_layout = QHBoxLayout()
        self.edit_dir = QLineEdit(grp_opt)
        self.edit_dir.setPlaceholderText("Select output directory...")
        self.edit_dir.setText(os.path.expanduser("~/Documents"))
        self.btn_browse = QPushButton("Browse...", grp_opt)
        self.btn_browse.clicked.connect(self._on_browse_dir)
        dir_layout.addWidget(self.edit_dir)
        dir_layout.addWidget(self.btn_browse)
        f_layout.addRow("Destination:", dir_layout)

        layout.addWidget(grp_opt)

        # Interactive Folder Structure & Name Template Builder
        from openreview.ui.export_template_builder import ExportTemplateBuilderWidget, resolve_export_pattern
        sample_clip = clips_with_tracks[0] if clips_with_tracks else None
        self.template_builder = ExportTemplateBuilderWidget(
            sequence=self.sequence,
            sample_clip=sample_clip,
            parent=self,
        )
        layout.addWidget(self.template_builder)

        self.prog_bar = QProgressBar(self)
        self.prog_bar.setRange(0, 100)
        self.prog_bar.setValue(0)
        self.prog_bar.setVisible(False)
        layout.addWidget(self.prog_bar)

        self.lbl_status = QLabel(f"Ready to export {len(clips_with_tracks)} clip(s).", self)
        self.lbl_status.setStyleSheet("color: #888888; font-size: 10.5px;")
        layout.addWidget(self.lbl_status)

        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        self.btn_cancel = QPushButton("Cancel", self)
        self.btn_cancel.clicked.connect(self.reject)
        btn_layout.addWidget(self.btn_cancel)

        self.btn_export = QPushButton("Export", self)
        self.btn_export.setObjectName("btn_export")
        self.btn_export.clicked.connect(self._on_start_export)
        btn_layout.addWidget(self.btn_export)

        layout.addLayout(btn_layout)

    def _on_browse_dir(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Select Output Directory", self.edit_dir.text())
        if d:
            self.edit_dir.setText(d)

    def _on_start_export(self) -> None:
        out_dir = self.edit_dir.text().strip()
        if not out_dir or not os.path.isdir(out_dir):
            QMessageBox.warning(self, "Invalid Directory", "Please choose a valid destination directory.")
            return

        fmt = self.combo_format.currentText()
        pattern = self.template_builder.get_pattern() or "{name}"

        try:
            from openreview.core.preferences import PreferencesManager
            p_mgr = PreferencesManager.instance()
            p_mgr.prefs.last_export_template = pattern
            p_mgr.save()
        except Exception:
            pass

        self.prog_bar.setVisible(True)
        self.prog_bar.setValue(10)
        self.btn_export.setEnabled(False)

        try:
            if "CSV" in fmt:
                self._export_csv(out_dir)
            elif "OpenTimelineIO" in fmt:
                self._export_otio(out_dir)
            elif "EDL" in fmt:
                self._export_edl(out_dir)
            else:
                self._export_media(out_dir, fmt, pattern)

            self.prog_bar.setValue(100)
            self.lbl_status.setText("Export completed successfully!")
            QMessageBox.information(self, "Export Complete", f"Successfully exported clips to:\n{out_dir}")
            self.accept()
        except Exception as e:
            logger.exception("Export failed: %s", e)
            self.lbl_status.setText(f"Error: {e}")
            QMessageBox.critical(self, "Export Failed", f"Export failed:\n{e}")
            self.btn_export.setEnabled(True)

    def _export_csv(self, out_dir: str) -> None:
        import csv
        out_path = os.path.join(out_dir, f"{self.sequence.name}_cut_list.csv")
        os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
        fps = self.sequence.fps
        with open(out_path, mode="w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow([
                "Event", "Clip Name", "Track", "Source Path",
                "Src In", "Src Out", "Dst In", "Dst Out", "Duration",
                "TC In", "TC Out", "Colorspace", "Version"
            ])
            for idx, (track, clip) in enumerate(self.clips_with_tracks, start=1):
                writer.writerow([
                    idx,
                    clip.name,
                    track.name,
                    clip.file_path,
                    clip.src_in,
                    clip.src_out,
                    clip.dst_in,
                    clip.dst_out,
                    clip.dst_duration,
                    frame_to_timecode(clip.dst_in, fps),
                    frame_to_timecode(clip.dst_out, fps),
                    clip.colorspace,
                    clip.version,
                ])

    def _export_otio(self, out_dir: str) -> None:
        import json
        out_path = os.path.join(out_dir, f"{self.sequence.name}_turnover.otio")
        os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
        sub_seq = TimelineSequence(
            id=f"{self.sequence.id}_turnover",
            name=f"{self.sequence.name} Turnover",
            fps=self.sequence.fps,
            width=self.sequence.width,
            height=self.sequence.height,
            start_timecode=self.sequence.start_timecode,
            in_frame=self.sequence.in_frame,
            out_frame=self.sequence.out_frame,
        )
        tracks_by_id: Dict[str, Track] = {}
        for track, clip in self.clips_with_tracks:
            if track.id not in tracks_by_id:
                new_t = Track(id=track.id, name=track.name, track_type=track.track_type)
                tracks_by_id[track.id] = new_t
                sub_seq.tracks.append(new_t)
            tracks_by_id[track.id].add_clip(clip)

        otio_data = sub_seq.to_otio_dict()
        with open(out_path, mode="w", encoding="utf-8") as f:
            json.dump(otio_data, f, indent=2)

    def _export_edl(self, out_dir: str) -> None:
        out_path = os.path.join(out_dir, f"{self.sequence.name}_turnover.edl")
        os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
        fps = self.sequence.fps
        lines = [
            f"TITLE: {self.sequence.name.upper()}",
            "FCM: NON-DROP FRAME",
            "",
        ]
        for idx, (track, clip) in enumerate(self.clips_with_tracks, start=1):
            reel = (clip.reel or "AX")[:8].ljust(8)
            track_code = "V " if track.track_type == TrackType.VIDEO else "A "
            trans = "C        "
            src_in_tc = frame_to_timecode(clip.src_in, fps)
            src_out_tc = frame_to_timecode(clip.src_out, fps)
            dst_in_tc = frame_to_timecode(clip.dst_in, fps)
            dst_out_tc = frame_to_timecode(clip.dst_out, fps)
            event_line = f"{idx:03d}  {reel} {track_code} {trans} {src_in_tc} {src_out_tc} {dst_in_tc} {dst_out_tc}"
            lines.append(event_line)
            lines.append(f"* FROM CLIP NAME: {clip.name}")
            if clip.file_path:
                lines.append(f"* SOURCE FILE: {clip.file_path}")
            lines.append("")

        with open(out_path, mode="w", encoding="utf-8") as f:
            f.write("\n".join(lines))

    def _export_media(self, out_dir: str, fmt: str, pattern: str) -> None:
        import json
        from openreview.ui.export_template_builder import resolve_export_pattern
        is_video = "MP4" in fmt or "MOV" in fmt
        ext = "mp4" if "MP4" in fmt else ("mov" if "MOV" in fmt else "exr")
        total = len(self.clips_with_tracks)

        for idx, (track, clip) in enumerate(self.clips_with_tracks, start=1):
            rel_path = resolve_export_pattern(pattern, clip, track, self.sequence, index=idx, ext=ext)
            clip_file = os.path.normpath(os.path.join(out_dir, rel_path))
            os.makedirs(os.path.dirname(os.path.abspath(clip_file)), exist_ok=True)

            meta_file = os.path.splitext(clip_file)[0] + "_manifest.json"
            manifest = {
                "clip_name": clip.name,
                "track": track.name,
                "source_media": clip.file_path,
                "src_in": clip.src_in,
                "src_out": clip.src_out,
                "dst_in": clip.dst_in,
                "dst_out": clip.dst_out,
                "duration_frames": clip.dst_duration,
                "fps": self.sequence.fps,
                "colorspace": clip.colorspace,
                "version": clip.version,
                "export_target": clip_file,
            }
            with open(meta_file, "w", encoding="utf-8") as mf:
                json.dump(manifest, mf, indent=2)

            if clip.file_path and os.path.exists(clip.file_path):
                import shutil
                import subprocess
                ffmpeg_bin = shutil.which("ffmpeg")
                if ffmpeg_bin and is_video:
                    fps = self.sequence.fps
                    start_sec = max(0.0, (clip.src_in - 1) / fps)
                    dur_sec = max(0.04, clip.src_duration / fps)
                    cmd = [
                        ffmpeg_bin, "-y",
                        "-ss", f"{start_sec:.4f}",
                        "-i", clip.file_path,
                        "-t", f"{dur_sec:.4f}",
                        "-c:v", "libx264" if ext == "mp4" else "prores_ks",
                        "-c:a", "aac",
                        clip_file
                    ]
                    try:
                        subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
                    except Exception as err:
                        logger.warning("ffmpeg export failed: %s; wrote manifest", err)
                elif not os.path.exists(clip_file):
                    with open(clip_file, "wb") as cf:
                        cf.write(b"")

            self.prog_bar.setValue(int(10 + (idx + 1) / total * 85))


class TrackHeaderWidget(QFrame):
    """Left-hand header for an editorial track with visibility, lock, solo, mute, and REF controls."""
    track_changed = Signal(str)

    def __init__(self, track: Track, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.track = track
        self.setFixedHeight(34)
        self.setStyleSheet("""
            QFrame {
                background-color: #1a1a1a;
                border-bottom: 1px solid #282828;
                border-right: 2px solid #222222;
            }
            QLabel {
                color: #e5a823;
                font-weight: bold;
                font-size: 11px;
            }
            QToolButton {
                background-color: #252525;
                color: #888888;
                border: 1px solid #333333;
                border-radius: 2px;
                font-size: 10px;
                font-weight: bold;
                min-width: 18px;
                max-width: 18px;
                min-height: 18px;
                max-height: 18px;
            }
            QToolButton:hover {
                background-color: #383838;
                color: #ffffff;
            }
            QToolButton:checked {
                background-color: #e5a823;
                color: #000000;
                border-color: #f5b833;
            }
            QToolButton#muteBtn:checked {
                background-color: #c0392b;
                color: #ffffff;
            }
            QToolButton#visBtn {
                background-color: #252525;
                border: 1px solid #383838;
            }
            QToolButton#visBtn:hover {
                background-color: #383838;
                border-color: #555555;
            }
            QToolButton#visBtn:checked {
                background-color: #2b2b2b;
                border: 1px solid #505050;
            }
            QToolButton#lockBtn:checked {
                background-color: #3a2a10;
                border: 1px solid #e5a823;
            }
            QToolButton#refBtn {
                min-width: 26px;
                max-width: 26px;
                font-size: 8.5px;
            }
            QToolButton#refBtn:checked {
                background-color: #2980b9;
                color: #ffffff;
                border-color: #3498db;
            }
        """)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(5, 4, 5, 4)
        layout.setSpacing(3)

        # Track Name
        self.lbl_name = QLabel(track.name, self)
        layout.addWidget(self.lbl_name)
        layout.addStretch()

        is_video = (track.track_type == TrackType.VIDEO)

        if is_video:
            # Offline Reference Track Toggle (REF)
            self.btn_ref = QToolButton(self)
            self.btn_ref.setObjectName("refBtn")
            self.btn_ref.setText("REF")
            self.btn_ref.setCheckable(True)
            self.btn_ref.setChecked(track.is_reference)
            self.btn_ref.setToolTip("Designate as Editorial Offline Reference Track for Viewport A/B compare")
            self.btn_ref.toggled.connect(self._on_ref_toggled)
            layout.addWidget(self.btn_ref)

            # Visibility (Monochrome Eye SVG)
            self.btn_vis = QToolButton(self)
            self.btn_vis.setObjectName("visBtn")
            self.btn_vis.setCheckable(True)
            self.btn_vis.setChecked(track.visible)
            self.btn_vis.setToolTip("Toggle Video Track Visibility")
            self._update_vis_icon(track.visible)
            self.btn_vis.toggled.connect(self._on_vis_toggled)
            layout.addWidget(self.btn_vis)

        # Lock (Monochrome Lock SVG)
        self.btn_lock = QToolButton(self)
        self.btn_lock.setObjectName("lockBtn")
        self.btn_lock.setCheckable(True)
        self.btn_lock.setChecked(track.locked)
        self.btn_lock.setToolTip("Lock Track against edits")
        self._update_lock_icon(track.locked)
        self.btn_lock.toggled.connect(self._on_lock_toggled)
        layout.addWidget(self.btn_lock)

        # Solo (S - Additive)
        self.btn_solo = QToolButton(self)
        self.btn_solo.setText("S")
        self.btn_solo.setCheckable(True)
        self.btn_solo.setChecked(track.solo)
        self.btn_solo.setToolTip("Solo Track (Additive)")
        self.btn_solo.toggled.connect(self._on_solo_toggled)
        layout.addWidget(self.btn_solo)

        if not is_video:
            # Mute (M) for Audio tracks only (video has visibility toggle)
            self.btn_mute = QToolButton(self)
            self.btn_mute.setObjectName("muteBtn")
            self.btn_mute.setText("M")
            self.btn_mute.setCheckable(True)
            self.btn_mute.setChecked(track.mute)
            self.btn_mute.setToolTip("Mute Audio Track")
            self.btn_mute.toggled.connect(self._on_mute_toggled)
            layout.addWidget(self.btn_mute)

    def _update_vis_icon(self, visible: bool) -> None:
        if hasattr(self, "btn_vis"):
            icon_name = "eye" if visible else "eye_off"
            icon_color = "#f0f0f0" if visible else "#555555"
            self.btn_vis.setIcon(get_svg_icon(icon_name, color=icon_color, size=13))

    def _update_lock_icon(self, locked: bool) -> None:
        if hasattr(self, "btn_lock"):
            icon_name = "lock" if locked else "lock_open"
            icon_color = "#e5a823" if locked else "#666666"
            self.btn_lock.setIcon(get_svg_icon(icon_name, color=icon_color, size=13))

    def _on_ref_toggled(self, val: bool) -> None:
        self.track.is_reference = val
        self.track_changed.emit(self.track.id)

    def _on_vis_toggled(self, val: bool) -> None:
        self.track.visible = val
        self._update_vis_icon(val)
        self.track_changed.emit(self.track.id)

    def _on_lock_toggled(self, val: bool) -> None:
        self.track.locked = val
        self._update_lock_icon(val)
        self.track_changed.emit(self.track.id)

    def _on_solo_toggled(self, val: bool) -> None:
        self.track.solo = val
        self.track_changed.emit(self.track.id)

    def _on_mute_toggled(self, val: bool) -> None:
        self.track.mute = val
        self.track_changed.emit(self.track.id)


class TimelineCanvas(QWidget):
    """
    Multi-track canvas rendering tracks, clips, ruler, In/Out range, markers, and trimming.
    """
    playhead_moved = Signal(int)
    clip_selected = Signal(str, str)         # track_id, clip_id
    clip_moved = Signal(str, str, int)       # target_track_id, clip_id, new_dst_in
    clip_deleted = Signal(str, str, bool)    # track_id, clip_id, ripple
    clip_dropped = Signal(str, str)          # track_id, clip_id
    cut_requested = Signal(str, int)         # track_id, frame
    scrub_frame_requested = Signal(int)
    scrub_started = Signal()
    scrub_finished = Signal(int)
    zoom_changed = Signal(float)
    scroll_changed = Signal(int)
    in_out_changed = Signal(object, object)  # in_mark, out_mark
    marker_added = Signal(int, str, str, str)# frame, name, comment, color
    marker_deleted = Signal(int)
    sequence_changed = Signal()
    toggle_playback_requested = Signal()
    shuttle_reverse_requested = Signal()
    pause_requested = Signal()
    shuttle_forward_requested = Signal()
    step_backward_requested = Signal(int)
    step_forward_requested = Signal(int)

    TRACK_HEIGHT = 34
    RULER_HEIGHT = 26

    CLIP_COLORS = [
        QColor("#2b5b84"),  # Blue
        QColor("#7d4427"),  # Terracotta
        QColor("#2e694b"),  # Green
        QColor("#613f75"),  # Purple
        QColor("#7a6520"),  # Gold
    ]

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._sequence: Optional[TimelineSequence] = None
        self._playhead_frame: int = 1
        self._zoom: float = 1.0  # pixels per frame
        self._scroll_x: int = 0
        self._selected_clip_ids: set[str] = set()
        self._selected_marker_frame: Optional[int] = None
        self._selected_gap: Optional[Tuple[str, Optional[Tuple[int, int]]]] = None
        self._display_timecode: bool = True
        self._active_tool: str = "select"  # select, slide, razor
        self._snapping: bool = True

        # Clip Dragging & Trimming State
        self._drag_mode: Optional[str] = None  # "move", "scrub", "trim_in", "trim_out", "slide"
        self._drag_clip: Optional[ClipItem] = None
        self._drag_track: Optional[Track] = None
        self._drag_start_mouse_pos: Optional[QPointF] = None
        self._drag_start_dst_in: int = 0
        self._drag_start_dst_out: int = 0
        self._drag_start_src_in: int = 0
        self._drag_start_src_out: int = 0
        self._is_dragging: bool = False
        self._ghost_dst_in: int = 0
        self._ghost_dst_out: int = 0
        self._ghost_track: Optional[Track] = None

        # Box Select (Marquee) State
        self._marquee_active: bool = False
        self._marquee_start: Optional[QPointF] = None
        self._marquee_rect: Optional[QRectF] = None
        self._marquee_initial_selection: set[str] = set()

        # Drag & Drop Media State
        self._drop_active: bool = False
        self._drop_frame: int = 1
        self._drop_track: Optional[Track] = None

        # Decoupled 60 FPS Scrubbing Engine State
        self._last_emitted_scrub_frame: Optional[int] = None
        self._last_scrub_dispatch_time: float = 0.0
        self._scrub_settle_timer = QTimer(self)
        self._scrub_settle_timer.setSingleShot(True)
        self._scrub_settle_timer.setInterval(35)
        self._scrub_settle_timer.timeout.connect(self._on_scrub_settle_tick)

        self.setAcceptDrops(True)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._clip_thumbnail_cache: Dict[str, Optional[QPixmap]] = {}
        self._cached_sequence_frames: set[int] = set()

    def set_cached_sequence_frames(self, frames: set[int]) -> None:
        """Update cached destination frame set to render the RAM cache bar."""
        self._cached_sequence_frames = set(frames)
        self.update()

    def _get_clip_thumbnail(self, clip: ClipItem) -> Optional[QPixmap]:
        """Return small cached preview thumbnail of the clip head frame."""
        if not clip or not clip.file_path:
            return None
        cache_key = f"{clip.file_path}_{clip.src_in}"
        if cache_key in self._clip_thumbnail_cache:
            return self._clip_thumbnail_cache[cache_key]

        real_path = clip.file_path
        if "####" in real_path:
            real_path = real_path.replace("####", f"{clip.src_in:04d}")
        elif "%04d" in real_path:
            real_path = real_path.replace("%04d", f"{clip.src_in:04d}")

        pixmap = None
        if os.path.exists(real_path) and not os.path.isdir(real_path):
            try:
                from openreview.core.video_reader import is_video_file
                if is_video_file(real_path):
                    import cv2
                    cap = cv2.VideoCapture(real_path)
                    cap.set(cv2.CAP_PROP_POS_FRAMES, max(0, clip.src_in - 1))
                    ret, frame = cap.read()
                    cap.release()
                    if ret and frame is not None:
                        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                        h, w, ch = rgb.shape
                        qimg = QImage(rgb.data, w, h, ch * w, QImage.Format.Format_RGB888)
                        scaled = qimg.scaled(48, 30, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
                        pixmap = QPixmap.fromImage(scaled)
                else:
                    qimg = QImage(real_path)
                    if not qimg.isNull():
                        scaled = qimg.scaled(48, 30, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
                        pixmap = QPixmap.fromImage(scaled)
            except Exception:
                pixmap = None

        if pixmap is None:
            pm = QPixmap(48, 30)
            pm.fill(QColor("#111111"))
            p = QPainter(pm)
            p.setPen(QColor("#2c2c2c"))
            p.drawRect(0, 0, 47, 29)
            p.setPen(QColor("#555555"))
            font = p.font()
            font.setPointSize(6)
            font.setBold(True)
            p.setFont(font)
            p.drawText(QRectF(0, 0, 48, 30), Qt.AlignmentFlag.AlignCenter, "CLIP")
            p.end()
            pixmap = pm

        self._clip_thumbnail_cache[cache_key] = pixmap
        return pixmap

    def _on_scrub_settle_tick(self) -> None:
        """Ensure the latest playhead frame is delivered after rapid scrubbing."""
        if self._playhead_frame != self._last_emitted_scrub_frame:
            self._last_emitted_scrub_frame = self._playhead_frame
            self.playhead_moved.emit(self._playhead_frame)
            self.scrub_frame_requested.emit(self._playhead_frame)

    @property
    def _selected_clip_id(self) -> str:
        return next(iter(self._selected_clip_ids)) if self._selected_clip_ids else ""

    @_selected_clip_id.setter
    def _selected_clip_id(self, val: str) -> None:
        if val:
            self._selected_clip_ids = {val}
        else:
            self._selected_clip_ids.clear()

    def select_clip(self, clip_id: str) -> None:
        """Select a clip by id on the timeline canvas."""
        if clip_id:
            self._selected_clip_ids = {clip_id}
        else:
            self._selected_clip_ids.clear()
        self.update()

    @property
    def _is_dragging_clip(self) -> bool:
        return self._is_dragging

    @_is_dragging_clip.setter
    def _is_dragging_clip(self, val: bool) -> None:
        self._is_dragging = val

    def set_sequence(self, sequence: Optional[TimelineSequence]) -> None:
        self._sequence = sequence
        self._selected_clip_ids.clear()
        self._selected_marker_frame = None
        self.update()

    def set_playhead_frame(self, frame: int) -> None:
        if self._playhead_frame != frame:
            self._playhead_frame = frame
            self.update()

    def set_active_tool(self, tool: str) -> None:
        self._active_tool = tool
        self.update()

    def set_snapping(self, enabled: bool) -> None:
        self._snapping = enabled

    def set_zoom(self, zoom: float, scroll_x: Optional[int] = None) -> None:
        self._zoom = max(0.05, min(10.0, zoom))
        if scroll_x is not None:
            self._scroll_x = max(0, scroll_x)
        self.zoom_changed.emit(self._zoom)
        self.scroll_changed.emit(self._scroll_x)
        self.update()

    def zoom_in(self) -> None:
        self.set_zoom(self._zoom * 1.25)

    def zoom_out(self) -> None:
        self.set_zoom(self._zoom * 0.8)

    def zoom_to_fit(self) -> None:
        if not self._sequence:
            return
        dur = max(24, self._sequence.duration)
        w = max(100, self.width())
        new_zoom = max(0.05, min(10.0, float(w) / float(dur)))
        self._zoom = new_zoom
        self._scroll_x = 0
        self.zoom_changed.emit(self._zoom)
        self.scroll_changed.emit(self._scroll_x)
        self.update()

    def mark_in(self) -> None:
        if self._sequence:
            self._sequence.in_mark = self._playhead_frame
            self.in_out_changed.emit(self._sequence.in_mark, self._sequence.out_mark)
            self.update()

    def mark_out(self) -> None:
        if self._sequence:
            self._sequence.out_mark = self._playhead_frame
            self.in_out_changed.emit(self._sequence.in_mark, self._sequence.out_mark)
            self.update()

    def clear_in_out(self) -> None:
        if self._sequence:
            self._sequence.in_mark = None
            self._sequence.out_mark = None
            self.in_out_changed.emit(None, None)
            self.update()

    def frame_to_x(self, frame: int) -> float:
        start = self._sequence.in_frame if self._sequence else 1
        return (frame - start) * self._zoom - self._scroll_x

    def x_to_frame(self, x: float, snap: bool = True) -> int:
        start = self._sequence.in_frame if self._sequence else 1
        frame = int(round((x + self._scroll_x) / self._zoom)) + start
        if snap and self._snapping and self._sequence:
            snap_dist_frames = max(1, int(round(8 / self._zoom)))
            # Snap to playhead
            if abs(frame - self._playhead_frame) <= snap_dist_frames:
                return self._playhead_frame
            # Snap to In/Out marks
            if self._sequence.in_mark is not None and abs(frame - self._sequence.in_mark) <= snap_dist_frames:
                return self._sequence.in_mark
            if self._sequence.out_mark is not None and abs(frame - self._sequence.out_mark) <= snap_dist_frames:
                return self._sequence.out_mark
            # Snap to clip boundaries
            for t in self._sequence.tracks:
                for c in t.clips:
                    if abs(frame - c.dst_in) <= snap_dist_frames:
                        return c.dst_in
                    if abs(frame - (c.dst_out + 1)) <= snap_dist_frames:
                        return c.dst_out + 1
                    if abs(frame - c.dst_out) <= snap_dist_frames:
                        return c.dst_out
        return max(1, frame)

    def _find_clip_edge_at(self, pos: QPointF) -> Optional[Tuple[str, Track, ClipItem]]:
        """Returns ('in', track, clip) or ('out', track, clip) if within 8px of an edge in screen space."""
        if not self._sequence:
            return None
        y = pos.y() - self.RULER_HEIGHT
        t_idx = int(y // self.TRACK_HEIGHT)
        if 0 <= t_idx < len(self._sequence.tracks):
            track = self._sequence.tracks[t_idx]
            if track.locked:
                return None

            best_match = None
            best_dist = 11.0  # 10px pixel tolerance

            for clip in track.clips:
                x_in = self.frame_to_x(clip.dst_in)
                x_out = self.frame_to_x(clip.dst_out + 1)
                dist_in = abs(pos.x() - x_in)
                dist_out = abs(pos.x() - x_out)

                if dist_in <= 10.0 and dist_in < best_dist:
                    best_dist = dist_in
                    best_match = ("in", track, clip)

                if dist_out <= 10.0 and dist_out < best_dist:
                    best_dist = dist_out
                    best_match = ("out", track, clip)

            return best_match
        return None

    def _compute_ruler_steps(self) -> Tuple[int, int]:
        """
        Compute optimal major and minor frame steps so timecode text labels
        maintain >= 110px spacing at the current zoom level, preventing crowded text.
        Returns (major_step, minor_step).
        """
        CANDIDATES = [
            1, 2, 5, 10, 12, 24, 48, 72, 120, 240, 480, 1200, 2400, 4800, 14400, 28800, 86400
        ]
        min_pixel_spacing = 110.0
        major_step = CANDIDATES[-1]
        for c in CANDIDATES:
            if c * self._zoom >= min_pixel_spacing:
                major_step = c
                break
        else:
            major_step = int(round(min_pixel_spacing / max(0.0001, self._zoom)))

        if major_step >= 2400:
            minor_step = major_step // 4
        elif major_step in (480, 120, 48, 24, 12):
            minor_step = major_step // 2 if major_step <= 24 else major_step // 4
        elif major_step in (10, 2):
            minor_step = major_step // 2
        elif major_step == 5:
            minor_step = 1
        else:
            minor_step = max(1, major_step // 2)

        return max(1, major_step), max(1, minor_step)

    def get_selected_clips(self) -> List[Tuple[Track, ClipItem]]:
        """Return list of (track, clip) pairs for currently selected clips."""
        if not self._sequence or not self._selected_clip_ids:
            return []
        res = []
        for t in self._sequence.tracks:
            for c in t.clips:
                if c.id in self._selected_clip_ids:
                    res.append((t, c))
        return res

    def remove_selected_clips(self, ripple: bool = False) -> None:
        """Remove all currently selected clips from their tracks."""
        if not self._sequence or not self._selected_clip_ids:
            return
        clips = list(self.get_selected_clips())
        self._selected_clip_ids.clear()
        deleted_info = []
        shifted_state = []
        for track, clip in clips:
            deleted_info.append({"track": track, "clip": clip})
            if ripple:
                shift_dur = clip.dst_duration
                for sc in track.clips:
                    if sc.dst_in > clip.dst_out:
                        shifted_state.append({
                            "track": track,
                            "id": sc.id,
                            "old_dst_in": sc.dst_in,
                            "old_dst_out": sc.dst_out,
                            "new_dst_in": sc.dst_in - shift_dur,
                            "new_dst_out": sc.dst_out - shift_dur,
                        })
            track.remove_clip(clip.id, ripple=ripple)
            self.clip_deleted.emit(track.id, clip.id, ripple)

        cmd = ClipDeleteCommand(
            sequence=self._sequence,
            deleted_clips_info=deleted_info,
            shifted_clips_state=shifted_state,
            on_change=lambda: (self.sequence_changed.emit(), self.update()),
        )
        UndoManager.instance().push(cmd)
        self.sequence_changed.emit()
        self.update()

    def remove_selected_clip(self, ripple: bool = False) -> None:
        """Remove selected clip(s). Compatible with single-clip and multi-clip selection."""
        self.remove_selected_clips(ripple=ripple)

    def restore_selected_clips(self) -> None:
        """
        Restore selected clips (and any linked partner clips) to their full media length
        where space permits without colliding with neighboring clips or extending past sequence frame 1.
        Wrapped in ClipTrimCommand for full undo/redo.
        """
        if not self._sequence:
            return
        selected = self.get_selected_clips()
        if not selected:
            # If no clip explicitly selected, restore clip under playhead if any
            for track in self._sequence.tracks:
                if track.locked:
                    continue
                c = track.get_clip_at_frame(self._playhead_frame)
                if c:
                    selected = [(track, c)]
                    break

        if not selected:
            return

        restored_any = False
        for track, clip in selected:
            # Check linked partner clips
            partners = self._sequence.get_linked_clips(clip)
            clips_to_restore = [(track, clip)] + [(p_t, p_c) for p_t, p_c in partners if not p_t.locked]

            for t, c in clips_to_restore:
                old_in = c.dst_in
                old_out = c.dst_out
                old_sin = c.src_in
                old_sout = c.src_out

                new_in, new_out, new_sin, new_sout = self._sequence.restore_clip_to_full_source(t, c)
                if (new_in != old_in or new_out != old_out or new_sin != old_sin or new_sout != old_sout):
                    c.dst_in = new_in
                    c.dst_out = new_out
                    c.src_in = new_sin
                    c.src_out = new_sout
                    t.clips.sort(key=lambda x: x.dst_in)

                    cmd = ClipTrimCommand(
                        sequence=self._sequence,
                        track=t,
                        clip=c,
                        old_dst_in=old_in,
                        old_dst_out=old_out,
                        old_src_in=old_sin,
                        old_src_out=old_sout,
                        new_dst_in=new_in,
                        new_dst_out=new_out,
                        new_src_in=new_sin,
                        new_src_out=new_sout,
                        on_change=lambda: (self.sequence_changed.emit(), self.update()),
                    )
                    UndoManager.instance().push(cmd)
                    restored_any = True

        if restored_any:
            self.sequence_changed.emit()
            self.update()

    def _get_target_clip_for_trim(self) -> Optional[Tuple[Track, ClipItem]]:
        """Return (track, clip) for trimming: selected clip if any, or top-most clip under playhead."""
        if not self._sequence:
            return None
        sel = self.get_selected_clips()
        if sel:
            return sel[0]
        f = self._playhead_frame
        for track in self._sequence.tracks:
            if track.locked or not track.visible:
                continue
            clip = track.get_clip_at_frame(f)
            if clip:
                return (track, clip)
        return None

    def unlink_selected_clips(self) -> None:
        """
        Unlink selected clips and their partner clips so they can be trimmed, moved,
        and edited independently. Undoable via ClipLinkCommand.
        """
        if not self._sequence:
            return
        selected = self.get_selected_clips()
        if not selected:
            return

        from openreview.core.undo_manager import ClipLinkCommand

        links_data: List[Dict[str, Any]] = []
        affected_clips: Set[str] = set()

        for track, clip in selected:
            if clip.link_id and clip.id not in affected_clips:
                partners = self._sequence.get_linked_clips(clip)
                all_linked = [(track, clip)] + partners
                for p_track, p_clip in all_linked:
                    if p_clip.id not in affected_clips:
                        affected_clips.add(p_clip.id)
                        links_data.append({
                            "clip": p_clip,
                            "old_link_id": p_clip.link_id,
                            "new_link_id": None,
                        })
                        p_clip.link_id = None

        if links_data:
            cmd = ClipLinkCommand(
                sequence=self._sequence,
                links_data=links_data,
                description="Unlink Clips",
                on_change=lambda: (self.sequence_changed.emit(), self.update()),
            )
            UndoManager.instance().push(cmd)
            self.sequence_changed.emit()
            self.update()

    def link_selected_clips(self) -> None:
        """
        Link two or more selected clips together so they trim, move, and ripple in lockstep.
        Undoable via ClipLinkCommand.
        """
        if not self._sequence:
            return
        selected = self.get_selected_clips()
        if len(selected) < 2:
            return

        from uuid import uuid4
        from openreview.core.undo_manager import ClipLinkCommand

        new_link_id = f"link_{uuid4().hex[:8]}"
        links_data: List[Dict[str, Any]] = []

        for track, clip in selected:
            links_data.append({
                "clip": clip,
                "old_link_id": clip.link_id,
                "new_link_id": new_link_id,
            })
            clip.link_id = new_link_id

        cmd = ClipLinkCommand(
            sequence=self._sequence,
            links_data=links_data,
            description="Link Clips",
            on_change=lambda: (self.sequence_changed.emit(), self.update()),
        )
        UndoManager.instance().push(cmd)
        self.sequence_changed.emit()
        self.update()

    def toggle_link_selected_clips(self) -> None:
        """Toggle link/unlink state for selected clips (Ctrl+L)."""
        selected = self.get_selected_clips()
        if not selected:
            return
        has_linked = any(clip.link_id for _, clip in selected)
        if has_linked:
            self.unlink_selected_clips()
        elif len(selected) >= 2:
            self.link_selected_clips()

    def ripple_trim_head_to_playhead(self) -> None:
        """
        Ripple trim head of target clip to current playhead frame (Q).
        Clips downstream ripple left to close the gap.
        Undoable via ClipTrimCommand.
        """
        target = self._get_target_clip_for_trim()
        if not target:
            return
        track, clip = target
        curr_f = self._playhead_frame
        if curr_f <= clip.dst_in or curr_f > clip.dst_out:
            return

        old_dst_in = clip.dst_in
        old_dst_out = clip.dst_out
        old_src_in = clip.src_in
        old_src_out = clip.src_out

        delta = curr_f - old_dst_in
        new_dst_in = curr_f
        new_src_in = old_src_in + delta

        partners = []
        if clip.link_id:
            partners = self._sequence.get_linked_clips(clip)

        subsequent = [c for c in track.clips if c.dst_in > old_dst_in and c.id != clip.id]
        shifted = []
        for sc in subsequent:
            shifted.append({
                "id": sc.id,
                "old_dst_in": sc.dst_in,
                "old_dst_out": sc.dst_out,
                "new_dst_in": sc.dst_in - delta,
                "new_dst_out": sc.dst_out - delta,
            })
            sc.dst_in -= delta
            sc.dst_out -= delta

        clip.dst_in = new_dst_in
        clip.src_in = new_src_in
        track.clips.sort(key=lambda c: c.dst_in)

        cmd = ClipTrimCommand(
            sequence=self._sequence,
            track=track,
            clip=clip,
            old_dst_in=old_dst_in,
            old_dst_out=old_dst_out,
            old_src_in=old_src_in,
            old_src_out=old_src_out,
            new_dst_in=new_dst_in,
            new_dst_out=old_dst_out,
            new_src_in=new_src_in,
            new_src_out=old_src_out,
            shifted_clips_state=shifted,
            on_change=lambda: (self.sequence_changed.emit(), self.update()),
        )
        UndoManager.instance().push(cmd)

        for p_track, p_clip in partners:
            if p_track.locked:
                continue
            p_old_in = p_clip.dst_in
            p_old_out = p_clip.dst_out
            p_old_sin = p_clip.src_in
            p_old_sout = p_clip.src_out
            p_subsequent = [c for c in p_track.clips if c.dst_in > p_old_in and c.id != p_clip.id]
            p_shifted = []
            for sc in p_subsequent:
                p_shifted.append({
                    "id": sc.id,
                    "old_dst_in": sc.dst_in,
                    "old_dst_out": sc.dst_out,
                    "new_dst_in": sc.dst_in - delta,
                    "new_dst_out": sc.dst_out - delta,
                })
                sc.dst_in -= delta
                sc.dst_out -= delta
            p_clip.dst_in = curr_f
            p_clip.src_in = p_old_sin + delta
            p_track.clips.sort(key=lambda c: c.dst_in)
            p_cmd = ClipTrimCommand(
                sequence=self._sequence,
                track=p_track,
                clip=p_clip,
                old_dst_in=p_old_in,
                old_dst_out=p_old_out,
                old_src_in=p_old_sin,
                old_src_out=p_old_sout,
                new_dst_in=curr_f,
                new_dst_out=p_old_out,
                new_src_in=p_clip.src_in,
                new_src_out=p_old_sout,
                shifted_clips_state=p_shifted,
                on_change=lambda: (self.sequence_changed.emit(), self.update()),
            )
            UndoManager.instance().push(p_cmd)

        self.sequence_changed.emit()
        self.update()

    def ripple_trim_tail_to_playhead(self) -> None:
        """
        Ripple trim tail of target clip to playhead - 1 (W).
        Clips downstream ripple left to close the gap.
        Undoable via ClipTrimCommand.
        """
        target = self._get_target_clip_for_trim()
        if not target:
            return
        track, clip = target
        curr_f = self._playhead_frame
        new_dst_out = curr_f - 1
        if new_dst_out < clip.dst_in or new_dst_out >= clip.dst_out:
            return

        old_dst_in = clip.dst_in
        old_dst_out = clip.dst_out
        old_src_in = clip.src_in
        old_src_out = clip.src_out

        delta = old_dst_out - new_dst_out
        new_src_out = old_src_out - delta

        partners = []
        if clip.link_id:
            partners = self._sequence.get_linked_clips(clip)

        subsequent = [c for c in track.clips if c.dst_in > old_dst_in and c.id != clip.id]
        shifted = []
        for sc in subsequent:
            shifted.append({
                "id": sc.id,
                "old_dst_in": sc.dst_in,
                "old_dst_out": sc.dst_out,
                "new_dst_in": sc.dst_in - delta,
                "new_dst_out": sc.dst_out - delta,
            })
            sc.dst_in -= delta
            sc.dst_out -= delta

        clip.dst_out = new_dst_out
        clip.src_out = new_src_out
        track.clips.sort(key=lambda c: c.dst_in)

        cmd = ClipTrimCommand(
            sequence=self._sequence,
            track=track,
            clip=clip,
            old_dst_in=old_dst_in,
            old_dst_out=old_dst_out,
            old_src_in=old_src_in,
            old_src_out=old_src_out,
            new_dst_in=old_dst_in,
            new_dst_out=new_dst_out,
            new_src_in=old_src_in,
            new_src_out=new_src_out,
            shifted_clips_state=shifted,
            on_change=lambda: (self.sequence_changed.emit(), self.update()),
        )
        UndoManager.instance().push(cmd)

        for p_track, p_clip in partners:
            if p_track.locked:
                continue
            p_old_in = p_clip.dst_in
            p_old_out = p_clip.dst_out
            p_old_sin = p_clip.src_in
            p_old_sout = p_clip.src_out
            p_subsequent = [c for c in p_track.clips if c.dst_in > p_old_in and c.id != p_clip.id]
            p_shifted = []
            for sc in p_subsequent:
                p_shifted.append({
                    "id": sc.id,
                    "old_dst_in": sc.dst_in,
                    "old_dst_out": sc.dst_out,
                    "new_dst_in": sc.dst_in - delta,
                    "new_dst_out": sc.dst_out - delta,
                })
                sc.dst_in -= delta
                sc.dst_out -= delta
            p_clip.dst_out = new_dst_out
            p_clip.src_out = p_old_sout - delta
            p_track.clips.sort(key=lambda c: c.dst_in)
            p_cmd = ClipTrimCommand(
                sequence=self._sequence,
                track=p_track,
                clip=p_clip,
                old_dst_in=p_old_in,
                old_dst_out=p_old_out,
                old_src_in=p_old_sin,
                old_src_out=p_old_sout,
                new_dst_in=p_old_in,
                new_dst_out=new_dst_out,
                new_src_in=p_old_sin,
                new_src_out=p_clip.src_out,
                shifted_clips_state=p_shifted,
                on_change=lambda: (self.sequence_changed.emit(), self.update()),
            )
            UndoManager.instance().push(p_cmd)

        self.sequence_changed.emit()
        self.update()

    def rename_selected_clip_dialog(self) -> None:
        """Open rename dialog for single clip or batch dialog for multiple clips."""
        sel = self.get_selected_clips()
        if not sel:
            return
        if len(sel) > 1:
            self.batch_rename_dialog()
            return
        track, clip = sel[0]
        new_name, ok = QInputDialog.getText(self, "Rename Clip", "Enter new clip name:", text=clip.name)
        if ok and new_name.strip():
            clip.name = new_name.strip()
            self.sequence_changed.emit()
            self.update()

    def batch_rename_dialog(self) -> None:
        """Open Batch Rename wizard with increment tokens and live preview table."""
        if not self._sequence:
            return
        sel = self.get_selected_clips()
        if not sel:
            for t in self._sequence.tracks:
                for c in t.clips:
                    sel.append((t, c))
        if not sel:
            return
        dlg = BatchRenameDialog(sel, parent=self)
        if dlg.exec():
            self.sequence_changed.emit()
            self.update()

    def export_selected_clips_dialog(self) -> None:
        """Open Clip Export Wizard to render individual media files or export editorial turnovers."""
        if not self._sequence:
            return
        sel = self.get_selected_clips()
        if not sel:
            for t in self._sequence.tracks:
                for c in t.clips:
                    sel.append((t, c))
        if not sel:
            QMessageBox.information(self, "Export Clips", "No clips available to export.")
            return
        dlg = ClipExportDialog(sel, self._sequence, parent=self)
        dlg.exec()

    def edit_marker_dialog(self, frame: int) -> None:
        """Open modal dialog to view, edit, or delete an existing review marker."""
        if not self._sequence:
            return
        marker = self._sequence.get_marker_at_frame(frame)
        if not marker:
            return
        dlg = MarkerEditDialog(marker, parent=self)
        if dlg.exec():
            if dlg.action == "delete":
                cmd = MarkerDeleteCommand(
                    sequence=self._sequence,
                    marker=marker,
                    on_change=lambda: (self.sequence_changed.emit(), self.update()),
                )
                self._sequence.remove_marker_at_frame(frame)
                UndoManager.instance().push(cmd)
                if self._selected_marker_frame == frame:
                    self._selected_marker_frame = None
                self.marker_deleted.emit(frame)
            elif dlg.action == "save":
                old_data = {
                    "name": marker.name,
                    "comment": marker.comment,
                    "color": marker.color,
                    "category": marker.category,
                }
                new_data = {
                    "name": dlg.title_edit.text() or "Review Note",
                    "comment": dlg.comment_edit.toPlainText(),
                    "color": dlg.selected_color,
                    "category": dlg.selected_category,
                }
                marker.name = new_data["name"]
                marker.comment = new_data["comment"]
                marker.color = new_data["color"]
                marker.category = new_data["category"]
                cmd = MarkerEditCommand(
                    marker=marker,
                    old_data=old_data,
                    new_data=new_data,
                    on_change=lambda: (self.sequence_changed.emit(), self.update()),
                )
                UndoManager.instance().push(cmd)
            self.sequence_changed.emit()
            self.update()

    def delete_selected_marker(self) -> None:
        """Delete currently selected review marker from ruler."""
        if self._sequence and self._selected_marker_frame is not None:
            f = self._selected_marker_frame
            marker = self._sequence.get_marker_at_frame(f)
            if marker:
                cmd = MarkerDeleteCommand(
                    sequence=self._sequence,
                    marker=marker,
                    on_change=lambda: (self.sequence_changed.emit(), self.update()),
                )
                self._sequence.remove_marker_at_frame(f)
                UndoManager.instance().push(cmd)
            self._selected_marker_frame = None
            self.marker_deleted.emit(f)
            self.sequence_changed.emit()
            self.update()

    def _split_at_playhead(self) -> None:
        """
        Split/Razor clip at current playhead frame ('C' key).
        If a clip is selected, splits that clip if playhead is within its bounds.
        If no clip is selected, splits the top visible unlocked clip under the playhead.
        """
        if not self._sequence:
            return
        frame = self._playhead_frame

        target_track = None
        target_clip = None

        # 1. Check selected clips first
        sel_clips = self.get_selected_clips()
        for trk, clp in sel_clips:
            if not trk.locked and clp.dst_in < frame <= clp.dst_out:
                target_track = trk
                target_clip = clp
                break

        # 2. If no selected clip intersected playhead, find top visible unlocked clip under playhead
        if not target_clip:
            for trk in self._sequence.tracks:
                if trk.locked or not trk.visible:
                    continue
                clp = trk.get_clip_at_frame(frame)
                if clp and clp.dst_in < frame <= clp.dst_out:
                    target_track = trk
                    target_clip = clp
                    break

        if target_track and target_clip:
            return self.split_clip(target_track.id, frame)
        return None

    def split_clip(self, track_id: str, frame: int) -> Optional[ClipItem]:
        """Split a clip at frame on track_id. Returns the newly created clip item."""
        if not self._sequence:
            return None
        for t in self._sequence.tracks:
            if t.id == track_id:
                clip = t.get_clip_at_frame(frame)
                if clip and clip.dst_in < frame <= clip.dst_out:
                    split_offset = frame - clip.dst_in
                    new_src_split = clip.src_in + split_offset

                    clip2 = ClipItem(
                        id=f"{clip.id}_cut_{frame}",
                        name=f"{clip.name}_cut",
                        file_path=clip.file_path,
                        src_in=new_src_split,
                        src_out=clip.src_out,
                        dst_in=frame,
                        dst_out=clip.dst_out,
                        speed=clip.speed,
                        reel=clip.reel,
                        colorspace=clip.colorspace,
                        version=clip.version,
                        tags=list(clip.tags),
                    )
                    orig_dst_out = clip.dst_out
                    orig_src_out = clip.src_out
                    clip.src_out = new_src_split - 1
                    clip.dst_out = frame - 1
                    t.add_clip(clip2)

                    cmd = ClipSplitCommand(
                        track=t,
                        clip_original=clip,
                        original_dst_out=orig_dst_out,
                        original_src_out=orig_src_out,
                        new_split_clip=clip2,
                        on_change=lambda: (self.sequence_changed.emit(), self.update()),
                    )
                    UndoManager.instance().push(cmd)

                    # Also split linked partner clips synchronously
                    if clip.link_id:
                        from uuid import uuid4
                        new_link_id = f"link_{uuid4().hex[:8]}"
                        clip2.link_id = new_link_id
                        for p_track, p_clip in self._sequence.get_linked_clips(clip):
                            if p_clip.dst_in < frame <= p_clip.dst_out:
                                p_offset = frame - p_clip.dst_in
                                p_new_src = p_clip.src_in + p_offset
                                p_clip2 = ClipItem(
                                    id=f"{p_clip.id}_cut_{frame}",
                                    name=f"{p_clip.name}_cut",
                                    file_path=p_clip.file_path,
                                    src_in=p_new_src,
                                    src_out=p_clip.src_out,
                                    dst_in=frame,
                                    dst_out=p_clip.dst_out,
                                    speed=p_clip.speed,
                                    link_id=new_link_id,
                                )
                                p_orig_dst_out = p_clip.dst_out
                                p_orig_src_out = p_clip.src_out
                                p_clip.src_out = p_new_src - 1
                                p_clip.dst_out = frame - 1
                                p_track.add_clip(p_clip2)
                                UndoManager.instance().push(ClipSplitCommand(
                                    track=p_track,
                                    clip_original=p_clip,
                                    original_dst_out=p_orig_dst_out,
                                    original_src_out=p_orig_src_out,
                                    new_split_clip=p_clip2,
                                    on_change=lambda: (self.sequence_changed.emit(), self.update()),
                                ))

                    self.update()
                    self.sequence_changed.emit()
                    self.cut_requested.emit(track_id, frame)
                    return clip2
                break
        return None

    def _get_gap_at_frame(self, track: Track, frame: int) -> Optional[Tuple[int, int]]:
        """
        Return (gap_start, gap_end) for empty space containing frame on track,
        or None if frame is inside a clip or beyond the last clip with nothing following.
        """
        if not track or not track.clips:
            return None
        sorted_clips = sorted(track.clips, key=lambda c: c.dst_in)
        first_clip = sorted_clips[0]

        # 1. Gap before the first clip
        seq_in = self._sequence.in_frame if self._sequence else 1
        if frame < first_clip.dst_in:
            gap_start = seq_in
            gap_end = first_clip.dst_in - 1
            if gap_start <= frame <= gap_end and gap_end >= gap_start:
                return gap_start, gap_end

        # 2. Gap between two clips
        for i in range(len(sorted_clips) - 1):
            c_prev = sorted_clips[i]
            c_next = sorted_clips[i + 1]
            if c_prev.dst_out < frame < c_next.dst_in:
                gap_start = c_prev.dst_out + 1
                gap_end = c_next.dst_in - 1
                if gap_end >= gap_start:
                    return gap_start, gap_end

        return None

    def ripple_delete_gap(self, track_id: str, gap_start: int, gap_end: int, all_tracks: bool = False) -> None:
        """
        Close empty gap by shifting subsequent clips left by gap duration.
        If all_tracks is True, shifts clips on all unlocked tracks.
        """
        if not self._sequence or gap_end < gap_start:
            return
        gap_dur = gap_end - gap_start + 1

        tracks_to_ripple = self._sequence.tracks if all_tracks else [t for t in self._sequence.tracks if t.id == track_id]

        for trk in tracks_to_ripple:
            if trk.locked:
                continue
            for clp in trk.clips:
                if clp.dst_in > gap_end:
                    clp.dst_in -= gap_dur
                    clp.dst_out -= gap_dur
            trk.clips.sort(key=lambda c: c.dst_in)

        # Move playhead to gap_start if it was inside the gap
        if gap_start <= self._playhead_frame <= gap_end:
            self._playhead_frame = gap_start
            self.playhead_moved.emit(self._playhead_frame)

        self.sequence_changed.emit()
        self.update()

    def add_marker_dialog(self, frame: Optional[int] = None) -> None:
        """Open modal dialog to attach a review marker / note flag at frame."""
        if not self._sequence:
            return
        target_f = frame if frame is not None else self._playhead_frame

        dlg = QDialog(self)
        dlg.setWindowTitle(f"Add Review Marker @ Frame {target_f}")
        dlg.setStyleSheet("background-color: #202020; color: #dddddd;")
        form = QFormLayout(dlg)

        name_edit = QLineEdit(f"Note @ {target_f}", dlg)
        cat_combo = QComboBox(dlg)
        cat_combo.addItems(["Note (Yellow)", "Approved (Green)", "Retake (Red)", "Client Note (Blue)"])
        comment_edit = QTextEdit(dlg)
        comment_edit.setPlaceholderText("Enter review feedback / action item...")
        comment_edit.setMaximumHeight(80)

        form.addRow("Title:", name_edit)
        form.addRow("Category:", cat_combo)
        form.addRow("Comment:", comment_edit)

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, dlg)
        btns.accepted.connect(dlg.accept)
        btns.rejected.connect(dlg.reject)
        form.addRow(btns)

        if dlg.exec():
            cat_text = cat_combo.currentText()
            if "Approved" in cat_text:
                color = "#2ecc71"
                category = "Approved"
            elif "Retake" in cat_text:
                color = "#e74c3c"
                category = "Retake"
            elif "Client" in cat_text:
                color = "#3498db"
                category = "Client"
            else:
                color = "#f1c40f"
                category = "Note"

            marker = self._sequence.add_marker(
                frame=target_f,
                name=name_edit.text() or "Review Note",
                comment=comment_edit.toPlainText(),
                color=color,
                category=category,
            )
            cmd = MarkerAddCommand(
                sequence=self._sequence,
                marker=marker,
                on_change=lambda: (self.sequence_changed.emit(), self.update()),
            )
            UndoManager.instance().push(cmd)
            self.marker_added.emit(target_f, name_edit.text(), comment_edit.toPlainText(), color)
            self.sequence_changed.emit()
            self.update()

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)

        width = self.width()
        height = self.height()

        # 1. Background
        painter.fillRect(0, 0, width, height, QColor("#121212"))

        if not self._sequence:
            painter.setPen(QColor("#555555"))
            painter.drawText(20, 50, "No Timeline Sequence Loaded")
            return

        # 2. Draw Track Lanes
        y = self.RULER_HEIGHT
        for idx, track in enumerate(self._sequence.tracks):
            lane_rect = QRectF(0, y, width, self.TRACK_HEIGHT)
            lane_bg = QColor("#171717") if idx % 2 == 0 else QColor("#151515")
            if track.is_reference:
                lane_bg = QColor("#14222e")
            painter.fillRect(lane_rect, lane_bg)
            painter.setPen(QColor("#242424"))
            painter.drawLine(0, int(y + self.TRACK_HEIGHT), width, int(y + self.TRACK_HEIGHT))

            # Draw Clips
            for c_idx, clip in enumerate(track.clips):
                x_start = self.frame_to_x(clip.dst_in)
                x_end = self.frame_to_x(clip.dst_out + 1)

                # Horizontal Viewport Frustum Culling
                if x_end < 0 or x_start > width:
                    continue

                clip_w = max(4.0, x_end - x_start)
                clip_rect = QRectF(x_start, y + 2, clip_w, self.TRACK_HEIGHT - 4)

                if track.track_type == TrackType.AUDIO:
                    # Audio track styling: teal gradient
                    color = QColor("#13362a") if track.visible else QColor("#0d1e17")
                    from PySide6.QtGui import QLinearGradient
                    a_grad = QLinearGradient(clip_rect.topLeft(), clip_rect.bottomLeft())
                    a_grad.setColorAt(0.0, color.lighter(125))
                    a_grad.setColorAt(1.0, color.darker(120))
                    painter.fillRect(clip_rect, a_grad)

                    # Draw Audio Waveform Peaks
                    from openreview.audio.waveform_cache import WaveformCache
                    peaks = WaveformCache.instance().get_waveform_peaks(
                        clip.file_path,
                        fps=self._sequence.fps if self._sequence else 24.0,
                        fallback_frames=clip.dst_duration,
                    )
                    mid_y = y + 2 + (self.TRACK_HEIGHT - 4) / 2.0
                    half_h = (self.TRACK_HEIGHT - 8) / 2.0

                    painter.setPen(QPen(QColor("#1b5a45"), 1))
                    painter.drawLine(int(max(0, x_start)), int(mid_y), int(min(width, x_end)), int(mid_y))

                    wave_color = QColor("#2ecc71") if track.visible else QColor("#1b794b")
                    painter.setPen(wave_color)

                    step_px = 3.0
                    # Cull waveform draw loop to visible on-screen span
                    cur_px = max(x_start, 0.0)
                    end_px = min(x_end, float(width))
                    while cur_px < end_px:
                        f_offset = int((cur_px - x_start) / max(1.0, clip_w) * clip.dst_duration)
                        f_src = (clip.src_in - 1) + f_offset
                        f_idx = min(len(peaks) - 1, max(0, f_src))
                        min_amp = peaks[f_idx, 0]
                        max_amp = peaks[f_idx, 1]
                        top_y = mid_y - max_amp * half_h
                        bot_y = mid_y - min_amp * half_h
                        painter.drawLine(int(cur_px), int(top_y), int(cur_px), int(bot_y))
                        cur_px += step_px
                    text_offset_x = 4.0
                else:
                    # Video track styling with vertical gradient
                    color = self.CLIP_COLORS[(c_idx + idx) % len(self.CLIP_COLORS)]
                    if track.is_reference:
                        color = QColor("#2980b9").darker(130)
                    if not track.visible:
                        color = color.darker(200)

                    from PySide6.QtGui import QLinearGradient
                    v_grad = QLinearGradient(clip_rect.topLeft(), clip_rect.bottomLeft())
                    v_grad.setColorAt(0.0, color.lighter(120))
                    v_grad.setColorAt(0.5, color)
                    v_grad.setColorAt(1.0, color.darker(128))
                    painter.fillRect(clip_rect, v_grad)

                    # Thumbnail inside strip's head
                    text_offset_x = 4.0
                    if clip_w > 44:
                        thumb = self._get_clip_thumbnail(clip)
                        if thumb and not thumb.isNull():
                            thumb_h = self.TRACK_HEIGHT - 8
                            thumb_w = int(thumb_h * 1.45)
                            if thumb_w < clip_w - 10:
                                thumb_rect = QRectF(x_start + 3, y + 4, thumb_w, thumb_h)
                                painter.drawPixmap(thumb_rect.toRect(), thumb)
                                painter.setPen(QColor(0, 0, 0, 150))
                                painter.drawRect(thumb_rect)
                                text_offset_x = thumb_w + 8.0

                # Border / Selection outline
                if clip.id in self._selected_clip_ids:
                    painter.setPen(QPen(QColor("#e5a823"), 2))
                else:
                    painter.setPen(QPen(QColor("#3a3a3a"), 1))
                painter.drawRect(clip_rect)

                # Soft Effects Ribbon & Pill Badge
                has_active_fx = any(getattr(fx, "enabled", True) for fx in clip.soft_effects)
                if has_active_fx:
                    fx_rect = QRectF(x_start, y + 2, clip_w, 3)
                    painter.fillRect(fx_rect, QColor("#e5a823"))
                    if clip_w > 48:
                        badge_w, badge_h = 22.0, 12.0
                        badge_x = x_end - badge_w - 4.0
                        badge_y = y + 4.0
                        badge_rect = QRectF(badge_x, badge_y, badge_w, badge_h)
                        painter.fillRect(badge_rect, QColor("#2b220d"))
                        painter.setPen(QPen(QColor("#e5a823"), 1))
                        painter.drawRect(badge_rect)
                        fx_font = painter.font()
                        fx_font.setPointSize(7)
                        fx_font.setBold(True)
                        painter.setFont(fx_font)
                        painter.setPen(QColor("#e5a823"))
                        painter.drawText(badge_rect, Qt.AlignmentFlag.AlignCenter, "FX")

                # Text label
                if clip_w > 30:
                    painter.setPen(QColor("#ffffff"))
                    font = painter.font()
                    font.setPointSize(8)
                    font.setBold(True)
                    painter.setFont(font)
                    link_prefix = "🔗 " if clip.link_id else ""
                    label = f"{link_prefix}{clip.name} [{clip.version}]"
                    painter.drawText(clip_rect.adjusted(text_offset_x, 2, -4, -2), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, label)

            y += self.TRACK_HEIGHT

        # 3. Draw Drag Ghost Preview (Moving Clip)
        if self._is_dragging and self._drag_mode == "move" and self._drag_clip and self._ghost_track:
            try:
                g_idx = self._sequence.tracks.index(self._ghost_track)
                gy = self.RULER_HEIGHT + g_idx * self.TRACK_HEIGHT
                gx_start = self.frame_to_x(self._ghost_dst_in)
                gx_end = self.frame_to_x(self._ghost_dst_out + 1)
                gw = max(4.0, gx_end - gx_start)
                g_rect = QRectF(gx_start, gy + 2, gw, self.TRACK_HEIGHT - 4)

                painter.fillRect(g_rect, QColor(229, 168, 35, 110))
                painter.setPen(QPen(QColor("#f5b833"), 2, Qt.PenStyle.DashLine))
                painter.drawRect(g_rect)

                delta_f = self._ghost_dst_in - self._drag_start_dst_in
                hud_txt = f"{self._drag_clip.name} | [{self._ghost_dst_in} - {self._ghost_dst_out}] (Δ {delta_f:+d}f)"
                painter.setPen(QColor("#ffffff"))
                font = painter.font()
                font.setPointSize(8)
                font.setBold(True)
                painter.setFont(font)
                painter.drawText(g_rect.adjusted(6, 2, -6, -2), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, hud_txt)
            except Exception:
                pass

        # 4. Draw Trim / Slide HUD
        if self._is_dragging and self._drag_mode in ("trim_in", "trim_out", "slide") and self._drag_clip and self._drag_track:
            try:
                t_idx = self._sequence.tracks.index(self._drag_track)
                cy = self.RULER_HEIGHT + t_idx * self.TRACK_HEIGHT
                cx_start = self.frame_to_x(self._drag_clip.dst_in)
                cx_end = self.frame_to_x(self._drag_clip.dst_out + 1)
                cw = max(4.0, cx_end - cx_start)
                c_rect = QRectF(cx_start, cy + 2, cw, self.TRACK_HEIGHT - 4)

                painter.setPen(QPen(QColor("#f5b833"), 2, Qt.PenStyle.DashLine))
                painter.drawRect(c_rect)

                hud_str = f"{self._drag_clip.name} | [{self._drag_mode.upper()}] In: {self._drag_clip.src_in} | Out: {self._drag_clip.src_out} | Dur: {self._drag_clip.dst_duration}f"
                painter.setPen(QColor("#ffffff"))
                font = painter.font()
                font.setPointSize(8)
                font.setBold(True)
                painter.setFont(font)
                painter.drawText(c_rect.adjusted(6, 2, -6, -2), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, hud_str)
            except Exception:
                pass

        # 5. Draw Drop Ingestion Indicator
        if self._drop_active and self._drop_track:
            try:
                d_idx = self._sequence.tracks.index(self._drop_track)
                dy = self.RULER_HEIGHT + d_idx * self.TRACK_HEIGHT
                dx = self.frame_to_x(self._drop_frame)
                drop_rect = QRectF(dx, dy + 2, max(30.0, 48 * self._zoom), self.TRACK_HEIGHT - 4)
                painter.fillRect(drop_rect, QColor(46, 204, 113, 110))
                painter.setPen(QPen(QColor("#2ecc71"), 2, Qt.PenStyle.DashLine))
                painter.drawRect(drop_rect)
            except Exception:
                pass

        # 6. Draw Timecode Ruler at Top
        painter.fillRect(0, 0, width, self.RULER_HEIGHT, QColor("#1c1c1c"))
        painter.setPen(QColor("#2d2d2d"))
        painter.drawLine(0, self.RULER_HEIGHT, width, self.RULER_HEIGHT)

        # Draw RAM Cache Indicator Line at bottom of ruler
        if self._cached_sequence_frames:
            for f in self._cached_sequence_frames:
                fx = self.frame_to_x(f)
                if -2 <= fx <= width + 2:
                    cw = max(1.0, self._zoom)
                    painter.fillRect(QRectF(fx, self.RULER_HEIGHT - 2.5, cw, 2.5), QColor("#2ecc71"))

        # Draw In / Out Shaded Overlay on Ruler
        if self._sequence and (self._sequence.in_mark is not None or self._sequence.out_mark is not None):
            in_f = self._sequence.in_mark if self._sequence.in_mark is not None else self._sequence.in_frame
            out_f = self._sequence.out_mark if self._sequence.out_mark is not None else self._sequence.out_frame
            if in_f <= out_f:
                in_x = self.frame_to_x(in_f)
                out_x = self.frame_to_x(out_f + 1)
                ruler_io_rect = QRectF(in_x, 0, max(2.0, out_x - in_x), self.RULER_HEIGHT)
                painter.fillRect(ruler_io_rect, QColor(229, 168, 35, 55))

                # In Bracket '['
                painter.setPen(QPen(QColor("#f5b833"), 2.5))
                painter.drawLine(int(in_x), 2, int(in_x), self.RULER_HEIGHT - 2)
                painter.drawLine(int(in_x), 2, int(in_x + 6), 2)
                painter.drawLine(int(in_x), self.RULER_HEIGHT - 2, int(in_x + 6), self.RULER_HEIGHT - 2)

                # Out Bracket ']'
                painter.drawLine(int(out_x), 2, int(out_x), self.RULER_HEIGHT - 2)
                painter.drawLine(int(out_x), 2, int(out_x - 6), 2)
                painter.drawLine(int(out_x), self.RULER_HEIGHT - 2, int(out_x - 6), self.RULER_HEIGHT - 2)

        # Draw Ruler Ticks (dynamic step >= 110px with clean subdivisions)
        major_step, minor_step = self._compute_ruler_steps()
        start_frame = self.x_to_frame(0, snap=False)
        end_frame = self.x_to_frame(width, snap=False)
        fps = self._sequence.fps if self._sequence else 24.0
        seq_in = self._sequence.in_frame if self._sequence else 1
        start_tc = self._sequence.start_timecode if self._sequence and self._sequence.start_timecode else "00:00:00:00"

        # Align relative to sequence in_frame so tick marks land cleanly on round intervals (00:00:00:00, 00:00:05:00, etc.)
        aligned_minor = seq_in + ((start_frame - seq_in) // minor_step) * minor_step
        if minor_step < major_step:
            painter.setPen(QPen(QColor("#383838"), 1))
            for f in range(aligned_minor, end_frame + minor_step, minor_step):
                if (f - seq_in) % major_step != 0:
                    x = self.frame_to_x(f)
                    painter.drawLine(int(x), self.RULER_HEIGHT - 4, int(x), self.RULER_HEIGHT)

        # Draw major ticks with timecode or frame label
        font = painter.font()
        font.setPointSize(7.5)
        painter.setFont(font)
        fm = painter.fontMetrics()

        aligned_major = seq_in + ((start_frame - seq_in) // major_step) * major_step
        last_label_end_x = -999999.0
        for f in range(aligned_major, end_frame + major_step, major_step):
            x = self.frame_to_x(f)
            # Major tick mark
            painter.setPen(QPen(QColor("#5c5c5c"), 1))
            painter.drawLine(int(x), self.RULER_HEIGHT - 8, int(x), self.RULER_HEIGHT)

            rel_f = f - seq_in
            if getattr(self, "_display_timecode", True):
                label_str = frame_to_timecode(rel_f, fps, start_timecode=start_tc)
            else:
                label_str = f"{f:,}f"

            text_w = fm.horizontalAdvance(label_str)
            # Collision prevention: enforce breathing room between text labels
            if x >= last_label_end_x + 12:
                painter.setPen(QPen(QColor("#a8a8a8"), 1))
                painter.drawText(int(x) + 4, self.RULER_HEIGHT - 9, label_str)
                last_label_end_x = x + text_w + 4

        # 7. Draw Review Markers on Ruler
        if self._sequence and self._sequence.markers:
            for m in self._sequence.markers:
                mx = self.frame_to_x(m.frame)
                is_selected = (m.frame == self._selected_marker_frame)
                # Pin pole
                pole_color = QColor("#ffffff") if is_selected else QColor(m.color)
                painter.setPen(QPen(pole_color, 2.0 if is_selected else 1.5))
                painter.drawLine(int(mx), 2, int(mx), self.RULER_HEIGHT)
                # Pin flag
                painter.setBrush(QBrush(QColor(m.color)))
                if is_selected:
                    painter.setPen(QPen(QColor("#f5b833"), 2.0))
                else:
                    painter.setPen(QPen(QColor(m.color).darker(130), 1.0))
                m_poly = QPolygonF([
                    QPointF(mx, 3),
                    QPointF(mx + 9, 7.5),
                    QPointF(mx, 12),
                ])
                painter.drawPolygon(m_poly)
                if is_selected:
                    painter.setBrush(Qt.BrushStyle.NoBrush)
                    painter.setPen(QPen(QColor("#f5b833"), 1.5))
                    painter.drawEllipse(QPointF(mx + 9, 7.5), 3.5, 3.5)

        # 8. Draw Playhead Scrubber Line
        ph_x = self.frame_to_x(self._playhead_frame)
        painter.setPen(QPen(QColor("#e5a823"), 1.5))
        painter.drawLine(int(ph_x), 0, int(ph_x), height)

        # Playhead Triangle Head
        painter.setBrush(QBrush(QColor("#e5a823")))
        poly = QPolygonF([
            QPointF(ph_x - 6, 0),
            QPointF(ph_x + 6, 0),
            QPointF(ph_x, 10),
        ])
        painter.drawPolygon(poly)

        # 9. Draw Box Select (Marquee)
        if self._marquee_active and self._marquee_rect:
            painter.save()
            painter.setPen(QPen(QColor(229, 168, 35, 220), 1.0, Qt.PenStyle.DashLine))
            painter.setBrush(QBrush(QColor(229, 168, 35, 45)))
            painter.drawRect(self._marquee_rect)
            painter.restore()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if not self._sequence:
            return

        self.setFocus()
        pos = event.position()
        frame = self.x_to_frame(pos.x(), snap=False)

        # Right click -> Context menu
        if event.button() == Qt.MouseButton.RightButton:
            self._show_context_menu(event.globalPosition().toPoint(), pos)
            return

        if event.button() != Qt.MouseButton.LeftButton:
            return

        # Click inside ruler -> check marker hit or scrub
        if pos.y() <= self.RULER_HEIGHT:
            if self._sequence and self._sequence.markers:
                for m in self._sequence.markers:
                    mx = self.frame_to_x(m.frame)
                    if abs(pos.x() - mx) <= 7.0:
                        self._selected_marker_frame = m.frame
                        self._selected_clip_ids.clear()
                        self._playhead_frame = m.frame
                        self.playhead_moved.emit(m.frame)
                        self.scrub_frame_requested.emit(m.frame)
                        self.update()
                        return

            self._selected_marker_frame = None
            self._drag_mode = "scrub"
            self.scrub_started.emit()
            self._playhead_frame = frame
            self._last_emitted_scrub_frame = frame
            self._last_scrub_dispatch_time = time.perf_counter()
            self.playhead_moved.emit(frame)
            self.scrub_frame_requested.emit(frame)
            self.update()
            return

        # Check edge hover for trimming (Select Tool)
        if self._active_tool == "select":
            edge_info = self._find_clip_edge_at(pos)
            if edge_info:
                edge_type, track, clip = edge_info
                self._drag_mode = f"trim_{edge_type}"
                self._drag_clip = clip
                self._drag_track = track
                self._drag_start_mouse_pos = pos
                self._drag_start_dst_in = clip.dst_in
                self._drag_start_dst_out = clip.dst_out
                self._drag_start_src_in = clip.src_in
                self._drag_start_src_out = clip.src_out
                self._trim_initial_subsequent = [
                    {"id": sc.id, "old_dst_in": sc.dst_in, "old_dst_out": sc.dst_out}
                    for sc in track.clips if sc.dst_in > clip.dst_in and sc.id != clip.id
                ]
                self._is_dragging = True
                self._selected_clip_ids = {clip.id}
                self._trim_linked_partners = []
                if not (event.modifiers() & Qt.KeyboardModifier.AltModifier) and clip.link_id:
                    for p_track, p_clip in self._sequence.get_linked_clips(clip):
                        self._selected_clip_ids.add(p_clip.id)
                        self._trim_linked_partners.append({
                            "track": p_track,
                            "clip": p_clip,
                            "start_dst_in": p_clip.dst_in,
                            "start_dst_out": p_clip.dst_out,
                            "start_src_in": p_clip.src_in,
                            "start_src_out": p_clip.src_out,
                            "initial_subsequent": [
                                {"id": sc.id, "old_dst_in": sc.dst_in, "old_dst_out": sc.dst_out}
                                for sc in p_track.clips if sc.dst_in > p_clip.dst_in and sc.id != p_clip.id
                            ],
                        })
                self._selected_marker_frame = None
                self.clip_selected.emit(track.id, clip.id)
                self.update()
                return

        # Click inside tracks
        y = pos.y() - self.RULER_HEIGHT
        track_idx = int(y // self.TRACK_HEIGHT)
        clicked_clip = None
        clicked_track = None

        if 0 <= track_idx < len(self._sequence.tracks):
            clicked_track = self._sequence.tracks[track_idx]
            clicked_clip = clicked_track.get_clip_at_frame(frame)

        if self._active_tool == "razor":
            if clicked_track and clicked_clip:
                self.cut_requested.emit(clicked_track.id, frame)
            self.update()
            return

        if self._active_tool == "slide" and clicked_clip and clicked_track:
            self._drag_mode = "slide"
            self._drag_clip = clicked_clip
            self._drag_track = clicked_track
            self._drag_start_mouse_pos = pos
            self._drag_start_dst_in = clicked_clip.dst_in
            self._drag_start_dst_out = clicked_clip.dst_out
            self._drag_start_src_in = clicked_clip.src_in
            self._drag_start_src_out = clicked_clip.src_out

            sorted_clips = sorted(clicked_track.clips, key=lambda c: c.dst_in)
            try:
                c_idx = sorted_clips.index(clicked_clip)
                self._slide_prev_clip = sorted_clips[c_idx - 1] if c_idx > 0 else None
                self._slide_next_clip = sorted_clips[c_idx + 1] if c_idx < len(sorted_clips) - 1 else None
            except ValueError:
                self._slide_prev_clip = None
                self._slide_next_clip = None

            self._slide_prev_start = (
                (self._slide_prev_clip.dst_in, self._slide_prev_clip.dst_out,
                 self._slide_prev_clip.src_in, self._slide_prev_clip.src_out)
                if self._slide_prev_clip else None
            )
            self._slide_next_start = (
                (self._slide_next_clip.dst_in, self._slide_next_clip.dst_out,
                 self._slide_next_clip.src_in, self._slide_next_clip.src_out)
                if self._slide_next_clip else None
            )
            self._is_dragging = True
            self._selected_clip_ids = {clicked_clip.id}
            self._selected_marker_frame = None
            self.clip_selected.emit(clicked_track.id, clicked_clip.id)
            self.update()
            return

        if self._active_tool == "slide":
            # Clicked empty track space with slide tool
            self._selected_marker_frame = None
            self._drag_mode = None
            self._playhead_frame = frame
            self.playhead_moved.emit(frame)
            self.scrub_frame_requested.emit(frame)
            self.update()
            return

        if clicked_clip and clicked_track:
            self._selected_marker_frame = None
            partner_clips = []
            if not (event.modifiers() & Qt.KeyboardModifier.AltModifier) and clicked_clip.link_id:
                partner_clips = [p_clip for _, p_clip in self._sequence.get_linked_clips(clicked_clip)]

            if event.modifiers() & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier):
                if clicked_clip.id in self._selected_clip_ids:
                    self._selected_clip_ids.remove(clicked_clip.id)
                    for p in partner_clips:
                        self._selected_clip_ids.discard(p.id)
                else:
                    self._selected_clip_ids.add(clicked_clip.id)
                    for p in partner_clips:
                        self._selected_clip_ids.add(p.id)
            else:
                if clicked_clip.id not in self._selected_clip_ids:
                    self._selected_clip_ids = {clicked_clip.id}
                    for p in partner_clips:
                        self._selected_clip_ids.add(p.id)

            self._playhead_frame = frame
            self.playhead_moved.emit(frame)
            self.scrub_frame_requested.emit(frame)

            self.clip_selected.emit(clicked_track.id, clicked_clip.id)
            self._drag_mode = "move"
            self._drag_clip = clicked_clip
            self._drag_track = clicked_track
            self._drag_start_mouse_pos = pos
            self._drag_start_dst_in = clicked_clip.dst_in
            self._drag_start_dst_out = clicked_clip.dst_out
            self._is_dragging = False
            self._ghost_dst_in = clicked_clip.dst_in
            self._ghost_dst_out = clicked_clip.dst_out
            self._ghost_track = clicked_track
            self._move_linked_partners = []
            if not (event.modifiers() & Qt.KeyboardModifier.AltModifier) and clicked_clip.link_id:
                for p_track, p_clip in self._sequence.get_linked_clips(clicked_clip):
                    self._move_linked_partners.append({
                        "track": p_track,
                        "clip": p_clip,
                        "start_dst_in": p_clip.dst_in,
                        "start_dst_out": p_clip.dst_out,
                    })
            self.update()
        else:
            # Click on empty track area -> Box Select (Marquee) if on tracks, or scrub if clicked empty
            is_mod = bool(event.modifiers() & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier))
            self._marquee_active = True
            self._marquee_start = pos
            self._marquee_rect = QRectF(pos, pos)
            self._marquee_initial_selection = set(self._selected_clip_ids) if is_mod else set()
            if not is_mod:
                self._selected_clip_ids.clear()
            self._selected_marker_frame = None
            if clicked_track:
                self._selected_gap = (clicked_track.id, self._get_gap_at_frame(clicked_track, frame))
            else:
                self._selected_gap = None
            self._drag_mode = "marquee"
            self._playhead_frame = frame
            self.playhead_moved.emit(frame)
            self.scrub_frame_requested.emit(frame)
            self.update()

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        if not self._sequence:
            return
        pos = event.position()
        if pos.y() <= self.RULER_HEIGHT and self._sequence.markers:
            for m in self._sequence.markers:
                mx = self.frame_to_x(m.frame)
                if abs(pos.x() - mx) <= 7.0:
                    self.edit_marker_dialog(m.frame)
                    return
        super().mouseDoubleClickEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if not self._sequence:
            return

        pos = event.position()

        # Update cursor on hover when idle
        if not self._drag_mode:
            edge_info = self._find_clip_edge_at(pos)
            if edge_info and self._active_tool == "select":
                self.setCursor(Qt.CursorShape.SizeHorCursor)
            elif self._active_tool == "razor":
                self.setCursor(Qt.CursorShape.CrossCursor)
            elif self._active_tool == "slide":
                self.setCursor(Qt.CursorShape.SplitHCursor)
            else:
                self.setCursor(Qt.CursorShape.ArrowCursor)

        if self._drag_mode == "marquee" and self._marquee_start is not None:
            self._marquee_rect = QRectF(self._marquee_start, pos).normalized()
            new_selection = set(self._marquee_initial_selection)
            if self._sequence:
                for t_idx, track in enumerate(self._sequence.tracks):
                    track_y = self.RULER_HEIGHT + (t_idx * self.TRACK_HEIGHT)
                    track_box = QRectF(self._marquee_rect.left(), track_y, self._marquee_rect.width(), self.TRACK_HEIGHT)
                    if not self._marquee_rect.intersects(track_box):
                        continue
                    for clip in track.clips:
                        clip_x = (clip.dst_in * self._zoom) - self._scroll_x
                        clip_w = max(4.0, (clip.duration * self._zoom))
                        clip_rect = QRectF(clip_x, track_y + 2, clip_w, self.TRACK_HEIGHT - 4)
                        if clip_rect.intersects(self._marquee_rect):
                            if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
                                if clip.id in self._marquee_initial_selection:
                                    new_selection.discard(clip.id)
                                else:
                                    new_selection.add(clip.id)
                            else:
                                new_selection.add(clip.id)
            self._selected_clip_ids = new_selection
            self.update()
            return

        if self._drag_mode == "scrub":
            frame = self.x_to_frame(pos.x(), snap=self._snapping)
            if frame != self._playhead_frame:
                self._playhead_frame = frame
                # 60 FPS immediate UI playhead update
                self.update()

                # Decoupled throttled decode dispatch: ~25ms throttle for video decode
                now = time.perf_counter()
                if (now - self._last_scrub_dispatch_time) >= 0.025:
                    self._last_scrub_dispatch_time = now
                    self._last_emitted_scrub_frame = frame
                    self.playhead_moved.emit(frame)
                    self.scrub_frame_requested.emit(frame)
                else:
                    self._scrub_settle_timer.start(35)
            return

        if self._drag_mode == "trim_in" and self._drag_clip and self._drag_track:
            delta_px = pos.x() - self._drag_start_mouse_pos.x()
            delta_frames = int(round(delta_px / self._zoom))

            # Leftward drag (negative delta) extends head into available media frames
            orig_src_in, orig_src_out = self._drag_clip.get_media_source_bounds()
            avail_head = max(0, self._drag_start_src_in - orig_src_in)
            min_delta = -avail_head

            # Cannot extend past sequence frame 1
            min_delta = max(min_delta, 1 - self._drag_start_dst_in)

            # If not ripple (Shift not held), cannot overlap previous clip on track
            if not (event.modifiers() & Qt.KeyboardModifier.ShiftModifier):
                prev_clips = [c for c in self._drag_track.clips if c.dst_out < self._drag_start_dst_in and c.id != self._drag_clip.id]
                if prev_clips:
                    closest_prev = max(prev_clips, key=lambda c: c.dst_out)
                    min_delta = max(min_delta, (closest_prev.dst_out + 1) - self._drag_start_dst_in)

            # Rightward drag (positive delta) trims head, leaving at least 1 frame
            max_delta = self._drag_start_dst_out - self._drag_start_dst_in

            # Synchronized safety clamping across linked partners
            if hasattr(self, "_trim_linked_partners") and self._trim_linked_partners:
                for p_info in self._trim_linked_partners:
                    p_clip = p_info["clip"]
                    p_orig_in, p_orig_out = p_clip.get_media_source_bounds()
                    p_avail_head = max(0, p_info["start_src_in"] - p_orig_in)
                    min_delta = max(min_delta, -p_avail_head)
                    min_delta = max(min_delta, 1 - p_info["start_dst_in"])
                    if not (event.modifiers() & Qt.KeyboardModifier.ShiftModifier):
                        p_prev = [c for c in p_info["track"].clips if c.dst_out < p_info["start_dst_in"] and c.id != p_clip.id]
                        if p_prev:
                            p_closest = max(p_prev, key=lambda c: c.dst_out)
                            min_delta = max(min_delta, (p_closest.dst_out + 1) - p_info["start_dst_in"])
                    p_max_delta = p_info["start_dst_out"] - p_info["start_dst_in"]
                    max_delta = min(max_delta, p_max_delta)

            clamped_delta = max(min_delta, min(max_delta, delta_frames))

            new_in = self._drag_start_dst_in + clamped_delta
            delta_applied = new_in - self._drag_clip.dst_in
            self._drag_clip.dst_in = new_in
            self._drag_clip.src_in = self._drag_start_src_in + clamped_delta

            if hasattr(self, "_trim_linked_partners"):
                for p_info in self._trim_linked_partners:
                    p_clip = p_info["clip"]
                    p_clip.dst_in = p_info["start_dst_in"] + clamped_delta
                    p_clip.src_in = p_info["start_src_in"] + clamped_delta

            # Shift subsequent clips when ripple modifier (Shift) is held
            if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                for init_sc in getattr(self, "_trim_initial_subsequent", []):
                    sc = self._drag_track.get_clip_by_id(init_sc["id"])
                    if sc:
                        sc.dst_in = init_sc["old_dst_in"] + clamped_delta
                        sc.dst_out = init_sc["old_dst_out"] + clamped_delta
                if hasattr(self, "_trim_linked_partners"):
                    for p_info in self._trim_linked_partners:
                        for init_sc in p_info.get("initial_subsequent", []):
                            sc = p_info["track"].get_clip_by_id(init_sc["id"])
                            if sc:
                                sc.dst_in = init_sc["old_dst_in"] + clamped_delta
                                sc.dst_out = init_sc["old_dst_out"] + clamped_delta

            self._playhead_frame = new_in
            now = time.perf_counter()
            if (now - self._last_scrub_dispatch_time) >= 0.025:
                self._last_scrub_dispatch_time = now
                self._last_emitted_scrub_frame = new_in
                self.playhead_moved.emit(new_in)
                self.scrub_frame_requested.emit(new_in)
            else:
                self._scrub_settle_timer.start(35)
            self.update()
            return

        if self._drag_mode == "trim_out" and self._drag_clip and self._drag_track:
            delta_px = pos.x() - self._drag_start_mouse_pos.x()
            delta_frames = int(round(delta_px / self._zoom))

            # Leftward drag (negative delta) trims tail, leaving at least 1 frame
            min_delta = self._drag_start_dst_in - self._drag_start_dst_out

            # Rightward drag (positive delta) extends tail into available media frames
            orig_src_in, orig_src_out = self._drag_clip.get_media_source_bounds()
            avail_tail = max(0, orig_src_out - self._drag_start_src_out)
            max_delta = avail_tail

            # If not ripple (Shift not held), cannot overlap next clip on track
            if not (event.modifiers() & Qt.KeyboardModifier.ShiftModifier):
                next_clips = [c for c in self._drag_track.clips if c.dst_in > self._drag_start_dst_out and c.id != self._drag_clip.id]
                if next_clips:
                    closest_next = min(next_clips, key=lambda c: c.dst_in)
                    max_delta = min(max_delta, (closest_next.dst_in - 1) - self._drag_start_dst_out)

            # Synchronized safety clamping across linked partners
            if hasattr(self, "_trim_linked_partners") and self._trim_linked_partners:
                for p_info in self._trim_linked_partners:
                    p_clip = p_info["clip"]
                    _, p_orig_out = p_clip.get_media_source_bounds()
                    p_avail_tail = max(0, p_orig_out - p_info["start_src_out"])
                    max_delta = min(max_delta, p_avail_tail)
                    p_min_delta = p_info["start_dst_in"] - p_info["start_dst_out"]
                    min_delta = max(min_delta, p_min_delta)
                    if not (event.modifiers() & Qt.KeyboardModifier.ShiftModifier):
                        p_next = [c for c in p_info["track"].clips if c.dst_in > p_info["start_dst_out"] and c.id != p_clip.id]
                        if p_next:
                            p_closest = min(p_next, key=lambda c: c.dst_in)
                            max_delta = min(max_delta, (p_closest.dst_in - 1) - p_info["start_dst_out"])

            clamped_delta = max(min_delta, min(max_delta, delta_frames))

            new_out = self._drag_start_dst_out + clamped_delta
            delta_applied = new_out - self._drag_clip.dst_out
            self._drag_clip.dst_out = new_out
            self._drag_clip.src_out = self._drag_start_src_out + clamped_delta

            if hasattr(self, "_trim_linked_partners"):
                for p_info in self._trim_linked_partners:
                    p_clip = p_info["clip"]
                    p_clip.dst_out = p_info["start_dst_out"] + clamped_delta
                    p_clip.src_out = p_info["start_src_out"] + clamped_delta

            # Shift subsequent clips when ripple modifier (Shift) is held
            if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                for init_sc in getattr(self, "_trim_initial_subsequent", []):
                    sc = self._drag_track.get_clip_by_id(init_sc["id"])
                    if sc:
                        sc.dst_in = init_sc["old_dst_in"] + clamped_delta
                        sc.dst_out = init_sc["old_dst_out"] + clamped_delta
                if hasattr(self, "_trim_linked_partners"):
                    for p_info in self._trim_linked_partners:
                        for init_sc in p_info.get("initial_subsequent", []):
                            sc = p_info["track"].get_clip_by_id(init_sc["id"])
                            if sc:
                                sc.dst_in = init_sc["old_dst_in"] + clamped_delta
                                sc.dst_out = init_sc["old_dst_out"] + clamped_delta

            self._playhead_frame = new_out
            now = time.perf_counter()
            if (now - self._last_scrub_dispatch_time) >= 0.025:
                self._last_scrub_dispatch_time = now
                self._last_emitted_scrub_frame = new_out
                self.playhead_moved.emit(new_out)
                self.scrub_frame_requested.emit(new_out)
            else:
                self._scrub_settle_timer.start(35)
            self.update()
            return

        if self._drag_mode == "slide" and self._drag_clip and self._drag_track:
            delta_px = pos.x() - self._drag_start_mouse_pos.x()
            delta_frames = int(round(delta_px / self._zoom))
            dur = self._drag_clip.dst_duration

            # Calculate min/max delta
            min_delta = 1 - self._drag_start_dst_in
            max_delta = 999999

            prev_c = getattr(self, "_slide_prev_clip", None)
            next_c = getattr(self, "_slide_next_clip", None)

            if prev_c and getattr(self, "_slide_prev_start", None):
                p_dst_in, p_dst_out, p_src_in, p_src_out = self._slide_prev_start
                if p_dst_out == self._drag_start_dst_in - 1:
                    # Touching: roll edit cut
                    min_delta = max(min_delta, (p_dst_in + 1) - (p_dst_out + 1))
                else:
                    # Gap: can slide left until contacting prev_c
                    min_delta = max(min_delta, (p_dst_out + 1) - self._drag_start_dst_in)

            if next_c and getattr(self, "_slide_next_start", None):
                n_dst_in, n_dst_out, n_src_in, n_src_out = self._slide_next_start
                if n_dst_in == self._drag_start_dst_out + 1:
                    # Touching: roll edit cut
                    max_delta = min(max_delta, (n_dst_out - 1) - (n_dst_in - 1))
                else:
                    # Gap: can slide right until contacting next_c
                    max_delta = min(max_delta, (n_dst_in - 1) - self._drag_start_dst_out)

            clamped_delta = max(min_delta, min(max_delta, delta_frames))

            new_in = self._drag_start_dst_in + clamped_delta
            new_out = new_in + dur - 1
            self._drag_clip.dst_in = new_in
            self._drag_clip.dst_out = new_out

            if prev_c and getattr(self, "_slide_prev_start", None):
                p_dst_in, p_dst_out, p_src_in, p_src_out = self._slide_prev_start
                if p_dst_out == self._drag_start_dst_in - 1:
                    prev_c.dst_out = new_in - 1
                    prev_c.src_out = p_src_out + clamped_delta

            if next_c and getattr(self, "_slide_next_start", None):
                n_dst_in, n_dst_out, n_src_in, n_src_out = self._slide_next_start
                if n_dst_in == self._drag_start_dst_out + 1:
                    next_c.dst_in = new_out + 1
                    next_c.src_in = n_src_in + clamped_delta

            self._playhead_frame = new_in
            self.playhead_moved.emit(new_in)
            self.scrub_frame_requested.emit(new_in)
            self.sequence_changed.emit()
            self.update()
            return

        if self._drag_mode == "move" and self._drag_clip and self._drag_track:
            if not self._is_dragging and self._drag_start_mouse_pos:
                dist = (pos - self._drag_start_mouse_pos).manhattanLength()
                if dist > 3:
                    self._is_dragging = True

            if self._is_dragging:
                delta_px = pos.x() - self._drag_start_mouse_pos.x()
                delta_frames = int(round(delta_px / self._zoom))
                dur = self._drag_clip.dst_duration
                new_in = max(1, self._drag_start_dst_in + delta_frames)
                new_out = new_in + dur - 1

                # Magnetic Snapping during drag
                if self._snapping:
                    snap_dist = max(1, int(round(8 / self._zoom)))
                    if abs(new_in - self._playhead_frame) <= snap_dist:
                        new_in = self._playhead_frame
                        new_out = new_in + dur - 1
                    elif abs(new_out - self._playhead_frame) <= snap_dist:
                        new_out = self._playhead_frame
                        new_in = new_out - dur + 1
                    for t in self._sequence.tracks:
                        for c in t.clips:
                            if c.id == self._drag_clip.id:
                                continue
                            if abs(new_in - (c.dst_out + 1)) <= snap_dist:
                                new_in = c.dst_out + 1
                                new_out = new_in + dur - 1
                            elif abs(new_out - (c.dst_in - 1)) <= snap_dist:
                                new_out = c.dst_in - 1
                                new_in = new_out - dur + 1
                            elif abs(new_in - c.dst_in) <= snap_dist:
                                new_in = c.dst_in
                                new_out = new_in + dur - 1

                self._ghost_dst_in = new_in
                self._ghost_dst_out = new_out

                # Track destination check (Video to Video, Audio to Audio)
                y = pos.y() - self.RULER_HEIGHT
                t_idx = int(y // self.TRACK_HEIGHT)
                if 0 <= t_idx < len(self._sequence.tracks):
                    cand_t = self._sequence.tracks[t_idx]
                    if cand_t.track_type == self._drag_track.track_type:
                        self._ghost_track = cand_t
                    else:
                        self._ghost_track = self._drag_track
                else:
                    self._ghost_track = self._drag_track

                self.update()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if self._drag_mode == "move" and self._drag_clip and self._drag_track:
            if self._is_dragging:
                target_t = self._ghost_track or self._drag_track
                old_t = self._drag_track
                old_in = self._drag_start_dst_in
                old_out = getattr(self, "_drag_start_dst_out", old_in + self._drag_clip.dst_duration - 1)
                new_in = self._ghost_dst_in
                new_out = self._ghost_dst_out

                if old_t.id != target_t.id or old_in != new_in:
                    self._drag_clip.dst_in = new_in
                    self._drag_clip.dst_out = new_out
                    if target_t.id != old_t.id:
                        old_t.remove_clip(self._drag_clip.id)
                        target_t.add_clip(self._drag_clip)
                    else:
                        old_t.clips.sort(key=lambda c: c.dst_in)

                    cmd = ClipMoveCommand(
                        sequence=self._sequence,
                        clip=self._drag_clip,
                        old_track=old_t,
                        new_track=target_t,
                        old_dst_in=old_in,
                        old_dst_out=old_out,
                        new_dst_in=new_in,
                        new_dst_out=new_out,
                        on_change=lambda: (self.sequence_changed.emit(), self.update()),
                    )
                    UndoManager.instance().push(cmd)

                    delta_shift = new_in - old_in
                    if delta_shift != 0 and hasattr(self, "_move_linked_partners"):
                        for p_info in self._move_linked_partners:
                            p_track = p_info["track"]
                            p_clip = p_info["clip"]
                            p_old_in = p_info["start_dst_in"]
                            p_old_out = p_info["start_dst_out"]
                            p_new_in = p_old_in + delta_shift
                            p_new_out = p_old_out + delta_shift
                            p_clip.dst_in = p_new_in
                            p_clip.dst_out = p_new_out
                            p_track.clips.sort(key=lambda c: c.dst_in)
                            p_cmd = ClipMoveCommand(
                                sequence=self._sequence,
                                clip=p_clip,
                                old_track=p_track,
                                new_track=p_track,
                                old_dst_in=p_old_in,
                                old_dst_out=p_old_out,
                                new_dst_in=p_new_in,
                                new_dst_out=p_new_out,
                                on_change=lambda: (self.sequence_changed.emit(), self.update()),
                            )
                            UndoManager.instance().push(p_cmd)

                    self.clip_moved.emit(target_t.id, self._drag_clip.id, self._drag_clip.dst_in)
                    self.sequence_changed.emit()

        elif self._drag_mode in ("trim_in", "trim_out") and self._drag_clip and self._drag_track:
            old_in = self._drag_start_dst_in
            old_out = self._drag_start_dst_out
            old_sin = self._drag_start_src_in
            old_sout = self._drag_start_src_out
            new_in = self._drag_clip.dst_in
            new_out = self._drag_clip.dst_out
            new_sin = self._drag_clip.src_in
            new_sout = self._drag_clip.src_out

            shifted = []
            if hasattr(self, "_trim_initial_subsequent") and self._trim_initial_subsequent:
                for sc in self._trim_initial_subsequent:
                    c = self._drag_track.get_clip_by_id(sc["id"])
                    if c:
                        shifted.append({
                            "id": sc["id"],
                            "old_dst_in": sc["old_dst_in"],
                            "old_dst_out": sc["old_dst_out"],
                            "new_dst_in": c.dst_in,
                            "new_dst_out": c.dst_out,
                        })

            if old_in != new_in or old_out != new_out or old_sin != new_sin or old_sout != new_sout:
                cmd = ClipTrimCommand(
                    sequence=self._sequence,
                    track=self._drag_track,
                    clip=self._drag_clip,
                    old_dst_in=old_in,
                    old_dst_out=old_out,
                    old_src_in=old_sin,
                    old_src_out=old_sout,
                    new_dst_in=new_in,
                    new_dst_out=new_out,
                    new_src_in=new_sin,
                    new_src_out=new_sout,
                    shifted_clips_state=shifted,
                    on_change=lambda: (self.sequence_changed.emit(), self.update()),
                )
                UndoManager.instance().push(cmd)

            if hasattr(self, "_trim_linked_partners"):
                for p_info in self._trim_linked_partners:
                    p_clip = p_info["clip"]
                    p_track = p_info["track"]
                    p_shift = []
                    for sc in p_info.get("initial_subsequent", []):
                        c = p_track.get_clip_by_id(sc["id"])
                        if c:
                            p_shift.append({
                                "id": sc["id"],
                                "old_dst_in": sc["old_dst_in"],
                                "old_dst_out": sc["old_dst_out"],
                                "new_dst_in": c.dst_in,
                                "new_dst_out": c.dst_out,
                            })
                    if (p_clip.dst_in != p_info["start_dst_in"] or p_clip.dst_out != p_info["start_dst_out"] or
                        p_clip.src_in != p_info["start_src_in"] or p_clip.src_out != p_info["start_src_out"]):
                        p_cmd = ClipTrimCommand(
                            sequence=self._sequence,
                            track=p_track,
                            clip=p_clip,
                            old_dst_in=p_info["start_dst_in"],
                            old_dst_out=p_info["start_dst_out"],
                            old_src_in=p_info["start_src_in"],
                            old_src_out=p_info["start_src_out"],
                            new_dst_in=p_clip.dst_in,
                            new_dst_out=p_clip.dst_out,
                            new_src_in=p_clip.src_in,
                            new_src_out=p_clip.src_out,
                            shifted_clips_state=p_shift,
                            on_change=lambda: (self.sequence_changed.emit(), self.update()),
                        )
                        UndoManager.instance().push(p_cmd)
                    p_track.clips.sort(key=lambda c: c.dst_in)

            self._drag_track.clips.sort(key=lambda c: c.dst_in)
            self._trim_initial_subsequent = []
            self._trim_linked_partners = []
            self.sequence_changed.emit()

        elif self._drag_mode == "slide" and self._drag_clip and self._drag_track:
            prev_c = getattr(self, "_slide_prev_clip", None)
            next_c = getattr(self, "_slide_next_clip", None)
            adj_changes = []
            if prev_c and getattr(self, "_slide_prev_start", None):
                curr_p = (prev_c.dst_in, prev_c.dst_out, prev_c.src_in, prev_c.src_out)
                if curr_p != self._slide_prev_start:
                    adj_changes.append({"clip": prev_c, "old": self._slide_prev_start, "new": curr_p})
            if next_c and getattr(self, "_slide_next_start", None):
                curr_n = (next_c.dst_in, next_c.dst_out, next_c.src_in, next_c.src_out)
                if curr_n != self._slide_next_start:
                    adj_changes.append({"clip": next_c, "old": self._slide_next_start, "new": curr_n})

            if (self._drag_clip.dst_in != self._drag_start_dst_in or adj_changes):
                cmd = ClipSlideCommand(
                    clip=self._drag_clip,
                    old_dst_in=self._drag_start_dst_in,
                    old_dst_out=self._drag_start_dst_out,
                    new_dst_in=self._drag_clip.dst_in,
                    new_dst_out=self._drag_clip.dst_out,
                    adjacent_changes=adj_changes,
                    on_change=lambda: (self.sequence_changed.emit(), self.update()),
                )
                UndoManager.instance().push(cmd)

            self._slide_prev_clip = None
            self._slide_next_clip = None
            self._slide_prev_start = None
            self._slide_next_start = None
            self.sequence_changed.emit()

        elif self._drag_mode == "marquee":
            self._marquee_active = False
            self._marquee_rect = None
            self._marquee_start = None
            selected_clips = self.get_selected_clips()
            if selected_clips:
                t, c = selected_clips[0]
                self.clip_selected.emit(t.id, c.id)

        elif self._drag_mode == "scrub":
            self._scrub_settle_timer.stop()
            if self._last_emitted_scrub_frame != self._playhead_frame:
                self._last_emitted_scrub_frame = self._playhead_frame
                self.playhead_moved.emit(self._playhead_frame)
                self.scrub_frame_requested.emit(self._playhead_frame)
            self.scrub_finished.emit(self._playhead_frame)

        self._drag_mode = None
        self._drag_clip = None
        self._drag_track = None
        self._is_dragging = False
        self._ghost_track = None
        self.update()

    def wheelEvent(self, event: QWheelEvent) -> None:
        if event.modifiers() & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier):
            # Zoom centered on mouse cursor
            mouse_x = event.position().x()
            frame_at_mouse = self.x_to_frame(mouse_x, snap=False)
            delta = event.angleDelta().y()
            factor = 1.2 if delta > 0 else 0.833
            new_zoom = max(0.05, min(10.0, self._zoom * factor))
            start_frame = self._sequence.in_frame if self._sequence else 1
            new_scroll_x = int((frame_at_mouse - start_frame) * new_zoom - mouse_x)
            self.set_zoom(new_zoom, scroll_x=max(0, new_scroll_x))
        else:
            delta = event.angleDelta().x() or event.angleDelta().y()
            self._scroll_x = max(0, self._scroll_x - int(delta))
            self.scroll_changed.emit(self._scroll_x)
            self.update()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        key = event.key()
        if key in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            if self._selected_marker_frame is not None:
                self.delete_selected_marker()
                event.accept()
                return
            if self._selected_clip_ids:
                ripple = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
                self.remove_selected_clips(ripple=ripple)
                event.accept()
                return
            if getattr(self, "_selected_gap", None):
                trk_id, gap_info = self._selected_gap
                if gap_info:
                    all_trks = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
                    self.ripple_delete_gap(trk_id, gap_info[0], gap_info[1], all_tracks=all_trks)
                    self._selected_gap = None
                    event.accept()
                    return
        elif key == Qt.Key.Key_C:
            self._split_at_playhead()
            event.accept()
            return
        elif key == Qt.Key.Key_Q:
            self.ripple_trim_head_to_playhead()
            event.accept()
            return
        elif key == Qt.Key.Key_W:
            self.ripple_trim_tail_to_playhead()
            event.accept()
            return
        elif key == Qt.Key.Key_R and (event.modifiers() & Qt.KeyboardModifier.ControlModifier) and (event.modifiers() & Qt.KeyboardModifier.AltModifier):
            self.restore_selected_clips()
            event.accept()
            return
        elif key == Qt.Key.Key_L and (event.modifiers() & Qt.KeyboardModifier.ControlModifier):
            self.toggle_link_selected_clips()
            event.accept()
            return
        elif key in (Qt.Key.Key_Plus, Qt.Key.Key_Equal):
            self.zoom_in()
            event.accept()
            return
        elif key in (Qt.Key.Key_Minus, Qt.Key.Key_Underscore):
            self.zoom_out()
            event.accept()
            return
        elif key == Qt.Key.Key_I:
            self.mark_in()
            event.accept()
            return
        elif key == Qt.Key.Key_O:
            self.mark_out()
            event.accept()
            return
        elif key == Qt.Key.Key_M:
            self.add_marker_dialog()
            event.accept()
            return
        elif key == Qt.Key.Key_F2:
            self.rename_selected_clip_dialog()
            event.accept()
            return
        elif key == Qt.Key.Key_Space:
            self.toggle_playback_requested.emit()
            event.accept()
            return
        elif key == Qt.Key.Key_J:
            self.shuttle_reverse_requested.emit()
            event.accept()
            return
        elif key == Qt.Key.Key_K:
            self.pause_requested.emit()
            event.accept()
            return
        elif key == Qt.Key.Key_L:
            self.shuttle_forward_requested.emit()
            event.accept()
            return
        elif key == Qt.Key.Key_Left:
            self.step_backward_requested.emit(1)
            event.accept()
            return
        elif key == Qt.Key.Key_Right:
            self.step_forward_requested.emit(1)
            event.accept()
            return
        elif key == Qt.Key.Key_I:
            self.mark_in()
            event.accept()
            return
        elif key == Qt.Key.Key_O:
            self.mark_out()
            event.accept()
            return
        elif key == Qt.Key.Key_X and (event.modifiers() & (Qt.KeyboardModifier.AltModifier | Qt.KeyboardModifier.ControlModifier)):
            self.clear_in_out()
            event.accept()
            return
        elif key == Qt.Key.Key_M:
            self.add_marker_dialog()
            event.accept()
            return
        elif key in (Qt.Key.Key_Plus, Qt.Key.Key_Equal):
            self.zoom_in()
            event.accept()
            return
        elif key in (Qt.Key.Key_Minus, Qt.Key.Key_Underscore):
            self.zoom_out()
            event.accept()
            return
        elif key == Qt.Key.Key_F:
            self.zoom_to_fit()
            event.accept()
            return
        elif key == Qt.Key.Key_V:
            self.set_active_tool("select")
            event.accept()
            return
        elif key == Qt.Key.Key_U:
            self.set_active_tool("slide")
            event.accept()
            return
        elif key == Qt.Key.Key_C:
            self.set_active_tool("razor")
            event.accept()
            return
        elif key == Qt.Key.Key_S:
            self.set_snapping(not self._snapping)
            event.accept()
            return
        super().keyPressEvent(event)

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if event.mimeData().hasUrls() or event.mimeData().hasText() or event.mimeData().hasFormat("application/x-openreview-source"):
            event.setDropAction(Qt.DropAction.CopyAction)
            event.accept()
            self._drop_active = True
            self.update()

    def dragMoveEvent(self, event: QDragMoveEvent) -> None:
        pos = event.position()
        self._drop_frame = self.x_to_frame(pos.x(), snap=self._snapping)
        y = pos.y() - self.RULER_HEIGHT
        t_idx = int(y // self.TRACK_HEIGHT)
        if self._sequence and 0 <= t_idx < len(self._sequence.tracks):
            self._drop_track = self._sequence.tracks[t_idx]
        elif self._sequence and self._sequence.tracks:
            self._drop_track = self._sequence.tracks[0]
        else:
            self._drop_track = None
        self._drop_active = True
        self.update()
        event.setDropAction(Qt.DropAction.CopyAction)
        event.accept()

    def dragLeaveEvent(self, event) -> None:
        self._drop_active = False
        self.update()

    def dropEvent(self, event: QDropEvent) -> None:
        self._drop_active = False
        event.setDropAction(Qt.DropAction.CopyAction)
        event.accept()
        if not self._sequence:
            return

        pos = event.position()
        drop_frame = self.x_to_frame(pos.x(), snap=self._snapping)
        y = pos.y() - self.RULER_HEIGHT
        t_idx = int(y // self.TRACK_HEIGHT)
        target_track = None
        if 0 <= t_idx < len(self._sequence.tracks):
            target_track = self._sequence.tracks[t_idx]

        file_paths = []
        if event.mimeData().hasUrls():
            for u in event.mimeData().urls():
                lp = u.toLocalFile()
                if lp:
                    file_paths.append(lp)
        elif event.mimeData().hasText():
            txt = event.mimeData().text().strip()
            for line in txt.splitlines():
                if os.path.exists(line.strip()):
                    file_paths.append(line.strip())

        if not file_paths:
            return

        AUDIO_EXTS = {".wav", ".mp3", ".aac", ".flac", ".ogg", ".aif", ".aiff", ".m4a"}
        curr_frame = drop_frame

        from openreview.core.video_reader import is_video_file, VideoMediaReader
        from openreview.core.sequence_parser import detect_image_sequence

        for path in file_paths:
            _, ext = os.path.splitext(path.lower())
            is_audio = ext in AUDIO_EXTS
            track = target_track
            clip_name = os.path.splitext(os.path.basename(path))[0]
            clip_path = path

            if is_audio:
                if not track or track.track_type != TrackType.AUDIO:
                    audio_tracks = [t for t in self._sequence.tracks if t.track_type == TrackType.AUDIO]
                    track = audio_tracks[0] if audio_tracks else self._sequence.add_audio_track()
                src_in = 1
                dur = 120
                try:
                    import soundfile as sf
                    info = sf.info(path)
                    fps = self._sequence.fps if self._sequence else 24.0
                    dur = max(1, int(round(info.duration * fps)))
                except Exception:
                    pass
                src_out = src_in + dur - 1
            else:
                if not track or track.track_type != TrackType.VIDEO:
                    video_tracks = [t for t in self._sequence.tracks if t.track_type == TrackType.VIDEO]
                    track = video_tracks[0] if video_tracks else self._sequence.add_video_track()

                src_in = 1
                dur = 48
                if is_video_file(path):
                    try:
                        vr = VideoMediaReader(path)
                        dur = max(1, vr.total_frames)
                        src_in = 1
                        src_out = dur
                        vr.close()
                    except Exception:
                        src_out = src_in + dur - 1
                else:
                    seq_info = detect_image_sequence(path)
                    if seq_info and len(seq_info.available_frames) > 1:
                        clip_name = seq_info.prefix.rstrip("._- ") or clip_name
                        clip_path = f"{seq_info.prefix}{seq_info.separator}####{seq_info.extension}"
                        src_in = seq_info.start_frame
                        src_out = seq_info.end_frame
                        dur = max(1, seq_info.end_frame - seq_info.start_frame + 1)
                    else:
                        src_out = src_in + dur - 1

            from uuid import uuid4
            has_embedded_audio = not is_audio and file_has_audio(path)
            link_id = f"link_{uuid4().hex[:8]}" if has_embedded_audio else None

            new_clip = ClipItem(
                id=f"clip_{len(track.clips) + 1}_{curr_frame}",
                name=clip_name,
                file_path=clip_path,
                src_in=src_in,
                src_out=src_out,
                dst_in=curr_frame,
                dst_out=curr_frame + dur - 1,
                link_id=link_id,
                metadata={
                    "original_src_in": src_in,
                    "original_src_out": src_out,
                    "media_duration": dur,
                },
            )
            track.add_clip(new_clip)
            cmd = ClipAddCommand(
                track=track,
                clip=new_clip,
                on_change=lambda: (self.sequence_changed.emit(), self.update()),
            )
            UndoManager.instance().push(cmd)
            self._selected_clip_ids = {new_clip.id}

            if has_embedded_audio:
                audio_tracks = [t for t in self._sequence.tracks if t.track_type == TrackType.AUDIO]
                audio_track = audio_tracks[0] if audio_tracks else self._sequence.add_audio_track()
                audio_clip = ClipItem(
                    id=f"clip_a_{len(audio_track.clips) + 1}_{curr_frame}",
                    name=clip_name,
                    file_path=clip_path,
                    src_in=src_in,
                    src_out=src_out,
                    dst_in=curr_frame,
                    dst_out=curr_frame + dur - 1,
                    link_id=link_id,
                    metadata={
                        "original_src_in": src_in,
                        "original_src_out": src_out,
                        "media_duration": dur,
                    },
                )
                audio_track.add_clip(audio_clip)
                a_cmd = ClipAddCommand(
                    track=audio_track,
                    clip=audio_clip,
                    on_change=lambda: (self.sequence_changed.emit(), self.update()),
                )
                UndoManager.instance().push(a_cmd)
                self._selected_clip_ids.add(audio_clip.id)
                self.clip_dropped.emit(audio_track.id, audio_clip.id)

            self.clip_dropped.emit(track.id, new_clip.id)
            curr_frame += dur

        self.sequence_changed.emit()
        self.update()
        event.acceptProposedAction()

    def _clear_all_markers(self) -> None:
        """Clear all review markers in the active sequence."""
        if self._sequence and self._sequence.markers:
            self._sequence.markers.clear()
            self._selected_marker_frame = None
            self.sequence_changed.emit()
            self.update()

    def _show_context_menu(self, global_pos, local_pos) -> None:
        if not self._sequence:
            return

        frame = self.x_to_frame(local_pos.x(), snap=False)
        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu {
                background-color: #202020;
                color: #dddddd;
                border: 1px solid #333333;
            }
            QMenu::item:selected {
                background-color: #e5a823;
                color: #000000;
            }
        """)

        # Context Menu on Ruler
        if local_pos.y() <= self.RULER_HEIGHT:
            marker_hit = None
            if self._sequence.markers:
                for m in self._sequence.markers:
                    mx = self.frame_to_x(m.frame)
                    if abs(local_pos.x() - mx) <= 7.0:
                        marker_hit = m
                        break

            if marker_hit:
                self._selected_marker_frame = marker_hit.frame
                self.update()
                act_edit = menu.addAction(get_svg_icon("marker", color=marker_hit.color, size=13), f"Edit Marker '{marker_hit.name}'...")
                act_edit.triggered.connect(lambda: self.edit_marker_dialog(marker_hit.frame))
                act_del = menu.addAction(get_svg_icon("delete", color="#e74c3c", size=13), f"Delete Marker @ {marker_hit.frame}")
                act_del.triggered.connect(self.delete_selected_marker)
            else:
                act_marker = menu.addAction(get_svg_icon("marker", color="#f1c40f", size=13), f"Add Review Marker @ {frame} (M)")
                act_marker.triggered.connect(lambda: self.add_marker_dialog(frame))

            if self._sequence.markers:
                menu.addSeparator()
                act_clear_all = menu.addAction("Clear All Review Markers")
                act_clear_all.triggered.connect(self._clear_all_markers)

            menu.addSeparator()
            act_fit = menu.addAction("Zoom to Fit (F)")
            act_fit.triggered.connect(self.zoom_to_fit)
            menu.exec(global_pos)
            return

        # Context Menu in Tracks Area
        y = local_pos.y() - self.RULER_HEIGHT
        t_idx = int(y // self.TRACK_HEIGHT)
        target_clip = None
        target_track = None
        if 0 <= t_idx < len(self._sequence.tracks):
            target_track = self._sequence.tracks[t_idx]
            target_clip = target_track.get_clip_at_frame(frame)

        if target_clip and target_track:
            if target_clip.id not in self._selected_clip_ids:
                self._selected_clip_ids = {target_clip.id}
                self._selected_marker_frame = None
                self.clip_selected.emit(target_track.id, target_clip.id)
                self.update()

        # Ripple Delete Gap Context Menu (Right clicking empty space before or between clips)
        if target_clip is None and target_track is not None:
            gap_info = self._get_gap_at_frame(target_track, frame)
            if gap_info:
                gap_start, gap_end = gap_info
                gap_dur = gap_end - gap_start + 1
                act_gap = menu.addAction(
                    get_svg_icon("delete", color="#e74c3c", size=13),
                    f"Ripple Delete Gap ({gap_dur} frames @ {gap_start}-{gap_end})"
                )
                act_gap.triggered.connect(lambda: self.ripple_delete_gap(target_track.id, gap_start, gap_end, all_tracks=False))

                act_gap_all = menu.addAction(
                    f"Ripple Delete Gap Across All Tracks ({gap_dur} frames)"
                )
                act_gap_all.triggered.connect(lambda: self.ripple_delete_gap(target_track.id, gap_start, gap_end, all_tracks=True))
                menu.addSeparator()

        sel_clips = self.get_selected_clips()

        # Export & Rename Actions
        if len(sel_clips) == 1:
            trk, clp = sel_clips[0]
            act_export = menu.addAction(get_svg_icon("export", color="#38d9c6", size=13), f"Export Clip '{clp.name}'...")
            act_export.triggered.connect(self.export_selected_clips_dialog)

            act_ren = menu.addAction(f"Rename Clip '{clp.name}'... (F2)")
            act_ren.triggered.connect(self.rename_selected_clip_dialog)

            act_batch_ren = menu.addAction("Batch Rename Clips...")
            act_batch_ren.triggered.connect(self.batch_rename_dialog)
            menu.addSeparator()

        elif len(sel_clips) > 1:
            act_export = menu.addAction(get_svg_icon("export", color="#38d9c6", size=13), f"Export Selected Clips ({len(sel_clips)} items)...")
            act_export.triggered.connect(self.export_selected_clips_dialog)

            act_export_ind = menu.addAction("Export Clips Individually...")
            act_export_ind.triggered.connect(self.export_selected_clips_dialog)

            act_batch_ren = menu.addAction(f"Batch Rename Selected Clips ({len(sel_clips)} items)... (F2)")
            act_batch_ren.triggered.connect(self.batch_rename_dialog)
            menu.addSeparator()

        else:
            act_export_all = menu.addAction(get_svg_icon("export", color="#38d9c6", size=13), "Export All Clips in Sequence...")
            act_export_all.triggered.connect(self.export_selected_clips_dialog)

            act_batch_all = menu.addAction("Batch Rename All Clips...")
            act_batch_all.triggered.connect(self.batch_rename_dialog)
            menu.addSeparator()

        # Add Marker Action
        act_marker = menu.addAction(get_svg_icon("marker", color="#f1c40f", size=13), f"Add Review Marker @ {frame} (M)")
        act_marker.triggered.connect(lambda: self.add_marker_dialog(frame))

        menu.addSeparator()

        # Clip Editing Actions
        if sel_clips:
            act_restore = menu.addAction(
                get_svg_icon("restore", color="#2ecc71", size=13),
                f"Restore Clip{'s' if len(sel_clips) > 1 else ''} to Full Length\tCtrl+Alt+R"
            )
            act_restore.triggered.connect(self.restore_selected_clips)

            # Link / Unlink Clips
            has_linked = any(c.link_id for _, c in sel_clips)
            if has_linked:
                act_unlink = menu.addAction(
                    get_svg_icon("unlink", color="#f39c12", size=13),
                    "Unlink Audio and Video\tCtrl+L",
                )
                act_unlink.triggered.connect(self.unlink_selected_clips)
            elif len(sel_clips) >= 2:
                act_link = menu.addAction(
                    get_svg_icon("link", color="#38d9c6", size=13),
                    "Link Selected Clips\tCtrl+L",
                )
                act_link.triggered.connect(self.link_selected_clips)

            act_del = menu.addAction(get_svg_icon("delete", color="#e74c3c", size=13), "Delete Selected Clip(s) (Lift)")
            act_del.setShortcut("Delete")
            act_del.triggered.connect(lambda: self.remove_selected_clips(ripple=False))

            act_rip = menu.addAction(get_svg_icon("delete", color="#f39c12", size=13), "Ripple Delete (Close Gap)")
            act_rip.setShortcut("Shift+Delete")
            act_rip.triggered.connect(lambda: self.remove_selected_clips(ripple=True))

            if target_track:
                menu.addSeparator()
                act_cut = menu.addAction(get_svg_icon("razor", color="#e5a823", size=13), "Razor Cut at Cursor (C)")
                act_cut.triggered.connect(lambda: self.cut_requested.emit(target_track.id, frame))

        menu.addSeparator()
        act_in = menu.addAction("Mark In Point (I)")
        act_in.triggered.connect(self.mark_in)
        act_out = menu.addAction("Mark Out Point (O)")
        act_out.triggered.connect(self.mark_out)
        act_clear_io = menu.addAction("Clear In/Out Range (Alt+X)")
        act_clear_io.triggered.connect(self.clear_in_out)

        menu.addSeparator()
        act_fit = menu.addAction("Zoom to Fit (F)")
        act_fit.triggered.connect(self.zoom_to_fit)

        menu.exec(global_pos)


class MultiTrackTimelineWidget(QWidget):
    """
    Complete Multi-Track Editorial Timeline Widget.
    Combines track headers, interactive canvas, ruler, navigation, zoom slider,
    horizontal scrollbar, In/Out range controls, timecode direct jump box, and editorial tools.
    """
    playhead_changed = Signal(int)
    clip_selected = Signal(str, str)         # track_id, clip_id
    clip_moved = Signal(str, str, int)       # target_track_id, clip_id, new_dst_in
    clip_deleted = Signal(str, str, bool)    # track_id, clip_id, ripple
    clip_dropped = Signal(str, str)          # track_id, clip_id
    scrub_frame_requested = Signal(int)
    scrub_started = Signal()
    scrub_finished = Signal(int)
    in_out_changed = Signal(object, object)  # in_mark, out_mark
    sequence_changed = Signal()
    toggle_playback_requested = Signal()
    shuttle_reverse_requested = Signal()
    pause_requested = Signal()
    shuttle_forward_requested = Signal()
    step_backward_requested = Signal(int)
    step_forward_requested = Signal(int)
    settings_requested = Signal()

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._sequence: Optional[TimelineSequence] = None
        self._display_timecode: bool = True

        self._setup_ui()

    def _setup_ui(self) -> None:
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # Top Control Toolbar
        tb_frame = QFrame(self)
        tb_frame.setFixedHeight(28)
        tb_frame.setStyleSheet("""
            QFrame {
                background-color: #1a1a1a;
                border-bottom: 1px solid #282828;
            }
            QToolButton {
                background-color: transparent;
                color: #aaaaaa;
                border: 1px solid transparent;
                border-radius: 2px;
                padding: 2px 4px;
                min-width: 22px;
                min-height: 20px;
            }
            QToolButton:hover {
                background-color: #2b2b2b;
                color: #ffffff;
            }
            QToolButton:checked {
                background-color: #e5a823;
                color: #000000;
                font-weight: bold;
            }
            QSlider::groove:horizontal {
                height: 4px;
                background: #282828;
                border-radius: 2px;
            }
            QSlider::sub-page:horizontal {
                background: #e5a823;
                border-radius: 2px;
            }
            QSlider::handle:horizontal {
                background: #cccccc;
                width: 10px;
                margin-top: -3px;
                margin-bottom: -3px;
                border-radius: 5px;
            }
            QSlider::handle:horizontal:hover {
                background: #ffffff;
            }
        """)
        tb_layout = QHBoxLayout(tb_frame)
        tb_layout.setContentsMargins(6, 2, 6, 2)
        tb_layout.setSpacing(4)

        # Timecode Display & Direct Jump Box
        self.btn_tc_mode = QToolButton(tb_frame)
        self.btn_tc_mode.setText("TC")
        self.btn_tc_mode.setToolTip("Toggle Timecode (TC) / Frame Count (FR) display")
        self.btn_tc_mode.setStyleSheet("font-size: 9.5px; font-weight: bold; min-width: 26px; color: #e5a823; border: 1px solid #333333; border-radius: 2px;")
        self.btn_tc_mode.clicked.connect(self._toggle_tc_mode)
        tb_layout.addWidget(self.btn_tc_mode)

        self.timecode_edit = TimecodeLineEdit(tb_frame)
        self.timecode_edit.setFixedWidth(88)
        self.timecode_edit.setFixedHeight(20)
        self.timecode_edit.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.timecode_edit.setToolTip("Current Playhead Timecode / Frame. Enter numbers (HHMMSSFF) to jump; automatically formats colons.")
        self.timecode_edit.setStyleSheet("""
            QLineEdit {
                background-color: #121212;
                color: #e5a823;
                border: 1px solid #333333;
                border-radius: 2px;
                font-family: 'Consolas', 'Courier New', monospace;
                font-size: 11px;
                font-weight: bold;
                padding: 1px 3px;
            }
            QLineEdit:focus {
                border-color: #f5b833;
                background-color: #1a1a1a;
            }
        """)
        self.timecode_edit.returnPressed.connect(self._on_timecode_jump)
        tb_layout.addWidget(self.timecode_edit)

        # NLE Transport Controls (Prev Cut, Play/Pause, Next Cut)
        self.btn_prev_cut = QToolButton(tb_frame)
        self.btn_prev_cut.setIcon(get_svg_icon("prev_cut", color="#cccccc", size=13))
        self.btn_prev_cut.setToolTip("Previous Cut Boundary (A / Up)")
        self.btn_prev_cut.clicked.connect(self._jump_prev_cut)
        tb_layout.addWidget(self.btn_prev_cut)

        self.btn_play_pause = QToolButton(tb_frame)
        self.btn_play_pause.setIcon(get_svg_icon("play", color="#e5a823", size=13))
        self.btn_play_pause.setToolTip("Play / Pause Timeline Sequence (Space)")
        self.btn_play_pause.clicked.connect(self.toggle_playback_requested.emit)
        tb_layout.addWidget(self.btn_play_pause)

        self.btn_next_cut = QToolButton(tb_frame)
        self.btn_next_cut.setIcon(get_svg_icon("next_cut", color="#cccccc", size=13))
        self.btn_next_cut.setToolTip("Next Cut Boundary (S / Down)")
        self.btn_next_cut.clicked.connect(self._jump_next_cut)
        tb_layout.addWidget(self.btn_next_cut)

        sep0 = QFrame(tb_frame)
        sep0.setFrameShape(QFrame.Shape.VLine)
        sep0.setFrameShadow(QFrame.Shadow.Sunken)
        sep0.setStyleSheet("background-color: #333333; margin: 2px 4px;")
        tb_layout.addWidget(sep0)

        # 1. Tools: Select, Slide, Razor, Snap
        self.btn_select = QToolButton(tb_frame)
        self.btn_select.setIcon(get_svg_icon("select", color="#dddddd", size=14))
        self.btn_select.setToolTip("Select & Trim Tool (V) - Drag clips or hover edge to trim")
        self.btn_select.setCheckable(True)
        self.btn_select.setChecked(True)
        self.btn_select.clicked.connect(lambda: self._set_tool("select"))
        tb_layout.addWidget(self.btn_select)

        self.btn_slide = QToolButton(tb_frame)
        self.btn_slide.setIcon(get_svg_icon("slide", color="#dddddd", size=14))
        self.btn_slide.setToolTip("Slide Tool (U) - Slide clip position while adjusting boundary cuts")
        self.btn_slide.setCheckable(True)
        self.btn_slide.clicked.connect(lambda: self._set_tool("slide"))
        tb_layout.addWidget(self.btn_slide)

        self.btn_razor = QToolButton(tb_frame)
        self.btn_razor.setIcon(get_svg_icon("razor", color="#dddddd", size=14))
        self.btn_razor.setToolTip("Razor Blade Tool (C) - Split clip at edit point")
        self.btn_razor.setCheckable(True)
        self.btn_razor.clicked.connect(lambda: self._set_tool("razor"))
        tb_layout.addWidget(self.btn_razor)

        self.btn_snap = QToolButton(tb_frame)
        self.btn_snap.setIcon(get_svg_icon("snap", color="#dddddd", size=14))
        self.btn_snap.setToolTip("Snap to Edits (S) - Magnetic snapping to cuts & playhead")
        self.btn_snap.setCheckable(True)
        self.btn_snap.setChecked(True)
        self.btn_snap.toggled.connect(self._on_snap_toggled)
        tb_layout.addWidget(self.btn_snap)

        # Review Marker Tool
        self.btn_marker = QToolButton(tb_frame)
        self.btn_marker.setIcon(get_svg_icon("marker", color="#f1c40f", size=14))
        self.btn_marker.setToolTip("Add Review Marker (M) - Attach QC notes and status tags to frame")
        self.btn_marker.clicked.connect(lambda: self.canvas.add_marker_dialog())
        tb_layout.addWidget(self.btn_marker)

        # Delete Button
        self.btn_delete = QToolButton(tb_frame)
        self.btn_delete.setIcon(get_svg_icon("delete", color="#e74c3c", size=14))
        self.btn_delete.setToolTip("Delete Selected Clip (Delete: Lift, Shift+Delete: Ripple)")
        self.btn_delete.clicked.connect(lambda: self.canvas.remove_selected_clips(ripple=False))
        tb_layout.addWidget(self.btn_delete)

        # Separator line
        sep = QFrame(tb_frame)
        sep.setFrameShape(QFrame.Shape.VLine)
        sep.setFrameShadow(QFrame.Shadow.Sunken)
        sep.setStyleSheet("background-color: #333333; margin: 2px 4px;")
        tb_layout.addWidget(sep)

        # In / Out Range Controls
        self.btn_mark_in = QToolButton(tb_frame)
        self.btn_mark_in.setText("[ In")
        self.btn_mark_in.setToolTip("Mark Sequence In Point (I)")
        self.btn_mark_in.setStyleSheet("font-size: 10px; font-weight: bold; min-width: 32px;")
        self.btn_mark_in.clicked.connect(lambda: self.canvas.mark_in())
        tb_layout.addWidget(self.btn_mark_in)

        self.btn_mark_out = QToolButton(tb_frame)
        self.btn_mark_out.setText("Out ]")
        self.btn_mark_out.setToolTip("Mark Sequence Out Point (O)")
        self.btn_mark_out.setStyleSheet("font-size: 10px; font-weight: bold; min-width: 32px;")
        self.btn_mark_out.clicked.connect(lambda: self.canvas.mark_out())
        tb_layout.addWidget(self.btn_mark_out)

        self.btn_clear_io = QToolButton(tb_frame)
        self.btn_clear_io.setText("✕ I/O")
        self.btn_clear_io.setToolTip("Clear In/Out Range (Alt+X)")
        self.btn_clear_io.setStyleSheet("font-size: 9.5px; color: #888888; min-width: 34px;")
        self.btn_clear_io.clicked.connect(lambda: self.canvas.clear_in_out())
        tb_layout.addWidget(self.btn_clear_io)

        # Separator line
        sep2 = QFrame(tb_frame)
        sep2.setFrameShape(QFrame.Shape.VLine)
        sep2.setFrameShadow(QFrame.Shadow.Sunken)
        sep2.setStyleSheet("background-color: #333333; margin: 2px 4px;")
        tb_layout.addWidget(sep2)

        # Zoom Controls
        lbl_zoom = QLabel("Zoom:", tb_frame)
        lbl_zoom.setStyleSheet("color: #777777; font-size: 10px;")
        tb_layout.addWidget(lbl_zoom)

        self.zoom_slider = QSlider(Qt.Orientation.Horizontal, tb_frame)
        self.zoom_slider.setRange(5, 500)
        self.zoom_slider.setValue(100)
        self.zoom_slider.setFixedWidth(85)
        self.zoom_slider.setToolTip("Timeline Horizontal Zoom Slider")
        self.zoom_slider.valueChanged.connect(self._on_zoom_slider_changed)
        tb_layout.addWidget(self.zoom_slider)

        self.btn_zoom_fit = QToolButton(tb_frame)
        self.btn_zoom_fit.setText("Fit (F)")
        self.btn_zoom_fit.setToolTip("Zoom to fit full sequence (F / Shift+Z)")
        self.btn_zoom_fit.clicked.connect(lambda: self.canvas.zoom_to_fit())
        tb_layout.addWidget(self.btn_zoom_fit)

        tb_layout.addStretch()

        # Add Track Buttons
        self.btn_add_v = QPushButton("+ Video Track", tb_frame)
        self.btn_add_v.setFixedHeight(20)
        self.btn_add_v.setStyleSheet("font-size: 10px; padding: 2px 6px;")
        self.btn_add_v.clicked.connect(self._on_add_video_track)
        tb_layout.addWidget(self.btn_add_v)

        self.btn_add_a = QPushButton("+ Audio Track", tb_frame)
        self.btn_add_a.setFixedHeight(20)
        self.btn_add_a.setStyleSheet("font-size: 10px; padding: 2px 6px;")
        self.btn_add_a.clicked.connect(self._on_add_audio_track)
        tb_layout.addWidget(self.btn_add_a)

        # Sequence / Project Settings Button
        self.btn_settings = QToolButton(tb_frame)
        self.btn_settings.setIcon(get_svg_icon("settings", color="#e5a823", size=13))
        self.btn_settings.setToolTip("Sequence / Project Settings (Resolution, Frame Rate, Colorspace)")
        self.btn_settings.clicked.connect(self.settings_requested.emit)
        tb_layout.addWidget(self.btn_settings)

        main_layout.addWidget(tb_frame)

        # Middle Content: Splitter between Track Headers (Left) and Canvas (Right)
        self.splitter = QSplitter(Qt.Orientation.Horizontal, self)

        # Left Container: Track Headers
        self.headers_container = QWidget(self.splitter)
        self.headers_layout = QVBoxLayout(self.headers_container)
        self.headers_layout.setContentsMargins(0, 26, 0, 0)
        self.headers_layout.setSpacing(0)
        self.headers_layout.addStretch()
        self.splitter.addWidget(self.headers_container)

        # Right Container: Timeline Canvas
        self.canvas = TimelineCanvas(self.splitter)
        self.canvas.playhead_moved.connect(self.playhead_changed.emit)
        self.canvas.playhead_moved.connect(lambda _: self._sync_timecode_display())
        self.canvas.clip_selected.connect(self.clip_selected.emit)
        self.canvas.clip_moved.connect(self.clip_moved.emit)
        self.canvas.clip_deleted.connect(self.clip_deleted.emit)
        self.canvas.clip_dropped.connect(self.clip_dropped.emit)
        self.canvas.cut_requested.connect(self._on_razor_cut)
        self.canvas.scrub_frame_requested.connect(self.scrub_frame_requested.emit)
        self.canvas.scrub_started.connect(self.scrub_started.emit)
        self.canvas.scrub_finished.connect(self.scrub_finished.emit)
        self.canvas.zoom_changed.connect(self._sync_zoom_ui)
        self.canvas.scroll_changed.connect(self._sync_scrollbar)
        self.canvas.in_out_changed.connect(self.in_out_changed.emit)
        self.canvas.sequence_changed.connect(self.sequence_changed.emit)
        self.canvas.toggle_playback_requested.connect(self.toggle_playback_requested.emit)
        self.canvas.shuttle_reverse_requested.connect(self.shuttle_reverse_requested.emit)
        self.canvas.pause_requested.connect(self.pause_requested.emit)
        self.canvas.shuttle_forward_requested.connect(self.shuttle_forward_requested.emit)
        self.canvas.step_backward_requested.connect(self.step_backward_requested.emit)
        self.canvas.step_forward_requested.connect(self.step_forward_requested.emit)
        self.splitter.addWidget(self.canvas)
        self.timecode_edit.set_canvas(self.canvas)

        self.splitter.setSizes([165, 800])
        main_layout.addWidget(self.splitter, 1)

        # Bottom Horizontal Scrollbar
        self.scrollbar = QScrollBar(Qt.Orientation.Horizontal, self)
        self.scrollbar.setFixedHeight(12)
        self.scrollbar.setStyleSheet("""
            QScrollBar:horizontal {
                height: 12px;
                background: #141414;
                border-top: 1px solid #222222;
            }
            QScrollBar::handle:horizontal {
                background: #2d2d2d;
                min-width: 30px;
                border-radius: 3px;
                margin: 2px;
            }
            QScrollBar::handle:horizontal:hover {
                background: #444444;
            }
            QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
                width: 0px;
            }
        """)
        self.scrollbar.valueChanged.connect(self._on_scrollbar_value_changed)
        main_layout.addWidget(self.scrollbar)

        # Global Timeline Key Shortcuts (C to split clip, Ctrl+K to split)
        from PySide6.QtGui import QKeySequence, QShortcut
        self._shortcut_split_c = QShortcut(QKeySequence("C"), self, lambda: self.canvas._split_at_playhead())
        self._shortcut_split_k = QShortcut(QKeySequence("Ctrl+K"), self, lambda: self.canvas._split_at_playhead())

    def keyPressEvent(self, event: QKeyEvent) -> None:
        key = event.key()
        if key in (Qt.Key.Key_Plus, Qt.Key.Key_Equal):
            self.canvas.zoom_in()
            event.accept()
            return
        elif key in (Qt.Key.Key_Minus, Qt.Key.Key_Underscore):
            self.canvas.zoom_out()
            event.accept()
            return
        elif key == Qt.Key.Key_I:
            self.canvas.mark_in()
            event.accept()
            return
        elif key == Qt.Key.Key_O:
            self.canvas.mark_out()
            event.accept()
            return
        elif key == Qt.Key.Key_M:
            self.canvas.add_marker_dialog()
            event.accept()
            return
        elif key == Qt.Key.Key_U:
            self._set_tool("slide")
            event.accept()
            return
        elif key == Qt.Key.Key_V:
            self._set_tool("select")
            event.accept()
            return
        elif key in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            self.canvas.keyPressEvent(event)
            return
        elif key == Qt.Key.Key_C:
            self.canvas._split_at_playhead()
            event.accept()
            return
        elif key == Qt.Key.Key_Q:
            self.canvas.ripple_trim_head_to_playhead()
            event.accept()
            return
        elif key == Qt.Key.Key_W:
            self.canvas.ripple_trim_tail_to_playhead()
            event.accept()
            return
        elif key == Qt.Key.Key_R and (event.modifiers() & Qt.KeyboardModifier.ControlModifier) and (event.modifiers() & Qt.KeyboardModifier.AltModifier):
            self.canvas.restore_selected_clips()
            event.accept()
            return
        super().keyPressEvent(event)

    def _jump_prev_cut(self) -> None:
        """Jump playhead to previous cut boundary (A / Up)."""
        if not self._sequence:
            return
        cur_f = self.canvas._playhead_frame
        cut_points = set()
        cut_points.add(self._sequence.in_frame)
        for t in self._sequence.tracks:
            for c in t.clips:
                cut_points.add(c.dst_in)
                cut_points.add(c.dst_out + 1)
        prev_cuts = sorted([f for f in cut_points if f < cur_f])
        if prev_cuts:
            target = prev_cuts[-1]
            self.set_playhead_frame(target)
            self.playhead_changed.emit(target)
            self.scrub_frame_requested.emit(target)

    def _jump_next_cut(self) -> None:
        """Jump playhead to next cut boundary (S / Down)."""
        if not self._sequence:
            return
        cur_f = self.canvas._playhead_frame
        cut_points = set()
        for t in self._sequence.tracks:
            for c in t.clips:
                cut_points.add(c.dst_in)
                cut_points.add(c.dst_out + 1)
        next_cuts = sorted([f for f in cut_points if f > cur_f])
        if next_cuts:
            target = next_cuts[0]
            self.set_playhead_frame(target)
            self.playhead_changed.emit(target)
            self.scrub_frame_requested.emit(target)

    def ripple_delete_gap(self, track_id: str, gap_start: int, gap_end: int, all_tracks: bool = False) -> None:
        """Forward ripple delete gap request to canvas."""
        self.canvas.ripple_delete_gap(track_id, gap_start, gap_end, all_tracks=all_tracks)

    def set_sequence(self, sequence: Optional[TimelineSequence]) -> None:
        self._sequence = sequence
        self.canvas.set_sequence(sequence)
        self._rebuild_headers()
        self._sync_scrollbar()
        self._sync_timecode_display()

    def set_playhead_frame(self, frame: int) -> None:
        self.canvas.set_playhead_frame(frame)
        self._sync_timecode_display()

    def _toggle_tc_mode(self) -> None:
        self._display_timecode = not self._display_timecode
        self.canvas._display_timecode = self._display_timecode
        self.btn_tc_mode.setText("TC" if self._display_timecode else "FR")
        if hasattr(self, "timecode_edit"):
            self.timecode_edit.clearFocus()
            self.timecode_edit.set_timecode_mode(self._display_timecode)
        self._sync_timecode_display(force=True)
        self.canvas.update()

    def _sync_timecode_display(self, force: bool = False) -> None:
        if not hasattr(self, "timecode_edit"):
            return
        if self.timecode_edit.hasFocus() and not force:
            return
        is_tc = getattr(self, "_display_timecode", True)
        self.timecode_edit.set_timecode_mode(is_tc)
        f = self.canvas._playhead_frame
        fps = self._sequence.fps if self._sequence else 24.0
        seq_in = self._sequence.in_frame if self._sequence else 1
        start_tc = self._sequence.start_timecode if self._sequence and self._sequence.start_timecode else "00:00:00:00"
        if is_tc:
            rel_f = f - seq_in
            self.timecode_edit.setText(frame_to_timecode(rel_f, fps, start_timecode=start_tc))
        else:
            self.timecode_edit.setText(str(f))
            self.timecode_edit._raw_digits = str(f)

    def _on_timecode_jump(self) -> None:
        text = self.timecode_edit.text().strip()
        if not text:
            return
        fps = self._sequence.fps if self._sequence else 24.0
        seq_in = self._sequence.in_frame if self._sequence else 1
        start_tc = self._sequence.start_timecode if self._sequence and self._sequence.start_timecode else "00:00:00:00"
        target_frame: Optional[int] = None

        if text.startswith(("+", "-")):
            try:
                delta = int(text)
                target_frame = self.canvas._playhead_frame + delta
            except ValueError:
                pass

        if target_frame is None:
            is_tc = getattr(self, "_display_timecode", True)
            if not is_tc:
                try:
                    target_frame = int(text)
                except ValueError:
                    pass
            else:
                base_f = timecode_to_frame(start_tc, fps) if start_tc != "00:00:00:00" else 0
                if text.isdigit() and len(text) == 8:
                    hh = int(text[0:2])
                    mm = int(text[2:4])
                    ss = int(text[4:6])
                    ff = int(text[6:8])
                    tc_f = timecode_to_frame(f"{hh:02d}:{mm:02d}:{ss:02d}:{ff:02d}", fps)
                elif ":" in text:
                    tc_f = timecode_to_frame(text, fps)
                elif text.isdigit():
                    target_frame = int(text)
                    tc_f = None
                else:
                    tc_f = timecode_to_frame(text, fps)
                if tc_f is not None:
                    target_frame = seq_in + (tc_f - base_f)

        if target_frame is not None and target_frame > 0:
            self.set_playhead_frame(target_frame)
            self.playhead_changed.emit(target_frame)
            self.scrub_frame_requested.emit(target_frame)
            self.canvas.update()
        self._sync_timecode_display(force=True)
        self.canvas.setFocus()

    def select_clip(self, clip_id: str) -> None:
        self.canvas._selected_clip_id = clip_id
        self.canvas.update()

    def zoom_to_fit(self) -> None:
        self.canvas.zoom_to_fit()

    def zoom_in(self) -> None:
        self.canvas.zoom_in()

    def zoom_out(self) -> None:
        self.canvas.zoom_out()

    def mark_in(self) -> None:
        self.canvas.mark_in()

    def mark_out(self) -> None:
        self.canvas.mark_out()

    def clear_in_out(self) -> None:
        self.canvas.clear_in_out()

    def add_marker(self, frame: Optional[int] = None) -> None:
        self.canvas.add_marker_dialog(frame)

    def _sync_zoom_ui(self, zoom: float) -> None:
        self.zoom_slider.blockSignals(True)
        self.zoom_slider.setValue(int(zoom * 100))
        self.zoom_slider.blockSignals(False)
        self._sync_scrollbar()

    def _sync_scrollbar(self, *args) -> None:
        if not self._sequence:
            self.scrollbar.setRange(0, 0)
            return
        total_w = int(self._sequence.duration * self.canvas._zoom)
        vis_w = self.canvas.width()
        max_scroll = max(0, total_w - vis_w)
        self.scrollbar.blockSignals(True)
        self.scrollbar.setRange(0, max_scroll)
        self.scrollbar.setPageStep(vis_w)
        self.scrollbar.setValue(self.canvas._scroll_x)
        self.scrollbar.blockSignals(False)

    def _on_scrollbar_value_changed(self, val: int) -> None:
        self.canvas._scroll_x = val
        self.canvas.update()

    def _on_zoom_slider_changed(self, val: int) -> None:
        zoom = float(val) / 100.0
        self.canvas.set_zoom(zoom)

    def _rebuild_headers(self) -> None:
        while self.headers_layout.count() > 1:
            item = self.headers_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        if not self._sequence:
            return

        for track in self._sequence.tracks:
            hdr = TrackHeaderWidget(track)
            hdr.track_changed.connect(self._on_track_header_changed)
            self.headers_layout.insertWidget(self.headers_layout.count() - 1, hdr)

    def _on_track_header_changed(self, track_id: str) -> None:
        """Trigger visual redraw and notify main window / compositor / audio engine of track changes."""
        self.canvas.update()
        self.sequence_changed.emit()

    def set_is_playing(self, is_playing: bool) -> None:
        """Update transport play/pause button icon."""
        icon_name = "pause" if is_playing else "play"
        self.btn_play_pause.setIcon(get_svg_icon(icon_name, color="#e5a823", size=13))

    def _set_tool(self, tool_name: str) -> None:
        self.btn_select.setChecked(tool_name == "select")
        self.btn_slide.setChecked(tool_name == "slide")
        self.btn_razor.setChecked(tool_name == "razor")
        self.canvas.set_active_tool(tool_name)

    def _on_snap_toggled(self, val: bool) -> None:
        self.canvas.set_snapping(val)

    def _on_add_video_track(self) -> None:
        if self._sequence:
            self._sequence.add_video_track()
            self._rebuild_headers()
            self.canvas.update()
            self.sequence_changed.emit()

    def _on_add_audio_track(self) -> None:
        if self._sequence:
            self._sequence.add_audio_track()
            self._rebuild_headers()
            self.canvas.update()
            self.sequence_changed.emit()

    def _on_razor_cut(self, track_id: str, frame: int) -> None:
        if not self._sequence:
            return
        for t in self._sequence.tracks:
            if t.id == track_id:
                clip = t.get_clip_at_frame(frame)
                if clip and clip.dst_in < frame <= clip.dst_out:
                    self.canvas.split_clip(track_id, frame)
                break


# Backwards compatibility alias
MultitrackTimelineWidget = MultiTrackTimelineWidget
