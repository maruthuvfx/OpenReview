"""
Media Reader Engine and Asynchronous Prefetch Worker.
Provides synthetic high-dynamic-range test frame generation and disk media loading.
"""
from __future__ import annotations

import os
import math
import time
from typing import Optional, Callable, Any
import numpy as np
from PIL import Image
from PySide6.QtCore import QObject, Signal, QRunnable, QThreadPool, QTimer

from openreview.core.cancellation import CancellationToken, CancellationScopeGuard
from openreview.core.cache import CacheMode


def get_adaptive_downsample_factor(width: int, height: int) -> int:
    """
    Calculate the adaptive downsample factor for fast scrubbing.
    - 4K or greater (>= 3840 wide or >= 2160 high): 4x downsample (1/4 resolution)
    - 2K / HD (< 3840 wide and < 2160 high): 2x downsample (1/2 resolution)
    """
    if width >= 3840 or height >= 2160:
        return 4
    return 2


def read_adaptive_frame(
    reader: Any,
    frame_number: int,
    factor: Optional[int] = None,
    normalize_fn: Optional[Callable[[Any], Any]] = None,
) -> Optional[np.ndarray]:
    """
    Read and decode a single frame adaptively downsampled for real-time fluid playback and scrubbing.
    - Factor 4 for 4K+ (>= 3840 wide or >= 2160 high)
    - Factor 2 for HD/2K (< 3840 wide and < 2160 high)
    - Ensures C-contiguous 4-channel RGBA memory layout.
    """
    if reader is None:
        return None
    w = getattr(reader, "width", 0)
    h = getattr(reader, "height", 0)
    if factor is None:
        factor = get_adaptive_downsample_factor(w, h) if (w > 0 and h > 0) else 2

    raw = reader.read_frame(frame_number)
    if raw is None:
        return None
    if normalize_fn is not None:
        raw = normalize_fn(raw)

    if isinstance(raw, np.ndarray) and raw.ndim >= 2:
        if factor > 1:
            raw = np.ascontiguousarray(raw[::factor, ::factor])
        return ensure_c_contiguous_rgba(raw)
    elif isinstance(raw, (tuple, list)):
        processed = []
        for elem in raw:
            if isinstance(elem, np.ndarray) and elem.ndim >= 2:
                if factor > 1:
                    elem = np.ascontiguousarray(elem[::factor, ::factor])
                processed.append(ensure_c_contiguous_rgba(elem))
            else:
                processed.append(elem)
        return tuple(processed)
    return raw


def ensure_c_contiguous_rgba(data: np.ndarray) -> np.ndarray:
    """
    Standardize frame array to C-contiguous 4-channel RGBA in background thread.
    - Preserves float16 or uint8 (or converts float32 to float16).
    - If 3-channel RGB: appends 1.0 (float) or 255 (uint8) alpha channel.
    - If 1-channel or 2-channel: expands to 4-channel RGBA.
    - Guarantees C-contiguous memory layout for zero-overhead PBO streaming.
    """
    if not isinstance(data, np.ndarray) or data.size == 0 or data.ndim < 2:
        return data

    h, w = data.shape[:2]

    # 1D/2D grayscale/matte -> RGBA
    if data.ndim == 2:
        if data.dtype == np.uint8:
            rgba = np.empty((h, w, 4), dtype=np.uint8)
            rgba[:, :, 0] = data
            rgba[:, :, 1] = data
            rgba[:, :, 2] = data
            rgba[:, :, 3] = 255
            return np.ascontiguousarray(rgba)
        else:
            target_dtype = np.float16 if data.dtype in (np.float32, np.float16) else data.dtype
            d = data.astype(target_dtype, copy=False)
            rgba = np.empty((h, w, 4), dtype=target_dtype)
            rgba[:, :, 0] = d
            rgba[:, :, 1] = d
            rgba[:, :, 2] = d
            rgba[:, :, 3] = 1.0
            return np.ascontiguousarray(rgba)

    # 3-channel RGB -> 4-channel RGBA
    if data.ndim == 3 and data.shape[2] == 3:
        if data.dtype == np.uint8:
            rgba = np.empty((h, w, 4), dtype=np.uint8)
            rgba[:, :, :3] = data
            rgba[:, :, 3] = 255
            return np.ascontiguousarray(rgba)
        else:
            target_dtype = np.float16 if data.dtype in (np.float32, np.float16) else data.dtype
            rgba = np.empty((h, w, 4), dtype=target_dtype)
            rgba[:, :, :3] = data.astype(target_dtype, copy=False)
            rgba[:, :, 3] = 1.0
            return np.ascontiguousarray(rgba)

    # 4-channel RGBA: convert float32 to float16 for GPU performance & memory efficiency
    if data.dtype == np.float32:
        data = data.astype(np.float16, copy=False)

    if not data.flags.c_contiguous:
        data = np.ascontiguousarray(data)

    return data


