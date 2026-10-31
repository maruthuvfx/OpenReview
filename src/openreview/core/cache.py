"""
Ring-Buffer Frame Cache and RAM Pool Manager.
Provides bounded in-memory frame storage, lookahead prefetching, and LRU eviction.
"""
from __future__ import annotations

from enum import Enum
import atexit
import collections
import ctypes
import os
import re
import shutil
import sys
import tempfile
import threading
import time
from typing import Dict, List, Optional, Set, Tuple, Union, Any
import numpy as np
from PySide6.QtCore import QObject, Signal


def get_system_ram_stats_mb() -> Tuple[float, float]:
    """
    Detect total and currently available system RAM in megabytes.
    Returns (total_ram_mb, avail_ram_mb).
    """
    try:
        import psutil
        vm = psutil.virtual_memory()
        total_mb = float(vm.total) / (1024.0 * 1024.0)
        avail_mb = float(vm.available) / (1024.0 * 1024.0)
        if total_mb > 0:
            return total_mb, max(256.0, avail_mb)
    except Exception:
        pass

    if sys.platform == "win32":
        try:
            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("sullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            stat = MEMORYSTATUSEX()
            stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):
                total_mb = float(stat.ullTotalPhys) / (1024.0 * 1024.0)
                avail_mb = float(stat.ullAvailPhys) / (1024.0 * 1024.0)
                if total_mb > 0:
                    return total_mb, max(256.0, avail_mb)
        except Exception:
            pass

    return 16384.0, 8192.0


def compute_dynamic_ram_budget_mb(
    used_cache_bytes: int = 0,
    reserve_ratio: float = 0.80,
    min_os_headroom_mb: float = 2048.0,
) -> float:
    """
    Compute safe RAM budget in MB based on currently available system RAM plus
    any memory already held by OpenReview's frame cache.
    Allocates up to `reserve_ratio` (default 80%) of usable RAM while preserving
    at least `min_os_headroom_mb` (scaled on low-RAM machines) for the OS and other apps.
    """
    total_mb, os_avail_mb = get_system_ram_stats_mb()
    used_cache_mb = max(0.0, float(used_cache_bytes) / (1024.0 * 1024.0))
    usable_mb = min(total_mb, os_avail_mb + used_cache_mb)

    effective_headroom = min(min_os_headroom_mb, total_mb * 0.20)
    max_after_headroom = max(256.0, usable_mb - effective_headroom)
    target_mb = min(usable_mb * reserve_ratio, max_after_headroom)

    if target_mb >= 1024.0:
        return float(round(target_mb, -2))
    return float(max(256.0, round(target_mb)))


def get_dynamic_gb_presets(total_ram_mb: Optional[float] = None) -> List[int]:
    """
    Generate power-of-two GB presets scaling dynamically up to system total RAM
    without restricting to 16 GB (e.g., 1, 2, 4, 8, 16, 32, 64, 128, 256+ GB).
    """
    if total_ram_mb is None:
        total_ram_mb, _ = get_system_ram_stats_mb()
    total_gb = max(8.0, total_ram_mb / 1024.0)

    presets: List[int] = []
    gb = 1
    while gb <= total_gb:
        presets.append(gb)
        gb *= 2

    # Ensure standard baseline presets exist for tests/low-spec machines
    for base_gb in (1, 2, 4, 8):
        if base_gb not in presets:
            presets.append(base_gb)
    presets.sort()
    return presets


class CacheMode(str, Enum):
    """Caching strategy modes for OpenReview (modeled on Open RV)."""
    OFF = "off"                    # CacheOff (-nc): Caching disabled, zero background prefetch
    PLAY_AHEAD = "play_ahead"      # CacheBuffer (-l): Lookahead sliding buffer ahead and behind playhead
    REGION = "region"              # CacheGreedy (-c): Strictly cache within active In/Out region marks
    ALL = "all"                    # Full sequence cache


class AudioCacheMode(str, Enum):
    """Audio caching strategy modes (modeled on Open RV setAudioCacheMode)."""
    OFF = "off"                    # Audio caching disabled
    BUFFER = "buffer"              # Lookahead streaming audio buffer
    GREEDY = "greedy"              # Greedy pre-buffering for responsive scrubbing


