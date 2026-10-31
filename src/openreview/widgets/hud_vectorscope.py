"""
Live Real-Time Vectorscope Monitor HUD Widget.
Displays chrominance (Cb, Cr) signal with Rec.709 75%/100% targets, skin-tone line, and phosphor glow.
"""
from __future__ import annotations

import math
from typing import Optional
import numpy as np
import cv2
from PySide6.QtCore import Qt, QRectF, QPointF
from PySide6.QtGui import QPainter, QColor, QPen, QImage, QFont
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QFrame,
)


class VectorscopeCanvas(QWidget):
    """Custom-painted Vectorscope canvas with Rec.709 targets, skin tone line, and phosphor trace."""

    # Rec.709 targets (Cb, Cr)
    TARGETS = {
        "R": (-0.1146, 0.5000, QColor(235, 75, 75)),
        "Mg": (0.3854, 0.4542, QColor(220, 80, 220)),
        "B": (0.5000, -0.0458, QColor(70, 140, 240)),
        "Cy": (0.1146, -0.5000, QColor(60, 210, 210)),
        "G": (-0.3854, -0.4542, QColor(60, 200, 90)),
        "Yl": (-0.5000, 0.0458, QColor(230, 210, 60)),
    }

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setFixedSize(224, 224)
        self._trace_img: Optional[QImage] = None

        self._cx = 112.0
        self._cy = 112.0
        self._r_100 = 88.0
        self._r_75 = 66.0
        self._scale = 146.0

    def update_data(self, image_data: Optional[np.ndarray]) -> None:
        if image_data is None or image_data.size == 0:
            self._trace_img = None
            self.update()
            return

        w = self.width()
        h = self.height()

        # Handle uint8 vs float
        if image_data.dtype == np.uint8:
            u8 = image_data[:, :, :3]
        else:
            u8 = np.clip(image_data[:, :, :3] * 255.0, 0, 255).astype(np.uint8)

        # Downsample to 140x140 for fast 60fps chrominance accumulation
        samp = cv2.resize(u8, (140, 140), interpolation=cv2.INTER_AREA)
        r = samp[:, :, 0].astype(np.float32) / 255.0
        g = samp[:, :, 1].astype(np.float32) / 255.0
        b = samp[:, :, 2].astype(np.float32) / 255.0

        # Rec.709 Luminance and Chrominance (Cb, Cr)
        y = 0.2126 * r + 0.7152 * g + 0.0722 * b
        cb = (b - y) / 1.8556
        cr = (r - y) / 1.5748

        # Map to canvas coordinates
        px = np.clip(np.round(self._cx + cb * self._scale).astype(int), 0, w - 1).ravel()
        py = np.clip(np.round(self._cy - cr * self._scale).astype(int), 0, h - 1).ravel()

        acc = np.zeros((h, w), dtype=np.float32)
        np.add.at(acc, (py, px), 1.0)

        # Phosphor trace intensity glow
        intensity = np.clip(acc * 24.0, 0, 255).astype(np.uint8)
        rgba = np.zeros((h, w, 4), dtype=np.uint8)
        rgba[:, :, 0] = (intensity * 0.40).astype(np.uint8)
        rgba[:, :, 1] = intensity
        rgba[:, :, 2] = (intensity * 0.70).astype(np.uint8)
        rgba[:, :, 3] = np.clip(intensity.astype(int) * 1.5, 0, 245).astype(np.uint8)

        self._trace_img = QImage(rgba.data, w, h, w * 4, QImage.Format.Format_RGBA8888).copy()
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        w = self.width()
        h = self.height()
        cx = self._cx
        cy = self._cy

        # Dark background
        painter.fillRect(0, 0, w, h, QColor(16, 16, 16, 240))

        # Circular graticules
        # 100% saturation circle
        painter.setPen(QPen(QColor(55, 55, 55), 1, Qt.PenStyle.SolidLine))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(QPointF(cx, cy), self._r_100, self._r_100)

        # 75% saturation circle
        painter.setPen(QPen(QColor(42, 42, 42), 1, Qt.PenStyle.DashLine))
        painter.drawEllipse(QPointF(cx, cy), self._r_75, self._r_75)

        # Center Crosshair
        painter.setPen(QPen(QColor(48, 48, 48), 1, Qt.PenStyle.DashLine))
        painter.drawLine(int(cx - self._r_100), int(cy), int(cx + self._r_100), int(cy))
        painter.drawLine(int(cx), int(cy - self._r_100), int(cx), int(cy + self._r_100))

        # Skin tone reference line (I-axis, ~123 deg in upper-left quadrant)
        rad_skin = math.radians(123.0)
        skin_x = cx + self._r_100 * math.cos(rad_skin)
        skin_y = cy - self._r_100 * math.sin(rad_skin)
        painter.setPen(QPen(QColor(215, 145, 80, 180), 1, Qt.PenStyle.DashLine))
        painter.drawLine(QPointF(cx, cy), QPointF(skin_x, skin_y))

        # Skin line label
        font = QFont("Monospace", 7)
        font.setStyleHint(QFont.StyleHint.Monospace)
        painter.setFont(font)
        painter.setPen(QPen(QColor(215, 145, 80), 1))
        painter.drawText(int(skin_x - 14), int(skin_y - 2), "Skin")

        # Draw Rec.709 Target Boxes (75% and 100%)
        box_font = QFont("Monospace", 7, QFont.Weight.Bold)
        painter.setFont(box_font)

        for name, (t_cb, t_cr, color) in self.TARGETS.items():
            # 75% target position
            x75 = cx + (t_cb * 0.75) * self._scale
            y75 = cy - (t_cr * 0.75) * self._scale

            # 100% target position
            x100 = cx + t_cb * self._scale
            y100 = cy - t_cr * self._scale

            # 75% Target Box
            painter.setPen(QPen(QColor(80, 80, 80), 1))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(QRectF(x75 - 4, y75 - 4, 8, 8))

            # 100% Target Cross/Box
            painter.setPen(QPen(QColor(55, 55, 55), 1))
            painter.drawRect(QRectF(x100 - 3, y100 - 3, 6, 6))

            # Label near 75% box
            painter.setPen(QPen(color, 1))
            lbl_x = x75 + (5 if t_cb >= 0 else -18)
            lbl_y = y75 + (10 if t_cr <= 0 else -4)
            painter.drawText(int(lbl_x), int(lbl_y), name)

        # Draw signal phosphor trace
        if self._trace_img is not None and not self._trace_img.isNull():
            painter.drawImage(0, 0, self._trace_img)

        # Outer border
        painter.setPen(QPen(QColor(42, 42, 42), 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(0, 0, w - 1, h - 1)


class VectorscopeHUD(QWidget):
    """Complete Vectorscope Monitor HUD panel with graticule and skin-tone reference."""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        # Canvas
        self.canvas = VectorscopeCanvas(self)
        layout.addWidget(self.canvas)

        # Bottom info status bar
        info_frame = QFrame(self)
        info_layout = QHBoxLayout(info_frame)
        info_layout.setContentsMargins(2, 0, 2, 0)
        info_layout.setSpacing(4)

        self.info_label = QLabel("Rec.709 | 75% & 100% Targets | Skin Line", self)
        self.info_label.setStyleSheet("color: #777777; font-family: monospace; font-size: 9.5px;")
        info_layout.addWidget(self.info_label)
        layout.addWidget(info_frame)

    def update_frame(self, image_data: Optional[np.ndarray]) -> None:
        self.canvas.update_data(image_data)
