"""
Integration tests for UI and MainWindow.
"""
from openreview.ui.main_window import MainWindow
from openreview.models.comparison import ComparisonMode


def test_main_window_instantiation(qtbot):
    window = MainWindow()
    qtbot.addWidget(window)

    assert window.windowTitle().startswith("OpenReview")
    assert window.top_bar is not None
    assert window.viewport is not None
    assert window.timeline is not None
    assert window.markup_palette is not None
    assert window.hud_hist is not None
    assert window.hud_wipe is not None


def test_main_window_playback_and_stepping(qtbot):
    window = MainWindow()
    qtbot.addWidget(window)

    initial_frame = window.clock.current_frame
    window.clock.step_forward(1)
    assert window.clock.current_frame == initial_frame + 1

    window.clock.step_backward(1)
    assert window.clock.current_frame == initial_frame


def test_wipe_toggle(qtbot):
    window = MainWindow()
    qtbot.addWidget(window)

    assert window.comparison_state.mode == ComparisonMode.OFF
    window._on_toggle_wipe()
    assert window.comparison_state.mode == ComparisonMode.WIPE
    window._on_toggle_wipe()
    assert window.comparison_state.mode == ComparisonMode.OFF


def test_clear_in_out_button(qtbot):
    window = MainWindow()
    qtbot.addWidget(window)

    window.clock.set_in_out(1020, 1080)
    assert window.clock.in_frame == 1020
    assert window.clock.out_frame == 1080

    window.timeline.btn_clear_in_out.click()
    assert window.clock.in_frame == window.clock.start_frame
    assert window.clock.out_frame == window.clock.end_frame


def test_timeline_in_out_shortcuts(qtbot):
    from PySide6.QtCore import Qt
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()

    window.clock.set_range(1001, 1200)
    assert window.clock.in_frame == 1001
    assert window.clock.out_frame == 1200

    # Seek to frame 1050 and set in_mark -> in_mark becomes 1050
    window.clock.set_frame(1050)
    window.clock.set_in_mark()
    assert window.clock.in_frame == 1050
    assert window.clock.out_frame == 1200

    # Seek to frame 1150 and press ']' -> out_mark becomes 1150
    window.clock.set_frame(1150)
    qtbot.keyClick(window, Qt.Key.Key_BracketRight)
    assert window.clock.in_frame == 1050
    assert window.clock.out_frame == 1150

    # Press 'Alt+I' -> clears in/out marks
    qtbot.keyClick(window, Qt.Key.Key_I, modifier=Qt.KeyboardModifier.AltModifier)
    assert window.clock.in_frame == 1001
    assert window.clock.out_frame == 1200

    # Seek to 1080 and press ']'
    window.clock.set_frame(1080)
    qtbot.keyClick(window, Qt.Key.Key_BracketRight)
    assert window.clock.in_frame == 1001
    assert window.clock.out_frame == 1080

    # Press 'Alt+O' -> clears in/out marks
    qtbot.keyClick(window, Qt.Key.Key_O, modifier=Qt.KeyboardModifier.AltModifier)
    assert window.clock.in_frame == 1001
    assert window.clock.out_frame == 1200

    # Test setting in mark and out mark
    window.clock.set_frame(1030)
    window.clock.set_in_mark()
    assert window.clock.in_frame == 1030

    window.clock.set_frame(1120)
    qtbot.keyClick(window, Qt.Key.Key_BracketRight)
    assert window.clock.out_frame == 1120


def test_jkl_shuttle_keys(qtbot):
    from PySide6.QtCore import Qt
    window = MainWindow()
    qtbot.addWidget(window)

    # Press 'Shift+L' to shuttle forward
    qtbot.keyClick(window, Qt.Key.Key_L, modifier=Qt.KeyboardModifier.ShiftModifier)
    assert window.clock.is_playing
    assert window.clock.shuttle_speed == 1.0

    qtbot.keyClick(window, Qt.Key.Key_L, modifier=Qt.KeyboardModifier.ShiftModifier)
    assert window.clock.shuttle_speed == 2.0

    # Press 'K' to stop
    qtbot.keyClick(window, Qt.Key.Key_K)
    assert not window.clock.is_playing
    assert window.clock.shuttle_speed == 1.0

    # Press 'J' to shuttle reverse
    qtbot.keyClick(window, Qt.Key.Key_J)
    assert window.clock.is_playing
    assert window.clock.shuttle_speed == -1.0


def test_load_sequence_path(qtbot, tmp_path):
    from PIL import Image

    # Create dummy sequence
    for i in range(101, 106):
        img = Image.new("RGB", (32, 32), color=(i, 0, 0))
        img.save(str(tmp_path / f"test.{i:04d}.png"))

    window = MainWindow()
    qtbot.addWidget(window)

    first_file = str(tmp_path / "test.0101.png")
    window._load_path(first_file)

    assert window.clock.start_frame == 101
    assert window.clock.end_frame == 105
    assert window.clock.current_frame == 101
    assert "test.####.png" in window.windowTitle()


def test_pixel_inspected_handler(qtbot):
    window = MainWindow()
    qtbot.addWidget(window)

    # Simulate pixel inspection event
    window._on_pixel_inspected(x=100, y=200, r=0.5, g=0.8, b=0.2, a=1.0)
    assert "X: 100" in window.coord_label.text()
    assert "Y: 200" in window.coord_label.text()
    assert "127" in window.u8_label.text()
    assert "204" in window.u8_label.text()



