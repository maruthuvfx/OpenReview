"""
3DEqualizer-Styled OpenReview Main Window.
Integrates top color bar, maximized viewport with floating HUD cards,
vertical markup toolbar, utilitarian timeline with compact cache bar, and live pixel inspector status bar.
"""
from __future__ import annotations

import os
import sys
import math
import colorsys
import logging
from pathlib import Path

logger = logging.getLogger(__name__)
from typing import Optional, List, Dict, Any, Set
import numpy as np
from PySide6.QtCore import Qt, QPoint, QTimer
from PySide6.QtGui import QKeySequence, QShortcut, QAction, QKeyEvent, QActionGroup
from PySide6.QtWidgets import (
    QApplication,
    QMainWindow,
    QWidget,
    QDockWidget,
    QVBoxLayout,
    QHBoxLayout,
    QToolBar,
    QLabel,
    QFileDialog,
    QMessageBox,
    QLineEdit,
    QTextEdit,
    QPlainTextEdit,
    QSpinBox,
    QDoubleSpinBox,
    QInputDialog,
    QTabWidget,
)

from openreview.core.clock import PlaybackClock
from openreview.core.cache import (
    RingBufferCache,
    CacheMode,
    get_system_ram_stats_mb,
    compute_dynamic_ram_budget_mb,
    get_dynamic_gb_presets,
)
from openreview.core.reader import (
    SyntheticPatternReader,
    ReaderEngine,
    read_adaptive_frame,
    get_adaptive_downsample_factor,
    ensure_c_contiguous_rgba,
)
from openreview.core.async_source_loader import AsyncSourceLoader
from openreview.core.video_reader import is_video_file, VideoMediaReader
from openreview.core.preferences import PreferencesManager, UserPreferences
from openreview.core.shortcuts import ShortcutManager
from openreview.ui.preferences_dialog import PreferencesDialog
from openreview.network.ipc_server import IpcServer
from openreview.packages.package_manager import PackageManager
from openreview.ui.package_manager_dialog import PackageManagerDialog
from openreview.models.session import (
    OpenReviewSession,
    ViewportSessionState,
    ColorSessionState,
    MatteSessionState,
)
from openreview.core.exporter import MediaExporter
from openreview.ui.export_dialog import ExportDialog
from openreview.color.ocio_manager import OCIOManager
from openreview.color.standard_colorspaces import (
    STANDARD_COLORSPACES,
    COLORSPACE_BY_NAME,
    COLORSPACE_BY_ID,
    detect_media_input_colorspace,
    map_standard_to_ocio_space,
)
from openreview.color.lut_manager import CubeLut, AscCdl, ColorGradeSettings
from openreview.ui.color_dialog import ColorAdjustmentsDialog, ColorAdjustmentsWidget
from openreview.models.annotations import AnnotationStore, PerMediaAnnotations, Stroke, ShapeType
from openreview.models.comparison import ComparisonState, ComparisonMode
from openreview.widgets.top_bar import TopControlBar
from openreview.widgets.markup_tools import MarkupToolPalette
from openreview.widgets.viewport import ViewportCanvas
from openreview.widgets.timeline import TimelineWidget
from openreview.widgets.floating_hud import FloatingHUDCard
from openreview.widgets.hud_histogram import HistogramHUD
from openreview.widgets.hud_waveform import WaveformHUD
from openreview.widgets.hud_vectorscope import VectorscopeHUD
from openreview.widgets.hud_wipe import WipeComparisonHUD
from openreview.widgets.hud_color_inspector import ColorInspectorHUD
from openreview.packages.builtin.comp_qc.qc_state import QcMode
from openreview.ui.image_info_dialog import ImageInfoDialog, ImageInfoWidget
from openreview.ui.process_info_dialog import ProcessInfoDialog
from openreview.ui.goto_frame_dialog import GoToFrameDialog
from openreview.ui.frame_rate_dialog import FrameRateDialog
from openreview.ui.annotation_manager_dialog import AnnotationManagerDialog, AnnotationManagerWidget
from openreview.audio.audio_engine import AudioEngine
from openreview.ui.audio_dialog import AudioSettingsDialog, AudioSettingsWidget
from openreview.widgets.hud_layers import HUDLayersWidget
from openreview.widgets.hud_stereo import HUDStereoWidget
from openreview.ui.ocio_dialog import OCIOConfigDialog, OCIOConfigWidget
from openreview.network.sync_engine import DailiesSyncEngine
from openreview.ui.dailies_sync_dialog import DailiesSyncDialog, DailiesSyncWidget
from openreview.models.session_graph import (
    SessionGraph,
    SourceItem,
    FolderItem,
    SequenceView,
    StackView,
    LayoutView,
    ViewType,
)
from openreview.ui.sources_panel import SourcesPanelWidget, SourcesDockWidget
from openreview.ui.file_browser_panel import FileBrowserDockWidget, FileBrowserPanelWidget
from openreview.packages.builtin.comp_qc.hud_comp_qc import CompQCDockWidget
from openreview.packages.builtin.comp_qc.qc_state import CompQCState
from openreview.core.console_logger import ConsoleLogManager, LogEntry
from openreview.ui.console_window import ConsoleWindow
from openreview.core.layout_compositor import composite_layout_grid
from openreview.core.version_control import get_next_version, get_prev_version, get_version_summary
from openreview.core.timecode import frame_to_timecode
from openreview.ui.theme import get_3dequalizer_stylesheet

import logging
logger = logging.getLogger("openreview")


