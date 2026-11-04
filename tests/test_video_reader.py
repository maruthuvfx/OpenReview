"""
Unit tests for VideoMediaReader and video container formats.
"""
import os
import cv2
import numpy as np
import pytest
from openreview.core.video_reader import VideoMediaReader, is_video_file, VIDEO_EXTENSIONS


def test_is_video_file():
    assert is_video_file("render.mp4")
    assert is_video_file("/path/to/shot_010.mov")
    assert is_video_file("dailies.mkv")
    assert is_video_file("clip.avi")
    assert is_video_file("web_cut.webm")
    assert is_video_file("movie.flv")
    assert is_video_file("comp.wmv")

    assert not is_video_file("render.1001.exr")
    assert not is_video_file("plate.png")
    assert not is_video_file("texture.jpg")
    assert not is_video_file("")
    assert not is_video_file(None)


def test_video_reader_synthetic_stream(tmp_path):
    vid_path = str(tmp_path / "synthetic_test.mp4")
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(vid_path, fourcc, 25.0, (128, 72))
    assert writer.isOpened(), "Failed to initialize cv2.VideoWriter"

    for i in range(12):
        img = np.zeros((72, 128, 3), dtype=np.uint8)
        img[:, :] = (i * 20, 100, 200)
        writer.write(img)
    writer.release()

    assert os.path.exists(vid_path)

    reader = VideoMediaReader(vid_path)
    assert reader.fps == 25.0
    assert reader.total_frames >= 12
    assert reader.width == 128
    assert reader.height == 72

    # Fast uint8 Sequential Read (default for maximum playback FPS)
    f1 = reader.read_frame(1)
    assert f1 is not None
    assert f1.shape == (72, 128, 4)
    assert f1.dtype in (np.uint8, np.float32)

    f2 = reader.read_frame(2)
    assert f2 is not None
    assert f2.shape == (72, 128, 4)

    # Random Seek
    f10 = reader.read_frame(10)
    assert f10 is not None
    assert f10.shape == (72, 128, 4)

    reader.close()

    # Test explicit float32 reader mode
    reader_f = VideoMediaReader(vid_path, as_float=True)
    f1_f = reader_f.read_frame(1)
    assert f1_f is not None
    assert f1_f.dtype == np.float32
    assert 0.0 <= np.max(f1_f) <= 1.0
    reader_f.close()


def test_all_requested_video_codecs_and_extensions():
    """Verify support for all 25 requested video codec/FourCC groups and stream extensions."""
    from openreview.core.video_reader import is_supported_video_codec, get_video_codec_info

    requested_codecs = [
        "wmv3", "wvc1", "wmva",
        "wmv1", "wmv2",
        "thra",
        "SVQ 3",
        "SVQ 1",
        "RV30", "RV40",
        "RV10", "RV13", "RV20",
        "VP7",
        "VP60", "VP61", "VP62", "VP6F", "VP6A",
        "VP50", "VP5", "VP51",
        "VP31", "VP30", "VP3",
        "DIVX", "Xvid", "mp4s", "m4s2", "xvid", "mp4v", "fmp4", "3iv2", "smp4",
        "mp2v", "mpg2", "vcr2", "hdv1", "hdv2", "hdv3", "mx*n", "mx*p", "mx3n", "mx4n", "mx5n", "mx3p", "mx4p", "mx5p",
        "mpeg", "mp1v", "mpg1", "PIM1",
        "IV41", "IV51",
        "IV31", "IV32",
        "h264", "s264", "AVC1", "DAVC", "H264", "X264", "VSSH",
        "h263",
        "h262",
        "h261",
        "FSV1",
        "DIV4", "DIV5", "DIV6", "col1", "col0", "3ivd",
        "DIV1", "DIV2", "DIV3", "mp41", "mp42", "MPG4", "MPG3",
        "BBCD",
        "cvid",
    ]

    for codec in requested_codecs:
        assert is_supported_video_codec(codec), f"Codec {codec} should be supported"
        dec, desc = get_video_codec_info(codec)
        assert dec != "unknown"
        assert desc

    # Verify file extension recognition including mx*n / mx*p wildcards
    for ext in [
        "wmv3", "wvc1", "wmva", "wmv1", "wmv2", "thra", "svq3", "svq1",
        "rv30", "rv40", "rv10", "rv13", "rv20", "vp7", "vp60", "vp61", "vp62", "vp6f", "vp6a",
        "vp50", "vp5", "vp51", "vp31", "vp30", "vp3", "divx", "xvid", "mp4s", "m4s2", "mp4v",
        "fmp4", "3iv2", "smp4", "mp2v", "mpg2", "vcr2", "hdv1", "hdv2", "hdv3", "mx3n", "mx5p",
        "mpeg", "mp1v", "mpg1", "pim1", "iv41", "iv51", "iv31", "iv32", "h264", "s264", "avc1",
        "davc", "x264", "vssh", "h263", "h262", "h261", "fsv1", "div4", "div5", "div6", "col1",
        "col0", "3ivd", "div1", "div2", "div3", "mp41", "mp42", "mpg4", "mpg3", "bbcd", "cvid",
    ]:
        assert is_video_file(f"stream.{ext}"), f"Extension .{ext} should be recognized as video"