def test_load_video_path(qtbot, tmp_path):
    import cv2
    import numpy as np

    vid_path = str(tmp_path / "sample.mov")
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(vid_path, fourcc, 24.0, (64, 64))
    for i in range(8):
        frame = np.full((64, 64, 3), i * 30, dtype=np.uint8)
        writer.write(frame)
    writer.release()

    window = MainWindow()
    qtbot.addWidget(window)
    # Set artificial prior in/out marks before loading video
    window.clock.set_in_out(1050, 1100)

    window._load_path(vid_path)

    # In and Out marks must match exact video duration [1, total_frames] instead of prior marks
    assert window.clock.start_frame == 1
    assert window.clock.end_frame >= 8
    assert window.clock.in_frame == 1
    assert window.clock.out_frame == window.clock.end_frame
    assert window.clock.fps == 24.0
    assert "sample.mov" in window.windowTitle()
    assert window.viewport.input_space == 0  # sRGB
    assert window.viewport.view_transform == 0  # sRGB Display


def test_annotation_tool_selection_and_drawing(qtbot):
    from openreview.models.annotations import ShapeType, Stroke

    window = MainWindow()
    qtbot.addWidget(window)

    # Select PEN tool
    window.markup_palette.btn_pen.click()
    assert window.viewport._active_draw_tool == ShapeType.PEN

    # Add a stroke to frame 1001
    stroke = Stroke(shape_type=ShapeType.PEN, points=[(0.1, 0.1), (0.2, 0.2)], color=(229, 168, 35, 255))
    window.annotation_store.add_stroke(1001, stroke)

    assert window.annotation_store.has_annotations(1001)
    strokes = window.annotation_store.get_strokes(1001)
    assert len(strokes) == 1
    assert strokes[0].shape_type == ShapeType.PEN


def test_top_bar_color_transform_change(qtbot):
    window = MainWindow()
    qtbot.addWidget(window)

    # Change to ACEScg (1) and ACES 1.0 - SDR Video (1)
    window.top_bar.set_color_spaces(1, 1)
    assert window.viewport.input_space == 1
    assert window.viewport.view_transform == 1

    # Change back to sRGB (0) and sRGB Display (0)
    window.top_bar.set_color_spaces(0, 0)
    assert window.viewport.input_space == 0
    assert window.viewport.view_transform == 0


def test_channel_toggle_keys(qtbot):
    from PySide6.QtCore import Qt
    window = MainWindow()
    qtbot.addWidget(window)

    # Default is RGB (0)
    assert window.viewport.channel_mode == 0

    # Press 'R': toggles to Red (1)
    qtbot.keyClick(window, Qt.Key.Key_R)
    assert window.viewport.channel_mode == 1

    # Press 'R' again: toggles back to RGB (0)
    qtbot.keyClick(window, Qt.Key.Key_R)
    assert window.viewport.channel_mode == 0

    # Press 'G': toggles to Green (2)
    qtbot.keyClick(window, Qt.Key.Key_G)
    assert window.viewport.channel_mode == 2

    # Press 'G' again: toggles back to RGB (0)
    qtbot.keyClick(window, Qt.Key.Key_G)
    assert window.viewport.channel_mode == 0

    # Press 'Y': toggles to Luminance (5)
    qtbot.keyClick(window, Qt.Key.Key_Y)
    assert window.viewport.channel_mode == 5

    # Press 'Y' again: toggles back to RGB (0)
    qtbot.keyClick(window, Qt.Key.Key_Y)
    assert window.viewport.channel_mode == 0

    # Solo Blue then press 'C' to reset to RGB
    qtbot.keyClick(window, Qt.Key.Key_B)
    assert window.viewport.channel_mode == 3
    qtbot.keyClick(window, Qt.Key.Key_C)
    assert window.viewport.channel_mode == 0


def test_exposure_interaction(qtbot):
    from PySide6.QtCore import Qt
    window = MainWindow()
    qtbot.addWidget(window)

    assert window.top_bar.gain == 1.0
    assert window.top_bar.gamma == 1.0
    assert window.top_bar.saturation == 1.0

    # 1. Gain spinbox & slider interaction
    window.top_bar.gain_spin.setValue(1.5)
    assert round(window.viewport.gain, 2) == 1.5
    assert round(window.top_bar.gain_spin.value(), 2) == 1.5

    window.top_bar.gain_spin.setValue(2.25)
    assert round(window.viewport.gain, 2) == 2.25

    # Reset Gain [↺]
    assert window.top_bar.btn_gain_reset.text() == "↺"
    window.top_bar.btn_gain_reset.click()
    assert window.viewport.gain == 1.0
    assert window.top_bar.gain_spin.value() == 1.0
    assert window.top_bar.gain_slider.value() == 0

    # 2. Gamma interaction & reset [↺]
    assert window.top_bar.btn_gam_reset.text() == "↺"
    window.top_bar.gam_spin.setValue(2.2)
    assert round(window.viewport.gamma, 2) == 2.2
    assert round(window.top_bar.gam_spin.value(), 2) == 2.2

    window.top_bar.btn_gam_reset.click()
    assert window.viewport.gamma == 1.0
    assert window.top_bar.gam_spin.value() == 1.0
    assert window.top_bar.gam_slider.value() == 500

    # 3. Saturation interaction & reset [↺]
    assert window.top_bar.btn_sat_reset.text() == "↺"
    window.top_bar.sat_spin.setValue(1.8)
    assert round(window.viewport.saturation, 2) == 1.8
    assert round(window.top_bar.sat_spin.value(), 2) == 1.8

    window.top_bar.btn_sat_reset.click()
    assert window.viewport.saturation == 1.0
    assert window.top_bar.sat_spin.value() == 1.0
    assert window.top_bar.sat_slider.value() == 500