class DiskFrameCache:
    """
    Tier-2 Disk Frame Cache storing frames on the system temporary drive.
    - Stores frames as fast float16 or uint8 NumPy .npy files.
    - Thread-safe write and read operations.
    - Enforces a dynamic disk budget (default 20 GB or 50% available disk space).
    - Evicts least-recently-used (LRU) files when disk budget is exceeded.
    - Cleans up temporary cache directory on application shutdown.
    """

    def __init__(
        self,
        cache_dir: Optional[str] = None,
        max_disk_mb: Optional[float] = None,
        auto_cleanup: bool = True,
    ):
        self._lock = threading.Lock()
        if cache_dir is None:
            base_temp = os.path.join(tempfile.gettempdir(), "OpenReview_Cache")
            self._cache_dir = os.path.join(base_temp, f"session_{os.getpid()}_{id(self)}")
        else:
            self._cache_dir = cache_dir

        os.makedirs(self._cache_dir, exist_ok=True)
        self._auto_cleanup = auto_cleanup
        self._cleaned_up = False

        if max_disk_mb is not None:
            self._max_disk_bytes = int(max(0.01, float(max_disk_mb)) * 1024 * 1024)
        else:
            self._max_disk_bytes = self._calculate_default_budget_bytes()

        self._disk_files: Dict[Tuple[str, int], str] = {}  # (source_id, frame) -> file_path
        self._disk_bytes: Dict[Tuple[str, int], int] = {}  # (source_id, frame) -> byte_size
        self._access_times: Dict[Tuple[str, int], float] = {}  # (source_id, frame) -> last_access_ts
        self._source_frames: Dict[str, Set[int]] = collections.defaultdict(set)
        self._used_disk_bytes: int = 0

        if self._auto_cleanup:
            atexit.register(self.cleanup)

    def _calculate_default_budget_bytes(self) -> int:
        """Default budget: 20 GB or 50% free disk space, whichever is smaller (min 512 MB)."""
        try:
            total, used, free = shutil.disk_usage(self._cache_dir)
            cap_20gb = 20 * 1024 * 1024 * 1024
            half_free = int(free * 0.50)
            return max(512 * 1024 * 1024, min(cap_20gb, half_free))
        except Exception:
            return 20 * 1024 * 1024 * 1024

    def _file_path(self, source_id: str, frame: int) -> str:
        safe_sid = re.sub(r"[^a-zA-Z0-9_\-]", "_", str(source_id))
        return os.path.join(self._cache_dir, f"frame_{safe_sid}_{frame}.npy")

    @property
    def cache_dir(self) -> str:
        return self._cache_dir

    @property
    def max_disk_mb(self) -> float:
        with self._lock:
            return self._max_disk_bytes / (1024.0 * 1024.0)

    def set_max_disk_mb(self, mb: float) -> None:
        with self._lock:
            self._max_disk_bytes = int(max(0.01, float(mb)) * 1024 * 1024)
            self._evict_if_needed_locked()

    def has(self, source_id: str, frame: int) -> bool:
        if not source_id:
            return False
        key = (source_id, frame)
        with self._lock:
            return key in self._disk_files

    def get(self, source_id: str, frame: int) -> Optional[np.ndarray]:
        if not source_id:
            return None
        key = (source_id, frame)
        path = None
        with self._lock:
            if key in self._disk_files:
                path = self._disk_files[key]
                self._access_times[key] = time.time()
        if path and os.path.exists(path):
            try:
                arr = np.load(path)
                return arr
            except Exception:
                self.delete(source_id, frame)
                return None
        return None

    def put(self, source_id: str, frame: int, image_data: np.ndarray) -> bool:
        if not source_id or image_data is None:
            return False
        key = (source_id, frame)
        save_arr = image_data
        if isinstance(image_data, np.ndarray) and image_data.dtype == np.float32:
            try:
                save_arr = image_data.astype(np.float16, copy=False)
            except Exception:
                save_arr = image_data

        path = self._file_path(source_id, frame)
        safe_sid = re.sub(r"[^a-zA-Z0-9_\-]", "_", str(source_id))
        tmp_path = os.path.join(self._cache_dir, f"tmp_{os.getpid()}_{threading.get_ident()}_{safe_sid}_{frame}.npy")
        try:
            np.save(tmp_path, save_arr)
            os.replace(tmp_path, path)
            file_bytes = os.path.getsize(path)
        except Exception:
            try:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
            except Exception:
                pass
            return False

        with self._lock:
            if key in self._disk_files:
                self._used_disk_bytes -= self._disk_bytes.get(key, 0)
            self._disk_files[key] = path
            self._disk_bytes[key] = file_bytes
            self._access_times[key] = time.time()
            self._used_disk_bytes += file_bytes
            self._source_frames[source_id].add(frame)
            self._evict_if_needed_locked()
        return True

    def delete(self, source_id: str, frame: int) -> None:
        key = (source_id, frame)
        path = None
        with self._lock:
            if key in self._disk_files:
                path = self._disk_files.pop(key, None)
                b = self._disk_bytes.pop(key, 0)
                self._access_times.pop(key, None)
                self._used_disk_bytes -= b
                if source_id in self._source_frames:
                    self._source_frames[source_id].discard(frame)
        if path and os.path.exists(path):
            try:
                os.remove(path)
            except Exception:
                pass

    def clear_source(self, source_id: str) -> None:
        if not source_id:
            return
        to_del = []
        with self._lock:
            self._source_frames.pop(source_id, None)
            keys = [k for k in self._disk_files.keys() if k[0] == source_id]
            for k in keys:
                path = self._disk_files.pop(k, None)
                b = self._disk_bytes.pop(k, 0)
                self._access_times.pop(k, None)
                self._used_disk_bytes -= b
                if path:
                    to_del.append(path)
        for p in to_del:
            try:
                if os.path.exists(p):
                    os.remove(p)
            except Exception:
                pass

    def clear(self) -> None:
        to_del = []
        with self._lock:
            self._source_frames.clear()
            to_del = list(self._disk_files.values())
            self._disk_files.clear()
            self._disk_bytes.clear()
            self._access_times.clear()
            self._used_disk_bytes = 0
        for p in to_del:
            try:
                if os.path.exists(p):
                    os.remove(p)
            except Exception:
                pass

    def _evict_if_needed_locked(self) -> None:
        if self._used_disk_bytes <= self._max_disk_bytes:
            return
        sorted_keys = sorted(self._disk_files.keys(), key=lambda k: self._access_times.get(k, 0.0))
        for k in sorted_keys:
            if self._used_disk_bytes <= self._max_disk_bytes:
                break
            path = self._disk_files.pop(k, None)
            b = self._disk_bytes.pop(k, 0)
            self._access_times.pop(k, None)
            self._used_disk_bytes -= b
            sid, frm = k
            if sid in self._source_frames:
                self._source_frames[sid].discard(frm)
            if path and os.path.exists(path):
                try:
                    os.remove(path)
                except Exception:
                    pass

    def get_stats(self) -> Tuple[float, float, float]:
        with self._lock:
            used_mb = self._used_disk_bytes / (1024.0 * 1024.0)
            max_mb = self._max_disk_bytes / (1024.0 * 1024.0)
            pct = (self._used_disk_bytes / self._max_disk_bytes) * 100.0 if self._max_disk_bytes > 0 else 0.0
            return used_mb, max_mb, pct

    def get_cached_frames(self, source_id: Optional[str] = None) -> Set[int]:
        with self._lock:
            if source_id:
                return set(self._source_frames.get(source_id, set()))
            return {k[1] for k in self._disk_files.keys()}

    def get_source_cached_count(self, source_id: str) -> int:
        with self._lock:
            return len(self._source_frames.get(source_id, ()))

    def cleanup(self) -> None:
        with self._lock:
            if self._cleaned_up:
                return
            self._cleaned_up = True
            files = list(self._disk_files.values())
            self._disk_files.clear()
            self._disk_bytes.clear()
            self._access_times.clear()
            self._used_disk_bytes = 0
        for f in files:
            try:
                if os.path.exists(f):
                    os.remove(f)
            except Exception:
                pass
        try:
            if os.path.exists(self._cache_dir):
                shutil.rmtree(self._cache_dir, ignore_errors=True)
        except Exception:
            pass


