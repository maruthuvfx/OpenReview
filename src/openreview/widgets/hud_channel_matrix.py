"""
Interactive Multi-Channel EXR Layer Matrix & Channel Soloing HUD Widget.
Provides an interactive matrix controller to inspect, route, swizzle, and solo
arbitrary EXR channels (Color passes, Cryptomatte, Depth/Z, Normals, Motion Vectors)
directly in the viewport with real-time GPU acceleration.
"""
from __future__ import annotations

from typing import Dict, List, Optional
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from openreview.core.exr_matrix import (
    EXRChannelInfo,
    EXRMatrixMapping,
    SemanticPassType,
    build_layer_preset,
)
from openreview.resources.svg_icons import get_svg_icon


class HUDChannelMatrixWidget(QWidget):
    """
    Interactive AOV Channel Matrix & Swizzling Controller Panel.
    """

    mapping_changed = Signal(EXRMatrixMapping)
    channel_mode_changed = Signal(int, bool)     # (mode: 0..5, is_greyscale: bool)
    channel_enable_changed = Signal(tuple)       # (r: bool, g: bool, b: bool, a: bool)
    normals_remap_toggled = Signal(bool)

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setMinimumWidth(260)

        self._all_channels: List[EXRChannelInfo] = []
        self._layers_dict: Dict[str, List[EXRChannelInfo]] = {}
        self._current_mapping = EXRMatrixMapping()
        self._is_greyscale_solo: bool = True
        self._active_solo_channel: int = 0  # 0: RGB

        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        # 1. Header Row
        hdr_layout = QHBoxLayout()
        hdr_layout.setContentsMargins(0, 0, 0, 0)
        title_lbl = QLabel("EXR Channel Matrix & Swizzler", self)
        title_lbl.setStyleSheet("font-weight: bold; color: #e5a823; font-size: 11px;")
        hdr_layout.addWidget(title_lbl)

        self.lbl_stats = QLabel("0 channels", self)
        self.lbl_stats.setStyleSheet(
            "background-color: #262626; color: #aaaaaa; padding: 1px 6px; border-radius: 9px; font-size: 10px;"
        )
        hdr_layout.addWidget(self.lbl_stats, 0, Qt.AlignmentFlag.AlignRight)
        layout.addLayout(hdr_layout)

        # 2. Preset Quick-Bar (1-Click Switchers)
        preset_layout = QHBoxLayout()
        preset_layout.setSpacing(3)

        self.btn_preset_beauty = QPushButton("Beauty", self)
        self.btn_preset_beauty.setToolTip("Route primary Beauty RGB pass")
        self.btn_preset_beauty.clicked.connect(lambda: self.apply_preset("beauty"))
        preset_layout.addWidget(self.btn_preset_beauty)

        self.btn_preset_norm = QPushButton("Normals", self)
        self.btn_preset_norm.setToolTip("Route surface normals (N.X, N.Y, N.Z) with [-1..1]->[0..1] remap")
        self.btn_preset_norm.clicked.connect(lambda: self.apply_preset("normal"))
        preset_layout.addWidget(self.btn_preset_norm)

        self.btn_preset_depth = QPushButton("Depth", self)
        self.btn_preset_depth.setToolTip("Route Depth / Z pass with auto-normalization")
        self.btn_preset_depth.clicked.connect(lambda: self.apply_preset("depth"))
        preset_layout.addWidget(self.btn_preset_depth)

        self.btn_preset_motion = QPushButton("Motion", self)
        self.btn_preset_motion.setToolTip("Route Motion / Velocity vectors")
        self.btn_preset_motion.clicked.connect(lambda: self.apply_preset("motion"))
        preset_layout.addWidget(self.btn_preset_motion)

        self.btn_preset_crypto = QPushButton("Crypto", self)
        self.btn_preset_crypto.setToolTip("Preview Cryptomatte manifest pseudo-color IDs")
        self.btn_preset_crypto.clicked.connect(lambda: self.apply_preset("crypto"))
        preset_layout.addWidget(self.btn_preset_crypto)

        layout.addLayout(preset_layout)

        # 3. GPU Channel Solo Toolbar
        solo_frame = QFrame(self)
        solo_frame.setStyleSheet("""
            QFrame {
                background-color: #1a1a1a;
                border: 1px solid #2e2e2e;
                border-radius: 4px;
                padding: 2px;
            }
            QPushButton {
                background-color: #242424;
                border: 1px solid #383838;
                color: #dcdcdc;
                padding: 3px 5px;
                border-radius: 2px;
                font-size: 10px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #333333;
                border-color: #e5a823;
            }
            QPushButton:checked {
                background-color: #3d3215;
                border-color: #e5a823;
                color: #e5a823;
            }
        """)
        solo_layout = QHBoxLayout(solo_frame)
        solo_layout.setContentsMargins(4, 2, 4, 2)
        solo_layout.setSpacing(2)

        lbl_solo = QLabel("Solo:", solo_frame)
        lbl_solo.setStyleSheet("color: #888; font-size: 10px; border: none;")
        solo_layout.addWidget(lbl_solo)

        self.solo_group = QButtonGroup(self)
        self.solo_group.setExclusive(True)

        solo_modes = [("RGB", 0), ("R", 1), ("G", 2), ("B", 3), ("A", 4), ("Y", 5)]
        self._solo_btns = {}
        for label, mode in solo_modes:
            btn = QPushButton(label, solo_frame)
            btn.setCheckable(True)
            btn.setFixedWidth(26)
            if mode == 0:
                btn.setChecked(True)
            btn.clicked.connect(lambda checked, m=mode: self._on_solo_clicked(m))
            self.solo_group.addButton(btn)
            self._solo_btns[mode] = btn
            solo_layout.addWidget(btn)

        solo_layout.addSpacing(6)

        # Greyscale / Color Solo Mode Toggle
        self.btn_mono = QPushButton("Mono", solo_frame)
        self.btn_mono.setCheckable(True)
        self.btn_mono.setChecked(True)
        self.btn_mono.setToolTip("Toggle Greyscale (Mono) vs Isolated Color for R/G/B soloing")
        self.btn_mono.toggled.connect(self._on_mono_toggled)
        solo_layout.addWidget(self.btn_mono)

        layout.addWidget(solo_frame)

        # 4. Interactive Channel Matrix Routing Grid
        matrix_frame = QFrame(self)
        matrix_frame.setStyleSheet("""
            QFrame {
                background-color: #141414;
                border: 1px solid #282828;
                border-radius: 4px;
                padding: 4px;
            }
            QLabel {
                font-size: 10px;
                font-weight: bold;
                border: none;
            }
            QComboBox {
                background-color: #1e1e1e;
                border: 1px solid #333333;
                border-radius: 2px;
                color: #e0e0e0;
                padding: 2px 4px;
                font-size: 10px;
                min-height: 20px;
            }
            QComboBox:hover {
                border-color: #e5a823;
            }
            QComboBox::drop-down {
                border: none;
                width: 14px;
            }
        """)
        matrix_layout = QGridLayout(matrix_frame)
        matrix_layout.setContentsMargins(4, 4, 4, 4)
        matrix_layout.setSpacing(4)

        # Layer / Pass Row
        lbl_lyr = QLabel("Layer ←", matrix_frame)
        lbl_lyr.setStyleSheet("color: #e5a823;")
        self.combo_layer = QComboBox(matrix_frame)
        self.combo_layer.setToolTip("Select an entire layer to automatically route its sub-channels to RGBA")
        self.combo_layer.currentIndexChanged.connect(self._on_layer_selector_changed)
        matrix_layout.addWidget(lbl_lyr, 0, 0)
        matrix_layout.addWidget(self.combo_layer, 0, 1)

        # R Channel Row
        lbl_r = QLabel("R ←", matrix_frame)
        lbl_r.setStyleSheet("color: #ff5555;")
        self.combo_r = QComboBox(matrix_frame)
        self.combo_r.currentIndexChanged.connect(self._on_routing_changed)
        matrix_layout.addWidget(lbl_r, 1, 0)
        matrix_layout.addWidget(self.combo_r, 1, 1)

        # G Channel Row
        lbl_g = QLabel("G ←", matrix_frame)
        lbl_g.setStyleSheet("color: #55ff55;")
        self.combo_g = QComboBox(matrix_frame)
        self.combo_g.currentIndexChanged.connect(self._on_routing_changed)
        matrix_layout.addWidget(lbl_g, 2, 0)
        matrix_layout.addWidget(self.combo_g, 2, 1)

        # B Channel Row
        lbl_b = QLabel("B ←", matrix_frame)
        lbl_b.setStyleSheet("color: #5599ff;")
        self.combo_b = QComboBox(matrix_frame)
        self.combo_b.currentIndexChanged.connect(self._on_routing_changed)
        matrix_layout.addWidget(lbl_b, 3, 0)
        matrix_layout.addWidget(self.combo_b, 3, 1)

        # A Channel Row
        lbl_a = QLabel("A ←", matrix_frame)
        lbl_a.setStyleSheet("color: #dddddd;")
        self.combo_a = QComboBox(matrix_frame)
        self.combo_a.currentIndexChanged.connect(self._on_routing_changed)
        matrix_layout.addWidget(lbl_a, 4, 0)
        matrix_layout.addWidget(self.combo_a, 4, 1)

        # Quick Actions Row
        action_layout = QHBoxLayout()
        action_layout.setSpacing(4)

        self.btn_swap_rb = QPushButton("⇄ Swap R/B", matrix_frame)
        self.btn_swap_rb.setStyleSheet("font-size: 10px; padding: 2px 4px;")
        self.btn_swap_rb.setToolTip("Swap Red and Blue channel assignments")
        self.btn_swap_rb.clicked.connect(self._swap_rb)
        action_layout.addWidget(self.btn_swap_rb)

        self.chk_norm_remap = QCheckBox("Remap Normals", matrix_frame)
        self.chk_norm_remap.setStyleSheet("color: #bbb; font-size: 10px; border: none;")
        self.chk_norm_remap.setToolTip("Hardware remap [-1, 1] to [0, 1] range")
        self.chk_norm_remap.toggled.connect(self._on_norm_remap_toggled)
        action_layout.addWidget(self.chk_norm_remap)

        matrix_layout.addLayout(action_layout, 5, 0, 1, 2)
        layout.addWidget(matrix_frame)

        # 5. Search / Filter Input
        self.search_input = QLineEdit(self)
        self.search_input.setPlaceholderText("Filter channels or layers (press Enter to solo)...")
        self.search_input.setClearButtonEnabled(True)
        self.search_input.setStyleSheet("""
            QLineEdit {
                background-color: #141414;
                border: 1px solid #333333;
                border-radius: 3px;
                color: #e0e0e0;
                padding: 3px 6px;
                font-size: 10px;
            }
            QLineEdit:focus {
                border-color: #e5a823;
            }
        """)
        self.search_input.textChanged.connect(self._filter_table)
        self.search_input.returnPressed.connect(self._on_search_return_pressed)
        layout.addWidget(self.search_input)

        # 6. Channels Table
        self.table = QTableWidget(self)
        self.table.setColumnCount(3)
        self.table.setHorizontalHeaderLabels(["Channel", "Type", "Layer"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.table.setStyleSheet("""
            QTableWidget {
                background-color: #121212;
                border: 1px solid #282828;
                color: #cccccc;
                font-size: 10px;
                min-height: 120px;
            }
            QTableWidget::item:hover {
                background-color: #1e1e1e;
            }
            QTableWidget::item:selected {
                background-color: #2c281e;
                color: #e5a823;
                font-weight: bold;
            }
            QHeaderView::section {
                background-color: #1c1c1c;
                color: #888888;
                font-size: 10px;
                border: 1px solid #242424;
                padding: 2px;
            }
        """)
        self.table.itemDoubleClicked.connect(self._on_table_double_clicked)
        layout.addWidget(self.table)

    def populate_matrix(
        self,
        layers_dict: Dict[str, List[EXRChannelInfo]],
        all_channels: List[EXRChannelInfo],
    ) -> None:
        """Populate available EXR channels and layers into matrix controller."""
        self._layers_dict = layers_dict
        self._all_channels = all_channels

        n_ch = len(all_channels)
        n_lyr = len(layers_dict)
        self.lbl_stats.setText(f"{n_ch} ch | {n_lyr} lyr")

        # Update layer selector combobox
        if hasattr(self, "combo_layer"):
            self.combo_layer.blockSignals(True)
            self.combo_layer.clear()
            self.combo_layer.addItem("<Custom / Manual>", None)
            for lyr in layers_dict.keys():
                self.combo_layer.addItem(lyr, lyr)
            self.combo_layer.blockSignals(False)

        # Update channel comboboxes with formatted [Layer] Channel names
        for combo in (self.combo_r, self.combo_g, self.combo_b, self.combo_a):
            combo.blockSignals(True)
            combo.clear()
            combo.addItem("<None>", None)
            for c in all_channels:
                label = f"[{c.layer}] {c.name}" if c.layer else c.name
                combo.addItem(label, c.name)
            combo.blockSignals(False)

        # Select Beauty defaults
        self.apply_preset("beauty")

        # Update table
        self.table.setRowCount(len(all_channels))
        for row, c in enumerate(all_channels):
            item_name = QTableWidgetItem(c.name)
            item_type = QTableWidgetItem(c.pixel_type)
            item_type.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            item_lyr = QTableWidgetItem(c.layer)
            self.table.setItem(row, 0, item_name)
            self.table.setItem(row, 1, item_type)
            self.table.setItem(row, 2, item_lyr)

    def _on_layer_selector_changed(self, index: int) -> None:
        if not hasattr(self, "combo_layer"):
            return
        lyr = self.combo_layer.currentData()
        if lyr:
            self.apply_preset(lyr)

    def apply_preset(self, preset_type: str) -> None:
        """Apply an intelligent mapping preset based on available channels."""
        target_layer = None
        preset_lower = preset_type.lower()

        # 1. Exact or case-insensitive match on layer name
        for lyr in self._layers_dict.keys():
            if lyr.lower() == preset_lower:
                target_layer = lyr
                break

        # 2. Semantic keyword match
        if not target_layer:
            if preset_lower in ("beauty", "rgb", "rgba"):
                for lyr in self._layers_dict.keys():
                    if lyr.lower() in ("beauty", "rgb", "rgba"):
                        target_layer = lyr
                        break
            elif preset_lower in ("normal", "normals", "n"):
                for lyr in self._layers_dict.keys():
                    ll = lyr.lower()
                    if ll in ("n", "norm", "normal", "normals") or any(n in ll for n in ("norm", "normal", "n_")):
                        target_layer = lyr
                        break
            elif preset_lower in ("depth", "z"):
                for lyr in self._layers_dict.keys():
                    ll = lyr.lower()
                    if ll in ("z", "depth") or "depth" in ll:
                        target_layer = lyr
                        break
            elif preset_lower in ("motion", "vel", "velocity", "vector"):
                for lyr in self._layers_dict.keys():
                    ll = lyr.lower()
                    if any(m in ll for m in ("motion", "vector", "forward", "vel")):
                        target_layer = lyr
                        break
            elif "crypto" in preset_lower:
                for lyr in self._layers_dict.keys():
                    if "crypto" in lyr.lower():
                        target_layer = lyr
                        break

        # 3. Substring match
        if not target_layer:
            for lyr in self._layers_dict.keys():
                if preset_lower in lyr.lower():
                    target_layer = lyr
                    break

        if not target_layer and self._layers_dict:
            target_layer = list(self._layers_dict.keys())[0]

        channels = self._layers_dict.get(target_layer, []) if target_layer else []
        mapping = build_layer_preset(target_layer or "beauty", channels)
        self.set_mapping(mapping)

    def set_mapping(self, mapping: EXRMatrixMapping) -> None:
        """Update comboboxes and active mapping state."""
        self._current_mapping = mapping

        def _select_channel(combo: QComboBox, name: Optional[str]) -> None:
            combo.blockSignals(True)
            if not name:
                combo.setCurrentIndex(0)
            else:
                idx = combo.findData(name)
                if idx >= 0:
                    combo.setCurrentIndex(idx)
                else:
                    combo.setCurrentIndex(0)
            combo.blockSignals(False)

        _select_channel(self.combo_r, mapping.r_channel)
        _select_channel(self.combo_g, mapping.g_channel)
        _select_channel(self.combo_b, mapping.b_channel)
        _select_channel(self.combo_a, mapping.a_channel)

        self.chk_norm_remap.blockSignals(True)
        self.chk_norm_remap.setChecked(mapping.remap_normals)
        self.chk_norm_remap.blockSignals(False)

        # Synchronize combo_layer visual selection
        if hasattr(self, "combo_layer"):
            self.combo_layer.blockSignals(True)
            matched_lyr = None
            if mapping.crypto_layer:
                matched_lyr = mapping.crypto_layer
            else:
                for lyr, chs in self._layers_dict.items():
                    ch_names = {c.name for c in chs}
                    if mapping.r_channel in ch_names:
                        matched_lyr = lyr
                        break
            if matched_lyr:
                idx = self.combo_layer.findData(matched_lyr)
                self.combo_layer.setCurrentIndex(idx if idx >= 0 else 0)
            else:
                self.combo_layer.setCurrentIndex(0)
            self.combo_layer.blockSignals(False)

        self.mapping_changed.emit(self._current_mapping)

    def _on_routing_changed(self) -> None:
        r = self.combo_r.currentData()
        g = self.combo_g.currentData()
        b = self.combo_b.currentData()
        a = self.combo_a.currentData()

        self._current_mapping = EXRMatrixMapping(
            r_channel=r,
            g_channel=g,
            b_channel=b,
            a_channel=a,
            remap_normals=self.chk_norm_remap.isChecked(),
        )
        self.mapping_changed.emit(self._current_mapping)

    def _swap_rb(self) -> None:
        r = self.combo_r.currentData()
        b = self.combo_b.currentData()
        idx_r = self.combo_r.currentIndex()
        idx_b = self.combo_b.currentIndex()

        self.combo_r.blockSignals(True)
        self.combo_b.blockSignals(True)
        self.combo_r.setCurrentIndex(idx_b)
        self.combo_b.setCurrentIndex(idx_r)
        self.combo_r.blockSignals(False)
        self.combo_b.blockSignals(False)
        self._on_routing_changed()

    def _on_norm_remap_toggled(self, checked: bool) -> None:
        self._current_mapping.remap_normals = checked
        self.normals_remap_toggled.emit(checked)
        self.mapping_changed.emit(self._current_mapping)

    def _on_solo_clicked(self, mode: int) -> None:
        self._active_solo_channel = mode
        self.channel_mode_changed.emit(mode, self._is_greyscale_solo)

    def _on_mono_toggled(self, checked: bool) -> None:
        self._is_greyscale_solo = checked
        self.btn_mono.setText("Mono" if checked else "Color")
        self.channel_mode_changed.emit(self._active_solo_channel, checked)

    def _filter_table(self, text: str) -> None:
        q = text.strip().lower()
        for r in range(self.table.rowCount()):
            ch_item = self.table.item(r, 0)
            type_item = self.table.item(r, 1)
            lyr_item = self.table.item(r, 2)
            match = (
                (ch_item and q in ch_item.text().lower())
                or (type_item and q in type_item.text().lower())
                or (lyr_item and q in lyr_item.text().lower())
            )
            self.table.setRowHidden(r, bool(q and not match))

    def _on_search_return_pressed(self) -> None:
        """Instant solo viewport when user presses Enter in search box."""
        q = self.search_input.text().strip().lower()
        if not q:
            return

        # 1. Match against known layer names
        for lyr in self._layers_dict.keys():
            if q == lyr.lower() or q in lyr.lower():
                self.apply_preset(lyr)
                return

        # 2. Pick top visible row from table
        for r in range(self.table.rowCount()):
            if not self.table.isRowHidden(r):
                ch_name = self.table.item(r, 0).text()
                lyr_name = self.table.item(r, 2).text()
                if lyr_name in self._layers_dict:
                    self.apply_preset(lyr_name)
                else:
                    self.set_mapping(
                        EXRMatrixMapping(
                            r_channel=ch_name,
                            g_channel=ch_name,
                            b_channel=ch_name,
                            a_channel=None,
                        )
                    )
                return

    def _on_table_double_clicked(self, item: QTableWidgetItem) -> None:
        """Double clicking a channel in the table routes it to R, G, B immediately."""
        ch_name = self.table.item(item.row(), 0).text()
        self.set_mapping(
            EXRMatrixMapping(
                r_channel=ch_name,
                g_channel=ch_name,
                b_channel=ch_name,
                a_channel=None,
            )
        )