def test_channel_toggle_vs_ctrl_g(qtbot, monkeypatch):
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QInputDialog
    monkeypatch.setattr(QInputDialog, "getInt", lambda *a, **k: (1050, True))
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()

    assert window.viewport.channel_mode == 0

    # Ctrl+G pops up goto frame modal and navigates without toggling green channel
    qtbot.keyClick(window, Qt.Key.Key_G, modifier=Qt.KeyboardModifier.ControlModifier)
    assert window.clock.current_frame == 1050
    assert window.viewport.channel_mode == 0

    # Defocus to test viewport shortcuts
    window.viewport.setFocus()

    # Solo 'G' toggles green channel to 2
    qtbot.keyClick(window, Qt.Key.Key_G)
    assert window.viewport.channel_mode == 2

    # Solo 'G' again toggles back to RGB (0)
    qtbot.keyClick(window, Qt.Key.Key_G)
    assert window.viewport.channel_mode == 0


def test_cache_menu_and_budgets(qtbot):
    from openreview.core.cache import CacheMode
    import numpy as np

    window = MainWindow()
    qtbot.addWidget(window)
    window._on_set_cache_mode(CacheMode.REGION, start_prefetch=False)

    # 1. Default cache is In/Out Region (dormant until Shift+C)
    assert window.cache.mode == CacheMode.REGION
    assert "Region" in window.cache_status_label.text()
    assert "Region" in window.timeline.compact_cache_bar.toolTip()

    # Test Shift+C starts and pauses caching
    assert window._cache_started is False
    window._on_toggle_cache()
    assert window._cache_started is True
    window._on_toggle_cache()
    assert window._cache_started is False

    # 2. Switch to Play-Ahead mode
    window.cache_mode_actions[CacheMode.PLAY_AHEAD].trigger()
    assert window.cache.mode == CacheMode.PLAY_AHEAD
    assert "Ahead" in window.cache_status_label.text()

    # Put a dummy frame into cache while active
    dummy = np.zeros((10, 10, 4), dtype=np.float32)
    window.cache.put_frame(1001, dummy)
    assert window.cache.has_frame(1001)

    # 3. Clear RAM Cache action
    window.clear_cache_act.trigger()
    assert not window.cache.has_frame(1001)
    assert "RAM Cache Cleared" in window.statusBar().currentMessage()

    # 4. Cycle cache mode by clicking compact cache bar
    # Currently PLAY_AHEAD -> clicking cycles to REGION -> ALL -> OFF
    window.timeline.compact_cache_bar.cycle_mode_requested.emit()
    assert window.cache.mode == CacheMode.REGION
    assert "Region" in window.cache_status_label.text()

    window.timeline.compact_cache_bar.cycle_mode_requested.emit()
    assert window.cache.mode == CacheMode.ALL
    assert "Full" in window.cache_status_label.text()

    window.timeline.compact_cache_bar.cycle_mode_requested.emit()
    assert window.cache.mode == CacheMode.OFF
    assert window.cache_status_label.text() == "Cache: OFF"

    # 5. Test Budget Presets & Dynamic Available RAM Scaling
    from openreview.core.cache import get_dynamic_gb_presets, compute_dynamic_ram_budget_mb
    assert 4096.0 in window.budget_actions
    window.budget_actions[4096.0].trigger()
    _, max_mb, _ = window.cache.get_memory_stats()
    assert max_mb == 4096.0
    assert window.cache.auto_memory_budget is False
    assert "4096 MB" in window.statusBar().currentMessage()

    window.budget_actions[8192.0].trigger()
    _, max_mb, _ = window.cache.get_memory_stats()
    assert max_mb == 8192.0

    # Switch back to dynamic Auto RAM budget
    assert "auto" in window.budget_actions
    window.budget_actions["auto"].trigger()
    assert window.cache.auto_memory_budget is True
    _, max_mb, _ = window.cache.get_memory_stats()
    assert max_mb == compute_dynamic_ram_budget_mb(window.cache._used_bytes)

    # Verify presets are not restricted to 16 GB on high-RAM systems
    presets_64gb = get_dynamic_gb_presets(64 * 1024.0)
    assert 32 in presets_64gb and 64 in presets_64gb
    presets_128gb = get_dynamic_gb_presets(128 * 1024.0)
    assert 32 in presets_128gb and 64 in presets_128gb and 128 in presets_128gb


