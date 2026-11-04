"""
Integration tests for Viewport canvas double click pass soloing and SourcesPanel EXR tools bar.
"""
from __future__ import annotations

import numpy as np
import pytest
from PySide6.QtCore import Qt, QPointF
from PySide6.QtGui import QMouseEvent

from openreview.models.session_graph import SessionGraph, SourceItem
from openreview.ui.sources_panel import SourcesPanelWidget
from openreview.widgets.viewport import ViewportCanvas


def test_viewport_contact_sheet_double_click_solo(qtbot):
    viewport = ViewportCanvas()
    qtbot.addWidget(viewport)

    # Setup contact sheet mode with tile map
    tile_map = {
        "beauty": (0, 0, 320, 180),
        "normal_CAMERA": (320, 0, 320, 180),
        "point_WORLD": (0, 180, 320, 180),
    }
    viewport.set_contact_sheet_mode(True, tile_map)
    viewport._img_width = 640
    viewport._img_height = 360

    # Mock screen to image mapping for testing
    def mock_screen_to_image(sx, sy):
        return int(sx), int(sy)

    viewport._screen_to_image_coords = mock_screen_to_image

    # Double click on normal_CAMERA tile (e.g. at x=400, y=50)
    with qtbot.waitSignal(viewport.contact_sheet_tile_double_clicked, timeout=1000) as blocker:
        ev = QMouseEvent(
            QMouseEvent.Type.MouseButtonDblClick,
            QPointF(400, 50),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        viewport.mouseDoubleClickEvent(ev)

    assert blocker.args == ["normal_CAMERA"]


def test_sources_panel_exr_properties_bar_and_signals(qtbot):
    graph = SessionGraph()
    panel = SourcesPanelWidget(graph)
    qtbot.addWidget(panel)

    # Check dedicated red-area properties bar exists
    assert hasattr(panel, "exr_properties_bar")
    assert hasattr(panel, "btn_exr_contact_sheet")
    assert hasattr(panel, "btn_exr_view_single")
    assert hasattr(panel, "btn_exr_export_sheet")
    assert hasattr(panel, "btn_exr_inspector")

    # Signals
    with qtbot.waitSignal(panel.exr_contact_sheet_requested, timeout=1000):
        panel.btn_exr_contact_sheet.click()

    with qtbot.waitSignal(panel.exr_view_single_requested, timeout=1000):
        panel.btn_exr_view_single.click()

    with qtbot.waitSignal(panel.exr_export_sheet_requested, timeout=1000):
        panel.btn_exr_export_sheet.click()

    with qtbot.waitSignal(panel.exr_show_inspector_requested, timeout=1000):
        panel.btn_exr_inspector.click()


def test_sources_panel_exr_source_item_loading(qtbot):
    graph = SessionGraph()
    panel = SourcesPanelWidget(graph)
    qtbot.addWidget(panel)

    # Add an EXR SourceItem
    src = SourceItem(name="shot_comp.exr", path="D:/Need_exr/shot_comp.0001.exr")
    assert src.file_path == "D:/Need_exr/shot_comp.0001.exr"
    graph.add_source(src)
    from openreview.models.session_graph import ViewType
    graph.set_active_view(ViewType.SOURCE, src.id)

    # refresh_tree and _refresh_subpanels should run without AttributeError
    panel.show()
    panel.refresh_tree()
    panel._refresh_subpanels()

    assert panel.exr_properties_bar.isHidden()


def test_top_bar_set_current_layer(qtbot):
    from openreview.widgets.top_bar import TopControlBar
    from openreview.color.ocio_manager import OCIOManager
    from PySide6.QtWidgets import QToolBar

    tb = QToolBar()
    bar = TopControlBar(OCIOManager(), tb)
    qtbot.addWidget(tb)

    bar.set_available_layers(["beauty", "diffuse", "specular", "normal_CAMERA"])
    assert bar.layer_combo.count() == 4

    bar.set_current_layer("specular")
    assert bar.layer_combo.currentText() == "specular"

    bar.set_current_layer("beauty")
    assert bar.layer_combo.currentText() == "beauty"


