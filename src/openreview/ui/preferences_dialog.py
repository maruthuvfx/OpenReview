"""
Preferences Configuration Dialog for OpenReview.
Provides General, Playback & GPU Optimization, Caching, and Keyboard Shortcuts tabs.
Styled with 3DEqualizer dark studio aesthetic.
"""
from __future__ import annotations

import os
import shutil
import tempfile
from dataclasses import asdict
from typing import Optional, Dict
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeyEvent, QKeySequence, QColor
from PySide6.QtWidgets import (
    QDialog,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QTabWidget,
    QFormLayout,
    QDoubleSpinBox,
    QSpinBox,
    QCheckBox,
    QComboBox,
    QPushButton,
    QLabel,
    QGroupBox,
    QLineEdit,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QMessageBox,
    QScrollArea,
    QSlider,
    QColorDialog,
    QFileDialog,
)

from openreview.widgets.draggable_scroll_area import BlenderScrollArea

from openreview.core.preferences import PreferencesManager, UserPreferences
from openreview.core.shortcuts import ShortcutManager, ShortcutAction
from openreview.color.standard_colorspaces import STANDARD_COLORSPACES
from openreview.color.ocio_manager import OCIOManager
from openreview.ui.ocio_dialog import OCIOConfigWidget
from openreview.core.gpu_detector import (
    detect_system_gpus,
    get_active_gpu_device,
    GpuDevice,
)


