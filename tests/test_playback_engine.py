"""
Unit and Integration Tests for High-Performance VFX Playback & Caching Engine.
Verifies cooperative cancellation, dual-queue priority, contiguous buffer conditioning,
pinned memory safeguards, and decoupled asynchronous frame delivery.
"""
import time
import numpy as np
import pytest
from PySide6.QtCore import QObject

from openreview.core.cancellation import CancellationToken, CancellationScopeGuard
from openreview.core.cache import RingBufferCache, CacheMode
from openreview.core.reader import (
    MediaReader,
    SyntheticPatternReader,
    ReaderEngine,
    ensure_c_contiguous_rgba,
)
from openreview.core.clock import PlaybackClock


class DummySlowReader(MediaReader):
    """Simulates a heavy disk media reader (e.g. 4K OpenEXR decompression)."""

    def __init__(self, delay_sec: float = 0.05):
        self.delay_sec = delay_sec
        self.call_count = 0

    def read_frame(self, frame: int) -> np.ndarray:
        self.call_count += 1
        time.sleep(self.delay_sec)
        # Return a 3-channel RGB image (needs alpha conversion)
        arr = np.full((100, 100, 3), 0.5, dtype=np.float32)
        return arr


def test_cancellation_token():
    token = CancellationToken(generation=1)
    assert not token.is_cancelled()
    assert token.generation == 1

    guard = CancellationScopeGuard(token)
    assert not guard.aborted()

    token.request_cancellation()
    assert token.is_cancelled()
    assert guard.aborted()

    token.reset(new_generation=2)
    assert not token.is_cancelled()
    assert token.generation == 2
    assert not guard.aborted()


def test_ensure_c_contiguous_rgba():
    # 1. 3-channel uint8 -> 4-channel uint8 with alpha=255
    rgb_u8 = np.zeros((64, 64, 3), dtype=np.uint8)
    rgb_u8[:, :, 0] = 120
    rgba_u8 = ensure_c_contiguous_rgba(rgb_u8)
    assert rgba_u8.shape == (64, 64, 4)
    assert rgba_u8.dtype == np.uint8
    assert rgba_u8.flags.c_contiguous
    assert rgba_u8[0, 0, 0] == 120
    assert rgba_u8[0, 0, 3] == 255

    # 2. 3-channel float32 -> 4-channel float16 with alpha=1.0
    rgb_f32 = np.full((32, 32, 3), 0.75, dtype=np.float32)
    rgba_f16 = ensure_c_contiguous_rgba(rgb_f32)
    assert rgba_f16.shape == (32, 32, 4)
    assert rgba_f16.dtype == np.float16
    assert rgba_f16.flags.c_contiguous
    assert np.isclose(rgba_f16[0, 0, 0], 0.75)
    assert np.isclose(rgba_f16[0, 0, 3], 1.0)

    # 3. 2D grayscale -> 4-channel RGBA
    gray = np.full((20, 20), 0.4, dtype=np.float32)
    rgba_gray = ensure_c_contiguous_rgba(gray)
    assert rgba_gray.shape == (20, 20, 4)
    assert rgba_gray.dtype == np.float16
    assert rgba_gray.flags.c_contiguous
    assert np.isclose(rgba_gray[0, 0, 0], 0.4)
    assert np.isclose(rgba_gray[0, 0, 1], 0.4)
    assert np.isclose(rgba_gray[0, 0, 2], 0.4)
    assert np.isclose(rgba_gray[0, 0, 3], 1.0)

    # 4. Already C-contiguous RGBA float16 -> untouched
    ready = np.zeros((10, 10, 4), dtype=np.float16)
    ready[:, :, 3] = 1.0
    out = ensure_c_contiguous_rgba(ready)
    assert out is ready  # Zero copy


def test_cache_pinning():
    cache = RingBufferCache(max_memory_mb=1.0, mode=CacheMode.PLAY_AHEAD)
    # 1. Pinning API
    assert not cache.is_pinned("src_1", 101)
    cache.pin_frame("src_1", 101)
    assert cache.is_pinned("src_1", 101)

    # Fill cache beyond 1.0 MB budget with small frames
    # Each frame 200x200x4 float16 is ~320 KB
    for f in range(100, 106):
        arr = np.zeros((200, 200, 4), dtype=np.float16)
        cache.put_source_frame("src_1", f, arr)

    # Frame 101 must NOT be evicted because it is pinned
    assert cache.has_source_frame("src_1", 101)

    # Unpin frame 101
    cache.unpin_frame("src_1", 101)
    assert not cache.is_pinned("src_1", 101)


