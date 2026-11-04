"""
Hierarchical Metadata & ExifTool / OpenEXR Attribute Tree Engine.
Extracts deep, structured, multi-domain metadata from OpenEXR headers,
ExifTool outputs, video containers, image formats, and pipeline context.
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple, Union

logger = logging.getLogger(__name__)

try:
    import OpenEXR
    import Imath
    HAS_OPENEXR = True
except ImportError:
    HAS_OPENEXR = False
    OpenEXR = None
    Imath = None

try:
    from PIL import Image
    from PIL.ExifTags import TAGS, GPSTAGS
    HAS_PIL = True
except ImportError:
    HAS_PIL = False


@dataclass
class MetadataNode:
    """Represents a hierarchical node in the metadata attribute tree."""
    key: str
    value: Any = ""
    type_name: str = ""           # e.g. "box2i", "v2f", "timecode", "float", "int", "string", "chlist", "category"
    category: str = "General"     # e.g. "Key Attributes", "EXR Header", "Channels", "Camera / Optics", "ExifTool / EXIF", "Container", "File System"
    raw_key: str = ""
    is_pinned: bool = False
    children: List[MetadataNode] = field(default_factory=list)

    def add_child(self, child: MetadataNode) -> MetadataNode:
        self.children.append(child)
        return child

    def to_dict(self) -> Dict[str, Any]:
        """Convert node and children to nested dictionary for JSON export."""
        res: Dict[str, Any] = {
            "key": self.key,
            "value": str(self.value) if not isinstance(self.value, (int, float, bool, list, dict)) else self.value,
            "type": self.type_name,
            "category": self.category,
        }
        if self.raw_key:
            res["raw_key"] = self.raw_key
        if self.children:
            res["children"] = [c.to_dict() for c in self.children]
        return res


def find_exiftool_executable(custom_path: Optional[str] = None) -> Optional[str]:
    """
    Locate exiftool in system PATH, user preferences, environment variables,
    or common operating system install directories.
    """
    # 1. Custom / Preferences path
    if custom_path and os.path.isfile(custom_path):
        return custom_path

    # 2. Environment variable
    env_p = os.environ.get("EXIFTOOL_PATH")
    if env_p and os.path.isfile(env_p):
        return env_p

    # 3. System PATH
    which_p = shutil.which("exiftool")
    if which_p:
        return which_p

    # 4. Common standard install locations
    candidates = [
        # Windows
        os.path.expandvars(r"%LOCALAPPDATA%\exiftool\exiftool.exe"),
        r"C:\tools\exiftool\exiftool.exe",
        r"C:\Program Files\exiftool\exiftool.exe",
        r"C:\ProgramData\chocolatey\bin\exiftool.exe",
        r"C:\exiftool\exiftool.exe",
        # Unix / macOS
        "/usr/local/bin/exiftool",
        "/usr/bin/exiftool",
        "/opt/homebrew/bin/exiftool",
    ]
    for c in candidates:
        if os.path.isfile(c):
            return c
    return None


def get_exiftool_version(exiftool_bin: str) -> Optional[str]:
    """Retrieve version string from ExifTool binary."""
    try:
        res = subprocess.run([exiftool_bin, "-ver"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=1.5)
        if res.returncode == 0 and res.stdout:
            return res.stdout.strip()
    except Exception:
        pass
    return None


def run_exiftool(file_path: str, exiftool_bin: Optional[str] = None) -> Dict[str, Any]:
    """
    Run ExifTool on the target file with JSON output grouped by family (-G).
    Returns nested dictionary grouped by tag family (e.g. EXIF, MakerNotes, XMP, QuickTime).
    """
    bin_path = exiftool_bin or find_exiftool_executable()
    if not bin_path or not os.path.isfile(file_path):
        return {}

    try:
        cmd = [
            bin_path,
            "-json",
            "-G",
            "-a",
            "-u",
            "-struct",
            file_path,
        ]
        res = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=3.0,
        )
        if res.returncode == 0 and res.stdout:
            data = json.loads(res.stdout)
            if isinstance(data, list) and len(data) > 0:
                return data[0]
            elif isinstance(data, dict):
                return data
    except Exception as e:
        logger.debug("ExifTool execution failed on %s: %s", file_path, e)
    return {}


# ------------------------------------------------------------------------------
# OpenEXR Header Tree Extraction
# ------------------------------------------------------------------------------
def format_imath_value(val: Any) -> Tuple[str, str]:
    """
    Format Imath / OpenEXR attribute into (formatted_value, type_name).
    """
    if val is None:
        return "", "null"

    val_type = type(val).__name__

    # 1. Box2i / Box2f (Windows)
    if hasattr(val, "min") and hasattr(val, "max"):
        try:
            min_x, min_y = int(val.min.x), int(val.min.y)
            max_x, max_y = int(val.max.x), int(val.max.y)
            w = max_x - min_x + 1
            h = max_y - min_y + 1
            return f"({min_x}, {min_y}) - ({max_x}, {max_y}) [{w} x {h}]", "box2i"
        except Exception:
            return str(val), "box"

    # 2. TimeCode
    if hasattr(val, "hours") and hasattr(val, "minutes") and hasattr(val, "seconds") and hasattr(val, "frame"):
        try:
            tc_str = f"{val.hours:02d}:{val.minutes:02d}:{val.seconds:02d}:{val.frame:02d}"
            drop = " (dropFrame)" if getattr(val, "dropFrame", 0) else ""
            return f"{tc_str}{drop}", "timecode"
        except Exception:
            return str(val), "timecode"

    # 3. Rational
    if hasattr(val, "n") and hasattr(val, "d"):
        try:
            flt_val = float(val.n) / max(1, float(val.d))
            return f"{val.n} / {val.d} ({flt_val:.4f})", "rational"
        except Exception:
            return str(val), "rational"

    # 4. Vectors (V2f, V2i, V3f, V3i)
    if hasattr(val, "x") and hasattr(val, "y"):
        if hasattr(val, "z"):
            return f"({val.x:.4f}, {val.y:.4f}, {val.z:.4f})", "v3f"
        return f"({val.x:.4f}, {val.y:.4f})", "v2f"

    # 5. Chromaticities
    if hasattr(val, "red") and hasattr(val, "green") and hasattr(val, "blue") and hasattr(val, "white"):
        try:
            return (
                f"R:({val.red.x:.4f}, {val.red.y:.4f}) G:({val.green.x:.4f}, {val.green.y:.4f}) "
                f"B:({val.blue.x:.4f}, {val.blue.y:.4f}) W:({val.white.x:.4f}, {val.white.y:.4f})",
                "chromaticities",
            )
        except Exception:
            return str(val), "chromaticities"

    # 6. Compression / LineOrder
    if val_type in ("Compression", "LineOrder", "PixelType", "LevelMode"):
        return str(val), val_type.lower()

    # 7. Floats and Ints
    if isinstance(val, str):
        return val, "string"
    if isinstance(val, float):
        return f"{val:.6g}", "float"
    if isinstance(val, int):
        return str(val), "int"
    if isinstance(val, (bytes, bytearray)):
        try:
            return val.decode("utf-8"), "string"
        except Exception:
            return f"<binary {len(val)} bytes>", "bytes"

    return str(val), val_type.lower()


def extract_exr_attribute_nodes(file_path: str) -> List[MetadataNode]:
    """
    Extract structured hierarchical MetadataNode tree from OpenEXR header.
    Categorizes into Header Specs, Channels & AOVs, Cryptomatte, Camera/Optics,
    Render/DCC, and Custom Attributes.
    """
    if not HAS_OPENEXR or not os.path.isfile(file_path):
        return []

    try:
        inp = OpenEXR.InputFile(file_path)
        header = inp.header()
        inp.close()
    except Exception as e:
        logger.debug("Failed opening EXR header for %s: %s", file_path, e)
        return []

    categories: Dict[str, MetadataNode] = {
        "header": MetadataNode(key="Header Specifications", type_name="category", category="EXR Header"),
        "channels": MetadataNode(key="Channels & AOVs", type_name="category", category="Channels"),
        "camera": MetadataNode(key="Camera & Lens Optics", type_name="category", category="Camera / Optics"),
        "render": MetadataNode(key="Render & DCC Tokens", type_name="category", category="Production / Pipeline"),
        "crypto": MetadataNode(key="Cryptomatte Manifests", type_name="category", category="Cryptomatte"),
        "custom": MetadataNode(key="Custom Header Attributes", type_name="category", category="Custom"),
    }

    # 1. Standard Header Specifications
    std_keys = [
        "displayWindow", "dataWindow", "pixelAspectRatio", "compression",
        "lineOrder", "screenWindowCenter", "screenWindowWidth",
    ]
    for k in std_keys:
        if k in header:
            val_str, type_str = format_imath_value(header[k])
            categories["header"].add_child(MetadataNode(
                key=k,
                value=val_str,
                type_name=type_str,
                category="EXR Header",
                raw_key=f"exr/{k}",
            ))

    # 2. Channels & AOVs
    channels_dict = header.get("channels", {})
    if channels_dict:
        # Group channels by layer prefix
        layers: Dict[str, List[Tuple[str, Any]]] = {}
        for c_name, c_val in sorted(channels_dict.items()):
            if "." in c_name:
                parts = c_name.split(".")
                lyr = parts[0]
                ch_sub = ".".join(parts[1:])
            else:
                lyr = "beauty"
                ch_sub = c_name
            layers.setdefault(lyr, []).append((ch_sub, c_val))

        for lyr_name, ch_list in sorted(layers.items()):
            lyr_node = MetadataNode(
                key=lyr_name,
                value=f"{len(ch_list)} channel(s)",
                type_name="layer",
                category="Channels",
                raw_key=f"exr/channels/{lyr_name}",
            )
            for ch_name, ch_obj in ch_list:
                p_type = str(getattr(ch_obj, "type", "FLOAT"))
                x_samp = getattr(ch_obj, "xSampling", 1)
                y_samp = getattr(ch_obj, "ySampling", 1)
                samp_str = f" [{x_samp}x{y_samp}]" if (x_samp != 1 or y_samp != 1) else ""
                lyr_node.add_child(MetadataNode(
                    key=ch_name,
                    value=f"{p_type}{samp_str}",
                    type_name=p_type.lower(),
                    category="Channels",
                    raw_key=f"exr/channels/{lyr_name}.{ch_name}",
                ))
            categories["channels"].add_child(lyr_node)

    # 3. Categorize remaining attributes
    camera_keywords = {
        "camera", "aperture", "focallength", "focusdistance", "isospeedratings",
        "shutter", "lens", "filmgate", "nearclip", "farclip", "timecode",
        "framespersecond", "exptime", "worldtocamera", "worldtondc", "chromaticities",
        "whitebalance", "fnumber", "exposuretime", "focal_length", "focus_distance",
    }
    render_keywords = {"arnold", "vray", "renderman", "karma", "nuke", "blender", "unreal", "katana", "houdini", "alembic"}

    reserved_keys = set(std_keys) | {"channels"}

    for k, v in sorted(header.items()):
        if k in reserved_keys:
            continue

        k_lower = k.lower()
        val_str, type_str = format_imath_value(v)

        # Cryptomatte
        if "crypto" in k_lower:
            categories["crypto"].add_child(MetadataNode(
                key=k,
                value=val_str,
                type_name=type_str,
                category="Cryptomatte",
                raw_key=f"exr/{k}",
            ))
        # Camera / Optics
        elif any(ck in k_lower for ck in camera_keywords):
            categories["camera"].add_child(MetadataNode(
                key=k,
                value=val_str,
                type_name=type_str,
                category="Camera / Optics",
                raw_key=f"exr/{k}",
                is_pinned=k_lower in ("focallength", "aperture", "timecode", "isospeedratings", "framespersecond"),
            ))
        # Render / DCC Tokens
        elif any(rk in k_lower for rk in render_keywords) or "/" in k:
            categories["render"].add_child(MetadataNode(
                key=k,
                value=val_str,
                type_name=type_str,
                category="Production / Pipeline",
                raw_key=f"exr/{k}",
            ))
        # Custom
        else:
            categories["custom"].add_child(MetadataNode(
                key=k,
                value=val_str,
                type_name=type_str,
                category="Custom",
                raw_key=f"exr/{k}",
            ))

    # Return only non-empty categories
    return [node for node in categories.values() if node.children]


# ------------------------------------------------------------------------------
# ExifTool / EXIF / Container Tree Extraction
# ------------------------------------------------------------------------------
def extract_exiftool_nodes(file_path: str, exiftool_bin: Optional[str] = None) -> List[MetadataNode]:
    """
    Extract structured MetadataNodes from ExifTool output or Pillow/FFprobe fallback.
    """
    raw_data = run_exiftool(file_path, exiftool_bin)
    if not raw_data:
        # Fallback to Pillow EXIF tags if image file
        return extract_pillow_exif_nodes(file_path)

    # Group by ExifTool family prefix (e.g. EXIF:ISO, QuickTime:Duration, XMP:Creator)
    groups: Dict[str, MetadataNode] = {}

    for full_tag, val in raw_data.items():
        if full_tag in ("SourceFile", "ExifToolVersion"):
            continue

        if ":" in full_tag:
            family, tag_name = full_tag.split(":", 1)
        else:
            family, tag_name = "General", full_tag

        if family not in groups:
            groups[family] = MetadataNode(
                key=f"ExifTool: {family}",
                type_name="category",
                category="ExifTool / EXIF",
            )

        val_str = str(val) if not isinstance(val, (list, dict)) else json.dumps(val)
        val_type = type(val).__name__
        if isinstance(val, (int, float)):
            val_type = "number"
        elif isinstance(val, str):
            val_type = "string"

        groups[family].add_child(MetadataNode(
            key=tag_name,
            value=val_str,
            type_name=val_type,
            category=f"ExifTool ({family})",
            raw_key=full_tag,
        ))

    return list(groups.values())


def extract_pillow_exif_nodes(file_path: str) -> List[MetadataNode]:
    """Fallback EXIF extraction using Pillow when ExifTool is not available."""
    if not HAS_PIL or not os.path.isfile(file_path):
        return []

    nodes: List[MetadataNode] = []
    try:
        with Image.open(file_path) as img:
            # 1. Image Info
            if hasattr(img, "info") and img.info:
                info_node = MetadataNode(key="Image Embedded Info", type_name="category", category="ExifTool / EXIF")
                for k, v in sorted(img.info.items()):
                    if k != "exif" and len(str(v)) < 1000:
                        info_node.add_child(MetadataNode(
                            key=str(k),
                            value=str(v),
                            type_name=type(v).__name__,
                            category="Image Info",
                            raw_key=f"pil/info/{k}",
                        ))
                if info_node.children:
                    nodes.append(info_node)

            # 2. EXIF Tags
            if hasattr(img, "_getexif") and callable(img._getexif):
                raw_exif = img._getexif()
                if raw_exif:
                    exif_node = MetadataNode(key="EXIF Tags (Pillow)", type_name="category", category="ExifTool / EXIF")
                    for tag_id, val in sorted(raw_exif.items()):
                        tag_name = TAGS.get(tag_id, f"Tag_{tag_id}")
                        if len(str(val)) < 1000:
                            exif_node.add_child(MetadataNode(
                                key=tag_name,
                                value=str(val),
                                type_name=type(val).__name__,
                                category="EXIF",
                                raw_key=f"exif/{tag_name}",
                            ))
                    if exif_node.children:
                        nodes.append(exif_node)
    except Exception as e:
        logger.debug("Pillow EXIF extraction fallback failed on %s: %s", file_path, e)
    return nodes


# ------------------------------------------------------------------------------
# Unified Master Attribute Tree Builder
# ------------------------------------------------------------------------------
def build_unified_metadata_tree(
    file_path: str,
    context: Optional[Dict[str, Any]] = None,
    exiftool_bin: Optional[str] = None,
) -> List[MetadataNode]:
    """
    Construct the complete multi-domain hierarchical attribute tree.
    Combines Pinned Key Attributes, OpenEXR Headers, ExifTool / EXIF,
    Video Streams & Codecs, File System, and Studio OCIO color state.
    """
    master_tree: List[MetadataNode] = []
    ctx = context or {}

    # Group 1: Key Production Attributes (Pinned at top)
    key_attrs = MetadataNode(
        key="Key Production Attributes",
        type_name="category",
        category="Key Attributes",
        is_pinned=True,
    )

    # Group 2: File System Attributes
    fs_node = MetadataNode(key="File System & Container", type_name="category", category="File System")
    if file_path and os.path.exists(file_path):
        fs_node.add_child(MetadataNode(key="File Name", value=os.path.basename(file_path), type_name="string", category="File System"))
        fs_node.add_child(MetadataNode(key="Directory", value=os.path.dirname(file_path), type_name="string", category="File System"))
        fs_node.add_child(MetadataNode(key="Full Path", value=os.path.abspath(file_path), type_name="string", category="File System"))
        try:
            st = os.stat(file_path)
            sz_mb = st.st_size / (1024.0 * 1024.0)
            sz_str = f"{sz_mb / 1024.0:.2f} GB ({st.st_size:,} bytes)" if sz_mb >= 1024.0 else f"{sz_mb:.2f} MB ({st.st_size:,} bytes)"
            fs_node.add_child(MetadataNode(key="File Size", value=sz_str, type_name="size", category="File System"))
            fs_node.add_child(MetadataNode(key="Modified Time", value=time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(st.st_mtime)), type_name="datetime", category="File System"))
            fs_node.add_child(MetadataNode(key="Created Time", value=time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(st.st_ctime)), type_name="datetime", category="File System"))
        except Exception:
            pass

    # Extract EXR nodes if applicable
    is_exr = file_path and (file_path.lower().endswith(".exr") or ctx.get("extension") == ".exr")
    exr_nodes = extract_exr_attribute_nodes(file_path) if is_exr else []

    # Extract ExifTool nodes
    exif_nodes = extract_exiftool_nodes(file_path, exiftool_bin)

    # Populate Key Production Attributes from available sources
    # Resolution
    w = ctx.get("width")
    h = ctx.get("height")
    if w and h:
        key_attrs.add_child(MetadataNode(key="Resolution", value=f"{w} x {h} ({(w * h) / 1e6:.2f} MP)", type_name="resolution", category="Key Attributes"))

    # Timecode
    tc_found = False
    for cat in exr_nodes:
        for ch in cat.children:
            if ch.type_name == "timecode" or ch.key.lower() == "timecode":
                key_attrs.add_child(MetadataNode(key="Timecode", value=ch.value, type_name="timecode", category="Key Attributes"))
                tc_found = True
                break
        if tc_found:
            break

    # Focal Length & Aperture
    for cat in exr_nodes:
        for ch in cat.children:
            k_low = ch.key.lower()
            if "focallength" in k_low:
                key_attrs.add_child(MetadataNode(key="Focal Length", value=f"{ch.value} mm", type_name="float", category="Key Attributes"))
            elif "aperture" in k_low or "fnumber" in k_low:
                key_attrs.add_child(MetadataNode(key="Aperture", value=f"ƒ/{ch.value}", type_name="float", category="Key Attributes"))
            elif "isospeedratings" in k_low or "iso" in k_low:
                key_attrs.add_child(MetadataNode(key="ISO", value=str(ch.value), type_name="int", category="Key Attributes"))

    # Compression
    if ctx.get("compression"):
        key_attrs.add_child(MetadataNode(key="Compression", value=str(ctx["compression"]), type_name="compression", category="Key Attributes"))
    elif ctx.get("video_codec"):
        key_attrs.add_child(MetadataNode(key="Codec", value=str(ctx["video_codec"]), type_name="codec", category="Key Attributes"))

    # Colorspace
    if ctx.get("input_space_name"):
        key_attrs.add_child(MetadataNode(key="Input Colorspace", value=str(ctx["input_space_name"]), type_name="colorspace", category="Key Attributes"))
    if ctx.get("view_transform_name"):
        key_attrs.add_child(MetadataNode(key="View Transform", value=str(ctx["view_transform_name"]), type_name="view", category="Key Attributes"))

    # Add Key Attributes if we gathered any
    if key_attrs.children:
        master_tree.append(key_attrs)

    # Add OpenEXR header branches
    if exr_nodes:
        master_tree.extend(exr_nodes)

    # Add ExifTool / EXIF branches
    if exif_nodes:
        master_tree.extend(exif_nodes)

    # Video Streams & Codec Specs (from context / ffprobe)
    stream_node = MetadataNode(key="Streams & Video Specifications", type_name="category", category="Streams")
    stream_keys = [
        ("video_codec", "Video Codec"),
        ("pixel_format", "Pixel Format"),
        ("color_space", "Stream Color Space"),
        ("color_primaries", "Color Primaries"),
        ("color_transfer", "Transfer Function"),
        ("video_bitrate_str", "Video Bitrate"),
        ("fps", "Native FPS"),
        ("nb_frames", "Total Frames"),
        ("audio_codec", "Audio Codec"),
        ("audio_sample_rate", "Audio Sample Rate"),
        ("audio_channels", "Audio Channels"),
    ]
    for ctx_key, label in stream_keys:
        if ctx.get(ctx_key):
            stream_node.add_child(MetadataNode(
                key=label,
                value=str(ctx[ctx_key]),
                type_name="stream",
                category="Streams",
                raw_key=f"stream/{ctx_key}",
            ))
    if stream_node.children:
        master_tree.append(stream_node)

    # File System branch
    if fs_node.children:
        master_tree.append(fs_node)

    # Review & Studio Viewport Color
    review_node = MetadataNode(key="Studio Review & Playback State", type_name="category", category="Review")
    review_node.add_child(MetadataNode(key="Current Frame", value=str(ctx.get("current_frame", 1)), type_name="int", category="Review"))
    review_node.add_child(MetadataNode(key="Frame Range", value=f"[{ctx.get('start_frame', 1)} .. {ctx.get('end_frame', 100)}]", type_name="range", category="Review"))
    review_node.add_child(MetadataNode(key="Exposure EV", value=f"{ctx.get('exposure_ev', 0.0):+.2f} EV", type_name="float", category="Review"))
    review_node.add_child(MetadataNode(key="Display Gamma", value=f"{ctx.get('gamma', 1.0):.2f}", type_name="float", category="Review"))
    if review_node.children:
        master_tree.append(review_node)

    return master_tree
