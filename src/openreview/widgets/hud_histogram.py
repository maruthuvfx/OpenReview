"""
Live RGB & Luminance Histogram HUD Widget.
Inspired by DJV with real-time composite curves and min/max clipping readouts.
"""
from __future__ import annotations

from typing import Optional
import numpy as np
from PySide6.QtCore import Qt, QRectF
from PySide6.QtGui import QPainter, QColor, QPen, QPainterPath
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QComboBox,
    QFrame,
    QSizePolicy,
)


class HistogramCanvas(QWidget):
    """Custom-painted RGB histogram curve canvas."""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setMinimumSize(180, 80)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._hist_r = np.zeros(256, dtype=np.float32)
        self._hist_g = np.zeros(256, dtype=np.float32)
        self._hist_b = np.zeros(256, dtype=np.float32)
        self._hist_y = np.zeros(256, dtype=np.float32)
        self._max_val = 1.0

    def set_histogram_data(
        self,
        hist_r: Optional[np.ndarray],
        hist_g: Optional[np.ndarray],
        hist_b: Optional[np.ndarray],
        hist_y: Optional[np.ndarray],
        max_val: float,
    ) -> None:
        """Set pre-computed histogram distributions from asynchronous worker (thread-safe UI update)."""
        if hist_r is None:
            self._hist_r = np.zeros(256, dtype=np.float32)
            self._hist_g = np.zeros(256, dtype=np.float32)
            self._hist_b = np.zeros(256, dtype=np.float32)
            self._hist_y = np.zeros(256, dtype=np.float32)
            self._max_val = 1.0
        else:
            self._hist_r = hist_r
            self._hist_g = hist_g
            self._hist_b = hist_b
            self._hist_y = hist_y
            self._max_val = max_val
        self.update()

    def update_data(self, image_data: Optional[np.ndarray]) -> None:
        if image_data is None or image_data.size == 0:
            return

        # Fast subsampling for instant responsiveness (every 4th pixel)
        sub = image_data[::4, ::4, :3]
        if sub.size == 0:
            return

        # Quantize to 0..255 for histogram bins
        if sub.dtype == np.uint8:
            u8 = sub
        else:
            u8 = np.clip(sub * 255.0, 0, 255).astype(np.uint8)
        r = u8[:, :, 0].ravel()
        g = u8[:, :, 1].ravel()
        b = u8[:, :, 2].ravel()

        self._hist_r = np.bincount(r, minlength=256).astype(np.float32)
        self._hist_g = np.bincount(g, minlength=256).astype(np.float32)
        self._hist_b = np.bincount(b, minlength=256).astype(np.float32)
        self._hist_y = (0.2126 * self._hist_r + 0.7152 * self._hist_g + 0.0722 * self._hist_b)

        # Normalize height (ignore zero/saturated extreme spikes)
        mid_max = max(
            np.max(self._hist_r[1:255]),
            np.max(self._hist_g[1:255]),
            np.max(self._hist_b[1:255]),
            1.0,
        )
        self._max_val = mid_max * 1.05
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Background
        w = self.width()
        h = self.height()
        painter.fillRect(0, 0, w, h, QColor(16, 16, 16, 240))

        # Grid lines
        painter.setPen(QPen(QColor(40, 40, 40), 1, Qt.PenStyle.DashLine))
        painter.drawLine(w // 4, 0, w // 4, h)
        painter.drawLine(w // 2, 0, w // 2, h)
        painter.drawLine(3 * w // 4, 0, 3 * w // 4, h)
        painter.drawLine(0, h // 2, w, h // 2)

        # Draw Curves
        def make_path(data: np.ndarray) -> QPainterPath:
            path = QPainterPath()
            path.moveTo(0, h)
            for i in range(256):
                x = (i / 255.0) * w
                val = min(data[i], self._max_val)
                y = h - ((val / self._max_val) * (h - 6))
                path.lineTo(x, y)
            path.lineTo(w, h)
            return path

        # Red Channel
        painter.setPen(QPen(QColor(235, 75, 75, 200), 1.2))
        painter.setBrush(QColor(235, 75, 75, 35))
        painter.drawPath(make_path(self._hist_r))

        # Green Channel
        painter.setPen(QPen(QColor(60, 200, 90, 200), 1.2))
        painter.setBrush(QColor(60, 200, 90, 35))
        painter.drawPath(make_path(self._hist_g))

        # Blue Channel
        painter.setPen(QPen(QColor(70, 140, 240, 200), 1.2))
        painter.setBrush(QColor(70, 140, 240, 35))
        painter.drawPath(make_path(self._hist_b))

        # Border
        painter.setPen(QPen(QColor(55, 55, 55), 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(0, 0, w - 1, h - 1)


class HistogramHUD(QWidget):
    """Complete Histogram HUD panel with stats and resolution selectors."""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        # Canvas
        self.canvas = HistogramCanvas(self)
        layout.addWidget(self.canvas, 1)

        # Info readout row (Min / Max clipping values) - compact horizontal bar
        info_frame = QFrame(self)
        info_frame.setStyleSheet("background: transparent;")
        info_layout = QHBoxLayout(info_frame)
        info_layout.setContentsMargins(4, 2, 4, 2)
        info_layout.setSpacing(8)

        self.min_label = QLabel("Min: [0, 0, 0]", self)
        self.min_label.setStyleSheet("color: #888888; font-family: monospace; font-size: 10px;")
        info_layout.addWidget(self.min_label)

        info_layout.addStretch()

        self.max_label = QLabel("Max: [255, 255, 255]", self)
        self.max_label.setStyleSheet("color: #888888; font-family: monospace; font-size: 10px;")
        info_layout.addWidget(self.max_label)

        layout.addWidget(info_frame, 0)

    def set_histogram_data(
        self,
        hist_r: Optional[np.ndarray],
        hist_g: Optional[np.ndarray],
        hist_b: Optional[np.ndarray],
        hist_y: Optional[np.ndarray],
        max_val: float,
    ) -> None:
        """Set pre-computed histogram distributions asynchronously."""
        self.canvas.set_histogram_data(hist_r, hist_g, hist_b, hist_y, max_val)

    def update_frame(self, image_data: Optional[np.ndarray]) -> None:
        self.canvas.update_data(image_data)
        if image_data is not None and image_data.size > 0:
            sub = image_data[::4, ::4, :3] if image_data.ndim >= 2 else image_data
            if sub.dtype == np.uint8:
                min_vals = np.min(sub, axis=(0, 1)).astype(int)
                max_vals = np.max(sub, axis=(0, 1)).astype(int)
            else:
                min_vals = np.clip(np.min(sub, axis=(0, 1)) * 255.0, 0, 255).astype(int)
                max_vals = np.clip(np.max(sub, axis=(0, 1)) * 255.0, 0, 255).astype(int)
            self.min_label.setText(f"Min: RGB [{min_vals[0]}, {min_vals[1]}, {min_vals[2]}]")
            self.max_label.setText(f"Max: RGB [{max_vals[0]}, {max_vals[1]}, {max_vals[2]}]")
