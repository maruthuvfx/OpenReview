"""
Comprehensive VFX and Media Metadata Extraction Engine.
Extracts container, codec, color, stream, and file system metadata
using ffprobe, OpenCV, and Pillow with robust cross-platform fallbacks.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from typing import Any, Dict, Optional
import numpy as np

try:
    import cv2
    HAS_CV2 = True
except ImportError:
    HAS_CV2 = False

try:
    from PIL import Image
    HAS_PIL = True
except ImportError:
    HAS_PIL = False


def find_ffprobe_executable() -> Optional[str]:
    """Locate ffprobe in PATH or common standard install locations."""
    # 1. System PATH
    p = shutil.which("ffprobe")
    if p:
        return p

    # 2. Local AppData / common Windows paths
    candidates = [
        os.path.expandvars(r"%LOCALAPPDATA%\ffmpeg\ffmpeg-8.1.2-full_build\bin\ffprobe.exe"),
        os.path.expandvars(r"%LOCALAPPDATA%\ffmpeg\bin\ffprobe.exe"),
        r"C:\ffmpeg\bin\ffprobe.exe",
        r"C:\Program Files\ffmpeg\bin\ffprobe.exe",
    ]
    for c in candidates:
        if os.path.isfile(c):
            return c
    return None


def extract_media_metadata(file_path: Optional[str]) -> Dict[str, Any]:
    """
    Extract comprehensive metadata dictionary for given media file or container.
    Returns normalized dictionary with metadata fields.
    """
    actual_path = file_path
    if file_path and not os.path.exists(file_path):
        try:
            parent_dir = os.path.dirname(file_path)
            basename = os.path.basename(file_path)
            if parent_dir and os.path.isdir(parent_dir):
                import re
                p = re.sub(r"#+", lambda _: r"\d+", basename)
                p = re.sub(r"%0?\d*d", lambda _: r"\d+", p)
                rx = re.compile(f"^{p}$", re.IGNORECASE)
                for f in os.listdir(parent_dir):
                    if rx.match(f):
                        candidate = os.path.join(parent_dir, f)
                        if os.path.isfile(candidate):
                            actual_path = candidate
                            break
        except Exception:
            pass

        if not actual_path or not os.path.exists(actual_path):
            try:
                from openreview.core.sequence_parser import detect_image_sequence
                seq_info = detect_image_sequence(file_path)
                if seq_info and hasattr(seq_info, "frame_path"):
                    first_f = seq_info.available_frames[0] if seq_info.available_frames else seq_info.start_frame
                    fp = seq_info.frame_path(first_f)
                    if fp and os.path.exists(fp):
                        actual_path = fp
            except Exception:
                pass

    if not actual_path or not os.path.exists(actual_path):
        return {
            "file_path": file_path or "No Media Loaded",
            "file_name": os.path.basename(file_path) if file_path else "Synthetic Stream",
            "file_dir": os.path.dirname(file_path) if file_path else "Memory",
            "status": "No file on disk",
        }

    meta: Dict[str, Any] = {
        "file_path": file_path,
        "resolved_file_path": actual_path,
        "file_name": os.path.basename(file_path),
        "file_dir": os.path.dirname(file_path),
        "extension": os.path.splitext(actual_path)[1].lower(),
    }

    # 1. File System Attributes
    try:
        st = os.stat(actual_path)
        meta["file_size_bytes"] = st.st_size
        meta["file_size_mb"] = st.st_size / (1024.0 * 1024.0)
        if meta["file_size_mb"] >= 1024.0:
            meta["file_size_str"] = f"{meta['file_size_mb'] / 1024.0:.2f} GB ({st.st_size:,} bytes)"
        else:
            meta["file_size_str"] = f"{meta['file_size_mb']:.2f} MB ({st.st_size:,} bytes)"
        meta["modified_time"] = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(st.st_mtime))
        meta["created_time"] = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(st.st_ctime))
    except Exception as e:
        meta["file_stat_error"] = str(e)

    # 2. ffprobe Detailed Container & Stream Analysis
    ffprobe_bin = find_ffprobe_executable()
    ffprobe_success = False

    if ffprobe_bin:
        try:
            cmd = [
                ffprobe_bin,
                "-v", "quiet",
                "-print_format", "json",
                "-show_format",
                "-show_streams",
                actual_path,
            ]
            res = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=2.5,
            )
            if res.returncode == 0 and res.stdout:
                data = json.loads(res.stdout)
                fmt = data.get("format", {})
                meta["format_name"] = fmt.get("format_long_name", fmt.get("format_name", ""))
                overall_br = int(fmt.get("bit_rate", 0))
                meta["overall_bitrate"] = overall_br
                if overall_br > 0:
                    meta["overall_bitrate_str"] = f"{overall_br / 1_000_000.0:.2f} Mbps ({overall_br // 1000:,} kbps)"
                meta["duration_sec"] = float(fmt.get("duration", 0.0))
                meta["format_tags"] = fmt.get("tags", {})

                for stream in data.get("streams", []):
                    stype = stream.get("codec_type")
                    if stype == "video" and "video_codec" not in meta:
                        meta["video_codec"] = stream.get("codec_long_name", stream.get("codec_name", ""))
                        meta["video_codec_short"] = stream.get("codec_name", "")
                        meta["video_codec_tag"] = stream.get("codec_tag_string", "")
                        meta["video_profile"] = stream.get("profile", "")
                        meta["video_level"] = str(stream.get("level", ""))
                        meta["pixel_format"] = stream.get("pix_fmt", "")
                        meta["color_space"] = stream.get("color_space", "")
                        meta["color_primaries"] = stream.get("color_primaries", "")
                        meta["color_transfer"] = stream.get("color_transfer", "")
                        meta["color_range"] = "TV (Limited 16-235)" if stream.get("color_range") == "tv" else ("PC (Full 0-255)" if stream.get("color_range") == "pc" else stream.get("color_range", ""))
                        v_br = int(stream.get("bit_rate", 0))
                        meta["video_bitrate"] = v_br
                        if v_br > 0:
                            meta["video_bitrate_str"] = f"{v_br / 1_000_000.0:.2f} Mbps ({v_br // 1000:,} kbps)"
                        meta["width"] = int(stream.get("width", 0))
                        meta["height"] = int(stream.get("height", 0))
                        meta["bits_per_sample"] = stream.get("bits_per_raw_sample", "")
                        meta["nb_frames"] = int(stream.get("nb_frames", 0))
                        meta["fps_str"] = stream.get("avg_frame_rate", "")
                        meta["stream_tags"] = stream.get("tags", {})
                        meta["field_order"] = stream.get("field_order", "progressive")
                        ffprobe_success = True

                    elif stype == "audio" and "audio_codec" not in meta:
                        meta["audio_codec"] = stream.get("codec_long_name", stream.get("codec_name", ""))
                        meta["audio_sample_rate"] = stream.get("sample_rate", "")
                        meta["audio_channels"] = stream.get("channels", 0)
                        meta["audio_channel_layout"] = stream.get("channel_layout", "")
                        a_br = int(stream.get("bit_rate", 0))
                        meta["audio_bitrate"] = a_br
                        if a_br > 0:
                            meta["audio_bitrate_str"] = f"{a_br // 1000} kbps"
        except Exception as e:
            meta["ffprobe_error"] = str(e)

    # 3. PyAV & OpenCV Fallback for Video Files if ffprobe unavailable or partial
    from openreview.core.video_reader import is_video_file, get_video_codec_info, HAS_AV
    from openreview.core.image_io import is_image_file

    if not ffprobe_success and is_video_file(actual_path):
        if HAS_AV:
            try:
                import av
                with av.open(actual_path) as container:
                    if container.streams.video:
                        v_st = container.streams.video[0]
                        ctx = v_st.codec_context
                        if "width" not in meta or not meta["width"]:
                            meta["width"] = int(ctx.width or v_st.width or 0)
                        if "height" not in meta or not meta["height"]:
                            meta["height"] = int(ctx.height or v_st.height or 0)
                        rate = v_st.guessed_rate or v_st.average_rate
                        if "fps" not in meta and rate:
                            meta["fps"] = float(rate)
                        if "nb_frames" not in meta or not meta["nb_frames"]:
                            if v_st.frames and v_st.frames > 0:
                                meta["nb_frames"] = v_st.frames
                            elif container.duration:
                                meta["nb_frames"] = max(1, int(round((container.duration / av.time_base) * (meta.get("fps") or 24.0))))
                        if "video_codec" not in meta:
                            codec_name = ctx.name or ""
                            dec_name, family_desc = get_video_codec_info(codec_name)
                            meta["video_codec"] = f"{family_desc} ({codec_name})"
                            meta["video_codec_short"] = dec_name
                            meta["video_codec_tag"] = getattr(ctx, "codec_tag", "") or codec_name
                        if "pixel_format" not in meta and ctx.pix_fmt:
                            meta["pixel_format"] = str(ctx.pix_fmt)
                        if "video_profile" not in meta and ctx.profile:
                            meta["video_profile"] = str(ctx.profile)
                    if container.streams.audio and "audio_codec" not in meta:
                        a_st = container.streams.audio[0]
                        a_ctx = a_st.codec_context
                        meta["audio_codec"] = a_ctx.name or ""
                        meta["audio_sample_rate"] = str(a_ctx.sample_rate or "")
                        meta["audio_channels"] = a_ctx.channels or 0
                    if container.bit_rate and "overall_bitrate_str" not in meta:
                        meta["overall_bitrate_str"] = f"{container.bit_rate // 1000} kbps"
            except Exception:
                pass

    if not ffprobe_success and HAS_CV2 and is_video_file(actual_path) and ("width" not in meta or not meta["width"]):
        try:
            cap = cv2.VideoCapture(actual_path)
            if cap.isOpened():
                if "width" not in meta or not meta["width"]:
                    meta["width"] = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
                if "height" not in meta or not meta["height"]:
                    meta["height"] = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
                if "nb_frames" not in meta or not meta["nb_frames"]:
                    meta["nb_frames"] = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
                if "fps" not in meta:
                    meta["fps"] = float(cap.get(cv2.CAP_PROP_FPS))
                fourcc_int = int(cap.get(cv2.CAP_PROP_FOURCC))
                if fourcc_int > 0 and "video_codec" not in meta:
                    fourcc_str = "".join([chr((fourcc_int >> 8 * i) & 0xFF) for i in range(4)]).strip("\x00")
                    dec_name, family_desc = get_video_codec_info(fourcc_str)
                    meta["video_codec"] = f"{family_desc} ({fourcc_str})"
                    meta["video_codec_short"] = dec_name
                    meta["video_codec_tag"] = fourcc_str
                br_kbps = cap.get(cv2.CAP_PROP_BITRATE)
                if br_kbps > 0 and "overall_bitrate_str" not in meta:
                    meta["overall_bitrate_str"] = f"{br_kbps:.0f} kbps"
            cap.release()
        except Exception:
            pass

    # 4. Pillow Metadata for Still Images and Image Sequences
    if HAS_PIL and is_image_file(actual_path):
        try:
            with Image.open(actual_path) as img:
                meta["image_format"] = img.format or meta["extension"][1:].upper()
                meta["image_mode"] = img.mode
                if "width" not in meta or not meta["width"]:
                    meta["width"], meta["height"] = img.size
                if hasattr(img, "info") and img.info:
                    meta["image_info_tags"] = {
                        str(k): str(v) for k, v in img.info.items() if len(str(v)) < 500 and k != "exif"
                    }
                if hasattr(img, "_getexif") and callable(img._getexif):
                    try:
                        raw_exif = img._getexif()
                        if raw_exif:
                            from PIL.ExifTags import TAGS
                            exif_tags = {}
                            for ek, ev in raw_exif.items():
                                tag_name = TAGS.get(ek, str(ek))
                                if len(str(ev)) < 500:
                                    exif_tags[tag_name] = str(ev)
                            meta["exif_tags"] = exif_tags
                    except Exception:
                        pass
        except Exception:
            pass

    # 5. OpenEXR Header Metadata & Attributes
    ext = meta.get("extension", "")
    if ext == ".exr" or (actual_path and str(actual_path).lower().endswith(".exr")):
        try:
            from openreview.core.exr_contact_sheet import extract_exr_detailed_metadata
            exr_data = extract_exr_detailed_metadata(actual_path)
            if exr_data:
                meta["exr_details"] = exr_data
                if exr_data.get("display_window"):
                    disp = exr_data["display_window"]
                    meta["width"] = int(disp[2] - disp[0] + 1)
                    meta["height"] = int(disp[3] - disp[1] + 1)
                if exr_data.get("pixel_aspect"):
                    meta["pixel_aspect"] = float(exr_data["pixel_aspect"])
                if exr_data.get("compression") and exr_data["compression"] != "Unknown":
                    meta["video_codec"] = f"EXR ({exr_data['compression']})"
                    meta["video_codec_short"] = str(exr_data["compression"])
        except Exception as e:
            meta["exr_error"] = str(e)

    # 6. Aggregate unified metadata tags
    all_tags = {}
    if "exr_details" in meta and meta["exr_details"]:
        ed = meta["exr_details"]
        if ed.get("data_window"):
            dw = ed["data_window"]
            all_tags["exr:dataWindow"] = f"[{dw[0]}, {dw[1]}] -> [{dw[2]}, {dw[3]}] ({dw[2] - dw[0] + 1}x{dw[3] - dw[1] + 1})"
        if ed.get("display_window"):
            disp = ed["display_window"]
            all_tags["exr:displayWindow"] = f"[{disp[0]}, {disp[1]}] -> [{disp[2]}, {disp[3]}] ({disp[2] - disp[0] + 1}x{disp[3] - disp[1] + 1})"
        if ed.get("compression"):
            all_tags["exr:compression"] = str(ed["compression"])
        if ed.get("line_order"):
            all_tags["exr:lineOrder"] = str(ed["line_order"])
        if ed.get("pixel_aspect"):
            all_tags["exr:pixelAspectRatio"] = f"{ed['pixel_aspect']:.4f}"
        if ed.get("channels"):
            all_tags["exr:channels"] = f"{len(ed['channels'])} channels ({', '.join(c['name'] for c in ed['channels'][:10])}{'...' if len(ed['channels']) > 10 else ''})"
        if ed.get("layers"):
            all_tags["exr:layers"] = ", ".join(ed["layers"])
        if ed.get("cryptomatte_layers"):
            all_tags["exr:cryptomatte_layers"] = ", ".join(ed["cryptomatte_layers"])
        for ak, av in ed.get("attributes", {}).items():
            all_tags[ak] = str(av)

    if meta.get("format_tags"):
        for fk, fv in meta["format_tags"].items():
            all_tags[f"format:{fk}"] = str(fv)

    if meta.get("stream_tags"):
        for sk, sv in meta["stream_tags"].items():
            all_tags[f"stream:{sk}"] = str(sv)

    if meta.get("image_info_tags"):
        for ik, iv in meta["image_info_tags"].items():
            all_tags[ik] = str(iv)

    if meta.get("exif_tags"):
        for ek, ev in meta["exif_tags"].items():
            all_tags[f"exif:{ek}"] = str(ev)

    meta["all_metadata_tags"] = all_tags
    return meta
