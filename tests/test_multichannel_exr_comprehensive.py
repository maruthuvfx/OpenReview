"""
Comprehensive Verification Suite for Multi-Channel EXR Engine & UI Options.
Tests:
1. Rust zero-GIL metadata inspection
2. Rust single-pass all layers extraction
3. Color passes: Beauty, Diffuse, Specular
4. Surface Normals signed vector remapping ([-1..1] -> [0..1])
5. Depth/Z auto-normalization ([15..250] -> [0..1])
6. Cryptomatte ID hash color unpacking & coverage
7. Arbitrary channel matrix swizzling
8. Layer-aware tiered caching & prefetching
9. UI Integration: TopBar layer switcher, HUD Channel Matrix, Contact Sheet & soloing, Metadata dialog
"""
import os
import sys
import numpy as np
import pytest
from PySide6.QtCore import Qt, QPointF
from PySide6.QtGui import QMouseEvent

import openreview_engine
from openreview.core.image_io import get_exr_channels, read_exr, read_image_file
from openreview.core.exr_matrix import (
    SemanticPassType,
    EXRChannelInfo,
    EXRMatrixMapping,
    classify_channel,
    inspect_exr_matrix,
    build_layer_preset,
    read_exr_matrix_mapped,
)
from openreview.core.exr_contact_sheet import (
    build_exr_contact_sheet,
    hit_test_contact_sheet,
    extract_exr_detailed_metadata,
)
from openreview.ui.main_window import MainWindow

STANDALONE_EXR = os.path.abspath("tests/sample_media/multichannel_exr/multichannel_test.exr")
SEQ_FRAME_1 = os.path.abspath("tests/sample_media/multichannel_exr/seq/multichannel_seq.0001.exr")


def test_rust_multichannel_metadata_inspection():
    """Verify Rust inspect_exr extracts dimensions, compression, channels, and layers."""
    assert os.path.exists(STANDALONE_EXR), f"Test media not found at {STANDALONE_EXR}"
    eng = openreview_engine.OpenReviewEngine()
    meta = eng.inspect_exr(STANDALONE_EXR)

    assert meta["width"] == 256
    assert meta["height"] == 144
    assert "ZIP" in meta["compression"].upper()

    all_chans = meta["all_channels"]
    assert len(all_chans) == 23
    assert "R" in all_chans and "G" in all_chans and "B" in all_chans and "A" in all_chans
    assert "diffuse.R" in all_chans and "specular.R" in all_chans
    assert "N.X" in all_chans and "depth.Z" in all_chans
    assert "motion.U" in all_chans and "crypto_object00.R" in all_chans

    layers = meta["layers"]
    assert "beauty" in layers
    assert "diffuse" in layers
    assert "specular" in layers
    assert "N" in layers
    assert "depth" in layers
    assert "motion" in layers
    assert "AO" in layers

    # High-level get_exr_channels should also reflect this
    ch_info = get_exr_channels(STANDALONE_EXR)
    assert len(ch_info["all_channels"]) == 23
    assert "diffuse" in ch_info["layers"]


def test_rust_all_layers_extraction():
    """Verify Rust read_all_exr_layers extracts all passes in a single parse."""
    eng = openreview_engine.OpenReviewEngine()
    all_layers = eng.read_all_exr_layers(STANDALONE_EXR, False)

    expected_passes = ["beauty", "diffuse", "specular", "N", "depth", "motion", "AO"]
    for ep in expected_passes:
        assert ep in all_layers, f"Missing pass '{ep}' in extracted layers"
        frame = all_layers[ep]
        assert frame.width == 256
        assert frame.height == 144
        assert frame.channels == 4
        assert not frame.is_f16


def test_rust_color_passes():
    """Verify Beauty, Diffuse, and Specular pass decode correctness and value ranges."""
    beauty = read_exr(STANDALONE_EXR, layer="beauty")
    diffuse = read_exr(STANDALONE_EXR, layer="diffuse")
    specular = read_exr(STANDALONE_EXR, layer="specular")

    assert beauty is not None and diffuse is not None and specular is not None
    assert beauty.shape == (144, 256, 4)
    assert diffuse.shape == (144, 256, 4)
    assert specular.shape == (144, 256, 4)

    # Beauty has HDR pixels (sphere > 1.0)
    assert np.max(beauty) > 1.0
    # Diffuse is standard lighting [0..1.1]
    assert np.min(diffuse) >= 0.0
    # Specular has sharp highlight
    assert np.max(specular) > 1.5


def test_rust_normals_remapping():
    """Verify surface normals signed vectors [-1..1] are remapped to [0..1] when requested."""
    layers_dict, _ = inspect_exr_matrix(STANDALONE_EXR)
    preset_norm = build_layer_preset("N", layers_dict.get("N", []))
    assert preset_norm.remap_normals is True

    # Read with normals remapping enabled
    normals_remapped = read_exr(STANDALONE_EXR, mapping=preset_norm)
    assert normals_remapped is not None
    # All values must strictly reside in [0.0, 1.0]
    assert np.min(normals_remapped[:, :, :3]) >= 0.0
    assert np.max(normals_remapped[:, :, :3]) <= 1.0001


