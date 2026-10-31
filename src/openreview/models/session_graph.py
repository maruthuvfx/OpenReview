"""
Session Graph Data Models for OpenReview.
Provides hierarchical media management (Sources Bin), editorial sequences,
multi-layer image stacks with difference comparisons, and multi-tile layouts.
"""
from __future__ import annotations

import os
import uuid
import math
from enum import Enum
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Tuple, Any
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from openreview.core.sequence_parser import detect_image_sequence
from openreview.core.video_reader import is_video_file, VideoMediaReader
from openreview.core.metadata import extract_media_metadata


class ViewType(str, Enum):
    SOURCE = "source"
    SEQUENCE = "sequence"
    STACK = "stack"
    LAYOUT = "layout"


@dataclass
class SourceItem:
    """Represents a media asset (video, image sequence, still, or audio) in the Sources bin."""
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    name: str = "Media"
    path: str = ""
    media_type: str = "video"  # "video", "sequence", "still", "audio"
    start_frame: int = 1001
    end_frame: int = 1200
    fps: float = 24.0
    width: int = 1920
    height: int = 1080
    colorspace: str = "linear"
    input_space: int = 0
    ocio_colorspace: Optional[str] = None
    codec: str = "Raw"
    parent_folder_id: Optional[str] = None
    orig_start_frame: Optional[int] = None
    orig_end_frame: Optional[int] = None
    manual_in: Optional[int] = None
    manual_out: Optional[int] = None
    is_proxy: bool = False
    proxy_resolved: bool = True
    premultiplied: Optional[bool] = None

    def __post_init__(self):
        if self.colorspace:
            from openreview.color.standard_colorspaces import COLORSPACE_BY_NAME, COLORSPACE_BY_ID
            cs_lower = str(self.colorspace).lower().strip()
            if cs_lower in COLORSPACE_BY_NAME:
                self.input_space = COLORSPACE_BY_NAME[cs_lower].id
                self.colorspace = COLORSPACE_BY_NAME[cs_lower].name
            elif "aces" in cs_lower:
                self.input_space = 0
                self.colorspace = "linear"
            elif "cineon" in cs_lower:
                self.input_space = 3
                self.colorspace = "Cineon"
            elif "709" in cs_lower:
                self.input_space = 2
                self.colorspace = "rec709"
            elif "raw" in cs_lower:
                self.input_space = 10
                self.colorspace = "Raw"
            elif "srgb" in cs_lower:
                self.input_space = 1
                self.colorspace = "sRGB"
            elif self.input_space is not None and self.input_space in COLORSPACE_BY_ID:
                self.colorspace = COLORSPACE_BY_ID[self.input_space].name
        elif self.input_space is not None:
            from openreview.color.standard_colorspaces import COLORSPACE_BY_ID
            if self.input_space in COLORSPACE_BY_ID:
                self.colorspace = COLORSPACE_BY_ID[self.input_space].name

        if self.orig_start_frame is None:
            self.orig_start_frame = self.start_frame
        if self.orig_end_frame is None:
            self.orig_end_frame = self.end_frame
        if self.premultiplied is None:
            ext = os.path.splitext(self.path)[1].lower() if self.path else ""
            if ext in (".png", ".jpg", ".jpeg", ".bmp"):
                self.premultiplied = False
            else:
                self.premultiplied = True

    def reset_in_out(self) -> None:
        """Reset clip in/out range back to original full media duration."""
        if self.orig_start_frame is not None:
            self.start_frame = self.orig_start_frame
        if self.orig_end_frame is not None:
            self.end_frame = self.orig_end_frame
        self.manual_in = None
        self.manual_out = None

    @property
    def file_path(self) -> str:
        """Alias for path attribute for seamless compatibility across readers and UI panels."""
        return self.path

    @property
    def duration(self) -> int:
        return max(1, self.end_frame - self.start_frame + 1)

    @property
    def frame_in(self) -> int:
        return self.start_frame

    @frame_in.setter
    def frame_in(self, value: int) -> None:
        self.start_frame = int(value)

    @property
    def frame_out(self) -> int:
        return self.end_frame

    @frame_out.setter
    def frame_out(self, value: int) -> None:
        self.end_frame = int(value)

    @property
    def resolution_str(self) -> str:
        return f"{self.width}x{self.height}" if self.width > 0 and self.height > 0 else "Unknown"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "path": self.path,
            "media_type": self.media_type,
            "start_frame": self.start_frame,
            "end_frame": self.end_frame,
            "orig_start_frame": self.orig_start_frame,
            "orig_end_frame": self.orig_end_frame,
            "manual_in": self.manual_in,
            "manual_out": self.manual_out,
            "fps": self.fps,
            "width": self.width,
            "height": self.height,
            "colorspace": self.colorspace,
            "input_space": int(self.input_space),
            "ocio_colorspace": self.ocio_colorspace,
            "codec": self.codec,
            "parent_folder_id": self.parent_folder_id,
            "is_proxy": self.is_proxy,
            "proxy_resolved": self.proxy_resolved,
            "premultiplied": bool(self.premultiplied),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> SourceItem:
        sf = int(data.get("start_frame", 1001))
        ef = int(data.get("end_frame", 1200))
        m_in = data.get("manual_in")
        m_out = data.get("manual_out")
        return cls(
            id=data.get("id", str(uuid.uuid4())),
            name=data.get("name", "Media"),
            path=data.get("path", ""),
            media_type=data.get("media_type", "video"),
            start_frame=sf,
            end_frame=ef,
            fps=float(data.get("fps", 24.0)),
            width=int(data.get("width", 1920)),
            height=int(data.get("height", 1080)),
            colorspace=data.get("colorspace", "linear"),
            input_space=int(data.get("input_space", 0)),
            ocio_colorspace=data.get("ocio_colorspace"),
            codec=data.get("codec", "Raw"),
            parent_folder_id=data.get("parent_folder_id"),
            orig_start_frame=int(data.get("orig_start_frame", sf)),
            orig_end_frame=int(data.get("orig_end_frame", ef)),
            manual_in=int(m_in) if m_in is not None else None,
            manual_out=int(m_out) if m_out is not None else None,
            is_proxy=bool(data.get("is_proxy", False)),
            proxy_resolved=bool(data.get("proxy_resolved", True)),
            premultiplied=data.get("premultiplied"),
        )


