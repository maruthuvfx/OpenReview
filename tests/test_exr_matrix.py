import os
import tempfile
import numpy as np
import pytest
from PySide6.QtCore import Qt, QPointF
from PySide6.QtGui import QKeyEvent

from openreview.core.exr_matrix import (
    SemanticPassType,
    EXRChannelInfo,
    EXRMatrixMapping,
    classify_channel,
    inspect_exr_matrix,
    build_layer_preset,
    read_exr_matrix_mapped,
)
from openreview.core.image_io import read_exr, read_image_file
from openreview.widgets.hud_channel_matrix import HUDChannelMatrixWidget
from openreview.widgets.viewport import ViewportCanvas
from openreview.ui.main_window import MainWindow


def test_classify_channel():
    c_diff = classify_channel("diffuse.R")
    assert c_diff.layer == "diffuse"
    assert c_diff.channel_role == "R"
    assert c_diff.semantic_type == SemanticPassType.COLOR

    c_norm = classify_channel("N.X")
    assert c_norm.layer == "N"
    assert c_norm.channel_role == "X"
    assert c_norm.semantic_type == SemanticPassType.NORMAL

    c_depth = classify_channel("depth.Z")
    assert c_depth.layer == "depth"
    assert c_depth.channel_role == "Z"
    assert c_depth.semantic_type == SemanticPassType.DEPTH

    c_z = classify_channel("Z")
    assert c_z.layer == "beauty"
    assert c_z.semantic_type == SemanticPassType.DEPTH

    c_motion = classify_channel("motion.u")
    assert c_motion.layer == "motion"
    assert c_motion.channel_role == "U"
    assert c_motion.semantic_type == SemanticPassType.VECTOR

    c_crypto = classify_channel("crypto_object00.R")
    assert c_crypto.layer == "crypto_object"
    assert c_crypto.channel_role == "R"
    assert c_crypto.semantic_type == SemanticPassType.CRYPTOMATTE

    c_alpha = classify_channel("A")
    assert c_alpha.semantic_type == SemanticPassType.ALPHA


def test_build_layer_preset():
    ch_normals = [
        EXRChannelInfo(name="N.X", channel_role="X", semantic_type=SemanticPassType.NORMAL),
        EXRChannelInfo(name="N.Y", channel_role="Y", semantic_type=SemanticPassType.NORMAL),
        EXRChannelInfo(name="N.Z", channel_role="Z", semantic_type=SemanticPassType.NORMAL),
    ]
    p_norm = build_layer_preset("N", ch_normals)
    assert p_norm.r_channel == "N.X"
    assert p_norm.g_channel == "N.Y"
    assert p_norm.b_channel == "N.Z"
    assert p_norm.remap_normals is True

    ch_depth = [
        EXRChannelInfo(name="depth.Z", channel_role="Z", semantic_type=SemanticPassType.DEPTH),
    ]
    p_depth = build_layer_preset("depth", ch_depth)
    assert p_depth.r_channel == "depth.Z"
    assert p_depth.g_channel == "depth.Z"
    assert p_depth.b_channel == "depth.Z"
    assert p_depth.auto_normalize_depth is True


def _create_synthetic_multichannel_exr(path: str) -> None:
    import OpenEXR
    import Imath

    w, h = 64, 48
    header = OpenEXR.Header(w, h)
    header["channels"] = {
        "R": Imath.Channel(Imath.PixelType(Imath.PixelType.FLOAT)),
        "G": Imath.Channel(Imath.PixelType(Imath.PixelType.FLOAT)),
        "B": Imath.Channel(Imath.PixelType(Imath.PixelType.FLOAT)),
        "A": Imath.Channel(Imath.PixelType(Imath.PixelType.FLOAT)),
        "diffuse.R": Imath.Channel(Imath.PixelType(Imath.PixelType.FLOAT)),
        "diffuse.G": Imath.Channel(Imath.PixelType(Imath.PixelType.FLOAT)),
        "diffuse.B": Imath.Channel(Imath.PixelType(Imath.PixelType.FLOAT)),
        "N.X": Imath.Channel(Imath.PixelType(Imath.PixelType.FLOAT)),
        "N.Y": Imath.Channel(Imath.PixelType(Imath.PixelType.FLOAT)),
        "N.Z": Imath.Channel(Imath.PixelType(Imath.PixelType.FLOAT)),
        "depth.Z": Imath.Channel(Imath.PixelType(Imath.PixelType.FLOAT)),
        "crypto_object00.R": Imath.Channel(Imath.PixelType(Imath.PixelType.FLOAT)),
        "crypto_object00.G": Imath.Channel(Imath.PixelType(Imath.PixelType.FLOAT)),
    }

    # Generate synthetic channel buffers
    r_arr = np.full((h, w), 0.75, dtype=np.float32)
    g_arr = np.full((h, w), 0.50, dtype=np.float32)
    b_arr = np.full((h, w), 0.25, dtype=np.float32)
    a_arr = np.ones((h, w), dtype=np.float32)

    diff_r = np.full((h, w), 0.20, dtype=np.float32)
    diff_g = np.full((h, w), 0.80, dtype=np.float32)
    diff_b = np.full((h, w), 0.40, dtype=np.float32)

    norm_x = np.full((h, w), -0.5, dtype=np.float32)
    norm_y = np.full((h, w), 0.0, dtype=np.float32)
    norm_z = np.full((h, w), 0.866, dtype=np.float32)

    depth_z = np.linspace(10.0, 100.0, w * h, dtype=np.float32).reshape((h, w))

    # Cryptomatte ID buffer
    id_val = np.uint32(0x3F8000).view(np.float32)  # Float representation of hash
    crypto_id = np.full((h, w), id_val, dtype=np.float32)
    crypto_cov = np.ones((h, w), dtype=np.float32)

    out_file = OpenEXR.OutputFile(path, header)
    out_file.writePixels({
        "R": r_arr.tobytes(),
        "G": g_arr.tobytes(),
        "B": b_arr.tobytes(),
        "A": a_arr.tobytes(),
        "diffuse.R": diff_r.tobytes(),
        "diffuse.G": diff_g.tobytes(),
        "diffuse.B": diff_b.tobytes(),
        "N.X": norm_x.tobytes(),
        "N.Y": norm_y.tobytes(),
        "N.Z": norm_z.tobytes(),
        "depth.Z": depth_z.tobytes(),
        "crypto_object00.R": crypto_id.tobytes(),
        "crypto_object00.G": crypto_cov.tobytes(),
    })
    out_file.close()


