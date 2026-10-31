"""
Tests for Adaptive Resolution Scrubbing and Viewport Upgrade.
Verifies:
1. Adaptive downsampling factor determination for 4K+ vs HD/2K.
2. Cache proxy tagging, in-place replacement by full-res, and disk exclusion.
3. InteractiveDecodeTask asynchronous proxy downsampling.
4. Viewport canvas coordinate preservation during proxy display.
5. Scrub lifecycle (scrubbing flag, downsample request, settle full-res upgrade).
"""
import pytest
import numpy as np
from unittest.mock import MagicMock

from openreview.core.cache import RingBufferCache, CacheMode
from openreview.core.reader import (
    get_adaptive_downsample_factor,
    InteractiveDecodeTask,
    SyntheticPatternReader,
)
from openreview.core.cancellation import CancellationToken
from openreview.widgets.viewport import ViewportCanvas
from openreview.core.clock import PlaybackClock
from openreview.models.annotations import AnnotationStore


def test_adaptive_downsample_factor():
    """Verify 4K+ gets 4x downsample (1/4 res) and 2K/HD gets 2x (1/2 res)."""
    # 4K UHD
    assert get_adaptive_downsample_factor(3840, 2160) == 4
    # 4K DCI
    assert get_adaptive_downsample_factor(4096, 2160) == 4
    # 8K UHD
    assert get_adaptive_downsample_factor(7680, 4320) == 4
    # Full HD (1080p)
    assert get_adaptive_downsample_factor(1920, 1080) == 2
    # 2K DCI
    assert get_adaptive_downsample_factor(2048, 1080) == 2
    # 720p
    assert get_adaptive_downsample_factor(1280, 720) == 2


def test_cache_proxy_tagging_and_replacement():
    """Verify RingBufferCache tracks proxy tags and replaces them cleanly on full-res store."""
    cache = RingBufferCache(max_memory_mb=100.0, mode=CacheMode.PLAY_AHEAD, enable_disk_cache=False)

    source_id = "test_src"
    frame = 101
    proxy_img = np.zeros((540, 960, 4), dtype=np.uint8)
    full_img = np.zeros((2160, 3840, 4), dtype=np.uint8)

    # Put as proxy
    cache.put_source_frame(source_id, frame, proxy_img, is_proxy=True)
    assert cache.has_source_frame(source_id, frame) is True
    assert cache.is_source_frame_proxy(source_id, frame) is True
    assert cache.is_frame_proxy(frame, source_id=source_id) is True

    # Retrieve with allow_proxy=True and allow_proxy=False
    retrieved_proxy = cache.get_source_frame(source_id, frame, allow_proxy=True)
    assert retrieved_proxy is not None
    assert retrieved_proxy.shape == (540, 960, 4)

    retrieved_none = cache.get_source_frame(source_id, frame, allow_proxy=False)
    assert retrieved_none is None

    # Replace with full resolution frame
    cache.put_source_frame(source_id, frame, full_img, is_proxy=False)
    assert cache.has_source_frame(source_id, frame) is True
    assert cache.is_source_frame_proxy(source_id, frame) is False
    assert cache.is_frame_proxy(frame, source_id=source_id) is False

    retrieved_full = cache.get_source_frame(source_id, frame, allow_proxy=False)
    assert retrieved_full is not None
    assert retrieved_full.shape == (2160, 3840, 4)

    # Test clear
    cache.clear()
    assert cache.has_source_frame(source_id, frame) is False
    assert cache.is_source_frame_proxy(source_id, frame) is False


def test_interactive_decode_task_downsample():
    """Verify InteractiveDecodeTask performs fast strided subsampling when is_proxy=True."""
    reader = SyntheticPatternReader(width=1920, height=1080)
    token = CancellationToken(generation=1)
    received = []

    def callback(timeline_frame, source_frame, source_id, success, data, is_proxy):
        received.append((timeline_frame, success, data, is_proxy))

    task = InteractiveDecodeTask(
        reader=reader,
        cache=None,
        timeline_frame=50,
        source_frame=50,
        source_id="syn",
        token=token,
        downsample_factor=2,
        is_proxy=True,
        callback=callback,
    )
    task.run()

    assert len(received) == 1
    tf, success, data, is_proxy = received[0]
    assert success is True
    assert is_proxy is True
    assert data is not None
    # 1920x1080 subsampled by 2 is 960x540
    assert data.shape[0] == 540
    assert data.shape[1] == 960
    assert data.shape[2] == 4