def test_image_and_video_background_caching(qtbot, tmp_path):
    from openreview.core.cache import CacheMode
    from PIL import Image
    import cv2
    import numpy as np

    # 1. Test image sequence prefetching with Play-Ahead Cache
    seq_dir = tmp_path / "seq"
    seq_dir.mkdir()
    for i in range(1, 10):
        img = Image.new("RGB", (32, 32), color=(i * 20, 0, 0))
        img.save(str(seq_dir / f"shot.{i:04d}.png"))

    window = MainWindow()
    qtbot.addWidget(window)
    # Enable Play-Ahead cache and start prefetch
    window._on_set_cache_mode(CacheMode.PLAY_AHEAD, start_prefetch=True)

    first_seq = str(seq_dir / "shot.0001.png")
    window._load_path(first_seq)
    window._trigger_prefetch()

    # Wait for Qt event loop to process background prefetch signals
    qtbot.waitUntil(lambda: len(window.cache.get_cached_frames()) >= 3, timeout=3000)
    cached = window.cache.get_cached_frames()
    assert 1 in cached

    # 2. Test Region Cache (in and out region only)
    window.cache.clear()
    window.clock.set_in_out(3, 6)
    window._on_set_cache_mode(CacheMode.REGION, start_prefetch=True)
    qtbot.waitUntil(lambda: window.cache.has_frame(4), timeout=3000)
    region_cached = window.cache.get_cached_frames()
    # Region cached frames must be within In/Out marks [3, 6] (or active playhead)
    for f in region_cached:
        assert (3 <= f <= 6) or f == window.clock.current_frame

    # 3. Test video prefetching with Play-Ahead
    window.cache.clear()
    window._on_set_cache_mode(CacheMode.PLAY_AHEAD, start_prefetch=True)
    vid_path = str(tmp_path / "cache_test.mp4")
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(vid_path, fourcc, 24.0, (64, 64))
    for i in range(12):
        frame = np.full((64, 64, 3), i * 20, dtype=np.uint8)
        writer.write(frame)
    writer.release()

    window._load_path(vid_path)
    window._trigger_prefetch()
    qtbot.waitUntil(lambda: len(window.cache.get_cached_frames()) >= 3, timeout=3000)
    vid_cached = window.cache.get_cached_frames()
    assert 1 in vid_cached
    assert len(vid_cached) >= 3
    qtbot.waitUntil(lambda: window.timeline.compact_cache_bar._cached_frames == window.cache.get_cached_frames(), timeout=3000)
    assert window.timeline.compact_cache_bar._cached_frames == window.cache.get_cached_frames()


def test_presentation_mode_and_escape(qtbot):
    from PySide6.QtCore import Qt

    window = MainWindow()
    qtbot.addWidget(window)

    assert not getattr(window, "_in_presentation_mode", False)
    assert not window.viewport.is_presentation_mode

    # 1. Enter presentation mode
    window._on_toggle_presentation_mode()
    assert getattr(window, "_in_presentation_mode", True)
    assert window.viewport.is_presentation_mode
    assert not window.menuBar().isVisible()
    assert not window.top_bar.isVisible()
    assert not window.timeline.isVisible()

    # 2. Test Escape key exits presentation mode
    qtbot.keyClick(window, Qt.Key.Key_Escape)
    assert not getattr(window, "_in_presentation_mode", False)
    assert not window.viewport.is_presentation_mode
    assert window.menuBar().isVisible()
    assert window.top_bar.isVisible()
    assert window.timeline.isVisible()

    # 3. Enter presentation mode and test viewport signal exit (right click option)
    window._on_toggle_presentation_mode()
    assert getattr(window, "_in_presentation_mode", True)
    window.viewport.exit_presentation_requested.emit()
    assert not getattr(window, "_in_presentation_mode", False)
    assert not window.viewport.is_presentation_mode


def test_huds_hidden_by_default(qtbot):
    window = MainWindow()
    qtbot.addWidget(window)

    assert not window.hud_hist.isVisible()
    assert not window.hud_wipe.isVisible()
    assert not window.hud_inspector.isVisible()


def test_resolution_status_info(qtbot):
    import numpy as np

    window = MainWindow()
    qtbot.addWidget(window)

    # Default resolution
    assert window.resolution_status_label.text() in ("No Media", "1920x1080")

    # Push a 3840x2160 test image
    test_img = np.zeros((2160, 3840, 4), dtype=np.float32)
    window.viewport.set_primary_frame(1, test_img)

    assert window.resolution_status_label.text() == "3840x2160"
    assert window.viewport.image_width == 3840
    assert window.viewport.image_height == 2160


def test_texture_filtering_toggle_shift_n(qtbot):
    from PySide6.QtCore import Qt

    window = MainWindow()
    qtbot.addWidget(window)

    assert window.viewport.texture_filtering == "linear"

    # Toggle to nearest
    window.viewport.toggle_texture_filtering()
    assert window.viewport.texture_filtering == "nearest"

    # Toggle back to linear
    window.viewport.toggle_texture_filtering()
    assert window.viewport.texture_filtering == "linear"

    # Test hotkey N
    qtbot.keyClick(window, Qt.Key.Key_N)
    assert window.viewport.texture_filtering == "nearest"


def test_fullscreen_toggle_f12(qtbot):
    from PySide6.QtCore import Qt

    window = MainWindow()
    qtbot.addWidget(window)

    # Trigger F12 key
    qtbot.keyClick(window, Qt.Key.Key_F12)
    assert window.isFullScreen()

    # Toggle back
    qtbot.keyClick(window, Qt.Key.Key_F12)
    assert not window.isFullScreen()


