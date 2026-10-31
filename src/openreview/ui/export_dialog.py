"""
Unified VFX Studio Media Export Dialog for OpenReview.
Supports QuickTime (Apple ProRes, H.264, H.265), OpenEXR, DPX, TIFF, PNG, JPEG, Targa,
configurable color space conversions, burn-in of annotations/grading,
flexible downrezzing presets, and asynchronous non-blocking background rendering.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional, List, Callable, Tuple
import numpy as np

from PySide6.QtCore import Qt, Signal, QThread
from PySide6.QtWidgets import (
    QDialog,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QFormLayout,
    QComboBox,
    QLineEdit,
    QPushButton,
    QCheckBox,
    QProgressBar,
    QFileDialog,
    QMessageBox,
    QSpinBox,
    QLabel,
    QGroupBox,
    QStackedWidget,
    QScrollArea,
)

from openreview.widgets.draggable_scroll_area import BlenderScrollArea

from openreview.core.clock import PlaybackClock
from openreview.core.exporter import MediaExporter
from openreview.core.preferences import PreferencesManager


class ExportWorker(QThread):
    """Background rendering worker for non-blocking media export."""
    progress = Signal(int, int, str)       # current, total, status_message
    finished = Signal(str)                 # output path
    error = Signal(str)                    # error message

    def __init__(
        self,
        config: dict,
        raw_frame_reader: Callable[[int], Optional[np.ndarray]],
        annotation_getter: Optional[Callable[[int], list]] = None,
        viewport=None,
    ):
        super().__init__()
        self.config = config
        self.raw_frame_reader = raw_frame_reader
        self.annotation_getter = annotation_getter
        self.viewport = viewport
        self._is_cancelled = False

    def cancel(self) -> None:
        self._is_cancelled = True

    def is_cancelled(self) -> bool:
        return self._is_cancelled

    def run(self) -> None:
        try:
            frames = self.config["frames"]
            if not frames:
                self.error.emit("No frames provided for export.")
                return

            dest = self.config["destination"]
            export_type = self.config["export_type"]   # "video", "sequence", "single"
            target_space = self.config.get("colorspace", "Current View (WYSIWYG)")
            burn_grade = self.config.get("burn_grade", True)
            burn_annotations = self.config.get("burn_annotations", True)
            target_w = self.config.get("target_width")
            target_h = self.config.get("target_height")
            fps = float(self.config.get("fps", 24.0))

            def _frame_provider(frame_num: int) -> np.ndarray:
                raw = self.raw_frame_reader(frame_num)
                if isinstance(raw, (tuple, list)):
                    raw = next((x for x in raw if isinstance(x, np.ndarray)), None)
                if raw is None:
                    raw = np.zeros((1080, 1920, 3), dtype=np.uint8)

                # 1. Colorspace conversion & grading
                img = MediaExporter.convert_colorspace(raw, target_space, self.viewport, burn_in_grade=burn_grade)

                # 2. Annotations burn-in
                if burn_annotations and self.annotation_getter:
                    strokes = self.annotation_getter(frame_num)
                    if strokes:
                        is_float = img.dtype in (np.float32, np.float64, np.float16)
                        if is_float:
                            u8 = np.clip(img[:, :, :3] * 255.0, 0, 255).astype(np.uint8)
                            u8_annotated = MediaExporter.render_strokes_on_image(u8, strokes)
                            img = u8_annotated.astype(np.float32) / 255.0
                        else:
                            img = MediaExporter.render_strokes_on_image(img, strokes)

                # 3. Downrez / Resizing
                downscale_gt_fhd = self.config.get("downscale_gt_fhd", False)
                is_custom_res = self.config.get("is_custom_res", False)
                if not is_custom_res and downscale_gt_fhd and (img.shape[1] > 1920 or img.shape[0] > 1080):
                    half_w = max(1, img.shape[1] // 2)
                    half_h = max(1, img.shape[0] // 2)
                    img = MediaExporter.resize_frame(img, half_w, half_h)
                elif target_w and target_h and (img.shape[1] != target_w or img.shape[0] != target_h):
                    img = MediaExporter.resize_frame(img, target_w, target_h)

                return img

            total_frames = len(frames)

            def _progress_cb(cur: int, tot: int) -> None:
                pct = int((cur / tot) * 100) if tot > 0 else 0
                self.progress.emit(cur, tot, f"Processing frame {cur} of {tot} ({pct}%)...")

            if export_type == "single":
                self.progress.emit(0, 1, "Rendering single frame...")
                target_f = frames[0]
                img = _frame_provider(target_f)
                ext = self.config.get("ext", Path(dest).suffix.lstrip(".").lower() or "jpg")
                comp = self.config.get("compression", "4" if ext == "png" else "zip")
                bd = self.config.get("bit_depth", "8" if ext == "png" else "16")
                q = int(self.config.get("quality", 85))
                dither = bool(self.config.get("dither", True))
                png_mode = self.config.get("png_mode", "png8")
                MediaExporter.save_image_file(img, dest, compression=comp, bit_depth=bd, quality=q, dither=dither, png_mode=png_mode)
                self.progress.emit(1, 1, "Completed.")
                self.finished.emit(str(dest))

            elif export_type == "video":
                codec = self.config.get("codec", "Apple ProRes 422 HQ")
                crf = int(self.config.get("crf", 18))
                MediaExporter.export_video_ffmpeg(
                    _frame_provider,
                    frames,
                    dest,
                    fps=fps,
                    codec=codec,
                    crf=crf,
                    progress_callback=_progress_cb,
                    cancel_token=self.is_cancelled,
                )
                if not self._is_cancelled:
                    self.finished.emit(str(dest))
                else:
                    self.error.emit("Export cancelled by user.")

            elif export_type == "sequence":
                prefix = self.config.get("prefix", "frame")
                ext = self.config.get("ext", "exr")
                pad = int(self.config.get("pad", 4))
                comp = self.config.get("compression", "4" if ext == "png" else "zip")
                bd = self.config.get("bit_depth", "8" if ext == "png" else "16")
                q = self.config.get("quality", 95)
                MediaExporter.export_sequence(
                    _frame_provider,
                    frames,
                    dest,
                    filename_prefix=prefix,
                    file_ext=ext,
                    pad=pad,
                    compression=comp,
                    bit_depth=bd,
                    quality=q,
                    progress_callback=_progress_cb,
                    cancel_token=self.is_cancelled,
                    dither=bool(self.config.get("dither", True)),
                    png_mode=self.config.get("png_mode", "png8"),
                )
                if not self._is_cancelled:
                    self.finished.emit(str(dest))
                else:
                    self.error.emit("Export cancelled by user.")

        except Exception as e:
            self.error.emit(str(e))


class ExportDialog(QDialog):
    """Studio VFX Media Export Dialog."""

    export_started = Signal(dict)

    def __init__(
        self,
        clock: PlaybackClock,
        marked_frames: List[int],
        annotated_frames: List[int],
        input_resolution: Tuple[int, int] = (1920, 1080),
        raw_frame_reader: Optional[Callable[[int], Optional[np.ndarray]]] = None,
        annotation_getter: Optional[Callable[[int], list]] = None,
        viewport=None,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.setWindowTitle("Export Media - OpenReview")
        self.resize(600, 720)
        self.setMinimumWidth(560)
        self.clock = clock
        self.marked_frames = sorted(list(marked_frames))
        self.annotated_frames = sorted(list(annotated_frames))
        self.in_w, self.in_h = input_resolution if (input_resolution and input_resolution[0] > 0) else (1920, 1080)
        self.aspect_ratio = float(self.in_w) / max(1, self.in_h)
        self.raw_frame_reader = raw_frame_reader
        self.annotation_getter = annotation_getter
        self.viewport = viewport

        self._updating_res = False
        self._worker: Optional[ExportWorker] = None

        self._setup_ui()

    def _setup_ui(self) -> None:
        self.setStyleSheet("""
            QDialog {
                background-color: #1e1e1e;
                color: #e0e0e0;
            }
            QGroupBox {
                border: 1px solid #333333;
                border-radius: 4px;
                margin-top: 10px;
                padding-top: 12px;
                color: #e5a823;
                font-weight: bold;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                subcontrol-position: top left;
                padding: 0 4px;
            }
            QLabel {
                color: #cccccc;
            }
            QLineEdit, QSpinBox, QComboBox {
                background-color: #121212;
                border: 1px solid #3a3a3a;
                color: #ffffff;
                padding: 4px;
                border-radius: 2px;
            }
            QLineEdit:focus, QSpinBox:focus, QComboBox:focus {
                border-color: #e5a823;
            }
            QPushButton {
                background-color: #2b2b2b;
                border: 1px solid #444444;
                color: #ffffff;
                padding: 6px 14px;
                border-radius: 3px;
            }
            QPushButton:hover {
                border-color: #e5a823;
            }
            QProgressBar {
                border: 1px solid #333333;
                background-color: #121212;
                text-align: center;
                color: #ffffff;
                border-radius: 3px;
                height: 18px;
            }
            QProgressBar::chunk {
                background-color: #27ae60;
            }
        """ + __import__("openreview.ui.theme", fromlist=["get_spinbox_arrow_stylesheet"]).get_spinbox_arrow_stylesheet())

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(12, 12, 12, 12)
        main_layout.setSpacing(10)

        scroll_area = BlenderScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setFrameShape(QScrollArea.Shape.NoFrame)

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(10)

        # ----------------------------------------------------------------------
        # 1. Target Format Selection
        # ----------------------------------------------------------------------
        grp_fmt = QGroupBox("Export Format & Settings")
        layout_fmt = QVBoxLayout(grp_fmt)

        row_fmt = QHBoxLayout()
        row_fmt.addWidget(QLabel("Output Format:"))
        self.combo_format = QComboBox()
        self.combo_format.addItem("QuickTime Movie (.mov)", "mov")
        self.combo_format.addItem("MPEG-4 Movie (.mp4)", "mp4")
        self.combo_format.addItem("OpenEXR Sequence (.exr)", "seq_exr")
        self.combo_format.addItem("DPX Sequence (.dpx)", "seq_dpx")
        self.combo_format.addItem("TIFF Sequence (.tif)", "seq_tif")
        self.combo_format.addItem("PNG Sequence (.png)", "seq_png")
        self.combo_format.addItem("JPEG Sequence (.jpg)", "seq_jpg")
        self.combo_format.addItem("Targa Sequence (.tga)", "seq_tga")
        self.combo_format.addItem("Single Frame Only", "single")
        self.combo_format.currentIndexChanged.connect(self._on_format_changed)
        row_fmt.addWidget(self.combo_format, 1)
        layout_fmt.addLayout(row_fmt)

        # Dynamic stacked format settings
        self.stack_settings = QStackedWidget()

        # Page 0: QuickTime (.mov)
        page_mov = QWidget()
        form_mov = QFormLayout(page_mov)
        form_mov.setContentsMargins(0, 4, 0, 0)
        self.combo_mov_codec = QComboBox()
        self.combo_mov_codec.addItems([
            "Apple ProRes 422 HQ",
            "Apple ProRes 422",
            "Apple ProRes 4444",
            "Apple ProRes 422 Proxy",
            "H.264",
            "H.265 (HEVC)",
        ])
        self.combo_mov_codec.currentIndexChanged.connect(self._on_codec_changed)
        form_mov.addRow("Video Codec:", self.combo_mov_codec)
        self.spin_mov_crf = QSpinBox()
        self.spin_mov_crf.setRange(0, 51)
        self.spin_mov_crf.setValue(18)
        self.spin_mov_crf.setEnabled(False)
        form_mov.addRow("Constant Rate Factor (CRF):", self.spin_mov_crf)
        self.stack_settings.addWidget(page_mov)

        # Page 1: MP4 (.mp4)
        page_mp4 = QWidget()
        form_mp4 = QFormLayout(page_mp4)
        form_mp4.setContentsMargins(0, 4, 0, 0)
        self.combo_mp4_codec = QComboBox()
        self.combo_mp4_codec.addItems(["H.264", "H.265 (HEVC)"])
        form_mp4.addRow("Video Codec:", self.combo_mp4_codec)
        self.spin_mp4_crf = QSpinBox()
        self.spin_mp4_crf.setRange(0, 51)
        self.spin_mp4_crf.setValue(18)
        form_mp4.addRow("Constant Rate Factor (CRF):", self.spin_mp4_crf)
        self.stack_settings.addWidget(page_mp4)

        # Page 2: OpenEXR
        page_exr = QWidget()
        form_exr = QFormLayout(page_exr)
        form_exr.setContentsMargins(0, 4, 0, 0)
        self.combo_exr_comp = QComboBox()
        self.combo_exr_comp.addItems(["ZIP (Single Scanline)", "PIZ (Wavelet)", "DWAA", "DWAB", "RLE", "Uncompressed (None)"])
        form_exr.addRow("Compression:", self.combo_exr_comp)
        self.combo_exr_depth = QComboBox()
        self.combo_exr_depth.addItems(["16-bit Half Float", "32-bit Float"])
        form_exr.addRow("Bit Depth:", self.combo_exr_depth)
        self.stack_settings.addWidget(page_exr)

        # Page 3: DPX
        page_dpx = QWidget()
        form_dpx = QFormLayout(page_dpx)
        form_dpx.setContentsMargins(0, 4, 0, 0)
        self.combo_dpx_depth = QComboBox()
        self.combo_dpx_depth.addItems(["10-bit SMPTE 268M", "16-bit SMPTE 268M"])
        form_dpx.addRow("Bit Depth:", self.combo_dpx_depth)
        self.stack_settings.addWidget(page_dpx)

        # Page 4: TIFF
        page_tif = QWidget()
        form_tif = QFormLayout(page_tif)
        form_tif.setContentsMargins(0, 4, 0, 0)
        self.combo_tif_depth = QComboBox()
        self.combo_tif_depth.addItems(["16-bit Unsigned Int", "8-bit Unsigned Int"])
        form_tif.addRow("Bit Depth:", self.combo_tif_depth)
        self.stack_settings.addWidget(page_tif)

        # Page 5: PNG
        page_png = QWidget()
        form_png = QFormLayout(page_png)
        form_png.setContentsMargins(0, 4, 0, 0)

        prefs = PreferencesManager.instance().prefs
        pref_png_mode = getattr(prefs, "export_png_mode", "png8")
        pref_png_dither = getattr(prefs, "export_png_dither", True)
        pref_png_comp = getattr(prefs, "export_png_compression", 4)
        pref_jpg_quality = getattr(prefs, "export_jpg_quality", 85)
        pref_default_fmt = getattr(prefs, "default_export_image_format", "jpg").lower()

        self.combo_png_mode = QComboBox()
        self.combo_png_mode.addItem("PNG-8 Indexed (Quantized 256 Colors - ~70% smaller)", "png8")
        self.combo_png_mode.addItem("PNG-24 Truecolor Optimized (Strictly Lossless)", "png24_opt")
        self.combo_png_mode.addItem("16-bit Unsigned Int (Deep Color)", "16")
        idx_mode = self.combo_png_mode.findData(pref_png_mode)
        if idx_mode >= 0:
            self.combo_png_mode.setCurrentIndex(idx_mode)
        form_png.addRow("Format Mode:", self.combo_png_mode)

        self.chk_png_dither = QCheckBox("Enable Color Dithering for Gradients")
        self.chk_png_dither.setChecked(pref_png_dither)
        self.chk_png_dither.setToolTip("Apply Floyd-Steinberg dithering for smooth color transitions in PNG-8. Uncheck for maximum flat compression.")
        form_png.addRow("", self.chk_png_dither)

        self.combo_png_comp = QComboBox()
        self.combo_png_comp.addItem("Balanced (Level 4 - Recommended)", "4")
        self.combo_png_comp.addItem("Maximum Compression (Level 9)", "9")
        self.combo_png_comp.addItem("Standard (Level 6)", "6")
        self.combo_png_comp.addItem("Fast / Draft (Level 1)", "1")
        idx_comp = self.combo_png_comp.findData(str(pref_png_comp))
        if idx_comp >= 0:
            self.combo_png_comp.setCurrentIndex(idx_comp)
        form_png.addRow("Compression:", self.combo_png_comp)

        def _on_png_mode_changed(idx: int):
            is_png8 = (self.combo_png_mode.currentData() == "png8")
            self.chk_png_dither.setEnabled(is_png8)
        self.combo_png_mode.currentIndexChanged.connect(_on_png_mode_changed)
        _on_png_mode_changed(self.combo_png_mode.currentIndex())

        self.stack_settings.addWidget(page_png)

        # Page 6: JPEG
        page_jpg = QWidget()
        form_jpg = QFormLayout(page_jpg)
        form_jpg.setContentsMargins(0, 4, 0, 0)
        self.spin_jpg_quality = QSpinBox()
        self.spin_jpg_quality.setRange(1, 100)
        self.spin_jpg_quality.setValue(pref_jpg_quality)
        form_jpg.addRow("JPEG Quality (1-100):", self.spin_jpg_quality)
        self.stack_settings.addWidget(page_jpg)

        # Page 7: Targa
        page_tga = QWidget()
        lbl_tga = QLabel("Format: 8-bit RGB Targa (.tga)")
        lbl_tga.setStyleSheet("color: #888888; font-style: italic;")
        QVBoxLayout(page_tga).addWidget(lbl_tga)
        self.stack_settings.addWidget(page_tga)

        # Page 8: Single Frame
        page_single = QWidget()
        form_single = QFormLayout(page_single)
        form_single.setContentsMargins(0, 4, 0, 0)
        self.combo_single_fmt = QComboBox()
        self.combo_single_fmt.addItem("JPEG Image (.jpg)", "jpg")
        self.combo_single_fmt.addItem("PNG Image (.png)", "png")
        self.combo_single_fmt.addItem("OpenEXR Image (.exr)", "exr")
        self.combo_single_fmt.addItem("DPX Image (.dpx)", "dpx")
        self.combo_single_fmt.addItem("TIFF Image (.tif)", "tif")
        form_single.addRow("Format:", self.combo_single_fmt)

        self.spin_single_jpg_quality = QSpinBox()
        self.spin_single_jpg_quality.setRange(1, 100)
        self.spin_single_jpg_quality.setValue(pref_jpg_quality)
        self.lbl_single_jpg_q = QLabel("JPEG Quality (1-100):")
        form_single.addRow(self.lbl_single_jpg_q, self.spin_single_jpg_quality)

        self.spin_single_jpg_quality.valueChanged.connect(self.spin_jpg_quality.setValue)
        self.spin_jpg_quality.valueChanged.connect(self.spin_single_jpg_quality.setValue)

        def _on_single_fmt_changed(_=None):
            is_jpg = (self.combo_single_fmt.currentData() == "jpg")
            self.lbl_single_jpg_q.setVisible(is_jpg)
            self.spin_single_jpg_quality.setVisible(is_jpg)

        self.combo_single_fmt.currentIndexChanged.connect(_on_single_fmt_changed)

        idx_single = self.combo_single_fmt.findData(pref_default_fmt)
        if idx_single >= 0:
            self.combo_single_fmt.setCurrentIndex(idx_single)
        _on_single_fmt_changed()

        self.stack_settings.addWidget(page_single)

        layout_fmt.addWidget(self.stack_settings)
        layout.addWidget(grp_fmt)

        # ----------------------------------------------------------------------
        # 2. Colorspace Conversion & Burn-In
        # ----------------------------------------------------------------------
        grp_color = QGroupBox("Color Space & Burn-In Options")
        form_color = QFormLayout(grp_color)

        self.combo_colorspace = QComboBox()
        self.combo_colorspace.addItem("Current View (WYSIWYG)", "Current View (WYSIWYG)")
        self.combo_colorspace.addItem("Scene-Linear (ACEScg)", "Scene-Linear (ACEScg)")
        self.combo_colorspace.addItem("sRGB (Display)", "sRGB (Display)")
        self.combo_colorspace.addItem("Rec.709 (Display)", "Rec.709 (Display)")
        self.combo_colorspace.addItem("Cineon Log", "Cineon Log")
        self.combo_colorspace.addItem("Raw / Pass-Through", "Raw / Pass-Through")
        self.combo_colorspace.currentIndexChanged.connect(self._on_colorspace_changed)
        form_color.addRow("Output Colorspace:", self.combo_colorspace)

        self.chk_burn_annotations = QCheckBox("Burn-in Vector Annotations & Text Notes")
        self.chk_burn_annotations.setChecked(True)
        form_color.addRow("", self.chk_burn_annotations)

        self.chk_burn_grade = QCheckBox("Burn-in Viewport Exposure, CDL, Grade & 3D LUT")
        self.chk_burn_grade.setChecked(True)
        self.chk_burn_grade.setEnabled(False)  # Disabled when WYSIWYG
        form_color.addRow("", self.chk_burn_grade)

        layout.addWidget(grp_color)

        # ----------------------------------------------------------------------
        # 3. Output Resolution / Downrezzing
        # ----------------------------------------------------------------------
        grp_res = QGroupBox("Output Resolution & Downrezzing")
        layout_res = QVBoxLayout(grp_res)

        row_preset = QHBoxLayout()
        row_preset.addWidget(QLabel("Resolution Preset:"))
        self.combo_res_preset = QComboBox()
        self.combo_res_preset.addItem(f"Native Resolution (100%) [{self.in_w} x {self.in_h}]", "100")
        self.combo_res_preset.addItem("75% Scale", "75")
        self.combo_res_preset.addItem("50% Scale (Half Res)", "50")
        self.combo_res_preset.addItem("25% Scale (Quarter Res)", "25")
        self.combo_res_preset.addItem("4K UHD (3840 x 2160)", "4k")
        self.combo_res_preset.addItem("2K DCI (2048 x 1080)", "2k")
        self.combo_res_preset.addItem("1080p FHD (1920 x 1080)", "1080p")
        self.combo_res_preset.addItem("720p HD (1280 x 720)", "720p")
        self.combo_res_preset.addItem("Custom Resolution...", "custom")
        self.combo_res_preset.currentIndexChanged.connect(self._on_res_preset_changed)
        row_preset.addWidget(self.combo_res_preset, 1)
        layout_res.addLayout(row_preset)

        row_dims = QHBoxLayout()
        row_dims.addWidget(QLabel("Width:"))
        self.spin_width = QSpinBox()
        self.spin_width.setRange(16, 16384)
        self.spin_width.setValue(self.in_w)
        self.spin_width.setEnabled(False)
        self.spin_width.valueChanged.connect(self._on_width_changed)
        row_dims.addWidget(self.spin_width)

        row_dims.addWidget(QLabel("Height:"))
        self.spin_height = QSpinBox()
        self.spin_height.setRange(16, 16384)
        self.spin_height.setValue(self.in_h)
        self.spin_height.setEnabled(False)
        self.spin_height.valueChanged.connect(self._on_height_changed)
        row_dims.addWidget(self.spin_height)

        self.chk_aspect = QCheckBox("Lock Aspect")
        self.chk_aspect.setChecked(True)
        self.chk_aspect.setEnabled(False)
        row_dims.addWidget(self.chk_aspect)
        layout_res.addLayout(row_dims)

        self.chk_downscale_gt_fhd = QCheckBox("Downscale resolution by half (50%) when media exceeds Full HD (1920x1080)")
        self.chk_downscale_gt_fhd.setToolTip(
            "Automatically reduces width and height by 50% when source media is larger than 1920x1080 to minimize export file sizes."
        )
        pref_downscale = getattr(prefs, "export_downscale_greater_than_fhd", True)
        self.chk_downscale_gt_fhd.setChecked(pref_downscale)
        self.chk_downscale_gt_fhd.toggled.connect(self._update_res_summary)
        layout_res.addWidget(self.chk_downscale_gt_fhd)

        self.lbl_res_summary = QLabel(f"Input: {self.in_w} x {self.in_h}  →  Output: {self.in_w} x {self.in_h} (100%)")
        self.lbl_res_summary.setStyleSheet("color: #27ae60; font-weight: bold; padding: 2px;")
        layout_res.addWidget(self.lbl_res_summary)

        layout.addWidget(grp_res)

        # ----------------------------------------------------------------------
        # 4. Frame Range
        # ----------------------------------------------------------------------
        grp_range = QGroupBox("Frame Range")
        range_layout = QVBoxLayout(grp_range)

        self.combo_range = QComboBox()
        self.combo_range.addItem(f"Active In/Out Range ({self.clock.in_frame} - {self.clock.out_frame})", "in_out")
        self.combo_range.addItem(f"Full Sequence ({self.clock.start_frame} - {self.clock.end_frame})", "full")
        self.combo_range.addItem(f"Current Frame ({self.clock.current_frame})", "current")
        self.combo_range.addItem(f"Marked / Bookmarked Frames ({len(self.marked_frames)} frames)", "marked")
        self.combo_range.addItem(f"Annotated Frames ({len(self.annotated_frames)} frames)", "annotated")
        self.combo_range.addItem("Custom Range", "custom")
        self.combo_range.currentIndexChanged.connect(self._on_range_changed)
        range_layout.addWidget(self.combo_range)

        custom_row = QHBoxLayout()
        custom_row.addWidget(QLabel("Start:"))
        self.spin_custom_start = QSpinBox()
        self.spin_custom_start.setRange(self.clock.start_frame, self.clock.end_frame)
        self.spin_custom_start.setValue(self.clock.in_frame)
        self.spin_custom_start.setEnabled(False)
        custom_row.addWidget(self.spin_custom_start)

        custom_row.addWidget(QLabel("End:"))
        self.spin_custom_end = QSpinBox()
        self.spin_custom_end.setRange(self.clock.start_frame, self.clock.end_frame)
        self.spin_custom_end.setValue(self.clock.out_frame)
        self.spin_custom_end.setEnabled(False)
        custom_row.addWidget(self.spin_custom_end)
        range_layout.addLayout(custom_row)

        layout.addWidget(grp_range)

        # ----------------------------------------------------------------------
        # 5. Destination & Sequence Naming
        # ----------------------------------------------------------------------
        grp_dest = QGroupBox("Export Destination")
        form_dest = QFormLayout(grp_dest)

        path_row = QHBoxLayout()
        self.edit_path = QLineEdit()
        self.btn_browse = QPushButton("Browse...")
        self.btn_browse.clicked.connect(self._browse_destination)
        path_row.addWidget(self.edit_path)
        path_row.addWidget(self.btn_browse)
        form_dest.addRow("Path / Folder:", path_row)

        # Sequence naming row
        self.row_seq_name = QHBoxLayout()
        self.edit_prefix = QLineEdit("frame")
        self.spin_pad = QSpinBox()
        self.spin_pad.setRange(1, 8)
        self.spin_pad.setValue(4)
        self.row_seq_name.addWidget(QLabel("Prefix:"))
        self.row_seq_name.addWidget(self.edit_prefix, 2)
        self.row_seq_name.addWidget(QLabel("Padding:"))
        self.row_seq_name.addWidget(self.spin_pad, 1)
        form_dest.addRow("File Naming:", self.row_seq_name)

        layout.addWidget(grp_dest)

        scroll_area.setWidget(container)
        main_layout.addWidget(scroll_area)

        # ----------------------------------------------------------------------
        # 6. Realtime Progress & Actions
        # ----------------------------------------------------------------------
        self.lbl_status = QLabel("")
        self.lbl_status.setStyleSheet("color: #aaaaaa; font-size: 11px;")
        main_layout.addWidget(self.lbl_status)

        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        main_layout.addWidget(self.progress_bar)

        btn_row = QHBoxLayout()
        btn_row.addStretch()

        self.btn_export = QPushButton("Export")
        self.btn_export.setStyleSheet("background-color: #27ae60; color: #ffffff; font-weight: bold; min-width: 100px;")
        self.btn_export.clicked.connect(self._on_export_clicked)
        btn_row.addWidget(self.btn_export)

        self.btn_close = QPushButton("Close")
        self.btn_close.clicked.connect(self.reject)
        btn_row.addWidget(self.btn_close)

        main_layout.addLayout(btn_row)

        if pref_default_fmt == "jpg":
            idx_seq = self.combo_format.findData("seq_jpg")
            if idx_seq >= 0:
                self.combo_format.setCurrentIndex(idx_seq)

        self._on_format_changed(self.combo_format.currentIndex())
        self._on_colorspace_changed(0)
        self._update_res_summary()

    # --------------------------------------------------------------------------
    # Handlers & Dynamic Updates
    # --------------------------------------------------------------------------
    def _on_format_changed(self, idx: int) -> None:
        self.stack_settings.setCurrentIndex(idx)
        fmt = self.combo_format.currentData()
        is_seq = str(fmt).startswith("seq_")
        is_single = (fmt == "single")

        # Toggle prefix and padding visibility for sequence vs file
        if hasattr(self, "row_seq_name"):
            for i in range(self.row_seq_name.count()):
                w = self.row_seq_name.itemAt(i).widget()
                if w:
                    w.setVisible(is_seq)

        if is_single:
            self.combo_range.setCurrentIndex(2)  # Current frame
            self.combo_range.setEnabled(False)
        else:
            self.combo_range.setEnabled(True)

    def _on_codec_changed(self, idx: int) -> None:
        codec_name = self.combo_mov_codec.currentText()
        is_h26x = ("H.264" in codec_name or "H.265" in codec_name)
        self.spin_mov_crf.setEnabled(is_h26x)

    def _on_colorspace_changed(self, idx: int) -> None:
        cs = self.combo_colorspace.currentData()
        if cs == "Current View (WYSIWYG)":
            self.chk_burn_grade.setChecked(True)
            self.chk_burn_grade.setEnabled(False)
        elif cs == "Raw / Pass-Through":
            self.chk_burn_grade.setChecked(False)
            self.chk_burn_grade.setEnabled(False)
        else:
            self.chk_burn_grade.setEnabled(True)

    def _on_res_preset_changed(self, idx: int) -> None:
        val = self.combo_res_preset.currentData()
        self._updating_res = True
        if val == "100":
            w, h = self.in_w, self.in_h
            self.spin_width.setEnabled(False)
            self.spin_height.setEnabled(False)
            self.chk_aspect.setEnabled(False)
        elif val == "75":
            w, h = max(16, int(round(self.in_w * 0.75))), max(16, int(round(self.in_h * 0.75)))
            self.spin_width.setEnabled(False)
            self.spin_height.setEnabled(False)
            self.chk_aspect.setEnabled(False)
        elif val == "50":
            w, h = max(16, int(round(self.in_w * 0.50))), max(16, int(round(self.in_h * 0.50)))
            self.spin_width.setEnabled(False)
            self.spin_height.setEnabled(False)
            self.chk_aspect.setEnabled(False)
        elif val == "25":
            w, h = max(16, int(round(self.in_w * 0.25))), max(16, int(round(self.in_h * 0.25)))
            self.spin_width.setEnabled(False)
            self.spin_height.setEnabled(False)
            self.chk_aspect.setEnabled(False)
        elif val == "4k":
            w, h = 3840, 2160
            self.spin_width.setEnabled(False)
            self.spin_height.setEnabled(False)
            self.chk_aspect.setEnabled(False)
        elif val == "2k":
            w, h = 2048, 1080
            self.spin_width.setEnabled(False)
            self.spin_height.setEnabled(False)
            self.chk_aspect.setEnabled(False)
        elif val == "1080p":
            w, h = 1920, 1080
            self.spin_width.setEnabled(False)
            self.spin_height.setEnabled(False)
            self.chk_aspect.setEnabled(False)
        elif val == "720p":
            w, h = 1280, 720
            self.spin_width.setEnabled(False)
            self.spin_height.setEnabled(False)
            self.chk_aspect.setEnabled(False)
        else:  # Custom
            w, h = self.spin_width.value(), self.spin_height.value()
            self.spin_width.setEnabled(True)
            self.spin_height.setEnabled(True)
            self.chk_aspect.setEnabled(True)

        self.spin_width.setValue(w)
        self.spin_height.setValue(h)
        self._updating_res = False
        self._update_res_summary()

    def _on_width_changed(self, new_w: int) -> None:
        if self._updating_res:
            return
        if self.chk_aspect.isChecked() and self.aspect_ratio > 0:
            self._updating_res = True
            new_h = max(16, int(round(new_w / self.aspect_ratio)))
            self.spin_height.setValue(new_h)
            self._updating_res = False
        self._update_res_summary()

    def _on_height_changed(self, new_h: int) -> None:
        if self._updating_res:
            return
        if self.chk_aspect.isChecked() and self.aspect_ratio > 0:
            self._updating_res = True
            new_w = max(16, int(round(new_h * self.aspect_ratio)))
            self.spin_width.setValue(new_w)
            self._updating_res = False
        self._update_res_summary()

    def _update_res_summary(self) -> None:
        out_w = self.spin_width.value()
        out_h = self.spin_height.value()
        is_custom_preset = self.combo_res_preset.currentData() not in ("100",)
        will_downscale = (
            not is_custom_preset
            and hasattr(self, "chk_downscale_gt_fhd")
            and self.chk_downscale_gt_fhd.isChecked()
            and (self.in_w > 1920 or self.in_h > 1080)
        )
        if will_downscale:
            disp_w = max(1, self.in_w // 2)
            disp_h = max(1, self.in_h // 2)
            pct = int(round((disp_w / max(1, self.in_w)) * 100))
            self.lbl_res_summary.setText(f"Input: {self.in_w} x {self.in_h}  →  Output: {disp_w} x {disp_h} ({pct}% Downscale > Full HD)")
        else:
            pct = int(round((out_w / max(1, self.in_w)) * 100))
            self.lbl_res_summary.setText(f"Input: {self.in_w} x {self.in_h}  →  Output: {out_w} x {out_h} ({pct}%)")

    def _on_range_changed(self, index: int) -> None:
        is_custom = self.combo_range.currentData() == "custom"
        self.spin_custom_start.setEnabled(is_custom)
        self.spin_custom_end.setEnabled(is_custom)

    def _browse_destination(self) -> None:
        fmt = self.combo_format.currentData()
        if fmt == "mov":
            path, _ = QFileDialog.getSaveFileName(self, "Export QuickTime Movie", "", "QuickTime Movie (*.mov)")
        elif fmt == "mp4":
            path, _ = QFileDialog.getSaveFileName(self, "Export MPEG-4 Movie", "", "MPEG-4 Movie (*.mp4)")
        elif fmt == "single":
            single_ext = self.combo_single_fmt.currentData()
            path, _ = QFileDialog.getSaveFileName(self, "Export Single Frame", "", f"Image File (*.{single_ext})")
        else:
            path = QFileDialog.getExistingDirectory(self, "Select Image Sequence Folder")

        if path:
            self.edit_path.setText(path)

    def get_selected_frames(self) -> List[int]:
        mode = self.combo_range.currentData()
        if mode == "in_out":
            return list(range(self.clock.in_frame, self.clock.out_frame + 1))
        elif mode == "full":
            return list(range(self.clock.start_frame, self.clock.end_frame + 1))
        elif mode == "current":
            return [self.clock.current_frame]
        elif mode == "marked":
            return self.marked_frames if self.marked_frames else [self.clock.current_frame]
        elif mode == "annotated":
            return self.annotated_frames if self.annotated_frames else [self.clock.current_frame]
        elif mode == "custom":
            s = self.spin_custom_start.value()
            e = self.spin_custom_end.value()
            if e < s:
                e = s
            return list(range(s, e + 1))
        return [self.clock.current_frame]

    # --------------------------------------------------------------------------
    # Export Execution
    # --------------------------------------------------------------------------
    def _build_export_config(self) -> Optional[dict]:
        out_path = self.edit_path.text().strip()
        if not out_path:
            QMessageBox.warning(self, "Missing Destination", "Please specify an export destination path.")
            return None

        frames = self.get_selected_frames()
        if not frames:
            QMessageBox.warning(self, "No Frames", "No frames selected to export.")
            return None

        fmt = self.combo_format.currentData()
        target_w = self.spin_width.value()
        target_h = self.spin_height.value()

        config = {
            "destination": out_path,
            "frames": frames,
            "colorspace": self.combo_colorspace.currentData(),
            "burn_annotations": self.chk_burn_annotations.isChecked(),
            "burn_grade": self.chk_burn_grade.isChecked(),
            "target_width": target_w,
            "target_height": target_h,
            "fps": self.clock.fps,
            "downscale_gt_fhd": self.chk_downscale_gt_fhd.isChecked() if hasattr(self, "chk_downscale_gt_fhd") else False,
            "is_custom_res": self.combo_res_preset.currentData() not in ("100",),
        }

        if fmt == "mov":
            config["export_type"] = "video"
            config["codec"] = self.combo_mov_codec.currentText()
            config["crf"] = self.spin_mov_crf.value()
        elif fmt == "mp4":
            config["export_type"] = "video"
            config["codec"] = self.combo_mp4_codec.currentText()
            config["crf"] = self.spin_mp4_crf.value()
        elif fmt == "single":
            config["export_type"] = "single"
            ext = self.combo_single_fmt.currentData()
            config["ext"] = ext
            if ext == "png":
                png_mode = getattr(self, "combo_png_mode", None)
                p_mode = png_mode.currentData() if png_mode else "png8"
                config["png_mode"] = p_mode
                config["bit_depth"] = "16" if p_mode == "16" else ("png8" if p_mode == "png8" else "8")
                config["compression"] = self.combo_png_comp.currentData() if hasattr(self, "combo_png_comp") else "4"
                config["dither"] = self.chk_png_dither.isChecked() if hasattr(self, "chk_png_dither") else True
            elif ext == "jpg":
                config["quality"] = self.spin_single_jpg_quality.value() if hasattr(self, "spin_single_jpg_quality") else self.spin_jpg_quality.value()
        elif str(fmt).startswith("seq_"):
            config["export_type"] = "sequence"
            ext_map = {
                "seq_exr": "exr",
                "seq_dpx": "dpx",
                "seq_tif": "tif",
                "seq_png": "png",
                "seq_jpg": "jpg",
                "seq_tga": "tga",
            }
            config["ext"] = ext_map.get(fmt, "exr")
            config["prefix"] = self.edit_prefix.text().strip() or "frame"
            config["pad"] = self.spin_pad.value()

            # Format-specific compression & bit-depth options
            if fmt == "seq_exr":
                comp_text = self.combo_exr_comp.currentText()
                comp_map = {
                    "ZIP (Single Scanline)": "zip",
                    "PIZ (Wavelet)": "piz",
                    "DWAA": "dwaa",
                    "DWAB": "dwab",
                    "RLE": "rle",
                    "Uncompressed (None)": "none",
                }
                config["compression"] = comp_map.get(comp_text, "zip")
                config["bit_depth"] = "16" if "16" in self.combo_exr_depth.currentText() else "32"
            elif fmt == "seq_dpx":
                config["bit_depth"] = "16" if "16" in self.combo_dpx_depth.currentText() else "10"
            elif fmt == "seq_tif":
                config["bit_depth"] = "16" if "16" in self.combo_tif_depth.currentText() else "8"
            elif fmt == "seq_png":
                png_mode = self.combo_png_mode.currentData() or "png8"
                config["png_mode"] = png_mode
                config["bit_depth"] = "16" if png_mode == "16" else ("png8" if png_mode == "png8" else "8")
                config["compression"] = self.combo_png_comp.currentData() or "4"
                config["dither"] = self.chk_png_dither.isChecked()
            elif fmt == "seq_jpg":
                config["quality"] = self.spin_jpg_quality.value()

        return config

    def _on_export_clicked(self) -> None:
        if self._worker and self._worker.isRunning():
            # Cancel in progress
            self._worker.cancel()
            self.lbl_status.setText("Cancelling export...")
            self.btn_export.setEnabled(False)
            return

        config = self._build_export_config()
        if not config:
            return

        # If raw_frame_reader is provided, run asynchronous background export!
        if self.raw_frame_reader is not None:
            self.progress_bar.setVisible(True)
            self.progress_bar.setRange(0, len(config["frames"]))
            self.progress_bar.setValue(0)
            self.lbl_status.setText("Initializing background export...")

            self.btn_export.setText("Cancel Export")
            self.btn_export.setStyleSheet("background-color: #c0392b; color: #ffffff; font-weight: bold; min-width: 100px;")
            self.btn_close.setEnabled(False)

            self._worker = ExportWorker(
                config=config,
                raw_frame_reader=self.raw_frame_reader,
                annotation_getter=self.annotation_getter,
                viewport=self.viewport,
            )
            self._worker.progress.connect(self._on_worker_progress)
            self._worker.finished.connect(self._on_worker_finished)
            self._worker.error.connect(self._on_worker_error)
            self._worker.start()
        else:
            # Fallback legacy signal
            self.export_started.emit(config)
            self.accept()

    def _on_worker_progress(self, current: int, total: int, msg: str) -> None:
        self.progress_bar.setValue(current)
        self.lbl_status.setText(msg)

    def _on_worker_finished(self, out_path: str) -> None:
        self.progress_bar.setValue(self.progress_bar.maximum())
        self.lbl_status.setText(f"Export completed: {out_path}")
        self.btn_export.setText("Export")
        self.btn_export.setStyleSheet("background-color: #27ae60; color: #ffffff; font-weight: bold; min-width: 100px;")
        self.btn_export.setEnabled(True)
        self.btn_close.setEnabled(True)
        QMessageBox.information(self, "Export Successful", f"Successfully exported media to:\n{out_path}")
        self.accept()

    def _on_worker_error(self, err_msg: str) -> None:
        self.lbl_status.setText(f"Export error: {err_msg}")
        self.btn_export.setText("Export")
        self.btn_export.setStyleSheet("background-color: #27ae60; color: #ffffff; font-weight: bold; min-width: 100px;")
        self.btn_export.setEnabled(True)
        self.btn_close.setEnabled(True)
        QMessageBox.critical(self, "Export Failed", f"Export failed:\n{err_msg}")

    def closeEvent(self, event) -> None:
        if self._worker and self._worker.isRunning():
            reply = QMessageBox.question(
                self,
                "Cancel Export?",
                "An export is currently in progress. Do you want to cancel and exit?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply == QMessageBox.StandardButton.Yes:
                self._worker.cancel()
                self._worker.wait(2000)
                event.accept()
            else:
                event.ignore()
        else:
            event.accept()
