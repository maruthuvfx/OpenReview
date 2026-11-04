"""
Professional Media I/O Engine for OpenReview.
Provides native, high-performance decoding for Visual Effects image formats:
- OpenEXR (.exr): Multi-channel, multi-part 16-bit half & 32-bit float HDR decoding.
- SMPTE DPX (.dpx): Native vectorized NumPy decoding for 8-bit, 10-bit (Method A/B), 12-bit, and 16-bit.
- Standard Formats (.tif, .tiff, .png, .jpg, .jpeg, .hdr, .webp, .bmp, .tga): Pillow & OpenCV acceleration.
"""
from __future__ import annotations

import os
import struct
import logging
from typing import Optional, Tuple, Dict, Any
import numpy as np

from openreview.core.buffer_pool import FrameBufferPool

logger = logging.getLogger(__name__)

# Ensure OpenCV uses OpenEXR if supported
os.environ["OPENCV_IO_ENABLE_OPENEXR"] = "1"

# Multi-Threaded OpenEXR Global Decompression Engine:
# OpenEXR C++ library defaults to 0 (single-threaded).
# Enable parallel scanline/tile decompression across all available CPU cores.
try:
    import OpenEXR
    HAS_OPENEXR = True
    try:
        _exr_threads = max(2, os.cpu_count() or 4)
        OpenEXR.set_global_thread_count(_exr_threads)
    except Exception as _ex:
        logger.debug("Failed to set OpenEXR global thread count: %s", _ex)
except ImportError:
    HAS_OPENEXR = False

def _is_rust_io_enabled() -> bool:
    try:
        from openreview.core.preferences import PreferencesManager
        return getattr(PreferencesManager.instance().prefs, "enable_rust_engine", True)
    except Exception:
        return True



# ==============================================================================
# OpenEXR Reader & Writer
# ==============================================================================

_EXR_CHANNELS_CACHE: Dict[str, Dict[str, Any]] = {}


def get_exr_channels(path: str) -> Dict[str, Any]:
    """
    Inspect OpenEXR channels and group them into logical layers.
    Returns:
    {
        "all_channels": ["R", "G", "B", "A", "diffuse.R", ...],
        "layers": {
            "beauty": ["R", "G", "B", "A"],
            "diffuse": ["diffuse.R", "diffuse.G", "diffuse.B"],
            ...
        },
        "cryptomatte_layers": ["crypto_object", "crypto_material", ...],
        "views": ["left", "right"] (if multi-view EXR)
    }
    """
    if not path or not os.path.exists(path):
        return {"all_channels": [], "layers": {}, "cryptomatte_layers": [], "views": []}

    norm_path = os.path.normpath(os.path.abspath(path))
    if norm_path in _EXR_CHANNELS_CACHE:
        return _EXR_CHANNELS_CACHE[norm_path]

    # 0. Accelerated Rust Engine Inspection (Zero-GIL, high speed)
    if _is_rust_io_enabled():
        try:
            import openreview_engine
            rust_eng = getattr(get_exr_channels, "_rust_engine", None)
            if rust_eng is None:
                rust_eng = openreview_engine.OpenReviewEngine()
                get_exr_channels._rust_engine = rust_eng
            meta = rust_eng.inspect_exr(path)
            if meta and meta.get("all_channels"):
                _EXR_CHANNELS_CACHE[norm_path] = meta
                return meta
        except Exception as _re:
            logger.debug("Rust inspect_exr failed: %s", _re)

    try:
        import OpenEXR
        inp = OpenEXR.InputFile(path)
        header = inp.header()
        channels = list(header.get("channels", {}).keys())
        inp.close()

        layers: Dict[str, list[str]] = {}
        crypto_layers: set[str] = set()
        views: set[str] = set()

        for ch in channels:
            parts = ch.split(".")
            if len(parts) == 1:
                layers.setdefault("beauty", []).append(ch)
            elif len(parts) == 2:
                lyr, c = parts
                if lyr.lower() in ("left", "right", "l", "r"):
                    views.add(lyr.lower())
                    layers.setdefault(lyr, []).append(ch)
                elif "crypto" in lyr.lower():
                    base_crypto = lyr.rstrip("0123456789")
                    crypto_layers.add(base_crypto)
                    layers.setdefault(lyr, []).append(ch)
                else:
                    layers.setdefault(lyr, []).append(ch)
            else:
                # Multi-part naming like layer.view.channel or crypto_object00.R
                first = parts[0]
                if "crypto" in first.lower():
                    base_crypto = first.rstrip("0123456789")
                    crypto_layers.add(base_crypto)
                layers.setdefault(first, []).append(ch)

        # Ensure "beauty" is present if standard R, G, B exists
        if "beauty" not in layers and any(c in channels for c in ["R", "G", "B"]):
            layers["beauty"] = [c for c in ["R", "G", "B", "A"] if c in channels]

        result = {
            "all_channels": channels,
            "layers": layers,
            "cryptomatte_layers": sorted(list(crypto_layers)),
            "views": sorted(list(views)),
        }
        _EXR_CHANNELS_CACHE[norm_path] = result
        return result
    except Exception as e:
        logger.debug("Failed to inspect EXR channels on %s: %s", path, e)
        return {"all_channels": [], "layers": {}, "cryptomatte_layers": [], "views": []}


