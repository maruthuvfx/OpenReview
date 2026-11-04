"""
Hardware-Accelerated Batch Export Queue Dialog for OpenReview.
Features:
- Multi-job queue table with live progress, speed (FPS), and status badges
- One-click batch queuing for all session sources
- Multi-deliverable studio export packs (ProRes 422 HQ Master + GPU H.264 Screener)
- Hardware acceleration engine selection (NVENC / QuickSync / AMF / VideoToolbox / MediaFoundation)
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional, List, Tuple, Callable

from PySide6.QtCore import Qt, QTimer
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
    QComboBox,
    QLineEdit,
    QFileDialog,
    QProgressBar,
    QMessageBox,
    QGroupBox,
    QSplitter,
)

from openreview.core.batch_export import (
    BatchExportEngine,
    BatchExportJob,
    BatchExportStatus,
    BATCH_PRESETS,
    DELIVERABLE_PACKS,
)
from openreview.core.exporter import MediaExporter


class BatchExportDialog(QDialog):
    """Studio Batch Export Queue & Deliverables Manager."""

    def __init__(
        self,
        engine: Optional[BatchExportEngine] = None,
        session_graph=None,
        active_view_info: Optional[dict] = None,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.setWindowTitle("Batch Export Queue - Hardware-Accelerated Delivery")
        self.resize(920, 640)
        self.setMinimumSize(780, 480)

        self.engine = engine or BatchExportEngine.instance()
        self.session_graph = session_graph
        self.active_view_info = active_view_info or {}

        # Default output directory
        self._default_output_dir = os.path.join(os.path.expanduser("~"), "OpenReview_Exports")

        self._setup_ui()
        self._connect_signals()
        self._populate_queue_table()

        # Periodic UI refresh timer
        self._refresh_timer = QTimer(self)
        self._refresh_timer.timeout.connect(self._update_live_stats)
        self._refresh_timer.start(250)

    def _setup_ui(self) -> None:
        self.setStyleSheet("""
            QDialog {
                background-color: #1e1e1e;
                color: #e0e0e0;
            }
            QGroupBox {
                border: 1px solid #333333;
                border-radius: 4px;
                margin-top: 8px;
                padding-top: 10px;
                color: #e5a823;
                font-weight: bold;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                subcontrol-position: top left;
                padding: 0 4px;
            }
            QLabel {
                color: #cccccc;
            }
            QLineEdit, QComboBox {
                background-color: #121212;
                border: 1px solid #3a3a3a;
                color: #ffffff;
                padding: 4px;
                border-radius: 2px;
            }
            QLineEdit:focus, QComboBox:focus {
                border-color: #e5a823;
            }
            QPushButton {
                background-color: #2b2b2b;
                border: 1px solid #444444;
                color: #ffffff;
                padding: 6px 12px;
                border-radius: 3px;
                font-weight: bold;
            }
            QPushButton:hover {
                border-color: #e5a823;
                background-color: #353535;
            }
            QTableWidget {
                background-color: #141414;
                border: 1px solid #333333;
                gridline-color: #242424;
                color: #e0e0e0;
                selection-background-color: #3a321a;
                selection-color: #ffffff;
            }
            QHeaderView::section {
                background-color: #222222;
                color: #bbbbbb;
                padding: 4px 6px;
                border: 1px solid #333333;
                font-weight: bold;
            }
            QProgressBar {
                border: 1px solid #333333;
                background-color: #121212;
                text-align: center;
                color: #ffffff;
                border-radius: 2px;
                height: 16px;
            }
            QProgressBar::chunk {
                background-color: #27ae60;
            }
        """)

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(12, 12, 12, 12)
        main_layout.setSpacing(10)

        # ----------------------------------------------------------------------
        # Top Toolbar: Action & Preset Selection
        # ----------------------------------------------------------------------
        toolbar = QHBoxLayout()
        toolbar.setSpacing(8)

        self.btn_add_current = QPushButton("+ Add Current Media")
        self.btn_add_current.setToolTip("Queue current timeline media for batch export")
        self.btn_add_current.clicked.connect(self._on_add_current_clicked)
        toolbar.addWidget(self.btn_add_current)

        self.btn_add_all = QPushButton("+ Add All Sources in Bin")
        self.btn_add_all.setToolTip("Queue all media assets in the Sources bin")
        self.btn_add_all.clicked.connect(self._on_add_all_sources_clicked)
        toolbar.addWidget(self.btn_add_all)

        self.btn_add_pack = QPushButton("+ Add Dailies Multi-Pack")
        self.btn_add_pack.setToolTip("Queue ProRes Master + H.264 Web Screener simultaneous deliverables")
        self.btn_add_pack.clicked.connect(self._on_add_multipack_clicked)
        toolbar.addWidget(self.btn_add_pack)

        toolbar.addStretch()

        self.btn_remove = QPushButton("Remove Selected")
        self.btn_remove.clicked.connect(self._on_remove_selected_clicked)
        toolbar.addWidget(self.btn_remove)

        self.btn_clear_done = QPushButton("Clear Completed")
        self.btn_clear_done.clicked.connect(self._on_clear_completed_clicked)
        toolbar.addWidget(self.btn_clear_done)

        main_layout.addLayout(toolbar)

        # ----------------------------------------------------------------------
        # Output Directory & Preset Configuration Bar
        # ----------------------------------------------------------------------
        cfg_box = QGroupBox("Batch Destination & Delivery Profile")
        cfg_layout = QHBoxLayout(cfg_box)

        cfg_layout.addWidget(QLabel("Output Folder:"))
        self.edit_output_dir = QLineEdit(self._default_output_dir)
        cfg_layout.addWidget(self.edit_output_dir, 1)

        self.btn_browse_dir = QPushButton("Browse...")
        self.btn_browse_dir.clicked.connect(self._on_browse_output_dir)
        cfg_layout.addWidget(self.btn_browse_dir)

        cfg_layout.addWidget(QLabel("Preset Profile:"))
        self.combo_preset = QComboBox()
        for k, v in BATCH_PRESETS.items():
            self.combo_preset.addItem(v["name"], k)
        cfg_layout.addWidget(self.combo_preset)

        cfg_layout.addWidget(QLabel("Hardware Accel:"))
        self.combo_hwaccel = QComboBox()
        self.combo_hwaccel.addItems([
            "Auto (GPU Accelerated)",
            "NVIDIA NVENC",
            "Intel QuickSync",
            "AMD AMF",
            "Apple VideoToolbox",
            "CPU Software",
        ])
        cfg_layout.addWidget(self.combo_hwaccel)

        main_layout.addWidget(cfg_box)

        # ----------------------------------------------------------------------
        # Queue Table
        # ----------------------------------------------------------------------
        self.table = QTableWidget(0, 8)
        self.table.setHorizontalHeaderLabels([
            "Status",
            "Job Name",
            "Source",
            "Format / Codec",
            "Resolution",
            "Frames",
            "Progress",
            "Output Destination",
        ])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Interactive)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(6, QHeaderView.ResizeMode.Interactive)
        self.table.horizontalHeader().setSectionResizeMode(7, QHeaderView.ResizeMode.Stretch)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setAlternatingRowColors(True)

        main_layout.addWidget(self.table, 1)

        # ----------------------------------------------------------------------
        # Bottom Controls: Live Queue Progress & Execution
        # ----------------------------------------------------------------------
        bottom_box = QVBoxLayout()
        bottom_box.setSpacing(6)

        prog_row = QHBoxLayout()
        self.lbl_queue_status = QLabel("Queue Idle: 0 jobs")
        self.lbl_queue_status.setStyleSheet("font-weight: bold; color: #e5a823;")
        prog_row.addWidget(self.lbl_queue_status)

        prog_row.addStretch()

        self.lbl_fps = QLabel("Encoding Speed: -- FPS")
        self.lbl_fps.setStyleSheet("font-family: monospace; color: #27ae60; font-weight: bold;")
        prog_row.addWidget(self.lbl_fps)

        bottom_box.addLayout(prog_row)

        self.overall_progress = QProgressBar()
        self.overall_progress.setRange(0, 100)
        self.overall_progress.setValue(0)
        bottom_box.addWidget(self.overall_progress)

        btn_row = QHBoxLayout()
        btn_row.addStretch()

        self.btn_start = QPushButton("Start Batch Queue")
        self.btn_start.setStyleSheet("""
            QPushButton {
                background-color: #1e5e2e;
                color: #ffffff;
                font-size: 13px;
                padding: 7px 20px;
                border: 1px solid #2ecc71;
            }
            QPushButton:hover {
                background-color: #27ae60;
            }
        """)
        self.btn_start.clicked.connect(self._on_start_queue_clicked)
        btn_row.addWidget(self.btn_start)

        self.btn_pause = QPushButton("Pause")
        self.btn_pause.clicked.connect(self._on_pause_queue_clicked)
        self.btn_pause.setEnabled(False)
        btn_row.addWidget(self.btn_pause)

        self.btn_cancel = QPushButton("Cancel All")
        self.btn_cancel.setStyleSheet("background-color: #4a2222; border-color: #c0392b;")
        self.btn_cancel.clicked.connect(self._on_cancel_queue_clicked)
        self.btn_cancel.setEnabled(False)
        btn_row.addWidget(self.btn_cancel)

        self.btn_close = QPushButton("Close")
        self.btn_close.clicked.connect(self.close)
        btn_row.addWidget(self.btn_close)

        bottom_box.addLayout(btn_row)
        main_layout.addLayout(bottom_box)

    def _connect_signals(self) -> None:
        self.engine.queue_updated.connect(self._populate_queue_table)
        self.engine.job_started.connect(self._on_job_started)
        self.engine.job_progress.connect(self._on_job_progress)
        self.engine.job_completed.connect(self._on_job_completed)
        self.engine.job_failed.connect(self._on_job_failed)
        self.engine.queue_progress.connect(self._on_queue_progress)
        self.engine.queue_finished.connect(self._on_queue_finished)
        self.engine.queue_state_changed.connect(self._on_queue_state_changed)

    def _populate_queue_table(self) -> None:
        jobs = self.engine.get_jobs()
        self.table.setRowCount(len(jobs))

        for row, job in enumerate(jobs):
            # 0: Status badge
            status_item = QTableWidgetItem(job.status.value.upper())
            status_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            if job.status == BatchExportStatus.COMPLETED:
                status_item.setForeground(Qt.GlobalColor.green)
            elif job.status == BatchExportStatus.ENCODING:
                status_item.setForeground(Qt.GlobalColor.yellow)
            elif job.status == BatchExportStatus.FAILED:
                status_item.setForeground(Qt.GlobalColor.red)
            elif job.status == BatchExportStatus.CANCELLED:
                status_item.setForeground(Qt.GlobalColor.gray)
            else:
                status_item.setForeground(Qt.GlobalColor.cyan)
            self.table.setItem(row, 0, status_item)

            # 1: Name
            self.table.setItem(row, 1, QTableWidgetItem(job.name))

            # 2: Source
            self.table.setItem(row, 2, QTableWidgetItem(job.source_name))

            # 3: Codec / Format
            self.table.setItem(row, 3, QTableWidgetItem(f"{job.codec} ({job.hwaccel.upper()})"))

            # 4: Resolution
            res_str = f"{job.target_resolution[0]}x{job.target_resolution[1]}" if job.target_resolution else "Native"
            self.table.setItem(row, 4, QTableWidgetItem(res_str))

            # 5: Frames
            f_count = len(job.frames)
            f_str = f"{job.frames[0]}-{job.frames[-1]} ({f_count} f)" if f_count > 0 else "--"
            self.table.setItem(row, 5, QTableWidgetItem(f_str))

            # 6: Progress
            pct = int(job.progress * 100)
            prog_item = QTableWidgetItem(f"{pct}%" if job.status != BatchExportStatus.COMPLETED else "100%")
            prog_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(row, 6, prog_item)

            # 7: Output
            self.table.setItem(row, 7, QTableWidgetItem(job.output_path))

        queued = sum(1 for j in jobs if j.status == BatchExportStatus.QUEUED)
        done = sum(1 for j in jobs if j.status == BatchExportStatus.COMPLETED)
        self.lbl_queue_status.setText(f"Queue: {len(jobs)} total jobs ({done} completed, {queued} queued)")

    def _update_live_stats(self) -> None:
        if not self.engine.is_running():
            return
        jobs = self.engine.get_jobs()
        for row, job in enumerate(jobs):
            if job.status == BatchExportStatus.ENCODING:
                pct = int(job.progress * 100)
                item = self.table.item(row, 6)
                if item:
                    item.setText(f"{pct}% ({job.current_frame}/{job.total_frames})")
                self.lbl_fps.setText(f"Encoding Speed: {job.fps_current:.1f} FPS")

    # --------------------------------------------------------------------------
    # Engine Signal Handlers
    # --------------------------------------------------------------------------
    def _on_job_started(self, job: BatchExportJob) -> None:
        self._populate_queue_table()

    def _on_job_progress(self, job_id: str, cur: int, tot: int, fps: float, msg: str) -> None:
        self.lbl_fps.setText(f"Encoding Speed: {fps:.1f} FPS")

    def _on_job_completed(self, job_id: str, out_path: str) -> None:
        self._populate_queue_table()

    def _on_job_failed(self, job_id: str, err: str) -> None:
        self._populate_queue_table()

    def _on_queue_progress(self, completed: int, total: int, overall_pct: float) -> None:
        self.overall_progress.setValue(int(overall_pct * 100))
        self.lbl_queue_status.setText(f"Batch Progress: {completed} of {total} jobs ({int(overall_pct * 100)}%)")

    def _on_queue_finished(self, completed: int, failed: int) -> None:
        self._populate_queue_table()
        self.overall_progress.setValue(100 if failed == 0 else self.overall_progress.value())
        self.lbl_fps.setText("Encoding Speed: -- FPS")
        self.btn_start.setEnabled(True)
        self.btn_pause.setEnabled(False)
        self.btn_cancel.setEnabled(False)
        msg = f"Batch export completed!\n{completed} jobs succeeded."
        if failed > 0:
            msg += f"\n{failed} jobs failed."
        QMessageBox.information(self, "Batch Export Finished", msg)

    def _on_queue_state_changed(self, running: bool) -> None:
        self.btn_start.setEnabled(not running)
        self.btn_pause.setEnabled(running)
        self.btn_cancel.setEnabled(running)

    # --------------------------------------------------------------------------
    # UI Actions
    # --------------------------------------------------------------------------
    def _on_browse_output_dir(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Select Batch Output Directory", self.edit_output_dir.text())
        if d:
            self.edit_output_dir.setText(d)

    def _resolve_hwaccel_key(self) -> str:
        txt = self.combo_hwaccel.currentText().lower()
        if "nvenc" in txt:
            return "nvenc"
        if "quicksync" in txt or "qsv" in txt:
            return "qsv"
        if "amf" in txt:
            return "amf"
        if "videotoolbox" in txt:
            return "videotoolbox"
        if "cpu" in txt or "software" in txt:
            return "cpu"
        return "auto"

    def _on_add_current_clicked(self) -> None:
        provider = self.active_view_info.get("frame_provider")
        frames = self.active_view_info.get("frames", [])
        if not provider or not frames:
            QMessageBox.warning(self, "No Media", "No active media view available to queue.")
            return

        name = self.active_view_info.get("name", "Active_Media")
        fps = float(self.active_view_info.get("fps", 24.0))
        out_dir = self.edit_output_dir.text()
        preset_key = self.combo_preset.currentData()
        hw = self._resolve_hwaccel_key()

        job = self.engine.create_job_from_preset(
            name=name,
            output_dir=out_dir,
            frames=frames,
            frame_provider=provider,
            preset_key=preset_key,
            fps=fps,
            source_id="active",
            source_name=name,
        )
        job.hwaccel = hw
        self.engine.add_job(job)

    def _on_add_all_sources_clicked(self) -> None:
        if not self.session_graph or not getattr(self.session_graph, "sources", None):
            QMessageBox.warning(self, "No Sources", "No media assets found in the Sources bin.")
            return

        sources = list(self.session_graph.sources.values())
        if not sources:
            QMessageBox.warning(self, "No Sources", "The Sources bin is empty.")
            return

        out_dir = self.edit_output_dir.text()
        preset_key = self.combo_preset.currentData()
        hw = self._resolve_hwaccel_key()

        reader_getter = self.active_view_info.get("reader_getter")
        added_count = 0

        for src in sources:
            # Build frame list
            start_f = getattr(src, "start_frame", 1001)
            end_f = getattr(src, "end_frame", 1001)
            f_list = list(range(start_f, end_f + 1))
            fps = float(getattr(src, "fps", 24.0))

            # Resolve reader
            reader = reader_getter(src) if reader_getter else None
            if not reader:
                continue

            def _make_provider(rdr):
                return lambda fn: rdr.read_frame(fn)

            job = self.engine.create_job_from_preset(
                name=src.name,
                output_dir=out_dir,
                frames=f_list,
                frame_provider=_make_provider(reader),
                preset_key=preset_key,
                fps=fps,
                source_id=src.id,
                source_name=src.name,
            )
            job.hwaccel = hw
            self.engine.add_job(job)
            added_count += 1

        QMessageBox.information(self, "Sources Queued", f"Queued {added_count} source media assets for batch export.")

    def _on_add_multipack_clicked(self) -> None:
        provider = self.active_view_info.get("frame_provider")
        frames = self.active_view_info.get("frames", [])
        if not provider or not frames:
            QMessageBox.warning(self, "No Media", "No active media view available to queue.")
            return

        name = self.active_view_info.get("name", "Shot")
        fps = float(self.active_view_info.get("fps", 24.0))
        out_dir = self.edit_output_dir.text()

        ids = self.engine.add_multi_pack(
            name=name,
            output_dir=out_dir,
            frames=frames,
            frame_provider=provider,
            pack_key="dailies_pack",
            fps=fps,
        )
        QMessageBox.information(self, "Multi-Pack Queued", f"Queued {len(ids)} deliverable jobs (ProRes 422 HQ Master + H.264 GPU Screener).")

    def _on_remove_selected_clicked(self) -> None:
        rows = sorted(set(idx.row() for idx in self.table.selectedIndexes()), reverse=True)
        jobs = self.engine.get_jobs()
        for r in rows:
            if r < len(jobs):
                self.engine.remove_job(jobs[r].id)

    def _on_clear_completed_clicked(self) -> None:
        self.engine.clear_completed()

    def _on_start_queue_clicked(self) -> None:
        self.engine.start()

    def _on_pause_queue_clicked(self) -> None:
        self.engine.pause()

    def _on_cancel_queue_clicked(self) -> None:
        if QMessageBox.question(self, "Cancel Batch", "Cancel all running and queued export jobs?") == QMessageBox.StandardButton.Yes:
            self.engine.cancel()
