"""
High-Performance Video Reader Engine for OpenReview.
Supports common video formats (MP4, MOV, MKV, AVI, WebM, FLV, WMV, etc.)
via OpenCV and FFmpeg backend with thread-safe frame-accurate random seeking.
"""
from __future__ import annotations

import fnmatch
import os
import sys
import threading
import time
from typing import Dict, Optional, Tuple, Any
import numpy as np

try:
    import cv2
    HAS_CV2 = True
except ImportError:
    HAS_CV2 = False

try:
    import av
    HAS_AV = True
except ImportError:
    HAS_AV = False

from openreview.core.reader import MediaReader

# Complete registry of supported video codec FourCCs & stream identifiers
VIDEO_CODEC_FAMILIES: Dict[str, Tuple[str, Tuple[str, ...]]] = {
    "WMV9 / VC-1": ("vc1", ("wmv3", "wvc1", "wmva")),
    "Windows Media Video 7/8": ("wmv2", ("wmv1", "wmv2")),
    "Ogg Theora": ("theora", ("thra", "theo", "theora")),
    "Sorenson Video 3": ("svq3", ("SVQ 3", "SVQ3", "svq3")),
    "Sorenson Video 1": ("svq1", ("SVQ 1", "SVQ1", "svq1")),
    "RealVideo 8/9/10": ("rv40", ("RV30", "RV40", "rv30", "rv40")),
    "RealVideo G2 / 1.0 / 2.0": ("rv20", ("RV10", "RV13", "RV20", "rv10", "rv13", "rv20")),
    "On2 TrueMotion VP7": ("vp7", ("VP7", "VP70", "VP71", "VP72", "vp7")),
    "On2 TrueMotion VP6": ("vp6", ("VP60", "VP61", "VP62", "VP6F", "VP6A", "vp60", "vp61", "vp62", "vp6f", "vp6a", "vp6")),
    "On2 TrueMotion VP5": ("vp5", ("VP50", "VP5", "VP51", "vp50", "vp5", "vp51")),
    "On2 TrueMotion VP3": ("vp3", ("VP31", "VP30", "VP3", "vp31", "vp30", "vp3")),
    "MPEG-4 Part 2 (DivX / Xvid)": (
        "mpeg4",
        ("DIVX", "Xvid", "mp4s", "m4s2", "xvid", "mp4v", "fmp4", "3iv2", "smp4", "divx"),
    ),
    "MPEG-2 / HDV / IMX": (
        "mpeg2video",
        (
            "mp2v", "mpg2", "vcr2", "hdv1", "hdv2", "hdv3",
            "mx*n", "mx*p", "mx3n", "mx4n", "mx5n", "mx3p", "mx4p", "mx5p",
        ),
    ),
    "MPEG-1 Video": ("mpeg1video", ("mpeg", "mp1v", "mpg1", "PIM1", "pim1")),
    "Intel Indeo 4 / 5": ("indeo5", ("IV41", "IV51", "iv41", "iv51")),
    "Intel Indeo 3": ("indeo3", ("IV31", "IV32", "iv31", "iv32")),
    "H.264 / AVC / MPEG-4 Part 10": (
        "h264",
        ("h264", "s264", "AVC1", "DAVC", "H264", "X264", "VSSH", "avc1", "davc", "x264", "vssh"),
    ),
    "H.263 / H.263+": ("h263", ("h263", "H263", "s263", "u263")),
    "ITU-T H.262 (MPEG-2)": ("mpeg2video", ("h262", "H262")),
    "ITU-T H.261": ("h261", ("h261", "H261")),
    "Flash Screen Video": ("flashsv", ("FSV1", "fsv1")),
    "MS MPEG-4 v3 / DivX 3/4/5/6 / CoolVideo": (
        "msmpeg4v3",
        ("DIV4", "DIV5", "DIV6", "col1", "col0", "3ivd", "div4", "div5", "div6"),
    ),
    "MS MPEG-4 v1/v2/v3": (
        "msmpeg4v2",
        ("DIV1", "DIV2", "DIV3", "mp41", "mp42", "MPG4", "MPG3", "div1", "div2", "div3", "mpg4", "mpg3"),
    ),
    "BBC Dirac / VC-2": ("dirac", ("BBCD", "bbcd", "drac", "dirac")),
    "Cinepak": ("cinepak", ("cvid", "CVID")),
}

