"""
Multi-Channel EXR Layer Matrix & AOV Engine.
Provides high-performance parsing of OpenEXR channel hierarchies, semantic
classification of render passes (Beauty, Diffuse, Specular, Normals, Position,
Depth/Z, Motion Vectors, Cryptomatte), arbitrary channel-to-RGBA matrix routing,
and zero-copy frame buffer pool extraction.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from openreview.core.buffer_pool import FrameBufferPool

logger = logging.getLogger(__name__)


class SemanticPassType(IntEnum):
    COLOR = 0       # Standard RGB/RGBA color pass (Beauty, Diffuse, Specular, etc.)
    NORMAL = 1      # Surface Normals (X, Y, Z or R, G, B [-1..1])
    POSITION = 2    # World / Camera Position (X, Y, Z)
    DEPTH = 3       # Z-Depth / Distance
    VECTOR = 4      # 2D/3D Motion Vectors (U, V or X, Y)
    CRYPTOMATTE = 5 # Cryptomatte ID & Coverage manifests
    ALPHA = 6       # Matte / Isolation masks
    GENERIC = 7     # Custom or uncategorized channels


@dataclass
class EXRChannelInfo:
    """Metadata describing an individual channel within an OpenEXR file."""
    name: str                       # e.g., 'diffuse.R', 'N.X', 'depth.Z'
    pixel_type: str = "HALF"        # 'HALF', 'FLOAT', 'UINT'
    layer: str = "beauty"           # e.g., 'diffuse', 'N', 'depth', 'beauty'
    channel_role: str = "R"         # 'R', 'G', 'B', 'A', 'X', 'Y', 'Z', 'U', 'V'
    semantic_type: SemanticPassType = SemanticPassType.COLOR


@dataclass
class EXRMatrixMapping:
    """
    Arbitrary routing definition mapping source EXR channels to RGBA viewport channels.
    """
    r_channel: Optional[str] = None
    g_channel: Optional[str] = None
    b_channel: Optional[str] = None
    a_channel: Optional[str] = None
    semantic_type: SemanticPassType = SemanticPassType.COLOR
    auto_normalize_depth: bool = False
    remap_normals: bool = False     # Remap [-1, 1] -> [0, 1] for visual inspection
    crypto_layer: Optional[str] = None

    def key(self) -> str:
        """Deterministic cache key representing this mapping."""
        return (
            f"{self.r_channel}:{self.g_channel}:{self.b_channel}:{self.a_channel}:"
            f"{int(self.semantic_type)}:{int(self.auto_normalize_depth)}:"
            f"{int(self.remap_normals)}:{self.crypto_layer or ''}"
        )


def classify_channel(channel_name: str, pixel_type: str = "HALF") -> EXRChannelInfo:
    """
    Parse channel name and classify into layer, role, and semantic type.
    Examples:
      'diffuse.R' -> layer='diffuse', role='R', type=COLOR
      'N.X'       -> layer='N', role='X', type=NORMAL
      'Z'         -> layer='Z', role='Z', type=DEPTH
      'motion.u'  -> layer='motion', role='U', type=VECTOR
      'crypto_object00.R' -> layer='crypto_object', role='R', type=CRYPTOMATTE
    """
    ch_raw = channel_name.strip()
    parts = ch_raw.split(".")
    ch_lower = ch_raw.lower()

    if len(parts) > 1:
        layer = ".".join(parts[:-1])
        role = parts[-1].upper()
    else:
        layer = "beauty"
        role = ch_raw.upper()

    # Determine semantic type
    layer_lower = layer.lower()
    if "crypto" in layer_lower:
        sem_type = SemanticPassType.CRYPTOMATTE
        # Strip index suffix e.g. crypto_object00 -> crypto_object
        base_layer = layer
        while base_layer and base_layer[-1].isdigit():
            base_layer = base_layer[:-1]
        if base_layer:
            layer = base_layer
    elif layer_lower in ("n", "norm", "normal", "normals") or any(norm_term in layer_lower for norm_term in ("norm", "normal", "n_")):
        sem_type = SemanticPassType.NORMAL
    elif layer_lower in ("p", "pos", "position") or any(pos_term in layer_lower for pos_term in ("pos", "position", "pref")):
        sem_type = SemanticPassType.POSITION
    elif layer_lower in ("z", "depth") or ch_lower in ("z", "depth.z") or "depth" in layer_lower:
        sem_type = SemanticPassType.DEPTH
    elif any(vec_term in layer_lower for vec_term in ("motion", "vector", "forward", "backward", "vel", "velocity")):
        sem_type = SemanticPassType.VECTOR
    elif ch_lower in ("a", "alpha") or role == "A":
        sem_type = SemanticPassType.ALPHA
    else:
        sem_type = SemanticPassType.COLOR

    return EXRChannelInfo(
        name=ch_raw,
        pixel_type=pixel_type,
        layer=layer,
        channel_role=role,
        semantic_type=sem_type,
    )


def inspect_exr_matrix(path: str) -> Tuple[Dict[str, List[EXRChannelInfo]], List[EXRChannelInfo]]:
    """
    Inspect an OpenEXR file and extract categorized layers and all channels.
    Returns:
      (layers_dict: Dict[layer_name, List[EXRChannelInfo]], all_channels: List[EXRChannelInfo])
    """
    if not path or not os.path.exists(path):
        return {}, []

    # 0. Rust-accelerated metadata inspection
    try:
        from openreview.core.image_io import _is_rust_io_enabled
        if _is_rust_io_enabled():
            import openreview_engine
            rust_eng = getattr(inspect_exr_matrix, "_rust_engine", None)
            if rust_eng is None:
                rust_eng = openreview_engine.OpenReviewEngine()
                inspect_exr_matrix._rust_engine = rust_eng
            meta = rust_eng.inspect_exr(path)
            if meta and meta.get("all_channels"):
                all_channels = [classify_channel(ch) for ch in meta["all_channels"]]
                layers_dict = {}
                for ch_info in all_channels:
                    layers_dict.setdefault(ch_info.layer, []).append(ch_info)
                return layers_dict, all_channels
    except Exception:
        pass

    try:
        import OpenEXR
        import Imath

        inp = OpenEXR.InputFile(path)
        header = inp.header()
        channels = header.get("channels", {})
        inp.close()

        all_channels: List[EXRChannelInfo] = []
        layers_dict: Dict[str, List[EXRChannelInfo]] = {}

        for ch_name, ch_obj in channels.items():
            pt_str = "HALF"
            if hasattr(ch_obj, "type"):
                t = ch_obj.type
                if hasattr(t, "v"):
                    v = t.v
                else:
                    v = getattr(t, "value", None)
                if v == Imath.PixelType.FLOAT:
                    pt_str = "FLOAT"
                elif v == Imath.PixelType.UINT:
                    pt_str = "UINT"

            info = classify_channel(ch_name, pt_str)
            all_channels.append(info)
            layers_dict.setdefault(info.layer, []).append(info)

        return layers_dict, all_channels
    except Exception as e:
        logger.debug("Failed to inspect EXR matrix on %s: %s", path, e)
        return {}, []


def build_layer_preset(layer_name: str, channels: List[EXRChannelInfo]) -> EXRMatrixMapping:
    """
    Build an intelligent default mapping for a given layer based on detected channel roles.
    """
    layer_lower = layer_name.lower()
    ch_map = {c.channel_role.upper(): c.name for c in channels}
    # Also support X, Y, Z mapping to R, G, B
    if "X" in ch_map and "R" not in ch_map:
        ch_map["R"] = ch_map["X"]
    if "Y" in ch_map and "G" not in ch_map:
        ch_map["G"] = ch_map["Y"]
    if "Z" in ch_map and "B" not in ch_map:
        ch_map["B"] = ch_map["Z"]
    if "U" in ch_map and "R" not in ch_map:
        ch_map["R"] = ch_map["U"]
    if "V" in ch_map and "G" not in ch_map:
        ch_map["G"] = ch_map["V"]

    sem_type = channels[0].semantic_type if channels else SemanticPassType.COLOR
    is_depth = (sem_type == SemanticPassType.DEPTH or "z" in layer_lower or "depth" in layer_lower)
    is_normal = (sem_type == SemanticPassType.NORMAL or "norm" in layer_lower)
    is_crypto = (sem_type == SemanticPassType.CRYPTOMATTE or "crypto" in layer_lower)

    if is_crypto:
        return EXRMatrixMapping(
            crypto_layer=layer_name,
            semantic_type=SemanticPassType.CRYPTOMATTE,
        )

    if is_depth:
        # Route depth channel to R, G, B with auto-normalization
        z_ch = ch_map.get("Z") or ch_map.get("R") or (channels[0].name if channels else None)
        return EXRMatrixMapping(
            r_channel=z_ch,
            g_channel=z_ch,
            b_channel=z_ch,
            a_channel=None,
            semantic_type=SemanticPassType.DEPTH,
            auto_normalize_depth=True,
        )

    r_ch = ch_map.get("R")
    g_ch = ch_map.get("G", r_ch)
    b_ch = ch_map.get("B", r_ch)
    a_ch = ch_map.get("A")

    return EXRMatrixMapping(
        r_channel=r_ch,
        g_channel=g_ch,
        b_channel=b_ch,
        a_channel=a_ch,
        semantic_type=sem_type,
        remap_normals=is_normal,
    )


def read_exr_matrix_mapped(
    path: str,
    mapping: EXRMatrixMapping,
    as_f16: bool = True,
) -> Optional[np.ndarray]:
    """
    Read specific OpenEXR channels according to an EXRMatrixMapping, using the
    recycled FrameBufferPool for zero heap allocations.
    """
    if not path or not os.path.exists(path):
        return None

    # 0. Accelerated Rust Engine: Zero-GIL, parallel multi-threaded extraction
    try:
        from openreview.core.image_io import _is_rust_io_enabled
        if _is_rust_io_enabled():
            import openreview_engine
            rust_eng = getattr(read_exr_matrix_mapped, "_rust_engine", None)
            if rust_eng is None:
                rust_eng = openreview_engine.OpenReviewEngine()
                read_exr_matrix_mapped._rust_engine = rust_eng

            native_frame = rust_eng.read_exr(
                path,
                as_f16=as_f16,
                layer=None,
                r_ch=mapping.r_channel,
                g_ch=mapping.g_channel,
                b_ch=mapping.b_channel,
                a_ch=mapping.a_channel,
                auto_normalize_depth=mapping.auto_normalize_depth,
                remap_normals=mapping.remap_normals,
                crypto_layer=mapping.crypto_layer,
            )
            dtype = np.float16 if as_f16 else np.float32
            shape = (native_frame.height, native_frame.width, 4)
            out = FrameBufferPool.instance().acquire(shape, dtype)
            np.copyto(out, np.frombuffer(native_frame.as_memoryview(), dtype=dtype).reshape(shape))
            return out
    except Exception:
        pass

    try:
        import OpenEXR
        import Imath

        inp = OpenEXR.InputFile(path)
        header = inp.header()
        dw = header["dataWindow"]
        w = dw.max.x - dw.min.x + 1
        h = dw.max.y - dw.min.y + 1

        if w <= 0 or h <= 0:
            inp.close()
            return None

        pt_float = Imath.PixelType(Imath.PixelType.FLOAT)
        chan_dict = header.get("channels", {})

        # Handle Cryptomatte Layer
        if mapping.semantic_type == SemanticPassType.CRYPTOMATTE or mapping.crypto_layer:
            crypto_prefix = mapping.crypto_layer or "crypto_object"
            # Find crypto channels matching prefix
            r_ch = None
            g_ch = None
            for k in chan_dict.keys():
                kl = k.lower()
                if kl.startswith(crypto_prefix.lower()):
                    if kl.endswith((".r", "00.r")):
                        r_ch = k
                    elif kl.endswith((".g", "00.g")):
                        g_ch = k

            if r_ch:
                id_buf = np.frombuffer(inp.channel(r_ch, pt_float), dtype=np.float32).reshape((h, w))
                cov_buf = (
                    np.frombuffer(inp.channel(g_ch, pt_float), dtype=np.float32).reshape((h, w))
                    if g_ch
                    else np.ones((h, w), dtype=np.float32)
                )
                inp.close()

                id_uint32 = id_buf.view(np.uint32)
                r_vis = ((id_uint32 & 0xFF0000) >> 16).astype(np.float32) / 255.0
                g_vis = ((id_uint32 & 0x00FF00) >> 8).astype(np.float32) / 255.0
                b_vis = (id_uint32 & 0x0000FF).astype(np.float32) / 255.0
                mask = (id_uint32 != 0)
                r_vis = np.where(mask, r_vis, 0.0)
                g_vis = np.where(mask, g_vis, 0.0)
                b_vis = np.where(mask, b_vis, 0.0)
                a_vis = np.clip(cov_buf, 0.0, 1.0)

                target_dtype = np.float16 if as_f16 else np.float32
                out = FrameBufferPool.instance().acquire((h, w, 4), target_dtype)
                out[:, :, 0] = r_vis
                out[:, :, 1] = g_vis
                out[:, :, 2] = b_vis
                out[:, :, 3] = a_vis
                return out

        # Read routed channels
        ch_data: Dict[str, np.ndarray] = {}
        unique_req_channels = {
            ch for ch in (mapping.r_channel, mapping.g_channel, mapping.b_channel, mapping.a_channel) if ch and ch in chan_dict
        }

        for ch in unique_req_channels:
            raw = inp.channel(ch, pt_float)
            ch_data[ch] = np.frombuffer(raw, dtype=np.float32).reshape((h, w))

        inp.close()

        # Handle Depth Auto-Normalization
        if mapping.auto_normalize_depth and mapping.r_channel in ch_data:
            z_arr = ch_data[mapping.r_channel]
            z_min = float(np.min(z_arr))
            z_max = float(np.max(z_arr))
            if z_max > z_min:
                norm_z = (z_arr - z_min) / (z_max - z_min)
                ch_data[mapping.r_channel] = norm_z

        # Assemble into pooled buffer
        target_dtype = np.float16 if as_f16 else np.float32
        out = FrameBufferPool.instance().acquire((h, w, 4), target_dtype)

        # Red Channel
        if mapping.r_channel and mapping.r_channel in ch_data:
            out[:, :, 0] = ch_data[mapping.r_channel]
        else:
            out[:, :, 0] = 0.0

        # Green Channel
        if mapping.g_channel and mapping.g_channel in ch_data:
            out[:, :, 1] = ch_data[mapping.g_channel]
        else:
            out[:, :, 1] = out[:, :, 0]

        # Blue Channel
        if mapping.b_channel and mapping.b_channel in ch_data:
            out[:, :, 2] = ch_data[mapping.b_channel]
        else:
            out[:, :, 2] = out[:, :, 0]

        # Alpha Channel
        if mapping.a_channel and mapping.a_channel in ch_data:
            out[:, :, 3] = ch_data[mapping.a_channel]
        else:
            out[:, :, 3] = 1.0

        # Normals visual remap: [-1, 1] -> [0, 1]
        if mapping.remap_normals:
            out[:, :, :3] = np.clip(out[:, :, :3] * 0.5 + 0.5, 0.0, 1.0)

        return out

    except Exception as e:
        logger.debug("Failed to read matrix-mapped EXR %s: %s", path, e)
        return None
