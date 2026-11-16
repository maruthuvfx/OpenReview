"""
CMX 3600 EDL (Edit Decision List) Parser.
Parses standard EDL turnover files into structured events and TimelineSequence tracks.
"""
from __future__ import annotations

import re
import logging
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any

from openreview.models.timeline_sequence import TimelineSequence, Track, ClipItem, TrackType, ShotStatus
from openreview.core.timecode import timecode_to_frame

logger = logging.getLogger(__name__)


@dataclass
class EDLEvent:
    event_num: str
    reel: str
    track_type: str  # V, A, A2, etc.
    edit_type: str   # C (cut), D (dissolve), W (wipe)
    src_in_tc: str
    src_out_tc: str
    dst_in_tc: str
    dst_out_tc: str
    src_in_frame: int = 0
    src_out_frame: int = 0
    dst_in_frame: int = 0
    dst_out_frame: int = 0
    clip_name: str = ""
    source_file: str = ""
    speed: float = 100.0  # percentage
    comments: List[str] = field(default_factory=list)


class EDLParser:
    """CMX 3600 EDL Parser supporting video, audio, dissolves, comments, and speed ramps."""

    EVENT_REGEX = re.compile(
        r"^(\d{3,6})\s+([\w\-]+)\s+([VA\d]+)\s+([CDW])\s*(?:\d+)?\s+"
        r"(\d{2}:\d{2}:\d{2}[:;]\d{2})\s+(\d{2}:\d{2}:\d{2}[:;]\d{2})\s+"
        r"(\d{2}:\d{2}:\d{2}[:;]\d{2})\s+(\d{2}:\d{2}:\d{2}[:;]\d{2})"
    )
    M2_SPEED_REGEX = re.compile(
        r"^M2\s+([\w\-]+)\s+([\-\d\.]+)\s+(\d{2}:\d{2}:\d{2}[:;]\d{2})"
    )

    def __init__(self, fps: float = 24.0):
        self.fps = fps

    def parse_file(self, file_path: str) -> List[EDLEvent]:
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        return self.parse_lines(lines)

    def parse_lines(self, lines: List[str]) -> List[EDLEvent]:
        events: List[EDLEvent] = []
        current_event: Optional[EDLEvent] = None

        for line in lines:
            line_str = line.strip()
            if not line_str or line_str.startswith("TITLE:") or line_str.startswith("FCM:"):
                continue

            # Check if this line starts a new edit event
            match = self.EVENT_REGEX.match(line_str)
            if match:
                ev_num, reel, trk, edit, src_in, src_out, dst_in, dst_out = match.groups()
                current_event = EDLEvent(
                    event_num=ev_num,
                    reel=reel,
                    track_type=trk,
                    edit_type=edit,
                    src_in_tc=src_in,
                    src_out_tc=src_out,
                    dst_in_tc=dst_in,
                    dst_out_tc=dst_out,
                    src_in_frame=timecode_to_frame(src_in, self.fps),
                    src_out_frame=timecode_to_frame(src_out, self.fps),
                    dst_in_frame=timecode_to_frame(dst_in, self.fps),
                    dst_out_frame=timecode_to_frame(dst_out, self.fps),
                )
                events.append(current_event)
                continue

            # M2 Speed command (e.g. M2 AX 048.0 01:00:10:00)
            m2_match = self.M2_SPEED_REGEX.match(line_str)
            if m2_match and current_event:
                _, speed_fps, _ = m2_match.groups()
                try:
                    current_event.speed = (float(speed_fps) / self.fps) * 100.0
                except (ValueError, ZeroDivisionError):
                    pass
                continue

            # Comments and metadata lines
            if current_event:
                if line_str.startswith("* FROM CLIP NAME:") or line_str.startswith("* CLIP NAME:"):
                    clip_name = line_str.split(":", 1)[1].strip()
                    current_event.clip_name = clip_name
                elif line_str.startswith("* SOURCE FILE:"):
                    src_file = line_str.split(":", 1)[1].strip()
                    current_event.source_file = src_file
                elif line_str.startswith("*"):
                    current_event.comments.append(line_str.lstrip("* ").strip())

        return events

    def to_sequence(self, events: List[EDLEvent], sequence_name: str = "Conformed EDL") -> TimelineSequence:
        """Convert parsed EDL events into a multi-track TimelineSequence."""
        seq = TimelineSequence(name=sequence_name, fps=self.fps)
        v_track = Track(id="v1", name="Video 1", track_type=TrackType.VIDEO)
        a_track = Track(id="a1", name="Audio 1", track_type=TrackType.AUDIO)

        seq.tracks.append(v_track)
        seq.tracks.append(a_track)

        min_dst = 999999999
        max_dst = 0

        for ev in events:
            clip_name = ev.clip_name or f"Shot_{ev.event_num}_{ev.reel}"
            target_track = v_track if "V" in ev.track_type else a_track

            clip = ClipItem(
                id=f"clip_edl_{ev.event_num}",
                name=clip_name,
                file_path=ev.source_file,
                src_in=ev.src_in_frame,
                src_out=ev.src_out_frame,
                dst_in=ev.dst_in_frame,
                dst_out=ev.dst_out_frame,
                speed=ev.speed / 100.0,
                reel=ev.reel,
                status=ShotStatus.MISSING if not ev.source_file else ShotStatus.MATCHED,
            )
            target_track.add_clip(clip)

            if ev.dst_in_frame < min_dst:
                min_dst = ev.dst_in_frame
            if ev.dst_out_frame > max_dst:
                max_dst = ev.dst_out_frame

        if events:
            seq.in_frame = min_dst if min_dst < 999999999 else 1
            seq.out_frame = max(seq.in_frame + 1, max_dst)

        return seq
