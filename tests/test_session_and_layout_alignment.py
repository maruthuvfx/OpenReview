"""
Unit and Integration Tests for Atomic Session Clearing, In/Out Range Persistence, and Layout Start Frame Alignment.
"""
import pytest
from PySide6.QtCore import Qt

from openreview.models.session_graph import SessionGraph, SourceItem, LayoutView, ViewType
from openreview.ui.main_window import MainWindow


def test_layout_start_frame_alignment_and_tile_resolution():
    graph = SessionGraph()
    # Source A: 1001-1050 (duration 50)
    s1 = SourceItem(name="Shot01", start_frame=1001, end_frame=1050)
    # Source B: 1-100 (duration 100)
    s2 = SourceItem(name="Shot02", start_frame=1, end_frame=100)
    # Source C: 201-260 (duration 60)
    s3 = SourceItem(name="Shot03", start_frame=201, end_frame=260)

    graph.sources[s1.id] = s1
    graph.sources[s2.id] = s2
    graph.sources[s3.id] = s3

    lay = LayoutView(name="Grid", source_ids=[s1.id, s2.id, s3.id], align_mode="frame_1")

    # 1. Default "frame_1" alignment: timeline runs 1 .. max_duration (100)
    start_f, end_f = lay.get_timeline_range(graph.sources)
    assert start_f == 1
    assert end_f == 100

    # At timeline frame 1: all tiles should play from their respective start frames
    assert lay.resolve_tile_frame(s1, 1, graph.sources) == 1001
    assert lay.resolve_tile_frame(s2, 1, graph.sources) == 1
    assert lay.resolve_tile_frame(s3, 1, graph.sources) == 201

    # At timeline frame 25: all tiles should be offset by 24 frames
    assert lay.resolve_tile_frame(s1, 25, graph.sources) == 1025
    assert lay.resolve_tile_frame(s2, 25, graph.sources) == 25
    assert lay.resolve_tile_frame(s3, 25, graph.sources) == 225

    # At timeline frame 70: s1 (50 frames) clamps to its last frame 1050; s3 (60 frames) clamps to 260
    assert lay.resolve_tile_frame(s1, 70, graph.sources) == 1050
    assert lay.resolve_tile_frame(s2, 70, graph.sources) == 70
    assert lay.resolve_tile_frame(s3, 70, graph.sources) == 260

    # 2. "min_source" alignment: smallest start frame is 1 (from s2)
    lay.align_mode = "min_source"
    start_f, end_f = lay.get_timeline_range(graph.sources)
    assert start_f == 1
    assert end_f == 100

    # If sources only had s1 (1001-1050) and s3 (201-260), min_source should be 201
    lay2 = LayoutView(name="Grid2", source_ids=[s1.id, s3.id], align_mode="min_source")
    start_f2, end_f2 = lay2.get_timeline_range(graph.sources)
    assert start_f2 == 201
    assert end_f2 == 201 + 60 - 1  # 260
    assert lay2.resolve_tile_frame(s1, 201, graph.sources) == 1001
    assert lay2.resolve_tile_frame(s3, 201, graph.sources) == 201


def test_in_out_range_auto_snap_and_persistence(qtbot):
    win = MainWindow()
    qtbot.addWidget(win)

    s1 = SourceItem(name="ShotA", start_frame=1001, end_frame=1050)  # duration 50
    s2 = SourceItem(name="ShotB", start_frame=1, end_frame=200)      # duration 200
    win.session_graph.sources[s1.id] = s1
    win.session_graph.sources[s2.id] = s2

    # 1. Switch to ShotA without manual in/out: automatically sets in=1001, out=1050
    win.session_graph.set_active_view(ViewType.SOURCE, s1.id)
    assert win.clock.start_frame == 1001
    assert win.clock.end_frame == 1050
    assert win.clock.in_frame == 1001
    assert win.clock.out_frame == 1050
    assert s1.manual_in is None
    assert s1.manual_out is None

    # 2. User sets manual in=1010, out=1030 on ShotA
    win.clock.set_in_out(1010, 1030)
    assert s1.manual_in == 1010
    assert s1.manual_out == 1030

    # 3. Switch to ShotB (length 200): automatically snaps to full duration 1..200
    win.session_graph.set_active_view(ViewType.SOURCE, s2.id)
    assert win.clock.start_frame == 1
    assert win.clock.end_frame == 200
    assert win.clock.in_frame == 1
    assert win.clock.out_frame == 200

    # 4. Switch back to ShotA: restores user's manual In/Out 1010..1030
    win.session_graph.set_active_view(ViewType.SOURCE, s1.id)
    assert win.clock.start_frame == 1001
    assert win.clock.end_frame == 1050
    assert win.clock.in_frame == 1010
    assert win.clock.out_frame == 1030

    # 5. User clears In/Out on ShotA: resets to full duration
    win.clock.clear_in_out()
    assert s1.manual_in is None
    assert s1.manual_out is None
    assert win.clock.in_frame == 1001
    assert win.clock.out_frame == 1050

    win.close()


def test_atomic_session_reset(qtbot):
    win = MainWindow()
    qtbot.addWidget(win)

    s1 = SourceItem(name="ShotA", start_frame=1001, end_frame=1050)
    win.session_graph.sources[s1.id] = s1
    win.session_graph.set_active_view(ViewType.SOURCE, s1.id)
    win.sources_dock.panel.refresh_tree()

    assert len(win.session_graph.sources) == 1

    # Perform atomic session reset
    win._perform_atomic_session_reset()

    # Verify everything is cleanly cleared
    assert len(win.session_graph.sources) == 0
    assert len(win._source_readers) == 0
    assert win.reader is None
    assert win.clock.start_frame == 1001
    assert win.clock.end_frame == 1200
    assert win.clock.in_frame == 1001
    assert win.clock.out_frame == 1200
    assert win.sources_dock.panel.list_inputs.count() == 0

    win.close()


def test_sequence_media_reader_set_gap_handling():
    from openreview.core.sequence_parser import SequenceInfo, DiskSequenceReader

    seq = SequenceInfo(
        directory="fake_dir",
        prefix="shot",
        separator=".",
        padding=4,
        extension=".exr",
        start_frame=1001,
        end_frame=1005,
        available_frames={1001, 1002, 1005},
        missing_frames={1003, 1004},
    )
    reader = DiskSequenceReader(seq)
    # Target frame is before the available range (e.g., frame 1)
    # Previously, this crashed with TypeError: 'set' object is not subscriptable
    frame_data = reader.read_frame(1)
    assert frame_data is not None
    assert frame_data.shape == (reader._cached_height, reader._cached_width, 4)


def test_layout_view_frame_changed_fetch(qtbot):
    win = MainWindow()
    qtbot.addWidget(win)

    s1 = SourceItem(name="ShotA", start_frame=1001, end_frame=1050)
    win.session_graph.sources[s1.id] = s1
    lay = LayoutView(name="Grid", source_ids=[s1.id], align_mode="frame_1")
    win.session_graph.layouts[lay.id] = lay

    win.session_graph.set_active_view(ViewType.LAYOUT, lay.id)
    # Trigger frame changed at timeline frame 1
    win._on_frame_changed(1)

    assert win.clock.start_frame == 1
    assert win.clock.end_frame == 50
    win.close()