def test_reader_engine_dual_queue_and_immediate_request(qtbot):
    cache = RingBufferCache(max_memory_mb=100.0, mode=CacheMode.PLAY_AHEAD)
    reader = DummySlowReader(delay_sec=0.02)
    engine = ReaderEngine(reader=reader, cache=cache)

    assert hasattr(engine, "_interactive_pool")
    assert hasattr(engine, "_prefetch_pool")

    received = []

    def on_ready(tf, sf, sid, success, data):
        received.append((tf, sf, sid, success, data))

    # Request immediate frame on interactive pool
    engine.request_immediate_frame("src_test", 1005, 1005, reader=reader, callback=on_ready)

    # Wait for completion via qtbot
    qtbot.waitUntil(lambda: len(received) == 1, timeout=1500)

    tf, sf, sid, success, data = received[0]
    assert success is True
    assert tf == 1005
    assert sf == 1005
    assert sid == "src_test"
    assert data is not None
    assert data.shape == (100, 100, 4)
    assert data.dtype == np.float16
    assert data.flags.c_contiguous
    # Verify it was stored in cache
    assert cache.has_source_frame("src_test", 1005)


def test_reader_engine_cooperative_cancellation(qtbot):
    cache = RingBufferCache(max_memory_mb=100.0, mode=CacheMode.PLAY_AHEAD)
    reader = DummySlowReader(delay_sec=0.10)
    engine = ReaderEngine(reader=reader, cache=cache)

    # Request immediate frame
    received = []
    engine.request_immediate_frame(
        "src_test", 2001, 2001, reader=reader, callback=lambda *args: received.append(args)
    )

    # Immediately cancel pending tasks (e.g. user scrubbed past)
    engine.cancel_pending()

    # Wait a moment to ensure aborted task does not deliver to callback or cache
    time.sleep(0.15)
    assert not cache.has_source_frame("src_test", 2001)


def test_viewport_pbo_streaming_engine_modes(qtbot):
    from openreview.widgets.viewport import ViewportCanvas, PlaybackEngineMode
    from openreview.core.preferences import PreferencesManager

    vp = ViewportCanvas()
    qtbot.addWidget(vp)

    # 1. Default with enable_pbo_streaming=True
    PreferencesManager.instance().prefs.enable_pbo_streaming = True
    vp.set_playback_engine("opengl_core")
    assert vp.is_pbo_enabled() is True

    # 2. Explicit ASYNC_PBO
    vp.set_playback_engine(PlaybackEngineMode.ASYNC_PBO)
    assert vp.is_pbo_enabled() is True

    # 3. Explicit SOFTWARE_SAFE overrides preference
    vp.set_playback_engine(PlaybackEngineMode.SOFTWARE_SAFE)
    assert vp.is_pbo_enabled() is False

    # 4. Disabled via preferences
    vp.set_playback_engine(PlaybackEngineMode.OPENGL_CORE)
    PreferencesManager.instance().prefs.enable_pbo_streaming = False
    assert vp.is_pbo_enabled() is False

    # Restore default
    PreferencesManager.instance().prefs.enable_pbo_streaming = True


def test_viewport_pbo_ping_pong_texture_upload(qtbot):
    from openreview.widgets.viewport import ViewportCanvas, PlaybackEngineMode

    vp = ViewportCanvas()
    qtbot.addWidget(vp)
    vp.show()
    qtbot.waitExposed(vp)

    vp.makeCurrent()
    if not vp._shader_program:
        vp.initializeGL()

    vp.set_playback_engine(PlaybackEngineMode.ASYNC_PBO)
    assert vp.is_pbo_enabled() is True

    # Render frame 1
    frame1 = np.ones((128, 128, 4), dtype=np.float16)
    vp.set_primary_frame(1, frame1)
    vp._upload_texture_a()

    idx_after_f1 = vp._pbo_a_index
    assert idx_after_f1 == 1

    # Render frame 2 (should swap ping-pong index back to 0)
    frame2 = np.ones((128, 128, 4), dtype=np.float16) * 0.5
    vp.set_primary_frame(2, frame2)
    vp._upload_texture_a()

    idx_after_f2 = vp._pbo_a_index
    assert idx_after_f2 == 0

    # Both PBO buffers in the double-buffer pair must be allocated
    assert vp._pbo_a[0] is not None and vp._pbo_a[0].isCreated()
    assert vp._pbo_a[1] is not None and vp._pbo_a[1].isCreated()
