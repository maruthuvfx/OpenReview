"""
OpenReview Media Export Engine.
Exports QuickTime movies (.mov / .mp4) and Image Sequences (.png / .jpg)
with optional WYSIWYG burn-in of annotations, color transforms, and mattes.
"""
from __future__ import annotations

import os
import subprocess
import shutil
from pathlib import Path
from typing import List, Callable, Optional, Tuple, Dict, Any
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import BoundedSemaphore, Lock
import numpy as np
from PIL import Image
from openreview.color.standard_colorspaces import (
    apply_standard_to_linear,
    apply_linear_to_standard,
    linear_to_srgb,
)

try:
    import cv2
    HAS_CV2 = True
except ImportError:
    HAS_CV2 = False

try:
    import OpenEXR
    import Imath
    HAS_OPENEXR = True
except ImportError:
    HAS_OPENEXR = False


class MediaExporter:
    """Handles disk writing for single frames, sequences, and video containers."""

    @staticmethod
    def _to_uint8(image_data: np.ndarray) -> np.ndarray:
        """Convert numpy array to uint8 RGB (0..255)."""
        if isinstance(image_data, (tuple, list)):
            image_data = next((x for x in image_data if isinstance(x, np.ndarray)), None)
        if image_data is None:
            return np.zeros((1080, 1920, 3), dtype=np.uint8)
        if image_data.dtype == np.uint8:
            return image_data[:, :, :3]
        if image_data.dtype == np.uint16:
            return (image_data[:, :, :3] >> 8).astype(np.uint8)
        return np.clip(image_data[:, :, :3] * 255.0, 0, 255).astype(np.uint8)

    @staticmethod
    def resize_frame(image: np.ndarray, target_w: int, target_h: int) -> np.ndarray:
        """High-quality downscaling/upscaling using Area/Lanczos resampling."""
        if isinstance(image, (tuple, list)):
            image = next((x for x in image if isinstance(x, np.ndarray)), None)
        if image is None:
            return np.zeros((target_h, target_w, 3), dtype=np.uint8)
        h, w = image.shape[:2]
        if w == target_w and h == target_h:
            return image
        if HAS_CV2:
            interp = cv2.INTER_AREA if (target_w <= w and target_h <= h) else cv2.INTER_LANCZOS4
            return cv2.resize(image, (target_w, target_h), interpolation=interp)
        else:
            if image.dtype != np.uint8:
                u8 = MediaExporter._to_uint8(image)
                pil_img = Image.fromarray(u8).resize((target_w, target_h), Image.Resampling.LANCZOS)
                return np.array(pil_img)
            else:
                pil_img = Image.fromarray(image).resize((target_w, target_h), Image.Resampling.LANCZOS)
                return np.array(pil_img)

    @staticmethod
    def save_exr(
        file_path: str | Path,
        image_data: np.ndarray,
        compression: str = "zip",
        bit_depth: str = "16",
    ) -> None:
        """Save a float32 or float16 RGB/RGBA numpy array to an OpenEXR file."""
        p = Path(file_path)
        p.parent.mkdir(parents=True, exist_ok=True)

        if not HAS_OPENEXR:
            if HAS_CV2:
                bgr = cv2.cvtColor(image_data.astype(np.float32), cv2.COLOR_RGB2BGR) if image_data.ndim >= 3 and image_data.shape[2] >= 3 else image_data
                cv2.imwrite(str(p), bgr)
                return
            raise RuntimeError("OpenEXR or OpenCV is required for EXR export.")

        h, w = image_data.shape[:2]
        header = OpenEXR.Header(w, h)

        comp_lower = str(compression).lower()
        if comp_lower == "piz":
            header['compression'] = Imath.Compression(Imath.Compression.PIZ_COMPRESSION)
        elif comp_lower == "rle":
            header['compression'] = Imath.Compression(Imath.Compression.RLE_COMPRESSION)
        elif comp_lower == "dwaa":
            header['compression'] = Imath.Compression(Imath.Compression.DWAA_COMPRESSION)
        elif comp_lower == "dwab":
            header['compression'] = Imath.Compression(Imath.Compression.DWAB_COMPRESSION)
        elif comp_lower in ("none", "uncompressed"):
            header['compression'] = Imath.Compression(Imath.Compression.NO_COMPRESSION)
        else:
            header['compression'] = Imath.Compression(Imath.Compression.ZIP_COMPRESSION)

        is_half = str(bit_depth).strip() in ("16", "half", "Half")
        pt = Imath.PixelType(Imath.PixelType.HALF if is_half else Imath.PixelType.FLOAT)
        dtype = np.float16 if is_half else np.float32

        has_alpha = (image_data.ndim >= 3 and image_data.shape[2] >= 4)
        if has_alpha:
            header['channels'] = {
                'R': Imath.Channel(pt),
                'G': Imath.Channel(pt),
                'B': Imath.Channel(pt),
                'A': Imath.Channel(pt),
            }
            r = image_data[:, :, 0].astype(dtype, copy=False).tobytes()
            g = image_data[:, :, 1].astype(dtype, copy=False).tobytes()
            b = image_data[:, :, 2].astype(dtype, copy=False).tobytes()
            a = image_data[:, :, 3].astype(dtype, copy=False).tobytes()
            pixels = {'R': r, 'G': g, 'B': b, 'A': a}
        else:
            header['channels'] = {
                'R': Imath.Channel(pt),
                'G': Imath.Channel(pt),
                'B': Imath.Channel(pt),
            }
            r = image_data[:, :, 0].astype(dtype, copy=False).tobytes()
            g = image_data[:, :, 1].astype(dtype, copy=False).tobytes()
            b = image_data[:, :, 2].astype(dtype, copy=False).tobytes()
            pixels = {'R': r, 'G': g, 'B': b}

        out = OpenEXR.OutputFile(str(p), header)
        try:
            out.writePixels(pixels)
        finally:
            out.close()

    @staticmethod
    def save_dpx(
        file_path: str | Path,
        image_data: np.ndarray,
        bit_depth: int = 10,
    ) -> None:
        """Save an image array to a standard 10-bit or 16-bit SMPTE 268M DPX file."""
        if isinstance(image_data, (tuple, list)):
            image_data = next((x for x in image_data if isinstance(x, np.ndarray)), None)
        if image_data is None:
            image_data = np.zeros((1080, 1920, 3), dtype=np.uint8)
        p = Path(file_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        from openreview.core.image_io import write_dpx
        if image_data.dtype == np.uint8:
            arr = image_data.astype(np.float32) / 255.0
        elif image_data.dtype == np.uint16:
            arr = image_data.astype(np.float32) / 65535.0
        else:
            arr = image_data.astype(np.float32)
        success = write_dpx(str(p), arr, bit_depth=bit_depth)
        if not success:
            raise RuntimeError(f"Failed to write DPX file to {p}")

    @staticmethod
    def save_image_file(
        image_data: np.ndarray,
        file_path: str | Path,
        compression: str | int = "zip",
        bit_depth: str = "8",
        quality: int = 95,
        dither: bool = True,
        png_mode: str = "png24_opt",
    ) -> None:
        """Save image array to disk in EXR, DPX, TIFF, PNG, JPEG, or Targa format."""
        if isinstance(image_data, (tuple, list)):
            image_data = next((x for x in image_data if isinstance(x, np.ndarray)), None)
        if image_data is None:
            image_data = np.zeros((1080, 1920, 3), dtype=np.uint8)
        p = Path(file_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        ext = p.suffix.lower()

        if ext == ".exr":
            if image_data.dtype == np.uint8:
                f_img = image_data.astype(np.float32) / 255.0
            elif image_data.dtype == np.uint16:
                f_img = image_data.astype(np.float32) / 65535.0
            else:
                f_img = image_data.astype(np.float32)
            MediaExporter.save_exr(p, f_img, compression=str(compression), bit_depth=bit_depth)
            return

        if ext == ".dpx":
            bd = 16 if str(bit_depth).strip() == "16" else 10
            MediaExporter.save_dpx(p, image_data, bit_depth=bd)
            return

        if ext in (".tif", ".tiff"):
            if str(bit_depth).strip() == "16":
                if image_data.dtype in (np.float32, np.float64, np.float16):
                    arr_16 = np.clip(image_data * 65535.0, 0, 65535).astype(np.uint16)
                elif image_data.dtype == np.uint8:
                    arr_16 = (image_data.astype(np.uint16) * 257)
                else:
                    arr_16 = image_data.astype(np.uint16)
                if HAS_CV2:
                    bgr = cv2.cvtColor(arr_16, cv2.COLOR_RGB2BGR) if arr_16.ndim >= 3 and arr_16.shape[2] >= 3 else arr_16
                    cv2.imwrite(str(p), bgr)
                else:
                    img = Image.fromarray(arr_16)
                    img.save(str(p))
                return
            else:
                arr_8 = MediaExporter._to_uint8(image_data)
                img = Image.fromarray(arr_8)
                img.save(str(p))
                return

        if ext == ".png":
            # Extract PNG compression level (0..9, default 4 for balanced size & speed)
            png_comp = 4
            if isinstance(compression, int):
                png_comp = max(0, min(9, compression))
            elif isinstance(compression, str):
                s = compression.strip().lower()
                if s.isdigit():
                    png_comp = max(0, min(9, int(s)))
                elif "fast" in s:
                    png_comp = 1
                elif "max" in s:
                    png_comp = 9
                elif "bal" in s:
                    png_comp = 4

            is_16bit = str(bit_depth).strip() in ("16", "half", "deep") or png_mode == "16"
            is_png8 = (png_mode == "png8" or str(bit_depth).strip() in ("png8", "8_indexed", "quantize"))

            if is_16bit:
                if image_data.dtype in (np.float32, np.float64, np.float16):
                    arr_16 = np.clip(image_data * 65535.0, 0, 65535).astype(np.uint16)
                elif image_data.dtype == np.uint8:
                    arr_16 = (image_data.astype(np.uint16) * 257)
                else:
                    arr_16 = image_data.astype(np.uint16)
                if HAS_CV2:
                    bgr = cv2.cvtColor(arr_16, cv2.COLOR_RGB2BGR) if arr_16.ndim >= 3 and arr_16.shape[2] >= 3 else arr_16
                    cv2.imwrite(str(p), bgr, [cv2.IMWRITE_PNG_COMPRESSION, png_comp])
                else:
                    img = Image.fromarray(arr_16)
                    img.save(str(p), compress_level=png_comp)
                return

            # Convert to uint8 (H, W, C)
            arr_8 = MediaExporter._to_uint8(image_data)

            if is_png8:
                # Ultra-Compressed 8-bit Indexed Palette (PNG-8)
                # Adaptive 256 colors with optional Floyd-Steinberg dithering (~70% smaller)
                has_alpha = (arr_8.ndim == 3 and arr_8.shape[2] == 4)
                img = Image.fromarray(arr_8, mode="RGBA" if has_alpha else "RGB")
                dither_algo = Image.Dither.FLOYDSTEINBERG if dither else Image.Dither.NONE
                quantized = img.quantize(colors=256, method=Image.Quantize.MEDIANCUT, dither=dither_algo)
                quantized.save(str(p), format="PNG", optimize=True, compress_level=png_comp)
                return
            else:
                # PNG-24 Truecolor Optimized (Strictly Lossless)
                if HAS_CV2:
                    bgr = cv2.cvtColor(arr_8, cv2.COLOR_RGB2BGR) if arr_8.ndim >= 3 and arr_8.shape[2] >= 3 else arr_8
                    cv2.imwrite(str(p), bgr, [cv2.IMWRITE_PNG_COMPRESSION, png_comp])
                else:
                    img = Image.fromarray(arr_8)
                    img.save(str(p), optimize=True, compress_level=png_comp)
                return

        if ext in (".jpg", ".jpeg"):
            arr_8 = MediaExporter._to_uint8(image_data)
            if HAS_CV2:
                bgr = cv2.cvtColor(arr_8, cv2.COLOR_RGB2BGR) if arr_8.ndim >= 3 and arr_8.shape[2] >= 3 else arr_8
                cv2.imwrite(str(p), bgr, [cv2.IMWRITE_JPEG_QUALITY, int(quality)])
            else:
                img = Image.fromarray(arr_8)
                img.save(str(p), quality=int(quality))
            return

        if ext == ".tga":
            arr_8 = MediaExporter._to_uint8(image_data)
            img = Image.fromarray(arr_8)
            img.save(str(p))
            return

        arr_8 = MediaExporter._to_uint8(image_data)
        img = Image.fromarray(arr_8)
        img.save(str(p))

    @staticmethod
    def export_video(
        frame_provider: Callable[[int], np.ndarray],
        frames: List[int],
        output_path: str | Path,
        fps: float = 24.0,
        progress_callback: Optional[Callable[[int, int], None]] = None,
        cancel_token: Optional[Callable[[], bool]] = None,
    ) -> str:
        """
        Export frames to a video container (.mov / .mp4) using OpenCV VideoWriter.
        """
        if not HAS_CV2:
            raise RuntimeError("OpenCV (cv2) is required for video export.")

        if not frames:
            raise ValueError("No frames provided for video export.")

        p = Path(output_path)
        p.parent.mkdir(parents=True, exist_ok=True)

        first_frame = frame_provider(frames[0])
        h, w = first_frame.shape[:2]

        ext = p.suffix.lower()
        fourcc = cv2.VideoWriter_fourcc(*"mp4v")

        writer = cv2.VideoWriter(str(p), fourcc, float(fps), (w, h))
        if not writer.isOpened():
            raise RuntimeError(f"Failed to open video writer for {p}")

        try:
            total = len(frames)
            for i, f in enumerate(frames):
                if cancel_token and cancel_token():
                    break
                img_rgb = frame_provider(f)
                img_u8 = MediaExporter._to_uint8(img_rgb)
                if img_u8.shape[0] != h or img_u8.shape[1] != w:
                    img_u8 = cv2.resize(img_u8, (w, h))

                img_bgr = cv2.cvtColor(img_u8, cv2.COLOR_RGB2BGR)
                writer.write(img_bgr)

                if progress_callback:
                    progress_callback(i + 1, total)
        finally:
            writer.release()

        return str(p)

    _VERIFIED_ENCODERS: Dict[str, bool] = {}

    @staticmethod
    def detect_hardware_encoders() -> Dict[str, bool]:
        """Query FFmpeg binary to check built-in hardware encoder modules."""
        ffmpeg_bin = shutil.which("ffmpeg")
        if not ffmpeg_bin:
            return {}
        try:
            res = subprocess.run([ffmpeg_bin, "-encoders"], capture_output=True, text=True, timeout=5.0)
            out = res.stdout.lower()
            return {
                "nvenc": "h264_nvenc" in out,
                "qsv": "h264_qsv" in out,
                "amf": "h264_amf" in out,
                "videotoolbox": "h264_videotoolbox" in out,
                "mf": "h264_mf" in out,
                "prores_ks": "prores_ks" in out,
                "libx264": "libx264" in out,
                "libx265": "libx265" in out,
            }
        except Exception:
            return {}

    @staticmethod
    def is_encoder_functional(encoder_name: str) -> bool:
        """Run a micro-probe to verify if the hardware encoder initializes on the GPU."""
        if encoder_name in MediaExporter._VERIFIED_ENCODERS:
            return MediaExporter._VERIFIED_ENCODERS[encoder_name]
        ffmpeg_bin = shutil.which("ffmpeg")
        if not ffmpeg_bin:
            return False
        cmd = [
            ffmpeg_bin, "-y",
            "-f", "lavfi", "-i", "color=c=black:s=64x64:d=0.04",
            "-c:v", encoder_name,
            "-f", "null", "-"
        ]
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=3.0)
            ok = (res.returncode == 0)
        except Exception:
            ok = False
        MediaExporter._VERIFIED_ENCODERS[encoder_name] = ok
        return ok

    @staticmethod
    def resolve_video_encoder(
        codec: str,
        hwaccel: str = "auto",
        crf: int = 18,
        is_mov: bool = False,
    ) -> Tuple[str, List[str], str]:
        """
        Resolve the optimal video encoder and FFmpeg command parameters.
        Supports:
          - Apple ProRes: Proxy (0), LT (1), 422 (2), HQ (3), 4444 (4), 4444 XQ (5)
          - NVIDIA NVENC: h264_nvenc, hevc_nvenc, av1_nvenc
          - Intel QuickSync: h264_qsv, hevc_qsv, av1_qsv
          - AMD AMF: h264_amf, hevc_amf, av1_amf
          - Apple VideoToolbox: h264_videotoolbox, hevc_videotoolbox, prores_videotoolbox
          - Windows MediaFoundation: h264_mf, hevc_mf
          - CPU software fallback: libx264, libx265, prores_ks
        Returns: (encoder_name, extra_args_list, description_str)
        """
        codec_str = str(codec).strip().lower()
        hw_pref = str(hwaccel).strip().lower()

        # 1. Apple ProRes Profiles (.mov container)
        if "prores" in codec_str:
            if "proxy" in codec_str:
                profile = "0"
                desc_name = "Proxy"
            elif "lt" in codec_str:
                profile = "1"
                desc_name = "LT"
            elif "4444 xq" in codec_str or "xq" in codec_str:
                profile = "5"
                desc_name = "4444 XQ"
            elif "4444" in codec_str:
                profile = "4"
                desc_name = "4444"
            elif "422" in codec_str and "hq" not in codec_str:
                profile = "2"
                desc_name = "422 Standard"
            else:
                profile = "3"
                desc_name = "422 HQ"

            pix_fmt = "yuva444p10le" if profile in ("4", "5") else "yuv422p10le"

            # Check Apple VideoToolbox hardware ProRes on macOS
            if hw_pref in ("auto", "videotoolbox") and MediaExporter.is_encoder_functional("prores_videotoolbox"):
                return (
                    "prores_videotoolbox",
                    ["-c:v", "prores_videotoolbox", "-profile:v", profile, "-pix_fmt", pix_fmt],
                    f"Apple ProRes {desc_name} (VideoToolbox Hardware)",
                )

            return (
                "prores_ks",
                ["-c:v", "prores_ks", "-profile:v", profile, "-pix_fmt", pix_fmt],
                f"Apple ProRes {desc_name} (iCodec Pro 10-bit)",
            )

        # 2. H.265 / HEVC
        if "265" in codec_str or "hevc" in codec_str:
            # NVIDIA NVENC
            if (hw_pref in ("auto", "nvenc") or "nvenc" in codec_str) and MediaExporter.is_encoder_functional("hevc_nvenc"):
                return (
                    "hevc_nvenc",
                    ["-c:v", "hevc_nvenc", "-preset", "p4", "-cq", str(crf), "-pix_fmt", "yuv420p"],
                    "H.265 / HEVC (NVIDIA NVENC GPU)",
                )
            # Intel QuickSync
            if (hw_pref in ("auto", "qsv") or "qsv" in codec_str or "quicksync" in codec_str) and MediaExporter.is_encoder_functional("hevc_qsv"):
                return (
                    "hevc_qsv",
                    ["-c:v", "hevc_qsv", "-global_quality", str(crf), "-pix_fmt", "yuv420p"],
                    "H.265 / HEVC (Intel QuickSync GPU)",
                )
            # Apple VideoToolbox
            if (hw_pref in ("auto", "videotoolbox") or "videotoolbox" in codec_str) and MediaExporter.is_encoder_functional("hevc_videotoolbox"):
                return (
                    "hevc_videotoolbox",
                    ["-c:v", "hevc_videotoolbox", "-q:v", str(max(10, 100 - crf * 2)), "-pix_fmt", "yuv420p"],
                    "H.265 / HEVC (Apple VideoToolbox GPU)",
                )
            # AMD AMF
            if (hw_pref in ("auto", "amf") or "amf" in codec_str) and MediaExporter.is_encoder_functional("hevc_amf"):
                return (
                    "hevc_amf",
                    ["-c:v", "hevc_amf", "-quality", "quality", "-pix_fmt", "yuv420p"],
                    "H.265 / HEVC (AMD AMF GPU)",
                )
            # Windows MediaFoundation
            if (hw_pref in ("auto", "mf") or "mediafoundation" in codec_str) and MediaExporter.is_encoder_functional("hevc_mf"):
                return (
                    "hevc_mf",
                    ["-c:v", "hevc_mf", "-b:v", "10M", "-pix_fmt", "yuv420p"],
                    "H.265 / HEVC (MediaFoundation GPU)",
                )
            # Software Fallback
            return (
                "libx265",
                ["-c:v", "libx265", "-crf", str(crf), "-preset", "medium", "-pix_fmt", "yuv420p"],
                "H.265 / HEVC (libx265 CPU)",
            )

        # 3. H.264 / AVC (Default)
        # NVIDIA NVENC
        if (hw_pref in ("auto", "nvenc") or "nvenc" in codec_str) and MediaExporter.is_encoder_functional("h264_nvenc"):
            return (
                "h264_nvenc",
                ["-c:v", "h264_nvenc", "-preset", "p4", "-cq", str(crf), "-pix_fmt", "yuv420p"],
                "H.264 (NVIDIA NVENC GPU)",
            )
        # Intel QuickSync
        if (hw_pref in ("auto", "qsv") or "qsv" in codec_str or "quicksync" in codec_str) and MediaExporter.is_encoder_functional("h264_qsv"):
            return (
                "h264_qsv",
                ["-c:v", "h264_qsv", "-global_quality", str(crf), "-pix_fmt", "yuv420p"],
                "H.264 (Intel QuickSync GPU)",
            )
        # Apple VideoToolbox
        if (hw_pref in ("auto", "videotoolbox") or "videotoolbox" in codec_str) and MediaExporter.is_encoder_functional("h264_videotoolbox"):
            return (
                "h264_videotoolbox",
                ["-c:v", "h264_videotoolbox", "-q:v", str(max(10, 100 - crf * 2)), "-pix_fmt", "yuv420p"],
                "H.264 (Apple VideoToolbox GPU)",
            )
        # AMD AMF
        if (hw_pref in ("auto", "amf") or "amf" in codec_str) and MediaExporter.is_encoder_functional("h264_amf"):
            return (
                "h264_amf",
                ["-c:v", "h264_amf", "-quality", "quality", "-pix_fmt", "yuv420p"],
                "H.264 (AMD AMF GPU)",
            )
        # Windows MediaFoundation
        if (hw_pref in ("auto", "mf") or "mediafoundation" in codec_str) and MediaExporter.is_encoder_functional("h264_mf"):
            return (
                "h264_mf",
                ["-c:v", "h264_mf", "-b:v", "10M", "-pix_fmt", "yuv420p"],
                "H.264 (MediaFoundation GPU)",
            )

        # Software Fallback
        return (
            "libx264",
            ["-c:v", "libx264", "-crf", str(crf), "-preset", "medium", "-pix_fmt", "yuv420p"],
            "H.264 (libx264 CPU)",
        )

    @staticmethod
    def export_video_ffmpeg(
        frame_provider: Callable[[int], np.ndarray],
        frames: List[int],
        output_path: str | Path,
        fps: float = 24.0,
        codec: str = "Apple ProRes 422 HQ",
        crf: int = 18,
        hwaccel: str = "auto",
        progress_callback: Optional[Callable[[int, int], None]] = None,
        cancel_token: Optional[Callable[[], bool]] = None,
    ) -> str:
        """
        Export frames to QuickTime (.mov) or MP4 (.mp4) using FFmpeg rawvideo pipe.
        Supports Apple ProRes (Proxy, 422, HQ, 4444) and H.264 / H.265.
        Falls back cleanly to OpenCV VideoWriter if FFmpeg is unavailable.
        """
        ffmpeg_bin = shutil.which("ffmpeg")
        if not ffmpeg_bin:
            return MediaExporter.export_video(frame_provider, frames, output_path, fps, progress_callback, cancel_token)

        p = Path(output_path)
        p.parent.mkdir(parents=True, exist_ok=True)

        if not frames:
            raise ValueError("No frames provided for video export.")

        first_frame = frame_provider(frames[0])
        h, w = first_frame.shape[:2]
        # Video codecs require even dimensions
        if w % 2 != 0:
            w -= 1
        if h % 2 != 0:
            h -= 1

        is_mov_file = (p.suffix.lower() == ".mov")
        enc_name, codec_args, enc_desc = MediaExporter.resolve_video_encoder(
            codec, hwaccel=hwaccel, crf=crf, is_mov=is_mov_file
        )

        cmd = [
            ffmpeg_bin,
            "-y",
            "-f", "rawvideo",
            "-vcodec", "rawvideo",
            "-s", f"{w}x{h}",
            "-pix_fmt", "rgb24",
            "-r", f"{fps:.4f}",
            "-i", "-",
            *codec_args,
            str(p),
        ]

        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        try:
            total = len(frames)
            for i, f in enumerate(frames):
                if cancel_token and cancel_token():
                    proc.kill()
                    return str(p)
                img = frame_provider(f)
                if img.shape[0] != h or img.shape[1] != w:
                    img = MediaExporter.resize_frame(img, w, h)
                img_u8 = MediaExporter._to_uint8(img)
                if not img_u8.flags["C_CONTIGUOUS"]:
                    img_u8 = np.ascontiguousarray(img_u8)
                proc.stdin.write(img_u8.tobytes())
                if progress_callback:
                    progress_callback(i + 1, total)

            proc.stdin.close()
            stdout_data, stderr_data = proc.communicate(timeout=60)
            if proc.returncode != 0:
                stderr_text = stderr_data.decode("utf-8", errors="ignore") if stderr_data else ""
                if enc_name not in ("libx264", "libx265", "prores_ks"):
                    logger.warning("Hardware encoder %s failed (%s). Retrying with software CPU encoder...", enc_name, stderr_text[:200])
                    return MediaExporter.export_video_ffmpeg(
                        frame_provider,
                        frames,
                        output_path,
                        fps=fps,
                        codec=codec,
                        crf=crf,
                        hwaccel="cpu",
                        progress_callback=progress_callback,
                        cancel_token=cancel_token,
                    )
                raise RuntimeError(f"FFmpeg export failed (code {proc.returncode}): {stderr_text}")
        except Exception as e:
            if proc.poll() is None:
                proc.kill()
            raise e

        return str(p)

    @staticmethod
    def export_sequence(
        frame_provider: Callable[[int], np.ndarray],
        frames: List[int],
        output_dir: str | Path,
        filename_prefix: str = "frame",
        file_ext: str = "png",
        pad: int = 4,
        compression: str = "4",
        bit_depth: str = "8",
        quality: int = 95,
        progress_callback: Optional[Callable[[int, int], None]] = None,
        cancel_token: Optional[Callable[[], bool]] = None,
        max_workers: int = 4,
        dither: bool = True,
        png_mode: str = "png8",
    ) -> List[str]:
        """
        Export list of frames as an image sequence with parallel writing.
        e.g. output_dir/frame.0101.png
        """
        out_dir = Path(output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)

        ext = file_ext.lstrip(".")
        total = len(frames)
        if total == 0:
            return []

        # Determine thread count
        num_workers = min(max_workers, max(1, os.cpu_count() or 4), total)
        if num_workers <= 1 or total == 1:
            saved_paths: List[str] = []
            for i, f in enumerate(frames):
                if cancel_token and cancel_token():
                    break
                frame_num_str = f"{f:0{pad}d}"
                filename = f"{filename_prefix}.{frame_num_str}.{ext}"
                file_path = out_dir / filename
                img = frame_provider(f)
                MediaExporter.save_image_file(
                    img,
                    file_path,
                    compression=compression,
                    bit_depth=bit_depth,
                    quality=quality,
                    dither=dither,
                    png_mode=png_mode,
                )
                saved_paths.append(str(file_path))
                if progress_callback:
                    progress_callback(i + 1, total)
            return saved_paths

        # Multithreaded pipelined writing with bounded memory
        saved_paths = [None] * total
        completed_count = 0
        lock = Lock()
        sem = BoundedSemaphore(num_workers * 2)

        def _worker_write(idx: int, frame_img: np.ndarray, target_path: Path):
            nonlocal completed_count
            try:
                if not (cancel_token and cancel_token()):
                    MediaExporter.save_image_file(
                        frame_img,
                        target_path,
                        compression=compression,
                        bit_depth=bit_depth,
                        quality=quality,
                        dither=dither,
                        png_mode=png_mode,
                    )
                    with lock:
                        saved_paths[idx] = str(target_path)
            finally:
                sem.release()
                with lock:
                    completed_count += 1
                    cur_comp = completed_count
                if progress_callback:
                    progress_callback(cur_comp, total)

        with ThreadPoolExecutor(max_workers=num_workers) as executor:
            for i, f in enumerate(frames):
                if cancel_token and cancel_token():
                    break
                sem.acquire()
                frame_num_str = f"{f:0{pad}d}"
                filename = f"{filename_prefix}.{frame_num_str}.{ext}"
                file_path = out_dir / filename
                img = frame_provider(f)
                executor.submit(_worker_write, i, img, file_path)

        return [p for p in saved_paths if p is not None]

    @staticmethod
    def _build_smooth_bezier_path(points: list) -> Any:
        from PySide6.QtGui import QPainterPath
        from PySide6.QtCore import QPointF
        path = QPainterPath()
        if not points:
            return path
        if len(points) == 1:
            path.moveTo(points[0])
            path.lineTo(points[0])
            return path
        if len(points) == 2:
            path.moveTo(points[0])
            path.lineTo(points[1])
            return path

        filtered = [points[0]]
        for p in points[1:]:
            dx = p.x() - filtered[-1].x()
            dy = p.y() - filtered[-1].y()
            if dx * dx + dy * dy >= 2.25:
                filtered.append(p)
        if len(filtered) < 2:
            filtered = [points[0], points[-1]]

        n = len(filtered)
        if n == 2:
            path.moveTo(filtered[0])
            path.lineTo(filtered[1])
            return path

        p_prev = QPointF(2.0 * filtered[0].x() - filtered[1].x(), 2.0 * filtered[0].y() - filtered[1].y())
        p_next = QPointF(2.0 * filtered[-1].x() - filtered[-2].x(), 2.0 * filtered[-1].y() - filtered[-2].y())
        pts = [p_prev] + filtered + [p_next]

        path.moveTo(filtered[0])
        for i in range(1, n):
            p0 = pts[i - 1]
            p1 = pts[i]
            p2 = pts[i + 1]
            p3 = pts[i + 2]
            c1 = QPointF(p1.x() + (p2.x() - p0.x()) / 6.0, p1.y() + (p2.y() - p0.y()) / 6.0)
            c2 = QPointF(p2.x() - (p3.x() - p1.x()) / 6.0, p2.y() - (p3.y() - p1.y()) / 6.0)
            path.cubicTo(c1, c2, p2)

        return path

    @staticmethod
    def render_strokes_on_image(base_img: np.ndarray, strokes: list) -> np.ndarray:
        """
        Renders vector markup strokes directly onto a base image at native image resolution,
        completely independent of screen display size, viewport pan, and zoom.
        """
        if isinstance(base_img, (tuple, list)):
            base_img = next((x for x in base_img if isinstance(x, np.ndarray)), None)

        if base_img is None:
            return np.zeros((1080, 1920, 3), dtype=np.uint8)

        if base_img.dtype != np.uint8:
            rgb_u8 = np.clip(base_img[:, :, :3] * 255.0, 0, 255).astype(np.uint8)
        else:
            rgb_u8 = base_img[:, :, :3].copy()

        if not strokes:
            return rgb_u8

        h, w = rgb_u8.shape[:2]

        from PySide6.QtGui import QPainter, QColor, QPen, QFont, QImage
        from PySide6.QtCore import Qt, QPointF, QRect, QRectF
        import math
        from openreview.models.annotations import ShapeType

        # Create QImage copy from contiguous buffer
        qimg = QImage(rgb_u8.data, w, h, rgb_u8.strides[0], QImage.Format.Format_RGB888).copy()

        painter = QPainter(qimg)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        scale = max(1.0, max(w, h) / 1920.0)

        for stroke in strokes:
            if not stroke.points:
                continue

            color = QColor(*stroke.color)
            scaled_width = max(1.0, stroke.width * scale)
            pen = QPen(color, scaled_width, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
            painter.setPen(pen)

            # Map normalized [0.0, 1.0] coordinates to native image pixel coordinates
            pts = [QPointF(p[0] * w, p[1] * h) for p in stroke.points]

            if stroke.shape_type == ShapeType.PEN:
                if len(pts) == 1:
                    painter.drawPoint(pts[0])
                else:
                    path = MediaExporter._build_smooth_bezier_path(pts)
                    painter.drawPath(path)

            elif stroke.shape_type == ShapeType.LINE and len(pts) >= 2:
                painter.drawLine(pts[0], pts[-1])

            elif stroke.shape_type == ShapeType.ARROW and len(pts) >= 2:
                p0 = pts[0]
                p1 = pts[-1]
                painter.drawLine(p0, p1)
                dx = p1.x() - p0.x()
                dy = p1.y() - p0.y()
                length = math.hypot(dx, dy)
                if length > 5:
                    ux = dx / length
                    uy = dy / length
                    arrow_sz = 14.0 * scale
                    left_x = p1.x() - arrow_sz * (ux + uy * 0.5)
                    left_y = p1.y() - arrow_sz * (uy - ux * 0.5)
                    right_x = p1.x() - arrow_sz * (ux - uy * 0.5)
                    right_y = p1.y() - arrow_sz * (uy + ux * 0.5)
                    painter.drawLine(p1, QPointF(left_x, left_y))
                    painter.drawLine(p1, QPointF(right_x, right_y))

            elif stroke.shape_type == ShapeType.RECTANGLE and len(pts) >= 2:
                p0 = pts[0]
                p1 = pts[-1]
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawRect(QRectF(min(p0.x(), p1.x()), min(p0.y(), p1.y()), abs(p1.x() - p0.x()), abs(p1.y() - p0.y())))

            elif stroke.shape_type == ShapeType.CIRCLE and len(pts) >= 2:
                p0 = pts[0]
                p1 = pts[-1]
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawEllipse(QRectF(min(p0.x(), p1.x()), min(p0.y(), p1.y()), abs(p1.x() - p0.x()), abs(p1.y() - p0.y())))

            elif stroke.shape_type == ShapeType.TEXT and pts:
                p0 = pts[0]
                txt = stroke.text or "Note"
                base_font_pt = max(10, int(stroke.width * 3.5)) if stroke.width > 1.0 else 12
                font_pt = max(8, int(base_font_pt * scale))
                font = QFont("Segoe UI", font_pt, QFont.Weight.Bold)
                painter.setFont(font)
                metrics = painter.fontMetrics()

                lines = txt.split("\n")
                line_height = metrics.lineSpacing()
                max_line_w = max((metrics.horizontalAdvance(line) for line in lines), default=int(40 * scale))
                total_h = line_height * len(lines)

                pad_x = int(8 * scale)
                pad_y = int(6 * scale)
                box_w = max_line_w + pad_x * 2
                box_h = total_h + pad_y * 2

                box_x = int(p0.x())
                box_y = int(p0.y())
                text_rect = QRect(box_x, box_y, box_w, box_h)

                painter.setBrush(QColor(18, 18, 18, 220))
                painter.setPen(QPen(color, max(1.0, 1.5 * scale)))
                painter.drawRoundedRect(text_rect, 4.0 * scale, 4.0 * scale)

                painter.setPen(color)
                cur_y = box_y + pad_y + metrics.ascent()
                for line in lines:
                    painter.drawText(box_x + pad_x, cur_y, line)
                    cur_y += line_height

        painter.end()

        # Convert back to uint8 numpy array
        qimg = qimg.convertToFormat(QImage.Format.Format_RGB888)
        w, h = qimg.width(), qimg.height()
        ptr = qimg.constBits()
        return np.frombuffer(ptr, dtype=np.uint8).reshape((h, w, 3)).copy()

    @staticmethod
    def export_pdf_review_sheet(
        output_pdf_path: str | Path,
        entries: List[dict],
        title: Optional[str] = None,
        fps: float = 24.0,
        pdf_config: Optional[dict] = None,
        progress_callback: Optional[Callable[[int, int, str], None]] = None,
    ) -> str:
        """
        Generate a structured, professional studio PDF review report using reportlab.
        Includes high-resolution frame thumbnails with burned-in annotations and review notes.
        Fully customizable via user preferences or explicit pdf_config overrides.
        """
        import tempfile
        from reportlab.lib.pagesizes import letter, A4, legal, landscape, portrait
        from reportlab.lib import colors
        from reportlab.platypus import (
            SimpleDocTemplate,
            Paragraph,
            Spacer,
            Table,
            TableStyle,
            Image as RLImage,
        )
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle

        p = Path(output_pdf_path)
        p.parent.mkdir(parents=True, exist_ok=True)

        # Pull defaults from PreferencesManager if available
        prefs = None
        try:
            from openreview.core.preferences import PreferencesManager
            prefs = PreferencesManager.instance().prefs
        except Exception:
            pass

        cfg = pdf_config or {}

        # 1. Resolve branding & header settings
        doc_title = title or cfg.get("title") or getattr(prefs, "pdf_title", "OpenReview - VFX Review Sheet")
        studio_name = cfg.get("studio_name") or getattr(prefs, "pdf_studio_name", "")
        author_name = cfg.get("author_name") or getattr(prefs, "pdf_author_name", "")
        logo_path = cfg.get("logo_path") or getattr(prefs, "pdf_logo_path", "")
        header_color_hex = cfg.get("header_color") or getattr(prefs, "pdf_header_color", "#c28a10")

        # 2. Resolve metadata toggles
        show_date = cfg.get("show_date", getattr(prefs, "pdf_show_date", True))
        show_fps = cfg.get("show_fps", getattr(prefs, "pdf_show_fps", True))
        show_keyframe_count = cfg.get("show_keyframe_count", getattr(prefs, "pdf_show_keyframe_count", True))

        # 3. Resolve page size & orientation
        page_size_name = str(cfg.get("page_size", getattr(prefs, "pdf_page_size", "letter"))).lower()
        orientation_name = str(cfg.get("page_orientation", getattr(prefs, "pdf_page_orientation", "landscape"))).lower()

        base_sizes = {"letter": letter, "a4": A4, "legal": legal}
        raw_size = base_sizes.get(page_size_name, letter)
        final_pagesize = landscape(raw_size) if orientation_name == "landscape" else portrait(raw_size)

        # 4. Resolve layout mode & card element toggles
        layout_mode = str(cfg.get("layout_mode", getattr(prefs, "pdf_layout_mode", "detailed_row"))).lower()
        show_frame_num = cfg.get("show_frame_num", getattr(prefs, "pdf_show_frame_num", True))
        show_timecode = cfg.get("show_timecode", getattr(prefs, "pdf_show_timecode", True))
        show_stroke_count = cfg.get("show_stroke_count", getattr(prefs, "pdf_show_stroke_count", True))
        show_notes = cfg.get("show_notes", getattr(prefs, "pdf_show_notes", True))
        show_source_path = cfg.get("show_source_path", getattr(prefs, "pdf_show_source_path", True))

        doc = SimpleDocTemplate(
            str(p),
            pagesize=final_pagesize,
            leftMargin=36,
            rightMargin=36,
            topMargin=36,
            bottomMargin=36,
        )

        printable_width = final_pagesize[0] - 72

        styles = getSampleStyleSheet()
        try:
            head_c = colors.HexColor(header_color_hex)
        except Exception:
            head_c = colors.HexColor("#c28a10")

        title_style = ParagraphStyle(
            "DocTitle",
            parent=styles["Heading1"],
            fontSize=18,
            leading=22,
            textColor=head_c,
            fontName="Helvetica-Bold",
        )
        studio_style = ParagraphStyle(
            "StudioHeading",
            parent=styles["Normal"],
            fontSize=11,
            leading=14,
            textColor=colors.HexColor("#333333"),
            fontName="Helvetica-Bold",
        )
        sub_style = ParagraphStyle(
            "SubHeader",
            parent=styles["Normal"],
            fontSize=9,
            leading=13,
            textColor=colors.HexColor("#666666"),
            fontName="Helvetica",
        )
        item_title_style = ParagraphStyle(
            "ItemTitle",
            parent=styles["Heading2"],
            fontSize=11,
            leading=15,
            textColor=colors.HexColor("#1a1a1a"),
            fontName="Helvetica-Bold",
        )
        note_style = ParagraphStyle(
            "NoteStyle",
            parent=styles["Normal"],
            fontSize=9,
            leading=13,
            textColor=colors.HexColor("#2a2a2a"),
            fontName="Helvetica",
        )

        story = []
        temp_files = []
        try:
            # Helper to resolve and rasterize logo image (supporting SVG & bitmap formats with proportional auto-scaling)
            def _resolve_logo_image(path_str: str) -> Optional[tuple[str, float, float]]:
                if not path_str or not os.path.isfile(path_str):
                    return None
                try:
                    p_lower = path_str.lower()
                    if p_lower.endswith(".svg"):
                        from PySide6.QtSvg import QSvgRenderer
                        from PySide6.QtGui import QImage, QPainter
                        from PySide6.QtCore import Qt

                        renderer = QSvgRenderer(path_str)
                        if not renderer.isValid():
                            return None
                        d_size = renderer.defaultSize()
                        orig_w = float(d_size.width()) if d_size.width() > 0 else 200.0
                        orig_h = float(d_size.height()) if d_size.height() > 0 else 60.0

                        # Rasterize SVG to high-res transparent PNG
                        scale = min(1200.0 / orig_w, 400.0 / orig_h)
                        render_w = max(1, int(orig_w * max(1.0, scale)))
                        render_h = max(1, int(orig_h * max(1.0, scale)))

                        qimg = QImage(render_w, render_h, QImage.Format.Format_ARGB32)
                        qimg.fill(Qt.GlobalColor.transparent)
                        painter = QPainter(qimg)
                        renderer.render(painter)
                        painter.end()

                        tf = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
                        tf_path = tf.name
                        tf.close()
                        temp_files.append(tf_path)
                        qimg.save(tf_path)
                        final_path = tf_path
                    else:
                        final_path = path_str
                        from PIL import Image as PILImage
                        with PILImage.open(final_path) as im:
                            orig_w, orig_h = float(im.width), float(im.height)

                    # Proportional auto-scale (max 120 width x 45 height)
                    aspect = orig_w / max(1.0, orig_h)
                    target_w = min(120.0, 45.0 * aspect)
                    target_h = target_w / aspect
                    if target_h > 45.0:
                        target_h = 45.0
                        target_w = target_h * aspect

                    return final_path, target_w, target_h
                except Exception as e:
                    logger.warning("[MediaExporter] Could not process logo '%s': %s", path_str, e)
                    return None

            # Subtitle items
            import datetime
            date_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
            sub_items = []
            if show_date:
                sub_items.append(f"Generated on {date_str}")
            if show_fps:
                sub_items.append(f"FPS: {fps:.2f}")
            if show_keyframe_count:
                sub_items.append(f"Annotated Keyframes: {len(entries)}")
            if author_name:
                sub_items.append(f"Reviewer: {author_name}")

            subtitle_text = " &nbsp;|&nbsp; ".join(sub_items) if sub_items else ""

            # Build Header (with optional Studio Logo)
            header_text_elements = [Paragraph(doc_title, title_style)]
            if studio_name:
                header_text_elements.append(Paragraph(f"<b>Facility / Studio:</b> {studio_name}", studio_style))
            if subtitle_text:
                header_text_elements.append(Paragraph(subtitle_text, sub_style))

            logo_info = _resolve_logo_image(logo_path)
            if logo_info is not None:
                final_logo_path, logo_w, logo_h = logo_info
                col_w_logo = logo_w + 12
                logo_img = RLImage(final_logo_path, width=logo_w, height=logo_h)
                logo_table = Table([[logo_img, header_text_elements]], colWidths=[col_w_logo, printable_width - col_w_logo])
                logo_table.setStyle(
                    TableStyle([
                        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                        ("PADDING", (0, 0), (-1, -1), 0),
                    ])
                )
                story.append(logo_table)
            else:
                story.extend(header_text_elements)

            story.append(Spacer(1, 14))
            # Helper to build card content for an entry
            def _build_card_content(item: dict) -> list:
                f_num = item.get("frame", 0)
                tc = item.get("timecode", "00:00:00:00")
                note_text = item.get("note", "").strip() or "No review notes."
                st_count = item.get("strokes_count", 0)
                src_path = item.get("source_path", "")

                text_content = []
                hdr_parts = []
                if show_frame_num:
                    hdr_parts.append(f"<b>Frame:</b> {f_num}")
                if show_timecode:
                    hdr_parts.append(f"<b>Timecode:</b> {tc}")
                if hdr_parts:
                    text_content.append(Paragraph(" &nbsp;&nbsp;|&nbsp;&nbsp; ".join(hdr_parts), item_title_style))

                if show_source_path and src_path:
                    base_name = Path(src_path).name
                    text_content.append(Paragraph(f"<b>Source:</b> {base_name}", sub_style))

                if show_stroke_count:
                    text_content.append(Paragraph(f"<b>Markup:</b> {st_count} vector stroke(s)", sub_style))

                if show_notes:
                    text_content.append(Spacer(1, 4))
                    text_content.append(Paragraph("<b>Review Notes:</b>", sub_style))
                    text_content.append(Paragraph(note_text.replace("\n", "<br/>"), note_style))

                return text_content

            def _prepare_thumb(img_data: Optional[np.ndarray], target_w: int, target_h: int) -> RLImage:
                tf = tempfile.NamedTemporaryFile(suffix=".jpg", delete=False)
                tf_path = tf.name
                tf.close()
                temp_files.append(tf_path)

                if img_data is not None:
                    # Downscale to 2x thumbnail resolution for high-DPI retina sharpness without embedding massive uncompressed frames
                    render_w = max(int(target_w * 2), 640)
                    render_h = max(int(target_h * 2), 360)
                    h, w = img_data.shape[:2]
                    if w > render_w or h > render_h:
                        thumb_arr = MediaExporter.resize_frame(img_data, render_w, render_h)
                    else:
                        thumb_arr = img_data
                    MediaExporter.save_image_file(thumb_arr, tf_path, quality=90)
                else:
                    blank = np.zeros((100, 100, 3), dtype=np.uint8)
                    MediaExporter.save_image_file(blank, tf_path, quality=90)

                return RLImage(tf_path, width=target_w, height=target_h)

            total_entries = len(entries)

            if layout_mode == "dense_grid":
                # Dense Grid: 2 columns per row
                col_w = (printable_width - 12) / 2.0
                thumb_w = int(col_w - 16)
                thumb_h = int(thumb_w * 9.0 / 16.0)

                grid_cards = []
                for idx, item in enumerate(entries):
                    if progress_callback:
                        progress_callback(idx, total_entries + 1, f"Preparing PDF thumbnails ({idx + 1} of {total_entries})...")
                    img_data = item.get("image")
                    if isinstance(img_data, (tuple, list)):
                        img_data = next((x for x in img_data if isinstance(x, np.ndarray)), None)

                    thumb = _prepare_thumb(img_data, thumb_w, thumb_h)
                    card_elements = [thumb, Spacer(1, 4)] + _build_card_content(item)

                    card_table = Table([[card_elements]], colWidths=[col_w])
                    card_table.setStyle(
                        TableStyle([
                            ("VALIGN", (0, 0), (-1, -1), "TOP"),
                            ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f8f9fa")),
                            ("BOX", (0, 0), (-1, -1), 1, colors.HexColor("#dddddd")),
                            ("PADDING", (0, 0), (-1, -1), 6),
                        ])
                    )
                    grid_cards.append(card_table)

                # Pair up cards into 2-column rows
                for i in range(0, len(grid_cards), 2):
                    row_items = [grid_cards[i]]
                    if i + 1 < len(grid_cards):
                        row_items.append(grid_cards[i + 1])
                        widths = [col_w, col_w]
                    else:
                        row_items.append("")
                        widths = [col_w, col_w]

                    row_pair = Table([row_items], colWidths=widths)
                    row_pair.setStyle(
                        TableStyle([
                            ("VALIGN", (0, 0), (-1, -1), "TOP"),
                            ("PADDING", (0, 0), (-1, -1), 0),
                        ])
                    )
                    story.append(row_pair)
                    story.append(Spacer(1, 10))

            else:
                # Detailed Row: Thumbnail Left, Details Right
                is_landscape = (orientation_name == "landscape")
                thumb_w = 320 if is_landscape else 240
                thumb_h = int(thumb_w * 9.0 / 16.0)
                details_w = printable_width - (thumb_w + 10)

                for idx, item in enumerate(entries):
                    if progress_callback:
                        progress_callback(idx, total_entries + 1, f"Preparing PDF thumbnails ({idx + 1} of {total_entries})...")
                    img_data = item.get("image")
                    if isinstance(img_data, (tuple, list)):
                        img_data = next((x for x in img_data if isinstance(x, np.ndarray)), None)

                    thumb = _prepare_thumb(img_data, thumb_w, thumb_h)
                    text_content = _build_card_content(item)

                    row_table = Table([[thumb, text_content]], colWidths=[thumb_w + 10, details_w])
                    row_table.setStyle(
                        TableStyle([
                            ("VALIGN", (0, 0), (-1, -1), "TOP"),
                            ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f8f9fa")),
                            ("BOX", (0, 0), (-1, -1), 1, colors.HexColor("#dddddd")),
                            ("PADDING", (0, 0), (-1, -1), 8),
                        ])
                    )

                    story.append(row_table)
                    story.append(Spacer(1, 12))

            if progress_callback:
                progress_callback(total_entries, total_entries + 1, "Compiling PDF document...")
            doc.build(story)
            if progress_callback:
                progress_callback(total_entries + 1, total_entries + 1, "Done")
        finally:
            for tf_path in temp_files:
                try:
                    os.remove(tf_path)
                except Exception:
                    pass

        return str(p)

    @staticmethod
    def _linearize_input(rgb: np.ndarray, input_space: int = 0) -> np.ndarray:
        """Convert float32 RGB to scene-linear based on input color space index."""
        if 0 <= input_space <= 10:
            return apply_standard_to_linear(rgb, input_space)
        elif input_space == 101:  # ALEXA LogC
            cut, a, b, c, d, e, f = 0.1496582, 0.247190, 0.385537, 0.078277, 0.105910, 5.253018, 0.0400405
            lin = np.where(rgb > cut, (np.power(10.0, (rgb - b) / a) - d) / c, (rgb - f) / e)
            return np.maximum(lin, 0.0)
        elif input_space == 102:  # ALEXA LogC Film
            cut, a, b, c, d, e, f = 0.1496582, 0.247190, 0.385537, 0.078277, 0.105910, 5.253018, 0.0400405
            lin = np.where(rgb > cut, (np.power(10.0, (rgb - b) / a) - d) / c, (rgb - f) / e)
            return np.power(np.maximum(lin, 0.0), 1.15) * 1.1
        return rgb.copy()

    @staticmethod
    def _apply_color_grade(lin: np.ndarray, viewport) -> np.ndarray:
        """Apply exposure, gain, ASC CDL, grading edits, and 3D LUT to scene-linear float array."""
        if viewport is None:
            return lin

        # Scene-linear Exposure & Gain
        exposure_ev = float(getattr(viewport, "exposure_ev", 0.0))
        gain = float(getattr(viewport, "gain", 1.0))
        total_gain = gain * (2.0 ** exposure_ev)
        lin = lin * total_gain

        # Fast Saturation in Scene-Linear
        saturation = float(getattr(viewport, "saturation", 1.0))
        if abs(saturation - 1.0) > 0.0001:
            luma = lin[:, :, 0:1] * 0.2126 + lin[:, :, 1:2] * 0.7152 + lin[:, :, 2:3] * 0.0722
            lin = luma * (1.0 - saturation) + lin * saturation

        # ASC CDL Evaluation
        cg = getattr(viewport, "color_grading", None)
        if cg and getattr(cg, "cdl_enabled", False) and getattr(cg, "active_cdl", None):
            cdl = cg.active_cdl
            slope = np.array(cdl.slope, dtype=np.float32)
            offset = np.array(cdl.offset, dtype=np.float32)
            power = np.array(cdl.power, dtype=np.float32)
            sat = float(cdl.saturation)
            sop = np.power(np.maximum(lin * slope + offset, 0.0), np.maximum(power, 0.0001))
            luma = sop[:, :, 0:1] * 0.2126 + sop[:, :, 1:2] * 0.7152 + sop[:, :, 2:3] * 0.0722
            lin = np.maximum(0.0, luma * (1.0 - sat) + sop * sat)

        # Interactive Color Grading Edits
        if cg:
            g_gain = np.array(cg.gain, dtype=np.float32)
            g_offset = np.array(cg.offset, dtype=np.float32)
            lin = lin * g_gain + g_offset

            if cg.gamma > 0.001 and abs(cg.gamma - 1.0) > 0.001:
                lin = np.power(np.maximum(lin, 0.0), 1.0 / cg.gamma)
            if abs(cg.contrast - 1.0) > 0.001:
                lin = np.maximum(0.0, (lin - 0.18) * cg.contrast + 0.18)
            if abs(cg.saturation - 1.0) > 0.001:
                luma = lin[:, :, 0:1] * 0.2126 + lin[:, :, 1:2] * 0.7152 + lin[:, :, 2:3] * 0.0722
                lin = np.maximum(0.0, luma * (1.0 - cg.saturation) + lin * cg.saturation)
            if abs(cg.hue) > 0.01:
                angle = np.radians(cg.hue)
                cos_a, sin_a = np.cos(angle), np.sin(angle)
                mat = np.array([
                    [0.213 + cos_a * 0.787 - sin_a * 0.213, 0.715 - cos_a * 0.715 - sin_a * 0.715, 0.072 - cos_a * 0.072 + sin_a * 0.928],
                    [0.213 - cos_a * 0.213 + sin_a * 0.143, 0.715 + cos_a * 0.285 + sin_a * 0.140, 0.072 - cos_a * 0.072 - sin_a * 0.283],
                    [0.213 - cos_a * 0.213 - sin_a * 0.787, 0.715 - cos_a * 0.715 + sin_a * 0.715, 0.072 + cos_a * 0.928 + sin_a * 0.072],
                ], dtype=np.float32)
                lin = np.dot(lin, mat.T)

            # 3D LUT Evaluation
            if getattr(cg, "lut_enabled", False) and getattr(cg, "active_lut", None):
                lut = cg.active_lut
                if lut.data_3d is not None and lut.size_3d > 0:
                    sz = lut.size_3d
                    coords = np.clip(lin * (sz - 1), 0, sz - 1)
                    i0 = np.floor(coords).astype(np.int32)
                    i1 = np.minimum(i0 + 1, sz - 1)
                    f = coords - i0
                    lut_arr = lut.data_3d
                    c000 = lut_arr[i0[..., 2], i0[..., 1], i0[..., 0]]
                    c100 = lut_arr[i0[..., 2], i0[..., 1], i1[..., 0]]
                    c010 = lut_arr[i0[..., 2], i1[..., 1], i0[..., 0]]
                    c110 = lut_arr[i0[..., 2], i1[..., 1], i1[..., 0]]
                    c001 = lut_arr[i1[..., 2], i0[..., 1], i0[..., 0]]
                    c101 = lut_arr[i1[..., 2], i0[..., 1], i1[..., 0]]
                    c011 = lut_arr[i1[..., 2], i1[..., 1], i0[..., 0]]
                    c111 = lut_arr[i1[..., 2], i1[..., 1], i1[..., 0]]
                    fx, fy, fz = f[..., 0:1], f[..., 1:2], f[..., 2:3]
                    c00 = c000 * (1 - fx) + c100 * fx
                    c01 = c001 * (1 - fx) + c101 * fx
                    c10 = c010 * (1 - fx) + c110 * fx
                    c11 = c011 * (1 - fx) + c111 * fx
                    c0 = c00 * (1 - fy) + c10 * fy
                    c1 = c01 * (1 - fy) + c11 * fy
                    lin = c0 * (1 - fz) + c1 * fz

        return lin

    @staticmethod
    def _apply_display_transform(lin: np.ndarray, viewport) -> np.ndarray:
        """Apply display view transform and monitor gamma/brightness trim."""
        if viewport is None:
            return lin

        view_transform = int(getattr(viewport, "view_transform", 1))
        if 0 <= view_transform <= 10:
            disp = apply_linear_to_standard(lin, view_transform)
        elif view_transform == 100:  # ACES Filmic Tone Mapping
            a, b, c, d, e = 2.51, 0.03, 2.43, 0.59, 0.14
            x = np.maximum(lin, 0.0)
            filmic = np.clip((x * (a * x + b)) / (x * (c * x + d) + e), 0.0, 1.0)
            disp = linear_to_srgb(filmic)
        else:
            disp = lin

        # Display Gamma & Brightness Trim
        gamma = float(getattr(viewport, "gamma", 1.0))
        if abs(gamma - 1.0) > 0.0001:
            gamma_pow = 1.0 / max(0.0001, abs(gamma))
            disp = np.power(np.maximum(disp, 0.0), gamma_pow)
            if gamma < 0:
                disp = 1.0 - disp

        cg = getattr(viewport, "color_grading", None)
        if cg and abs(cg.brightness) > 0.001:
            disp = disp * (2.0 ** cg.brightness)

        if cg and getattr(cg, "invert_colors", False):
            disp = 1.0 - disp

        return disp

    @staticmethod
    def apply_screenspace_color_transforms(raw_img: np.ndarray, viewport) -> np.ndarray:
        """
        Apply complete source input colorspace linearization, scene-linear exposure & gain,
        ASC CDL, interactive color grading edits, 3D LUT, display view transform, and gamma,
        producing a color-managed uint8 RGB image matching viewport screen rendering exactly.
        """
        if isinstance(raw_img, (tuple, list)):
            raw_img = next((x for x in raw_img if isinstance(x, np.ndarray)), None)

        if raw_img is None:
            return np.zeros((1080, 1920, 3), dtype=np.uint8)

        if raw_img.dtype in (np.float32, np.float64, np.float16):
            rgb = raw_img[:, :, :3].astype(np.float32)
        else:
            rgb = raw_img[:, :, :3].astype(np.float32) / 255.0

        if viewport is None:
            return np.clip(rgb * 255.0, 0, 255).astype(np.uint8)

        input_space = int(getattr(viewport, "input_space", 0))
        lin = MediaExporter._linearize_input(rgb, input_space)
        lin = MediaExporter._apply_color_grade(lin, viewport)
        disp = MediaExporter._apply_display_transform(lin, viewport)
        return np.clip(disp * 255.0, 0, 255).astype(np.uint8)

    @staticmethod
    def convert_colorspace(
        raw_img: np.ndarray,
        target_space: str,
        viewport=None,
        burn_in_grade: bool = True,
    ) -> np.ndarray:
        """
        Convert raw frame image to specified target color space.
        Supported target_space options:
          - 'Current View (WYSIWYG)': full screen-space color management (uint8)
          - 'Scene-Linear (ACEScg)': scene-linear float32, optional grade burn-in
          - 'sRGB (Display)': sRGB transfer curve float32/uint8, optional grade burn-in
          - 'Rec.709 (Display)': Rec.709 transfer curve float32/uint8, optional grade burn-in
          - 'Cineon Log': Cineon log printing density curve float32, optional grade burn-in
          - 'Raw / Pass-Through': untouched raw frame data
        """
        if isinstance(raw_img, (tuple, list)):
            raw_img = next((x for x in raw_img if isinstance(x, np.ndarray)), None)

        if raw_img is None:
            return np.zeros((1080, 1920, 3), dtype=np.uint8)

        space_str = str(target_space).strip().lower()
        if "wysiwyg" in space_str or "current" in space_str:
            return MediaExporter.apply_screenspace_color_transforms(raw_img, viewport)

        if "raw" in space_str or "pass-through" in space_str:
            return raw_img

        if raw_img.dtype in (np.float32, np.float64, np.float16):
            rgb = raw_img[:, :, :3].astype(np.float32)
        else:
            rgb = raw_img[:, :, :3].astype(np.float32) / 255.0

        input_space = int(getattr(viewport, "input_space", 0)) if viewport else (0 if raw_img.dtype == np.uint8 else 1)
        lin = MediaExporter._linearize_input(rgb, input_space)

        if burn_in_grade and viewport:
            lin = MediaExporter._apply_color_grade(lin, viewport)

        if "linear" in space_str or "acescg" in space_str:
            return lin.astype(np.float32)

        if "709" in space_str:
            higher = 1.099 * np.power(np.maximum(lin, 0.0), 0.45) - 0.099
            lower = 4.5 * lin
            disp = np.where(lin > 0.018, higher, lower)
            return np.clip(disp, 0.0, 1.0).astype(np.float32)

        if "cineon" in space_str:
            x = np.maximum(lin * 0.989202 + 0.010798, 1e-6)
            cineon = np.clip((685.0 + 300.0 * np.log10(x)) / 1023.0, 0.0, 1.0)
            return cineon.astype(np.float32)

        # Default sRGB
        higher = 1.055 * np.power(np.maximum(lin, 0.0), 1.0 / 2.4) - 0.055
        lower = 12.92 * lin
        disp = np.where(lin > 0.0031308, higher, lower)
        return np.clip(disp, 0.0, 1.0).astype(np.float32)



# Module-level convenience aliases
detect_hardware_encoders = MediaExporter.detect_hardware_encoders
is_encoder_functional = MediaExporter.is_encoder_functional
resolve_video_encoder = MediaExporter.resolve_video_encoder
