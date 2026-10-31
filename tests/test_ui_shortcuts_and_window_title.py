import pytest
from pathlib import Path
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QSizePolicy, QComboBox

from openreview.core.shortcuts import ShortcutManager
from openreview.models.session_graph import ViewType, SourceItem, SequenceView, StackView, LayoutView
from openreview.widgets.top_bar import TopControlBar


def test_export_annotations_shortcut_definition():
    sm = ShortcutManager()
    action = sm.get_action("annot.export")
    assert action is not None
    assert action.default_shortcut == "Ctrl+E"
    assert action.category == "Annotations"
    assert "Export Annotations" in action.name

    # Check aliases for Ctrl+E
    assert sm._aliases.get("Ctrl+E") == "annot.export"
    assert sm._aliases.get("Ctrl+e") == "annot.export"

    # Verify exposure reset still works via E, e and Ctrl+Alt+E
    assert sm._aliases.get("E") == "color.exp_reset"
    assert sm._aliases.get("e") == "color.exp_reset"
    assert sm._aliases.get("Ctrl+Alt+E") == "color.exp_reset"


def test_top_control_bar_slider_and_dropdown_sizing(qtbot):
    from unittest.mock import MagicMock
    ocio_mock = MagicMock()
    ocio_mock.gain = 1.0
    ocio_mock.gamma = 1.0
    ocio_mock.saturation = 1.0

    top_bar = TopControlBar(ocio_mock)
    qtbot.addWidget(top_bar)

    # 1. Check sliders minimum width
    assert top_bar.gain_slider.minimumWidth() >= 75
    assert top_bar.gam_slider.minimumWidth() >= 75
    assert top_bar.sat_slider.minimumWidth() >= 75

    # 2. Check dropdown size policies and compressability
    assert top_bar.input_combo.sizePolicy().horizontalPolicy() == QSizePolicy.Policy.Preferred
    assert top_bar.input_combo.minimumWidth() == 125
    assert top_bar.input_combo.minimumContentsLength() == 12
    assert top_bar.input_combo.sizeAdjustPolicy() == QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon

    assert top_bar.view_combo.sizePolicy().horizontalPolicy() == QSizePolicy.Policy.Preferred
    assert top_bar.view_combo.minimumWidth() == 85
    assert top_bar.view_combo.minimumContentsLength() == 7
    assert top_bar.view_combo.sizeAdjustPolicy() == QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon


def test_window_title_saved_and_unsaved_session(qtbot):
    from openreview.ui.main_window import MainWindow

    win = MainWindow()
    qtbot.addWidget(win)

    # 1. No media, no session
    win._current_session_file = None
    win._update_window_title()
    assert win.windowTitle() == "OpenReview - Professional VFX Review & Playback [ACEScg / OCIO v2]"

    # 2. Saved session, no media
    win._current_session_file = Path("test_session.ovr")
    win._update_window_title()
    assert win.windowTitle() == "OpenReview - test_session.ovr"

    # 3. Add active source item - Unsaved session
    win._current_session_file = None
    src = SourceItem(name="Plate_010", path="/path/to/plate.mov", start_frame=1001, end_frame=1100, fps=24.0)
    win.session_graph.add_source(src)
    win.session_graph.set_active_view(ViewType.SOURCE, src.id)
    win._update_window_title()
    assert win.windowTitle() == "OpenReview - Plate_010 [1001-1100 @ 24.00 FPS]"

    # 4. Add active source item - Saved session
    win._current_session_file = Path("review_shot10.ovr")
    win._update_window_title()
    assert win.windowTitle() == "OpenReview - review_shot10.ovr - Plate_010 [1001-1100 @ 24.00 FPS]"

    # 5. Rename active source item updates window title
    src.name = "Plate_010_v02"
    win._on_source_renamed_update_title(src.id)
    assert win.windowTitle() == "OpenReview - review_shot10.ovr - Plate_010_v02 [1001-1100 @ 24.00 FPS]"

    # 6. Sequence view - Saved session
    seq = SequenceView(name="SeqEdit", start_frame=1)
    win.session_graph.sequences[seq.id] = seq
    win.session_graph.set_active_view(ViewType.SEQUENCE, seq.id)
    win._update_window_title()
    assert win.windowTitle() == "OpenReview - review_shot10.ovr - Sequence: SeqEdit [1-1, 0 clips]"

    # 7. Stack view - Saved session
    stk = StackView(name="CompStack", compare_mode="wipe")
    win.session_graph.stacks[stk.id] = stk
    win.session_graph.set_active_view(ViewType.STACK, stk.id)
    win._update_window_title()
    assert win.windowTitle() == "OpenReview - review_shot10.ovr - Stack: CompStack [WIPE mode]"

    # 8. Layout view - Unsaved session
    win._current_session_file = None
    lay = LayoutView(name="GridComparison", layout_type="grid_2x2", scale_pct=100)
    win.session_graph.layouts[lay.id] = lay
    win.session_graph.set_active_view(ViewType.LAYOUT, lay.id)
    win._update_window_title()
    assert win.windowTitle() == "OpenReview - Layout: GridComparison [grid_2x2, 100% scale]"