def read_exr(
    path: str,
    layer: Optional[str] = None,
    as_f16: bool = False,
    mapping: Optional[Any] = None,
) -> Optional[np.ndarray]:
    """
    Read an OpenEXR image file into a Float32 RGBA NumPy array (H, W, 4).
    Supports 16-bit half, 32-bit float, and arbitrary multi-channel layers.
    If layer is specified, extracts channels belonging to that layer.
    """
    if not os.path.exists(path):
        return None

    if mapping is not None:
        from openreview.core.exr_matrix import read_exr_matrix_mapped
        return read_exr_matrix_mapped(path, mapping, as_f16=as_f16)

    # 0. Accelerated Rust Engine: Zero-GIL, parallel multi-threaded Rayon decoder
    if _is_rust_io_enabled():
        try:
            import openreview_engine
            rust_eng = getattr(read_exr, "_rust_engine", None)
            if rust_eng is None:
                rust_eng = openreview_engine.OpenReviewEngine()
                read_exr._rust_engine = rust_eng
            native_frame = rust_eng.read_exr(path, as_f16, layer=layer)
            dtype = np.float16 if as_f16 else np.float32
            shape = (native_frame.height, native_frame.width, 4)
            out = FrameBufferPool.instance().acquire(shape, dtype)
            np.copyto(out, np.frombuffer(native_frame.as_memoryview(), dtype=dtype).reshape(shape))
            return out
        except Exception:
            pass

    # 1. Primary: High-performance OpenEXR C++ binding
    try:
        import OpenEXR
        import Imath

        inp = OpenEXR.InputFile(path)
        header = inp.header()
        dw = header["dataWindow"]
        w = dw.max.x - dw.min.x + 1
        h = dw.max.y - dw.min.y + 1

        if w <= 0 or h <= 0:
            logger.warning("Invalid EXR dataWindow dimensions: %dx%d for %s", w, h, path)
            return None

        channels = header.get("channels", {})
        chan_keys = list(channels.keys())
        chan_lower = {k.lower(): k for k in chan_keys}

        pt_float = Imath.PixelType(Imath.PixelType.FLOAT)
        pt_pixel = Imath.PixelType(Imath.PixelType.HALF) if as_f16 else pt_float
        ch_dtype = np.float16 if as_f16 else np.float32

        # Handle Cryptomatte preview extraction if requested
        if layer and "crypto" in layer.lower():
            # Find matching crypto channels (e.g. crypto_object00.R, G, B, A)
            crypto_prefix = layer
            c_r = _find_channel_name([f"{crypto_prefix}00.r", f"{crypto_prefix}.r"], chan_lower, chan_keys)
            c_g = _find_channel_name([f"{crypto_prefix}00.g", f"{crypto_prefix}.g"], chan_lower, chan_keys)
            c_b = _find_channel_name([f"{crypto_prefix}00.b", f"{crypto_prefix}.b"], chan_lower, chan_keys)
            c_a = _find_channel_name([f"{crypto_prefix}00.a", f"{crypto_prefix}.a"], chan_lower, chan_keys)
            if c_r:
                id_buf = np.frombuffer(inp.channel(c_r, pt_float), dtype=np.float32).reshape((h, w))
                cov_buf = np.frombuffer(inp.channel(c_g, pt_float), dtype=np.float32).reshape((h, w)) if c_g else np.ones((h, w), dtype=np.float32)
                inp.close()
                # Unpack float32 bits into pseudo RGB hash color for Cryptomatte ID visualization
                id_uint32 = id_buf.view(np.uint32)
                r_vis = ((id_uint32 & 0xFF0000) >> 16).astype(np.float32) / 255.0
                g_vis = ((id_uint32 & 0x00FF00) >> 8).astype(np.float32) / 255.0
                b_vis = (id_uint32 & 0x0000FF).astype(np.float32) / 255.0
                # Zero out empty background (ID 0)
                mask = (id_uint32 != 0)
                r_vis = np.where(mask, r_vis, 0.0)
                g_vis = np.where(mask, g_vis, 0.0)
                b_vis = np.where(mask, b_vis, 0.0)
                a_vis = np.clip(cov_buf, 0.0, 1.0)
                return np.ascontiguousarray(np.stack([r_vis, g_vis, b_vis, a_vis], axis=-1), dtype=np.float32)

        # Target channels based on layer
        if layer and layer != "beauty":
            prefix = layer.lower() + "."
            lyr_chans = [k for k in chan_keys if k.lower().startswith(prefix)]
            if lyr_chans:
                # Find R, G, B, A or single channel within this layer
                r_ch = next((k for k in lyr_chans if k.lower().endswith((".r", ".x", ".red"))), None)
                g_ch = next((k for k in lyr_chans if k.lower().endswith((".g", ".y", ".green"))), None)
                b_ch = next((k for k in lyr_chans if k.lower().endswith((".b", ".z", ".blue"))), None)
                a_ch = next((k for k in lyr_chans if k.lower().endswith((".a", ".w", ".alpha"))), None)

                # Depth / single-channel pass (e.g. depth.Z)
                if not (r_ch or g_ch or b_ch) and len(lyr_chans) == 1:
                    single_val = np.frombuffer(inp.channel(lyr_chans[0], pt_float), dtype=np.float32).reshape((h, w))
                    inp.close()
                    # Normalize depth / float range to 0..1 for visualization
                    s_min = float(single_val.min())
                    s_max = float(single_val.max())
                    vis = (single_val - s_min) / max(1e-6, s_max - s_min) if s_max > s_min else single_val
                    return np.ascontiguousarray(np.stack([vis, vis, vis, np.ones_like(vis)], axis=-1), dtype=np.float32)

                r = np.frombuffer(inp.channel(r_ch, pt_float), dtype=np.float32).reshape((h, w)) if r_ch else np.zeros((h, w), dtype=np.float32)
                g = np.frombuffer(inp.channel(g_ch, pt_float), dtype=np.float32).reshape((h, w)) if g_ch else r.copy()
                b = np.frombuffer(inp.channel(b_ch, pt_float), dtype=np.float32).reshape((h, w)) if b_ch else r.copy()
                a = np.frombuffer(inp.channel(a_ch, pt_float), dtype=np.float32).reshape((h, w)) if a_ch else np.ones((h, w), dtype=np.float32)
                inp.close()
                return np.ascontiguousarray(np.stack([r, g, b, a], axis=-1), dtype=np.float32)

        # Default Beauty pass: resolve R, G, B, A, or Y
        r_name = _find_channel_name(["r", "red"], chan_lower, chan_keys)
        g_name = _find_channel_name(["g", "green"], chan_lower, chan_keys)
        b_name = _find_channel_name(["b", "blue"], chan_lower, chan_keys)
        a_name = _find_channel_name(["a", "alpha"], chan_lower, chan_keys)
        y_name = _find_channel_name(["y", "luma", "luminance"], chan_lower, chan_keys)

        # Decode Red/Luma
        if r_name:
            r = np.frombuffer(inp.channel(r_name, pt_pixel), dtype=ch_dtype).reshape((h, w))
        elif y_name:
            r = np.frombuffer(inp.channel(y_name, pt_pixel), dtype=ch_dtype).reshape((h, w))
        elif chan_keys:
            r = np.frombuffer(inp.channel(chan_keys[0], pt_pixel), dtype=ch_dtype).reshape((h, w))
        else:
            r = np.zeros((h, w), dtype=ch_dtype)

        # Decode Green
        if g_name:
            g = np.frombuffer(inp.channel(g_name, pt_pixel), dtype=ch_dtype).reshape((h, w))
        elif y_name:
            g = r.copy()
        elif len(chan_keys) > 1:
            g = np.frombuffer(inp.channel(chan_keys[1], pt_pixel), dtype=ch_dtype).reshape((h, w))
        else:
            g = r.copy()

        # Decode Blue
        if b_name:
            b = np.frombuffer(inp.channel(b_name, pt_pixel), dtype=ch_dtype).reshape((h, w))
        elif y_name:
            b = r.copy()
        elif len(chan_keys) > 2:
            b = np.frombuffer(inp.channel(chan_keys[2], pt_pixel), dtype=ch_dtype).reshape((h, w))
        else:
            b = r.copy()

        # Decode Alpha
        if a_name:
            a = np.frombuffer(inp.channel(a_name, pt_pixel), dtype=ch_dtype).reshape((h, w))
        else:
            a = np.ones((h, w), dtype=ch_dtype)

        inp.close()
        target_dtype = np.float16 if as_f16 else np.float32
        out = FrameBufferPool.instance().acquire((h, w, 4), target_dtype)
        out[:, :, 0] = r
        out[:, :, 1] = g
        out[:, :, 2] = b
        out[:, :, 3] = a
        return out

    except Exception as e:
        logger.debug("OpenEXR native reader failed on %s: %s; trying fallback", path, e)

    # 2. Secondary: OpenCV IMREAD_UNCHANGED
    try:
        import cv2
        mat = cv2.imread(path, cv2.IMREAD_UNCHANGED | cv2.IMREAD_ANYCOLOR | cv2.IMREAD_ANYDEPTH)
        if mat is not None:
            return _standardize_to_rgba_float(mat, as_f16=as_f16)
    except Exception as e:
        logger.debug("OpenCV EXR read failed on %s: %s", path, e)

    logger.warning("Failed to decode OpenEXR file: %s", path)
    return None


