"""
Unit and Integration Tests for the Top Floating Annotation Capsule Pill and 'X' Shortcut.
"""
import pytest
from PySide6.QtCore import Qt, QPoint, QEvent
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QWidget

from openreview.models.annotations import ShapeType, AnnotationStore, Stroke
from openreview.widgets.markup_tools import MarkupToolPalette
from openreview.widgets.viewport import ViewportCanvas
from openreview.ui.main_window import MainWindow
from openreview.core.shortcuts import ShortcutManager


def test_floating_annotation_bar_components(qtbot):
    parent = QWidget()
    parent.resize(1000, 600)
    bar = MarkupToolPalette(parent)
    qtbot.addWidget(parent)

    assert bar.height() == 42
    assert bar.btn_select.isChecked()

    # Tool selection
    received_tools = []
    bar.tool_changed.connect(received_tools.append)

    bar.btn_pen.click()
    assert received_tools[-1] == ShapeType.PEN
    assert bar._current_tool == ShapeType.PEN

    bar.btn_box.click()
    assert received_tools[-1] == ShapeType.RECTANGLE

    bar.btn_text.click()
    assert received_tools[-1] == ShapeType.TEXT


def test_brush_size_and_opacity_sliders(qtbot):
    bar = MarkupToolPalette()
    qtbot.addWidget(bar)

    assert bar._current_width in (5.0, 6.0)
    assert bar.slider_size.value() in (5, 6)
    assert bar.lbl_size.text() in ("5px", "6px")

    sizes = []
    bar.stroke_width_changed.connect(sizes.append)
    bar.slider_size.setValue(12)
    assert sizes[-1] == 12.0
    assert bar.lbl_size.text() == "12px"

    opacities = []
    bar.opacity_changed.connect(opacities.append)
    bar.slider_opacity.setValue(75)
    assert opacities[-1] == 0.75
    assert bar.lbl_opacity.text() == "75%"


def test_color_swatches_and_actions(qtbot):
    bar = MarkupToolPalette()
    qtbot.addWidget(bar)

    assert len(bar.swatch_buttons) == 5
    assert bar._current_color == (255, 209, 59, 255)

    colors = []
    bar.color_changed.connect(colors.append)

    # Click red swatch (second button in group)
    bar.swatch_buttons[1].click()
    assert colors[-1] == (255, 77, 79, 255)

    # Click blue swatch (fourth button in group)
    bar.swatch_buttons[3].click()
    assert colors[-1] == (24, 144, 255, 255)

    # Undo and Clear signals
    undo_called = []
    clear_called = []
    close_called = []
    bar.undo_requested.connect(lambda: undo_called.append(True))
    bar.clear_requested.connect(lambda: clear_called.append(True))
    bar.close_requested.connect(lambda: close_called.append(True))

    bar.btn_undo.click()
    assert undo_called == [True]

    bar.btn_clear.click()
    assert clear_called == [True]

    bar.btn_close.click()
    assert close_called == [True]


def test_auto_centering_and_drag(qtbot):
    parent = QWidget()
    parent.resize(1200, 800)
    bar = MarkupToolPalette(parent)
    bar.resize(600, 42)
    qtbot.addWidget(parent)

    bar.auto_center_in_parent(force=True)
    expected_x = (1200 - bar.width()) // 2
    assert bar.x() == expected_x
    assert bar.y() == 16


def test_main_window_toggle_x_shortcut(qtbot):
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()

    # Starts hidden (off by default)
    assert window.markup_palette is not None
    assert not window.markup_palette.isVisible()

    # Toggle with X shortcut -> becomes visible
    sm = window.shortcut_mgr
    event_x = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_X, Qt.KeyboardModifier.NoModifier, "x")
    handled = sm.eventFilter(window, event_x)
    assert handled is True
    assert window.markup_palette.isVisible()

    # Toggle back off with X -> hidden again
    handled2 = sm.eventFilter(window, event_x)
    assert handled2 is True
    assert not window.markup_palette.isVisible()

    window.close()


def test_viewport_live_stroke_width_and_opacity(qtbot):
    store = AnnotationStore()
    viewport = ViewportCanvas(store)
    qtbot.addWidget(viewport)

    viewport.set_draw_width(8.0)
    assert viewport._active_draw_width == 8.0

    viewport.set_draw_opacity(0.5)
    assert viewport._active_draw_opacity == 0.5
    # Alpha channel of draw color scaled by 0.5
    assert viewport._active_draw_color[3] == int(round(255 * 0.5))


def test_clear_frame_shortcut_delete(qtbot):
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()

    # Add a mock stroke to frame 1001
    stroke = Stroke(shape_type=ShapeType.PEN, points=[(0.1, 0.1), (0.2, 0.2)])
    window.annotation_store.add_stroke(1001, stroke)
    assert len(window.annotation_store.get_strokes(1001)) == 1

    # Press Delete key to clear annotations
    sm = window.shortcut_mgr
    event_del = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Delete, Qt.KeyboardModifier.NoModifier)
    handled = sm.eventFilter(window, event_del)
    assert handled is True
    assert len(window.annotation_store.get_strokes(1001)) == 0
