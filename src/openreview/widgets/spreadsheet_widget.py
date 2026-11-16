"""
Editorial Spreadsheet and Cut List Widget.
Provides:
- High-density tabular view of timeline sequence events, tracks, cuts, and metadata
- Two-way synchronization with timeline playhead and selection
- Status tagging (MATCHED, MISSING, APPROVED, WIP, HOLD, OMIT)
- Media relinking, version switching, and CSV export
"""
from __future__ import annotations

import csv
import logging
from typing import Optional, List, Dict, Any

from PySide6.QtCore import Qt, Signal, QObject, QItemSelectionModel
from PySide6.QtGui import QColor, QFont, QBrush
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QPushButton,
    QLineEdit,
    QComboBox,
    QLabel,
    QFileDialog,
    QMessageBox,
    QMenu,
)

from openreview.models.timeline_sequence import TimelineSequence, Track, ClipItem, ShotStatus, TrackType
from openreview.ui.svg_icons import get_svg_icon

logger = logging.getLogger(__name__)


class SpreadsheetWidget(QWidget):
    """
    High-density editorial cut list spreadsheet synced bidirectionally with the timeline.
    """
    clip_selected = Signal(str, str, int)  # track_id, clip_id, dst_in_frame
    conform_requested = Signal(str)        # file_path
    relink_requested = Signal()
    cut_deleted = Signal(str, str, bool)   # track_id, clip_id, ripple
    sequence_modified = Signal()

    COLUMNS = [
        "Event",
        "Status",
        "Shot Name",
        "Track",
        "Src In",
        "Src Out",
        "Src Dur",
        "Dst In",
        "Dst Out",
        "Dst Dur",
        "Version",
        "Reel",
        "File Path",
    ]

    STATUS_COLORS = {
        ShotStatus.MATCHED: "#2ecc71",
        ShotStatus.MISSING: "#e74c3c",
        ShotStatus.CONFLICT: "#e67e22",
        ShotStatus.APPROVED: "#3498db",
        ShotStatus.WIP: "#f1c40f",
        ShotStatus.HOLD: "#9b59b6",
        ShotStatus.OMIT: "#7f8c8d",
    }

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._sequence: Optional[TimelineSequence] = None
        self._is_updating = False
        self._highlighted_clip_id: Optional[str] = None

        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        self.setMinimumWidth(220)
        self.setStyleSheet("""
            QPushButton {
                background-color: #282828;
                border: 1px solid #3a3a3a;
                color: #e0e0e0;
                padding: 2px 4px;
                border-radius: 2px;
                font-size: 11px;
            }
            QPushButton:hover {
                background-color: #353535;
                border-color: #e5a823;
            }
            QPushButton:pressed {
                background-color: #e5a823;
                color: #121212;
            }
            QLineEdit {
                background-color: #141414;
                border: 1px solid #333333;
                color: #ffffff;
                padding: 2px 5px;
                border-radius: 2px;
                font-size: 11px;
            }
            QLineEdit:focus {
                border-color: #e5a823;
            }
        """)

        # Compact 2-row Toolbar
        # Row 1: Quick Action Controls
        row1_layout = QHBoxLayout()
        row1_layout.setContentsMargins(0, 0, 0, 0)
        row1_layout.setSpacing(3)

        self.btn_import = QPushButton("Import...", self)
        self.btn_import.setFixedHeight(22)
        self.btn_import.setToolTip("Import Cut List (EDL, OTIO, XML, CSV)")
        self.btn_import.clicked.connect(self._on_import_cut)
        row1_layout.addWidget(self.btn_import)

        self.btn_match = QPushButton("Relink", self)
        self.btn_match.setFixedHeight(22)
        self.btn_match.setToolTip("Relink / Match Media on Disk")
        self.btn_match.clicked.connect(self.relink_requested.emit)
        row1_layout.addWidget(self.btn_match)

        self.btn_ver_up = QPushButton("▲", self)
        self.btn_ver_up.setFixedSize(22, 22)
        self.btn_ver_up.setToolTip("Version Up Selected Clip")
        self.btn_ver_up.clicked.connect(self._on_version_up_selected)
        row1_layout.addWidget(self.btn_ver_up)

        self.btn_ver_down = QPushButton("▼", self)
        self.btn_ver_down.setFixedSize(22, 22)
        self.btn_ver_down.setToolTip("Version Down Selected Clip")
        self.btn_ver_down.clicked.connect(self._on_version_down_selected)
        row1_layout.addWidget(self.btn_ver_down)

        self.btn_delete_cut = QPushButton("✕ Del", self)
        self.btn_delete_cut.setFixedHeight(22)
        self.btn_delete_cut.setIcon(get_svg_icon("delete", color="#e74c3c", size=11))
        self.btn_delete_cut.setToolTip("Delete selected cut (Delete key: Lift, Shift+Delete: Ripple)")
        self.btn_delete_cut.clicked.connect(lambda: self._on_delete_selected_cut(ripple=False))
        row1_layout.addWidget(self.btn_delete_cut)

        row1_layout.addStretch()

        self.btn_export_csv = QPushButton("CSV", self)
        self.btn_export_csv.setFixedHeight(22)
        self.btn_export_csv.setToolTip("Export Cut List as CSV")
        self.btn_export_csv.clicked.connect(self._on_export_csv)
        row1_layout.addWidget(self.btn_export_csv)

        layout.addLayout(row1_layout)

        # Row 2: Search & Filter Bar
        row2_layout = QHBoxLayout()
        row2_layout.setContentsMargins(0, 0, 0, 0)
        row2_layout.setSpacing(3)

        self.search_filter = QLineEdit(self)
        self.search_filter.setPlaceholderText("Filter shots / reels...")
        self.search_filter.setFixedHeight(22)
        self.search_filter.setClearButtonEnabled(True)
        self.search_filter.textChanged.connect(self._apply_filter)
        row2_layout.addWidget(self.search_filter)

        layout.addLayout(row2_layout)

        # Table Widget
        self.table = QTableWidget(self)
        self.table.setColumnCount(len(self.COLUMNS))
        self.table.setHorizontalHeaderLabels(self.COLUMNS)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(True)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.horizontalHeader().setDefaultSectionSize(75)
        self.table.setAlternatingRowColors(True)
        self.table.setStyleSheet("""
            QTableWidget {
                background-color: #161616;
                alternate-background-color: #1e1e1e;
                color: #dddddd;
                gridline-color: #262626;
                border: 1px solid #222222;
                font-size: 11px;
                selection-background-color: #342a12;
                selection-color: #f0f0f0;
            }
            QTableWidget::item {
                padding: 3px 5px;
                border-bottom: 1px solid #202020;
            }
            QTableWidget::item:hover {
                background-color: #252525;
            }
            QTableWidget::item:selected {
                background-color: #342a12;
                color: #f5f5f5;
                border-left: 2px solid #e5a823;
            }
            QHeaderView::section {
                background-color: #202020;
                color: #aaaaaa;
                padding: 4px;
                border: 1px solid #282828;
                font-weight: bold;
                font-size: 10.5px;
            }
        """)
        self.table.itemSelectionChanged.connect(self._on_selection_changed)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._on_context_menu)
        layout.addWidget(self.table)

    def set_sequence(self, sequence: Optional[TimelineSequence]) -> None:
        self._sequence = sequence
        self._highlighted_clip_id = None
        self.refresh()

    def refresh(self) -> None:
        if self._is_updating:
            return
        self._is_updating = True
        self.table.setRowCount(0)

        if not self._sequence:
            self._is_updating = False
            return

        all_clips = self._sequence.get_all_clips()
        self.table.setRowCount(len(all_clips))

        for row_idx, (track, clip) in enumerate(all_clips):
            evt_str = f"{row_idx + 1:03d}"
            status_str = clip.status.value if isinstance(clip.status, ShotStatus) else str(clip.status)

            item_evt = QTableWidgetItem(evt_str)
            item_status = QTableWidgetItem(status_str)
            color_hex = self.STATUS_COLORS.get(clip.status, "#888888")
            item_status.setForeground(QBrush(QColor(color_hex)))
            font = item_status.font()
            font.setBold(True)
            item_status.setFont(font)

            item_name = QTableWidgetItem(clip.name)
            item_trk = QTableWidgetItem(track.name)
            item_src_in = QTableWidgetItem(str(clip.src_in))
            item_src_out = QTableWidgetItem(str(clip.src_out))
            item_src_dur = QTableWidgetItem(str(clip.src_duration))
            item_dst_in = QTableWidgetItem(str(clip.dst_in))
            item_dst_out = QTableWidgetItem(str(clip.dst_out))
            item_dst_dur = QTableWidgetItem(str(clip.dst_duration))
            item_ver = QTableWidgetItem(clip.version)
            item_reel = QTableWidgetItem(clip.reel)
            item_path = QTableWidgetItem(clip.file_path)

            for col_idx, item in enumerate([
                item_evt, item_status, item_name, item_trk,
                item_src_in, item_src_out, item_src_dur,
                item_dst_in, item_dst_out, item_dst_dur,
                item_ver, item_reel, item_path
            ]):
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                item.setData(Qt.ItemDataRole.UserRole, (track.id, clip.id, clip.dst_in))
                self.table.setItem(row_idx, col_idx, item)

        self._is_updating = False

    def select_clip(self, clip_id: str) -> None:
        """Select row corresponding to clip_id."""
        if self._is_updating:
            return
        for r in range(self.table.rowCount()):
            item = self.table.item(r, 0)
            if item:
                data = item.data(Qt.ItemDataRole.UserRole)
                if data and data[1] == clip_id:
                    self._is_updating = True
                    self.table.selectRow(r)
                    self.table.scrollToItem(item)
                    self._is_updating = False
                    break

    def highlight_frame(self, frame: int) -> None:
        """Highlight clip that spans frame with cut-boundary caching to eliminate 60Hz reflows."""
        if not self._sequence or self._is_updating:
            return
        top = self._sequence.get_top_clip_at_frame(frame)
        if top:
            _, clip = top
            if getattr(self, "_highlighted_clip_id", None) == clip.id:
                return
            self._highlighted_clip_id = clip.id
            self.select_clip(clip.id)
        else:
            self._highlighted_clip_id = None

    def _on_selection_changed(self) -> None:
        if self._is_updating:
            return
        selected_rows = self.table.selectionModel().selectedRows()
        if selected_rows:
            r = selected_rows[0].row()
            item = self.table.item(r, 0)
            if item:
                track_id, clip_id, dst_in = item.data(Qt.ItemDataRole.UserRole)
                self.clip_selected.emit(track_id, clip_id, dst_in)

    def _on_import_cut(self) -> None:
        try:
            from openreview.ui.conform_dialog import ConformDialog
            dlg = ConformDialog(parent=self.window())
            dlg.conform_committed.connect(self.conform_requested.emit)
            dlg.exec()
        except Exception as e:
            path, _ = QFileDialog.getOpenFileName(
                self, "Import Cut Turnover", "",
                "Turnover Files (*.edl *.xml *.aaf *.otio);;CMX 3600 EDL (*.edl);;FCP XML (*.xml);;Avid AAF (*.aaf);;OpenTimelineIO (*.otio)"
            )
            if path:
                self.conform_requested.emit(path)

    def _on_version_up_selected(self) -> None:
        selected_rows = self.table.selectionModel().selectedRows()
        if not selected_rows or not self._sequence:
            return
        r = selected_rows[0].row()
        item = self.table.item(r, 0)
        if item:
            track_id, clip_id, _ = item.data(Qt.ItemDataRole.UserRole)
            for t in self._sequence.tracks:
                if t.id == track_id:
                    for c in t.clips:
                        if c.id == clip_id:
                            new_path = c.version_up()
                            if new_path:
                                self.refresh()
                                self.select_clip(clip_id)
                            return

    def _on_version_down_selected(self) -> None:
        selected_rows = self.table.selectionModel().selectedRows()
        if not selected_rows or not self._sequence:
            return
        r = selected_rows[0].row()
        item = self.table.item(r, 0)
        if item:
            track_id, clip_id, _ = item.data(Qt.ItemDataRole.UserRole)
            for t in self._sequence.tracks:
                if t.id == track_id:
                    for c in t.clips:
                        if c.id == clip_id:
                            new_path = c.version_down()
                            if new_path:
                                self.refresh()
                                self.select_clip(clip_id)
                            return

    def _apply_filter(self, text: str) -> None:
        query = text.strip().lower()
        for r in range(self.table.rowCount()):
            if not query:
                self.table.setRowHidden(r, False)
                continue
            name_item = self.table.item(r, 2)
            reel_item = self.table.item(r, 11)
            name_str = (name_item.text() if name_item else "").lower()
            reel_str = (reel_item.text() if reel_item else "").lower()
            match = (query in name_str) or (query in reel_str)
            self.table.setRowHidden(r, not match)

    def _on_export_csv(self) -> None:
        if not self._sequence:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Export Cut List CSV", "", "CSV Files (*.csv)")
        if not path:
            return
        try:
            with open(path, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(self.COLUMNS)
                for r in range(self.table.rowCount()):
                    row_data = [self.table.item(r, c).text() if self.table.item(r, c) else "" for c in range(len(self.COLUMNS))]
                    writer.writerow(row_data)
            QMessageBox.information(self, "Export CSV", f"Successfully exported cut list to:\n{path}")
        except Exception as e:
            QMessageBox.warning(self, "Export CSV Failed", f"Could not export CSV:\n{e}")

    def _on_delete_selected_cut(self, ripple: bool = False) -> None:
        """Remove currently selected cut from the active sequence."""
        if not self._sequence:
            return
        selected_rows = self.table.selectionModel().selectedRows()
        if not selected_rows:
            return
        r = selected_rows[0].row()
        item = self.table.item(r, 0)
        if not item:
            return
        track_id, clip_id, _ = item.data(Qt.ItemDataRole.UserRole)
        res = self._sequence.remove_clip(clip_id, ripple=ripple)
        if res is not None:
            self.refresh()
            self.sequence_modified.emit()
            self.cut_deleted.emit(track_id, clip_id, ripple)

    def _on_context_menu(self, pos) -> None:
        item = self.table.itemAt(pos)
        if not item:
            return
        r = item.row()
        self.table.selectRow(r)
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

        act_lift = menu.addAction(get_svg_icon("delete", color="#e74c3c", size=13), "Remove Cut (Lift)")
        act_lift.setShortcut("Delete")
        act_lift.triggered.connect(lambda: self._on_delete_selected_cut(ripple=False))

        act_ripple = menu.addAction(get_svg_icon("delete", color="#f39c12", size=13), "Ripple Delete (Close Gap)")
        act_ripple.setShortcut("Shift+Delete")
        act_ripple.triggered.connect(lambda: self._on_delete_selected_cut(ripple=True))

        menu.addSeparator()
        act_vup = menu.addAction("Version Up")
        act_vup.triggered.connect(self._on_version_up_selected)
        act_vdown = menu.addAction("Version Down")
        act_vdown.triggered.connect(self._on_version_down_selected)

        menu.exec(self.table.viewport().mapToGlobal(pos))

    def keyPressEvent(self, event) -> None:
        if event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            ripple = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
            self._on_delete_selected_cut(ripple=ripple)
            event.accept()
            return
        super().keyPressEvent(event)