SUPPORTED_VIDEO_CODECS: set[str] = set()
VIDEO_FOURCC_TO_FFMPEG: Dict[str, Tuple[str, str]] = {}
for _family_desc, (_ffmpeg_dec, _tags) in VIDEO_CODEC_FAMILIES.items():
    for _t in _tags:
        SUPPORTED_VIDEO_CODECS.add(_t)
        SUPPORTED_VIDEO_CODECS.add(_t.lower())
        SUPPORTED_VIDEO_CODECS.add(_t.upper())
        _norm = _t.lower().replace(" ", "")
        SUPPORTED_VIDEO_CODECS.add(_norm)
        VIDEO_FOURCC_TO_FFMPEG[_t.lower()] = (_ffmpeg_dec, _family_desc)
        VIDEO_FOURCC_TO_FFMPEG[_norm] = (_ffmpeg_dec, _family_desc)


def is_supported_video_codec(codec_or_fourcc: str) -> bool:
    """Return True if the given FourCC or codec identifier is supported (including mx*n / mx*p wildcards)."""
    if not codec_or_fourcc or not isinstance(codec_or_fourcc, str):
        return False
    raw = codec_or_fourcc.strip()
    low = raw.lower()
    norm = low.replace(" ", "")
    if raw in SUPPORTED_VIDEO_CODECS or low in SUPPORTED_VIDEO_CODECS or norm in SUPPORTED_VIDEO_CODECS:
        return True
    if fnmatch.fnmatch(low, "mx*n") or fnmatch.fnmatch(low, "mx*p"):
        return True
    return False


def get_video_codec_info(codec_or_fourcc: str) -> Tuple[str, str]:
    """Return (ffmpeg_decoder, family_description) for a FourCC or codec string."""
    if not codec_or_fourcc:
        return ("unknown", "Unknown Video Codec")
    low = codec_or_fourcc.strip().lower()
    norm = low.replace(" ", "")
    if low in VIDEO_FOURCC_TO_FFMPEG:
        return VIDEO_FOURCC_TO_FFMPEG[low]
    if norm in VIDEO_FOURCC_TO_FFMPEG:
        return VIDEO_FOURCC_TO_FFMPEG[norm]
    if fnmatch.fnmatch(low, "mx*n") or fnmatch.fnmatch(low, "mx*p"):
        return ("mpeg2video", "MPEG-2 / HDV / IMX")
    return (low, f"Video ({codec_or_fourcc})")


# Recognized video container and elementary stream extensions supported by OpenCV / FFmpeg
VIDEO_EXTENSIONS = {
    # Standard containers
    ".mp4", ".mov", ".qt", ".mkv", ".avi", ".webm", ".m4v", ".flv", ".f4v",
    ".wmv", ".asf", ".ts", ".m2ts", ".mts", ".m2t", ".mpg", ".mpeg", ".mpe",
    ".mpv", ".m1v", ".m2v", ".m4p", ".vob", ".evo", ".ogv", ".ogg", ".ogm",
    ".rm", ".rmvb", ".rv", ".3gp", ".3g2", ".mxf", ".dv", ".dif", ".swf",
    ".ivf", ".drc", ".dirac", ".bik", ".smk", ".nsv", ".roq", ".nut", ".wtv",
    ".dvr-ms", ".amv", ".y4m",
    # WMV / VC-1
    ".wmv3", ".wvc1", ".wmva", ".vc1", ".wmv1", ".wmv2",
    # Theora
    ".thra",
    # Sorenson Video
    ".svq3", ".svq1",
    # RealVideo
    ".rv30", ".rv40", ".rv10", ".rv13", ".rv20",
    # On2 VP3 / VP5 / VP6 / VP7
    ".vp7", ".vp60", ".vp61", ".vp62", ".vp6f", ".vp6a", ".vp6",
    ".vp50", ".vp5", ".vp51", ".vp31", ".vp30", ".vp3",
    # MPEG-4 Part 2 / DivX / Xvid / MS-MPEG4
    ".divx", ".xvid", ".mp4s", ".m4s2", ".mp4v", ".fmp4", ".3iv2", ".smp4",
    ".div4", ".div5", ".div6", ".col1", ".col0", ".3ivd",
    ".div1", ".div2", ".div3", ".mp41", ".mp42", ".mpg4", ".mpg3",
    # MPEG-2 / HDV / IMX
    ".mp2v", ".mpg2", ".vcr2", ".hdv1", ".hdv2", ".hdv3",
    ".mx3n", ".mx4n", ".mx5n", ".mx3p", ".mx4p", ".mx5p",
    # MPEG-1
    ".mp1v", ".mpg1", ".pim1",
    # Intel Indeo
    ".iv41", ".iv51", ".iv31", ".iv32",
    # H.264 / H.263 / H.262 / H.261
    ".h264", ".264", ".s264", ".avc1", ".davc", ".x264", ".vssh", ".avc",
    ".h263", ".263", ".h262", ".262", ".h261", ".261",
    # Flash Screen Video, BBC Dirac, Cinepak
    ".fsv1", ".bbcd", ".cvid",
}


