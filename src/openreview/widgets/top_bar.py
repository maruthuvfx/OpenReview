"""
Top Quick Bar Widget for Color (Foundry Nuke-style Gain, Gamma, Saturation), Channels, Audio, and OCIO.
"""
from __future__ import annotations

from typing import Optional, Tuple
import math
from PySide6.QtCore import Qt, Signal, QRectF, QPoint, QSize
from PySide6.QtGui import QPainter, QColor, QPen, QFont
from PySide6.QtWidgets import (
    QWidget,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QDoubleSpinBox,
    QAbstractSpinBox,
    QComboBox,
    QButtonGroup,
    QFrame,
    QSizePolicy,
    QMenu,
)
from openreview.color.ocio_manager import OCIOManager
from openreview.color.standard_colorspaces import STANDARD_COLORSPACES


def gain_to_fstop_label(gain: float) -> str:
    """Format gain multiplier as relative whole f-stop centered at f/8 (Foundry Nuke style)."""
    if gain <= 1e-6:
        return "f/0"
    ev = math.log2(gain)
    stops = {
        -6: "f/1.0",
        -5: "f/1.4",
        -4: "f/2",
        -3: "f/2.8",
        -2: "f/4",
        -1: "f/5.6",
        0: "f/8",
        1: "f/11",
        2: "f/16",
        3: "f/22",
        4: "f/32",
        5: "f/45",
        6: "f/64",
    }
    nearest = int(round(ev))
    return stops.get(nearest, f"f/{8.0 * (math.sqrt(2) ** nearest):.1f}")


def gain_to_fstop_tooltip(gain: float) -> str:
    """Format detailed tooltip with aperture, EV offset, and gain multiplier."""
    if gain <= 1e-6:
        return "Aperture: f/0 | Gain: 0.000 | Click to reset to f/8"
    ev = math.log2(gain)
    lbl = gain_to_fstop_label(gain)
    return f"Aperture: {lbl} ({ev:+.2f} EV, Gain {gain:.3f}) | Click to reset to f/8"