def test_pyav_video_reader_scrub_and_gop_cache(tmp_path):
    """Verify PyAVVideoReader forward seek, backward scrub, GOP caching, and frame clamping."""
    vid_path = str(tmp_path / "pyav_gop_test.mp4")
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(vid_path, fourcc, 24.0, (160, 120))
    for i in range(48):
        img = np.zeros((120, 160, 3), dtype=np.uint8)
        img[:, :] = (i * 5, 120, 200)
        writer.write(img)
    writer.release()

    from openreview.core.video_reader import PyAVVideoReader, HAS_AV
    if not HAS_AV:
        pytest.skip("PyAV is not installed")

    reader = PyAVVideoReader(vid_path)
    assert reader.backend == "PyAV"
    assert reader.fps == 24.0
    assert reader.total_frames >= 48
    assert reader.width == 160
    assert reader.height == 120

    # Sequential playback
    for f in range(1, 6):
        frame = reader.read_frame(f)
        assert frame is not None
        assert frame.shape == (120, 160, 4)
        assert frame.dtype == np.uint8

    # Jump seek to frame 30
    frame_30 = reader.read_frame(30)
    assert frame_30 is not None
    assert frame_30.shape == (120, 160, 4)

    # Scrub backwards (hits GOP cache)
    frame_29 = reader.read_frame(29)
    assert frame_29 is not None
    frame_28 = reader.read_frame(28)
    assert frame_28 is not None

    # Bounds clamping
    f_zero = reader.read_frame(0)
    assert f_zero is not None
    f_overflow = reader.read_frame(1000)
    assert f_overflow is not None

    reader.close()


def test_video_reader_backend_selection(tmp_path):
    """Verify VideoMediaReader selects PyAV by default and honors force_backend='opencv'."""
    vid_path = str(tmp_path / "backend_test.mp4")
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(vid_path, fourcc, 24.0, (64, 48))
    for i in range(10):
        img = np.zeros((48, 64, 3), dtype=np.uint8)
        writer.write(img)
    writer.release()

    from openreview.core.video_reader import VideoMediaReader, HAS_AV

    # Default backend
    reader_default = VideoMediaReader(vid_path)
    if HAS_AV:
        assert reader_default.backend == "PyAV"
    else:
        assert reader_default.backend == "OpenCV"
    reader_default.close()

    # Force OpenCV backend
    reader_cv = VideoMediaReader(vid_path, force_backend="opencv")
    assert reader_cv.backend == "OpenCV"
    assert reader_cv.fps == 24.0
    f = reader_cv.read_frame(1)
    assert f is not None
    assert f.shape == (48, 64, 4)
    reader_cv.close()


def test_video_metadata_extraction_pyav(tmp_path):
    """Verify PyAV metadata extraction extracts stream parameters without external CLI."""
    vid_path = str(tmp_path / "meta_test.mp4")
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(vid_path, fourcc, 30.0, (192, 108))
    for i in range(15):
        img = np.zeros((108, 192, 3), dtype=np.uint8)
        writer.write(img)
    writer.release()

    from openreview.core.metadata import extract_media_metadata
    meta = extract_media_metadata(vid_path)
    assert meta["width"] == 192
    assert meta["height"] == 108
    assert meta.get("fps") == 30.0 or meta.get("fps_str") in ("30/1", "30.0")
    assert meta["nb_frames"] >= 15
    assert "video_codec" in meta
