"""
Tests for On-Screen Color Adjustment Sliders Overlay (Gain, Gamma, Hue, Saturation).
"""
import pytest
from PySide6.QtCore import Qt, QPointF
from PySide6.QtGui import QKeyEvent, QMouseEvent
from PySide6.QtWidgets import QWidget

from openreview.color.lut_manager import ColorGradeSettings
from openreview.widgets.color_sliders_overlay import ColorSlidersOverlay, ColorRulerSlider
from openreview.widgets.timeline import MagnifiedTimelinePopup
from openreview.core.clock import PlaybackClock


def test_color_ruler_slider_init_and_properties(qtbot):
    slider = ColorRulerSlider(
        "GAIN",
        0.015,
        64.0,
        1.0,
        step=0.05,
        decimals=3,
        suffix="x",
        is_log=True,
    )
    qtbot.addWidget(slider)

    assert slider.name == "GAIN"
    assert slider.min_val == 0.015
    assert slider.max_val == 64.0
    assert slider.default_val == 1.0
    assert slider.value == 1.0
    assert slider.suffix == "x"
    assert slider.is_log is True
    # Native tooltip is removed to prevent black box popups on hover
    assert slider.toolTip() == ""

    # Test log ratio mapping: 1.0 is 0 EV, should be at center ratio ~0.5
    ratio_1 = slider._val_to_ratio(1.0)
    assert abs(ratio_1 - 0.5) < 0.05


def test_color_ruler_slider_value_change_and_reset(qtbot):
    slider = ColorRulerSlider("GAMMA", 0.0, 4.0, 1.0, step=0.025, decimals=3)
    qtbot.addWidget(slider)

    with qtbot.waitSignal(slider.valueChanged, timeout=1000) as blocker:
        slider.value = 2.2
    assert blocker.args == [2.2]
    assert slider.value == 2.2

    # Double-click reset
    slider.reset_default()
    assert slider.value == 1.0


def test_color_sliders_overlay_structure_and_sliders(qtbot):
    parent = QWidget()
    parent.resize(1000, 700)
    qtbot.addWidget(parent)

    overlay = ColorSlidersOverlay(parent=parent)
    assert overlay.slider_gain.name == "GAIN"
    assert overlay.slider_gain.min_val == 0.015
    assert overlay.slider_gain.max_val == 64.0

    assert overlay.slider_gamma.name == "GAMMA"
    assert overlay.slider_gamma.min_val == 0.0
    assert overlay.slider_gamma.max_val == 4.0

    assert overlay.slider_hue.name == "HUE"
    assert overlay.slider_hue.min_val == -180.0
    assert overlay.slider_hue.max_val == 180.0

    assert overlay.slider_sat.name == "SATURATION"
    assert overlay.slider_sat.min_val == 0.0
    assert overlay.slider_sat.max_val == 4.0

    assert not overlay.isVisible()


def test_color_sliders_overlay_open_and_live_preview(qtbot):
    parent = QWidget()
    parent.resize(1000, 700)
    parent.show()
    qtbot.addWidget(parent)

    overlay = ColorSlidersOverlay(parent=parent)

    initial_settings = ColorGradeSettings(
        gain=(1.5, 1.5, 1.5),
        gamma=1.2,
        hue=15.0,
        saturation=1.4,
    )

    overlay.open_overlay(initial_settings)
    assert not overlay.isHidden()
    assert abs(overlay.slider_gain.value - 1.5) < 0.05
    assert abs(overlay.slider_gamma.value - 1.2) < 0.01
    assert abs(overlay.slider_hue.value - 15.0) < 0.01
    assert abs(overlay.slider_sat.value - 1.4) < 0.01

    # Adjust gain and verify live preview signal
    with qtbot.waitSignal(overlay.colorGradeChanged, timeout=1000) as blocker:
        overlay.slider_gain.value = 2.0
    emitted = blocker.args[0]
    assert emitted.gain == (2.0, 2.0, 2.0)


def test_color_sliders_overlay_dynamic_70_percent_width(qtbot):
    parent = QWidget()
    parent.resize(1600, 900)
    parent.show()
    qtbot.addWidget(parent)

    overlay = ColorSlidersOverlay(parent=parent)
    overlay.open_overlay(ColorGradeSettings())

    # At 1600px width, 70% is 1120px
    expected_w = int(1600 * 0.70)
    assert abs(overlay.width() - expected_w) <= 2

    # Resize parent to 1200px
    parent.resize(1200, 800)
    overlay.auto_center_in_parent()
    expected_w_2 = int(1200 * 0.70)
    assert abs(overlay.width() - expected_w_2) <= 2