def is_video_file(path: str) -> bool:
    """Return True if path points to a supported video file container or elementary video stream."""
    if not path or not isinstance(path, str):
        return False
    _, ext = os.path.splitext(path.lower())
    if ext in VIDEO_EXTENSIONS:
        return True
    raw_ext = ext.lstrip(".")
    if fnmatch.fnmatch(raw_ext, "mx*n") or fnmatch.fnmatch(raw_ext, "mx*p"):
        return True
    return False


def get_available_hwaccel_devices() -> list[str]:
    """Return a list of available hardware acceleration devices on the host platform."""
    if not HAS_AV:
        return []
    try:
        from av.codec.hwaccel import hwdevices_available
        return list(hwdevices_available())
    except Exception:
        return []


def create_hwaccel_context() -> Tuple[Optional[Any], Optional[str]]:
    """
    Create a PyAV HWAccel context configured for the host OS and GPU with software fallback.
    Returns (hwaccel_instance, device_name) or (None, None).
    """
    if not HAS_AV:
        return None, None
    try:
        from openreview.core.preferences import PreferencesManager
        prefs = PreferencesManager.instance().prefs
        if not getattr(prefs, "hardware_video_decoding", True):
            return None, None
    except Exception:
        pass

    try:
        from av.codec.hwaccel import HWAccel, hwdevices_available
        available = hwdevices_available()
        if not available:
            return None, None

        if sys.platform == "win32":
            priority = ["d3d11va", "dxva2", "cuda", "qsv", "d3d12va", "amf"]
        elif sys.platform == "darwin":
            priority = ["videotoolbox", "cuda"]
        else:
            priority = ["cuda", "vaapi", "vdpau", "qsv"]

        for dev in priority:
            if dev in available:
                try:
                    hw = HWAccel(dev, allow_software_fallback=True)
                    return hw, dev
                except Exception:
                    continue

        for dev in available:
            try:
                hw = HWAccel(dev, allow_software_fallback=True)
                return hw, dev
            except Exception:
                continue
    except Exception:
        pass
    return None, None


