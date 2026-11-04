"""
Unit tests for image sequence auto-detection and gap-tolerant reader.
"""
import os
import tempfile
import pytest
from PIL import Image

from openreview.core.sequence_parser import (
    detect_image_sequence,
    DiskSequenceReader,
    SequenceInfo,
)


def test_sequence_auto_detection():
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create dummy sequence: shot.1001.png, shot.1002.png, shot.1005.png (gap: 1003, 1004)
        for frame in [1001, 1002, 1005]:
            img = Image.new("RGBA", (32, 32), color=(100, 100, 100, 255))
            fpath = os.path.join(tmpdir, f"shot_comp.{frame:04d}.png")
            img.save(fpath)

        # Detect by passing one file path
        sample_path = os.path.join(tmpdir, "shot_comp.1002.png")
        seq = detect_image_sequence(sample_path)

        assert seq is not None
        assert seq.prefix == "shot_comp"
        assert seq.separator == "."
        assert seq.padding == 4
        assert seq.extension == ".png"
        assert seq.start_frame == 1001
        assert seq.end_frame == 1005
        assert seq.available_frames == {1001, 1002, 1005}
        assert seq.missing_frames == {1003, 1004}

        # Test pattern formatting
        assert "####" in seq.hash_pattern
        assert "%04d" in seq.pattern

        # Test reader with gap tolerance
        reader = DiskSequenceReader(seq)
        f1001 = reader.read_frame(1001)
        assert f1001 is not None
        assert f1001.shape == (32, 32, 4)

        # Frame 1003 is missing in gap: should hold previous frame data
        f1003 = reader.read_frame(1003)
        assert f1003 is not None
        assert f1003.shape == (32, 32, 4)


def test_single_file_not_sequence():
    with tempfile.TemporaryDirectory() as tmpdir:
        single_path = os.path.join(tmpdir, "texture_diffuse.png")
        img = Image.new("RGBA", (16, 16))
        img.save(single_path)

        seq = detect_image_sequence(single_path)
        assert seq is None


def test_rust_native_sequence_scanner():
    """Verify openreview_engine.scan_sequence directly on various pattern conventions."""
    import openreview_engine
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create sequence with underscore separator
        for f in (50, 51, 52, 55):
            with open(os.path.join(tmpdir, f"render_v2_{f:04d}.exr"), "w") as fp:
                fp.write("dummy")

        # 1. Scan from directory
        info = openreview_engine.scan_sequence(tmpdir)
        assert info is not None
        assert info.prefix == "render_v2"
        assert info.separator == "_"
        assert info.padding == 4
        assert info.extension == ".exr"
        assert info.start_frame == 50
        assert info.end_frame == 55
        assert info.available_frames == [50, 51, 52, 55]
        assert info.missing_frames == [53, 54]
        assert "render_v2_####.exr" in info.hash_pattern
        assert "render_v2_%04d.exr" in info.pattern

        # 2. Scan from specific file path
        sample = os.path.join(tmpdir, "render_v2_0051.exr")
        info_file = openreview_engine.scan_sequence(sample)
        assert info_file is not None
        assert info_file.start_frame == 50
        assert info_file.end_frame == 55


def test_detect_sequence_from_directory():
    """Verify detect_image_sequence works when passed a directory path."""
    with tempfile.TemporaryDirectory() as tmpdir:
        for f in range(1, 6):
            with open(os.path.join(tmpdir, f"plate.{f:04d}.dpx"), "w") as fp:
                fp.write("dummy")

        seq = detect_image_sequence(tmpdir)
        assert seq is not None
        assert seq.prefix == "plate"
        assert seq.start_frame == 1
        assert seq.end_frame == 5
        assert len(seq.missing_frames) == 0


def test_bare_numbered_sequence():
    """Verify numbered sequences without prefix (e.g. 1001.exr)."""
    import openreview_engine
    with tempfile.TemporaryDirectory() as tmpdir:
        for f in (1001, 1002, 1003):
            with open(os.path.join(tmpdir, f"{f}.exr"), "w") as fp:
                fp.write("dummy")

        info = openreview_engine.scan_sequence(os.path.join(tmpdir, "1002.exr"))
        assert info is not None
        assert info.prefix == ""
        assert info.separator == ""
        assert info.padding == 4
        assert info.start_frame == 1001
        assert info.end_frame == 1003