class KeyRecorderDialog(QDialog):
    """
    Modal dialog that captures any pressed key combination in real time,
    validates for duplicate assignments, and prompts for conflict resolution.
    """
    def __init__(self, action: ShortcutAction, shortcut_mgr: ShortcutManager, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle("Record Key Combination")
        self.setFixedSize(440, 210)
        self.setModal(True)
        self.action = action
        self.shortcut_mgr = shortcut_mgr
        self.recorded_seq = ""

        self.setStyleSheet("""
            QDialog {
                background-color: #1e1e1e;
                color: #e0e0e0;
            }
            QLabel {
                color: #cccccc;
            }
            QPushButton {
                background-color: #2b2b2b;
                border: 1px solid #444444;
                color: #ffffff;
                padding: 6px 14px;
                border-radius: 3px;
            }
            QPushButton:hover {
                border-color: #e5a823;
            }
        """)

        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(18, 18, 18, 18)

        lbl_title = QLabel(f"<b>Assigning shortcut for:</b> <span style='color: #e5a823;'>{action.name}</span>")
        lbl_title.setStyleSheet("font-size: 13px;")
        layout.addWidget(lbl_title)

        self.lbl_instruction = QLabel("Press any key combination now...\n(e.g., Space, Ctrl+Shift+P, F5, Alt+Left, Slash)\n\nPress Esc to cancel.")
        self.lbl_instruction.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_instruction.setStyleSheet("""
            background-color: #141414;
            border: 2px dashed #444444;
            border-radius: 6px;
            padding: 16px;
            color: #88aaff;
            font-size: 12px;
        """)
        layout.addWidget(self.lbl_instruction)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        btn_cancel = QPushButton("Cancel")
        btn_cancel.clicked.connect(self.reject)
        btn_row.addWidget(btn_cancel)
        layout.addLayout(btn_row)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        key = event.key()
        # Ignore modifier keys alone (user still holding them down)
        if key in (Qt.Key.Key_Shift, Qt.Key.Key_Control, Qt.Key.Key_Alt, Qt.Key.Key_Meta):
            return

        if key == Qt.Key.Key_Escape:
            self.reject()
            return

        key_combo = event.keyCombination()
        seq = QKeySequence(key_combo)
        seq_str = seq.toString(QKeySequence.SequenceFormat.PortableText)
        if not seq_str and event.text():
            seq_str = event.text().upper()

        if not seq_str:
            return

        # Check for assignment conflicts
        conflict = self.shortcut_mgr.find_conflict(seq_str, exclude_action_id=self.action.action_id)
        if conflict:
            reply = QMessageBox.question(
                self,
                "Shortcut Conflict Detected",
                f"Shortcut '{seq_str}' is already assigned to:\n\n"
                f"• {conflict.name} ({conflict.category})\n\n"
                f"Do you want to reassign '{seq_str}' to '{self.action.name}'?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return
            # Clear conflicting action's shortcut
            self.shortcut_mgr.set_shortcut(conflict.action_id, "")

        self.recorded_seq = seq_str
        self.accept()


class PreferencesDialog(QDialog):
    """Studio preferences configuration dialog with comprehensive shortcut customization."""

    hud_opacity_changed = Signal(float)

    def __init__(self, mgr: Optional[PreferencesManager] = None, parent: Optional[QWidget] = None, ocio_manager: Optional[OCIOManager] = None):
        if isinstance(mgr, QWidget) and parent is None:
            parent = mgr
            mgr = None
        super().__init__(parent)
        self.setWindowTitle("OpenReview Preferences")
        self.resize(760, 560)
        self.mgr = mgr or PreferencesManager.instance()
        self.shortcut_mgr = ShortcutManager.instance()
        self.ocio_manager = ocio_manager or getattr(parent, "ocio_manager", None) or OCIOManager()
        self._current_prefs = self.mgr.prefs

        # Temporary local shortcut working state: action_id -> shortcut_str
        self._working_shortcuts: Dict[str, str] = {}
        for act in self.shortcut_mgr.get_actions():
            self._working_shortcuts[act.action_id] = act.effective_shortcut

        self._setup_ui()
        self._load_from_prefs(self._current_prefs)

    def _wrap_tab_in_scroll(self, widget: QWidget) -> BlenderScrollArea:
        scroll = BlenderScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        scroll.setWidget(widget)
        return scroll

    def _setup_ui(self) -> None:
        self.setStyleSheet("""
            QDialog {
                background-color: #1e1e1e;
                color: #e0e0e0;
            }
            QTabWidget::pane {
                border: 1px solid #333333;
                background-color: #1e1e1e;
            }
            QTabBar::tab {
                background: #252525;
                color: #aaaaaa;
                padding: 7px 18px;
                border-top-left-radius: 3px;
                border-top-right-radius: 3px;
                font-weight: 500;
            }
            QTabBar::tab:selected {
                background: #333333;
                color: #e5a823;
                font-weight: bold;
            }
            QGroupBox {
                border: 1px solid #333333;
                margin-top: 6px;
                padding-top: 10px;
                color: #e5a823;
                font-weight: bold;
            }
            QCheckBox {
                color: #dddddd;
            }
            QLabel {
                color: #cccccc;
            }
            QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {
                background-color: #121212;
                border: 1px solid #3a3a3a;
                color: #ffffff;
                padding: 4px 6px;
                border-radius: 2px;
            }
            QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {
                border-color: #e5a823;
            }
            QPushButton {
                background-color: #2b2b2b;
                border: 1px solid #444444;
                color: #ffffff;
                padding: 5px 12px;
                border-radius: 3px;
            }
            QPushButton:hover {
                border-color: #e5a823;
            }
            QTableWidget {
                background-color: #141414;
                border: 1px solid #333333;
                color: #dddddd;
                gridline-color: #262626;
                selection-background-color: #3d3420;
                selection-color: #ffffff;
            }
            QHeaderView::section {
                background-color: #252525;
                color: #bbbbbb;
                padding: 4px;
                border: 1px solid #333333;
                font-weight: bold;
            }
        """ + __import__("openreview.ui.theme", fromlist=["get_spinbox_arrow_stylesheet"]).get_spinbox_arrow_stylesheet())

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        self.tabs = QTabWidget(self)

        # -------------------------------------------------------------
        # Tab 1: General & Playback Performance
        # -------------------------------------------------------------
        tab_general = QWidget()
        gen_layout = QVBoxLayout(tab_general)
        gen_layout.setContentsMargins(12, 12, 12, 12)
        gen_layout.setSpacing(12)

        form_gen = QFormLayout()
        form_gen.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        self.spin_fps = QDoubleSpinBox()
        self.spin_fps.setRange(1.0, 120.0)
        self.spin_fps.setDecimals(3)
        self.spin_fps.setSuffix(" fps")
        form_gen.addRow("Default FPS:", self.spin_fps)

        gen_layout.addLayout(form_gen)

        grp_startup = QGroupBox("Startup & Playback Behavior")
        grp_layout = QVBoxLayout(grp_startup)
        grp_layout.setSpacing(8)

        self.chk_play_on_startup = QCheckBox("Play on startup")
        self.chk_fullscreen = QCheckBox("Start in full screen mode")
        self.chk_canvas_scrub = QCheckBox("Mouse scrub in canvas to scrub over timeline")
        self.chk_auto_retime = QCheckBox("Auto retime mismatched FPS media")

        grp_layout.addWidget(self.chk_play_on_startup)
        grp_layout.addWidget(self.chk_fullscreen)
        grp_layout.addWidget(self.chk_canvas_scrub)
        grp_layout.addWidget(self.chk_auto_retime)
        gen_layout.addWidget(grp_startup)

        # Floating HUD & Overlay Appearance
        grp_hud = QGroupBox("Floating HUD & Canvas Overlay Appearance")
        hud_layout = QVBoxLayout(grp_hud)
        hud_layout.setSpacing(6)

        hud_row = QHBoxLayout()
        lbl_hud_op = QLabel("Floating HUD Opacity:")
        self.slider_hud_opacity = QSlider(Qt.Orientation.Horizontal)
        self.slider_hud_opacity.setRange(30, 100)
        self.slider_hud_opacity.setValue(85)
        self.slider_hud_opacity.setSingleStep(5)
        self.lbl_hud_opacity_val = QLabel("85%")
        self.lbl_hud_opacity_val.setFixedWidth(40)
        self.slider_hud_opacity.valueChanged.connect(self._on_hud_opacity_slider_changed)

        hud_row.addWidget(lbl_hud_op)
        hud_row.addWidget(self.slider_hud_opacity, 1)
        hud_row.addWidget(self.lbl_hud_opacity_val)
        hud_layout.addLayout(hud_row)
        gen_layout.addWidget(grp_hud)

        # Playback & GPU Optimization Section
        grp_perf = QGroupBox("Playback Smoothness & Hardware Optimization")
        perf_form = QFormLayout(grp_perf)
        perf_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        self.combo_texture_mode = QComboBox()
        self.combo_texture_mode.addItem("Auto (Recommended: Fast In-Place Texture)", "auto")
        self.combo_texture_mode.addItem("8-bit RGBA (Ultra-Fast for Low-Spec Laptops)", "rgba8")
        self.combo_texture_mode.addItem("16-bit Half-Float RGBA (Fast HDR / Wide Gamut)", "rgba16f")
        self.combo_texture_mode.addItem("32-bit Float RGBA (GPU Workstations)", "rgba32f")
        perf_form.addRow("Texture Transfer Mode:", self.combo_texture_mode)

        self.spin_prefetch_buffer = QSpinBox()
        self.spin_prefetch_buffer.setRange(12, 240)
        self.spin_prefetch_buffer.setSingleStep(12)
        self.spin_prefetch_buffer.setSuffix(" frames")
        perf_form.addRow("Lookahead Buffer Size:", self.spin_prefetch_buffer)

        self.spin_prefetch_threads = QSpinBox()
        self.spin_prefetch_threads.setRange(1, 32)
        self.spin_prefetch_threads.setSingleStep(1)
        self.spin_prefetch_threads.setSuffix(" threads")
        perf_form.addRow("Prefetch Worker Threads:", self.spin_prefetch_threads)

        self.chk_laptop_mode = QCheckBox("Laptop Low-Overhead Mode (Reduces memory bandwidth & thermals)")
        perf_form.addRow("", self.chk_laptop_mode)

        gen_layout.addWidget(grp_perf)

        # OS File Associations Group
        grp_assoc = QGroupBox("Operating System File Associations")
        assoc_layout = QVBoxLayout(grp_assoc)
        assoc_layout.setSpacing(6)
        lbl_assoc_info = QLabel("Associate OpenReview with .ovr session files and add 'Open with OpenReview' in Windows Explorer.")
        lbl_assoc_info.setStyleSheet("color: #888888; font-size: 11px;")
        assoc_layout.addWidget(lbl_assoc_info)

        assoc_btn_row = QHBoxLayout()
        self.btn_register_assoc = QPushButton("Register File Associations")
        self.btn_register_assoc.clicked.connect(self._on_register_file_associations_clicked)
        self.btn_unregister_assoc = QPushButton("Unregister")
        self.btn_unregister_assoc.clicked.connect(self._on_unregister_file_associations_clicked)
        self.lbl_assoc_status = QLabel("")
        assoc_btn_row.addWidget(self.btn_register_assoc)
        assoc_btn_row.addWidget(self.btn_unregister_assoc)
        assoc_btn_row.addWidget(self.lbl_assoc_status)
        assoc_btn_row.addStretch()
        assoc_layout.addLayout(assoc_btn_row)
        gen_layout.addWidget(grp_assoc)

        gen_layout.addStretch()

        self.tabs.addTab(self._wrap_tab_in_scroll(tab_general), "General & Playback")

        # -------------------------------------------------------------
        # Tab 2: Dedicated GPU Acceleration & Playback Engine
        # -------------------------------------------------------------
        tab_gpu = QWidget()
        gpu_layout = QVBoxLayout(tab_gpu)
        gpu_layout.setContentsMargins(12, 12, 12, 12)
        gpu_layout.setSpacing(12)

        # 1. Playback Engine Selection Group
        grp_engine = QGroupBox("Playback Engine & GPU Device")
        engine_form = QFormLayout(grp_engine)
        engine_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        self.combo_playback_engine = QComboBox()
        self.combo_playback_engine.addItem("OpenGL Core (Hardware Accelerated VFX Shaders)", "opengl_core")
        self.combo_playback_engine.addItem("Async PBO DMA Stream (Double-Buffered Texture Transfer)", "async_pbo")
        self.combo_playback_engine.addItem("Software Safe Mode (CPU Fallback / Low Bandwidth)", "software_safe")
        engine_form.addRow("Active Engine:", self.combo_playback_engine)

        dev_row = QHBoxLayout()
        self.combo_gpu_device = QComboBox()
        self.combo_gpu_device.currentIndexChanged.connect(self._on_selected_gpu_changed)
        dev_row.addWidget(self.combo_gpu_device, 1)

        self.btn_rescan_gpus = QPushButton("Re-scan")
        self.btn_rescan_gpus.setToolTip("Re-scan system for newly connected or updated GPUs")
        self.btn_rescan_gpus.clicked.connect(self._populate_gpu_devices)
        dev_row.addWidget(self.btn_rescan_gpus)

        engine_form.addRow("Compute / Display GPU:", dev_row)
        gpu_layout.addWidget(grp_engine)

        # 2. Live Hardware Diagnostics / Telemetry Card
        grp_telemetry = QGroupBox("Hardware Telemetry & Detected Capabilities")
        telem_layout = QVBoxLayout(grp_telemetry)
        self.lbl_gpu_telemetry = QLabel("Scanning GPU hardware...")
        self.lbl_gpu_telemetry.setStyleSheet("""
            background-color: #141414;
            border: 1px solid #333333;
            border-radius: 4px;
            padding: 10px;
            color: #88aaff;
            font-family: monospace;
            font-size: 11px;
        """)
        telem_layout.addWidget(self.lbl_gpu_telemetry)
        gpu_layout.addWidget(grp_telemetry)

        # 3. Acceleration Features & Video Pipeline
        grp_features = QGroupBox("Hardware Acceleration Features")
        feat_layout = QVBoxLayout(grp_features)
        feat_layout.setSpacing(8)

        self.chk_hw_video = QCheckBox("Enable Hardware Video Decoding (CUDA / NVDEC / D3D11VA)")
        self.chk_hw_video.setToolTip("Leverage dedicated GPU ASIC decoders for H.264/HEVC/ProRes decoding")
        feat_layout.addWidget(self.chk_hw_video)

        self.chk_opencl = QCheckBox("Enable OpenCL / GPU SIMD Acceleration")
        self.chk_opencl.setToolTip("Accelerate color matrix transforms, conversions, and compute operations on OpenCL device")
        feat_layout.addWidget(self.chk_opencl)

        self.chk_pbo_streaming = QCheckBox("Enable Asynchronous PBO Texture Streaming")
        self.chk_pbo_streaming.setToolTip("Use double-buffered Pixel Buffer Objects to upload frames asynchronously without stalls")
        feat_layout.addWidget(self.chk_pbo_streaming)

        self.chk_vsync = QCheckBox("Enable Vertical Sync (VSync - Tear-Free Display)")
        self.chk_vsync.setToolTip("Synchronize frame presentation with monitor refresh rate to prevent screen tearing")
        feat_layout.addWidget(self.chk_vsync)

        gpu_layout.addWidget(grp_features)

        # 4. Resource Allocation
        grp_resources = QGroupBox("Resource Budget")
        res_form = QFormLayout(grp_resources)
        res_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        self.spin_gpu_vram = QSpinBox()
        self.spin_gpu_vram.setRange(256, 32768)
        self.spin_gpu_vram.setSingleStep(256)
        self.spin_gpu_vram.setSuffix(" MB")
        self.spin_gpu_vram.setToolTip("Maximum GPU VRAM budget allocated for texture and PBO pools")
        res_form.addRow("GPU VRAM Budget:", self.spin_gpu_vram)

        gpu_layout.addWidget(grp_resources)
        gpu_layout.addStretch()

        self.tabs.addTab(self._wrap_tab_in_scroll(tab_gpu), "GPU Acceleration")

        # -------------------------------------------------------------
        # Tab 3: Caching Preferences
        # -------------------------------------------------------------
        tab_cache = QWidget()
        cache_layout = QVBoxLayout(tab_cache)
        cache_layout.setContentsMargins(12, 12, 12, 12)
        cache_layout.setSpacing(12)

        from openreview.core.cache import get_system_ram_stats_mb, compute_dynamic_ram_budget_mb
        total_ram_mb, _ = get_system_ram_stats_mb()
        total_ram_gb = max(64.0, round(total_ram_mb / 1024.0, 1))
        auto_budget_gb = compute_dynamic_ram_budget_mb(0) / 1024.0

        cache_form = QFormLayout()
        cache_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        self.combo_cache_mode = QComboBox()
        self.combo_cache_mode.addItem("Cache Off (Default)", "off")
        self.combo_cache_mode.addItem("Play-Ahead Cache", "play_ahead")
        self.combo_cache_mode.addItem("In/Out Region Cache (Only)", "region")
        self.combo_cache_mode.addItem("Full Sequence Cache", "all")
        cache_form.addRow("Default Cache Mode:", self.combo_cache_mode)

        self.combo_cache_toggle = QComboBox()
        self.combo_cache_toggle.addItem("In/Out Region Cache (Default)", "region")
        self.combo_cache_toggle.addItem("Play-Ahead Cache", "play_ahead")
        self.combo_cache_toggle.addItem("Full Sequence Cache", "all")
        self.combo_cache_toggle.addItem("Resume Last Active Mode", "last_active")
        cache_form.addRow("Cache Toggle Target:", self.combo_cache_toggle)

        self.chk_auto_ram_budget = QCheckBox(
            f"Adapt to Available System RAM (~{auto_budget_gb:.1f} GB / {total_ram_mb / 1024.0:.1f} GB Total)"
        )
        self.chk_auto_ram_budget.setToolTip(
            "Automatically allocate up to 80% of available system RAM (preserving at least 2 GB for the OS)"
        )
        self.chk_auto_ram_budget.toggled.connect(self._on_auto_ram_budget_toggled)
        cache_form.addRow("RAM Budget Mode:", self.chk_auto_ram_budget)

        self.spin_lookahead_gb = QDoubleSpinBox()
        self.spin_lookahead_gb.setRange(0.25, total_ram_gb)
        self.spin_lookahead_gb.setSingleStep(0.5)
        self.spin_lookahead_gb.setSuffix(" GB")
        cache_form.addRow("Look Ahead Cache Size:", self.spin_lookahead_gb)

        self.spin_region_gb = QDoubleSpinBox()
        self.spin_region_gb.setRange(0.25, total_ram_gb)
        self.spin_region_gb.setSingleStep(0.5)
        self.spin_region_gb.setSuffix(" GB")
        cache_form.addRow("Region Cache Size:", self.spin_region_gb)

        self.spin_behind_pct = QSpinBox()
        self.spin_behind_pct.setRange(0, 100)
        self.spin_behind_pct.setSuffix(" %")
        cache_form.addRow("Portion of Look Ahead Behind Playhead:", self.spin_behind_pct)

        # Tier-2 Temp Drive Disk Caching
        self.chk_enable_disk_cache = QCheckBox("Enable High-Capacity Temp Drive Disk Cache (Tier-2)")
        self.chk_enable_disk_cache.setToolTip(
            "Store and spill cached frames to temporary drive to cache more sources without exhausting RAM"
        )
        cache_form.addRow("Temp Drive Cache:", self.chk_enable_disk_cache)

        self.spin_disk_cache_gb = QDoubleSpinBox()
        self.spin_disk_cache_gb.setRange(1.0, 500.0)
        self.spin_disk_cache_gb.setSingleStep(5.0)
        self.spin_disk_cache_gb.setSuffix(" GB")
        cache_form.addRow("Disk Cache Budget:", self.spin_disk_cache_gb)

        self.btn_clear_disk_cache = QPushButton("Clear Temp Disk Cache Now")
        self.btn_clear_disk_cache.setToolTip("Immediately purges all temporary cached frames from disk")
        self.btn_clear_disk_cache.clicked.connect(self._on_clear_disk_cache_clicked)
        cache_form.addRow("Disk Maintenance:", self.btn_clear_disk_cache)

        cache_layout.addLayout(cache_form)
        cache_layout.addStretch()

        self.tabs.addTab(self._wrap_tab_in_scroll(tab_cache), "Caching")

        # -------------------------------------------------------------
        # Tab 4: Dedicated Export & Delivery Settings
        # -------------------------------------------------------------
        tab_export = self._create_tab_export_delivery()
        self.tabs.addTab(self._wrap_tab_in_scroll(tab_export), "Export & Delivery")

        # -------------------------------------------------------------
        # Tab 5: Color Management (Standard Colorspaces vs OpenColorIO)
        # -------------------------------------------------------------
        tab_color = self._create_tab_color_management()
        self.tabs.addTab(self._wrap_tab_in_scroll(tab_color), "Color Management")

        # -------------------------------------------------------------
        # Tab 5: Dedicated Keyboard Shortcuts Customization
        # -------------------------------------------------------------
        tab_shortcuts = QWidget()
        sc_layout = QVBoxLayout(tab_shortcuts)
        sc_layout.setContentsMargins(12, 12, 12, 12)
        sc_layout.setSpacing(10)

        # Search filter and category bar
        filter_row = QHBoxLayout()
        self.sc_search = QLineEdit()
        self.sc_search.setPlaceholderText("Search actions or shortcuts...")
        self.sc_search.textChanged.connect(self._refresh_shortcuts_table)
        filter_row.addWidget(QLabel("Search:"))
        filter_row.addWidget(self.sc_search, 1)

        self.sc_category_combo = QComboBox()
        self.sc_category_combo.addItem("All Categories")
        cats = sorted(list({a.category for a in self.shortcut_mgr.get_actions()}))
        for c in cats:
            self.sc_category_combo.addItem(c)
        self.sc_category_combo.currentIndexChanged.connect(self._refresh_shortcuts_table)
        filter_row.addWidget(QLabel("Category:"))
        filter_row.addWidget(self.sc_category_combo)

        sc_layout.addLayout(filter_row)

        # Shortcuts Table
        self.sc_table = QTableWidget()
        self.sc_table.setColumnCount(4)
        self.sc_table.setHorizontalHeaderLabels(["Action", "Category", "Shortcut", "Default"])
        self.sc_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.sc_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.sc_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.sc_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.sc_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.sc_table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.sc_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.sc_table.doubleClicked.connect(self._on_record_shortcut)
        sc_layout.addWidget(self.sc_table)

        # Shortcuts Action Buttons
        sc_btn_row = QHBoxLayout()
        self.btn_record_sc = QPushButton("Record Shortcut...")
        self.btn_record_sc.clicked.connect(self._on_record_shortcut)
        sc_btn_row.addWidget(self.btn_record_sc)

        self.btn_clear_sc = QPushButton("Clear Shortcut")
        self.btn_clear_sc.clicked.connect(self._on_clear_selected_shortcut)
        sc_btn_row.addWidget(self.btn_clear_sc)

        self.btn_reset_sc = QPushButton("Reset Selected to Default")
        self.btn_reset_sc.clicked.connect(self._on_reset_selected_shortcut)
        sc_btn_row.addWidget(self.btn_reset_sc)

        sc_btn_row.addStretch()

        self.btn_reset_all_sc = QPushButton("Reset All Shortcuts to Defaults")
        self.btn_reset_all_sc.clicked.connect(self._on_reset_all_shortcuts)
        sc_btn_row.addWidget(self.btn_reset_all_sc)

        sc_layout.addLayout(sc_btn_row)

        self.tabs.addTab(self._wrap_tab_in_scroll(tab_shortcuts), "Keyboard Shortcuts")

        layout.addWidget(self.tabs)

        # -------------------------------------------------------------
        # Bottom Dialog Control Buttons
        # -------------------------------------------------------------
        btn_row = QHBoxLayout()
        self.btn_defaults = QPushButton("Reset All to Defaults")
        self.btn_defaults.clicked.connect(self._reset_defaults)
        btn_row.addWidget(self.btn_defaults)

        btn_row.addStretch()

        self.btn_apply = QPushButton("Apply")
        self.btn_apply.clicked.connect(self._apply)
        btn_row.addWidget(self.btn_apply)

        self.btn_save = QPushButton("Save && Close")
        self.btn_save.setStyleSheet("background-color: #27ae60; color: #ffffff; font-weight: bold;")
        self.btn_save.clicked.connect(self._save_and_close)
        btn_row.addWidget(self.btn_save)

        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.clicked.connect(self.reject)
        btn_row.addWidget(self.btn_cancel)

        layout.addLayout(btn_row)

        self._refresh_shortcuts_table()

    def _refresh_shortcuts_table(self) -> None:
        """Populate or update rows in the shortcuts table."""
        search_txt = self.sc_search.text().strip().lower()
        selected_cat = self.sc_category_combo.currentText()

        actions = self.shortcut_mgr.get_actions()
        filtered = []
        for a in actions:
            if selected_cat != "All Categories" and a.category != selected_cat:
                continue
            cur_key = self._working_shortcuts.get(a.action_id, a.default_shortcut)
            if search_txt:
                match = (
                    search_txt in a.name.lower()
                    or search_txt in a.category.lower()
                    or search_txt in cur_key.lower()
                    or search_txt in a.default_shortcut.lower()
                    or search_txt in a.description.lower()
                )
                if not match:
                    continue
            filtered.append(a)

        self.sc_table.setRowCount(len(filtered))
        for row, a in enumerate(filtered):
            cur_key = self._working_shortcuts.get(a.action_id, a.default_shortcut)
            is_custom = (cur_key != a.default_shortcut)

            item_name = QTableWidgetItem(a.name)
            item_name.setData(Qt.ItemDataRole.UserRole, a.action_id)
            item_cat = QTableWidgetItem(a.category)
            item_sc = QTableWidgetItem(cur_key if cur_key else "<Unassigned>")
            item_def = QTableWidgetItem(a.default_shortcut)

            if is_custom:
                gold = QColor("#e5a823")
                item_name.setForeground(gold)
                item_sc.setForeground(gold)

            self.sc_table.setItem(row, 0, item_name)
            self.sc_table.setItem(row, 1, item_cat)
            self.sc_table.setItem(row, 2, item_sc)
            self.sc_table.setItem(row, 3, item_def)

    def _get_selected_action_id(self) -> Optional[str]:
        cur_row = self.sc_table.currentRow()
        if cur_row < 0:
            return None
        item = self.sc_table.item(cur_row, 0)
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def _on_record_shortcut(self) -> None:
        """Launch interactive key recorder for the selected action."""
        action_id = self._get_selected_action_id()
        if not action_id:
            QMessageBox.information(self, "No Selection", "Please select an action to record a shortcut for.")
            return

        act = self.shortcut_mgr.get_action(action_id)
        if not act:
            return

        dlg = KeyRecorderDialog(act, self.shortcut_mgr, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            new_key = dlg.recorded_seq
            self._working_shortcuts[action_id] = new_key
            self._refresh_shortcuts_table()

    def _on_clear_selected_shortcut(self) -> None:
        """Clear shortcut for selected action."""
        action_id = self._get_selected_action_id()
        if action_id:
            self._working_shortcuts[action_id] = ""
            self._refresh_shortcuts_table()

    def _on_reset_selected_shortcut(self) -> None:
        """Reset selected action shortcut to its factory default."""
        action_id = self._get_selected_action_id()
        if action_id:
            act = self.shortcut_mgr.get_action(action_id)
            if act:
                self._working_shortcuts[action_id] = act.default_shortcut
                self._refresh_shortcuts_table()

    def _on_reset_all_shortcuts(self) -> None:
        """Reset all shortcuts back to system defaults."""
        reply = QMessageBox.question(
            self,
            "Reset All Shortcuts",
            "Are you sure you want to reset all keyboard shortcuts back to default factory settings?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            for act in self.shortcut_mgr.get_actions():
                self._working_shortcuts[act.action_id] = act.default_shortcut
            self._refresh_shortcuts_table()

    def _create_tab_color_management(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(12)

        # 1. Mode Selection Group
        grp_mode = QGroupBox("Color Management Mode")
        mode_layout = QVBoxLayout(grp_mode)
        mode_form = QFormLayout()
        mode_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        self.combo_cm_mode = QComboBox()
        self.combo_cm_mode.addItem("Standard Colorspaces (Generic Broadcast & Film Curves)", "standard")
        self.combo_cm_mode.addItem("OpenColorIO (Studio OCIO v2 Pipeline)", "ocio")
        self.combo_cm_mode.currentIndexChanged.connect(self._on_cm_mode_changed)
        mode_form.addRow("Active System:", self.combo_cm_mode)
        mode_layout.addLayout(mode_form)

        lbl_mode_info = QLabel(
            "Choose between generic standard mathematical colorspaces (linear, sRGB, rec709, Cineon, etc.) "
            "or a custom studio OpenColorIO v2 configuration."
        )
        lbl_mode_info.setStyleSheet("color: #888888; font-size: 11px;")
        lbl_mode_info.setWordWrap(True)
        mode_layout.addWidget(lbl_mode_info)
        layout.addWidget(grp_mode)

        # 2. Standard Colorspaces Configuration
        self.grp_std_cm = QGroupBox("Standard Colorspace Configuration (Viewing & Internal Working Space)")
        std_layout = QVBoxLayout(self.grp_std_cm)
        std_form = QFormLayout()
        std_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        self.combo_std_default_view = QComboBox()
        self.combo_std_default_input = QComboBox()

        for cs in STANDARD_COLORSPACES:
            item_text = f"{cs.name} - {cs.description}"
            self.combo_std_default_view.addItem(item_text, cs.name)
            self.combo_std_default_input.addItem(item_text, cs.name)

        self.chk_auto_detect_input = QCheckBox("Auto-choose input colorspace based on media metadata")
        self.chk_auto_detect_input.setToolTip(
            "When enabled, OpenReview inspects container metadata, camera tags (RED, Panavision, Viper), "
            "and file formats (EXR scene-linear, DPX log) to set the input colorspace automatically on media open."
        )
        self.chk_auto_detect_input.setChecked(True)

        std_form.addRow("Default Viewing Transform:", self.combo_std_default_view)
        std_form.addRow("Default Input Colorspace:", self.combo_std_default_input)
        std_form.addRow("", self.chk_auto_detect_input)
        std_layout.addLayout(std_form)

        lbl_cs_guide = QLabel(
            "<b>Available Standard Colorspaces:</b><br>"
            "• <b>linear</b>: The internal working space for compositing operations.<br>"
            "• <b>sRGB</b>: The standard for 8-bit web and monitor displays.<br>"
            "• <b>rec709</b>: The standard for HDTV and broadcast video.<br>"
            "• <b>Cineon</b>: The standard for film scan data (logarithmic).<br>"
            "• <b>Gamma 1.8 & 2.2</b>: Standard gamma curves for different display standards.<br>"
            "• <b>Panalog</b>: A logarithmic curve used in older video systems.<br>"
            "• <b>REDLog</b>: The color encoding for RED digital cinema cameras.<br>"
            "• <b>ViperLog</b>: The color encoding for Pace/Viper cameras.<br>"
            "• <b>REDSpace</b>: A linear colorspace specific to RED cameras.<br>"
            "• <b>Raw</b>: Direct pass-through without color transformation."
        )
        lbl_cs_guide.setStyleSheet("color: #a0a0a0; font-size: 11px; background-color: #161616; padding: 10px; border-radius: 4px; border: 1px solid #2a2a2a;")
        lbl_cs_guide.setWordWrap(True)
        std_layout.addWidget(lbl_cs_guide)
        layout.addWidget(self.grp_std_cm)

        # 3. OpenColorIO Studio Configuration
        self.grp_ocio_cm = QGroupBox("OpenColorIO Studio Configuration")
        ocio_layout = QVBoxLayout(self.grp_ocio_cm)
        self.ocio_config_widget = OCIOConfigWidget(self.ocio_manager, self.grp_ocio_cm)
        ocio_layout.addWidget(self.ocio_config_widget)
        layout.addWidget(self.grp_ocio_cm)

        layout.addStretch()
        return tab

    def _on_cm_mode_changed(self) -> None:
        mode = self.combo_cm_mode.currentData() or "standard"
        is_standard = (mode == "standard")
        self.grp_std_cm.setVisible(is_standard)
        self.grp_ocio_cm.setVisible(not is_standard)

    def _create_tab_export_delivery(self) -> QWidget:
        tab_export = QWidget()
        export_layout = QVBoxLayout(tab_export)
        export_layout.setContentsMargins(12, 12, 12, 12)
        export_layout.setSpacing(12)

        # 1. Image & Sequence Export Defaults
        grp_defaults = QGroupBox("General Image & Sequence Export Defaults")
        defaults_form = QFormLayout(grp_defaults)
        defaults_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        self.combo_default_export_fmt = QComboBox()
        self.combo_default_export_fmt.addItem("JPEG Image / Sequence (.jpg) [Compact & Fast]", "jpg")
        self.combo_default_export_fmt.addItem("PNG Image / Sequence (.png) [Lossless / Quantized]", "png")
        defaults_form.addRow("Default Export Format:", self.combo_default_export_fmt)

        self.chk_downscale_greater_than_fhd = QCheckBox("Downscale resolution by half (50%) when media exceeds Full HD (1920x1080)")
        self.chk_downscale_greater_than_fhd.setToolTip(
            "When enabled, exports of media larger than 1920x1080 (such as 2K, 4K UHD, 8K) "
            "will be downscaled to 50% width and height to optimize delivery file sizes."
        )
        defaults_form.addRow("", self.chk_downscale_greater_than_fhd)

        export_layout.addWidget(grp_defaults)

        # 2. JPEG Compression & Quality Settings
        grp_jpeg = QGroupBox("JPEG Quality & Compression")
        jpeg_form = QFormLayout(grp_jpeg)
        jpeg_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        row_jpg_q = QHBoxLayout()
        self.slider_jpg_quality = QSlider(Qt.Orientation.Horizontal)
        self.slider_jpg_quality.setRange(1, 100)
        self.slider_jpg_quality.setValue(85)
        self.slider_jpg_quality.setTickInterval(10)
        self.slider_jpg_quality.setTickPosition(QSlider.TickPosition.TicksBelow)

        self.spin_jpg_quality = QSpinBox()
        self.spin_jpg_quality.setRange(1, 100)
        self.spin_jpg_quality.setValue(85)
        self.spin_jpg_quality.setSuffix(" %")

        self.slider_jpg_quality.valueChanged.connect(self.spin_jpg_quality.setValue)
        self.spin_jpg_quality.valueChanged.connect(self.slider_jpg_quality.setValue)

        row_jpg_q.addWidget(self.slider_jpg_quality, 1)
        row_jpg_q.addWidget(self.spin_jpg_quality)
        jpeg_form.addRow("JPEG Quality (1-100):", row_jpg_q)

        lbl_jpg_hint = QLabel("Higher values preserve visual fidelity; lower values reduce file size. 85 is recommended.")
        lbl_jpg_hint.setStyleSheet("color: #888888; font-size: 11px;")
        jpeg_form.addRow("", lbl_jpg_hint)

        export_layout.addWidget(grp_jpeg)

        # 3. PNG Compression & Quantization Settings
        grp_png = QGroupBox("PNG Compression & Quantization")
        png_form = QFormLayout(grp_png)
        png_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        self.combo_default_png_mode = QComboBox()
        self.combo_default_png_mode.addItem("PNG-8 Indexed (Quantized 256 Colors - ~70% smaller)", "png8")
        self.combo_default_png_mode.addItem("PNG-24 Truecolor Optimized (Strictly Lossless)", "png24_opt")
        self.combo_default_png_mode.addItem("16-bit Unsigned Int (Deep Color)", "16")
        png_form.addRow("Default PNG Mode:", self.combo_default_png_mode)

        self.chk_default_png_dither = QCheckBox("Enable Color Dithering for Gradients (Floyd-Steinberg)")
        png_form.addRow("", self.chk_default_png_dither)

        self.combo_default_png_comp = QComboBox()
        self.combo_default_png_comp.addItem("Balanced (Level 4 - Recommended)", 4)
        self.combo_default_png_comp.addItem("Maximum Compression (Level 9)", 9)
        self.combo_default_png_comp.addItem("Standard (Level 6)", 6)
        self.combo_default_png_comp.addItem("Fast / Draft (Level 1)", 1)
        png_form.addRow("Default PNG Compression:", self.combo_default_png_comp)

        export_layout.addWidget(grp_png)

        # 4. PDF Review Sheets Branding & Information
        grp_brand = QGroupBox("PDF Review Sheets - Branding & Header Information")
        brand_form = QFormLayout(grp_brand)
        brand_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        self.edit_pdf_title = QLineEdit()
        self.edit_pdf_title.setPlaceholderText("OpenReview - VFX Review Sheet")
        self.edit_pdf_title.setToolTip("Main document header title printed at the top of the PDF")
        brand_form.addRow("Document Title:", self.edit_pdf_title)

        self.edit_pdf_studio = QLineEdit()
        self.edit_pdf_studio.setPlaceholderText("e.g. Industrial Light & Magic, Studio Main, Dailies Lab")
        self.edit_pdf_studio.setToolTip("Studio / Facility branding name printed below the document title")
        brand_form.addRow("Studio / Facility:", self.edit_pdf_studio)

        self.edit_pdf_author = QLineEdit()
        self.edit_pdf_author.setPlaceholderText("e.g. VFX Supervisor, Lead Artist, Reviewer Name")
        self.edit_pdf_author.setToolTip("Reviewer or author attribution name")
        brand_form.addRow("Reviewer / Artist:", self.edit_pdf_author)

        logo_row = QHBoxLayout()
        self.edit_pdf_logo = QLineEdit()
        self.edit_pdf_logo.setPlaceholderText("Optional path to company logo image (.png, .jpg, .svg)")
        logo_row.addWidget(self.edit_pdf_logo, 1)

        self.btn_browse_pdf_logo = QPushButton("Browse...")
        self.btn_browse_pdf_logo.clicked.connect(self._on_browse_pdf_logo)
        logo_row.addWidget(self.btn_browse_pdf_logo)

        self.btn_clear_pdf_logo = QPushButton("Clear")
        self.btn_clear_pdf_logo.clicked.connect(lambda: self.edit_pdf_logo.clear())
        logo_row.addWidget(self.btn_clear_pdf_logo)
        brand_form.addRow("Company Logo:", logo_row)

        color_row = QHBoxLayout()
        self.edit_pdf_header_color = QLineEdit("#c28a10")
        self.edit_pdf_header_color.setFixedWidth(80)
        self.edit_pdf_header_color.textChanged.connect(self._on_pdf_color_text_changed)
        color_row.addWidget(self.edit_pdf_header_color)

        self.btn_pdf_header_color = QPushButton("Pick Color...")
        self.btn_pdf_header_color.setFixedWidth(100)
        self.btn_pdf_header_color.clicked.connect(self._on_pick_pdf_header_color)
        color_row.addWidget(self.btn_pdf_header_color)

        self.lbl_pdf_color_swatch = QLabel()
        self.lbl_pdf_color_swatch.setFixedSize(24, 24)
        self.lbl_pdf_color_swatch.setStyleSheet("background-color: #c28a10; border: 1px solid #555555; border-radius: 3px;")
        color_row.addWidget(self.lbl_pdf_color_swatch)
        color_row.addStretch()
        brand_form.addRow("Header Accent Color:", color_row)

        export_layout.addWidget(grp_brand)

        # 5. PDF Header Subtitle Elements
        grp_sub = QGroupBox("PDF Review Sheets - Header Subtitle Elements")
        sub_layout = QVBoxLayout(grp_sub)
        sub_layout.setSpacing(6)

        self.chk_pdf_show_date = QCheckBox("Include Generation Timestamp / Date")
        self.chk_pdf_show_date.setChecked(True)
        sub_layout.addWidget(self.chk_pdf_show_date)

        self.chk_pdf_show_fps = QCheckBox("Include Media Framerate (FPS)")
        self.chk_pdf_show_fps.setChecked(True)
        sub_layout.addWidget(self.chk_pdf_show_fps)

        self.chk_pdf_show_keyframe_count = QCheckBox("Include Annotated Keyframe Count")
        self.chk_pdf_show_keyframe_count.setChecked(True)
        sub_layout.addWidget(self.chk_pdf_show_keyframe_count)

        export_layout.addWidget(grp_sub)

        # 6. PDF Page Layout & Presentation
        grp_page = QGroupBox("PDF Review Sheets - Page Format & Layout")
        page_form = QFormLayout(grp_page)
        page_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        self.combo_pdf_page_size = QComboBox()
        self.combo_pdf_page_size.addItem("Letter (US Standard 8.5 x 11 in)", "letter")
        self.combo_pdf_page_size.addItem("A4 (ISO Standard 210 x 297 mm)", "a4")
        self.combo_pdf_page_size.addItem("Legal (8.5 x 14 in)", "legal")
        page_form.addRow("Page Size:", self.combo_pdf_page_size)

        self.combo_pdf_orientation = QComboBox()
        self.combo_pdf_orientation.addItem("Landscape (Wide - Recommended for 16:9 / Film)", "landscape")
        self.combo_pdf_orientation.addItem("Portrait (Tall)", "portrait")
        page_form.addRow("Orientation:", self.combo_pdf_orientation)

        self.combo_pdf_layout_mode = QComboBox()
        self.combo_pdf_layout_mode.addItem("Detailed Row (Side-by-Side: Thumbnail Left, Notes Right)", "detailed_row")
        self.combo_pdf_layout_mode.addItem("Dense Grid (Storyboards: 2 Columns with Notes Below)", "dense_grid")
        page_form.addRow("Card Layout Mode:", self.combo_pdf_layout_mode)

        export_layout.addWidget(grp_page)

        # 7. PDF Card Content Elements
        grp_card = QGroupBox("PDF Review Sheets - Review Card Content Elements")
        card_layout = QVBoxLayout(grp_card)
        card_layout.setSpacing(6)

        self.chk_pdf_show_frame_num = QCheckBox("Include Frame Number")
        self.chk_pdf_show_frame_num.setChecked(True)
        card_layout.addWidget(self.chk_pdf_show_frame_num)

        self.chk_pdf_show_timecode = QCheckBox("Include Timecode (SMPTE)")
        self.chk_pdf_show_timecode.setChecked(True)
        card_layout.addWidget(self.chk_pdf_show_timecode)

        self.chk_pdf_show_source_path = QCheckBox("Include Source File Name / Media Path")
        self.chk_pdf_show_source_path.setChecked(True)
        card_layout.addWidget(self.chk_pdf_show_source_path)

        self.chk_pdf_show_stroke_count = QCheckBox("Include Vector Markup / Stroke Count")
        self.chk_pdf_show_stroke_count.setChecked(True)
        card_layout.addWidget(self.chk_pdf_show_stroke_count)

        self.chk_pdf_show_notes = QCheckBox("Include Review Notes & Comments")
        self.chk_pdf_show_notes.setChecked(True)
        card_layout.addWidget(self.chk_pdf_show_notes)

        export_layout.addWidget(grp_card)
        export_layout.addStretch()

        return tab_export

    def _create_tab_pdf_export(self) -> QWidget:
        """Backwards compatibility alias for _create_tab_export_delivery."""
        return self._create_tab_export_delivery()

    def _on_browse_pdf_logo(self) -> None:
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Company / Studio Logo Image",
            "",
            "Images (*.png *.jpg *.jpeg *.svg);;All Files (*.*)",
        )
        if file_path:
            self.edit_pdf_logo.setText(file_path)

    def _set_pdf_header_color(self, hex_color: str) -> None:
        hex_color = hex_color.strip() if hex_color else "#c28a10"
        if not hex_color.startswith("#"):
            hex_color = "#" + hex_color
        self.edit_pdf_header_color.setText(hex_color)
        self.lbl_pdf_color_swatch.setStyleSheet(
            f"background-color: {hex_color}; border: 1px solid #555555; border-radius: 3px;"
        )

    def _on_pdf_color_text_changed(self, text: str) -> None:
        t = text.strip()
        if QColor.isValidColor(t):
            self.lbl_pdf_color_swatch.setStyleSheet(
                f"background-color: {t}; border: 1px solid #555555; border-radius: 3px;"
            )

    def _on_pick_pdf_header_color(self) -> None:
        cur_c = QColor(self.edit_pdf_header_color.text().strip() or "#c28a10")
        picked = QColorDialog.getColor(cur_c, self, "Select Header Accent Color")
        if picked.isValid():
            self._set_pdf_header_color(picked.name())

    def open_tab(self, tab_name: str) -> None:
        """Switch to tab by title matching (case-insensitive)."""
        target = tab_name.strip().lower()
        for idx in range(self.tabs.count()):
            if target in self.tabs.tabText(idx).strip().lower():
                self.tabs.setCurrentIndex(idx)
                return

    def _populate_gpu_devices(self) -> None:
        """Scan system GPUs and populate combo_gpu_device dropdown."""
        self._detected_gpus = detect_system_gpus()
        cur_selection = self.combo_gpu_device.currentData() if self.combo_gpu_device.count() > 0 else ""

        self.combo_gpu_device.blockSignals(True)
        self.combo_gpu_device.clear()

        self.combo_gpu_device.addItem("Auto (System Default / Best GPU)", "auto")
        for dev in self._detected_gpus:
            vram_str = f"{dev.vram_mb} MB" if dev.vram_mb > 0 else "Shared VRAM"
            disp = f"{dev.name} ({dev.vendor} - {vram_str})"
            self.combo_gpu_device.addItem(disp, dev.name)

        found_idx = self.combo_gpu_device.findData(cur_selection)
        if found_idx >= 0:
            self.combo_gpu_device.setCurrentIndex(found_idx)
        else:
            self.combo_gpu_device.setCurrentIndex(0)

        self.combo_gpu_device.blockSignals(False)
        self._update_gpu_telemetry()

    def _on_selected_gpu_changed(self, index: int) -> None:
        self._update_gpu_telemetry()

    def _update_gpu_telemetry(self) -> None:
        """Update live telemetry display label based on selected or active GPU."""
        target_name = self.combo_gpu_device.currentData()
        active_gpu = get_active_gpu_device(target_name if target_name != "auto" else None)

        vram_text = f"{active_gpu.vram_mb} MB ({'Dedicated' if active_gpu.is_dedicated else 'Integrated/Shared'})" if active_gpu.vram_mb > 0 else "Unified Memory"
        opencl_text = "Available & Ready" if active_gpu.opencl_supported else "Not Available"
        driver_text = active_gpu.driver_version or "Standard OS Driver"
        gl_version = active_gpu.opengl_version or "OpenGL 4.x Compatible"

        telem_str = (
            f"Device:    {active_gpu.name}\n"
            f"Vendor:    {active_gpu.vendor}\n"
            f"Memory:    {vram_text}\n"
            f"Driver:    {driver_text}\n"
            f"OpenCL:    {opencl_text}\n"
            f"GL Core:   {gl_version}"
        )
        self.lbl_gpu_telemetry.setText(telem_str)

    def _load_from_prefs(self, p: UserPreferences) -> None:
        self.spin_fps.setValue(p.default_fps)
        self.chk_play_on_startup.setChecked(p.play_on_startup)
        self.chk_fullscreen.setChecked(p.start_fullscreen)
        self.chk_canvas_scrub.setChecked(p.canvas_scrub_enabled)
        self.chk_auto_retime.setChecked(p.auto_retime_mismatched_fps)

        # Performance settings
        idx_tex = self.combo_texture_mode.findData(getattr(p, "playback_texture_mode", "auto"))
        if idx_tex >= 0:
            self.combo_texture_mode.setCurrentIndex(idx_tex)

        self.spin_prefetch_buffer.setValue(getattr(p, "prefetch_buffer_size", 48))
        self.spin_prefetch_threads.setValue(getattr(p, "prefetch_worker_threads", 4))
        self.chk_laptop_mode.setChecked(getattr(p, "fast_playback_laptop_mode", False))

        # Export Defaults
        idx_fmt = self.combo_default_export_fmt.findData(getattr(p, "default_export_image_format", "jpg"))
        if idx_fmt >= 0:
            self.combo_default_export_fmt.setCurrentIndex(idx_fmt)
        self.chk_downscale_greater_than_fhd.setChecked(getattr(p, "export_downscale_greater_than_fhd", True))
        jpg_q = getattr(p, "export_jpg_quality", 85)
        self.spin_jpg_quality.setValue(jpg_q)
        self.slider_jpg_quality.setValue(jpg_q)

        idx_png = self.combo_default_png_mode.findData(getattr(p, "export_png_mode", "png8"))
        if idx_png >= 0:
            self.combo_default_png_mode.setCurrentIndex(idx_png)
        self.chk_default_png_dither.setChecked(getattr(p, "export_png_dither", True))
        idx_comp = self.combo_default_png_comp.findData(getattr(p, "export_png_compression", 4))
        if idx_comp >= 0:
            self.combo_default_png_comp.setCurrentIndex(idx_comp)

        # GPU Acceleration settings
        self._populate_gpu_devices()
        idx_engine = self.combo_playback_engine.findData(getattr(p, "playback_engine", "opengl_core"))
        if idx_engine >= 0:
            self.combo_playback_engine.setCurrentIndex(idx_engine)

        target_gpu = getattr(p, "selected_gpu_device", "auto")
        idx_gpu = self.combo_gpu_device.findData(target_gpu)
        if idx_gpu >= 0:
            self.combo_gpu_device.setCurrentIndex(idx_gpu)

        self.chk_hw_video.setChecked(getattr(p, "hardware_video_decoding", True))
        self.chk_opencl.setChecked(getattr(p, "enable_opencl_acceleration", True))
        self.chk_pbo_streaming.setChecked(getattr(p, "enable_pbo_streaming", True))
        self.chk_vsync.setChecked(getattr(p, "vsync_enabled", False))
        self.spin_gpu_vram.setValue(int(getattr(p, "gpu_vram_limit_mb", 2048.0)))
        self._update_gpu_telemetry()

        # Color Management
        cm_mode = getattr(p, "color_management_mode", "standard")
        idx_cm = self.combo_cm_mode.findData(cm_mode)
        if idx_cm >= 0:
            self.combo_cm_mode.setCurrentIndex(idx_cm)
        self._on_cm_mode_changed()

        std_view = getattr(p, "standard_view_transform", "sRGB")
        idx_std_v = self.combo_std_default_view.findData(std_view)
        if idx_std_v >= 0:
            self.combo_std_default_view.setCurrentIndex(idx_std_v)

        std_in = getattr(p, "standard_input_space", "linear")
        idx_std_i = self.combo_std_default_input.findData(std_in)
        if idx_std_i >= 0:
            self.combo_std_default_input.setCurrentIndex(idx_std_i)

        self.chk_auto_detect_input.setChecked(getattr(p, "auto_detect_input_colorspace", True))

        idx = self.combo_cache_mode.findData(p.default_cache_mode)
        if idx >= 0:
            self.combo_cache_mode.setCurrentIndex(idx)

        idx_toggle = self.combo_cache_toggle.findData(getattr(p, "cache_toggle_mode", "region"))
        if idx_toggle >= 0:
            self.combo_cache_toggle.setCurrentIndex(idx_toggle)

        auto_ram = getattr(p, "auto_ram_budget", True)
        self.chk_auto_ram_budget.setChecked(auto_ram)
        self._on_auto_ram_budget_toggled(auto_ram)
        self.spin_lookahead_gb.setValue(p.lookahead_cache_gb)
        self.spin_region_gb.setValue(p.region_cache_gb)
        self.spin_behind_pct.setValue(int(p.lookahead_behind_pct))
        self.chk_enable_disk_cache.setChecked(getattr(p, "enable_disk_cache", True))
        self.spin_disk_cache_gb.setValue(getattr(p, "max_disk_cache_gb", 20.0))
        op_val = int(getattr(p, "hud_opacity", 0.85) * 100)
        self.slider_hud_opacity.setValue(op_val)
        self.lbl_hud_opacity_val.setText(f"{op_val}%")

        # PDF Review Sheet Settings
        self.edit_pdf_title.setText(getattr(p, "pdf_title", "OpenReview - VFX Review Sheet"))
        self.edit_pdf_studio.setText(getattr(p, "pdf_studio_name", ""))
        self.edit_pdf_author.setText(getattr(p, "pdf_author_name", ""))
        self.edit_pdf_logo.setText(getattr(p, "pdf_logo_path", ""))
        self._set_pdf_header_color(getattr(p, "pdf_header_color", "#c28a10"))

        self.chk_pdf_show_date.setChecked(getattr(p, "pdf_show_date", True))
        self.chk_pdf_show_fps.setChecked(getattr(p, "pdf_show_fps", True))
        self.chk_pdf_show_keyframe_count.setChecked(getattr(p, "pdf_show_keyframe_count", True))

        idx_size = self.combo_pdf_page_size.findData(getattr(p, "pdf_page_size", "letter"))
        if idx_size >= 0:
            self.combo_pdf_page_size.setCurrentIndex(idx_size)

        idx_ori = self.combo_pdf_orientation.findData(getattr(p, "pdf_page_orientation", "landscape"))
        if idx_ori >= 0:
            self.combo_pdf_orientation.setCurrentIndex(idx_ori)

        idx_lay = self.combo_pdf_layout_mode.findData(getattr(p, "pdf_layout_mode", "detailed_row"))
        if idx_lay >= 0:
            self.combo_pdf_layout_mode.setCurrentIndex(idx_lay)

        self.chk_pdf_show_frame_num.setChecked(getattr(p, "pdf_show_frame_num", True))
        self.chk_pdf_show_timecode.setChecked(getattr(p, "pdf_show_timecode", True))
        self.chk_pdf_show_source_path.setChecked(getattr(p, "pdf_show_source_path", True))
        self.chk_pdf_show_stroke_count.setChecked(getattr(p, "pdf_show_stroke_count", True))
        self.chk_pdf_show_notes.setChecked(getattr(p, "pdf_show_notes", True))

        self._update_assoc_status()

    def _on_auto_ram_budget_toggled(self, checked: bool) -> None:
        self.spin_lookahead_gb.setEnabled(not checked)
        self.spin_region_gb.setEnabled(not checked)

    def _on_hud_opacity_slider_changed(self, val: int) -> None:
        self.lbl_hud_opacity_val.setText(f"{val}%")
        self.hud_opacity_changed.emit(val / 100.0)

    def _on_clear_disk_cache_clicked(self) -> None:
        try:
            base_temp = os.path.join(tempfile.gettempdir(), "OpenReview_Cache")
            if os.path.exists(base_temp):
                shutil.rmtree(base_temp, ignore_errors=True)
            os.makedirs(base_temp, exist_ok=True)
            parent = self.parent()
            if parent and hasattr(parent, "cache") and hasattr(parent.cache, "disk_cache") and parent.cache.disk_cache:
                parent.cache.disk_cache.clear()
            QMessageBox.information(self, "Disk Cache Cleared", "Temporary disk cache files have been purged.")
        except Exception as e:
            QMessageBox.warning(self, "Clear Failed", f"Failed to clear disk cache: {e}")

    def _update_assoc_status(self) -> None:
        try:
            from openreview.core.file_associations import is_file_association_registered
            if is_file_association_registered():
                self.lbl_assoc_status.setText("<span style='color: #2ecc71; font-weight: bold;'>● Registered</span>")
            else:
                self.lbl_assoc_status.setText("<span style='color: #888888;'>○ Not registered</span>")
        except Exception:
            pass

    def _on_register_file_associations_clicked(self) -> None:
        from openreview.core.file_associations import register_file_associations
        success = register_file_associations()
        self._update_assoc_status()
        if success:
            QMessageBox.information(self, "Associations Registered", "OpenReview session files (.ovr) and media formats successfully associated with Windows Explorer.")
        else:
            QMessageBox.warning(self, "Registration Notice", "File associations could not be written to registry. Please check permissions.")

    def _on_unregister_file_associations_clicked(self) -> None:
        from openreview.core.file_associations import unregister_file_associations
        unregister_file_associations()
        self._update_assoc_status()
        QMessageBox.information(self, "Associations Removed", "OpenReview file associations have been removed from Windows Explorer.")

    def _gather_prefs(self) -> UserPreferences:
        # Clone existing preferences so unexposed settings are never erased
        p = UserPreferences(**asdict(self._current_prefs))
        p.default_fps = self.spin_fps.value()
        p.play_on_startup = self.chk_play_on_startup.isChecked()
        p.start_fullscreen = self.chk_fullscreen.isChecked()
        p.canvas_scrub_enabled = self.chk_canvas_scrub.isChecked()
        p.auto_retime_mismatched_fps = self.chk_auto_retime.isChecked()
        p.default_cache_mode = self.combo_cache_mode.currentData()
        p.cache_toggle_mode = self.combo_cache_toggle.currentData()
        p.auto_ram_budget = self.chk_auto_ram_budget.isChecked()
        p.lookahead_cache_gb = self.spin_lookahead_gb.value()
        p.region_cache_gb = self.spin_region_gb.value()
        p.lookahead_behind_pct = float(self.spin_behind_pct.value())
        p.enable_disk_cache = self.chk_enable_disk_cache.isChecked()
        p.max_disk_cache_gb = self.spin_disk_cache_gb.value()
        p.playback_texture_mode = self.combo_texture_mode.currentData()
        p.prefetch_buffer_size = self.spin_prefetch_buffer.value()
        p.prefetch_worker_threads = self.spin_prefetch_threads.value()
        p.fast_playback_laptop_mode = self.chk_laptop_mode.isChecked()
        p.playback_engine = self.combo_playback_engine.currentData() or "opengl_core"
        p.selected_gpu_device = self.combo_gpu_device.currentData() or "auto"
        p.hardware_video_decoding = self.chk_hw_video.isChecked()
        p.enable_opencl_acceleration = self.chk_opencl.isChecked()
        p.enable_pbo_streaming = self.chk_pbo_streaming.isChecked()
        p.vsync_enabled = self.chk_vsync.isChecked()
        p.gpu_vram_limit_mb = float(self.spin_gpu_vram.value())
        p.hud_opacity = self.slider_hud_opacity.value() / 100.0

        # Export Defaults
        p.default_export_image_format = self.combo_default_export_fmt.currentData() or "jpg"
        p.export_downscale_greater_than_fhd = self.chk_downscale_greater_than_fhd.isChecked()
        p.export_jpg_quality = self.spin_jpg_quality.value()
        p.export_png_mode = self.combo_default_png_mode.currentData() or "png8"
        p.export_png_dither = self.chk_default_png_dither.isChecked()
        p.export_png_compression = int(self.combo_default_png_comp.currentData() or 4)

        # PDF Review Sheet Settings
        p.pdf_title = self.edit_pdf_title.text().strip() or "OpenReview - VFX Review Sheet"
        p.pdf_studio_name = self.edit_pdf_studio.text().strip()
        p.pdf_author_name = self.edit_pdf_author.text().strip()
        p.pdf_logo_path = self.edit_pdf_logo.text().strip()
        p.pdf_header_color = self.edit_pdf_header_color.text().strip() or "#c28a10"
        p.pdf_show_date = self.chk_pdf_show_date.isChecked()
        p.pdf_show_fps = self.chk_pdf_show_fps.isChecked()
        p.pdf_show_keyframe_count = self.chk_pdf_show_keyframe_count.isChecked()
        p.pdf_page_size = self.combo_pdf_page_size.currentData() or "letter"
        p.pdf_page_orientation = self.combo_pdf_orientation.currentData() or "landscape"
        p.pdf_layout_mode = self.combo_pdf_layout_mode.currentData() or "detailed_row"
        p.pdf_show_frame_num = self.chk_pdf_show_frame_num.isChecked()
        p.pdf_show_timecode = self.chk_pdf_show_timecode.isChecked()
        p.pdf_show_source_path = self.chk_pdf_show_source_path.isChecked()
        p.pdf_show_stroke_count = self.chk_pdf_show_stroke_count.isChecked()
        p.pdf_show_notes = self.chk_pdf_show_notes.isChecked()

        p.color_management_mode = self.combo_cm_mode.currentData() or "standard"
        p.standard_view_transform = self.combo_std_default_view.currentData() or "sRGB"
        p.standard_input_space = self.combo_std_default_input.currentData() or "linear"
        p.auto_detect_input_colorspace = self.chk_auto_detect_input.isChecked()
        p.shortcuts = dict(self._working_shortcuts)
        return p

    def _reset_defaults(self) -> None:
        self._load_from_prefs(UserPreferences())
        for act in self.shortcut_mgr.get_actions():
            self._working_shortcuts[act.action_id] = act.default_shortcut
        self.slider_hud_opacity.setValue(85)
        self.lbl_hud_opacity_val.setText("85%")
        self.hud_opacity_changed.emit(0.85)
        self._refresh_shortcuts_table()

    def _apply(self) -> None:
        new_prefs = self._gather_prefs()
        # Apply working shortcuts in batch to avoid triggering dozens of synchronous disk & registry writes
        self.shortcut_mgr.set_shortcuts_batch(self._working_shortcuts, persist=False)
        self.mgr.save(new_prefs)

    def _save_and_close(self) -> None:
        self._apply()
        self.accept()
