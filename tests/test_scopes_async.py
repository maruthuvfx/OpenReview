"""
Unit tests for Asynchronous Non-Blocking Real-Time Scopes Engine.
"""
import numpy as np
import pytest
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication

from openreview.widgets.hud_scopes_async import (
    adaptive_downsample_for_scopes,
    compute_histogram_bins,
    compute_waveform_trace,
    compute_vectorscope_trace,
    ScopeAsyncEngine,
)
from openreview.widgets.hud_histogram import HistogramHUD
from openreview.widgets.hud_waveform import WaveformHUD
from openreview.widgets.hud_vectorscope import VectorscopeHUD


def test_adaptive_downsample_zero_copy():
    """Verify adaptive downsampling slices large frames without copying memory."""
    # 4K frame
    frame_4k = np.zeros((2160, 3840, 4), dtype=np.float32)
    sub_4k = adaptive_downsample_for_scopes(frame_4k)
    assert sub_4k.shape == (540, 960, 4)
    assert sub_4k.base is frame_4k, "Must be zero-copy strided view"

    # HD frame
    frame_hd = np.zeros((1080, 1920, 4), dtype=np.uint8)
    sub_hd = adaptive_downsample_for_scopes(frame_hd)
    assert sub_hd.shape == (540, 960, 4)
    assert sub_hd.base is frame_hd

    # Small thumbnail
    frame_small = np.zeros((240, 320, 4), dtype=np.uint8)
    sub_small = adaptive_downsample_for_scopes(frame_small)
    assert sub_small.shape == (240, 320, 4)


def test_compute_histogram_bins():
    """Verify histogram computation returns accurate 256-bin distributions."""
    img = np.zeros((120, 160, 4), dtype=np.uint8)
    img[:, :, 0] = 50
    img[:, :, 1] = 100
    img[:, :, 2] = 200

    res = compute_histogram_bins(img)
    assert res is not None
    hr, hg, hb, hy, max_val = res
    assert len(hr) == 256
    assert len(hg) == 256
    assert len(hb) == 256
    assert len(hy) == 256
    assert hr[50] > 0
    assert hg[100] > 0
    assert hb[200] > 0
    assert max_val > 0


def test_compute_waveform_modes():
    """Verify waveform rasterization for Overlay, Parade, and Luma modes."""
    img = np.zeros((120, 160, 4), dtype=np.float32)
    img[:, :, :3] = 0.5

    for mode in ("RGB Overlay", "RGB Parade", "Luma (Y)"):
        qimg = compute_waveform_trace(img, mode, pw=270, ph=153)
        assert qimg is not None
        assert isinstance(qimg, QImage)
        assert not qimg.isNull()
        assert qimg.width() == 270
        assert qimg.height() == 153


def test_compute_vectorscope():
    """Verify vectorscope chrominance trace generation."""
    img = np.zeros((120, 160, 4), dtype=np.uint8)
    img[:, :, 0] = 220
    img[:, :, 1] = 80
    img[:, :, 2] = 40

    qimg = compute_vectorscope_trace(img, cx=110, cy=110, scale=140, w=220, h=220)
    assert qimg is not None
    assert isinstance(qimg, QImage)
    assert not qimg.isNull()
    assert qimg.width() == 220
    assert qimg.height() == 220


def test_scope_async_engine_dispatch(qtbot):
    """Verify ScopeAsyncEngine background execution and non-blocking delivery."""
    engine = ScopeAsyncEngine()
    hist_hud = HistogramHUD()
    wf_hud = WaveformHUD()
    vec_hud = VectorscopeHUD()
    engine.register_widgets(hist_hud, wf_hud, vec_hud)

    img = np.ones((100, 100, 4), dtype=np.uint8) * 128

    with qtbot.waitSignal(engine.signals.scopes_ready, timeout=2000):
        engine.dispatch(img, show_hist=True, show_waveform=True, show_vectorscope=True)

    # Verify widgets received computed results
    assert hist_hud.canvas._hist_r is not None
    assert wf_hud.canvas._trace_img is not None
    assert vec_hud.canvas._trace_img is not None

    engine.stop()