@dataclass
class FolderItem:
    """Represents a folder / bin in the Sources/Sequences/Stacks/Layouts hierarchy."""
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    name: str = "New Folder"
    parent_id: Optional[str] = None
    child_folder_ids: List[str] = field(default_factory=list)
    source_ids: List[str] = field(default_factory=list)  # contains IDs of items (sources, sequences, stacks, layouts)
    folder_type: str = "source"  # "source", "sequence", "stack", "layout"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "parent_id": self.parent_id,
            "child_folder_ids": list(self.child_folder_ids),
            "source_ids": list(self.source_ids),
            "folder_type": self.folder_type,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> FolderItem:
        return cls(
            id=data.get("id", str(uuid.uuid4())),
            name=data.get("name", "New Folder"),
            parent_id=data.get("parent_id"),
            child_folder_ids=list(data.get("child_folder_ids", [])),
            source_ids=list(data.get("source_ids", [])),
            folder_type=data.get("folder_type", "source"),
        )


@dataclass
class SequenceClip:
    """A clip placement inside an editorial SequenceView."""
    source_id: str
    trim_in: int = 1001
    trim_out: int = 1200

    @property
    def duration(self) -> int:
        return max(1, self.trim_out - self.trim_in + 1)

    @property
    def in_frame(self) -> int:
        return self.trim_in

    @property
    def out_frame(self) -> int:
        return self.trim_out

    @property
    def timeline_in(self) -> int:
        return getattr(self, "_timeline_in", self.trim_in)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source_id": self.source_id,
            "trim_in": self.trim_in,
            "trim_out": self.trim_out,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> SequenceClip:
        return cls(
            source_id=data["source_id"],
            trim_in=int(data.get("trim_in", 1001)),
            trim_out=int(data.get("trim_out", 1200)),
        )


@dataclass
class SequenceView:
    """An editorial sequence of clips assembled from sources."""
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    name: str = "Default Sequence"
    clips: List[SequenceClip] = field(default_factory=list)
    start_frame: int = 1001
    fps: float = 24.0
    parent_folder_id: Optional[str] = None
    manual_in: Optional[int] = None
    manual_out: Optional[int] = None

    @property
    def total_duration(self) -> int:
        return sum(c.duration for c in self.clips)

    @property
    def end_frame(self) -> int:
        if not self.clips:
            return self.start_frame
        return self.start_frame + self.total_duration - 1

    def resolve_frame(self, timeline_frame: int, sources_map: Dict[str, SourceItem]) -> Tuple[Optional[SourceItem], int]:
        """
        Map timeline frame into the specific SourceItem and source media frame.
        Returns (source_item, source_frame).
        """
        if not self.clips:
            return None, timeline_frame

        current = self.start_frame
        for clip in self.clips:
            clip_dur = clip.duration
            if current <= timeline_frame < current + clip_dur:
                offset = timeline_frame - current
                source_frame = clip.trim_in + offset
                source = sources_map.get(clip.source_id)
                return source, source_frame
            current += clip_dur

        # Clamp to last clip
        if self.clips:
            last_clip = self.clips[-1]
            source = sources_map.get(last_clip.source_id)
            return source, last_clip.trim_out
        return None, timeline_frame

    def move_clip(self, from_idx: int, to_idx: int) -> None:
        if 0 <= from_idx < len(self.clips) and 0 <= to_idx < len(self.clips):
            clip = self.clips.pop(from_idx)
            self.clips.insert(to_idx, clip)

    def remove_clip(self, index: int) -> None:
        if 0 <= index < len(self.clips):
            self.clips.pop(index)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "clips": [c.to_dict() for c in self.clips],
            "start_frame": self.start_frame,
            "fps": self.fps,
            "parent_folder_id": self.parent_folder_id,
            "manual_in": self.manual_in,
            "manual_out": self.manual_out,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> SequenceView:
        m_in = data.get("manual_in")
        m_out = data.get("manual_out")
        return cls(
            id=data.get("id", str(uuid.uuid4())),
            name=data.get("name", "Sequence"),
            clips=[SequenceClip.from_dict(c) for c in data.get("clips", [])],
            start_frame=int(data.get("start_frame", 1001)),
            fps=float(data.get("fps", 24.0)),
            parent_folder_id=data.get("parent_folder_id"),
            manual_in=int(m_in) if m_in is not None else None,
            manual_out=int(m_out) if m_out is not None else None,
        )