def test_color_reset_shortcuts(qtbot):
    from PySide6.QtCore import Qt

    window = MainWindow()
    qtbot.addWidget(window)

    # Exposure: set to 2.0 and reset with E
    window.top_bar.set_exposure(2.0)
    assert window.viewport.exposure_ev == 2.0
    qtbot.keyClick(window, Qt.Key.Key_E)
    assert window.viewport.exposure_ev == 0.0

    # Color Adjustments: set gamma, gain, saturation and reset with Shift+Home
    window.top_bar.set_gamma(2.5)
    window.top_bar.set_gain(2.0)
    window.top_bar.set_saturation(0.5)
    assert window.viewport.gamma == 2.5
    assert window.viewport.gain == 2.0
    assert window.viewport.saturation == 0.5
    qtbot.keyClick(window, Qt.Key.Key_Home, Qt.KeyboardModifier.ShiftModifier)
    assert window.viewport.gamma == 1.0
    assert window.viewport.gain == 1.0
    assert window.viewport.saturation == 1.0


def test_rotation_proportions_no_squeeze(qtbot):
    import numpy as np

    window = MainWindow()
    qtbot.addWidget(window)

    # Load 1920x1080 image
    img = np.zeros((1080, 1920, 4), dtype=np.float32)
    window.viewport.set_primary_frame(1, img)

    # When unrotated (0 deg): center maps to (0.5, 0.5)
    cx, cy = window.viewport._screen_to_image_norm(window.viewport.width() / 2.0, window.viewport.height() / 2.0)
    assert round(cx, 2) == 0.5
    assert round(cy, 2) == 0.5

    # Rotate 90 deg clockwise
    window.viewport.set_rotation(90)
    assert window.viewport.rotation == 90

    # Center must still be at (0.5, 0.5)
    rcx, rcy = window.viewport._screen_to_image_norm(window.viewport.width() / 2.0, window.viewport.height() / 2.0)
    assert round(rcx, 2) == 0.5
    assert round(rcy, 2) == 0.5

    # Image screen boundaries: rotation converts 16:9 into 9:16 aspect ratio
    # When 90 deg rotated, displayed width < displayed height in a landscape viewport
    p_tl = window.viewport._image_norm_to_screen(0.0, 0.0)
    p_br = window.viewport._image_norm_to_screen(1.0, 1.0)
    p_tr = window.viewport._image_norm_to_screen(1.0, 0.0)
    p_bl = window.viewport._image_norm_to_screen(0.0, 1.0)
    all_x = [p_tl.x(), p_tr.x(), p_bl.x(), p_br.x()]
    all_y = [p_tl.y(), p_tr.y(), p_bl.y(), p_br.y()]
    disp_w = max(all_x) - min(all_x)
    disp_h = max(all_y) - min(all_y)

    # In 90 degree rotation, disp_w / disp_h should be (1080 / 1920) = 0.5625 (NOT stretched to 1.777)
    ratio = disp_w / disp_h
    assert round(ratio, 2) == round(1080.0 / 1920.0, 2)


def test_source_details_toggle_i(qtbot):
    from PySide6.QtCore import Qt
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()

    assert getattr(window, "_source_details_dialog", None) is None

    # Press 'I' -> opens Source Details dialog
    qtbot.keyClick(window, Qt.Key.Key_I)
    dlg = getattr(window, "_source_details_dialog", None)
    assert dlg is not None
    assert dlg.isVisible()

    # Press 'I' again -> closes Source Details dialog
    qtbot.keyClick(dlg, Qt.Key.Key_I)
    assert not dlg.isVisible() or getattr(window, "_source_details_dialog", None) is None


def test_matte_overlay_cycling_ctrl_m(qtbot):
    from PySide6.QtCore import Qt
    window = MainWindow()
    qtbot.addWidget(window)

    # Initial matte is None
    assert window.viewport.matte_ratio is None

    # Cycle 1: 1.33:1
    qtbot.keyClick(window, Qt.Key.Key_M, modifier=Qt.KeyboardModifier.ControlModifier)
    assert window.viewport.matte_ratio == 1.333

    # Cycle 2: 1.66:1
    qtbot.keyClick(window, Qt.Key.Key_M, modifier=Qt.KeyboardModifier.ControlModifier)
    assert window.viewport.matte_ratio == 1.666

    # Cycle 3: 1.77:1
    qtbot.keyClick(window, Qt.Key.Key_M, modifier=Qt.KeyboardModifier.ControlModifier)
    assert window.viewport.matte_ratio == 1.777

    # Cycle 4: 1.85:1
    qtbot.keyClick(window, Qt.Key.Key_M, modifier=Qt.KeyboardModifier.ControlModifier)
    assert window.viewport.matte_ratio == 1.85

    # Cycle 5: 2.35:1
    qtbot.keyClick(window, Qt.Key.Key_M, modifier=Qt.KeyboardModifier.ControlModifier)
    assert window.viewport.matte_ratio == 2.35

    # Cycle 6: 2.40:1
    qtbot.keyClick(window, Qt.Key.Key_M, modifier=Qt.KeyboardModifier.ControlModifier)
    assert window.viewport.matte_ratio == 2.40

    # Cycle 7: None
    qtbot.keyClick(window, Qt.Key.Key_M, modifier=Qt.KeyboardModifier.ControlModifier)
    assert window.viewport.matte_ratio is None