def test_viewport_preserves_logical_coordinates(qtbot):
    """Verify viewport canvas preserves logical dimensions when rendering a proxy frame."""
    store = AnnotationStore()
    viewport = ViewportCanvas(store)
    qtbot.addWidget(viewport)

    full_frame = np.zeros((2160, 3840, 4), dtype=np.uint8)
    proxy_frame = np.zeros((540, 960, 4), dtype=np.uint8)

    # 1. Set full frame
    viewport.set_primary_frame(1001, full_frame)
    assert viewport._img_width == 3840
    assert viewport._img_height == 2160
    assert viewport.image_width == 3840
    assert viewport.image_height == 2160

    # 2. Set proxy frame with logical_size specified
    viewport.set_primary_frame(1002, proxy_frame, logical_size=(3840, 2160), is_proxy=True)
    assert viewport._img_width == 3840
    assert viewport._img_height == 2160
    assert viewport.image_width == 3840
    assert viewport.image_height == 2160

    # 3. Sampling pixel region on proxy scales coordinates properly and returns RGBA
    r, g, b, a = viewport._sample_image_region(ix=1920, iy=1080, aperture=1)
    assert (r, g, b, a) == (0.0, 0.0, 0.0, 0.0)

    # 4. Upgrade to full resolution frame
    viewport.set_primary_frame(1002, full_frame, is_proxy=False)
    assert viewport._img_width == 3840
    assert viewport._img_height == 2160


def test_main_window_adaptive_scrubbing_and_settle(qtbot):
    """Verify MainWindow scrub lifecycle: _is_scrubbing, downsample immediate request, and settle upgrade."""
    from openreview.ui.main_window import MainWindow

    window = MainWindow()
    qtbot.addWidget(window)

    assert window._is_scrubbing is False

    # Simulate viewport scrub
    window._on_viewport_scrub(1)
    assert window._is_scrubbing is True
    assert window._scrub_settle_timer.isActive() is True

    # Simulate immediate proxy frame delivery
    proxy_data = np.zeros((540, 960, 4), dtype=np.uint8)
    curr_frame = window.clock.current_frame
    window._on_immediate_frame_ready(
        timeline_frame=curr_frame,
        source_frame=curr_frame,
        source_id="",
        success=True,
        data=proxy_data,
        is_proxy=True,
    )
    assert "(Proxy)" in window.resolution_status_label.text()
    assert getattr(window.viewport, "_is_proxy", False) is True

    # Simulate scrub settled
    window._on_scrub_settled()
    assert window._is_scrubbing is False

    # Simulate full-res frame delivery
    full_data = np.zeros((1080, 1920, 4), dtype=np.uint8)
    window._on_immediate_frame_ready(
        timeline_frame=curr_frame,
        source_frame=curr_frame,
        source_id="",
        success=True,
        data=full_data,
        is_proxy=False,
    )
    assert "(Proxy)" not in window.resolution_status_label.text()
    assert "1920x1080" in window.resolution_status_label.text()
    assert getattr(window.viewport, "_is_proxy", False) is False


def test_direct_adaptive_scrub_rendering(qtbot):
    """Verify that scrubbing through an uncached frame renders adaptively immediately without pauses."""
    from openreview.ui.main_window import MainWindow

    window = MainWindow()
    qtbot.addWidget(window)
    window._on_set_cache_mode(CacheMode.PLAY_AHEAD)

    window.reader = SyntheticPatternReader(width=1920, height=1080)
    window.reader_engine.set_reader(window.reader)

    window.clock.set_range(1, 100)
    window.clock.set_frame(20)
    window.cache.clear()

    # Frame 21 is uncached before scrub
    assert window.cache.has_frame(21) is False

    # User scrubs forward by 1 frame
    window._on_viewport_scrub(1)
    assert window._is_scrubbing is True
    assert window.clock.current_frame == 21

    # Viewport directly rendered proxy without waiting or pausing
    assert getattr(window.viewport, "_is_proxy", False) is True
    assert "(Proxy)" in window.resolution_status_label.text()
    assert window.cache.has_frame(21) is True
    assert window.cache.is_frame_proxy(21) is True