@dataclass
class StackView:
    """An image stack of media layers for A/B, difference, and composite comparison."""
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    name: str = "Default Stack"
    source_ids: List[str] = field(default_factory=list)
    index_a: int = 0
    index_b: int = 1
    current_layer_index: int = 0
    # compare_mode: "wipe", "switch", "split", "difference", "difference_inverted", "over", "add"
    compare_mode: str = "switch"
    wipe_position: float = 0.5
    wipe_angle_deg: float = 0.0
    diff_boost: float = 1.0
    parent_folder_id: Optional[str] = None
    stack_mix: float = 1.0
    channels_a: Tuple[bool, bool, bool, bool] = (True, True, True, True)
    manual_in: Optional[int] = None
    manual_out: Optional[int] = None

    def cycle_forward(self) -> int:
        """Cycle strictly between Layer A and Layer B in stack."""
        if not self.source_ids:
            self.current_layer_index = 0
            return 0
        if len(self.source_ids) == 1:
            self.current_layer_index = 0
            return 0
        # If currently at index_a, toggle to index_b if valid, otherwise toggle back to index_a
        if self.current_layer_index == self.index_a and 0 <= self.index_b < len(self.source_ids):
            self.current_layer_index = self.index_b
        else:
            self.current_layer_index = self.index_a if 0 <= self.index_a < len(self.source_ids) else 0
        return self.current_layer_index

    def cycle_backward(self) -> int:
        """Cycle strictly between Layer A and Layer B in stack."""
        return self.cycle_forward()

    def get_current_layer(self, sources_map: Dict[str, SourceItem]) -> Optional[SourceItem]:
        if 0 <= self.current_layer_index < len(self.source_ids):
            return sources_map.get(self.source_ids[self.current_layer_index])
        elif self.source_ids:
            self.current_layer_index = 0
            return sources_map.get(self.source_ids[0])
        return None

    def get_layer_a(self, sources_map: Dict[str, SourceItem]) -> Optional[SourceItem]:
        if 0 <= self.index_a < len(self.source_ids):
            return sources_map.get(self.source_ids[self.index_a])
        return None

    def get_layer_b(self, sources_map: Dict[str, SourceItem]) -> Optional[SourceItem]:
        if 0 <= self.index_b < len(self.source_ids):
            return sources_map.get(self.source_ids[self.index_b])
        return None

    def move_layer(self, from_idx: int, to_idx: int) -> None:
        if 0 <= from_idx < len(self.source_ids) and 0 <= to_idx < len(self.source_ids):
            sid = self.source_ids.pop(from_idx)
            self.source_ids.insert(to_idx, sid)

    def remove_layer(self, index: int) -> None:
        if 0 <= index < len(self.source_ids):
            self.source_ids.pop(index)
            if self.index_a >= len(self.source_ids):
                self.index_a = max(0, len(self.source_ids) - 1)
            if self.index_b >= len(self.source_ids):
                self.index_b = max(0, len(self.source_ids) - 1)
            if self.current_layer_index >= len(self.source_ids):
                self.current_layer_index = max(0, len(self.source_ids) - 1)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "source_ids": list(self.source_ids),
            "index_a": self.index_a,
            "index_b": self.index_b,
            "current_layer_index": self.current_layer_index,
            "compare_mode": self.compare_mode,
            "wipe_position": self.wipe_position,
            "wipe_angle_deg": self.wipe_angle_deg,
            "diff_boost": self.diff_boost,
            "parent_folder_id": self.parent_folder_id,
            "stack_mix": self.stack_mix,
            "channels_a": list(self.channels_a),
            "manual_in": self.manual_in,
            "manual_out": self.manual_out,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> StackView:
        ch_raw = data.get("channels_a", [True, True, True, True])
        ch_tuple = (bool(ch_raw[0]), bool(ch_raw[1]), bool(ch_raw[2]), bool(ch_raw[3])) if len(ch_raw) >= 4 else (True, True, True, True)
        m_in = data.get("manual_in")
        m_out = data.get("manual_out")
        return cls(
            id=data.get("id", str(uuid.uuid4())),
            name=data.get("name", "Default Stack"),
            source_ids=list(data.get("source_ids", [])),
            index_a=int(data.get("index_a", 0)),
            index_b=int(data.get("index_b", 1)),
            current_layer_index=int(data.get("current_layer_index", 0)),
            compare_mode=data.get("compare_mode", "switch"),
            wipe_position=float(data.get("wipe_position", 0.5)),
            wipe_angle_deg=float(data.get("wipe_angle_deg", 0.0)),
            diff_boost=float(data.get("diff_boost", 1.0)),
            parent_folder_id=data.get("parent_folder_id"),
            stack_mix=float(data.get("stack_mix", 1.0)),
            channels_a=ch_tuple,
            manual_in=int(m_in) if m_in is not None else None,
            manual_out=int(m_out) if m_out is not None else None,
        )


