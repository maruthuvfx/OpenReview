"""
OpenColorIO v2 Display Transform 3D LUT Pre-Baking & Caching Engine.
Pre-bakes OCIO v2 display/view color pipelines into hardware-accelerated 3D textures
with HDR log2 / rational shaper functions for real-time 60-120+ FPS playback.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

try:
    import PyOpenColorIO as ocio
    HAS_OCIO = True
except ImportError:
    try:
        import opencolorio as ocio
        HAS_OCIO = True
    except ImportError:
        HAS_OCIO = False
        ocio = None

logger = logging.getLogger(__name__)

# Shaper function modes
SHAPER_LINEAR = 0
SHAPER_LOG2 = 1
SHAPER_RATIONAL = 2


@dataclass
class OcioLutBakeResult:
    """Represents a pre-baked 3D LUT ready for OpenGL 3D texture upload."""
    data_3d: np.ndarray             # Shape: (size, size, size, 4) in float32
    size: int = 32                  # Lattice edge length (e.g. 32, 48, 64)
    shaper_mode: int = SHAPER_LOG2   # 0: Linear [0, 1], 1: Log2 20-stop [-10, 10] EV, 2: Rational x/(1+x)
    min_ev: float = -10.0           # Minimum EV for log2 shaper (e.g. 2^-10 = 0.000976)
    max_ev: float = 10.0            # Maximum EV for log2 shaper (e.g. 2^+10 = 1024.0)
    config_name: str = ""
    src_space: str = "ACEScg"
    display: str = "sRGB - Display"
    view: str = "ACES 1.0 - SDR Video"
    cache_key: str = ""
    bake_time_ms: float = 0.0

    @property
    def lut_size(self) -> int:
        return self.size


class OcioLutCache:
    """Thread-safe LRU cache storing pre-baked 3D LUT results."""

    _instance: Optional[OcioLutCache] = None
    _lock = threading.Lock()

    def __init__(self, max_entries: int = 32, capacity: Optional[int] = None):
        if capacity is not None:
            max_entries = capacity
        self._cache: Dict[str, OcioLutBakeResult] = {}
        self._access_order: List[str] = []
        self._max_entries = max_entries
        self._cache_lock = threading.Lock()

    @property
    def size(self) -> int:
        with self._cache_lock:
            return len(self._cache)

    def __len__(self) -> int:
        return self.size

    @classmethod
    def instance(cls) -> OcioLutCache:
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def get(self, cache_key: str) -> Optional[OcioLutBakeResult]:
        with self._cache_lock:
            if cache_key in self._cache:
                if cache_key in self._access_order:
                    self._access_order.remove(cache_key)
                self._access_order.append(cache_key)
                return self._cache[cache_key]
        return None

    def put(self, cache_key: str, result: OcioLutBakeResult) -> None:
        with self._cache_lock:
            if cache_key in self._cache:
                self._cache[cache_key] = result
                if cache_key in self._access_order:
                    self._access_order.remove(cache_key)
                self._access_order.append(cache_key)
                return

            if len(self._cache) >= self._max_entries:
                oldest = self._access_order.pop(0)
                self._cache.pop(oldest, None)

            self._cache[cache_key] = result
            self._access_order.append(cache_key)

    def clear(self) -> None:
        with self._cache_lock:
            self._cache.clear()
            self._access_order.clear()


def make_cache_key(
    config_id: str,
    src_space: str,
    display: str,
    view: str,
    size: int = 32,
    shaper_mode: int = SHAPER_LOG2,
) -> str:
    return f"{config_id}::{src_space}::{display}::{view}::{size}::{shaper_mode}"


def bake_display_view_lut(
    config: Any = None,
    src_space: str = "ACEScg",
    display: str = "sRGB - Display",
    view: str = "ACES 1.0 - SDR Video",
    size: int = 32,
    lut_size: Optional[int] = None,
    shaper_mode: int = SHAPER_LOG2,
    min_ev: float = -10.0,
    max_ev: float = 10.0,
    config_name: str = "builtin",
) -> OcioLutBakeResult:
    """
    Bake an OCIO v2 Display/View color transform into a 3D float32 lattice.
    Evaluation is accelerated via PyOpenColorIO CPU processor or analytical fallback.
    """
    if lut_size is not None:
        size = lut_size
    cache_key = make_cache_key(config_name, src_space, display, view, size, shaper_mode)
    cached = OcioLutCache.instance().get(cache_key)
    if cached is not None:
        return cached

    t0 = time.perf_counter()

    # 1. Generate normalized 3D lattice coordinates [0, 1]
    # In OpenGL 3D textures: s = R, t = G, r = B.
    # Meshgrid indexing 'ij' gives shape (size, size, size)
    coords = np.linspace(0.0, 1.0, size, dtype=np.float32)
    grid_b, grid_g, grid_r = np.meshgrid(coords, coords, coords, indexing="ij")

    # 2. Invert shaper function to compute scene-linear input values for lattice points
    if shaper_mode == SHAPER_LOG2:
        # Log2 20-stop HDR Shaper [-10, +10] EV
        # norm = (log2(x) - min_ev) / (max_ev - min_ev)
        # => x = 2.0 ** (norm * (max_ev - min_ev) + min_ev)
        lin_r = 2.0 ** (grid_r * (max_ev - min_ev) + min_ev)
        lin_g = 2.0 ** (grid_g * (max_ev - min_ev) + min_ev)
        lin_b = 2.0 ** (grid_b * (max_ev - min_ev) + min_ev)
        # Cleanly anchor exact zero
        lin_r[grid_r == 0.0] = 0.0
        lin_g[grid_g == 0.0] = 0.0
        lin_b[grid_b == 0.0] = 0.0
    elif shaper_mode == SHAPER_RATIONAL:
        # Rational Shaper s(x) = x / (1 + x) => x = s / (1 - s)
        eps = 1e-6
        s_r = np.clip(grid_r, 0.0, 1.0 - eps)
        s_g = np.clip(grid_g, 0.0, 1.0 - eps)
        s_b = np.clip(grid_b, 0.0, 1.0 - eps)
        lin_r = s_r / (1.0 - s_r)
        lin_g = s_g / (1.0 - s_g)
        lin_b = s_b / (1.0 - s_b)
    else:
        # Standard Linear [0, 1]
        lin_r = grid_r.copy()
        lin_g = grid_g.copy()
        lin_b = grid_b.copy()

    lattice = np.stack([lin_r, lin_g, lin_b], axis=-1)

    # 3. Evaluate Transform
    evaluated = False
    if HAS_OCIO and config is not None:
        try:
            transform = ocio.DisplayViewTransform(
                src=src_space,
                display=display,
                view=view,
            )
            processor = config.getProcessor(transform)
            cpu = processor.getDefaultCPUProcessor()
            cpu.applyRGB(lattice)
            evaluated = True
        except Exception as e:
            logger.debug("OCIO bake via DisplayViewTransform failed: %s; falling back to ColorSpaceTransform", e)
            try:
                # Try ColorSpaceTransform fallback
                cs_trans = ocio.ColorSpaceTransform(src=src_space, dst=view)
                processor = config.getProcessor(cs_trans)
                cpu = processor.getDefaultCPUProcessor()
                cpu.applyRGB(lattice)
                evaluated = True
            except Exception as e2:
                logger.debug("OCIO bake ColorSpaceTransform fallback failed: %s", e2)

    if not evaluated:
        # Analytical ACES filmic fallback tone mapping
        lattice = _analytical_aces_bake(lattice)

    # 4. Pack into RGBA float32 array (OpenGL Target3D expects 4-channel RGBA)
    rgba = np.ones((size, size, size, 4), dtype=np.float32)
    rgba[..., :3] = lattice

    bake_time_ms = (time.perf_counter() - t0) * 1000.0

    result = OcioLutBakeResult(
        data_3d=np.ascontiguousarray(rgba, dtype=np.float32),
        size=size,
        shaper_mode=shaper_mode,
        min_ev=min_ev,
        max_ev=max_ev,
        config_name=config_name,
        src_space=src_space,
        display=display,
        view=view,
        cache_key=cache_key,
        bake_time_ms=bake_time_ms,
    )

    OcioLutCache.instance().put(cache_key, result)
    return result


def _analytical_aces_bake(lattice: np.ndarray) -> np.ndarray:
    """Narkowicz ACES filmic tone reproduction curve for analytical fallback."""
    x = np.maximum(0.0, lattice)
    a = 2.51
    b = 0.03
    c = 2.43
    d = 0.59
    e = 0.14
    fitted = np.clip((x * (a * x + b)) / (x * (c * x + d) + e), 0.0, 1.0)
    # Convert linear to sRGB OETF display
    higher = 1.055 * np.power(np.maximum(fitted, 0.0), 1.0 / 2.4) - 0.055
    lower = 12.92 * fitted
    return np.where(fitted > 0.0031308, higher, lower).astype(np.float32)