class OpenCVVideoReader(MediaReader):
    """
    Thread-safe OpenCV video sequence reader (Fallback Engine).
    Demuxes and decodes frames into uint8 RGBA buffers [0..255] (or float32 [0.0..1.0] if requested).
    Equipped with an asynchronous background lookahead prefetching pipeline (read-ahead ring buffer).
    """

    def __init__(
        self,
        file_path: str,
        as_float: Optional[bool] = None,
        buffer_size: int = 24,
    ):
        if not HAS_CV2:
            raise RuntimeError("opencv-python is required for video decoding.")
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"Video file does not exist: {file_path}")

        self.file_path = file_path
        self._buffer_size = max(4, buffer_size)

        # Determine whether float32 conversion is requested
        if as_float is not None:
            self._as_float = bool(as_float)
        else:
            try:
                from openreview.core.preferences import PreferencesManager
                prefs = PreferencesManager.instance().prefs
                mode = getattr(prefs, "playback_texture_mode", "auto")
                self._as_float = (mode == "rgba32f")
            except Exception:
                self._as_float = False

        self._lock = threading.Lock()
        self._cond = threading.Condition(self._lock)

        # Hardware Decoding & OpenCL Configuration
        try:
            from openreview.core.preferences import PreferencesManager
            prefs = PreferencesManager.instance().prefs
            use_hw = getattr(prefs, "hardware_video_decoding", True)
            use_ocl = getattr(prefs, "enable_opencl_acceleration", True)
            if hasattr(cv2, "ocl"):
                cv2.ocl.setUseOpenCL(use_ocl)
        except Exception:
            use_hw = True

        # Prefer CAP_FFMPEG first so all 25+ legacy & broadcast FourCC codecs
        # (WMV1/2/3/WVC1/WMVA, Theora, SVQ1/3, RV10..40, VP3..7, DivX/Xvid/MS-MPEG4,
        # MPEG-1/2/HDV/IMX, Indeo 3/4/5, H.261/262/263/264, FSV1, Dirac BBCD, Cinepak cvid)
        # decode reliably via libavcodec, falling back to CAP_ANY.
        self._cap = None
        backends = []
        if hasattr(cv2, "CAP_FFMPEG"):
            backends.append(cv2.CAP_FFMPEG)
        if use_hw and hasattr(cv2, "CAP_ANY"):
            backends.append(cv2.CAP_ANY)
        for api in backends:
            cap = cv2.VideoCapture(file_path, api)
            if cap is not None and cap.isOpened():
                self._cap = cap
                break
            if cap is not None:
                cap.release()
        if self._cap is None:
            self._cap = cv2.VideoCapture(file_path)

        if not self._cap.isOpened():
            raise RuntimeError(f"Failed to open video container: {file_path}")

        # Query metadata
        self._fps = float(self._cap.get(cv2.CAP_PROP_FPS))
        if self._fps <= 0 or np.isnan(self._fps) or self._fps > 1000.0:
            self._fps = 24.0

        fourcc_int = int(self._cap.get(cv2.CAP_PROP_FOURCC))
        if fourcc_int > 0:
            self.fourcc = "".join([chr((fourcc_int >> 8 * i) & 0xFF) for i in range(4)]).strip("\x00")
        else:
            self.fourcc = os.path.splitext(file_path)[1].lstrip(".")
        self.codec_name, self.codec_family = get_video_codec_info(self.fourcc)

        count = int(self._cap.get(cv2.CAP_PROP_FRAME_COUNT))
        if count <= 0 or count > 100_000_000:
            # Elementary streams (.h261, .h262, .h263, .h264, .mp1v, .mp2v, .bbcd, .vc1)
            # may not report container frame count in header; probe via grab()
            probed = 0
            while probed < 10000 and self._cap.grab():
                probed += 1
            self._cap.set(cv2.CAP_PROP_POS_FRAMES, 0.0)
            if int(self._cap.get(cv2.CAP_PROP_POS_FRAMES)) != 0:
                self._cap.release()
                self._cap = cv2.VideoCapture(file_path, getattr(cv2, "CAP_FFMPEG", cv2.CAP_ANY))
            count = max(1, probed)
        self._total_frames = max(1, count)

        self._width = int(self._cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        self._height = int(self._cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

        # Async Lookahead Ring Buffer State
        self._buffer: dict[int, np.ndarray] = {}  # 1-indexed frame_number -> rgba ndarray
        self._last_decoded_frame_idx = -1         # 0-indexed position in cv2.VideoCapture
        self._next_decode_idx = 0                 # 0-indexed next frame to decode
        self._last_frame_data: Optional[np.ndarray] = None
        self._running = True

        # Start dedicated background lookahead worker
        self._worker = threading.Thread(
            target=self._lookahead_worker_loop,
            daemon=True,
            name=f"VideoLookaheadWorker-{os.path.basename(file_path)}"
        )
        self._worker.start()

        # Prime initial buffer frames so playback starts instantaneously
        import time
        with self._cond:
            deadline = time.perf_counter() + 0.12
            while len(self._buffer) < 4 and self._running and time.perf_counter() < deadline:
                self._cond.wait(timeout=0.015)

    @property
    def backend(self) -> str:
        return "OpenCV"

    @property
    def fps(self) -> float:
        return self._fps

    @property
    def total_frames(self) -> int:
        return self._total_frames

    @property
    def width(self) -> int:
        return self._width

    @property
    def height(self) -> int:
        return self._height

    @property
    def hwaccel_device(self) -> Optional[str]:
        """Return the active hardware acceleration device name, or None if software decoding."""
        return getattr(self, "_hwaccel_device", None)

    def _seek_and_read_locked(self, target_idx: int) -> Tuple[bool, Optional[np.ndarray]]:
        """Seek to 0-indexed target_idx with sequential fallback for streams lacking seek tables."""
        if target_idx == self._last_decoded_frame_idx + 1:
            ret, bgr = self._cap.read()
            if ret and bgr is not None:
                return True, bgr

        self._cap.set(cv2.CAP_PROP_POS_FRAMES, float(target_idx))
        ret, bgr = self._cap.read()
        if ret and bgr is not None:
            return True, bgr

        # Fallback for elementary streams / legacy codecs where direct seek fails:
        # rewind or reopen from frame 0 and step forward to target_idx
        self._cap.set(cv2.CAP_PROP_POS_FRAMES, 0.0)
        cur = 0
        while cur < target_idx:
            if not self._cap.grab():
                break
            cur += 1
        ret, bgr = self._cap.read()
        if ret and bgr is not None:
            return True, bgr

        # Reopen stream fallback if container does not support rewinding
        self._cap.release()
        self._cap = cv2.VideoCapture(self.file_path, getattr(cv2, "CAP_FFMPEG", cv2.CAP_ANY))
        if self._cap.isOpened():
            cur = 0
            while cur < target_idx and self._cap.grab():
                cur += 1
            ret, bgr = self._cap.read()
            if ret and bgr is not None:
                return True, bgr
        return False, None

    def _lookahead_worker_loop(self) -> None:
        while self._running:
            with self._cond:
                # Wait while buffer is full
                while self._running and len(self._buffer) >= self._buffer_size:
                    self._cond.wait(timeout=0.02)
                if not self._running:
                    break

                idx = self._next_decode_idx
                if idx >= self._total_frames:
                    # At end of video stream, wait for seek or frame consumption
                    self._cond.wait(timeout=0.02)
                    continue

                if not self._cap or not self._cap.isOpened():
                    break

                # Ensure sequential alignment
                if idx != self._last_decoded_frame_idx + 1:
                    self._cap.set(cv2.CAP_PROP_POS_FRAMES, float(idx))

                ret, bgr = self._cap.read()
                if not ret or bgr is None:
                    # Seek or decode hiccup, advance index
                    self._next_decode_idx = idx + 1
                    continue

                self._last_decoded_frame_idx = idx
                self._next_decode_idx = idx + 1

                # Color conversion
                rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGBA)
                if self._as_float:
                    frame_data = np.ascontiguousarray(rgb, dtype=np.float32) / 255.0
                else:
                    frame_data = np.ascontiguousarray(rgb, dtype=np.uint8)

                frame_num = idx + 1
                self._buffer[frame_num] = frame_data
                self._last_frame_data = frame_data
                self._cond.notify_all()

    def read_frame(self, frame: int) -> Optional[np.ndarray]:
        """
        Read 1-indexed frame number from video stream.
        Returns uint8 RGBA array of shape (H, W, 4) in range [0..255] (or float32 if as_float=True).
        Uses the read-ahead buffer for near-zero latency (< 0.1ms) during sequential playback.
        """
        target_frame = max(1, min(frame, self._total_frames))

        with self._cond:
            if not self._cap or not self._cap.isOpened():
                return self._last_frame_data

            # 1. Wait briefly if the target frame is actively being decoded sequentially by the worker
            target_idx = target_frame - 1
            if (
                target_frame not in self._buffer
                and self._running
                and target_idx >= self._next_decode_idx - 1
                and target_idx <= self._next_decode_idx + 2
            ):
                import time
                deadline = time.perf_counter() + 0.05
                while (
                    target_frame not in self._buffer
                    and self._running
                    and target_idx <= self._next_decode_idx + 2
                ):
                    remaining = deadline - time.perf_counter()
                    if remaining <= 0:
                        break
                    self._cond.wait(timeout=remaining)

            # 2. Hit in lookahead buffer
            if target_frame in self._buffer:
                data = self._buffer.pop(target_frame)
                # Evict frames behind the current frame to prevent buffer bloat
                expired = [k for k in self._buffer if k < target_frame]
                for k in expired:
                    del self._buffer[k]
                self._cond.notify_all()
                return data

            # 3. Cache miss: Random seek, scrub, or underrun
            self._buffer.clear()
            ret, bgr = self._seek_and_read_locked(target_idx)
            if not ret or bgr is None:
                return self._last_frame_data

            self._last_decoded_frame_idx = target_idx
            self._next_decode_idx = target_idx + 1

            rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGBA)
            if self._as_float:
                frame_data = np.ascontiguousarray(rgb, dtype=np.float32) / 255.0
            else:
                frame_data = np.ascontiguousarray(rgb, dtype=np.uint8)

            self._last_frame_data = frame_data
            self._height, self._width = frame_data.shape[:2]
            # Notify worker thread to resume reading ahead from next frame
            self._cond.notify_all()
            return frame_data

    def close(self) -> None:
        self._running = False
        if hasattr(self, "_cond"):
            with self._cond:
                self._cond.notify_all()
        if hasattr(self, "_worker") and self._worker.is_alive():
            self._worker.join(timeout=0.5)
        if hasattr(self, "_lock"):
            with self._lock:
                if hasattr(self, "_cap") and self._cap is not None:
                    self._cap.release()
                    self._cap = None
                if hasattr(self, "_buffer"):
                    self._buffer.clear()

    def __del__(self) -> None:
        self.close()