def write_exr(path: str, data: np.ndarray) -> bool:
    """Write an image array to a 32-bit Float OpenEXR file."""
    try:
        import OpenEXR
        import Imath

        h, w = data.shape[:2]
        header = OpenEXR.Header(w, h)
        pt = Imath.PixelType(Imath.PixelType.FLOAT)
        header["channels"] = {
            "R": Imath.Channel(pt),
            "G": Imath.Channel(pt),
            "B": Imath.Channel(pt),
            "A": Imath.Channel(pt),
        }

        arr = data.astype(np.float32, copy=False)
        if arr.ndim == 2:
            r = g = b = arr
            a = np.ones_like(arr)
        elif arr.shape[2] == 3:
            r, g, b = arr[:, :, 0], arr[:, :, 1], arr[:, :, 2]
            a = np.ones((h, w), dtype=np.float32)
        else:
            r, g, b, a = arr[:, :, 0], arr[:, :, 1], arr[:, :, 2], arr[:, :, 3]

        out = OpenEXR.OutputFile(path, header)
        out.writePixels({
            "R": np.ascontiguousarray(r).tobytes(),
            "G": np.ascontiguousarray(g).tobytes(),
            "B": np.ascontiguousarray(b).tobytes(),
            "A": np.ascontiguousarray(a).tobytes(),
        })
        out.close()
        return True
    except Exception as e:
        logger.error("Failed to write EXR %s: %s", path, e)
        return False


def _find_channel_name(targets: list[str], chan_lower: dict[str, str], chan_keys: list[str]) -> Optional[str]:
    """Find exact channel matching targets, or ending in .target (e.g. beauty.R)."""
    for t in targets:
        if t in chan_lower:
            return chan_lower[t]
    for k in chan_keys:
        parts = k.split(".")
        if parts[-1].lower() in targets:
            return k
    return None


# ==============================================================================
# SMPTE DPX Reader & Writer (Pure Python + NumPy Vectorized)
# ==============================================================================

def read_dpx(path: str, as_f16: bool = False) -> Optional[np.ndarray]:
    """
    Read an ANSI/SMPTE 268M DPX file into a Float32 RGBA NumPy array.
    Supports:
    - 10-bit RGB (Method A, filled into 32-bit words, Big-Endian and Little-Endian)
    - 10-bit RGB (Method B, packed)
    - 16-bit RGB and RGBA (Big-Endian and Little-Endian)
    - 12-bit RGB
    - 8-bit RGB and RGBA
    """
    if not os.path.exists(path):
        return None

    # 0. Accelerated Rust Engine: Multi-threaded zero-GIL DPX decoder
    if _is_rust_io_enabled():
        try:
            import openreview_engine
            rust_eng = getattr(read_dpx, "_rust_engine", None)
            if rust_eng is None:
                rust_eng = openreview_engine.OpenReviewEngine()
                read_dpx._rust_engine = rust_eng
            native_frame = rust_eng.read_dpx(path, as_f16)
            dtype = np.float16 if as_f16 else np.float32
            shape = (native_frame.height, native_frame.width, 4)
            out = FrameBufferPool.instance().acquire(shape, dtype)
            np.copyto(out, np.frombuffer(native_frame.as_memoryview(), dtype=dtype).reshape(shape))
            return out
        except Exception:
            pass

    try:
        with open(path, "rb") as f:
            header_bytes = f.read(2048)
            if len(header_bytes) < 820:
                logger.warning("DPX file too small for header: %s", path)
                return None

            magic = header_bytes[:4]
            if magic == b"SDPX":
                endian = ">"  # Big-Endian
            elif magic == b"XPDS":
                endian = "<"  # Little-Endian
            else:
                logger.warning("Not a valid DPX magic marker (%r) in %s", magic, path)
                return None

            data_offset = struct.unpack_from(f"{endian}I", header_bytes, 4)[0]
            width = struct.unpack_from(f"{endian}I", header_bytes, 772)[0]
            height = struct.unpack_from(f"{endian}I", header_bytes, 776)[0]

            if width <= 0 or height <= 0:
                logger.warning("Invalid DPX dimensions: %dx%d in %s", width, height, path)
                return None

            # SMPTE 268M Element 0 specification:
            # 780..799: Data Sign & Quantity references (20 bytes)
            # 800: Descriptor (1 byte)
            # 801: Transfer characteristic (1 byte)
            # 802: Colorimetric specification (1 byte)
            # 803: Bit depth (1 byte)
            # 804..805: Packing (2 bytes, 1=Method A, 2=Method B, 0=Packed)
            # 806..807: Encoding (2 bytes, 0=None, 1=RLE)
            # 808..811: Offset to data (4 bytes)
            descriptor = header_bytes[800]
            bit_depth = header_bytes[803]
            packing = struct.unpack_from(f"{endian}H", header_bytes, 804)[0]

            # Fallback for non-standard or legacy DPX writers with 4-byte offset shift
            if bit_depth not in (8, 10, 12, 16):
                descriptor = header_bytes[804]
                bit_depth = header_bytes[807]
                packing = struct.unpack_from(f"{endian}H", header_bytes, 808)[0]

            # Use element 0 data offset if valid, otherwise fallback to generic file data_offset
            elem_data_offset = struct.unpack_from(f"{endian}I", header_bytes, 808)[0]
            if 0 < elem_data_offset < len(header_bytes) * 1000:
                actual_data_offset = elem_data_offset
            else:
                actual_data_offset = data_offset

            # Seek directly to image data
            f.seek(actual_data_offset)
            total_pixels = width * height

            # ------------------------------------------------------------------
            # 10-Bit DPX (Most common VFX master plate format)
            # ------------------------------------------------------------------
            if bit_depth == 10:
                # In 10-bit DPX, 1 pixel (RGB, 30 bits) is packed into one 32-bit word
                num_words = total_pixels
                raw = f.read(num_words * 4)
                words = np.frombuffer(raw, dtype=f"{endian}u4")
                if len(words) < total_pixels:
                    logger.warning("Truncated DPX data in %s (got %d words, expected %d)", path, len(words), total_pixels)
                    return None

                words = words[:total_pixels]

                target_dt = np.float16 if as_f16 else np.float32
                out = FrameBufferPool.instance().acquire((height, width, 4), target_dt)
                flat_out = out.reshape((total_pixels, 4))
                scale = np.float32(1.0 / 1023.0)
                if packing == 2:  # Method B (Least significant bit aligned)
                    np.multiply(words & 0x3FF, scale, out=flat_out[:, 0], casting="unsafe")
                    np.multiply((words >> 10) & 0x3FF, scale, out=flat_out[:, 1], casting="unsafe")
                    np.multiply((words >> 20) & 0x3FF, scale, out=flat_out[:, 2], casting="unsafe")
                else:  # Method A (Standard SMPTE 268M MSB aligned: bits 31..22, 21..12, 11..2)
                    np.multiply((words >> 22) & 0x3FF, scale, out=flat_out[:, 0], casting="unsafe")
                    np.multiply((words >> 12) & 0x3FF, scale, out=flat_out[:, 1], casting="unsafe")
                    np.multiply((words >> 2) & 0x3FF, scale, out=flat_out[:, 2], casting="unsafe")

                flat_out[:, 3] = 1.0
                return out

            # ------------------------------------------------------------------
            # 16-Bit DPX
            # ------------------------------------------------------------------
            elif bit_depth == 16:
                channels = 4 if descriptor == 51 else 3
                num_samples = total_pixels * channels
                raw = f.read(num_samples * 2)
                samples = np.frombuffer(raw, dtype=f"{endian}u2")[:num_samples]
                samples = samples.reshape((height, width, channels)).astype(np.float32) / 65535.0

                if channels == 3:
                    a = np.ones((height, width, 1), dtype=np.float32)
                    rgba = np.concatenate([samples, a], axis=-1)
                else:
                    rgba = samples
                return np.ascontiguousarray(rgba, dtype=np.float32)

            # ------------------------------------------------------------------
            # 12-Bit DPX
            # ------------------------------------------------------------------
            elif bit_depth == 12:
                channels = 4 if descriptor == 51 else 3
                num_samples = total_pixels * channels
                raw = f.read(num_samples * 2)
                samples = np.frombuffer(raw, dtype=f"{endian}u2")[:num_samples]
                samples = (samples >> 4).reshape((height, width, channels)).astype(np.float32) / 4095.0

                if channels == 3:
                    a = np.ones((height, width, 1), dtype=np.float32)
                    rgba = np.concatenate([samples, a], axis=-1)
                else:
                    rgba = samples
                return np.ascontiguousarray(rgba, dtype=np.float32)

            # ------------------------------------------------------------------
            # 8-Bit DPX
            # ------------------------------------------------------------------
            elif bit_depth == 8:
                channels = 4 if descriptor == 51 else 3
                num_samples = total_pixels * channels
                raw = f.read(num_samples)
                samples = np.frombuffer(raw, dtype=np.uint8)[:num_samples]
                samples = samples.reshape((height, width, channels)).astype(np.float32) / 255.0

                if channels == 3:
                    a = np.ones((height, width, 1), dtype=np.float32)
                    rgba = np.concatenate([samples, a], axis=-1)
                else:
                    rgba = samples
                return np.ascontiguousarray(rgba, dtype=np.float32)

            else:
                logger.warning("Unsupported DPX bit depth %d in %s", bit_depth, path)

    except Exception as e:
        logger.exception("Error decoding DPX file %s: %s", path, e)

    # Secondary fallback to OpenCV
    try:
        import cv2
        mat = cv2.imread(path, cv2.IMREAD_UNCHANGED)
        if mat is not None:
            return _standardize_to_rgba_float(mat, as_f16=as_f16)
    except Exception:
        pass

    return None