class MediaReader:
    """Base interface for media sequence readers."""

    def read_frame(self, frame: int) -> Optional[np.ndarray]:
        """Read and return an RGBA image array (float32 [0..inf] or uint8 [0..255])."""
        raise NotImplementedError


class SyntheticPatternReader(MediaReader):
    """
    High-performance NumPy-native VFX test pattern generator:
    - Thread-safe SMPTE color bars & smooth linear gradient ramps (including > 1.0 HDR values)
    - Moving timecode pendulum / spinner to visually verify real-time frame rate
    - Grid lines and center crosshair
    """

    _cached_templates: dict[tuple[int, int], np.ndarray] = {}

    def __init__(self, width: int = 1920, height: int = 1080):
        self.width = width
        self.height = height
        key = (width, height)
        if key not in SyntheticPatternReader._cached_templates:
            SyntheticPatternReader._cached_templates[key] = self._create_base_template()
        self._base_template = SyntheticPatternReader._cached_templates[key]

    def _create_base_template(self) -> np.ndarray:
        """Precompute the static background template once in pure NumPy."""
        base = np.full((self.height, self.width, 4), 0.10, dtype=np.float32)
        base[:, :, 3] = 1.0

        # Background grid
        base[::120, :, :3] = 0.16
        base[:, ::120, :3] = 0.16

        # Center crosshair
        cx, cy = self.width // 2, self.height // 2
        base[cy - 2 : cy + 2, cx - 40 : cx + 40, :3] = 0.4
        base[cy - 40 : cy + 40, cx - 2 : cx + 2, :3] = 0.4

        # SMPTE Color Bars (Upper half)
        bar_colors = [
            [0.8, 0.8, 0.8, 1.0],  # 75% White
            [0.8, 0.8, 0.0, 1.0],  # Yellow
            [0.0, 0.8, 0.8, 1.0],  # Cyan
            [0.0, 0.8, 0.0, 1.0],  # Green
            [0.8, 0.0, 0.8, 1.0],  # Magenta
            [0.8, 0.0, 0.0, 1.0],  # Red
            [0.0, 0.0, 0.8, 1.0],  # Blue
        ]
        bar_w = self.width // len(bar_colors)
        bar_h = int(self.height * 0.35)
        top_y = int(self.height * 0.12)
        for i, col in enumerate(bar_colors):
            x0 = i * bar_w
            x1 = x0 + bar_w if i < len(bar_colors) - 1 else self.width
            base[top_y : top_y + bar_h, x0:x1] = col

        # Smooth horizontal gradient ramp below bars
        ramp_y0 = top_y + bar_h + 10
        ramp_y1 = ramp_y0 + 50
        ramp_vals = np.linspace(0.0, 1.0, self.width, dtype=np.float32)
        for c in range(3):
            base[ramp_y0:ramp_y1, :, c] = ramp_vals

        # Inject HDR bright hotspot (values > 1.0) near top right to test OCIO exposure
        hdr_x, hdr_y = int(self.width * 0.88), int(self.height * 0.28)
        base[hdr_y - 25 : hdr_y + 25, hdr_x - 25 : hdr_x + 25, :3] = 3.2

        return base

    def read_frame(self, frame: int) -> np.ndarray:
        # Fast copy of base template
        arr = self._base_template.copy()

        # Dynamic Moving Pendulum (to visually verify 24/60 fps playback)
        cx, cy = self.width // 2, self.height // 2
        angle = (frame * 6.0) % 360.0
        rad = math.radians(angle)
        pendulum_len = 160.0
        px = int(cx + pendulum_len * math.sin(rad))
        py = int(cy + pendulum_len * math.cos(rad) * 0.4)

        # Draw circle at pendulum tip (3DE amber gold: [0.898, 0.658, 0.137, 1.0])
        r = 14
        y_min = max(0, py - r)
        y_max = min(self.height, py + r)
        x_min = max(0, px - r)
        x_max = min(self.width, px + r)
        for yy in range(y_min, y_max):
            for xx in range(x_min, x_max):
                if (xx - px) ** 2 + (yy - py) ** 2 <= r * r:
                    arr[yy, xx] = [0.898, 0.658, 0.137, 1.0]

        return arr


