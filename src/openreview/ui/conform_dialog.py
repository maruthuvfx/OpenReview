"""
Conform & Relinking Turnover Wizard Dialog for OpenReview.
Provides:
- Turnover file import (CMX 3600 EDL, Final Cut Pro XML, Avid AAF, OpenTimelineIO .otio)
- Multi-directory recursive media search paths with persistence
- Rule-based smart relinking (Reel name, Timecode overlap, tokenized {shot}/{version} matching)
- Interactive match preview table with visual status indicators (Matched, Missing, Conflict)
- One-click sequence commitment to the Multi-Track Timeline
"""
from __future__ import annotations

import os
import logging
from typing import Optional, List, Dict, Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont, QBrush
from PySide6.QtWidgets import (
    QDialog,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QGridLayout,
    QFormLayout,
    QLabel,
    QLineEdit,
    QComboBox,
    QSpinBox,
    QDoubleSpinBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QListWidget,
    QFileDialog,
    QMessageBox,
    QTabWidget,
    QCheckBox,
    QFrame,
    QGroupBox,
)

from openreview.models.timeline_sequence import TimelineSequence, Track, ClipItem, ShotStatus, TrackType
from openreview.core.conform import EDLParser, XMLParser, AAFParser, MediaRelinker, RelinkReport
from openreview.sdk.timeline_api import TimelineAPI
from openreview.core.preferences import PreferencesManager

logger = logging.getLogger(__name__)


