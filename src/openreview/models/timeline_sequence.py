"""
Timeline Sequence and Multi-Track Editorial Data Models.
Provides:
- Track models (Video, Audio, Soft Effects / Adjustment)
- ClipItem model with source/destination in/out, retime, colorspace, reel, version, tags, and soft effects
- SoftEffect model (Grade/CDL, Transform, Burn-in, Retime, Mask)
- TimelineSequence container with OpenTimelineIO (.otio) serialization and deserialization
"""
from __future__ import annotations

import os
import re
import json
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Any, Tuple

logger = logging.getLogger(__name__)


class TrackType(str, Enum):
    VIDEO = "video"
    AUDIO = "audio"
    FX = "fx"


class SoftEffectType(str, Enum):
    GRADE_CDL = "grade_cdl"
    TRANSFORM = "transform"
    BURNIN = "burnin"
    RETIME = "retime"
    MASK = "mask"
    OPACITY = "opacity"


class ShotStatus(str, Enum):
    MATCHED = "MATCHED"
    MISSING = "MISSING"
    CONFLICT = "CONFLICT"
    APPROVED = "APPROVED"
    WIP = "WIP"
    HOLD = "HOLD"
    OMIT = "OMIT"


@dataclass
class SoftEffect:
    """Non-destructive real-time GPU/shader modifier applied to clip or track."""
    id: str
    effect_type: SoftEffectType
    name: str = ""
    enabled: bool = True
    collapsed: bool = False
    parameters: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "effect_type": self.effect_type.value if isinstance(self.effect_type, SoftEffectType) else str(self.effect_type),
            "name": self.name or self.effect_type.value,
            "enabled": self.enabled,
            "collapsed": self.collapsed,
            "parameters": dict(self.parameters),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SoftEffect":
        eff_type_val = data.get("effect_type", SoftEffectType.GRADE_CDL.value)
        try:
            eff_type = SoftEffectType(eff_type_val)
        except ValueError:
            eff_type = SoftEffectType.GRADE_CDL
        return cls(
            id=data.get("id", ""),
            effect_type=eff_type,
            name=data.get("name", ""),
            enabled=data.get("enabled", True),
            collapsed=data.get("collapsed", False),
            parameters=data.get("parameters", {}),
        )