@dataclass
class LayoutView:
    """A multi-tile grid layout displaying multiple sources simultaneously."""
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    name: str = "Default Layout"
    source_ids: List[str] = field(default_factory=list)
    layout_type: str = "auto_grid"  # "auto_grid", "side_by_side", "over_under", "grid_2x2", "grid_3x3"
    scale_pct: int = 100  # 100, 50, 25, 10
    parent_folder_id: Optional[str] = None
    align_mode: str = "frame_1"  # "frame_1", "min_source", "native"
    manual_in: Optional[int] = None
    manual_out: Optional[int] = None

    def get_grid_dimensions(self) -> Tuple[int, int]:
        """Calculate (rows, cols) based on input count and layout type."""
        n = max(1, len(self.source_ids))
        if self.layout_type == "side_by_side":
            return 1, n
        if self.layout_type == "over_under":
            return n, 1
        if self.layout_type == "grid_2x2":
            return 2, 2
        if self.layout_type == "grid_3x3":
            return 3, 3

        # Auto Grid
        if n == 1:
            return 1, 1
        if n == 2:
            return 1, 2
        if n in (3, 4):
            return 2, 2
        if n in (5, 6):
            return 2, 3
        if n in (7, 8, 9):
            return 3, 3
        cols = math.ceil(math.sqrt(n))
        rows = math.ceil(n / cols)
        return rows, cols

    def get_timeline_range(self, sources_map: Dict[str, SourceItem]) -> Tuple[int, int]:
        """Return (start_frame, end_frame) for the layout timeline based on align_mode."""
        sources = [sources_map[sid] for sid in self.source_ids if sid in sources_map]
        if not sources:
            return 1, 100

        max_dur = max(s.duration for s in sources)
        min_start = min(s.start_frame for s in sources)

        if self.align_mode == "min_source":
            return min_start, min_start + max_dur - 1
        elif self.align_mode == "native":
            max_end = max(s.end_frame for s in sources)
            return min_start, max_end
        else:  # "frame_1" (default)
            return 1, max_dur

    def resolve_tile_frame(self, src: SourceItem, timeline_frame: int, sources_map: Dict[str, SourceItem]) -> int:
        """Resolve the source frame for a tile at the given timeline frame."""
        if self.align_mode == "native":
            return timeline_frame

        start_f, _ = self.get_timeline_range(sources_map)
        offset = timeline_frame - start_f
        tile_frame = src.start_frame + offset
        # Clamp to media bounds so shorter clips hold their last frame
        return max(src.start_frame, min(src.end_frame, tile_frame))

    def move_tile(self, from_idx: int, to_idx: int) -> None:
        if 0 <= from_idx < len(self.source_ids) and 0 <= to_idx < len(self.source_ids):
            sid = self.source_ids.pop(from_idx)
            self.source_ids.insert(to_idx, sid)

    def remove_tile(self, index: int) -> None:
        if 0 <= index < len(self.source_ids):
            self.source_ids.pop(index)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "source_ids": list(self.source_ids),
            "layout_type": self.layout_type,
            "scale_pct": self.scale_pct,
            "parent_folder_id": self.parent_folder_id,
            "align_mode": self.align_mode,
            "manual_in": self.manual_in,
            "manual_out": self.manual_out,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> LayoutView:
        m_in = data.get("manual_in")
        m_out = data.get("manual_out")
        return cls(
            id=data.get("id", str(uuid.uuid4())),
            name=data.get("name", "Layout"),
            source_ids=list(data.get("source_ids", [])),
            layout_type=data.get("layout_type", "auto_grid"),
            scale_pct=int(data.get("scale_pct", 100)),
            parent_folder_id=data.get("parent_folder_id"),
            align_mode=data.get("align_mode", "frame_1"),
            manual_in=int(m_in) if m_in is not None else None,
            manual_out=int(m_out) if m_out is not None else None,
        )


def detect_media_colorspace(file_path: str, ext: str) -> str:
    """Auto-detect appropriate input colorspace using standard colorspaces engine."""
    try:
        from openreview.color.standard_colorspaces import detect_media_input_colorspace
        meta = {}
        if file_path and os.path.exists(file_path):
            try:
                meta = extract_media_metadata(file_path)
            except Exception:
                meta = {}
        cs_info = detect_media_input_colorspace(file_path, meta)
        return cs_info.name
    except Exception:
        return "sRGB"


