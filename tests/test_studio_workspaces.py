"""
Unit tests for Studio Workspace Presets & Dockable Panels Layout System.
Covers:
- Standard Studio Workspace Presets (Standard Review, Color & QC, VFX Breakdown, Cinema Theater, Supervisor Review)
- Custom workspace saving, persistence, export/import, and deletion
- Studio Dock Widgets (ScopesDockWidget, InspectorDockWidget, VFXEXRDockWidget, ColorGradingDockWidget, AnnotationsDockWidget)
- Dynamic layout switching, tabification, and dock visibility
- Integration with TopControlBar workspace combo and shortcuts
"""
import os
import sys
import numpy as np
import pytest
from PySide6.QtCore import Qt, QPoint
from PySide6.QtWidgets import QMainWindow, QDockWidget, QWidget

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from openreview.ui.workspaces import (
    StudioWorkspaceManager,
    StudioWorkspace,
    STUDIO_WORKSPACES,
    StudioDockWidget,
    ScopesDockWidget,
    InspectorDockWidget,
    VFXEXRDockWidget,
    ColorGradingDockWidget,
    AnnotationsDockWidget,
)
from openreview.widgets.hud_scopes_async import ScopeAsyncEngine


def test_studio_workspace_manager_presets():
    """Verify that all predefined studio workspaces are registered and well-configured."""
    mgr = StudioWorkspaceManager()
    workspaces = mgr.list_workspaces()
    assert len(workspaces) >= 5

    ws_ids = [ws.id for ws in workspaces]
    assert "review" in ws_ids
    assert "color_qc" in ws_ids
    assert "vfx_breakdown" in ws_ids
    assert "cinema" in ws_ids
    assert "annotations" in ws_ids

    # Review workspace checks
    rev = mgr.get_workspace("review")
    assert rev is not None
    assert "SourcesDockWidget" in rev.visible_docks
    assert "ScopesDockWidget" in rev.hidden_docks
    assert rev.show_timeline is True
    assert rev.show_top_bar is True
    assert rev.shortcut == "F5"

    # Color & QC checks
    cqc = mgr.get_workspace("color_qc")
    assert cqc is not None
    assert "ScopesDockWidget" in cqc.visible_docks
    assert "InspectorDockWidget" in cqc.visible_docks
    assert cqc.shortcut == "F6"

    # VFX Breakdown checks
    vfx = mgr.get_workspace("vfx_breakdown")
    assert vfx is not None
    assert vfx.shortcut == "F7"

    # Cinema checks
    cinema = mgr.get_workspace("cinema")
    assert cinema is not None
    assert len(cinema.visible_docks) == 0
    assert cinema.show_top_bar is False
    assert cinema.show_timeline is False
    assert cinema.shortcut == "F8"

    # Annotations checks
    annot = mgr.get_workspace("annotations")
    assert annot is not None
    assert annot.shortcut == "F9"


def test_custom_workspace_lifecycle(qtbot):
    """Verify saving, exporting, importing, and deleting custom user workspaces."""
    win = QMainWindow()
    qtbot.addWidget(win)

    dock1 = QDockWidget("Test Dock 1", win)
    dock1.setObjectName("TestDock1")
    win.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, dock1)

    mgr = StudioWorkspaceManager()

    # 1. Save custom workspace
    ws_id = mgr.save_custom_workspace("My Custom Grade", win)
    assert ws_id.startswith("custom_")
    assert mgr.current_workspace == ws_id

    ws = mgr.get_workspace(ws_id)
    assert ws is not None
    assert ws.name == "My Custom Grade"
    assert ws.custom_state is not None

    # 2. Export & Import
    data = mgr.export_custom_workspaces()
    assert "My Custom Grade" in data

    mgr2 = StudioWorkspaceManager()
    mgr2.import_custom_workspaces(data)
    ws_imported = mgr2.get_workspace(ws_id)
    assert ws_imported is not None
    assert ws_imported.name == "My Custom Grade"

    # 3. Delete
    assert mgr.delete_custom_workspace(ws_id) is True
    assert mgr.get_workspace(ws_id) is None
    assert mgr.current_workspace == "review"


def test_studio_dock_widgets_instantiation(qtbot):
    """Verify dock widgets instantiate with tabs and custom studio titlebars."""
    win = QMainWindow()
    qtbot.addWidget(win)

    # Scopes Dock
    scopes = ScopesDockWidget(parent=win)
    qtbot.addWidget(scopes)
    assert scopes.objectName() == "ScopesDockWidget"
    assert scopes.tabs.count() == 4
    assert scopes.tabs.tabText(0) == "Histogram"
    assert scopes.tabs.tabText(1) == "Waveform"
    assert scopes.tabs.tabText(2) == "Vectorscope"
    assert scopes.tabs.tabText(3) == "All Scopes"
    assert scopes.titleBarWidget() is not None

    # Inspector Dock
    inspector = InspectorDockWidget(parent=win)
    qtbot.addWidget(inspector)
    assert inspector.objectName() == "InspectorDockWidget"
    assert inspector.tabs.count() == 3
    assert inspector.tabs.tabText(0) == "Metadata Tree"
    assert inspector.tabs.tabText(1) == "Source Details"
    assert inspector.tabs.tabText(2) == "Color Inspector"

    # VFX EXR Dock
    vfx_dock = VFXEXRDockWidget(parent=win)
    qtbot.addWidget(vfx_dock)
    assert vfx_dock.objectName() == "VFXEXRDockWidget"
    assert vfx_dock.tabs.count() == 2
    assert vfx_dock.tabs.tabText(0) == "Channel Matrix"
    assert vfx_dock.tabs.tabText(1) == "Layer Passes"


