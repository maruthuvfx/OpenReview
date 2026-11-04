"""
Multi-Channel EXR Contact Sheet Engine.
Assembles all AOVs / passes of a multi-channel EXR into an adaptive review grid
with crisp bottom-right layer name badges, canvas hit-testing for single-click /
double-click pass soloing, auto-depth normalization, and full EXR header metadata extraction.
"""
from __future__ import annotations

import logging
import math
import os
from typing import Dict, List, Optional, Tuple

import numpy as np
from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QImage, QPainter, QPen

from openreview.core.layout_compositor import normalize_to_rgba_float32, resize_frame_fast

logger = logging.getLogger(__name__)


def get_contact_sheet_grid(num_passes: int) -> Tuple[int, int]:
    """
    Calculate optimal (rows, cols) grid for the given number of passes,
    balancing the canvas aspect ratio toward ~16:9 widescreen layout.
    """
    if num_passes <= 1:
        return 1, 1
    if num_passes == 2:
        return 1, 2
    if num_passes <= 4:
        return 2, 2
    if num_passes <= 6:
        return 2, 3
    if num_passes <= 9:
        return 3, 3
    if num_passes <= 12:
        return 3, 4
    if num_passes <= 16:
        return 4, 4
    if num_passes <= 20:
        return 4, 5
    if num_passes <= 25:
        return 5, 5
    if num_passes <= 30:
        return 5, 6
    if num_passes <= 36:
        return 6, 6

    # General fallback for large pass counts
    cols = max(1, int(math.ceil(math.sqrt(num_passes * 1.5))))
    rows = max(1, int(math.ceil(num_passes / cols)))
    return rows, cols


def auto_normalize_depth_pass(frame: np.ndarray) -> np.ndarray:
    """
    Normalize arbitrary float Z/Depth buffer values into visible [0.0..1.0] luminance range.
    Handles high dynamic range distances (e.g., 5.0 to 2000.0) and inverts depth if near values
    should appear brighter.
    """
    if frame is None:
        return frame

    arr = np.copy(frame)
    # Extract scalar channel for depth analysis
    if arr.ndim == 3:
        depth_slice = arr[:, :, 0]
    else:
        depth_slice = arr

    finite_mask = np.isfinite(depth_slice)
    if not np.any(finite_mask):
        return normalize_to_rgba_float32(arr)

    # Exclude infinity / negative / zero backgrounds
    valid_vals = depth_slice[finite_mask]
    pos_vals = valid_vals[valid_vals > 0]
    if len(pos_vals) == 0:
        min_val = float(np.min(valid_vals))
        max_val = float(np.max(valid_vals))
    else:
        min_val = float(np.min(pos_vals))
        max_val = float(np.max(pos_vals))

    if max_val > min_val:
        normalized = np.zeros_like(depth_slice, dtype=np.float32)
        normalized[finite_mask] = (depth_slice[finite_mask] - min_val) / (max_val - min_val)
        normalized = np.clip(normalized, 0.0, 1.0)
    else:
        normalized = np.clip(depth_slice, 0.0, 1.0)

    h, w = normalized.shape[:2]
    out_rgba = np.ones((h, w, 4), dtype=np.float32)
    out_rgba[:, :, 0] = normalized
    out_rgba[:, :, 1] = normalized
    out_rgba[:, :, 2] = normalized
    return out_rgba


