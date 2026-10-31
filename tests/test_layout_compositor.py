"""
Unit tests for Layout Compositor:
1. Normalization of uint8, uint16, float32, and RGB/RGBA channel layouts
2. Prevention of color blowing out / washed-out highlights in multi-tile layout composites
3. Aspect ratio preservation (centered letterboxing/pillarboxing)
4. Dynamic base dimension calculation
"""
import numpy as np
import pytest

from openreview.models.session_graph import LayoutView, SourceItem
from openreview.core.layout_compositor import (
    normalize_to_rgba_float32,
    resize_frame_fast,
    composite_layout_grid,
)


def test_normalize_to_rgba_float32():
    # 1. uint8 4-channel
    u8 = np.full((10, 10, 4), 255, dtype=np.uint8)
    res_u8 = normalize_to_rgba_float32(u8)
    assert res_u8.dtype == np.float32
    assert np.allclose(res_u8, 1.0)

    # Midtones uint8
    u8_mid = np.full((10, 10, 4), 128, dtype=np.uint8)
    res_mid = normalize_to_rgba_float32(u8_mid)
    assert np.allclose(res_mid[:, :, :3], 128.0 / 255.0)

    # 2. uint16 4-channel
    u16 = np.full((10, 10, 4), 65535, dtype=np.uint16)
    res_u16 = normalize_to_rgba_float32(u16)
    assert res_u16.dtype == np.float32
    assert np.allclose(res_u16, 1.0)

    # 3. 3-channel RGB -> 4-channel RGBA
    rgb = np.full((10, 10, 3), 100, dtype=np.uint8)
    res_rgb = normalize_to_rgba_float32(rgb)
    assert res_rgb.shape == (10, 10, 4)
    assert np.allclose(res_rgb[:, :, 3], 1.0)  # Alpha = 1.0
    assert np.allclose(res_rgb[:, :, 0], 100.0 / 255.0)

    # 4. 2D Grayscale -> 4-channel RGBA
    gray = np.full((10, 10), 200, dtype=np.uint8)
    res_gray = normalize_to_rgba_float32(gray)
    assert res_gray.shape == (10, 10, 4)
    assert np.allclose(res_gray[:, :, 3], 1.0)
    assert np.allclose(res_gray[:, :, :3], 200.0 / 255.0)

    # 5. float32 input preserves values
    f32 = np.full((10, 10, 4), 0.75, dtype=np.float32)
    res_f32 = normalize_to_rgba_float32(f32)
    assert np.allclose(res_f32, 0.75)


def test_composite_layout_grid_prevents_color_washout():
    # Video frames from VideoMediaReader are uint8 with values up to 255
    tile1 = np.full((1080, 1920, 4), 210, dtype=np.uint8)
    tile2 = np.full((1080, 1920, 4), 180, dtype=np.uint8)

    src1 = SourceItem(id="s1", name="video1.mp4", path="video1.mp4", width=1920, height=1080)
    src2 = SourceItem(id="s2", name="video2.mp4", path="video2.mp4", width=1920, height=1080)
    sources = {"s1": src1, "s2": src2}

    layout = LayoutView(id="l1", name="Grid", source_ids=["s1", "s2"], scale_pct=50)

    def fetcher(src):
        return tile1 if src.id == "s1" else tile2

    comp = composite_layout_grid(layout, sources, fetcher)

    assert comp.dtype == np.float32
    # Crucial assertion: values must be in [0.0, 1.0], NEVER blown out to ~200.0!
    assert comp.max() <= 1.0001
    assert comp.min() >= 0.0

    # Check tile 1 color ~210/255 = 0.8235
    tile1_sample = comp[10:50, 10:50, :3]
    assert np.allclose(tile1_sample, 210.0 / 255.0, atol=0.01)

    # Check tile 2 color ~180/255 = 0.7058
    # Cols = 2, scale = 50% => tile_w = 960
    tile2_sample = comp[10:50, 970:1010, :3]
    assert np.allclose(tile2_sample, 180.0 / 255.0, atol=0.01)


def test_composite_layout_grid_preserves_aspect_ratio():
    # DCI 4K (4096 x 2160, aspect ratio ~ 1.896:1) in a 16:9 cell (1.777:1)
    # Must letterbox top and bottom with clean dark padding
    dci_frame = np.full((2160, 4096, 4), 255, dtype=np.uint8)

    src = SourceItem(id="s1", name="dci.mp4", path="dci.mp4", width=4096, height=2160)
    sources = {"s1": src}
    layout = LayoutView(id="l1", name="DCI_Layout", source_ids=["s1"], scale_pct=100)

    # Force a 16:9 base cell: 1920 x 1080
    comp = composite_layout_grid(
        layout,
        sources,
        lambda s: dci_frame,
        base_width=1920,
        base_height=1080,
    )

    # 4096x2160 scaled to fit 1920x1080:
    # fit_scale = min(1920/4096, 1080/2160) = min(0.46875, 0.5) = 0.46875
    # scaled_w = 1920, scaled_h = 1012
    # pad_y = (1080 - 1012) // 2 = 34
    # Rows 0..33 should be letterbox background (pure black 0.0)
    assert np.allclose(comp[5, 960, :3], 0.0, atol=0.01)
    # Center pixel should be white (1.0)
    assert np.allclose(comp[540, 960, :3], 1.0, atol=0.01)


def test_composite_layout_grid_dynamic_base_dimensions():
    # Two 4K UHD sources (3840x2160)
    src1 = SourceItem(id="s1", name="uhd1.mp4", path="uhd1.mp4", width=3840, height=2160)
    src2 = SourceItem(id="s2", name="uhd2.mp4", path="uhd2.mp4", width=3840, height=2160)
    sources = {"s1": src1, "s2": src2}

    layout = LayoutView(id="l1", name="UHD_Grid", source_ids=["s1", "s2"], scale_pct=100)

    comp = composite_layout_grid(layout, sources, lambda s: np.zeros((2160, 3840, 4), dtype=np.uint8))
    # 2 columns of 3840x2160 => total 7680 x 2160
    assert comp.shape == (2160, 7680, 4)
