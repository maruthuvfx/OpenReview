"""
High-Performance Zero-Copy Frame Buffer Pool for OpenReview.
Recycles pre-allocated contiguous NumPy frame buffers to eliminate OS page faults,
virtual memory fragmentation, and Python Garbage Collection (GC) pauses during 60+ FPS playback.
"""
from __future__ import annotations

import threading
from typing import Dict, Tuple, Optional, Any
import numpy as np


class FrameBufferPool:
    """
    Thread-safe frame buffer recycling pool.
    Reuses allocated C-contiguous NumPy frame buffers across reader threads,
    cache eviction, and double-buffered OpenGL PBO streaming pipelines.
    """

    _instance: Optional[FrameBufferPool] = None
    _singleton_lock = threading.Lock()

    @classmethod
    def instance(cls) -> FrameBufferPool:
        """Access global thread-safe singleton instance."""
        if cls._instance is None:
            with cls._singleton_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        """Reset singleton (used in test teardown)."""
        with cls._singleton_lock:
            if cls._instance is not None:
                cls._instance.clear()
                cls._instance = None

    def __init__(self, max_buffers_per_key: int = 16, enabled: bool = True):
        self._enabled = enabled
        self._max_buffers = max(2, max_buffers_per_key)
        self._lock = threading.Lock()
        # Key: (shape, np.dtype) -> List[np.ndarray]
        self._pools: Dict[Tuple[Tuple[int, ...], np.dtype], list[np.ndarray]] = {}
        # Metrics
        self._hits = 0
        self._misses = 0
        self._releases = 0

    @property
    def enabled(self) -> bool:
        return self._enabled

    @enabled.setter
    def enabled(self, value: bool) -> None:
        with self._lock:
            self._enabled = bool(value)
            if not self._enabled:
                self._pools.clear()

    @property
    def max_buffers_per_key(self) -> int:
        return self._max_buffers

    @max_buffers_per_key.setter
    def max_buffers_per_key(self, value: int) -> None:
        with self._lock:
            self._max_buffers = max(2, int(value))
            for k in list(self._pools.keys()):
                if len(self._pools[k]) > self._max_buffers:
                    self._pools[k] = self._pools[k][:self._max_buffers]

    def acquire(self, shape: Tuple[int, ...], dtype: Any = np.float16) -> np.ndarray:
        """
        Acquire a contiguous frame buffer matching shape and dtype.
        Returns a recycled buffer if available, or allocates a new one.
        """
        dt = np.dtype(dtype)
        key = (shape, dt)

        if self._enabled:
            with self._lock:
                pool = self._pools.get(key)
                if pool and len(pool) > 0:
                    self._hits += 1
                    return pool.pop()
                self._misses += 1

        # Allocate new C-contiguous array
        return np.empty(shape, dtype=dt)

    def release(self, buffer: Optional[np.ndarray]) -> bool:
        """
        Release a frame buffer back to the recycling pool.
        Returns True if recycled, False if dropped or rejected.
        """
        if not self._enabled or buffer is None:
            return False

        if not isinstance(buffer, np.ndarray) or buffer.size == 0:
            return False

        # Only recycle C-contiguous writeable arrays
        if not buffer.flags.c_contiguous or not buffer.flags.writeable:
            return False

        key = (buffer.shape, buffer.dtype)

        with self._lock:
            pool = self._pools.setdefault(key, [])
            if len(pool) < self._max_buffers:
                # Store buffer for reuse
                pool.append(buffer)
                self._releases += 1
                return True

        return False

    def clear(self) -> None:
        """Evict all pooled buffers and reclaim RAM."""
        with self._lock:
            self._pools.clear()
            self._hits = 0
            self._misses = 0
            self._releases = 0

    def get_stats(self) -> Dict[str, Any]:
        """Return live pooling statistics, pool size, and hit ratio."""
        with self._lock:
            total_buffers = sum(len(p) for p in self._pools.values())
            total_bytes = sum(sum(b.nbytes for b in p) for p in self._pools.values())
            total_reqs = self._hits + self._misses
            hit_ratio = (self._hits / total_reqs * 100.0) if total_reqs > 0 else 0.0

            return {
                "enabled": self._enabled,
                "total_buffers": total_buffers,
                "total_bytes": total_bytes,
                "total_mb": total_bytes / (1024.0 * 1024.0),
                "hits": self._hits,
                "misses": self._misses,
                "releases": self._releases,
                "hit_ratio_pct": hit_ratio,
                "active_keys": len(self._pools),
            }
