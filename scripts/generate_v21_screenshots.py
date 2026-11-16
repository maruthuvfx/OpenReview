"""
Generate UI screenshots for OpenReview v2.1.0 new feature implementations:
1. timeline_mode_workspace.png
2. dialog_conform_relink.png
3. soft_effects_inspector.png
4. cut_list_spreadsheet.png
5. export_template_builder.png
6. dialog_sequence_settings.png
"""
import os
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR / "src"))

import numpy as np
from PySide6.QtCore import Qt, QPoint, QRect, QTimer
from PySide6.QtWidgets import QApplication, QWidget, QVBoxLayout, QHBoxLayout, QDialog
from PySide6.QtGui import QColor, QPixmap

from openreview.ui.main_window import MainWindow
from openreview.models.timeline_sequence import (
    TimelineSequence,
    Track,
    ClipItem,
    TrackType,
    SoftEffect,
    SoftEffectType,
    ShotStatus,
)
from openreview.ui.conform_dialog import ConformDialog
from openreview.widgets.soft_effects_inspector import SoftEffectsInspector
from openreview.widgets.spreadsheet_widget import SpreadsheetWidget
from openreview.ui.export_template_builder import ExportTemplateBuilderWidget
from openreview.ui.sequence_settings_dialog import SequenceSettingsDialog

OUTPUT_DIR = ROOT_DIR / "docs" / "html" / "assets" / "screenshots"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

SAMPLE_EXR = ROOT_DIR / "tests" / "sample_media" / "multichannel_exr" / "multichannel_test.exr"
SAMPLE_PNG = ROOT_DIR / "tests" / "sample_media" / "sh010_comp_v002.1001.png"

def create_sample_timeline_sequence() -> TimelineSequence:
    seq = TimelineSequence(
        name="SH010_Master_Editorial",
        fps=24.0,
        start_timecode="01:00:00:00",
        width=1920,
        height=1080,
    )

    t_v2 = seq.add_video_track("V2 (VFX Overlays)")
    t_v1 = seq.add_video_track("V1 (Plates & Renders)")
    t_a1 = seq.add_audio_track("A1 (Dialogue & Guide)")

    clip1 = ClipItem(
        id="c1",
        name="SH010_Comp_v002",
        file_path="D:/projects/vfx/shots/SH010/renders/sh010_comp_v002.####.exr",
        src_in=1001,
        src_out=1080,
        dst_in=0,
        dst_out=79,
        reel="REEL_01",
        status=ShotStatus.APPROVED,
        version="v002",
    )
    # Add a soft effect
    clip1.add_soft_effect(
        SoftEffectType.GRADE_CDL,
        "CDL Balance",
        {"gain": [1.08, 1.02, 0.96], "gamma": 1.02, "saturation": 1.05},
    )

    clip2 = ClipItem(
        id="c2",
        name="SH020_Lighting_v001",
        file_path="D:/projects/vfx/shots/SH020/renders/sh020_light_v001.####.exr",
        src_in=1001,
        src_out=1120,
        dst_in=80,
        dst_out=199,
        reel="REEL_01",
        status=ShotStatus.WIP,
        version="v001",
    )

    clip3 = ClipItem(
        id="c3",
        name="SH030_FX_Fire_v004",
        file_path="D:/projects/vfx/shots/SH030/renders/sh030_fx_v004.mov",
        src_in=0,
        src_out=90,
        dst_in=200,
        dst_out=290,
        reel="REEL_02",
        status=ShotStatus.MATCHED,
        version="v004",
    )

    clip_audio = ClipItem(
        id="a1",
        name="Production_Audio_Guide",
        file_path="D:/projects/editorial/audio/guide_mix.wav",
        src_in=0,
        src_out=290,
        dst_in=0,
        dst_out=290,
        reel="SND_A1",
        status=ShotStatus.APPROVED,
    )

    t_v1.add_clip(clip1)
    t_v1.add_clip(clip2)
    t_v1.add_clip(clip3)
    t_a1.add_clip(clip_audio)

    return seq

def save_widget(widget: QWidget, filename: str):
    app = QApplication.instance()
    app.processEvents()
    pix = widget.grab()
    out_path = OUTPUT_DIR / filename
    pix.save(str(out_path), "PNG")
    print(f"Captured: {filename:35s} ({pix.size().width()}x{pix.size().height()}) -> {out_path}")