def test_matte_opacity_cycling_shift_m(qtbot):
    from PySide6.QtCore import Qt
    window = MainWindow()
    qtbot.addWidget(window)

    # Default opacity is 1.0 (100%)
    assert window.viewport.matte_opacity == 1.0

    # Cycle 1: 25% (0.25)
    qtbot.keyClick(window, Qt.Key.Key_M, modifier=Qt.KeyboardModifier.ShiftModifier)
    assert window.viewport.matte_opacity == 0.25

    # Cycle 2: 35% (0.35)
    qtbot.keyClick(window, Qt.Key.Key_M, modifier=Qt.KeyboardModifier.ShiftModifier)
    assert window.viewport.matte_opacity == 0.35

    # Cycle 3: 50% (0.50)
    qtbot.keyClick(window, Qt.Key.Key_M, modifier=Qt.KeyboardModifier.ShiftModifier)
    assert window.viewport.matte_opacity == 0.50

    # Cycle 4: 100% (1.0)
    qtbot.keyClick(window, Qt.Key.Key_M, modifier=Qt.KeyboardModifier.ShiftModifier)
    assert window.viewport.matte_opacity == 1.0


def test_presentation_mode_ctrl_p(qtbot):
    from PySide6.QtCore import Qt
    window = MainWindow()
    qtbot.addWidget(window)

    assert not getattr(window, "_in_presentation_mode", False)

    # Press Ctrl+P -> enters presentation mode
    qtbot.keyClick(window, Qt.Key.Key_P, modifier=Qt.KeyboardModifier.ControlModifier)
    assert getattr(window, "_in_presentation_mode", True)

    # Press Ctrl+P -> exits presentation mode
    qtbot.keyClick(window, Qt.Key.Key_P, modifier=Qt.KeyboardModifier.ControlModifier)
    assert not getattr(window, "_in_presentation_mode", False)


def test_color_inspector_f5(qtbot):
    from PySide6.QtCore import Qt
    window = MainWindow()
    qtbot.addWidget(window)

    assert window.hud_inspector.isHidden()

    # Press F5 -> reveals inspector HUD
    qtbot.keyClick(window, Qt.Key.Key_F5)
    assert not window.hud_inspector.isHidden()

    # Press F5 again -> hides inspector HUD
    qtbot.keyClick(window, Qt.Key.Key_F5)
    assert window.hud_inspector.isHidden()


def test_color_adjustments_slash_shortcut(qtbot, monkeypatch):
    from PySide6.QtCore import Qt
    window = MainWindow()
    qtbot.addWidget(window)

    called = False
    def mock_open_color_adjustments():
        nonlocal called
        called = True

    monkeypatch.setattr(window, "_on_open_color_adjustments", mock_open_color_adjustments)

    qtbot.keyClick(window, Qt.Key.Key_Slash)
    assert called is True


def test_fullscreen_f12_not_f11(qtbot):
    from PySide6.QtCore import Qt
    window = MainWindow()
    qtbot.addWidget(window)

    # F11 should NOT trigger fullscreen
    qtbot.keyClick(window, Qt.Key.Key_F11)
    assert not window.isFullScreen()

    # F12 should trigger fullscreen
    qtbot.keyClick(window, Qt.Key.Key_F12)
    assert window.isFullScreen()

    # F12 toggles back
    qtbot.keyClick(window, Qt.Key.Key_F12)
    assert not window.isFullScreen()


def test_menu_hierarchy_session_under_file_and_huds_under_window(qtbot):
    window = MainWindow()
    qtbot.addWidget(window)

    mb = window.menuBar()
    top_menu_titles = [act.text().replace("&", "") for act in mb.actions()]

    # Verify Session and HUDs are NOT top-level menus
    assert "Session" not in top_menu_titles
    assert "HUDs" not in top_menu_titles

    # Verify File and Window are top-level menus
    assert "File" in top_menu_titles
    assert "Window" in top_menu_titles

    # Verify File menu contains session management actions
    file_menu = None
    window_menu = None
    review_menu = None

    for act in mb.actions():
        clean_text = act.text().replace("&", "")
        if clean_text == "File":
            file_menu = act.menu()
        elif clean_text == "Window":
            window_menu = act.menu()
        elif clean_text == "Review":
            review_menu = act.menu()
        elif clean_text == "View":
            view_menu = act.menu()

    assert file_menu is not None
    file_action_texts = [a.text() for a in file_menu.actions()]
    assert any("New Session" in t for t in file_action_texts)
    assert any("Open Session" in t for t in file_action_texts)
    assert any("Close / Clear Session" in t for t in file_action_texts)
    assert not any("Shotgun" in t for t in file_action_texts)
    assert any("Dailies Sync" in t for t in file_action_texts)

    # Verify Window menu contains System & Display / Layout actions
    assert window_menu is not None
    win_action_texts = [a.text() for a in window_menu.actions()]
    assert any("Toggle Full Screen" in t for t in win_action_texts)
    assert any("Preferences" in t for t in win_action_texts)

    # Verify Review menu has A/B Wipe Comparison
    assert review_menu is not None
    review_action_texts = [a.text() for a in review_menu.actions()]
    assert any("A/B Wipe Comparison" in t for t in review_action_texts)
    assert not any("Image Info" in t for t in review_action_texts)

    # Verify View menu has Source Media Details
    assert view_menu is not None
    view_action_texts = [a.text() for a in view_menu.actions()]
    assert any("Source Media Details" in t for t in view_action_texts)


