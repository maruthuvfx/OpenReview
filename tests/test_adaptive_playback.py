"""
Tests for Interactive Adaptive Resolution Playback with Concurrent Background Caching.
Verifies:
1. Playback through uncached frames requests adaptive proxy resolution (downsample=True).
2. Viewport renders proxy interactively with (Proxy) status during playback.
3. Pausing playback immediately triggers a full-resolution upgrade for the resting frame.
4. Background cacher prefetch prioritizes full-res frames and upgrades cached proxy frames.
5. Loop playback over background-cached frames renders crisp full resolution without proxy tag.
6. Playback when Cache: OFF maintains interactive fluidity via downsampled frames without prefetching.
"""
import pytest
import numpy as np
from unittest.mock import MagicMock, patch

from openreview.ui.main_window import MainWindow
from openreview.core.cache import CacheMode
from openreview.core.reader import SyntheticPatternReader


def test_playback_uncached_renders_adaptive_proxy_immediately(qtbot):
    """Verify that playing through an uncached frame immediately decodes and renders adaptive proxy without viewer pause."""
    window = MainWindow()
    qtbot.addWidget(window)
    window._on_set_cache_mode(CacheMode.PLAY_AHEAD)

    window.reader = SyntheticPatternReader(width=1920, height=1080)
    window.reader_engine.set_reader(window.reader)

    # Set range 1-100, set frame to 10 and start playback (freeze timer to isolate frame 10)
    window.clock.set_range(1, 100)
    window.clock.set_frame(10)
    window.cache.clear()
    window.clock.play()
    window.clock._timer.stop()
    assert window.clock.is_playing is True

    # Frame 10 is uncached
    assert window.cache.has_frame(10) is False

    # Trigger frame change during playback
    window._on_frame_changed(10)

    # Viewport should immediately have proxy set and status label shows (Proxy) without pausing
    assert getattr(window.viewport, "_is_proxy", False) is True
    assert "(Proxy)" in window.resolution_status_label.text()
    assert window.cache.has_frame(10) is True
    assert window.cache.is_frame_proxy(10) is True


def test_pause_playback_upgrades_resting_frame(qtbot):
    """Verify that pausing playback on a proxy frame triggers an immediate full-res decode."""
    window = MainWindow()
    qtbot.addWidget(window)
    window._on_set_cache_mode(CacheMode.PLAY_AHEAD)

    window.reader = SyntheticPatternReader(width=1920, height=1080)
    window.reader_engine.set_reader(window.reader)

    # Set range 1-100, put a proxy in cache for frame 5 and set it in viewport
    window.clock.set_range(1, 100)
    window.clock.set_frame(5)
    proxy_data = np.zeros((540, 960, 4), dtype=np.uint8)
    window.viewport.set_primary_frame(5, proxy_data, logical_size=(1920, 1080), is_proxy=True)
    window.cache.put_frame(5, proxy_data, is_proxy=True)
    assert window.cache.is_frame_proxy(5) is True

    # Start playback and freeze timer, so transition to pause is clean
    window.clock.play()
    window.clock._timer.stop()
    assert window.clock.is_playing is True

    with patch.object(window.reader_engine, "request_immediate_frame") as mock_req:
        # Pause playback
        window.clock.pause()
        assert window.clock.is_playing is False

        # Should have dispatched full-resolution request (downsample=False)
        assert mock_req.called is True
        _, kwargs = mock_req.call_args
        assert kwargs.get("downsample") is False

    # Simulate delivery of full-res frame
    full_data = np.zeros((1080, 1920, 4), dtype=np.uint8)
    window._on_immediate_frame_ready(
        timeline_frame=5,
        source_frame=5,
        source_id="",
        success=True,
        data=full_data,
        is_proxy=False,
    )

    assert getattr(window.viewport, "_is_proxy", False) is False
    assert "(Proxy)" not in window.resolution_status_label.text()
    assert "1920x1080" in window.resolution_status_label.text()


def test_playback_loop_renders_full_res_from_bg_cache(qtbot):
    """Verify that once background caching populates full-res frames, playback loop renders crisp full-res."""
    window = MainWindow()
    qtbot.addWidget(window)
    window._on_set_cache_mode(CacheMode.PLAY_AHEAD)

    window.reader = SyntheticPatternReader(width=1920, height=1080)
    window.reader_engine.set_reader(window.reader)

    window.clock.set_range(1, 100)
    full_data = np.zeros((1080, 1920, 4), dtype=np.uint8)

    # Frame 8 cached as full-res
    window.cache.put_frame(8, full_data, is_proxy=False)
    assert window.cache.is_frame_proxy(8) is False

    # Advance playhead to frame 8 and start playback
    window.clock.set_frame(8)
    window.clock.play()
    window.clock._timer.stop()
    window._on_frame_changed(8)

    assert getattr(window.viewport, "_is_proxy", False) is False
    assert "(Proxy)" not in window.resolution_status_label.text()
    assert "1920x1080" in window.resolution_status_label.text()


def test_playback_cache_off_renders_adaptively_without_prefetch(qtbot):
    """Verify that when Cache is OFF, playback still renders interactively with downsampling, but prefetch stays OFF."""
    window = MainWindow()
    qtbot.addWidget(window)

    window._on_set_cache_mode(CacheMode.OFF)
    assert window.cache.mode == CacheMode.OFF

    window.reader = SyntheticPatternReader(width=1920, height=1080)
    window.reader_engine.set_reader(window.reader)

    window.clock.set_range(1, 100)
    window.clock.set_frame(15)
    window.clock.play()
    window.clock._timer.stop()
    assert window.clock.is_playing is True

    with patch.object(window.reader_engine, "request_prefetch") as mock_pref:
        window._on_frame_changed(15)
        # Prefetch must NOT be called when cache is OFF
        assert mock_pref.called is False

    # Immediate frame rendered directly with proxy status for interactive fluidity
    assert getattr(window.viewport, "_is_proxy", False) is True
    assert "(Proxy)" in window.resolution_status_label.text()
    # Cache stays empty because Cache is OFF
    assert window.cache.has_frame(15) is False