class NukeSlider(QSlider):
    """
    Foundry Nuke-style viewer slider with custom painted groove, tick marks,
    labels, and vertical golden amber indicator thumb.
    Supports smooth interactive dragging, direct click-jumping, and Ctrl+Click reset.
    """

    def __init__(
        self,
        ticks: Optional[list[Tuple[int, str]]] = None,
        default_val: int = 0,
        min_v: int = -600,
        max_v: int = 600,
        orientation=Qt.Orientation.Horizontal,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(orientation, parent)
        self.setObjectName("nukeSlider")
        self.setRange(min_v, max_v)
        self.setValue(default_val)
        self._default_value: int = default_val
        self._ticks = ticks or []
        self.setFixedHeight(34)
        self.setMinimumWidth(75)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setTracking(True)
        self.setMouseTracking(True)

    def sizeHint(self) -> QSize:
        return QSize(160, 34)

    def set_default_value(self, val: int) -> None:
        self._default_value = val

    def default_value(self) -> int:
        return self._default_value

    def _val_from_x(self, x: float) -> int:
        margin = 8
        track_w = max(1, self.width() - margin * 2)
        ratio = max(0.0, min(1.0, (x - margin) / float(track_w)))
        return int(round(self.minimum() + ratio * (self.maximum() - self.minimum())))

    def mousePressEvent(self, event) -> None:
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.setValue(self._default_value)
            self.sliderMoved.emit(self._default_value)
            self.valueChanged.emit(self._default_value)
            event.accept()
            return
        if event.button() == Qt.MouseButton.LeftButton:
            new_val = self._val_from_x(event.position().x())
            self.setValue(new_val)
            self.sliderMoved.emit(new_val)
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if event.buttons() & Qt.MouseButton.LeftButton:
            new_val = self._val_from_x(event.position().x())
            self.setValue(new_val)
            self.sliderMoved.emit(new_val)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        margin = 8
        w = self.width() - margin * 2
        y_center = 9

        # Track line
        painter.setPen(QPen(QColor(55, 55, 55), 1))
        painter.drawLine(margin, y_center, margin + w, y_center)

        # Ticks & labels
        font = QFont("Segoe UI", 7)
        painter.setFont(font)
        rng = float(max(1, self.maximum() - self.minimum()))
        num_ticks = len(self._ticks)
        for i, (t_val, t_lbl) in enumerate(self._ticks):
            ratio = (t_val - self.minimum()) / rng
            tx = margin + ratio * w
            painter.setPen(QPen(QColor(95, 95, 95), 1))
            # Downward tick mark from track line
            painter.drawLine(int(round(tx)), y_center, int(round(tx)), y_center + 4)
            painter.setPen(QColor(200, 200, 200))
            if i == 0:
                painter.drawText(QRectF(margin - 2, y_center + 5, 36, 15), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, t_lbl)
            elif i == num_ticks - 1:
                painter.drawText(QRectF(margin + w - 34, y_center + 5, 36, 15), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, t_lbl)
            else:
                painter.drawText(QRectF(tx - 18, y_center + 5, 36, 15), Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter, t_lbl)

        # Thumb (Foundry Nuke golden amber vertical marker)
        curr_ratio = (self.value() - self.minimum()) / rng
        cx = margin + curr_ratio * w
        painter.setPen(QPen(QColor(255, 255, 255, 220), 1))
        painter.setBrush(QColor(229, 168, 35))  # Nuke amber
        painter.drawRect(QRectF(cx - 2.5, y_center - 5, 5, 11))


# Alias for backwards compatibility
ResetClickSlider = NukeSlider


class TopControlBar(QFrame):
    """
    Top toolbar providing rapid access to:
    - Foundry Nuke-style Gain slider & numeric input (default 1.00, 0.0 - 4.0)
    - Foundry Nuke-style Gamma slider & numeric input (default 1.00, 0.1 - 4.0)
    - Foundry Nuke-style Saturation slider & numeric input (default 1.00, 0.0 - 4.0)
    - Channel isolation (RGB, R, G, B, A, Luma Y)
    - Audio settings & volume HUD toggle
    - OCIO Display & View transforms
    - Viewport Zoom Fit & 1:1 Actual Size
    """

    gain_changed = Signal(float)
    gamma_changed = Signal(float)
    saturation_changed = Signal(float)
    exposure_changed = Signal(float)  # Retained for backwards compatibility
    audio_clicked = Signal()
    channel_changed = Signal(int)  # 0: RGB, 1: R, 2: G, 3: B, 4: A, 5: Y
    layer_changed = Signal(str)     # Selected EXR / multi-channel layer name (e.g. "beauty", "diffuse")
    stereo_changed = Signal(int)    # 0: Off, 1: Left, 2: Right, 3: Anaglyph, 4: SBS, 5: Diff
    color_transforms_changed = Signal(int, int)  # input_space_idx, view_transform_idx
    ocio_changed = Signal(str, str)  # display, view
    zoom_fit_requested = Signal()
    zoom_100_requested = Signal()
    scrub_mode_toggled = Signal(bool)

    def __init__(self, ocio_manager: OCIOManager, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.ocio = ocio_manager
        self._color_management_mode: str = "standard"
        self._current_channel_mode: int = 0
        self._gain: float = 1.0
        self._saturation: float = 1.0
        self.setFixedHeight(38)
        self._setup_ui()

    def _setup_ui(self) -> None:
        self.setStyleSheet("""
            QFrame {
                background-color: #202020;
                border-bottom: 1px solid #333333;
            }
            QSlider#nukeSlider {
                min-height: 34px;
                max-height: 34px;
                height: 34px;
                border: none;
                background: transparent;
            }
            QSlider#nukeSlider::groove:horizontal {
                height: 0px;
                background: transparent;
                border: none;
            }
            QSlider#nukeSlider::handle:horizontal {
                width: 0px;
                height: 0px;
                background: transparent;
                border: none;
            }
            QPushButton#chanBtn {
                min-width: 22px;
                max-width: 22px;
                min-height: 20px;
                max-height: 20px;
                padding: 0px;
                font-weight: bold;
                font-size: 10px;
            }
            QPushButton#chanBtn:checked {
                background-color: #e5a823;
                color: #111111;
            }
            QPushButton#resetBtn {
                min-width: 18px;
                max-width: 18px;
                min-height: 18px;
                max-height: 18px;
                padding: 0px;
                font-size: 11px;
                font-weight: bold;
                background-color: #2a2a2a;
                color: #aaaaaa;
                border: 1px solid #3c3c3c;
                border-radius: 2px;
            }
            QPushButton#resetBtn:hover {
                background-color: #e5a823;
                color: #111111;
                border-color: #f0b429;
            }
            QPushButton#resetBtn:pressed {
                background-color: #c98e16;
                color: #ffffff;
            }
            QPushButton#audioBtn {
                min-width: 26px;
                max-width: 26px;
                min-height: 20px;
                max-height: 20px;
                font-size: 12px;
                background-color: #2a2a2a;
                border: 1px solid #3c3c3c;
                border-radius: 3px;
            }
            QPushButton#audioBtn:hover {
                background-color: #383838;
                border-color: #e5a823;
            }
            QPushButton#scrubBtn {
                min-width: 24px;
                max-width: 24px;
                min-height: 22px;
                max-height: 22px;
                font-size: 13px;
                background-color: #2a2a2a;
                border: 1px solid #3c3c3c;
                border-radius: 2px;
                color: #cccccc;
            }
            QPushButton#scrubBtn:hover {
                background-color: #383838;
                border-color: #e5a823;
            }
            QPushButton#scrubBtn:checked {
                background-color: #e5a823;
                color: #111111;
                border: 1px solid #f0b429;
                font-weight: bold;
            }
            QPushButton#scrubBtn:checked:hover {
                background-color: #f0b429;
                color: #000000;
            }
            QPushButton#fstopBtn {
                min-width: 18px;
                max-width: 18px;
                min-height: 22px;
                max-height: 22px;
                padding: 0px;
                font-size: 10px;
                background-color: #262626;
                color: #cccccc;
                border: 1px solid #383838;
                border-radius: 2px;
            }
            QPushButton#fstopBtn:hover {
                background-color: #353535;
                border-color: #e5a823;
                color: #ffffff;
            }
            QPushButton#fstopLabelBtn {
                min-width: 54px;
                max-width: 54px;
                width: 54px;
                min-height: 22px;
                max-height: 22px;
                height: 22px;
                padding: 1px 2px;
                font-size: 11px;
                font-weight: bold;
                font-family: "Consolas", "Courier New", monospace;
                background-color: #191919;
                color: #e5a823;
                border: 1px solid #383838;
                border-radius: 2px;
            }
            QPushButton#fstopLabelBtn:hover {
                background-color: #282828;
                border-color: #e5a823;
            }
        """ + __import__("openreview.ui.theme", fromlist=["get_spinbox_arrow_stylesheet"]).get_spinbox_arrow_stylesheet())

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 3, 8, 3)
        layout.setSpacing(4)

        # 1. Gain (Foundry Nuke style with f-stop stepper and log slider)
        self.btn_fstop_prev = QPushButton("◀", self)
        self.btn_fstop_prev.setObjectName("fstopBtn")
        self.btn_fstop_prev.setFixedSize(18, 22)
        self.btn_fstop_prev.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.btn_fstop_prev.setToolTip("Step Down 1 F-Stop (Half Exposure / -1 EV)")
        self.btn_fstop_prev.clicked.connect(self.step_fstop_down)
        layout.addWidget(self.btn_fstop_prev)

        self.btn_fstop_label = QPushButton("f/8", self)
        self.btn_fstop_label.setObjectName("fstopLabelBtn")
        self.btn_fstop_label.setFixedSize(54, 22)
        self.btn_fstop_label.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.btn_fstop_label.setToolTip(gain_to_fstop_tooltip(1.0))
        self.btn_fstop_label.clicked.connect(self.reset_gain)
        layout.addWidget(self.btn_fstop_label)

        self.btn_fstop_next = QPushButton("▶", self)
        self.btn_fstop_next.setObjectName("fstopBtn")
        self.btn_fstop_next.setFixedSize(18, 22)
        self.btn_fstop_next.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.btn_fstop_next.setToolTip("Step Up 1 F-Stop (Double Exposure / +1 EV)")
        self.btn_fstop_next.clicked.connect(self.step_fstop_up)
        layout.addWidget(self.btn_fstop_next)

        self.gain_spin = QDoubleSpinBox(self)
        self.gain_spin.setRange(0.015, 64.0)
        self.gain_spin.setSingleStep(0.025)
        self.gain_spin.setDecimals(3)
        self.gain_spin.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        self.gain_spin.setValue(1.0)
        self.gain_spin.setFixedSize(54, 22)
        self.gain_spin.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.gain_spin.setKeyboardTracking(True)
        self.gain_spin.valueChanged.connect(self._on_gain_spin_changed)
        layout.addWidget(self.gain_spin)

        self.btn_gain_reset = QPushButton("↺", self)
        self.btn_gain_reset.hide()
        self.btn_gain_reset.clicked.connect(self.reset_gain)

        self.gain_slider = NukeSlider(
            ticks=[(-600, "0.015"), (-332, "0.1"), (0, "1"), (332, "10"), (600, "64")],
            default_val=0,
            min_v=-600,
            max_v=600,
            parent=self,
        )
        self.gain_slider.setToolTip("Gain (Logarithmic 0.015625 to 64.0) | Ctrl+Click to reset to 1.0")
        self.gain_slider.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.gain_slider.setMinimumWidth(75)
        self.gain_slider.valueChanged.connect(self._on_gain_slider_changed)
        self.gain_slider.sliderMoved.connect(self._on_gain_slider_changed)
        layout.addWidget(self.gain_slider, 1)

        layout.addSpacing(4)

        # 2. Gamma (Foundry Nuke style Y)
        gam_lbl = QLabel("Y", self)
        gam_lbl.setStyleSheet("color: #e5a823; font-weight: bold; font-size: 12px;")
        gam_lbl.setToolTip("Gamma (Y)")
        gam_lbl.setFixedWidth(12)
        gam_lbl.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        layout.addWidget(gam_lbl)

        self.gam_spin = QDoubleSpinBox(self)
        self.gam_spin.setRange(0.0, 4.0)
        self.gam_spin.setSingleStep(0.025)
        self.gam_spin.setDecimals(3)
        self.gam_spin.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        self.gam_spin.setValue(self.ocio.gamma)
        self.gam_spin.setFixedSize(54, 22)
        self.gam_spin.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.gam_spin.setKeyboardTracking(True)
        self.gam_spin.valueChanged.connect(self._on_gam_spin_changed)
        layout.addWidget(self.gam_spin)

        self.btn_gam_reset = QPushButton("↺", self)
        self.btn_gam_reset.hide()
        self.btn_gam_reset.clicked.connect(self.reset_gamma)

        self.gam_slider = NukeSlider(
            ticks=[(0, "0"), (180, "0.1"), (500, "1"), (667, "2"), (833, "3"), (1000, "4")],
            default_val=500,
            min_v=0,
            max_v=1000,
            parent=self,
        )
        self.gam_slider.setToolTip("Gamma (0.0 to 4.0, Center = 1.0) | Ctrl+Click to reset to 1.0")
        self.gam_slider.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.gam_slider.setMinimumWidth(75)
        self.gam_slider.valueChanged.connect(self._on_gam_slider_changed)
        self.gam_slider.sliderMoved.connect(self._on_gam_slider_changed)
        layout.addWidget(self.gam_slider, 1)

        layout.addSpacing(4)

        # 3. Saturation (Foundry Nuke style S)
        sat_lbl = QLabel("S", self)
        sat_lbl.setStyleSheet("color: #e5a823; font-weight: bold; font-size: 12px;")
        sat_lbl.setToolTip("Saturation (S)")
        sat_lbl.setFixedWidth(12)
        sat_lbl.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        layout.addWidget(sat_lbl)

        self.sat_spin = QDoubleSpinBox(self)
        self.sat_spin.setRange(0.0, 4.0)
        self.sat_spin.setSingleStep(0.025)
        self.sat_spin.setDecimals(3)
        self.sat_spin.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        self.sat_spin.setValue(1.0)
        self.sat_spin.setFixedSize(54, 22)
        self.sat_spin.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.sat_spin.setKeyboardTracking(True)
        self.sat_spin.valueChanged.connect(self._on_sat_spin_changed)
        layout.addWidget(self.sat_spin)

        self.btn_sat_reset = QPushButton("↺", self)
        self.btn_sat_reset.hide()
        self.btn_sat_reset.clicked.connect(self.reset_saturation)

        self.sat_slider = NukeSlider(
            ticks=[(0, "0"), (180, "0.1"), (500, "1"), (667, "2"), (833, "3"), (1000, "4")],
            default_val=500,
            min_v=0,
            max_v=1000,
            parent=self,
        )
        self.sat_slider.setToolTip("Saturation (0.0 to 4.0, Center = 1.0) | Ctrl+Click to reset to 1.0")
        self.sat_slider.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.sat_slider.setMinimumWidth(75)
        self.sat_slider.valueChanged.connect(self._on_sat_slider_changed)
        self.sat_slider.sliderMoved.connect(self._on_sat_slider_changed)
        layout.addWidget(self.sat_slider, 1)

        layout.addSpacing(4)

        # 4. Compact Channel Switch Icon Button with Popup Menu
        from openreview.resources.svg_icons import get_svg_icon
        self.btn_channel = QPushButton(self)
        self.btn_channel.setObjectName("channelPopupBtn")
        self.btn_channel.setIcon(get_svg_icon("channel", color_normal="#e5a823", color_active="#f0b429", size=15))
        self.btn_channel.setText("RGB")
        self.btn_channel.setToolTip("Channel Isolation (Click to switch channels | Hotkeys: C, R, G, B, A, L)")
        self.btn_channel.setStyleSheet("""
            QPushButton#channelPopupBtn {
                min-width: 48px;
                max-width: 58px;
                min-height: 22px;
                max-height: 22px;
                padding: 1px 4px;
                font-weight: bold;
                font-size: 10px;
                color: #e5a823;
                background-color: #262626;
                border: 1px solid #3c3c3c;
                border-radius: 2px;
            }
            QPushButton#channelPopupBtn:hover {
                background-color: #353535;
                border-color: #e5a823;
            }
        """)
        self.btn_channel.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_channel.clicked.connect(self._show_channel_menu)
        layout.addWidget(self.btn_channel)

        # Retain internal combo objects for backwards compatibility & external HUD synchronization
        self.chan_combo = QComboBox(self)
        self.chan_combo.hide()
        self.chan_combo.addItem("RGB Composite (C)", 0)
        self.chan_combo.addItem("Red (R)", 1)
        self.chan_combo.addItem("Green (G)", 2)
        self.chan_combo.addItem("Blue (B)", 3)
        self.chan_combo.addItem("Alpha (A)", 4)
        self.chan_combo.addItem("Luminance (Y)", 5)
        self.chan_combo.currentIndexChanged.connect(self._on_channel_combo_changed)

        self.layer_combo = QComboBox(self)
        self.layer_combo.hide()
        self.layer_combo.addItem("beauty", "beauty")
        self.layer_combo.currentIndexChanged.connect(self._on_layer_combo_changed)

        self.stereo_combo = QComboBox(self)
        self.stereo_combo.hide()
        self.stereo_combo.addItem("2D (Off)", 0)
        self.stereo_combo.addItem("Left Eye", 1)
        self.stereo_combo.addItem("Right Eye", 2)
        self.stereo_combo.addItem("Anaglyph", 3)
        self.stereo_combo.addItem("Side-by-Side", 4)
        self.stereo_combo.addItem("Difference", 5)
        self.stereo_combo.currentIndexChanged.connect(self._on_stereo_combo_changed)

        # Keep dummy button references for backwards compatibility
        self._chan_buttons = {}
        for m in range(6):
            b = QPushButton(self)
            b.hide()
            self._chan_buttons[m] = b

        # Keep dummy audio button reference for backwards compatibility
        from openreview.resources import get_vfx_icon
        self.btn_audio = QPushButton(self)
        self.btn_audio.setIcon(get_vfx_icon("volume"))
        self.btn_audio.hide()

        layout.addSpacing(6)

        # 5. Colorspace (Input Space & View Display Transform)
        cs_lbl = QLabel("Color:", self)
        cs_lbl.setStyleSheet("color: #888888; font-weight: bold;")
        cs_lbl.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        layout.addWidget(cs_lbl)

        self.input_combo = QComboBox(self)
        self.input_combo.setToolTip("Source Input Colorspace")
        self.input_combo.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self.input_combo.setMinimumWidth(125)
        self.input_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.input_combo.setMinimumContentsLength(12)
        self.input_combo.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.input_combo.currentIndexChanged.connect(self._on_color_transform_changed)
        layout.addWidget(self.input_combo)

        view_lbl = QLabel("View:", self)
        view_lbl.setStyleSheet("color: #888888;")
        view_lbl.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        layout.addWidget(view_lbl)

        self.view_combo = QComboBox(self)
        self.view_combo.setToolTip("Display View Transform")
        self.view_combo.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self.view_combo.setMinimumWidth(85)
        self.view_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.view_combo.setMinimumContentsLength(7)
        self.view_combo.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.view_combo.currentIndexChanged.connect(self._on_color_transform_changed)
        layout.addWidget(self.view_combo)

        self.setup_standard_spaces(default_view="sRGB", default_input="linear")

        layout.addSpacing(6)

        # Scrub Mode toggle button (placed before Fit button)
        self.btn_scrub = QPushButton("⇄", self)
        self.btn_scrub.setObjectName("scrubBtn")
        self.btn_scrub.setToolTip("Toggle Canvas Scrub Mode (Drag left/right on canvas to scrub frames)")
        self.btn_scrub.setCheckable(True)
        self.btn_scrub.setChecked(True)
        self.btn_scrub.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_scrub.toggled.connect(self.scrub_mode_toggled.emit)
        layout.addWidget(self.btn_scrub)

        # 6. Zoom Fit & 1:1
        self.fit_btn = QPushButton("Fit", self)
        self.fit_btn.setToolTip("Fit Image to Viewport (F)")
        self.fit_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.fit_btn.clicked.connect(self.zoom_fit_requested.emit)
        layout.addWidget(self.fit_btn)

        self.zoom_100_btn = QPushButton("1:1", self)
        self.zoom_100_btn.setToolTip("Actual 100% Size (1)")
        self.zoom_100_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.zoom_100_btn.clicked.connect(self.zoom_100_requested.emit)
        layout.addWidget(self.zoom_100_btn)

        # Clear focus policies for non-text inputs
        self.gain_slider.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.gam_slider.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.sat_slider.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_gain_reset.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_gam_reset.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_sat_reset.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        for b in self._chan_buttons.values():
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        self.gain_spin.editingFinished.connect(self.gain_spin.clearFocus)
        self.gam_spin.editingFinished.connect(self.gam_spin.clearFocus)
        self.sat_spin.editingFinished.connect(self.sat_spin.clearFocus)

    # --------------------------------------------------------------------------
    # Backward Compatibility Aliases
    # --------------------------------------------------------------------------
    @property
    def exp_spin(self) -> QDoubleSpinBox:
        return self.gain_spin

    @property
    def exp_slider(self) -> QSlider:
        return self.gain_slider

    @property
    def btn_exp_reset(self) -> QPushButton:
        return self.btn_gain_reset

    @property
    def exp_reset_btn(self) -> QPushButton:
        return self.btn_gain_reset

    @property
    def gam_reset_btn(self) -> QPushButton:
        return self.btn_gam_reset

    @property
    def exposure_ev(self) -> float:
        return self.ocio.exposure_ev

    @property
    def gain(self) -> float:
        return self._gain

    @property
    def gamma(self) -> float:
        return self.ocio.gamma

    @property
    def saturation(self) -> float:
        return self._saturation

    @property
    def channel_mode(self) -> int:
        return self._current_channel_mode

    @property
    def spin_gamma(self):
        return self.gam_spin

    @property
    def slider_gamma(self):
        return self.gam_slider

    @property
    def spin_exposure(self):
        return self.gain_spin

    @property
    def slider_exposure(self):
        return self.gain_slider

    # --------------------------------------------------------------------------
    # Gain & F-Stop Handlers (Foundry Nuke style)
    # --------------------------------------------------------------------------
    def step_fstop_down(self) -> None:
        """Step down 1 f-stop (half exposure / -1 EV)."""
        current_gain = max(0.015625, self._gain)
        ev = math.log2(current_gain)
        new_ev = math.floor(ev - 0.01)
        new_gain = max(0.015625, min(64.0, 2.0 ** new_ev))
        self.set_gain(new_gain)

    def step_fstop_up(self) -> None:
        """Step up 1 f-stop (double exposure / +1 EV)."""
        current_gain = max(0.015625, self._gain)
        ev = math.log2(current_gain)
        new_ev = math.ceil(ev + 0.01)
        new_gain = max(0.015625, min(64.0, 2.0 ** new_ev))
        self.set_gain(new_gain)

    def _on_gain_spin_changed(self, val: float) -> None:
        val = max(0.015625, min(64.0, val))
        self._gain = val
        self.btn_fstop_label.setText(gain_to_fstop_label(val))
        self.btn_fstop_label.setToolTip(gain_to_fstop_tooltip(val))
        ev = math.log2(val)
        slider_val = max(-600, min(600, int(round(ev * 100.0))))
        self.gain_slider.blockSignals(True)
        self.gain_slider.setValue(slider_val)
        self.gain_slider.blockSignals(False)
        self.gain_changed.emit(val)

    def _on_gain_slider_changed(self, val: int) -> None:
        g = round(2.0 ** (val / 100.0), 3)
        self._gain = g
        self.btn_fstop_label.setText(gain_to_fstop_label(g))
        self.btn_fstop_label.setToolTip(gain_to_fstop_tooltip(g))
        self.gain_spin.blockSignals(True)
        self.gain_spin.setValue(g)
        self.gain_spin.blockSignals(False)
        self.gain_changed.emit(g)

    def set_gain(self, g: float) -> None:
        """Set gain multiplier (Foundry Nuke style, 0.015625 to 64.0)."""
        g = max(0.015625, min(64.0, round(float(g), 3)))
        self._gain = g
        self.btn_fstop_label.setText(gain_to_fstop_label(g))
        self.btn_fstop_label.setToolTip(gain_to_fstop_tooltip(g))
        self.gain_spin.blockSignals(True)
        self.gain_slider.blockSignals(True)
        self.gain_spin.setValue(g)
        ev = math.log2(g)
        slider_val = max(-600, min(600, int(round(ev * 100.0))))
        self.gain_slider.setValue(slider_val)
        self.gain_spin.blockSignals(False)
        self.gain_slider.blockSignals(False)
        self.gain_changed.emit(g)

    def reset_gain(self) -> None:
        """Reset gain to 1.000 (f/8)."""
        self.set_gain(1.0)

    def set_exposure(self, ev: float) -> None:
        """Set exposure in EV (maps to gain = 2^ev for backward compatibility)."""
        ev = round(float(ev), 3)
        self.ocio.exposure_ev = ev
        gain_val = 2.0 ** ev
        self.set_gain(gain_val)
        self.exposure_changed.emit(ev)

    def reset_exposure(self) -> None:
        """Reset exposure to 0.0 EV / gain 1.000."""
        self.ocio.exposure_ev = 0.0
        self.reset_gain()
        self.exposure_changed.emit(0.0)

    # --------------------------------------------------------------------------
    # Gamma Handlers (Foundry Nuke style Y)
    # --------------------------------------------------------------------------
    def _on_gam_spin_changed(self, val: float) -> None:
        val = max(0.0, min(4.0, val))
        self.ocio.gamma = val
        if val <= 0.0:
            s_val = 0
        elif val <= 0.1:
            s_val = int(round((val / 0.1) * 180.0))
        elif val <= 1.0:
            s_val = int(round(180.0 + ((val - 0.1) / 0.9) * 320.0))
        else:
            s_val = int(round(500.0 + ((min(4.0, val) - 1.0) / 3.0) * 500.0))
        self.gam_slider.blockSignals(True)
        self.gam_slider.setValue(s_val)
        self.gam_slider.blockSignals(False)
        self.gamma_changed.emit(val)

    def _on_gam_slider_changed(self, val: int) -> None:
        if val <= 0:
            g = 0.0
        elif val <= 180:
            g = round((val / 180.0) * 0.1, 3)
        elif val <= 500:
            g = round(0.1 + ((val - 180.0) / 320.0) * 0.9, 3)
        else:
            g = round(1.0 + ((val - 500.0) / 500.0) * 3.0, 3)
        self.ocio.gamma = g
        self.gam_spin.blockSignals(True)
        self.gam_spin.setValue(g)
        self.gam_spin.blockSignals(False)
        self.gamma_changed.emit(g)

    def set_gamma(self, g: float) -> None:
        """Set gamma multiplier (0.0 to 4.0, center = 1.0)."""
        g = max(0.0, min(4.0, round(float(g), 3)))
        self.ocio.gamma = g
        self.gam_spin.blockSignals(True)
        self.gam_slider.blockSignals(True)
        self.gam_spin.setValue(g)
        if g <= 0.0:
            s_val = 0
        elif g <= 0.1:
            s_val = int(round((g / 0.1) * 180.0))
        elif g <= 1.0:
            s_val = int(round(180.0 + ((g - 0.1) / 0.9) * 320.0))
        else:
            s_val = int(round(500.0 + ((min(4.0, g) - 1.0) / 3.0) * 500.0))
        self.gam_slider.setValue(s_val)
        self.gam_spin.blockSignals(False)
        self.gam_slider.blockSignals(False)
        self.gamma_changed.emit(g)

    def reset_gamma(self) -> None:
        """Reset gamma to 1.000."""
        self.set_gamma(1.0)

    # --------------------------------------------------------------------------
    # Saturation Handlers (Foundry Nuke style S)
    # --------------------------------------------------------------------------
    def _on_sat_spin_changed(self, val: float) -> None:
        val = max(0.0, min(4.0, val))
        self._saturation = val
        if val <= 0.0:
            s_val = 0
        elif val <= 0.1:
            s_val = int(round((val / 0.1) * 180.0))
        elif val <= 1.0:
            s_val = int(round(180.0 + ((val - 0.1) / 0.9) * 320.0))
        else:
            s_val = int(round(500.0 + ((min(4.0, val) - 1.0) / 3.0) * 500.0))
        self.sat_slider.blockSignals(True)
        self.sat_slider.setValue(s_val)
        self.sat_slider.blockSignals(False)
        self.saturation_changed.emit(val)

    def _on_sat_slider_changed(self, val: int) -> None:
        if val <= 0:
            s = 0.0
        elif val <= 180:
            s = round((val / 180.0) * 0.1, 3)
        elif val <= 500:
            s = round(0.1 + ((val - 180.0) / 320.0) * 0.9, 3)
        else:
            s = round(1.0 + ((val - 500.0) / 500.0) * 3.0, 3)
        self._saturation = s
        self.sat_spin.blockSignals(True)
        self.sat_spin.setValue(s)
        self.sat_spin.blockSignals(False)
        self.saturation_changed.emit(s)

    def set_saturation(self, s: float) -> None:
        """Set saturation multiplier (0.0 to 4.0, center = 1.0)."""
        s = max(0.0, min(4.0, round(float(s), 3)))
        self._saturation = s
        self.sat_spin.blockSignals(True)
        self.sat_slider.blockSignals(True)
        self.sat_spin.setValue(s)
        if s <= 0.0:
            s_val = 0
        elif s <= 0.1:
            s_val = int(round((s / 0.1) * 180.0))
        elif s <= 1.0:
            s_val = int(round(180.0 + ((s - 0.1) / 0.9) * 320.0))
        else:
            s_val = int(round(500.0 + ((min(4.0, s) - 1.0) / 3.0) * 500.0))
        self.sat_slider.setValue(s_val)
        self.sat_spin.blockSignals(False)
        self.sat_slider.blockSignals(False)
        self.saturation_changed.emit(s)

    def reset_saturation(self) -> None:
        """Reset saturation to 1.000."""
        self.set_saturation(1.0)

    def set_scrub_mode(self, enabled: bool) -> None:
        """Set scrub mode button state without re-triggering signal."""
        self.btn_scrub.blockSignals(True)
        self.btn_scrub.setChecked(bool(enabled))
        self.btn_scrub.blockSignals(False)

    @property
    def is_scrub_mode_active(self) -> bool:
        return self.btn_scrub.isChecked()

    def reset_all_colors(self) -> None:
        """Reset Gain (1.0), Gamma (1.0), and Saturation (1.0)."""
        self.reset_gain()
        self.reset_gamma()
        self.reset_saturation()
        self.reset_exposure()

    # --------------------------------------------------------------------------
    # Channel Handlers
    # --------------------------------------------------------------------------
    def _on_channel_combo_changed(self, index: int) -> None:
        mode_id = self.chan_combo.itemData(index)
        if mode_id is not None:
            self._current_channel_mode = int(mode_id)
            for mid, btn in self._chan_buttons.items():
                btn.setChecked(mid == self._current_channel_mode)
            self.channel_changed.emit(self._current_channel_mode)

    def _on_channel_btn_clicked(self, mode_id: int) -> None:
        if mode_id != 0 and self._current_channel_mode == mode_id:
            self.set_channel_mode(0)
        else:
            self.set_channel_mode(mode_id)

    def _show_channel_menu(self) -> None:
        menu = QMenu(self)
        channels = [
            (0, "RGB Composite (C)"),
            (1, "Red Channel (R)"),
            (2, "Green Channel (G)"),
            (3, "Blue Channel (B)"),
            (4, "Alpha Channel (A)"),
            (5, "Luminance Y (Y)"),
        ]
        for mode_id, text in channels:
            act = menu.addAction(text)
            act.setCheckable(True)
            act.setChecked(self._current_channel_mode == mode_id)
            act.triggered.connect(lambda checked=False, m=mode_id: self.set_channel_mode(m))
        menu.exec(self.btn_channel.mapToGlobal(QPoint(0, self.btn_channel.height())))

    def set_channel_mode(self, mode: int) -> None:
        self._current_channel_mode = mode
        idx = self.chan_combo.findData(mode)
        if idx >= 0:
            self.chan_combo.blockSignals(True)
            self.chan_combo.setCurrentIndex(idx)
            self.chan_combo.blockSignals(False)
        labels = {0: "RGB", 1: "Red", 2: "Green", 3: "Blue", 4: "Alpha", 5: "Luma"}
        if hasattr(self, "btn_channel"):
            self.btn_channel.setText(labels.get(mode, "RGB"))
        for mid, btn in self._chan_buttons.items():
            btn.blockSignals(True)
            btn.setChecked(mid == mode)
            btn.blockSignals(False)
        self.channel_changed.emit(mode)

    def toggle_channel_mode(self, mode: int) -> None:
        """Toggle specified channel mode: if active, revert to RGB (0); else activate it."""
        if mode == 0:
            self.set_channel_mode(0)
        elif self._current_channel_mode == mode:
            self.set_channel_mode(0)
        else:
            self.set_channel_mode(mode)

    # --------------------------------------------------------------------------
    # Layer & Stereo Handlers
    # --------------------------------------------------------------------------
    def _on_layer_combo_changed(self, index: int) -> None:
        layer_name = self.layer_combo.currentData()
        if layer_name:
            self.layer_changed.emit(str(layer_name))

    def set_available_layers(self, layers: list[str], active_layer: str = "beauty") -> None:
        """Dynamically populate EXR layer dropdown."""
        self.layer_combo.blockSignals(True)
        self.layer_combo.clear()
        if not layers:
            layers = ["beauty"]
        for lyr in layers:
            self.layer_combo.addItem(lyr, lyr)
        idx = self.layer_combo.findData(active_layer)
        if idx >= 0:
            self.layer_combo.setCurrentIndex(idx)
        else:
            self.layer_combo.setCurrentIndex(0)
        self.layer_combo.blockSignals(False)

    def set_current_layer(self, layer_name: str) -> None:
        """Select active layer in layer_combo dropdown without re-triggering signal loop."""
        idx = self.layer_combo.findData(layer_name)
        if idx >= 0:
            self.layer_combo.blockSignals(True)
            self.layer_combo.setCurrentIndex(idx)
            self.layer_combo.blockSignals(False)

    def _on_stereo_combo_changed(self, index: int) -> None:
        mode = self.stereo_combo.currentData()
        if mode is not None:
            self.stereo_changed.emit(int(mode))

    def set_stereo_mode(self, mode: int) -> None:
        idx = self.stereo_combo.findData(mode)
        if idx >= 0:
            self.stereo_combo.blockSignals(True)
            self.stereo_combo.setCurrentIndex(idx)
            self.stereo_combo.blockSignals(False)
            self.stereo_changed.emit(mode)

    # --------------------------------------------------------------------------
    # Colorspace Handlers
    # --------------------------------------------------------------------------
    def setup_standard_spaces(self, default_view: str = "sRGB", default_input: str = "linear") -> None:
        """Populate input and view combos with standard colorspaces."""
        self._color_management_mode = "standard"
        if hasattr(self.ocio, "color_management_mode"):
            self.ocio.color_management_mode = "standard"

        self.input_combo.blockSignals(True)
        self.input_combo.clear()
        for cs in STANDARD_COLORSPACES:
            self.input_combo.addItem(cs.label, cs.id)
            self.input_combo.setItemData(self.input_combo.count() - 1, cs.description, Qt.ItemDataRole.ToolTipRole)

        in_item = next((cs for cs in STANDARD_COLORSPACES if cs.name.lower() == default_input.lower()), STANDARD_COLORSPACES[0])
        in_idx = self.input_combo.findData(in_item.id)
        if in_idx >= 0:
            self.input_combo.setCurrentIndex(in_idx)
        self.input_combo.blockSignals(False)

        self.view_combo.blockSignals(True)
        self.view_combo.clear()
        for cs in STANDARD_COLORSPACES:
            self.view_combo.addItem(cs.label, cs.id)
            self.view_combo.setItemData(self.view_combo.count() - 1, cs.description, Qt.ItemDataRole.ToolTipRole)

        view_item = next((cs for cs in STANDARD_COLORSPACES if cs.name.lower() == default_view.lower()), STANDARD_COLORSPACES[1])
        vw_idx = self.view_combo.findData(view_item.id)
        if vw_idx >= 0:
            self.view_combo.setCurrentIndex(vw_idx)
        self.view_combo.blockSignals(False)

        self._on_color_transform_changed()

    def set_color_management_mode(self, mode: str, default_view: str = "sRGB", default_input: str = "linear") -> None:
        """Switch top bar between Standard Colorspaces and OpenColorIO mode."""
        self._color_management_mode = mode
        if hasattr(self.ocio, "color_management_mode"):
            self.ocio.color_management_mode = mode
        if mode == "standard":
            self.setup_standard_spaces(default_view, default_input)
        else:
            spaces = self.ocio.get_color_spaces()
            views = self.ocio.get_views()
            self.update_ocio_spaces(spaces, views)

    def _on_color_transform_changed(self) -> None:
        in_idx = self.input_combo.currentData()
        view_idx = self.view_combo.currentData()
        if in_idx is None:
            in_idx = 0
        if view_idx is None:
            view_idx = 1
        if hasattr(self.ocio, "standard_input_id"):
            self.ocio.standard_input_id = int(in_idx)
        if hasattr(self.ocio, "standard_view_id"):
            self.ocio.standard_view_id = int(view_idx)
        self.color_transforms_changed.emit(int(in_idx), int(view_idx))

    def set_color_spaces(self, input_space_idx: int, view_transform_idx: int) -> None:
        in_cb_idx = self.input_combo.findData(input_space_idx)
        if in_cb_idx >= 0:
            self.input_combo.blockSignals(True)
            self.input_combo.setCurrentIndex(in_cb_idx)
            self.input_combo.blockSignals(False)

        vw_cb_idx = self.view_combo.findData(view_transform_idx)
        if vw_cb_idx >= 0:
            self.view_combo.blockSignals(True)
            self.view_combo.setCurrentIndex(vw_cb_idx)
            self.view_combo.blockSignals(False)

        if hasattr(self.ocio, "standard_input_id"):
            self.ocio.standard_input_id = int(input_space_idx)
        if hasattr(self.ocio, "standard_view_id"):
            self.ocio.standard_view_id = int(view_transform_idx)
        self.color_transforms_changed.emit(int(input_space_idx), int(view_transform_idx))

    def set_ocio_context(self, display: str, view: str) -> None:
        """Update top bar combo boxes to reflect active OCIO display/view."""
        vw_idx = self.view_combo.findText(view)
        if vw_idx >= 0:
            self.view_combo.blockSignals(True)
            self.view_combo.setCurrentIndex(vw_idx)
            self.view_combo.blockSignals(False)
        self._on_color_transform_changed()

    def update_ocio_spaces(self, spaces: list[str], views: list[str]) -> None:
        """Update input and view combos with spaces and views from custom OCIO config."""
        self._color_management_mode = "ocio"
        if hasattr(self.ocio, "color_management_mode"):
            self.ocio.color_management_mode = "ocio"

        if spaces:
            self.input_combo.blockSignals(True)
            self.input_combo.clear()
            for idx, sp in enumerate(spaces):
                self.input_combo.addItem(sp, idx)
            self.input_combo.blockSignals(False)

        if views:
            self.view_combo.blockSignals(True)
            self.view_combo.clear()
            for idx, vw in enumerate(views):
                self.view_combo.addItem(vw, idx)
            self.view_combo.blockSignals(False)

        self._on_color_transform_changed()