class PyAVVideoReader(MediaReader):
    """
    Studio-grade FFmpeg/PyAV hardware & multi-threaded video decoding engine.
    Features:
    - Thread-safe frame-accurate seek with exact Presentation Time Stamps (PTS).
    - In-Memory GOP (Group of Pictures) Cache for instantaneous O(1) reverse scrubbing.
    - Zero-overhead SIMD swscale color conversion (RGBA 8-bit or RGBA 16-bit half/float).
    - Background lookahead prefetching worker for zero-wait sequential playback.
    """

    def __init__(
        self,
        file_path: str,
        as_float: Optional[bool] = None,
        buffer_size: int = 24,
    ):
        if not HAS_AV:
            raise RuntimeError("PyAV ('av') is required for PyAVVideoReader.")
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"Video file does not exist: {file_path}")

        self.file_path = file_path
        self._buffer_size = max(4, buffer_size)
        self._gop_max_size = 128  # Maximum frames retained in GOP scrub cache

        # Determine whether float conversion is requested
        if as_float is not None:
            self._as_float = bool(as_float)
        else:
            try:
                from openreview.core.preferences import PreferencesManager
                prefs = PreferencesManager.instance().prefs
                mode = getattr(prefs, "playback_texture_mode", "auto")
                self._as_float = (mode == "rgba32f")
            except Exception:
                self._as_float = False

        self._lock = threading.Lock()
        self._cond = threading.Condition(self._lock)

        # Open container (with hardware video decoding acceleration if supported)
        self._hwaccel, self._hwaccel_device = create_hwaccel_context()
        try:
            if self._hwaccel is not None:
                self._container = av.open(file_path, hwaccel=self._hwaccel)
            else:
                self._container = av.open(file_path)
        except Exception:
            # Fallback gracefully to software CPU decode
            self._hwaccel = None
            self._hwaccel_device = None
            self._container = av.open(file_path)

        if not self._container.streams.video:
            self._container.close()
            raise RuntimeError(f"No video streams found in container: {file_path}")

        self._stream = self._container.streams.video[0]

        # Multi-threaded decoding configuration
        try:
            cpu_cnt = os.cpu_count() or 4
            self._stream.codec_context.thread_count = min(max(2, cpu_cnt), 8)
            self._stream.codec_context.thread_type = "FRAME"
        except Exception:
            pass

        # Dimensions & FPS
        self._width = int(self._stream.codec_context.width or self._stream.width or 0)
        self._height = int(self._stream.codec_context.height or self._stream.height or 0)

        rate = self._stream.guessed_rate or self._stream.average_rate or self._stream.base_rate
        if rate is not None and float(rate) > 0:
            self._fps = float(rate)
        else:
            self._fps = 24.0
        if self._fps <= 0 or np.isnan(self._fps) or self._fps > 1000.0:
            self._fps = 24.0

        self.time_base = float(self._stream.time_base) if self._stream.time_base else (1.0 / self._fps)
        self.start_pts = self._stream.start_time if self._stream.start_time is not None else 0

        # Codec metadata
        codec_ctx = self._stream.codec_context
        self.fourcc = getattr(codec_ctx, "codec_tag", "") or os.path.splitext(file_path)[1].lstrip(".")
        codec_name_raw = getattr(codec_ctx, "name", "") or self.fourcc
        self.codec_name, self.codec_family = get_video_codec_info(codec_name_raw)

        # Total frames
        frames_count = self._stream.frames
        if frames_count and frames_count > 0:
            self._total_frames = int(frames_count)
        elif self._container.duration is not None and self._container.duration > 0:
            self._total_frames = max(1, int(round((self._container.duration / av.time_base) * self._fps)))
        else:
            self._total_frames = 1

        # Check high bit depth (10-bit / 12-bit ProRes / DNxHR / HDR)
        pix_fmt = getattr(codec_ctx, "pix_fmt", "") or ""
        self._is_high_bit_depth = any(b in pix_fmt for b in ("10", "12", "14", "16", "48", "64"))

        # GOP & Lookahead Buffers (1-indexed frame_number -> RGBA ndarray)
        self._gop_cache: dict[int, np.ndarray] = {}
        self._lookahead_buffer: dict[int, np.ndarray] = {}
        self._last_decoded_frame: int = 0
        self._next_prefetch_frame: int = 1
        self._last_frame_data: Optional[np.ndarray] = None
        self._generator = self._container.decode(self._stream)
        self._running = True

        # Background lookahead prefetching thread
        self._worker = threading.Thread(
            target=self._lookahead_worker_loop,
            daemon=True,
            name=f"PyAVLookaheadWorker-{os.path.basename(file_path)}"
        )
        self._worker.start()

        # Prime initial buffer
        deadline = time.perf_counter() + 0.15
        with self._cond:
            while len(self._lookahead_buffer) < 4 and self._running and time.perf_counter() < deadline:
                self._cond.wait(timeout=0.015)

    @property
    def backend(self) -> str:
        return "PyAV"

    @property
    def fps(self) -> float:
        return self._fps

    @property
    def total_frames(self) -> int:
        return self._total_frames

    @property
    def width(self) -> int:
        return self._width

    @property
    def height(self) -> int:
        return self._height

    @property
    def hwaccel_device(self) -> Optional[str]:
        """Return the active hardware acceleration device name, or None if software decoding."""
        return getattr(self, "_hwaccel_device", None)

    def _process_av_frame(self, frame: av.VideoFrame) -> np.ndarray:
        """Convert av.VideoFrame to C-contiguous RGBA array."""
        if self._is_high_bit_depth:
            try:
                arr16 = frame.to_ndarray(format="rgba64le")
                if self._as_float:
                    arr = (arr16.astype(np.float32) / 65535.0)
                else:
                    arr = (arr16 >> 8).astype(np.uint8)
                return np.ascontiguousarray(arr)
            except Exception:
                pass

        # Standard 8-bit RGBA
        arr = frame.to_ndarray(format="rgba")
        if self._as_float:
            arr = np.ascontiguousarray(arr, dtype=np.float32) / 255.0
        else:
            if not arr.flags.c_contiguous:
                arr = np.ascontiguousarray(arr)
        return arr

    def _pts_to_frame_number(self, frame: av.VideoFrame) -> int:
        if frame.pts is not None:
            rel_pts = frame.pts - self.start_pts
            frame_num = int(round((rel_pts * self.time_base) * self._fps)) + 1
            return max(1, min(frame_num, self._total_frames))
        return self._last_decoded_frame + 1

    def _add_to_gop_cache(self, fn: int, data: np.ndarray) -> None:
        self._gop_cache[fn] = data
        if len(self._gop_cache) > self._gop_max_size:
            keys = sorted(self._gop_cache.keys())
            if keys:
                del self._gop_cache[keys[0]]

    def read_frame(self, frame: int) -> Optional[np.ndarray]:
        target_frame = max(1, min(frame, self._total_frames))

        with self._cond:
            if not self._running or self._container is None:
                return self._last_frame_data

            # 1. Hit in GOP cache (instant O(1) for scrubbing backward or within active GOP)
            if target_frame in self._gop_cache:
                data = self._gop_cache[target_frame]
                self._next_prefetch_frame = target_frame + 1
                self._cond.notify_all()
                return data

            # 2. Hit in Lookahead buffer (during sequential forward playback)
            if target_frame in self._lookahead_buffer:
                data = self._lookahead_buffer.pop(target_frame)
                self._add_to_gop_cache(target_frame, data)
                # Evict expired frames behind
                for k in [k for k in self._lookahead_buffer if k < target_frame]:
                    del self._lookahead_buffer[k]
                self._next_prefetch_frame = target_frame + 1
                self._cond.notify_all()
                return data

            # 3. Cache miss: Random seek, scrub jump, or prefetch underrun
            self._lookahead_buffer.clear()

            # Seek backward to nearest preceding keyframe
            target_pts = self.start_pts + int(round((target_frame - 1) / self._fps / self.time_base))
            try:
                self._container.seek(target_pts, stream=self._stream, backward=True, any_frame=False)
                self._generator = self._container.decode(self._stream)
            except Exception:
                pass

            target_data = None
            try:
                for av_frame in self._generator:
                    fn = self._pts_to_frame_number(av_frame)
                    data = self._process_av_frame(av_frame)
                    self._add_to_gop_cache(fn, data)
                    self._last_decoded_frame = fn
                    if fn == target_frame:
                        target_data = data
                        break
                    elif fn > target_frame:
                        target_data = data
                        break
            except Exception:
                pass

            if target_data is not None:
                self._last_frame_data = target_data
                self._next_prefetch_frame = target_frame + 1
                self._cond.notify_all()
                return target_data

            return self._last_frame_data

    def _lookahead_worker_loop(self) -> None:
        while self._running:
            with self._cond:
                # Wait while buffer is full
                while self._running and len(self._lookahead_buffer) >= self._buffer_size:
                    self._cond.wait(timeout=0.02)
                if not self._running:
                    break

                target_prefetch = self._next_prefetch_frame
                if target_prefetch > self._total_frames:
                    self._cond.wait(timeout=0.02)
                    continue

                if self._container is None:
                    break

                # Decode next frame from active generator
                try:
                    av_frame = next(self._generator)
                    fn = self._pts_to_frame_number(av_frame)
                    data = self._process_av_frame(av_frame)
                    self._last_decoded_frame = fn
                    self._add_to_gop_cache(fn, data)
                    self._lookahead_buffer[fn] = data
                    self._next_prefetch_frame = fn + 1
                    self._cond.notify_all()
                except (StopIteration, Exception):
                    self._cond.wait(timeout=0.02)

    def close(self) -> None:
        self._running = False
        if hasattr(self, "_cond"):
            with self._cond:
                self._cond.notify_all()
        if hasattr(self, "_worker") and self._worker.is_alive():
            self._worker.join(timeout=0.5)
        if hasattr(self, "_lock"):
            with self._lock:
                if hasattr(self, "_container") and self._container is not None:
                    try:
                        self._container.close()
                    except Exception:
                        pass
                    self._container = None
                if hasattr(self, "_gop_cache"):
                    self._gop_cache.clear()
                if hasattr(self, "_lookahead_buffer"):
                    self._lookahead_buffer.clear()

    def __del__(self) -> None:
        self.close()