def test_inspect_and_read_exr_matrix():
    with tempfile.NamedTemporaryFile(suffix=".exr", delete=False) as tf:
        tmp_exr = tf.name

    try:
        _create_synthetic_multichannel_exr(tmp_exr)

        # 1. Inspect matrix
        layers_dict, all_channels = inspect_exr_matrix(tmp_exr)
        assert "diffuse" in layers_dict
        assert "N" in layers_dict
        assert "depth" in layers_dict
        assert "crypto_object" in layers_dict
        assert len(all_channels) >= 12

        # 2. Read Beauty pass
        mapping_beauty = EXRMatrixMapping(r_channel="R", g_channel="G", b_channel="B", a_channel="A")
        arr_beauty = read_exr_matrix_mapped(tmp_exr, mapping_beauty, as_f16=False)
        assert arr_beauty is not None
        assert arr_beauty.shape == (48, 64, 4)
        np.testing.assert_allclose(arr_beauty[0, 0, :3], [0.75, 0.50, 0.25], atol=1e-4)

        # 3. Read Normals with [-1, 1] -> [0, 1] remap
        mapping_norm = EXRMatrixMapping(
            r_channel="N.X",
            g_channel="N.Y",
            b_channel="N.Z",
            remap_normals=True,
        )
        arr_norm = read_exr_matrix_mapped(tmp_exr, mapping_norm, as_f16=False)
        assert arr_norm is not None
        # norm_x was -0.5 -> remapped should be -0.5 * 0.5 + 0.5 = 0.25
        np.testing.assert_allclose(arr_norm[0, 0, 0], 0.25, atol=1e-3)
        # norm_y was 0.0 -> remapped should be 0.5
        np.testing.assert_allclose(arr_norm[0, 0, 1], 0.50, atol=1e-3)

        # 4. Read Depth with auto-normalization
        mapping_depth = EXRMatrixMapping(
            r_channel="depth.Z",
            g_channel="depth.Z",
            b_channel="depth.Z",
            auto_normalize_depth=True,
        )
        arr_depth = read_exr_matrix_mapped(tmp_exr, mapping_depth, as_f16=False)
        assert arr_depth is not None
        assert np.min(arr_depth[:, :, 0]) >= 0.0
        assert np.max(arr_depth[:, :, 0]) <= 1.0

        # 5. Read Cryptomatte manifest
        mapping_crypto = EXRMatrixMapping(
            crypto_layer="crypto_object",
            semantic_type=SemanticPassType.CRYPTOMATTE,
        )
        arr_crypto = read_exr_matrix_mapped(tmp_exr, mapping_crypto, as_f16=False)
        assert arr_crypto is not None
        assert arr_crypto.shape == (48, 64, 4)

        # 6. Read via read_exr and read_image_file with mapping
        arr_via_io = read_exr(tmp_exr, mapping=mapping_beauty, as_f16=False)
        assert arr_via_io is not None
        np.testing.assert_allclose(arr_via_io[0, 0, :3], [0.75, 0.50, 0.25], atol=1e-4)

        arr_via_img = read_image_file(tmp_exr, mapping=mapping_beauty, as_f16=False)
        assert arr_via_img is not None
        np.testing.assert_allclose(arr_via_img[0, 0, :3], [0.75, 0.50, 0.25], atol=1e-4)

    finally:
        if os.path.exists(tmp_exr):
            os.remove(tmp_exr)