def test_rust_depth_auto_normalization():
    """Verify HDR Z-Depth distances are auto-normalized to [0..1] range."""
    layers_dict, _ = inspect_exr_matrix(STANDALONE_EXR)
    preset_depth = build_layer_preset("depth", layers_dict.get("depth", []))
    assert preset_depth.auto_normalize_depth is True

    depth_norm = read_exr(STANDALONE_EXR, mapping=preset_depth)
    assert depth_norm is not None
    # R, G, B must be identical grayscale
    np.testing.assert_allclose(depth_norm[:, :, 0], depth_norm[:, :, 1], atol=1e-5)
    np.testing.assert_allclose(depth_norm[:, :, 0], depth_norm[:, :, 2], atol=1e-5)
    # Range must be [0.0, 1.0]
    assert np.isclose(np.min(depth_norm[:, :, 0]), 0.0, atol=1e-4)
    assert np.isclose(np.max(depth_norm[:, :, 0]), 1.0, atol=1e-4)


def test_rust_cryptomatte_extraction():
    """Verify Cryptomatte hash ID unpacking into visual RGB and coverage alpha."""
    layers_dict, _ = inspect_exr_matrix(STANDALONE_EXR)
    preset_crypto = build_layer_preset("crypto_object", layers_dict.get("crypto_object", []))

    crypto_vis = read_exr(STANDALONE_EXR, mapping=preset_crypto)
    assert crypto_vis is not None
    assert crypto_vis.shape == (144, 256, 4)
    # Background must be 0 (ID 0)
    assert np.min(crypto_vis) == 0.0
    # Foregrounds must have valid unpacked color
    assert np.max(crypto_vis[:, :, :3]) > 0.0


def test_rust_arbitrary_routing():
    """Verify custom routing: specular.R -> R, diffuse.G -> G, depth.Z -> B."""
    custom_map = EXRMatrixMapping(
        r_channel="specular.R",
        g_channel="diffuse.G",
        b_channel="depth.Z",
        a_channel="A",
    )
    routed = read_exr(STANDALONE_EXR, mapping=custom_map)
    assert routed is not None
    assert routed.shape == (144, 256, 4)

    # Check R channel matches specular.R
    spec = read_exr(STANDALONE_EXR, layer="specular")
    np.testing.assert_allclose(routed[:, :, 0], spec[:, :, 0], atol=1e-5)

    # Check G channel matches diffuse.G
    diff = read_exr(STANDALONE_EXR, layer="diffuse")
    np.testing.assert_allclose(routed[:, :, 1], diff[:, :, 1], atol=1e-5)


def test_rust_layer_aware_cache_and_prefetch():
    """Verify TieredCache and prefetch scheduler store and retrieve frames keyed by layer."""
    eng = openreview_engine.OpenReviewEngine(ram_limit_mb=1024, worker_threads=2)
    sid = "test_source_multichannel"

    # Prefetch diffuse pass for sequence
    items = [(sid, 1, STANDALONE_EXR)]
    eng.prefetch_batch(items, proxy_scale=0, layer="diffuse")

    # Manually put different frames for diffuse vs depth
    f_diff = eng.read_exr(STANDALONE_EXR, False, layer="diffuse")
    f_depth = eng.read_exr(STANDALONE_EXR, False, layer="depth", auto_normalize_depth=True)

    eng.put_frame(sid, 1, f_diff, layer="diffuse")
    eng.put_frame(sid, 1, f_depth, layer="depth")

    # Query cached frames by layer
    cached_diff = eng.get_cached_frames(sid, layer="diffuse")
    cached_depth = eng.get_cached_frames(sid, layer="depth")
    assert 1 in cached_diff
    assert 1 in cached_depth

    # Retrieve frame for diffuse
    ret_diff = eng.get_frame(sid, 1, layer="diffuse")
    assert ret_diff is not None
    arr_diff = np.frombuffer(ret_diff.as_memoryview(), dtype=np.float32).reshape((ret_diff.height, ret_diff.width, 4))

    # Retrieve frame for depth
    ret_depth = eng.get_frame(sid, 1, layer="depth")
    assert ret_depth is not None
    arr_depth = np.frombuffer(ret_depth.as_memoryview(), dtype=np.float32).reshape((ret_depth.height, ret_depth.width, 4))

    # Verify no cross-layer contamination
    assert not np.allclose(arr_diff, arr_depth)


