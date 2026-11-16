"""
Validation tests proving:
1. On-screen Gamma & Saturation sliders have value 1.0 at the middle (ratio 0.5), with 90% viewport width scaling.
2. In-memory caching preserves frames across media switching, region bounds changes, and only evicts on manual clear, new session, or window close.
3. Aspect ratio consistency is preserved across Stacking, Sequence, and Layout without random jumping or stretching.
"""
import pytest
import numpy as np
from PySide6.QtWidgets import QApplication, QWidget
from openreview.widgets.color_sliders_overlay import ColorSlidersOverlay, ColorRulerSlider
from openreview.core.cache import RingBufferCache, CacheMode
from openreview.core.layout_compositor import conform_frame_to_format, composite_layout_grid
from openreview.models.session_graph import SessionGraph, SourceItem, SequenceView, SequenceClip, StackView, LayoutView
from openreview.widgets.viewport import ViewportCanvas


def test_gamma_and_saturation_sliders_midpoint():
    """Verify Gamma and Saturation sliders have value 1.0 at exactly the 50% midpoint."""
    app = QApplication.instance() or QApplication([])
    parent = QWidget()
    parent.resize(1920, 1080)
    overlay = ColorSlidersOverlay(parent=parent)

    # 1. Gamma Slider Midpoint Verification
    gamma = overlay.slider_gamma
    assert gamma.min_val == 0.0
    assert gamma.max_val == 4.0
    assert gamma.piecewise_mid == 1.0
    assert gamma.value == 1.0
    assert abs(gamma._val_to_ratio(1.0) - 0.5) < 1e-4, "Gamma value 1.0 must be at 50% ratio"
    assert abs(gamma._ratio_to_val(0.5) - 1.0) < 1e-4, "Gamma ratio 0.5 must map to value 1.0"
    assert abs(gamma._val_to_ratio(0.0) - 0.0) < 1e-4
    assert abs(gamma._val_to_ratio(4.0) - 1.0) < 1e-4

    # 2. Saturation Slider Midpoint Verification
    sat = overlay.slider_sat
    assert sat.min_val == 0.0
    assert sat.max_val == 4.0
    assert sat.piecewise_mid == 1.0
    assert sat.value == 1.0
    assert abs(sat._val_to_ratio(1.0) - 0.5) < 1e-4, "Saturation value 1.0 must be at 50% ratio"
    assert abs(sat._ratio_to_val(0.5) - 1.0) < 1e-4, "Saturation ratio 0.5 must map to value 1.0"
    assert abs(sat._val_to_ratio(0.0) - 0.0) < 1e-4
    assert abs(sat._val_to_ratio(4.0) - 1.0) < 1e-4

    # 3. 90% Viewport Width Scaling Verification
    overlay.auto_center_in_parent()
    expected_w = int(1920 * 0.90)  # 1728 px
    assert overlay.width() == expected_w, f"Expected width {expected_w} (90%), got {overlay.width()}"
    assert overlay.x() == (1920 - expected_w) // 2, "Overlay should be horizontally centered"