def create_proxy_source_item(path: str, parent_folder_id: Optional[str] = None) -> SourceItem:
    """
    Instantiate an immediate lightweight placeholder SourceItem (modeled on Open RV movieProxie)
    enabling zero-latency UI timeline appearance while background worker threads probe metadata.
    """
    name = os.path.basename(path) if path else "Loading..."
    return SourceItem(
        name=name,
        path=path,
        media_type="sequence" if ("#" in name or "%" in name) else "video",
        start_frame=1,
        end_frame=24,
        fps=24.0,
        width=1920,
        height=1080,
        colorspace="linear",
        input_space=0,
        codec="Probing...",
        parent_folder_id=parent_folder_id,
        is_proxy=True,
        proxy_resolved=False,
    )


def build_source_item_from_path(path: str, parent_folder_id: Optional[str] = None) -> Optional[SourceItem]:
    """
    Inspect media file or sequence on disk and build a SourceItem.
    Auto-detects and preserves per-clip colorspace from container/image metadata.
    Thread-safe function suitable for background worker execution.
    """
    if not path or not os.path.exists(path):
        return None

    from openreview.color.standard_colorspaces import detect_media_input_colorspace

    name = os.path.basename(path)
    ext = os.path.splitext(path)[1].lower()

    # Image sequence
    seq_info = detect_image_sequence(path)
    if seq_info and len(seq_info.available_frames) > 1:
        pat_name = f"{seq_info.prefix}{seq_info.separator}{'#' * seq_info.padding}{seq_info.extension}"
        first_frame_path = seq_info.frame_path(seq_info.start_frame) if hasattr(seq_info, "frame_path") else path
        try:
            meta = extract_media_metadata(first_frame_path)
        except Exception:
            meta = {}
        det_cs = detect_media_input_colorspace(first_frame_path, meta)
        return SourceItem(
            name=pat_name,
            path=path,
            media_type="sequence",
            start_frame=seq_info.start_frame,
            end_frame=seq_info.end_frame,
            fps=24.0,
            width=1920,
            height=1080,
            colorspace=det_cs.name,
            input_space=det_cs.id,
            codec="Image Sequence",
            parent_folder_id=parent_folder_id,
        )
    elif is_video_file(path):
        try:
            meta = extract_media_metadata(path)
        except Exception:
            meta = {}
        det_cs = detect_media_input_colorspace(path, meta)
        try:
            reader = VideoMediaReader(path)
            return SourceItem(
                name=name,
                path=path,
                media_type="video",
                start_frame=1,
                end_frame=reader.total_frames,
                fps=reader.fps,
                width=reader.width,
                height=reader.height,
                colorspace=det_cs.name,
                input_space=det_cs.id,
                codec="Video",
                parent_folder_id=parent_folder_id,
            )
        except Exception:
            return SourceItem(
                name=name,
                path=path,
                media_type="video",
                start_frame=1,
                end_frame=100,
                fps=24.0,
                colorspace=det_cs.name,
                input_space=det_cs.id,
                parent_folder_id=parent_folder_id,
            )
    elif ext in (".wav", ".mp3", ".aac", ".flac", ".ogg", ".aiff", ".m4a"):
        return SourceItem(
            name=name,
            path=path,
            media_type="audio",
            start_frame=1,
            end_frame=1,
            fps=24.0,
            colorspace="Raw",
            input_space=10,
            codec="Audio",
            parent_folder_id=parent_folder_id,
        )
    else:
        # Still image or general file
        try:
            meta = extract_media_metadata(path)
        except Exception:
            meta = {}
        det_cs = detect_media_input_colorspace(path, meta)
        return SourceItem(
            name=name,
            path=path,
            media_type="still",
            start_frame=1001,
            end_frame=1001,
            fps=24.0,
            colorspace=det_cs.name,
            input_space=det_cs.id,
            codec="Still Image",
            parent_folder_id=parent_folder_id,
        )


