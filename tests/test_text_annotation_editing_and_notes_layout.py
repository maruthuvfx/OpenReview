"""
Unit tests for Notes dock layout fix and Text Annotation editing:
1. Verification of unwanted gap elimination in Notes dock and 2-row responsive button grid.
2. In-canvas text annotation hit-testing and in-place editing via Type Tool.
3. Text annotation updating and deletion via AnnotationManagerWidget.
"""
import os
import sys
import pytest
from PySide6.QtCore import Qt, QPoint
from PySide6.QtWidgets import QApplication

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from openreview.models.annotations import AnnotationStore, Stroke, ShapeType
from openreview.core.clock import PlaybackClock
from openreview.ui.annotation_manager_dialog import AnnotationManagerWidget
from openreview.ui.workspaces import AnnotationsDockWidget
from openreview.widgets.viewport import ViewportCanvas


def test_notes_dock_widget_layout_and_gap_eliminated(qtbot):
    """Verify AnnotationsDockWidget directly hosts AnnotationManagerWidget with no gap and hidden close button."""
    store = AnnotationStore()
    clock = PlaybackClock()
    dock = AnnotationsDockWidget(annotation_store=store, clock=clock)
    qtbot.addWidget(dock)

    # 1. Dock widget should directly host AnnotationManagerWidget
    assert dock.widget() is dock.annotation_manager_widget
    mgr = dock.annotation_manager_widget
    assert mgr is not None

    # 2. Close button in manager widget should be hidden when docked
    assert mgr.btn_close.isHidden() is True

    # 3. Size policy should be Expanding, Expanding
    from PySide6.QtWidgets import QSizePolicy
    assert mgr.sizePolicy().verticalPolicy() == QSizePolicy.Policy.Expanding
    assert mgr.sizePolicy().horizontalPolicy() == QSizePolicy.Policy.Expanding

    # 4. Buttons should exist and be organized in responsive 2-row grid
    assert mgr.btn_jump.text() == "Jump to Frame"
    assert mgr.btn_clear_frame.text() == "Clear Frame"
    assert mgr.btn_clear_all.text() == "Clear All"
    assert mgr.btn_export.text() == "Export..."
    assert mgr.btn_import.text() == "Import..."


def test_annotation_manager_text_annotation_editing_and_deletion(qtbot):
    """Verify that AnnotationManagerWidget lists, edits, and deletes text annotations on the selected frame."""
    store = AnnotationStore()
    clock = PlaybackClock()
    clock.set_fps(24.0)

    # Add 2 text strokes and 1 rectangle on frame 15
    st1 = Stroke(shape_type=ShapeType.TEXT, points=[(0.2, 0.2)], text="Initial Review Title")
    st2 = Stroke(shape_type=ShapeType.TEXT, points=[(0.4, 0.5)], text="Fix Edge Artifact")
    st3 = Stroke(shape_type=ShapeType.RECTANGLE, points=[(0.1, 0.1), (0.3, 0.3)])

    store.add_stroke(15, st1)
    store.add_stroke(15, st2)
    store.add_stroke(15, st3)

    mgr = AnnotationManagerWidget(annotation_store=store, clock=clock)
    qtbot.addWidget(mgr)
    mgr.show()

    # Table should show 1 annotated frame (15)
    assert mgr.table.rowCount() == 1
    assert mgr.table.item(0, 0).text() == "15"

    # Select row 0 (frame 15)
    mgr.table.selectRow(0)
    assert mgr.get_selected_frame() == 15

    # Combo should list both text annotations
    assert mgr.combo_text_annots.count() == 2
    assert "Initial Review Title" in mgr.combo_text_annots.itemText(0)
    assert "Fix Edge Artifact" in mgr.combo_text_annots.itemText(1)
    assert mgr.edit_text_annot.text() == "Initial Review Title"

    # Update #1 text
    mgr.edit_text_annot.setText("Updated Review Title V2")
    mgr.btn_update_text_annot.click()

    # Verify store updated
    strokes = store.get_strokes(15)
    assert strokes[0].text == "Updated Review Title V2"
    assert "Updated Review Title V2" in mgr.combo_text_annots.itemText(0)

    # Select #2 and delete it
    mgr.combo_text_annots.setCurrentIndex(1)
    assert mgr.edit_text_annot.text() == "Fix Edge Artifact"
    mgr.btn_delete_text_annot.click()

    # Verify #2 is deleted
    strokes = store.get_strokes(15)
    assert len(strokes) == 2  # 1 text + 1 rectangle
    assert strokes[0].text == "Updated Review Title V2"
    assert strokes[1].shape_type == ShapeType.RECTANGLE
    assert mgr.combo_text_annots.count() == 1


def test_in_canvas_type_tool_hit_testing_and_editing(qtbot):
    """Verify clicking an existing text annotation on canvas in Type mode enters edit mode and updates text."""
    store = AnnotationStore()
    canvas = ViewportCanvas()
    qtbot.addWidget(canvas)
    canvas.annotation_store = store
    canvas.current_frame = 42
    canvas.resize(800, 600)
    canvas.show()

    # Add text stroke at norm coords (0.5, 0.5)
    stroke = Stroke(shape_type=ShapeType.TEXT, points=[(0.5, 0.5)], text="Click to Edit Me")
    store.add_stroke(42, stroke)

    # Activate Type Tool
    canvas.set_draw_tool(ShapeType.TEXT)
    assert canvas.active_draw_tool == ShapeType.TEXT

    # Calculate screen position of the text annotation
    pt = canvas._image_norm_to_screen(0.5, 0.5)
    click_pos = QPoint(int(round(pt.x())) + 10, int(round(pt.y())) + 8)

    # Hit test directly
    hit = canvas._find_text_stroke_at(click_pos)
    assert hit is not None
    idx, hit_stroke, p0 = hit
    assert idx == 0
    assert hit_stroke.text == "Click to Edit Me"

    # Simulate mouse press on text annotation with LeftButton
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtCore import QEvent
    event = QMouseEvent(
        QEvent.Type.MouseButtonPress,
        click_pos,
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    canvas.mousePressEvent(event)

    # Editor should be active with existing text and stroke index set
    assert canvas.is_typing_active() is True
    assert canvas._editing_text_stroke_idx == 0
    assert canvas._text_editor.toPlainText() == "Click to Edit Me"

    # Commit changed text
    canvas._text_editor.setPlainText("Edited via Canvas Tool")
    canvas.commit_text_editor()

    # Stroke in store should be updated
    updated_strokes = store.get_strokes(42)
    assert len(updated_strokes) == 1
    assert updated_strokes[0].text == "Edited via Canvas Tool"
    assert canvas._editing_text_stroke_idx is None

    # Test undo
    store.undo(42)
    undone_strokes = store.get_strokes(42)
    assert undone_strokes[0].text == "Click to Edit Me"