def test_color_sliders_overlay_commit_and_cancel(qtbot):
    parent = QWidget()
    parent.resize(1000, 700)
    qtbot.addWidget(parent)

    overlay = ColorSlidersOverlay(parent=parent)
    initial_settings = ColorGradeSettings(gain=(1.0, 1.0, 1.0), gamma=1.0, hue=0.0, saturation=1.0)

    # 1. Test Cancel reverts settings
    overlay.open_overlay(initial_settings)
    overlay.slider_gain.value = 3.5

    with qtbot.waitSignal(overlay.colorGradeChanged, timeout=1000) as blocker:
        overlay.cancel()
    assert not overlay.isVisible()
    reverted = blocker.args[0]
    assert reverted.gain == (1.0, 1.0, 1.0)

    # 2. Test Commit preserves settings
    overlay.open_overlay(initial_settings)
    overlay.slider_gain.value = 2.5
    with qtbot.waitSignal(overlay.committed, timeout=1000) as blocker:
        overlay.commit()
    assert not overlay.isVisible()
    committed = blocker.args[0]
    assert committed.gain == (2.5, 2.5, 2.5)


def test_color_sliders_overlay_ruler_spacing(qtbot):
    parent = QWidget()
    parent.resize(1000, 700)
    qtbot.addWidget(parent)

    clock = PlaybackClock()
    ruler = MagnifiedTimelinePopup(clock, parent=parent)
    ruler.resize(400, 32)
    ruler.hide()

    overlay = ColorSlidersOverlay(parent=parent)
    overlay.open_overlay(ColorGradeSettings())

    y_without_ruler = overlay.y()

    ruler.show()
    overlay.auto_center_in_parent()
    y_with_ruler = overlay.y()

    assert y_with_ruler < y_without_ruler
    assert y_without_ruler - y_with_ruler >= ruler.height()


def test_two_way_live_sync_with_top_control_bar(qtbot):
    from openreview.ui.main_window import MainWindow

    win = MainWindow()
    qtbot.addWidget(win)
    win.show()

    # Open overlay
    win._on_toggle_color_sliders()
    assert not win.color_sliders_overlay.isHidden()

    # --- 1. Adjusting overlay Gain updates top_bar and viewport ---
    win.color_sliders_overlay.slider_gain.value = 2.5
    assert abs(win.top_bar._gain - 2.5) < 0.05
    assert abs(win.viewport.gain - 2.5) < 0.05
    assert abs(win.viewport.color_grading.gain[0] - 2.5) < 0.05

    # --- 2. Adjusting top_bar Gain updates overlay ---
    win.top_bar.set_gain(1.75)
    assert abs(win.color_sliders_overlay.slider_gain.value - 1.75) < 0.05

    # --- 3. Adjusting overlay Gamma updates top_bar and viewport ---
    win.color_sliders_overlay.slider_gamma.value = 1.8
    assert abs(win.top_bar.ocio.gamma - 1.8) < 0.05
    assert abs(win.viewport.gamma - 1.8) < 0.05

    # --- 4. Adjusting top_bar Gamma updates overlay ---
    win.top_bar.set_gamma(0.85)
    assert abs(win.color_sliders_overlay.slider_gamma.value - 0.85) < 0.05

    # --- 5. Adjusting overlay Saturation updates top_bar and viewport ---
    win.color_sliders_overlay.slider_sat.value = 2.2
    assert abs(win.top_bar._saturation - 2.2) < 0.05
    assert abs(win.viewport.saturation - 2.2) < 0.05

    # --- 6. Adjusting top_bar Saturation updates overlay ---
    win.top_bar.set_saturation(1.35)
    assert abs(win.color_sliders_overlay.slider_sat.value - 1.35) < 0.05

    # Commit and close
    key_event = QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_Return, Qt.KeyboardModifier.NoModifier)
    win.color_sliders_overlay.eventFilter(win.color_sliders_overlay, key_event)
    assert win.color_sliders_overlay.isHidden()