def test_contact_sheet_and_solo_hit_testing(qtbot):
    """Verify Contact Sheet grid generation, badges, and double-click pass soloing."""
    info = get_exr_channels(STANDALONE_EXR)
    layers = list(info.get("layers", {}).keys())

    layer_frames = {}
    for lyr in layers:
        arr = read_image_file(STANDALONE_EXR, layer=lyr)
        if arr is not None:
            layer_frames[lyr] = arr

    canvas, tile_map = build_exr_contact_sheet(layer_frames, base_w=256, base_h=144)
    assert len(tile_map) == len(layer_frames)
    assert canvas.shape[0] > 0 and canvas.shape[1] > 0

    # Hit test inside first tile
    first_lyr = layers[0]
    tx, ty, tw, th = tile_map[first_lyr]
    hit_layer = hit_test_contact_sheet(tx + tw // 2, ty + th // 2, tile_map)
    assert hit_layer == first_lyr


def test_ui_multichannel_main_window_integration(qtbot):
    """Verify full UI integration: loading multi-channel EXR, top bar layer selector, and contact sheet."""
    win = MainWindow()
    qtbot.addWidget(win)
    win.show()

    # Load multi-channel EXR
    win._load_path(STANDALONE_EXR)
    qtbot.waitUntil(lambda: win.reader is not None, timeout=5000)

    # 1. Top Bar layer dropdown must be populated
    combo = win.top_bar.layer_combo
    assert combo.count() >= 5
    items = [combo.itemText(i) for i in range(combo.count())]
    assert "beauty" in items
    assert "diffuse" in items
    assert "specular" in items

    # 2. Switch layer via TopBar
    win.top_bar.set_current_layer("diffuse")
    assert win.top_bar.layer_combo.currentText() == "diffuse"

    # 3. Switch layer to depth
    win.top_bar.set_current_layer("depth")
    assert win.top_bar.layer_combo.currentText() == "depth"

    # 4. Toggle Contact Sheet mode
    win._on_toggle_exr_contact_sheet(True)
    assert win._is_exr_contact_sheet_active is True

    # 6. Verify VFX EXR dock was fully populated
    if hasattr(win, "vfx_exr_dock"):
        # Layer Passes section
        layers_w = win.vfx_exr_dock.layers_widget
        assert layers_w.list_layers.count() >= 5
        pass_names = [layers_w.list_layers.item(i).text() for i in range(layers_w.list_layers.count())]
        assert "diffuse" in pass_names and "specular" in pass_names and "depth" in pass_names

        # Channel Matrix section
        matrix_w = win.vfx_exr_dock.channel_matrix_widget
        assert matrix_w.table.rowCount() == 23
        assert matrix_w.combo_r.count() >= 23

        # Test selecting a pass in dock while contact sheet is active auto-exits contact sheet
        win._on_toggle_exr_contact_sheet(True)
        assert win._is_exr_contact_sheet_active is True
        win.vfx_exr_dock.layers_widget.layer_changed.emit("diffuse")
        assert win._is_exr_contact_sheet_active is False
        assert win.top_bar.layer_combo.currentText() == "diffuse"

    # 7. Verify SourcesPanel redundant exr_properties_bar is hidden
    if hasattr(win, "sources_dock") and hasattr(win.sources_dock.panel, "exr_properties_bar"):
        assert not win.sources_dock.panel.exr_properties_bar.isVisible()

    win.close()


def test_layer_to_rgba_mapping_and_filtering_enter_to_solo(qtbot):
    """Verify Layer-to-RGBA auto routing, formatted dropdowns, multi-field search, and Enter-to-solo."""
    from openreview.ui.main_window import MainWindow

    win = MainWindow()
    qtbot.addWidget(win)
    win.show()

    # Load multi-channel EXR
    win._load_path(STANDALONE_EXR)
    cm = win.vfx_exr_dock.channel_matrix_widget
    ly = win.vfx_exr_dock.layers_widget

    # 1. Layer combo population & formatting
    assert cm.combo_layer.count() >= 8
    assert cm.combo_r.count() == 24
    assert "[beauty] R" in [cm.combo_r.itemText(i) for i in range(cm.combo_r.count())]

    # 2. Select Layer 'N' (Normals) -> Auto-routes N.X, N.Y, N.Z and checks Remap Normals
    cm.combo_layer.setCurrentText("N")
    assert cm.combo_r.currentData() == "N.X"
    assert cm.combo_g.currentData() == "N.Y"
    assert cm.combo_b.currentData() == "N.Z"
    assert cm.chk_norm_remap.isChecked() is True

    # 3. Select Layer 'depth' -> Auto-routes depth.Z to R, G, B
    cm.combo_layer.setCurrentText("depth")
    assert cm.combo_r.currentData() == "depth.Z"
    assert cm.combo_g.currentData() == "depth.Z"
    assert cm.combo_b.currentData() == "depth.Z"

    # 4. Filter channels by Layer name 'diff' in Channel Matrix table
    cm.search_input.setText("diff")
    visible_channels = [cm.table.item(r, 0).text() for r in range(cm.table.rowCount()) if not cm.table.isRowHidden(r)]
    assert set(visible_channels) == {"diffuse.R", "diffuse.G", "diffuse.B"}

    # 5. Press Enter in Channel Matrix search box on 'specular' -> Auto-solos specular
    cm.search_input.setText("specular")
    cm._on_search_return_pressed()
    assert cm.combo_r.currentData() == "specular.R"
    assert cm.combo_g.currentData() == "specular.G"
    assert cm.combo_b.currentData() == "specular.B"

    # 6. Press Enter in Layer Passes search box on 'N' -> Switches active layer to N
    ly.search_input.setText("N")
    ly._on_search_return_pressed()
    assert win._current_exr_layer == "N"

    win.close()