class DiskMediaReader(MediaReader):
    """Loads image files from disk (PNG, JPEG, TIFF, DPX, etc.) via Pillow."""

    def __init__(self, file_pattern: str, active_layer: Optional[str] = None):
        self.file_pattern = file_pattern
        self.active_layer = active_layer

    def set_layer(self, layer: Optional[str]) -> None:
        self.active_layer = layer

    def _frame_path(self, frame: int) -> str:
        if "%" in self.file_pattern:
            return self.file_pattern % frame
        elif "#" in self.file_pattern:
            pad = self.file_pattern.count("#")
            return self.file_pattern.replace("#" * pad, f"{frame:0{pad}d}")
        return self.file_pattern

    def read_frame(self, frame: int) -> Optional[np.ndarray]:
        path = self._frame_path(frame)
        if not os.path.exists(path):
            return None

        from openreview.core.image_io import read_image_file
        arr = read_image_file(path, layer=self.active_layer)
        if arr is not None:
            return arr

        return None


class TaskSignals(QObject):
    loaded = Signal(int, int, str, int, bool)  # timeline_frame, source_frame, source_id, generation, success


class InteractiveDecodeTask(QRunnable):
    """
    High-priority decode task for the active playhead frame during uncached playback
    or interactive timeline scrubbing. Runs on the dedicated interactive worker pool.
    Supports adaptive resolution downsampling for fluid scrubbing without UI stalls.
    """

    def __init__(
        self,
        reader: Optional[MediaReader],
        cache,
        timeline_frame: int,
        source_frame: int,
        source_id: str,
        token: CancellationToken,
        downsample_factor: int = 1,
        is_proxy: bool = False,
        callback=None,
    ):
        super().__init__()
        self.reader = reader
        self.cache = cache
        self.timeline_frame = timeline_frame
        self.source_frame = source_frame
        self.source_id = source_id
        self.token = token
        self.downsample_factor = max(0, int(downsample_factor))
        self.is_proxy = bool(is_proxy)
        self.callback = callback
        self.signals = TaskSignals()

    def run(self) -> None:
        if self.reader is None or self.token.is_cancelled():
            self._finish(False, None)
            return

        try:
            raw = self.reader.read_frame(self.source_frame)
            if raw is None or self.token.is_cancelled():
                self._finish(False, None)
                return

            if self.is_proxy:
                factor = self.downsample_factor
                if factor <= 1 and raw.ndim >= 2:
                    factor = get_adaptive_downsample_factor(raw.shape[1], raw.shape[0])
                if factor > 1 and raw.ndim >= 2:
                    raw = raw[::factor, ::factor]

            data = ensure_c_contiguous_rgba(raw)
            if self.token.is_cancelled():
                self._finish(False, None)
                return

            # Store in cache if cache is active
            if self.cache is not None and getattr(self.cache, "mode", None) != CacheMode.OFF:
                if self.source_id:
                    self.cache.put_source_frame(
                        self.source_id,
                        self.source_frame,
                        data,
                        is_proxy=self.is_proxy,
                    )
                else:
                    self.cache.put_frame(
                        self.timeline_frame,
                        data,
                        is_proxy=self.is_proxy,
                    )

            self._finish(True, data)
        except Exception:
            self._finish(False, None)

    def _finish(self, success: bool, data: Optional[np.ndarray]) -> None:
        try:
            self.signals.loaded.emit(
                self.timeline_frame,
                self.source_frame,
                self.source_id,
                self.token.generation,
                success,
            )
            if self.callback:
                try:
                    self.callback(self.timeline_frame, self.source_frame, self.source_id, success, data, self.is_proxy)
                except TypeError:
                    self.callback(self.timeline_frame, self.source_frame, self.source_id, success, data)
        except Exception:
            pass