def test_timeline_zoom_vs_exposure_shortcuts(qtbot):
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QKeyEvent

    window = MainWindow()
    qtbot.addWidget(window)

    # 1. Equal / Plus increases gain by 0.10
    g0 = window.top_bar.gain_spin.value()
    window.keyPressEvent(QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_Equal, Qt.KeyboardModifier.NoModifier, "="))
    assert round(window.top_bar.gain_spin.value(), 2) == round(g0 + 0.10, 2)

    # 2. Minus decreases gain by 0.10
    window.keyPressEvent(QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_Minus, Qt.KeyboardModifier.NoModifier, "-"))
    assert round(window.top_bar.gain_spin.value(), 2) == round(g0, 2)

    # 3. Shift++ / Shift+= increases gamma by 0.05
    gam0 = window.top_bar.gam_spin.value()
    window.keyPressEvent(QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_Equal, Qt.KeyboardModifier.ShiftModifier, "+"))
    assert round(window.top_bar.gam_spin.value(), 2) == round(gam0 + 0.05, 2)

    # 4. Shift+- decreases gamma by 0.05
    window.keyPressEvent(QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_Minus, Qt.KeyboardModifier.ShiftModifier, "-"))
    assert round(window.top_bar.gam_spin.value(), 2) == round(gam0, 2)

    # 5. Ctrl+Shift++ / Ctrl+Shift+= increases saturation by 0.05
    s0 = window.top_bar.sat_spin.value()
    window.keyPressEvent(QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_Equal, Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier, "+"))
    assert round(window.top_bar.sat_spin.value(), 2) == round(s0 + 0.05, 2)

    # 6. Ctrl+Shift+- decreases saturation by 0.05
    window.keyPressEvent(QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_Minus, Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier, "-"))
    assert round(window.top_bar.sat_spin.value(), 2) == round(s0, 2)