def write_dpx(path: str, data: np.ndarray, bit_depth: int = 10) -> bool:
    """Write an image array to a standard 10-bit or 16-bit SMPTE 268M DPX file."""
    try:
        h, w = data.shape[:2]
        header = bytearray(2048)
        header[0:4] = b"SDPX"  # Big-Endian
        struct.pack_into(">I", header, 4, 2048)  # Data offset
        struct.pack_into(">I", header, 772, w)   # Width
        struct.pack_into(">I", header, 776, h)   # Height
        header[800] = 50  # RGB Descriptor
        header[801] = 1   # User defined / linear transfer
        header[802] = 2   # Colorimetric
        header[803] = bit_depth
        struct.pack_into(">H", header, 804, 1)   # Method A packing
        struct.pack_into(">I", header, 808, 2048)  # Element 0 data offset

        arr = np.clip(data, 0.0, 1.0)
        r = arr[:, :, 0] if arr.ndim >= 3 else arr
        g = arr[:, :, 1] if arr.ndim >= 3 and arr.shape[2] >= 2 else r
        b = arr[:, :, 2] if arr.ndim >= 3 and arr.shape[2] >= 3 else r

        if bit_depth == 10:
            r_10 = (r * 1023.0).astype(np.uint32)
            g_10 = (g * 1023.0).astype(np.uint32)
            b_10 = (b * 1023.0).astype(np.uint32)
            words = ((r_10 & 0x3FF) << 22) | ((g_10 & 0x3FF) << 12) | ((b_10 & 0x3FF) << 2)
            pixel_bytes = words.astype(">u4").tobytes()
        elif bit_depth == 16:
            r_16 = (r * 65535.0).astype(">u2")
            g_16 = (g * 65535.0).astype(">u2")
            b_16 = (b * 65535.0).astype(">u2")
            rgb_16 = np.stack([r_16, g_16, b_16], axis=-1).astype(">u2")
            pixel_bytes = rgb_16.tobytes()
        else:
            raise ValueError(f"Unsupported write bit depth: {bit_depth}")

        with open(path, "wb") as f:
            f.write(header)
            f.write(pixel_bytes)
        return True
    except Exception as e:
        logger.error("Failed to write DPX %s: %s", path, e)
        return False


# ==============================================================================
# XnView Complete Image Format Extensions Registry (https://www.xnview.com/en/image-formats/)
# ==============================================================================