class RingBufferCache(QObject):
    """
    Thread-safe RAM ring buffer for image review frames.
    - Adapts RAM usage dynamically to system RAM availability or a configured budget.
    - Supports multiple cache modes: OFF (default), PLAY_AHEAD, REGION (In/Out only), and ALL.
    - Evicts frames furthest from the current playhead or outside In/Out marks.
    - Generates cache status bitmasks and utilization percentages for the UI.
    """

    cache_changed = Signal()  # Emitted when cached frame set changes
    memory_usage_changed = Signal(float, float)  # used_mb, max_mb
    disk_usage_changed = Signal(float, float)  # disk_used_mb, disk_max_mb
    mode_changed = Signal(str)  # Emitted when cache mode changes ('off', 'play_ahead', 'region', 'all')
    buffering_state_changed = Signal(bool)  # Emitted when waiting for buffer refill (isBuffering in Open RV)
    audio_cache_mode_changed = Signal(str)  # Emitted when audio cache mode changes

    def __init__(
        self,
        max_memory_mb: Optional[float] = None,
        lookahead_frames: int = 48,
        lookbehind_frames: Optional[int] = None,
        lookback_ratio: float = 0.20,
        buffer_wait_seconds: float = 1.0,
        mode: CacheMode = CacheMode.OFF,
        auto_memory_budget: bool = False,
        enable_disk_cache: bool = True,
        max_disk_mb: Optional[float] = None,
        disk_cache_dir: Optional[str] = None,
        parent: Optional[QObject] = None,
    ):
        super().__init__(parent)
        self._mode = mode
        self._auto_memory_budget = bool(auto_memory_budget or max_memory_mb is None)
        self._last_auto_refresh_ts: float = 0.0
        initial_mb = (
            compute_dynamic_ram_budget_mb(0)
            if max_memory_mb is None
            else max(0.01, float(max_memory_mb))
        )
        self._max_memory_bytes = int(initial_mb * 1024 * 1024)
        self._lookahead = lookahead_frames
        self._lookback_ratio = float(lookback_ratio)
        self._lookbehind = max(1, lookbehind_frames if lookbehind_frames is not None else int(round(lookahead_frames * self._lookback_ratio)))
        self._buffer_wait_seconds = float(buffer_wait_seconds)
        self._is_buffering = False
        self._audio_cache_mode = AudioCacheMode.BUFFER
        self._audio_cached_seconds = 0.0
        self._audio_scrubbing = False
        self._pre_scrub_audio_mode = AudioCacheMode.BUFFER
        self._current_playhead = 1001
        self._in_frame: Optional[int] = None
        self._out_frame: Optional[int] = None

        self._lock = threading.Lock()
        self._active_source_id: Optional[str] = None
        self._pinned_keys: set[Any] = set()
        self._proxy_keys: set[Any] = set()  # (source_id, frame) or int frame -> proxy tag
        self._last_emit_ts: float = 0.0
        # Tier 1 Storage: Primary display RGBA textures for real-time playback
        # Storage: (source_id, frame) or int frame -> np.ndarray
        self._frames: Dict[Any, Any] = {}
        self._frame_bytes: Dict[Any, int] = {}
        self._frame_mtimes: Dict[Any, float] = {}
        self._used_bytes: int = 0

        # Tier 2 Storage: Lazy raw multi-channel / pass layer cache
        # Storage: (source_id, frame, layer_name) -> np.ndarray
        self._layer_cache: Dict[Tuple[str, int, str], np.ndarray] = {}
        self._layer_bytes: Dict[Tuple[str, int, str], int] = {}
        self._layer_used_bytes: int = 0
        self._max_layer_memory_bytes: int = int(self._max_memory_bytes * 0.5)  # 50% budget for raw layers

        # Tier 3 Storage: High-capacity Temp Drive Disk Cache
        self._disk_cache_enabled = bool(enable_disk_cache)
        self._disk_cache = DiskFrameCache(
            cache_dir=disk_cache_dir,
            max_disk_mb=max_disk_mb,
            auto_cleanup=True,
        )

    # --------------------------------------------------------------------------
    # Configuration
    # --------------------------------------------------------------------------
    @property
    def mode(self) -> CacheMode:
        return self._mode

    @property
    def auto_memory_budget(self) -> bool:
        return self._auto_memory_budget

    @property
    def max_memory_mb(self) -> float:
        with self._lock:
            return self._max_memory_bytes / (1024.0 * 1024.0)

    def pin_frame(self, source_id: Optional[str], frame: int) -> None:
        """Pin a frame in the cache to prevent eviction while it is actively being displayed/streamed."""
        key = (source_id, frame) if source_id else frame
        with self._lock:
            self._pinned_keys.add(key)

    def unpin_frame(self, source_id: Optional[str], frame: int) -> None:
        """Unpin a previously pinned frame."""
        key = (source_id, frame) if source_id else frame
        with self._lock:
            self._pinned_keys.discard(key)

    def is_pinned(self, source_id: Optional[str], frame: int) -> bool:
        """Check if a frame is currently pinned."""
        key = (source_id, frame) if source_id else frame
        with self._lock:
            return key in self._pinned_keys

    def _refresh_auto_budget_locked(self, force: bool = False) -> None:
        """Recalculate memory budget from live available system RAM while lock is held."""
        if not self._auto_memory_budget:
            return
        now = time.monotonic()
        if not force and (now - self._last_auto_refresh_ts) < 2.5:
            return
        self._last_auto_refresh_ts = now
        new_mb = compute_dynamic_ram_budget_mb(self._used_bytes)
        self._max_memory_bytes = int(max(128.0, new_mb) * 1024 * 1024)
        self._max_layer_memory_bytes = int(self._max_memory_bytes * 0.5)

    def refresh_auto_budget(self) -> float:
        """Force an immediate refresh of the dynamic available RAM budget and return max_mb."""
        with self._lock:
            self._refresh_auto_budget_locked(force=True)
            self._evict_if_needed(self._current_playhead)
            max_mb = self._max_memory_bytes / (1024.0 * 1024.0)
        self._emit_status()
        return max_mb

    def set_auto_memory_budget(self, enabled: bool = True) -> float:
        """Enable or disable dynamic system available RAM budget."""
        with self._lock:
            self._auto_memory_budget = bool(enabled)
            if self._auto_memory_budget:
                self._refresh_auto_budget_locked(force=True)
                self._evict_if_needed(self._current_playhead)
            max_mb = self._max_memory_bytes / (1024.0 * 1024.0)
        self._emit_status()
        return max_mb

    def set_active_source(self, source_id: Optional[str]) -> None:
        """Set the active source context for single-frame lookup/store."""
        self._active_source_id = source_id

    def set_mode(self, mode: CacheMode | str) -> None:
        """Update caching operational mode."""
        if isinstance(mode, str):
            mode = CacheMode(mode)
        if self._mode != mode:
            self._mode = mode
            self._emit_status()
            self.mode_changed.emit(self._mode.value)

    def set_region_bounds(self, in_frame: int, out_frame: int) -> None:
        """Inform the cache of the active In/Out region marks."""
        self._in_frame = in_frame
        self._out_frame = out_frame
        if self._mode == CacheMode.REGION:
            with self._lock:
                self._evict_if_needed(self._current_playhead)
            self._emit_status()

    @property
    def disk_cache_enabled(self) -> bool:
        return self._disk_cache_enabled

    def set_disk_cache_enabled(self, enabled: bool) -> None:
        self._disk_cache_enabled = bool(enabled)
        if not self._disk_cache_enabled and self._disk_cache:
            self._disk_cache.clear()
        self._emit_status()

    @property
    def disk_cache(self) -> DiskFrameCache:
        return self._disk_cache

    def set_max_disk_mb(self, mb: float) -> None:
        if self._disk_cache:
            self._disk_cache.set_max_disk_mb(mb)
            self._emit_status()

    def get_disk_memory_stats(self) -> Tuple[float, float, float]:
        if self._disk_cache:
            return self._disk_cache.get_stats()
        return 0.0, 0.0, 0.0

    def set_max_memory_mb(self, mb: float, auto: bool = False) -> None:
        """Update maximum memory budget in megabytes."""
        with self._lock:
            self._auto_memory_budget = bool(auto)
            self._max_memory_bytes = int(max(1.0, mb) * 1024 * 1024)
            self._max_layer_memory_bytes = int(self._max_memory_bytes * 0.5)
            self._evict_if_needed(self._current_playhead)
        self._emit_status()

    def set_prefetch_range(self, lookahead: int, lookbehind: int) -> None:
        """Configure how many frames to prefetch ahead and behind the playhead."""
        self._lookahead = max(1, lookahead)
        self._lookbehind = max(0, lookbehind)

    @property
    def lookback_ratio(self) -> float:
        """Ratio of lookahead buffer reserved behind playhead (default 0.20 matching Open RV -lookback)."""
        return self._lookback_ratio

    def set_lookback_ratio(self, ratio: float) -> None:
        """Set ratio of lookahead buffer reserved behind playhead (modeled on Open RV -lookback)."""
        self._lookback_ratio = max(0.0, min(1.0, float(ratio)))
        self._lookbehind = max(1, int(round(self._lookahead * self._lookback_ratio)))

    @property
    def buffer_wait_seconds(self) -> float:
        """Max buffer wait time in cached seconds (modeled on Open RV -bwait)."""
        return self._buffer_wait_seconds

    def set_buffer_wait_seconds(self, secs: float) -> None:
        """Set max buffer wait time in cached seconds (modeled on Open RV -bwait)."""
        self._buffer_wait_seconds = max(0.0, float(secs))

    @property
    def is_buffering(self) -> bool:
        """Returns True if the renderer is paused to allow cache buffering to occur (Open RV isBuffering)."""
        return self._is_buffering

    def set_buffering(self, buffering: bool) -> None:
        """Set playback buffering pause state."""
        b = bool(buffering)
        if self._is_buffering != b:
            self._is_buffering = b
            self.buffering_state_changed.emit(self._is_buffering)

    @property
    def audio_cache_mode(self) -> AudioCacheMode:
        """Active audio caching mode (modeled on Open RV audioCacheMode)."""
        return self._audio_cache_mode

    def set_audio_cache_mode(self, mode: Union[AudioCacheMode, str]) -> None:
        """Set audio cache mode (modeled on Open RV setAudioCacheMode)."""
        if isinstance(mode, str):
            mode = AudioCacheMode(mode)
        if self._audio_cache_mode != mode:
            self._audio_cache_mode = mode
            self.audio_cache_mode_changed.emit(self._audio_cache_mode.value)

    @property
    def audio_cached_seconds(self) -> float:
        """Cached audio buffer duration in seconds."""
        return self._audio_cached_seconds

    def set_audio_cached_seconds(self, secs: float) -> None:
        """Update cached audio duration in seconds."""
        self._audio_cached_seconds = max(0.0, float(secs))

    def set_audio_scrubbing(self, scrubbing: bool) -> None:
        """
        Dynamically toggle audio scrubbing mode (modeled on Open RV rvui.mu):
        Switches to greedy audio buffering while actively scrubbing to avoid clicks/starvation.
        """
        b = bool(scrubbing)
        if self._audio_scrubbing != b:
            self._audio_scrubbing = b
            if self._audio_scrubbing:
                self._pre_scrub_audio_mode = self._audio_cache_mode
                self.set_audio_cache_mode(AudioCacheMode.GREEDY)
            else:
                self.set_audio_cache_mode(self._pre_scrub_audio_mode)

    def cache_info(self) -> Tuple[int, int, int, float, float, float, List[Tuple[int, int]]]:
        """
        Returns Open RV compatible tuple:
        (
            capacity_bytes,       # int64: capacity of cache in bytes
            bytes_used,           # int64: bytes used by cache
            lookahead_bytes_used, # int64: bytes dedicated to forward look-ahead
            lookahead_seconds,    # float: look-ahead cached seconds
            lookahead_wait_time,  # float: look-ahead wait time in seconds (-bwait)
            audio_cached_seconds, # float: audio cache time in seconds
            cached_frame_ranges   # List[Tuple[int, int]]: start-end frame pairs of cached ranges
        )
        """
        with self._lock:
            cap = self._max_memory_bytes
            used = self._used_bytes
            ranges = self._compute_contiguous_ranges_locked()
            fps = 24.0
            lookahead_frames_count = 0
            lookahead_bytes = 0
            for k, b in self._frame_bytes.items():
                frm = k[1] if isinstance(k, tuple) else int(k)
                if frm > self._current_playhead:
                    lookahead_frames_count += 1
                    lookahead_bytes += b
            lookahead_sec = lookahead_frames_count / fps
            return (
                cap,
                used,
                lookahead_bytes,
                lookahead_sec,
                self._buffer_wait_seconds,
                self._audio_cached_seconds,
                ranges,
            )

    def get_contiguous_cached_ranges(self, source_id: Optional[str] = None) -> List[Tuple[int, int]]:
        """Return contiguous sorted [start_frame, end_frame] tuples for timeline cache bar drawing."""
        with self._lock:
            return self._compute_contiguous_ranges_locked(source_id)

    def _compute_contiguous_ranges_locked(self, source_id: Optional[str] = None) -> List[Tuple[int, int]]:
        frames: List[int] = []
        for k in self._frames.keys():
            if isinstance(k, tuple):
                if source_id is None or k[0] == source_id:
                    frames.append(k[1])
            elif isinstance(k, int):
                frames.append(k)
        if not frames:
            return []
        sorted_f = sorted(set(frames))
        ranges: List[Tuple[int, int]] = []
        start_f = sorted_f[0]
        end_f = start_f
        for f in sorted_f[1:]:
            if f == end_f + 1:
                end_f = f
            else:
                ranges.append((start_f, end_f))
                start_f = f
                end_f = f
        ranges.append((start_f, end_f))
        return ranges

    def clear_all_but_frame(self, frame: int) -> None:
        """Clear the frame cache except for the given frame (modeled on Open RV clearAllButFrame)."""
        with self._lock:
            to_remove = []
            for k in self._frames.keys():
                frm = k[1] if isinstance(k, tuple) else int(k)
                if frm != frame:
                    to_remove.append(k)
            for k in to_remove:
                b = self._frame_bytes.pop(k, 0)
                self._frames.pop(k, None)
                self._frame_mtimes.pop(k, None)
                self._proxy_keys.discard(k)
                self._used_bytes -= b
        self._emit_status()

    def release_all_cached_images(self) -> None:
        """Force flushing and deallocation of all cached image data (Open RV releaseAllCachedImages)."""
        self.clear()

    # --------------------------------------------------------------------------
    # Source-Level Cache Access
    # --------------------------------------------------------------------------
    def is_source_frame_proxy(self, source_id: str, frame: int) -> bool:
        """Check if a specific source frame in RAM is a downsampled proxy."""
        if not source_id:
            return False
        with self._lock:
            return (source_id, frame) in self._proxy_keys

    def is_frame_proxy(self, frame: int, source_id: Optional[str] = None) -> bool:
        """Check if a cached frame is a downsampled proxy."""
        with self._lock:
            sid = source_id or self._active_source_id
            if sid and (sid, frame) in self._proxy_keys:
                return True
            return frame in self._proxy_keys

    def get_source_frame(self, source_id: str, frame: int, allow_proxy: bool = True) -> Optional[np.ndarray]:
        """Retrieve a cached frame for a specific input source (RAM first, then Temp Disk)."""
        if not source_id:
            return None
        with self._lock:
            key = (source_id, frame)
            if not (not allow_proxy and key in self._proxy_keys):
                ram_res = self._frames.get(key)
                if ram_res is not None:
                    return ram_res
        if self._disk_cache_enabled and self._disk_cache:
            disk_res = self._disk_cache.get(source_id, frame)
            if disk_res is not None:
                return disk_res
        return None

    def has_source_frame(self, source_id: str, frame: int, allow_proxy: bool = True) -> bool:
        """Check if a specific source frame is in RAM or Temp Disk cache."""
        if not source_id:
            return False
        with self._lock:
            key = (source_id, frame)
            if key in self._frames:
                if allow_proxy or (key not in self._proxy_keys):
                    return True
        if self._disk_cache_enabled and self._disk_cache:
            return self._disk_cache.has(source_id, frame)
        return False

    def has_source_frame_ram(self, source_id: str, frame: int) -> bool:
        """Check if frame is in Tier-1 RAM cache specifically."""
        if not source_id:
            return False
        with self._lock:
            return (source_id, frame) in self._frames

    def has_source_frame_disk(self, source_id: str, frame: int) -> bool:
        """Check if frame is in Tier-2 Temp Disk cache specifically."""
        if not self._disk_cache_enabled or not self._disk_cache or not source_id:
            return False
        return self._disk_cache.has(source_id, frame)

    def put_source_frame(
        self,
        source_id: str,
        frame: int,
        image_data: np.ndarray,
        mtime: Optional[float] = None,
        write_to_disk: bool = False,
        is_proxy: bool = False,
    ) -> None:
        """Store a frame for a specific source into the shared RAM pool (and optionally Temp Disk)."""
        if self._mode == CacheMode.OFF or not source_id or image_data is None:
            return

        byte_size = image_data.nbytes if isinstance(image_data, np.ndarray) else 0
        if byte_size == 0:
            return

        # In Region mode, do not cache frames outside [in_frame, out_frame] unless it's playhead
        if self._mode == CacheMode.REGION and self._in_frame is not None and self._out_frame is not None:
            if not (self._in_frame <= frame <= self._out_frame) and frame != self._current_playhead:
                return

        key = (source_id, frame)
        with self._lock:
            if key in self._frames:
                self._used_bytes -= self._frame_bytes[key]

            self._frames[key] = image_data
            self._frame_bytes[key] = byte_size
            self._frame_mtimes[key] = float(mtime) if mtime is not None else time.time()
            if is_proxy:
                self._proxy_keys.add(key)
            else:
                self._proxy_keys.discard(key)
            self._used_bytes += byte_size

            self._refresh_auto_budget_locked()
            self._evict_if_needed(self._current_playhead)

        if not is_proxy and write_to_disk and self._disk_cache_enabled and self._disk_cache:
            threading.Thread(
                target=self._disk_cache.put,
                args=(source_id, frame, image_data),
                daemon=True,
            ).start()

        self._emit_status()

    def put_source_frame_disk_only(self, source_id: str, frame: int, image_data: np.ndarray) -> bool:
        """Store frame directly into Temp Disk cache without consuming RAM pool."""
        if not self._disk_cache_enabled or not self._disk_cache or not source_id or image_data is None:
            return False
        res = self._disk_cache.put(source_id, frame, image_data)
        self._emit_status()
        return res

    # --------------------------------------------------------------------------
    # Tier 2: Multi-Channel Layer Cache Access
    # --------------------------------------------------------------------------
    def get_layer_frame(self, source_id: str, frame: int, layer: str) -> Optional[np.ndarray]:
        """Retrieve a cached multi-channel layer frame."""
        if not source_id or not layer:
            return None
        with self._lock:
            return self._layer_cache.get((source_id, frame, layer))

    def has_layer_frame(self, source_id: str, frame: int, layer: str) -> bool:
        """Check if a specific multi-channel layer frame is in Tier-2 RAM cache."""
        if not source_id or not layer:
            return False
        with self._lock:
            return (source_id, frame, layer) in self._layer_cache

    def put_layer_frame(self, source_id: str, frame: int, layer: str, image_data: np.ndarray) -> None:
        """Store a multi-channel layer frame into Tier-2 RAM cache."""
        if self._mode == CacheMode.OFF or not source_id or not layer or image_data is None:
            return

        byte_size = image_data.nbytes if isinstance(image_data, np.ndarray) else 0
        if byte_size == 0:
            return

        key = (source_id, frame, layer)
        with self._lock:
            if key in self._layer_cache:
                self._layer_used_bytes -= self._layer_bytes[key]

            self._layer_cache[key] = image_data
            self._layer_bytes[key] = byte_size
            self._layer_used_bytes += byte_size

            # Evict from layer cache if exceeded
            while self._layer_used_bytes > self._max_layer_memory_bytes and self._layer_cache:
                oldest_k = next(iter(self._layer_cache))
                b = self._layer_bytes.pop(oldest_k, 0)
                self._layer_cache.pop(oldest_k, None)
                self._layer_used_bytes -= b

    def clear_layer_cache(self) -> None:
        """Clear Tier-2 multi-channel layer cache."""
        with self._lock:
            self._layer_cache.clear()
            self._layer_bytes.clear()
            self._layer_used_bytes = 0

    def clear_source(self, source_id: str) -> None:
        """Clear cached frames for a specific source from RAM and Temp Disk."""
        if not source_id:
            return
        with self._lock:
            keys_to_remove = [k for k in self._frames.keys() if isinstance(k, tuple) and k[0] == source_id]
            for k in keys_to_remove:
                self._used_bytes -= self._frame_bytes.pop(k, 0)
                self._frames.pop(k, None)
                self._frame_mtimes.pop(k, None)
                self._proxy_keys.discard(k)

            layer_keys = [k for k in self._layer_cache.keys() if k[0] == source_id]
            for k in layer_keys:
                self._layer_used_bytes -= self._layer_bytes.pop(k, 0)
                self._layer_cache.pop(k, None)

        if self._disk_cache:
            self._disk_cache.clear_source(source_id)
        self._emit_status()

    def clear_source_frame(self, source_id: str, frame: int) -> None:
        """Clear a specific frame from cache for a given source."""
        key = (source_id, frame)
        with self._lock:
            if key in self._frames:
                self._used_bytes -= self._frame_bytes.pop(key, 0)
                self._frames.pop(key, None)
                self._frame_mtimes.pop(key, None)
                self._proxy_keys.discard(key)
        if self._disk_cache:
            self._disk_cache.delete(source_id, frame)
        self._emit_status()

    def get_source_frame_mtime(self, source_id: str, frame: int) -> Optional[float]:
        """Return the stored file modification timestamp for a cached source frame."""
        key = (source_id, frame)
        with self._lock:
            return self._frame_mtimes.get(key)

    def get_source_cached_frames(self, source_id: str, allow_proxy: bool = True) -> Set[int]:
        """Return the set of RAM-cached frame numbers for a specific source."""
        if not source_id:
            return set()
        with self._lock:
            return {
                k[1] for k in self._frames.keys()
                if isinstance(k, tuple) and k[0] == source_id and (allow_proxy or k not in self._proxy_keys)
            }

    def get_source_disk_cached_frames(self, source_id: str) -> Set[int]:
        """Return the set of Temp-disk-cached frame numbers for a specific source."""
        if not self._disk_cache_enabled or not self._disk_cache or not source_id:
            return set()
        return self._disk_cache.get_cached_frames(source_id)

    def get_source_disk_cached_count(self, source_id: str) -> int:
        """Return the count of Temp-disk-cached frames for a specific source."""
        if not self._disk_cache_enabled or not self._disk_cache or not source_id:
            return 0
        return self._disk_cache.get_source_cached_count(source_id)

    def get_all_source_cached_frames(self, source_id: str) -> Set[int]:
        """Return union of RAM and disk cached frame numbers for a specific source."""
        return self.get_source_cached_frames(source_id) | self.get_source_disk_cached_frames(source_id)

    def get_disk_cached_frames(self) -> Set[int]:
        """Return cached frame numbers on temp disk for the active source context."""
        if not self._disk_cache_enabled or not self._disk_cache:
            return set()
        return self._disk_cache.get_cached_frames(self._active_source_id)

    # --------------------------------------------------------------------------
    # Timeline & Compatibility Cache Access
    # --------------------------------------------------------------------------
    def get_frame(self, frame: int, allow_proxy: bool = True) -> Optional[Any]:
        """Retrieve a frame if present in RAM cache, else return None."""
        with self._lock:
            if self._active_source_id:
                key = (self._active_source_id, frame)
                if not (not allow_proxy and key in self._proxy_keys):
                    res = self._frames.get(key)
                    if res is not None:
                        return res
            if not (not allow_proxy and frame in self._proxy_keys):
                return self._frames.get(frame)
            return None

    def has_frame(self, frame: int, allow_proxy: bool = True) -> bool:
        """Check if frame exists in cache."""
        with self._lock:
            if self._active_source_id:
                key = (self._active_source_id, frame)
                if key in self._frames:
                    return allow_proxy or (key not in self._proxy_keys)
            if frame in self._frames:
                return allow_proxy or (frame not in self._proxy_keys)
            return False

    def put_frame(self, frame: int, image_data: Union[np.ndarray, tuple], is_proxy: bool = False) -> None:
        """Store a frame array or (primary, reference) tuple into the cache."""
        if self._mode == CacheMode.OFF:
            return

        if isinstance(image_data, np.ndarray):
            byte_size = image_data.nbytes
        elif isinstance(image_data, tuple):
            byte_size = sum(x.nbytes for x in image_data if isinstance(x, np.ndarray))
        else:
            return

        # In Region mode, do not cache frames outside [in_frame, out_frame] unless it's playhead
        if self._mode == CacheMode.REGION and self._in_frame is not None and self._out_frame is not None:
            if not (self._in_frame <= frame <= self._out_frame) and frame != self._current_playhead:
                return

        key = (self._active_source_id, frame) if self._active_source_id else frame
        with self._lock:
            if key in self._frames:
                self._used_bytes -= self._frame_bytes[key]

            self._frames[key] = image_data
            self._frame_bytes[key] = byte_size
            if is_proxy:
                self._proxy_keys.add(key)
            else:
                self._proxy_keys.discard(key)
            self._used_bytes += byte_size

            self._refresh_auto_budget_locked()
            self._evict_if_needed(self._current_playhead)

        self._emit_status()

    def clear(self) -> None:
        """Clear all cached frames from memory and temp disk."""
        with self._lock:
            self._frames.clear()
            self._frame_bytes.clear()
            self._frame_mtimes.clear()
            self._proxy_keys.clear()
            self._used_bytes = 0
            self._layer_cache.clear()
            self._layer_bytes.clear()
            self._layer_used_bytes = 0
        if self._disk_cache:
            self._disk_cache.clear()
        self._emit_status()

    def update_playhead(self, playhead: int) -> None:
        """Inform the cache of the current playhead position to prioritize prefetch/retention."""
        self._current_playhead = playhead

    def get_cached_frames(self, allow_proxy: bool = True) -> Set[int]:
        """Return cached frame numbers for the active source context or flat keys."""
        with self._lock:
            out = set()
            for k in self._frames.keys():
                if not allow_proxy and k in self._proxy_keys:
                    continue
                if isinstance(k, tuple):
                    if not self._active_source_id or k[0] == self._active_source_id:
                        out.add(k[1])
                elif isinstance(k, int):
                    out.add(k)
            return out

    def get_prefetch_targets(
        self,
        start_frame: int,
        end_frame: int,
        in_frame: Optional[int] = None,
        out_frame: Optional[int] = None,
    ) -> Tuple[int, int]:
        """
        Get the desired prefetch window [window_start, window_end] clamped to sequence bounds.
        Returns (0, -1) when caching is OFF.
        """
        if self._mode == CacheMode.OFF:
            return 0, -1

        inf = in_frame if in_frame is not None else (self._in_frame if self._in_frame is not None else start_frame)
        outf = out_frame if out_frame is not None else (self._out_frame if self._out_frame is not None else end_frame)

        if self._mode == CacheMode.REGION:
            # Cache In and Out region only
            return max(start_frame, inf), min(end_frame, outf)

        if self._mode == CacheMode.ALL:
            return start_frame, end_frame

        # CacheMode.PLAY_AHEAD (Lookahead around current playhead)
        window_start = max(start_frame, self._current_playhead - self._lookbehind)
        window_end = min(end_frame, self._current_playhead + self._lookahead)
        return window_start, window_end

    def get_memory_stats(self) -> Tuple[float, float, float]:
        """Return (used_mb, max_mb, percent_used)."""
        with self._lock:
            used_mb = self._used_bytes / (1024.0 * 1024.0)
            max_mb = self._max_memory_bytes / (1024.0 * 1024.0)
            pct = (self._used_bytes / self._max_memory_bytes) * 100.0 if self._max_memory_bytes > 0 else 0.0
            return used_mb, max_mb, pct

    # --------------------------------------------------------------------------
    # Internal Eviction
    # --------------------------------------------------------------------------
    def _evict_if_needed(self, playhead: int) -> None:
        """Must be called while self._lock is acquired."""
        if self._used_bytes <= self._max_memory_bytes:
            return

        def _get_frame_num(key: Any) -> int:
            if isinstance(key, tuple):
                return key[1]
            return int(key)

        def _is_active_source(key: Any) -> bool:
            if isinstance(key, tuple) and self._active_source_id:
                return key[0] == self._active_source_id
            return True

        def _eviction_priority(k: Any) -> tuple:
            f = _get_frame_num(k)
            # 1. Out of region frames evicted first in REGION mode
            in_region = 1
            if self._mode == CacheMode.REGION and self._in_frame is not None and self._out_frame is not None:
                in_region = 0 if (self._in_frame <= f <= self._out_frame) else 1

            # 2. Inactive source frames evicted before active source
            is_active = 0 if _is_active_source(k) else 1

            # 3. Directional distance using lookback ratio and lookahead windows:
            # Frames beyond lookbehind window are evicted first.
            # Frames beyond lookahead window are evicted second.
            # Frames inside lookbehind window are protected until lookahead needs RAM.
            # Frames inside lookahead window are prioritized for playback.
            diff = f - playhead
            if diff < -self._lookbehind:
                dist_score = 100000 + abs(diff)  # Beyond lookbehind: highest eviction priority
            elif diff > self._lookahead:
                dist_score = 50000 + diff         # Beyond lookahead: next to evict
            elif diff < 0:
                dist_score = 10000 + abs(diff)    # Inside lookbehind window: evict oldest lookbehind first
            else:
                dist_score = diff                 # Inside lookahead window: evict furthest ahead first

            return (in_region, is_active, dist_score)

        candidates = sorted(
            self._frames.keys(),
            key=_eviction_priority,
            reverse=True,
        )

        for k in candidates:
            if self._used_bytes <= self._max_memory_bytes:
                break
            # Never evict pinned frames (e.g. actively rendered frame or working set)
            if k in self._pinned_keys:
                continue

            frame_num = _get_frame_num(k)
            # Do not evict the exact current playhead frame of active source if possible
            if frame_num == playhead and _is_active_source(k) and len(self._frames) > 1:
                continue

            # Spill to temp drive disk cache if enabled (never spill downsampled proxies)
            is_proxy = k in self._proxy_keys
            if not is_proxy and self._disk_cache_enabled and self._disk_cache is not None and isinstance(k, tuple):
                sid = k[0]
                frm = k[1]
                frame_data = self._frames.get(k)
                if frame_data is not None and not self._disk_cache.has(sid, frm):
                    try:
                        self._disk_cache.put(sid, frm, frame_data)
                    except Exception:
                        pass

            b = self._frame_bytes.pop(k, 0)
            self._frames.pop(k, None)
            self._frame_mtimes.pop(k, None)
            self._proxy_keys.discard(k)
            self._used_bytes -= b

    def _emit_status(self, force: bool = False) -> None:
        now = time.monotonic()
        if not force and (now - self._last_emit_ts) < 0.04:
            return
        self._last_emit_ts = now

        used_mb, max_mb, _ = self.get_memory_stats()
        self.cache_changed.emit()
        self.memory_usage_changed.emit(used_mb, max_mb)
        if self._disk_cache:
            d_used, d_max, _ = self._disk_cache.get_stats()
            self.disk_usage_changed.emit(d_used, d_max)
