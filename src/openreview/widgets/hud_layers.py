"""
Multi-Channel EXR Layers & Passes HUD Widget.
Provides togglable floating widget to inspect, filter, and switch active EXR passes,
toggle adaptive contact sheet mode, view single pass, export contact sheets,
isolate channels (RGB/R/G/B/A), auto-normalize depth/Z, and inspect header metadata.
"""
from __future__ import annotations

from typing import List, Optional
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from openreview.resources.svg_icons import get_svg_icon


class HUDLayersWidget(QWidget):
    """Interactive widget for reviewing multi-channel EXR render passes, AOVs, and contact sheets."""

    layer_changed = Signal(str)
    contact_sheet_toggled = Signal(bool)
    view_single_requested = Signal()
    export_contact_sheet_requested = Signal()
    channel_mode_requested = Signal(int)
    auto_depth_toggled = Signal(bool)
    metadata_requested = Signal()

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setMinimumWidth(280)
        self._layers: List[str] = ["beauty"]
        self._current_layer: str = "beauty"
        self._contact_sheet_active: bool = False
        self._auto_depth_active: bool = False
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        # Header Row: Title & Pass Counter Badge
        hdr_layout = QHBoxLayout()
        hdr_layout.setContentsMargins(0, 0, 0, 0)
        hdr_lbl = QLabel("Multi-Channel EXR Passes / AOVs", self)
        hdr_lbl.setStyleSheet("font-weight: bold; color: #e5a823; font-size: 11px;")
        hdr_layout.addWidget(hdr_lbl)

        self.lbl_count = QLabel("1 pass", self)
        self.lbl_count.setStyleSheet(
            "background-color: #262626; color: #aaaaaa; padding: 1px 6px; border-radius: 9px; font-size: 10px;"
        )
        hdr_layout.addWidget(self.lbl_count, 0, Qt.AlignmentFlag.AlignRight)
        layout.addLayout(hdr_layout)

        # 1. Search / Filter Input
        self.search_input = QLineEdit(self)
        self.search_input.setPlaceholderText("Filter passes (press Enter to solo)...")
        self.search_input.setClearButtonEnabled(True)
        self.search_input.setStyleSheet("""
            QLineEdit {
                background-color: #141414;
                border: 1px solid #333333;
                border-radius: 3px;
                color: #e0e0e0;
                padding: 4px 6px;
                font-size: 11px;
            }
            QLineEdit:focus {
                border-color: #e5a823;
            }
        """)
        self.search_input.textChanged.connect(self._filter_layers)
        self.search_input.returnPressed.connect(self._on_search_return_pressed)
        layout.addWidget(self.search_input)

        # 2. Main Passes List
        self.list_layers = QListWidget(self)
        self.list_layers.setStyleSheet("""
            QListWidget {
                background-color: #141414;
                border: 1px solid #2e2e2e;
                color: #cccccc;
                font-size: 11px;
                min-height: 140px;
                border-radius: 2px;
            }
            QListWidget::item {
                padding: 4px 6px;
                border-bottom: 1px solid #1a1a1a;
            }
            QListWidget::item:hover {
                background-color: #202020;
                color: #ffffff;
            }
            QListWidget::item:selected {
                background-color: #2c281e;
                color: #e5a823;
                font-weight: bold;
                border-left: 2px solid #e5a823;
            }
        """)
        self.list_layers.itemClicked.connect(self._on_item_clicked)
        layout.addWidget(self.list_layers)

        # 3. Dedicated Red Area Controls
        self.properties_box = QFrame(self)
        self.properties_box.setStyleSheet("""
            QFrame {
                background-color: #181818;
                border: 1px solid #2c2c2c;
                border-radius: 4px;
                padding: 4px;
            }
            QPushButton {
                background-color: #242424;
                border: 1px solid #3a3a3a;
                color: #dcdcdc;
                padding: 4px 6px;
                border-radius: 3px;
                font-size: 10px;
                font-weight: 500;
            }
            QPushButton:hover {
                background-color: #333333;
                border-color: #e5a823;
                color: #ffffff;
            }
            QPushButton:checked {
                background-color: #3d3215;
                border-color: #e5a823;
                color: #e5a823;
                font-weight: bold;
            }
            QPushButton:pressed {
                background-color: #e5a823;
                color: #121212;
            }
        """)
        prop_layout = QVBoxLayout(self.properties_box)
        prop_layout.setContentsMargins(4, 4, 4, 4)
        prop_layout.setSpacing(4)

        # Row A: Primary Actions (Contact Sheet, View Single, Export)
        row_actions = QHBoxLayout()
        row_actions.setSpacing(4)

        self.btn_contact_sheet = QPushButton("Contact Sheet", self)
        self.btn_contact_sheet.setCheckable(True)
        self.btn_contact_sheet.setIcon(get_svg_icon("layout", color_normal="#cccccc", color_active="#e5a823", size=13))
        self.btn_contact_sheet.setToolTip("View all EXR passes simultaneously in an adaptive contact sheet grid")
        self.btn_contact_sheet.toggled.connect(self._on_contact_sheet_toggled)
        row_actions.addWidget(self.btn_contact_sheet, stretch=1)

        self.btn_view_single = QPushButton("View Single", self)
        self.btn_view_single.setIcon(get_svg_icon("media_image", color_normal="#cccccc", color_active="#e5a823", size=13))
        self.btn_view_single.setToolTip("Exit contact sheet and return to beauty / primary pass")
        self.btn_view_single.clicked.connect(self._on_view_single_clicked)
        row_actions.addWidget(self.btn_view_single, stretch=1)

        self.btn_export = QPushButton("Export...", self)
        self.btn_export.setIcon(get_svg_icon("export", color_normal="#38d9c6", color_active="#e5a823", size=13))
        self.btn_export.setToolTip("Export Contact Sheet frame snapshot or sequence")
        self.btn_export.clicked.connect(self.export_contact_sheet_requested.emit)
        row_actions.addWidget(self.btn_export, stretch=1)
        prop_layout.addLayout(row_actions)

        # Row B: Channel Soloing, Auto-Depth & Header Info
        row_tools = QHBoxLayout()
        row_tools.setSpacing(2)

        self.channel_group = QButtonGroup(self)
        self.channel_group.setExclusive(True)

        channels = [("RGB", 0), ("R", 1), ("G", 2), ("B", 3), ("A", 4)]
        self._chan_btns = {}
        for label, mode in channels:
            btn = QPushButton(label, self)
            btn.setCheckable(True)
            btn.setFixedWidth(28)
            btn.setToolTip(f"Solo {label} channel")
            if mode == 0:
                btn.setChecked(True)
            btn.clicked.connect(lambda checked, m=mode: self.channel_mode_requested.emit(m))
            self.channel_group.addButton(btn)
            self._chan_btns[mode] = btn
            row_tools.addWidget(btn)

        row_tools.addSpacing(4)

        # Auto-Depth Toggle
        self.btn_auto_depth = QPushButton("Z-Norm", self)
        self.btn_auto_depth.setCheckable(True)
        self.btn_auto_depth.setToolTip("Auto-normalize float range on Z/Depth passes to prevent black clipping")
        self.btn_auto_depth.toggled.connect(self._on_auto_depth_toggled)
        row_tools.addWidget(self.btn_auto_depth)

        # EXR Header Metadata
        self.btn_info = QPushButton("Header", self)
        self.btn_info.setIcon(get_svg_icon("reveal", color_normal="#38d9c6", size=12))
        self.btn_info.setToolTip("Inspect full OpenEXR header attributes and channels")
        self.btn_info.clicked.connect(self.metadata_requested.emit)
        row_tools.addWidget(self.btn_info)

        prop_layout.addLayout(row_tools)
        layout.addWidget(self.properties_box)

        self.refresh_list(["beauty"])

    def refresh_list(self, layers: List[str]) -> None:
        """Populate available passes list."""
        self._layers = layers if layers else ["beauty"]
        self.lbl_count.setText(f"{len(self._layers)} passes" if len(self._layers) != 1 else "1 pass")
        self.list_layers.clear()

        for lyr in self._layers:
            item = QListWidgetItem(lyr)
            self.list_layers.addItem(item)
            if lyr == self._current_layer:
                self.list_layers.setCurrentItem(item)

        self._filter_layers(self.search_input.text())

    def _filter_layers(self, filter_text: str) -> None:
        q = filter_text.strip().lower()
        for i in range(self.list_layers.count()):
            item = self.list_layers.item(i)
            item.setHidden(bool(q and q not in item.text().lower()))

    def _on_search_return_pressed(self) -> None:
        """Instant solo top filtered pass when user presses Enter in search box."""
        q = self.search_input.text().strip().lower()
        if not q:
            return
        # 1. Exact match takes top priority
        for i in range(self.list_layers.count()):
            item = self.list_layers.item(i)
            if item.text().lower() == q:
                self.list_layers.setCurrentItem(item)
                self._on_item_clicked(item)
                return
        # 2. First visible item
        for i in range(self.list_layers.count()):
            item = self.list_layers.item(i)
            if not item.isHidden():
                self.list_layers.setCurrentItem(item)
                self._on_item_clicked(item)
                break

    def set_current_layer(self, layer: str) -> None:
        self._current_layer = layer
        for i in range(self.list_layers.count()):
            item = self.list_layers.item(i)
            if item.text() == layer:
                self.list_layers.setCurrentItem(item)
                break

    def set_contact_sheet_active(self, active: bool) -> None:
        """Update toggle visual state without re-triggering signal loop."""
        self._contact_sheet_active = active
        self.btn_contact_sheet.blockSignals(True)
        self.btn_contact_sheet.setChecked(active)
        self.btn_contact_sheet.blockSignals(False)

    def _on_item_clicked(self, item: QListWidgetItem) -> None:
        self._current_layer = item.text()
        # Selecting an individual pass turns off contact sheet mode
        if self._contact_sheet_active:
            self.set_contact_sheet_active(False)
        self.layer_changed.emit(self._current_layer)

    def _on_contact_sheet_toggled(self, checked: bool) -> None:
        self._contact_sheet_active = checked
        self.contact_sheet_toggled.emit(checked)

    def _on_view_single_clicked(self) -> None:
        self.set_contact_sheet_active(False)
        self.view_single_requested.emit()

    def _on_auto_depth_toggled(self, checked: bool) -> None:
        self._auto_depth_active = checked
        self.auto_depth_toggled.emit(checked)