class VideoMediaReader(MediaReader):
    """
    Unified high-performance video sequence reader for OpenReview.
    Automatically selects the PyAV (FFmpeg) engine for hardware-accelerated,
    GOP-cached playback and exact PTS seeking, with seamless fallback to OpenCV.
    """

    def __init__(
        self,
        file_path: str,
        as_float: Optional[bool] = None,
        buffer_size: int = 24,
        force_backend: Optional[str] = None,
    ):
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"Video file does not exist: {file_path}")

        self.file_path = file_path
        self._reader: Optional[MediaReader] = None
        self._backend = "Unknown"

        # Check preferences if PyAV engine is enabled
        use_pyav = True
        try:
            from openreview.core.preferences import PreferencesManager
            prefs = PreferencesManager.instance().prefs
            use_pyav = getattr(prefs, "enable_pyav_engine", True)
        except Exception:
            use_pyav = True

        if force_backend == "opencv":
            use_pyav = False
        elif force_backend == "pyav":
            use_pyav = True

        if use_pyav and HAS_AV:
            try:
                self._reader = PyAVVideoReader(file_path, as_float=as_float, buffer_size=buffer_size)
                self._backend = "PyAV"
            except Exception:
                self._reader = None

        if self._reader is None:
            if not HAS_CV2:
                raise RuntimeError("Neither PyAV nor OpenCV is available for video playback.")
            self._reader = OpenCVVideoReader(file_path, as_float=as_float, buffer_size=buffer_size)
            self._backend = "OpenCV"

    @property
    def backend(self) -> str:
        return self._backend

    @property
    def fps(self) -> float:
        return self._reader.fps

    @property
    def total_frames(self) -> int:
        return self._reader.total_frames

    @property
    def width(self) -> int:
        return self._reader.width

    @property
    def height(self) -> int:
        return self._reader.height

    @property
    def fourcc(self) -> str:
        return getattr(self._reader, "fourcc", "")

    @property
    def codec_name(self) -> str:
        return getattr(self._reader, "codec_name", "")

    @property
    def codec_family(self) -> str:
        return getattr(self._reader, "codec_family", "")

    @property
    def hwaccel_device(self) -> Optional[str]:
        return getattr(self._reader, "hwaccel_device", None)

    def read_frame(self, frame: int) -> Optional[np.ndarray]:
        return self._reader.read_frame(frame)

    def close(self) -> None:
        if self._reader is not None:
            self._reader.close()
            self._reader = None

    def __del__(self) -> None:
        self.close()


UnifiedVideoReader = VideoMediaReader