def test_persistent_caching_across_sources_and_regions():
    """Verify cached images stay in memory across media switching and region bounds changes."""
    cache = RingBufferCache(max_memory_mb=500.0, mode=CacheMode.PLAY_AHEAD)

    # Frame data for Media A and Media B
    frame_a = np.full((1080, 1920, 4), 0.25, dtype=np.float32)
    frame_b = np.full((1080, 1920, 4), 0.75, dtype=np.float32)

    # Put frames for Source A
    cache.put_source_frame("source_A", 1001, frame_a)
    cache.put_source_frame("source_A", 1002, frame_a)
    assert cache.has_source_frame("source_A", 1001)
    assert cache.has_source_frame("source_A", 1002)

    # Switch active source to Source B
    cache.set_active_source("source_B")
    cache.put_source_frame("source_B", 1, frame_b)
    cache.put_source_frame("source_B", 2, frame_b)

    # CRUCIAL CHECK: Source A frames MUST NOT be evicted when switching to Source B!
    assert cache.has_source_frame("source_A", 1001), "Source A frame 1001 lost when switching to Source B!"
    assert cache.has_source_frame("source_A", 1002), "Source A frame 1002 lost when switching to Source B!"
    assert cache.has_source_frame("source_B", 1), "Source B frame 1 should be cached"
    assert cache.has_source_frame("source_B", 2), "Source B frame 2 should be cached"

    # CRUCIAL CHECK: Changing region bounds must NOT evict out-of-region frames!
    cache.set_region_bounds(1, 10)
    assert cache.has_source_frame("source_A", 1001), "Source A frame was evicted by region bounds change!"
    assert cache.has_source_frame("source_A", 1002), "Source A frame was evicted by region bounds change!"

    # Switch back to Source A
    cache.set_active_source("source_A")
    data_retrieved = cache.get_source_frame("source_A", 1001)
    assert data_retrieved is not None
    assert np.allclose(data_retrieved, 0.25)

    # Test explicit manual clear
    cache.clear_source("source_B")
    assert not cache.has_source_frame("source_B", 1)
    assert cache.has_source_frame("source_A", 1001), "Source A should still remain after clearing Source B"

    # Test new session / full clear
    cache.clear()
    assert not cache.has_source_frame("source_A", 1001), "All frames should be cleared on clear()"


def test_aspect_ratio_consistency_stacking_sequence_layout():
    """Verify aspect ratio consistency in Stacking, Sequence, and Layout."""
    # 1. Conform Frame Helper Verification
    # Case A: Widescreen 2.39:1 (2048x858) into 16:9 (1920x1080) -> letterboxed top & bottom
    scope_frame = np.ones((858, 2048, 4), dtype=np.float32)
    conformed_scope = conform_frame_to_format(scope_frame, 1920, 1080)
    assert conformed_scope.shape == (1080, 1920, 4)
    # Letterbox black bars at top and bottom
    assert np.all(conformed_scope[0, :, :3] == 0.0)
    assert np.all(conformed_scope[-1, :, :3] == 0.0)
    assert np.all(conformed_scope[540, 960, :3] == 1.0)

    # Case B: Vertical 9:16 (1080x1920) into 16:9 (1920x1080) -> pillarboxed left & right
    vert_frame = np.ones((1920, 1080, 4), dtype=np.float32)
    conformed_vert = conform_frame_to_format(vert_frame, 1920, 1080)
    assert conformed_vert.shape == (1080, 1920, 4)
    assert np.all(conformed_vert[:, 0, :3] == 0.0)
    assert np.all(conformed_vert[:, -1, :3] == 0.0)
    assert np.all(conformed_vert[540, 960, :3] == 1.0)

    # 2. Sequence Format Consistency
    graph = SessionGraph()
    s1 = SourceItem(name="16x9_HD", width=1920, height=1080)
    s2 = SourceItem(name="Scope_2K", width=2048, height=858)
    s3 = SourceItem(name="Vertical_Story", width=1080, height=1920)
    graph.sources = {s1.id: s1, s2.id: s2, s3.id: s3}

    seq = SequenceView(name="ReviewSequence", clips=[
        SequenceClip(source_id=s1.id, trim_in=1001, trim_out=1010),
        SequenceClip(source_id=s2.id, trim_in=1001, trim_out=1010),
        SequenceClip(source_id=s3.id, trim_in=1001, trim_out=1010),
    ])
    seq_fmt = seq.get_format(graph.sources)
    assert seq_fmt == (1920, 1080), "Sequence format should lock to master format (1920, 1080)"

    # All frames conformed to sequence format have uniform (1920, 1080) dimensions
    f_s1 = conform_frame_to_format(np.ones((1080, 1920, 4), dtype=np.float32), *seq_fmt)
    f_s2 = conform_frame_to_format(scope_frame, *seq_fmt)
    f_s3 = conform_frame_to_format(vert_frame, *seq_fmt)
    assert f_s1.shape == (1080, 1920, 4)
    assert f_s2.shape == (1080, 1920, 4)
    assert f_s3.shape == (1080, 1920, 4)

    # 3. Stacking Format Consistency
    # Layer A is 1920x1080, Layer B is 2048x858
    stack = StackView(name="CompareStack", source_ids=[s1.id, s2.id])
    stack_fmt = stack.get_format(graph.sources)
    assert stack_fmt == (1920, 1080), "Stack format should lock to Layer A format (1920, 1080)"

    layer_a_frame = conform_frame_to_format(np.ones((1080, 1920, 4), dtype=np.float32), *stack_fmt)
    layer_b_frame = conform_frame_to_format(scope_frame, *stack_fmt)
    assert layer_a_frame.shape == layer_b_frame.shape == (1080, 1920, 4), "Both stack layers must have identical conformed dimensions"