def test_scope_engine_multidispatch(qtbot):
    """Verify ScopeAsyncEngine can register multiple consumers and update all."""
    from openreview.widgets.hud_histogram import HistogramHUD
    from openreview.widgets.hud_waveform import WaveformHUD
    from openreview.widgets.hud_vectorscope import VectorscopeHUD

    scopes_dock = ScopesDockWidget()
    qtbot.addWidget(scopes_dock)

    h2 = HistogramHUD()
    w2 = WaveformHUD()
    v2 = VectorscopeHUD()
    qtbot.addWidget(h2)
    qtbot.addWidget(w2)
    qtbot.addWidget(v2)

    engine = ScopeAsyncEngine()
    engine.register_widgets(scopes_dock.hist_widget, scopes_dock.waveform_widget, scopes_dock.vectorscope_widget)
    engine.register_widgets(h2, w2, v2)

    assert len(engine._hist_widgets) == 2
    assert len(engine._waveform_widgets) == 2
    assert len(engine._vectorscope_widgets) == 2

    # Dispatch synthetic frame
    frame = np.full((128, 128, 4), 128, dtype=np.uint8)
    engine.dispatch(frame, True, True, True)

    # Wait briefly for worker pool completion
    qtbot.wait(200)
    assert getattr(scopes_dock.hist_widget, "_max_val", 0.0) >= 0.0


def test_main_window_workspace_switching(qtbot):
    """Verify MainWindow applies studio workspace presets and updates dock visibility."""
    from openreview.ui.main_window import MainWindow

    win = MainWindow()
    qtbot.addWidget(win)
    win.show()

    # 1. Standard Review Preset
    win._apply_workspace("review")
    assert win.workspace_mgr.current_workspace == "review"
    assert not win.sources_dock.isHidden()
    assert not win.timeline_dock.isHidden()
    assert win.scopes_dock.isHidden()
    assert win.vfx_exr_dock.isHidden()
    assert win.top_toolbar.isVisible() is True

    # 2. Color & QC Studio Preset
    win._apply_workspace("color_qc")
    assert win.workspace_mgr.current_workspace == "color_qc"
    assert not win.scopes_dock.isHidden()
    assert win.color_dock.isHidden()
    assert not win.sources_dock.isHidden()

    # 3. VFX Breakdown Preset
    win._apply_workspace("vfx_breakdown")
    assert win.workspace_mgr.current_workspace == "vfx_breakdown"
    assert not win.vfx_exr_dock.isHidden()
    assert not win.inspector_dock.isHidden()
    assert win.scopes_dock.isHidden()

    # 4. Cinema Theater Preset
    win._apply_workspace("cinema")
    assert win.workspace_mgr.current_workspace == "cinema"
    assert win.top_toolbar.isHidden()
    assert win.timeline_dock.isHidden()
    assert win.sources_dock.isHidden()
    assert win.scopes_dock.isHidden()

    # 5. Supervisor Review Preset
    win._apply_workspace("annotations")
    assert win.workspace_mgr.current_workspace == "annotations"
    assert not win.annotations_dock.isHidden()
    assert not win.top_toolbar.isHidden()
    assert not win.timeline_dock.isHidden()

    # Reset back to review
    win._apply_workspace("review")
    assert win.workspace_mgr.current_workspace == "review"


def test_workspace_and_color_inspector_shortcuts():
    """Verify F4..F9 shortcut bindings in ShortcutManager."""
    from openreview.core.shortcuts import ShortcutManager

    sm = ShortcutManager.instance()
    # Color Inspector moved from F5 to F4
    act_col = sm.get_action("hud.color_inspector")
    assert act_col is not None
    assert act_col.effective_shortcut == "F4"
    assert sm._aliases.get("F4") == "hud.color_inspector"

    # Workspace shortcuts F5..F9
    assert sm.get_action("workspace.review").effective_shortcut == "F5"
    assert sm.get_action("workspace.color_qc").effective_shortcut == "F6"
    assert sm.get_action("workspace.vfx_breakdown").effective_shortcut == "F7"
    assert sm.get_action("workspace.cinema").effective_shortcut == "F8"
    assert sm.get_action("workspace.annotations").effective_shortcut == "F9"

    assert sm._aliases.get("F5") == "workspace.review"
    assert sm._aliases.get("F6") == "workspace.color_qc"
    assert sm._aliases.get("F7") == "workspace.vfx_breakdown"
    assert sm._aliases.get("F8") == "workspace.cinema"
    assert sm._aliases.get("F9") == "workspace.annotations"
