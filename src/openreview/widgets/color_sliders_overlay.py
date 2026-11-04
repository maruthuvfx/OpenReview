"""
On-screen Color Adjustment Sliders Overlay (Gain, Gamma, Hue, Saturation).
Features high-precision horizontal scrubbing, dynamic ruler ticks with Gaussian
curve deformation and amber glow inspired by the Babydino ruler style,
75% resting opacity, 100% tooltip/hint opacity, crisp drop shadows for bright footage,
logarithmic 0.015-64.0 Gain matching Foundry Nuke & top control bar,
full two-way live linking, dynamic 70% viewport width scaling, and keyboard commit/cancel.
"""
from __future__ import annotations

import copy
import math
from typing import Optional, List, Tuple
from PySide6.QtCore import Qt, QRectF, QPointF, Signal, QEvent, QObject
from PySide6.QtGui import (
    QPainter,
    QPainterPath,
    QColor,
    QFont,
    QPen,
    QBrush,
    QRadialGradient,
    QLinearGradient,
    QMouseEvent,
    QWheelEvent,
)
from PySide6.QtWidgets import (
    QWidget,
    QHBoxLayout,
    QVBoxLayout,
    QApplication,
)

from openreview.color.lut_manager import ColorGradeSettings


class ColorRulerSlider(QWidget):
    """
    Individual horizontal ruler slider with organic Gaussian curve bulge,
    tick marks, amber glow thumb knob, floating numeric readout, and
    high-contrast drop shadows. Supports both linear and logarithmic scaling.
    """
    valueChanged = Signal(float)

    def __init__(
        self,
        name: str,
        min_val: float,
        max_val: float,
        default_val: float,
        step: float = 0.05,
        decimals: int = 2,
        suffix: str = "",
        is_log: bool = False,
        log_ticks: Optional[List[Tuple[float, str]]] = None,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.name = name
        self.min_val = float(min_val)
        self.max_val = float(max_val)
        self.default_val = float(default_val)
        self.step = float(step)
        self.decimals = decimals
        self.suffix = suffix
        self.is_log = is_log
        self.log_ticks = log_ticks or []
        self._value = float(default_val)

        # Log mapping params: default -6 EV (0.015625) to +6 EV (64.0)
        self._log_min_ev = math.log2(self.min_val) if self.min_val > 0 else -6.0
        self._log_max_ev = math.log2(self.max_val) if self.max_val > 0 else 6.0

        self._is_hovered: bool = False
        self._is_dragging: bool = False
        self._drag_start_x: float = 0.0
        self._drag_start_val: float = self._value

        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setCursor(Qt.CursorShape.SizeHorCursor)

        self.setMinimumSize(140, 78)
        self.setMaximumHeight(86)

    @property
    def value(self) -> float:
        return self._value

    @value.setter
    def value(self, val: float) -> None:
        clamped = max(self.min_val, min(self.max_val, float(val)))
        if abs(self._value - clamped) > 1e-4:
            self._value = clamped
            self.valueChanged.emit(self._value)
            self.update()

    def set_value_quiet(self, val: float) -> None:
        """Set value without re-emitting valueChanged signal."""
        clamped = max(self.min_val, min(self.max_val, float(val)))
        if abs(self._value - clamped) > 1e-4:
            self._value = clamped
            self.update()

    def reset_default(self) -> None:
        self.value = self.default_val

    def _val_to_ratio(self, val: float) -> float:
        """Map value to [0.0, 1.0] along track."""
        if self.is_log:
            ev = math.log2(max(1e-6, val))
            span = self._log_max_ev - self._log_min_ev
            ratio = (ev - self._log_min_ev) / max(1e-4, span)
            return max(0.0, min(1.0, ratio))
        else:
            span = self.max_val - self.min_val
            if span <= 0:
                return 0.0
            return max(0.0, min(1.0, (val - self.min_val) / span))

    def _ratio_to_val(self, ratio: float) -> float:
        """Map [0.0, 1.0] along track to parameter value."""
        ratio = max(0.0, min(1.0, ratio))
        if self.is_log:
            ev = self._log_min_ev + ratio * (self._log_max_ev - self._log_min_ev)
            raw = 2.0 ** ev
            if raw >= 10.0:
                return round(raw, 1)
            elif raw >= 1.0:
                return round(raw, 2)
            else:
                return round(raw, 3)
        else:
            raw = self.min_val + ratio * (self.max_val - self.min_val)
            return round(raw, self.decimals)

    def enterEvent(self, event) -> None:
        self._is_hovered = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._is_hovered = False
        self.update()
        super().leaveEvent(event)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._is_dragging = True
            self._drag_start_x = event.position().x()
            self._drag_start_val = self._value

            margin_x = 22.0
            track_w = max(10.0, float(self.width()) - 2.0 * margin_x)
            click_x = max(margin_x, min(float(self.width()) - margin_x, event.position().x()))
            ratio = (click_x - margin_x) / track_w
            self.value = self._ratio_to_val(ratio)
            self.update()
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._is_dragging:
            cur_x = event.position().x()
            margin_x = 22.0
            track_w = max(10.0, float(self.width()) - 2.0 * margin_x)

            if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
                # High-precision fine dragging
                dx = cur_x - self._drag_start_x
                if self.is_log:
                    delta_ev = (dx / track_w) * (self._log_max_ev - self._log_min_ev) * 0.15
                    start_ev = math.log2(max(1e-6, self._drag_start_val))
                    new_val = 2.0 ** (start_ev + delta_ev)
                    self.value = round(new_val, 3)
                else:
                    delta_val = (dx / track_w) * (self.max_val - self.min_val) * 0.15
                    self.value = round(self._drag_start_val + delta_val, self.decimals)
            else:
                clamped_x = max(margin_x, min(float(self.width()) - margin_x, cur_x))
                ratio = (clamped_x - margin_x) / track_w
                self.value = self._ratio_to_val(ratio)

            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._is_dragging = False
            self.update()
            event.accept()
        else:
            super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.reset_default()
            event.accept()
        else:
            super().mouseDoubleClickEvent(event)

    def wheelEvent(self, event: QWheelEvent) -> None:
        num_degrees = event.angleDelta().y() / 8.0
        num_steps = num_degrees / 15.0
        scale = 0.2 if (event.modifiers() & Qt.KeyboardModifier.ControlModifier) else 1.0

        if self.is_log:
            ev_step = 0.1 * scale * num_steps
            cur_ev = math.log2(max(1e-6, self._value))
            self.value = round(2.0 ** (cur_ev + ev_step), 3)
        else:
            step_val = self.step * scale * num_steps
            self.value = round(self._value + step_val, self.decimals)
        event.accept()

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)

        w = float(self.width())
        h = float(self.height())

        # Resting opacity 75%, active/hovered 100%
        is_active = self._is_hovered or self._is_dragging
        opacity = 1.0 if is_active else 0.75

        p.save()
        p.setOpacity(opacity)

        margin_x = 22.0
        track_w = max(10.0, w - 2.0 * margin_x)
        track_y = 46.0

        val_ratio = self._val_to_ratio(self._value)
        thumb_x = margin_x + val_ratio * track_w

        # --- 1. Parameter Label (Centered at top) with Drop Shadow ---
        p.save()
        label_font = QFont("Segoe UI", 9)
        label_font.setBold(True)
        label_font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 1.4)
        p.setFont(label_font)

        # Dark Drop Shadow
        p.setPen(QColor(0, 0, 0, 230 if is_active else 190))
        p.drawText(QRectF(0.0, 3.0, w, 16.0), Qt.AlignmentFlag.AlignCenter, self.name)
        # Foreground Text
        p.setPen(QColor(245, 245, 245, 255 if is_active else 225))
        p.drawText(QRectF(0.0, 2.0, w, 16.0), Qt.AlignmentFlag.AlignCenter, self.name)
        p.restore()

        # --- 2. Floating Numeric Readout above Knob with Drop Shadow ---
        p.save()
        readout_font = QFont("Segoe UI", 11)
        readout_font.setBold(True)
        p.setFont(readout_font)

        if self.is_log:
            if self._value >= 10.0:
                val_str = f"{self._value:.1f}{self.suffix}"
            elif self._value >= 1.0:
                val_str = f"{self._value:.2f}{self.suffix}"
            else:
                val_str = f"{self._value:.3f}{self.suffix}"
        else:
            val_str = f"{self._value:.{self.decimals}f}{self.suffix}"

        # Dark Drop Shadow
        p.setPen(QColor(0, 0, 0, 240 if is_active else 200))
        readout_rect_shadow = QRectF(thumb_x - 45.0, track_y - 25.0, 90.0, 18.0)
        p.drawText(readout_rect_shadow, Qt.AlignmentFlag.AlignCenter, val_str)

        # Amber Foreground Text
        p.setPen(QColor(245, 166, 35) if is_active else QColor(240, 175, 55))
        readout_rect = QRectF(thumb_x - 45.0, track_y - 26.0, 90.0, 18.0)
        p.drawText(readout_rect, Qt.AlignmentFlag.AlignCenter, val_str)
        p.restore()

        # --- 3. Gaussian Bulge Curve & Amber Glow ---
        amp = 13.0
        sigma = 20.0

        glow_grad = QRadialGradient(QPointF(thumb_x, track_y + amp * 0.45), sigma * 1.5)
        glow_alpha = 160 if is_active else 100
        glow_grad.setColorAt(0.0, QColor(245, 166, 35, glow_alpha))
        glow_grad.setColorAt(0.5, QColor(245, 140, 20, int(glow_alpha * 0.45)))
        glow_grad.setColorAt(1.0, QColor(245, 140, 20, 0))

        p.save()
        glow_path = QPainterPath()
        glow_path.moveTo(thumb_x - sigma * 1.8, track_y)
        steps = 28
        for i in range(steps + 1):
            px = (thumb_x - sigma * 1.8) + (i / float(steps)) * (sigma * 3.6)
            dx = px - thumb_x
            py = track_y + amp * math.exp(- (dx / sigma) ** 2)
            glow_path.lineTo(px, py)
        glow_path.lineTo(thumb_x + sigma * 1.8, track_y)
        glow_path.closeSubpath()
        p.fillPath(glow_path, QBrush(glow_grad))
        p.restore()

        # --- 4. Ruler Tick Marks with Drop Shadow Outlines ---
        p.save()
        tick_font = QFont("Segoe UI", 7)
        p.setFont(tick_font)

        if self.is_log and self.log_ticks:
            for val, lbl in self.log_ticks:
                t_ratio = self._val_to_ratio(val)
                tx = margin_x + t_ratio * track_w
                dx = tx - thumb_x
                base_y = track_y + amp * math.exp(- (dx / sigma) ** 2)
                tick_len = 7.0

                # Dark shadow under-stroke for ticks
                p.setPen(QPen(QColor(0, 0, 0, 220 if is_active else 180), 2.6))
                p.drawLine(QPointF(tx, base_y - tick_len), QPointF(tx, base_y))

                # Foreground tick stroke
                p.setPen(QPen(QColor(245, 245, 245, 240 if is_active else 200), 1.2))
                p.drawLine(QPointF(tx, base_y - tick_len), QPointF(tx, base_y))

                if abs(tx - thumb_x) > 16.0:
                    # Text Shadow
                    p.setPen(QColor(0, 0, 0, 230 if is_active else 190))
                    p.drawText(QRectF(tx - 15.0, base_y + 4.0, 30.0, 11.0), Qt.AlignmentFlag.AlignCenter, lbl)
                    # Text Foreground
                    p.setPen(QColor(230, 230, 230, 240 if is_active else 200))
                    p.drawText(QRectF(tx - 15.0, base_y + 3.0, 30.0, 11.0), Qt.AlignmentFlag.AlignCenter, lbl)
        else:
            num_major_ticks = 4
            num_sub_ticks = 4
            total_ticks = num_major_ticks * (num_sub_ticks + 1) + 1

            for i in range(total_ticks):
                t_ratio = i / float(total_ticks - 1)
                tx = margin_x + t_ratio * track_w
                is_major = (i % (num_sub_ticks + 1) == 0)

                dx = tx - thumb_x
                base_y = track_y + amp * math.exp(- (dx / sigma) ** 2)

                tick_len = 7.0 if is_major else 4.0

                # Dark shadow under-stroke
                p.setPen(QPen(QColor(0, 0, 0, 220 if is_active else 180), 2.6 if is_major else 2.0))
                p.drawLine(QPointF(tx, base_y - tick_len), QPointF(tx, base_y))

                # Foreground tick stroke
                if is_major:
                    p.setPen(QPen(QColor(245, 245, 245, 240 if is_active else 200), 1.2))
                    p.drawLine(QPointF(tx, base_y - tick_len), QPointF(tx, base_y))

                    if abs(tx - thumb_x) > 16.0:
                        tick_val = self.min_val + t_ratio * (self.max_val - self.min_val)
                        t_str = f"{tick_val:.0f}" if (self.max_val - self.min_val) >= 4.0 else f"{tick_val:.1f}"

                        p.setPen(QColor(0, 0, 0, 230 if is_active else 190))
                        p.drawText(QRectF(tx - 15.0, base_y + 4.0, 30.0, 11.0), Qt.AlignmentFlag.AlignCenter, t_str)
                        p.setPen(QColor(230, 230, 230, 240 if is_active else 200))
                        p.drawText(QRectF(tx - 15.0, base_y + 3.0, 30.0, 11.0), Qt.AlignmentFlag.AlignCenter, t_str)
                else:
                    p.setPen(QPen(QColor(210, 210, 210, 200 if is_active else 150), 0.9))
                    p.drawLine(QPointF(tx, base_y - tick_len), QPointF(tx, base_y))
        p.restore()

        # --- 5. Curved Track Path with Dark Drop Shadow Under-stroke ---
        track_path = QPainterPath()
        track_path.moveTo(margin_x, track_y)
        track_steps = 60
        for i in range(1, track_steps + 1):
            px = margin_x + (i / float(track_steps)) * track_w
            dx = px - thumb_x
            py = track_y + amp * math.exp(- (dx / sigma) ** 2)
            track_path.lineTo(px, py)

        p.save()
        p.setPen(QPen(QColor(0, 0, 0, 230 if is_active else 190), 3.4))
        p.drawPath(track_path)

        p.setPen(QPen(QColor(255, 255, 255, 230 if is_active else 180), 1.6))
        p.drawPath(track_path)
        p.restore()

        # --- 6. Thumb Knob with Arrows & Shadow ---
        knob_r = 12.0
        knob_y = track_y + amp * 0.72

        p.save()
        shadow_rect = QRectF(thumb_x - knob_r - 3.0, knob_y - knob_r + 1.0, (knob_r + 3.0) * 2, (knob_r + 3.0) * 2)
        shadow_grad = QRadialGradient(QPointF(thumb_x, knob_y + 1.5), knob_r + 5.0)
        shadow_grad.setColorAt(0.0, QColor(0, 0, 0, 240 if is_active else 180))
        shadow_grad.setColorAt(1.0, QColor(0, 0, 0, 0))
        p.fillRect(shadow_rect, QBrush(shadow_grad))

        knob_path = QPainterPath()
        knob_path.addEllipse(QPointF(thumb_x, knob_y), knob_r, knob_r)

        btn_grad = QLinearGradient(thumb_x, knob_y - knob_r, thumb_x, knob_y + knob_r)
        btn_grad.setColorAt(0.0, QColor(48, 50, 58))
        btn_grad.setColorAt(1.0, QColor(22, 23, 28))
        p.fillPath(knob_path, QBrush(btn_grad))

        border_pen = QPen(
            QColor(245, 166, 35, 255) if is_active else QColor(190, 190, 200, 210),
            1.5 if is_active else 1.2
        )
        p.setPen(border_pen)
        p.drawPath(knob_path)

        p.setPen(Qt.PenStyle.NoPen)
        arrow_color = QColor(245, 166, 35) if is_active else QColor(230, 230, 230)
        p.setBrush(QBrush(arrow_color))

        left_arr = QPainterPath()
        left_arr.moveTo(thumb_x - 6.0, knob_y)
        left_arr.lineTo(thumb_x - 2.5, knob_y - 3.0)
        left_arr.lineTo(thumb_x - 2.5, knob_y + 3.0)
        left_arr.closeSubpath()
        p.drawPath(left_arr)

        right_arr = QPainterPath()
        right_arr.moveTo(thumb_x + 6.0, knob_y)
        right_arr.lineTo(thumb_x + 2.5, knob_y - 3.0)
        right_arr.lineTo(thumb_x + 2.5, knob_y + 3.0)
        right_arr.closeSubpath()
        p.drawPath(right_arr)

        p.restore()
        p.restore()