XNVIEW_IMAGE_EXTENSIONS: set[str] = {
    '.001', '.16', '.256', '.2bp', '.2d', '.3', '.301', '.3200', '.3201', '.32k', '.3fr', '.411',
    '.4bit', '.4bt', '.4mi', '.4pl', '.4pm', '.64c', '.73i', '.82i', '.83i', '.85i', '.86i', '.89i',
    '.92i', '.a', '.a64', '.abr', '.abs', '.acc', '.ace', '.acorn', '.acr', '.adt', '.afdesign', '.afl',
    '.afphoto', '.afx', '.ai', '.aipd', '.alias', '.als', '.ami', '.ani', '.anv', '.apng', '.apx', '.arf',
    '.arn', '.art', '.arw', '.atk', '.att', '.avif', '.avs', '.avw', '.awd', '.b16', '.b2d', '.b3d',
    '.b_w', '.bay', '.bfl', '.bfli', '.bfx', '.bga', '.bie', '.bif', '.big', '.bip', '.bld', '.blend',
    '.blk', '.blp', '.bm', '.bmc', '.bmf', '.bmg', '.bmp', '.bms', '.bmx', '.bn', '.bob', '.bpg',
    '.bpr', '.bpx', '.brk', '.brt', '.bs', '.bsg', '.btn', '.bum', '.bw', '.c4', '.cad', '.cal',
    '.cals', '.cam', '.can', '.car', '.cat', '.cbm', '.ccx', '.cdr', '.cdu', '.ce', '.ce1', '.ce2',
    '.cel', '.cgm', '.ch', '.che', '.cin', '.cip', '.cit', '.clip', '.clo', '.cloe', '.clp', '.cmp',
    '.cmt', '.cmu', '.cmx', '.cp8', '.cpa', '.cpc', '.cpi', '.cpt', '.cr2', '.cr2_originl', '.cr3', '.crd',
    '.crg', '.crw', '.cs1', '.csv', '.ct', '.ctf', '.cur', '.cursor', '.cut', '.cvp', '.cwg', '.dat',
    '.dc3', '.dcm', '.dcr', '.dcx', '.dd', '.dds', '.dib', '.dic', '.dicom', '.dis', '.djv', '.djvu',
    '.dng', '.dol', '.doo', '.dpx', '.drz', '.dsi', '.dta', '.dwg', '.dwt', '.dxf', '.ecc', '.ecw',
    '.ef3', '.efx', '.ei', '.eidi', '.eif', '.emf', '.emz', '.epa', '.epi', '.eps', '.ept', '.erf',
    '.eri', '.esm', '.exr', '.f96', '.fac', '.face', '.fax', '.fbm', '.fcx', '.fff', '.fgs', '.fi',
    '.fif', '.fit', '.fits', '.flc', '.fli', '.flif', '.flp', '.flt', '.fmf', '.fp2', '.fpg', '.fpr',
    '.fpt', '.fpx', '.fre', '.frm', '.fsh', '.fsy', '.ftf', '.fts', '.fun', '.fx3', '.fxd', '.fxm',
    '.fxo', '.fxr', '.fxs', '.g16', '.g3', '.g3n', '.g4', '.gaf', '.gb', '.gbr', '.gcd', '.gem',
    '.geo', '.gg', '.gif', '.giff', '.gig', '.gih', '.gl2', '.gm', '.gm2', '.gm4', '.gmf', '.gn',
    '.gp4', '.grb', '.grey', '.gro', '.gro2', '.gro4', '.gry', '.gun', '.hbm', '.hdp', '.hdr', '.hdri',
    '.hdru', '.hed', '.heic', '.heif', '.hf', '.hg', '.hgl', '.hir', '.hp', '.hp1', '.hp2', '.hpg',
    '.hpgl', '.hpgl2', '.hpi', '.hr', '.hru', '.hrz', '.hta', '.i17', '.i18', '.ia', '.iam', '.ib7',
    '.ibg', '.ica', '.icb', '.icl', '.icn', '.icns', '.ico', '.icon', '.idc', '.if9', '.iff', '.ifl',
    '.ifx', '.iim', '.iimg', '.iiq', '.ilbm', '.im', '.im5', '.ima', '.img', '.imi', '.imt', '.indd',
    '.info', '.insp', '.int', '.inta', '.ioca', '.ipg', '.iph', '.ipl', '.ipn', '.ipt', '.iris', '.ish',
    '.ism', '.iss', '.itg', '.ithmb', '.ivb', '.iw4', '.iwc', '.j', '.j2c', '.j2k', '.j6i', '.jb2',
    '.jbf', '.jbg', '.jbig', '.jbr', '.jfif', '.jif', '.jig', '.jj', '.jls', '.jng', '.jp2', '.jpc',
    '.jpe', '.jpeg', '.jpf', '.jpg', '.jpm', '.jps', '.jpt', '.jpx', '.jtf', '.jxl', '.jxr', '.k25',
    '.kap', '.kdc', '.kfx', '.kla', '.koa', '.kps', '.kqp', '.kra', '.kro', '.l', '.lbm', '.lda',
    '.ldf', '.lff', '.lif', '.lsm', '.lss', '.lvp', '.lwf', '.lwi', '.m8', '.mac', '.macp', '.mag',
    '.map', '.max', '.mbfavs', '.mbfs', '.mbm', '.mdl', '.mef', '.mgr', '.mh', '.mic', '.mif', '.miff',
    '.mil', '.mix', '.mng', '.mod', '.mon', '.mos', '.mph', '.mpnt', '.mpo', '.mrc', '.mrf', '.mrw',
    '.msk', '.msp', '.mtv', '.mtx', '.ncd', '.ncr', '.nct', '.ncy', '.nef', '.nef_originl', '.neo', '.ngg',
    '.nitf', '.nlm', '.nol', '.npm', '.nrw', '.nsf', '.nsif', '.nsr', '.ntf', '.oaz', '.ocp', '.ofx',
    '.oil', '.ols', '.ora', '.orf', '.orf_originl', '.ori', '.otb', '.p2', '.p64', '.p7', '.pac', '.paint',
    '.pam', '.pan', '.pat', '.pax', '.pbm', '.pbt', '.pc1', '.pc2', '.pc3', '.pcc', '.pcd', '.pcl',
    '.pcp', '.pct', '.pcx', '.pd', '.pdb', '.pdd', '.pdf', '.pds', '.pdx', '.pe4', '.pef', '.pfi',
    '.pfm', '.pfr', '.pg', '.pgc', '.pgf', '.pgm', '.ph', '.php', '.pi', '.pi1', '.pi2', '.pi3',
    '.pi4', '.pi5', '.pi6', '.pic', '.picio', '.pict', '.pict2', '.pig', '.pix', '.pixar', '.pld', '.plo',
    '.plt', '.pm', '.pmg', '.pmp', '.png', '.pnm', '.pnt', '.pntg', '.pp4', '.pp5', '.ppm', '.ppma',
    '.ppp', '.pps', '.ppt', '.pr', '.prc', '.prf', '.pri', '.prn', '.ps', '.ps1', '.ps2', '.ps3',
    '.psa', '.psb', '.psd', '.pse', '.psf', '.psg', '.psp', '.pspbrush', '.pspframe', '.pspimage', '.pspmask', '.psptube',
    '.pst', '.ptg', '.pwc', '.pwp', '.pxa', '.pxb', '.pxr', '.pxs', '.pzl', '.pzp', '.q0', '.qdv',
    '.qoi', '.qrt', '.qti', '.qtif', '.qtl', '.rad', '.raf', '.ras', '.rast', '.raw', '.rawraw', '.rdc',
    '.rfa', '.rgb', '.rgba', '.rgbe', '.rgh', '.ric', '.rix', '.rl4', '.rl8', '.rla', '.rlb', '.rlc',
    '.rle', '.rp', '.rpbm', '.rpf', '.rpgm', '.rpm', '.rpnm', '.rppm', '.rs', '.rsb', '.rsrc', '.rw2',
    '.rwl', '.rwz', '.sar', '.sc2', '.sci', '.scr', '.sct', '.scx', '.sd0', '.sd1', '.sd2', '.sdg',
    '.sdt', '.seq', '.sff', '.sfw', '.sgi', '.si', '.sid', '.sif', '.sim', '.sir', '.sj1', '.skb',
    '.skf', '.skn', '.skp', '.smp', '.sna', '.spc', '.sps', '.spu', '.sr', '.sr2', '.srf', '.srw',
    '.ssi', '.ssp', '.sst', '.st4', '.st5', '.st6', '.st7', '.st8', '.sti', '.stm', '.stw', '.stx',
    '.sul', '.sun', '.suniff', '.svg', '.svgz', '.syj', '.syn', '.synu', '.sys', '.t1', '.t2', '.taac',
    '.tap', '.targa', '.tdi', '.tdim', '.tex', '.tg4', '.tga', '.thb', '.tif', '.tiff', '.til', '.tile',
    '.tim', '.tjp', '.tm2', '.tn1', '.tn2', '.tn3', '.tnl', '.tny', '.tpic', '.trp', '.tsk', '.ttf',
    '.tub', '.txc', '.uni', '.upi', '.urt', '.uyvy', '.v', '.vda', '.vff', '.vfx', '.vga', '.vi',
    '.vic', '.vicar', '.vid', '.vif', '.viff', '.vit', '.vob', '.vpb', '.vst', '.vtf', '.wad', '.wal',
    '.wap', '.wb1', '.wbc', '.wbm', '.wbmp', '.wbp', '.wbz', '.wdp', '.webp', '.wep', '.wfx', '.wic',
    '.wlm', '.wmf', '.wmz', '.wpg', '.wrl', '.wsq', '.wzl', '.x', '.x11', '.x3f', '.xar', '.xbm',
    '.xcf', '.xfx', '.xif', '.xim', '.xnf', '.xp0', '.xpm', '.xv', '.xwd', '.xyz', '.xyze', '.ypc',
    '.yuv', '.zbr', '.zmf',
}

IMAGE_EXTENSIONS: set[str] = set(XNVIEW_IMAGE_EXTENSIONS)


def is_image_file(path: str) -> bool:
    """Return True if path has an extension recognized in the XnView image formats list."""
    if not path or not isinstance(path, str):
        return False
    _, ext = os.path.splitext(path.lower())
    return ext in IMAGE_EXTENSIONS


# ==============================================================================
# Specialized TIFF, Targa (TGA), Cineon, PFM/PNM, Radiance HDR, SGI, PIX, SVG/Qt
# ==============================================================================