def test_viewport_channel_matrix_and_soloing(qtbot):
    viewport = ViewportCanvas()
    qtbot.addWidget(viewport)
    viewport.resize(400, 300)
    viewport.show()

    # Test solo channel
    viewport.solo_channel(1, greyscale=True)
    assert viewport.channel_mode == 1
    assert viewport.channel_solo_mode == 1

    viewport.solo_channel(2, greyscale=False)
    assert viewport.channel_mode == 2
    assert viewport.channel_solo_mode == 0

    # Test custom 4x4 channel matrix
    custom_mat = np.array([
        [0.0, 1.0, 0.0, 0.0],
        [1.0, 0.0, 0.0, 0.0],
        [0.0, 0.0, 1.0, 0.0],
        [0.0, 0.0, 0.0, 1.0],
    ], dtype=np.float32)
    viewport.set_channel_matrix(custom_mat)
    np.testing.assert_array_equal(viewport.channel_matrix, custom_mat)

    # Test channel enable / mute
    viewport.set_channel_enable(True, True, False, True)
    assert viewport.channel_enable == (1.0, 1.0, 0.0, 1.0)


def test_hud_channel_matrix_widget(qtbot):
    widget = HUDChannelMatrixWidget()
    qtbot.addWidget(widget)

    channels = [
        EXRChannelInfo(name="R", layer="beauty", channel_role="R", semantic_type=SemanticPassType.COLOR),
        EXRChannelInfo(name="G", layer="beauty", channel_role="G", semantic_type=SemanticPassType.COLOR),
        EXRChannelInfo(name="B", layer="beauty", channel_role="B", semantic_type=SemanticPassType.COLOR),
        EXRChannelInfo(name="A", layer="beauty", channel_role="A", semantic_type=SemanticPassType.ALPHA),
        EXRChannelInfo(name="N.X", layer="N", channel_role="X", semantic_type=SemanticPassType.NORMAL),
        EXRChannelInfo(name="N.Y", layer="N", channel_role="Y", semantic_type=SemanticPassType.NORMAL),
        EXRChannelInfo(name="N.Z", layer="N", channel_role="Z", semantic_type=SemanticPassType.NORMAL),
        EXRChannelInfo(name="depth.Z", layer="depth", channel_role="Z", semantic_type=SemanticPassType.DEPTH),
    ]
    layers_dict = {
        "beauty": channels[0:4],
        "N": channels[4:7],
        "depth": [channels[7]],
    }

    widget.populate_matrix(layers_dict, channels)
    assert widget.combo_r.count() == 9  # <None> + 8 channels
    assert widget.table.rowCount() == 8

    # Apply Normals preset
    widget.btn_preset_norm.click()
    assert widget._current_mapping.r_channel == "N.X"
    assert widget._current_mapping.g_channel == "N.Y"
    assert widget._current_mapping.b_channel == "N.Z"
    assert widget.chk_norm_remap.isChecked()

    # Swap R and B
    widget.btn_swap_rb.click()
    assert widget.combo_r.currentData() == "N.Z"
    assert widget.combo_b.currentData() == "N.X"

    # Test Solo buttons
    widget._solo_btns[1].click()
    assert widget._active_solo_channel == 1

    # Test Mono / Color toggle
    assert widget.btn_mono.isChecked()
    widget.btn_mono.click()
    assert not widget.btn_mono.isChecked()
    assert widget.btn_mono.text() == "Color"


def test_main_window_exr_matrix_integration(qtbot):
    window = MainWindow()
    qtbot.addWidget(window)
    window.resize(1000, 700)
    window.show()

    # Test solo channel integration
    window._on_channel_changed(1)
    assert window.viewport.channel_mode == 1
    assert window.viewport.channel_solo_mode == 1

    # Test matrix mapping change
    mapping = EXRMatrixMapping(r_channel="R", g_channel="G", b_channel="B")
    window._on_exr_matrix_mapping_changed(mapping)

    # Test normals remap toggle
    window._on_normals_remap_toggled(True)
    assert window.viewport.channel_solo_mode == 2

    window._on_normals_remap_toggled(False)
    assert window.viewport.channel_solo_mode == 1

    # Test cycling layers with [ and ]
    window.layers_widget.refresh_list(["beauty", "diffuse", "specular", "N"])
    window._current_exr_layer = "beauty"

    # Cycle forward (])
    window._on_cycle_exr_layer(forward=True)
    assert window._current_exr_layer == "diffuse"

    # Cycle forward again
    window._on_cycle_exr_layer(forward=True)
    assert window._current_exr_layer == "specular"

    # Cycle backward ([)
    window._on_cycle_exr_layer(forward=False)
    assert window._current_exr_layer == "diffuse"