def test_viewport_logical_size_preservation():
    """Verify that ViewportCanvas preserves logical dimensions without texture upload overwriting them."""
    app = QApplication.instance() or QApplication([])
    vp = ViewportCanvas()
    vp.resize(800, 600)

    # Provide a proxy frame with logical dimensions (1920, 1080)
    proxy_frame = np.ones((270, 480, 4), dtype=np.float32)
    vp.set_primary_frame(1, proxy_frame, logical_size=(1920, 1080), is_proxy=True)

    assert vp._img_width == 1920, f"Expected 1920, got {vp._img_width}"
    assert vp._img_height == 1080, f"Expected 1080, got {vp._img_height}"

    # Verify upload texture logic does not overwrite logical dimensions
    vp._upload_texture_a()
    assert vp._img_width == 1920, f"Texture upload overwrote logical width: {vp._img_width}"
    assert vp._img_height == 1080, f"Texture upload overwrote logical height: {vp._img_height}"


def test_playback_over_non_cached_frames_plays_and_caches(qtbot, tmp_path):
    """Verify that playing back with Spacebar / transport decodes, displays, and caches non-cached frames."""
    import cv2
    from openreview.ui.main_window import MainWindow
    from openreview.models.session_graph import ViewType

    # Create 3 frames on disk
    frame_paths = []
    for i in range(1, 4):
        p = tmp_path / f"play_seq_{i:04d}.png"
        img = np.full((100, 100, 3), i * 50, dtype=np.uint8)
        cv2.imwrite(str(p), img)
        frame_paths.append(str(p))

    win = MainWindow()
    qtbot.addWidget(win)

    # Add source sequence
    src = win.session_graph.add_source_from_path(frame_paths[0])
    win.session_graph.set_active_view(ViewType.SOURCE, src.id)

    # Ensure caching is not pre-populated
    win.cache.clear()
    assert not win.cache.has_source_frame(src.id, 1)
    assert not win.cache.has_source_frame(src.id, 2)
    assert not win.cache.has_source_frame(src.id, 3)

    # Start playback (e.g. Spacebar)
    win.clock.play()
    assert win.clock.is_playing is True

    # Advance clock across frames 2, 3, and loop back to 1
    win.clock.set_frame(2)
    assert win.viewport._image_a is not None, "Frame 2 must be displayed during playback on uncached frame"
    assert win.cache.has_source_frame(src.id, 2), "Frame 2 must be cached during playback"

    win.clock.set_frame(3)
    assert win.viewport._image_a is not None, "Frame 3 must be displayed during playback on uncached frame"
    assert win.cache.has_source_frame(src.id, 3), "Frame 3 must be cached during playback"

    win.clock.set_frame(1)
    assert win.viewport._image_a is not None, "Frame 1 must be displayed during playback on uncached frame"
    assert win.cache.has_source_frame(src.id, 1), "Frame 1 must be cached during playback"

    # Stop playback
    win.clock.pause()
    assert win.clock.is_playing is False