def test_comprehensive_all_shortcuts_audit(qtbot, monkeypatch):
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QKeyEvent
    from PySide6.QtWidgets import QMessageBox, QFileDialog, QDialog, QInputDialog

    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.No)
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: None)
    monkeypatch.setattr(QMessageBox, "about", lambda *a, **k: None)
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: ("", ""))
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: ("", ""))
    monkeypatch.setattr(QDialog, "exec", lambda self: 1)
    monkeypatch.setattr(QInputDialog, "getInt", lambda *a, **k: (1001, True))

    window = MainWindow()
    qtbot.addWidget(window)

    shortcut_list = [
        ("Space", Qt.Key.Key_Space, Qt.KeyboardModifier.NoModifier, " "),
        ("Left", Qt.Key.Key_Left, Qt.KeyboardModifier.NoModifier, ""),
        ("Right", Qt.Key.Key_Right, Qt.KeyboardModifier.NoModifier, ""),
        ("Shift+Left", Qt.Key.Key_Left, Qt.KeyboardModifier.ShiftModifier, ""),
        ("Shift+Right", Qt.Key.Key_Right, Qt.KeyboardModifier.ShiftModifier, ""),
        ("Home", Qt.Key.Key_Home, Qt.KeyboardModifier.NoModifier, ""),
        ("End", Qt.Key.Key_End, Qt.KeyboardModifier.NoModifier, ""),
        ("I", Qt.Key.Key_I, Qt.KeyboardModifier.NoModifier, "i"),
        ("Ctrl+I", Qt.Key.Key_I, Qt.KeyboardModifier.ControlModifier, "i"),
        ("Alt+I", Qt.Key.Key_I, Qt.KeyboardModifier.AltModifier, "i"),
        ("BracketLeft", Qt.Key.Key_BracketLeft, Qt.KeyboardModifier.NoModifier, "["),
        ("BracketRight", Qt.Key.Key_BracketRight, Qt.KeyboardModifier.NoModifier, "]"),
        ("Alt+O", Qt.Key.Key_O, Qt.KeyboardModifier.AltModifier, "o"),
        ("Ctrl+O", Qt.Key.Key_O, Qt.KeyboardModifier.ControlModifier, "o"),
        ("Ctrl+Shift+O", Qt.Key.Key_O, Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier, "o"),
        ("Ctrl+N", Qt.Key.Key_N, Qt.KeyboardModifier.ControlModifier, "n"),
        ("N", Qt.Key.Key_N, Qt.KeyboardModifier.NoModifier, "n"),
        ("PageUp", Qt.Key.Key_PageUp, Qt.KeyboardModifier.NoModifier, ""),
        ("PageDown", Qt.Key.Key_PageDown, Qt.KeyboardModifier.NoModifier, ""),
        ("Ctrl+S", Qt.Key.Key_S, Qt.KeyboardModifier.ControlModifier, "s"),
        ("Ctrl+Shift+S", Qt.Key.Key_S, Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier, "s"),
        ("Ctrl+Shift+E", Qt.Key.Key_E, Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier, "e"),
        ("Ctrl+E", Qt.Key.Key_E, Qt.KeyboardModifier.ControlModifier, "e"),
        ("E", Qt.Key.Key_E, Qt.KeyboardModifier.NoModifier, "e"),
        ("Ctrl+G", Qt.Key.Key_G, Qt.KeyboardModifier.ControlModifier, "g"),
        ("G", Qt.Key.Key_G, Qt.KeyboardModifier.NoModifier, "g"),
        ("Ctrl+Shift+G", Qt.Key.Key_G, Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier, "g"),
        ("R", Qt.Key.Key_R, Qt.KeyboardModifier.NoModifier, "r"),
        ("B", Qt.Key.Key_B, Qt.KeyboardModifier.NoModifier, "b"),
        ("A", Qt.Key.Key_A, Qt.KeyboardModifier.NoModifier, "a"),
        ("Alt+A", Qt.Key.Key_A, Qt.KeyboardModifier.AltModifier, "a"),
        ("Ctrl+Shift+A", Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier, "a"),
        ("C", Qt.Key.Key_C, Qt.KeyboardModifier.NoModifier, "c"),
        ("Alt+C", Qt.Key.Key_C, Qt.KeyboardModifier.AltModifier, "c"),
        ("F5", Qt.Key.Key_F5, Qt.KeyboardModifier.NoModifier, ""),
        ("L", Qt.Key.Key_L, Qt.KeyboardModifier.NoModifier, "l"),
        ("Y", Qt.Key.Key_Y, Qt.KeyboardModifier.NoModifier, "y"),
        ("J", Qt.Key.Key_J, Qt.KeyboardModifier.NoModifier, "j"),
        ("K", Qt.Key.Key_K, Qt.KeyboardModifier.NoModifier, "k"),
        ("Shift+L", Qt.Key.Key_L, Qt.KeyboardModifier.ShiftModifier, "l"),
        ("Equal", Qt.Key.Key_Equal, Qt.KeyboardModifier.NoModifier, "="),
        ("Minus", Qt.Key.Key_Minus, Qt.KeyboardModifier.NoModifier, "-"),
        ("Ctrl+=", Qt.Key.Key_Equal, Qt.KeyboardModifier.ControlModifier, "="),
        ("Ctrl+-", Qt.Key.Key_Minus, Qt.KeyboardModifier.ControlModifier, "-"),
        ("Ctrl+Shift+C", Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier, "c"),
        ("W", Qt.Key.Key_W, Qt.KeyboardModifier.NoModifier, "w"),
        ("D", Qt.Key.Key_D, Qt.KeyboardModifier.NoModifier, "d"),
        ("Ctrl+Shift+D", Qt.Key.Key_D, Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier, "d"),
        ("X", Qt.Key.Key_X, Qt.KeyboardModifier.NoModifier, "x"),
        ("Shift+X", Qt.Key.Key_X, Qt.KeyboardModifier.ShiftModifier, "x"),
        ("Shift+Y", Qt.Key.Key_Y, Qt.KeyboardModifier.ShiftModifier, "y"),
        ("Period", Qt.Key.Key_Period, Qt.KeyboardModifier.NoModifier, "."),
        ("Comma", Qt.Key.Key_Comma, Qt.KeyboardModifier.NoModifier, ","),
        ("Ctrl+,", Qt.Key.Key_Comma, Qt.KeyboardModifier.ControlModifier, ","),
        ("M", Qt.Key.Key_M, Qt.KeyboardModifier.NoModifier, "m"),
        ("Ctrl+M", Qt.Key.Key_M, Qt.KeyboardModifier.ControlModifier, "m"),
        ("Shift+M", Qt.Key.Key_M, Qt.KeyboardModifier.ShiftModifier, "m"),
        ("Alt+Right", Qt.Key.Key_Right, Qt.KeyboardModifier.AltModifier, ""),
        ("Alt+Left", Qt.Key.Key_Left, Qt.KeyboardModifier.AltModifier, ""),
        ("Ctrl+1", Qt.Key.Key_1, Qt.KeyboardModifier.ControlModifier, "1"),
        ("Ctrl+2", Qt.Key.Key_2, Qt.KeyboardModifier.ControlModifier, "2"),
        ("Ctrl+4", Qt.Key.Key_4, Qt.KeyboardModifier.ControlModifier, "4"),
        ("Ctrl+8", Qt.Key.Key_8, Qt.KeyboardModifier.ControlModifier, "8"),
        ("F", Qt.Key.Key_F, Qt.KeyboardModifier.NoModifier, "f"),
        ("Alt+F", Qt.Key.Key_F, Qt.KeyboardModifier.AltModifier, "f"),
        ("1", Qt.Key.Key_1, Qt.KeyboardModifier.NoModifier, "1"),
        ("QuoteLeft", Qt.Key.Key_QuoteLeft, Qt.KeyboardModifier.NoModifier, "`"),
        ("Z", Qt.Key.Key_Z, Qt.KeyboardModifier.NoModifier, "z"),
        ("Shift+Z", Qt.Key.Key_Z, Qt.KeyboardModifier.ShiftModifier, "Z"),
        ("Ctrl+P", Qt.Key.Key_P, Qt.KeyboardModifier.ControlModifier, "p"),
        ("F12", Qt.Key.Key_F12, Qt.KeyboardModifier.NoModifier, ""),
        ("Esc", Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier, ""),
        ("Slash", Qt.Key.Key_Slash, Qt.KeyboardModifier.NoModifier, "/"),
        ("H", Qt.Key.Key_H, Qt.KeyboardModifier.NoModifier, "h"),
        ("Shift+Home", Qt.Key.Key_Home, Qt.KeyboardModifier.ShiftModifier, ""),
        ("Ctrl+Q", Qt.Key.Key_Q, Qt.KeyboardModifier.ControlModifier, "q"),
    ]

    for name, k, mod, t in shortcut_list:
        window.viewport.setFocus()
        ev = QKeyEvent(QKeyEvent.Type.KeyPress, k, mod, t)
        window.keyPressEvent(ev)
        assert ev.isAccepted(), f"Shortcut {name} was not accepted by MainWindow.keyPressEvent"










