"""
A/B Wipe and Split Media Comparison HUD Controller.
Provides interactive controls for wipe position, orientation (Horizontal / Vertical),
free angle rotation, split, and difference boost with full reset functionality.
"""
from __future__ import annotations

import math
from typing import Optional
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QDoubleSpinBox,
)
from openreview.models.comparison import ComparisonState, ComparisonMode


class WipeComparisonHUD(QWidget):
    """Interactive A/B wipe and difference comparison controller panel."""

    comparison_changed = Signal(ComparisonState)

    def __init__(self, state: Optional[ComparisonState] = None, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.state = state or ComparisonState()
        self._setup_ui()
        self.setFixedWidth(270)

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)

        # 1. Mode Selection Buttons
        mode_layout = QHBoxLayout()
        mode_layout.setSpacing(2)

        self.btn_off = QPushButton("Off", self)
        self.btn_off.setCheckable(True)
        self.btn_off.setChecked(self.state.mode == ComparisonMode.OFF)
        self.btn_off.setToolTip("Disable media comparison (single view)")
        self.btn_off.clicked.connect(lambda: self._set_mode(ComparisonMode.OFF))
        mode_layout.addWidget(self.btn_off)

        self.btn_wipe = QPushButton("Wipe", self)
        self.btn_wipe.setCheckable(True)
        self.btn_wipe.setChecked(self.state.mode == ComparisonMode.WIPE)
        self.btn_wipe.setToolTip("Enable interactive A/B wipe comparison (W)")
        self.btn_wipe.clicked.connect(lambda: self._set_mode(ComparisonMode.WIPE))
        mode_layout.addWidget(self.btn_wipe)

        self.btn_split = QPushButton("Split", self)
        self.btn_split.setCheckable(True)
        self.btn_split.setChecked(self.state.mode == ComparisonMode.SPLIT)
        self.btn_split.setToolTip("Side-by-side split comparison")
        self.btn_split.clicked.connect(lambda: self._set_mode(ComparisonMode.SPLIT))
        mode_layout.addWidget(self.btn_split)

        self.btn_diff = QPushButton("Diff", self)
        self.btn_diff.setCheckable(True)
        self.btn_diff.setChecked(self.state.mode == ComparisonMode.DIFFERENCE)
        self.btn_diff.setToolTip("Absolute pixel difference comparison")
        self.btn_diff.clicked.connect(lambda: self._set_mode(ComparisonMode.DIFFERENCE))
        mode_layout.addWidget(self.btn_diff)

        self.btn_spot = QPushButton("Spot", self)
        self.btn_spot.setCheckable(True)
        self.btn_spot.setChecked(self.state.mode == ComparisonMode.SPOTLIGHT)
        self.btn_spot.setToolTip("Spotlight / Circle Loupe inspection wipe")
        self.btn_spot.clicked.connect(lambda: self._set_mode(ComparisonMode.SPOTLIGHT))
        mode_layout.addWidget(self.btn_spot)

        layout.addLayout(mode_layout)

        # Options Row: Divider Line & Turbo Heatmap
        options_layout = QHBoxLayout()
        options_layout.setSpacing(4)
        self.chk_line = QPushButton("Divider Line", self)
        self.chk_line.setCheckable(True)
        self.chk_line.setChecked(getattr(self.state, "wipe_show_line", True))
        self.chk_line.setToolTip("Toggle anti-aliased divider overlay line")
        self.chk_line.setStyleSheet("font-size: 10px; padding: 2px 4px;")
        self.chk_line.clicked.connect(self._on_toggle_line)
        options_layout.addWidget(self.chk_line)

        self.chk_heatmap = QPushButton("Turbo Heatmap", self)
        self.chk_heatmap.setCheckable(True)
        self.chk_heatmap.setChecked(getattr(self.state, "diff_heatmap", False))
        self.chk_heatmap.setToolTip("Toggle perceptual false-color Turbo difference heatmap")
        self.chk_heatmap.setStyleSheet("font-size: 10px; padding: 2px 4px;")
        self.chk_heatmap.clicked.connect(self._on_toggle_heatmap)
        options_layout.addWidget(self.chk_heatmap)
        layout.addLayout(options_layout)

        # 2. Orientation Switcher (Vertical vs Horizontal)
        orient_layout = QHBoxLayout()
        orient_layout.setSpacing(4)
        orient_label = QLabel("Orientation:", self)
        orient_label.setStyleSheet("color: #aaa; font-size: 11px;")
        orient_layout.addWidget(orient_label)

        self.btn_vert = QPushButton("Vertical", self)
        self.btn_vert.setCheckable(True)
        self.btn_vert.setToolTip("Vertical split divider (0° - Left vs Right, Shift+W)")
        self.btn_vert.clicked.connect(lambda: self.set_orientation_degrees(0.0))
        orient_layout.addWidget(self.btn_vert)

        self.btn_horiz = QPushButton("Horizontal", self)
        self.btn_horiz.setCheckable(True)
        self.btn_horiz.setToolTip("Horizontal split divider (90° - Top vs Bottom, Shift+W)")
        self.btn_horiz.clicked.connect(lambda: self.set_orientation_degrees(90.0))
        orient_layout.addWidget(self.btn_horiz)

        layout.addLayout(orient_layout)

        # 3. Wipe Position Row
        pos_header = QHBoxLayout()
        pos_header.addWidget(QLabel("Wipe Position:", self))
        self.pos_val_label = QLabel(f"{int(round(self.state.wipe_position * 100))}%", self)
        self.pos_val_label.setStyleSheet("color: #e5a823; font-weight: bold;")
        pos_header.addWidget(self.pos_val_label)
        layout.addLayout(pos_header)

        self.pos_slider = QSlider(Qt.Orientation.Horizontal, self)
        self.pos_slider.setRange(0, 100)
        self.pos_slider.setValue(int(round(self.state.wipe_position * 100)))
        self.pos_slider.valueChanged.connect(self._on_pos_changed)
        layout.addWidget(self.pos_slider)

        # 4. Wipe Angle / Rotation Row
        angle_header = QHBoxLayout()
        angle_header.addWidget(QLabel("Wipe Angle:", self))
        self.angle_val_label = QLabel(f"{int(round(self.state.wipe_angle_deg))}°", self)
        self.angle_val_label.setStyleSheet("color: #e5a823; font-weight: bold;")
        angle_header.addWidget(self.angle_val_label)
        layout.addLayout(angle_header)

        self.angle_slider = QSlider(Qt.Orientation.Horizontal, self)
        self.angle_slider.setRange(0, 360)
        self.angle_slider.setValue(int(round(self.state.wipe_angle_deg)) % 360)
        self.angle_slider.valueChanged.connect(self._on_angle_changed)
        layout.addWidget(self.angle_slider)

        # Angle Presets
        presets_layout = QHBoxLayout()
        presets_layout.setSpacing(2)
        for deg in (0, 90, 180, 270):
            btn_deg = QPushButton(f"{deg}°", self)
            btn_deg.setFixedHeight(20)
            btn_deg.setStyleSheet("font-size: 10px; padding: 1px 4px;")
            btn_deg.clicked.connect(lambda checked=False, d=deg: self.set_orientation_degrees(float(d)))
            presets_layout.addWidget(btn_deg)
        layout.addLayout(presets_layout)

        # 5. Difference Gain Row
        diff_header = QHBoxLayout()
        diff_header.addWidget(QLabel("Diff Gain:", self))
        self.diff_spin = QDoubleSpinBox(self)
        self.diff_spin.setRange(1.0, 10.0)
        self.diff_spin.setSingleStep(0.5)
        self.diff_spin.setValue(self.state.diff_boost)
        self.diff_spin.valueChanged.connect(self._on_diff_changed)
        diff_header.addWidget(self.diff_spin)
        layout.addLayout(diff_header)

        # 6. Reset Control Row
        action_layout = QHBoxLayout()
        self.btn_reset = QPushButton("↺ Reset Wipe", self)
        self.btn_reset.setToolTip("Reset position to 50% center and angle to 0° (Alt+W)")
        self.btn_reset.setStyleSheet("""
            QPushButton {
                background-color: #2b2b2b;
                color: #e5a823;
                border: 1px solid #444;
                border-radius: 3px;
                padding: 4px;
                font-weight: 500;
            }
            QPushButton:hover {
                background-color: #383838;
                border-color: #e5a823;
            }
            QPushButton:pressed {
                background-color: #1f1f1f;
            }
        """)
        self.btn_reset.clicked.connect(self.reset_wipe)
        action_layout.addWidget(self.btn_reset)
        layout.addLayout(action_layout)

        self._update_orientation_buttons()

    def _set_mode(self, mode: ComparisonMode) -> None:
        self.state.mode = mode
        self.btn_off.setChecked(mode == ComparisonMode.OFF)
        self.btn_wipe.setChecked(mode == ComparisonMode.WIPE)
        self.btn_split.setChecked(mode == ComparisonMode.SPLIT)
        self.btn_diff.setChecked(mode == ComparisonMode.DIFFERENCE)
        if hasattr(self, "btn_spot"):
            self.btn_spot.setChecked(mode == ComparisonMode.SPOTLIGHT)
        self.comparison_changed.emit(self.state)

    def _on_toggle_line(self) -> None:
        self.state.toggle_wipe_line()
        self.chk_line.setChecked(self.state.wipe_show_line)
        self.comparison_changed.emit(self.state)

    def _on_toggle_heatmap(self) -> None:
        self.state.toggle_diff_heatmap()
        self.chk_heatmap.setChecked(self.state.diff_heatmap)
        self.comparison_changed.emit(self.state)

    def toggle_wipe(self) -> None:
        """Invoked when shortcut 'W' is pressed."""
        if self.state.mode == ComparisonMode.WIPE:
            self._set_mode(ComparisonMode.OFF)
        else:
            self._set_mode(ComparisonMode.WIPE)

    def set_orientation_degrees(self, degrees: float) -> None:
        """Set wipe angle to specified degrees and synchronize UI."""
        self.state.wipe_angle_deg = float(degrees % 360.0)
        self.angle_slider.blockSignals(True)
        self.angle_slider.setValue(int(round(self.state.wipe_angle_deg)))
        self.angle_slider.blockSignals(False)
        self.angle_val_label.setText(f"{int(round(self.state.wipe_angle_deg))}°")
        if hasattr(self, "chk_line"):
            self.chk_line.setChecked(self.state.wipe_show_line)
        if hasattr(self, "chk_heatmap"):
            self.chk_heatmap.setChecked(self.state.diff_heatmap)
        self._update_orientation_buttons()
        self.comparison_changed.emit(self.state)

    def flip_orientation(self) -> None:
        """Flip between Vertical (0°) and Horizontal (90°) orientation (Shift+W)."""
        self.state.flip_orientation()
        self.set_orientation_degrees(self.state.wipe_angle_deg)

    def reset_wipe(self) -> None:
        """Reset wipe position to 50%, angle to 0° (vertical split), and diff gain to 1.0 (Alt+W)."""
        self.state.reset_wipe()
        # Update sliders & readouts
        self.pos_slider.blockSignals(True)
        self.pos_slider.setValue(int(round(self.state.wipe_position * 100)))
        self.pos_slider.blockSignals(False)
        self.pos_val_label.setText(f"{int(round(self.state.wipe_position * 100))}%")

        self.angle_slider.blockSignals(True)
        self.angle_slider.setValue(int(round(self.state.wipe_angle_deg)))
        self.angle_slider.blockSignals(False)
        self.angle_val_label.setText(f"{int(round(self.state.wipe_angle_deg))}°")

        self.diff_spin.blockSignals(True)
        self.diff_spin.setValue(self.state.diff_boost)
        self.diff_spin.blockSignals(False)

        self._update_orientation_buttons()
        self.comparison_changed.emit(self.state)

    def _update_orientation_buttons(self) -> None:
        """Reflect current angle on the orientation buttons."""
        norm_angle = round(self.state.wipe_angle_deg) % 180
        is_vert = (norm_angle in (0, 180))
        is_horiz = (norm_angle == 90)

        self.btn_vert.blockSignals(True)
        self.btn_vert.setChecked(is_vert)
        self.btn_vert.blockSignals(False)

        self.btn_horiz.blockSignals(True)
        self.btn_horiz.setChecked(is_horiz)
        self.btn_horiz.blockSignals(False)

    def _on_pos_changed(self, value: int) -> None:
        pos = value / 100.0
        self.state.wipe_position = pos
        angle_rad = math.radians(float(self.state.wipe_angle_deg))
        self.state.wipe_center = (
            max(0.0, min(1.0, 0.5 + (pos - 0.5) * math.cos(angle_rad))),
            max(0.0, min(1.0, 0.5 + (pos - 0.5) * math.sin(angle_rad))),
        )
        self.pos_val_label.setText(f"{value}%")
        self.comparison_changed.emit(self.state)

    def update_wipe_position(self, pos: float) -> None:
        """Update slider and label from external or in-canvas interactive wipe."""
        self.state.wipe_position = float(pos)
        val = int(round(pos * 100))
        self.pos_slider.blockSignals(True)
        self.pos_slider.setValue(val)
        self.pos_slider.blockSignals(False)
        self.pos_val_label.setText(f"{val}%")

    def _on_angle_changed(self, value: int) -> None:
        self.state.wipe_angle_deg = float(value)
        self.angle_val_label.setText(f"{value}°")
        self._update_orientation_buttons()
        self.comparison_changed.emit(self.state)

    def update_wipe_angle(self, angle: float) -> None:
        """Update angle slider and label from in-canvas interactive rotation."""
        self.state.wipe_angle_deg = float(angle)
        val = int(round(angle)) % 360
        self.angle_slider.blockSignals(True)
        self.angle_slider.setValue(val)
        self.angle_slider.blockSignals(False)
        self.angle_val_label.setText(f"{val}°")
        self._update_orientation_buttons()

    def _on_diff_changed(self, value: float) -> None:
        self.state.diff_boost = float(value)
        self.comparison_changed.emit(self.state)