class SessionGraph(QObject):
    """
    Central repository for Sources, Folders, and Viewables (Sequences, Stacks, Layouts).
    Dispatches signals on hierarchy changes and active view switches.
    """
    graph_changed = Signal()
    active_view_changed = Signal(str, str)  # (view_type, view_id)
    source_properties_changed = Signal(str)  # source_id
    view_properties_changed = Signal(str, str)  # (view_type, view_id)

    def __init__(self, parent: Optional[QObject] = None):
        super().__init__(parent)
        self.sources: Dict[str, SourceItem] = {}
        self.folders: Dict[str, FolderItem] = {}
        self.root_folder_ids: List[str] = []
        self.root_source_ids: List[str] = []

        self.sequences: Dict[str, SequenceView] = {}
        self.stacks: Dict[str, StackView] = {}
        self.layouts: Dict[str, LayoutView] = {}

        self.active_view_type: ViewType = ViewType.SOURCE
        self.active_view_id: Optional[str] = None

    # --------------------------------------------------------------------------
    # Sources & Folders
    # --------------------------------------------------------------------------
    def add_source(self, item: SourceItem) -> SourceItem:
        """Add an instantiated SourceItem directly to the sources bin."""
        self.sources[item.id] = item
        self.graph_changed.emit()
        return item

    def add_source_from_path(self, path: str, parent_folder_id: Optional[str] = None, auto_activate: bool = True) -> Optional[SourceItem]:
        """Inspect media file or sequence on disk and add to sources bin."""
        if not path or not os.path.exists(path):
            return None

        # Deduplicate by path
        norm_path = os.path.normpath(path)
        for s in self.sources.values():
            if os.path.normpath(s.path) == norm_path:
                return s

        source = build_source_item_from_path(path, parent_folder_id)
        if not source:
            return None

        self.sources[source.id] = source
        if parent_folder_id and parent_folder_id in self.folders:
            self.folders[parent_folder_id].source_ids.append(source.id)
        else:
            self.root_source_ids.append(source.id)

        # If no active view, make this first source active (if auto_activate)
        if auto_activate and self.active_view_id is None:
            self.set_active_view(ViewType.SOURCE, source.id)

        self.graph_changed.emit()
        return source

    def add_sources_batch(self, items: List[SourceItem], parent_folder_id: Optional[str] = None, auto_activate: bool = True) -> List[SourceItem]:
        """Batch-add multiple pre-built SourceItems with a single graph_changed signal emission."""
        added: List[SourceItem] = []
        for item in items:
            if not item:
                continue
            norm_path = os.path.normpath(item.path)
            already_exists = False
            for s in self.sources.values():
                if os.path.normpath(s.path) == norm_path:
                    already_exists = True
                    break
            if already_exists:
                continue

            self.sources[item.id] = item
            target_parent = item.parent_folder_id or parent_folder_id
            if target_parent and target_parent in self.folders:
                self.folders[target_parent].source_ids.append(item.id)
            else:
                self.root_source_ids.append(item.id)

            if auto_activate and self.active_view_id is None:
                self.set_active_view(ViewType.SOURCE, item.id)
            added.append(item)

        if added:
            self.graph_changed.emit()
        return added

    def add_sources_from_paths(self, paths: List[str], parent_folder_id: Optional[str] = None, auto_activate: bool = True) -> List[SourceItem]:
        items: List[SourceItem] = []
        for p in paths:
            s = build_source_item_from_path(p, parent_folder_id)
            if s:
                items.append(s)
        return self.add_sources_batch(items, parent_folder_id, auto_activate=auto_activate)

    def create_folder(
        self,
        name: str = "New Folder",
        parent_folder_id: Optional[str] = None,
        parent_id: Optional[str] = None,
        folder_type: str = "source",
    ) -> FolderItem:
        target_parent = parent_folder_id or parent_id
        if target_parent and target_parent in self.folders:
            folder_type = self.folders[target_parent].folder_type
        folder = FolderItem(name=name, parent_id=target_parent, folder_type=folder_type)
        self.folders[folder.id] = folder
        if target_parent and target_parent in self.folders:
            self.folders[target_parent].child_folder_ids.append(folder.id)
        else:
            self.root_folder_ids.append(folder.id)
        self.graph_changed.emit()
        return folder

    def move_item_to_folder(self, item_type: str, item_id: str, target_folder_id: Optional[str]) -> bool:
        """Move any item (source, sequence, stack, layout) into a target folder or to root level."""
        # Find item object
        item = None
        if item_type == "source":
            item = self.sources.get(item_id)
        elif item_type == "sequence":
            item = self.sequences.get(item_id)
        elif item_type == "stack":
            item = self.stacks.get(item_id)
        elif item_type == "layout":
            item = self.layouts.get(item_id)

        if not item:
            return False

        old_folder_id = item.parent_folder_id
        if old_folder_id == target_folder_id:
            return True

        # Remove from previous folder or root
        if old_folder_id and old_folder_id in self.folders:
            if item_id in self.folders[old_folder_id].source_ids:
                self.folders[old_folder_id].source_ids.remove(item_id)
        elif item_type == "source" and item_id in self.root_source_ids:
            self.root_source_ids.remove(item_id)

        # Assign new folder
        item.parent_folder_id = target_folder_id
        if target_folder_id and target_folder_id in self.folders:
            target_f = self.folders[target_folder_id]
            if item_id not in target_f.source_ids:
                target_f.source_ids.append(item_id)
        elif item_type == "source":
            if item_id not in self.root_source_ids:
                self.root_source_ids.append(item_id)

        self.graph_changed.emit()
        return True

    def move_folder_to_folder(self, folder_id: str, target_parent_id: Optional[str]) -> bool:
        """Move a folder into another folder, preventing cycles."""
        if folder_id not in self.folders:
            return False
        if folder_id == target_parent_id:
            return False

        # Cycle check: target_parent_id cannot be a descendant of folder_id
        curr = target_parent_id
        while curr:
            if curr == folder_id:
                return False
            curr = self.folders[curr].parent_id if curr in self.folders else None

        f = self.folders[folder_id]
        old_parent_id = f.parent_id
        if old_parent_id == target_parent_id:
            return True

        # Remove from old parent or root
        if old_parent_id and old_parent_id in self.folders:
            if folder_id in self.folders[old_parent_id].child_folder_ids:
                self.folders[old_parent_id].child_folder_ids.remove(folder_id)
        elif folder_id in self.root_folder_ids:
            self.root_folder_ids.remove(folder_id)

        # Add to new parent or root
        f.parent_id = target_parent_id
        if target_parent_id and target_parent_id in self.folders:
            target_parent = self.folders[target_parent_id]
            if folder_id not in target_parent.child_folder_ids:
                target_parent.child_folder_ids.append(folder_id)
            f.folder_type = target_parent.folder_type
        else:
            if folder_id not in self.root_folder_ids:
                self.root_folder_ids.append(folder_id)

        self.graph_changed.emit()
        return True

    def delete_source(self, source_id: str) -> None:
        if source_id in self.sources:
            del self.sources[source_id]
            if source_id in self.root_source_ids:
                self.root_source_ids.remove(source_id)
            for f in self.folders.values():
                if source_id in f.source_ids:
                    f.source_ids.remove(source_id)

            # Clean from sequences, stacks, layouts
            for seq in self.sequences.values():
                seq.clips = [c for c in seq.clips if c.source_id != source_id]
            for stk in self.stacks.values():
                if source_id in stk.source_ids:
                    stk.source_ids.remove(source_id)
            for lay in self.layouts.values():
                if source_id in lay.source_ids:
                    lay.source_ids.remove(source_id)

            if self.active_view_id == source_id:
                self._fallback_active_view()

            self.graph_changed.emit()

    def delete_all_sources(self) -> None:
        """Remove all sources, source folders, and clear references."""
        self.sources.clear()
        self.root_source_ids.clear()
        # Remove source-type folders
        source_folder_ids = [fid for fid, f in self.folders.items() if f.folder_type == "source"]
        for fid in source_folder_ids:
            self.delete_folder(fid)
        for seq in self.sequences.values():
            seq.clips.clear()
        for stk in self.stacks.values():
            stk.source_ids.clear()
        for lay in self.layouts.values():
            lay.source_ids.clear()

        self._fallback_active_view()
        self.graph_changed.emit()

    def delete_folder(self, folder_id: str) -> None:
        if folder_id not in self.folders:
            return
        folder = self.folders.pop(folder_id)
        if folder_id in self.root_folder_ids:
            self.root_folder_ids.remove(folder_id)
        for f in self.folders.values():
            if folder_id in f.child_folder_ids:
                f.child_folder_ids.remove(folder_id)

        # Move children to root
        for sid in folder.source_ids:
            if sid in self.sources:
                self.sources[sid].parent_folder_id = None
                self.root_source_ids.append(sid)
            elif sid in self.sequences:
                self.sequences[sid].parent_folder_id = None
            elif sid in self.stacks:
                self.stacks[sid].parent_folder_id = None
            elif sid in self.layouts:
                self.layouts[sid].parent_folder_id = None

        for cid in folder.child_folder_ids:
            if cid in self.folders:
                self.folders[cid].parent_id = None
                self.root_folder_ids.append(cid)

        self.graph_changed.emit()

    # --------------------------------------------------------------------------
    # Viewable Creation: Sequences, Stacks, Layouts
    # --------------------------------------------------------------------------
    def create_sequence(self, name: str = "New Sequence", source_ids: Optional[List[str]] = None) -> SequenceView:
        sids = source_ids or []
        clips = []
        for sid in sids:
            if sid in self.sources:
                s = self.sources[sid]
                clips.append(SequenceClip(source_id=sid, trim_in=s.start_frame, trim_out=s.end_frame))

        seq = SequenceView(name=name, clips=clips)
        if clips and clips[0].source_id in self.sources:
            seq.start_frame = self.sources[clips[0].source_id].start_frame
            seq.fps = self.sources[clips[0].source_id].fps

        self.sequences[seq.id] = seq
        self.set_active_view(ViewType.SEQUENCE, seq.id)
        self.graph_changed.emit()
        return seq

    def create_stack(self, name: str = "New Stack", source_ids: Optional[List[str]] = None, compare_mode: str = "switch") -> StackView:
        sids = list(source_ids or [])
        stk = StackView(name=name, source_ids=sids, compare_mode=compare_mode)
        if len(sids) >= 2:
            stk.index_a = 0
            stk.index_b = 1
        elif len(sids) == 1:
            stk.index_a = 0
            stk.index_b = 0

        self.stacks[stk.id] = stk
        self.set_active_view(ViewType.STACK, stk.id)
        self.graph_changed.emit()
        return stk

    def create_layout(self, name: str = "New Layout", source_ids: Optional[List[str]] = None, scale_pct: int = 100) -> LayoutView:
        sids = list(source_ids or [])
        lay = LayoutView(name=name, source_ids=sids, scale_pct=scale_pct)
        self.layouts[lay.id] = lay
        self.set_active_view(ViewType.LAYOUT, lay.id)
        self.graph_changed.emit()
        return lay

    def delete_view(self, view_type: ViewType, view_id: str) -> None:
        if view_type == ViewType.SEQUENCE and view_id in self.sequences:
            del self.sequences[view_id]
        elif view_type == ViewType.STACK and view_id in self.stacks:
            del self.stacks[view_id]
        elif view_type == ViewType.LAYOUT and view_id in self.layouts:
            del self.layouts[view_id]

        if self.active_view_id == view_id:
            self._fallback_active_view()

        self.graph_changed.emit()

    # --------------------------------------------------------------------------
    # Active View State
    # --------------------------------------------------------------------------
    def set_active_view(self, view_type: Any, view_id: str) -> None:
        if isinstance(view_type, str):
            view_type = ViewType(view_type.lower())
        self.active_view_type = view_type
        self.active_view_id = view_id
        self.active_view_changed.emit(view_type.value, view_id)

    def get_active_view_name(self) -> str:
        if self.active_view_type == ViewType.SOURCE and self.active_view_id in self.sources:
            return self.sources[self.active_view_id].name
        if self.active_view_type == ViewType.SEQUENCE and self.active_view_id in self.sequences:
            return self.sequences[self.active_view_id].name
        if self.active_view_type == ViewType.STACK and self.active_view_id in self.stacks:
            return self.stacks[self.active_view_id].name
        if self.active_view_type == ViewType.LAYOUT and self.active_view_id in self.layouts:
            return self.layouts[self.active_view_id].name
        return "No Active View"

    def get_active_viewable(self) -> Any:
        if self.active_view_type == ViewType.SOURCE:
            return self.sources.get(self.active_view_id)
        if self.active_view_type == ViewType.SEQUENCE:
            return self.sequences.get(self.active_view_id)
        if self.active_view_type == ViewType.STACK:
            return self.stacks.get(self.active_view_id)
        if self.active_view_type == ViewType.LAYOUT:
            return self.layouts.get(self.active_view_id)
        return None

    def _fallback_active_view(self) -> None:
        """Select a reasonable fallback viewable when active view is deleted."""
        if self.sequences:
            first_seq = next(iter(self.sequences.values()))
            self.set_active_view(ViewType.SEQUENCE, first_seq.id)
        elif self.stacks:
            first_stk = next(iter(self.stacks.values()))
            self.set_active_view(ViewType.STACK, first_stk.id)
        elif self.layouts:
            first_lay = next(iter(self.layouts.values()))
            self.set_active_view(ViewType.LAYOUT, first_lay.id)
        elif self.sources:
            first_src = next(iter(self.sources.values()))
            self.set_active_view(ViewType.SOURCE, first_src.id)
        else:
            self.active_view_id = None
            self.active_view_changed.emit("none", "")

    # --------------------------------------------------------------------------
    # Serialization (Session Save & Restore)
    # --------------------------------------------------------------------------
    def to_dict(self) -> Dict[str, Any]:
        return {
            "sources": {sid: s.to_dict() for sid, s in self.sources.items()},
            "folders": {fid: f.to_dict() for fid, f in self.folders.items()},
            "root_folder_ids": list(self.root_folder_ids),
            "root_source_ids": list(self.root_source_ids),
            "sequences": {qid: q.to_dict() for qid, q in self.sequences.items()},
            "stacks": {kid: k.to_dict() for kid, k in self.stacks.items()},
            "layouts": {lid: l.to_dict() for lid, l in self.layouts.items()},
            "active_view_type": self.active_view_type.value,
            "active_view_id": self.active_view_id,
        }

    def load_from_dict(self, data: Dict[str, Any]) -> None:
        self.sources.clear()
        self.folders.clear()
        self.sequences.clear()
        self.stacks.clear()
        self.layouts.clear()
        self.root_folder_ids.clear()
        self.root_source_ids.clear()

        for sid, sdata in data.get("sources", {}).items():
            self.sources[sid] = SourceItem.from_dict(sdata)
        for fid, fdata in data.get("folders", {}).items():
            self.folders[fid] = FolderItem.from_dict(fdata)
        self.root_folder_ids = list(data.get("root_folder_ids", []))
        self.root_source_ids = list(data.get("root_source_ids", []))

        for qid, qdata in data.get("sequences", {}).items():
            self.sequences[qid] = SequenceView.from_dict(qdata)
        for kid, kdata in data.get("stacks", {}).items():
            self.stacks[kid] = StackView.from_dict(kdata)
        for lid, ldata in data.get("layouts", {}).items():
            self.layouts[lid] = LayoutView.from_dict(ldata)

        vt_str = data.get("active_view_type", "source")
        try:
            self.active_view_type = ViewType(vt_str)
        except ValueError:
            self.active_view_type = ViewType.SOURCE
        self.active_view_id = data.get("active_view_id")
        self.graph_changed.emit()

    def clear(self) -> None:
        """Reset the graph to completely empty state."""
        self.sources.clear()
        self.folders.clear()
        self.sequences.clear()
        self.stacks.clear()
        self.layouts.clear()
        self.root_folder_ids.clear()
        self.root_source_ids.clear()
        self.active_view_id = None
        self.active_view_type = ViewType.SOURCE
        self.graph_changed.emit()

    @classmethod
    def from_dict(cls, data: Dict[str, Any], parent: Optional[Any] = None) -> SessionGraph:
        """Create and populate a SessionGraph instance from dictionary snapshot."""
        graph = cls(parent)
        graph.load_from_dict(data)
        return graph