class PrefetchTask(QRunnable):
    """
    Thread-safe background prefetch task.
    Decodes and stores frames directly into thread-safe RAM cache from worker threads,
    avoiding main GUI thread queuing and event-loop lag.
    """

    def __init__(
        self,
        reader: Optional[MediaReader],
        cache,
        timeline_frame: int,
        source_frame: int = -1,
        source_id: str = "",
        generation: int = 0,
        is_cancelled_fn=None,
    ):
        super().__init__()
        self.reader = reader
        self.cache = cache
        self.timeline_frame = timeline_frame
        self.source_frame = source_frame if source_frame >= 0 else timeline_frame
        self.source_id = source_id
        self.generation = generation
        self.is_cancelled_fn = is_cancelled_fn
        self.signals = TaskSignals()

    def run(self) -> None:
        if self.reader is None:
            self._finish(False)
            return
        if self.is_cancelled_fn and self.is_cancelled_fn():
            self._finish(False)
            return

        try:
            raw = self.reader.read_frame(self.source_frame)
            if raw is not None:
                if self.is_cancelled_fn and self.is_cancelled_fn():
                    self._finish(False)
                    return

                data = ensure_c_contiguous_rgba(raw)

                # Store directly into thread-safe cache from background thread
                if self.cache is not None:
                    if self.source_id:
                        self.cache.put_source_frame(self.source_id, self.source_frame, data)
                    else:
                        self.cache.put_frame(self.timeline_frame, data)

                self._finish(True)
                return
        except Exception:
            pass

        self._finish(False)

    def _finish(self, success: bool) -> None:
        try:
            self.signals.loaded.emit(
                self.timeline_frame,
                self.source_frame,
                self.source_id,
                self.generation,
                success,
            )
        except Exception:
            pass


class BackgroundDiskCacheTask(QRunnable):
    """
    Background worker task that decodes and saves a frame directly to Temp Disk
    in the background worker thread.
    Zero disk writing and zero image buffer handling occur on the main GUI thread.
    """

    def __init__(
        self,
        reader: Optional[MediaReader],
        disk_cache,
        source_id: str,
        source_frame: int,
        generation: int,
        is_cancelled_fn=None,
    ):
        super().__init__()
        self.reader = reader
        self.disk_cache = disk_cache
        self.source_id = source_id
        self.source_frame = source_frame
        self.generation = generation
        self.is_cancelled_fn = is_cancelled_fn
        self.signals = TaskSignals()

    def run(self) -> None:
        if self.reader is None or (self.is_cancelled_fn and self.is_cancelled_fn()):
            self._finish(False)
            return

        try:
            if self.is_cancelled_fn and self.is_cancelled_fn():
                self._finish(False)
                return

            raw = self.reader.read_frame(self.source_frame)
            if raw is not None:
                if self.is_cancelled_fn and self.is_cancelled_fn():
                    self._finish(False)
                    return

                data = ensure_c_contiguous_rgba(raw)

                # Write directly to Temp Disk in this worker thread!
                # np.save and atomic os.replace happen HERE, NOT ON MAIN THREAD!
                if self.disk_cache is not None:
                    self.disk_cache.put(self.source_id, self.source_frame, data)

                self._finish(True)
                return
        except Exception:
            pass

        self._finish(False)

    def _finish(self, success: bool) -> None:
        try:
            self.signals.loaded.emit(
                -1,
                self.source_frame,
                self.source_id,
                self.generation,
                success,
            )
        except Exception:
            pass
        except Exception:
            pass


