"""
OpenReview Session Data Model and Serialization (.ovr).
Captures complete workspace state including loaded media, in/out marks,
bookmarks, viewport transforms, color settings, mattes, and annotations.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional, List, Dict, Any


@dataclass
class ViewportSessionState:
    zoom: float = 1.0
    pan_x: float = 0.0
    pan_y: float = 0.0
    flip_x: bool = False
    flip_y: bool = False
    rotation: int = 0  # 0, 90, 180, 270
    pixel_aspect_ratio: float = 1.0
    texture_filtering: str = "linear"  # "linear" or "nearest"
    downsample_ratio: float = 1.0


@dataclass
class ColorSessionState:
    exposure_ev: float = 0.0
    gamma: float = 1.0
    channel_mode: int = 0
    input_space: int = 0
    view_transform: int = 0


@dataclass
class MatteSessionState:
    ratio: Optional[float] = None
    opacity: float = 1.0


@dataclass
class OpenReviewSession:
    """Full session snapshot for .ovr JSON files."""
    version: str = "1.0"
    media_path: Optional[str] = None
    media_type: str = "none"  # "sequence", "video", "none"
    start_frame: int = 1
    end_frame: int = 100
    in_frame: int = 1
    out_frame: int = 100
    current_frame: int = 1
    fps: float = 24.0
    loop_mode: str = "cycle"
    bookmarks: List[int] = field(default_factory=list)
    background_mode: str = "black"  # "black", "grey18", "grey50", "white", "checker"

    viewport: ViewportSessionState = field(default_factory=ViewportSessionState)
    color: ColorSessionState = field(default_factory=ColorSessionState)
    matte: MatteSessionState = field(default_factory=MatteSessionState)
    annotations: Dict[str, Any] = field(default_factory=dict)
    per_media_annotations: Dict[str, Any] = field(default_factory=dict)
    session_graph: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> OpenReviewSession:
        vp_data = data.get("viewport", {})
        col_data = data.get("color", {})
        mat_data = data.get("matte", {})

        return cls(
            version=data.get("version", "1.0"),
            media_path=data.get("media_path"),
            media_type=data.get("media_type", "none"),
            start_frame=int(data.get("start_frame", 1)),
            end_frame=int(data.get("end_frame", 100)),
            in_frame=int(data.get("in_frame", 1)),
            out_frame=int(data.get("out_frame", 100)),
            current_frame=int(data.get("current_frame", 1)),
            fps=float(data.get("fps", 24.0)),
            loop_mode=str(data.get("loop_mode", "cycle")),
            bookmarks=[int(b) for b in data.get("bookmarks", [])],
            background_mode=str(data.get("background_mode", "black")),
            viewport=ViewportSessionState(
                zoom=float(vp_data.get("zoom", 1.0)),
                pan_x=float(vp_data.get("pan_x", 0.0)),
                pan_y=float(vp_data.get("pan_y", 0.0)),
                flip_x=bool(vp_data.get("flip_x", False)),
                flip_y=bool(vp_data.get("flip_y", False)),
                rotation=int(vp_data.get("rotation", 0)),
                pixel_aspect_ratio=float(vp_data.get("pixel_aspect_ratio", 1.0)),
                texture_filtering=str(vp_data.get("texture_filtering", "linear")),
                downsample_ratio=float(vp_data.get("downsample_ratio", 1.0)),
            ),
            color=ColorSessionState(
                exposure_ev=float(col_data.get("exposure_ev", 0.0)),
                gamma=float(col_data.get("gamma", 1.0)),
                channel_mode=int(col_data.get("channel_mode", 0)),
                input_space=int(col_data.get("input_space", 0)),
                view_transform=int(col_data.get("view_transform", 0)),
            ),
            matte=MatteSessionState(
                ratio=float(mat_data["ratio"]) if mat_data.get("ratio") is not None else None,
                opacity=float(mat_data.get("opacity", 1.0)),
            ),
            annotations=data.get("annotations", {}),
            per_media_annotations=data.get("per_media_annotations", {}),
            session_graph=data.get("session_graph", {}),
        )

    def save_to_file(self, filepath: str | Path) -> None:
        p = Path(filepath)
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=4)

    @classmethod
    def load_from_file(cls, filepath: str | Path) -> OpenReviewSession:
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
        return cls.from_dict(data)