class ColorSlidersOverlay(QWidget):
    """
    4 On-Screen Horizontal Color Sliders Overlay (Gain, Gamma, Hue, Saturation).
    Positioned at bottom center of viewport canvas.
    Transparent background, 75% resting opacity, 100% tooltip/hint bar opacity,
    drop shadows for bright footage, dynamically covers 70% of screen width,
    two-way linking with top toolbar, and keyboard commit/cancel.
    """
    colorGradeChanged = Signal(ColorGradeSettings)
    gainChanged = Signal(float)
    gammaChanged = Signal(float)
    saturationChanged = Signal(float)
    hueChanged = Signal(float)
    committed = Signal(ColorGradeSettings)
    cancelled = Signal()

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setStyleSheet("background: transparent; border: none;")

        self._original_settings: Optional[ColorGradeSettings] = None
        self._current_settings: ColorGradeSettings = ColorGradeSettings()
        self._filter_installed: bool = False
        self._block_sync: bool = False

        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(12, 4, 12, 4)
        root_layout.setSpacing(2)

        # 4 sliders side-by-side
        sliders_layout = QHBoxLayout()
        sliders_layout.setContentsMargins(0, 0, 0, 0)
        sliders_layout.setSpacing(16)

        # 1. Gain Slider (Foundry Nuke style 0.015 to 64.0 log scale, default 1.0)
        self.slider_gain = ColorRulerSlider(
            "GAIN",
            0.015,
            64.0,
            1.0,
            step=0.05,
            decimals=3,
            suffix="x",
            is_log=True,
            log_ticks=[(0.015, "0.015"), (0.125, "0.12"), (1.0, "1"), (8.0, "8"), (64.0, "64")],
            parent=self,
        )
        self.slider_gain.valueChanged.connect(self._on_gain_changed)
        sliders_layout.addWidget(self.slider_gain)

        # 2. Gamma Slider (0.0 to 4.0 matching top_bar, default 1.0)
        self.slider_gamma = ColorRulerSlider("GAMMA", 0.0, 4.0, 1.0, step=0.025, decimals=3, suffix="", parent=self)
        self.slider_gamma.valueChanged.connect(self._on_gamma_changed)
        sliders_layout.addWidget(self.slider_gamma)

        # 3. Hue Slider (-180.0 to 180.0, default 0.0)
        self.slider_hue = ColorRulerSlider("HUE", -180.0, 180.0, 0.0, step=1.0, decimals=1, suffix="°", parent=self)
        self.slider_hue.valueChanged.connect(self._on_hue_changed)
        sliders_layout.addWidget(self.slider_hue)

        # 4. Saturation Slider (0.0 to 4.0 matching top_bar, default 1.0)
        self.slider_sat = ColorRulerSlider("SATURATION", 0.0, 4.0, 1.0, step=0.025, decimals=3, suffix="", parent=self)
        self.slider_sat.valueChanged.connect(self._on_sat_changed)
        sliders_layout.addWidget(self.slider_sat)

        root_layout.addLayout(sliders_layout)
        self.adjustSize()
        self.hide()

    def set_gain_quiet(self, val: float) -> None:
        """Update Gain from top bar without re-triggering signal cycle."""
        self._block_sync = True
        try:
            self.slider_gain.set_value_quiet(val)
            self._current_settings.gain = (val, val, val)
        finally:
            self._block_sync = False

    def set_gamma_quiet(self, val: float) -> None:
        """Update Gamma from top bar without re-triggering signal cycle."""
        self._block_sync = True
        try:
            self.slider_gamma.set_value_quiet(val)
            self._current_settings.gamma = val
        finally:
            self._block_sync = False

    def set_saturation_quiet(self, val: float) -> None:
        """Update Saturation from top bar without re-triggering signal cycle."""
        self._block_sync = True
        try:
            self.slider_sat.set_value_quiet(val)
            self._current_settings.saturation = val
        finally:
            self._block_sync = False

    def set_hue_quiet(self, val: float) -> None:
        """Update Hue without re-triggering signal cycle."""
        self._block_sync = True
        try:
            self.slider_hue.set_value_quiet(val)
            self._current_settings.hue = val
        finally:
            self._block_sync = False

    def open_overlay(self, current_settings: ColorGradeSettings) -> None:
        """Open overlay initialized with current color parameters."""
        self._original_settings = copy.deepcopy(current_settings)
        self._current_settings = copy.deepcopy(current_settings)

        master_gain = float(self._current_settings.gain[0])
        self.slider_gain.set_value_quiet(master_gain)
        self.slider_gamma.set_value_quiet(self._current_settings.gamma)
        self.slider_hue.set_value_quiet(self._current_settings.hue)
        self.slider_sat.set_value_quiet(self._current_settings.saturation)

        self.auto_center_in_parent()
        self.show()
        self.raise_()
        self.setFocus()

        app = QApplication.instance()
        if app and not self._filter_installed:
            app.installEventFilter(self)
            self._filter_installed = True

    def commit(self) -> None:
        """Commit color adjustments and close overlay."""
        self._uninstall_filter()
        self.hide()
        self.committed.emit(self._current_settings)

    def cancel(self) -> None:
        """Revert changes back to initial state and close overlay."""
        self._uninstall_filter()
        self.hide()
        if self._original_settings is not None:
            self._current_settings = copy.deepcopy(self._original_settings)
            self.colorGradeChanged.emit(self._current_settings)
            self.gainChanged.emit(float(self._current_settings.gain[0]))
            self.gammaChanged.emit(float(self._current_settings.gamma))
            self.saturationChanged.emit(float(self._current_settings.saturation))
            self.hueChanged.emit(float(self._current_settings.hue))
        self.cancelled.emit()

    def toggle(self, current_settings: ColorGradeSettings) -> None:
        """Toggle open/close. Commits when closed via toggle."""
        if self.isVisible():
            self.commit()
        else:
            self.open_overlay(current_settings)

    def auto_center_in_parent(self) -> None:
        """
        Dynamically cover 70% of the viewport width with a sensible 640px minimum,
        positioned horizontally centered at the bottom of the parent canvas.
        If the Magnified Time Scrub Ruler is active, position stacked on top of it.
        """
        parent = self.parentWidget()
        if not parent:
            return

        pw = parent.width()
        ph = parent.height()

        desired_w = max(640, min(pw - 24, int(pw * 0.70)))
        h = 100
        self.setFixedSize(desired_w, h)

        x = max(8, (pw - desired_w) // 2)

        ruler_offset = 0
        for child in parent.children():
            if child != self and child.isWidgetType() and not getattr(child, "isHidden", lambda: True)():
                c_name = child.__class__.__name__
                if "MagnifiedTimeline" in c_name or "TimelinePopup" in c_name:
                    ruler_offset = child.height() + 12
                    break

        bottom_margin = 16 + ruler_offset
        y = max(8, ph - h - bottom_margin)
        self.move(x, y)

    def paintEvent(self, event) -> None:
        """Draw bottom shortcut hint bar at 100% opacity with drop shadow."""
        super().paintEvent(event)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)

        # 100% Opacity for tooltip hint bar
        p.setOpacity(1.0)

        w = float(self.width())
        h = float(self.height())

        hint_text = "Drag to scrub   •   Ctrl+Drag: fine tune   •   Double-click: reset   •   Enter: commit   •   Esc: cancel"
        font = QFont("Segoe UI", 8)
        p.setFont(font)

        # Drop Shadow
        p.setPen(QColor(0, 0, 0, 240))
        p.drawText(QRectF(0.0, h - 17.0, w, 16.0), Qt.AlignmentFlag.AlignCenter, hint_text)

        # Foreground 100% visible text
        p.setPen(QColor(220, 220, 225, 255))
        p.drawText(QRectF(0.0, h - 18.0, w, 16.0), Qt.AlignmentFlag.AlignCenter, hint_text)

    def _uninstall_filter(self) -> None:
        if self._filter_installed:
            app = QApplication.instance()
            if app:
                try:
                    app.removeEventFilter(self)
                except Exception:
                    pass
            self._filter_installed = False

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        """Intercept Enter, Escape, and 'S' to commit/cancel."""
        if not self.isVisible():
            return False

        if event.type() == QEvent.Type.KeyPress:
            key = event.key()
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self.commit()
                return True
            elif key == Qt.Key.Key_Escape:
                self.cancel()
                return True
            elif key == Qt.Key.Key_S and not (event.modifiers() & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier)):
                self.commit()
                return True

        return super().eventFilter(watched, event)

    def _on_gain_changed(self, val: float) -> None:
        if self._block_sync:
            return
        self._current_settings.gain = (val, val, val)
        self.colorGradeChanged.emit(self._current_settings)
        self.gainChanged.emit(val)

    def _on_gamma_changed(self, val: float) -> None:
        if self._block_sync:
            return
        self._current_settings.gamma = val
        self.colorGradeChanged.emit(self._current_settings)
        self.gammaChanged.emit(val)

    def _on_hue_changed(self, val: float) -> None:
        if self._block_sync:
            return
        self._current_settings.hue = val
        self.colorGradeChanged.emit(self._current_settings)
        self.hueChanged.emit(val)

    def _on_sat_changed(self, val: float) -> None:
        if self._block_sync:
            return
        self._current_settings.saturation = val
        self.colorGradeChanged.emit(self._current_settings)
        self.saturationChanged.emit(val)

    def hideEvent(self, event) -> None:
        self._uninstall_filter()
        super().hideEvent(event)
