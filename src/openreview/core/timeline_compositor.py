"""
Timeline Compositor and Real-Time Multi-Track Playback Engine.
Provides:
- Top-down multi-track composite evaluation at sequence destination frames
- Asynchronous and synchronous frame retrieval for video tracks
- Soft Effects stack evaluation (CDL Grade, Transform, Aspect Mask, Burn-in)
- High-performance LRU frame caching for real-time 24/60 fps playback
- Studio placeholder slate generator for conformed clips without offline media
"""
from __future__ import annotations

import os
import re
import queue
import logging
import threading
from typing import Optional, Tuple, Dict, Any, List

import cv2
import numpy as np
from PySide6.QtCore import Qt, QRect, QPoint
from PySide6.QtGui import QImage, QPainter, QColor, QFont, QPen, QBrush

from openreview.models.timeline_sequence import TimelineSequence, Track, ClipItem, SoftEffect, SoftEffectType
from openreview.color.lut_manager import AscCdl
from openreview.core.timecode import frame_to_timecode
from openreview.core.video_reader import is_video_file, VideoMediaReader

logger = logging.getLogger(__name__)


class TimelineCompositor:
    """
    Evaluates multi-track timeline sequences, compiles GLSL soft effects,
    and returns composited frame textures for real-time playback.
    """

    def __init__(self, cache_size: int = 192):
        self._cache: Dict[Tuple[str, int, str], np.ndarray] = {}
        self._cache_keys: List[Tuple[str, int, str]] = []
        self._cache_size = max(48, cache_size)
        self._video_readers: Dict[str, VideoMediaReader] = {}
        self._lock = threading.Lock()

        # Dedicated Asynchronous Background Prefetch Worker for Zero-Lag Playback
        self._prefetch_queue: queue.Queue = queue.Queue(maxsize=1)
        self._worker_running = True
        self._worker_thread = threading.Thread(target=self._prefetch_worker_loop, daemon=True)
        self._worker_thread.start()

    def clear_cache(self) -> None:
        with self._lock:
            self._cache.clear()
            self._cache_keys.clear()
            for vr in self._video_readers.values():
                try:
                    vr.close()
                except Exception:
                    pass
            self._video_readers.clear()

    def close(self) -> None:
        """Release all active decoders, terminate background worker, and clean cached buffers."""
        self._worker_running = False
        try:
            self._prefetch_queue.put_nowait(None)
        except Exception:
            pass
        self.clear_cache()

    def _conform_clip_to_canvas(self, frame_data: np.ndarray, target_w: int, target_h: int) -> np.ndarray:
        """Conform source plate to sequence canvas resolution, scaling to fit while preserving aspect ratio."""
        if frame_data is None or len(frame_data.shape) < 2:
            return frame_data

        src_h, src_w = frame_data.shape[:2]
        if src_w == target_w and src_h == target_h:
            return frame_data

        scale = min(target_w / float(src_w), target_h / float(src_h))
        fit_w = max(1, int(round(src_w * scale)))
        fit_h = max(1, int(round(src_h * scale)))

        # Fast OpenCV scaling
        resized = cv2.resize(frame_data, (fit_w, fit_h), interpolation=cv2.INTER_LINEAR)

        if fit_w == target_w and fit_h == target_h:
            return resized

        # Create padded canvas with centered letterbox / pillarbox
        channels = frame_data.shape[2] if len(frame_data.shape) > 2 else 1
        canvas_shape = (target_h, target_w, channels) if channels > 1 else (target_h, target_w)
        canvas = np.zeros(canvas_shape, dtype=frame_data.dtype)
        if channels == 4:
            if frame_data.dtype == np.float32 or frame_data.dtype == np.float64:
                canvas[..., 3] = 1.0
            else:
                canvas[..., 3] = 255

        off_x = (target_w - fit_w) // 2
        off_y = (target_h - fit_h) // 2
        canvas[off_y:off_y + fit_h, off_x:off_x + fit_w] = resized
        return canvas

    def _get_clip_effects_signature(self, clip: ClipItem) -> str:
        """Generate a deterministic signature of active soft effects and their parameters."""
        parts = []
        for fx in clip.soft_effects:
            if not fx.enabled:
                continue
            p_items = sorted((str(k), str(v)) for k, v in fx.parameters.items())
            p_str = ";".join(f"{k}={v}" for k, v in p_items)
            parts.append(f"{fx.id}:{fx.effect_type.value}:{p_str}")
        return "|".join(parts)

    def resolve_clip_frame_path(self, clip: ClipItem, src_frame: int) -> str:
        """Resolve patterned frame paths like shot.####.exr or shot.%04d.exr."""
        path = clip.file_path
        if not path:
            return ""

        # Video files don't need frame replacement in file path
        if is_video_file(path):
            return path

        # Replace #### with padded frame
        if "####" in path:
            return path.replace("####", f"{src_frame:04d}")
        if "#####" in path:
            return path.replace("#####", f"{src_frame:05d}")

        # Replace %04d printf formats
        m = re.search(r'%0(\d)d', path)
        if m:
            pad = int(m.group(1))
            return re.sub(r'%0\d+d', f"{src_frame:0{pad}d}", path)

        # Support concrete numbered files: e.g. shot.1001.exr -> shot.{src_frame:04d}.exr
        num_m = re.search(r'^(.*[^\d])(\d+)(\.[a-zA-Z0-9]+)$', path)
        if num_m:
            prefix, digits, ext = num_m.group(1), num_m.group(2), num_m.group(3)
            pad = len(digits)
            candidate = f"{prefix}{src_frame:0{pad}d}{ext}"
            if os.path.exists(candidate):
                return candidate

        return path

    def render_frame(
        self,
        sequence: TimelineSequence,
        dst_frame: int,
    ) -> Tuple[Optional[np.ndarray], Optional[AscCdl], Dict[str, Any]]:
        """
        Evaluate sequence at dst_frame.
        Returns:
            (frame_data, cdl_object, soft_effect_attributes)
        """
        top = sequence.get_top_clip_at_frame(dst_frame)
        if not top:
            # Empty frame
            return None, None, {}

        track, clip = top
        src_frame = clip.map_dst_to_src_frame(dst_frame)

        # 1. Soft Effects Evaluation & Parameter Parsing
        cdl = None
        aspect_mask = None
        burnin_text = None
        transform_params = {}
        extra_params_opacity = None

        for fx in clip.soft_effects:
            if not fx.enabled:
                continue
            if fx.effect_type == SoftEffectType.GRADE_CDL:
                s = float(fx.parameters.get("slope", 1.0))
                o = float(fx.parameters.get("offset", 0.0))
                p = float(fx.parameters.get("power", 1.0))
                sat = float(fx.parameters.get("saturation", 1.0))
                cdl = AscCdl(slope=(s, s, s), offset=(o, o, o), power=(p, p, p), saturation=sat, name=fx.name)
            elif fx.effect_type == SoftEffectType.MASK:
                aspect_mask = fx.parameters.get("aspect", "2.39:1 CinemaScope")
            elif fx.effect_type == SoftEffectType.BURNIN:
                template = fx.parameters.get("text", "{shot} | {version} | {src_tc}")
                src_tc = frame_to_timecode(src_frame, sequence.fps)
                dst_tc = frame_to_timecode(dst_frame, sequence.fps)
                burnin_text = (
                    template.replace("{shot}", clip.name)
                    .replace("{version}", clip.version)
                    .replace("{src_tc}", src_tc)
                    .replace("{dst_tc}", dst_tc)
                    .replace("{frame}", str(src_frame))
                    .replace("{src_f}", str(src_frame))
                    .replace("{dst_f}", str(dst_frame))
                    .replace("{reel}", clip.reel or "AX")
                    .replace("{track}", track.name)
                )
            elif fx.effect_type == SoftEffectType.TRANSFORM:
                transform_params = dict(fx.parameters)
            elif fx.effect_type == SoftEffectType.OPACITY:
                opacity_val = float(fx.parameters.get("opacity_percent", 100.0)) / 100.0
                blend_mode = str(fx.parameters.get("blend_mode", "Normal"))
                extra_params_opacity = {"opacity": opacity_val, "blend_mode": blend_mode}

        extra_params = {
            "track": track,
            "clip": clip,
            "src_frame": src_frame,
            "aspect_mask": aspect_mask,
            "burnin_text": burnin_text,
            "transform": transform_params,
            "opacity": extra_params_opacity,
        }

        # 2. Check Frame Cache with Effects Signature
        effects_sig = self._get_clip_effects_signature(clip)
        cache_key = (clip.id, src_frame, effects_sig)
        with self._lock:
            if cache_key in self._cache:
                return self._cache[cache_key], cdl, extra_params

        # 3. Read Media from Disk if exists
        real_path = self.resolve_clip_frame_path(clip, src_frame)
        frame_data = None

        if real_path and os.path.exists(real_path) and not os.path.isdir(real_path):
            try:
                if is_video_file(real_path):
                    # Automatic Hybrid Mode:
                    # Direct zero-copy uint8 for pure video playback, float32 only when CDL/soft effects are active
                    needs_float = cdl is not None or bool(transform_params)
                    with self._lock:
                        if real_path not in self._video_readers or getattr(self._video_readers[real_path], "_as_float", False) != needs_float:
                            if real_path in self._video_readers:
                                try:
                                    self._video_readers[real_path].close()
                                except Exception:
                                    pass
                            self._video_readers[real_path] = VideoMediaReader(real_path, as_float=needs_float)
                        vr = self._video_readers[real_path]
                    frame_data = vr.read_frame(src_frame)
                else:
                    from openreview.core.image_io import read_image_file
                    frame_data = read_image_file(real_path, as_f16=False)
            except Exception as e:
                logger.warning("[TimelineCompositor] Error reading %s: %s", real_path, e)

        # 4. Fallback: Generate Studio Preview Slate Plate
        if frame_data is None:
            frame_data = self._generate_studio_slate(
                sequence=sequence,
                track=track,
                clip=clip,
                dst_frame=dst_frame,
                src_frame=src_frame,
            )
        elif sequence.metadata.get("scale_to_fit_canvas", False):
            # Conform source media to sequence canvas resolution (Scale to Fit preserving aspect ratio)
            target_w = sequence.width or 1920
            target_h = sequence.height or 1080
            frame_data = self._conform_clip_to_canvas(frame_data, target_w, target_h)

        # 5. Non-destructive Soft Effects Processing Stack
        if frame_data is not None:
            frame_data = self._apply_soft_effects(
                frame_data=frame_data,
                sequence=sequence,
                track=track,
                clip=clip,
                src_frame=src_frame,
                dst_frame=dst_frame,
            )

        # 6. Insert into LRU Cache
        with self._lock:
            if len(self._cache_keys) >= self._cache_size:
                oldest_key = self._cache_keys.pop(0)
                self._cache.pop(oldest_key, None)
            self._cache[cache_key] = frame_data
            self._cache_keys.append(cache_key)

        return frame_data, cdl, extra_params

    def _apply_soft_effects(
        self,
        frame_data: np.ndarray,
        sequence: TimelineSequence,
        track: Track,
        clip: ClipItem,
        src_frame: int,
        dst_frame: int,
    ) -> np.ndarray:
        """Apply non-destructive real-time soft effects pipeline to image buffer."""
        if frame_data is None or len(frame_data.shape) < 2:
            return frame_data

        img = frame_data.copy()

        for fx in clip.soft_effects:
            if not fx.enabled:
                continue

            p = fx.parameters or {}

            if fx.effect_type == SoftEffectType.TRANSFORM:
                img = self._apply_transform_effect(img, p)
            elif fx.effect_type == SoftEffectType.MASK:
                img = self._apply_mask_crop_effect(img, p)
            elif fx.effect_type == SoftEffectType.BURNIN:
                img = self._apply_burnin_effect(img, p, sequence, track, clip, src_frame, dst_frame)
            elif fx.effect_type == SoftEffectType.OPACITY:
                img = self._apply_opacity_effect(img, p)

        return img

    def _apply_transform_effect(self, img: np.ndarray, p: Dict[str, Any]) -> np.ndarray:
        """Apply affine 2D transform (Scale X/Y, Pan X/Y, Rotation, Anchor, Flip H/V)."""
        h, w = img.shape[:2]
        if h <= 0 or w <= 0:
            return img

        # 1. Flips
        flip_h = bool(p.get("flip_h", False))
        flip_v = bool(p.get("flip_v", False))
        if flip_h and flip_v:
            img = cv2.flip(img, -1)
        elif flip_h:
            img = cv2.flip(img, 1)
        elif flip_v:
            img = cv2.flip(img, 0)

        # 2. Scale, Pan, Rotation, Anchor
        pan_x = float(p.get("translate_x", p.get("pan_x", 0.0)))
        pan_y = float(p.get("translate_y", p.get("pan_y", 0.0)))

        scale_val = float(p.get("scale", 1.0))
        scale_x = float(p.get("scale_x", scale_val))
        scale_y = float(p.get("scale_y", scale_val))

        rotation = float(p.get("rotation", 0.0))
        anchor_x = float(p.get("anchor_x", 0.5))
        anchor_y = float(p.get("anchor_y", 0.5))

        is_identity = (
            abs(pan_x) < 1e-4 and abs(pan_y) < 1e-4 and
            abs(scale_x - 1.0) < 1e-4 and abs(scale_y - 1.0) < 1e-4 and
            abs(rotation % 360.0) < 1e-4
        )
        if is_identity:
            return img

        cx = anchor_x * w
        cy = anchor_y * h

        rad = np.radians(rotation)
        cos_a = np.cos(rad)
        sin_a = np.sin(rad)

        T1 = np.array([[1.0, 0.0, -cx], [0.0, 1.0, -cy], [0.0, 0.0, 1.0]], dtype=np.float64)
        S = np.array([[scale_x, 0.0, 0.0], [0.0, scale_y, 0.0], [0.0, 0.0, 1.0]], dtype=np.float64)
        R = np.array([[cos_a, -sin_a, 0.0], [sin_a, cos_a, 0.0], [0.0, 0.0, 1.0]], dtype=np.float64)
        T2 = np.array([[1.0, 0.0, cx + pan_x], [0.0, 1.0, cy + pan_y], [0.0, 0.0, 1.0]], dtype=np.float64)

        M = (T2 @ R @ S @ T1)[:2, :]

        channels = img.shape[2] if len(img.shape) > 2 else 1
        border_val = (0, 0, 0, 0) if channels == 4 else (0, 0, 0)

        transformed = cv2.warpAffine(
            img,
            M,
            (w, h),
            flags=cv2.INTER_LINEAR,
            borderMode=cv2.BORDER_CONSTANT,
            borderValue=border_val,
        )
        return transformed

    def _apply_mask_crop_effect(self, img: np.ndarray, p: Dict[str, Any]) -> np.ndarray:
        """Apply Crop & Blanking bars (Aspect ratio preset or custom % edge crops)."""
        h, w = img.shape[:2]
        if h <= 0 or w <= 0:
            return img

        # Aspect preset
        aspect_str = str(p.get("aspect", "")).strip()
        aspect_ratio = None
        if "2.39" in aspect_str:
            aspect_ratio = 2.39
        elif "1.85" in aspect_str:
            aspect_ratio = 1.85
        elif "1.78" in aspect_str or "16:9" in aspect_str:
            aspect_ratio = 16.0 / 9.0
        elif "1.33" in aspect_str or "4:3" in aspect_str:
            aspect_ratio = 4.0 / 3.0
        elif "1.00" in aspect_str or "Square" in aspect_str:
            aspect_ratio = 1.0
        elif "9:16" in aspect_str or "Vertical" in aspect_str:
            aspect_ratio = 9.0 / 16.0

        if aspect_ratio is not None and aspect_ratio > 0:
            curr_aspect = w / float(h)
            if aspect_ratio > curr_aspect:
                target_h = w / aspect_ratio
                pad_y = int(round((h - target_h) / 2.0))
                if 0 < pad_y < h // 2:
                    img[:pad_y, :] = 0
                    img[h - pad_y:, :] = 0
            elif aspect_ratio < curr_aspect:
                target_w = h * aspect_ratio
                pad_x = int(round((w - target_w) / 2.0))
                if 0 < pad_x < w // 2:
                    img[:, :pad_x] = 0
                    img[:, w - pad_x:] = 0

        # Custom edge crops (percentages)
        crop_l = float(p.get("crop_left", 0.0))
        crop_r = float(p.get("crop_right", 0.0))
        crop_t = float(p.get("crop_top", 0.0))
        crop_b = float(p.get("crop_bottom", 0.0))

        if crop_l > 0.0:
            xl = int(round(w * (min(crop_l, 49.9) / 100.0)))
            img[:, :xl] = 0
        if crop_r > 0.0:
            xr = int(round(w * (1.0 - min(crop_r, 49.9) / 100.0)))
            img[:, xr:] = 0
        if crop_t > 0.0:
            yt = int(round(h * (min(crop_t, 49.9) / 100.0)))
            img[:yt, :] = 0
        if crop_b > 0.0:
            yb = int(round(h * (1.0 - min(crop_b, 49.9) / 100.0)))
            img[yb:, :] = 0

        return img

    def _apply_burnin_effect(
        self,
        img: np.ndarray,
        p: Dict[str, Any],
        sequence: TimelineSequence,
        track: Track,
        clip: ClipItem,
        src_frame: int,
        dst_frame: int,
    ) -> np.ndarray:
        """Apply burned-in editorial metadata text slate with anti-aliasing."""
        h, w = img.shape[:2]
        if h <= 0 or w <= 0:
            return img

        raw_template = str(p.get("text", "{shot} | {version} | {src_tc}"))
        src_tc = frame_to_timecode(src_frame, sequence.fps)
        dst_tc = frame_to_timecode(dst_frame, sequence.fps)

        burnin_text = (
            raw_template.replace("{shot}", clip.name)
            .replace("{version}", clip.version)
            .replace("{src_tc}", src_tc)
            .replace("{dst_tc}", dst_tc)
            .replace("{frame}", str(src_frame))
            .replace("{src_f}", str(src_frame))
            .replace("{dst_f}", str(dst_frame))
            .replace("{reel}", clip.reel or "AX")
            .replace("{track}", track.name)
        )

        placement = str(p.get("placement", "Bottom Center"))
        font_size = int(p.get("font_size", 18))
        opacity = float(p.get("opacity", 1.0))
        if opacity <= 0.0 or not burnin_text.strip():
            return img

        scale = max(0.4, (font_size / 22.0) * (h / 1080.0))
        thickness = max(1, int(round(2 * scale)))
        font = cv2.FONT_HERSHEY_SIMPLEX

        (tw, th), baseline = cv2.getTextSize(burnin_text, font, scale, thickness)
        margin_x = int(w * 0.05)
        margin_y = int(h * 0.05)

        if "Bottom" in placement:
            y = h - margin_y - baseline
        else:
            y = margin_y + th

        if "Left" in placement:
            x = margin_x
        elif "Right" in placement:
            x = max(10, w - margin_x - tw)
        else:
            x = max(10, (w - tw) // 2)

        pad = int(8 * scale)
        box_x1 = max(0, x - pad)
        box_y1 = max(0, y - th - pad)
        box_x2 = min(w, x + tw + pad)
        box_y2 = min(h, y + baseline + pad)

        is_float = (img.dtype == np.float32 or img.dtype == np.float64)
        bg_col = (0.05, 0.05, 0.05) if is_float else (15, 15, 15)
        text_col = (1.0, 1.0, 1.0) if is_float else (255, 255, 255)

        bg_opacity = 0.75 * opacity
        channels = img.shape[2] if len(img.shape) > 2 else 1

        sub_roi = img[box_y1:box_y2, box_x1:box_x2]
        if sub_roi.size > 0:
            if is_float:
                for c in range(min(3, channels)):
                    sub_roi[..., c] = sub_roi[..., c] * (1.0 - bg_opacity) + bg_col[c] * bg_opacity
            else:
                for c in range(min(3, channels)):
                    sub_roi[..., c] = (sub_roi[..., c].astype(np.float32) * (1.0 - bg_opacity) + bg_col[c] * bg_opacity).astype(np.uint8)

        cv2.putText(
            img,
            burnin_text,
            (x, y),
            font,
            scale,
            text_col,
            thickness,
            cv2.LINE_AA,
        )
        return img

    def _apply_opacity_effect(self, img: np.ndarray, p: Dict[str, Any]) -> np.ndarray:
        """Apply non-destructive clip opacity."""
        opacity_percent = float(p.get("opacity_percent", 100.0))
        if opacity_percent >= 99.9:
            return img

        factor = max(0.0, min(1.0, opacity_percent / 100.0))
        is_float = (img.dtype == np.float32 or img.dtype == np.float64)
        channels = img.shape[2] if len(img.shape) > 2 else 1

        if channels == 4:
            if is_float:
                img[..., 3] *= factor
            else:
                img[..., 3] = (img[..., 3].astype(np.float32) * factor).astype(np.uint8)
        else:
            if is_float:
                img *= factor
            else:
                img = (img.astype(np.float32) * factor).astype(np.uint8)

        return img

    def render_reference_frame(
        self,
        sequence: TimelineSequence,
        dst_frame: int,
    ) -> Optional[np.ndarray]:
        """
        Evaluate and render the designated editorial reference track at dst_frame for Viewport A/B compare.
        """
        ref_info = sequence.get_reference_clip_at_frame(dst_frame)
        if not ref_info:
            return None
        track, clip = ref_info
        src_frame = clip.map_dst_to_src_frame(dst_frame)
        cache_key = (f"ref_{clip.id}", src_frame, "")
        with self._lock:
            if cache_key in self._cache:
                return self._cache[cache_key]

        real_path = self.resolve_clip_frame_path(clip, src_frame)
        frame_data = None
        if real_path and os.path.exists(real_path) and not os.path.isdir(real_path):
            try:
                if is_video_file(real_path):
                    with self._lock:
                        if real_path not in self._video_readers:
                            self._video_readers[real_path] = VideoMediaReader(real_path, as_float=False)
                        vr = self._video_readers[real_path]
                    frame_data = vr.read_frame(src_frame)
                else:
                    from openreview.core.image_io import read_image_file
                    frame_data = read_image_file(real_path, as_f16=False)
            except Exception:
                pass
        if frame_data is None:
            frame_data = self._generate_studio_slate(sequence, track, clip, dst_frame, src_frame)

        with self._lock:
            if len(self._cache_keys) >= self._cache_size:
                oldest_key = self._cache_keys.pop(0)
                self._cache.pop(oldest_key, None)
            self._cache[cache_key] = frame_data
            self._cache_keys.append(cache_key)
        return frame_data

    def get_cached_dst_frames(self, sequence: TimelineSequence) -> set[int]:
        """Return the set of timeline destination frames currently cached in memory."""
        cached_dst: set[int] = set()
        if not sequence:
            return cached_dst
        with self._lock:
            for t in sequence.tracks:
                for c in t.clips:
                    sig = self._get_clip_effects_signature(c)
                    for f in range(c.dst_in, c.dst_out + 1):
                        src_f = c.map_dst_to_src_frame(f)
                        if (c.id, src_f, sig) in self._cache:
                            cached_dst.add(f)
        return cached_dst

    def _prefetch_worker_loop(self) -> None:
        """Asynchronous background worker loop that pre-decodes lookahead sequence frames."""
        while self._worker_running:
            try:
                task = self._prefetch_queue.get(timeout=0.2)
            except queue.Empty:
                continue

            if not self._worker_running or task is None:
                break

            seq, start_frame, count = task
            end_frame = start_frame + count
            for f in range(start_frame, end_frame):
                if not self._worker_running:
                    break
                # If a newer playhead prefetch request was enqueued, cancel this sweep immediately
                if not self._prefetch_queue.empty():
                    break

                top = seq.get_top_clip_at_frame(f)
                if not top:
                    continue
                _, clip = top
                src_frame = clip.map_dst_to_src_frame(f)
                sig = self._get_clip_effects_signature(clip)
                cache_key = (clip.id, src_frame, sig)

                with self._lock:
                    in_cache = cache_key in self._cache

                if not in_cache:
                    try:
                        self.render_frame(seq, f)
                    except Exception:
                        pass

    def request_prefetch(self, sequence: TimelineSequence, current_dst: int, count: int = 48) -> None:
        """Enqueue non-blocking asynchronous prefetch request for background decoding ahead of playhead."""
        if not sequence or not self._worker_running:
            return
        # Discard stale pending prefetch tasks
        try:
            while not self._prefetch_queue.empty():
                self._prefetch_queue.get_nowait()
        except Exception:
            pass
        try:
            self._prefetch_queue.put_nowait((sequence, current_dst + 1, count))
        except Exception:
            pass

    def prefetch_ahead(self, sequence: TimelineSequence, current_dst: int, count: int = 48) -> None:
        """Non-blocking prefetch delegator for smooth playback."""
        self.request_prefetch(sequence, current_dst, count=count)

    def _generate_studio_slate(
        self,
        sequence: TimelineSequence,
        track: Track,
        clip: ClipItem,
        dst_frame: int,
        src_frame: int,
    ) -> np.ndarray:
        """Render a high-resolution dark studio slate with metadata burn-in."""
        w = sequence.width or 1920
        h = sequence.height or 1080

        qimg = QImage(w, h, QImage.Format.Format_RGBA8888)
        qimg.fill(QColor(18, 20, 24))

        painter = QPainter(qimg)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        # Subtle dark editorial grid pattern
        painter.setPen(QColor(28, 32, 40))
        for x in range(0, w, 120):
            painter.drawLine(x, 0, x, h)
        for y in range(0, h, 120):
            painter.drawLine(0, y, w, y)

        # Central bounding frame
        margin_x = int(w * 0.08)
        margin_y = int(h * 0.08)
        frame_rect = QRect(margin_x, margin_y, w - 2 * margin_x, h - 2 * margin_y)
        painter.setPen(QPen(QColor(229, 168, 35, 180), 2))
        painter.drawRect(frame_rect)

        # Header Pill: Track & Version
        pill_rect = QRect(margin_x + 16, margin_y + 16, 260, 32)
        painter.setBrush(QBrush(QColor(30, 34, 42)))
        painter.setPen(QPen(QColor(229, 168, 35), 1))
        painter.drawRoundedRect(pill_rect, 4, 4)

        font_header = QFont("Segoe UI", 11, QFont.Weight.Bold)
        painter.setFont(font_header)
        painter.setPen(QColor(229, 168, 35))
        painter.drawText(pill_rect, Qt.AlignmentFlag.AlignCenter, f"{track.name}  |  {clip.version.upper()}")

        # Shot Name Center
        font_title = QFont("Segoe UI", 36, QFont.Weight.Bold)
        painter.setFont(font_title)
        painter.setPen(QColor(255, 255, 255))
        painter.drawText(frame_rect, Qt.AlignmentFlag.AlignCenter, clip.name)

        # Subtitle / Status
        font_sub = QFont("Segoe UI", 14)
        painter.setFont(font_sub)
        status_color = QColor(46, 204, 113) if clip.status.value == "MATCHED" else QColor(231, 76, 60)
        painter.setPen(status_color)
        sub_rect = QRect(margin_x, margin_y + int(frame_rect.height() * 0.6), frame_rect.width(), 40)
        painter.drawText(sub_rect, Qt.AlignmentFlag.AlignCenter, f"Status: {clip.status.value}  -  Colorspace: {clip.colorspace}")

        # Footer Burn-In: Source TC, Sequence TC, Frame numbers
        font_footer = QFont("Consolas", 12, QFont.Weight.Bold)
        painter.setFont(font_footer)
        painter.setPen(QColor(200, 200, 200))

        src_tc = frame_to_timecode(src_frame, sequence.fps)
        dst_tc = frame_to_timecode(dst_frame, sequence.fps)
        footer_text = f"SRC TC: {src_tc} (F:{src_frame})   |   SEQ TC: {dst_tc} (F:{dst_frame})   |   {w}x{h} @ {sequence.fps:.2f}fps"
        footer_rect = QRect(margin_x + 16, margin_y + frame_rect.height() - 40, frame_rect.width() - 32, 30)
        painter.drawText(footer_rect, Qt.AlignmentFlag.AlignCenter, footer_text)

        painter.end()

        # Convert QImage to NumPy RGBA float32 array in [0.0, 1.0]
        ptr = qimg.constBits()
        arr = np.frombuffer(ptr, dtype=np.uint8).reshape((h, w, 4))
        arr_float = (arr.astype(np.float32) / 255.0).copy()
        return arr_float
