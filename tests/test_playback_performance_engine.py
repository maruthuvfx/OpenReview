"""
Unit tests for Track A: Real-Time Playback Performance Engine.
Covers:
- Hardware-Accelerated Video Decoding via PyAV HWAccel & direct fallback
- Multi-Threaded OpenEXR Global Decompression Engine
- Float16 Memory-Compressed RAM Ring Cache & 50% Memory Reduction
- Dynamic LOD / Sub-Sampled Scrubbing & Crisp Full-Res Settle
"""
import os
import sys
import tempfile
import numpy as np
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from openreview.core.video_reader import (
    get_available_hwaccel_devices,
    create_hwaccel_context,
    PyAVVideoReader,
    UnifiedVideoReader,
    HAS_AV,
)
from openreview.core.cache import RingBufferCache, CacheMode
from openreview.core.preferences import PreferencesManager
from openreview.core.image_io import read_exr, HAS_OPENEXR


def test_hwaccel_detection_and_context():
    """Verify hardware acceleration device enumeration and context creation."""
    if not HAS_AV:
        pytest.skip("PyAV not installed")

    devs = get_available_hwaccel_devices()
    assert isinstance(devs, list)

    hw, dev_name = create_hwaccel_context()
    if devs:
        assert hw is not None
        assert dev_name in devs
        assert getattr(hw, "allow_software_fallback", True) is True

    # Test preference disable override
    prefs = PreferencesManager.instance().prefs
    orig_val = prefs.hardware_video_decoding
    try:
        prefs.hardware_video_decoding = False
        hw_disabled, name_disabled = create_hwaccel_context()
        assert hw_disabled is None
        assert name_disabled is None
    finally:
        prefs.hardware_video_decoding = orig_val


def test_pyav_video_reader_hwaccel():
    """Verify PyAVVideoReader opens video with hardware decoding context."""
    if not HAS_AV:
        pytest.skip("PyAV not installed")

    import av

    # Create a synthetic 12-frame H.264 video
    tmp_path = os.path.join(tempfile.gettempdir(), "test_hwaccel_playback.mp4")
    try:
        out = av.open(tmp_path, mode="w")
        stream = out.add_stream("h264", rate=24)
        stream.width = 160
        stream.height = 120
        stream.pix_fmt = "yuv420p"

        for i in range(12):
            img = np.zeros((120, 160, 3), dtype=np.uint8)
            img[:, :, 0] = i * 20
            img[:, :, 1] = 128
            frame = av.VideoFrame.from_ndarray(img, format="bgr24")
            for pkt in stream.encode(frame):
                out.mux(pkt)
        for pkt in stream.encode():
            out.mux(pkt)
        out.close()

        # Open with UnifiedVideoReader / PyAVVideoReader
        reader = UnifiedVideoReader(tmp_path)
        assert reader.backend == "PyAV"
        assert reader.total_frames >= 10
        assert reader.width == 160
        assert reader.height == 120

        # Read first and middle frames
        f1 = reader.read_frame(1)
        assert f1 is not None
        assert f1.shape == (120, 160, 4)
        assert f1.dtype == np.uint8

        f6 = reader.read_frame(6)
        assert f6 is not None
        assert f6.shape == (120, 160, 4)

        reader.close()
    finally:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except Exception:
                pass


def test_openexr_multithreading_and_float16():
    """Verify OpenEXR global multi-threading is activated and Float16 decode path operates."""
    if not HAS_OPENEXR:
        pytest.skip("OpenEXR not installed")

    import OpenEXR
    import Imath

    # Verify global thread count was configured to multi-core
    threads = OpenEXR.global_thread_count()
    expected_min = max(2, min(os.cpu_count() or 4, 64))
    assert threads >= 2, f"Expected multi-threaded decompression, got {threads}"

    # Write a synthetic test EXR file
    tmp_path = os.path.join(tempfile.gettempdir(), "test_perf_f16.exr")
    try:
        w, h = 64, 48
        header = OpenEXR.Header(w, h)
        pt = Imath.PixelType(Imath.PixelType.HALF)
        header["channels"] = {
            "R": Imath.Channel(pt),
            "G": Imath.Channel(pt),
            "B": Imath.Channel(pt),
            "A": Imath.Channel(pt),
        }
        r_data = (np.ones((h, w), dtype=np.float16) * 0.75).tobytes()
        g_data = (np.ones((h, w), dtype=np.float16) * 0.50).tobytes()
        b_data = (np.ones((h, w), dtype=np.float16) * 0.25).tobytes()
        a_data = np.ones((h, w), dtype=np.float16).tobytes()

        out_file = OpenEXR.OutputFile(tmp_path, header)
        out_file.writePixels({"R": r_data, "G": g_data, "B": b_data, "A": a_data})
        out_file.close()

        # Read with as_f16=True (Direct Float16 decode)
        f16_arr = read_exr(tmp_path, as_f16=True)
        assert f16_arr is not None
        assert f16_arr.dtype == np.float16
        assert f16_arr.shape == (h, w, 4)
        np.testing.assert_allclose(f16_arr[0, 0, :3], [0.75, 0.50, 0.25], atol=1e-2)

        # Read with as_f16=False (Float32 path)
        f32_arr = read_exr(tmp_path, as_f16=False)
        assert f32_arr is not None
        assert f32_arr.dtype == np.float32
        assert f32_arr.shape == (h, w, 4)
    finally:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except Exception:
                pass


def test_float16_cache_compression():
    """Verify Float16 memory compression cuts RAM consumption by 50% in cache."""
    cache = RingBufferCache(max_memory_mb=128.0, mode=CacheMode.PLAY_AHEAD)

    w, h = 200, 100
    # Create Float32 frame (200 x 100 x 4 x 4 = 320,000 bytes)
    f32_frame = np.ones((h, w, 4), dtype=np.float32) * 0.42

    cache.put_source_frame("source_test", 1001, f32_frame)

    cached = cache.get_source_frame("source_test", 1001)
    assert cached is not None
    # Must be stored compressed as float16
    assert cached.dtype == np.float16
    assert cached.nbytes == 200 * 100 * 4 * 2  # 160,000 bytes (half of 320,000!)
    np.testing.assert_allclose(cached[0, 0, 0], 0.42, atol=1e-3)

    # Check put_frame as well with compress_f16=True
    cache.put_frame(1002, f32_frame, compress_f16=True)
    cached_tl = cache.get_frame(1002)
    assert cached_tl is not None
    assert cached_tl.dtype == np.float16
    assert cached_tl.nbytes == 160000

    cache.clear()


def test_dynamic_lod_scrubbing_and_settle():
    """Verify dynamic LOD scrubbing preference and scrub settled snapback logic."""
    cache = RingBufferCache(max_memory_mb=128.0, mode=CacheMode.PLAY_AHEAD)

    # Store full-res and proxy-tagged frames
    full_res = np.ones((100, 100, 4), dtype=np.float16)
    proxy_res = np.ones((50, 50, 4), dtype=np.float16)

    cache.put_source_frame("src_lod", 1001, full_res, is_proxy=False)
    cache.put_source_frame("src_lod", 1002, proxy_res, is_proxy=True)

    assert cache.is_source_frame_proxy("src_lod", 1001) is False
    assert cache.is_source_frame_proxy("src_lod", 1002) is True

    # When allow_proxy is False, proxy frame 1002 should not be returned as final display
    assert cache.get_source_frame("src_lod", 1002, allow_proxy=False) is None
    # When allow_proxy is True, proxy frame is returned
    assert cache.get_source_frame("src_lod", 1002, allow_proxy=True) is not None

    cache.clear()