def test_annotation_menu_export_action_and_shortcut(qtbot):
    from openreview.ui.main_window import MainWindow

    win = MainWindow()
    qtbot.addWidget(win)

    # Check Annotation menu contains 'Export Annotations... (Ctrl+E)'
    annot_menu = None
    for action in win.menuBar().actions():
        if action.text() == "&Annotation":
            annot_menu = action.menu()
            break
    assert annot_menu is not None

    export_act = None
    for act in annot_menu.actions():
        if "Export Annotations" in act.text():
            export_act = act
            break

    assert export_act is not None
    assert "(Ctrl+E)" in export_act.text()


def test_fit_width_shortcut_and_aliases():
    sm = ShortcutManager()
    action = sm.get_action("view.fit_width")
    assert action is not None
    assert action.default_shortcut == "Ctrl+F"

    # Verify Ctrl+F and Alt+F aliases
    assert sm._aliases.get("Ctrl+F") == "view.fit_width"
    assert sm._aliases.get("Ctrl+f") == "view.fit_width"
    assert sm._aliases.get("Alt+F") == "view.fit_width"
    assert sm._aliases.get("Alt+f") == "view.fit_width"


def test_help_menu_cleanup_and_window_menu(qtbot):
    from openreview.ui.main_window import MainWindow

    win = MainWindow()
    qtbot.addWidget(win)

    mb = win.menuBar()
    help_menu = None
    win_menu = None
    for act in mb.actions():
        if act.text() == "&Help":
            help_menu = act.menu()
        elif act.text() == "&Window":
            win_menu = act.menu()

    assert help_menu is not None
    assert win_menu is not None

    help_action_texts = [a.text() for a in help_menu.actions()]
    assert not any("Package & Plugin Manager" in t for t in help_action_texts), "Package & Plugin Manager should be removed from Help menu"

    win_action_texts = [a.text() for a in win_menu.actions()]
    assert any("Package & Plugin Manager" in t for t in win_action_texts), "Package & Plugin Manager should remain in Window menu"


def test_viewport_context_menu_presentation_and_fit_width(qtbot):
    from unittest.mock import MagicMock
    from openreview.widgets.viewport import ViewportCanvas
    from openreview.models.annotations import AnnotationStore

    annot_store = MagicMock(spec=AnnotationStore)
    vp = ViewportCanvas(annot_store)
    qtbot.addWidget(vp)

    menu = vp._create_context_menu()
    menu_texts = [a.text() for a in menu.actions()]
    assert any("Presentation Mode (ctrl+p)" in t for t in menu_texts)
    assert any("Fit Width (ctrl+f)" in t for t in menu_texts)


def test_cache_toggle_preference_and_behavior(qtbot):
    from openreview.ui.main_window import MainWindow
    from openreview.core.cache import CacheMode

    win = MainWindow()
    qtbot.addWidget(win)

    # 1. Default toggle mode should be "region"
    assert getattr(win.pref_mgr.prefs, "cache_toggle_mode", "region") == "region"

    # Start with cache OFF
    win._on_set_cache_mode(CacheMode.OFF)
    assert win.cache.mode == CacheMode.OFF

    # Toggle ON -> should toggle to CacheMode.REGION
    win._on_toggle_cache()
    assert win.cache.mode == CacheMode.REGION
    assert getattr(win, "_cache_started", False) is True

    # Toggle OFF -> should toggle back to CacheMode.OFF
    win._on_toggle_cache()
    assert win.cache.mode == CacheMode.OFF

    # Change preference to "play_ahead"
    win.pref_mgr.prefs.cache_toggle_mode = "play_ahead"
    win._on_toggle_cache()
    assert win.cache.mode == CacheMode.PLAY_AHEAD

    # Toggle OFF again
    win._on_toggle_cache()
    assert win.cache.mode == CacheMode.OFF