@dataclass
class ClipItem:
    """An editorial clip placed on a timeline track."""
    id: str
    name: str
    file_path: str
    src_in: int = 1001
    src_out: int = 1048
    dst_in: int = 1
    dst_out: int = 48
    speed: float = 1.0
    reel: str = ""
    colorspace: str = "ACEScg"
    status: ShotStatus = ShotStatus.MATCHED
    version: str = "v01"
    tags: List[str] = field(default_factory=list)
    soft_effects: List[SoftEffect] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    link_id: Optional[str] = None

    @property
    def src_duration(self) -> int:
        return max(1, self.src_out - self.src_in + 1)

    @property
    def dst_duration(self) -> int:
        return max(1, self.dst_out - self.dst_in + 1)

    @property
    def duration(self) -> int:
        """Convenience alias for dst_duration."""
        return self.dst_duration

    def contains_dst_frame(self, frame: int) -> bool:
        return self.dst_in <= frame <= self.dst_out

    def get_media_source_bounds(self) -> Tuple[int, int]:
        """
        Return the full source media boundary (original_src_in, original_src_out).
        Checks clip metadata, or dynamically probes media file / image sequence on disk.
        """
        if "original_src_in" in self.metadata and "original_src_out" in self.metadata:
            return int(self.metadata["original_src_in"]), int(self.metadata["original_src_out"])
        if "media_duration" in self.metadata:
            dur = int(self.metadata["media_duration"])
            orig_in = 1
            orig_out = dur
            self.metadata["original_src_in"] = orig_in
            self.metadata["original_src_out"] = orig_out
            return orig_in, orig_out

        orig_in = 1
        orig_out = max(self.src_out, self.src_in)

        if self.file_path:
            try:
                import os
                from openreview.core.video_reader import is_video_file, VideoMediaReader
                from openreview.core.sequence_parser import detect_image_sequence

                if is_video_file(self.file_path):
                    vr = VideoMediaReader(self.file_path)
                    orig_in = 1
                    orig_out = max(1, vr.total_frames)
                    vr.close()
                else:
                    seq_info = detect_image_sequence(self.file_path)
                    if seq_info and len(seq_info.available_frames) > 1:
                        orig_in = seq_info.start_frame
                        orig_out = seq_info.end_frame
                    else:
                        _, ext = os.path.splitext(self.file_path.lower())
                        if ext in {".wav", ".mp3", ".aac", ".flac", ".ogg", ".aif", ".aiff", ".m4a"}:
                            try:
                                import wave
                                with wave.open(self.file_path, "r") as wf:
                                    frames = wf.getnframes()
                                    rate = wf.getframerate()
                                    dur_sec = frames / float(rate) if rate else 0
                                    orig_in = 1
                                    orig_out = max(1, int(round(dur_sec * 24.0)))
                            except Exception:
                                pass
                self.metadata["original_src_in"] = orig_in
                self.metadata["original_src_out"] = orig_out
                self.metadata["media_duration"] = max(1, orig_out - orig_in + 1)
            except Exception:
                pass

        return orig_in, orig_out

    def map_dst_to_src_frame(self, dst_frame: int) -> int:
        """Map sequence destination frame to clip source frame considering speed."""
        offset = dst_frame - self.dst_in
        return int(round(self.src_in + (offset * self.speed)))

    def trim_head(self, delta_frames: int) -> int:
        """
        Trim or extend the in-point of the clip.
        Positive delta trims head (makes clip shorter, moves dst_in and src_in right).
        Negative delta extends head (makes clip longer, moves dst_in and src_in left).
        Returns actual delta applied (preventing duration < 1).
        """
        max_trim = self.dst_duration - 1
        applied = min(max_trim, delta_frames)
        self.dst_in += applied
        self.src_in += applied
        return applied

    def trim_tail(self, delta_frames: int) -> int:
        """
        Trim or extend the out-point of the clip.
        Positive delta extends tail (makes clip longer, moves dst_out and src_out right).
        Negative delta trims tail (makes clip shorter, moves dst_out and src_out left).
        Returns actual delta applied (preventing duration < 1).
        """
        min_delta = -(self.dst_duration - 1)
        applied = max(min_delta, delta_frames)
        self.dst_out += applied
        self.src_out += applied
        return applied

    def slip(self, delta_frames: int) -> None:
        """
        Slip source media frames without changing timeline duration or placement.
        Positive delta shifts source media forwards in time.
        Negative delta shifts source media backwards.
        """
        self.src_in += delta_frames
        self.src_out += delta_frames

    def version_up(self) -> Optional[str]:
        """Bump clip file path version if pattern is recognized and exists."""
        from openreview.core.version_control import bump_version_string
        new_name, new_path = bump_version_string(self.name, self.file_path, direction=1)
        if new_path and os.path.exists(new_path):
            self.name = new_name
            self.file_path = new_path
            m = re.search(r'[vV](\d+)', self.name)
            if m:
                self.version = f"v{m.group(1)}"
            return self.file_path
        return None

    def version_down(self) -> Optional[str]:
        """Step clip file path down a version if exists."""
        from openreview.core.version_control import bump_version_string
        new_name, new_path = bump_version_string(self.name, self.file_path, direction=-1)
        if new_path and os.path.exists(new_path):
            self.name = new_name
            self.file_path = new_path
            m = re.search(r'[vV](\d+)', self.name)
            if m:
                self.version = f"v{m.group(1)}"
            return self.file_path
        return None

    def add_soft_effect(self, effect_type: SoftEffectType, name: str = "", params: Optional[Dict[str, Any]] = None) -> SoftEffect:
        effect_id = f"fx_{self.id}_{len(self.soft_effects) + 1}"
        eff = SoftEffect(
            id=effect_id,
            effect_type=effect_type,
            name=name or effect_type.value,
            enabled=True,
            parameters=params or {},
        )
        self.soft_effects.append(eff)
        return eff

    def remove_soft_effect(self, effect_id: str) -> bool:
        """Remove a soft effect from clip by effect ID."""
        for i, fx in enumerate(self.soft_effects):
            if fx.id == effect_id:
                self.soft_effects.pop(i)
                return True
        return False

    def move_soft_effect_up(self, index: int) -> bool:
        """Move soft effect up towards top of stack."""
        if 0 < index < len(self.soft_effects):
            self.soft_effects[index - 1], self.soft_effects[index] = self.soft_effects[index], self.soft_effects[index - 1]
            return True
        return False

    def move_soft_effect_down(self, index: int) -> bool:
        """Move soft effect down towards bottom of stack."""
        if 0 <= index < len(self.soft_effects) - 1:
            self.soft_effects[index + 1], self.soft_effects[index] = self.soft_effects[index], self.soft_effects[index + 1]
            return True
        return False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "file_path": self.file_path,
            "src_in": self.src_in,
            "src_out": self.src_out,
            "dst_in": self.dst_in,
            "dst_out": self.dst_out,
            "speed": self.speed,
            "reel": self.reel,
            "colorspace": self.colorspace,
            "status": self.status.value if isinstance(self.status, ShotStatus) else str(self.status),
            "version": self.version,
            "tags": list(self.tags),
            "soft_effects": [fx.to_dict() for fx in self.soft_effects],
            "metadata": dict(self.metadata),
            "link_id": self.link_id,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ClipItem":
        status_val = data.get("status", ShotStatus.MATCHED.value)
        try:
            status = ShotStatus(status_val)
        except ValueError:
            status = ShotStatus.MATCHED

        clip = cls(
            id=data.get("id", ""),
            name=data.get("name", "Untitled Clip"),
            file_path=data.get("file_path", ""),
            src_in=data.get("src_in", 1001),
            src_out=data.get("src_out", 1048),
            dst_in=data.get("dst_in", 1),
            dst_out=data.get("dst_out", 48),
            speed=data.get("speed", 1.0),
            reel=data.get("reel", ""),
            colorspace=data.get("colorspace", "ACEScg"),
            status=status,
            version=data.get("version", "v01"),
            tags=data.get("tags", []),
            metadata=data.get("metadata", {}),
            link_id=data.get("link_id", None),
        )
        for fx_data in data.get("soft_effects", []):
            clip.soft_effects.append(SoftEffect.from_dict(fx_data))
        return clip


