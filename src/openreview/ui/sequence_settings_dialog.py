"""
Timeline Sequence & Project Settings Dialog.
Allows configuring canvas resolution, frame rate, working colorspace, and starting timecode.
"""
from __future__ import annotations

import logging
from typing import Optional, Dict, Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QFormLayout,
    QLabel,
    QLineEdit,
    QSpinBox,
    QDoubleSpinBox,
    QComboBox,
    QPushButton,
    QDialogButtonBox,
    QGroupBox,
)

from openreview.models.timeline_sequence import TimelineSequence

logger = logging.getLogger(__name__)


class SequenceSettingsDialog(QDialog):
    """Studio Project & Sequence Settings Modal Dialog."""
    settings_saved = Signal(dict)

    RESOLUTION_PRESETS = [
        ("1920 x 1080 (HD 1080p - 16:9)", 1920, 1080),
        ("3840 x 2160 (4K UHD - 16:9)", 3840, 2160),
        ("2048 x 1080 (2K DCI Flat - 1.85:1)", 2048, 1080),
        ("2048 x 858 (2K DCI Scope - 2.39:1)", 2048, 858),
        ("4096 x 2160 (4K DCI Flat - 1.85:1)", 4096, 2160),
        ("4096 x 1716 (4K DCI Scope - 2.39:1)", 4096, 1716),
        ("1080 x 1920 (Vertical Mobile - 9:16)", 1080, 1920),
        ("Custom Resolution", 0, 0),
    ]

    FPS_PRESETS = [
        ("23.976 fps", 23.976),
        ("24.0 fps (Cinema / VFX)", 24.0),
        ("25.0 fps (PAL / Broadcast)", 25.0),
        ("29.97 fps (NTSC Broadcast)", 29.97),
        ("30.0 fps", 30.0),
        ("48.0 fps (HFR)", 48.0),
        ("50.0 fps", 50.0),
        ("59.94 fps", 59.94),
        ("60.0 fps", 60.0),
        ("Custom", 0.0),
    ]

    COLORSPACE_PRESETS = [
        "ACEScg",
        "Rec.709",
        "sRGB",
        "DCI-P3",
        "ACEScc",
        "Linear sRGB",
        "ARRI LogC3",
        "ARRI LogC4",
        "Apple Log",
        "REDWideGamutRGB",
    ]

    def __init__(self, sequence: Optional[TimelineSequence] = None, parent: Optional[QDialog] = None):
        super().__init__(parent)
        self.sequence = sequence
        self.setWindowTitle("Sequence / Project Settings")
        self.setMinimumWidth(440)
        self.setStyleSheet("""
            QDialog {
                background-color: #1a1a1a;
                color: #e0e0e0;
            }
            QGroupBox {
                border: 1px solid #333333;
                border-radius: 4px;
                margin-top: 10px;
                font-weight: bold;
                color: #e5a823;
                background-color: #202020;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 8px;
                padding: 0 4px;
            }
            QLabel {
                color: #cccccc;
                font-size: 11px;
            }
            QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {
                background-color: #141414;
                border: 1px solid #3a3a3a;
                border-radius: 3px;
                color: #f0f0f0;
                padding: 3px 6px;
                font-size: 11px;
            }
            QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {
                border-color: #e5a823;
            }
            QPushButton {
                background-color: #2b2b2b;
                border: 1px solid #444444;
                color: #ffffff;
                border-radius: 3px;
                padding: 5px 14px;
                font-weight: bold;
                font-size: 11px;
            }
            QPushButton:hover {
                background-color: #383838;
                border-color: #e5a823;
            }
            QPushButton#btnSave {
                background-color: #342a12;
                border: 1px solid #e5a823;
                color: #e5a823;
            }
            QPushButton#btnSave:hover {
                background-color: #4a3a14;
                color: #ffffff;
            }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        # General Group
        grp_gen = QGroupBox("General & Sequence Details", self)
        form_gen = QFormLayout(grp_gen)
        form_gen.setContentsMargins(10, 14, 10, 10)
        form_gen.setSpacing(6)

        seq_name = sequence.name if sequence else "Master_Sequence"
        self.edit_name = QLineEdit(seq_name, self)
        form_gen.addRow("Sequence Name:", self.edit_name)

        self.edit_start_tc = QLineEdit("00:00:00:00", self)
        form_gen.addRow("Starting Timecode:", self.edit_start_tc)
        layout.addWidget(grp_gen)

        # Video Format & Canvas Size
        grp_vid = QGroupBox("Canvas Resolution & Format", self)
        form_vid = QFormLayout(grp_vid)
        form_vid.setContentsMargins(10, 14, 10, 10)
        form_vid.setSpacing(6)

        self.combo_res = QComboBox(self)
        cur_w = sequence.width if sequence else 1920
        cur_h = sequence.height if sequence else 1080
        matched_idx = 0
        for i, (label, w, h) in enumerate(self.RESOLUTION_PRESETS):
            self.combo_res.addItem(label, (w, h))
            if w == cur_w and h == cur_h:
                matched_idx = i

        self.combo_res.setCurrentIndex(matched_idx)
        self.combo_res.currentIndexChanged.connect(self._on_resolution_preset_changed)
        form_vid.addRow("Preset:", self.combo_res)

        res_box = QHBoxLayout()
        self.spin_w = QSpinBox(self)
        self.spin_w.setRange(128, 16384)
        self.spin_w.setValue(cur_w)
        self.spin_w.valueChanged.connect(self._on_custom_res_changed)

        self.spin_h = QSpinBox(self)
        self.spin_h.setRange(128, 16384)
        self.spin_h.setValue(cur_h)
        self.spin_h.valueChanged.connect(self._on_custom_res_changed)

        res_box.addWidget(QLabel("Width:"))
        res_box.addWidget(self.spin_w, 1)
        res_box.addWidget(QLabel("Height:"))
        res_box.addWidget(self.spin_h, 1)
        form_vid.addRow("Dimensions:", res_box)

        self.lbl_aspect = QLabel(f"Aspect: {cur_w / max(1, cur_h):.3f}:1", self)
        self.lbl_aspect.setStyleSheet("color: #888888; font-size: 10px;")
        form_vid.addRow("", self.lbl_aspect)
        layout.addWidget(grp_vid)

        # Timing & Color
        grp_timing = QGroupBox("Timing & Working Colorspace", self)
        form_timing = QFormLayout(grp_timing)
        form_timing.setContentsMargins(10, 14, 10, 10)
        form_timing.setSpacing(6)

        self.combo_fps = QComboBox(self)
        cur_fps = sequence.fps if sequence else 24.0
        fps_idx = 1
        for i, (lbl, f_val) in enumerate(self.FPS_PRESETS):
            self.combo_fps.addItem(lbl, f_val)
            if abs(f_val - cur_fps) < 0.005:
                fps_idx = i

        self.combo_fps.setCurrentIndex(fps_idx)
        self.combo_fps.currentIndexChanged.connect(self._on_fps_preset_changed)
        form_timing.addRow("Timebase (FPS):", self.combo_fps)

        self.spin_fps = QDoubleSpinBox(self)
        self.spin_fps.setRange(1.0, 240.0)
        self.spin_fps.setDecimals(3)
        self.spin_fps.setValue(cur_fps)
        form_timing.addRow("Exact FPS:", self.spin_fps)

        self.combo_cs = QComboBox(self)
        cur_cs = getattr(sequence, "colorspace", "ACEScg") or "ACEScg"
        for cs in self.COLORSPACE_PRESETS:
            self.combo_cs.addItem(cs)
        cs_idx = self.combo_cs.findText(cur_cs)
        if cs_idx >= 0:
            self.combo_cs.setCurrentIndex(cs_idx)
        form_timing.addRow("Working Colorspace:", self.combo_cs)
        layout.addWidget(grp_timing)

        # Dialog Buttons
        btn_box = QHBoxLayout()
        btn_box.addStretch()

        btn_cancel = QPushButton("Cancel", self)
        btn_cancel.clicked.connect(self.reject)
        btn_box.addWidget(btn_cancel)

        btn_save = QPushButton("Save Settings", self)
        btn_save.setObjectName("btnSave")
        btn_save.clicked.connect(self._on_save)
        btn_box.addWidget(btn_save)

        layout.addLayout(btn_box)

    def _on_resolution_preset_changed(self, idx: int) -> None:
        w, h = self.combo_res.itemData(idx)
        if w > 0 and h > 0:
            self.spin_w.blockSignals(True)
            self.spin_h.blockSignals(True)
            self.spin_w.setValue(w)
            self.spin_h.setValue(h)
            self.spin_w.blockSignals(False)
            self.spin_h.blockSignals(False)
            self.lbl_aspect.setText(f"Aspect: {w / h:.3f}:1")

    def _on_custom_res_changed(self) -> None:
        w = self.spin_w.value()
        h = max(1, self.spin_h.value())
        self.lbl_aspect.setText(f"Aspect: {w / h:.3f}:1")

    def _on_fps_preset_changed(self, idx: int) -> None:
        fps_val = self.combo_fps.itemData(idx)
        if fps_val > 0.0:
            self.spin_fps.setValue(fps_val)

    def _on_save(self) -> None:
        data = {
            "name": self.edit_name.text().strip() or "Sequence",
            "width": self.spin_w.value(),
            "height": self.spin_h.value(),
            "fps": self.spin_fps.value(),
            "colorspace": self.combo_cs.currentText(),
            "start_timecode": self.edit_start_tc.text().strip(),
        }
        if self.sequence:
            self.sequence.name = data["name"]
            self.sequence.width = data["width"]
            self.sequence.height = data["height"]
            self.sequence.fps = data["fps"]
            self.sequence.colorspace = data["colorspace"]
            self.sequence.metadata["scale_to_fit_canvas"] = True

        self.settings_saved.emit(data)
        self.accept()