class ConformDialog(QDialog):
    """
    Studio Conform and Media Relinking Wizard Dialog.
    """
    conform_committed = Signal(object)  # TimelineSequence

    RESOLUTION_PRESETS = [
        ("1920x1080 (HD 1080p)", 1920, 1080),
        ("2048x858 (2K Cinemascope 2.39)", 2048, 858),
        ("2048x1080 (2K DCI Flat)", 2048, 1080),
        ("3840x2160 (4K UHD)", 3840, 2160),
        ("4096x1716 (4K Cinemascope 2.39)", 4096, 1716),
        ("4096x2160 (4K DCI Full)", 4096, 2160),
    ]

    FPS_PRESETS = [23.976, 24.0, 25.0, 29.97, 30.0, 48.0, 50.0, 59.94, 60.0]

    def __init__(self, initial_file: str = "", parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle("Conform & Media Relinking Wizard - OpenReview Studio")
        self.resize(880, 620)
        self._parsed_sequence: Optional[TimelineSequence] = None
        self._search_paths: List[str] = []

        self._setup_ui()
        self._load_preferences()

        if initial_file and os.path.exists(initial_file):
            self.edit_turnover_path.setText(initial_file)
            self._on_parse_turnover()

    def _setup_ui(self) -> None:
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(12, 12, 12, 12)
        main_layout.setSpacing(8)

        # Tabs: 1. Setup & Media Roots, 2. Match Preview Table
        self.tabs = QTabWidget(self)
        self.tabs.setStyleSheet("""
            QTabWidget::pane {
                border: 1px solid #282828;
                background-color: #141414;
            }
            QTabBar::tab {
                background: #1c1c1c;
                color: #888888;
                padding: 6px 14px;
                font-weight: bold;
                font-size: 11px;
                border: 1px solid #282828;
                border-bottom: none;
                margin-right: 2px;
                border-top-left-radius: 4px;
                border-top-right-radius: 4px;
            }
            QTabBar::tab:selected {
                color: #e5a823;
                background: #141414;
                border-top: 2px solid #e5a823;
            }
        """)

        # Tab 1: Configuration & Media Roots
        tab_config = QWidget()
        tab_config_layout = QVBoxLayout(tab_config)
        tab_config_layout.setContentsMargins(10, 10, 10, 10)
        tab_config_layout.setSpacing(10)

        # 1. Turnover File Selector
        grp_file = QGroupBox("1. Editorial Turnover File")
        f_layout = QHBoxLayout(grp_file)
        self.edit_turnover_path = QLineEdit(self)
        self.edit_turnover_path.setPlaceholderText("Select CMX 3600 EDL, Final Cut Pro XML, AAF, or OTIO turnover...")
        self.btn_browse_turnover = QPushButton("Browse...", self)
        self.btn_browse_turnover.clicked.connect(self._on_browse_turnover)
        f_layout.addWidget(self.edit_turnover_path, 1)
        f_layout.addWidget(self.btn_browse_turnover)
        tab_config_layout.addWidget(grp_file)

        # 2. Sequence Settings
        grp_seq = QGroupBox("2. Sequence & Timeline Properties")
        seq_form = QFormLayout(grp_seq)
        seq_form.setSpacing(6)

        self.edit_seq_name = QLineEdit("Conformed Sequence", self)
        seq_form.addRow("Sequence Name:", self.edit_seq_name)

        self.combo_res = QComboBox(self)
        for label, w, h in self.RESOLUTION_PRESETS:
            self.combo_res.addItem(label, (w, h))
        seq_form.addRow("Format / Resolution:", self.combo_res)

        self.combo_fps = QComboBox(self)
        for fps_val in self.FPS_PRESETS:
            self.combo_fps.addItem(f"{fps_val} fps", fps_val)
        self.combo_fps.setCurrentIndex(1)  # 24.0
        seq_form.addRow("Timebase (FPS):", self.combo_fps)

        self.edit_start_tc = QLineEdit("00:00:00:00", self)
        seq_form.addRow("Start Timecode:", self.edit_start_tc)
        tab_config_layout.addWidget(grp_seq)

        # 3. Media Search Roots
        grp_roots = QGroupBox("3. High-Res Media Search Paths")
        roots_layout = QVBoxLayout(grp_roots)
        roots_layout.setSpacing(6)

        self.list_paths = QListWidget(self)
        self.list_paths.setFixedHeight(90)
        self.list_paths.setStyleSheet("background-color: #181818; border: 1px solid #282828; color: #ddd;")
        roots_layout.addWidget(self.list_paths)

        roots_btns = QHBoxLayout()
        self.btn_add_path = QPushButton("+ Add Folder...", self)
        self.btn_add_path.clicked.connect(self._on_add_search_path)
        roots_btns.addWidget(self.btn_add_path)

        self.btn_rem_path = QPushButton("- Remove", self)
        self.btn_rem_path.clicked.connect(self._on_remove_search_path)
        roots_btns.addWidget(self.btn_rem_path)

        self.btn_clear_paths = QPushButton("Clear", self)
        self.btn_clear_paths.clicked.connect(self._on_clear_search_paths)
        roots_btns.addWidget(self.btn_clear_paths)
        roots_btns.addStretch()
        roots_layout.addLayout(roots_btns)

        # Relinking Options
        opts_layout = QHBoxLayout()
        self.chk_recursive = QCheckBox("Recursive directory scan", self)
        self.chk_recursive.setChecked(True)
        opts_layout.addWidget(self.chk_recursive)

        self.chk_fuzzy = QCheckBox("Token pattern matching ({shot}_{version})", self)
        self.chk_fuzzy.setChecked(True)
        opts_layout.addWidget(self.chk_fuzzy)

        self.chk_tc = QCheckBox("Verify timecode overlap", self)
        self.chk_tc.setChecked(True)
        opts_layout.addWidget(self.chk_tc)
        opts_layout.addStretch()
        roots_layout.addLayout(opts_layout)

        tab_config_layout.addWidget(grp_roots)

        # Parse & Match Button
        self.btn_scan = QPushButton("🔍  Parse Turnover & Relink Media", self)
        self.btn_scan.setFixedHeight(32)
        self.btn_scan.setStyleSheet("""
            QPushButton {
                background-color: #e5a823;
                color: #000000;
                font-weight: bold;
                font-size: 12px;
                border-radius: 4px;
            }
            QPushButton:hover {
                background-color: #f5b833;
            }
        """)
        self.btn_scan.clicked.connect(self._on_parse_turnover)
        tab_config_layout.addWidget(self.btn_scan)

        self.tabs.addTab(tab_config, "1. Turnover & Search Paths")

        # Tab 2: Match Preview Table
        tab_preview = QWidget()
        tab_preview_layout = QVBoxLayout(tab_preview)
        tab_preview_layout.setContentsMargins(10, 10, 10, 10)
        tab_preview_layout.setSpacing(6)

        # Status Summary Header
        self.lbl_summary = QLabel("No turnover parsed yet.", self)
        self.lbl_summary.setStyleSheet("font-weight: bold; font-size: 12px; color: #e5a823;")
        tab_preview_layout.addWidget(self.lbl_summary)

        # Table
        self.table_preview = QTableWidget(self)
        self.table_preview.setColumnCount(7)
        self.table_preview.setHorizontalHeaderLabels([
            "Status", "Event #", "Shot Name", "Track", "Src In-Out", "Dst In-Out", "Matched Media Path"
        ])
        self.table_preview.horizontalHeader().setStretchLastSection(True)
        self.table_preview.horizontalHeader().setDefaultSectionSize(85)
        self.table_preview.setShowGrid(True)
        self.table_preview.verticalHeader().setVisible(False)
        self.table_preview.setStyleSheet("""
            QTableWidget {
                background-color: #161616;
                color: #dddddd;
                gridline-color: #282828;
                border: 1px solid #222222;
                font-size: 11px;
            }
            QHeaderView::section {
                background-color: #202020;
                color: #aaaaaa;
                padding: 4px;
                border: 1px solid #282828;
                font-weight: bold;
            }
        """)
        tab_preview_layout.addWidget(self.table_preview)

        self.tabs.addTab(tab_preview, "2. Match Preview & Verification")
        main_layout.addWidget(self.tabs, 1)

        # Dialog Footer Buttons
        btn_box = QHBoxLayout()
        btn_box.setSpacing(8)

        self.btn_commit = QPushButton("✓  Commit to Multi-Track Timeline", self)
        self.btn_commit.setEnabled(False)
        self.btn_commit.setFixedHeight(30)
        self.btn_commit.setStyleSheet("""
            QPushButton {
                background-color: #27ae60;
                color: #ffffff;
                font-weight: bold;
                font-size: 11.5px;
                border-radius: 3px;
                padding: 0 16px;
            }
            QPushButton:hover {
                background-color: #2ecc71;
            }
            QPushButton:disabled {
                background-color: #333333;
                color: #666666;
            }
        """)
        self.btn_commit.clicked.connect(self._on_commit_sequence)
        btn_box.addWidget(self.btn_commit)

        btn_box.addStretch()

        self.btn_cancel = QPushButton("Cancel", self)
        self.btn_cancel.clicked.connect(self.reject)
        btn_box.addWidget(self.btn_cancel)

        main_layout.addLayout(btn_box)

    def _load_preferences(self) -> None:
        """Load default search roots from Preferences."""
        prefs = PreferencesManager.instance().prefs
        saved_paths = getattr(prefs, "conform_search_paths", [])
        if isinstance(saved_paths, list):
            for p in saved_paths:
                if p and p not in self._search_paths:
                    self._search_paths.append(p)
                    self.list_paths.addItem(p)

    def _save_preferences(self) -> None:
        """Persist current search roots in Preferences."""
        prefs = PreferencesManager.instance().prefs
        prefs.conform_search_paths = list(self._search_paths)
        PreferencesManager.instance().save()

    def _on_browse_turnover(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Select Turnover File", "",
            "Turnover Files (*.edl *.xml *.aaf *.otio *.otiod);;CMX 3600 EDL (*.edl);;FCP XML (*.xml);;Avid AAF (*.aaf);;OpenTimelineIO (*.otio)"
        )
        if path:
            self.edit_turnover_path.setText(path)
            self._on_parse_turnover()

    def _on_add_search_path(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Select Media Search Directory")
        if folder and folder not in self._search_paths:
            self._search_paths.append(folder)
            self.list_paths.addItem(folder)
            self._save_preferences()

    def _on_remove_search_path(self) -> None:
        row = self.list_paths.currentRow()
        if row >= 0:
            item = self.list_paths.takeItem(row)
            if item.text() in self._search_paths:
                self._search_paths.remove(item.text())
            self._save_preferences()

    def _on_clear_search_paths(self) -> None:
        self.list_paths.clear()
        self._search_paths.clear()
        self._save_preferences()

    def _on_parse_turnover(self) -> None:
        path = self.edit_turnover_path.text().strip()
        if not path or not os.path.exists(path):
            QMessageBox.warning(self, "Invalid File", "Please select a valid turnover file.")
            return

        fps = float(self.combo_fps.currentData() or 24.0)
        ext = os.path.splitext(path)[1].lower()

        try:
            if ext == ".edl":
                seq = TimelineAPI.conform_edl(path, fps=fps)
            elif ext == ".xml":
                seq = TimelineAPI.conform_xml(path, fps=fps)
            elif ext in (".otio", ".otiod"):
                seq = TimelineAPI.import_otio(path)
            else:
                seq = AAFParser(fps=fps).parse_file(path)

            # Apply UI sequence settings
            custom_name = self.edit_seq_name.text().strip()
            if custom_name:
                seq.name = custom_name
            w, h = self.combo_res.currentData() or (1920, 1080)
            seq.width = w
            seq.height = h
            seq.start_timecode = self.edit_start_tc.text().strip() or "00:00:00:00"

            # Relink against search paths
            relinker = MediaRelinker(search_paths=self._search_paths)
            report = relinker.relink_sequence(seq)

            self._parsed_sequence = seq
            self._populate_preview_table(seq, report)
            self.btn_commit.setEnabled(True)
            self.tabs.setCurrentIndex(1)  # Jump to Match Preview tab

        except Exception as e:
            logger.error("[ConformDialog] Error parsing turnover: %s", e)
            QMessageBox.warning(self, "Parse Error", f"Failed to parse turnover file:\n{e}")

    def _populate_preview_table(self, sequence: TimelineSequence, report: RelinkReport) -> None:
        all_clips = sequence.get_all_clips()
        self.table_preview.setRowCount(len(all_clips))

        pct = (report.matched / max(1, report.total_clips)) * 100.0
        self.lbl_summary.setText(
            f"Summary: {report.total_clips} Total Clips  |  {report.matched} Matched ({pct:.1f}%)  |  {report.missing} Missing Media"
        )

        for row, (track, clip) in enumerate(all_clips):
            # Status item
            status_item = QTableWidgetItem(clip.status.value)
            if clip.status == ShotStatus.MATCHED:
                status_item.setForeground(QBrush(QColor("#2ecc71")))
            else:
                status_item.setForeground(QBrush(QColor("#e74c3c")))
            font = status_item.font()
            font.setBold(True)
            status_item.setFont(font)

            evt_item = QTableWidgetItem(f"{row + 1:03d}")
            name_item = QTableWidgetItem(clip.name)
            trk_item = QTableWidgetItem(track.name)
            src_item = QTableWidgetItem(f"{clip.src_in} - {clip.src_out} ({clip.src_duration}f)")
            dst_item = QTableWidgetItem(f"{clip.dst_in} - {clip.dst_out} ({clip.dst_duration}f)")
            path_item = QTableWidgetItem(clip.file_path or "[MISSING / OFFLINE]")

            for col_idx, item in enumerate([status_item, evt_item, name_item, trk_item, src_item, dst_item, path_item]):
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.table_preview.setItem(row, col_idx, item)

    def _on_commit_sequence(self) -> None:
        if self._parsed_sequence:
            self.conform_committed.emit(self._parsed_sequence)
            self.accept()

    def get_conformed_sequence(self) -> Optional[TimelineSequence]:
        return self._parsed_sequence