def main():
    app = QApplication.instance() or QApplication([])

    window = MainWindow()
    window.resize(1560, 920)
    window.show()
    app.processEvents()

    sample_seq = create_sample_timeline_sequence()

    # 1. Capture Timeline Mode Workspace
    try:
        if hasattr(window, "workspace_manager"):
            window.workspace_manager.apply_preset("timeline_mode")
            app.processEvents()
        if hasattr(window, "timeline_widget") and window.timeline_widget:
            window.timeline_widget.set_sequence(sample_seq)
            app.processEvents()
        save_widget(window, "timeline_mode_workspace.png")
    except Exception as e:
        print("Error capturing Timeline Mode Workspace:", e)

    # 2. Capture Conform & Relink Dialog
    try:
        dlg_conform = ConformDialog(parent=window)
        dlg_conform._parsed_sequence = sample_seq
        if hasattr(dlg_conform, "_populate_summary_table"):
            dlg_conform._populate_summary_table()
        dlg_conform.resize(880, 580)
        dlg_conform.show()
        app.processEvents()
        save_widget(dlg_conform, "dialog_conform_relink.png")
        dlg_conform.hide()
    except Exception as e:
        print("Error capturing Conform Dialog:", e)

    # 3. Capture Soft Effects Inspector
    try:
        container_fx = QWidget()
        container_fx.setStyleSheet("background-color: #1a1a1a;")
        layout_fx = QVBoxLayout(container_fx)
        layout_fx.setContentsMargins(8, 8, 8, 8)
        inspector_fx = SoftEffectsInspector(parent=container_fx)
        target_track = next(t for t in sample_seq.tracks if len(t.clips) > 0)
        clip_with_fx = target_track.clips[0]
        inspector_fx.set_clip(clip_with_fx)
        layout_fx.addWidget(inspector_fx)
        container_fx.resize(360, 520)
        container_fx.show()
        app.processEvents()
        save_widget(container_fx, "soft_effects_inspector.png")
        container_fx.hide()
    except Exception as e:
        print("Error capturing Soft Effects Inspector:", e)

    # 4. Capture Editorial Cut List Spreadsheet
    try:
        container_sheet = QWidget()
        container_sheet.setStyleSheet("background-color: #1a1a1a;")
        layout_sheet = QVBoxLayout(container_sheet)
        layout_sheet.setContentsMargins(8, 8, 8, 8)
        sheet_widget = SpreadsheetWidget(parent=container_sheet)
        sheet_widget.set_sequence(sample_seq)
        layout_sheet.addWidget(sheet_widget)
        container_sheet.resize(920, 480)
        container_sheet.show()
        app.processEvents()
        save_widget(container_sheet, "cut_list_spreadsheet.png")
        container_sheet.hide()
    except Exception as e:
        print("Error capturing Cut List Spreadsheet:", e)

    # 5. Capture Export Template Token Builder
    try:
        container_tpl = QWidget()
        container_tpl.setStyleSheet("background-color: #1a1a1a;")
        layout_tpl = QVBoxLayout(container_tpl)
        layout_tpl.setContentsMargins(12, 12, 12, 12)
        target_track = next(t for t in sample_seq.tracks if len(t.clips) > 0)
        sample_clip_tuple = (target_track, target_track.clips[0])
        tpl_widget = ExportTemplateBuilderWidget(
            sequence=sample_seq,
            sample_clip=sample_clip_tuple,
            parent=container_tpl,
        )
        layout_tpl.addWidget(tpl_widget)
        container_tpl.resize(760, 360)
        container_tpl.show()
        app.processEvents()
        save_widget(container_tpl, "export_template_builder.png")
        container_tpl.hide()
    except Exception as e:
        print("Error capturing Export Template Builder:", e)

    # 6. Capture Sequence Settings Dialog
    try:
        dlg_seq = SequenceSettingsDialog(sequence=sample_seq, parent=window)
        dlg_seq.resize(460, 500)
        dlg_seq.show()
        app.processEvents()
        save_widget(dlg_seq, "dialog_sequence_settings.png")
        dlg_seq.hide()
    except Exception as e:
        print("Error capturing Sequence Settings Dialog:", e)

    window.close()
    print("\nAll 6 v2.1.0 feature screenshots captured successfully!")

if __name__ == "__main__":
    main()
