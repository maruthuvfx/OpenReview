import os
import pytest
from unittest.mock import patch
from openreview.models.session_graph import SessionGraph, SourceItem, build_source_item_from_path, ViewType
from openreview.color.standard_colorspaces import COLORSPACE_BY_ID, COLORSPACE_BY_NAME
from openreview.ui.main_window import MainWindow


def test_source_item_colorspace_and_input_space_sync():
    """Verify SourceItem initializes, synchronizes, and serializes input_space."""
    # Default linear
    s_lin = SourceItem(name="plate_exr", colorspace="linear")
    assert s_lin.input_space == 0
    assert s_lin.colorspace == "linear"

    # Cineon
    s_cin = SourceItem(name="film_scan", colorspace="Cineon")
    assert s_cin.input_space == 3
    assert s_cin.colorspace == "Cineon"

    # rec709
    s_709 = SourceItem(name="video_709", colorspace="rec709")
    assert s_709.input_space == 2
    assert s_709.colorspace == "rec709"

    # Serialization roundtrip
    data = s_cin.to_dict()
    assert data["input_space"] == 3
    assert data["colorspace"] == "Cineon"

    restored = SourceItem.from_dict(data)
    assert restored.input_space == 3
    assert restored.colorspace == "Cineon"


def test_build_source_item_auto_detect(tmp_path):
    """Verify build_source_item_from_path auto-detects colorspace per file format."""
    # 1. EXR -> linear
    exr_file = tmp_path / "shot_010.exr"
    exr_file.write_bytes(b"\x76\x2f\x31\x01test")
    item_exr = build_source_item_from_path(str(exr_file))
    assert item_exr is not None
    assert item_exr.colorspace == "linear"
    assert item_exr.input_space == 0

    # 2. DPX -> Cineon
    dpx_file = tmp_path / "plate.1001.dpx"
    dpx_file.write_bytes(b"SDPXtest")
    item_dpx = build_source_item_from_path(str(dpx_file))
    assert item_dpx is not None
    assert item_dpx.colorspace == "Cineon"
    assert item_dpx.input_space == 3

    # 3. PNG -> sRGB
    png_file = tmp_path / "texture.png"
    png_file.write_bytes(b"\x89PNG\r\n\x1a\ntest")
    item_png = build_source_item_from_path(str(png_file))
    assert item_png is not None
    assert item_png.colorspace == "sRGB"
    assert item_png.input_space == 1


def test_per_clip_colorspace_switching_and_persistence(qapp, tmp_path):
    """Verify each clip maintains its own input colorspace when active view switches."""
    win = MainWindow()
    win.pref_mgr.prefs.color_management_mode = "standard"
    win.top_bar.set_color_management_mode("standard", default_view="sRGB", default_input="linear")

    graph = win.session_graph

    # Create Clip A: linear EXR (id 0)
    clip_a = SourceItem(name="Clip A (EXR)", colorspace="linear", input_space=0)
    graph.add_source(clip_a)

    # Create Clip B: Cineon DPX (id 3)
    clip_b = SourceItem(name="Clip B (DPX)", colorspace="Cineon", input_space=3)
    graph.add_source(clip_b)

    # 1. Activate Clip A -> top bar should be linear (0)
    graph.set_active_view(ViewType.SOURCE, clip_a.id)
    assert win.top_bar.input_combo.currentData() == 0
    assert win.viewport.input_space == 0

    # 2. Activate Clip B -> top bar should switch to Cineon (3)
    graph.set_active_view(ViewType.SOURCE, clip_b.id)
    assert win.top_bar.input_combo.currentData() == 3
    assert win.viewport.input_space == 3

    # 3. Switch back to Clip A -> top bar should restore linear (0)
    graph.set_active_view(ViewType.SOURCE, clip_a.id)
    assert win.top_bar.input_combo.currentData() == 0
    assert win.viewport.input_space == 0

    # 4. User manually overrides Clip A to rec709 (id 2) via top bar
    win.top_bar.set_color_spaces(2, 1)  # input=rec709 (2), view=sRGB (1)
    assert clip_a.input_space == 2
    assert clip_a.colorspace == "rec709"

    # 5. Switch to Clip B -> Clip B must still be Cineon (3)
    graph.set_active_view(ViewType.SOURCE, clip_b.id)
    assert win.top_bar.input_combo.currentData() == 3
    assert win.viewport.input_space == 3
    assert clip_b.input_space == 3
    assert clip_b.colorspace == "Cineon"

    # 6. Switch back to Clip A -> Clip A must still be rec709 (2)
    graph.set_active_view(ViewType.SOURCE, clip_a.id)
    assert win.top_bar.input_combo.currentData() == 2
    assert win.viewport.input_space == 2
    assert clip_a.input_space == 2
    assert clip_a.colorspace == "rec709"

    win.close()


def test_sources_panel_colorspace_combo_change(qapp):
    """Verify changing colorspace dropdown in SourcesPanel inspector updates the source item and main window."""
    win = MainWindow()
    win.pref_mgr.prefs.color_management_mode = "standard"
    win.top_bar.set_color_management_mode("standard", default_view="sRGB", default_input="linear")

    graph = win.session_graph
    clip = SourceItem(name="Test Clip", colorspace="linear", input_space=0)
    graph.add_source(clip)
    graph.set_active_view(ViewType.SOURCE, clip.id)

    panel = win.sources_dock.panel
    panel._populate_clip_edit(clip)

    # Change to Cineon (id 3)
    c_idx = panel.combo_colorspace.findData(3)
    assert c_idx >= 0
    panel.combo_colorspace.setCurrentIndex(c_idx)

    # Source should now be Cineon
    assert clip.input_space == 3
    assert clip.colorspace == "Cineon"

    # Top bar should reflect Cineon
    assert win.top_bar.input_combo.currentData() == 3
    assert win.viewport.input_space == 3

    win.close()


def test_auto_detect_on_load_path(qapp, tmp_path):
    """Verify loading a file into MainWindow auto-configures the clip's input colorspace."""
    win = MainWindow()
    win.pref_mgr.prefs.color_management_mode = "standard"
    win.pref_mgr.prefs.auto_detect_input_colorspace = True

    # 1. EXR file
    exr_file = tmp_path / "shot.exr"
    exr_file.write_bytes(b"\x76\x2f\x31\x01\x02\x00\x00\x00")
    win._load_path(str(exr_file))

    active_item = win.session_graph.get_active_viewable()
    assert isinstance(active_item, SourceItem)
    assert active_item.colorspace == "linear"
    assert active_item.input_space == 0
    assert win.top_bar.input_combo.currentData() == 0

    # 2. DPX file
    dpx_file = tmp_path / "scan.dpx"
    dpx_file.write_bytes(b"SDPX\x00\x00\x00\x00")
    win._load_path(str(dpx_file))

    active_dpx = win.session_graph.get_active_viewable()
    assert isinstance(active_dpx, SourceItem)
    assert active_dpx.colorspace == "Cineon"
    assert active_dpx.input_space == 3
    assert win.top_bar.input_combo.currentData() == 3

    win.close()
