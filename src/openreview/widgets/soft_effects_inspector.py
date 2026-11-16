"""
Soft Effects & Color Inspector Widget.
Provides:
- Collapsible cards for each applied soft effect with Active toggle, Reorder (▲/▼), and Remove (✕)
- Enhanced Transform with Scale X/Y, Uniform Lock, Pan X/Y, Rotation (-360 to +360), Anchor X/Y, Flip H/V
- Additional editorial soft effects: Crop & Blanking (Aspect presets & edge crops), Burn-in / Text Slate, Opacity & Blend Mode, CDL Grade, Retime
- Stackable non-destructive parameter updates wired live to sequence and viewport compositor
"""
from __future__ import annotations

import logging
from typing import Optional, Dict, Any, List

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont, QColor
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QFormLayout,
    QLabel,
    QDoubleSpinBox,
    QSpinBox,
    QSlider,
    QComboBox,
    QLineEdit,
    QCheckBox,
    QPushButton,
    QToolButton,
    QScrollArea,
    QFrame,
    QGroupBox,
)

from openreview.models.timeline_sequence import ClipItem, SoftEffect, SoftEffectType
from openreview.core.undo_manager import UndoManager, SoftEffectCommand

logger = logging.getLogger(__name__)


class SoftEffectCard(QFrame):
    """
    Collapsible card container for an individual soft effect in the clip's stack.
    Includes active toggle, collapse/expand chevron, title, reorder up/down, and delete.
    """
    param_changed = Signal(str, object)   # param_name, new_val
    param_editing_finished = Signal(dict, dict)  # old_params, new_params
    toggled = Signal(bool)
    collapsed_toggled = Signal(bool)
    move_up_requested = Signal()
    move_down_requested = Signal()
    remove_requested = Signal()

    def __init__(self, effect: SoftEffect, is_first: bool, is_last: bool, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.effect = effect
        self._is_first = is_first
        self._is_last = is_last
        self._uniform_scale_locked = bool(effect.parameters.get("uniform_scale_lock", True))
        self._editing_start_params = dict(effect.parameters)

        self.setObjectName("softEffectCard")
        self.setStyleSheet("""
            QFrame#softEffectCard {
                background-color: #1a1a1a;
                border: 1px solid #303030;
                border-radius: 4px;
            }
            QFrame#softEffectCard:hover {
                border-color: #444444;
            }
            QToolButton {
                background: transparent;
                border: none;
                color: #aaaaaa;
                font-weight: bold;
                font-size: 11px;
                padding: 2px 4px;
                border-radius: 2px;
            }
            QToolButton:hover {
                background-color: #333333;
                color: #ffffff;
            }
            QToolButton#deleteBtn:hover {
                background-color: #a82323;
                color: #ffffff;
            }
            QToolButton#toggleCollapse {
                font-size: 10px;
                color: #e5a823;
            }
            QLabel.titleLabel {
                font-weight: bold;
                font-size: 11px;
                color: #e5a823;
            }
            QDoubleSpinBox, QSpinBox, QComboBox, QLineEdit {
                background-color: #121212;
                border: 1px solid #2d2d2d;
                border-radius: 3px;
                color: #f0f0f0;
                padding: 2px 4px;
                font-size: 11px;
            }
            QDoubleSpinBox:focus, QSpinBox:focus, QComboBox:focus, QLineEdit:focus {
                border: 1px solid #e5a823;
            }
            QCheckBox {
                color: #cccccc;
                font-size: 11px;
            }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(4)

        # Header bar
        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(4)

        # Collapse / Expand chevron
        self.btn_collapse = QToolButton(self)
        self.btn_collapse.setObjectName("toggleCollapse")
        self.btn_collapse.setText("▶" if self.effect.collapsed else "▼")
        self.btn_collapse.setToolTip("Collapse / Expand Effect Controls")
        self.btn_collapse.clicked.connect(self._toggle_collapse)
        header.addWidget(self.btn_collapse)

        # Enable / Active checkbox
        self.chk_active = QCheckBox("Active", self)
        self.chk_active.setChecked(self.effect.enabled)
        self.chk_active.toggled.connect(self._on_active_toggled)
        header.addWidget(self.chk_active)

        # Effect Title
        title_text = self._get_display_title(self.effect)
        self.lbl_title = QLabel(title_text, self)
        self.lbl_title.setProperty("class", "titleLabel")
        self.lbl_title.setStyleSheet("font-weight: bold; font-size: 11px; color: #e5a823;")
        header.addWidget(self.lbl_title, 1)

        # Move Up (▲)
        self.btn_up = QToolButton(self)
        self.btn_up.setText("▲")
        self.btn_up.setToolTip("Move Effect Up (Render Earlier)")
        self.btn_up.setEnabled(not self._is_first)
        self.btn_up.clicked.connect(self.move_up_requested.emit)
        header.addWidget(self.btn_up)

        # Move Down (▼)
        self.btn_down = QToolButton(self)
        self.btn_down.setText("▼")
        self.btn_down.setToolTip("Move Effect Down (Render Later)")
        self.btn_down.setEnabled(not self._is_last)
        self.btn_down.clicked.connect(self.move_down_requested.emit)
        header.addWidget(self.btn_down)

        # Remove / Delete (✕)
        self.btn_del = QToolButton(self)
        self.btn_del.setObjectName("deleteBtn")
        self.btn_del.setText("✕")
        self.btn_del.setToolTip("Delete Soft Effect")
        self.btn_del.clicked.connect(self.remove_requested.emit)
        header.addWidget(self.btn_del)

        layout.addLayout(header)

        # Parameters body container
        self.body_widget = QWidget(self)
        self._build_parameters_ui(self.body_widget)
        self.body_widget.setVisible(not self.effect.collapsed)
        layout.addWidget(self.body_widget)

    def _get_display_title(self, fx: SoftEffect) -> str:
        if fx.name:
            return fx.name
        titles = {
            SoftEffectType.GRADE_CDL: "Color Grade (CDL)",
            SoftEffectType.TRANSFORM: "Transform / Reformat",
            SoftEffectType.MASK: "Crop & Aspect Blanking",
            SoftEffectType.BURNIN: "Text / Burn-in Overlay",
            SoftEffectType.OPACITY: "Opacity & Blend Mode",
            SoftEffectType.RETIME: "Retime / Speed",
        }
        return titles.get(fx.effect_type, fx.effect_type.value.title())

    def _toggle_collapse(self) -> None:
        new_state = not self.effect.collapsed
        self.effect.collapsed = new_state
        self.btn_collapse.setText("▶" if new_state else "▼")
        self.body_widget.setVisible(not new_state)
        self.collapsed_toggled.emit(new_state)

    def _on_active_toggled(self, val: bool) -> None:
        self.effect.enabled = val
        self.toggled.emit(val)

    def _build_parameters_ui(self, parent_widget: QWidget) -> None:
        body_layout = QVBoxLayout(parent_widget)
        body_layout.setContentsMargins(4, 4, 4, 2)
        body_layout.setSpacing(4)

        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setSpacing(5)

        fx = self.effect
        p = fx.parameters

        if fx.effect_type == SoftEffectType.GRADE_CDL:
            # Slope, Offset, Power, Saturation
            spin_slope = QDoubleSpinBox()
            spin_slope.setRange(0.0, 10.0)
            spin_slope.setSingleStep(0.05)
            spin_slope.setValue(p.get("slope", 1.0))
            spin_slope.valueChanged.connect(lambda v: self.param_changed.emit("slope", v))
            form.addRow("Slope:", spin_slope)

            spin_offset = QDoubleSpinBox()
            spin_offset.setRange(-2.0, 2.0)
            spin_offset.setSingleStep(0.02)
            spin_offset.setValue(p.get("offset", 0.0))
            spin_offset.valueChanged.connect(lambda v: self.param_changed.emit("offset", v))
            form.addRow("Offset:", spin_offset)

            spin_power = QDoubleSpinBox()
            spin_power.setRange(0.1, 5.0)
            spin_power.setSingleStep(0.05)
            spin_power.setValue(p.get("power", 1.0))
            spin_power.valueChanged.connect(lambda v: self.param_changed.emit("power", v))
            form.addRow("Power:", spin_power)

            spin_sat = QDoubleSpinBox()
            spin_sat.setRange(0.0, 5.0)
            spin_sat.setSingleStep(0.05)
            spin_sat.setValue(p.get("saturation", 1.0))
            spin_sat.valueChanged.connect(lambda v: self.param_changed.emit("saturation", v))
            form.addRow("Saturation:", spin_sat)

        elif fx.effect_type == SoftEffectType.TRANSFORM:
            # Pan / Position X & Y
            pan_layout = QHBoxLayout()
            spin_tx = QDoubleSpinBox()
            spin_tx.setRange(-4000.0, 4000.0)
            spin_tx.setSingleStep(1.0)
            spin_tx.setValue(p.get("translate_x", p.get("pan_x", 0.0)))
            spin_tx.valueChanged.connect(lambda v: self._on_pan_x_changed(v))
            pan_layout.addWidget(QLabel("X:"))
            pan_layout.addWidget(spin_tx, 1)

            spin_ty = QDoubleSpinBox()
            spin_ty.setRange(-4000.0, 4000.0)
            spin_ty.setSingleStep(1.0)
            spin_ty.setValue(p.get("translate_y", p.get("pan_y", 0.0)))
            spin_ty.valueChanged.connect(lambda v: self._on_pan_y_changed(v))
            pan_layout.addWidget(QLabel("Y:"))
            pan_layout.addWidget(spin_ty, 1)

            btn_reset_pan = QToolButton()
            btn_reset_pan.setText("⟲")
            btn_reset_pan.setToolTip("Reset Pan to 0,0")
            btn_reset_pan.clicked.connect(lambda: (spin_tx.setValue(0.0), spin_ty.setValue(0.0)))
            pan_layout.addWidget(btn_reset_pan)
            form.addRow("Position (Pan):", pan_layout)

            # Scale X, Scale Y & Uniform Lock
            scale_layout = QHBoxLayout()
            self.spin_sx = QDoubleSpinBox()
            self.spin_sx.setRange(0.01, 20.0)
            self.spin_sx.setSingleStep(0.05)
            curr_scale = p.get("scale", 1.0)
            self.spin_sx.setValue(p.get("scale_x", curr_scale))

            self.spin_sy = QDoubleSpinBox()
            self.spin_sy.setRange(0.01, 20.0)
            self.spin_sy.setSingleStep(0.05)
            self.spin_sy.setValue(p.get("scale_y", curr_scale))

            self.btn_lock_scale = QToolButton()
            self.btn_lock_scale.setCheckable(True)
            self.btn_lock_scale.setChecked(self._uniform_scale_locked)
            self.btn_lock_scale.setText("🔗" if self._uniform_scale_locked else "🔓")
            self.btn_lock_scale.setToolTip("Lock Uniform Scale Ratio")
            self.btn_lock_scale.toggled.connect(self._on_lock_scale_toggled)

            self.spin_sx.valueChanged.connect(self._on_scale_x_changed)
            self.spin_sy.valueChanged.connect(self._on_scale_y_changed)

            scale_layout.addWidget(QLabel("X:"))
            scale_layout.addWidget(self.spin_sx, 1)
            scale_layout.addWidget(self.btn_lock_scale)
            scale_layout.addWidget(QLabel("Y:"))
            scale_layout.addWidget(self.spin_sy, 1)

            btn_reset_scale = QToolButton()
            btn_reset_scale.setText("⟲")
            btn_reset_scale.setToolTip("Reset Scale to 1.0")
            btn_reset_scale.clicked.connect(lambda: (self.spin_sx.setValue(1.0), self.spin_sy.setValue(1.0)))
            scale_layout.addWidget(btn_reset_scale)
            form.addRow("Scale:", scale_layout)

            # Rotation (Angle & Quick 90 deg buttons)
            rot_layout = QHBoxLayout()
            spin_rot = QDoubleSpinBox()
            spin_rot.setRange(-360.0, 360.0)
            spin_rot.setSingleStep(1.0)
            spin_rot.setSuffix("°")
            spin_rot.setValue(p.get("rotation", 0.0))
            spin_rot.valueChanged.connect(lambda v: self.param_changed.emit("rotation", v))
            rot_layout.addWidget(spin_rot, 1)

            btn_rot_neg90 = QPushButton("-90°")
            btn_rot_neg90.setFixedHeight(22)
            btn_rot_neg90.clicked.connect(lambda: spin_rot.setValue((spin_rot.value() - 90) % 360))
            rot_layout.addWidget(btn_rot_neg90)

            btn_rot_pos90 = QPushButton("+90°")
            btn_rot_pos90.setFixedHeight(22)
            btn_rot_pos90.clicked.connect(lambda: spin_rot.setValue((spin_rot.value() + 90) % 360))
            rot_layout.addWidget(btn_rot_pos90)

            btn_reset_rot = QToolButton()
            btn_reset_rot.setText("⟲")
            btn_reset_rot.setToolTip("Reset Rotation to 0°")
            btn_reset_rot.clicked.connect(lambda: spin_rot.setValue(0.0))
            rot_layout.addWidget(btn_reset_rot)
            form.addRow("Rotation:", rot_layout)

            # Center / Anchor Point
            anchor_layout = QHBoxLayout()
            spin_ax = QDoubleSpinBox()
            spin_ax.setRange(-1.0, 2.0)
            spin_ax.setSingleStep(0.05)
            spin_ax.setValue(p.get("anchor_x", 0.5))
            spin_ax.valueChanged.connect(lambda v: self.param_changed.emit("anchor_x", v))
            anchor_layout.addWidget(QLabel("X:"))
            anchor_layout.addWidget(spin_ax, 1)

            spin_ay = QDoubleSpinBox()
            spin_ay.setRange(-1.0, 2.0)
            spin_ay.setSingleStep(0.05)
            spin_ay.setValue(p.get("anchor_y", 0.5))
            spin_ay.valueChanged.connect(lambda v: self.param_changed.emit("anchor_y", v))
            anchor_layout.addWidget(QLabel("Y:"))
            anchor_layout.addWidget(spin_ay, 1)
            form.addRow("Anchor Center:", anchor_layout)

            # Flips (Horizontal / Vertical)
            flip_layout = QHBoxLayout()
            btn_flip_h = QPushButton("Flip Horizontal")
            btn_flip_h.setCheckable(True)
            btn_flip_h.setChecked(bool(p.get("flip_h", False)))
            btn_flip_h.toggled.connect(lambda v: self.param_changed.emit("flip_h", v))
            flip_layout.addWidget(btn_flip_h)

            btn_flip_v = QPushButton("Flip Vertical")
            btn_flip_v.setCheckable(True)
            btn_flip_v.setChecked(bool(p.get("flip_v", False)))
            btn_flip_v.toggled.connect(lambda v: self.param_changed.emit("flip_v", v))
            flip_layout.addWidget(btn_flip_v)
            form.addRow("Orientation:", flip_layout)

        elif fx.effect_type == SoftEffectType.MASK:
            # Crop & Blanking
            combo_mask = QComboBox()
            combo_mask.addItems([
                "Custom Crop",
                "2.39:1 CinemaScope",
                "1.85:1 Flat",
                "1.78:1 (16:9)",
                "1.33:1 (4:3)",
                "1.00:1 Square",
                "9:16 Vertical",
            ])
            cur_mask = p.get("aspect", "2.39:1 CinemaScope")
            idx = combo_mask.findText(cur_mask)
            if idx >= 0:
                combo_mask.setCurrentIndex(idx)
            combo_mask.currentTextChanged.connect(lambda v: self.param_changed.emit("aspect", v))
            form.addRow("Aspect Preset:", combo_mask)

            # Custom Crop sliders
            crop_lr_layout = QHBoxLayout()
            spin_crop_l = QDoubleSpinBox()
            spin_crop_l.setRange(0.0, 50.0)
            spin_crop_l.setSuffix("%")
            spin_crop_l.setValue(p.get("crop_left", 0.0))
            spin_crop_l.valueChanged.connect(lambda v: self.param_changed.emit("crop_left", v))
            crop_lr_layout.addWidget(QLabel("Left:"))
            crop_lr_layout.addWidget(spin_crop_l, 1)

            spin_crop_r = QDoubleSpinBox()
            spin_crop_r.setRange(0.0, 50.0)
            spin_crop_r.setSuffix("%")
            spin_crop_r.setValue(p.get("crop_right", 0.0))
            spin_crop_r.valueChanged.connect(lambda v: self.param_changed.emit("crop_right", v))
            crop_lr_layout.addWidget(QLabel("Right:"))
            crop_lr_layout.addWidget(spin_crop_r, 1)
            form.addRow("Horizontal Crop:", crop_lr_layout)

            crop_tb_layout = QHBoxLayout()
            spin_crop_t = QDoubleSpinBox()
            spin_crop_t.setRange(0.0, 50.0)
            spin_crop_t.setSuffix("%")
            spin_crop_t.setValue(p.get("crop_top", 0.0))
            spin_crop_t.valueChanged.connect(lambda v: self.param_changed.emit("crop_top", v))
            crop_tb_layout.addWidget(QLabel("Top:"))
            crop_tb_layout.addWidget(spin_crop_t, 1)

            spin_crop_b = QDoubleSpinBox()
            spin_crop_b.setRange(0.0, 50.0)
            spin_crop_b.setSuffix("%")
            spin_crop_b.setValue(p.get("crop_bottom", 0.0))
            spin_crop_b.valueChanged.connect(lambda v: self.param_changed.emit("crop_bottom", v))
            crop_tb_layout.addWidget(QLabel("Bottom:"))
            crop_tb_layout.addWidget(spin_crop_b, 1)
            form.addRow("Vertical Crop:", crop_tb_layout)

        elif fx.effect_type == SoftEffectType.BURNIN:
            edit_burn = QLineEdit(p.get("text", "{shot} | {version} | {src_tc}"))
            edit_burn.textChanged.connect(lambda v: self.param_changed.emit("text", v))
            form.addRow("Format / Text:", edit_burn)

            combo_pos = QComboBox()
            combo_pos.addItems([
                "Bottom Center",
                "Bottom Left",
                "Bottom Right",
                "Top Center",
                "Top Left",
                "Top Right",
            ])
            cur_pos = p.get("placement", "Bottom Center")
            idx = combo_pos.findText(cur_pos)
            if idx >= 0:
                combo_pos.setCurrentIndex(idx)
            combo_pos.currentTextChanged.connect(lambda v: self.param_changed.emit("placement", v))
            form.addRow("Placement:", combo_pos)

            spin_size = QSpinBox()
            spin_size.setRange(8, 72)
            spin_size.setValue(p.get("font_size", 18))
            spin_size.valueChanged.connect(lambda v: self.param_changed.emit("font_size", v))
            form.addRow("Font Size (pt):", spin_size)

            slider_burn_op = QSlider(Qt.Orientation.Horizontal)
            slider_burn_op.setRange(0, 100)
            slider_burn_op.setValue(int(p.get("opacity", 1.0) * 100))
            slider_burn_op.valueChanged.connect(lambda v: self.param_changed.emit("opacity", float(v) / 100.0))
            form.addRow("Text Opacity:", slider_burn_op)

        elif fx.effect_type == SoftEffectType.OPACITY:
            op_layout = QHBoxLayout()
            spin_op = QDoubleSpinBox()
            spin_op.setRange(0.0, 100.0)
            spin_op.setSuffix("%")
            spin_op.setValue(p.get("opacity_percent", 100.0))
            spin_op.valueChanged.connect(lambda v: self.param_changed.emit("opacity_percent", v))
            op_layout.addWidget(spin_op, 1)
            form.addRow("Clip Opacity:", op_layout)

            combo_blend = QComboBox()
            combo_blend.addItems(["Normal", "Add", "Multiply", "Screen", "Overlay"])
            cur_blend = p.get("blend_mode", "Normal")
            idx = combo_blend.findText(cur_blend)
            if idx >= 0:
                combo_blend.setCurrentIndex(idx)
            combo_blend.currentTextChanged.connect(lambda v: self.param_changed.emit("blend_mode", v))
            form.addRow("Blend Mode:", combo_blend)

        elif fx.effect_type == SoftEffectType.RETIME:
            spin_speed = QDoubleSpinBox()
            spin_speed.setRange(1.0, 1000.0)
            spin_speed.setValue(p.get("speed_percent", 100.0))
            spin_speed.valueChanged.connect(lambda v: self.param_changed.emit("speed_percent", v))
            form.addRow("Speed %:", spin_speed)

        body_layout.addLayout(form)

        for child in parent_widget.findChildren(QWidget):
            if isinstance(child, (QDoubleSpinBox, QSpinBox, QSlider, QComboBox, QLineEdit, QCheckBox)):
                self._track_control(child)

    def _track_control(self, widget: QWidget) -> None:
        if hasattr(widget, "editingFinished"):
            widget.editingFinished.connect(self._check_and_emit_param_finished)
        if hasattr(widget, "sliderReleased"):
            widget.sliderReleased.connect(self._check_and_emit_param_finished)
        if isinstance(widget, QComboBox):
            widget.currentIndexChanged.connect(lambda _: self._check_and_emit_param_finished())
        elif isinstance(widget, QCheckBox):
            widget.toggled.connect(lambda _: self._check_and_emit_param_finished())

    def _check_and_emit_param_finished(self) -> None:
        curr = dict(self.effect.parameters)
        if curr != self._editing_start_params:
            old_p = self._editing_start_params
            self._editing_start_params = curr
            self.param_editing_finished.emit(old_p, curr)

    def _on_pan_x_changed(self, v: float) -> None:
        self.effect.parameters["translate_x"] = v
        self.effect.parameters["pan_x"] = v
        self.param_changed.emit("translate_x", v)

    def _on_pan_y_changed(self, v: float) -> None:
        self.effect.parameters["translate_y"] = v
        self.effect.parameters["pan_y"] = v
        self.param_changed.emit("translate_y", v)

    def _on_lock_scale_toggled(self, locked: bool) -> None:
        self._uniform_scale_locked = locked
        self.btn_lock_scale.setText("🔗" if locked else "🔓")
        self.effect.parameters["uniform_scale_lock"] = locked
        self.param_changed.emit("uniform_scale_lock", locked)

    def _on_scale_x_changed(self, v: float) -> None:
        self.effect.parameters["scale_x"] = v
        self.effect.parameters["scale"] = v
        if self._uniform_scale_locked and abs(self.spin_sy.value() - v) > 0.001:
            self.spin_sy.blockSignals(True)
            self.spin_sy.setValue(v)
            self.spin_sy.blockSignals(False)
            self.effect.parameters["scale_y"] = v
        self.param_changed.emit("scale_x", v)

    def _on_scale_y_changed(self, v: float) -> None:
        self.effect.parameters["scale_y"] = v
        if self._uniform_scale_locked and abs(self.spin_sx.value() - v) > 0.001:
            self.spin_sx.blockSignals(True)
            self.spin_sx.setValue(v)
            self.spin_sx.blockSignals(False)
            self.effect.parameters["scale_x"] = v
            self.effect.parameters["scale"] = v
        self.param_changed.emit("scale_y", v)


class SoftEffectsInspector(QWidget):
    """
    Inspector panel for configuring non-destructive real-time GPU Soft Effects.
    Supports collapsible cards, reordering, adding, and removing effects.
    """
    effect_changed = Signal(str, str)  # clip_id, effect_id

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._clip: Optional[ClipItem] = None
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)

        # Header
        self.lbl_clip_name = QLabel("No Clip Selected", self)
        self.lbl_clip_name.setStyleSheet("font-weight: bold; font-size: 12px; color: #e5a823;")
        layout.addWidget(self.lbl_clip_name)

        # Add Effect Bar
        btn_bar = QHBoxLayout()
        btn_bar.setContentsMargins(0, 0, 0, 0)
        btn_bar.setSpacing(4)

        self.combo_add_fx = QComboBox(self)
        self.combo_add_fx.addItem("+ Add Transform / Reformat", SoftEffectType.TRANSFORM.value)
        self.combo_add_fx.addItem("+ Add Crop & Blanking", SoftEffectType.MASK.value)
        self.combo_add_fx.addItem("+ Add Grade (CDL)", SoftEffectType.GRADE_CDL.value)
        self.combo_add_fx.addItem("+ Add Text / Burn-in Overlay", SoftEffectType.BURNIN.value)
        self.combo_add_fx.addItem("+ Add Opacity & Blend Mode", SoftEffectType.OPACITY.value)
        self.combo_add_fx.addItem("+ Add Retime / Speed", SoftEffectType.RETIME.value)
        btn_bar.addWidget(self.combo_add_fx, 1)

        self.btn_add = QPushButton("Add", self)
        self.btn_add.setStyleSheet("""
            QPushButton {
                background-color: #2b2b2b;
                color: #e5a823;
                border: 1px solid #444444;
                font-weight: bold;
                padding: 3px 8px;
                border-radius: 3px;
            }
            QPushButton:hover {
                background-color: #383838;
                border-color: #e5a823;
            }
        """)
        self.btn_add.clicked.connect(self._on_add_effect)
        btn_bar.addWidget(self.btn_add)
        layout.addLayout(btn_bar)

        # Scroll area for effects stack
        self.scroll = QScrollArea(self)
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll_content = QWidget()
        self.stack_layout = QVBoxLayout(self.scroll_content)
        self.stack_layout.setContentsMargins(0, 0, 0, 0)
        self.stack_layout.setSpacing(6)
        self.stack_layout.addStretch()
        self.scroll.setWidget(self.scroll_content)
        layout.addWidget(self.scroll, 1)

    def set_clip(self, clip: Optional[ClipItem]) -> None:
        self._clip = clip
        if not clip:
            self.lbl_clip_name.setText("No Clip Selected")
            self._clear_stack()
            return

        self.lbl_clip_name.setText(f"Clip: {clip.name} [{clip.version}]")
        self._rebuild_stack()

    def _clear_stack(self) -> None:
        while self.stack_layout.count() > 1:
            item = self.stack_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def _rebuild_stack(self) -> None:
        self._clear_stack()
        if not self._clip:
            return

        total_fx = len(self._clip.soft_effects)
        for idx, fx in enumerate(self._clip.soft_effects):
            card = SoftEffectCard(
                effect=fx,
                is_first=(idx == 0),
                is_last=(idx == total_fx - 1),
                parent=self.scroll_content,
            )
            card.param_changed.connect(lambda k, v, f=fx: self._update_param(f, k, v))
            card.param_editing_finished.connect(lambda old_p, new_p, f=fx: self.record_param_change(f, old_p, new_p))
            card.toggled.connect(lambda v, f=fx: self._on_toggle_effect(f, v))
            card.move_up_requested.connect(lambda i=idx: self._on_move_up(i))
            card.move_down_requested.connect(lambda i=idx: self._on_move_down(i))
            card.remove_requested.connect(lambda f=fx: self._on_remove_effect(f))

            # Insert before trailing stretch
            self.stack_layout.insertWidget(self.stack_layout.count() - 1, card)

    def record_param_change(self, fx: SoftEffect, old_params: Dict[str, Any], new_params: Dict[str, Any]) -> None:
        if not self._clip or old_params == new_params:
            return
        cmd = SoftEffectCommand(
            clip=self._clip,
            action="param_change",
            effect=fx,
            old_params=old_params,
            new_params=new_params,
            on_change=lambda: (self._rebuild_stack(), self.effect_changed.emit(self._clip.id, fx.id)),
        )
        UndoManager.instance().push(cmd)

    def _update_param(self, fx: SoftEffect, key: str, value: Any) -> None:
        fx.parameters[key] = value
        if self._clip:
            self.effect_changed.emit(self._clip.id, fx.id)

    def _on_toggle_effect(self, fx: SoftEffect, enabled: bool) -> None:
        old_params = dict(fx.parameters)
        old_enabled = fx.enabled
        fx.enabled = enabled
        if self._clip:
            cmd = SoftEffectCommand(
                clip=self._clip,
                action="param_change",
                effect=fx,
                old_params={"enabled": old_enabled},
                new_params={"enabled": enabled},
                on_change=lambda: (self._rebuild_stack(), self.effect_changed.emit(self._clip.id, fx.id)),
            )
            UndoManager.instance().push(cmd)
            self.effect_changed.emit(self._clip.id, fx.id)

    def _on_move_up(self, index: int) -> None:
        if not self._clip or index <= 0 or index >= len(self._clip.soft_effects):
            return
        fx = self._clip.soft_effects[index]
        if self._clip.move_soft_effect_up(index):
            cmd = SoftEffectCommand(
                clip=self._clip,
                action="reorder",
                effect=fx,
                old_index=index,
                new_index=index - 1,
                on_change=lambda: (self._rebuild_stack(), self.effect_changed.emit(self._clip.id, fx.id)),
            )
            UndoManager.instance().push(cmd)
            self._rebuild_stack()
            self.effect_changed.emit(self._clip.id, fx.id)

    def _on_move_down(self, index: int) -> None:
        if not self._clip or index < 0 or index >= len(self._clip.soft_effects) - 1:
            return
        fx = self._clip.soft_effects[index]
        if self._clip.move_soft_effect_down(index):
            cmd = SoftEffectCommand(
                clip=self._clip,
                action="reorder",
                effect=fx,
                old_index=index,
                new_index=index + 1,
                on_change=lambda: (self._rebuild_stack(), self.effect_changed.emit(self._clip.id, fx.id)),
            )
            UndoManager.instance().push(cmd)
            self._rebuild_stack()
            self.effect_changed.emit(self._clip.id, fx.id)

    def _on_remove_effect(self, fx: SoftEffect) -> None:
        if not self._clip:
            return
        idx = self._clip.soft_effects.index(fx) if fx in self._clip.soft_effects else -1
        if self._clip.remove_soft_effect(fx.id):
            cmd = SoftEffectCommand(
                clip=self._clip,
                action="remove",
                effect=fx,
                old_index=idx,
                on_change=lambda: (self._rebuild_stack(), self.effect_changed.emit(self._clip.id, fx.id)),
            )
            UndoManager.instance().push(cmd)
            self._rebuild_stack()
            self.effect_changed.emit(self._clip.id, fx.id)

    def _on_add_effect(self) -> None:
        if not self._clip:
            return
        eff_type_str = self.combo_add_fx.currentData()
        eff_type = SoftEffectType(eff_type_str)
        new_fx = self._clip.add_soft_effect(eff_type)
        if new_fx:
            cmd = SoftEffectCommand(
                clip=self._clip,
                action="add",
                effect=new_fx,
                new_index=len(self._clip.soft_effects) - 1,
                on_change=lambda: (self._rebuild_stack(), self.effect_changed.emit(self._clip.id, new_fx.id)),
            )
            UndoManager.instance().push(cmd)
            self._rebuild_stack()
            self.effect_changed.emit(self._clip.id, new_fx.id)