def draw_contact_sheet_badges(
    canvas_rgba_float: np.ndarray,
    tile_rects: Dict[str, Tuple[int, int, int, int]],
) -> np.ndarray:
    """
    Draw sleek bottom-right layer name badges with rounded dark pill background
    and amber border directly onto the float32 canvas.
    """
    h, w = canvas_rgba_float.shape[:2]
    if h <= 0 or w <= 0 or not tile_rects:
        return canvas_rgba_float

    from PySide6.QtGui import QGuiApplication
    if not QGuiApplication.instance():
        return canvas_rgba_float

    # Convert float32 [0..1] to uint8 RGBA QImage for high-quality antialiased text rendering
    uint8_data = np.ascontiguousarray(np.clip(canvas_rgba_float * 255.0, 0, 255).astype(np.uint8))
    qimg = QImage(uint8_data.data, w, h, w * 4, QImage.Format.Format_RGBA8888)

    painter = QPainter(qimg)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)

    base_font_size = max(9, min(14, int(min(w, h) / 75)))
    font = QFont("Segoe UI", base_font_size, QFont.Weight.Bold)
    font.setStyleHint(QFont.StyleHint.SansSerif)
    painter.setFont(font)
    fm = QFontMetrics(font)

    for layer_name, (tx, ty, tw, th) in tile_rects.items():
        if tw <= 20 or th <= 16:
            continue

        clean_text = str(layer_name)
        text_w = fm.horizontalAdvance(clean_text)
        text_h = fm.height()

        pad_x = 8
        pad_y = 4
        pill_w = text_w + pad_x * 2
        pill_h = text_h + pad_y * 2

        # Anchor to bottom-right of tile with 10px margin
        margin = max(6, int(min(tw, th) * 0.03))
        pill_x = tx + tw - pill_w - margin
        pill_y = ty + th - pill_h - margin

        # Clamp pill inside tile bounds
        pill_x = max(tx + 2, pill_x)
        pill_y = max(ty + 2, pill_y)

        pill_rect = QRect(int(pill_x), int(pill_y), int(pill_w), int(pill_h))

        # Semi-transparent dark pill background
        painter.setBrush(QColor(16, 16, 16, 215))
        painter.setPen(QPen(QColor(229, 168, 35, 210), 1.2))
        painter.drawRoundedRect(pill_rect, 4.0, 4.0)

        # High-contrast white/amber text
        painter.setPen(QColor(250, 250, 250))
        painter.drawText(pill_rect, Qt.AlignmentFlag.AlignCenter, clean_text)

    painter.end()

    # Convert back to float32 [0.0..1.0] RGBA array
    ptr = qimg.constBits()
    rendered_uint8 = np.frombuffer(ptr, dtype=np.uint8).reshape((h, w, 4))
    return (rendered_uint8.astype(np.float32) / 255.0).copy()


def build_exr_contact_sheet(
    layer_frames: Dict[str, np.ndarray],
    base_w: int = 1920,
    base_h: int = 1080,
    auto_depth: bool = True,
    draw_labels: bool = True,
) -> Tuple[np.ndarray, Dict[str, Tuple[int, int, int, int]]]:
    """
    Composite all available passes of a multi-channel EXR into a unified contact sheet grid.
    Returns:
        (canvas_rgba_float32, tile_map)
        tile_map: Dict[layer_name, (x_start, y_start, width, height)] in canvas pixel space.
    """
    if not layer_frames:
        empty = np.zeros((base_h, base_w, 4), dtype=np.float32)
        empty[:, :, 3] = 1.0
        return empty, {}

    layers = list(layer_frames.keys())
    num_passes = len(layers)
    rows, cols = get_contact_sheet_grid(num_passes)

    # Detect native aspect ratio from available frames
    src_sample_w = base_w
    src_sample_h = base_h
    for f in layer_frames.values():
        if f is not None and f.ndim >= 2:
            src_sample_h, src_sample_w = f.shape[:2]
            break

    aspect = src_sample_w / max(1, src_sample_h)

    # Calculate optimal resolution per tile cell preserving native source aspect ratio
    tile_w = max(160, int(round(base_w / cols)))
    tile_h = max(90, int(round(tile_w / aspect)))

    total_w = cols * tile_w
    total_h = rows * tile_h

    # Pure black canvas with opaque alpha (zero grey borders)
    canvas = np.zeros((total_h, total_w, 4), dtype=np.float32)
    canvas[:, :, 3] = 1.0

    tile_map: Dict[str, Tuple[int, int, int, int]] = {}

    for idx, lyr in enumerate(layers):
        r = idx // cols
        c = idx % cols

        cell_x = c * tile_w
        cell_y = r * tile_h

        frame = layer_frames.get(lyr)
        if frame is None:
            # Unused or empty tile: seamless pure black with no borders
            canvas[cell_y : cell_y + tile_h, cell_x : cell_x + tile_w, :3] = 0.0
            canvas[cell_y : cell_y + tile_h, cell_x : cell_x + tile_w, 3] = 1.0
            continue

        # Optional Depth / Z auto-normalization
        is_depth = any(s in lyr.lower() for s in ("depth", ".z", "z_depth", "depth_camera"))
        if auto_depth and is_depth:
            frame_rgba = auto_normalize_depth_pass(frame)
        else:
            frame_rgba = normalize_to_rgba_float32(frame)

        src_h, src_w = frame_rgba.shape[:2]
        if src_w > 0 and src_h > 0:
            fit_scale = min(tile_w / src_w, tile_h / src_h)
            scaled_w = max(1, int(round(src_w * fit_scale)))
            scaled_h = max(1, int(round(src_h * fit_scale)))

            resized = resize_frame_fast(frame_rgba, scaled_w, scaled_h)

            # Center within tile cell bounds
            pad_x = (tile_w - scaled_w) // 2
            pad_y = (tile_h - scaled_h) // 2

            t_x = cell_x + pad_x
            t_y = cell_y + pad_y

            canvas[t_y : t_y + scaled_h, t_x : t_x + scaled_w] = resized
            tile_map[lyr] = (t_x, t_y, scaled_w, scaled_h)
        else:
            resized = resize_frame_fast(frame_rgba, tile_w, tile_h)
            canvas[cell_y : cell_y + tile_h, cell_x : cell_x + tile_w] = resized
            tile_map[lyr] = (cell_x, cell_y, tile_w, tile_h)

    # Draw bottom-right badges on all active tiles
    if draw_labels:
        canvas = draw_contact_sheet_badges(canvas, tile_map)

    return canvas, tile_map