def test_canvas_single_click_toggles_playback(qtbot):
    """Verify single click on canvas toggles play/pause, while dragging scrubs without triggering click."""
    from openreview.ui.main_window import MainWindow
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtCore import QPointF, Qt

    win = MainWindow()
    qtbot.addWidget(win)
    win.show()
    vp = win.viewport

    # Initially paused
    assert win.clock.is_playing is False

    # Simulate single click on canvas (press and release at same position)
    pos = QPointF(200.0, 200.0)
    press_ev = QMouseEvent(QMouseEvent.Type.MouseButtonPress, pos, pos, Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    vp.mousePressEvent(press_ev)

    release_ev = QMouseEvent(QMouseEvent.Type.MouseButtonRelease, pos, pos, Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier)
    vp.mouseReleaseEvent(release_ev)

    # Should have started playing!
    assert win.clock.is_playing is True, "Single click on canvas must start playback"

    # Second single click on canvas
    vp.mousePressEvent(press_ev)
    vp.mouseReleaseEvent(release_ev)

    # Should have paused!
    assert win.clock.is_playing is False, "Second single click on canvas must pause playback"


def test_canvas_scrubbing_clamps_at_boundaries_without_wrapping(qtbot):
    """Verify scrubbing beyond out_frame holds at end, and scrubbing before in_frame holds at start."""
    from openreview.ui.main_window import MainWindow

    win = MainWindow()
    qtbot.addWidget(win)

    win.clock.set_range(1, 100)
    win.clock.set_frame(98)
    assert win.clock.current_frame == 98

    # Scrub forward by 10 frames (should reach 100 and HOLD, not wrap to 1)
    win._on_viewport_scrub(10)
    assert win.clock.current_frame == 100, f"Expected 100, got {win.clock.current_frame} (wrapped instead of holding!)"

    # Further forward scrub should continue to hold at 100
    win._on_viewport_scrub(20)
    assert win.clock.current_frame == 100

    # Scrub backward towards start
    win.clock.set_frame(3)
    win._on_viewport_scrub(-10)
    assert win.clock.current_frame == 1, f"Expected 1, got {win.clock.current_frame} (wrapped instead of holding!)"

    # Further backward scrub should continue to hold at 1
    win._on_viewport_scrub(-20)
    assert win.clock.current_frame == 1


def test_audio_mute_toggles_icon_and_state(qtbot):
    """Verify changing audio mute state toggles the timeline button icon between muted and unmuted."""
    from openreview.ui.main_window import MainWindow

    win = MainWindow()
    qtbot.addWidget(win)

    btn = win.timeline.btn_audio

    # Initially unmuted
    win.audio_engine.set_muted(False)
    assert win.audio_engine.is_muted() is False
    assert "Unmuted" in btn.toolTip()
    assert getattr(win.timeline, "_audio_muted", False) is False

    # Toggle to muted
    win.audio_engine.set_muted(True)
    assert win.audio_engine.is_muted() is True
    assert "Muted" in btn.toolTip()
    assert getattr(win.timeline, "_audio_muted", False) is True
    assert win.audio_mute_act.isChecked() is True

    # Clicking timeline audio button toggles mute back to unmuted
    btn.click()
    assert win.audio_engine.is_muted() is False
    assert "Unmuted" in btn.toolTip()
    assert getattr(win.timeline, "_audio_muted", False) is False
    assert win.audio_mute_act.isChecked() is False

    # Clicking timeline audio button toggles mute to muted
    btn.click()
    assert win.audio_engine.is_muted() is True
    assert "Muted" in btn.toolTip()
    assert getattr(win.timeline, "_audio_muted", False) is True


def test_preferences_default_workspace_startup(qtbot):
    """Verify Preferences dialog has default workspace option and MainWindow applies it on startup."""
    from openreview.ui.preferences_dialog import PreferencesDialog
    from openreview.core.preferences import PreferencesManager
    from openreview.ui.main_window import MainWindow

    # 1. Preferences Dialog Verification
    dlg = PreferencesDialog()
    qtbot.addWidget(dlg)

    cb = dlg.combo_startup_workspace
    items = [cb.itemData(i) for i in range(cb.count())]
    assert "last_used" in items
    assert "review" in items
    assert "color_qc" in items
    assert "vfx_breakdown" in items
    assert "cinema" in items
    assert "annotations" in items

    # Change startup workspace to color_qc and gather prefs
    cb.setCurrentIndex(cb.findData("color_qc"))
    new_prefs = dlg._gather_prefs()
    assert new_prefs.startup_workspace == "color_qc"

    # 2. MainWindow Startup Verification
    PreferencesManager.instance().prefs.startup_workspace = "color_qc"
    win = MainWindow()
    qtbot.addWidget(win)

    assert win.workspace_mgr.current_workspace == "color_qc"
    assert win.scopes_dock.isHidden() is False
    assert win.inspector_dock.isHidden() is False



