"""
Generate UI screenshots for OpenReview v2.0.0 new features.
Captures the 6 requested new features into docs/html/assets/screenshots/:
1. hud_channel_matrix.png
2. dialog_batch_export.png
3. studio_workspaces.png
4. color_sliders_overlay.png
5. metadata_tree_inspector.png
6. hud_scopes_async.png
"""
import os
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR / "src"))

import numpy as np
from PySide6.QtCore import Qt, QPoint, QRect, QTimer
from PySide6.QtWidgets import QApplication, QWidget, QHBoxLayout
from PySide6.QtGui import QColor, QPixmap

from openreview.ui.main_window import MainWindow
from openreview.widgets.hud_channel_matrix import HUDChannelMatrixWidget
from openreview.core.exr_matrix import inspect_exr_matrix, EXRChannelInfo, SemanticPassType
from openreview.core.batch_export import BatchExportEngine, BatchExportJob
from openreview.ui.batch_export_dialog import BatchExportDialog
from openreview.widgets.color_sliders_overlay import ColorSlidersOverlay, ColorGradeSettings
from openreview.widgets.metadata_tree_widget import MetadataTreeWidget
from openreview.core.metadata_tree import build_unified_metadata_tree
from openreview.widgets.hud_waveform import WaveformHUD
from openreview.widgets.hud_vectorscope import VectorscopeHUD

OUTPUT_DIR = ROOT_DIR / "docs" / "html" / "assets" / "screenshots"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

SAMPLE_EXR = ROOT_DIR / "tests" / "sample_media" / "multichannel_exr" / "multichannel_test.exr"
SAMPLE_PNG = ROOT_DIR / "tests" / "sample_media" / "sh010_comp_v002.1001.png"

def save_widget(widget: QWidget, filename: str):
    app = QApplication.instance()
    app.processEvents()
    pix = widget.grab()
    out_path = OUTPUT_DIR / filename
    pix.save(str(out_path), "PNG")
    print(f"Captured: {filename:35s} ({pix.size().width()}x{pix.size().height()}) -> {out_path}")