def read_tiff(path: str, as_f16: bool = False) -> Optional[np.ndarray]:
    """
    Read TIFF (.tif, .tiff) preserving full bit depth (8-bit, 16-bit uint, 32-bit float).
    Prefers OpenCV for direct uncompressed / LZW 16-bit/float buffers, falling back to Pillow.
    """
    if not os.path.exists(path):
        return None

    # 0. Accelerated Rust Engine: Multi-threaded zero-GIL TIFF decoder
    if _is_rust_io_enabled():
        try:
            import openreview_engine
            rust_eng = getattr(read_tiff, "_rust_engine", None)
            if rust_eng is None:
                rust_eng = openreview_engine.OpenReviewEngine()
                read_tiff._rust_engine = rust_eng
            native_frame = rust_eng.read_tiff(path, as_f16)
            dtype = np.float16 if as_f16 else np.float32
            shape = (native_frame.height, native_frame.width, 4)
            out = FrameBufferPool.instance().acquire(shape, dtype)
            np.copyto(out, np.frombuffer(native_frame.as_memoryview(), dtype=dtype).reshape(shape))
            return out
        except Exception:
            pass

    try:
        import cv2
        mat = cv2.imread(path, cv2.IMREAD_UNCHANGED | cv2.IMREAD_ANYCOLOR | cv2.IMREAD_ANYDEPTH)
        if mat is not None:
            return _standardize_to_rgba_float(mat, as_f16=as_f16)
    except Exception as e:
        logger.debug("OpenCV TIFF read failed on %s: %s", path, e)

    try:
        from PIL import Image
        with Image.open(path) as img:
            if img.mode in ("I;16", "I", "F"):
                arr = np.array(img, dtype=np.float32)
                max_val = 65535.0 if img.mode == "I;16" else (float(arr.max()) or 1.0)
                norm = np.clip(arr / max_val, 0.0, 1.0)
                rgba = np.stack([norm, norm, norm, np.ones_like(norm)], axis=-1)
                return np.ascontiguousarray(rgba, dtype=np.float32)
            else:
                img_rgba = img.convert("RGBA")
                arr = np.asarray(img_rgba, dtype=np.float32) / 255.0
                return np.ascontiguousarray(arr, dtype=np.float32)
    except Exception as e:
        logger.debug("Pillow TIFF read failed on %s: %s", path, e)

    return None


def read_tga(path: str) -> Optional[np.ndarray]:
    """
    Read Targa (.tga, .tpic, .vda, .icb, .vst) images supporting 24-bit RGB and 32-bit RGBA formats.
    """
    if not os.path.exists(path):
        return None

    try:
        from PIL import Image
        with Image.open(path) as img:
            img_rgba = img.convert("RGBA")
            arr = np.asarray(img_rgba, dtype=np.float32) / 255.0
            return np.ascontiguousarray(arr, dtype=np.float32)
    except Exception as e:
        logger.debug("Pillow TGA read failed on %s: %s", path, e)

    try:
        import cv2
        mat = cv2.imread(path, cv2.IMREAD_UNCHANGED | cv2.IMREAD_ANYCOLOR | cv2.IMREAD_ANYDEPTH)
        if mat is not None:
            return _standardize_to_rgba_float(mat, as_f16=as_f16)
    except Exception as e:
        logger.debug("OpenCV TGA read failed on %s: %s", path, e)

    return None


def read_cineon(path: str) -> Optional[np.ndarray]:
    """
    Read Kodak Cineon (.cin) 10-bit log image files into Float32 RGBA (H, W, 4).
    Supports big-endian (0x802A5FD7) and little-endian (0xD75F2A80) Cineon headers.
    """
    if not os.path.exists(path):
        return None
    try:
        with open(path, "rb") as f:
            raw = f.read()
        if len(raw) < 1024:
            return None
        magic_be = struct.unpack(">I", raw[:4])[0]
        if magic_be == 0x802A5FD7:
            endian = ">"
        elif magic_be == 0xD75F2A80:
            endian = "<"
        else:
            return None

        offset = struct.unpack(f"{endian}I", raw[4:8])[0]
        # Image orientation / channels header starts at byte 192
        num_channels = struct.unpack("B", raw[193:194])[0] or 3
        # First channel descriptor starts at byte 196 (28 bytes each):
        # byte 0: designator1, byte 1: designator2, byte 2: bit_depth, byte 3: unused,
        # bytes 4..8: pixels_per_line (width), bytes 8..12: lines_per_image (height)
        bit_depth = struct.unpack("B", raw[198:199])[0] or 10
        w = struct.unpack(f"{endian}I", raw[200:204])[0]
        h = struct.unpack(f"{endian}I", raw[204:208])[0]
        if w <= 0 or h <= 0 or w > 32768 or h > 32768:
            return None

        pixel_data = raw[offset:]
        if bit_depth == 10:
            dt = np.dtype(f"{endian}u4")
            words = np.frombuffer(pixel_data, dtype=dt)
            total_samples = w * h * max(3, num_channels)
            words_needed = (total_samples + 2) // 3
            if len(words) < words_needed:
                # Try row-padded 32-bit words
                words_per_row = (w * 3 + 2) // 3
                if len(words) >= words_per_row * h:
                    words = words[: words_per_row * h].reshape((h, words_per_row))
                    s0 = (words >> 22) & 0x3FF
                    s1 = (words >> 12) & 0x3FF
                    s2 = (words >> 2) & 0x3FF
                    interleaved = np.stack([s0, s1, s2], axis=-1).reshape((h, -1))[:, : w * 3]
                    rgb = interleaved.reshape((h, w, 3)).astype(np.float32) / 1023.0
                    alpha = np.ones((h, w, 1), dtype=np.float32)
                    return np.ascontiguousarray(np.concatenate([rgb, alpha], axis=-1), dtype=np.float32)
                return None
            words = words[:words_needed]
            s0 = (words >> 22) & 0x3FF
            s1 = (words >> 12) & 0x3FF
            s2 = (words >> 2) & 0x3FF
            samples = np.empty(words_needed * 3, dtype=np.float32)
            samples[0::3] = s0
            samples[1::3] = s1
            samples[2::3] = s2
            rgb = (samples[: w * h * 3] / 1023.0).reshape((h, w, 3))
            alpha = np.ones((h, w, 1), dtype=np.float32)
            return np.ascontiguousarray(np.concatenate([rgb, alpha], axis=-1), dtype=np.float32)
    except Exception as e:
        logger.debug("Cineon read failed on %s: %s", path, e)
    return None


def read_pfm_pnm(path: str) -> Optional[np.ndarray]:
    """
    Read Portable FloatMap (.pfm: PF/Pf 32-bit float HDR) and Netpbm
    (.pbm, .pgm, .ppm, .pnm, .pam, .rpbm, .rpgm, .rppm, .rpnm, .ppma) into Float32 RGBA.
    """
    if not os.path.exists(path):
        return None
    try:
        with open(path, "rb") as f:
            magic = f.readline().strip()
            if magic in (b"PF", b"Pf"):
                is_color = magic == b"PF"
                line = f.readline()
                while line.startswith(b"#") or not line.strip():
                    line = f.readline()
                dims = line.split()
                if len(dims) < 2:
                    return None
                w, h = int(dims[0]), int(dims[1])
                scale_line = f.readline()
                while scale_line.startswith(b"#") or not scale_line.strip():
                    scale_line = f.readline()
                scale = float(scale_line.strip())
                endian = "<" if scale < 0 else ">"
                channels = 3 if is_color else 1
                data = np.frombuffer(f.read(), dtype=f"{endian}f4")
                if len(data) < w * h * channels:
                    return None
                arr = data[: w * h * channels].reshape((h, w, channels))[::-1]  # PFM is bottom-to-top
                if channels == 1:
                    arr = np.repeat(arr, 3, axis=-1)
                alpha = np.ones((h, w, 1), dtype=np.float32)
                return np.ascontiguousarray(np.concatenate([arr, alpha], axis=-1), dtype=np.float32)
    except Exception as e:
        logger.debug("PFM read failed on %s: %s", path, e)
    return None