def test_accidental_viewer_zoom_scroll_protection(qtbot):
    from openreview.widgets.viewport import ViewportCanvas
    from openreview.models.annotations import AnnotationStore
    from PySide6.QtWidgets import QPushButton
    from PySide6.QtGui import QWheelEvent
    from PySide6.QtCore import QPointF, QPoint

    vp = ViewportCanvas(AnnotationStore())
    qtbot.addWidget(vp)
    vp.resize(800, 600)
    vp.show()

    initial_zoom = vp._zoom

    # 1. Wheel event over bare canvas area -> zoom changes
    bare_pos = QPointF(100.0, 100.0)
    event_bare = QWheelEvent(
        bare_pos,
        vp.mapToGlobal(bare_pos.toPoint()),
        QPoint(0, 0),
        QPoint(0, 120),
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.NoScrollPhase,
        False,
    )
    vp.wheelEvent(event_bare)
    assert vp._zoom > initial_zoom

    # Reset zoom
    vp._zoom = 1.0

    # 2. Add an opened widget on top of the viewport at (200, 200, 150, 50)
    btn = QPushButton("HUD Button", vp)
    btn.setGeometry(200, 200, 150, 50)
    btn.show()

    # Wheel event directly over the button widget
    widget_pos = QPointF(250.0, 220.0)
    event_over_widget = QWheelEvent(
        widget_pos,
        vp.mapToGlobal(widget_pos.toPoint()),
        QPoint(0, 0),
        QPoint(0, 120),
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.NoScrollPhase,
        False,
    )
    vp.wheelEvent(event_over_widget)
    # Zoom should NOT change because accidental zoom protection intercepted it
    assert vp._zoom == 1.0


def test_frame_rate_dialog_and_presets(qtbot):
    from openreview.ui.frame_rate_dialog import FrameRateDialog
    from PySide6.QtWidgets import QPushButton, QDialog
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QKeyEvent

    # 1. Test clicking a preset pill confirms and closes (accepts) dialog
    dlg = FrameRateDialog(current_fps=24.0)
    qtbot.addWidget(dlg)

    assert dlg.spin_box.value() == 24.0

    # Click 29.97 preset pill -> should set FPS and accept
    pills = [b for b in dlg.findChildren(QPushButton, "presetPill") if "29.97" in b.text()]
    assert len(pills) == 1
    pills[0].click()
    assert abs(dlg.spin_box.value() - 29.97) < 0.001
    assert dlg.get_fps() == 29.97
    assert dlg.result() == QDialog.DialogCode.Accepted

    # 2. Test typing in the input field and pressing Enter confirms and closes (accepts)
    dlg2 = FrameRateDialog(current_fps=24.0)
    qtbot.addWidget(dlg2)
    dlg2.show()

    dlg2.spin_box.lineEdit().clear()
    dlg2.spin_box.lineEdit().setText("47.5")
    key_event = QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_Return, Qt.KeyboardModifier.NoModifier)
    dlg2.spin_box.lineEdit().keyPressEvent(key_event)

    assert abs(dlg2.get_fps() - 47.5) < 0.001
    assert dlg2.result() == QDialog.DialogCode.Accepted


def test_playback_set_fps_shortcut_and_menu(qtbot):
    from openreview.ui.main_window import MainWindow

    win = MainWindow()
    qtbot.addWidget(win)

    # 1. Shortcut registry check
    sm = win.shortcut_mgr
    action = sm.get_action("playback.set_fps")
    assert action is not None
    assert action.default_shortcut == "Shift+F"
    assert sm._aliases.get("Shift+F") == "playback.set_fps"
    assert sm._aliases.get("Shift+f") == "playback.set_fps"

    # 2. Menu action check
    review_menu = None
    for a in win.menuBar().actions():
        if a.text() == "&Review":
            review_menu = a.menu()
            break
    assert review_menu is not None

    fps_act = None
    for sub in review_menu.actions():
        if sub.menu() and sub.text() == "Playback Mode":
            for a in sub.menu().actions():
                if "Set Frame Rate" in a.text():
                    fps_act = a
                    break
    assert fps_act is not None
    assert "Shift+F" in fps_act.text()

    # 3. Timeline FPS badge button check
    assert hasattr(win.timeline, "btn_fps")
    assert "fps" in win.timeline.btn_fps.text().lower()

    # When clock FPS changes, timeline badge updates
    win.clock.set_fps(29.97)
    assert "29.97" in win.timeline.btn_fps.text()
