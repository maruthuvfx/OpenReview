"""
Generic Standard Colorspaces Definition and Transfer Functions.
Implements industry-standard color curves matching Foundry Nuke and studio pipelines:
linear, sRGB, rec709, Cineon, Gamma 1.8, Gamma 2.2, Panalog, REDLog, ViperLog, REDSpace, and Raw.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Dict, Optional, Tuple
import os
import numpy as np


@dataclass(frozen=True)
class StandardColorSpaceInfo:
    id: int
    name: str
    label: str
    description: str


# Standard Colorspaces Catalog
STANDARD_COLORSPACES: List[StandardColorSpaceInfo] = [
    StandardColorSpaceInfo(
        id=0,
        name="linear",
        label="linear",
        description="The internal working space for compositing operations (Identity/Linear).",
    ),
    StandardColorSpaceInfo(
        id=1,
        name="sRGB",
        label="sRGB",
        description="The standard for 8-bit web and monitor displays.",
    ),
    StandardColorSpaceInfo(
        id=2,
        name="rec709",
        label="rec709",
        description="The standard for HDTV and broadcast video.",
    ),
    StandardColorSpaceInfo(
        id=3,
        name="Cineon",
        label="Cineon",
        description="The standard for film scan data (logarithmic).",
    ),
    StandardColorSpaceInfo(
        id=4,
        name="Gamma 1.8",
        label="Gamma 1.8",
        description="Standard gamma curve for legacy Mac / print prepress display standards.",
    ),
    StandardColorSpaceInfo(
        id=5,
        name="Gamma 2.2",
        label="Gamma 2.2",
        description="Standard gamma curve for PC and power-law display standards.",
    ),
    StandardColorSpaceInfo(
        id=6,
        name="Panalog",
        label="Panalog",
        description="A logarithmic curve used in older video systems (Panavision Genesis).",
    ),
    StandardColorSpaceInfo(
        id=7,
        name="REDLog",
        label="REDLog",
        description="The color encoding for RED digital cinema cameras.",
    ),
    StandardColorSpaceInfo(
        id=8,
        name="ViperLog",
        label="ViperLog",
        description="The color encoding for Pace/Viper cameras.",
    ),
    StandardColorSpaceInfo(
        id=9,
        name="REDSpace",
        label="REDSpace",
        description="A linear colorspace specific to RED cameras.",
    ),
    StandardColorSpaceInfo(
        id=10,
        name="Raw",
        label="Raw / Unmanaged",
        description="Raw pass-through without color transformation.",
    ),
]

COLORSPACE_BY_ID: Dict[int, StandardColorSpaceInfo] = {cs.id: cs for cs in STANDARD_COLORSPACES}
COLORSPACE_BY_NAME: Dict[str, StandardColorSpaceInfo] = {cs.name.lower(): cs for cs in STANDARD_COLORSPACES}

# REDSpace 3x3 conversion matrix (RED wide gamut to/from Rec.709 linear working space)
# Nuke standard REDSpace matrix:
REDSPACE_TO_REC709 = np.array([
    [1.478, -0.389, -0.089],
    [-0.063, 1.077, -0.014],
    [-0.010, -0.168, 1.178],
], dtype=np.float32)

REC709_TO_REDSPACE = np.linalg.inv(REDSPACE_TO_REC709).astype(np.float32)


# -----------------------------------------------------------------------------
# CPU NumPy / Float Transformation Functions
# -----------------------------------------------------------------------------

def srgb_to_linear(c: np.ndarray | float) -> np.ndarray | float:
    c_arr = np.asarray(c, dtype=np.float32)
    higher = np.power(np.maximum((c_arr + 0.055) / 1.055, 0.0), 2.4)
    lower = c_arr / 12.92
    res = np.where(c_arr > 0.04045, higher, lower)
    return res if isinstance(c, np.ndarray) else float(res)


def linear_to_srgb(lin: np.ndarray | float) -> np.ndarray | float:
    lin_arr = np.asarray(lin, dtype=np.float32)
    higher = 1.055 * np.power(np.maximum(lin_arr, 0.0), 1.0 / 2.4) - 0.055
    lower = 12.92 * lin_arr
    res = np.where(lin_arr > 0.0031308, higher, lower)
    return res if isinstance(lin, np.ndarray) else float(res)


def rec709_to_linear(c: np.ndarray | float) -> np.ndarray | float:
    c_arr = np.asarray(c, dtype=np.float32)
    higher = np.power(np.maximum((c_arr + 0.099) / 1.099, 0.0), 1.0 / 0.45)
    lower = c_arr / 4.5
    res = np.where(c_arr > 0.081, higher, lower)
    return res if isinstance(c, np.ndarray) else float(res)


def linear_to_rec709(lin: np.ndarray | float) -> np.ndarray | float:
    lin_arr = np.asarray(lin, dtype=np.float32)
    higher = 1.099 * np.power(np.maximum(lin_arr, 0.0), 0.45) - 0.099
    lower = 4.5 * lin_arr
    res = np.where(lin_arr > 0.018, higher, lower)
    return res if isinstance(lin, np.ndarray) else float(res)


def cineon_to_linear(c: np.ndarray | float) -> np.ndarray | float:
    c_arr = np.asarray(c, dtype=np.float32)
    x = (c_arr * 1023.0 - 685.0) / 300.0
    res = np.maximum(0.0, (np.power(10.0, x) - 0.010798) / 0.989202)
    return res if isinstance(c, np.ndarray) else float(res)


def linear_to_cineon(lin: np.ndarray | float) -> np.ndarray | float:
    lin_arr = np.asarray(lin, dtype=np.float32)
    clamped = np.maximum(lin_arr, 0.00001)
    x = np.log10(clamped * 0.989202 + 0.010798)
    res = np.clip((x * 300.0 + 685.0) / 1023.0, 0.0, 1.0)
    return res if isinstance(lin, np.ndarray) else float(res)


def gamma18_to_linear(c: np.ndarray | float) -> np.ndarray | float:
    c_arr = np.asarray(c, dtype=np.float32)
    res = np.power(np.maximum(c_arr, 0.0), 1.8)
    return res if isinstance(c, np.ndarray) else float(res)


def linear_to_gamma18(lin: np.ndarray | float) -> np.ndarray | float:
    lin_arr = np.asarray(lin, dtype=np.float32)
    res = np.power(np.maximum(lin_arr, 0.0), 1.0 / 1.8)
    return res if isinstance(lin, np.ndarray) else float(res)


def gamma22_to_linear(c: np.ndarray | float) -> np.ndarray | float:
    c_arr = np.asarray(c, dtype=np.float32)
    res = np.power(np.maximum(c_arr, 0.0), 2.2)
    return res if isinstance(c, np.ndarray) else float(res)


def linear_to_gamma22(lin: np.ndarray | float) -> np.ndarray | float:
    lin_arr = np.asarray(lin, dtype=np.float32)
    res = np.power(np.maximum(lin_arr, 0.0), 1.0 / 2.2)
    return res if isinstance(lin, np.ndarray) else float(res)


def panalog_to_linear(c: np.ndarray | float) -> np.ndarray | float:
    c_arr = np.asarray(c, dtype=np.float32)
    x = (c_arr * 1023.0 - 681.0) / 444.0
    res = np.maximum(0.0, (np.power(10.0, x) - 0.002) / 0.998)
    return res if isinstance(c, np.ndarray) else float(res)


def linear_to_panalog(lin: np.ndarray | float) -> np.ndarray | float:
    lin_arr = np.asarray(lin, dtype=np.float32)
    norm = np.maximum(lin_arr * 0.998 + 0.002, 1e-6)
    res = np.clip((np.log10(norm) * 444.0 + 681.0) / 1023.0, 0.0, 1.0)
    return res if isinstance(lin, np.ndarray) else float(res)


def redlog_to_linear(c: np.ndarray | float) -> np.ndarray | float:
    c_arr = np.asarray(c, dtype=np.float32)
    res = np.maximum(0.0, (np.power(10.0, c_arr * 3.0 - 3.0) - 0.00001) / 0.99999)
    return res if isinstance(c, np.ndarray) else float(res)


def linear_to_redlog(lin: np.ndarray | float) -> np.ndarray | float:
    lin_arr = np.asarray(lin, dtype=np.float32)
    norm = np.maximum(lin_arr * 0.99999 + 0.00001, 1e-6)
    res = np.clip((np.log10(norm) + 3.0) / 3.0, 0.0, 1.0)
    return res if isinstance(lin, np.ndarray) else float(res)


def viperlog_to_linear(c: np.ndarray | float) -> np.ndarray | float:
    c_arr = np.asarray(c, dtype=np.float32)
    x = (c_arr * 1023.0 - 1023.0) / 500.0
    res = np.maximum(0.0, (np.power(10.0, x) - 0.01) / 0.99)
    return res if isinstance(c, np.ndarray) else float(res)


def linear_to_viperlog(lin: np.ndarray | float) -> np.ndarray | float:
    lin_arr = np.asarray(lin, dtype=np.float32)
    norm = np.maximum(lin_arr * 0.99 + 0.01, 1e-6)
    res = np.clip((np.log10(norm) * 500.0 + 1023.0) / 1023.0, 0.0, 1.0)
    return res if isinstance(lin, np.ndarray) else float(res)


def redspace_to_linear(c: np.ndarray | Tuple[float, float, float]) -> np.ndarray:
    c_arr = np.asarray(c, dtype=np.float32)
    if c_arr.ndim == 1:
        return np.dot(REDSPACE_TO_REC709, c_arr)
    return np.tensordot(c_arr, REDSPACE_TO_REC709, axes=([-1], [1]))


def linear_to_redspace(lin: np.ndarray | Tuple[float, float, float]) -> np.ndarray:
    lin_arr = np.asarray(lin, dtype=np.float32)
    if lin_arr.ndim == 1:
        return np.dot(REC709_TO_REDSPACE, lin_arr)
    return np.tensordot(lin_arr, REC709_TO_REDSPACE, axes=([-1], [1]))


def apply_standard_to_linear(rgb: np.ndarray, space_id: int) -> np.ndarray:
    """Convert RGB data from standard colorspace to scene-linear compositing working space."""
    if space_id == 0:  # linear
        return rgb
    elif space_id == 1:  # sRGB
        return srgb_to_linear(rgb)
    elif space_id == 2:  # rec709
        return rec709_to_linear(rgb)
    elif space_id == 3:  # Cineon
        return cineon_to_linear(rgb)
    elif space_id == 4:  # Gamma 1.8
        return gamma18_to_linear(rgb)
    elif space_id == 5:  # Gamma 2.2
        return gamma22_to_linear(rgb)
    elif space_id == 6:  # Panalog
        return panalog_to_linear(rgb)
    elif space_id == 7:  # REDLog
        return redlog_to_linear(rgb)
    elif space_id == 8:  # ViperLog
        return viperlog_to_linear(rgb)
    elif space_id == 9:  # REDSpace
        return redspace_to_linear(rgb)
    elif space_id == 10:  # Raw
        return rgb
    return rgb


def apply_linear_to_standard(lin_rgb: np.ndarray, view_id: int) -> np.ndarray:
    """Convert scene-linear working color data to standard display viewing transform."""
    if view_id == 0:  # linear
        return lin_rgb
    elif view_id == 1:  # sRGB
        return linear_to_srgb(lin_rgb)
    elif view_id == 2:  # rec709
        return linear_to_rec709(lin_rgb)
    elif view_id == 3:  # Cineon
        return linear_to_cineon(lin_rgb)
    elif view_id == 4:  # Gamma 1.8
        return linear_to_gamma18(lin_rgb)
    elif view_id == 5:  # Gamma 2.2
        return linear_to_gamma22(lin_rgb)
    elif view_id == 6:  # Panalog
        return linear_to_panalog(lin_rgb)
    elif view_id == 7:  # REDLog
        return linear_to_redlog(lin_rgb)
    elif view_id == 8:  # ViperLog
        return linear_to_viperlog(lin_rgb)
    elif view_id == 9:  # REDSpace
        return linear_to_redspace(lin_rgb)
    elif view_id == 10:  # Raw
        return lin_rgb
    return lin_rgb

# -----------------------------------------------------------------------------
# Metadata-Driven Colorspace Auto-Detection Engine
# -----------------------------------------------------------------------------

def detect_media_input_colorspace(
    file_path: Optional[str],
    metadata: Optional[Dict[str, Any]] = None,
) -> StandardColorSpaceInfo:
    """
    Choose standard input colorspace automatically based on media format,
    container color transfer / primaries tags, bit-depth, and camera metadata.
    """
    ext = os.path.splitext(file_path)[1].lower() if file_path else ""

    # 1. Image Format / Extension Specific Rules
    if ext in (".exr", ".hdr"):
        return COLORSPACE_BY_ID[0]  # linear (VFX scene-linear HDR standard)
    if ext in (".dpx", ".cin"):
        return COLORSPACE_BY_ID[3]  # Cineon (Kodak film scan logarithmic standard)
    if ext in (".r3d",):
        return COLORSPACE_BY_ID[7]  # REDLog

    # If metadata wasn't passed in, try extracting if path exists
    if metadata is None and file_path and os.path.isfile(file_path):
        try:
            from openreview.core.metadata import extract_media_metadata
            metadata = extract_media_metadata(file_path)
        except Exception:
            metadata = {}

    meta = metadata or {}

    # 2. Inspect Explicit Stream Color Transfer Characteristics
    transfer = str(meta.get("color_transfer", "")).lower().strip()
    if transfer:
        if transfer in ("linear",):
            return COLORSPACE_BY_ID[0]  # linear
        elif transfer in ("iec61966-2-1", "iec61966_2_1", "srgb"):
            return COLORSPACE_BY_ID[1]  # sRGB
        elif transfer in ("bt709", "rec709", "smpte170m", "smpte240m", "bt2020_10", "bt2020_12"):
            return COLORSPACE_BY_ID[2]  # rec709
        elif transfer in ("gamma22", "power2.2", "gamma2.2"):
            return COLORSPACE_BY_ID[5]  # Gamma 2.2
        elif transfer in ("gamma18", "power1.8", "gamma1.8", "gamma28"):
            return COLORSPACE_BY_ID[4]  # Gamma 1.8
        elif "cineon" in transfer or "film" in transfer or "log" in transfer:
            return COLORSPACE_BY_ID[3]  # Cineon

    # 3. Inspect Camera Metadata / Tags (RED, Panavision, Viper, etc.)
    all_tags = meta.get("all_metadata_tags", {})
    tags_text = " ".join([f"{k} {v}" for k, v in all_tags.items()]).lower()

    if "redspace" in tags_text or "red_space" in tags_text:
        return COLORSPACE_BY_ID[9]  # REDSpace
    elif "redlog" in tags_text or "red_log" in tags_text or "redlogfilm" in tags_text:
        return COLORSPACE_BY_ID[7]  # REDLog
    elif "panalog" in tags_text or "genesis" in tags_text:
        return COLORSPACE_BY_ID[6]  # Panalog
    elif "viperlog" in tags_text or "filmstream" in tags_text:
        return COLORSPACE_BY_ID[8]  # ViperLog
    elif "cineon" in tags_text:
        return COLORSPACE_BY_ID[3]  # Cineon

    # 4. Inspect Primaries and Color Space
    primaries = str(meta.get("color_primaries", "")).lower().strip()
    cs_name = str(meta.get("color_space", "")).lower().strip()
    if "red" in primaries or "red" in cs_name:
        if "space" in primaries or "space" in cs_name:
            return COLORSPACE_BY_ID[9]
        return COLORSPACE_BY_ID[7]
    if "bt709" in primaries or "bt709" in cs_name:
        return COLORSPACE_BY_ID[2]

    # 5. Video Container Fallback Based on Resolution
    try:
        from openreview.core.video_reader import is_video_file
        is_vid = is_video_file(file_path) if file_path else ("video_codec" in meta or "duration_sec" in meta)
    except Exception:
        is_vid = ("video_codec" in meta or "duration_sec" in meta)

    if is_vid:
        w = int(meta.get("width") or 0)
        h = int(meta.get("height") or 0)
        # HD or UHD broadcast video (width >= 1280 or height >= 720) defaults to rec709
        if w >= 1280 or h >= 720:
            return COLORSPACE_BY_ID[2]  # rec709
        return COLORSPACE_BY_ID[1]  # sRGB for SD/web video

    # 6. Standard Still Image Formats
    if ext in (".png", ".jpg", ".jpeg", ".bmp", ".webp", ".gif", ".tif", ".tiff"):
        # Check EXIF / Image tags for Gamma 1.8 / Apple RGB
        if "gamma 1.8" in tags_text or "gamma1.8" in tags_text:
            return COLORSPACE_BY_ID[4]  # Gamma 1.8
        return COLORSPACE_BY_ID[1]  # sRGB

    # Default fallback
    return COLORSPACE_BY_ID[1]  # sRGB


def _norm_cs_name(s: str) -> str:
    return s.lower().replace(".", "").replace("-", "").replace("_", "").replace(" ", "")


def map_standard_to_ocio_space(detected_id: int, ocio_manager: Any) -> Optional[str]:
    """
    Intelligently map a detected standard colorspace ID to an available colorspace
    in the active OpenColorIO configuration.
    """
    if ocio_manager is None:
        return None

    available_spaces = ocio_manager.get_color_spaces()
    if not available_spaces:
        return None

    roles = ocio_manager.get_roles() if hasattr(ocio_manager, "get_roles") else {}
    detected_info = COLORSPACE_BY_ID.get(detected_id, COLORSPACE_BY_ID[1])
    det_norm = _norm_cs_name(detected_info.name)

    CANDIDATES: Dict[int, List[str]] = {
        0: [roles.get("scene_linear", ""), "ACEScg", "Linear sRGB", "scene_linear", "linear", "ACES2065-1", "Raw"],
        1: [roles.get("color_picking", ""), roles.get("texture_paint", ""), "sRGB", "sRGB - Texture", "Utility - sRGB - Texture", "sRGB - Display"],
        2: [roles.get("rec709", ""), "Rec.709", "Rec709", "Camera Rec.709", "Rec.709 - Camera", "Linear Rec.709 (sRGB)", "Utility - Rec.709 - Camera", "sRGB"],
        3: [roles.get("compositing_log", ""), "Cineon", "ACEScct", "ACEScc", "ADX10", "Input - Generic - Cineon"],
        4: ["Gamma 1.8", "Gamma 1.8 Rec.709 - Texture", "Utility - Gamma 1.8 - Texture", "sRGB"],
        5: ["Gamma 2.2", "Gamma 2.2 Rec.709 - Texture", "Utility - Gamma 2.2 - Texture", "sRGB"],
        6: ["Panalog", "Input - Panavision - Panalog", "ACEScct"],
        7: ["REDLog", "REDLogFilm", "Log3G10 REDWideGamutRGB", "Input - RED - REDLogFilm", "ACEScct"],
        8: ["ViperLog", "Input - Thomson - ViperLog", "ACEScct"],
        9: ["REDSpace", "Linear REDWideGamutRGB", "REDWideGamutRGB", "ACEScg"],
        10: ["Raw", "Utility - Raw"],
    }

    candidates = CANDIDATES.get(detected_id, [])
    for cand in candidates:
        if cand and cand in available_spaces:
            return cand

    # Normalized match on candidates
    norm_map = {_norm_cs_name(s): s for s in available_spaces}
    for cand in candidates:
        if cand and _norm_cs_name(cand) in norm_map:
            return norm_map[_norm_cs_name(cand)]

    # Normalized direct match with detected name
    if det_norm in norm_map:
        return norm_map[det_norm]

    # Partial substring normalized search
    for sp in available_spaces:
        if det_norm in _norm_cs_name(sp):
            return sp

    return getattr(ocio_manager, "input_space", None) or available_spaces[0]