def create_reader_for_path(file_path: str) -> Optional[MediaReader]:
    """Factory creating an appropriate MediaReader for a video, image sequence, or still image."""
    if not file_path:
        return None

    path_str = str(file_path).strip()
    if not path_str:
        return None

    # 1. Video Files (MP4, MOV, MKV, AVI, etc.)
    try:
        from openreview.core.video_reader import is_video_file, VideoMediaReader
        if os.path.isfile(path_str) and is_video_file(path_str):
            return VideoMediaReader(path_str)
    except Exception:
        pass

    # 2. Image Sequences (explicit file, directory, or printf/hash pattern)
    try:
        from openreview.core.sequence_parser import detect_image_sequence, DiskSequenceReader
        seq = detect_image_sequence(path_str)
        if seq and len(seq.available_frames) > 0:
            return DiskSequenceReader(seq)

        # Handle hash patterns like /path/to/shot.####.exr or %04d where pattern string itself isn't a file
        if ("#" in path_str or "%" in path_str) and not os.path.exists(path_str):
            dir_name = os.path.dirname(path_str)
            if os.path.isdir(dir_name):
                seq_dir = detect_image_sequence(dir_name)
                if seq_dir and len(seq_dir.available_frames) > 0:
                    return DiskSequenceReader(seq_dir)
    except Exception:
        pass

    # 3. Single Still Image File (EXR, DPX, TIFF, PNG, JPG, HDR, etc.)
    if os.path.isfile(path_str):
        try:
            return DiskMediaReader(path_str)
        except Exception:
            pass

    # 4. Directory containing single frame or fallback sequence
    if os.path.isdir(path_str):
        try:
            from openreview.core.sequence_parser import detect_image_sequence, DiskSequenceReader
            seq = detect_image_sequence(path_str)
            if seq and len(seq.available_frames) > 0:
                return DiskSequenceReader(seq)
        except Exception:
            pass

    return None


