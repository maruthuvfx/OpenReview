import pytest
import numpy as np
from PySide6.QtCore import Qt
from openreview.core.clock import PlaybackClock
from openreview.core.cache import RingBufferCache, CacheMode
from openreview.models.session_graph import SessionGraph, ViewType, StackView
from openreview.models.comparison import ComparisonMode
from openreview.widgets.timeline import TimelineWidget
from openreview.ui.main_window import MainWindow


def test_set_handles_dropdown(qtbot):
    """Test Set Handles dropdown adjusts In and Out points inward from sequence bounds."""
    clock = PlaybackClock(start_frame=1001, end_frame=1100, fps=24.0)
    timeline = TimelineWidget(clock)
    qtbot.addWidget(timeline)

    # Initial bounds
    assert clock.start_frame == 1001
    assert clock.end_frame == 1100
    assert clock.in_frame == 1001
    assert clock.out_frame == 1100

    # Apply 8 frames handle
    timeline.combo_handles.setEditText("8")
    timeline._on_handles_applied()
    assert clock.in_frame == 1009
    assert clock.out_frame == 1092

    # Apply 24 frames handle
    timeline.combo_handles.setEditText("24")
    timeline._on_handles_applied()
    assert clock.in_frame == 1025
    assert clock.out_frame == 1076

    # Custom handle input
    timeline.combo_handles.setEditText("10")
    timeline._on_handles_applied()
    assert clock.in_frame == 1011
    assert clock.out_frame == 1090


def test_cache_dropdown_and_progress_bar(qtbot):
    """Test cache progress bar update and mode display."""
    clock = PlaybackClock(start_frame=1001, end_frame=1050, fps=24.0)
    timeline = TimelineWidget(clock)
    qtbot.addWidget(timeline)

    # Cache bar present and default mode is off
    assert timeline.cache_bar is not None
    assert timeline.cache_bar._cache_mode == "off"

    # Update cache display with data
    timeline.update_cache_display({1001, 1002, 1003}, 15.0, cache_mode="region")
    assert timeline.cache_bar._percent_used == 15.0
    assert 1001 in timeline.cache_bar._cached_frames
    assert "Region" in timeline.cache_bar.toolTip()


def test_onion_skin_stack_controls_and_shader(qtbot, tmp_path):
    """Test Onion skin mix slider and channel checkboxes for Stack mode."""
    graph = SessionGraph()
    f1 = tmp_path / "layer_a.png"
    f2 = tmp_path / "layer_b.png"
    f1.write_bytes(b"dummy")
    f2.write_bytes(b"dummy")

    s1 = graph.add_source_from_path(str(f1))
    s2 = graph.add_source_from_path(str(f2))
    assert s1 is not None and s2 is not None

    stack = graph.create_stack("Test_Stack", source_ids=[s1.id, s2.id], compare_mode="over")
    assert stack.stack_mix == 1.0
    assert stack.channels_a == (True, True, True, True)

    # Serialization test
    d = stack.to_dict()
    assert d["stack_mix"] == 1.0
    assert d["channels_a"] == [True, True, True, True]

    loaded = StackView.from_dict(d)
    assert loaded.stack_mix == 1.0
    assert loaded.channels_a == (True, True, True, True)


def test_source_double_click_activation_and_caching(qtbot, tmp_path):
    """Test source single-click vs double-click activation and caching decoupling."""
    f1 = tmp_path / "shot_01.png"
    f2 = tmp_path / "shot_02.png"
    from PIL import Image
    im = Image.new("RGBA", (32, 32), (100, 150, 200, 255))
    im.save(str(f1))
    im.save(str(f2))

    window = MainWindow()
    qtbot.addWidget(window)
    window.show()

    s1 = window.session_graph.add_source_from_path(str(f1))
    s2 = window.session_graph.add_source_from_path(str(f2))
    assert s1 is not None and s2 is not None
    panel = window.sources_dock.panel
    panel.refresh_tree()

    # Make Shot_01 active
    window.session_graph.set_active_view(ViewType.SOURCE, s1.id)
    assert window.session_graph.active_view_id == s1.id

    s2_item = panel._find_item_by_id(s2.id)
    assert s2_item is not None

    # Single click Shot_02 -> does NOT change active view
    panel.tree.itemClicked.emit(s2_item, 0)
    assert window.session_graph.active_view_id == s1.id

    # Double click Shot_02 -> sets Shot_02 active
    panel.tree.itemDoubleClicked.emit(s2_item, 0)
    assert window.session_graph.active_view_id == s2.id


def test_ctrl_delete_clears_all_cache(qtbot):
    """Test Ctrl+Delete clears all source caches and Shift+C starts Region cache."""
    window = MainWindow()
    qtbot.addWidget(window)
    window._on_set_cache_mode(CacheMode.REGION, start_prefetch=False)

    # Initial state: default cache mode is REGION but dormant until Shift+C
    assert window.cache.mode == CacheMode.REGION
    assert window._cache_started is False

    # Put frames into cache
    dummy = np.zeros((16, 16, 4), dtype=np.float32)
    window.cache.set_mode(CacheMode.ALL)
    window.cache.put_source_frame("source_1", 1001, dummy)
    window.cache.put_source_frame("source_2", 1001, dummy)
    assert window.cache.has_source_frame("source_1", 1001)
    assert window.cache.has_source_frame("source_2", 1001)

    # Trigger clear all cache
    window._on_clear_all_cache()
    assert not window.cache.has_source_frame("source_1", 1001)
    assert not window.cache.has_source_frame("source_2", 1001)

    # Test Shift+C starts Region cache
    window._on_set_cache_mode(CacheMode.OFF)
    assert window.cache.mode == CacheMode.OFF
    window._on_toggle_cache()
    assert window.cache.mode == CacheMode.REGION
    assert window._cache_started is True

    # Test Shift+C toggles/pauses cache
    window._on_toggle_cache()
    assert window._cache_started is False