@dataclass
class TimelineMarker:
    """Editorial Review Marker / Note flag on the timeline ruler."""
    frame: int
    name: str = "Note"
    comment: str = ""
    color: str = "#e5a823"       # #2ecc71 (Approved), #f1c40f (WIP), #e74c3c (Retake), #3498db (Client)
    category: str = "General"    # Approved, WIP, Retake, Client, General
    author: str = "Supervisor"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "frame": self.frame,
            "name": self.name,
            "comment": self.comment,
            "color": self.color,
            "category": self.category,
            "author": self.author,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TimelineMarker":
        return cls(
            frame=data.get("frame", 1),
            name=data.get("name", "Note"),
            comment=data.get("comment", ""),
            color=data.get("color", "#e5a823"),
            category=data.get("category", "General"),
            author=data.get("author", "Supervisor"),
        )


@dataclass
class Track:
    """Editorial track containing non-overlapping or layered clips."""
    id: str
    name: str
    track_type: TrackType = TrackType.VIDEO
    visible: bool = True
    locked: bool = False
    solo: bool = False
    mute: bool = False
    is_reference: bool = False
    blend_mode: str = "Normal"
    opacity: float = 1.0
    clips: List[ClipItem] = field(default_factory=list)

    def add_clip(self, clip: ClipItem) -> None:
        self.clips.append(clip)
        self.clips.sort(key=lambda c: c.dst_in)

    def remove_clip(self, clip_id: str, ripple: bool = False) -> Optional[ClipItem]:
        """Remove a clip from the track. If ripple=True, shift subsequent clips backwards by clip duration."""
        target_idx = -1
        for idx, c in enumerate(self.clips):
            if c.id == clip_id:
                target_idx = idx
                break
        if target_idx == -1:
            return None
        removed = self.clips.pop(target_idx)
        if ripple:
            shift = removed.dst_duration
            for c in self.clips[target_idx:]:
                c.dst_in -= shift
                c.dst_out -= shift
        return removed

    def get_clip_at_frame(self, frame: int) -> Optional[ClipItem]:
        for c in self.clips:
            if c.contains_dst_frame(frame):
                return c
        return None

    def get_clip_by_id(self, clip_id: str) -> Optional[ClipItem]:
        for c in self.clips:
            if c.id == clip_id:
                return c
        return None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "track_type": self.track_type.value if isinstance(self.track_type, TrackType) else str(self.track_type),
            "visible": self.visible,
            "locked": self.locked,
            "solo": self.solo,
            "mute": self.mute,
            "is_reference": self.is_reference,
            "blend_mode": self.blend_mode,
            "opacity": self.opacity,
            "clips": [c.to_dict() for c in self.clips],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Track":
        t_type = TrackType(data.get("track_type", TrackType.VIDEO.value))
        track = cls(
            id=data.get("id", ""),
            name=data.get("name", "Track"),
            track_type=t_type,
            visible=data.get("visible", True),
            locked=data.get("locked", False),
            solo=data.get("solo", False),
            mute=data.get("mute", False),
            is_reference=data.get("is_reference", False),
            blend_mode=data.get("blend_mode", "Normal"),
            opacity=data.get("opacity", 1.0),
        )
        for clip_data in data.get("clips", []):
            track.add_clip(ClipItem.from_dict(clip_data))
        return track