def hit_test_contact_sheet(
    canvas_x: int,
    canvas_y: int,
    tile_map: Dict[str, Tuple[int, int, int, int]],
) -> Optional[str]:
    """
    Hit-test image coordinate (canvas_x, canvas_y) against the active contact sheet tile map.
    Returns the layer name if the coordinate hits a tile, or None if in empty margins.
    """
    for layer_name, (tx, ty, tw, th) in tile_map.items():
        if tx <= canvas_x < tx + tw and ty <= canvas_y < ty + th:
            return layer_name
    return None


def extract_exr_detailed_metadata(path: str) -> dict:
    """
    Extract comprehensive header attributes from OpenEXR image file.
    Includes dataWindow, displayWindow, compression, pixelAspectRatio, channels, and custom tags.
    """
    result = {
        "path": path,
        "filename": os.path.basename(path),
        "data_window": None,
        "display_window": None,
        "pixel_aspect": 1.0,
        "compression": "Unknown",
        "line_order": "IncreasingY",
        "channels": [],
        "layers": [],
        "cryptomatte_layers": [],
        "attributes": {},
    }

    if not os.path.exists(path):
        return result

    try:
        import OpenEXR
        import Imath

        inp = OpenEXR.InputFile(path)
        header = inp.header()
        inp.close()

        # Windows
        if "dataWindow" in header:
            dw = header["dataWindow"]
            result["data_window"] = (dw.min.x, dw.min.y, dw.max.x, dw.max.y)
        if "displayWindow" in header:
            disp = header["displayWindow"]
            result["display_window"] = (disp.min.x, disp.min.y, disp.max.x, disp.max.y)

        # Standard header keys
        if "pixelAspectRatio" in header:
            result["pixel_aspect"] = float(header["pixelAspectRatio"])
        if "compression" in header:
            result["compression"] = str(header["compression"])
        if "lineOrder" in header:
            result["line_order"] = str(header["lineOrder"])

        # Channels & Layers
        channels_dict = header.get("channels", {})
        parsed_channels = []
        layer_names = set()
        crypto_layers = set()

        for c_name, c_chan in sorted(channels_dict.items()):
            chan_type = str(getattr(c_chan, "type", "FLOAT"))
            x_samp = getattr(c_chan, "xSampling", 1)
            y_samp = getattr(c_chan, "ySampling", 1)
            parsed_channels.append({
                "name": c_name,
                "type": chan_type,
                "sampling": f"{x_samp}x{y_samp}",
            })

            if "." in c_name:
                parts = c_name.split(".")
                lyr = parts[0]
                layer_names.add(lyr)
                if "crypto" in lyr.lower():
                    crypto_layers.add(lyr)
            else:
                layer_names.add("beauty")

        result["channels"] = parsed_channels
        result["layers"] = sorted(list(layer_names))
        result["cryptomatte_layers"] = sorted(list(crypto_layers))

        # Custom header attributes
        reserved_keys = {
            "channels", "dataWindow", "displayWindow", "pixelAspectRatio",
            "compression", "lineOrder", "screenWindowCenter", "screenWindowWidth",
        }
        for k, v in header.items():
            if k not in reserved_keys:
                result["attributes"][str(k)] = str(v)

    except Exception as e:
        logger.debug("Failed detailed EXR header extraction on %s: %s", path, e)

    return result