def read_rgbe_hdr(path: str) -> Optional[np.ndarray]:
    """
    Read Radiance RGBE (.hdr, .hdri, .rgbe, .xyze) into Float32 RGBA (H, W, 4).
    Tries OpenCV IMREAD_ANYDEPTH first, then falls back to native RGBE parser.
    """
    if not os.path.exists(path):
        return None
    try:
        import cv2
        mat = cv2.imread(path, cv2.IMREAD_UNCHANGED | cv2.IMREAD_ANYCOLOR | cv2.IMREAD_ANYDEPTH)
        if mat is not None:
            return _standardize_to_rgba_float(mat, as_f16=as_f16)
    except Exception:
        pass

    try:
        with open(path, "rb") as f:
            raw = f.read()
        # Parse header until empty line followed by resolution "-Y <h> +X <w>"
        idx = 0
        n = len(raw)
        while idx < n:
            nl = raw.find(b"\n", idx)
            if nl == -1:
                return None
            line = raw[idx:nl].strip()
            idx = nl + 1
            if not line:
                break
        res_nl = raw.find(b"\n", idx)
        if res_nl == -1:
            return None
        res_line = raw[idx:res_nl].decode("ascii", "ignore").strip().split()
        idx = res_nl + 1
        if len(res_line) < 4:
            return None
        h = int(res_line[1])
        w = int(res_line[3])
        if w <= 0 or h <= 0:
            return None
        payload = raw[idx:]
        if len(payload) == w * h * 4:
            rgbe = np.frombuffer(payload, dtype=np.uint8).reshape((h, w, 4))
        else:
            # Decode adaptive RLE scanlines
            rgbe = np.zeros((h, w, 4), dtype=np.uint8)
            p = 0
            plen = len(payload)
            for y in range(h):
                if p + 4 > plen:
                    break
                if payload[p] == 2 and payload[p + 1] == 2 and not (payload[p + 2] & 0x80):
                    p += 4
                    for ch in range(4):
                        x = 0
                        while x < w and p < plen:
                            code = payload[p]
                            p += 1
                            if code > 128:
                                count = code - 128
                                if p < plen:
                                    rgbe[y, x : x + count, ch] = payload[p]
                                    p += 1
                                x += count
                            else:
                                count = code
                                rgbe[y, x : x + count, ch] = np.frombuffer(
                                    payload[p : p + count], dtype=np.uint8
                                )
                                p += count
                                x += count
        exp = rgbe[:, :, 3].astype(np.float32)
        mask = exp > 0
        scale = np.zeros((h, w), dtype=np.float32)
        scale[mask] = np.ldexp(1.0, (exp[mask] - (128 + 8)).astype(np.int32))
        rgb = (rgbe[:, :, :3].astype(np.float32) + 0.5) * scale[:, :, None]
        alpha = np.ones((h, w, 1), dtype=np.float32)
        return np.ascontiguousarray(np.concatenate([rgb, alpha], axis=-1), dtype=np.float32)
    except Exception as e:
        logger.debug("Native RGBE read failed on %s: %s", path, e)
    return None


def read_sgi(path: str) -> Optional[np.ndarray]:
    """
    Read Silicon Graphics IRIS (.sgi, .rgb, .rgba, .bw, .int, .inta, .iris) images
    supporting 8-bit/16-bit uncompressed and RLE-compressed files.
    """
    if not os.path.exists(path):
        return None
    try:
        from PIL import Image
        with Image.open(path) as img:
            img_rgba = img.convert("RGBA")
            arr = np.asarray(img_rgba, dtype=np.float32) / 255.0
            return np.ascontiguousarray(arr, dtype=np.float32)
    except Exception:
        pass
    try:
        with open(path, "rb") as f:
            raw = f.read()
        if len(raw) < 512:
            return None
        magic, storage, bpc, dim, w, h, zsize = struct.unpack(">HBBHHHH", raw[:12])
        if magic != 474 or w <= 0 or h <= 0 or zsize <= 0:
            return None
        if storage == 0:
            dtype = ">u2" if bpc == 2 else "u1"
            max_v = 65535.0 if bpc == 2 else 255.0
            data = np.frombuffer(raw[512:], dtype=dtype)
            if len(data) < w * h * zsize:
                return None
            planes = data[: w * h * zsize].reshape((zsize, h, w))[:, ::-1, :]  # bottom-to-top
            planes_f = planes.astype(np.float32) / max_v
            if zsize == 1:
                rgb = np.repeat(planes_f[0, :, :, None], 3, axis=-1)
                alpha = np.ones((h, w, 1), dtype=np.float32)
                return np.ascontiguousarray(np.concatenate([rgb, alpha], axis=-1), dtype=np.float32)
            elif zsize == 3:
                rgb = np.transpose(planes_f[:3], (1, 2, 0))
                alpha = np.ones((h, w, 1), dtype=np.float32)
                return np.ascontiguousarray(np.concatenate([rgb, alpha], axis=-1), dtype=np.float32)
            elif zsize >= 4:
                return np.ascontiguousarray(np.transpose(planes_f[:4], (1, 2, 0)), dtype=np.float32)
    except Exception as e:
        logger.debug("SGI read failed on %s: %s", path, e)
    return None


def read_alias_pix(path: str) -> Optional[np.ndarray]:
    """Read Alias/Wavefront PIX (.pix, .als, .alias) 24-bit RLE image into Float32 RGBA."""
    if not os.path.exists(path):
        return None
    try:
        with open(path, "rb") as f:
            raw = f.read()
        if len(raw) < 10:
            return None
        w, h, _, _, bpp = struct.unpack(">HHHHH", raw[:10])
        if w <= 0 or h <= 0 or w > 16384 or h > 16384 or bpp not in (24, 8):
            return None
        total_px = w * h
        p = 10
        n = len(raw)
        if bpp == 24:
            out = np.zeros((total_px, 3), dtype=np.uint8)
            idx = 0
            while idx < total_px and p + 4 <= n:
                count = raw[p]
                b, g, r = raw[p + 1], raw[p + 2], raw[p + 3]
                p += 4
                if count == 0:
                    break
                end = min(total_px, idx + count)
                out[idx:end, 0] = r
                out[idx:end, 1] = g
                out[idx:end, 2] = b
                idx = end
            if idx == total_px:
                rgb = out.reshape((h, w, 3)).astype(np.float32) / 255.0
                alpha = np.ones((h, w, 1), dtype=np.float32)
                return np.ascontiguousarray(np.concatenate([rgb, alpha], axis=-1), dtype=np.float32)
    except Exception as e:
        logger.debug("Alias PIX read failed on %s: %s", path, e)
    return None