@dataclass
class TimelineSequence:
    """
    Multi-track sequence container.
    Supports OpenTimelineIO (.otio) import/export and JSON serialization.
    """
    id: str = "seq_default"
    name: str = "OpenReview Sequence"
    fps: float = 24.0
    width: int = 1920
    height: int = 1080
    start_timecode: str = "00:00:00:00"
    in_frame: int = 1
    out_frame: int = 1000
    in_mark: Optional[int] = None
    out_mark: Optional[int] = None
    markers: List[TimelineMarker] = field(default_factory=list)
    tracks: List[Track] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def duration(self) -> int:
        if not self.tracks:
            return max(1, self.out_frame - self.in_frame + 1)
        max_dst = self.out_frame
        for t in self.tracks:
            for c in t.clips:
                if c.dst_out > max_dst:
                    max_dst = c.dst_out
        return max(1, max_dst - self.in_frame + 1)

    def add_video_track(self, name: str = "") -> Track:
        idx = len([t for t in self.tracks if t.track_type == TrackType.VIDEO]) + 1
        t_name = name or f"Video {idx}"
        track = Track(id=f"v_track_{idx}_{len(self.tracks)}", name=t_name, track_type=TrackType.VIDEO)
        self.tracks.insert(0, track)  # Top-down compositing: highest video index first
        return track

    def add_audio_track(self, name: str = "") -> Track:
        idx = len([t for t in self.tracks if t.track_type == TrackType.AUDIO]) + 1
        t_name = name or f"Audio {idx}"
        track = Track(id=f"a_track_{idx}_{len(self.tracks)}", name=t_name, track_type=TrackType.AUDIO)
        self.tracks.append(track)
        return track

    def get_top_clip_at_frame(self, frame: int) -> Optional[Tuple[Track, ClipItem]]:
        """Find the top-most visible video clip covering destination frame, respecting additive solo and layering."""
        v_tracks = [t for t in self.tracks if t.track_type == TrackType.VIDEO and t.visible]
        if not v_tracks:
            return None
        # Additive solo check: if any video track is soloed, only consider soloed tracks
        has_solo = any(t.solo for t in v_tracks)
        if has_solo:
            v_tracks = [t for t in v_tracks if t.solo]

        # Top-down NLE layering: tracks are stored in top-down order (highest video track first)
        for t in v_tracks:
            clip = t.get_clip_at_frame(frame)
            if clip is not None:
                return t, clip
        return None

    def get_all_clips(self) -> List[Tuple[Track, ClipItem]]:
        """Return flat list of (Track, ClipItem) sorted chronologically."""
        items = []
        for t in self.tracks:
            for c in t.clips:
                items.append((t, c))
        items.sort(key=lambda pair: pair[1].dst_in)
        return items

    def remove_clip(self, clip_id: str, ripple: bool = False) -> Optional[Tuple[Track, ClipItem]]:
        """Remove a clip across tracks. If ripple=True, shift downstream clips backwards on that track."""
        for t in self.tracks:
            removed = t.remove_clip(clip_id, ripple=ripple)
            if removed is not None:
                return t, removed
        return None

    def get_clip_by_id(self, clip_id: str) -> Optional[Tuple[Track, ClipItem]]:
        """Find track and clip by clip ID across all tracks."""
        for t in self.tracks:
            c = t.get_clip_by_id(clip_id)
            if c is not None:
                return t, c
        return None

    def get_linked_clips(self, clip: ClipItem) -> List[Tuple[Track, ClipItem]]:
        """Return all other (Track, ClipItem) pairs in sequence linked to the given clip."""
        if not clip.link_id:
            return []
        linked = []
        for t in self.tracks:
            for c in t.clips:
                if c.id != clip.id and c.link_id == clip.link_id:
                    linked.append((t, c))
        return linked

    def get_active_audio_clip_at_frame(self, frame: int) -> Optional[Tuple[Track, ClipItem]]:
        """Find the active audible clip covering destination frame, respecting audio track solo/mute."""
        a_tracks = [t for t in self.tracks if t.track_type == TrackType.AUDIO]
        has_solo = any(t.solo for t in a_tracks)
        for t in a_tracks:
            if t.mute or (has_solo and not t.solo):
                continue
            clip = t.get_clip_at_frame(frame)
            if clip is not None:
                return t, clip
        # Fallback to video tracks with audio if no audio track clip exists
        v_tracks = [t for t in self.tracks if t.track_type == TrackType.VIDEO and t.visible]
        has_v_solo = any(t.solo for t in v_tracks)
        for t in v_tracks:
            if t.mute or (has_v_solo and not t.solo):
                continue
            clip = t.get_clip_at_frame(frame)
            if clip is not None:
                return t, clip
        return None

    def add_marker(self, frame: int, name: str = "Note", comment: str = "", color: str = "#e5a823", category: str = "General", author: str = "Supervisor") -> TimelineMarker:
        marker = TimelineMarker(frame=frame, name=name, comment=comment, color=color, category=category, author=author)
        self.markers.append(marker)
        self.markers.sort(key=lambda m: m.frame)
        return marker

    def remove_marker_at_frame(self, frame: int) -> bool:
        initial_len = len(self.markers)
        self.markers = [m for m in self.markers if m.frame != frame]
        return len(self.markers) < initial_len

    def get_marker_at_frame(self, frame: int) -> Optional[TimelineMarker]:
        for m in self.markers:
            if m.frame == frame:
                return m
        return None

    def get_reference_clip_at_frame(self, frame: int) -> Optional[Tuple[Track, ClipItem]]:
        """Return the clip from the designated editorial reference track at frame."""
        for t in self.tracks:
            if t.is_reference and t.visible:
                clip = t.get_clip_at_frame(frame)
                if clip is not None:
                    return t, clip
        return None

    def restore_clip_to_full_source(self, track: Track, clip: ClipItem) -> Tuple[int, int, int, int]:
        """
        Restore trimmed head and tail of clip up to its full media source length,
        without colliding with adjacent clips on the same track or extending before sequence frame 1.
        Returns (new_dst_in, new_dst_out, new_src_in, new_src_out).
        """
        orig_src_in, orig_src_out = clip.get_media_source_bounds()

        # 1. Available head extension
        avail_head = max(0, clip.src_in - orig_src_in)
        max_left_shift_dst = max(0, clip.dst_in - 1)
        prev_clips = [c for c in track.clips if c.dst_out < clip.dst_in and c.id != clip.id]
        if prev_clips:
            closest_prev = max(prev_clips, key=lambda c: c.dst_out)
            max_left_shift_coll = max(0, clip.dst_in - (closest_prev.dst_out + 1))
            left_extend = min(avail_head, max_left_shift_dst, max_left_shift_coll)
        else:
            left_extend = min(avail_head, max_left_shift_dst)

        # 2. Available tail extension
        avail_tail = max(0, orig_src_out - clip.src_out)
        next_clips = [c for c in track.clips if c.dst_in > clip.dst_out and c.id != clip.id]
        if next_clips:
            closest_next = min(next_clips, key=lambda c: c.dst_in)
            max_right_shift_coll = max(0, (closest_next.dst_in - 1) - clip.dst_out)
            right_extend = min(avail_tail, max_right_shift_coll)
        else:
            right_extend = avail_tail

        new_dst_in = clip.dst_in - left_extend
        new_src_in = clip.src_in - left_extend
        new_dst_out = clip.dst_out + right_extend
        new_src_out = clip.src_out + right_extend

        return new_dst_in, new_dst_out, new_src_in, new_src_out

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "fps": self.fps,
            "width": self.width,
            "height": self.height,
            "start_timecode": self.start_timecode,
            "in_frame": self.in_frame,
            "out_frame": self.out_frame,
            "in_mark": self.in_mark,
            "out_mark": self.out_mark,
            "markers": [m.to_dict() for m in self.markers],
            "tracks": [t.to_dict() for t in self.tracks],
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TimelineSequence":
        seq = cls(
            id=data.get("id", "seq_default"),
            name=data.get("name", "OpenReview Sequence"),
            fps=data.get("fps", 24.0),
            width=data.get("width", 1920),
            height=data.get("height", 1080),
            start_timecode=data.get("start_timecode", "00:00:00:00"),
            in_frame=data.get("in_frame", 1),
            out_frame=data.get("out_frame", 1000),
            in_mark=data.get("in_mark"),
            out_mark=data.get("out_mark"),
            metadata=data.get("metadata", {}),
        )
        for m_data in data.get("markers", []):
            seq.markers.append(TimelineMarker.from_dict(m_data))
        for t_data in data.get("tracks", []):
            seq.tracks.append(Track.from_dict(t_data))
        return seq

    def save_json(self, file_path: str) -> None:
        """Persist sequence to JSON file."""
        os.makedirs(os.path.dirname(os.path.abspath(file_path)), exist_ok=True)
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=2)

    @classmethod
    def load_json(cls, file_path: str) -> "TimelineSequence":
        """Load sequence from JSON file."""
        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return cls.from_dict(data)

    def to_otio_dict(self) -> Dict[str, Any]:
        """
        Export OpenTimelineIO compatible dictionary structure.
        Compatible with otio schema 'Timeline.1'.
        """
        tracks_otio = []
        for t in self.tracks:
            items_otio = []
            cur_time = self.in_frame
            for c in t.clips:
                # Add gap if there is empty space before clip
                if c.dst_in > cur_time:
                    gap_dur = c.dst_in - cur_time
                    items_otio.append({
                        "OTIO_SCHEMA": "Gap.1",
                        "name": "Gap",
                        "source_range": {
                            "OTIO_SCHEMA": "TimeRange.1",
                            "start_time": {"OTIO_SCHEMA": "RationalTime.1", "rate": self.fps, "value": 0},
                            "duration": {"OTIO_SCHEMA": "RationalTime.1", "rate": self.fps, "value": gap_dur},
                        }
                    })
                items_otio.append({
                    "OTIO_SCHEMA": "Clip.1",
                    "name": c.name,
                    "source_range": {
                        "OTIO_SCHEMA": "TimeRange.1",
                        "start_time": {"OTIO_SCHEMA": "RationalTime.1", "rate": self.fps, "value": c.src_in},
                        "duration": {"OTIO_SCHEMA": "RationalTime.1", "rate": self.fps, "value": c.src_duration},
                    },
                    "media_reference": {
                        "OTIO_SCHEMA": "ExternalReference.1",
                        "target_url": c.file_path,
                    },
                    "metadata": {
                        "openreview": {
                            "reel": c.reel,
                            "colorspace": c.colorspace,
                            "status": c.status.value if isinstance(c.status, ShotStatus) else str(c.status),
                            "version": c.version,
                            "tags": c.tags,
                            "soft_effects": [fx.to_dict() for fx in c.soft_effects],
                        }
                    }
                })
                cur_time = c.dst_out + 1

            tracks_otio.append({
                "OTIO_SCHEMA": "Track.1",
                "name": t.name,
                "kind": "Video" if t.track_type == TrackType.VIDEO else "Audio",
                "children": items_otio,
            })

        return {
            "OTIO_SCHEMA": "Timeline.1",
            "name": self.name,
            "global_start_time": {"OTIO_SCHEMA": "RationalTime.1", "rate": self.fps, "value": self.in_frame},
            "tracks": {
                "OTIO_SCHEMA": "Stack.1",
                "name": "tracks",
                "children": tracks_otio,
            },
            "metadata": {
                "openreview": {
                    "width": self.width,
                    "height": self.height,
                    "start_timecode": self.start_timecode,
                }
            }
        }

    def export_otio(self, file_path: str) -> None:
        """Export to .otio file."""
        os.makedirs(os.path.dirname(os.path.abspath(file_path)), exist_ok=True)
        data = self.to_otio_dict()
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

    to_otio = to_otio_dict

    @classmethod
    def import_otio(cls, file_path: str) -> "TimelineSequence":
        """Import from .otio or .otiod JSON file."""
        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        name = data.get("name", "Imported OTIO Sequence")
        global_start = data.get("global_start_time", {})
        fps = float(global_start.get("rate", 24.0)) if global_start else 24.0
        start_frame = int(global_start.get("value", 1)) if global_start else 1

        seq_meta = data.get("metadata", {}).get("openreview", {})
        width = int(seq_meta.get("width", 1920))
        height = int(seq_meta.get("height", 1080))
        tc = seq_meta.get("start_timecode", "00:00:00:00")

        seq = cls(
            name=name,
            fps=fps,
            width=width,
            height=height,
            start_timecode=tc,
            in_frame=start_frame,
            out_frame=start_frame + 200,
        )

        stack = data.get("tracks", {})
        tracks_list = stack.get("children", [])
        for t_idx, otio_track in enumerate(tracks_list):
            kind = otio_track.get("kind", "Video").lower()
            t_type = TrackType.VIDEO if kind == "video" else TrackType.AUDIO
            track = Track(
                id=f"otio_track_{t_idx}",
                name=otio_track.get("name", f"Track {t_idx+1}"),
                track_type=t_type,
            )

            current_dst = start_frame
            for child in otio_track.get("children", []):
                schema = child.get("OTIO_SCHEMA", "")
                s_range = child.get("source_range", {})
                duration = int(s_range.get("duration", {}).get("value", 0))

                if "Gap" in schema:
                    current_dst += duration
                    continue

                if "Clip" in schema:
                    clip_name = child.get("name", "Clip")
                    start_val = int(s_range.get("start_time", {}).get("value", 0))
                    media_ref = child.get("media_reference", {})
                    target_url = media_ref.get("target_url", "")
                    meta = child.get("metadata", {}).get("openreview", {})

                    clip = ClipItem(
                        id=f"clip_{t_idx}_{len(track.clips)+1}",
                        name=clip_name,
                        file_path=target_url,
                        src_in=start_val,
                        src_out=start_val + max(0, duration - 1),
                        dst_in=current_dst,
                        dst_out=current_dst + max(0, duration - 1),
                        reel=meta.get("reel", ""),
                        colorspace=meta.get("colorspace", "ACEScg"),
                        version=meta.get("version", "v01"),
                        tags=meta.get("tags", []),
                    )
                    for fx_dict in meta.get("soft_effects", []):
                        clip.soft_effects.append(SoftEffect.from_dict(fx_dict))
                    track.add_clip(clip)
                    current_dst += duration

            seq.tracks.append(track)

        if seq.tracks:
            seq.out_frame = max(seq.out_frame, seq.duration + seq.in_frame - 1)

        return seq
