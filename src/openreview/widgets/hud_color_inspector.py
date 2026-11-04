"""
Studio Color Inspector Floating HUD Card Widget.
Provides high-precision pixel sampling with float RGBA, 8/10/16-bit integer readouts,
Hex color, HSV, Luma Y, color swatch, aperture sizing, and multi-point/area Eyedropper samplers.
"""
from __future__ import annotations

import colorsys
from typing import Optional, Tuple, List, Any
import numpy as np
from PySide6.QtCore import Qt, Signal, QTimer
from PySide6.QtGui import QColor, QClipboard, QGuiApplication
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QGridLayout,
    QLabel,
    QPushButton,
    QComboBox,
    QFrame,
    QScrollArea,
    QSizePolicy,
)

from openreview.widgets.draggable_scroll_area import BlenderScrollArea


class _StatSwatchTile(QFrame):
    """Swatch thumbnail preview tile for Min, Max, Average, or Median color values."""

    def __init__(self, title: str, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.title = title
        self.rgba: Tuple[float, float, float, float] = (0.0, 0.0, 0.0, 1.0)
        self.setMouseTracking(True)
        self.setStyleSheet("""
            QFrame {
                background-color: #1c1c1c;
                border: 1px solid #333333;
                border-radius: 3px;
            }
            QLabel {
                border: none;
                background: transparent;
            }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 3, 4, 3)
        layout.setSpacing(2)

        self.hdr_lbl = QLabel(title.upper(), self)
        self.hdr_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.hdr_lbl.setStyleSheet("color: #aaaaaa; font-size: 9px; font-weight: bold;")
        layout.addWidget(self.hdr_lbl)

        self.swatch_box = QFrame(self)
        self.swatch_box.setFixedHeight(22)
        self.swatch_box.setStyleSheet("background-color: #000000; border: 1px solid #555555; border-radius: 2px;")
        layout.addWidget(self.swatch_box)

        self.sub_lbl = QLabel("#000000", self)
        self.sub_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.sub_lbl.setStyleSheet("color: #cccccc; font-family: monospace; font-size: 8.5px;")
        layout.addWidget(self.sub_lbl)

    def set_rgba(self, rgba: Tuple[float, float, float, float]) -> None:
        self.rgba = rgba
        r, g, b, a = rgba
        u8_r = int(np.clip(r * 255.0, 0, 255))
        u8_g = int(np.clip(g * 255.0, 0, 255))
        u8_b = int(np.clip(b * 255.0, 0, 255))
        hex_code = f"#{u8_r:02X}{u8_g:02X}{u8_b:02X}"
        luma = 0.2126 * r + 0.7152 * g + 0.0722 * b

        self.swatch_box.setStyleSheet(
            f"background-color: {hex_code}; border: 1px solid #666666; border-radius: 2px;"
        )
        self.sub_lbl.setText(hex_code)
        self.setToolTip(
            f"{self.title} Swatch\n"
            f"Float: R:{r:0.4f} G:{g:0.4f} B:{b:0.4f} A:{a:0.4f}\n"
            f"8-bit: ({u8_r}, {u8_g}, {u8_b}) | Hex: {hex_code}\n"
            f"Luma Y: {luma:0.4f}"
        )


class _SamplerRowWidget(QFrame):
    """Compact row widget representing a single pinned Eyedropper sampler (#1, #2, ...)."""

    remove_clicked = Signal(int)
    row_clicked = Signal(int)

    def __init__(self, sampler: Any, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.sampler_id: int = int(sampler.sampler_id)
        self._sampler = sampler
        self._is_selected: bool = False
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._apply_row_style()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(2)

        # Top row: Swatch + #ID & Coords/Area + Copy + Remove (✕)
        top = QHBoxLayout()
        top.setSpacing(6)

        self.swatch = QFrame(self)
        self.swatch.setFixedSize(16, 16)
        top.addWidget(self.swatch)

        self.title_lbl = QLabel(self)
        self.title_lbl.setStyleSheet("color: #e5a823; font-weight: bold; font-size: 10px; font-family: monospace;")
        top.addWidget(self.title_lbl)

        top.addStretch()

        self.copy_btn = QPushButton("Copy", self)
        self.copy_btn.setFixedSize(36, 16)
        self.copy_btn.setToolTip(f"Copy Sampler #{self.sampler_id} values to clipboard")
        self.copy_btn.setStyleSheet("""
            QPushButton {
                background-color: #2c2c2c;
                color: #bbbbbb;
                border: 1px solid #444444;
                border-radius: 2px;
                font-size: 9px;
                padding: 0px;
            }
            QPushButton:hover {
                background-color: #e5a823;
                color: #111111;
            }
        """)
        self.copy_btn.clicked.connect(self._copy_sampler)
        top.addWidget(self.copy_btn)

        self.remove_btn = QPushButton("✕", self)
        self.remove_btn.setFixedSize(18, 16)
        self.remove_btn.setToolTip(f"Remove Sampler #{self.sampler_id}")
        self.remove_btn.setStyleSheet("""
            QPushButton {
                background-color: #2c2c2c;
                color: #cccccc;
                border: 1px solid #444444;
                border-radius: 2px;
                font-size: 9px;
                font-weight: bold;
                padding: 0px;
            }
            QPushButton:hover {
                background-color: #d9534f;
                color: #ffffff;
                border-color: #d9534f;
            }
        """)
        self.remove_btn.clicked.connect(lambda: self.remove_clicked.emit(self.sampler_id))
        top.addWidget(self.remove_btn)

        layout.addLayout(top)

        # Bottom lines: Float RGB + 8-bit/Hex/Luma
        self.float_lbl = QLabel(self)
        self.float_lbl.setTextFormat(Qt.TextFormat.RichText)
        self.float_lbl.setStyleSheet("color: #dddddd; font-family: monospace; font-size: 9.5px;")
        layout.addWidget(self.float_lbl)

        self.meta_lbl = QLabel(self)
        self.meta_lbl.setTextFormat(Qt.TextFormat.RichText)
        self.meta_lbl.setStyleSheet("color: #999999; font-family: monospace; font-size: 9.5px;")
        layout.addWidget(self.meta_lbl)

        self.update_from_sampler(sampler)

    def _apply_row_style(self) -> None:
        border_col = "#e5a823" if self._is_selected else "#353535"
        bg_col = "#28241c" if self._is_selected else "#212121"
        self.setStyleSheet(f"""
            QFrame {{
                background-color: {bg_col};
                border: 1px solid {border_col};
                border-radius: 3px;
            }}
            QLabel {{
                border: none;
                background: transparent;
            }}
        """)

    def set_selected(self, selected: bool) -> None:
        if self._is_selected != bool(selected):
            self._is_selected = bool(selected)
            self._apply_row_style()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.row_clicked.emit(self.sampler_id)
        super().mousePressEvent(event)

    def update_from_sampler(self, sampler: Any) -> None:
        self._sampler = sampler
        r, g, b, a = sampler.r, sampler.g, sampler.b, sampler.a
        u8_r = int(np.clip(r * 255.0, 0, 255))
        u8_g = int(np.clip(g * 255.0, 0, 255))
        u8_b = int(np.clip(b * 255.0, 0, 255))
        hex_code = f"#{u8_r:02X}{u8_g:02X}{u8_b:02X}"
        luma = 0.2126 * r + 0.7152 * g + 0.0722 * b

        self.swatch.setStyleSheet(
            f"background-color: {hex_code}; border: 1px solid #666666; border-radius: 2px;"
        )
        if getattr(sampler, "w", 1) > 1 or getattr(sampler, "h", 1) > 1:
            area_str = f"X:{sampler.x} Y:{sampler.y} [{sampler.w}×{sampler.h}]"
        elif getattr(sampler, "aperture", 1) > 1:
            ap = sampler.aperture
            area_str = f"X:{sampler.x} Y:{sampler.y} ({ap}×{ap})"
        else:
            area_str = f"X:{sampler.x} Y:{sampler.y}"

        self.title_lbl.setText(f"#{sampler.sampler_id}  {area_str}")
        self.float_lbl.setText(
            f"Float: <span style='color:#ff6b6b;'>R:{r:0.4f}</span> "
            f"<span style='color:#69db7c;'>G:{g:0.4f}</span> "
            f"<span style='color:#4dabf7;'>B:{b:0.4f}</span>"
        )
        self.meta_lbl.setText(
            f"8b: <span style='color:#ff6b6b;'>{u8_r}</span>,"
            f"<span style='color:#69db7c;'>{u8_g}</span>,"
            f"<span style='color:#4dabf7;'>{u8_b}</span> | {hex_code} | "
            f"<span style='color:#ffd43b;'>Y:{luma:0.4f}</span>"
        )

    def _copy_sampler(self) -> None:
        s = self._sampler
        u8_r = int(np.clip(s.r * 255.0, 0, 255))
        u8_g = int(np.clip(s.g * 255.0, 0, 255))
        u8_b = int(np.clip(s.b * 255.0, 0, 255))
        text = (
            f"Sampler #{s.sampler_id} | RGB Float: ({s.r:0.4f}, {s.g:0.4f}, {s.b:0.4f}) | "
            f"Hex: #{u8_r:02X}{u8_g:02X}{u8_b:02X} | Pos: ({s.x}, {s.y})"
        )
        cb = QGuiApplication.clipboard()
        if cb:
            cb.setText(text)


class ColorInspectorHUD(QWidget):
    """
    Precision Color Inspector & Pixel Analyzer Widget for Viewport Floating HUD.
    Continuously samples the pixel under the cursor (including under the widget itself),
    manages multiple pinned point/area Eyedropper samplers, and analyzes Min/Max/Avg/Median
    colorimetry plus NaN/Inf/Negative pixel diagnostics.
    """

    eyedropper_toggled = Signal(bool)
    aperture_changed = Signal(int)
    sampler_remove_requested = Signal(int)
    samplers_clear_requested = Signal()
    active_sampler_changed = Signal(int)
    analyzer_checks_changed = Signal(bool, bool, bool)  # (check_nan, check_inf, check_neg)

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setMinimumWidth(260)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        self.setMouseTracking(True)

        # Inspection state
        self._current_x: int = 0
        self._current_y: int = 0
        self._current_rgba: Tuple[float, float, float, float] = (0.0, 0.0, 0.0, 1.0)
        self._aperture: int = 1  # 1x1, 3x3, 5x5
        self._sampler_rows: dict[int, _SamplerRowWidget] = {}
        self._samplers_by_id: dict[int, Any] = {}
        self._selected_sampler_id: Optional[int] = None

        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        # Header: Swatch + Live Cursor Coordinates + Eyedropper Button
        top_row = QHBoxLayout()
        top_row.setSpacing(8)

        # Color Swatch Frame
        self.swatch = QFrame(self)
        self.swatch.setFixedSize(36, 36)
        self.swatch.setStyleSheet("background-color: #000000; border: 1px solid #444444; border-radius: 3px;")
        top_row.addWidget(self.swatch)

        coord_col = QVBoxLayout()
        coord_col.setSpacing(2)
        self.pos_label = QLabel("Pos: X: 0, Y: 0", self)
        self.pos_label.setStyleSheet("color: #e5a823; font-weight: bold; font-size: 11px;")
        self.hex_label = QLabel("Hex: #000000", self)
        self.hex_label.setStyleSheet("color: #aaaaaa; font-family: monospace; font-size: 10px;")
        coord_col.addWidget(self.pos_label)
        coord_col.addWidget(self.hex_label)
        top_row.addLayout(coord_col)

        top_row.addStretch()

        # Eyedropper (+ Sample) Tool Button (replaces Freeze button)
        self.eyedropper_btn = QPushButton("+ Eyedropper", self)
        self.eyedropper_btn.setCheckable(True)
        self.eyedropper_btn.setFixedSize(86, 24)
        self.eyedropper_btn.setToolTip(
            "Toggle Eyedropper tool: Click on canvas to pin a point sampler, or Click-and-Drag to sample a rectangular area"
        )
        self.eyedropper_btn.setStyleSheet("""
            QPushButton {
                background-color: #2a2a2a;
                color: #dddddd;
                border: 1px solid #4a4a4a;
                border-radius: 2px;
                font-size: 10px;
                font-weight: bold;
            }
            QPushButton:hover {
                border-color: #e5a823;
                color: #ffffff;
            }
            QPushButton:checked {
                background-color: #e5a823;
                color: #111111;
                border-color: #f0b429;
            }
        """)
        self.eyedropper_btn.toggled.connect(self._on_eyedropper_toggled)
        top_row.addWidget(self.eyedropper_btn)

        layout.addLayout(top_row)

        # Separator line
        sep = QFrame(self)
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("color: #333333;")
        layout.addWidget(sep)

        # Color Values Grid
        grid = QGridLayout()
        grid.setVerticalSpacing(2)
        grid.setHorizontalSpacing(6)
        grid.setContentsMargins(0, 1, 0, 1)

        def make_hdr(txt: str) -> QLabel:
            lbl = QLabel(txt, self)
            lbl.setStyleSheet("color: #888888; font-size: 10px; font-weight: bold;")
            lbl.setFixedHeight(17)
            return lbl

        def make_val(txt: str) -> QLabel:
            lbl = QLabel(txt, self)
            lbl.setTextFormat(Qt.TextFormat.RichText)
            lbl.setStyleSheet("color: #dddddd; font-family: monospace; font-size: 10px;")
            lbl.setFixedHeight(17)
            return lbl

        grid.addWidget(make_hdr("Float:"), 0, 0)
        self.val_float = make_val(
            "<span style='color:#ff6b6b;'>R: 0.0000</span> "
            "<span style='color:#69db7c;'>G: 0.0000</span> "
            "<span style='color:#4dabf7;'>B: 0.0000</span>"
        )
        grid.addWidget(self.val_float, 0, 1)

        grid.addWidget(make_hdr("Alpha:"), 1, 0)
        self.val_alpha = make_val("<span style='color:#ced4da;'>A: 1.0000 (100%)</span>")
        grid.addWidget(self.val_alpha, 1, 1)

        grid.addWidget(make_hdr("8-bit:"), 2, 0)
        self.val_8bit = make_val(
            "<span style='color:#ff6b6b;'>R: 0  </span> "
            "<span style='color:#69db7c;'>G: 0  </span> "
            "<span style='color:#4dabf7;'>B: 0  </span>"
        )
        grid.addWidget(self.val_8bit, 2, 1)

        grid.addWidget(make_hdr("10-bit:"), 3, 0)
        self.val_10bit = make_val(
            "<span style='color:#ff6b6b;'>R: 0   </span> "
            "<span style='color:#69db7c;'>G: 0   </span> "
            "<span style='color:#4dabf7;'>B: 0   </span>"
        )
        grid.addWidget(self.val_10bit, 3, 1)

        grid.addWidget(make_hdr("HSV:"), 4, 0)
        self.val_hsv = make_val("H: 0°  S: 0%  V: 0%")
        grid.addWidget(self.val_hsv, 4, 1)

        grid.addWidget(make_hdr("Luma Y:"), 5, 0)
        self.val_luma = make_val("<span style='color:#ffd43b;'>0.0000</span>")
        grid.addWidget(self.val_luma, 5, 1)

        grid.setColumnStretch(0, 0)
        grid.setColumnStretch(1, 1)
        for r in range(6):
            grid.setRowStretch(r, 0)
        layout.addLayout(grid)

        # Controls Bar: Aperture selector & Copy button
        bot_row = QHBoxLayout()
        bot_row.setSpacing(6)

        ap_lbl = QLabel("Aperture:", self)
        ap_lbl.setStyleSheet("color: #888888; font-size: 10px;")
        bot_row.addWidget(ap_lbl)

        self.aperture_combo = QComboBox(self)
        self.aperture_combo.addItem("1x1 Point", 1)
        self.aperture_combo.addItem("3x3 Box", 3)
        self.aperture_combo.addItem("5x5 Box", 5)
        self.aperture_combo.currentIndexChanged.connect(self._on_aperture_changed)
        self.aperture_combo.setFixedHeight(20)
        self.aperture_combo.setStyleSheet("font-size: 10px; background-color: #222222; color: #cccccc;")
        bot_row.addWidget(self.aperture_combo)

        bot_row.addStretch()

        self.btn_copy = QPushButton("Copy", self)
        self.btn_copy.setFixedSize(45, 20)
        self.btn_copy.setToolTip("Copy live cursor RGB float and Hex values to clipboard")
        self.btn_copy.setStyleSheet("""
            QPushButton {
                background-color: #2a2a2a;
                color: #cccccc;
                border: 1px solid #3c3c3c;
                border-radius: 2px;
                font-size: 10px;
            }
            QPushButton:hover {
                background-color: #e5a823;
                color: #111111;
            }
        """)
        self.btn_copy.clicked.connect(self._copy_to_clipboard)
        bot_row.addWidget(self.btn_copy)

        layout.addLayout(bot_row)

        # ----------------------------------------------------------------------
        # Pinned Eyedropper Samplers Section
        # ----------------------------------------------------------------------
        sep2 = QFrame(self)
        sep2.setFrameShape(QFrame.Shape.HLine)
        sep2.setStyleSheet("color: #333333;")
        layout.addWidget(sep2)

        sampler_hdr_row = QHBoxLayout()
        sampler_hdr_row.setSpacing(6)
        self.samplers_hdr_label = QLabel("Pinned Samplers (0/3)", self)
        self.samplers_hdr_label.setStyleSheet("color: #e5a823; font-size: 10px; font-weight: bold;")
        sampler_hdr_row.addWidget(self.samplers_hdr_label)

        sampler_hdr_row.addStretch()

        self.clear_samplers_btn = QPushButton("Clear All", self)
        self.clear_samplers_btn.setFixedSize(56, 18)
        self.clear_samplers_btn.setEnabled(False)
        self.clear_samplers_btn.setToolTip("Remove all pinned Eyedropper samplers from viewport")
        self.clear_samplers_btn.setStyleSheet("""
            QPushButton {
                background-color: #2a2a2a;
                color: #cccccc;
                border: 1px solid #3c3c3c;
                border-radius: 2px;
                font-size: 9.5px;
            }
            QPushButton:hover:enabled {
                background-color: #d9534f;
                color: #ffffff;
                border-color: #d9534f;
            }
            QPushButton:disabled {
                color: #555555;
                border-color: #2c2c2c;
            }
        """)
        self.clear_samplers_btn.clicked.connect(self.samplers_clear_requested.emit)
        sampler_hdr_row.addWidget(self.clear_samplers_btn)

        layout.addLayout(sampler_hdr_row)

        # Scrollable container for sampler rows
        self.samplers_scroll = BlenderScrollArea(self)
        self.samplers_scroll.setWidgetResizable(True)
        self.samplers_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.samplers_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.samplers_scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        self.samplers_scroll.setMaximumHeight(150)
        self.samplers_scroll.hide()

        self.samplers_list_widget = QWidget(self.samplers_scroll)
        self.samplers_list_widget.setStyleSheet("background: transparent;")
        self.samplers_list_layout = QVBoxLayout(self.samplers_list_widget)
        self.samplers_list_layout.setContentsMargins(0, 0, 0, 0)
        self.samplers_list_layout.setSpacing(4)
        self.samplers_list_layout.addStretch()
        self.samplers_scroll.setWidget(self.samplers_list_widget)

        layout.addWidget(self.samplers_scroll)

        # ----------------------------------------------------------------------
        # Pixel Analyzer Section (Min, Max, Avg, Median + NaN/Inf/Neg Checks)
        # Hidden when 0 samplers exist so the HUD stays compact until needed
        # ----------------------------------------------------------------------
        self.analyzer_container = QWidget(self)
        analyzer_layout = QVBoxLayout(self.analyzer_container)
        analyzer_layout.setContentsMargins(0, 0, 0, 0)
        analyzer_layout.setSpacing(4)

        sep3 = QFrame(self.analyzer_container)
        sep3.setFrameShape(QFrame.Shape.HLine)
        sep3.setStyleSheet("color: #333333;")
        analyzer_layout.addWidget(sep3)

        analyzer_hdr = QHBoxLayout()
        analyzer_hdr.setSpacing(6)
        self.analyzer_title_lbl = QLabel("Pixel Analyzer", self.analyzer_container)
        self.analyzer_title_lbl.setStyleSheet("color: #e5a823; font-size: 10px; font-weight: bold;")
        analyzer_hdr.addWidget(self.analyzer_title_lbl)

        analyzer_hdr.addStretch()

        target_lbl = QLabel("Target:", self.analyzer_container)
        target_lbl.setStyleSheet("color: #888888; font-size: 9.5px;")
        analyzer_hdr.addWidget(target_lbl)

        self.analyzer_target_combo = QComboBox(self.analyzer_container)
        self.analyzer_target_combo.addItem("None (Add Sampler)", -1)
        self.analyzer_target_combo.setFixedHeight(19)
        self.analyzer_target_combo.setMinimumWidth(115)
        self.analyzer_target_combo.setStyleSheet("font-size: 9.5px; background-color: #222222; color: #cccccc;")
        self.analyzer_target_combo.currentIndexChanged.connect(self._on_analyzer_target_combo_changed)
        analyzer_hdr.addWidget(self.analyzer_target_combo)

        analyzer_layout.addLayout(analyzer_hdr)

        # 4 Swatch Thumbnail Previews (Min, Max, Average, Median)
        swatches_row = QHBoxLayout()
        swatches_row.setSpacing(4)
        self.swatch_min = _StatSwatchTile("Min", self.analyzer_container)
        self.swatch_max = _StatSwatchTile("Max", self.analyzer_container)
        self.swatch_avg = _StatSwatchTile("Avg", self.analyzer_container)
        self.swatch_med = _StatSwatchTile("Median", self.analyzer_container)
        swatches_row.addWidget(self.swatch_min)
        swatches_row.addWidget(self.swatch_max)
        swatches_row.addWidget(self.swatch_avg)
        swatches_row.addWidget(self.swatch_med)
        analyzer_layout.addLayout(swatches_row)

        # Numeric Per-Channel Statistics Grid (Min, Max, Average, Median)
        stats_grid = QGridLayout()
        stats_grid.setSpacing(2)
        stats_grid.setContentsMargins(0, 2, 0, 2)

        def make_stat_val() -> QLabel:
            lbl = QLabel(
                "<span style='color:#ff6b6b;'>R:0.0000</span> "
                "<span style='color:#69db7c;'>G:0.0000</span> "
                "<span style='color:#4dabf7;'>B:0.0000</span> "
                "<span style='color:#ffd43b;'>Y:0.0000</span>",
                self.analyzer_container,
            )
            lbl.setTextFormat(Qt.TextFormat.RichText)
            lbl.setStyleSheet("color: #cccccc; font-family: monospace; font-size: 9px;")
            return lbl

        stats_grid.addWidget(make_hdr("Min:"), 0, 0)
        self.stat_min_lbl = make_stat_val()
        stats_grid.addWidget(self.stat_min_lbl, 0, 1)

        stats_grid.addWidget(make_hdr("Max:"), 1, 0)
        self.stat_max_lbl = make_stat_val()
        stats_grid.addWidget(self.stat_max_lbl, 1, 1)

        stats_grid.addWidget(make_hdr("Avg:"), 2, 0)
        self.stat_avg_lbl = make_stat_val()
        stats_grid.addWidget(self.stat_avg_lbl, 2, 1)

        stats_grid.addWidget(make_hdr("Med:"), 3, 0)
        self.stat_med_lbl = make_stat_val()
        stats_grid.addWidget(self.stat_med_lbl, 3, 1)

        stats_grid.setColumnStretch(0, 0)
        stats_grid.setColumnStretch(1, 1)
        analyzer_layout.addLayout(stats_grid)

        # NaN / Inf / Negative Check Toggles & Status Badges
        checks_row = QHBoxLayout()
        checks_row.setSpacing(4)

        self.chk_nan_btn = QPushButton("NaN: 0", self.analyzer_container)
        self.chk_nan_btn.setCheckable(True)
        self.chk_nan_btn.setChecked(True)
        self.chk_nan_btn.setFixedHeight(20)
        self.chk_nan_btn.setToolTip("Check & highlight NaN (Not-a-Number) pixels in sampled area")
        self.chk_nan_btn.toggled.connect(self._on_analyzer_checks_toggled)
        checks_row.addWidget(self.chk_nan_btn)

        self.chk_inf_btn = QPushButton("Inf: 0", self.analyzer_container)
        self.chk_inf_btn.setCheckable(True)
        self.chk_inf_btn.setChecked(True)
        self.chk_inf_btn.setFixedHeight(20)
        self.chk_inf_btn.setToolTip("Check & highlight ±Infinity pixels in sampled area")
        self.chk_inf_btn.toggled.connect(self._on_analyzer_checks_toggled)
        checks_row.addWidget(self.chk_inf_btn)

        self.chk_neg_btn = QPushButton("Neg (<0): 0", self.analyzer_container)
        self.chk_neg_btn.setCheckable(True)
        self.chk_neg_btn.setChecked(True)
        self.chk_neg_btn.setFixedHeight(20)
        self.chk_neg_btn.setToolTip("Check & highlight Negative (< 0.0) sub-zero pixels in sampled area")
        self.chk_neg_btn.toggled.connect(self._on_analyzer_checks_toggled)
        checks_row.addWidget(self.chk_neg_btn)

        analyzer_layout.addLayout(checks_row)
        self.analyzer_container.hide()
        layout.addWidget(self.analyzer_container)
        layout.addStretch(1)
        self._update_check_badge_styles(0, 0, 0)

    def _update_check_badge_styles(self, nan_cnt: int, inf_cnt: int, neg_cnt: int) -> None:
        """Update text and alert styling on the NaN, Inf, and Negative check buttons."""
        self.chk_nan_btn.setText(f"NaN: {nan_cnt}")
        self.chk_inf_btn.setText(f"Inf: {inf_cnt}")
        self.chk_neg_btn.setText(f"Neg (<0): {neg_cnt}")

        def _badge_css(count: int, alert_bg: str, alert_border: str) -> str:
            if count > 0:
                return f"""
                    QPushButton {{
                        background-color: {alert_bg};
                        color: #ffffff;
                        border: 1px solid {alert_border};
                        border-radius: 2px;
                        font-size: 9.5px;
                        font-weight: bold;
                    }}
                    QPushButton:checked {{
                        background-color: {alert_border};
                        color: #111111;
                        border: 1px solid #ffffff;
                    }}
                """
            return """
                QPushButton {
                    background-color: #222222;
                    color: #888888;
                    border: 1px solid #353535;
                    border-radius: 2px;
                    font-size: 9.5px;
                }
                QPushButton:checked {
                    background-color: #263329;
                    color: #7bd88f;
                    border: 1px solid #3b5941;
                }
            """

        self.chk_nan_btn.setStyleSheet(_badge_css(nan_cnt, "#5a1a5a", "#ff44ff"))
        self.chk_inf_btn.setStyleSheet(_badge_css(inf_cnt, "#5a3d10", "#ffaa00"))
        self.chk_neg_btn.setStyleSheet(_badge_css(neg_cnt, "#5a1d1d", "#ff5555"))

    def update_pixel(self, x: int, y: int, r: float, g: float, b: float, a: float) -> None:
        """Slot called whenever the cursor inspects a pixel on the viewport (including under this widget)."""
        self._current_x = x
        self._current_y = y
        self._current_rgba = (r, g, b, a)

        # Update Position & Hex
        self.pos_label.setText(f"Pos: X: {x:<5d} Y: {y:<5d}")

        # Clamped 8-bit & 10-bit
        u8_r = int(np.clip(r * 255.0, 0, 255))
        u8_g = int(np.clip(g * 255.0, 0, 255))
        u8_b = int(np.clip(b * 255.0, 0, 255))
        hex_code = f"#{u8_r:02X}{u8_g:02X}{u8_b:02X}"
        self.hex_label.setText(f"Hex: {hex_code}")

        # Update Swatch
        self.swatch.setStyleSheet(
            f"background-color: {hex_code}; border: 1px solid #555555; border-radius: 3px;"
        )

        # Values (Color-coded per channel: Red #ff6b6b, Green #69db7c, Blue #4dabf7, Alpha #ced4da, Luma #ffd43b)
        self.val_float.setText(
            f"<span style='color:#ff6b6b;'>R: {r:0.4f}</span>  "
            f"<span style='color:#69db7c;'>G: {g:0.4f}</span>  "
            f"<span style='color:#4dabf7;'>B: {b:0.4f}</span>"
        )
        self.val_alpha.setText(f"<span style='color:#ced4da;'>A: {a:0.4f} ({int(a * 100)}%)</span>")

        self.val_8bit.setText(
            f"<span style='color:#ff6b6b;'>R: {u8_r:<3d}</span>  "
            f"<span style='color:#69db7c;'>G: {u8_g:<3d}</span>  "
            f"<span style='color:#4dabf7;'>B: {u8_b:<3d}</span>"
        )

        u10_r = int(np.clip(r * 1023.0, 0, 1023))
        u10_g = int(np.clip(g * 1023.0, 0, 1023))
        u10_b = int(np.clip(b * 1023.0, 0, 255 if False else 1023))
        self.val_10bit.setText(
            f"<span style='color:#ff6b6b;'>R: {u10_r:<4d}</span> "
            f"<span style='color:#69db7c;'>G: {u10_g:<4d}</span> "
            f"<span style='color:#4dabf7;'>B: {u10_b:<4d}</span>"
        )

        # HSV
        h, s, v = colorsys.rgb_to_hsv(np.clip(r, 0, 1), np.clip(g, 0, 1), np.clip(b, 0, 1))
        self.val_hsv.setText(f"H: {int(h * 360):<3d}° S: {int(s * 100):<3d}% V: {int(v * 100):<3d}%")

        # Rec.709 Luma Y
        luma = 0.2126 * r + 0.7152 * g + 0.0722 * b
        self.val_luma.setText(f"<span style='color:#ffd43b;'>{luma:0.4f}</span>")

    def update_samplers(self, samplers: List[Any]) -> None:
        """Synchronize the pinned Eyedropper samplers list and Pixel Analyzer with ViewportCanvas state."""
        count = len(samplers)
        self.samplers_hdr_label.setText(f"Pinned Samplers ({count}/3)")
        self.clear_samplers_btn.setEnabled(count > 0)

        prev_objs = {id(s) for s in self._samplers_by_id.values()}
        self._samplers_by_id = {int(s.sampler_id): s for s in samplers}
        active_ids = set(self._samplers_by_id.keys())

        # Remove rows that no longer exist
        for sid in list(self._sampler_rows.keys()):
            if sid not in active_ids:
                row = self._sampler_rows.pop(sid)
                row.hide()
                self.samplers_list_layout.removeWidget(row)
                row.deleteLater()

        # Update or add rows in FIFO order
        newest_added_id: Optional[int] = None
        for s in samplers:
            sid = int(s.sampler_id)
            is_new_obj = id(s) not in prev_objs
            if sid in self._sampler_rows:
                row = self._sampler_rows[sid]
                row.update_from_sampler(s)
                if is_new_obj:
                    # Re-insert at the bottom when a sampler ID was recycled via FIFO replacement
                    self.samplers_list_layout.removeWidget(row)
                    idx = max(0, self.samplers_list_layout.count() - 1)
                    self.samplers_list_layout.insertWidget(idx, row)
                    newest_added_id = sid
            else:
                row = _SamplerRowWidget(s, self.samplers_list_widget)
                row.remove_clicked.connect(self.sampler_remove_requested.emit)
                row.row_clicked.connect(self.select_sampler)
                idx = max(0, self.samplers_list_layout.count() - 1)
                self.samplers_list_layout.insertWidget(idx, row)
                self._sampler_rows[sid] = row
                if is_new_obj:
                    newest_added_id = sid

        # Auto-select newest sampler when added, or fallback if current selection was removed
        if newest_added_id is not None:
            self._selected_sampler_id = newest_added_id
        elif self._selected_sampler_id not in active_ids:
            self._selected_sampler_id = samplers[-1].sampler_id if samplers else None

        # Refresh Target ComboBox without signal feedback
        self.analyzer_target_combo.blockSignals(True)
        self.analyzer_target_combo.clear()
        if not samplers:
            self.analyzer_target_combo.addItem("None (Add Sampler)", -1)
        else:
            for s in samplers:
                sid = int(s.sampler_id)
                desc = f"#{sid} ({s.w}×{s.h})" if (getattr(s, "w", 1) > 1 or getattr(s, "h", 1) > 1) else f"#{sid} ({s.x},{s.y})"
                self.analyzer_target_combo.addItem(desc, sid)
                if sid == self._selected_sampler_id:
                    self.analyzer_target_combo.setCurrentIndex(self.analyzer_target_combo.count() - 1)
        self.analyzer_target_combo.blockSignals(False)

        for sid, row in self._sampler_rows.items():
            row.set_selected(sid == self._selected_sampler_id)

        self._refresh_analyzer_readout()
        self.active_sampler_changed.emit(int(self._selected_sampler_id) if self._selected_sampler_id is not None else -1)

        if count > 0:
            self.samplers_scroll.setFixedHeight(min(138, count * 44))
            self.samplers_scroll.setVisible(True)
            self.analyzer_container.setVisible(True)
        else:
            self.samplers_scroll.setFixedHeight(0)
            self.samplers_scroll.setVisible(False)
            self.analyzer_container.setVisible(False)

        self._sync_widget_size()

    def _apply_auto_height(self) -> None:
        """Recalculate layout and resize both this widget and its parent FloatingHUDCard to auto height."""
        if self.samplers_list_layout is not None:
            self.samplers_list_layout.invalidate()
            self.samplers_list_layout.activate()
        self.samplers_list_widget.adjustSize()

        if self.layout() is not None:
            self.layout().invalidate()
            self.layout().activate()
        self.updateGeometry()
        self.adjustSize()

        parent = self.parentWidget()
        if parent is not None:
            if hasattr(parent, "auto_adjust_height"):
                parent.auto_adjust_height()
            elif not getattr(parent, "_is_collapsed", False):
                if parent.layout() is not None:
                    parent.layout().invalidate()
                    parent.layout().activate()
                hint = parent.sizeHint().expandedTo(parent.minimumSizeHint())
                parent.resize(max(parent.minimumWidth(), hint.width()), hint.height())

    def _sync_widget_size(self) -> None:
        """Automatically recalculate and shrink/expand this widget and its FloatingHUDCard container."""
        self._apply_auto_height()
        QTimer.singleShot(0, self._apply_auto_height)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._sync_widget_size()

    def get_active_analyzer_sampler_id(self) -> Optional[int]:
        """Return the ID of the sampler currently analyzed by the Pixel Analyzer."""
        return self._selected_sampler_id

    def select_sampler(self, sampler_id: int) -> None:
        """Select a specific pinned sampler for the Pixel Analyzer."""
        if sampler_id in self._samplers_by_id:
            self._selected_sampler_id = int(sampler_id)
            for sid, row in self._sampler_rows.items():
                row.set_selected(sid == self._selected_sampler_id)
            self.analyzer_target_combo.blockSignals(True)
            for i in range(self.analyzer_target_combo.count()):
                if self.analyzer_target_combo.itemData(i) == self._selected_sampler_id:
                    self.analyzer_target_combo.setCurrentIndex(i)
                    break
            self.analyzer_target_combo.blockSignals(False)
            self._refresh_analyzer_readout()
            self.active_sampler_changed.emit(self._selected_sampler_id)

    def _on_analyzer_target_combo_changed(self, idx: int) -> None:
        sid = self.analyzer_target_combo.currentData()
        if sid is not None and int(sid) > 0:
            self.select_sampler(int(sid))

    def _on_analyzer_checks_toggled(self, _checked: bool = False) -> None:
        self.analyzer_checks_changed.emit(
            self.chk_nan_btn.isChecked(),
            self.chk_inf_btn.isChecked(),
            self.chk_neg_btn.isChecked(),
        )

    def _refresh_analyzer_readout(self) -> None:
        """Update Min/Max/Avg/Median swatches, per-channel rows, and NaN/Inf/Neg badges from active sampler."""
        def _fmt_rgba_stat(rgba: Tuple[float, float, float, float]) -> str:
            r, g, b, _ = rgba
            y = 0.2126 * r + 0.7152 * g + 0.0722 * b
            return (
                f"<span style='color:#ff6b6b;'>R:{r:0.4f}</span> "
                f"<span style='color:#69db7c;'>G:{g:0.4f}</span> "
                f"<span style='color:#4dabf7;'>B:{b:0.4f}</span> "
                f"<span style='color:#ffd43b;'>Y:{y:0.4f}</span>"
            )

        s = self._samplers_by_id.get(self._selected_sampler_id) if self._selected_sampler_id is not None else None
        if s is None:
            zero = (0.0, 0.0, 0.0, 1.0)
            self.swatch_min.set_rgba(zero)
            self.swatch_max.set_rgba(zero)
            self.swatch_avg.set_rgba(zero)
            self.swatch_med.set_rgba(zero)
            zero_str = _fmt_rgba_stat(zero)
            self.stat_min_lbl.setText(zero_str)
            self.stat_max_lbl.setText(zero_str)
            self.stat_avg_lbl.setText(zero_str)
            self.stat_med_lbl.setText(zero_str)
            self._update_check_badge_styles(0, 0, 0)
            return

        min_rgba = getattr(s, "min_rgba", (s.r, s.g, s.b, s.a))
        max_rgba = getattr(s, "max_rgba", (s.r, s.g, s.b, s.a))
        avg_rgba = getattr(s, "avg_rgba", (s.r, s.g, s.b, s.a))
        med_rgba = getattr(s, "med_rgba", (s.r, s.g, s.b, s.a))

        self.swatch_min.set_rgba(min_rgba)
        self.swatch_max.set_rgba(max_rgba)
        self.swatch_avg.set_rgba(avg_rgba)
        self.swatch_med.set_rgba(med_rgba)

        self.stat_min_lbl.setText(_fmt_rgba_stat(min_rgba))
        self.stat_max_lbl.setText(_fmt_rgba_stat(max_rgba))
        self.stat_avg_lbl.setText(_fmt_rgba_stat(avg_rgba))
        self.stat_med_lbl.setText(_fmt_rgba_stat(med_rgba))

        nan_c = int(getattr(s, "nan_count", 0))
        inf_c = int(getattr(s, "inf_count", 0))
        neg_c = int(getattr(s, "neg_count", 0))
        self._update_check_badge_styles(nan_c, inf_c, neg_c)

    def set_eyedropper_active(self, active: bool) -> None:
        """Synchronize the + Eyedropper button checked state without signal loop."""
        self.eyedropper_btn.blockSignals(True)
        self.eyedropper_btn.setChecked(bool(active))
        self.eyedropper_btn.setText("Picking..." if active else "+ Eyedropper")
        self.eyedropper_btn.blockSignals(False)

    def _on_eyedropper_toggled(self, checked: bool) -> None:
        self.eyedropper_btn.setText("Picking..." if checked else "+ Eyedropper")
        self.eyedropper_toggled.emit(bool(checked))

    def _on_aperture_changed(self, idx: int) -> None:
        self._aperture = int(self.aperture_combo.currentData())
        self.aperture_changed.emit(self._aperture)

    @property
    def aperture(self) -> int:
        return self._aperture

    def _copy_to_clipboard(self) -> None:
        r, g, b, a = self._current_rgba
        u8_r = int(np.clip(r * 255.0, 0, 255))
        u8_g = int(np.clip(g * 255.0, 0, 255))
        u8_b = int(np.clip(b * 255.0, 0, 255))
        text = f"RGB Float: ({r:0.4f}, {g:0.4f}, {b:0.4f}) | Hex: #{u8_r:02X}{u8_g:02X}{u8_b:02X} | Pos: ({self._current_x}, {self._current_y})"
        cb = QGuiApplication.clipboard()
        if cb:
            cb.setText(text)


