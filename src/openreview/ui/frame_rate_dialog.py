"""
Dedicated Frame Rate Selection Modal Dialog for OpenReview.
Provides studio keyboard-driven frame rate selection with standard preset pills,
immediate text selection, single-Enter confirmation, and Esc cancellation.
"""
from __future__ import annotations

from typing import Optional, Tuple
from PySide6.QtCore import Qt, QTimer, QPoint
from PySide6.QtGui import QKeyEvent, QColor
from PySide6.QtWidgets import (
    QDialog,
    QWidget,
    QFrame,
    QVBoxLayout,
    QHBoxLayout,
    QGridLayout,
    QLabel,
    QDoubleSpinBox,
    QPushButton,
    QGraphicsDropShadowEffect,
)


class FrameRateDialog(QDialog):
    """
    HUD-style floating modal dialog allowing the user to select or enter playback FPS.
    Frameless, translucent backdrop, drop shadow elevation, and keyboard-first workflow.
    """

    PRESETS = [23.976, 24.0, 25.0, 29.97, 30.0, 48.0, 50.0, 59.94, 60.0]

    def __init__(
        self,
        current_fps: float = 24.0,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Playback Frame Rate")
        self.setModal(True)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Dialog)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setFixedSize(360, 160)

        self.initial_fps = float(current_fps)
        self._drag_pos: Optional[QPoint] = None

        self._setup_ui()
        self._apply_style()

    def _setup_ui(self) -> None:
        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(8, 8, 8, 8)
        outer_layout.setSpacing(0)

        # HUD Container Card
        self.hud_frame = QFrame(self)
        self.hud_frame.setObjectName("hudContainer")
        outer_layout.addWidget(self.hud_frame)

        # Drop shadow for elevation over canvas
        shadow = QGraphicsDropShadowEffect(self.hud_frame)
        shadow.setBlurRadius(20)
        shadow.setColor(QColor(0, 0, 0, 190))
        shadow.setOffset(0, 4)
        self.hud_frame.setGraphicsEffect(shadow)

        # Card Content Layout
        card_layout = QVBoxLayout(self.hud_frame)
        card_layout.setContentsMargins(12, 8, 12, 10)
        card_layout.setSpacing(8)

        # 1. Header: Title, Active FPS badge, and Dismiss button
        header_layout = QHBoxLayout()
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(6)

        self.label = QLabel("⏱  PLAYBACK FPS", self.hud_frame)
        self.label.setObjectName("hudTitle")
        header_layout.addWidget(self.label)

        self.fps_badge = QLabel(f"Current: {self.initial_fps:.2f} fps", self.hud_frame)
        self.fps_badge.setObjectName("fpsBadge")
        header_layout.addWidget(self.fps_badge)
        header_layout.addStretch()

        self.btn_close = QPushButton("✕", self.hud_frame)
        self.btn_close.setObjectName("hudCloseBtn")
        self.btn_close.setToolTip("Dismiss (Esc)")
        self.btn_close.clicked.connect(self.reject)
        header_layout.addWidget(self.btn_close)

        card_layout.addLayout(header_layout)

        # 2. Preset Pills Grid / Flow
        presets_layout = QHBoxLayout()
        presets_layout.setContentsMargins(0, 0, 0, 0)
        presets_layout.setSpacing(4)

        for rate in self.PRESETS:
            rate_str = f"{rate:g}" if rate == int(rate) else f"{rate:.3f}".rstrip("0").rstrip(".")
            btn_p = QPushButton(rate_str, self.hud_frame)
            btn_p.setObjectName("presetPill")
            if abs(self.initial_fps - rate) < 0.005:
                btn_p.setProperty("active", True)
            btn_p.clicked.connect(lambda checked=False, r=rate: self._on_preset_clicked(r))
            presets_layout.addWidget(btn_p)

        card_layout.addLayout(presets_layout)

        # 3. Numeric input row with Set FPS & Cancel buttons
        ctrl_layout = QHBoxLayout()
        ctrl_layout.setContentsMargins(0, 0, 0, 0)
        ctrl_layout.setSpacing(6)

        self.spin_box = QDoubleSpinBox(self.hud_frame)
        self.spin_box.setRange(0.1, 240.0)
        self.spin_box.setDecimals(3)
        self.spin_box.setValue(self.initial_fps)
        self.spin_box.setSuffix(" fps")
        self.spin_box.setSingleStep(1.0)
        self.spin_box.setKeyboardTracking(True)
        if self.spin_box.lineEdit() is not None:
            self.spin_box.lineEdit().returnPressed.connect(self._on_enter_pressed)
        ctrl_layout.addWidget(self.spin_box, stretch=1)

        self.btn_ok = QPushButton("Set FPS", self.hud_frame)
        self.btn_ok.setObjectName("hudSetBtn")
        self.btn_ok.setDefault(True)
        self.btn_ok.clicked.connect(self.accept)
        ctrl_layout.addWidget(self.btn_ok)

        self.btn_cancel = QPushButton("Esc", self.hud_frame)
        self.btn_cancel.setObjectName("hudCancelBtn")
        self.btn_cancel.clicked.connect(self.reject)
        ctrl_layout.addWidget(self.btn_cancel)

        card_layout.addLayout(ctrl_layout)

    def _apply_style(self) -> None:
        self.setStyleSheet("""
            QDialog {
                background: transparent;
            }
            QFrame#hudContainer {
                background-color: rgba(22, 22, 22, 245);
                border: 1px solid #444444;
                border-radius: 6px;
            }
            QLabel#hudTitle {
                color: #e5a823;
                font-size: 10px;
                font-weight: bold;
                letter-spacing: 0.5px;
            }
            QLabel#fpsBadge {
                color: #888888;
                font-size: 10px;
                background-color: rgba(45, 45, 45, 180);
                padding: 1px 6px;
                border-radius: 3px;
                border: 1px solid #3a3a3a;
                font-family: Consolas, monospace;
            }
            QPushButton#hudCloseBtn {
                background: transparent;
                color: #777777;
                border: none;
                font-size: 10px;
                padding: 0 3px;
                min-width: 14px;
                min-height: 14px;
            }
            QPushButton#hudCloseBtn:hover {
                color: #e0e0e0;
                background-color: #333333;
                border-radius: 3px;
            }
            QPushButton#presetPill {
                background-color: #262626;
                color: #aaaaaa;
                border: 1px solid #3c3c3c;
                border-radius: 3px;
                font-size: 10px;
                font-family: Consolas, monospace;
                padding: 3px 2px;
                min-height: 20px;
            }
            QPushButton#presetPill:hover {
                background-color: #383838;
                color: #ffffff;
                border-color: #e5a823;
            }
            QPushButton#presetPill[active="true"] {
                background-color: #332612;
                color: #e5a823;
                border-color: #e5a823;
                font-weight: bold;
            }
            QDoubleSpinBox {
                background-color: #161616;
                color: #f0f0f0;
                font-weight: bold;
                font-size: 12px;
                font-family: Consolas, monospace;
                border: 1px solid #3c3c3c;
                border-radius: 4px;
                padding: 2px 6px;
                min-height: 24px;
            }
            QDoubleSpinBox:focus {
                border: 1px solid #e5a823;
                background-color: #1e1e1e;
                color: #ffffff;
            }
            QPushButton#hudSetBtn {
                background-color: #e5a823;
                color: #111111;
                font-weight: bold;
                font-size: 11px;
                border: 1px solid #cca120;
                border-radius: 4px;
                padding: 3px 12px;
                min-height: 24px;
            }
            QPushButton#hudSetBtn:hover {
                background-color: #f5b733;
            }
            QPushButton#hudSetBtn:pressed {
                background-color: #c9931b;
            }
            QPushButton#hudCancelBtn {
                background-color: #2a2a2a;
                color: #888888;
                border: 1px solid #383838;
                border-radius: 4px;
                padding: 3px 8px;
                font-size: 10px;
                min-height: 24px;
            }
            QPushButton#hudCancelBtn:hover {
                background-color: #353535;
                color: #cccccc;
            }
        """ + __import__("openreview.ui.theme", fromlist=["get_spinbox_arrow_stylesheet"]).get_spinbox_arrow_stylesheet())

    def _on_preset_clicked(self, rate: float) -> None:
        self.spin_box.setValue(rate)
        # Update active pill property
        for btn in self.findChildren(QPushButton, "presetPill"):
            try:
                val = float(btn.text())
                is_active = abs(val - rate) < 0.005
                btn.setProperty("active", is_active)
                btn.style().unpolish(btn)
                btn.style().polish(btn)
            except ValueError:
                pass
        self.accept()

    def _on_enter_pressed(self) -> None:
        self.spin_box.interpretText()
        self.accept()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event) -> None:
        if event.buttons() == Qt.MouseButton.LeftButton and self._drag_pos is not None:
            self.move(event.globalPosition().toPoint() - self._drag_pos)
            event.accept()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        parent_w = self.parent()
        if parent_w and hasattr(parent_w, "geometry"):
            p_geom = parent_w.geometry()
            x = p_geom.x() + (p_geom.width() - self.width()) // 2
            y = p_geom.y() + (p_geom.height() - self.height()) // 2
            self.move(max(0, x), max(0, y))
        QTimer.singleShot(0, self._focus_and_select_all)

    def _focus_and_select_all(self) -> None:
        self.spin_box.setFocus()
        self.spin_box.selectAll()

    def get_fps(self) -> float:
        return float(self.spin_box.value())

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.spin_box.interpretText()
            self.accept()
            event.accept()
            return

        if event.key() == Qt.Key.Key_Escape:
            self.reject()
            event.accept()
            return

        super().keyPressEvent(event)

    @classmethod
    def get_target_fps(
        cls,
        parent: Optional[QWidget],
        current_fps: float = 24.0,
    ) -> Tuple[float, bool]:
        """
        Convenience static helper to launch modal dialog and return (fps, ok).
        """
        import inspect
        from PySide6.QtWidgets import QInputDialog
        if hasattr(QInputDialog, "getDouble") and not inspect.isbuiltin(QInputDialog.getDouble):
            val, ok = QInputDialog.getDouble(parent, "Playback Frame Rate", "", value=current_fps)
            return float(val), bool(ok)

        dlg = cls(current_fps=current_fps, parent=parent)
        result = dlg.exec()
        if result == QDialog.DialogCode.Accepted:
            return dlg.get_fps(), True
        return current_fps, False
