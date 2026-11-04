"""
Live Real-Time Waveform Monitor HUD Widget.
Supports RGB Overlay, RGB Parade, and Luma (Y) modes with phosphor intensity glow.
"""
from __future__ import annotations

from typing import Optional
import numpy as np
import cv2
from PySide6.QtCore import Qt, QRectF
from PySide6.QtGui import QPainter, QColor, QPen, QImage, QFont
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QComboBox,
    QFrame,
    QSizePolicy,
)


class WaveformCanvas(QWidget):
    """Custom-painted Waveform monitor canvas with phosphor trace accumulation."""

    MODE_OVERLAY = "RGB Overlay"
    MODE_PARADE = "RGB Parade"
    MODE_LUMA = "Luma (Y)"

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setMinimumSize(200, 100)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._mode: str = self.MODE_OVERLAY
        self._trace_img: Optional[QImage] = None

        # Layout metrics
        self._m_left = 32
        self._m_right = 6
        self._m_top = 8
        self._m_bottom = 14

    @property
    def _plot_w(self) -> int:
        return max(10, self.width() - self._m_left - self._m_right)

    @property
    def _plot_h(self) -> int:
        return max(10, self.height() - self._m_top - self._m_bottom)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self.update()

    def set_mode(self, mode: str) -> None:
        if self._mode != mode:
            self._mode = mode
            self.update()

    def set_trace_image(self, img: Optional[QImage]) -> None:
        """Set pre-computed trace image from asynchronous compute worker (thread-safe UI update)."""
        self._trace_img = img
        self.update()

    def update_data(self, image_data: Optional[np.ndarray]) -> None:
        if image_data is not None and image_data.ndim >= 2:
            ih, iw = image_data.shape[:2]
            step = 4 if (iw >= 3840 or ih >= 2160) else (2 if (iw >= 1920 or ih >= 1080) else 1)
            if step > 1:
                image_data = image_data[::step, ::step]
        if image_data is None or image_data.size == 0:
            self._trace_img = None
            self.update()
            return

        # Handle data types: normalize to uint8
        if image_data.dtype == np.uint8:
            u8 = image_data[:, :, :3]
        else:
            u8 = np.clip(image_data[:, :, :3] * 255.0, 0, 255).astype(np.uint8)

        pw = self._plot_w
        ph = self._plot_h

        if self._mode == self.MODE_PARADE:
            w_ch = pw // 3
            # Downsample to 3 columns per channel and reasonable row count
            samp = cv2.resize(u8, (w_ch, 120), interpolation=cv2.INTER_AREA)
            r = samp[:, :, 0]
            g = samp[:, :, 1]
            b = samp[:, :, 2]

            rgba = np.zeros((ph, pw, 4), dtype=np.uint8)

            def build_ch(data: np.ndarray, x_offset: int, color_idx: int) -> None:
                y = (ph - 1) - (data.astype(np.float32) / 255.0 * (ph - 1)).astype(np.int32)
                x = np.broadcast_to(np.arange(w_ch) + x_offset, y.shape).ravel()
                acc = np.zeros((ph, pw), dtype=np.float32)
                np.add.at(acc, (y.ravel(), x), 1.0)
                intensity = np.clip(acc * 22.0, 0, 255).astype(np.uint8)
                rgba[:, :, color_idx] = np.maximum(rgba[:, :, color_idx], intensity)
                rgba[:, :, 3] = np.maximum(rgba[:, :, 3], np.clip(intensity.astype(int) * 1.4, 0, 245).astype(np.uint8))

            build_ch(r, 0, 0)
            build_ch(g, w_ch, 1)
            build_ch(b, w_ch * 2, 2)

            self._trace_img = QImage(rgba.data, pw, ph, pw * 4, QImage.Format.Format_RGBA8888).copy()

        elif self._mode == self.MODE_LUMA:
            samp = cv2.resize(u8, (pw, 120), interpolation=cv2.INTER_AREA)
            r = samp[:, :, 0].astype(np.float32)
            g = samp[:, :, 1].astype(np.float32)
            b = samp[:, :, 2].astype(np.float32)
            y_val = 0.2126 * r + 0.7152 * g + 0.0722 * b

            y_pos = (ph - 1) - (y_val / 255.0 * (ph - 1)).astype(np.int32)
            x_pos = np.broadcast_to(np.arange(pw), y_pos.shape).ravel()
            acc = np.zeros((ph, pw), dtype=np.float32)
            np.add.at(acc, (y_pos.ravel(), x_pos), 1.0)

            intensity = np.clip(acc * 22.0, 0, 255).astype(np.uint8)
            rgba = np.zeros((ph, pw, 4), dtype=np.uint8)
            # Cool phosphor green/cyan glow
            rgba[:, :, 0] = (intensity * 0.45).astype(np.uint8)
            rgba[:, :, 1] = intensity
            rgba[:, :, 2] = (intensity * 0.75).astype(np.uint8)
            rgba[:, :, 3] = np.clip(intensity.astype(int) * 1.5, 0, 245).astype(np.uint8)

            self._trace_img = QImage(rgba.data, pw, ph, pw * 4, QImage.Format.Format_RGBA8888).copy()

        else:  # RGB Overlay
            samp = cv2.resize(u8, (pw, 120), interpolation=cv2.INTER_AREA)
            r = samp[:, :, 0]
            g = samp[:, :, 1]
            b = samp[:, :, 2]

            y_r = (ph - 1) - (r.astype(np.float32) / 255.0 * (ph - 1)).astype(np.int32)
            y_g = (ph - 1) - (g.astype(np.float32) / 255.0 * (ph - 1)).astype(np.int32)
            y_b = (ph - 1) - (b.astype(np.float32) / 255.0 * (ph - 1)).astype(np.int32)

            x_pos = np.broadcast_to(np.arange(pw), y_r.shape).ravel()

            acc_r = np.zeros((ph, pw), dtype=np.float32)
            acc_g = np.zeros((ph, pw), dtype=np.float32)
            acc_b = np.zeros((ph, pw), dtype=np.float32)

            np.add.at(acc_r, (y_r.ravel(), x_pos), 1.0)
            np.add.at(acc_g, (y_g.ravel(), x_pos), 1.0)
            np.add.at(acc_b, (y_b.ravel(), x_pos), 1.0)

            r_u8 = np.clip(acc_r * 22.0, 0, 255).astype(np.uint8)
            g_u8 = np.clip(acc_g * 22.0, 0, 255).astype(np.uint8)
            b_u8 = np.clip(acc_b * 22.0, 0, 255).astype(np.uint8)

            rgba = np.zeros((ph, pw, 4), dtype=np.uint8)
            rgba[:, :, 0] = r_u8
            rgba[:, :, 1] = g_u8
            rgba[:, :, 2] = b_u8
            # Phosphor composite brightness
            alpha = np.clip(r_u8.astype(int) + g_u8.astype(int) + b_u8.astype(int), 0, 245).astype(np.uint8)
            rgba[:, :, 3] = alpha

            self._trace_img = QImage(rgba.data, pw, ph, pw * 4, QImage.Format.Format_RGBA8888).copy()

        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        w = self.width()
        h = self.height()

        # Dark scope canvas background
        painter.fillRect(0, 0, w, h, QColor(16, 16, 16, 240))

        # Scale levels & graticule
        levels = [0.0, 0.25, 0.5, 0.75, 1.0]
        font = QFont("Monospace", 7)
        font.setStyleHint(QFont.StyleHint.Monospace)
        painter.setFont(font)

        pw = self._plot_w
        ph = self._plot_h
        x_start = self._m_left
        y_top = self._m_top

        for lev in levels:
            y = int(y_top + ph - (lev * ph))
            # Graticule dashed line
            if lev in (0.0, 1.0):
                painter.setPen(QPen(QColor(60, 60, 60), 1, Qt.PenStyle.SolidLine))
            else:
                painter.setPen(QPen(QColor(40, 40, 40), 1, Qt.PenStyle.DashLine))
            painter.drawLine(x_start, y, x_start + pw, y)

            # Label on left margin
            painter.setPen(QPen(QColor(110, 110, 110), 1))
            label_text = f"{int(lev * 100)}%"
            painter.drawText(2, y + 3, self._m_left - 4, 12, Qt.AlignmentFlag.AlignRight, label_text)

        # Parade separator vertical lines
        if self._mode == self.MODE_PARADE:
            w_ch = pw // 3
            painter.setPen(QPen(QColor(55, 55, 55), 1, Qt.PenStyle.DashLine))
            painter.drawLine(x_start + w_ch, y_top, x_start + w_ch, y_top + ph)
            painter.drawLine(x_start + w_ch * 2, y_top, x_start + w_ch * 2, y_top + ph)

            # Labels for channels
            painter.setPen(QPen(QColor(230, 80, 80), 1))
            painter.drawText(x_start + 4, y_top + 12, "R")
            painter.setPen(QPen(QColor(70, 210, 90), 1))
            painter.drawText(x_start + w_ch + 4, y_top + 12, "G")
            painter.setPen(QPen(QColor(80, 150, 245), 1))
            painter.drawText(x_start + w_ch * 2 + 4, y_top + 12, "B")

        # Paint phosphor trace
        if self._trace_img is not None and not self._trace_img.isNull():
            target_rect = QRectF(x_start, y_top, pw, ph)
            painter.drawImage(target_rect, self._trace_img)

        # Plot border
        painter.setPen(QPen(QColor(50, 50, 50), 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(x_start, y_top, pw, ph)

        # Outer border
        painter.setPen(QPen(QColor(42, 42, 42), 1))
        painter.drawRect(0, 0, w - 1, h - 1)


class WaveformHUD(QWidget):
    """Complete Waveform Monitor HUD panel with mode selector and IRE scale readouts."""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._last_frame_data: Optional[np.ndarray] = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        # Header controls
        hdr_layout = QHBoxLayout()
        hdr_layout.setContentsMargins(2, 0, 2, 0)
        hdr_layout.setSpacing(6)

        hdr_label = QLabel("Mode:", self)
        hdr_label.setStyleSheet("color: #888888; font-size: 10px; font-weight: bold;")
        hdr_layout.addWidget(hdr_label)

        self.mode_combo = QComboBox(self)
        self.mode_combo.addItem("RGB Overlay", WaveformCanvas.MODE_OVERLAY)
        self.mode_combo.addItem("RGB Parade", WaveformCanvas.MODE_PARADE)
        self.mode_combo.addItem("Luma (Y)", WaveformCanvas.MODE_LUMA)
        self.mode_combo.setFixedHeight(20)
        self.mode_combo.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.mode_combo.setStyleSheet("""
            QComboBox {
                font-size: 10px;
                background-color: #222222;
                color: #cccccc;
                border: 1px solid #3c3c3c;
                border-radius: 2px;
                padding: 1px 4px;
            }
            QComboBox::drop-down {
                border: none;
                width: 14px;
            }
            QComboBox QAbstractItemView {
                background-color: #1e1e1e;
                color: #dddddd;
                selection-background-color: #2d5a88;
                border: 1px solid #444444;
            }
        """)
        self.mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        hdr_layout.addWidget(self.mode_combo)
        hdr_layout.addStretch()

        layout.addLayout(hdr_layout)

        # Canvas
        self.canvas = WaveformCanvas(self)
        layout.addWidget(self.canvas, 1)

        # Bottom info status bar
        info_frame = QFrame(self)
        info_layout = QHBoxLayout(info_frame)
        info_layout.setContentsMargins(2, 0, 2, 0)
        info_layout.setSpacing(4)

        self.info_label = QLabel("IRE: 0% - 100% | Full Range", self)
        self.info_label.setStyleSheet("color: #777777; font-family: monospace; font-size: 9.5px;")
        info_layout.addWidget(self.info_label)
        layout.addWidget(info_frame, 0)

    def _on_mode_changed(self, index: int) -> None:
        mode = self.mode_combo.itemData(index)
        if mode:
            self.canvas.set_mode(mode)
            if self._last_frame_data is not None:
                self.canvas.update_data(self._last_frame_data)

    @property
    def _mode(self) -> str:
        return self.canvas._mode

    def set_trace_image(self, img: Optional[QImage]) -> None:
        """Set pre-computed trace image asynchronously."""
        self.canvas.set_trace_image(img)
        if img is None:
            self.info_label.setText("IRE: 0% - 100% | No Signal")
        else:
            self.info_label.setText("IRE: 0% - 100% | Active Signal")

    def update_frame(self, image_data: Optional[np.ndarray]) -> None:
        self._last_frame_data = image_data
        self.canvas.update_data(image_data)
        if image_data is None or image_data.size == 0:
            self.info_label.setText("IRE: 0% - 100% | No Signal")
        else:
            self.info_label.setText("IRE: 0% - 100% | Active Signal")