class ReaderEngine(QObject):
    """
    Coordinates asynchronous frame prefetching using QThreadPool.
    Monitors playhead movements and dispatches priority-ordered read requests
    for uncached frames, delivering frames to the main UI thread via Qt Signals.
    Supports multi-source sequence prefetch resolution and instant token-based cancellation on scrub.
    """

    frame_ready = Signal(int)
    background_progress = Signal(str, int, int)  # (source_id, cached_count, total_count)

    @staticmethod
    def create_reader(file_path: str) -> Optional[MediaReader]:
        """Create a MediaReader (video, sequence, or still image) for the given path."""
        return create_reader_for_path(file_path)

    def __init__(self, reader: Optional[MediaReader], cache, parent: Optional[QObject] = None):
        super().__init__(parent)
        self.reader = reader
        self.cache = cache

        # Dual-Queue Architecture:
        # 1. Prefetch Pool: bounded background worker pool for lookahead & disk cache
        self._prefetch_pool = QThreadPool(self)
        self.thread_pool = self._prefetch_pool  # Backward-compatibility alias

        # 2. Interactive Pool: high-priority dedicated worker pool for active playhead seeks
        self._interactive_pool = QThreadPool(self)
        self._interactive_pool.setMaxThreadCount(2)

        self._sync_thread_count()
        self._pending_keys: set[tuple[str, int]] = set()  # (source_id, source_frame)
        self._bg_pending_keys: set[tuple[str, int]] = set()  # (source_id, source_frame) for background caching
        self._interactive_pending: set[tuple[str, int]] = set()
        self._generation = 0
        self._active_token = CancellationToken(generation=0)
        self._is_paused: bool = False
        self._last_prefetch_params = None
        self._bg_cache_queue: list[list] = []  # [source_id, reader, start_frame, end_frame, cursor]
        self._last_bg_progress_time: float = 0.0
        self._prefetch_chain_timer = QTimer(self)
        self._prefetch_chain_timer.setSingleShot(True)
        self._prefetch_chain_timer.setInterval(60)
        self._prefetch_chain_timer.timeout.connect(self._prefetch_next_batch)

        try:
            from openreview.core.preferences import PreferencesManager
            PreferencesManager.instance().preferences_changed.connect(self._on_preferences_changed)
        except Exception:
            pass

    def _sync_thread_count(self) -> None:
        try:
            from openreview.core.preferences import PreferencesManager
            prefs = PreferencesManager.instance().prefs
            total_cores = os.cpu_count() or 4
            # Adaptive CPU reservation: reserve at least 2 cores for the UI & OpenGL rendering
            default_threads = max(1, min(total_cores - 2, 4))
            threads = getattr(prefs, "prefetch_worker_threads", default_threads)
            self.thread_pool.setMaxThreadCount(max(1, min(int(threads), 4)))
        except Exception:
            self.thread_pool.setMaxThreadCount(2)

    def _on_preferences_changed(self, _prefs=None) -> None:
        self._sync_thread_count()

    @property
    def current_generation(self) -> int:
        return self._generation

    @property
    def is_paused(self) -> bool:
        return self._is_paused

    def pause_prefetch(self) -> None:
        """Pause prefetching and invalidate pending tasks (e.g. during active scrub/drag)."""
        self._is_paused = True
        self.cancel_pending()

    def resume_prefetch(self) -> None:
        """Resume prefetching after scrubbing or dragging stops."""
        self._is_paused = False
        self._schedule_next_background_cache_task()

    def cancel_pending(self) -> None:
        """Invalidate all pending in-flight background prefetch and interactive decode jobs."""
        self._active_token.request_cancellation()
        self._generation += 1
        self._active_token = CancellationToken(generation=self._generation)
        self._pending_keys.clear()
        self._bg_pending_keys.clear()
        self._interactive_pending.clear()
        if hasattr(self, "_prefetch_chain_timer"):
            self._prefetch_chain_timer.stop()

    def request_immediate_frame(
        self,
        source_id: str,
        source_frame: int,
        timeline_frame: int,
        reader: Optional[MediaReader] = None,
        downsample: bool = False,
        callback=None,
    ) -> None:
        """
        Request immediate high-priority asynchronous decode of the active playhead frame.
        Executes on the dedicated interactive worker pool without waiting for the prefetch queue.
        When downsample=True, decodes at an adaptive proxy resolution for fluid scrubbing.
        """
        rdr = reader or self.reader
        if rdr is None:
            return

        sid_str = source_id or ""
        is_proxy = bool(downsample)
        key = (sid_str, source_frame, is_proxy)
        if key in self._interactive_pending:
            return

        self._interactive_pending.add(key)
        token = self._active_token

        factor = 1
        if is_proxy:
            w = getattr(rdr, "width", 0)
            h = getattr(rdr, "height", 0)
            factor = get_adaptive_downsample_factor(w, h) if (w > 0 and h > 0) else 0

        def _on_done(tf, sf, sid, success, data, proxy_flag=False):
            self._interactive_pending.discard(key)
            if not token.is_cancelled() and success:
                self.frame_ready.emit(tf)
            if callback:
                try:
                    callback(tf, sf, sid, success, data, proxy_flag)
                except TypeError:
                    callback(tf, sf, sid, success, data)

        task = InteractiveDecodeTask(
            reader=rdr,
            cache=self.cache,
            timeline_frame=timeline_frame,
            source_frame=source_frame,
            source_id=sid_str,
            token=token,
            downsample_factor=factor,
            is_proxy=is_proxy,
            callback=_on_done,
        )
        self._interactive_pool.start(task, priority=10000)

    def set_reader(self, reader: Optional[MediaReader]) -> None:
        self.reader = reader
        self.cancel_pending()

    def request_prefetch(
        self,
        start_frame: int,
        end_frame: int,
        in_frame: Optional[int] = None,
        out_frame: Optional[int] = None,
        current_frame: Optional[int] = None,
        resolver_fn=None,
        max_batch: int = 16,
    ) -> None:
        if self._is_paused:
            return
        self._last_prefetch_params = (start_frame, end_frame, in_frame, out_frame, current_frame, resolver_fn, max_batch)
        if current_frame is not None:
            self.cache.update_playhead(current_frame)
            last_p = getattr(self, "_last_prefetch_playhead", None)
            # Only bump generation (invalidating in-flight tasks) on non-contiguous scrubs/jumps
            if last_p is None:
                self._generation += 1
                self._pending_keys.clear()
            elif abs(current_frame - last_p) > 2:
                is_loop_wrap = (
                    (last_p >= end_frame - 1 and current_frame <= start_frame + 1)
                    or (last_p <= start_frame + 1 and current_frame >= end_frame - 1)
                )
                if not is_loop_wrap:
                    self._generation += 1
                    self._pending_keys.clear()
            self._last_prefetch_playhead = current_frame

        curr_gen = self._generation

        window_start, window_end = self.cache.get_prefetch_targets(
            start_frame, end_frame, in_frame=in_frame, out_frame=out_frame
        )
        if window_start > window_end:
            return  # Cache is OFF or empty target window

        # Quick exit if all frames in the target window are already cached in full resolution
        if hasattr(self.cache, "get_cached_frames"):
            cached_set = self.cache.get_cached_frames(allow_proxy=False)
            if all(f in cached_set for f in range(window_start, window_end + 1)):
                return

        cur_f = current_frame if current_frame is not None else start_frame

        # Build prioritized timeline frame order (directional: forward first, then behind)
        ahead_frames = list(range(cur_f, window_end + 1))
        behind_frames = list(range(cur_f - 1, window_start - 1, -1))
        ordered_timeline_frames = ahead_frames + behind_frames

        queued_count = 0
        for tf in ordered_timeline_frames:
            if queued_count >= max_batch:
                break

            # Resolve timeline frame to underlying source item(s)
            targets: list[tuple[Optional[str], int, Optional[MediaReader]]] = []
            if resolver_fn is not None:
                try:
                    targets = resolver_fn(tf)
                except Exception:
                    targets = []
            elif self.reader is not None:
                cur_sid = getattr(self.cache, "_active_source_id", None)
                targets = [(cur_sid, tf, self.reader)]

            for src_id, src_frame, rdr in targets:
                if rdr is None:
                    continue

                sid_str = src_id or ""
                cache_key = (sid_str, src_frame)

                # Check if already cached in full resolution
                if sid_str:
                    if self.cache.has_source_frame(sid_str, src_frame, allow_proxy=False):
                        continue
                else:
                    if self.cache.has_frame(src_frame, allow_proxy=False):
                        continue

                if cache_key in self._pending_keys:
                    continue

                self._pending_keys.add(cache_key)
                queued_count += 1

                # Calculate task priority: immediate upcoming frames get highest priority
                dist = tf - cur_f
                if dist >= 0:
                    prio = max(100, 2000 - min(1900, dist))
                else:
                    prio = max(0, 500 - min(500, abs(dist)))

                token = self._active_token
                task = PrefetchTask(
                    reader=rdr,
                    cache=self.cache,
                    timeline_frame=tf,
                    source_frame=src_frame,
                    source_id=sid_str,
                    generation=curr_gen,
                    is_cancelled_fn=lambda g=curr_gen, tok=token: g < self._generation or tok.is_cancelled(),
                )
                task.signals.loaded.connect(self._on_frame_loaded)
                self.thread_pool.start(task, priority=prio)

    def _on_frame_loaded(
        self,
        timeline_frame: int,
        source_frame: int,
        *args,
        **kwargs,
    ) -> None:
        # Flexible argument extraction supporting new lightweight signal and legacy/test signatures
        if len(args) >= 1 and isinstance(args[0], (np.ndarray, type(None))):
            data = args[0]
            source_id = args[1] if len(args) > 1 else kwargs.get("source_id", "")
            generation = args[2] if len(args) > 2 else kwargs.get("generation", 0)
            success = (data is not None)
        else:
            source_id = args[0] if len(args) > 0 else kwargs.get("source_id", "")
            generation = args[1] if len(args) > 1 else kwargs.get("generation", 0)
            success = args[2] if len(args) > 2 else kwargs.get("success", True)
            data = kwargs.get("data", None)

        key = (source_id, source_frame)
        self._pending_keys.discard(key)

        if self._is_paused or generation < self._generation:
            return

        # Direct insertion fallback if data was passed explicitly (e.g. from tests)
        if data is not None and self.cache is not None:
            if source_id:
                self.cache.put_source_frame(source_id, source_frame, data)
            else:
                self.cache.put_frame(timeline_frame, data)

        if success:
            self.frame_ready.emit(timeline_frame)

        # Smooth cooperative chaining of next batch when worker queue has breathing room
        if not self._is_paused and len(self._pending_keys) <= 2:
            if self._last_prefetch_params and hasattr(self, "_prefetch_chain_timer") and not self._prefetch_chain_timer.isActive():
                self._prefetch_chain_timer.start(30)
            elif len(self._pending_keys) == 0:
                self._schedule_next_background_cache_task()

    def _prefetch_next_batch(self) -> None:
        if not self._is_paused:
            if self._last_prefetch_params:
                self.request_prefetch(*self._last_prefetch_params)
            if len(self._pending_keys) == 0:
                self._schedule_next_background_cache_task()

    def queue_source_background_cache(
        self,
        source_id: str,
        reader: MediaReader,
        start_frame: int,
        end_frame: int,
    ) -> None:
        """Queue a source sequence to be cached in the background onto Temp Disk at idle priority."""
        if not source_id or not reader:
            return
        self._bg_cache_queue.append([source_id, reader, start_frame, end_frame, start_frame])
        self._schedule_next_background_cache_task()

    def clear_background_cache_queue(self) -> None:
        """Clear all queued secondary background caching jobs."""
        self._bg_cache_queue.clear()
        self._bg_pending_keys.clear()

    def _schedule_next_background_cache_task(self) -> None:
        # If prefetch is paused, or if interactive playback prefetch is active, yield CPU to UI
        if self._is_paused or len(self._pending_keys) > 0:
            return
        if len(self._bg_pending_keys) >= 2:
            return
        if not self._bg_cache_queue:
            return

        disk_cache = getattr(self.cache, "disk_cache", None)
        if disk_cache is None or not getattr(self.cache, "_disk_cache_enabled", True):
            return

        while self._bg_cache_queue:
            item = self._bg_cache_queue[0]
            src_id, rdr, s_frame, e_frame, cursor = item[0], item[1], item[2], item[3], item[4]
            uncached_frame = None

            curr_f = cursor
            while curr_f <= e_frame:
                key = (src_id, curr_f)
                if key not in self._bg_pending_keys and not self.cache.has_source_frame(src_id, curr_f, allow_proxy=False):
                    uncached_frame = curr_f
                    item[4] = curr_f + 1
                    break
                curr_f += 1

            if uncached_frame is None:
                # Sequence completed!
                self._bg_cache_queue.pop(0)
                total_span = max(1, e_frame - s_frame + 1)
                self.background_progress.emit(src_id, total_span, total_span)
                continue

            self._bg_pending_keys.add((src_id, uncached_frame))
            curr_gen = self._generation

            token = self._active_token
            task = BackgroundDiskCacheTask(
                reader=rdr,
                disk_cache=disk_cache,
                source_id=src_id,
                source_frame=uncached_frame,
                generation=curr_gen,
                is_cancelled_fn=lambda g=curr_gen, tok=token: g < self._generation or tok.is_cancelled() or self._is_paused or len(self._pending_keys) > 0,
            )
            task.signals.loaded.connect(self._on_background_frame_loaded)
            self.thread_pool.start(task, priority=0)
            break

    def _on_background_frame_loaded(
        self,
        timeline_frame: int,
        source_frame: int,
        *args,
        **kwargs,
    ) -> None:
        if len(args) >= 1 and isinstance(args[0], (np.ndarray, type(None))):
            source_id = args[1] if len(args) > 1 else kwargs.get("source_id", "")
            generation = args[2] if len(args) > 2 else kwargs.get("generation", 0)
        else:
            source_id = args[0] if len(args) > 0 else kwargs.get("source_id", "")
            generation = args[1] if len(args) > 1 else kwargs.get("generation", 0)

        key = (source_id, source_frame)
        self._bg_pending_keys.discard(key)
        if self._is_paused or generation < self._generation:
            return

        # Notify timeline / UI of cache updates (coalesced by UI timer)
        if hasattr(self.cache, "_emit_status"):
            self.cache._emit_status()

        # Throttled status updates (at most once every 200ms) to prevent UI thread flooding
        now = time.time()
        if self._bg_cache_queue and (now - self._last_bg_progress_time > 0.2):
            self._last_bg_progress_time = now
            cur_sid, _, s_f, e_f, _ = self._bg_cache_queue[0]
            if cur_sid == source_id:
                total_span = max(1, e_f - s_f + 1)
                cached_count = self.cache.get_source_disk_cached_count(source_id) if hasattr(self.cache, "get_source_disk_cached_count") else 0
                self.background_progress.emit(source_id, cached_count, total_span)

        if not self._is_paused and len(self._pending_keys) == 0 and len(self._bg_pending_keys) < 2:
            self._schedule_next_background_cache_task()


class CallableVirtualReader(MediaReader):
    """Virtual MediaReader wrapping a dynamic frame generation or composition callback."""

    def __init__(self, fetch_fn):
        self._fetch_fn = fetch_fn

    def read_frame(self, frame: int) -> Optional[Any]:
        try:
            return self._fetch_fn(frame)
        except Exception:
            return None


