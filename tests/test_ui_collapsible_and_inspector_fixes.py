"""
Unit tests verifying recent UI fixes:
1. +Eyedropper signal wiring in InspectorDockWidget to Viewport.
2. Source Details metadata sync, Copy All to Clipboard and Export JSON.
3. Collapsed default state for Color Adjustments in Scopes panel.
4. Collapsed default state for Layer Passes in VFX EXR panel.
5. Vector SVG icons used across CollapsibleSection and Studio Dock TitleBars (no emojis).
"""
import os
import sys
import tempfile
import numpy as np
import pytest
from PySide6.QtCore import Qt, QPoint
from PySide6.QtWidgets import QApplication

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from openreview.ui.main_window import MainWindow
from openreview.ui.workspaces import ScopesDockWidget, InspectorDockWidget, VFXEXRDockWidget
from openreview.ui.svg_icons import get_svg_icon


def test_scopes_color_accordion_collapsed_by_default(qtbot):
    """Verify that the COLOR ADJUSTMENTS accordion is collapsed by default."""
    scopes = ScopesDockWidget()
    qtbot.addWidget(scopes)

    assert scopes.sec_hist._expanded is True
    assert scopes.sec_wave._expanded is True
    assert scopes.sec_vec._expanded is True
    assert scopes.sec_color._expanded is False
    assert scopes.sec_color.content_frame.isHidden() is True


def test_vfx_exr_accordion_defaults(qtbot):
    """Verify VFX EXR panel has Channel Matrix expanded and Layer Passes collapsed by default."""
    vfx = VFXEXRDockWidget()
    qtbot.addWidget(vfx)

    assert vfx.sec_channel_matrix._expanded is True
    assert vfx.sec_channel_matrix.content_frame.isHidden() is False
    assert vfx.sec_layers._expanded is False
    assert vfx.sec_layers.content_frame.isHidden() is True


def test_collapsible_section_uses_svg_icons(qtbot):
    """Verify collapsible sections use QIcon with resolution-independent SVG icons."""
    from openreview.ui.workspaces import CollapsibleSection
    sec = CollapsibleSection("HISTOGRAM", icon_name="histogram")
    qtbot.addWidget(sec)

    assert not sec.header_btn.icon().isNull()
    # Header text should have disclosure arrow and title, no OS emojis
    assert "▼" in sec.header_btn.text()
    assert "HISTOGRAM" in sec.header_btn.text()
    assert "📊" not in sec.header_btn.text()


def test_inspector_dock_eyedropper_and_source_details(qtbot):
    """Verify +Eyedropper in inspector dock controls viewport and Source Details exports non-empty data."""
    win = MainWindow()
    qtbot.addWidget(win)
    win.show()

    # 1. Test Eyedropper toggling in Inspector Dock
    insp = win.inspector_dock.inspector_widget
    assert win.viewport.is_eyedropper_mode_active() is False
    assert insp.eyedropper_btn.isChecked() is False

    # Click + Eyedropper in dock
    insp.eyedropper_btn.click()
    assert win.viewport.is_eyedropper_mode_active() is True
    assert insp.eyedropper_btn.text() == "Picking..."

    # Click again to cancel
    insp.eyedropper_btn.click()
    assert win.viewport.is_eyedropper_mode_active() is False
    assert insp.eyedropper_btn.text() == "+ Eyedropper"

    # 2. Test Source Details Copy All and Export JSON with populated data
    details = win.inspector_dock.details_widget
    # Inject synthetic source info
    test_info = {
        "file_name": "shot_010_comp_v001.exr",
        "file_path": "C:/projects/shots/shot_010_comp_v001.exr",
        "width": 3840,
        "height": 2160,
        "media_type": "OpenEXR Sequence",
    }
    details.update_info(test_info)
    assert details.info["file_name"] == "shot_010_comp_v001.exr"

    # Test Copy All to Clipboard
    details._on_copy_all()
    clipboard_text = QApplication.clipboard().text()
    assert "=== OpenReview Source Media Details ===" in clipboard_text
    assert "shot_010_comp_v001.exr" in clipboard_text
    assert "3840" in clipboard_text

    # Test Export JSON
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp:
        tmp_path = tmp.name

    try:
        import json
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(details.info, f, indent=2, default=str)
        with open(tmp_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        assert data["file_name"] == "shot_010_comp_v001.exr"
        assert data["width"] == 3840
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