def main():
    app = QApplication.instance()
    if not app:
        app = QApplication([])

    window = MainWindow()
    window.resize(1440, 880)
    window.show()
    app.processEvents()

    # Load multi-channel EXR if present
    target_media = SAMPLE_EXR if SAMPLE_EXR.exists() else SAMPLE_PNG
    if target_media.exists():
        window._load_path(str(target_media))
        app.processEvents()

    # 1. Capture HUD Channel Matrix
    try:
        matrix_hud = HUDChannelMatrixWidget(parent=window.viewport)
        if SAMPLE_EXR.exists():
            layers_dict, channels = inspect_exr_matrix(str(SAMPLE_EXR))
            matrix_hud.populate_matrix(layers_dict, channels)
        else:
            sample_channels = [
                EXRChannelInfo(name="R", layer="beauty", channel_role="R", semantic_type=SemanticPassType.COLOR),
                EXRChannelInfo(name="G", layer="beauty", channel_role="G", semantic_type=SemanticPassType.COLOR),
                EXRChannelInfo(name="B", layer="beauty", channel_role="B", semantic_type=SemanticPassType.COLOR),
                EXRChannelInfo(name="A", layer="beauty", channel_role="A", semantic_type=SemanticPassType.ALPHA),
                EXRChannelInfo(name="N.X", layer="N", channel_role="X", semantic_type=SemanticPassType.NORMAL),
                EXRChannelInfo(name="N.Y", layer="N", channel_role="Y", semantic_type=SemanticPassType.NORMAL),
                EXRChannelInfo(name="N.Z", layer="N", channel_role="Z", semantic_type=SemanticPassType.NORMAL),
                EXRChannelInfo(name="depth.Z", layer="depth", channel_role="Z", semantic_type=SemanticPassType.DEPTH),
            ]
            sample_layers = {
                "beauty": sample_channels[0:4],
                "N": sample_channels[4:7],
                "depth": [sample_channels[7]],
            }
            matrix_hud.populate_matrix(sample_layers, sample_channels)

        matrix_hud.adjustSize()
        matrix_hud.show()
        app.processEvents()
        save_widget(matrix_hud, "hud_channel_matrix.png")
        matrix_hud.hide()
    except Exception as e:
        print("Error capturing HUD Channel Matrix:", e)

    # 2. Capture Batch Export Dialog
    try:
        engine = BatchExportEngine.instance()
        engine.clear_queue()
        engine.add_job(BatchExportJob(
            name="SH010_Comp_v002",
            output_path="D:/renders/SH010_Comp_v002.mov",
            format="mov",
            export_type="video",
            fps=24.0,
            frames=list(range(1001, 1051)),
            codec="Apple ProRes 4444"
        ))
        engine.add_job(BatchExportJob(
            name="SH020_Lighting_v001",
            output_path="D:/renders/SH020_Lighting_v001.mp4",
            format="mp4",
            export_type="video",
            fps=24.0,
            frames=list(range(1001, 1121)),
            codec="H.264 High 10"
        ))
        engine.add_job(BatchExportJob(
            name="SH030_FX_Fire_v004",
            output_path="D:/renders/SH030_FX_Fire_v004.####.exr",
            format="seq_exr",
            export_type="sequence",
            fps=24.0,
            frames=list(range(1001, 1081)),
            codec="EXR Multi-Layer (DWAB)"
        ))
        dlg_batch = BatchExportDialog(engine=engine, parent=window)
        dlg_batch.resize(880, 560)
        dlg_batch.show()
        app.processEvents()
        save_widget(dlg_batch, "dialog_batch_export.png")
        dlg_batch.hide()
    except Exception as e:
        print("Error capturing Batch Export Dialog:", e)

    # 3. Capture Studio Workspaces
    try:
        if hasattr(window, "workspace_manager"):
            window.workspace_manager.apply_preset("Comp Analysis")
            app.processEvents()
        save_widget(window, "studio_workspaces.png")
        if hasattr(window, "workspace_manager"):
            window.workspace_manager.apply_preset("Review & Dailies")
            app.processEvents()
    except Exception as e:
        print("Error capturing Studio Workspaces:", e)

    # 4. Capture Color Sliders Overlay
    try:
        sliders_overlay = ColorSlidersOverlay(parent=window.viewport)
        sliders_overlay.open_overlay(ColorGradeSettings(gain=(1.15, 1.15, 1.15), gamma=1.05, saturation=1.2, hue=5.0))
        sliders_overlay.auto_center_in_parent()
        sliders_overlay.show()
        app.processEvents()
        save_widget(window.viewport, "color_sliders_overlay.png")
        sliders_overlay.hide()
    except Exception as e:
        print("Error capturing Color Sliders Overlay:", e)

    # 5. Capture Metadata Tree Inspector Widget
    try:
        target_path = str(SAMPLE_EXR) if SAMPLE_EXR.exists() else str(SAMPLE_PNG)
        meta_container = QWidget()
        meta_container.setStyleSheet("background-color: #1a1a1a;")
        meta_layout = QHBoxLayout(meta_container)
        meta_layout.setContentsMargins(12, 12, 12, 12)
        meta_widget = MetadataTreeWidget(meta_container)
        meta_widget.set_media(target_path, frame=1001, context={"width": 1920, "height": 1080, "fps": 24.0, "format": "OpenEXR"})
        meta_layout.addWidget(meta_widget)
        meta_container.resize(650, 480)
        meta_container.show()
        app.processEvents()
        save_widget(meta_container, "metadata_tree_inspector.png")
        meta_container.hide()
    except Exception as e:
        print("Error capturing Metadata Tree Inspector:", e)

    # 6. Capture Async Scopes Dual HUD (Waveform & Vectorscope)
    try:
        scopes_container = QWidget(window.viewport)
        scopes_container.setStyleSheet("background-color: transparent;")
        sc_layout = QHBoxLayout(scopes_container)
        sc_layout.setContentsMargins(0, 0, 0, 0)
        sc_layout.setSpacing(12)

        wf_hud = WaveformHUD(parent=scopes_container)
        vs_hud = VectorscopeHUD(parent=scopes_container)
        sc_layout.addWidget(wf_hud)
        sc_layout.addWidget(vs_hud)

        # Generate realistic demo RGB gradients on the canvases
        demo_frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        demo_frame[:, :, 0] = np.tile(np.linspace(20, 240, 1920, dtype=np.uint8), (1080, 1))
        demo_frame[:, :, 1] = np.tile(np.linspace(10, 200, 1080, dtype=np.uint8)[:, None], (1, 1920))
        demo_frame[:, :, 2] = 128
        wf_hud.update_frame(demo_frame)
        vs_hud.update_frame(demo_frame)

        scopes_container.adjustSize()
        scopes_container.show()
        app.processEvents()
        save_widget(scopes_container, "hud_scopes_async.png")
        scopes_container.hide()
    except Exception as e:
        print("Error capturing Async Scopes HUD:", e)

    window.close()
    print("\nAll 6 new feature screenshots captured successfully!")

if __name__ == "__main__":
    main()