def read_qt_or_svg(path: str) -> Optional[np.ndarray]:
    """Read SVG/SVGZ vector images or Qt QImageReader-supported formats into Float32 RGBA."""
    if not os.path.exists(path):
        return None
    ext = os.path.splitext(path)[1].lower()
    if ext in (".svg", ".svgz"):
        try:
            from PySide6.QtSvg import QSvgRenderer
            from PySide6.QtGui import QImage, QPainter
            from PySide6.QtCore import Qt

            renderer = QSvgRenderer(path)
            if renderer.isValid():
                sz = renderer.defaultSize()
                w = max(1, sz.width() if sz.width() > 0 else 512)
                h = max(1, sz.height() if sz.height() > 0 else 512)
                qimg = QImage(w, h, QImage.Format.Format_RGBA8888)
                qimg.fill(Qt.GlobalColor.transparent)
                painter = QPainter(qimg)
                renderer.render(painter)
                painter.end()
                ptr = qimg.constBits()
                arr = np.frombuffer(ptr, dtype=np.uint8).reshape((h, qimg.bytesPerLine() // 4, 4))[:, :w, :]
                return np.ascontiguousarray(arr.astype(np.float32) / 255.0, dtype=np.float32)
        except Exception as e:
            logger.debug("Qt SVG read failed on %s: %s", path, e)

    try:
        from PySide6.QtGui import QImageReader, QImage

        reader = QImageReader(path)
        reader.setAutoTransform(True)
        qimg = reader.read()
        if not qimg.isNull():
            qimg = qimg.convertToFormat(QImage.Format.Format_RGBA8888)
            w, h = qimg.width(), qimg.height()
            ptr = qimg.constBits()
            arr = np.frombuffer(ptr, dtype=np.uint8).reshape((h, qimg.bytesPerLine() // 4, 4))[:, :w, :]
            return np.ascontiguousarray(arr.astype(np.float32) / 255.0, dtype=np.float32)
    except Exception as e:
        logger.debug("Qt QImageReader failed on %s: %s", path, e)
    return None


def read_ffmpeg_single_frame(path: str) -> Optional[np.ndarray]:
    """Fallback single-frame decoder using OpenCV's FFmpeg backend for specialty image formats."""
    if not os.path.exists(path):
        return None
    try:
        import cv2
        cap = cv2.VideoCapture(path, getattr(cv2, "CAP_FFMPEG", cv2.CAP_ANY))
        if cap.isOpened():
            ret, bgr = cap.read()
            cap.release()
            if ret and bgr is not None:
                return _standardize_to_rgba_float(bgr)
        else:
            cap.release()
    except Exception as e:
        logger.debug("FFmpeg single-frame read failed on %s: %s", path, e)
    return None


# ==============================================================================
# General Image Loader Dispatcher
# ==============================================================================

def read_image_file(
    path: str,
    layer: Optional[str] = None,
    as_f16: bool = False,
    mapping: Optional[Any] = None,
) -> Optional[np.ndarray]:
    """
    Unified, rock-solid image reader for OpenReview.
    Automatically identifies file type and decodes into a normalized Float32 RGBA array (H, W, 4).
    Supports all XnView image formats via specialized VFX decoders, Pillow, OpenCV, Qt, and FFmpeg.
    """
    if not os.path.exists(path):
        return None

    ext = os.path.splitext(path)[1].lower()

    # 1. Specialized EXR handler
    if ext == ".exr":
        result = read_exr(path, layer=layer, as_f16=as_f16, mapping=mapping)
        if result is not None:
            return result

    # 2. Specialized DPX handler
    if ext == ".dpx":
        result = read_dpx(path, as_f16=as_f16)
        if result is not None:
            return result

    # 3. Specialized Kodak Cineon handler
    if ext == ".cin":
        result = read_cineon(path)
        if result is not None:
            return result

    # 4. Specialized TIFF / Camera RAW-TIFF handler
    if ext in (".tif", ".tiff", ".fax", ".g3", ".g4", ".dng"):
        result = read_tiff(path, as_f16=as_f16)
        if result is not None:
            return result

    # 5. Specialized TGA handler
    if ext in (".tga", ".tpic", ".targa", ".vda", ".icb", ".vst"):
        result = read_tga(path)
        if result is not None:
            return result

    # 6. Specialized PFM / Netpbm float handler
    if ext in (".pfm", ".pbm", ".pgm", ".ppm", ".pnm", ".pam", ".rpbm", ".rpgm", ".rppm", ".rpnm", ".ppma"):
        result = read_pfm_pnm(path)
        if result is not None:
            return result

    # 7. Specialized Radiance RGBE HDR handler
    if ext in (".hdr", ".hdri", ".hdru", ".rgbe", ".xyze", ".rad"):
        result = read_rgbe_hdr(path)
        if result is not None:
            return result

    # 8. Specialized SGI IRIS RGB handler
    if ext in (".sgi", ".rgb", ".rgba", ".bw", ".int", ".inta", ".iris"):
        result = read_sgi(path)
        if result is not None:
            return result

    # 9. Specialized Alias PIX handler
    if ext in (".pix", ".als", ".alias"):
        result = read_alias_pix(path)
        if result is not None:
            return result

    # 10. Vector SVG / SVGZ via Qt
    if ext in (".svg", ".svgz"):
        result = read_qt_or_svg(path)
        if result is not None:
            return result

    # 11. Standard Pillow Loader (PNG, JPEG, WEBP, BMP, PSD, DDS, PCX, IFF, FITS, ICO, etc.)
    try:
        from PIL import Image
        with Image.open(path) as img:
            if img.mode in ("I;16", "I", "F"):
                arr = np.array(img, dtype=np.float32)
                max_val = 65535.0 if img.mode == "I;16" else (float(arr.max()) or 1.0)
                norm = np.clip(arr / max_val, 0.0, 1.0)
                rgba = np.stack([norm, norm, norm, np.ones_like(norm)], axis=-1)
                return np.ascontiguousarray(rgba, dtype=np.float32)
            img = img.convert("RGBA")
            arr = np.asarray(img, dtype=np.float32) / 255.0
            return np.ascontiguousarray(arr, dtype=np.float32)
    except Exception as e:
        logger.debug("Pillow open failed for %s: %s", path, e)

    # 12. Fallback to OpenCV
    try:
        import cv2
        mat = cv2.imread(path, cv2.IMREAD_UNCHANGED | cv2.IMREAD_ANYCOLOR | cv2.IMREAD_ANYDEPTH)
        if mat is not None:
            return _standardize_to_rgba_float(mat, as_f16=as_f16)
    except Exception as e:
        logger.debug("OpenCV open failed for %s: %s", path, e)

    # 13. Fallback to Qt QImageReader / SVG
    result = read_qt_or_svg(path)
    if result is not None:
        return result

    # 14. Fallback to FFmpeg single-frame decoder
    result = read_ffmpeg_single_frame(path)
    if result is not None:
        return result

    logger.warning("Could not read image file: %s", path)
    return None


def _standardize_to_rgba_float(mat: np.ndarray, as_f16: bool = False) -> np.ndarray:
    """Normalize arbitrary OpenCV matrix into a (H, W, 4) Float32/Float16 array via FrameBufferPool."""
    import cv2

    # Scale to 0.0 .. 1.0 based on data type
    if mat.dtype == np.uint8:
        scale = 255.0
    elif mat.dtype == np.uint16:
        scale = 65535.0
    else:
        scale = 1.0  # Already float32/float16

    mat_float = mat.astype(np.float32, copy=False) if scale == 1.0 else (mat.astype(np.float32) / scale)

    if mat_float.ndim == 2:
        res = cv2.cvtColor(mat_float, cv2.COLOR_GRAY2RGBA)
    elif mat_float.ndim == 3:
        channels = mat_float.shape[2]
        if channels == 3:
            res = cv2.cvtColor(mat_float, cv2.COLOR_BGR2RGBA)
        elif channels == 4:
            res = cv2.cvtColor(mat_float, cv2.COLOR_BGRA2RGBA)
        elif channels > 4:
            res = cv2.cvtColor(mat_float[:, :, :4], cv2.COLOR_BGRA2RGBA)
        else:
            res = mat_float
    else:
        res = mat_float

    target_dt = np.float16 if as_f16 else np.float32
    h, w = res.shape[:2]
    out = FrameBufferPool.instance().acquire((h, w, 4), target_dt)
    np.copyto(out, res.astype(target_dt, copy=False))
    return out