class MainWindow(QMainWindow):
    """Primary OpenReview Desktop Application Window."""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle("OpenReview - Professional VFX Review & Playback [ACEScg / OCIO v2]")
        self.setMinimumSize(960, 540)
        
        # Adaptive window geometry: fit within small laptop displays (1280x720 / 1366x768) while expanding on large monitors
        screen = QApplication.primaryScreen()
        if screen:
            avail = screen.availableGeometry()
            init_w = min(1500, max(960, int(avail.width() * 0.95)))
            init_h = min(920, max(540, int(avail.height() * 0.95)))
            self.resize(init_w, init_h)
        else:
            self.resize(1280, 720)

        self.setStyleSheet(get_3dequalizer_stylesheet())
        from openreview.resources import get_app_icon
        self.setWindowIcon(get_app_icon())

        # Borderless fullscreen tracking (preserves DWM popup compositing over OpenGL)
        self._in_borderless_fullscreen: bool = False
        self._pre_borderless_geom = None
        self._pre_borderless_flags = None
        self._pre_borderless_maximized: bool = False

        # 1. Initialize Core Engines
        self.pref_mgr = PreferencesManager.instance()
        self.pref_mgr.preferences_changed.connect(self._on_preferences_changed)
        prefs = self.pref_mgr.prefs

        # Initialize Diagnostic Console Logger & Stream/Exception Hooks early
        self.console_logger = ConsoleLogManager.instance()
        self.console_logger.install_hooks()
        self.console_logger.error_logged.connect(self._on_console_error_logged)

        self.shortcut_mgr = ShortcutManager.instance()
        app = QApplication.instance()
        if app:
            app.installEventFilter(self.shortcut_mgr)
        else:
            self.installEventFilter(self.shortcut_mgr)

        self.clock = PlaybackClock(start_frame=1001, end_frame=1200, fps=prefs.default_fps, parent=self)
        self.clock.set_play_all_frames(getattr(prefs, "play_all_frames", True))
        initial_cache_mode = CacheMode(getattr(prefs, "default_cache_mode", "off"))
        self._last_active_cache_mode = CacheMode.REGION
        self._active_view_is_sequence: bool = False
        self._pre_sequence_cache_mode: Optional[CacheMode] = None
        self._pre_sequence_cache_started: Optional[bool] = None
        use_auto_ram = getattr(prefs, "auto_ram_budget", True)
        disk_cache_dir = getattr(prefs, "disk_cache_dir", "") or None
        enable_disk = getattr(prefs, "enable_disk_cache", True)
        disk_gb = getattr(prefs, "max_disk_cache_gb", 20.0)
        self.cache = RingBufferCache(
            max_memory_mb=None if use_auto_ram else prefs.lookahead_cache_gb * 1024.0,
            lookahead_frames=getattr(prefs, "prefetch_buffer_size", 48),
            lookbehind_frames=int(getattr(prefs, "prefetch_buffer_size", 48) * (prefs.lookahead_behind_pct / 100.0)),
            mode=initial_cache_mode,
            auto_memory_budget=use_auto_ram,
            enable_disk_cache=enable_disk,
            max_disk_mb=disk_gb * 1024.0,
            disk_cache_dir=disk_cache_dir,
            parent=self,
        )
        self.reader = None
        self.reader_engine = ReaderEngine(None, self.cache, parent=self)
        self.async_source_loader = AsyncSourceLoader(self)
        self.async_source_loader.source_discovered.connect(self._on_async_source_discovered)
        self.async_source_loader.progress_message.connect(lambda msg: self.statusBar().showMessage(msg, 3000))
        self.reader_engine.background_progress.connect(self._on_background_caching_progress)
        self.cache.disk_usage_changed.connect(
            lambda _u, _m: self._on_memory_changed(self.cache.get_memory_stats()[0], self.cache.get_memory_stats()[1])
        )
        self.ocio_manager = OCIOManager()
        self.annotation_store = AnnotationStore(self)
        self.per_media_annotations = PerMediaAnnotations(self)
        self.comparison_state = ComparisonState()
        self.audio_engine = AudioEngine(self)
        self.sync_engine = DailiesSyncEngine(self)
        self.audio_engine.set_sequence_context(self.clock.start_frame, self.clock.fps)

        # Session & Media Tracking
        self._current_media_path: Optional[str] = None
        self._current_media_type: str = "none"
        self._current_session_file: Optional[Path] = None
        self._suppress_in_out_record: bool = False

        # Multi-Channel EXR Review & Contact Sheet State
        self._is_exr_contact_sheet_active: bool = False
        self._auto_depth_enabled: bool = True
        self._exr_contact_sheet_tile_map: Dict[str, Tuple[int, int, int, int]] = {}
        self._current_exr_layer: str = "beauty"

        # Central Session Graph (Media Bins, Folders, Sequences, Stacks, Layouts)
        self.session_graph = SessionGraph(self)
        self._source_readers: Dict[str, Any] = {}

        # Delayed Cache Timer for Smooth Media Drops
        self._delayed_cache_timer = QTimer(self)
        self._delayed_cache_timer.setSingleShot(True)
        self._delayed_cache_timer.setInterval(1000)
        self._delayed_cache_timer.timeout.connect(self._trigger_prefetch)

        # Coalesced Cache UI Update Timer (~10 FPS to avoid Qt event queue saturation)
        self._cache_ui_timer = QTimer(self)
        self._cache_ui_timer.setSingleShot(True)
        self._cache_ui_timer.setInterval(100)
        self._cache_ui_timer.timeout.connect(self._on_cache_updated)

        # Scrub Settle Timer (dedicates 100% CPU during active dragging)
        self._scrub_settle_timer = QTimer(self)
        self._scrub_settle_timer.setSingleShot(True)
        self._scrub_settle_timer.setInterval(150)
        self._scrub_settle_timer.timeout.connect(self._on_scrub_settled)

        self._async_drop_active = False
        self._cache_started = False
        self._is_scrubbing: bool = False
        self._last_prefetch_trigger_frame: int = -9999

        # 2. Build UI Hierarchy
        self._shortcuts: list[QShortcut] = []
        self._setup_ui()
        self._setup_menus()
        self._setup_shortcuts()

        # 3. Wire Signals & Engine Loops
        self._connect_signals()

        # Apply Startup Preferences
        init_cache_mode = CacheMode(getattr(prefs, "default_cache_mode", "off"))
        self._last_pref_default_cache_mode = init_cache_mode.value
        self._on_set_cache_mode(init_cache_mode, start_prefetch=False)
        if prefs.start_fullscreen:
            self._enter_borderless_fullscreen()
        if prefs.play_on_startup:
            self.clock.play()

        # Restore Safe Guides & Overlays preferences
        if hasattr(prefs, "safe_guides_matte_constrained"):
            self.viewport.set_safe_guides_matte_constrained(prefs.safe_guides_matte_constrained)
            if hasattr(self, "act_matte_constrain"):
                self.act_matte_constrain.setChecked(prefs.safe_guides_matte_constrained)

        # Restore Color Management (Standard Colorspaces vs OCIO)
        cm_mode = getattr(prefs, "color_management_mode", "standard")
        std_view = getattr(prefs, "standard_view_transform", "sRGB")
        std_input = getattr(prefs, "standard_input_space", "linear")
        self.top_bar.set_color_management_mode(cm_mode, std_view, std_input)

        if cm_mode == "ocio":
            if getattr(prefs, "ocio_config_path", "") and Path(prefs.ocio_config_path).exists():
                self.ocio_manager.load_config(prefs.ocio_config_path)
            if getattr(prefs, "ocio_lut_paths", []):
                for sp in prefs.ocio_lut_paths:
                    self.ocio_manager.add_search_path(sp)
            if getattr(prefs, "ocio_display", ""):
                self.ocio_manager.display = prefs.ocio_display
            if getattr(prefs, "ocio_view", ""):
                self.ocio_manager.view = prefs.ocio_view
            if getattr(prefs, "ocio_input_space", ""):
                self.ocio_manager.input_space = prefs.ocio_input_space
        self._update_color_menus()

        # Restore Dailies Sync room and username
        if getattr(prefs, "sync_room_name", ""):
            self.sync_engine.room_name = prefs.sync_room_name
        if getattr(prefs, "sync_username", ""):
            self.sync_engine._username = prefs.sync_username
        # Restore Canvas Scrub Mode state from preferences
        scrub_init = getattr(prefs, "canvas_scrub_enabled", True)
        self.viewport.set_scrub_mode(scrub_init)
        self.top_bar.set_scrub_mode(scrub_init)

        # Restore GPU playback engine & VSync
        if hasattr(prefs, "playback_engine"):
            self.viewport.set_playback_engine(prefs.playback_engine)
        if hasattr(prefs, "vsync_enabled"):
            self.viewport.set_vsync_enabled(prefs.vsync_enabled)

        # Initialize Package & Plugin System
        self.pkg_mgr = PackageManager.instance(main_window=self)
        self.pkg_mgr.load_all_plugins()

        # Initialize Local IPC Server for external Python SDK and DCC integrations
        self.ipc_server = IpcServer(main_window=self, parent=self)
        self.ipc_server.start()

        # Initial frame render
        self._on_frame_changed(self.clock.current_frame)

        # Ensure goto frame box is unselected by default and viewport holds focus
        if hasattr(self, "timeline") and hasattr(self.timeline, "goto_frame_box"):
            self.timeline.goto_frame_box.clearFocus()
        if hasattr(self, "viewport") and self.viewport:
            self.viewport.setFocus()

    # --------------------------------------------------------------------------
    # UI Layout Construction
    # --------------------------------------------------------------------------
    def _setup_ui(self) -> None:
        # Configure fluid, interactive dock behavior and full-width bottom corners
        self.setDockOptions(
            QMainWindow.DockOption.AnimatedDocks |
            QMainWindow.DockOption.AllowTabbedDocks |
            QMainWindow.DockOption.GroupedDragging
        )
        self.setCorner(Qt.Corner.BottomLeftCorner, Qt.DockWidgetArea.BottomDockWidgetArea)
        self.setCorner(Qt.Corner.BottomRightCorner, Qt.DockWidgetArea.BottomDockWidgetArea)
        self.setTabPosition(Qt.DockWidgetArea.RightDockWidgetArea, QTabWidget.TabPosition.North)
        self.setTabPosition(Qt.DockWidgetArea.LeftDockWidgetArea, QTabWidget.TabPosition.North)

        # 1. Top Controls Toolbar (Dockable, Movable, Floatable)
        self.top_toolbar = QToolBar("Top Controls", self)
        self.top_toolbar.setObjectName("TopControlToolBar")
        self.top_toolbar.setMovable(True)
        self.top_toolbar.setFloatable(True)
        self.top_toolbar.setAllowedAreas(Qt.ToolBarArea.AllToolBarAreas)
        self.top_bar = TopControlBar(self.ocio_manager, self.top_toolbar)
        self.top_toolbar.addWidget(self.top_bar)
        self.addToolBar(Qt.ToolBarArea.TopToolBarArea, self.top_toolbar)

        # 2. Sources & Session Manager Left Dock Panel (Premiere / RV style Media Bin)
        self.sources_dock = SourcesDockWidget(self.session_graph, self)
        self.sources_dock.panel.media_load_requested.connect(self._on_sources_media_load_requested)
        self.sources_dock.panel.active_view_changed.connect(self._on_active_view_changed)
        self.sources_dock.panel.view_updated.connect(self._on_sources_view_updated)
        self.sources_dock.panel.sources_cleared_to_fresh_start.connect(lambda: self._on_close_session(prompt=False))
        self.sources_dock.panel.reload_source_requested.connect(self._on_reload_source_media)
        self.sources_dock.panel.reload_source_update_requested.connect(self._on_reload_source_update)
        self.sources_dock.panel.exr_contact_sheet_requested.connect(lambda: self._on_toggle_exr_contact_sheet(True))
        self.sources_dock.panel.exr_view_single_requested.connect(self._on_view_single_exr_pass)
        self.sources_dock.panel.exr_export_sheet_requested.connect(self._on_export_exr_contact_sheet)
        self.sources_dock.panel.exr_show_inspector_requested.connect(self._on_show_exr_inspector)
        self.sources_dock.panel.exr_metadata_requested.connect(self._on_show_exr_metadata_path)
        self.session_graph.active_view_changed.connect(self._on_active_view_changed)
        self.session_graph.source_properties_changed.connect(self._on_source_renamed_update_title)
        self.session_graph.source_properties_changed.connect(self._on_source_properties_changed)
        self.session_graph.view_properties_changed.connect(lambda vt, vid: self._on_source_renamed_update_title())
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self.sources_dock)
        self.sources_dock.show()
        self.sources_dock.raise_()

        # 2b. Inbuilt File Browser Left/Right Dock Panel (Ctrl+B, default on Right, 420px width)
        self.file_browser_dock = FileBrowserDockWidget(self)
        self.file_browser_dock.panel.media_open_requested.connect(self._load_path)
        self.file_browser_dock.panel.media_add_to_bin_requested.connect(self._on_file_browser_add_to_bin)
        self.file_browser_dock.panel.session_import_requested.connect(self._on_import_session_file)
        self.file_browser_dock.panel.session_new_instance_requested.connect(self._on_launch_session_instance)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.file_browser_dock)
        self.file_browser_dock.hide()
        self.resizeDocks([self.file_browser_dock], [420], Qt.Orientation.Horizontal)

        # 2c. Inbuilt xtra QC Utils Right Dock Panel (Hold Q, default on Right, 380px width)
        self.comp_qc_state = CompQCState()
        self.comp_qc_dock = CompQCDockWidget(self.comp_qc_state, self)
        if hasattr(self.comp_qc_dock, "panel"):
            p = self.comp_qc_dock.panel
            if hasattr(p, "source_plate_dropped"):
                p.source_plate_dropped.connect(self._on_comp_qc_source_dropped)
                p.source_prefetch_requested.connect(self._on_comp_qc_prefetch_source)
                p.source_unload_requested.connect(self._on_comp_qc_unload_source)
                p.matte_dropped.connect(self._on_comp_qc_matte_dropped)
                p.matte_prefetch_requested.connect(self._on_comp_qc_prefetch_matte)
                p.matte_unload_requested.connect(self._on_comp_qc_unload_matte)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.comp_qc_dock)
        self.comp_qc_dock.hide()
        self.resizeDocks([self.comp_qc_dock], [380], Qt.Orientation.Horizontal)
        self.tabifyDockWidget(self.file_browser_dock, self.comp_qc_dock)

        # 3. Maximized Viewport Canvas (Sole Central Widget)
        self.viewport = ViewportCanvas(self.annotation_store, self)
        self.viewport.comp_qc_state = self.comp_qc_state
        self.comp_qc_state.changed.connect(self._on_comp_qc_state_changed)
        self.viewport.contact_sheet_tile_double_clicked.connect(self._on_exr_contact_sheet_tile_double_clicked)
        self.top_bar.scrub_mode_toggled.connect(self._on_scrub_mode_toggled)
        self.viewport.scrub_requested.connect(self._on_viewport_scrub)
        self.viewport.set_scrub_mode(True)
        self.top_bar.set_scrub_mode(True)
        self.setCentralWidget(self.viewport)

        # 4. Top Floating Annotation Capsule Pill HUD (Toggled with 'X', off by default)
        self.markup_palette = MarkupToolPalette(self.viewport)
        self.markup_palette.close_requested.connect(lambda: self._on_toggle_annotation_tools())
        self.markup_palette.auto_center_in_parent()
        self.markup_palette.hide()

        # 4. Floating In-Viewport Panels (Photoshop-style Dockable HUDs)
        self._setup_floating_huds()

        # 5. Seamless Full-Width Timeline Dock at Window Bottom (Spans under Left and Right panels)
        self.timeline_dock = QDockWidget("Timeline", self)
        self.timeline_dock.setObjectName("TimelineDockWidget")
        self.timeline_dock.setAllowedAreas(Qt.DockWidgetArea.BottomDockWidgetArea)
        self.timeline_dock.setFeatures(QDockWidget.DockWidgetFeature.NoDockWidgetFeatures)
        self.timeline_dock.setTitleBarWidget(QWidget(self.timeline_dock))
        self.timeline = TimelineWidget(
            self.clock,
            self.annotation_store,
            self.timeline_dock,
            canvas_widget=self.viewport,
        )
        self.timeline.fps_dialog_requested.connect(self._on_choose_frame_rate)
        self.timeline_dock.setWidget(self.timeline)
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self.timeline_dock)
        self.timeline_dock.show()

        # 6. Bottom Status Bar with Live Pixel Inspector & Cache Meter
        self._setup_status_bar()

        # 7. Non-Modal Diagnostic Console & Error Log Window (Hidden by default)
        self.console_window = ConsoleWindow(self.console_logger, self.pref_mgr, self)
        self.console_window.hide()
        self.console_window.visibility_changed.connect(self._on_console_visibility_changed)

    def _setup_floating_huds(self) -> None:
        """Create and place in-viewport Photoshop-style floating/dockable HUD cards."""
        # A. Live Histogram HUD (Top-Left)
        self.hist_widget = HistogramHUD()
        self.hud_hist = FloatingHUDCard(
            title="Live RGB Histogram",
            content_widget=self.hist_widget,
            parent=self.viewport,
            initial_pos=QPoint(16, 16),
        )

        # A2. Waveform Monitor HUD
        self.waveform_widget = WaveformHUD()
        self.hud_waveform = FloatingHUDCard(
            title="Waveform Monitor",
            content_widget=self.waveform_widget,
            parent=self.viewport,
            initial_pos=QPoint(16, 175),
        )

        # A3. Vectorscope Monitor HUD
        self.vectorscope_widget = VectorscopeHUD()
        self.hud_vectorscope = FloatingHUDCard(
            title="Vectorscope Monitor",
            content_widget=self.vectorscope_widget,
            parent=self.viewport,
            initial_pos=QPoint(16, 420),
        )

        # B. A/B Wipe Comparison Controller HUD (Top-Right)
        self.wipe_widget = WipeComparisonHUD(self.comparison_state)
        self.hud_wipe = FloatingHUDCard(
            title="A/B Wipe Comparison",
            content_widget=self.wipe_widget,
            parent=self.viewport,
            initial_pos=QPoint(980, 16),
        )

        # C. Color Inspector HUD (Left-Center)
        self.inspector_widget = ColorInspectorHUD()
        self.hud_inspector = FloatingHUDCard(
            title="Color Inspector",
            content_widget=self.inspector_widget,
            parent=self.viewport,
            initial_pos=QPoint(16, 260),
        )

        # E. Interactive Color Adjustments HUD
        self.color_adj_widget = ColorAdjustmentsWidget(self.viewport.color_grading)
        self.color_adj_widget.colorGradeChanged.connect(self._on_color_adj_changed)
        self.hud_color_adj = FloatingHUDCard(
            title="Interactive Color Adjustments",
            content_widget=self.color_adj_widget,
            parent=self.viewport,
            initial_pos=QPoint(40, 30),
        )

        # F. Annotation & Review Notes Manager HUD
        self.annotation_manager_widget = AnnotationManagerWidget(
            annotation_store=self.annotation_store,
            clock=self.clock,
            render_frame_callback=self._render_frame_for_export,
        )
        self.annotation_manager_widget.frame_jump_requested.connect(self.clock.set_frame)
        self.hud_annotations = FloatingHUDCard(
            title="Annotation & Notes Manager",
            content_widget=self.annotation_manager_widget,
            parent=self.viewport,
            initial_pos=QPoint(60, 40),
        )

        # G. Source Media Details HUD
        self.source_details_widget = ImageInfoWidget()
        self.hud_source_details = FloatingHUDCard(
            title="Source Media Details",
            content_widget=self.source_details_widget,
            parent=self.viewport,
            initial_pos=QPoint(80, 50),
        )
        self.hud_source_details.resize(480, 560)

        # H. Multi-User Remote Dailies Sync HUD
        self.dailies_sync_widget = DailiesSyncWidget(self.sync_engine)
        self.hud_dailies_sync = FloatingHUDCard(
            title="Remote Dailies Sync",
            content_widget=self.dailies_sync_widget,
            parent=self.viewport,
            initial_pos=QPoint(100, 50),
        )

        # I. Audio Settings & Offsets HUD
        self.audio_widget = AudioSettingsWidget(self.audio_engine)
        self.hud_audio = FloatingHUDCard(
            title="Audio Settings & Offsets",
            content_widget=self.audio_widget,
            parent=self.viewport,
            initial_pos=QPoint(120, 60),
        )

        # J1. EXR Multilayer Pass Inspector HUD
        self.layers_widget = HUDLayersWidget()
        self.layers_widget.layer_changed.connect(self._on_layer_changed)
        self.layers_widget.contact_sheet_toggled.connect(self._on_toggle_exr_contact_sheet)
        self.layers_widget.view_single_requested.connect(self._on_view_single_exr_pass)
        self.layers_widget.export_contact_sheet_requested.connect(self._on_export_exr_contact_sheet)
        self.layers_widget.channel_mode_requested.connect(self._on_channel_changed)
        self.layers_widget.auto_depth_toggled.connect(self._on_toggle_auto_depth)
        self.layers_widget.metadata_requested.connect(self._on_show_active_exr_metadata)
        self.hud_layers = FloatingHUDCard(
            title="EXR Multilayer Pass Inspector",
            content_widget=self.layers_widget,
            parent=self.viewport,
            initial_pos=QPoint(140, 60),
        )

        # J2. Stereoscopic 3D HUD
        self.stereo_widget = HUDStereoWidget()
        self.stereo_widget.mode_changed.connect(self._on_stereo_changed)
        self.hud_stereo = FloatingHUDCard(
            title="Stereoscopic 3D",
            content_widget=self.stereo_widget,
            parent=self.viewport,
            initial_pos=QPoint(160, 60),
        )

        # K. OpenColorIO Studio Config HUD
        self.ocio_widget = OCIOConfigWidget(self.ocio_manager)
        self.hud_ocio = FloatingHUDCard(
            title="OpenColorIO Configuration",
            content_widget=self.ocio_widget,
            parent=self.viewport,
            initial_pos=QPoint(180, 50),
        )

        # HUD cards hidden by default on startup
        self.hud_hist.hide()
        self.hud_waveform.hide()
        self.hud_vectorscope.hide()
        self.hud_wipe.hide()
        self.hud_inspector.hide()
        self.hud_color_adj.hide()
        self.hud_annotations.hide()
        self.hud_source_details.hide()
        self.hud_dailies_sync.hide()
        self.hud_audio.hide()
        self.hud_layers.hide()
        self.hud_stereo.hide()
        self.hud_ocio.hide()
        self._apply_hud_opacity(getattr(self.pref_mgr.prefs, "hud_opacity", 0.85))

    def _setup_status_bar(self) -> None:
        status = self.statusBar()

        # Coordinates readout
        self.coord_label = QLabel("X: 0  Y: 0", self)
        self.coord_label.setStyleSheet("color: #e5a823; font-family: monospace; font-weight: bold;")
        status.addWidget(self.coord_label)

        # Normalized Float RGBA (Color-coded R/G/B/A)
        self.rgba_label = QLabel(
            "RGBA: <span style='color:#ff6b6b;'>R:0.0000</span>, "
            "<span style='color:#69db7c;'>G:0.0000</span>, "
            "<span style='color:#4dabf7;'>B:0.0000</span>, "
            "<span style='color:#ced4da;'>A:1.0000</span>",
            self,
        )
        self.rgba_label.setTextFormat(Qt.TextFormat.RichText)
        self.rgba_label.setStyleSheet("color: #dddddd; font-family: monospace;")
        status.addWidget(self.rgba_label)

        # 8-bit RGB (Color-coded R/G/B)
        self.u8_label = QLabel(
            "(RGB: <span style='color:#ff6b6b;'>0</span>, "
            "<span style='color:#69db7c;'>0</span>, "
            "<span style='color:#4dabf7;'>0</span>)",
            self,
        )
        self.u8_label.setTextFormat(Qt.TextFormat.RichText)
        self.u8_label.setStyleSheet("color: #888888; font-family: monospace;")
        status.addWidget(self.u8_label)

        # HSV
        self.hsv_label = QLabel("HSV: 0.000, 0.000, 0.000", self)
        self.hsv_label.setStyleSheet("color: #888888; font-family: monospace;")
        status.addWidget(self.hsv_label)

        # Luminance Y (Color-coded Luma #ffd43b)
        self.luma_label = QLabel("<span style='color:#ffd43b;'>Y: 0.000</span>", self)
        self.luma_label.setTextFormat(Qt.TextFormat.RichText)
        self.luma_label.setStyleSheet("color: #ffd43b; font-family: monospace;")
        status.addWidget(self.luma_label)

        # Dedicated Status / Notification Message Label (prevents overlap with pixel inspector)
        self.status_msg_label = QLabel("", self)
        self.status_msg_label.setStyleSheet("color: #e5a823; font-style: italic; padding-left: 14px;")
        status.addWidget(self.status_msg_label, stretch=1)

        self._current_status_msg: str = ""

        def safe_show_message(text: str, timeout: int = 0) -> None:
            self._current_status_msg = str(text)
            try:
                if hasattr(self, "status_msg_label"):
                    self.status_msg_label.setText(str(text))
            except RuntimeError:
                pass
            if timeout > 0:
                def _clear(target=str(text)):
                    try:
                        if getattr(self, "_current_status_msg", None) == target:
                            self._current_status_msg = ""
                        if hasattr(self, "status_msg_label") and self.status_msg_label.text() == target:
                            self.status_msg_label.setText("")
                    except RuntimeError:
                        pass
                QTimer.singleShot(timeout, self, _clear)

        def safe_current_message() -> str:
            return getattr(self, "_current_status_msg", "")

        def safe_clear_message() -> None:
            self._current_status_msg = ""
            try:
                if hasattr(self, "status_msg_label"):
                    self.status_msg_label.setText("")
            except RuntimeError:
                pass

        status.showMessage = safe_show_message
        status.currentMessage = safe_current_message
        status.clearMessage = safe_clear_message

        status.addPermanentWidget(QLabel("|"))

        # Zoom level
        self.zoom_status_label = QLabel("Zoom: 100%", self)
        self.zoom_status_label.setStyleSheet("color: #aaaaaa; font-family: monospace;")
        status.addPermanentWidget(self.zoom_status_label)

        # Cache RAM Usage (Default Cache: OFF)
        self.cache_status_label = QLabel("Cache: OFF", self)
        self.cache_status_label.setStyleSheet("color: #888888; font-family: monospace;")
        status.addPermanentWidget(self.cache_status_label)

        # GPU Playback Engine badge (Clickable to open GPU Preferences)
        engine_str = getattr(self.pref_mgr.prefs, "playback_engine", "opengl_core")
        self.gpu_engine_status_label = QLabel(self._format_gpu_engine_label(engine_str), self)
        self.gpu_engine_status_label.setStyleSheet("""
            QLabel {
                color: #e5a823;
                font-family: monospace;
                font-weight: bold;
                padding: 1px 6px;
                border: 1px solid #444444;
                border-radius: 3px;
                background-color: #242424;
            }
            QLabel:hover {
                background-color: #333333;
                border-color: #e5a823;
            }
        """)
        self.gpu_engine_status_label.setCursor(Qt.CursorShape.PointingHandCursor)
        self.gpu_engine_status_label.setToolTip("Active Playback Engine & GPU Accelerator (Click to configure)")
        self.gpu_engine_status_label.mousePressEvent = lambda e: self._on_open_preferences_tab("GPU Acceleration")
        status.addPermanentWidget(self.gpu_engine_status_label)

        # Shuttle Speed Multiplier
        self.shuttle_status_label = QLabel("1.0x", self)
        self.shuttle_status_label.setStyleSheet("color: #e5a823; font-family: monospace; font-weight: bold;")
        self.shuttle_status_label.setToolTip("Current Shuttle Speed")
        status.addPermanentWidget(self.shuttle_status_label)

        # Measured FPS
        self.fps_status_label = QLabel("24.00 FPS (Sync)", self)
        self.fps_status_label.setStyleSheet("color: #e5a823; font-family: monospace; font-weight: bold;")
        status.addPermanentWidget(self.fps_status_label)

        # Dailies Sync status
        self.sync_status_label = QLabel("Sync: Offline", self)
        self.sync_status_label.setStyleSheet("color: #888888; font-family: monospace; font-weight: bold;")
        self.sync_status_label.setToolTip("Multi-User Remote Dailies Sync Status")
        status.addPermanentWidget(self.sync_status_label)

        # Resolution info (Bottom Right Corner)
        self.resolution_status_label = QLabel("No Media", self)
        self.resolution_status_label.setStyleSheet("color: #888888; font-family: monospace;")
        self.resolution_status_label.setToolTip("Active Media Resolution (Width x Height)")
        self._cached_res_str: str = "No Media"
        status.addPermanentWidget(self.resolution_status_label)

    def _update_resolution_status(self, text: str) -> None:
        if getattr(self, "_cached_res_str", None) != text:
            self._cached_res_str = text
            self.resolution_status_label.setText(text)

    # --------------------------------------------------------------------------
    # Menus
    # --------------------------------------------------------------------------
    def _setup_menus(self) -> None:
        mb = self.menuBar()

        # 1. File Menu
        file_menu = mb.addMenu("&File")
        new_sess_act = file_menu.addAction("New Session (Ctrl+N)")
        new_sess_act.triggered.connect(self._on_new_session)

        open_media_act = file_menu.addAction("Open Media Sequence... (Ctrl+O)")
        open_media_act.triggered.connect(self._on_open_media)

        open_sess_act = file_menu.addAction("Open Session... (Ctrl+Shift+O)")
        open_sess_act.triggered.connect(self._on_open_session)

        save_sess_act = file_menu.addAction("Save Session (Ctrl+S)")
        save_sess_act.triggered.connect(self._on_save_session)

        save_sess_as_act = file_menu.addAction("Save Session As... (Ctrl+Shift+S)")
        save_sess_as_act.triggered.connect(self._on_save_session_as)

        close_sess_act = file_menu.addAction("Close / Clear Session (Ctrl+Shift+N)")
        close_sess_act.triggered.connect(self._on_close_session)

        file_menu.addSeparator()
        self.recent_menu = file_menu.addMenu("Open &Recent")
        self._update_recent_menu()

        file_menu.addSeparator()
        sync_act = file_menu.addAction("Dailies Sync / Remote Review... (Ctrl+Shift+D)")
        sync_act.triggered.connect(self._on_open_dailies_sync)

        file_menu.addSeparator()
        export_act = file_menu.addAction("Export Media... (Ctrl+Shift+E)")
        export_act.triggered.connect(self._on_export_media)

        file_menu.addSeparator()
        exit_act = file_menu.addAction("Exit (Ctrl+Q)")
        exit_act.triggered.connect(self.close)

        # 2. Image Menu
        img_menu = mb.addMenu("&Image")
        scale_sub = img_menu.addMenu("Scale")
        scale_sub.addAction("1:1 Actual Size (Ctrl+1)").triggered.connect(lambda: self.viewport.set_scale_preset(1.0))
        scale_sub.addAction("2:1 Double Size (Ctrl+2)").triggered.connect(lambda: self.viewport.set_scale_preset(2.0))
        scale_sub.addAction("4:1 (Ctrl+4)").triggered.connect(lambda: self.viewport.set_scale_preset(4.0))
        scale_sub.addAction("8:1 (Ctrl+8)").triggered.connect(lambda: self.viewport.set_scale_preset(8.0))
        scale_sub.addAction("Fit Width (Ctrl+F)").triggered.connect(self.viewport.fit_width)

        img_menu.addSeparator()
        self.flop_act = img_menu.addAction("Flop Horizontal (Shift+X)")
        self.flop_act.setCheckable(True)
        self.flop_act.triggered.connect(self.viewport.toggle_flop_x)

        self.flip_act = img_menu.addAction("Flip Vertical (Shift+Y)")
        self.flip_act.setCheckable(True)
        self.flip_act.triggered.connect(self.viewport.toggle_flip_y)

        rot_sub = img_menu.addMenu("Rotation")
        rot_sub.addAction("Reset Rotation (0°)").triggered.connect(lambda: self.viewport.set_rotation(0))
        rot_sub.addAction("90° Clockwise").triggered.connect(lambda: self.viewport.set_rotation((self.viewport.rotation + 90) % 360))
        rot_sub.addAction("180°").triggered.connect(lambda: self.viewport.set_rotation(180))
        rot_sub.addAction("270° Counter-Clockwise").triggered.connect(lambda: self.viewport.set_rotation((self.viewport.rotation - 90) % 360))

        par_sub = img_menu.addMenu("Pixel Aspect Ratio")
        for label, val in [
            ("Square (1.0)", 1.0),
            ("NTSC D1 DV 4:3 (0.91)", 0.912),
            ("NTSC D1 DV 16:9 (1.21)", 1.212),
            ("PAL 4:3 (1.09)", 1.093),
            ("PAL 16:9 (1.46)", 1.457),
            ("Anamorphic 2:1 (2.0)", 2.0),
        ]:
            par_sub.addAction(label).triggered.connect(lambda checked=False, p=val: self.viewport.set_pixel_aspect_ratio(p))

        img_menu.addSeparator()
        filter_sub = img_menu.addMenu("Texture Filtering")
        filter_sub.addAction("Smooth (Linear)").triggered.connect(lambda: self.viewport.set_texture_filtering("linear"))
        filter_sub.addAction("Pixelated (Nearest)").triggered.connect(lambda: self.viewport.set_texture_filtering("nearest"))
        filter_toggle_act = filter_sub.addAction("Toggle Filtering (Linear/Nearest) (N)")
        filter_toggle_act.triggered.connect(self._on_toggle_texture_filtering)

        img_menu.addSeparator()
        self.premult_act = img_menu.addAction("Premultiplied Alpha")
        self.premult_act.setCheckable(True)
        self.premult_act.setChecked(True)
        self.premult_act.setToolTip("Toggle Premultiplied alpha composite (GL_ONE) vs Straight alpha (GL_SRC_ALPHA).")
        self.premult_act.triggered.connect(self._on_toggle_premultiplied_alpha)

        # Layered Input Menu (Dynamically visible for multi-layer EXR and Stereo)
        self.layered_input_menu = mb.addMenu("&Layered input")
        self.act_exr_pass_inspector = self.layered_input_menu.addAction("EXR Multilayer Pass Inspector")
        self.act_exr_pass_inspector.triggered.connect(lambda: self.hud_layers.setVisible(not self.hud_layers.isVisible()))
        self.act_stereo_controls = self.layered_input_menu.addAction("Stereoscopic 3D Controls")
        self.act_stereo_controls.triggered.connect(lambda: self.hud_stereo.setVisible(not self.hud_stereo.isVisible()))
        self.layered_input_menu.menuAction().setVisible(False)

        # 3. View Menu
        view_menu = mb.addMenu("&View")
        fit_act = view_menu.addAction("Fit Image (F)")
        fit_act.triggered.connect(self.viewport.fit_image)

        actual_act = view_menu.addAction("Actual Size 100% (1)")
        actual_act.triggered.connect(self.viewport.set_zoom_100)

        fit_w_act = view_menu.addAction("Fit Width (Ctrl+F)")
        fit_w_act.triggered.connect(self.viewport.fit_width)

        view_menu.addSeparator()
        matte_sub = view_menu.addMenu("Matte Overlay")
        cycle_matte_act = matte_sub.addAction("Cycle Matte Mode (Ctrl+M)")
        cycle_matte_act.triggered.connect(self._on_cycle_matte_ratio)
        matte_sub.addSeparator()
        for label, r in [
            ("No Matte", None),
            ("1.33:1 (4:3)", 1.333),
            ("1.66:1", 1.666),
            ("1.77:1 (16:9)", 1.777),
            ("1.85:1 (DCI Flat)", 1.85),
            ("2.35:1 (Cinemascope)", 2.35),
            ("2.40:1", 2.40),
        ]:
            matte_sub.addAction(label).triggered.connect(lambda checked=False, ratio=r: self.viewport.set_matte(ratio))

        matte_op_sub = view_menu.addMenu("Matte Opacity")
        cycle_op_act = matte_op_sub.addAction("Cycle Opacity Level (Shift+M)")
        cycle_op_act.triggered.connect(self._on_cycle_matte_opacity)
        matte_op_sub.addSeparator()
        for label, op in [("25%", 0.25), ("35%", 0.35), ("50%", 0.50), ("100%", 1.0)]:
            matte_op_sub.addAction(label).triggered.connect(lambda checked=False, o=op: self.viewport.set_matte_opacity(o))

        view_menu.addSeparator()
        bg_sub = view_menu.addMenu("Background")
        for label, b in [
            ("Solid Black", "black"),
            ("18% Neutral Grey", "grey18"),
            ("50% Grey", "grey50"),
            ("Solid White", "white"),
            ("Checkerboard", "checker"),
        ]:
            bg_sub.addAction(label).triggered.connect(lambda checked=False, bg=b: self.viewport.set_background_mode(bg))

        view_menu.addSeparator()
        self.disp_sub = view_menu.addMenu("Display Transform")

        self.oor_act = view_menu.addAction("Show Out-of-Range Colors")
        self.oor_act.setCheckable(True)
        self.oor_act.setChecked(False)
        self.oor_act.triggered.connect(self._on_toggle_out_of_range)

        # Stereo 3D View Submenu
        view_menu.addSeparator()
        stereo_sub = view_menu.addMenu("Stereo 3D")
        for label, smode in [
            ("2D (Off)", 0),
            ("Left Eye", 1),
            ("Right Eye", 2),
            ("Anaglyph (Red-Cyan)", 3),
            ("Side-by-Side (Split)", 4),
            ("Stereo Difference", 5),
        ]:
            stereo_sub.addAction(label).triggered.connect(lambda checked=False, m=smode: self.top_bar.set_stereo_mode(m))

        view_menu.addSeparator()
        # Safe Guides & Broadcast Overlays
        safe_menu = view_menu.addMenu("Safe Guides & Overlays")
        safe_menu.addAction("Toggle Safe Guides (Shift+G)").triggered.connect(self._on_toggle_safe_guides)
        safe_menu.addSeparator()
        self.act_action_safe = safe_menu.addAction("90% Action Safe")
        self.act_action_safe.setCheckable(True)
        self.act_action_safe.setChecked(self.viewport.show_action_safe)
        self.act_action_safe.toggled.connect(self._on_toggle_action_safe)

        self.act_title_safe = safe_menu.addAction("80% Title Safe")
        self.act_title_safe.setCheckable(True)
        self.act_title_safe.setChecked(self.viewport.show_title_safe)
        self.act_title_safe.toggled.connect(self._on_toggle_title_safe)

        self.act_crosshair = safe_menu.addAction("Center Crosshair")
        self.act_crosshair.setCheckable(True)
        self.act_crosshair.setChecked(self.viewport.show_crosshair)
        self.act_crosshair.toggled.connect(self._on_toggle_crosshair)

        self.act_thirds_grid = safe_menu.addAction("Rule of Thirds Grid (3x3)")
        self.act_thirds_grid.setCheckable(True)
        self.act_thirds_grid.setChecked(self.viewport.show_thirds_grid)
        self.act_thirds_grid.toggled.connect(self._on_toggle_thirds_grid)

        safe_menu.addSeparator()
        self.act_matte_constrain = safe_menu.addAction("Constrain Guides to Active Matte")
        self.act_matte_constrain.setCheckable(True)
        self.act_matte_constrain.setChecked(self.viewport.safe_guides_matte_constrained)
        self.act_matte_constrain.toggled.connect(self._on_toggle_matte_constrain)

        view_menu.addSeparator()
        present_act = view_menu.addAction("Presentation Mode (Ctrl+P)")
        present_act.triggered.connect(self._on_toggle_presentation_mode)

        self.source_details_act = view_menu.addAction("Source Media Details (I)")
        self.source_details_act.triggered.connect(self._on_toggle_source_details)

        # 4. Review Menu
        review_menu = mb.addMenu("&Review")

        # View Type Sub-menu
        vt_sub = review_menu.addMenu("View Type")
        self.view_type_group = QActionGroup(self)
        self.view_type_group.setExclusive(True)
        self.view_type_actions = {}
        for label, mode in [
            ("Sequence", ComparisonMode.SEQUENCE),
            ("Replace (Single)", ComparisonMode.REPLACE),
            ("Over (Alpha Composite)", ComparisonMode.OVER),
            ("Add", ComparisonMode.ADD),
            ("Difference", ComparisonMode.DIFFERENCE),
            ("Difference (Inverted)", ComparisonMode.DIFFERENCE_INVERTED),
        ]:
            act = vt_sub.addAction(label)
            act.setCheckable(True)
            if mode == ComparisonMode.REPLACE:
                act.setChecked(True)
            act.triggered.connect(lambda checked=False, m=mode: self._on_set_view_type(m))
            self.view_type_group.addAction(act)
            self.view_type_actions[mode] = act

        review_menu.addSeparator()

        # Pipeline Versioning
        ver_sub = review_menu.addMenu("Version Stepping")
        ver_sub.addAction("Version Up (Alt+Up)").triggered.connect(self._on_version_up)
        ver_sub.addAction("Version Down (Alt+Down)").triggered.connect(self._on_version_down)

        review_menu.addSeparator()

        # Timeline Magnifier Sub-menu
        tm_sub = review_menu.addMenu("Timeline Magnifier")
        tm_sub.addAction("Zoom In Timeline (Ctrl+=)").triggered.connect(self.timeline.zoom_in)
        tm_sub.addAction("Zoom Out Timeline (Ctrl+-)").triggered.connect(self.timeline.zoom_out)
        tm_sub.addAction("Reset Timeline Zoom").triggered.connect(self.timeline.reset_zoom)

        # Playback Timing & Synchronization
        play_mode_sub = review_menu.addMenu("Playback Mode")
        play_mode_sub.addAction("Play Forward (Space / L)").triggered.connect(self.clock.play_forward)
        play_mode_sub.addAction("Play Backward (J / Shift+Space)").triggered.connect(self.clock.play_backward)
        play_mode_sub.addSeparator()
        set_fps_act = play_mode_sub.addAction("Set Frame Rate... (Shift+F)")
        set_fps_act.triggered.connect(self._on_choose_frame_rate)
        play_mode_sub.addSeparator()
        self.play_all_frames_act = play_mode_sub.addAction("Play Every Frame (Frame-Accurate Realtime)")
        self.play_all_frames_act.setCheckable(True)
        self.play_all_frames_act.setChecked(self.clock.play_all_frames)
        def _on_play_all_toggled(enabled: bool):
            self.clock.set_play_all_frames(enabled)
            self.pref_mgr.prefs.play_all_frames = enabled
            self.pref_mgr.save()
        self.play_all_frames_act.toggled.connect(_on_play_all_toggled)

        review_menu.addSeparator()

        # Review Tools & Overlays
        self.wipes_act = review_menu.addAction("A/B Wipe Comparison (W)")
        self.wipes_act.triggered.connect(self._on_toggle_wipe)

        self.hist_act = review_menu.addAction("Live RGB Histogram (H)")
        self.hist_act.triggered.connect(self._toggle_hud_hist)

        self.waveform_act = review_menu.addAction("Waveform Monitor (V)")
        self.waveform_act.triggered.connect(self._toggle_hud_waveform)

        self.vectorscope_act = review_menu.addAction("Vectorscope Monitor (Shift+V)")
        self.vectorscope_act.triggered.connect(self._toggle_hud_vectorscope)

        self.wipe_flip_act = review_menu.addAction("Flip Wipe Orientation (Shift+W)")
        self.wipe_flip_act.triggered.connect(self._on_flip_wipe_orientation)

        self.wipe_reset_act = review_menu.addAction("Reset Wipe Controls (Alt+W)")
        self.wipe_reset_act.triggered.connect(self._on_reset_wipe)

        self.info_strip_act = review_menu.addAction("Info Strip (Ctrl+I)")
        self.info_strip_act.setCheckable(True)
        self.info_strip_act.setChecked(False)
        self.info_strip_act.triggered.connect(self._on_toggle_info_strip)

        review_menu.addSeparator()

        review_menu.addAction("Process Info & Performance...").triggered.connect(self._on_show_process_info)

        # 5. Marking Menu
        mark_menu = mb.addMenu("&Marking")
        bm_act = mark_menu.addAction("Toggle Bookmark (M)")
        bm_act.triggered.connect(lambda: self.clock.toggle_bookmark())

        next_bm_act = mark_menu.addAction("Next Marker / Bookmark (Alt+Right)")
        next_bm_act.triggered.connect(self._on_next_marker)

        prev_bm_act = mark_menu.addAction("Previous Marker / Bookmark (Alt+Left)")
        prev_bm_act.triggered.connect(self._on_prev_marker)

        clear_bms_act = mark_menu.addAction("Clear All Bookmarks")
        clear_bms_act.triggered.connect(self.clock.clear_all_bookmarks)

        mark_menu.addSeparator()
        in_act = mark_menu.addAction("Mark In ([)")
        in_act.triggered.connect(lambda: self.clock.set_in_mark())

        out_act = mark_menu.addAction("Mark Out (])")
        out_act.triggered.connect(lambda: self.clock.set_out_mark())

        cin_act = mark_menu.addAction("Clear In (Alt+I)")
        cin_act.triggered.connect(self.clock.clear_in_mark)

        cout_act = mark_menu.addAction("Clear Out (Alt+O)")
        cout_act.triggered.connect(self.clock.clear_out_mark)

        cinout_act = mark_menu.addAction("Clear In/Out Range")
        cinout_act.triggered.connect(self.clock.clear_in_out)

        bound_act = mark_menu.addAction("Mark Sequence Boundaries")
        bound_act.triggered.connect(self.clock.mark_sequence_boundaries)

        mark_annot_act = mark_menu.addAction("Mark Annotated Frames")
        mark_annot_act.triggered.connect(self._on_mark_annotated_frames)

        # 6. Annotation Menu
        annot_menu = mb.addMenu("&Annotation")
        mgr_act = annot_menu.addAction("Annotation Manager... (Ctrl+Shift+A)")
        mgr_act.triggered.connect(self._on_open_annotation_manager)

        toggle_tools_act = annot_menu.addAction("Toggle Annotation Toolbox (X)")
        toggle_tools_act.triggered.connect(self._on_toggle_annotation_tools)

        annot_menu.addSeparator()
        tools_sub = annot_menu.addMenu("Drawing Tools")
        tools_sub.addAction("Pan / Select Mode (Esc)").triggered.connect(lambda: self.viewport.set_draw_tool(None))
        tools_sub.addAction("Pen Tool (D)").triggered.connect(self.markup_palette.toggle_draw)
        tools_sub.addAction("Line Tool").triggered.connect(lambda: self.markup_palette.btn_line.click())
        tools_sub.addAction("Arrow Tool").triggered.connect(lambda: self.markup_palette.btn_arrow.click())
        tools_sub.addAction("Rectangle Tool").triggered.connect(lambda: self.markup_palette.btn_box.click())
        tools_sub.addAction("Circle Tool").triggered.connect(lambda: self.markup_palette.btn_circle.click())
        tools_sub.addAction("Text Tool").triggered.connect(lambda: self.markup_palette.btn_text.click())

        annot_menu.addSeparator()
        clear_frame_act = annot_menu.addAction("Clear Current Frame Annotations (Delete)")
        clear_frame_act.triggered.connect(self._on_clear_annotations)

        clear_all_annot_act = annot_menu.addAction("Clear All Annotations...")
        clear_all_annot_act.triggered.connect(self._on_clear_all_annotations)

        annot_menu.addSeparator()
        next_annot_act = annot_menu.addAction("Next Annotated Frame (.)")
        next_annot_act.triggered.connect(self._on_next_keyframe)

        prev_annot_act = annot_menu.addAction("Previous Annotated Frame (,)")
        prev_annot_act.triggered.connect(self._on_prev_keyframe)

        annot_menu.addSeparator()
        export_annot_act = annot_menu.addAction("Export Annotations... (Ctrl+E)")
        export_annot_act.triggered.connect(self._on_export_annotations)

        import_annot_act = annot_menu.addAction("Import Annotations...")
        import_annot_act.triggered.connect(self._on_import_annotations)

        # 7. Color Menu
        color_menu = mb.addMenu("&Color")

        # Input colorspace conversions
        self.input_sub = color_menu.addMenu("Input Colorspace")

        # LUT / CDL sub-menu
        lut_sub = color_menu.addMenu("LUT / CDL")
        lut_sub.addAction("Load 3D/1D LUT (.cube, .3dl)...").triggered.connect(self._on_load_file_lut)
        lut_sub.addAction("Load ASC CDL (.cdl, .cc, .ccc)...").triggered.connect(self._on_load_file_cdl)
        lut_sub.addSeparator()
        self.lut_toggle_act = lut_sub.addAction("Enable Active LUT")
        self.lut_toggle_act.setCheckable(True)
        self.lut_toggle_act.setChecked(True)
        self.lut_toggle_act.triggered.connect(self._on_toggle_lut_enabled)
        self.cdl_toggle_act = lut_sub.addAction("Enable Active CDL")
        self.cdl_toggle_act.setCheckable(True)
        self.cdl_toggle_act.setChecked(True)
        self.cdl_toggle_act.triggered.connect(self._on_toggle_cdl_enabled)
        lut_sub.addSeparator()
        lut_sub.addAction("Clear Active LUT & CDL").triggered.connect(self._on_clear_lut_and_cdl)

        color_menu.addSeparator()
        self.invert_colors_act = color_menu.addAction("Invert Colors (Shift+I)")
        self.invert_colors_act.setCheckable(True)
        self.invert_colors_act.setChecked(False)
        self.invert_colors_act.triggered.connect(self._on_toggle_invert_colors)

        adj_act = color_menu.addAction("Interactive Color Adjustments... (/)")
        adj_act.triggered.connect(self._on_open_color_adjustments)

        self.color_insp_act = color_menu.addAction("Color Inspector (F5)")
        self.color_insp_act.triggered.connect(self._on_toggle_color_inspector)

        ocio_cfg_act = color_menu.addAction("OpenColorIO Configuration...")
        ocio_cfg_act.triggered.connect(self._on_open_ocio_config)

        color_menu.addSeparator()
        reset_exp_act = color_menu.addAction("Reset Exposure (Ctrl+Alt+E)")
        reset_exp_act.triggered.connect(self.top_bar.reset_exposure)
        reset_gam_act = color_menu.addAction("Reset Gamma (Ctrl+Alt+G)")
        reset_gam_act.triggered.connect(self.top_bar.reset_gamma)
        reset_all_colors_act = color_menu.addAction("Reset All Colors (Ctrl+Alt+R)")
        reset_all_colors_act.triggered.connect(self._on_reset_all_colors)

        # 8. Audio Menu
        audio_menu = mb.addMenu("&Audio")
        self.audio_mute_act = audio_menu.addAction("Mute Audio")
        self.audio_mute_act.setCheckable(True)
        self.audio_mute_act.setChecked(self.audio_engine.is_muted())
        self.audio_mute_act.triggered.connect(self._on_toggle_audio_mute)

        self.audio_scrub_act = audio_menu.addAction("Audio Scrubbing on Drag")
        self.audio_scrub_act.setCheckable(True)
        self.audio_scrub_act.setChecked(self.audio_engine.is_scrubbing_enabled())
        self.audio_scrub_act.triggered.connect(self.audio_engine.set_scrubbing_enabled)

        audio_menu.addSeparator()
        vol_sub = audio_menu.addMenu("Volume Controls")
        for label, val in [("100%", 1.0), ("75%", 0.75), ("50%", 0.5), ("25%", 0.25), ("Mute (0%)", 0.0)]:
            vol_sub.addAction(label).triggered.connect(lambda checked=False, v=val: self.audio_engine.set_volume(v))

        audio_menu.addSeparator()
        audio_menu.addAction("Offset Audio (Seconds)...").triggered.connect(self._on_open_audio_settings)
        audio_menu.addAction("Offset Audio (Frames)...").triggered.connect(self._on_open_audio_settings)
        audio_menu.addAction("Reset All Audio Offsets").triggered.connect(self.audio_engine.reset_offsets)

        audio_menu.addSeparator()
        audio_menu.addAction("Audio Settings & Offsets...").triggered.connect(self._on_open_audio_settings)
        audio_menu.addAction("Load External Audio Track...").triggered.connect(self._on_load_external_audio)
        audio_menu.addAction("Unload Audio Track").triggered.connect(self.audio_engine.unload_audio)

        # 9. Cache Menu
        cache_menu = mb.addMenu("&Cache")

        self.toggle_cache_act = cache_menu.addAction("Toggle Cache On / Off (Shift+C)")
        self.toggle_cache_act.triggered.connect(self._on_toggle_cache)
        cache_menu.addSeparator()

        # Cache Mode Selection (Default: OFF)
        mode_menu = cache_menu.addMenu("Cache Mode")
        self.cache_mode_group = QActionGroup(self)
        self.cache_mode_group.setExclusive(True)

        self.cache_mode_actions = {}
        modes = [
            ("Cache Off (Default)", CacheMode.OFF),
            ("Play-Ahead Cache", CacheMode.PLAY_AHEAD),
            ("In/Out Region Cache (Only)", CacheMode.REGION),
            ("Full Sequence Cache", CacheMode.ALL),
        ]
        for label, m in modes:
            act = mode_menu.addAction(label)
            act.setCheckable(True)
            if m == self.cache.mode:
                act.setChecked(True)
            act.triggered.connect(lambda checked=False, mode=m: self._on_set_cache_mode(mode))
            self.cache_mode_group.addAction(act)
            self.cache_mode_actions[m] = act

        cache_menu.addSeparator()
        self.clear_cache_act = cache_menu.addAction("Clear Active Source Cache (Ctrl+Shift+C)")
        self.clear_cache_act.triggered.connect(self._on_clear_cache)
        self.clear_all_cache_act = cache_menu.addAction("Clear All Sources Cache")
        self.clear_all_cache_act.triggered.connect(self._on_clear_all_cache)

        cache_menu.addSeparator()
        budget_menu = cache_menu.addMenu("RAM Budget")
        self.budget_menu = budget_menu
        self.budget_action_group = QActionGroup(self)
        self.budget_action_group.setExclusive(True)

        total_ram_mb, _ = get_system_ram_stats_mb()
        auto_avail = compute_dynamic_ram_budget_mb(self.cache._used_bytes)

        pct_25 = round(total_ram_mb * 0.25, -2)
        pct_50 = round(total_ram_mb * 0.50, -2)
        pct_75 = round(total_ram_mb * 0.75, -2)
        pct_90 = round(total_ram_mb * 0.90, -2)

        self.budget_actions = {}

        # Dynamic Auto (Available System RAM) action
        self.auto_budget_action = budget_menu.addAction(
            f"Auto (Available System RAM: ~{auto_avail/1024.0:.1f} GB) [Recommended]"
        )
        self.auto_budget_action.setCheckable(True)
        self.auto_budget_action.triggered.connect(self._on_set_auto_cache_budget)
        self.budget_action_group.addAction(self.auto_budget_action)
        self.budget_actions["auto"] = self.auto_budget_action
        self.budget_actions[auto_avail] = self.auto_budget_action

        pct_presets = [
            (f"25% of Total RAM ({pct_25/1024.0:.1f} GB)", pct_25),
            (f"50% of Total RAM ({pct_50/1024.0:.1f} GB)", pct_50),
            (f"75% of Total RAM ({pct_75/1024.0:.1f} GB)", pct_75),
            (f"90% of Total RAM ({pct_90/1024.0:.1f} GB)", pct_90),
        ]
        for label, mb_val in pct_presets:
            act = budget_menu.addAction(label)
            act.setCheckable(True)
            act.triggered.connect(lambda checked=False, m=mb_val: self._on_set_cache_budget(m))
            self.budget_action_group.addAction(act)
            self.budget_actions[mb_val] = act

        budget_menu.addSeparator()

        for gb in get_dynamic_gb_presets(total_ram_mb):
            mb_preset = gb * 1024.0
            act = budget_menu.addAction(f"{gb} GB ({int(mb_preset)} MB)")
            act.setCheckable(True)
            act.triggered.connect(lambda checked=False, m=mb_preset: self._on_set_cache_budget(m))
            self.budget_action_group.addAction(act)
            self.budget_actions[mb_preset] = act

        if self.cache.auto_memory_budget:
            self.auto_budget_action.setChecked(True)
            self.cache.set_auto_memory_budget(True)
        else:
            target_mb = self.cache.max_memory_mb
            if target_mb in self.budget_actions:
                self.budget_actions[target_mb].setChecked(True)

        budget_menu.aboutToShow.connect(self._refresh_budget_menu_labels)

        # 10. Window Menu (Categorized sections ordered in ascending alphabetical order)
        win_menu = mb.addMenu("&Window")

        # Category 1: Panels & Navigation (Alphabetical A-Z)
        win_menu.addSection("Panels & Navigation")
        win_menu.addAction("Audio Settings & Offsets").triggered.connect(self._on_open_audio_settings)
        console_act = win_menu.addAction("Console & Error Log (Ctrl+Shift+L)")
        console_act.setCheckable(True)
        console_act.setChecked(False)
        console_act.triggered.connect(self._on_toggle_console_window)
        self.act_console_window = console_act
        win_menu.addAction("File Browser (Ctrl+B)").triggered.connect(self._on_toggle_file_browser)
        win_menu.addAction("Sources & Session Manager (`)").triggered.connect(self._on_toggle_playlist)

        # Category 3: Panel Layout & Docking (Alphabetical A-Z)
        win_menu.addSection("Panel Layout & Docking")
        win_menu.addAction("Close All Floating Panels").triggered.connect(self._on_close_all_panels)
        win_menu.addAction("Dock Visible Panels to Right Edge").triggered.connect(self._on_dock_all_panels_right)
        win_menu.addAction("Float / Restore All Panels").triggered.connect(self._on_float_all_panels)

        # Category 4: Toolbars (Alphabetical A-Z)
        win_menu.addSection("Toolbars")
        markup_tb_act = win_menu.addAction("Annotation Toolbox (X)")
        markup_tb_act.setCheckable(True)
        markup_tb_act.setChecked(False)
        markup_tb_act.triggered.connect(self._on_toggle_annotation_tools)
        self.act_annotation_toolbox = markup_tb_act

        top_tb_act = win_menu.addAction("Top Controls Toolbar")
        top_tb_act.setCheckable(True)
        top_tb_act.setChecked(True)
        top_tb_act.toggled.connect(self.top_toolbar.setVisible)
        self.top_toolbar.visibilityChanged.connect(top_tb_act.setChecked)

        # Category 5: System & Display (Alphabetical A-Z)
        win_menu.addSection("System & Display")
        pkg_win_act = win_menu.addAction("Package & Plugin Manager...")
        pkg_win_act.triggered.connect(self._on_open_package_manager)

        prefs_act = win_menu.addAction("Preferences... (Ctrl+,)")
        prefs_act.triggered.connect(self._on_open_preferences)

        fs_act = win_menu.addAction("Toggle Full Screen (F12)")
        fs_act.triggered.connect(self._on_toggle_fullscreen)

        # 9. Help Menu
        help_menu = mb.addMenu("&Help")
        app_doc_act = help_menu.addAction("Application Documentation")
        app_doc_act.triggered.connect(lambda: self._open_html_doc("index.html"))
        features_doc_act = help_menu.addAction("Features & Shortcuts Guide")
        features_doc_act.triggered.connect(lambda: self._open_html_doc("features.html"))
        use_cases_doc_act = help_menu.addAction("Use Cases & Workflows")
        use_cases_doc_act.triggered.connect(lambda: self._open_html_doc("use_cases.html"))
        sdk_doc_act = help_menu.addAction("Python SDK Documentation")
        sdk_doc_act.triggered.connect(lambda: self._open_html_doc("sdk.html"))
        open_docs_dir_act = help_menu.addAction("Open Documentation Folder...")
        open_docs_dir_act.triggered.connect(self._open_docs_folder)
        help_menu.addSeparator()
        upd_act = help_menu.addAction("Check for Updates...")
        upd_act.triggered.connect(self._on_open_package_manager_updates)

        help_menu.addSeparator()
        about_act = help_menu.addAction("About OpenReview")
        about_act.triggered.connect(self._show_about)

        # Initial evaluation of Layered Input menu visibility
        self._update_layered_input_menu_visibility()

    def _update_layered_input_menu_visibility(self) -> None:
        """Dynamically show/hide Layered input menu and enable its actions if multilayer EXR or Stereo media is loaded."""
        if not hasattr(self, "layered_input_menu"):
            return

        has_exr = False
        has_stereo = False

        # 1. Check current active stereo pair
        if getattr(self, "_current_stereo_pair", None) is not None:
            has_stereo = True

        # 2. Check active layers in layers widget
        if hasattr(self, "layers_widget"):
            lyrs = getattr(self.layers_widget, "_layers", [])
            if len(lyrs) > 1 or (len(lyrs) == 1 and lyrs[0] != "beauty"):
                has_exr = True

        # 3. Check sources in session_graph
        if hasattr(self, "session_graph"):
            for src in self.session_graph.sources.values():
                p = getattr(src, "path", "")
                if p:
                    p_lower = p.lower()
                    if not has_exr and (".exr" in p_lower):
                        has_exr = True
                    if not has_stereo:
                        try:
                            from openreview.core.stereo import detect_stereo_counterpart
                            if detect_stereo_counterpart(p):
                                has_stereo = True
                        except Exception:
                            pass
                if has_exr and has_stereo:
                    break

        menu_visible = has_exr or has_stereo
        self.layered_input_menu.menuAction().setVisible(menu_visible)
        if hasattr(self, "act_exr_pass_inspector"):
            self.act_exr_pass_inspector.setEnabled(has_exr)
        if hasattr(self, "act_stereo_controls"):
            self.act_stereo_controls.setEnabled(has_stereo)

    # --------------------------------------------------------------------------
    # Keyboard Shortcuts (RV / Nuke Hybrid)
    # --------------------------------------------------------------------------
    def _setup_shortcuts(self) -> None:
        """Bind centralized shortcut actions to their execution callbacks."""
        sm = self.shortcut_mgr

        # Transport & Playback
        sm.bind_callback("playback.toggle", self.clock.toggle_playback)
        sm.bind_callback("playback.play_fwd", self.clock.play_forward)
        sm.bind_callback("playback.play_back", self.clock.play_backward)
        sm.bind_callback("playback.step_fwd", lambda: self.clock.step_forward(1))
        sm.bind_callback("playback.step_back", lambda: self.clock.step_backward(1))
        sm.bind_callback("playback.step_fwd_10", lambda: self.clock.step_forward(10))
        sm.bind_callback("playback.step_back_10", lambda: self.clock.step_backward(10))
        sm.bind_callback("playback.first_frame", self.clock.first_frame)
        sm.bind_callback("playback.last_frame", self.clock.last_frame)
        sm.bind_callback("playback.shuttle_rev", self.clock.shuttle_reverse)
        sm.bind_callback("playback.shuttle_stop", self.clock.shuttle_stop)
        sm.bind_callback("playback.shuttle_fwd", self.clock.shuttle_forward)
        sm.bind_callback("playback.set_fps", self._on_choose_frame_rate)

        # Channels
        sm.bind_callback("channel.rgb", lambda: self.top_bar.toggle_channel_mode(0))
        sm.bind_callback("channel.red", lambda: self.top_bar.toggle_channel_mode(1))
        sm.bind_callback("channel.green", lambda: self.top_bar.toggle_channel_mode(2))
        sm.bind_callback("channel.blue", lambda: self.top_bar.toggle_channel_mode(3))
        sm.bind_callback("channel.alpha", lambda: self.top_bar.toggle_channel_mode(4))
        sm.bind_callback("channel.luma", lambda: self.top_bar.toggle_channel_mode(5))

        # View & Display
        sm.bind_callback("view.fit", self.viewport.fit_image)
        sm.bind_callback("view.actual_size", self.viewport.set_zoom_100)
        sm.bind_callback("view.fit_width", self.viewport.fit_width)
        sm.bind_callback("view.zoom_100", lambda: self.viewport.set_scale_preset(1.0))
        sm.bind_callback("view.zoom_200", lambda: self.viewport.set_scale_preset(2.0))
        sm.bind_callback("view.zoom_400", lambda: self.viewport.set_scale_preset(4.0))
        sm.bind_callback("view.zoom_800", lambda: self.viewport.set_scale_preset(8.0))
        sm.bind_callback("view.flop_x", self.viewport.toggle_flop_x)
        sm.bind_callback("view.flip_y", self.viewport.toggle_flip_y)
        sm.bind_callback("view.filter_toggle", self._on_toggle_texture_filtering)
        sm.bind_callback("view.fullscreen", self._on_toggle_fullscreen)
        sm.bind_callback("view.presentation", self._on_toggle_presentation_mode)
        sm.bind_callback("view.matte_cycle", self._on_cycle_matte_ratio)
        sm.bind_callback("view.matte_opacity_cycle", self._on_cycle_matte_opacity)

        # Color & Exposure
        sm.bind_callback("color.gain_up", lambda: self._step_gain(0.10))
        sm.bind_callback("color.gain_down", lambda: self._step_gain(-0.10))
        sm.bind_callback("color.gamma_up", lambda: self._step_gamma(0.05))
        sm.bind_callback("color.gamma_down", lambda: self._step_gamma(-0.05))
        sm.bind_callback("color.sat_up", lambda: self._step_saturation(0.05))
        sm.bind_callback("color.sat_down", lambda: self._step_saturation(-0.05))
        sm.bind_callback("color.invert_toggle", self._on_toggle_invert_shortcut)
        sm.bind_callback("color.exp_up", lambda: self.top_bar.exp_spin.setValue(round(self.top_bar.exp_spin.value() + 0.25, 2)))
        sm.bind_callback("color.exp_down", lambda: self.top_bar.exp_spin.setValue(round(self.top_bar.exp_spin.value() - 0.25, 2)))
        sm.bind_callback("color.exp_reset", self.top_bar.reset_exposure)
        sm.bind_callback("color.gamma_reset", self.top_bar.reset_gamma)
        sm.bind_callback("color.reset_all", self._on_reset_all_colors)
        sm.bind_callback("color.adjustments", lambda: self._on_open_color_adjustments())

        # Timeline
        sm.bind_callback("timeline.zoom_in", self.timeline.zoom_in)
        sm.bind_callback("timeline.zoom_out", self.timeline.zoom_out)
        sm.bind_callback("timeline.goto_frame", self._on_goto_frame_modal)
        sm.bind_callback("timeline.magnifier_toggle", self.timeline.toggle_magnifier_widget)

        # Marking & Bookmarks
        sm.bind_callback("mark.in", lambda: self.clock.set_in_mark())
        sm.bind_callback("mark.out", lambda: self.clock.set_out_mark())
        sm.bind_callback("mark.clear_in_out", self.clock.clear_in_out)
        sm.bind_callback("mark.bookmark_toggle", lambda: self.clock.toggle_bookmark())
        sm.bind_callback("mark.bookmark_next", self._on_next_marker)
        sm.bind_callback("mark.bookmark_prev", self._on_prev_marker)
        sm.bind_callback("mark.bookmark_segment_next", self._on_step_clip_or_segment_next)
        sm.bind_callback("mark.bookmark_segment_prev", self._on_step_clip_or_segment_prev)
        sm.bind_callback("mark.clear_in", self.clock.clear_in_out)
        sm.bind_callback("mark.clear_out", self.clock.clear_in_out)

        # Media Reloading
        sm.bind_callback("media.reload_frame", self._on_reload_current_frame)
        sm.bind_callback("media.reload_sequence", self._on_reload_sequence)

        # Media Stacking & Layer Cycling (Z / Shift+Z)
        sm.bind_callback("stack.cycle_fwd", self._on_stack_cycle_forward)
        sm.bind_callback("stack.cycle_bwd", self._on_stack_cycle_backward)

        # HUDs & Panels
        sm.bind_callback("hud.histogram", self._toggle_hud_hist)
        sm.bind_callback("hud.waveform", self._toggle_hud_waveform)
        sm.bind_callback("hud.vectorscope", self._toggle_hud_vectorscope)
        sm.bind_callback("hud.color_inspector", self._on_toggle_color_inspector)
        sm.bind_callback("hud.wipe", self._on_toggle_wipe)
        sm.bind_callback("hud.wipe_flip_orientation", self._on_flip_wipe_orientation)
        sm.bind_callback("hud.wipe_reset", self._on_reset_wipe)
        sm.bind_callback("hud.source_details", self._on_toggle_source_details)
        sm.bind_callback("hud.info_strip", self._on_toggle_info_strip)
        sm.bind_callback("hud.dailies_sync", self._on_open_dailies_sync)
        sm.bind_callback("hud.layers", lambda: self.hud_layers.setVisible(not self.hud_layers.isVisible()))
        sm.bind_callback("hud.stereo", lambda: self.hud_stereo.setVisible(not self.hud_stereo.isVisible()))
        sm.bind_callback("hud.toggle_file_browser", self._on_toggle_file_browser)
        sm.bind_callback("hud.console", self._on_toggle_console_window)
        sm.bind_callback("hud.comp_qc", self._on_toggle_comp_qc)

        # Annotations
        sm.bind_callback("annot.toggle_pen", self.markup_palette.toggle_draw)
        sm.bind_callback("annot.undo", lambda: self.annotation_store.undo(self.clock.current_frame))
        sm.bind_callback("annot.redo", lambda: self.annotation_store.redo(self.clock.current_frame))
        sm.bind_callback("annot.clear_frame", self._on_clear_annotations)
        sm.bind_callback("annot.next_keyframe", self._on_next_keyframe)
        sm.bind_callback("annot.prev_keyframe", self._on_prev_keyframe)
        sm.bind_callback("annot.toggle_palette", self._on_toggle_annotation_tools)
        sm.bind_callback("annot.manager", self._on_open_annotation_manager)
        sm.bind_callback("annot.export", self._on_export_annotations)

        # Sources & Session Manager
        sm.bind_callback("playlist.toggle", self._on_toggle_playlist)

        # Pipeline Versioning
        sm.bind_callback("version.up", self._on_version_up)
        sm.bind_callback("version.down", self._on_version_down)

        # Safe Guides & Overlays
        sm.bind_callback("view.safe_guides", self._on_toggle_safe_guides)
        sm.bind_callback("view.onion_skin", self._on_toggle_onion_skin)

        # Timeline Timecode
        sm.bind_callback("timeline.toggle_timecode", self._on_toggle_timecode)

        # Sessions & App
        sm.bind_callback("session.new", self._on_new_session)
        sm.bind_callback("session.close", self._on_close_session)
        sm.bind_callback("session.open_media", self._on_open_media)
        sm.bind_callback("session.open", self._on_open_session)
        sm.bind_callback("session.save", self._on_save_session)
        sm.bind_callback("session.save_as", self._on_save_session_as)
        sm.bind_callback("session.export", self._on_export_media)
        sm.bind_callback("app.preferences", self._on_open_preferences)
        sm.bind_callback("app.clear_cache", self._on_clear_cache)
        sm.bind_callback("app.clear_all_cache", self._on_clear_all_cache)
        sm.bind_callback("app.toggle_cache", self._on_toggle_cache)
        sm.bind_callback("app.quit", self.close)
        sm.bind_callback("app.escape", self._on_escape_pressed)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        """Handle top-level keyboard events via centralized ShortcutManager."""
        focus_w = self.focusWidget()

        # Check if focused widget or any parent is an editable input or in-canvas editor
        target = focus_w
        is_text_input = False
        while target is not None:
            if target.__class__.__name__ == "CanvasTextEditor":
                is_text_input = True
                break
            if isinstance(target, (QLineEdit, QTextEdit, QPlainTextEdit, QSpinBox, QDoubleSpinBox)):
                is_text_input = True
                break
            target = target.parent() if hasattr(target, "parent") else None

        if is_text_input:
            super().keyPressEvent(event)
            return

        # Direct Shift+C hotkey to toggle cache
        if event.modifiers() == Qt.KeyboardModifier.ShiftModifier and event.key() == Qt.Key.Key_C:
            self._on_toggle_cache()
            event.accept()
            return

        # Direct Ctrl+Delete hotkey to clear all RAM cache
        if event.modifiers() == Qt.KeyboardModifier.ControlModifier and event.key() == Qt.Key.Key_Delete:
            self._on_clear_all_cache()
            event.accept()
            return

        # If in-canvas text editor is active or Text annotation tool is selected
        if self.viewport.is_typing_active() or self.viewport.active_draw_tool == ShapeType.TEXT:
            if event.key() == Qt.Key.Key_Escape:
                if self.viewport.is_typing_active():
                    self.viewport.cancel_text_editor()
                else:
                    self.viewport.set_draw_tool(None)
                    self.markup_palette.select_tool(None)
                event.accept()
                return

            # Pass key event directly to viewport for direct typing
            self.viewport.keyPressEvent(event)
            if event.isAccepted():
                return
            return

        # Direct Q hotkey (Hold to peek, click/pin to latch)
        if not event.isAutoRepeat() and event.key() == Qt.Key.Key_Q and event.modifiers() == Qt.KeyboardModifier.NoModifier:
            if hasattr(self, "pkg_mgr") and self.pkg_mgr and not self.pkg_mgr.is_plugin_enabled("xtra-qc-Utils"):
                return
            dock = getattr(self, "comp_qc_dock", None)
            if dock is not None:
                self._q_key_held = True
                if dock.isHidden():
                    dock.show()
                    if (hasattr(self, "file_browser_dock") and
                        self.dockWidgetArea(self.file_browser_dock) == self.dockWidgetArea(dock) and
                        not self.file_browser_dock.isFloating() and not dock.isFloating()):
                        self.tabifyDockWidget(self.file_browser_dock, dock)
                    dock.raise_()
                event.accept()
                return

        # Direct O or 0 hotkey for Overlay Matte toggle
        if not event.isAutoRepeat() and event.key() in (Qt.Key.Key_O, Qt.Key.Key_0) and event.modifiers() == Qt.KeyboardModifier.NoModifier:
            if hasattr(self, "pkg_mgr") and self.pkg_mgr and not self.pkg_mgr.is_plugin_enabled("xtra-qc-Utils"):
                return
            if hasattr(self, "comp_qc_state") and self.comp_qc_state:
                self.comp_qc_state.overlay_matte_enabled = not self.comp_qc_state.overlay_matte_enabled
                st = "Enabled" if self.comp_qc_state.overlay_matte_enabled else "Disabled"
                self.statusBar().showMessage(f"xtra QC: Overlay Matte {st}", 2000)
                dock = getattr(self, "comp_qc_dock", None)
                panel = getattr(dock, "panel", dock) if dock else getattr(self, "hud_comp_qc", None)
                if panel and hasattr(panel, "btn_overlay_matte"):
                    panel.btn_overlay_matte.blockSignals(True)
                    panel.btn_overlay_matte.setChecked(self.comp_qc_state.overlay_matte_enabled)
                    panel.btn_overlay_matte.blockSignals(False)
                self._sync_matte_frame()
                self.viewport.update()
                event.accept()
                return

        if self.shortcut_mgr.eventFilter(self, event):
            return

        super().keyPressEvent(event)

    def keyReleaseEvent(self, event: QKeyEvent) -> None:
        """Handle key release events, closing QC dock if unlatched when releasing Q."""
        if not event.isAutoRepeat() and event.key() == Qt.Key.Key_Q:
            if hasattr(self, "pkg_mgr") and self.pkg_mgr and not self.pkg_mgr.is_plugin_enabled("xtra-qc-Utils"):
                return
            self._q_key_held = False
            dock = getattr(self, "comp_qc_dock", None)
            if dock is not None and not dock.isHidden():
                # Only close if NOT latched / pinned by user interaction
                if not getattr(dock, "is_latched", lambda: False)():
                    dock.hide()
                    event.accept()
                    return
        super().keyReleaseEvent(event)

    # --------------------------------------------------------------------------
    # Signal Wiring
    # --------------------------------------------------------------------------
    def _connect_signals(self) -> None:
        # Clock -> App & UI
        self.clock.frame_changed.connect(self._on_frame_changed)
        self.clock.fps_measured.connect(self._on_fps_measured)
        self.clock.in_out_changed.connect(self._on_in_out_changed_for_cache)
        self.clock.playback_state_changed.connect(self._on_playback_state_changed)

        # Reader -> Cache -> UI
        self.reader_engine.frame_ready.connect(self._on_frame_ready)
        self.cache.cache_changed.connect(self._schedule_cache_ui_update)
        self.cache.memory_usage_changed.connect(self._on_memory_changed)
        self.timeline.cache_mode_cycle_requested.connect(self._on_cycle_cache_mode)
        if hasattr(self.timeline, "cache_mode_selected"):
            self.timeline.cache_mode_selected.connect(self._on_set_cache_mode)
        self.timeline.audio_clicked.connect(self._on_open_audio_settings)
        self.timeline.clear_cache_requested.connect(self._on_clear_all_cache)
        self.timeline.clear_annotations_requested.connect(
            lambda: (self.viewport.update(), self.statusBar().showMessage("All annotations cleared.", 3000))
        )

        # Top Bar -> Viewport
        self.top_bar.gain_changed.connect(self._on_gain_changed)
        self.top_bar.gamma_changed.connect(self._on_gamma_changed)
        self.top_bar.saturation_changed.connect(self._on_saturation_changed)
        self.top_bar.exposure_changed.connect(self._on_exposure_changed)
        self.top_bar.audio_clicked.connect(self._on_open_audio_settings)
        self.top_bar.channel_changed.connect(self._on_channel_changed)
        self.top_bar.layer_changed.connect(self._on_layer_changed)
        self.top_bar.stereo_changed.connect(self._on_stereo_changed)
        self.top_bar.color_transforms_changed.connect(self._on_top_bar_color_transforms_changed)
        self.top_bar.zoom_fit_requested.connect(self.viewport.fit_image)
        self.top_bar.zoom_100_requested.connect(self.viewport.set_zoom_100)

        # Markup Palette -> Viewport & Annotation Store
        self.markup_palette.tool_changed.connect(self.viewport.set_draw_tool)
        self.viewport.draw_tool_changed.connect(self.markup_palette.select_tool)
        self.markup_palette.color_changed.connect(self.viewport.set_draw_color)
        self.markup_palette.stroke_width_changed.connect(self.viewport.set_draw_width)
        self.markup_palette.opacity_changed.connect(self.viewport.set_draw_opacity)
        self.markup_palette.clear_requested.connect(self._on_clear_annotations)
        self.markup_palette.undo_requested.connect(lambda: self.annotation_store.undo(self.clock.current_frame))
        self.markup_palette.redo_requested.connect(lambda: self.annotation_store.redo(self.clock.current_frame))
        self.annotation_store.annotations_changed.connect(self._on_annotations_changed)

        # Global shortcut suppression while typing or Text tool active
        self.shortcut_mgr.set_suppress_predicate(
            lambda: self.viewport.is_typing_active() or self.viewport.active_draw_tool == ShapeType.TEXT
        )

        # Viewport <-> Pixel Inspector Status Bar & Color Inspector HUD & Resolution
        self.viewport.pixel_inspected.connect(self._on_pixel_inspected)
        self.viewport.pixel_inspected.connect(self.inspector_widget.update_pixel)
        self.viewport.samplers_changed.connect(self.inspector_widget.update_samplers)
        self.viewport.eyedropper_mode_changed.connect(self.inspector_widget.set_eyedropper_active)
        self.inspector_widget.eyedropper_toggled.connect(self.viewport.set_eyedropper_mode)
        self.inspector_widget.aperture_changed.connect(self.viewport.set_inspector_aperture)
        self.inspector_widget.sampler_remove_requested.connect(self.viewport.remove_color_sampler)
        self.inspector_widget.samplers_clear_requested.connect(self.viewport.clear_color_samplers)
        self.inspector_widget.active_sampler_changed.connect(self.viewport.set_active_analyzer_sampler)
        self.inspector_widget.analyzer_checks_changed.connect(self.viewport.set_pixel_analyzer_checks)
        self.viewport.set_pixel_analyzer_checks(
            self.inspector_widget.chk_nan_btn.isChecked(),
            self.inspector_widget.chk_inf_btn.isChecked(),
            self.inspector_widget.chk_neg_btn.isChecked(),
        )
        self.hud_inspector.closed.connect(lambda: self.viewport.set_eyedropper_mode(False))
        self.viewport.zoom_changed.connect(self._on_zoom_changed)
        self.viewport.resolution_changed.connect(lambda w, h: self._update_resolution_status(f"{w}x{h}" if (w > 0 and h > 0) else "No Media"))

        # A/B Wipe HUD <-> Viewport bidirectional sync
        self.wipe_widget.comparison_changed.connect(self._on_comparison_changed)
        self.viewport.wipe_position_changed.connect(self.wipe_widget.update_wipe_position)
        self.viewport.wipe_angle_changed.connect(self.wipe_widget.update_wipe_angle)

        # Viewport Drag-and-Drop file loading
        self.viewport.files_dropped_target.connect(self._on_viewport_files_dropped_target)

        # Playback Clock Shuttle Speed changes
        self.clock.shuttle_speed_changed.connect(self._on_shuttle_speed_changed)

        # Viewport Presentation & Fullscreen Request Signals
        self.viewport.exit_presentation_requested.connect(self._on_toggle_presentation_mode)
        self.viewport.toggle_presentation_requested.connect(self._on_toggle_presentation_mode)
        self.viewport.exit_fullscreen_requested.connect(self._on_toggle_fullscreen)
        self.viewport.toggle_fullscreen_requested.connect(self._on_toggle_fullscreen)

        # Audio Engine Clock & Scrubbing Sync
        self.clock.frame_changed.connect(self._on_audio_clock_sync)
        self.timeline.seek_requested.connect(self._on_timeline_seek)
        if hasattr(self.timeline, "seek_finished"):
            self.timeline.seek_finished.connect(self._on_scrub_settled)
        self.timeline.focus_viewport_requested.connect(self.viewport.setFocus)
        self.clock.range_changed.connect(lambda s, e: self.audio_engine.set_sequence_context(s, self.clock.fps))
        self.clock.fps_changed.connect(lambda fps: self.audio_engine.set_sequence_context(self.clock.start_frame, fps))
        self.audio_engine.offsets_changed.connect(lambda _s, _f: self._on_audio_clock_sync(self.clock.current_frame))
        self.audio_engine.mute_changed.connect(lambda m: hasattr(self, 'audio_mute_act') and self.audio_mute_act.setChecked(m))
        self.audio_engine.scrubbing_changed.connect(lambda s: hasattr(self, 'audio_scrub_act') and self.audio_scrub_act.setChecked(s))

        # Dailies Sync Engine Signals
        self.sync_engine.status_changed.connect(self._on_sync_status_changed)
        self.sync_engine.remote_playback_received.connect(self._on_remote_playback)
        self.sync_engine.remote_media_received.connect(self._on_remote_media)
        self.sync_engine.remote_color_received.connect(self._on_remote_color)
        self.sync_engine.remote_stroke_received.connect(self._on_remote_stroke)
        self.sync_engine.remote_annotations_cleared.connect(self._on_remote_clear_annotations)
        self.sync_engine.remote_note_received.connect(self._on_remote_note)

        # OpenColorIO Configuration Updates
        self.ocio_manager.config_changed.connect(self._on_ocio_config_changed)

        # Dynamic Layered Input Menu Visibility Sync
        if hasattr(self, "session_graph"):
            self.session_graph.graph_changed.connect(self._update_layered_input_menu_visibility)

    # --------------------------------------------------------------------------
    # Event Handlers & Core Updates
    # --------------------------------------------------------------------------
    def _normalize_frame_data(self, data: Optional[np.ndarray | tuple]) -> Optional[np.ndarray | tuple]:
        if data is None:
            return None
        if isinstance(data, (tuple, list)):
            return tuple(self._normalize_frame_data(x) for x in data)
        if not isinstance(data, np.ndarray):
            return data
        if data.dtype == np.float32:
            return data.astype(np.float16, copy=False)
        return data

    def _update_pinned_frame(self, source_id: Optional[str], frame: int) -> None:
        """Protect currently displayed frame in cache from eviction while streaming to viewport."""
        last_pinned = getattr(self, "_last_pinned_key", None)
        new_key = (source_id, frame)
        if last_pinned != new_key:
            if last_pinned and hasattr(self.cache, "unpin_frame"):
                self.cache.unpin_frame(last_pinned[0], last_pinned[1])
            if hasattr(self.cache, "pin_frame"):
                self.cache.pin_frame(source_id, frame)
            self._last_pinned_key = new_key

    def _on_immediate_frame_ready(
        self,
        timeline_frame: int,
        source_frame: int,
        source_id: str,
        success: bool,
        data: Optional[np.ndarray],
        is_proxy: bool = False,
    ) -> None:
        """Handle completion of an immediate high-priority asynchronous decode."""
        if not success or data is None:
            return
        if self.clock.current_frame != timeline_frame:
            return

        orig_w = getattr(self.viewport, "_img_width", 0)
        orig_h = getattr(self.viewport, "_img_height", 0)
        logical_size = (orig_w, orig_h) if is_proxy and orig_w > 0 else None

        # Store in cache if full-res upgrade and caching not OFF
        if hasattr(self, "cache") and self.cache and self.cache.mode != CacheMode.OFF and not is_proxy:
            if source_id:
                self.cache.put_source_frame(source_id, source_frame, data, is_proxy=False)
            else:
                self.cache.put_frame(timeline_frame, data, is_proxy=False)

        viewable = self.session_graph.get_active_viewable() if hasattr(self, "session_graph") else None
        if isinstance(viewable, SequenceView):
            source, src_frame = viewable.resolve_frame(timeline_frame, self.session_graph.sources)
            if source and (source.id == source_id or not source_id):
                self._update_pinned_frame(source.id, source_frame)
                self.viewport.set_primary_frame(timeline_frame, data, logical_size=logical_size, is_proxy=is_proxy)
                if hasattr(data, "shape") and len(data.shape) >= 2:
                    if is_proxy:
                        pw = orig_w if orig_w > 0 else data.shape[1]
                        ph = orig_h if orig_h > 0 else data.shape[0]
                        self._update_resolution_status(f"{pw}x{ph} (Proxy)")
                    else:
                        self._update_resolution_status(f"{data.shape[1]}x{data.shape[0]}")
                self._update_hud_scopes(data)
        elif isinstance(viewable, StackView):
            layer_a = viewable.get_layer_a(self.session_graph.sources)
            layer_b = viewable.get_layer_b(self.session_graph.sources)
            if layer_a and (layer_a.id == source_id or not source_id):
                self._update_pinned_frame(layer_a.id, source_frame)
                self.viewport.set_primary_frame(timeline_frame, data, logical_size=logical_size, is_proxy=is_proxy)
                if hasattr(data, "shape") and len(data.shape) >= 2:
                    if is_proxy:
                        pw = orig_w if orig_w > 0 else data.shape[1]
                        ph = orig_h if orig_h > 0 else data.shape[0]
                        self._update_resolution_status(f"{pw}x{ph} (Proxy)")
                    else:
                        self._update_resolution_status(f"{data.shape[1]}x{data.shape[0]}")
                self._update_hud_scopes(data)
            elif layer_b and layer_b.id == source_id:
                self.viewport.set_reference_frame(data)
        else:
            self._update_pinned_frame(source_id or None, timeline_frame)
            self.viewport.set_primary_frame(timeline_frame, data, logical_size=logical_size, is_proxy=is_proxy)
            if hasattr(data, "shape") and len(data.shape) >= 2:
                if is_proxy:
                    pw = orig_w if orig_w > 0 else data.shape[1]
                    ph = orig_h if orig_h > 0 else data.shape[0]
                    self._update_resolution_status(f"{pw}x{ph} (Proxy)")
                else:
                    self._update_resolution_status(f"{data.shape[1]}x{data.shape[0]}")
            self._update_hud_scopes(data)

    def _on_frame_changed(self, frame: int) -> None:
        self.cache.update_playhead(frame)
        self.viewport.update_info_strip_data(current_frame=frame)

        if hasattr(self, "pkg_mgr") and self.pkg_mgr:
            self.pkg_mgr.dispatch_hook("on_frame_changed", frame)

        viewable = self.session_graph.get_active_viewable() if hasattr(self, "session_graph") else None
        sync_read_allowed = not getattr(self, "_async_drop_active", False)

        if isinstance(viewable, SequenceView):
            source, src_frame = viewable.resolve_frame(frame, self.session_graph.sources)
            data = None
            is_proxy = False
            if source:
                self.cache.set_active_source(source.id)
                data = self.cache.get_source_frame(source.id, src_frame)
                is_proxy = self.cache.is_source_frame_proxy(source.id, src_frame) if data is not None else False
                if data is None:
                    reader = self._get_reader_for_source(source)
                    if reader:
                        if self._is_scrubbing or self.clock.is_playing:
                            data = read_adaptive_frame(reader, src_frame, normalize_fn=self._normalize_frame_data)
                            if data is not None:
                                is_proxy = True
                                if self.cache.mode != CacheMode.OFF:
                                    self.cache.put_source_frame(source.id, src_frame, data, is_proxy=True)
                            elif hasattr(self, "reader_engine") and self.reader_engine:
                                self.reader_engine.request_immediate_frame(
                                    source.id, src_frame, frame, reader=reader, downsample=True, callback=self._on_immediate_frame_ready
                                )
                        elif not self.clock.is_playing and sync_read_allowed:
                            data = self._normalize_frame_data(reader.read_frame(src_frame))
                            if self.cache.mode != CacheMode.OFF and data is not None:
                                self.cache.put_source_frame(source.id, src_frame, data, is_proxy=False)
                        elif hasattr(self, "reader_engine") and self.reader_engine:
                            self.reader_engine.request_immediate_frame(
                                source.id, src_frame, frame, reader=reader, downsample=False, callback=self._on_immediate_frame_ready
                            )
            self.viewport.comparison_state.channels_a = (True, True, True, True)
            self.viewport.set_reference_frame(None)
            if data is not None:
                self._update_pinned_frame(source.id if source else None, src_frame if source else frame)
                orig_w = getattr(self.viewport, "_img_width", 0)
                orig_h = getattr(self.viewport, "_img_height", 0)
                if orig_w == 0 or orig_h == 0:
                    orig_w = getattr(reader, "width", 0) if "reader" in locals() and reader else 0
                    orig_h = getattr(reader, "height", 0) if "reader" in locals() and reader else 0
                logical_size = (orig_w, orig_h) if is_proxy and orig_w > 0 else None
                self.viewport.set_primary_frame(frame, data, logical_size=logical_size, is_proxy=is_proxy)
                if hasattr(data, "shape") and len(data.shape) >= 2:
                    if is_proxy:
                        pw = orig_w if orig_w > 0 else data.shape[1]
                        ph = orig_h if orig_h > 0 else data.shape[0]
                        self._update_resolution_status(f"{pw}x{ph} (Proxy)")
                    else:
                        self._update_resolution_status(f"{data.shape[1]}x{data.shape[0]}")
                self._update_hud_scopes(data)
            elif source is None:
                self.viewport.set_primary_frame(frame, None)

        elif isinstance(viewable, StackView):
            disp_is_proxy = False
            is_switch_layer_b = (viewable.compare_mode == "switch" and viewable.current_layer_index == viewable.index_b)
            if is_switch_layer_b:
                # Solo Layer B display (toggled with Z) - strictly guaranteed full RGBA
                layer_b = viewable.get_layer_b(self.session_graph.sources)
                data_b = None
                is_proxy_b = False
                if layer_b:
                    self.cache.set_active_source(layer_b.id)
                    data_b = self.cache.get_source_frame(layer_b.id, frame)
                    is_proxy_b = self.cache.is_source_frame_proxy(layer_b.id, frame) if data_b is not None else False
                    if data_b is None:
                        reader_b = self._get_reader_for_source(layer_b)
                        if reader_b:
                            if self._is_scrubbing or self.clock.is_playing:
                                data_b = read_adaptive_frame(reader_b, frame, normalize_fn=self._normalize_frame_data)
                                if data_b is not None:
                                    is_proxy_b = True
                                    if self.cache.mode != CacheMode.OFF:
                                        self.cache.put_source_frame(layer_b.id, frame, data_b, is_proxy=True)
                                elif hasattr(self, "reader_engine") and self.reader_engine:
                                    self.reader_engine.request_immediate_frame(
                                        layer_b.id, frame, frame, reader=reader_b, downsample=True, callback=self._on_immediate_frame_ready
                                    )
                            elif not self.clock.is_playing and sync_read_allowed:
                                data_b = self._normalize_frame_data(reader_b.read_frame(frame))
                                if self.cache.mode != CacheMode.OFF and data_b is not None:
                                    self.cache.put_source_frame(layer_b.id, frame, data_b, is_proxy=False)
                            elif hasattr(self, "reader_engine") and self.reader_engine:
                                self.reader_engine.request_immediate_frame(
                                    layer_b.id, frame, frame, reader=reader_b, downsample=False, callback=self._on_immediate_frame_ready
                                )
                disp_is_proxy = is_proxy_b
                self.viewport.comparison_state.mode = ComparisonMode.OFF
                self.viewport.comparison_state.channels_a = (True, True, True, True)
                self.viewport.set_reference_frame(None)
                if data_b is not None:
                    self._update_pinned_frame(layer_b.id if layer_b else None, frame)
                    orig_w = getattr(self.viewport, "_img_width", 0)
                    orig_h = getattr(self.viewport, "_img_height", 0)
                    if orig_w == 0 or orig_h == 0:
                        orig_w = getattr(reader_b, "width", 0) if "reader_b" in locals() and reader_b else 0
                        orig_h = getattr(reader_b, "height", 0) if "reader_b" in locals() and reader_b else 0
                    logical_size = (orig_w, orig_h) if is_proxy_b and orig_w > 0 else None
                    self.viewport.set_primary_frame(frame, data_b, logical_size=logical_size, is_proxy=is_proxy_b)
                    if hasattr(data_b, "shape") and len(data_b.shape) >= 2:
                        if is_proxy_b:
                            pw = orig_w if orig_w > 0 else data_b.shape[1]
                            ph = orig_h if orig_h > 0 else data_b.shape[0]
                            self._update_resolution_status(f"{pw}x{ph} (Proxy)")
                        else:
                            self._update_resolution_status(f"{data_b.shape[1]}x{data_b.shape[0]}")
                    self._update_hud_scopes(data_b)
                elif layer_b is None:
                    self.viewport.set_primary_frame(frame, None)
                disp_data = data_b
            else:
                # Normal comparison or Layer A mixed over Layer B (in "switch" or "over")
                layer_a = viewable.get_layer_a(self.session_graph.sources)
                layer_b = viewable.get_layer_b(self.session_graph.sources)
                data_a = None
                data_b = None
                is_proxy_a = False
                if layer_a:
                    self.cache.set_active_source(layer_a.id)
                    data_a = self.cache.get_source_frame(layer_a.id, frame)
                    is_proxy_a = self.cache.is_source_frame_proxy(layer_a.id, frame) if data_a is not None else False
                    if data_a is None:
                        reader_a = self._get_reader_for_source(layer_a)
                        if reader_a:
                            if self._is_scrubbing or self.clock.is_playing:
                                data_a = read_adaptive_frame(reader_a, frame, normalize_fn=self._normalize_frame_data)
                                if data_a is not None:
                                    is_proxy_a = True
                                    if self.cache.mode != CacheMode.OFF:
                                        self.cache.put_source_frame(layer_a.id, frame, data_a, is_proxy=True)
                                elif hasattr(self, "reader_engine") and self.reader_engine:
                                    self.reader_engine.request_immediate_frame(
                                        layer_a.id, frame, frame, reader=reader_a, downsample=True, callback=self._on_immediate_frame_ready
                                    )
                            elif not self.clock.is_playing and sync_read_allowed:
                                data_a = self._normalize_frame_data(reader_a.read_frame(frame))
                                if self.cache.mode != CacheMode.OFF and data_a is not None:
                                    self.cache.put_source_frame(layer_a.id, frame, data_a, is_proxy=False)
                            elif hasattr(self, "reader_engine") and self.reader_engine:
                                self.reader_engine.request_immediate_frame(
                                    layer_a.id, frame, frame, reader=reader_a, downsample=False, callback=self._on_immediate_frame_ready
                                )
                disp_is_proxy = is_proxy_a
                if layer_b:
                    data_b = self.cache.get_source_frame(layer_b.id, frame)
                    if data_b is None:
                        reader_b = self._get_reader_for_source(layer_b)
                        if reader_b:
                            if self._is_scrubbing or self.clock.is_playing:
                                data_b = read_adaptive_frame(reader_b, frame, normalize_fn=self._normalize_frame_data)
                                if data_b is not None and self.cache.mode != CacheMode.OFF:
                                    self.cache.put_source_frame(layer_b.id, frame, data_b, is_proxy=True)
                        reader_b = self._get_reader_for_source(layer_b)
                        if reader_b:
                            if not self.clock.is_playing and sync_read_allowed:
                                data_b = self._normalize_frame_data(reader_b.read_frame(frame))
                                if self.cache.mode != CacheMode.OFF and data_b is not None:
                                    self.cache.put_source_frame(layer_b.id, frame, data_b)
                            elif hasattr(self, "reader_engine") and self.reader_engine:
                                self.reader_engine.request_immediate_frame(
                                    layer_b.id, frame, frame, reader=reader_b, callback=self._on_immediate_frame_ready
                                )

                mode_map = {
                    "wipe": ComparisonMode.WIPE,
                    "switch": ComparisonMode.OVER,
                    "split": ComparisonMode.SPLIT,
                    "difference": ComparisonMode.DIFFERENCE,
                    "difference_inverted": ComparisonMode.DIFFERENCE_INVERTED,
                    "over": ComparisonMode.OVER,
                    "add": ComparisonMode.ADD,
                }
                comp_mode = mode_map.get(viewable.compare_mode, ComparisonMode.OVER)
                if layer_b is None:
                    comp_mode = ComparisonMode.OFF

                self.viewport.comparison_state.mode = comp_mode
                self.viewport.comparison_state.stack_mix = getattr(viewable, "stack_mix", 1.0)
                self.viewport.comparison_state.channels_a = getattr(viewable, "channels_a", (True, True, True, True))
                if data_a is not None:
                    self._update_pinned_frame(layer_a.id if layer_a else None, frame)
                    orig_w = getattr(self.viewport, "_img_width", 0)
                    orig_h = getattr(self.viewport, "_img_height", 0)
                    logical_size = (orig_w, orig_h) if is_proxy_a and orig_w > 0 else None
                    self.viewport.set_primary_frame(frame, data_a, logical_size=logical_size, is_proxy=is_proxy_a)
                elif layer_a is None:
                    self.viewport.set_primary_frame(frame, None)

                if data_b is not None:
                    self.viewport.set_reference_frame(data_b)
                elif layer_b is None:
                    self.viewport.set_reference_frame(None)
                disp_data = data_a

            if disp_data is not None and hasattr(disp_data, "shape") and len(disp_data.shape) >= 2:
                if disp_is_proxy:
                    orig_w = getattr(self.viewport, "_img_width", disp_data.shape[1])
                    orig_h = getattr(self.viewport, "_img_height", disp_data.shape[0])
                    self._update_resolution_status(f"{orig_w}x{orig_h} (Proxy)")
                else:
                    self._update_resolution_status(f"{disp_data.shape[1]}x{disp_data.shape[0]}")
            if disp_data is not None:
                self._update_hud_scopes(disp_data)

        elif isinstance(viewable, LayoutView):
            def _fetch(src: SourceItem) -> Optional[np.ndarray]:
                sf = viewable.resolve_tile_frame(src, frame, self.session_graph.sources)
                d = self.cache.get_source_frame(src.id, sf)
                if d is None:
                    r = self._get_reader_for_source(src)
                    if r and hasattr(self, "reader_engine") and self.reader_engine:
                        self.reader_engine.request_immediate_frame(
                            src.id, sf, frame, reader=r, callback=self._on_immediate_frame_ready
                        )
                return d

            comp_data = composite_layout_grid(
                viewable,
                self.session_graph.sources,
                _fetch,
            )

            self.viewport.set_reference_frame(None)
            if comp_data is not None:
                self.viewport.set_primary_frame(frame, comp_data)
                if hasattr(comp_data, "shape") and len(comp_data.shape) >= 2:
                    self._update_resolution_status(f"{comp_data.shape[1]}x{comp_data.shape[0]} ({viewable.scale_pct}%)")
                self._update_hud_scopes(comp_data)

        elif getattr(self, "_is_exr_contact_sheet_active", False) and self._is_current_media_exr(viewable, frame):
            comp_data, tile_map = self._composite_current_exr_contact_sheet(viewable, frame)
            self._exr_contact_sheet_tile_map = tile_map
            self.viewport.set_contact_sheet_mode(True, tile_map)
            self.viewport.set_reference_frame(None)
            if comp_data is not None:
                self.viewport.set_primary_frame(frame, comp_data)
                if hasattr(comp_data, "shape") and len(comp_data.shape) >= 2:
                    self._update_resolution_status(f"{comp_data.shape[1]}x{comp_data.shape[0]} (Contact Sheet)")
                self._update_hud_scopes(comp_data)

        else:
            self.viewport.set_contact_sheet_mode(False, None)
            # Standard single source or fallback
            cur_src_id = viewable.id if (viewable and hasattr(viewable, "id")) else None
            self.cache.set_active_source(cur_src_id)
            data = None
            is_proxy = False
            if cur_src_id:
                data = self.cache.get_source_frame(cur_src_id, frame)
                is_proxy = self.cache.is_source_frame_proxy(cur_src_id, frame) if data is not None else False
                if data is None and self.reader is not None:
                    if self._is_scrubbing or self.clock.is_playing:
                        data = read_adaptive_frame(self.reader, frame, normalize_fn=self._normalize_frame_data)
                        if data is not None:
                            is_proxy = True
                            if self.cache.mode != CacheMode.OFF:
                                self.cache.put_source_frame(cur_src_id, frame, data, is_proxy=True)
                        elif hasattr(self, "reader_engine") and self.reader_engine:
                            self.reader_engine.request_immediate_frame(
                                cur_src_id, frame, frame, reader=self.reader, downsample=True, callback=self._on_immediate_frame_ready
                            )
                    elif (not self.clock.is_playing or self.viewport._image_a is None) and sync_read_allowed:
                        data = self._normalize_frame_data(self.reader.read_frame(frame))
                        if self.cache.mode != CacheMode.OFF and data is not None:
                            self.cache.put_source_frame(cur_src_id, frame, data, is_proxy=False)
                    elif hasattr(self, "reader_engine") and self.reader_engine:
                        self.reader_engine.request_immediate_frame(
                            cur_src_id, frame, frame, reader=self.reader, downsample=False, callback=self._on_immediate_frame_ready
                        )
            else:
                data = self.cache.get_frame(frame)
                is_proxy = self.cache.is_frame_proxy(frame) if data is not None else False
                if data is None and self.reader is not None:
                    if self._is_scrubbing or self.clock.is_playing:
                        data = read_adaptive_frame(self.reader, frame, normalize_fn=self._normalize_frame_data)
                        if data is not None:
                            is_proxy = True
                            if self.cache.mode != CacheMode.OFF:
                                self.cache.put_frame(frame, data, is_proxy=True)
                        elif hasattr(self, "reader_engine") and self.reader_engine:
                            self.reader_engine.request_immediate_frame(
                                "", frame, frame, reader=self.reader, downsample=True, callback=self._on_immediate_frame_ready
                            )
                    elif (not self.clock.is_playing or self.viewport._image_a is None) and sync_read_allowed:
                        data = self._normalize_frame_data(self.reader.read_frame(frame))
                        if self.cache.mode != CacheMode.OFF and data is not None:
                            self.cache.put_frame(frame, data, is_proxy=False)
                    elif hasattr(self, "reader_engine") and self.reader_engine:
                        self.reader_engine.request_immediate_frame(
                            "", frame, frame, reader=self.reader, downsample=False, callback=self._on_immediate_frame_ready
                        )

            if data is not None:
                self._update_pinned_frame(cur_src_id, frame)
                orig_w = getattr(self.viewport, "_img_width", 0)
                orig_h = getattr(self.viewport, "_img_height", 0)
                if orig_w == 0 or orig_h == 0:
                    orig_w = getattr(self.reader, "width", 0) if hasattr(self, "reader") and self.reader else 0
                    orig_h = getattr(self.reader, "height", 0) if hasattr(self, "reader") and self.reader else 0
                logical_size = (orig_w, orig_h) if is_proxy and orig_w > 0 else None
                self.viewport.set_primary_frame(frame, data, logical_size=logical_size, is_proxy=is_proxy)
                if hasattr(data, "shape") and len(data.shape) >= 2:
                    if is_proxy:
                        pw = orig_w if orig_w > 0 else data.shape[1]
                        ph = orig_h if orig_h > 0 else data.shape[0]
                        self._update_resolution_status(f"{pw}x{ph} (Proxy)")
                    else:
                        self._update_resolution_status(f"{data.shape[1]}x{data.shape[0]}")
                self._update_hud_scopes(data)
            elif cur_src_id is None and self.reader is None:
                self.viewport.set_primary_frame(frame, None)

            # Check if wipe comparison is active between 2 selected sources or QC comparison
            qc = getattr(self, "comp_qc_state", None)
            is_qc_compare = qc is not None and (
                getattr(qc, "compare_plates", False)
                or getattr(qc, "compare_against_plate", False)
                or getattr(qc, "qc_mode", 0) in (QcMode.HIGHPASS_PLATE, QcMode.COMP_DIFF_PLATE)
            )
            needs_b = (self.viewport.comparison_state.mode != ComparisonMode.OFF) or is_qc_compare

            if needs_b:
                selected_sids = []
                if hasattr(self, "sources_dock"):
                    selected_sids = self.sources_dock.panel._get_selected_source_ids()
                ref_src = None
                if len(selected_sids) >= 2:
                    ref_id = selected_sids[1] if selected_sids[0] == cur_src_id else selected_sids[0]
                    ref_src = self.session_graph.sources.get(ref_id)
                elif len(self.session_graph.sources) > 1 and not is_qc_compare:
                    for sid, s in self.session_graph.sources.items():
                        if sid != cur_src_id:
                            ref_src = s
                            break
                if ref_src:
                    d_b = self.cache.get_source_frame(ref_src.id, frame)
                    if d_b is None:
                        r_b = self._get_reader_for_source(ref_src)
                        d_b = self._normalize_frame_data(r_b.read_frame(frame)) if r_b else None
                        if self.cache.mode != CacheMode.OFF and d_b is not None:
                            self.cache.put_source_frame(ref_src.id, frame, d_b)
                    self.viewport.set_reference_frame(d_b)
                elif hasattr(self, "_reader_b") and self._reader_b is not None:
                    mapped_b = self._resolve_qc_frame(self._reader_b, frame)
                    b_sid = getattr(qc, "source_item_id", None)
                    d_b = None
                    if b_sid:
                        d_b = self.cache.get_source_frame(b_sid, mapped_b)
                    if d_b is None and sync_read_allowed:
                        d_b = self._normalize_frame_data(self._reader_b.read_frame(mapped_b))
                        if b_sid and self.cache.mode != CacheMode.OFF and d_b is not None:
                            self.cache.put_source_frame(b_sid, mapped_b, d_b)
                    self.viewport.set_reference_frame(d_b)
                else:
                    self.viewport.set_reference_frame(None)
            elif getattr(self.viewport, "_image_b", None) is not None:
                self.viewport.set_reference_frame(None)

        # Synchronize matte frame if loaded in xtra QC Utils AND actively used
        self._sync_matte_frame(frame, sync_read_allowed=sync_read_allowed)

        # Broadcast playback if connected as presenter / host
        if self.sync_engine.is_connected and (self.sync_engine.is_presenter or self.sync_engine.is_host):
            if not getattr(self, "_suppress_sync_broadcast", False):
                self.sync_engine.broadcast_playback(
                    frame=frame,
                    is_playing=self.clock.is_playing,
                    fps=self.clock.fps,
                    shuttle_speed=self.clock.shuttle_speed,
                    in_frame=self.clock.in_frame,
                    out_frame=self.clock.out_frame,
                )

        # Trigger prefetching if caching is enabled
        self._trigger_prefetch()

        # Update source details HUD if visible (throttled during continuous playback)
        if hasattr(self, "hud_source_details") and hasattr(self, "source_details_widget") and self.hud_source_details.isVisible():
            import time
            now = time.time()
            is_playing = getattr(self.clock, "is_playing", False)
            last_update = getattr(self, "_last_source_details_update", 0.0)
            if not is_playing or (now - last_update >= 0.15):
                self._last_source_details_update = now
                self.source_details_widget.update_info(self._get_current_source_info())

    def _resolve_timeline_frame_sources(self, timeline_frame: int) -> list[tuple[Optional[str], int, Optional[Any]]]:
        """Decompose an upcoming timeline frame into its underlying source items, frames, and readers."""
        viewable = self.session_graph.get_active_viewable() if hasattr(self, "session_graph") else None
        if isinstance(viewable, SequenceView):
            src, sf = viewable.resolve_frame(timeline_frame, self.session_graph.sources)
            if src:
                return [(src.id, sf, self._get_reader_for_source(src))]
            return []
        elif isinstance(viewable, StackView):
            la = viewable.get_layer_a(self.session_graph.sources)
            lb = viewable.get_layer_b(self.session_graph.sources)
            res = []
            if la:
                res.append((la.id, timeline_frame, self._get_reader_for_source(la)))
            if lb and (not la or lb.id != la.id):
                res.append((lb.id, timeline_frame, self._get_reader_for_source(lb)))
            if not res:
                cur = viewable.get_current_layer(self.session_graph.sources)
                if cur:
                    res.append((cur.id, timeline_frame, self._get_reader_for_source(cur)))
            return res
        elif isinstance(viewable, LayoutView):
            res = []
            for sid in viewable.source_ids:
                src = self.session_graph.sources.get(sid)
                if src:
                    tile_f = viewable.resolve_tile_frame(src, timeline_frame, self.session_graph.sources)
                    res.append((src.id, tile_f, self._get_reader_for_source(src)))
            return res
        elif isinstance(viewable, SourceItem):
            return [(viewable.id, timeline_frame, self._get_reader_for_source(viewable))]
        elif self.reader is not None:
            cur_sid = getattr(self.cache, "_active_source_id", None)
            return [(cur_sid, timeline_frame, self.reader)]
        return []

    def _trigger_prefetch(self) -> None:
        """Trigger background prefetching using the active timeline resolver."""
        if not getattr(self, "_cache_started", False):
            return
        if self.cache.mode != CacheMode.OFF:
            if hasattr(self, "reader_engine") and self.reader_engine:
                if getattr(self.reader_engine, "is_paused", False):
                    self.reader_engine.resume_prefetch()
            self.reader_engine.request_prefetch(
                self.clock.start_frame,
                self.clock.end_frame,
                in_frame=self.clock.in_frame,
                out_frame=self.clock.out_frame,
                current_frame=self.clock.current_frame,
                resolver_fn=self._resolve_timeline_frame_sources,
            )

    def _schedule_delayed_prefetch(self, delay_ms: int = 1000) -> None:
        """Schedule background prefetch after a settling delay so dropped sources land smoothly."""
        if hasattr(self, "_delayed_cache_timer") and self.cache.mode != CacheMode.OFF:
            self._delayed_cache_timer.stop()
            self._delayed_cache_timer.start(delay_ms)

    def _cancel_delayed_prefetch_and_run(self) -> None:
        """Immediately cancel pending delayed cache timer and start prefetching directly."""
        if hasattr(self, "_delayed_cache_timer") and self._delayed_cache_timer.isActive():
            self._delayed_cache_timer.stop()
        if getattr(self, "_cache_started", False):
            self._trigger_prefetch()

    def _on_viewport_scrub(self, delta: int) -> None:
        """Handle viewport scrub drag: pause background prefetch and advance frame with full CPU priority."""
        self._is_scrubbing = True
        if hasattr(self, "reader_engine") and self.reader_engine:
            self.reader_engine.pause_prefetch()
        if hasattr(self, "_delayed_cache_timer"):
            self._delayed_cache_timer.stop()
        self.clock.step_forward(delta)
        if not self.clock.is_playing and hasattr(self, "audio_engine") and self.audio_engine:
            self.audio_engine.scrub_frame(self.clock.current_frame)
        if hasattr(self, "_scrub_settle_timer"):
            self._scrub_settle_timer.start(150)

    def _on_timeline_seek(self, frame: int) -> None:
        """Handle timeline seek click/drag: pause background prefetch and scrub audio/clock with full CPU priority."""
        self._is_scrubbing = True
        if hasattr(self, "reader_engine") and self.reader_engine:
            self.reader_engine.pause_prefetch()
        if hasattr(self, "_delayed_cache_timer"):
            self._delayed_cache_timer.stop()
        self.audio_engine.scrub_frame(frame)
        if hasattr(self, "_scrub_settle_timer"):
            self._scrub_settle_timer.start(150)

    def _on_scrub_settled(self, frame: Optional[int] = None) -> None:
        """Resume background prefetching and trigger full-resolution decode after interactive scrubbing ceases."""
        self._is_scrubbing = False
        if not self.clock.is_playing and hasattr(self, "audio_engine") and self.audio_engine:
            self.audio_engine.pause(self.clock.current_frame)
        if hasattr(self, "reader_engine") and self.reader_engine:
            self.reader_engine.resume_prefetch()

        # If current frame was rendered as a proxy or is a proxy in cache, request immediate full-res decode
        current_f = self.clock.current_frame
        is_proxy = False
        if hasattr(self, "cache") and self.cache:
            is_proxy = self.cache.is_frame_proxy(current_f)
        if not is_proxy and hasattr(self, "viewport") and getattr(self.viewport, "_is_proxy", False):
            is_proxy = True

        if is_proxy:
            viewable = self.session_graph.get_active_viewable() if hasattr(self, "session_graph") else None
            if isinstance(viewable, SequenceView):
                source, src_frame = viewable.resolve_frame(current_f, self.session_graph.sources)
                if source:
                    reader = self._get_reader_for_source(source)
                    if reader and hasattr(self, "reader_engine") and self.reader_engine:
                        self.reader_engine.request_immediate_frame(
                            source.id, src_frame, current_f, reader=reader, downsample=False, callback=self._on_immediate_frame_ready
                        )
            elif isinstance(viewable, StackView):
                layer_a = viewable.get_layer_a(self.session_graph.sources)
                if layer_a:
                    reader_a = self._get_reader_for_source(layer_a)
                    if reader_a and hasattr(self, "reader_engine") and self.reader_engine:
                        self.reader_engine.request_immediate_frame(
                            layer_a.id, current_f, current_f, reader=reader_a, downsample=False, callback=self._on_immediate_frame_ready
                        )
            else:
                cur_src_id = viewable.id if (viewable and hasattr(viewable, "id")) else ""
                rdr = self.reader
                if rdr and hasattr(self, "reader_engine") and self.reader_engine:
                    self.reader_engine.request_immediate_frame(
                        cur_src_id, current_f, current_f, reader=rdr, downsample=False, callback=self._on_immediate_frame_ready
                    )

        self._trigger_prefetch()

    def _schedule_cache_ui_update(self) -> None:
        """Coalesce rapid cache frame events to ~10 FPS so the main UI event loop is never flooded."""
        if hasattr(self, "_cache_ui_timer") and not self._cache_ui_timer.isActive():
            self._cache_ui_timer.start(100)

    def _on_frame_ready(self, frame: int) -> None:
        if frame == self.clock.current_frame:
            self._on_frame_changed(frame)

    def _on_playback_state_changed(self, is_playing: bool) -> None:
        """Synchronize audio playback state immediately and prefetch frames ahead when playback starts."""
        if hasattr(self, "viewport") and self.viewport:
            self.viewport.set_is_playing(is_playing)
        if hasattr(self, "audio_engine") and self.audio_engine:
            if is_playing:
                self.audio_engine.sync_to_frame(
                    self.clock.current_frame, True, self.clock.shuttle_speed
                )
            else:
                self.audio_engine.pause(self.clock.current_frame)
        if is_playing:
            self._last_prefetch_trigger_frame = self.clock.current_frame
            self._cancel_delayed_prefetch_and_run()
        else:
            # Upgrade resting frame to full resolution if currently showing a proxy
            current_f = self.clock.current_frame
            is_proxy = False
            if hasattr(self, "cache") and self.cache:
                is_proxy = self.cache.is_frame_proxy(current_f)
            if not is_proxy and hasattr(self, "viewport") and getattr(self.viewport, "_is_proxy", False):
                is_proxy = True

            if is_proxy:
                viewable = self.session_graph.get_active_viewable() if hasattr(self, "session_graph") else None
                if isinstance(viewable, SequenceView):
                    source, src_frame = viewable.resolve_frame(current_f, self.session_graph.sources)
                    if source:
                        reader = self._get_reader_for_source(source)
                        if reader and hasattr(self, "reader_engine") and self.reader_engine:
                            self.reader_engine.request_immediate_frame(
                                source.id, src_frame, current_f, reader=reader, downsample=False, callback=self._on_immediate_frame_ready
                            )
                elif isinstance(viewable, StackView):
                    layer_a = viewable.get_layer_a(self.session_graph.sources)
                    if layer_a:
                        reader_a = self._get_reader_for_source(layer_a)
                        if reader_a and hasattr(self, "reader_engine") and self.reader_engine:
                            self.reader_engine.request_immediate_frame(
                                layer_a.id, current_f, current_f, reader=reader_a, downsample=False, callback=self._on_immediate_frame_ready
                            )
                else:
                    cur_src_id = viewable.id if (viewable and hasattr(viewable, "id")) else ""
                    rdr = self.reader
                    if rdr and hasattr(self, "reader_engine") and self.reader_engine:
                        self.reader_engine.request_immediate_frame(
                            cur_src_id, current_f, current_f, reader=rdr, downsample=False, callback=self._on_immediate_frame_ready
                        )

    def _on_cache_updated(self) -> None:
        viewable = self.session_graph.get_active_viewable() if hasattr(self, "session_graph") else None
        cached = set()
        disk_cached = set()
        if isinstance(viewable, SourceItem):
            cached = self.cache.get_source_cached_frames(viewable.id)
            disk_cached = self.cache.get_source_disk_cached_frames(viewable.id)
        elif isinstance(viewable, SequenceView):
            clips = getattr(viewable, "clips", [])
            if clips:
                for clip in clips:
                    src_frames = self.cache.get_source_cached_frames(clip.source_id)
                    for sf in src_frames:
                        if clip.in_frame <= sf <= clip.out_frame:
                            cached.add(clip.timeline_in + (sf - clip.in_frame))
                    src_dframes = self.cache.get_source_disk_cached_frames(clip.source_id)
                    for sf in src_dframes:
                        if clip.in_frame <= sf <= clip.out_frame:
                            disk_cached.add(clip.timeline_in + (sf - clip.in_frame))
            else:
                for f in range(viewable.start_frame, viewable.end_frame + 1):
                    src, sf = viewable.resolve_frame(f, self.session_graph.sources)
                    if src:
                        if self.cache.has_source_frame_ram(src.id, sf):
                            cached.add(f)
                        elif self.cache.has_source_frame_disk(src.id, sf):
                            disk_cached.add(f)
        elif isinstance(viewable, StackView):
            la = viewable.get_layer_a(self.session_graph.sources)
            lb = viewable.get_layer_b(self.session_graph.sources)
            if la and lb and la.id != lb.id:
                ca = self.cache.get_source_cached_frames(la.id)
                cb = self.cache.get_source_cached_frames(lb.id)
                cached = ca.intersection(cb)
                cda = self.cache.get_source_disk_cached_frames(la.id)
                cdb = self.cache.get_source_disk_cached_frames(lb.id)
                disk_cached = cda.intersection(cdb)
            elif la:
                cached = self.cache.get_source_cached_frames(la.id)
                disk_cached = self.cache.get_source_disk_cached_frames(la.id)
            elif lb:
                cached = self.cache.get_source_cached_frames(lb.id)
                disk_cached = self.cache.get_source_disk_cached_frames(lb.id)
            else:
                cur = viewable.get_current_layer(self.session_graph.sources)
                if cur:
                    cached = self.cache.get_source_cached_frames(cur.id)
                    disk_cached = self.cache.get_source_disk_cached_frames(cur.id)
        elif isinstance(viewable, LayoutView):
            sids = [s for s in viewable.source_ids if s in self.session_graph.sources]
            if sids:
                cached = self.cache.get_source_cached_frames(sids[0]).copy()
                disk_cached = self.cache.get_source_disk_cached_frames(sids[0]).copy()
                for sid in sids[1:]:
                    cached.intersection_update(self.cache.get_source_cached_frames(sid))
                    disk_cached.intersection_update(self.cache.get_source_disk_cached_frames(sid))
        else:
            cached = self.cache.get_cached_frames()
            disk_cached = self.cache.get_disk_cached_frames()

        used_mb, max_mb, pct = self.cache.get_memory_stats()
        d_used_mb, d_max_mb, d_pct = self.cache.get_disk_memory_stats()
        self.timeline.update_cache_display(
            cached,
            pct,
            cache_mode=self.cache.mode.value,
            disk_cached_frames=disk_cached,
            disk_pct_used=d_pct,
        )
        disk_info = f" | Disk: {d_used_mb:.0f}MB" if d_used_mb > 0 else ""
        if self.cache.mode == CacheMode.OFF:
            if used_mb > 0 or d_used_mb > 0:
                self.cache_status_label.setText(f"Cache: Paused [{used_mb:.1f}MB]{disk_info} (Shift+C)")
                self.cache_status_label.setStyleSheet("color: #e67e22; font-family: monospace;")
            else:
                self.cache_status_label.setText("Cache: OFF")
                self.cache_status_label.setStyleSheet("color: #888888; font-family: monospace;")
        elif not getattr(self, "_cache_started", False):
            mode_tags = {
                CacheMode.PLAY_AHEAD: "Ahead",
                CacheMode.REGION: "Region",
                CacheMode.ALL: "Full",
            }
            tag = mode_tags.get(self.cache.mode, "RAM")
            self.cache_status_label.setText(f"Cache: Idle [{tag}]{disk_info} (Shift+C)")
            self.cache_status_label.setStyleSheet("color: #aaaaaa; font-family: monospace;")
        else:
            mode_tags = {
                CacheMode.PLAY_AHEAD: "Ahead",
                CacheMode.REGION: "Region",
                CacheMode.ALL: "Full",
            }
            tag = mode_tags.get(self.cache.mode, "RAM")
            self.cache_status_label.setText(f"Cache: {used_mb:.1f}MB / {max_mb:.0f}MB [{tag}]{disk_info}")
            self.cache_status_label.setStyleSheet("color: #2ecc71; font-family: monospace;")

    def _on_set_cache_mode(self, mode: CacheMode | str, start_prefetch: Optional[bool] = None) -> None:
        """Update caching operational mode (OFF, PLAY_AHEAD, REGION, ALL)."""
        if isinstance(mode, str):
            mode = CacheMode(mode)
        if mode != CacheMode.OFF:
            self._last_active_cache_mode = mode
        self.cache.set_mode(mode)
        if mode in self.cache_mode_actions:
            self.cache_mode_actions[mode].setChecked(True)
        if mode == CacheMode.OFF:
            self._cache_started = False
            if hasattr(self, "reader_engine") and self.reader_engine:
                self.reader_engine.pause_prefetch()
            if hasattr(self, "_delayed_cache_timer"):
                self._delayed_cache_timer.stop()
        else:
            if hasattr(self, "clock"):
                self.cache.set_region_bounds(self.clock.in_frame, self.clock.out_frame)
            if start_prefetch is not None:
                self._cache_started = bool(start_prefetch)
            else:
                self._cache_started = True

            if self._cache_started:
                if hasattr(self, "reader_engine") and self.reader_engine:
                    self.reader_engine.resume_prefetch()
                self._trigger_prefetch()
            else:
                if hasattr(self, "reader_engine") and self.reader_engine:
                    self.reader_engine.pause_prefetch()
                if hasattr(self, "_delayed_cache_timer"):
                    self._delayed_cache_timer.stop()

        self._on_cache_updated()
        mode_names = {
            CacheMode.OFF: "Off",
            CacheMode.PLAY_AHEAD: "Play-Ahead (Lookahead)",
            CacheMode.REGION: "In/Out Region Only",
            CacheMode.ALL: "Full Sequence",
        }
        self.statusBar().showMessage(f"Cache Mode: {mode_names.get(mode, str(mode))}", 3000)

    def _on_toggle_cache(self) -> None:
        """Toggle caching on/off (Shift+C), defaulting between OFF and In/Out Region according to preferences."""
        if not getattr(self, "_cache_started", False) or self.cache.mode == CacheMode.OFF:
            toggle_pref = getattr(self.pref_mgr.prefs, "cache_toggle_mode", "region")
            if toggle_pref == "last_active":
                target_mode = getattr(self, "_last_active_cache_mode", None) or CacheMode.REGION
            else:
                try:
                    target_mode = CacheMode(toggle_pref)
                except Exception:
                    target_mode = CacheMode.REGION
            self._on_set_cache_mode(target_mode, start_prefetch=True)
            mode_names = {
                CacheMode.PLAY_AHEAD: "Play-Ahead",
                CacheMode.REGION: "In/Out Region",
                CacheMode.ALL: "Full Sequence",
            }
            lbl = mode_names.get(target_mode, "Active")
            self.statusBar().showMessage(f"Caching Resumed [{lbl}]. Pre-cached data retained.", 3000)
        else:
            if self.cache.mode != CacheMode.OFF:
                self._last_active_cache_mode = self.cache.mode
            self._on_set_cache_mode(CacheMode.OFF)
            self.statusBar().showMessage("Caching Paused. Pre-cached data retained (Shift+C to resume).", 3000)

    def _on_cycle_cache_mode(self) -> None:
        """Cycle through cache modes when clicking the compact cache bar."""
        order = [CacheMode.OFF, CacheMode.PLAY_AHEAD, CacheMode.REGION, CacheMode.ALL]
        idx = order.index(self.cache.mode) if self.cache.mode in order else 0
        next_mode = order[(idx + 1) % len(order)]
        self._on_set_cache_mode(next_mode, start_prefetch=(next_mode != CacheMode.OFF))

    def _on_in_out_changed_for_cache(self, in_f: int, out_f: int) -> None:
        self.cache.set_region_bounds(in_f, out_f)
        if not getattr(self, "_suppress_in_out_record", False) and hasattr(self, "session_graph"):
            viewable = self.session_graph.get_active_viewable()
            if viewable is not None:
                if in_f == self.clock.start_frame and out_f == self.clock.end_frame:
                    viewable.manual_in = None
                    viewable.manual_out = None
                else:
                    viewable.manual_in = in_f
                    viewable.manual_out = out_f
        if self.cache.mode == CacheMode.REGION:
            self._trigger_prefetch()

    def _on_clear_cache(self) -> None:
        """Clear cached frames for the active source item or solo layer."""
        viewable = self.session_graph.get_active_viewable() if hasattr(self, "session_graph") else None
        if isinstance(viewable, SourceItem):
            self.cache.clear_source(viewable.id)
            self._on_cache_updated()
            self.statusBar().showMessage(f"RAM Cache cleared for '{viewable.name}'.", 3000)
        elif isinstance(viewable, StackView):
            cur = viewable.get_current_layer(self.session_graph.sources)
            if cur:
                self.cache.clear_source(cur.id)
                self._on_cache_updated()
                self.statusBar().showMessage(f"RAM Cache cleared for layer '{cur.name}'.", 3000)
            else:
                self.cache.clear()
                self._on_cache_updated()
                self.statusBar().showMessage("RAM Cache Cleared.", 3000)
        else:
            self.cache.clear()
            self._on_cache_updated()
            self.statusBar().showMessage("RAM Cache Cleared.", 3000)

        # Evict dropped QC media cache and reset progress bars
        if hasattr(self, "comp_qc_state") and self.comp_qc_state:
            if self.comp_qc_state.source_item_id and hasattr(self, "cache"):
                self.cache.clear_source(self.comp_qc_state.source_item_id)
            self.comp_qc_state.source_prefetch_progress = 0.0
            if self.comp_qc_state.matte_item_id and hasattr(self, "cache"):
                self.cache.clear_source(self.comp_qc_state.matte_item_id)
            self.comp_qc_state.matte_prefetch_progress = 0.0

    def _on_clear_all_cache(self) -> None:
        """Flush all cached RAM frames across all sources."""
        self.cache.clear()
        if hasattr(self, "comp_qc_state") and self.comp_qc_state:
            if self.comp_qc_state.source_item_id:
                self.cache.clear_source(self.comp_qc_state.source_item_id)
            self.comp_qc_state.source_prefetch_progress = 0.0
            if self.comp_qc_state.matte_item_id:
                self.cache.clear_source(self.comp_qc_state.matte_item_id)
            self.comp_qc_state.matte_prefetch_progress = 0.0
        self._on_cache_updated()
        self.statusBar().showMessage("All RAM Cache Cleared.", 3000)

    def _get_source_frame_path(self, source: SourceItem, frame: int) -> Optional[str]:
        """Resolve file path on disk for a specific source frame."""
        if not source:
            return None
        reader = self._get_reader_for_source(source)
        if hasattr(reader, "seq") and hasattr(reader.seq, "frame_path"):
            return reader.seq.frame_path(frame)
        elif hasattr(reader, "_frame_path"):
            return reader._frame_path(frame)
        elif hasattr(reader, "get_frame_path"):
            return reader.get_frame_path(frame)
        elif source.path and os.path.exists(source.path):
            return source.path
        return None

    def _on_reload_source_media(self, source_id: str) -> None:
        """Reload media: clear source cached frames and cache again if caching active."""
        source = self.session_graph.sources.get(source_id)
        if not source:
            return
        self.cache.clear_source(source_id)
        self._on_cache_updated()
        if self.cache.mode != CacheMode.OFF:
            self._trigger_prefetch()
        self.statusBar().showMessage(f"Reloaded media and flushed cache for '{source.name}'.", 3000)

    def _on_reload_source_update(self, source_id: str) -> None:
        """Reload update: clear and re-cache only frames different from cached frames (newer mtime)."""
        source = self.session_graph.sources.get(source_id)
        if not source:
            return

        cached_frames = self.cache.get_source_cached_frames(source_id)
        if not cached_frames:
            if self.cache.mode != CacheMode.OFF:
                self._trigger_prefetch()
            self.statusBar().showMessage(f"No cached frames to update for '{source.name}'.", 3000)
            return

        refreshed_count = 0
        for f in list(cached_frames):
            cached_mtime = self.cache.get_source_frame_mtime(source_id, f)
            frame_path = self._get_source_frame_path(source, f)
            if frame_path and os.path.exists(frame_path):
                current_mtime = os.path.getmtime(frame_path)
                if cached_mtime is not None and current_mtime > cached_mtime:
                    self.cache.clear_source_frame(source_id, f)
                    refreshed_count += 1
            else:
                self.cache.clear_source_frame(source_id, f)
                refreshed_count += 1

        self._on_cache_updated()
        if refreshed_count > 0 and self.cache.mode != CacheMode.OFF:
            self._trigger_prefetch()

        self.statusBar().showMessage(
            f"Reload update: refreshed {refreshed_count} modified frames for '{source.name}'.",
            3000,
        )

    def _refresh_budget_menu_labels(self) -> None:
        """Update the Auto RAM Budget menu action label with live system RAM availability."""
        if hasattr(self, "auto_budget_action"):
            live_mb = compute_dynamic_ram_budget_mb(self.cache._used_bytes)
            self.auto_budget_action.setText(
                f"Auto (Available System RAM: ~{live_mb/1024.0:.1f} GB) [Recommended]"
            )
            if self.cache.auto_memory_budget:
                self.cache.refresh_auto_budget()

    def _on_set_auto_cache_budget(self) -> None:
        """Enable dynamic available system RAM budget and refresh cache display."""
        max_mb = self.cache.set_auto_memory_budget(True)
        self._on_cache_updated()
        gb = max_mb / 1024.0
        self.statusBar().showMessage(
            f"RAM Cache Budget set to Auto (~{int(max_mb)} MB / {gb:.1f} GB Available)", 3000
        )

    def _on_set_cache_budget(self, mb: float) -> None:
        """Update maximum RAM cache budget in MB and refresh cache display."""
        self.cache.set_max_memory_mb(mb, auto=False)
        self._on_cache_updated()
        gb = mb / 1024.0
        self.statusBar().showMessage(f"RAM Cache Budget set to {int(mb)} MB ({gb:g} GB)", 3000)

    def _on_memory_changed(self, used_mb: float, max_mb: float) -> None:
        if self.cache.mode != CacheMode.OFF:
            mode_tags = {
                CacheMode.PLAY_AHEAD: "Ahead",
                CacheMode.REGION: "Region",
                CacheMode.ALL: "Full",
            }
            tag = mode_tags.get(self.cache.mode, "RAM")
            d_used, d_max, _ = self.cache.get_disk_memory_stats()
            disk_str = f" | Disk: {d_used:.0f}MB" if d_used > 0 else ""
            self.cache_status_label.setText(f"Cache: {used_mb:.1f}MB / {max_mb:.0f}MB [{tag}]{disk_str}")

    def _on_fps_measured(self, fps: float) -> None:
        self.fps_status_label.setText(f"{fps:.2f} FPS (Real-time)")

    def _on_exposure_changed(self, ev: float) -> None:
        self.viewport.exposure_ev = ev
        self.viewport.update()
        if self.sync_engine.is_connected and (self.sync_engine.is_presenter or self.sync_engine.is_host):
            if not getattr(self, "_suppress_sync_broadcast", False):
                self.sync_engine.broadcast_color(
                    exposure_ev=ev,
                    gamma=self.top_bar.gamma,
                    channel_mode=self.top_bar.channel_mode,
                    input_space=self.viewport.input_space,
                    view_transform=self.viewport.view_transform,
                )

    def _on_gamma_changed(self, g: float) -> None:
        self.viewport.gamma = g
        self.viewport.update()
        if self.sync_engine.is_connected and (self.sync_engine.is_presenter or self.sync_engine.is_host):
            if not getattr(self, "_suppress_sync_broadcast", False):
                self.sync_engine.broadcast_color(
                    exposure_ev=self.top_bar.exposure_ev,
                    gamma=g,
                    channel_mode=self.top_bar.channel_mode,
                    input_space=self.viewport.input_space,
                    view_transform=self.viewport.view_transform,
                )

    def _on_gain_changed(self, g: float) -> None:
        self.viewport.gain = g
        self.viewport.update()

    def _on_saturation_changed(self, s: float) -> None:
        self.viewport.saturation = s
        self.viewport.update()

    def _on_stack_cycle_forward(self) -> None:
        """Cycle to next layer in active StackView or next loaded source (Z)."""
        self._is_cycling_stack = True
        try:
            viewable = self.session_graph.get_active_viewable()
            if isinstance(viewable, StackView) and viewable.source_ids:
                viewable.compare_mode = "switch"
                idx = viewable.cycle_forward()
                comp_mode = ComparisonMode.OFF if (idx == viewable.index_b) else ComparisonMode.OVER
                self.comparison_state.mode = comp_mode
                self.viewport.comparison_state.mode = comp_mode
                src = viewable.get_current_layer(self.session_graph.sources)
                self.cache.set_active_source(src.id if src else None)
                src_name = src.name if src else "Layer"
                tag = "Layer A" if idx == viewable.index_a else ("Layer B" if idx == viewable.index_b else f"Layer {idx + 1}")
                self.statusBar().showMessage(f"Stack Switch: {tag} ({src_name})", 2500)
                self._on_frame_changed(self.clock.current_frame)
                self._on_cache_updated()
                if hasattr(self, "sources_dock"):
                    self.sources_dock.panel.refresh_tree()
                    self.sources_dock.panel._refresh_subpanels()
                self._refresh_active_source_details()
            elif hasattr(self, "comp_qc_state") and self.comp_qc_state and self.comp_qc_state.source_plate_path and self.comp_qc_state.source_item_id:
                qc_sid = self.comp_qc_state.source_item_id
                cur_id = viewable.id if (viewable and hasattr(viewable, "id")) else None
                if cur_id != qc_sid:
                    self._pre_qc_source_id = cur_id
                    self.session_graph.set_active_view(ViewType.SOURCE, qc_sid)
                    self.statusBar().showMessage("QC Stack Switch: Source Plate", 2000)
                else:
                    target_sid = getattr(self, "_pre_qc_source_id", None)
                    if not target_sid or target_sid not in self.session_graph.sources or target_sid == qc_sid:
                        for sid in self.session_graph.sources.keys():
                            if sid != qc_sid:
                                target_sid = sid
                                break
                    if target_sid:
                        self.session_graph.set_active_view(ViewType.SOURCE, target_sid)
                        src_name = self.session_graph.sources[target_sid].name if target_sid in self.session_graph.sources else "Media"
                        self.statusBar().showMessage(f"QC Stack Switch: {src_name}", 2000)
            elif hasattr(self, "session_graph") and len(self.session_graph.sources) > 1:
                sids = list(self.session_graph.sources.keys())
                cur_id = viewable.id if (viewable and hasattr(viewable, "id")) else sids[0]
                cur_idx = sids.index(cur_id) if cur_id in sids else 0
                next_sid = sids[(cur_idx + 1) % len(sids)]
                self.session_graph.set_active_view(ViewType.SOURCE, next_sid)
        finally:
            self._is_cycling_stack = False

    def _on_stack_cycle_backward(self) -> None:
        """Cycle to previous layer in active StackView or previous loaded source (Shift+Z)."""
        self._is_cycling_stack = True
        try:
            viewable = self.session_graph.get_active_viewable()
            if isinstance(viewable, StackView) and viewable.source_ids:
                viewable.compare_mode = "switch"
                idx = viewable.cycle_backward()
                comp_mode = ComparisonMode.OFF if (idx == viewable.index_b) else ComparisonMode.OVER
                self.comparison_state.mode = comp_mode
                self.viewport.comparison_state.mode = comp_mode
                src = viewable.get_current_layer(self.session_graph.sources)
                self.cache.set_active_source(src.id if src else None)
                src_name = src.name if src else "Layer"
                tag = "Layer A" if idx == viewable.index_a else ("Layer B" if idx == viewable.index_b else f"Layer {idx + 1}")
                self.statusBar().showMessage(f"Stack Switch: {tag} ({src_name})", 2500)
                self._on_frame_changed(self.clock.current_frame)
                self._on_cache_updated()
                if hasattr(self, "sources_dock"):
                    self.sources_dock.panel.refresh_tree()
                    self.sources_dock.panel._refresh_subpanels()
                self._refresh_active_source_details()
            elif hasattr(self, "comp_qc_state") and self.comp_qc_state and self.comp_qc_state.source_plate_path and self.comp_qc_state.source_item_id:
                qc_sid = self.comp_qc_state.source_item_id
                cur_id = viewable.id if (viewable and hasattr(viewable, "id")) else None
                if cur_id != qc_sid:
                    self._pre_qc_source_id = cur_id
                    self.session_graph.set_active_view(ViewType.SOURCE, qc_sid)
                    self.statusBar().showMessage("QC Stack Switch: Source Plate", 2000)
                else:
                    target_sid = getattr(self, "_pre_qc_source_id", None)
                    if not target_sid or target_sid not in self.session_graph.sources or target_sid == qc_sid:
                        for sid in self.session_graph.sources.keys():
                            if sid != qc_sid:
                                target_sid = sid
                                break
                    if target_sid:
                        self.session_graph.set_active_view(ViewType.SOURCE, target_sid)
                        src_name = self.session_graph.sources[target_sid].name if target_sid in self.session_graph.sources else "Media"
                        self.statusBar().showMessage(f"QC Stack Switch: {src_name}", 2000)
            elif hasattr(self, "session_graph") and len(self.session_graph.sources) > 1:
                sids = list(self.session_graph.sources.keys())
                cur_id = viewable.id if (viewable and hasattr(viewable, "id")) else sids[0]
                cur_idx = sids.index(cur_id) if cur_id in sids else 0
                prev_sid = sids[(cur_idx - 1) % len(sids)]
                self.session_graph.set_active_view(ViewType.SOURCE, prev_sid)
        finally:
            self._is_cycling_stack = False

    def _on_channel_changed(self, mode: int) -> None:
        self.viewport.channel_mode = mode
        self.viewport.update()

    def _on_layer_changed(self, layer_name: str) -> None:
        """Handle switching of multi-channel EXR layers."""
        self._current_exr_layer = layer_name
        if hasattr(self, "top_bar") and hasattr(self.top_bar, "set_current_layer"):
            self.top_bar.set_current_layer(layer_name)
        if hasattr(self, "layers_widget"):
            self.layers_widget.set_current_layer(layer_name)
        if hasattr(self, "reader") and self.reader and hasattr(self.reader, "set_layer"):
            self.reader.set_layer(layer_name)
        if hasattr(self, "_source_readers"):
            for r in self._source_readers.values():
                if hasattr(r, "set_layer"):
                    r.set_layer(layer_name)
        # Invalidate frame cache so new layer is immediately loaded & rendered
        self.cache.clear()
        self._on_frame_changed(self.clock.current_frame)
        self.statusBar().showMessage(f"Active EXR Layer: {layer_name}", 3000)

    def _get_active_exr_frame_path(self, viewable: Any, frame: int) -> Optional[str]:
        """Resolve physical EXR file path for the given frame and active viewable."""
        if hasattr(self, "reader") and self.reader:
            if hasattr(self.reader, "_frame_path"):
                return self.reader._frame_path(frame)
            elif hasattr(self.reader, "seq") and hasattr(self.reader.seq, "frame_path"):
                return self.reader.seq.frame_path(frame)
        if isinstance(viewable, SourceItem):
            return getattr(viewable, "path", getattr(viewable, "file_path", None))
        if getattr(self, "_current_media_path", None):
            return self._current_media_path
        return None

    def _is_current_media_exr(self, viewable: Any, frame: int) -> bool:
        p = self._get_active_exr_frame_path(viewable, frame)
        return bool(p and str(p).lower().endswith(".exr"))

    def _composite_current_exr_contact_sheet(
        self, viewable: Any, frame: int
    ) -> Tuple[np.ndarray, Dict[str, Tuple[int, int, int, int]]]:
        """Read all available layers of active EXR frame and composite into adaptive contact sheet grid."""
        f_path = self._get_active_exr_frame_path(viewable, frame)
        if not f_path or not os.path.exists(f_path):
            empty = np.zeros((1080, 1920, 4), dtype=np.float32)
            empty[:, :, 3] = 1.0
            return empty, {}

        from openreview.core.image_io import read_image_file, get_exr_channels
        from openreview.core.exr_contact_sheet import build_exr_contact_sheet

        lyrs = getattr(self.layers_widget, "_layers", [])
        if not lyrs or lyrs == ["beauty"]:
            info = get_exr_channels(f_path)
            lyrs = list(info.get("layers", {}).keys())
            for cl in info.get("cryptomatte_layers", []):
                if cl not in lyrs:
                    lyrs.append(cl)
            if not lyrs:
                lyrs = ["beauty"]

        layer_frames = {}
        for lyr in lyrs:
            arr = read_image_file(f_path, layer=lyr)
            if arr is not None:
                layer_frames[lyr] = arr

        base_w = getattr(self.reader, "width", 1920) or 1920
        base_h = getattr(self.reader, "height", 1080) or 1080

        return build_exr_contact_sheet(
            layer_frames,
            base_w=base_w,
            base_h=base_h,
            auto_depth=getattr(self, "_auto_depth_enabled", True),
            draw_labels=True,
        )

    def _on_toggle_exr_contact_sheet(self, enabled: bool) -> None:
        """Toggle adaptive multi-channel EXR Contact Sheet mode."""
        self._is_exr_contact_sheet_active = enabled
        if hasattr(self, "layers_widget"):
            self.layers_widget.set_contact_sheet_active(enabled)
        # Clear cache and trigger immediate frame rendering
        self.cache.clear()
        self._on_frame_changed(self.clock.current_frame)
        self.statusBar().showMessage(
            f"EXR Contact Sheet: {'Active' if enabled else 'Disabled'}",
            2500,
        )

    def _on_view_single_exr_pass(self) -> None:
        """Exit contact sheet mode and revert back to beauty or primary first pass."""
        self._is_exr_contact_sheet_active = False
        target_pass = "beauty"
        if hasattr(self, "layers_widget") and self.layers_widget._layers:
            if "beauty" in self.layers_widget._layers:
                target_pass = "beauty"
            else:
                target_pass = self.layers_widget._layers[0]

        if hasattr(self, "layers_widget"):
            self.layers_widget.set_contact_sheet_active(False)
            self.layers_widget.set_current_layer(target_pass)
        if hasattr(self, "top_bar") and hasattr(self.top_bar, "set_current_layer"):
            self.top_bar.set_current_layer(target_pass)

        self._on_layer_changed(target_pass)
        self.statusBar().showMessage(f"Single Pass View: {target_pass}", 2500)

    def _on_exr_contact_sheet_tile_double_clicked(self, layer_name: str) -> None:
        """Handle canvas double click on a contact sheet tile: solo that specific pass."""
        self._is_exr_contact_sheet_active = False
        if hasattr(self, "layers_widget"):
            self.layers_widget.set_contact_sheet_active(False)
            self.layers_widget.set_current_layer(layer_name)
        if hasattr(self, "top_bar") and hasattr(self.top_bar, "set_current_layer"):
            self.top_bar.set_current_layer(layer_name)

        self._on_layer_changed(layer_name)
        if hasattr(self, "viewport") and hasattr(self.viewport, "show_toast"):
            self.viewport.show_toast(f"Solo Pass: {layer_name}")
        self.statusBar().showMessage(f"Soloed Pass from Contact Sheet: {layer_name}", 3000)

    def _on_toggle_auto_depth(self, enabled: bool) -> None:
        """Toggle auto-range float normalization on Z/Depth passes."""
        self._auto_depth_enabled = enabled
        self.cache.clear()
        self._on_frame_changed(self.clock.current_frame)
        self.statusBar().showMessage(f"Auto-Depth Normalization: {'On' if enabled else 'Off'}", 2000)

    def _on_show_active_exr_metadata(self) -> None:
        """Open EXR Header Metadata Dialog for current media."""
        path = getattr(self, "_current_media_path", None)
        if not path and hasattr(self, "reader") and self.reader:
            if hasattr(self.reader, "_frame_path"):
                path = self.reader._frame_path(self.clock.current_frame)
            elif hasattr(self.reader, "seq") and hasattr(self.reader.seq, "frame_path"):
                path = self.reader.seq.frame_path(self.clock.current_frame)
        if path:
            self._on_show_exr_metadata_path(path)
        else:
            QMessageBox.information(self, "No Active EXR", "No active OpenEXR media file loaded.")

    def _on_show_exr_metadata_path(self, path: str) -> None:
        """Open EXR Header Metadata Dialog for specific file path."""
        from openreview.ui.exr_metadata_dialog import EXRMetadataDialog
        dlg = EXRMetadataDialog(path, self)
        dlg.exec()

    def _on_show_exr_inspector(self) -> None:
        """Reveal and focus EXR Multilayer Pass Inspector floating HUD."""
        if hasattr(self, "hud_layers"):
            self.hud_layers.show()
            self.hud_layers.raise_()

    def _on_export_exr_contact_sheet(self) -> None:
        """Pre-configure and trigger Export Dialog specifically for EXR Contact Sheet."""
        if not self._is_exr_contact_sheet_active:
            self._on_toggle_exr_contact_sheet(True)
        self._on_export_media()

    def _on_stereo_changed(self, mode: int) -> None:
        """Handle Stereo 3D mode switching."""
        self.viewport.stereo_mode = mode
        if mode != 0 and getattr(self, "_current_stereo_pair", None):
            pair = self._current_stereo_pair
            from openreview.core.image_io import read_image_file
            # Read right eye frame
            r_data = read_image_file(pair.right_path)
            self.viewport.set_reference_frame(r_data)
        elif mode == 0:
            if self.viewport.comparison_state.mode == ComparisonMode.OFF:
                self.viewport.set_reference_frame(None)
        self.viewport.update()

    def _toggle_hud_hist(self) -> None:
        """Toggle Live RGB Histogram HUD card with instant frame update."""
        vis = not self.hud_hist.isVisible()
        self.hud_hist.setVisible(vis)
        if vis and hasattr(self, "viewport") and getattr(self.viewport, "_image_a", None) is not None:
            self.hist_widget.update_frame(self.viewport._image_a)

    def _toggle_hud_waveform(self) -> None:
        """Toggle Waveform Monitor HUD card with instant frame update."""
        vis = not self.hud_waveform.isVisible()
        self.hud_waveform.setVisible(vis)
        if vis and hasattr(self, "viewport") and getattr(self.viewport, "_image_a", None) is not None:
            self.waveform_widget.update_frame(self.viewport._image_a)

    def _toggle_hud_vectorscope(self) -> None:
        """Toggle Vectorscope Monitor HUD card with instant frame update."""
        vis = not self.hud_vectorscope.isVisible()
        self.hud_vectorscope.setVisible(vis)
        if vis and hasattr(self, "viewport") and getattr(self.viewport, "_image_a", None) is not None:
            self.vectorscope_widget.update_frame(self.viewport._image_a)

    def _update_hud_scopes(self, data: Optional[np.ndarray]) -> None:
        """Update live scopes HUDs (Histogram, Waveform, Vectorscope) if visible."""
        if hasattr(self, "hud_hist") and self.hud_hist.isVisible():
            self.hist_widget.update_frame(data)
        if hasattr(self, "hud_waveform") and self.hud_waveform.isVisible():
            self.waveform_widget.update_frame(data)
        if hasattr(self, "hud_vectorscope") and self.hud_vectorscope.isVisible():
            self.vectorscope_widget.update_frame(data)

    def _on_color_adj_changed(self, settings: ColorGradeSettings) -> None:
        """Handle adjustments from full Color Adjustments dialog/HUD."""
        self.viewport.set_color_grading(settings)

    def _on_toggle_wipe(self) -> None:
        """Triggered by shortcut 'W'."""
        viewable = self.session_graph.get_active_viewable() if hasattr(self, "session_graph") else None
        if isinstance(viewable, StackView):
            if viewable.compare_mode == "wipe":
                prev = getattr(viewable, "_prev_compare_mode", "switch")
                viewable.compare_mode = prev if prev != "wipe" else "switch"
            else:
                viewable._prev_compare_mode = viewable.compare_mode
                viewable.compare_mode = "wipe"

            if hasattr(self, "sources_dock"):
                self.sources_dock.panel._refresh_subpanels()
            self._on_sources_view_updated()
            self.statusBar().showMessage(f"Stack Comparison Mode: {viewable.compare_mode.upper()}", 2500)
            return

        self.wipe_widget.toggle_wipe()

    def _on_flip_wipe_orientation(self) -> None:
        """Triggered by shortcut 'Shift+W'."""
        self.wipe_widget.flip_orientation()

    def _on_reset_wipe(self) -> None:
        """Triggered by shortcut 'Alt+W'."""
        self.wipe_widget.reset_wipe()

    def _on_comparison_changed(self, state: ComparisonState) -> None:
        self.comparison_state = state
        self.viewport.comparison_state = state
        self._on_frame_changed(self.clock.current_frame)
        self.viewport.update()

    def _on_clear_annotations(self) -> None:
        self.annotation_store.clear_frame(self.clock.current_frame)
        self.viewport.update()
        if self.sync_engine.is_connected and not getattr(self, "_suppress_sync_broadcast", False):
            self.sync_engine.broadcast_clear_annotations(self.clock.current_frame)

    def _on_next_keyframe(self) -> None:
        nxt = self.annotation_store.get_next_annotated_frame(self.clock.current_frame)
        if nxt is not None:
            self.clock.set_frame(nxt)

    def _on_prev_keyframe(self) -> None:
        prv = self.annotation_store.get_prev_annotated_frame(self.clock.current_frame)
        if prv is not None:
            self.clock.set_frame(prv)

    def _on_toggle_annotation_tools(self) -> None:
        """Toggle top floating annotation toolbox visibility (X)."""
        is_vis = self.markup_palette.isVisible()
        self.markup_palette.setVisible(not is_vis)
        if not is_vis:
            self.markup_palette.auto_center_in_parent()
            self.markup_palette.raise_()
        if hasattr(self, "act_annotation_toolbox"):
            self.act_annotation_toolbox.blockSignals(True)
            self.act_annotation_toolbox.setChecked(not is_vis)
            self.act_annotation_toolbox.blockSignals(False)
        state_str = "Hidden" if is_vis else "Visible"
        self.statusBar().showMessage(f"Annotation Toolbox: {state_str} (X)", 2500)

    def _on_open_annotation_manager(self) -> None:
        """Toggle the in-viewport Annotation & Notes Manager panel (Ctrl+Shift+A)."""
        vis = self.hud_annotations.isHidden()
        self.hud_annotations.setVisible(vis)
        if vis:
            self.annotation_manager_widget.refresh_table()
            self.hud_annotations.raise_()
        self.statusBar().showMessage(f"Annotation Manager Panel: {'Shown' if vis else 'Hidden'}", 2000)

    def _on_clear_all_annotations(self) -> None:
        """Prompt confirmation and clear all annotations across all frames."""
        reply = QMessageBox.question(
            self,
            "Clear All Annotations",
            "Are you sure you want to permanently clear all annotations and review notes across all frames?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self.annotation_store.clear_all()
            self.viewport.update()
            if self.sync_engine.is_connected and not getattr(self, "_suppress_sync_broadcast", False):
                self.sync_engine.broadcast_clear_annotations(-1)
            self.statusBar().showMessage("Cleared all annotations across sequence", 3000)

    def _on_mark_annotated_frames(self) -> None:
        """Add all frames with annotations to bookmarks."""
        annotated = self.annotation_store.get_annotated_frames()
        if not annotated:
            self.statusBar().showMessage("No annotated frames to mark", 2500)
            return
        for f in annotated:
            self.clock.add_bookmark(f)
        self.statusBar().showMessage(f"Marked {len(annotated)} annotated frame(s)", 3000)

    def _on_export_annotations(self) -> None:
        """Open Annotation Manager export workflow."""
        dlg = AnnotationManagerDialog(
            annotation_store=self.annotation_store,
            clock=self.clock,
            render_frame_callback=self._render_frame_for_export,
            parent=self,
        )
        dlg._on_export_clicked()

    def _on_import_annotations(self) -> None:
        """Open Annotation Manager import workflow."""
        dlg = AnnotationManagerDialog(
            annotation_store=self.annotation_store,
            clock=self.clock,
            render_frame_callback=self._render_frame_for_export,
            parent=self,
        )
        dlg._on_import_clicked()

    def _composite_comparison_pair(self, da: np.ndarray, db: np.ndarray) -> np.ndarray:
        """Composite two frame arrays (Layer A and Layer B) matching the active comparison mode."""
        if da is None:
            return db if db is not None else np.zeros((1080, 1920, 3), dtype=np.uint8)
        if db is None:
            return da

        try:
            ha, wa = da.shape[:2]
            hb, wb = db.shape[:2]
            if (ha, wa) != (hb, wb):
                from openreview.core.exporter import MediaExporter
                db = MediaExporter.resize_frame(db, wa, ha)

            mode = getattr(self.viewport.comparison_state, "mode", ComparisonMode.OFF)
            if mode == ComparisonMode.OFF:
                return da

            a_f = da.astype(np.float32) / (255.0 if da.dtype == np.uint8 else 1.0)
            b_f = db.astype(np.float32) / (255.0 if db.dtype == np.uint8 else 1.0)

            if a_f.ndim == 2:
                a_f = np.repeat(a_f[:, :, np.newaxis], 3, axis=2)
            if b_f.ndim == 2:
                b_f = np.repeat(b_f[:, :, np.newaxis], 3, axis=2)

            if a_f.shape[2] == 3:
                a_f = np.concatenate([a_f, np.ones((ha, wa, 1), dtype=np.float32)], axis=2)
            if b_f.shape[2] == 3:
                b_f = np.concatenate([b_f, np.ones((ha, wa, 1), dtype=np.float32)], axis=2)

            comp_res = None
            if mode == ComparisonMode.DIFFERENCE:
                diff_boost = float(getattr(self.viewport.comparison_state, "diff_boost", 1.0))
                comp_res = np.abs(a_f[:, :, :3] - b_f[:, :, :3]) * diff_boost
            elif mode == ComparisonMode.DIFFERENCE_INVERTED:
                diff_boost = float(getattr(self.viewport.comparison_state, "diff_boost", 1.0))
                comp_res = 1.0 - (np.abs(a_f[:, :, :3] - b_f[:, :, :3]) * diff_boost)
            elif mode == ComparisonMode.ADD:
                comp_res = a_f[:, :, :3] + b_f[:, :, :3]
            elif mode == ComparisonMode.OVER:
                stack_mix = float(getattr(self.viewport.comparison_state, "stack_mix", 1.0))
                alpha = a_f[:, :, 3:4] * stack_mix
                comp_res = a_f[:, :, :3] * alpha + b_f[:, :, :3] * (1.0 - alpha)
            elif mode in (ComparisonMode.WIPE, ComparisonMode.SPLIT):
                wipe_pos = float(getattr(self.viewport.comparison_state, "wipe_position", 0.5))
                wipe_angle = float(getattr(self.viewport.comparison_state, "wipe_angle_deg", 0.0))
                wc = getattr(self.viewport.comparison_state, "wipe_center", (0.5, 0.5))
                y_coords, x_coords = np.mgrid[0:ha, 0:wa]
                norm_x = (x_coords.astype(np.float32) + 0.5) / float(wa)
                norm_y = (y_coords.astype(np.float32) + 0.5) / float(ha)
                rad = math.radians(wipe_angle)
                nx = math.cos(rad)
                ny = math.sin(rad)
                dist = (norm_x - wc[0]) * nx + (norm_y - wc[1]) * ny
                mask = (dist <= (wipe_pos - 0.5))[:, :, np.newaxis]
                comp_res = np.where(mask, a_f[:, :, :3], b_f[:, :, :3])
            else:
                comp_res = a_f[:, :, :3]

            comp_res = np.clip(comp_res, 0.0, 1.0)
            if da.dtype == np.uint8:
                return (comp_res * 255.0).astype(np.uint8)
            return comp_res.astype(da.dtype)
        except Exception:
            return da

    def _resolve_frame_from_tuple(self, raw_tuple: tuple | list) -> np.ndarray:
        """Resolve a tuple of frame buffers into a single 2D/3D numpy array."""
        arrays = [x for x in raw_tuple if isinstance(x, np.ndarray)]
        if not arrays:
            return np.zeros((1080, 1920, 3), dtype=np.uint8)
        if len(arrays) == 1:
            return arrays[0]
        return self._composite_comparison_pair(arrays[0], arrays[1])

    def _render_frame_for_export(self, frame_num: int) -> np.ndarray:
        """Render a specific frame with annotations at native source frame resolution."""
        raw = None
        if getattr(self, "_is_exr_contact_sheet_active", False):
            viewable = self.session_graph.get_active_viewable() if hasattr(self, "session_graph") else None
            if self._is_current_media_exr(viewable, frame_num):
                raw, _ = self._composite_current_exr_contact_sheet(viewable, frame_num)

        if raw is None and hasattr(self, "session_graph"):
            viewable = self.session_graph.get_active_viewable()
            if isinstance(viewable, SourceItem):
                raw = self.cache.get_source_frame(viewable.id, frame_num)
                if raw is None and self.reader:
                    raw = self.reader.read_frame(frame_num)
            elif isinstance(viewable, SequenceView):
                src, sf = viewable.resolve_frame(frame_num, self.session_graph.sources)
                if src:
                    raw = self.cache.get_source_frame(src.id, sf)
                    if raw is None:
                        r = self._get_reader_for_source(src)
                        raw = r.read_frame(sf) if r else None
            elif isinstance(viewable, StackView):
                is_switch_layer_b = (viewable.compare_mode == "switch" and viewable.current_layer_index == viewable.index_b)
                if is_switch_layer_b:
                    layer_b = viewable.get_layer_b(self.session_graph.sources)
                    if layer_b:
                        raw = self.cache.get_source_frame(layer_b.id, frame_num)
                        if raw is None:
                            rb = self._get_reader_for_source(layer_b)
                            raw = rb.read_frame(frame_num) if rb else None
                else:
                    layer_a = viewable.get_layer_a(self.session_graph.sources)
                    layer_b = viewable.get_layer_b(self.session_graph.sources)
                    da = None
                    db = None
                    if layer_a:
                        da = self.cache.get_source_frame(layer_a.id, frame_num)
                        if da is None:
                            ra = self._get_reader_for_source(layer_a)
                            da = ra.read_frame(frame_num) if ra else None
                    if layer_b:
                        db = self.cache.get_source_frame(layer_b.id, frame_num)
                        if db is None:
                            rb = self._get_reader_for_source(layer_b)
                            db = rb.read_frame(frame_num) if rb else None
                    if da is not None and db is not None and self.viewport.comparison_state.mode != ComparisonMode.OFF:
                        raw = self._composite_comparison_pair(da, db)
                    else:
                        raw = da if da is not None else db
            elif isinstance(viewable, LayoutView):
                def _fetch_tile(src: SourceItem):
                    sf = viewable.resolve_tile_frame(src, frame_num, self.session_graph.sources)
                    d = self.cache.get_source_frame(src.id, sf)
                    if d is None:
                        r = self._get_reader_for_source(src)
                        d = r.read_frame(sf) if r else None
                    return d
                raw = composite_layout_grid(viewable, self.session_graph.sources, _fetch_tile)

        if raw is None and self.reader:
            raw = self.reader.read_frame(frame_num)
        if raw is None:
            if self.viewport._image_a is not None:
                raw = self.viewport._image_a
            else:
                raw = np.zeros((1080, 1920, 3), dtype=np.uint8)

        if isinstance(raw, (tuple, list)):
            raw = self._resolve_frame_from_tuple(raw)

        from openreview.core.exporter import MediaExporter
        base_rgb = MediaExporter.apply_screenspace_color_transforms(raw, self.viewport)
        strokes = self.annotation_store.get_strokes(frame_num)
        return MediaExporter.render_strokes_on_image(base_rgb, strokes)

    def _on_scrub_mode_toggled(self, active: bool) -> None:
        """Toggle canvas left-mouse scrubbing across frames."""
        self.viewport.set_scrub_mode(active)
        self.pref_mgr.prefs.canvas_scrub_enabled = active
        self.pref_mgr.save()
        self.statusBar().showMessage(f"Canvas Scrub Mode: {'Enabled' if active else 'Disabled'}", 2500)

    def _on_audio_clock_sync(self, frame: int) -> None:
        """Synchronize audio engine playback position with clock frame."""
        self.audio_engine.sync_to_frame(frame, self.clock.is_playing, self.clock.shuttle_speed)

    def _on_toggle_audio_mute(self) -> None:
        """Toggle audio mute state (Ctrl+M)."""
        muted = self.audio_engine.toggle_mute()
        state = "Muted" if muted else "Unmuted"
        self.statusBar().showMessage(f"Audio: {state}", 2500)

    def _on_open_audio_settings(self) -> None:
        """Toggle the in-viewport Audio Settings & Offsets panel."""
        vis = self.hud_audio.isHidden()
        self.hud_audio.setVisible(vis)
        if vis:
            self.hud_audio.raise_()
        self.statusBar().showMessage(f"Audio Settings Panel: {'Shown' if vis else 'Hidden'}", 2000)

    def _on_toggle_comp_qc(self) -> None:
        """Toggle the xtra QC Utils right dock panel (Q)."""
        if hasattr(self, "pkg_mgr") and self.pkg_mgr and not self.pkg_mgr.is_plugin_enabled("xtra-qc-Utils"):
            return
        target = getattr(self, "comp_qc_dock", None) or getattr(self, "hud_comp_qc", None)
        if target is not None:
            vis = target.isHidden()
            target.setVisible(vis)
            if vis:
                if (hasattr(self, "file_browser_dock") and
                    self.dockWidgetArea(self.file_browser_dock) == self.dockWidgetArea(target) and
                    not self.file_browser_dock.isFloating() and not target.isFloating()):
                    self.tabifyDockWidget(self.file_browser_dock, target)
                target.raise_()
                if hasattr(target, "set_latched"):
                    target.set_latched(True)
            self.statusBar().showMessage(f"xtra QC Utils: {'Shown' if vis else 'Hidden'}", 2000)

    def _frame_to_thumbnail_pixmap(self, frame: np.ndarray) -> Optional[QPixmap]:
        """Convert a decoded frame ndarray into an aspect-ratio fitted QPixmap thumbnail."""
        if frame is None or not isinstance(frame, np.ndarray) or frame.size == 0:
            return None
        try:
            from PySide6.QtGui import QImage, QPixmap
            arr = frame
            if arr.dtype != np.uint8:
                arr = np.clip(arr * 255.0, 0, 255).astype(np.uint8)
            arr = np.ascontiguousarray(arr)
            h, w = arr.shape[:2]
            ch = arr.shape[2] if arr.ndim > 2 else 1
            if ch == 4:
                fmt = QImage.Format.Format_RGBA8888
            elif ch == 3:
                fmt = QImage.Format.Format_RGB888
            else:
                fmt = QImage.Format.Format_Grayscale8
            qimg = QImage(arr.data, w, h, arr.strides[0], fmt).copy()
            return QPixmap.fromImage(qimg)
        except Exception as e:
            logger.debug("Failed creating thumbnail pixmap: %s", e)
            return None

    def _resolve_qc_frame(self, reader, timeline_frame: int) -> int:
        """Map active timeline frame into appropriate sequence frame for QC reader."""
        if reader is None:
            return timeline_frame
        seq = getattr(reader, "seq", None)
        if seq is not None and getattr(seq, "available_frames", None):
            if timeline_frame in seq.available_frames:
                return timeline_frame
            clock_start = getattr(self.clock, "start_frame", 1) if hasattr(self, "clock") else 1
            rel_offset = timeline_frame - clock_start
            target_f = seq.start_frame + rel_offset
            if target_f in seq.available_frames:
                return target_f
            return max(seq.start_frame, min(seq.end_frame, target_f))
        return timeline_frame

    def _on_comp_qc_source_dropped(self, file_path: str) -> None:
        """Handle source reference plate dropped onto xtra QC Utils."""
        try:
            if file_path:
                from openreview.core.sequence_parser import detect_image_sequence, DiskSequenceReader
                seq = detect_image_sequence(file_path)
                display_name = Path(file_path).name
                if seq and len(seq.available_frames) > 1:
                    reader = DiskSequenceReader(seq)
                    hashes = "#" * seq.padding
                    display_name = f"{seq.prefix}{seq.separator}{hashes}{seq.extension} [{seq.start_frame}-{seq.end_frame}]"
                else:
                    reader = ReaderEngine.create_reader(file_path)

                if reader:
                    self._reader_b = reader
                    if hasattr(self, "comparison_state"):
                        self.comparison_state.source_b_path = file_path
                    if hasattr(self, "comp_qc_state"):
                        self.comp_qc_state.source_plate_path = file_path

                    # File into 'QC References' folder in Source Bin without switching active view
                    if hasattr(self, "session_graph") and self.session_graph:
                        prev_active_id = self.session_graph.active_view_id
                        prev_view_type = self.session_graph.active_view_type or ViewType.SOURCE
                        folder_id = None
                        for fid, f in self.session_graph.folders.items():
                            if f.name == "QC References":
                                folder_id = fid
                                break
                        if not folder_id:
                            f = self.session_graph.create_folder(name="QC References", folder_type="source")
                            folder_id = f.id
                        source_item = self.session_graph.add_source_from_path(file_path, parent_folder_id=folder_id)
                        if prev_active_id is not None and self.session_graph.active_view_id != prev_active_id:
                            self.session_graph.set_active_view(prev_view_type, prev_active_id)
                        if source_item and hasattr(self, "comp_qc_state"):
                            self.comp_qc_state.source_item_id = source_item.id
                        if hasattr(self, "sources_dock") and self.sources_dock:
                            self.sources_dock.panel.refresh_tree()

                    cur_f = getattr(self.clock, "current_frame", 1) if hasattr(self, "clock") else 1
                    mapped_f = self._resolve_qc_frame(reader, cur_f)
                    b_frame = reader.read_frame(mapped_f)
                    if b_frame is not None and hasattr(self, "viewport"):
                        self.viewport.set_reference_frame(b_frame)

                    # Update thumbnail in drop placeholder
                    dock = getattr(self, "comp_qc_dock", None)
                    panel = getattr(dock, "panel", dock) if dock else getattr(self, "hud_comp_qc", None)
                    if panel:
                        if hasattr(panel, "drop_source"):
                            pix = self._frame_to_thumbnail_pixmap(b_frame)
                            panel.drop_source.set_thumbnail(pix, display_name)
                        if hasattr(panel, "sec_source_header") and hasattr(panel.sec_source_header, "btn_toggle"):
                            panel.sec_source_header.btn_toggle.setText(f"Source Plate: {display_name}")

                    self.statusBar().showMessage(f"xtra QC: Loaded reference plate: {display_name}", 3000)
                else:
                    self.statusBar().showMessage(f"xtra QC: Unable to decode plate: {Path(file_path).name}", 3000)
        except Exception as e:
            logger.warning("Failed loading source plate: %s", e)
            self.statusBar().showMessage(f"xtra QC: Error loading source plate: {e}", 3000)

    def _on_comp_qc_prefetch_source(self) -> None:
        """Prefetch frames for the loaded source plate into RAM cache."""
        plate_path = getattr(self.comp_qc_state, "source_plate_path", None)
        if not plate_path or not self._reader_b:
            self.statusBar().showMessage("xtra QC: No source plate loaded to prefetch.", 2500)
            return

        src_id = getattr(self.comp_qc_state, "source_item_id", None) or "qc_source_plate"
        start_f = getattr(self.clock, "start_frame", 1) if hasattr(self, "clock") else 1
        end_f = getattr(self.clock, "end_frame", 100) if hasattr(self, "clock") else 100
        if hasattr(self._reader_b, "seq") and self._reader_b.seq:
            start_f = self._reader_b.seq.start_frame
            end_f = self._reader_b.seq.end_frame
        elif hasattr(self, "session_graph") and self.session_graph and src_id in self.session_graph.sources:
            s_item = self.session_graph.sources[src_id]
            start_f = s_item.start_frame
            end_f = s_item.end_frame

        if hasattr(self, "comp_qc_state"):
            self.comp_qc_state.source_prefetch_progress = 0.0

        if hasattr(self, "reader_engine") and self.reader_engine:
            self.reader_engine.queue_source_background_cache(
                src_id,
                self._reader_b,
                start_f,
                end_f,
            )
            self.statusBar().showMessage(f"xtra QC: Prefetching source plate ({start_f}-{end_f})...", 2500)
        else:
            if hasattr(self, "comp_qc_state"):
                self.comp_qc_state.source_prefetch_progress = 1.0
            self.statusBar().showMessage("xtra QC: Source plate cached 100%.", 2500)

    def _on_comp_qc_unload_source(self) -> None:
        """Unload reference plate."""
        if hasattr(self, "comp_qc_state") and self.comp_qc_state:
            src_id = self.comp_qc_state.source_item_id
            if src_id and hasattr(self, "cache") and self.cache:
                self.cache.clear_source(src_id)
            self.comp_qc_state.source_plate_path = None
            self.comp_qc_state.source_item_id = None
            self.comp_qc_state.source_prefetch_progress = 0.0
        self._reader_b = None
        if hasattr(self, "comparison_state"):
            self.comparison_state.source_b_path = None
        if hasattr(self, "viewport"):
            self.viewport.set_reference_frame(None)
        dock = getattr(self, "comp_qc_dock", None)
        panel = getattr(dock, "panel", dock) if dock else getattr(self, "hud_comp_qc", None)
        if panel and hasattr(panel, "drop_source"):
            panel.drop_source.reset_text()
        self.statusBar().showMessage("xtra QC: Source plate unloaded.", 2000)

    def _on_comp_qc_state_changed(self) -> None:
        """Handle comp QC state updates, recompiling expressions and syncing matte frames."""
        qc = getattr(self, "comp_qc_state", None)
        if qc is not None and hasattr(self.viewport, "update_matte_expressions"):
            self.viewport.update_matte_expressions(
                getattr(qc, "expr_red", "r"),
                getattr(qc, "expr_green", "g"),
                getattr(qc, "expr_blue", "b"),
                getattr(qc, "expr_alpha", "a"),
            )
        self._sync_matte_frame()
        self.viewport.update()

    def _sync_matte_frame(self, frame: Optional[int] = None, sync_read_allowed: bool = True) -> None:
        """Fetch and upload the matte frame for the given frame (or clock frame) to the viewport."""
        qc = getattr(self, "comp_qc_state", None)
        is_matte_needed = qc is not None and (
            getattr(qc, "overlay_matte_enabled", False)
            or getattr(qc, "alpha_overlay_enabled", False)
            or getattr(qc, "greycheck_enabled", False)
            or getattr(qc, "checkerboard_enabled", False)
        )
        if frame is None:
            frame = getattr(self.clock, "current_frame", 1) if hasattr(self, "clock") else 1

        if is_matte_needed and hasattr(self, "_reader_matte") and self._reader_matte is not None:
            mapped_f = self._resolve_qc_frame(self._reader_matte, frame)
            m_sid = getattr(qc, "matte_item_id", None)
            m_frame = None
            if m_sid:
                m_frame = self.cache.get_source_frame(m_sid, mapped_f)
            if m_frame is None and sync_read_allowed:
                m_frame = self._reader_matte.read_frame(mapped_f)
                if m_sid and self.cache.mode != CacheMode.OFF and m_frame is not None:
                    self.cache.put_source_frame(m_sid, mapped_f, m_frame)
            if hasattr(self.viewport, "set_matte_frame"):
                self.viewport.set_matte_frame(m_frame)
        elif hasattr(self.viewport, "set_matte_frame") and getattr(self.viewport, "_image_matte", None) is not None:
            self.viewport.set_matte_frame(None)

    def _on_comp_qc_matte_dropped(self, file_path: str) -> None:
        """Handle RGBA matte dropped onto xtra QC Utils."""
        try:
            if file_path:
                from openreview.core.sequence_parser import detect_image_sequence, DiskSequenceReader
                seq = detect_image_sequence(file_path)
                display_name = Path(file_path).name
                if seq and len(seq.available_frames) > 1:
                    reader = DiskSequenceReader(seq)
                    hashes = "#" * seq.padding
                    display_name = f"{seq.prefix}{seq.separator}{hashes}{seq.extension} [{seq.start_frame}-{seq.end_frame}]"
                else:
                    reader = ReaderEngine.create_reader(file_path)

                if reader:
                    self._reader_matte = reader
                    if hasattr(self, "comp_qc_state"):
                        self.comp_qc_state.matte_path = file_path

                    # File into 'QC References' folder in Source Bin without switching active view
                    if hasattr(self, "session_graph") and self.session_graph:
                        prev_active_id = self.session_graph.active_view_id
                        prev_view_type = self.session_graph.active_view_type or ViewType.SOURCE
                        folder_id = None
                        for fid, f in self.session_graph.folders.items():
                            if f.name == "QC References":
                                folder_id = fid
                                break
                        if not folder_id:
                            f = self.session_graph.create_folder(name="QC References", folder_type="source")
                            folder_id = f.id
                        matte_item = self.session_graph.add_source_from_path(file_path, parent_folder_id=folder_id)
                        if prev_active_id is not None and self.session_graph.active_view_id != prev_active_id:
                            self.session_graph.set_active_view(prev_view_type, prev_active_id)
                        if matte_item and hasattr(self, "comp_qc_state"):
                            self.comp_qc_state.matte_item_id = matte_item.id
                        if hasattr(self, "sources_dock") and self.sources_dock:
                            self.sources_dock.panel.refresh_tree()

                    cur_f = getattr(self.clock, "current_frame", 1) if hasattr(self, "clock") else 1
                    mapped_f = self._resolve_qc_frame(reader, cur_f)
                    m_frame = reader.read_frame(mapped_f)
                    if m_frame is not None and hasattr(self.viewport, "set_matte_frame"):
                        self.viewport.set_matte_frame(m_frame)

                    # Update thumbnail in drop placeholder
                    dock = getattr(self, "comp_qc_dock", None)
                    panel = getattr(dock, "panel", dock) if dock else getattr(self, "hud_comp_qc", None)
                    if panel:
                        if hasattr(panel, "drop_matte"):
                            pix = self._frame_to_thumbnail_pixmap(m_frame)
                            panel.drop_matte.set_thumbnail(pix, display_name)
                        if hasattr(panel, "sec_matte_header") and hasattr(panel.sec_matte_header, "btn_toggle"):
                            panel.sec_matte_header.btn_toggle.setText(f"RGBA Matte: {display_name}")

                    self.statusBar().showMessage(f"xtra QC: Loaded RGBA matte: {display_name}", 3000)
                else:
                    self.statusBar().showMessage(f"xtra QC: Unable to decode matte: {Path(file_path).name}", 3000)
        except Exception as e:
            logger.warning("Failed loading matte: %s", e)
            self.statusBar().showMessage(f"xtra QC: Error loading matte: {e}", 3000)

    def _on_comp_qc_prefetch_matte(self) -> None:
        """Prefetch frames for the loaded RGBA matte into RAM cache."""
        matte_path = getattr(self.comp_qc_state, "matte_path", None)
        if not matte_path or not self._reader_matte:
            self.statusBar().showMessage("xtra QC: No matte loaded to prefetch.", 2500)
            return

        src_id = getattr(self.comp_qc_state, "matte_item_id", None) or "qc_matte"
        start_f = getattr(self.clock, "start_frame", 1) if hasattr(self, "clock") else 1
        end_f = getattr(self.clock, "end_frame", 100) if hasattr(self, "clock") else 100
        if hasattr(self._reader_matte, "seq") and self._reader_matte.seq:
            start_f = self._reader_matte.seq.start_frame
            end_f = self._reader_matte.seq.end_frame
        elif hasattr(self, "session_graph") and self.session_graph and src_id in self.session_graph.sources:
            s_item = self.session_graph.sources[src_id]
            start_f = s_item.start_frame
            end_f = s_item.end_frame

        if hasattr(self, "comp_qc_state"):
            self.comp_qc_state.matte_prefetch_progress = 0.0

        if hasattr(self, "reader_engine") and self.reader_engine:
            self.reader_engine.queue_source_background_cache(
                src_id,
                self._reader_matte,
                start_f,
                end_f,
            )
            self.statusBar().showMessage(f"xtra QC: Prefetching matte ({start_f}-{end_f})...", 2500)
        else:
            if hasattr(self, "comp_qc_state"):
                self.comp_qc_state.matte_prefetch_progress = 1.0
            self.statusBar().showMessage("xtra QC: Matte cached 100%.", 2500)

    def _on_comp_qc_unload_matte(self) -> None:
        """Unload RGBA matte."""
        if hasattr(self, "comp_qc_state") and self.comp_qc_state:
            matte_id = self.comp_qc_state.matte_item_id
            if matte_id and hasattr(self, "cache") and self.cache:
                self.cache.clear_source(matte_id)
            self.comp_qc_state.matte_path = None
            self.comp_qc_state.matte_item_id = None
            self.comp_qc_state.matte_prefetch_progress = 0.0
        self._reader_matte = None
        if hasattr(self, "viewport") and hasattr(self.viewport, "set_matte_frame"):
            self.viewport.set_matte_frame(None)
        dock = getattr(self, "comp_qc_dock", None)
        panel = getattr(dock, "panel", dock) if dock else getattr(self, "hud_comp_qc", None)
        if panel and hasattr(panel, "drop_matte"):
            panel.drop_matte.reset_text()
        self.statusBar().showMessage("xtra QC: Matte unloaded.", 2000)

    def _on_comp_qc_splines_loaded(self, shapes: list) -> None:
        """Handle spline vector curves loaded for overlay."""
        if hasattr(self, "viewport"):
            self.viewport.comp_qc_splines = shapes
            self.viewport.update()

    def _on_comp_qc_splines_cleared(self) -> None:
        """Clear spline vector curves overlay."""
        if hasattr(self, "viewport"):
            self.viewport.comp_qc_splines = []
            self.viewport.update()

    def _on_comp_qc_scan_missing_frames(self) -> None:
        """Scan disk for missing frames of the current active sequence."""
        target_path = None
        source = getattr(self, "active_source", None)
        if source and hasattr(source, "path"):
            target_path = str(source.path)
        if not target_path and hasattr(self, "session"):
            active = getattr(self.session, "active_source", None)
            if active and hasattr(active, "path"):
                target_path = str(active.path)

        dock = getattr(self, "comp_qc_dock", None)
        panel = getattr(dock, "panel", dock) if dock else getattr(self, "hud_comp_qc", None)
        if not target_path:
            if panel and hasattr(panel, "set_missing_frames_result"):
                panel.set_missing_frames_result([], 0)
                if hasattr(panel, "lbl_missing_result"):
                    panel.lbl_missing_result.setText("No active media sequence loaded to scan.")
            return

        from openreview.core.sequence_parser import detect_image_sequence
        seq_info = detect_image_sequence(target_path)
        if seq_info and panel and hasattr(panel, "set_missing_frames_result"):
            missing = sorted(list(seq_info.missing_frames))
            total = (seq_info.end_frame - seq_info.start_frame + 1)
            panel.set_missing_frames_result(missing, total)
        elif panel and hasattr(panel, "lbl_missing_result"):
            panel.lbl_missing_result.setText("Target media is a single file / video container (no missing frames).")
            panel.lbl_missing_result.setStyleSheet("color: #50E070; font-size: 11px;")

    def _on_load_external_audio(self) -> None:
        """Open file dialog to attach external audio file."""
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Load External Audio Track",
            "",
            "Audio & Video Files (*.wav *.mp3 *.aac *.flac *.m4a *.mov *.mp4 *.mkv);;All Files (*.*)",
        )
        if path:
            success = self.audio_engine.load_audio(path)
            if success:
                self.statusBar().showMessage(f"Attached audio track: {Path(path).name}", 4000)
            else:
                QMessageBox.warning(self, "Load Failed", f"Could not load audio file:\n{path}")


    def _on_choose_frame_rate(self) -> None:
        """Open Frame Rate Dialog and apply chosen playback speed."""
        fps, ok = FrameRateDialog.get_target_fps(self, self.clock.fps)
        if ok and fps > 0:
            self.clock.set_fps(fps)
            self.statusBar().showMessage(f"Playback Frame Rate set to {fps:.2f} FPS", 3000)

    def _on_goto_frame_modal(self) -> None:
        """Prompt user with a modal dialog to jump to an exact frame number (Ctrl+G)."""
        start_f = self.clock.start_frame
        end_f = self.clock.end_frame
        curr_f = self.clock.current_frame

        target_frame, ok = GoToFrameDialog.get_target_frame(self, start_f, end_f, curr_f)
        if ok:
            self.clock.set_current_frame(target_frame)
            self.statusBar().showMessage(f"Navigated to frame #{target_frame}", 2000)

    def _on_reload_current_frame(self) -> None:
        """Reload the currently displayed frame from disk (Ctrl+R)."""
        curr = self.clock.current_frame
        if hasattr(self, "cache"):
            with self.cache._lock:
                to_remove = [k for k in list(self.cache._frames.keys()) if (isinstance(k, tuple) and k[1] == curr) or k == curr]
                for k in to_remove:
                    self.cache._used_bytes -= self.cache._frame_bytes.pop(k, 0)
                    self.cache._frames.pop(k, None)
            self.cache._emit_status()
        self._on_frame_changed(curr)
        self.statusBar().showMessage(f"Reloaded frame #{curr} from disk.", 2500)

    def _on_reload_sequence(self) -> None:
        """Clear all cached frames and reload current sequence from disk (Ctrl+Shift+R)."""
        if hasattr(self, "cache"):
            self.cache.clear()
        self._on_frame_changed(self.clock.current_frame)
        self.statusBar().showMessage("Flushed cache and reloaded sequence.", 2500)

    def _step_gain(self, delta: float) -> None:
        curr = self.top_bar.gain_spin.value()
        val = max(0.015625, curr + delta)
        self.top_bar.set_gain(val)
        msg = f"Gain: {val:.2f}"
        self.statusBar().showMessage(msg, 1500)
        if hasattr(self, "viewport") and self.viewport:
            self.viewport.show_toast(msg)

    def _step_gamma(self, delta: float) -> None:
        curr = self.top_bar.gam_spin.value()
        val = max(0.0, curr + delta)
        self.top_bar.set_gamma(val)
        msg = f"Gamma: {val:.2f}"
        self.statusBar().showMessage(msg, 1500)
        if hasattr(self, "viewport") and self.viewport:
            self.viewport.show_toast(msg)

    def _step_saturation(self, delta: float) -> None:
        curr = self.top_bar.sat_spin.value()
        val = max(0.0, curr + delta)
        self.top_bar.set_saturation(val)
        msg = f"Saturation: {val:.2f}"
        self.statusBar().showMessage(msg, 1500)
        if hasattr(self, "viewport") and self.viewport:
            self.viewport.show_toast(msg)

    def _on_toggle_invert_shortcut(self) -> None:
        curr = False
        if hasattr(self, "invert_colors_act"):
            curr = not self.invert_colors_act.isChecked()
            self.invert_colors_act.setChecked(curr)
        self._on_toggle_invert_colors(curr)
        msg = f"Invert Colors: {'ON' if curr else 'OFF'}"
        self.statusBar().showMessage(msg, 1500)
        if hasattr(self, "viewport") and self.viewport:
            self.viewport.show_toast(msg)

    def _perform_atomic_session_reset(self) -> None:
        """Atomically clear all session state, media, caches, background workers, and UI panels."""
        self._suppress_in_out_record = True

        # 1. Stop playback and pause/unload audio
        self.clock.pause()
        if hasattr(self, "audio_engine") and self.audio_engine:
            self.audio_engine.unload_audio()
            self.audio_engine.reset_offsets()

        # 2. Cancel and drain background prefetching and secondary background caching
        if hasattr(self, "reader_engine") and self.reader_engine:
            self.reader_engine.clear_background_cache_queue()
            self.reader_engine.cancel_pending()
            if hasattr(self.reader_engine, "thread_pool"):
                self.reader_engine.thread_pool.clear()
            self.reader_engine.set_reader(None)

        self.reader = None

        # 3. Clear Session Graph and sources BEFORE resetting clock so frame change events cannot reload media
        if hasattr(self, "_source_readers"):
            self._source_readers.clear()
        if hasattr(self, "session_graph") and self.session_graph:
            self.session_graph.clear()

        # 4. Clear RAM and Disk caches and timeline indicators
        if hasattr(self, "cache") and self.cache:
            self.cache.clear()
            self.cache.set_active_source(None)
        if hasattr(self, "timeline") and self.timeline:
            self.timeline.update_cache_display(
                set(), 0.0, cache_mode=self.cache.mode.value if hasattr(self, "cache") else "off"
            )

        # 5. Reset Clock without triggering spurious frame decode signals
        if hasattr(self, "clock") and self.clock:
            was_blocked = self.clock.blockSignals(True)
            self.clock.clear_all_bookmarks()
            self.clock.set_range(1001, 1200, reset_in_out=True)
            self.clock.set_frame(1001)
            self.clock.blockSignals(was_blocked)

        if hasattr(self, "timeline") and self.timeline:
            self.timeline._on_range_changed(1001, 1200)
            self.timeline._on_frame_changed(1001)
            self.timeline.update()

        # 6. Clear Annotations, Marks & State
        if hasattr(self, "annotation_store") and self.annotation_store:
            self.annotation_store.clear_all()
        if hasattr(self, "annotation_manager_widget") and self.annotation_manager_widget:
            self.annotation_manager_widget.refresh_table()

        # 7. Reset comparison state
        if hasattr(self, "comparison_state") and self.comparison_state:
            self.comparison_state.reset()

        # 8. Clear Viewport Canvas completely (textures, primary frame, reference frame, matte)
        if hasattr(self, "viewport") and self.viewport:
            if hasattr(self, "comparison_state") and self.comparison_state:
                self.viewport.comparison_state = self.comparison_state
            self.viewport.clear_canvas()
            self.viewport.set_primary_frame(0, None)
            self.viewport.set_reference_frame(None)
            self.viewport.reset_transforms()
            self.viewport.set_matte(None)
            self.viewport.set_background_mode("black")
            self.viewport.set_contact_sheet_mode(False, None)
            self.viewport.update()

        # 9. Reset Top Bar Controls
        if hasattr(self, "top_bar") and self.top_bar:
            self.top_bar.reset_exposure()
            self.top_bar.reset_gamma()
            self.top_bar.set_channel_mode(0)

        # 10. Clear UI widgets & inspectors
        if hasattr(self, "hist_widget") and self.hist_widget:
            self.hist_widget.update_frame(None)
        if hasattr(self, "waveform_widget") and self.waveform_widget:
            self.waveform_widget.update_frame(None)
        if hasattr(self, "vectorscope_widget") and self.vectorscope_widget:
            self.vectorscope_widget.update_frame(None)
        if hasattr(self, "hud_notes_editor") and self.hud_notes_editor:
            self.hud_notes_editor.clear()

        # 11. Clear Sources Dock and panel
        if hasattr(self, "sources_dock") and self.sources_dock:
            panel = self.sources_dock.panel
            panel.tree.clear()
            panel.refresh_tree()
            panel.list_inputs.clear()
            panel.lbl_inputs_view.setText("Inputs for: None")
            if hasattr(panel, "frame_clip_edit"):
                panel.frame_clip_edit.hide()
            if hasattr(panel, "frame_layout_edit"):
                panel.frame_layout_edit.hide()

        self._current_media_path = None
        self._current_media_type = "none"
        self._current_session_file = None
        self._current_stereo_pair = None
        self._is_exr_contact_sheet_active = False
        self._exr_contact_sheet_tile_map.clear()
        if hasattr(self, "layers_widget"):
            self.layers_widget.set_contact_sheet_active(False)
            self.layers_widget.refresh_list(["beauty"])
        self._update_layered_input_menu_visibility()
        self._update_resolution_status("No Media")

        # Clear xtra QC Utils loaded plates and reactive state
        self._on_comp_qc_unload_source()
        self._on_comp_qc_unload_matte()
        if hasattr(self, "comp_qc_state") and self.comp_qc_state:
            self.comp_qc_state.reset_defaults()

        self._update_window_title()
        self.statusBar().showMessage("Session cleared - canvas and media reset", 3000)
        self._active_view_is_sequence = False
        self._pre_sequence_cache_mode = None
        self._pre_sequence_cache_started = None
        self._suppress_in_out_record = False

    def _on_close_session(self, prompt: bool = True) -> None:
        """Close active session and thoroughly clear everything from canvas, player and memory."""
        if prompt:
            reply = QMessageBox.question(
                self,
                "Close Session",
                "Close current session and clear canvas? All loaded media and annotations will be removed.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return

        self._perform_atomic_session_reset()

    # --------------------------------------------------------------------------
    # Multi-User Remote Dailies Sync & Studio OCIO Handlers
    # --------------------------------------------------------------------------
    def _on_open_dailies_sync(self) -> None:
        """Toggle the in-viewport Multi-User Remote Dailies Sync panel (Ctrl+Shift+D)."""
        vis = self.hud_dailies_sync.isHidden()
        self.hud_dailies_sync.setVisible(vis)
        if vis:
            self.hud_dailies_sync.raise_()
        self.statusBar().showMessage(f"Remote Dailies Sync Panel: {'Shown' if vis else 'Hidden'}", 2000)

    def _on_open_ocio_config(self) -> None:
        """Toggle the in-viewport OpenColorIO Configuration panel."""
        vis = self.hud_ocio.isHidden()
        self.hud_ocio.setVisible(vis)
        if vis:
            self.hud_ocio.raise_()
        self.statusBar().showMessage(f"OpenColorIO Panel: {'Shown' if vis else 'Hidden'}", 2000)

    def _on_ocio_config_changed(self) -> None:
        """Update top bar controls when a custom studio OCIO config is loaded."""
        spaces = self.ocio_manager.get_color_spaces()
        views = self.ocio_manager.get_views()
        self.top_bar.update_ocio_spaces(spaces, views)
        self.statusBar().showMessage(f"Applied OCIO Config: {self.ocio_manager.config_name}", 4000)

    def _on_sync_status_changed(self, connected: bool, message: str) -> None:
        """Update status bar indicator when sync connection state changes."""
        if connected:
            role = "Host" if self.sync_engine.is_host else "Client"
            presenter_tag = " [Presenter]" if self.sync_engine.is_presenter else ""
            self.sync_status_label.setText(f"Sync: {role}{presenter_tag}")
            self.sync_status_label.setStyleSheet("color: #2ecc71; font-family: monospace; font-weight: bold;")
        else:
            self.sync_status_label.setText("Sync: Offline")
            self.sync_status_label.setStyleSheet("color: #888888; font-family: monospace; font-weight: bold;")
        self.statusBar().showMessage(message, 4000)

    def _on_remote_playback(self, payload: dict) -> None:
        """Handle synchronized playback command from remote presenter."""
        if not self.sync_engine.is_following:
            return

        self._suppress_sync_broadcast = True
        try:
            target_f = payload.get("frame")
            is_playing = payload.get("is_playing", False)
            fps = payload.get("fps")
            in_f = payload.get("in_frame")
            out_f = payload.get("out_frame")

            if in_f is not None and out_f is not None:
                self.clock.set_in_out(in_f, out_f)
            if fps is not None and abs(self.clock.fps - fps) > 0.01:
                self.clock.set_fps(fps)
            if target_f is not None and target_f != self.clock.current_frame:
                self.clock.set_frame(target_f)

            if is_playing and not self.clock.is_playing:
                self.clock.play()
            elif not is_playing and self.clock.is_playing:
                self.clock.pause()
        finally:
            self._suppress_sync_broadcast = False

    def _on_remote_media(self, media_path: str) -> None:
        """Handle media load command from remote presenter."""
        if not self.sync_engine.is_following:
            return

        p = Path(media_path)
        if p.exists():
            self._load_path(str(p))
            self.statusBar().showMessage(f"Synced media with presenter: {p.name}", 4000)
        else:
            self.statusBar().showMessage(f"Presenter loaded: {p.name} (not found locally)", 4000)

    def _on_remote_color(self, payload: dict) -> None:
        """Handle color grading synchronization from remote presenter."""
        if not self.sync_engine.is_following:
            return

        self._suppress_sync_broadcast = True
        try:
            if "exposure_ev" in payload:
                self.top_bar.set_exposure(float(payload["exposure_ev"]))
            if "gamma" in payload:
                self.top_bar.set_gamma(float(payload["gamma"]))
            if "channel_mode" in payload:
                self.top_bar.set_channel_mode(int(payload["channel_mode"]))
            if "input_space" in payload and "view_transform" in payload:
                self.top_bar.set_color_spaces(int(payload["input_space"]), int(payload["view_transform"]))
        finally:
            self._suppress_sync_broadcast = False

    def _on_remote_stroke(self, frame: int, stroke_data: dict) -> None:
        """Handle live collaborative markup stroke from peer."""
        self._suppress_sync_broadcast = True
        try:
            stroke = Stroke.from_dict(stroke_data)
            self.annotation_store.add_stroke(frame, stroke)
            self.viewport.update()
            self.timeline.update()
        except Exception as e:
            print(f"[MainWindow] Error applying remote stroke: {e}")
        finally:
            self._suppress_sync_broadcast = False

    def _on_remote_clear_annotations(self, frame: int) -> None:
        """Handle remote request to clear annotations on a frame or all frames."""
        self._suppress_sync_broadcast = True
        try:
            if frame == -1:
                self.annotation_store.clear_all()
            else:
                self.annotation_store.clear_frame(frame)
            self.viewport.update()
            self.timeline.update()
        finally:
            self._suppress_sync_broadcast = False

    def _on_remote_note(self, frame: int, note: str) -> None:
        """Handle collaborative review note update from peer."""
        self._suppress_sync_broadcast = True
        try:
            self.annotation_store.set_frame_note(frame, note)
            self.timeline.update()
        finally:
            self._suppress_sync_broadcast = False

    def _on_pixel_inspected(self, x: int, y: int, r: float, g: float, b: float, a: float) -> None:
        self.coord_label.setText(f"X: {x:<5d} Y: {y:<5d}")
        self.rgba_label.setText(
            f"RGBA: <span style='color:#ff6b6b;'>R:{r:0.4f}</span>, "
            f"<span style='color:#69db7c;'>G:{g:0.4f}</span>, "
            f"<span style='color:#4dabf7;'>B:{b:0.4f}</span>, "
            f"<span style='color:#ced4da;'>A:{a:0.4f}</span>"
        )

        # 8-bit
        u8_r = int(np.clip(r * 255.0, 0, 255))
        u8_g = int(np.clip(g * 255.0, 0, 255))
        u8_b = int(np.clip(b * 255.0, 0, 255))
        self.u8_label.setText(
            f"(RGB: <span style='color:#ff6b6b;'>{u8_r}</span>, "
            f"<span style='color:#69db7c;'>{u8_g}</span>, "
            f"<span style='color:#4dabf7;'>{u8_b}</span>)"
        )

        # HSV
        h, s, v = colorsys.rgb_to_hsv(np.clip(r, 0, 1), np.clip(g, 0, 1), np.clip(b, 0, 1))
        self.hsv_label.setText(f"HSV: {h:0.3f}, {s:0.3f}, {v:0.3f}")

        # Luminance
        y_val = 0.2126 * r + 0.7152 * g + 0.0722 * b
        self.luma_label.setText(f"<span style='color:#ffd43b;'>Y: {y_val:0.3f}</span>")

    def _on_zoom_changed(self, zoom: float) -> None:
        pct = int(round(zoom * 100))
        self.zoom_status_label.setText(f"Zoom: {pct}%")

    def _on_shuttle_speed_changed(self, speed: float) -> None:
        direction = "Fwd" if speed > 0 else "Rev"
        self.shuttle_status_label.setText(f"{abs(speed):.1f}x {direction}" if speed != 1.0 else "1.0x")
        if hasattr(self, "audio_engine") and self.audio_engine:
            self.audio_engine.sync_to_frame(
                self.clock.current_frame, self.clock.is_playing, speed
            )

    def _on_annotations_changed(self, frame: int) -> None:
        self.viewport.update()
        self.timeline.update()
        if self.sync_engine.is_connected and not getattr(self, "_suppress_sync_broadcast", False):
            if frame != -1:
                strokes = self.annotation_store.get_strokes(frame)
                if strokes:
                    self.sync_engine.broadcast_stroke(frame, strokes[-1].to_dict())
                note = self.annotation_store.get_frame_note(frame)
                if note:
                    self.sync_engine.broadcast_note(frame, note)

    def _on_viewport_files_dropped_target(
        self,
        paths: list[str],
        target: str,
        source_ids: Optional[list[str]] = None,
    ) -> None:
        if not paths and not source_ids:
            return

        if not hasattr(self, "session_graph"):
            if paths:
                self._on_files_dropped(paths)
            return

        # ----------------------------------------------------------------------
        # Target: "bin" (Just add the media to source bin and do nothing)
        # ----------------------------------------------------------------------
        if target == "bin":
            added_sources = []
            if paths:
                for p in paths:
                    if os.path.exists(p):
                        src = self.session_graph.add_source_from_path(p, auto_activate=False)
                        if src:
                            added_sources.append(src)
            if hasattr(self, "sources_dock"):
                self.sources_dock.panel.refresh_tree()
            if added_sources:
                self.statusBar().showMessage(
                    f"Added {len(added_sources)} media item(s) to Sources bin", 3000
                )
            return

        # ----------------------------------------------------------------------
        # Target: "stack" (Add Media to the stack)
        # ----------------------------------------------------------------------
        sids_to_add: list[str] = list(source_ids or [])
        if paths:
            for p in paths:
                if os.path.exists(p):
                    src = self.session_graph.add_source_from_path(p, auto_activate=False)
                    if src and src.id not in sids_to_add:
                        sids_to_add.append(src.id)

        if not sids_to_add:
            return

        active_viewable = self.session_graph.get_active_viewable()
        target_stack: Optional[StackView] = None
        if isinstance(active_viewable, StackView):
            target_stack = active_viewable

        if target_stack is not None:
            # Append to active stack
            count_added = 0
            for sid in sids_to_add:
                if sid not in target_stack.source_ids:
                    target_stack.source_ids.append(sid)
                    count_added += 1

            # If stack had only 1 item before, set index_b to 1 if we now have >= 2 items
            if len(target_stack.source_ids) >= 2 and target_stack.index_b == target_stack.index_a:
                target_stack.index_b = 1

            self.session_graph.graph_changed.emit()
            if hasattr(self, "sources_dock"):
                self.sources_dock.panel.refresh_tree()
                self.sources_dock.panel._refresh_subpanels()

            self._on_active_view_changed("stack", target_stack.id)
            self.statusBar().showMessage(
                f"Added {count_added} layer(s) to stack '{target_stack.name}'", 3000
            )
        else:
            # Create a new stack named 'Default stack' or numbered sequentially
            existing_stack_names = {s.name for s in self.session_graph.stacks.values()}
            if "Default stack" not in existing_stack_names:
                new_name = "Default stack"
            else:
                n = 2
                while f"Stack {n}" in existing_stack_names:
                    n += 1
                new_name = f"Stack {n}"

            stk = self.session_graph.create_stack(
                name=new_name,
                source_ids=sids_to_add,
                compare_mode="switch",
            )
            if hasattr(self, "sources_dock"):
                self.sources_dock.panel.refresh_tree()
                self.sources_dock.panel._refresh_subpanels()
            self.statusBar().showMessage(
                f"Created '{stk.name}' with {len(sids_to_add)} layers", 3000
            )

    def _on_file_dropped(self, path: str) -> None:
        self._on_files_dropped([path])

    def _on_files_dropped(self, paths: list[str]) -> None:
        if not paths:
            return
        # Load primary dropped path asynchronously so viewport lands with zero hitching
        self._async_drop_active = True
        try:
            self._load_path(paths[0])
        finally:
            self._async_drop_active = False

        # Immediate priority async prefetch for the first frame so it appears on screen without blocking drop
        if self.cache.mode != CacheMode.OFF:
            cur_f = self.clock.current_frame
            self.reader_engine.request_prefetch(
                cur_f,
                cur_f,
                current_frame=cur_f,
                resolver_fn=self._resolve_timeline_frame_sources,
                max_batch=1,
            )

        if len(paths) > 1 and hasattr(self, "session_graph"):
            self.async_source_loader.load_paths_async(paths[1:])

    def _on_async_source_discovered(self, item: Optional[SourceItem], index: int, total: int) -> None:
        if not item or not hasattr(self, "session_graph"):
            return
        self.session_graph.add_sources_batch([item])
        if hasattr(self, "sources_dock"):
            self.sources_dock.panel.refresh_tree()
        # Inactive sources are not automatically cached; background caching remains dedicated
        # exclusively to the active viewport input.

    def _queue_source_for_background_caching(self, item: SourceItem) -> None:
        if not hasattr(self, "reader_engine") or not self.reader_engine:
            return
        from openreview.core.video_reader import is_video_file, VideoMediaReader
        from openreview.core.sequence_parser import detect_image_sequence, DiskSequenceReader
        from openreview.core.reader import DiskMediaReader

        rdr = None
        if item.media_type == "video":
            try:
                rdr = VideoMediaReader(item.path)
            except Exception:
                pass
        elif item.media_type == "sequence":
            seq = detect_image_sequence(item.path)
            if seq:
                rdr = DiskSequenceReader(seq)
        elif item.media_type == "still":
            rdr = DiskMediaReader(item.path)

        if rdr:
            self.reader_engine.queue_source_background_cache(
                item.id,
                rdr,
                item.start_frame,
                item.end_frame,
            )

    def _on_background_caching_progress(self, source_id: str, cached: int, total: int) -> None:
        if hasattr(self, "comp_qc_state") and self.comp_qc_state:
            ratio = float(cached) / max(1, total)
            if source_id == getattr(self.comp_qc_state, "source_item_id", None):
                self.comp_qc_state.source_prefetch_progress = ratio
            elif source_id == getattr(self.comp_qc_state, "matte_item_id", None):
                self.comp_qc_state.matte_prefetch_progress = ratio

        src_name = ""
        if hasattr(self, "session_graph") and source_id in self.session_graph.sources:
            src_name = self.session_graph.sources[source_id].name
        ram_mb, _, _ = self.cache.get_memory_stats()
        disk_mb, _, _ = self.cache.get_disk_memory_stats()
        self.statusBar().showMessage(
            f"Caching in background: {src_name} ({cached}/{total} frames) | RAM: {ram_mb:.0f}MB | Temp Disk: {disk_mb:.0f}MB",
            2000,
        )

    def _load_path(self, path: str) -> None:
        if not path or not os.path.exists(path):
            return

        self._is_exr_contact_sheet_active = False
        if hasattr(self, "layers_widget"):
            self.layers_widget.set_contact_sheet_active(False)
        if hasattr(self, "viewport"):
            self.viewport.set_contact_sheet_mode(False, None)

        self.pref_mgr.add_recent_file(path)
        self._update_recent_menu()

        if hasattr(self, "session_graph"):
            src = self.session_graph.add_source_from_path(path)
            self.session_graph.set_active_view(ViewType.SOURCE, src.id)
            if hasattr(self, "sources_dock"):
                self.sources_dock.panel.refresh_tree()

        if self.sync_engine.is_connected and (self.sync_engine.is_presenter or self.sync_engine.is_host):
            if not getattr(self, "_suppress_sync_broadcast", False):
                self.sync_engine.broadcast_media(path)

        from openreview.core.video_reader import is_video_file, VideoMediaReader
        from openreview.core.sequence_parser import detect_image_sequence, DiskSequenceReader
        from openreview.core.reader import DiskMediaReader

        # 0. Standalone Audio File Loading
        ext = os.path.splitext(path)[1].lower()
        if ext in (".wav", ".mp3", ".aac", ".flac", ".ogg", ".aiff", ".m4a"):
            success = self.audio_engine.load_audio(path)
            if success:
                self.statusBar().showMessage(f"Attached Audio Track: {os.path.basename(path)}", 4000)
                return

        # 1. Video File Loading (MP4, MOV, MKV, AVI, WebM, FLV, WMV, etc.)
        if is_video_file(path):
            try:
                vid_reader = VideoMediaReader(path)
                self.reader = vid_reader
                self.reader_engine.set_reader(self.reader)
                if 'src' in locals() and src:
                    self.cache.set_active_source(src.id)
                self._current_media_path = str(path)
                self._current_media_type = "video"
                self.clock.set_fps(vid_reader.fps)
                self.clock.set_range(1, vid_reader.total_frames, reset_in_out=True)
                self.cache.set_region_bounds(1, vid_reader.total_frames)
                self.clock.set_frame(1)
                # Load audio track from video file
                self.audio_engine.load_audio(path)
                self.audio_engine.set_sequence_context(1, vid_reader.fps)
                # Auto-configure input colorspace from video stream metadata
                self._auto_configure_media_colorspace(path, source_item=src if 'src' in locals() else None)
                base = os.path.basename(path)
                self._update_window_title()
                self.statusBar().showMessage(
                    f"Loaded Video: {base} ({vid_reader.total_frames} frames @ {vid_reader.fps:.2f} fps)",
                    6000,
                )
                self._on_frame_changed(1)
                return
            except Exception as e:
                self.statusBar().showMessage(f"Failed to load video: {e}", 5000)
                return

        # 2. Image Sequence Auto-Detection
        seq_info = detect_image_sequence(path)
        if seq_info and len(seq_info.available_frames) > 1:
            self.reader = DiskSequenceReader(seq_info)
            self.reader_engine.set_reader(self.reader)
            if 'src' in locals() and src:
                self.cache.set_active_source(src.id)
            self._current_media_path = str(path)
            self._current_media_type = "sequence"
            self.clock.set_range(seq_info.start_frame, seq_info.end_frame, reset_in_out=True)
            self.cache.set_region_bounds(seq_info.start_frame, seq_info.end_frame)
            self.clock.set_frame(seq_info.start_frame)
            self.audio_engine.set_sequence_context(seq_info.start_frame, self.clock.fps)

            # Auto-configure colorspace from sequence metadata
            self._auto_configure_media_colorspace(path, source_item=src if 'src' in locals() else None)

            gaps_info = f" ({len(seq_info.missing_frames)} gaps)" if seq_info.missing_frames else ""
            seq_display = f"{seq_info.prefix}{seq_info.separator}####{seq_info.extension}"
            self._update_window_title()
            self.statusBar().showMessage(
                f"Loaded Sequence: {seq_display} [{seq_info.start_frame}-{seq_info.end_frame}]{gaps_info}",
                6000,
            )
            self._on_frame_changed(seq_info.start_frame)
        else:
            # 3. Single Media Image
            self.reader = DiskMediaReader(path)
            self.reader_engine.set_reader(self.reader)
            if 'src' in locals() and src:
                self.cache.set_active_source(src.id)
            self._current_media_path = str(path)
            self._current_media_type = "image"

            # Auto-configure colorspace from image metadata
            self._auto_configure_media_colorspace(path, source_item=src if 'src' in locals() else None)

            self._on_frame_changed(self.clock.current_frame)
            self._update_window_title()
            self.statusBar().showMessage(f"Loaded Media: {os.path.basename(path)}", 5000)

        # 4. Multi-Channel EXR Layer Discovery
        if str(path).lower().endswith(".exr"):
            try:
                from openreview.core.image_io import get_exr_channels
                exr_info = get_exr_channels(str(path))
                lyrs = list(exr_info.get("layers", {}).keys())
                for cl in exr_info.get("cryptomatte_layers", []):
                    if cl not in lyrs:
                        lyrs.append(cl)
                if not lyrs:
                    lyrs = ["beauty"]
                self.top_bar.set_available_layers(lyrs, "beauty")
                if hasattr(self, "layers_widget"):
                    self.layers_widget.refresh_list(lyrs)
            except Exception:
                self.top_bar.set_available_layers(["beauty"], "beauty")
                if hasattr(self, "layers_widget"):
                    self.layers_widget.refresh_list(["beauty"])
        else:
            self.top_bar.set_available_layers(["beauty"], "beauty")
            if hasattr(self, "layers_widget"):
                self.layers_widget.refresh_list(["beauty"])

        # 5. Stereo 3D Auto-Detection
        try:
            from openreview.core.stereo import detect_stereo_counterpart
            stereo_pair = detect_stereo_counterpart(str(path))
            if stereo_pair:
                self._current_stereo_pair = stereo_pair
                self.statusBar().showMessage(
                    f"Stereo 3D Detected: Left={os.path.basename(stereo_pair.left_path)} | Right={os.path.basename(stereo_pair.right_path)}",
                    7000,
                )
            else:
                self._current_stereo_pair = None
        except Exception:
            self._current_stereo_pair = None

        self._update_layered_input_menu_visibility()

        if hasattr(self, "pkg_mgr") and self.pkg_mgr:
            try:
                self.pkg_mgr.dispatch_hook("on_media_loaded", str(path))
            except Exception:
                pass

        # Safely land source and defer background prefetch by 1.0s so UI remains completely responsive
        self._schedule_delayed_prefetch(1000)

    def _on_open_media(self) -> None:
        from openreview.core.image_io import IMAGE_EXTENSIONS
        from openreview.core.video_reader import VIDEO_EXTENSIONS
        img_globs = " ".join(f"*{ext}" for ext in sorted(IMAGE_EXTENSIONS))
        vid_globs = " ".join(f"*{ext}" for ext in sorted(VIDEO_EXTENSIONS)) + " *.mx*n *.mx*p"
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Open Media Sequence or Video",
            "",
            f"All Media ({img_globs} {vid_globs});;Images & Sequences ({img_globs});;Videos ({vid_globs});;All Files (*.*)",
        )
        if file_path:
            self._load_path(file_path)

    # --------------------------------------------------------------------------
    # Marker & Navigation Handlers
    # --------------------------------------------------------------------------
    def _on_next_marker(self) -> None:
        """Jump to the next bookmark or annotated keyframe (Alt+Right)."""
        annotated = set(self.annotation_store.get_annotated_frames())
        self.clock.next_marker(extra_markers=annotated, direction=1)

    def _on_prev_marker(self) -> None:
        """Jump to the previous bookmark or annotated keyframe (Alt+Left)."""
        annotated = set(self.annotation_store.get_annotated_frames())
        self.clock.next_marker(extra_markers=annotated, direction=-1)

    # --------------------------------------------------------------------------
    # Window & Presentation Handlers
    # --------------------------------------------------------------------------
    def isFullScreen(self) -> bool:
        """Transparently report fullscreen state for both borderless and native fullscreen."""
        return bool(getattr(self, "_in_borderless_fullscreen", False) or super().isFullScreen())

    def _apply_windows_fullscreen_taskbar_hint(self, fullscreen: bool) -> None:
        """Notify Windows Shell (ITaskbarList2) and enforce HWND bounds over the Windows Taskbar."""
        if not sys.platform.startswith("win"):
            return
        try:
            hwnd = int(self.winId())
            if not hwnd:
                return
            import ctypes
            from ctypes import wintypes

            user32 = ctypes.windll.user32
            ole32 = ctypes.windll.ole32

            # 1. Notify Windows Explorer taskbar via ITaskbarList2::MarkFullscreenWindow
            class GUID(ctypes.Structure):
                _fields_ = [
                    ("Data1", ctypes.c_ulong),
                    ("Data2", ctypes.c_ushort),
                    ("Data3", ctypes.c_ushort),
                    ("Data4", ctypes.c_ubyte * 8),
                ]

            CLSID_TaskbarList = GUID(
                0x56FDF344, 0xFD6D, 0x11D0, (ctypes.c_ubyte * 8)(0x95, 0x8A, 0x00, 0x60, 0x97, 0xC9, 0xA0, 0x90)
            )
            IID_ITaskbarList2 = GUID(
                0x602D4995, 0xB13A, 0x429B, (ctypes.c_ubyte * 8)(0xA6, 0x6E, 0x19, 0x35, 0xE4, 0x4F, 0x43, 0x17)
            )

            ole32.CoInitialize(None)
            taskbar_ptr = ctypes.c_void_p()
            CLSCTX_INPROC_SERVER = 1
            hr = ole32.CoCreateInstance(
                ctypes.byref(CLSID_TaskbarList),
                None,
                CLSCTX_INPROC_SERVER,
                ctypes.byref(IID_ITaskbarList2),
                ctypes.byref(taskbar_ptr),
            )
            if hr == 0 and taskbar_ptr.value:
                vtable = ctypes.cast(
                    ctypes.cast(taskbar_ptr, ctypes.POINTER(ctypes.c_void_p))[0],
                    ctypes.POINTER(ctypes.c_void_p),
                )
                # Index 3: HrInit(this)
                HrInitProto = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p)
                hr_init = HrInitProto(vtable[3])
                hr_init(taskbar_ptr)

                # Index 8: MarkFullscreenWindow(this, HWND, BOOL)
                MarkFsProto = ctypes.WINFUNCTYPE(
                    ctypes.c_long, ctypes.c_void_p, wintypes.HWND, wintypes.BOOL
                )
                mark_fs = MarkFsProto(vtable[8])
                mark_fs(taskbar_ptr, wintypes.HWND(hwnd), wintypes.BOOL(1 if fullscreen else 0))

                # Index 2: Release(this)
                ReleaseProto = ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)
                release_fn = ReleaseProto(vtable[2])
                release_fn(taskbar_ptr)

            # 2. Enforce physical monitor bounds (+1px height prevents DWM DirectFlip OpenGL
            # popup occlusion while still satisfying rcWindow.bottom >= rcMonitor.bottom so
            # Windows Explorer lowers the WS_EX_TOPMOST taskbar behind the window).
            if fullscreen:
                class MONITORINFO(ctypes.Structure):
                    _fields_ = [
                        ("cbSize", wintypes.DWORD),
                        ("rcMonitor", wintypes.RECT),
                        ("rcWork", wintypes.RECT),
                        ("dwFlags", wintypes.DWORD),
                    ]

                MONITOR_DEFAULTTONEAREST = 2
                hmon = user32.MonitorFromWindow(wintypes.HWND(hwnd), MONITOR_DEFAULTTONEAREST)
                if hmon:
                    mi = MONITORINFO()
                    mi.cbSize = ctypes.sizeof(MONITORINFO)
                    if user32.GetMonitorInfoW(wintypes.HANDLE(hmon), ctypes.byref(mi)):
                        rc = mi.rcMonitor
                        px = rc.left
                        py = rc.top
                        pw = rc.right - rc.left
                        ph = (rc.bottom - rc.top) + 1
                        HWND_TOP = 0
                        SWP_FRAMECHANGED = 0x0020
                        SWP_SHOWWINDOW = 0x0040
                        user32.SetWindowPos(
                            wintypes.HWND(hwnd),
                            wintypes.HWND(HWND_TOP),
                            px,
                            py,
                            pw,
                            ph,
                            SWP_FRAMECHANGED | SWP_SHOWWINDOW,
                        )
        except Exception:
            pass

    def _enter_borderless_fullscreen(self) -> None:
        """Expand window borderless to full screen geometry covering taskbar without exclusive DWM occlusion."""
        if getattr(self, "_in_borderless_fullscreen", False):
            self._apply_windows_fullscreen_taskbar_hint(True)
            return
        self._pre_borderless_maximized = self.isMaximized()
        self._pre_borderless_geom = self.normalGeometry() if self.isMaximized() else self.geometry()
        self._pre_borderless_flags = self.windowFlags()

        # Clear WindowMaximized state first so Win32/Qt does not clamp the frameless
        # window to availableGeometry() (work area above the taskbar).
        if self.isMaximized():
            self.setWindowState(self.windowState() & ~Qt.WindowState.WindowMaximized)

        # Frameless window covering the full screen
        self.setWindowFlags(self._pre_borderless_flags | Qt.WindowType.FramelessWindowHint)
        self.show()

        target_screen = self.screen() or QApplication.primaryScreen()
        if target_screen:
            geom = target_screen.geometry()
            # On Windows, when an OpenGL widget window strictly equals the physical monitor
            # bounds (0, 0, width, height), DWM/display driver activates DirectFlip scanout
            # which occludes QMenu popups. Extending height by +1 pixel avoids DirectFlip
            # while ensuring rcWindow.bottom >= rcMonitor.bottom so Windows hides the taskbar.
            if sys.platform.startswith("win"):
                geom.setHeight(geom.height() + 1)
            self.setGeometry(geom)

        self._apply_windows_fullscreen_taskbar_hint(True)
        self.raise_()
        self.activateWindow()
        self._in_borderless_fullscreen = True

    def _exit_borderless_fullscreen(self) -> None:
        """Restore window frame, geometry, and maximized state from borderless fullscreen."""
        if not getattr(self, "_in_borderless_fullscreen", False):
            if super().isFullScreen():
                self.showNormal()
            return
        self._in_borderless_fullscreen = False
        self._apply_windows_fullscreen_taskbar_hint(False)

        if getattr(self, "_pre_borderless_flags", None) is not None:
            self.setWindowFlags(self._pre_borderless_flags)

        if getattr(self, "_pre_borderless_maximized", False):
            self.showMaximized()
        elif getattr(self, "_pre_borderless_geom", None) is not None:
            self.showNormal()
            self.setGeometry(self._pre_borderless_geom)
        else:
            self.showNormal()

    def _on_toggle_fullscreen(self) -> None:
        """Toggle normal vs fullscreen display (F12)."""
        if getattr(self, "_in_presentation_mode", False):
            self._pre_presentation_was_fullscreen = False
            self._on_toggle_presentation_mode()
            return
        if self.isFullScreen():
            self._exit_borderless_fullscreen()
        else:
            self._enter_borderless_fullscreen()

    def _on_toggle_presentation_mode(self) -> None:
        """Toggle distraction-free presentation mode."""
        is_present = getattr(self, "_in_presentation_mode", False)
        if not is_present:
            self._pre_presentation_was_fullscreen = self.isFullScreen()
            self._in_presentation_mode = True
            self.viewport.set_presentation_mode(True)
            self._pre_presentation_maximized = self.isMaximized()
            self._pre_presentation_markup_palette = self.markup_palette.isVisible()
            self._pre_presentation_sources_dock = (
                hasattr(self, "sources_dock") and not self.sources_dock.isHidden()
            )
            self._pre_presentation_file_browser_dock = (
                hasattr(self, "file_browser_dock") and not self.file_browser_dock.isHidden()
            )
            self._pre_presentation_comp_qc_dock = (
                hasattr(self, "comp_qc_dock") and not self.comp_qc_dock.isHidden()
            )
            self.menuBar().setVisible(False)
            self.top_bar.setVisible(False)
            self.markup_palette.setVisible(False)
            if hasattr(self, "sources_dock"):
                self.sources_dock.setVisible(False)
            if hasattr(self, "file_browser_dock"):
                self.file_browser_dock.setVisible(False)
            if hasattr(self, "comp_qc_dock"):
                self.comp_qc_dock.setVisible(False)
            if hasattr(self, "top_toolbar"):
                self.top_toolbar.setVisible(False)
            if hasattr(self, "markup_toolbar"):
                self.markup_toolbar.setVisible(False)
            self._pre_presentation_huds = [c for c in self._get_all_hud_cards() if c.isVisible()]
            for c in self._pre_presentation_huds:
                c.setVisible(False)
            if self.timeline.magnified_popup and self.timeline.magnified_popup.isVisible():
                self._pre_presentation_magnifier = True
                self.timeline.magnified_popup.setVisible(False)
            else:
                self._pre_presentation_magnifier = False
            self.timeline.setVisible(False)
            if hasattr(self, "timeline_dock"):
                self.timeline_dock.setVisible(False)
            self.statusBar().setVisible(False)
            self._enter_borderless_fullscreen()
        else:
            self._in_presentation_mode = False
            self.viewport.set_presentation_mode(False)
            self.menuBar().setVisible(True)
            self.top_bar.setVisible(True)
            if getattr(self, "_pre_presentation_markup_palette", False):
                self.markup_palette.setVisible(True)
            if getattr(self, "_pre_presentation_sources_dock", False) and hasattr(self, "sources_dock"):
                self.sources_dock.setVisible(True)
            if getattr(self, "_pre_presentation_file_browser_dock", False) and hasattr(self, "file_browser_dock"):
                self.file_browser_dock.setVisible(True)
            if getattr(self, "_pre_presentation_comp_qc_dock", False) and hasattr(self, "comp_qc_dock"):
                self.comp_qc_dock.setVisible(True)
            if hasattr(self, "top_toolbar"):
                self.top_toolbar.setVisible(True)
            if hasattr(self, "markup_toolbar"):
                self.markup_toolbar.setVisible(True)
            for c in getattr(self, "_pre_presentation_huds", []):
                c.setVisible(True)
            if getattr(self, "_pre_presentation_magnifier", False):
                if self.timeline.magnified_popup:
                    self.timeline.magnified_popup.setVisible(True)
                    self.timeline.magnified_popup.auto_center_in_parent()
                    self.timeline.magnified_popup.raise_()
            if hasattr(self, "timeline_dock"):
                self.timeline_dock.setVisible(True)
            self.timeline.setVisible(True)
            self.statusBar().setVisible(True)
            if not getattr(self, "_pre_presentation_was_fullscreen", False):
                self._exit_borderless_fullscreen()
            else:
                self._apply_windows_fullscreen_taskbar_hint(True)

    def _on_toggle_premultiplied_alpha(self, checked: bool) -> None:
        """Toggle canvas alpha compositing between Premultiplied (GL_ONE) and Straight (GL_SRC_ALPHA)."""
        self.viewport.set_premultiplied_alpha(checked)
        if hasattr(self, "session_graph"):
            viewable = self.session_graph.get_active_viewable()
            from openreview.models.session_graph import SourceItem
            if isinstance(viewable, SourceItem):
                viewable.premultiplied = checked
                self.session_graph.source_properties_changed.emit(viewable.id)
        if hasattr(self, "sources_dock") and hasattr(self.sources_dock, "panel") and hasattr(self.sources_dock.panel, "chk_premultiplied"):
            self.sources_dock.panel.chk_premultiplied.blockSignals(True)
            self.sources_dock.panel.chk_premultiplied.setChecked(checked)
            self.sources_dock.panel.chk_premultiplied.blockSignals(False)
        self.statusBar().showMessage(f"Alpha Compositing: {'Premultiplied (GL_ONE)' if checked else 'Straight (GL_SRC_ALPHA)'}", 3000)

    def _on_toggle_texture_filtering(self) -> None:
        """Toggle texture filtering between Linear and Nearest (Shift+N)."""
        self.viewport.toggle_texture_filtering()
        mode_name = "Nearest (Pixelated)" if self.viewport.texture_filtering == "nearest" else "Linear (Smooth)"
        self.statusBar().showMessage(f"Texture Filtering: {mode_name}", 2500)

    def _on_cycle_matte_ratio(self) -> None:
        """Cycle through matte overlay modes (Ctrl+M)."""
        presets = [
            ("No Matte", None),
            ("1.33:1 (4:3)", 1.333),
            ("1.66:1", 1.666),
            ("1.77:1 (16:9)", 1.777),
            ("1.85:1 (DCI Flat)", 1.85),
            ("2.35:1 (Cinemascope)", 2.35),
            ("2.40:1", 2.40),
        ]
        curr = self.viewport.matte_ratio
        curr_idx = 0
        if curr is not None:
            for idx, (_, r) in enumerate(presets):
                if r is not None and abs(curr - r) < 0.02:
                    curr_idx = idx
                    break
        next_idx = (curr_idx + 1) % len(presets)
        label, next_ratio = presets[next_idx]
        self.viewport.set_matte(next_ratio)
        self.statusBar().showMessage(f"Matte Overlay: {label}", 2500)

    def _on_cycle_matte_opacity(self) -> None:
        """Cycle through matte opacity levels (Shift+M)."""
        presets = [
            ("25%", 0.25),
            ("35%", 0.35),
            ("50%", 0.50),
            ("100%", 1.0),
        ]
        curr = self.viewport.matte_opacity
        curr_idx = 3  # default 100%
        for idx, (_, op) in enumerate(presets):
            if abs(curr - op) < 0.05:
                curr_idx = idx
                break
        next_idx = (curr_idx + 1) % len(presets)
        label, next_op = presets[next_idx]
        self.viewport.set_matte_opacity(next_op)
        self.statusBar().showMessage(f"Matte Opacity: {label}", 2500)

    def _on_escape_pressed(self) -> None:
        """Escape exits presentation mode first, cancels text note, deselects drawing tool, or exits fullscreen."""
        if getattr(self, "_in_presentation_mode", False):
            self._on_toggle_presentation_mode()
        elif self.viewport.is_typing_active():
            self.viewport.cancel_text_editor()
        elif self.viewport.active_draw_tool is not None:
            self.viewport.set_draw_tool(None)
            self.markup_palette.select_tool(None)
        elif self.isFullScreen():
            self._exit_borderless_fullscreen()

    # --------------------------------------------------------------------------
    # Preferences Handlers
    # --------------------------------------------------------------------------
    def _format_gpu_engine_label(self, engine_mode: str) -> str:
        names = {
            "opengl_core": "Engine: OpenGL Core",
            "async_pbo": "Engine: Async PBO DMA",
            "software_safe": "Engine: Software Safe",
        }
        return names.get(str(engine_mode).lower(), f"Engine: {engine_mode}")

    def _apply_hud_opacity(self, opacity: float) -> None:
        """Apply floating HUD opacity to all active and standby floating HUD cards and markup palette."""
        for card in self._get_all_hud_cards():
            if hasattr(card, "set_hud_opacity"):
                card.set_hud_opacity(opacity)
        if hasattr(self, "markup_palette") and hasattr(self.markup_palette, "set_hud_opacity"):
            self.markup_palette.set_hud_opacity(opacity)

    def _auto_configure_media_colorspace(self, file_path: str, source_item: Optional[SourceItem] = None) -> None:
        """Auto-configure input colorspace based on media metadata if preference is enabled."""
        prefs = self.pref_mgr.prefs
        cm_mode = getattr(prefs, "color_management_mode", "standard")

        if source_item is None and hasattr(self, "session_graph"):
            viewable = self.session_graph.get_active_viewable()
            if isinstance(viewable, SourceItem) and (viewable.path == file_path or not file_path):
                source_item = viewable

        if not getattr(prefs, "auto_detect_input_colorspace", True):
            if cm_mode == "standard":
                in_name = getattr(prefs, "standard_input_space", "linear")
                cs_info = COLORSPACE_BY_NAME.get(in_name.lower(), COLORSPACE_BY_ID[0])
                view_id = self.viewport.view_transform if hasattr(self.viewport, "view_transform") else 1
                if source_item:
                    source_item.input_space = cs_info.id
                    source_item.colorspace = cs_info.name
                self.top_bar.set_color_spaces(cs_info.id, view_id)
            return

        from openreview.core.metadata import extract_media_metadata
        meta = extract_media_metadata(file_path)
        detected_cs = detect_media_input_colorspace(file_path, meta)

        if source_item:
            source_item.input_space = detected_cs.id
            source_item.colorspace = detected_cs.name

        if cm_mode == "standard":
            view_id = getattr(self.viewport, "view_transform", 1)
            if view_id is None:
                view_id = 1
            self.top_bar.set_color_spaces(detected_cs.id, view_id)
            self.statusBar().showMessage(
                f"Input Colorspace: {detected_cs.label} (auto-detected)",
                4000
            )
        else:
            ocio_space = map_standard_to_ocio_space(detected_cs.id, self.ocio_manager)
            if ocio_space:
                if source_item:
                    source_item.ocio_colorspace = ocio_space
                    source_item.colorspace = ocio_space
                self.ocio_manager.input_space = ocio_space
                cb_idx = self.top_bar.input_combo.findText(ocio_space)
                if cb_idx >= 0:
                    self.top_bar.input_combo.blockSignals(True)
                    self.top_bar.input_combo.setCurrentIndex(cb_idx)
                    self.top_bar.input_combo.blockSignals(False)
                    self.top_bar._on_color_transform_changed()
                self.statusBar().showMessage(
                    f"Input Colorspace: {ocio_space} (auto-mapped to OCIO)",
                    4000
                )

    def _update_color_menus(self) -> None:
        """Populate Display Transform and Input Colorspace menus matching active mode."""
        if not hasattr(self, "disp_sub") or not hasattr(self, "input_sub"):
            return
        self.disp_sub.clear()
        self.input_sub.clear()

        cm_mode = getattr(self.pref_mgr.prefs, "color_management_mode", "standard")
        if cm_mode == "standard":
            for cs in STANDARD_COLORSPACES:
                action_d = self.disp_sub.addAction(cs.label)
                action_d.setToolTip(cs.description)
                action_d.triggered.connect(lambda checked=False, i=cs.id: self._on_set_view_transform(i))

                action_i = self.input_sub.addAction(cs.label)
                action_i.setToolTip(cs.description)
                action_i.triggered.connect(lambda checked=False, i=cs.id: self._on_set_input_space(i))
        else:
            views = self.ocio_manager.get_views()
            for idx, vw in enumerate(views):
                self.disp_sub.addAction(vw).triggered.connect(lambda checked=False, i=idx: self._on_set_view_transform(i))
            spaces = self.ocio_manager.get_color_spaces()
            for idx, sp in enumerate(spaces):
                self.input_sub.addAction(sp).triggered.connect(lambda checked=False, i=idx: self._on_set_input_space(i))

    def _on_open_preferences(self) -> None:
        dlg = PreferencesDialog(self.pref_mgr, self, ocio_manager=self.ocio_manager)
        dlg.hud_opacity_changed.connect(self._apply_hud_opacity)
        dlg.exec()

    def _on_open_preferences_tab(self, tab_name: str) -> None:
        dlg = PreferencesDialog(self.pref_mgr, self, ocio_manager=self.ocio_manager)
        dlg.hud_opacity_changed.connect(self._apply_hud_opacity)
        dlg.open_tab(tab_name)
        dlg.exec()

    def _on_open_package_manager(self) -> None:
        dlg = PackageManagerDialog(self.pkg_mgr, self)
        dlg.exec()

    def _on_open_package_manager_updates(self) -> None:
        dlg = PackageManagerDialog(self.pkg_mgr, self)
        dlg.tabs.setCurrentIndex(1)
        dlg.exec()

    def _on_preferences_changed(self, prefs: UserPreferences) -> None:
        if getattr(prefs, "auto_ram_budget", True):
            self.cache.set_auto_memory_budget(True)
            if hasattr(self, "auto_budget_action"):
                self.auto_budget_action.setChecked(True)
        else:
            target_mb = prefs.lookahead_cache_gb * 1024.0
            self.cache.set_max_memory_mb(target_mb, auto=False)
            if hasattr(self, "budget_actions") and target_mb in self.budget_actions:
                self.budget_actions[target_mb].setChecked(True)
        new_default = getattr(prefs, "default_cache_mode", None)
        if new_default and getattr(self, "_last_pref_default_cache_mode", None) != new_default:
            self._last_pref_default_cache_mode = new_default
            self._on_set_cache_mode(CacheMode(new_default))
        buf_size = getattr(prefs, "prefetch_buffer_size", 48)
        behind_pct = getattr(prefs, "lookahead_behind_pct", 20.0)
        self.cache.set_prefetch_range(
            lookahead=buf_size,
            lookbehind=int(buf_size * (behind_pct / 100.0)),
        )
        scrub_en = getattr(prefs, "canvas_scrub_enabled", True)
        self.viewport.set_scrub_mode(scrub_en)
        self._apply_hud_opacity(getattr(prefs, "hud_opacity", 0.85))
        self.top_bar.set_scrub_mode(scrub_en)

        # Hot-switch GPU Playback Engine and VSync dynamically
        if hasattr(prefs, "playback_engine"):
            self.viewport.set_playback_engine(prefs.playback_engine)
            if hasattr(self, "gpu_engine_status_label"):
                self.gpu_engine_status_label.setText(self._format_gpu_engine_label(prefs.playback_engine))
        if hasattr(prefs, "vsync_enabled"):
            self.viewport.set_vsync_enabled(prefs.vsync_enabled)

        # Update Color Management Mode
        cm_mode = getattr(prefs, "color_management_mode", "standard")
        std_view = getattr(prefs, "standard_view_transform", "sRGB")
        std_input = getattr(prefs, "standard_input_space", "linear")
        self.top_bar.set_color_management_mode(cm_mode, std_view, std_input)
        if hasattr(self, "sources_dock") and self.sources_dock:
            self.sources_dock.panel.set_color_management_mode(cm_mode, self.ocio_manager.get_color_spaces() if cm_mode == "ocio" else None)
        self._update_color_menus()

        self.shortcut_mgr.load_from_preferences()
        self.viewport.update()

    # --------------------------------------------------------------------------
    # Session Management (.ovr)
    # --------------------------------------------------------------------------
    def _build_current_session(self) -> OpenReviewSession:
        cur_vid = getattr(self, "_current_active_view_id", None)
        cur_path = getattr(self, "_current_media_path", None)
        if hasattr(self, "per_media_annotations") and hasattr(self, "annotation_store"):
            if cur_vid:
                self.per_media_annotations.stash(cur_vid, self.annotation_store)
            if cur_path:
                self.per_media_annotations.stash(cur_path, self.annotation_store)

        return OpenReviewSession(
            media_path=self._current_media_path,
            media_type=self._current_media_type,
            start_frame=self.clock.start_frame,
            end_frame=self.clock.end_frame,
            in_frame=self.clock.in_frame,
            out_frame=self.clock.out_frame,
            current_frame=self.clock.current_frame,
            fps=self.clock.fps,
            loop_mode=self.clock.loop_mode,
            bookmarks=list(self.clock.bookmarks),
            background_mode=self.viewport.background_mode,
            viewport=ViewportSessionState(
                zoom=self.viewport.zoom,
                pan_x=self.viewport.pan_x,
                pan_y=self.viewport.pan_y,
                flip_x=self.viewport.flip_x,
                flip_y=self.viewport.flip_y,
                rotation=self.viewport.rotation,
                pixel_aspect_ratio=self.viewport.pixel_aspect_ratio,
                texture_filtering=self.viewport.texture_filtering,
                downsample_ratio=self.viewport.downsample_ratio,
            ),
            color=ColorSessionState(
                exposure_ev=self.top_bar.exposure_ev,
                gamma=self.top_bar.gamma,
                channel_mode=self.top_bar.channel_mode,
                input_space=self.viewport.input_space,
                view_transform=self.viewport.view_transform,
            ),
            matte=MatteSessionState(
                ratio=self.viewport.matte_ratio,
                opacity=self.viewport.matte_opacity,
            ),
            annotations=self.annotation_store.to_dict(),
            per_media_annotations=self.per_media_annotations.to_dict() if hasattr(self, "per_media_annotations") else {},
            session_graph=self.session_graph.to_dict() if hasattr(self, "session_graph") else {},
        )

    def _apply_session(self, sess: OpenReviewSession) -> None:
        if getattr(sess, "session_graph", None) and hasattr(self, "session_graph"):
            self.session_graph.load_from_dict(sess.session_graph)
            self._source_readers.clear()
            if hasattr(self, "sources_dock"):
                self.sources_dock.panel.refresh_tree()

        if sess.media_path and os.path.exists(sess.media_path):
            self._load_path(sess.media_path)

        self.clock.set_fps(sess.fps)
        self.clock.set_range(sess.start_frame, sess.end_frame, reset_in_out=False)
        self.clock.set_in_out(sess.in_frame, sess.out_frame)
        self.clock.set_loop_mode(sess.loop_mode)
        self.clock.set_bookmarks(sess.bookmarks)
        self.clock.set_frame(sess.current_frame)

        # Viewport transforms
        vp = sess.viewport
        self.viewport.set_flip_x(vp.flip_x)
        self.viewport.set_flip_y(vp.flip_y)
        self.viewport.set_rotation(vp.rotation)
        self.viewport.set_pixel_aspect_ratio(vp.pixel_aspect_ratio)
        self.viewport.set_texture_filtering(vp.texture_filtering)
        self.viewport.set_downsample_ratio(vp.downsample_ratio)
        self.viewport.set_pan(vp.pan_x, vp.pan_y)
        self.viewport.set_zoom(vp.zoom)
        self.viewport.set_background_mode(sess.background_mode)

        # Color
        col = sess.color
        self.top_bar.set_exposure(col.exposure_ev)
        self.top_bar.set_gamma(col.gamma)
        self.top_bar.set_channel_mode(col.channel_mode)
        self.top_bar.set_color_spaces(col.input_space, col.view_transform)

        # Matte
        self.viewport.set_matte(sess.matte.ratio, sess.matte.opacity)

        # Annotations
        if getattr(sess, "per_media_annotations", None) and hasattr(self, "per_media_annotations"):
            self.per_media_annotations.from_dict(sess.per_media_annotations)
        self.annotation_store.from_dict(sess.annotations)
        self.viewport.update()
        self.timeline.update()

    def _on_new_session(self) -> None:
        reply = QMessageBox.question(
            self,
            "New Session",
            "Create a new session? Unsaved changes will be cleared.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self._perform_atomic_session_reset()
            self.statusBar().showMessage("New session created", 4000)

    def _on_save_session(self) -> None:
        if self._current_session_file:
            self._save_session_to_path(self._current_session_file)
        else:
            self._on_save_session_as()

    def _on_save_session_as(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Save OpenReview Session",
            "",
            "OpenReview Session (*.ovr);;All Files (*.*)",
        )
        if path:
            if not path.lower().endswith(".ovr"):
                path += ".ovr"
            self._save_session_to_path(Path(path))

    def _save_session_to_path(self, filepath: Path) -> None:
        try:
            sess = self._build_current_session()
            sess.save_to_file(filepath)
            self._current_session_file = filepath
            self._update_window_title()
            self.statusBar().showMessage(f"Session saved to {filepath.name}", 4000)
        except Exception as e:
            QMessageBox.critical(self, "Save Error", f"Failed to save session: {e}")

    def _on_open_session(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Open OpenReview Session",
            "",
            "OpenReview Session (*.ovr);;All Files (*.*)",
        )
        if path:
            self._open_session_file(path)

    def _open_session_file(self, path: str) -> None:
        """Load an OpenReview session file (.ovr or .json)."""
        if not path or not os.path.exists(path):
            return
        try:
            sess = OpenReviewSession.load_from_file(path)
            self._apply_session(sess)
            self._current_session_file = Path(path)
            self._update_window_title()
            self.pref_mgr.add_recent_file(str(path))
            self._update_recent_menu()
            self.statusBar().showMessage(f"Loaded Session: {Path(path).name}", 5000)
        except Exception as e:
            QMessageBox.critical(self, "Load Error", f"Failed to load session: {e}")

    def load_initial_file(self, file_path: str) -> None:
        """Load a file passed from CLI or OS explorer file association."""
        if not file_path or not os.path.exists(file_path):
            return
        p = str(Path(file_path).resolve())
        if p.lower().endswith((".ovr", ".json")):
            self._open_session_file(p)
        else:
            self._load_path(p)

    # --------------------------------------------------------------------------
    # Export Pipeline
    # --------------------------------------------------------------------------
    def _on_export_media(self) -> None:
        # Determine source native resolution
        in_w, in_h = 1920, 1080
        if self.reader and getattr(self.reader, "width", 0) > 0 and getattr(self.reader, "height", 0) > 0:
            in_w, in_h = self.reader.width, self.reader.height
        elif self.viewport._image_a is not None:
            in_h, in_w = self.viewport._image_a.shape[:2]

        def _raw_reader(f: int) -> Optional[np.ndarray]:
            raw = None
            if getattr(self, "_is_exr_contact_sheet_active", False):
                viewable = self.session_graph.get_active_viewable() if hasattr(self, "session_graph") else None
                if self._is_current_media_exr(viewable, f):
                    comp, _ = self._composite_current_exr_contact_sheet(viewable, f)
                    return comp

            if hasattr(self, "session_graph"):
                viewable = self.session_graph.get_active_viewable()
                if isinstance(viewable, SourceItem):
                    raw = self.cache.get_source_frame(viewable.id, f)
                    if raw is None and self.reader:
                        raw = self.reader.read_frame(f)
                elif isinstance(viewable, SequenceView):
                    src, sf = viewable.resolve_frame(f, self.session_graph.sources)
                    if src:
                        raw = self.cache.get_source_frame(src.id, sf)
                        if raw is None:
                            r = self._get_reader_for_source(src)
                            raw = r.read_frame(sf) if r else None
            if raw is None and self.reader:
                raw = self.reader.read_frame(f)
            if raw is None and self.viewport._image_a is not None:
                raw = self.viewport._image_a
            if isinstance(raw, (tuple, list)):
                raw = self._resolve_frame_from_tuple(raw)
            return raw

        dlg = ExportDialog(
            clock=self.clock,
            marked_frames=list(self.clock.bookmarks),
            annotated_frames=self.annotation_store.get_annotated_frames(),
            input_resolution=(in_w, in_h),
            raw_frame_reader=_raw_reader,
            annotation_getter=self.annotation_store.get_strokes,
            viewport=self.viewport,
            parent=self,
        )
        dlg.export_started.connect(self._run_export)
        dlg.exec()

    def _run_export(self, export_data: dict) -> None:
        fmt = export_data.get("format", "")
        dest = export_data["destination"]
        frames = export_data["frames"]
        burn_in = export_data.get("burn_in", True)
        fps = export_data.get("fps", self.clock.fps)
        downscale_gt_fhd = export_data.get(
            "downscale_gt_fhd",
            getattr(self.pref_mgr.prefs, "export_downscale_greater_than_fhd", True) if hasattr(self, "pref_mgr") else True,
        )
        is_custom_res = export_data.get("is_custom_res", False)

        def _frame_provider(frame_num: int) -> np.ndarray:
            if burn_in:
                arr = self._render_frame_for_export(frame_num)
            else:
                raw = self.reader.read_frame(frame_num) if self.reader else None
                if isinstance(raw, (tuple, list)):
                    raw = self._resolve_frame_from_tuple(raw)
                if raw is None:
                    arr = np.zeros((1080, 1920, 3), dtype=np.uint8)
                elif raw.dtype in (np.float32, np.float64):
                    arr = np.clip(raw[:, :, :3] * 255.0, 0, 255).astype(np.uint8)
                else:
                    arr = raw[:, :, :3].astype(np.uint8)

            if not is_custom_res and downscale_gt_fhd and (arr.shape[1] > 1920 or arr.shape[0] > 1080):
                arr = MediaExporter.resize_frame(arr, max(1, arr.shape[1] // 2), max(1, arr.shape[0] // 2))

            return arr

        try:
            if fmt == "single":
                img = _frame_provider(frames[0] if frames else self.clock.current_frame)
                MediaExporter.save_image_file(
                    img,
                    dest,
                    compression=export_data.get("compression", "4"),
                    bit_depth=export_data.get("bit_depth", "8"),
                    quality=int(export_data.get("quality", 95)),
                    dither=bool(export_data.get("dither", True)),
                    png_mode=export_data.get("png_mode", "png8"),
                )
                self.statusBar().showMessage(f"Exported current frame to {dest}", 5000)
            elif fmt in ("mov", "mp4"):
                codec = export_data.get("codec", "Apple ProRes 422 HQ")
                MediaExporter.export_video_ffmpeg(_frame_provider, frames, dest, fps=fps, codec=codec)
                self.statusBar().showMessage(f"Exported video ({len(frames)} frames) to {dest}", 5000)
            else:
                ext = export_data.get("ext", "png")
                prefix = export_data.get("prefix", "frame")
                pad = int(export_data.get("pad", 4))
                bd = export_data.get("bit_depth", "8")
                comp = export_data.get("compression", "4")
                quality = int(export_data.get("quality", 95))
                dither = bool(export_data.get("dither", True))
                png_mode = export_data.get("png_mode", "png8")
                MediaExporter.export_sequence(
                    _frame_provider,
                    frames,
                    dest,
                    filename_prefix=prefix,
                    file_ext=ext,
                    pad=pad,
                    compression=comp,
                    bit_depth=bd,
                    quality=quality,
                    dither=dither,
                    png_mode=png_mode,
                )
                self.statusBar().showMessage(f"Exported sequence ({len(frames)} frames) to {dest}", 5000)
        except Exception as e:
            QMessageBox.critical(self, "Export Failed", f"Export error: {e}")

    # --------------------------------------------------------------------------
    # Color & LUT Engine Handlers
    # --------------------------------------------------------------------------
    def _on_top_bar_color_transforms_changed(self, in_idx: int, view_idx: int) -> None:
        """Handle color transform changes from top bar, syncing to viewport and active source."""
        self.viewport.set_color_transforms(in_idx, view_idx)
        if getattr(self, "_suppress_colorspace_sync", False):
            return

        viewable = self.session_graph.get_active_viewable()
        if isinstance(viewable, SourceItem):
            viewable.input_space = in_idx
            cm_mode = getattr(self.pref_mgr.prefs, "color_management_mode", "standard")
            if cm_mode == "standard":
                cs_info = COLORSPACE_BY_ID.get(in_idx)
                if cs_info:
                    viewable.colorspace = cs_info.name
            else:
                ocio_text = self.top_bar.input_combo.currentText()
                viewable.ocio_colorspace = ocio_text
                viewable.colorspace = ocio_text

            self._suppress_colorspace_sync = True
            try:
                self.session_graph.source_properties_changed.emit(viewable.id)
                if hasattr(self, "sources_dock") and self.sources_dock:
                    self.sources_dock.panel.refresh_properties()
            finally:
                self._suppress_colorspace_sync = False

    def _on_source_properties_changed(self, source_id: str) -> None:
        """Handle source property changes, synchronizing top bar if active source updated."""
        if getattr(self, "_suppress_colorspace_sync", False):
            return
        if self.session_graph.active_view_id == source_id and self.session_graph.active_view_type == ViewType.SOURCE:
            source = self.session_graph.sources.get(source_id)
            if source:
                cm_mode = getattr(self.pref_mgr.prefs, "color_management_mode", "standard")
                self._suppress_colorspace_sync = True
                try:
                    if cm_mode == "standard":
                        cur_view = getattr(self.viewport, "view_transform", 1)
                        if cur_view is None:
                            cur_view = 1
                        self.top_bar.set_color_spaces(source.input_space, cur_view)
                        self.viewport.set_color_transforms(source.input_space, cur_view)
                    else:
                        if source.ocio_colorspace:
                            self.ocio_manager.input_space = source.ocio_colorspace
                            cb_idx = self.top_bar.input_combo.findText(source.ocio_colorspace)
                            if cb_idx >= 0:
                                self.top_bar.input_combo.blockSignals(True)
                                self.top_bar.input_combo.setCurrentIndex(cb_idx)
                                self.top_bar.input_combo.blockSignals(False)
                        self.viewport.update()
                finally:
                    self._suppress_colorspace_sync = False

    def _on_set_input_space(self, idx: int) -> None:
        self.top_bar.set_color_spaces(idx, self.viewport.view_transform)
        item_text = self.top_bar.input_combo.itemText(idx)
        self.statusBar().showMessage(f"Input Colorspace: {item_text}", 3000)

    def _on_set_view_transform(self, idx: int) -> None:
        self.top_bar.set_color_spaces(self.viewport.input_space, idx)
        item_text = self.top_bar.view_combo.itemText(idx)
        self.statusBar().showMessage(f"Display View Transform: {item_text}", 3000)

    def _on_toggle_out_of_range(self, checked: bool) -> None:
        self.viewport.set_show_out_of_range(checked)
        state_str = "Enabled (Magenta/Cyan)" if checked else "Disabled"
        self.statusBar().showMessage(f"Out-of-Range Highlight: {state_str}", 3000)

    def _on_toggle_invert_colors(self, checked: bool) -> None:
        self.viewport.set_invert_colors(checked)
        self.statusBar().showMessage(f"Invert Colors: {'On' if checked else 'Off'}", 3000)

    def _on_toggle_lut_enabled(self, checked: bool) -> None:
        self.viewport.set_lut_enabled(checked)
        self.statusBar().showMessage(f"3D LUT: {'Enabled' if checked else 'Bypassed'}", 3000)

    def _on_toggle_cdl_enabled(self, checked: bool) -> None:
        self.viewport.set_cdl_enabled(checked)
        self.statusBar().showMessage(f"ASC CDL: {'Enabled' if checked else 'Bypassed'}", 3000)

    def _on_load_file_lut(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Load 3D or 1D Cube LUT", "", "LUT Files (*.cube *.3dl);;All Files (*.*)"
        )
        if path:
            try:
                lut = CubeLut.from_file(path)
                self.viewport.set_lut(lut)
                self.statusBar().showMessage(f"Loaded LUT: {os.path.basename(path)} (Size {lut.size_3d}^3)", 4000)
            except Exception as e:
                QMessageBox.warning(self, "LUT Load Error", f"Failed to load LUT:\n{e}")

    def _on_load_file_cdl(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Load ASC CDL", "", "CDL Files (*.cdl *.cc *.ccc *.xml);;All Files (*.*)"
        )
        if path:
            try:
                cdl = AscCdl.from_file(path)
                self.viewport.set_cdl(cdl)
                self.statusBar().showMessage(f"Loaded ASC CDL: {os.path.basename(path)}", 4000)
            except Exception as e:
                QMessageBox.warning(self, "CDL Load Error", f"Failed to load CDL:\n{e}")

    def _on_clear_lut_and_cdl(self) -> None:
        self.viewport.set_lut(None)
        self.viewport.set_cdl(None)
        self.statusBar().showMessage("Cleared active LUT and CDL.", 3000)

    def _on_open_color_adjustments(self) -> None:
        """Toggle Interactive Color Adjustments panel (/ or menu)."""
        vis = self.hud_color_adj.isHidden()
        self.hud_color_adj.setVisible(vis)
        if vis:
            self.hud_color_adj.raise_()
        self.statusBar().showMessage(f"Color Adjustments Panel: {'Shown' if vis else 'Hidden'}", 2000)

    # --------------------------------------------------------------------------
    # Review Tools & Diagnostics Handlers
    # --------------------------------------------------------------------------
    def _on_set_view_type(self, mode: ComparisonMode) -> None:
        self.comparison_state.mode = mode
        self.viewport.comparison_state = self.comparison_state
        self.viewport.update()
        names = {
            ComparisonMode.SEQUENCE: "Sequence",
            ComparisonMode.REPLACE: "Replace (Single)",
            ComparisonMode.OVER: "Over (Alpha Composite)",
            ComparisonMode.ADD: "Add",
            ComparisonMode.DIFFERENCE: "Difference",
            ComparisonMode.DIFFERENCE_INVERTED: "Difference (Inverted)",
        }
        self.statusBar().showMessage(f"Review View Type: {names.get(mode, 'Replace')}", 3000)

    def _on_toggle_color_inspector(self) -> None:
        vis = self.hud_inspector.isHidden()
        self.hud_inspector.setVisible(vis)
        if vis:
            self.hud_inspector.adjustSize()
            self.hud_inspector.raise_()
        else:
            self.viewport.set_eyedropper_mode(False)
        self.statusBar().showMessage(f"Color Inspector HUD: {'Shown' if vis else 'Hidden'}", 2000)

    def _on_toggle_info_strip(self) -> None:
        self.viewport.toggle_info_strip()
        if hasattr(self, "info_strip_act"):
            self.info_strip_act.setChecked(self.viewport.show_info_strip)
        self.statusBar().showMessage(f"Info Strip: {'Shown' if self.viewport.show_info_strip else 'Hidden'}", 2000)

    def _refresh_active_source_details(self) -> None:
        """Update floating details HUD, Viewport info strip, and subpanels for current active source."""
        active_src = None
        if hasattr(self, "session_graph"):
            viewable = self.session_graph.get_active_viewable()
            if isinstance(viewable, StackView) and viewable.source_ids:
                if hasattr(self, "sources_dock") and hasattr(self.sources_dock, "panel"):
                    row = self.sources_dock.panel.list_inputs.currentRow()
                    if 0 <= row < len(viewable.source_ids):
                        sid = viewable.source_ids[row]
                        active_src = self.session_graph.sources.get(sid)
                if not active_src:
                    if viewable.compare_mode == "switch":
                        active_src = viewable.get_current_layer(self.session_graph.sources)
                    else:
                        active_src = viewable.get_layer_a(self.session_graph.sources)
                if not active_src and viewable.source_ids:
                    active_src = self.session_graph.sources.get(viewable.source_ids[0])
            elif isinstance(viewable, SourceItem):
                active_src = viewable

            if active_src:
                self._current_media_path = active_src.path
                self._current_media_type = active_src.media_type
                is_premult = getattr(active_src, "premultiplied", True)
                self.viewport.set_premultiplied_alpha(is_premult)
                if hasattr(self, "premult_act"):
                    self.premult_act.setChecked(is_premult)
                if hasattr(self.viewport, "info_strip_data"):
                    self.viewport.info_strip_data["media_name"] = active_src.name
                    self.viewport.info_strip_data["file_path"] = active_src.path
                if active_src.width and active_src.height:
                    self.viewport.set_media_resolution(active_src.width, active_src.height)

        if hasattr(self, "hud_source_details") and not self.hud_source_details.isHidden():
            self.source_details_widget.update_info(self._get_current_source_info())
        if hasattr(self, "viewport"):
            self.viewport.update()

    def _get_current_source_info(self) -> dict:
        from openreview.core.metadata import extract_media_metadata
        from openreview.models.session_graph import SourceItem, StackView

        media_path = self._current_media_path
        media_type = self._current_media_type
        active_src = None

        # If inspecting, check active viewable / stack / selected source in session graph
        if hasattr(self, "session_graph"):
            viewable = self.session_graph.get_active_viewable()
            if isinstance(viewable, StackView) and viewable.source_ids:
                selected_input_src = None
                if hasattr(self, "sources_dock") and hasattr(self.sources_dock, "panel"):
                    row = self.sources_dock.panel.list_inputs.currentRow()
                    if 0 <= row < len(viewable.source_ids):
                        sid = viewable.source_ids[row]
                        selected_input_src = self.session_graph.sources.get(sid)

                if selected_input_src:
                    active_src = selected_input_src
                elif viewable.compare_mode == "switch":
                    active_src = viewable.get_current_layer(self.session_graph.sources)
                else:
                    active_src = viewable.get_layer_a(self.session_graph.sources)

                if not active_src and viewable.source_ids:
                    active_src = self.session_graph.sources.get(viewable.source_ids[0])

                if active_src:
                    media_path = active_src.path
                    media_type = active_src.media_type
            elif isinstance(viewable, SourceItem):
                active_src = viewable
                media_path = viewable.path
                media_type = viewable.media_type
            elif not media_path or media_path == "none":
                if hasattr(self, "sources_panel"):
                    selected = self.sources_panel._get_selected_nodes()
                    if selected and isinstance(selected[0], SourceItem):
                        active_src = selected[0]
                        media_path = selected[0].path
                        media_type = selected[0].media_type

        # Resolve frame disk path for image sequence if applicable
        target_path = media_path
        cur_f = getattr(self.clock, "current_frame", 1)
        if active_src and hasattr(self, "_get_source_frame_path"):
            resolved = self._get_source_frame_path(active_src, cur_f)
            if resolved and os.path.exists(resolved):
                target_path = resolved

        # Extract file & stream metadata via ffprobe / PIL / OpenCV / OpenEXR
        extracted = extract_media_metadata(target_path) if target_path else {}

        # Merge with live viewer & timeline properties
        info = dict(extracted)
        info["file_path"] = target_path or media_path or "Synthetic Stream"
        if target_path and media_path and target_path != media_path:
            info["sequence_pattern"] = media_path
            info["file_name"] = os.path.basename(target_path)
        info["media_type"] = media_type.capitalize() if media_type and media_type != "none" else ("Video" if info.get("video_codec") else "Synthetic Stream")

        if not info.get("width"):
            info["width"] = self.viewport._img_width
        if not info.get("height"):
            info["height"] = self.viewport._img_height

        info["pixel_aspect"] = self.viewport.pixel_aspect_ratio
        info["channels"] = "RGBA (4 Channels)"
        info["bit_depth"] = "8-bit Unsigned Byte" if (self.viewport._image_a is not None and self.viewport._image_a.dtype == np.uint8) else "32-bit Float (Single Precision)"
        info["input_space_name"] = self.top_bar.input_combo.currentText()
        info["view_transform_name"] = self.top_bar.view_combo.currentText()
        info["exposure_ev"] = self.viewport.exposure_ev
        info["gamma"] = self.viewport.gamma

        channel_names = ["RGB (All Channels)", "Red Only", "Green Only", "Blue Only", "Alpha Only", "Luminance (Y)"]
        ch_idx = min(len(channel_names) - 1, max(0, self.viewport.channel_mode))
        info["channel_mode_str"] = channel_names[ch_idx]

        info["start_frame"] = self.clock.start_frame
        info["end_frame"] = self.clock.end_frame
        info["current_frame"] = self.clock.current_frame
        info["in_frame"] = self.clock.in_frame
        info["out_frame"] = self.clock.out_frame
        info["fps"] = self.clock.fps

        return info

    def _on_toggle_source_details(self) -> None:
        """Toggle Source Details floating panel (I)."""
        vis = self.hud_source_details.isHidden()
        if vis:
            self.source_details_widget.update_info(self._get_current_source_info())
            self.hud_source_details.show()
            self.hud_source_details.raise_()
            self.hud_source_details.resize(480, 560)
        else:
            self.hud_source_details.close_panel()
        self.statusBar().showMessage(f"Source Details Panel: {'Shown' if vis else 'Hidden'}", 2000)

    def _on_show_source_details(self) -> None:
        """Display Source Details panel."""
        self._on_toggle_source_details()

    def _on_show_image_info(self) -> None:
        """Backward compatibility alias for Source Details."""
        self._on_toggle_source_details()

    @property
    def _source_details_dialog(self):
        return self.hud_source_details if not self.hud_source_details.isHidden() else None

    def _get_all_hud_cards(self) -> list[FloatingHUDCard]:
        cards = [
            self.hud_hist,
            self.hud_waveform,
            self.hud_vectorscope,
            self.hud_wipe,
            self.hud_inspector,
            self.hud_color_adj,
            self.hud_annotations,
            self.hud_source_details,
            self.hud_dailies_sync,
            self.hud_audio,
            self.hud_layers,
            self.hud_stereo,
            self.hud_ocio,
        ]
        if hasattr(self, "hud_comp_qc") and isinstance(self.hud_comp_qc, FloatingHUDCard):
            cards.append(self.hud_comp_qc)
        return cards

    def _on_dock_all_panels_right(self) -> None:
        """Photoshop-style dock: stack visible panels along the right edge of the viewport."""
        cards = [c for c in self._get_all_hud_cards() if c.isVisible()]
        if not cards:
            self.statusBar().showMessage("No panels currently visible to dock", 2000)
            return
        y_offset = 16
        for c in cards:
            c.dock_to_right()
            c.move(c.x(), y_offset)
            y_offset += c.height() + 8
        self.statusBar().showMessage("Docked visible panels to right edge", 2500)

    def _on_float_all_panels(self) -> None:
        """Undock all panels to float freely."""
        for c in self._get_all_hud_cards():
            c._dock_mode = None
            c.dock_btn.setStyleSheet("color: #aaaaaa; background: transparent; border: none;")
        self.statusBar().showMessage("Panels restored to free float", 2000)

    def _on_close_all_panels(self) -> None:
        for c in self._get_all_hud_cards():
            c.hide()
        self.statusBar().showMessage("Closed all floating panels", 2000)

    @property
    def sources_panel(self) -> SourcesPanelWidget:
        return self.sources_dock.panel

    # --------------------------------------------------------------------------
    # Workflow & UI Enhancements: Sources Dock, Version Stepping, Safe Guides, Recent Files
    # --------------------------------------------------------------------------
    def _on_toggle_playlist(self) -> None:
        """Toggle Sources & Session Manager dock panel (`)."""
        if hasattr(self, "sources_dock"):
            vis = not self.sources_dock.isVisible()
            self.sources_dock.setVisible(vis)
            if vis:
                self.sources_dock.raise_()
            self.statusBar().showMessage(f"Sources & Session Manager: {'Shown' if vis else 'Hidden'}", 2000)

    def _on_toggle_file_browser(self) -> None:
        """Toggle Inbuilt File Browser dock panel (Ctrl+B)."""
        if hasattr(self, "file_browser_dock"):
            vis = self.file_browser_dock.isHidden()
            self.file_browser_dock.setVisible(vis)
            if vis:
                if (hasattr(self, "comp_qc_dock") and
                    self.dockWidgetArea(self.comp_qc_dock) == self.dockWidgetArea(self.file_browser_dock) and
                    not self.file_browser_dock.isFloating() and not self.comp_qc_dock.isFloating()):
                    self.tabifyDockWidget(self.comp_qc_dock, self.file_browser_dock)
                self.file_browser_dock.raise_()
            self.statusBar().showMessage(f"File Browser: {'Shown' if vis else 'Hidden'}", 2000)

    def cycle_dock_tab(self, current_dock: QDockWidget, forward: bool = True) -> Optional[QDockWidget]:
        """Cycle to next or previous tab among docked widgets in the same dock area."""
        if not isinstance(current_dock, QDockWidget):
            return None
        area = self.dockWidgetArea(current_dock)
        candidates = [
            d for d in [
                getattr(self, "sources_dock", None),
                getattr(self, "file_browser_dock", None),
                getattr(self, "comp_qc_dock", None),
            ]
            if d is not None and self.dockWidgetArea(d) == area and not d.isFloating()
        ]
        try:
            for d in self.tabifiedDockWidgets(current_dock):
                if d not in candidates and isinstance(d, QDockWidget):
                    candidates.append(d)
        except Exception:
            pass

        # Exclude comp_qc_dock if plugin disabled
        if hasattr(self, "pkg_mgr") and self.pkg_mgr and not self.pkg_mgr.is_plugin_enabled("xtra-qc-Utils"):
            comp_qc = getattr(self, "comp_qc_dock", None)
            if comp_qc in candidates and comp_qc.isHidden():
                candidates.remove(comp_qc)

        visible_docks = [d for d in candidates if not d.isHidden()]
        cycle_list = visible_docks if len(visible_docks) > 1 else candidates

        if len(cycle_list) <= 1:
            return None

        try:
            curr_idx = cycle_list.index(current_dock)
        except ValueError:
            curr_idx = 0

        step = 1 if forward else -1
        target_dock = cycle_list[(curr_idx + step) % len(cycle_list)]
        if target_dock.isHidden():
            target_dock.show()
        target_dock.raise_()
        target_dock.setFocus()
        return target_dock

    def _on_toggle_console_window(self) -> None:
        """Toggle non-modal Console & Error Log window (Ctrl+Shift+L)."""
        if not hasattr(self, "console_window") or self.console_window is None:
            return
        vis = self.console_window.isHidden()
        if vis:
            self.console_window.show()
            self.console_window.raise_()
            self.console_window.activateWindow()
        else:
            self.console_window.hide()
        self._on_console_visibility_changed(vis)
        self.statusBar().showMessage(f"Console Window: {'Shown' if vis else 'Hidden'}", 2000)

    def _on_console_visibility_changed(self, visible: bool) -> None:
        """Sync Window menu check state with ConsoleWindow visibility."""
        if hasattr(self, "act_console_window") and self.act_console_window is not None:
            self.act_console_window.blockSignals(True)
            self.act_console_window.setChecked(bool(visible))
            self.act_console_window.blockSignals(False)

    def _on_console_error_logged(self, entry: LogEntry) -> None:
        """Automatically open ConsoleWindow when an error is logged on app console."""
        if not hasattr(self, "console_window") or self.console_window is None:
            return
        if getattr(self, "_in_presentation_mode", False):
            return
        auto_open = getattr(self.pref_mgr.prefs, "console_auto_open_on_error", True)
        if not auto_open:
            return
        if self.console_window.isHidden():
            self.console_window.show()
        self.console_window.raise_()
        self._on_console_visibility_changed(True)

    def _on_file_browser_add_to_bin(self, paths: list[str]) -> None:
        """Add media files or image sequences to the Source Media Bin."""
        if not paths:
            return
        for p in paths:
            if p and os.path.exists(p):
                self.session_graph.add_source_from_path(p)
        if hasattr(self, "sources_dock"):
            self.sources_dock.panel.refresh_tree()
        self.statusBar().showMessage(f"Added {len(paths)} item(s) to Source Bin", 3000)

    def _on_import_session_file(self, path: str) -> None:
        """Import/load an OpenReview session file (.ovr) into the active session."""
        if not path or not os.path.exists(path):
            return
        try:
            from openreview.models.session import OpenReviewSession
            sess = OpenReviewSession.load_from_file(path)
            self._apply_session(sess)
            self._current_session_file = Path(path)
            self._update_window_title()
            self.statusBar().showMessage(f"Imported Session: {Path(path).name}", 5000)
        except Exception as e:
            QMessageBox.critical(self, "Session Import Error", f"Failed to import session: {e}")

    def _on_launch_session_instance(self, path: str) -> None:
        """Open session in a completely new OpenReview application instance."""
        if not path or not os.path.exists(path):
            return
        try:
            run_script = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))), "run.py")
            if not os.path.exists(run_script):
                run_script = "run.py"
            subprocess.Popen([sys.executable, run_script, path])
            self.statusBar().showMessage(f"Launching new OpenReview instance for: {os.path.basename(path)}", 3000)
        except Exception as e:
            QMessageBox.critical(self, "Launch Error", f"Failed to launch new instance: {e}")

    def _get_reader_for_source(self, source: Optional[SourceItem]) -> Optional[Any]:
        if source is None:
            return None
        if source.id in self._source_readers:
            return self._source_readers[source.id]

        from openreview.core.video_reader import is_video_file, VideoMediaReader
        from openreview.core.sequence_parser import detect_image_sequence, DiskSequenceReader
        from openreview.core.reader import DiskMediaReader, SyntheticPatternReader

        path = source.path
        if not path or path == "synthetic_pattern" or not os.path.exists(path):
            reader = SyntheticPatternReader()
        elif is_video_file(path):
            try:
                reader = VideoMediaReader(path)
            except Exception:
                reader = SyntheticPatternReader()
        else:
            seq_info = detect_image_sequence(path)
            if seq_info and len(seq_info.available_frames) > 1:
                reader = DiskSequenceReader(seq_info)
            else:
                reader = DiskMediaReader(path)

        self._source_readers[source.id] = reader
        return reader

    def _on_sources_media_load_requested(self, paths: list[str]) -> None:
        self._on_files_dropped(paths)

    def _update_window_title(self) -> None:
        """Dynamically update main window title based on saved session and active viewable/media."""
        session_name = self._current_session_file.name if getattr(self, "_current_session_file", None) else None

        viewable = self.session_graph.get_active_viewable() if hasattr(self, "session_graph") else None
        item_details = None

        if viewable is not None:
            if isinstance(viewable, SourceItem):
                item_details = f"{viewable.name} [{viewable.frame_in}-{viewable.frame_out} @ {viewable.fps:.2f} FPS]"
            elif isinstance(viewable, SequenceView):
                item_details = f"Sequence: {viewable.name} [{viewable.start_frame}-{viewable.end_frame}, {len(viewable.clips)} clips]"
            elif isinstance(viewable, StackView):
                item_details = f"Stack: {viewable.name} [{viewable.compare_mode.upper()} mode]"
            elif isinstance(viewable, LayoutView):
                item_details = f"Layout: {viewable.name} [{viewable.layout_type}, {viewable.scale_pct}% scale]"
            else:
                item_details = getattr(viewable, "name", str(viewable))
        elif getattr(self, "_current_media_path", None):
            media_path = self._current_media_path
            media_type = getattr(self, "_current_media_type", "image")
            base = os.path.basename(media_path)
            if media_type == "video" and hasattr(self, "reader") and hasattr(self.reader, "total_frames"):
                vr = self.reader
                item_details = f"{base} [{getattr(vr, 'width', '')}x{getattr(vr, 'height', '')}, {vr.total_frames} frames @ {getattr(vr, 'fps', 24.0):.2f} FPS]"
            elif media_type == "sequence" and hasattr(self, "reader") and hasattr(self.reader, "seq"):
                seq = self.reader.seq
                item_details = f"{seq.prefix}{seq.separator}####{seq.extension} [{seq.start_frame}-{seq.end_frame}]"
            else:
                item_details = base

        if session_name:
            if item_details:
                title = f"OpenReview - {session_name} - {item_details}"
            else:
                title = f"OpenReview - {session_name}"
        else:
            if item_details:
                title = f"OpenReview - {item_details}"
            else:
                title = "OpenReview - Professional VFX Review & Playback [ACEScg / OCIO v2]"

        self.setWindowTitle(title)

    def _on_source_renamed_update_title(self, source_id: Optional[str] = None) -> None:
        """Update main window title if the currently active viewable was renamed."""
        viewable = self.session_graph.get_active_viewable() if hasattr(self, "session_graph") else None
        if not viewable:
            return
        if source_id and getattr(viewable, "id", None) != source_id:
            return
        self._update_window_title()

    def _on_active_view_changed(self, view_type: str, view_id: str) -> None:
        """Handle active view switch between Source, Sequence, Stack, and Layout."""
        viewable = self.session_graph.get_active_viewable()
        if viewable is None:
            return

        if self.clock.is_playing:
            self.clock.pause()

        prev_f = self.clock.current_frame
        prev_start = self.clock.start_frame
        prev_end = self.clock.end_frame
        prev_offset = prev_f - prev_start

        self._suppress_in_out_record = True
        start_f = 1
        end_f = 100

        prev_vid = getattr(self, "_current_active_view_id", None)
        prev_path = getattr(self, "_current_media_path", None)
        if prev_vid and prev_vid != view_id:
            if hasattr(self, "per_media_annotations") and hasattr(self, "annotation_store"):
                self.per_media_annotations.stash(prev_vid, self.annotation_store)
                if prev_path:
                    self.per_media_annotations.stash(prev_path, self.annotation_store)
        self._current_active_view_id = view_id

        if isinstance(viewable, SourceItem):
            self.cache.set_active_source(viewable.id)
            self._current_media_path = viewable.path
            self._current_media_type = viewable.media_type
            self.reader = self._get_reader_for_source(viewable)
            self.reader_engine.set_reader(self.reader)
            self.clock.set_fps(viewable.fps)
            is_cycling = getattr(self, "_is_cycling_stack", False)
            if is_cycling and viewable.media_type == "still" and prev_start < prev_end:
                start_f = prev_start
                end_f = prev_end
            else:
                start_f = viewable.start_frame
                end_f = viewable.end_frame

            if viewable.media_type == "video" and viewable.path and os.path.exists(viewable.path):
                self.audio_engine.load_audio(viewable.path)
            self.audio_engine.set_sequence_context(viewable.frame_in, viewable.fps)
            self.comparison_state.mode = ComparisonMode.OFF
            self.viewport.comparison_state.mode = ComparisonMode.OFF
            self.viewport.set_reference_frame(None)
            # Restore per-clip input colorspace
            self._suppress_colorspace_sync = True
            try:
                cm_mode = getattr(self.pref_mgr.prefs, "color_management_mode", "standard")
                if cm_mode == "standard":
                    in_idx = getattr(viewable, "input_space", None)
                    if in_idx is None:
                        cs_info = COLORSPACE_BY_NAME.get(str(viewable.colorspace).lower(), COLORSPACE_BY_ID[0])
                        in_idx = cs_info.id
                        viewable.input_space = in_idx
                    vw_idx = getattr(self.viewport, "view_transform", 1)
                    if vw_idx is None:
                        vw_idx = 1
                    self.top_bar.set_color_spaces(in_idx, vw_idx)
                    self.viewport.set_color_transforms(in_idx, vw_idx)
                else:
                    ocio_sp = getattr(viewable, "ocio_colorspace", None)
                    if not ocio_sp:
                        ocio_sp = map_standard_to_ocio_space(getattr(viewable, "input_space", 0), self.ocio_manager)
                        viewable.ocio_colorspace = ocio_sp
                    if ocio_sp:
                        self.ocio_manager.input_space = ocio_sp
                        cb_idx = self.top_bar.input_combo.findText(ocio_sp)
                        if cb_idx >= 0:
                            self.top_bar.input_combo.blockSignals(True)
                            self.top_bar.input_combo.setCurrentIndex(cb_idx)
                            self.top_bar.input_combo.blockSignals(False)
                        self.viewport.update()
            finally:
                self._suppress_colorspace_sync = False

            self._update_window_title()
            self.statusBar().showMessage(f"Active View: Source '{viewable.name}' [Input: {viewable.colorspace}]", 3000)

        elif isinstance(viewable, SequenceView):
            self.cache.set_active_source(None)
            self.clock.set_fps(viewable.fps)
            start_f = viewable.start_frame
            end_f = viewable.end_frame
            self.comparison_state.mode = ComparisonMode.OFF
            self.viewport.comparison_state.mode = ComparisonMode.OFF
            self.viewport.set_reference_frame(None)
            from openreview.core.reader import CallableVirtualReader
            def _fetch_seq(f: int):
                src, sf = viewable.resolve_frame(f, self.session_graph.sources)
                if src:
                    d = self.cache.get_source_frame(src.id, sf)
                    if d is not None:
                        return d
                    r = self._get_reader_for_source(src)
                    d = r.read_frame(sf) if r else None
                    if self.cache.mode != CacheMode.OFF and d is not None:
                        self.cache.put_source_frame(src.id, sf, d)
                    return d
                return None
            self.reader = CallableVirtualReader(_fetch_seq)
            self.reader_engine.set_reader(self.reader)
            self._update_window_title()
            self.statusBar().showMessage(f"Active View: Sequence '{viewable.name}' ({len(viewable.clips)} clips)", 3000)

        elif isinstance(viewable, StackView):
            if viewable.compare_mode == "switch":
                comp_mode = ComparisonMode.OFF if (viewable.current_layer_index == viewable.index_b) else ComparisonMode.OVER
            else:
                mode_map = {
                    "wipe": ComparisonMode.WIPE,
                    "switch": ComparisonMode.OVER,
                    "split": ComparisonMode.SPLIT,
                    "difference": ComparisonMode.DIFFERENCE,
                    "difference_inverted": ComparisonMode.DIFFERENCE_INVERTED,
                    "over": ComparisonMode.OVER,
                    "add": ComparisonMode.ADD,
                }
                comp_mode = mode_map.get(viewable.compare_mode, ComparisonMode.OVER)
            self.comparison_state.mode = comp_mode
            self.viewport.comparison_state.mode = comp_mode
            self.viewport.comparison_state.wipe_position = viewable.wipe_position
            self.viewport.comparison_state.wipe_angle_deg = viewable.wipe_angle_deg
            self.viewport.comparison_state.diff_boost = viewable.diff_boost
            self.viewport.comparison_state.stack_mix = getattr(viewable, "stack_mix", 1.0)
            self.comparison_state.stack_mix = getattr(viewable, "stack_mix", 1.0)
            wc = getattr(viewable, "wipe_center", (0.5, 0.5))
            self.viewport.comparison_state.wipe_center = wc
            self.comparison_state.wipe_center = wc

            if viewable.compare_mode == "switch" and viewable.current_layer_index == viewable.index_b:
                self.viewport.comparison_state.channels_a = (True, True, True, True)
                self.comparison_state.channels_a = (True, True, True, True)
            else:
                self.viewport.comparison_state.channels_a = getattr(viewable, "channels_a", (True, True, True, True))
                self.comparison_state.channels_a = getattr(viewable, "channels_a", (True, True, True, True))

            cur = viewable.get_current_layer(self.session_graph.sources)
            self.cache.set_active_source(cur.id if cur else None)
    
            sources_list = [self.session_graph.sources[sid] for sid in viewable.source_ids if sid in self.session_graph.sources]
            if sources_list:
                start_f = min(s.start_frame for s in sources_list)
                end_f = max(s.end_frame for s in sources_list)
            else:
                start_f, end_f = 1, 100

            from openreview.core.reader import CallableVirtualReader
            def _fetch_stack(f: int):
                if self.viewport.comparison_state.mode != ComparisonMode.OFF:
                    la = viewable.get_layer_a(self.session_graph.sources)
                    lb = viewable.get_layer_b(self.session_graph.sources)
                    da = None
                    db = None
                    if la:
                        da = self.cache.get_source_frame(la.id, f)
                        if da is None:
                            ra = self._get_reader_for_source(la)
                            da = ra.read_frame(f) if ra else None
                            if self.cache.mode != CacheMode.OFF and da is not None:
                                self.cache.put_source_frame(la.id, f, da)
                    if lb:
                        db = self.cache.get_source_frame(lb.id, f)
                        if db is None:
                            rb = self._get_reader_for_source(lb)
                            db = rb.read_frame(f) if rb else None
                            if self.cache.mode != CacheMode.OFF and db is not None:
                                self.cache.put_source_frame(lb.id, f, db)
                    return (da, db)
                else:
                    cur_layer = viewable.get_current_layer(self.session_graph.sources)
                    if cur_layer:
                        d = self.cache.get_source_frame(cur_layer.id, f)
                        if d is not None:
                            return d
                        r = self._get_reader_for_source(cur_layer)
                        d = r.read_frame(f) if r else None
                        if self.cache.mode != CacheMode.OFF and d is not None:
                            self.cache.put_source_frame(cur_layer.id, f, d)
                        return d
                    return None
            self.reader = CallableVirtualReader(_fetch_stack)
            self.reader_engine.set_reader(self.reader)

            self._update_window_title()
            self.statusBar().showMessage(f"Active View: Stack '{viewable.name}' ({viewable.compare_mode.upper()})", 3000)

        elif isinstance(viewable, LayoutView):
            self.comparison_state.mode = ComparisonMode.OFF
            self.viewport.comparison_state.mode = ComparisonMode.OFF
            self.viewport.set_reference_frame(None)
            self.cache.set_active_source(None)

            start_f, end_f = viewable.get_timeline_range(self.session_graph.sources)

            from openreview.core.reader import CallableVirtualReader
            def _fetch_layout(f: int):
                def _fetch_tile(src: SourceItem):
                    sf = viewable.resolve_tile_frame(src, f, self.session_graph.sources)
                    d = self.cache.get_source_frame(src.id, sf)
                    if d is not None:
                        return d
                    r = self._get_reader_for_source(src)
                    d = r.read_frame(sf) if r else None
                    if self.cache.mode != CacheMode.OFF and d is not None:
                        self.cache.put_source_frame(src.id, sf, d)
                    return d
                return composite_layout_grid(
                    viewable,
                    self.session_graph.sources,
                    _fetch_tile,
                )
            self.reader = CallableVirtualReader(_fetch_layout)
            self.reader_engine.set_reader(self.reader)

            self._update_window_title()
            self.statusBar().showMessage(f"Active View: Layout '{viewable.name}' ({viewable.scale_pct}% scale)", 3000)

        # Set range & restore or auto-expand In/Out marks
        if viewable.manual_in is not None and viewable.manual_out is not None:
            self.clock.set_range(start_f, end_f, reset_in_out=False)
            self.clock.set_in_out(viewable.manual_in, viewable.manual_out)
        else:
            self.clock.set_range(start_f, end_f, reset_in_out=True)

        is_cycling = getattr(self, "_is_cycling_stack", False)
        if is_cycling:
            if start_f <= prev_f <= end_f:
                target_f = prev_f
            else:
                target_f = max(start_f, min(end_f, start_f + prev_offset))
        else:
            target_f = start_f
        self.clock.set_frame(target_f)
        self._on_frame_changed(self.clock.current_frame)
        self.cache.set_region_bounds(start_f, end_f)
        self._suppress_in_out_record = False

        # Cancel any pending prefetch tasks from the previous view and clear background queue
        if hasattr(self, "reader_engine") and self.reader_engine:
            self.reader_engine.cancel_pending()
            self.reader_engine.clear_background_cache_queue()

        is_seq = isinstance(viewable, SequenceView)
        prev_is_seq = getattr(self, "_active_view_is_sequence", False)
        self._active_view_is_sequence = is_seq

        if is_seq and not prev_is_seq:
            # Entering Sequence view: remember previous mode & active status
            self._pre_sequence_cache_mode = self.cache.mode
            self._pre_sequence_cache_started = getattr(self, "_cache_started", False)
            # Switch to Full Sequence cache mode and immediately start prefetching
            self._on_set_cache_mode(CacheMode.ALL, start_prefetch=True)
        elif not is_seq and prev_is_seq:
            # Exiting Sequence view: restore pre-sequence cache mode & status
            prev_mode = getattr(self, "_pre_sequence_cache_mode", None)
            prev_started = getattr(self, "_pre_sequence_cache_started", False)
            if prev_mode is not None:
                self._on_set_cache_mode(prev_mode, start_prefetch=prev_started)
            self._pre_sequence_cache_mode = None
            self._pre_sequence_cache_started = None
        else:
            if self.cache.mode != CacheMode.OFF and getattr(self, "_cache_started", False):
                self._trigger_prefetch()

        # Restore annotations for incoming media
        view_path = getattr(viewable, "path", None)
        if hasattr(self, "per_media_annotations") and hasattr(self, "annotation_store"):
            if self.per_media_annotations.has_annotations(view_id):
                self.per_media_annotations.restore(view_id, self.annotation_store)
            elif view_path and self.per_media_annotations.has_annotations(view_path):
                self.per_media_annotations.restore(view_path, self.annotation_store)
            else:
                self.annotation_store.clear_all()
            if hasattr(self, "timeline"):
                self.timeline.update()

        self._on_frame_changed(self.clock.current_frame)
        self._on_cache_updated()
        self.viewport.update()

    def _on_sources_view_updated(self) -> None:
        if getattr(self, "_in_sources_view_updated", False):
            return
        self._in_sources_view_updated = True
        try:
            viewable = self.session_graph.get_active_viewable()
            if viewable is not None:
                if isinstance(viewable, StackView):
                    self.viewport.comparison_state.stack_mix = getattr(viewable, "stack_mix", 1.0)
                    self.viewport.comparison_state.channels_a = getattr(viewable, "channels_a", (True, True, True, True))
                    self.comparison_state.stack_mix = getattr(viewable, "stack_mix", 1.0)
                    self.comparison_state.channels_a = getattr(viewable, "channels_a", (True, True, True, True))
                    if hasattr(self, "sources_dock"):
                        self.sources_dock.panel._refresh_subpanels()
                    if viewable.compare_mode == "switch":
                        comp_mode = ComparisonMode.OFF if (viewable.current_layer_index == viewable.index_b) else ComparisonMode.OVER
                    else:
                        mode_map = {
                            "wipe": ComparisonMode.WIPE,
                            "switch": ComparisonMode.OVER,
                            "split": ComparisonMode.SPLIT,
                            "difference": ComparisonMode.DIFFERENCE,
                            "difference_inverted": ComparisonMode.DIFFERENCE_INVERTED,
                            "over": ComparisonMode.OVER,
                            "add": ComparisonMode.ADD,
                        }
                        comp_mode = mode_map.get(viewable.compare_mode, ComparisonMode.OVER)
                    self.comparison_state.mode = comp_mode
                    self.viewport.comparison_state.mode = comp_mode
                    self._on_frame_changed(self.clock.current_frame)
                    self.viewport.update()
                    self._on_cache_updated()
                    if self.cache.mode != CacheMode.OFF and getattr(self, "_cache_started", False):
                        self._trigger_prefetch()
                    if hasattr(self, "wipe_widget"):
                        self.wipe_widget.btn_wipe.setChecked(comp_mode == ComparisonMode.WIPE)
                        self.wipe_widget.btn_split.setChecked(comp_mode == ComparisonMode.SPLIT)
                        self.wipe_widget.btn_diff.setChecked(comp_mode == ComparisonMode.DIFFERENCE)
                        self.wipe_widget.btn_off.setChecked(comp_mode in (ComparisonMode.OFF, ComparisonMode.OVER))
                    self._update_window_title()
                else:
                    self._on_active_view_changed(self.session_graph.active_view_type.value, viewable.id)
                self._refresh_active_source_details()
        finally:
            self._in_sources_view_updated = False

    def _on_step_clip_or_segment_next(self) -> None:
        """Step to next clip boundaries in Sequence view (PageDown), or next bookmark segment."""
        if hasattr(self, "session_graph"):
            viewable = self.session_graph.get_active_viewable()
            if isinstance(viewable, SequenceView) and viewable.clips:
                cur_f = self.clock.current_frame
                cur_start = viewable.start_frame
                clip_spans = []
                for c in viewable.clips:
                    c_end = cur_start + c.duration - 1
                    clip_spans.append((cur_start, c_end, c))
                    cur_start = c_end + 1

                target_idx = None
                for i, (s, e, c) in enumerate(clip_spans):
                    if s <= cur_f <= e:
                        target_idx = min(len(clip_spans) - 1, i + 1)
                        break
                    elif cur_f < s:
                        target_idx = i
                        break
                if target_idx is None:
                    target_idx = len(clip_spans) - 1

                s, e, c = clip_spans[target_idx]
                self.clock.set_in_out(s, e)
                self.clock.seek(s)
                src_name = self.session_graph.sources[c.source_id].name if c.source_id in self.session_graph.sources else "Clip"
                self.viewport.show_toast(f"Sequence Clip {target_idx + 1}/{len(clip_spans)}: {src_name} ({s} - {e})")
                return

        self.clock.step_bookmark_segment(forward=True)

    def _on_step_clip_or_segment_prev(self) -> None:
        """Step to previous clip boundaries in Sequence view (PageUp), or previous bookmark segment."""
        if hasattr(self, "session_graph"):
            viewable = self.session_graph.get_active_viewable()
            if isinstance(viewable, SequenceView) and viewable.clips:
                cur_f = self.clock.current_frame
                cur_start = viewable.start_frame
                clip_spans = []
                for c in viewable.clips:
                    c_end = cur_start + c.duration - 1
                    clip_spans.append((cur_start, c_end, c))
                    cur_start = c_end + 1

                target_idx = None
                for i, (s, e, c) in enumerate(clip_spans):
                    if s <= cur_f <= e:
                        target_idx = max(0, i - 1)
                        break
                    elif cur_f < s:
                        target_idx = max(0, i - 1)
                        break
                if target_idx is None:
                    target_idx = max(0, len(clip_spans) - 1)

                s, e, c = clip_spans[target_idx]
                self.clock.set_in_out(s, e)
                self.clock.seek(s)
                src_name = self.session_graph.sources[c.source_id].name if c.source_id in self.session_graph.sources else "Clip"
                self.viewport.show_toast(f"Sequence Clip {target_idx + 1}/{len(clip_spans)}: {src_name} ({s} - {e})")
                return

        self.clock.step_bookmark_segment(forward=False)

    def _on_version_up(self) -> None:
        """Step to next iteration/version in current folder (Alt+Up)."""
        if not self._current_media_path:
            self.viewport.show_toast("No media loaded to version step")
            return
        next_v = get_next_version(self._current_media_path)
        if next_v:
            curr_f = self.clock.current_frame
            was_playing = self.clock.is_playing
            self._load_path(next_v)
            if self.clock.start_frame <= curr_f <= self.clock.end_frame:
                self.clock.set_frame(curr_f)
            if was_playing:
                self.clock.play()
            summary = get_version_summary(next_v) or os.path.basename(next_v)
            self.viewport.show_toast(f"Version Up: {summary}")
        else:
            self.viewport.show_toast("Already at Latest Version")

    def _on_version_down(self) -> None:
        """Step to previous iteration/version in current folder (Alt+Down)."""
        if not self._current_media_path:
            self.viewport.show_toast("No media loaded to version step")
            return
        prev_v = get_prev_version(self._current_media_path)
        if prev_v:
            curr_f = self.clock.current_frame
            was_playing = self.clock.is_playing
            self._load_path(prev_v)
            if self.clock.start_frame <= curr_f <= self.clock.end_frame:
                self.clock.set_frame(curr_f)
            if was_playing:
                self.clock.play()
            summary = get_version_summary(prev_v) or os.path.basename(prev_v)
            self.viewport.show_toast(f"Version Down: {summary}")
        else:
            self.viewport.show_toast("Already at Earliest Version")

    def _on_toggle_safe_guides(self) -> None:
        """Toggle broadcast and film safe area guides (Shift+G)."""
        self.viewport.toggle_safe_guides()

    def _on_toggle_action_safe(self, checked: bool) -> None:
        self.viewport.show_action_safe = bool(checked)
        self.viewport.update()

    def _on_toggle_title_safe(self, checked: bool) -> None:
        self.viewport.show_title_safe = bool(checked)
        self.viewport.update()

    def _on_toggle_crosshair(self, checked: bool) -> None:
        self.viewport.show_crosshair = bool(checked)
        self.viewport.update()

    def _on_toggle_thirds_grid(self, checked: bool) -> None:
        self.viewport.show_thirds_grid = bool(checked)
        self.viewport.update()

    def _on_toggle_matte_constrain(self, checked: bool) -> None:
        self.viewport.set_safe_guides_matte_constrained(checked)
        self.viewport.show_toast("Safe Guides: Constrained to Matte" if checked else "Safe Guides: Full Frame")
        try:
            prefs = self.pref_mgr.prefs
            prefs.safe_guides_matte_constrained = bool(checked)
            self.pref_mgr.save(prefs)
        except Exception:
            pass

    def _on_toggle_onion_skin(self) -> None:
        """Toggle animation frame ghosting / onion skinning (Shift+O)."""
        modes = ["off", "prev", "next", "both"]
        cur_idx = modes.index(self.viewport.onion_skin_mode) if self.viewport.onion_skin_mode in modes else 0
        new_mode = modes[(cur_idx + 1) % len(modes)]
        self.viewport.onion_skin_mode = new_mode
        self.viewport.show_toast(f"Onion Skin: {new_mode.upper()}")
        self.viewport.update()

    def _on_toggle_timecode(self) -> None:
        """Toggle SMPTE timecode format on timeline (Ctrl+T)."""
        self.timeline.toggle_timecode_mode()
        st = "ON" if self.timeline.timecode_mode else "OFF"
        self.viewport.show_toast(f"Timecode: {st}")

    def _update_recent_menu(self) -> None:
        """Dynamically populate Open Recent menu with recently reviewed media and sessions."""
        if not hasattr(self, "recent_menu"):
            return
        self.recent_menu.clear()
        recents = self.pref_mgr.get_recent_files()
        if recents:
            for path in recents:
                act = self.recent_menu.addAction(os.path.basename(path))
                act.setToolTip(path)
                act.triggered.connect(lambda checked=False, p=path: self._load_recent(p))
            self.recent_menu.addSeparator()
            self.recent_menu.addAction("Clear Recent History").triggered.connect(self._on_clear_recent_history)
        else:
            no_rec = self.recent_menu.addAction("No Recent Files")
            no_rec.setEnabled(False)

    def _load_recent(self, path: str) -> None:
        """Open a recent file or session."""
        if not os.path.exists(path):
            self.statusBar().showMessage(f"File not found: {path}", 4000)
            return
        if path.lower().endswith((".ovr", ".json")):
            self._open_session_file(path)
        else:
            self._load_path(path)

    def _on_clear_recent_history(self) -> None:
        """Clear recent files history."""
        self.pref_mgr.clear_recent_files()
        self._update_recent_menu()
        self.statusBar().showMessage("Cleared recent files history", 3000)

    def _on_show_process_info(self) -> None:
        metrics = {
            "target_fps": self.clock.fps,
            "measured_fps": getattr(self.clock, "_last_measured_fps", self.clock.fps),
            "dropped_frames": 0,
            "loop_mode": self.clock.loop_mode.capitalize(),
            "cache_mode": str(self.cache.mode.value).capitalize(),
            "cache_used_mb": self.cache.current_memory_mb,
            "cache_max_mb": self.cache.max_memory_mb,
            "gl_vendor": "OpenGL Core Profile",
            "gl_renderer": "Hardware Shader Core",
            "has_lut": (self.viewport.color_grading.active_lut is not None and self.viewport.color_grading.lut_enabled),
            "has_cdl": (self.viewport.color_grading.active_cdl is not None and self.viewport.color_grading.cdl_enabled),
        }
        dlg = ProcessInfoDialog(metrics, self)
        dlg.exec()

    def _on_reset_all_colors(self) -> None:
        self.top_bar.reset_all_colors()
        self.viewport.gain = 1.0
        self.viewport.saturation = 1.0
        self.viewport.gamma = 1.0
        self.viewport.exposure_ev = 0.0
        self.viewport.reset_color_grading()
        if hasattr(self, "invert_colors_act"):
            self.invert_colors_act.setChecked(False)
        if hasattr(self, "oor_act"):
            self.oor_act.setChecked(False)
        self.viewport.update()
        self.statusBar().showMessage("All color adjustments reset (Gain: 1.0, Gam: 1.0, Sat: 1.0).", 3000)

    def _show_about(self) -> None:
        from openreview.ui.about_dialog import AboutDialog
        dlg = AboutDialog(self)
        dlg.exec()


    def _open_html_doc(self, filename: str) -> None:
        """Open specified HTML documentation manual in system default web browser."""
        from openreview.resources import get_docs_dir
        from PySide6.QtGui import QDesktopServices
        from PySide6.QtCore import QUrl
        doc_path = get_docs_dir() / filename
        if doc_path.exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(doc_path)))
        else:
            self.statusBar().showMessage(f"Documentation file not found: {filename}", 4000)

    def _open_docs_folder(self) -> None:
        """Open documentation directory in OS file manager."""
        from openreview.resources import get_docs_dir
        from PySide6.QtGui import QDesktopServices
        from PySide6.QtCore import QUrl
        docs_dir = get_docs_dir()
        if docs_dir.exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(docs_dir)))

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if hasattr(self, "timeline") and hasattr(self.timeline, "goto_frame_box"):
            self.timeline.goto_frame_box.clearFocus()
        if hasattr(self, "viewport") and self.viewport:
            self.viewport.setFocus()

    def closeEvent(self, event) -> None:
        self.clock.pause()
        if hasattr(self, "audio_engine") and self.audio_engine:
            self.audio_engine.unload_audio()
        self.cache.clear()
        self.reader_engine.set_reader(None)
        self.viewport.clear_canvas()
        if hasattr(self, "ipc_server") and self.ipc_server:
            try:
                self.ipc_server.stop()
            except Exception:
                pass
        if hasattr(self, "pkg_mgr") and self.pkg_mgr:
            try:
                self.pkg_mgr.unload_all_plugins()
            except Exception:
                pass
        if hasattr(self, "console_logger") and self.console_logger:
            try:
                self.console_logger.error_logged.disconnect(self._on_console_error_logged)
            except Exception:
                pass
        if hasattr(self, "console_window") and self.console_window:
            self.console_window.hide()
        if hasattr(self, "shortcut_mgr") and self.shortcut_mgr:
            self.shortcut_mgr.set_suppress_predicate(None)
            app = QApplication.instance()
            if app:
                try:
                    app.removeEventFilter(self.shortcut_mgr)
                except Exception:
                    pass
        super().closeEvent(event)

    def contextMenuEvent(self, event) -> None:
        if hasattr(self, "viewport") and self.viewport is not None:
            self.viewport.contextMenuEvent(event)
            event.accept()
        else:
            super().contextMenuEvent(event)
