"""
Multi-Tile Layout Compositing Engine.
Assembles multi-stream review grids with configurable scale percentage (100%, 50%, 25%, 10%)
to minimize memory footprint, PCIe transfer overhead, and GPU fillrate.
Preserves source color range (normalized float32 [0.0..1.0]) and aspect ratio with letterboxing.
"""
from __future__ import annotations

import math
from typing import Dict, List, Optional, Tuple, Callable
import numpy as np

from openreview.models.session_graph import LayoutView, SourceItem


def normalize_to_rgba_float32(frame: np.ndarray) -> np.ndarray:
    """Normalize input array to RGBA float32 in range [0.0, 1.0]."""
    if frame is None:
        return frame

    # 1. Normalize data type to float32 [0.0, 1.0]
    if frame.dtype == np.uint8:
        norm = frame.astype(np.float32) / 255.0
    elif frame.dtype == np.uint16:
        norm = frame.astype(np.float32) / 65535.0
    elif np.issubdtype(frame.dtype, np.floating):
        norm = frame.astype(np.float32)
    else:
        norm = frame.astype(np.float32)

    # 2. Normalize channel dimension to RGBA (4 channels)
    if norm.ndim == 2:
        # Grayscale (H, W) -> RGBA
        h, w = norm.shape
        rgba = np.ones((h, w, 4), dtype=np.float32)
        rgba[:, :, 0] = norm
        rgba[:, :, 1] = norm
        rgba[:, :, 2] = norm
        return rgba
    elif norm.ndim == 3:
        h, w, c = norm.shape
        if c == 1:
            rgba = np.ones((h, w, 4), dtype=np.float32)
            rgba[:, :, 0] = norm[:, :, 0]
            rgba[:, :, 1] = norm[:, :, 0]
            rgba[:, :, 2] = norm[:, :, 0]
            return rgba
        elif c == 3:
            rgba = np.ones((h, w, 4), dtype=np.float32)
            rgba[:, :, :3] = norm
            return rgba
        elif c == 4:
            return norm
        elif c > 4:
            return norm[:, :, :4]

    return norm


def resize_frame_fast(frame: np.ndarray, target_w: int, target_h: int) -> np.ndarray:
    """Fast nearest/bilinear subsampling for layout tiles."""
    h, w = frame.shape[:2]
    if h == target_h and w == target_w:
        return frame

    if target_h <= 0 or target_w <= 0:
        c = frame.shape[2] if frame.ndim > 2 else 4
        return np.zeros((max(1, target_h), max(1, target_w), c), dtype=frame.dtype)

    # Fast row and column decimation / stride indexing
    row_indices = np.linspace(0, h - 1, target_h).astype(np.int32)
    col_indices = np.linspace(0, w - 1, target_w).astype(np.int32)

    return frame[row_indices[:, None], col_indices]


def conform_frame_to_format(
    frame: np.ndarray,
    target_w: int,
    target_h: int,
) -> np.ndarray:
    """
    Fit frame into (target_w, target_h) preserving source aspect ratio with centered
    letterboxing/pillarboxing. If frame already matches target_w and target_h, returns frame unchanged.
    """
    if frame is None or target_w <= 0 or target_h <= 0:
        return frame

    src_h, src_w = frame.shape[:2]
    if src_w == target_w and src_h == target_h:
        return frame

    fit_scale = min(float(target_w) / float(src_w), float(target_h) / float(src_h))
    scaled_w = max(1, int(round(src_w * fit_scale)))
    scaled_h = max(1, int(round(src_h * fit_scale)))

    resized = resize_frame_fast(frame, scaled_w, scaled_h)

    if scaled_w == target_w and scaled_h == target_h:
        return resized

    channels = frame.shape[2] if frame.ndim > 2 else 4
    canvas = np.zeros((target_h, target_w, channels), dtype=frame.dtype)
    if channels == 4:
        canvas[:, :, 3] = 1.0 if np.issubdtype(frame.dtype, np.floating) else 255

    pad_x = (target_w - scaled_w) // 2
    pad_y = (target_h - scaled_h) // 2
    canvas[pad_y : pad_y + scaled_h, pad_x : pad_x + scaled_w] = resized
    return canvas


def composite_layout_grid(
    layout: LayoutView,
    sources_map: Dict[str, SourceItem],
    frame_fetcher: Callable[[SourceItem], Optional[np.ndarray]],
    base_width: Optional[int] = None,
    base_height: Optional[int] = None,
) -> np.ndarray:
    """
    Composite multiple media source frames into a unified multi-tile layout grid.
    Honors layout.scale_pct (100%, 50%, 25%, 10%) for high-efficiency review.
    Preserves source aspect ratio with centered letterboxing/pillarboxing and
    normalizes all tiles into standard float32 [0.0..1.0] RGBA format.
    """
    rows, cols = layout.get_grid_dimensions()
    scale = max(0.1, float(layout.scale_pct) / 100.0)

    # Determine base dimensions dynamically from sources if not provided
    calc_base_w = base_width if (base_width and base_width > 0) else None
    calc_base_h = base_height if (base_height and base_height > 0) else None

    if calc_base_w is None or calc_base_h is None:
        max_w = 0
        max_h = 0
        for sid in layout.source_ids:
            s = sources_map.get(sid)
            if s and s.width > 0 and s.height > 0:
                max_w = max(max_w, s.width)
                max_h = max(max_h, s.height)
        calc_base_w = max_w if max_w > 0 else 1920
        calc_base_h = max_h if max_h > 0 else 1080

    tile_w = max(64, int(round(calc_base_w * scale)))
    tile_h = max(36, int(round(calc_base_h * scale)))

    total_w = cols * tile_w
    total_h = rows * tile_h

    # Canvas initialized to pure black (0.0) with opaque alpha
    canvas = np.zeros((total_h, total_w, 4), dtype=np.float32)
    canvas[:, :, 3] = 1.0

    for idx, sid in enumerate(layout.source_ids):
        if idx >= rows * cols:
            break
        r = idx // cols
        c = idx % cols

        source = sources_map.get(sid)
        tile_frame = None
        if source:
            tile_frame = frame_fetcher(source)

        y_start = r * tile_h
        y_end = y_start + tile_h
        x_start = c * tile_w
        x_end = x_start + tile_w

        if tile_frame is not None:
            # 1. Normalize tile to RGBA float32 in [0.0, 1.0]
            tile_rgba = normalize_to_rgba_float32(tile_frame)
            src_h, src_w = tile_rgba.shape[:2]

            # 2. Preserve source aspect ratio within tile cell bounds (letterbox/pillarbox)
            if src_w > 0 and src_h > 0:
                fit_scale = min(tile_w / src_w, tile_h / src_h)
                scaled_w = max(1, int(round(src_w * fit_scale)))
                scaled_h = max(1, int(round(src_h * fit_scale)))

                resized = resize_frame_fast(tile_rgba, scaled_w, scaled_h)

                # Center within tile cell
                pad_x = (tile_w - scaled_w) // 2
                pad_y = (tile_h - scaled_h) // 2

                canvas[
                    y_start + pad_y : y_start + pad_y + scaled_h,
                    x_start + pad_x : x_start + pad_x + scaled_w,
                ] = resized
            else:
                resized = resize_frame_fast(tile_rgba, tile_w, tile_h)
                canvas[y_start:y_end, x_start:x_end] = resized
        else:
            # Unused or empty tile: seamless pure black with no borders
            canvas[y_start:y_end, x_start:x_end, :3] = 0.0
            canvas[y_start:y_end, x_start:x_end, 3] = 1.0

    return canvas
