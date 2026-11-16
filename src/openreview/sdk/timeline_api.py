"""
OpenReview Dynamic Timelines Python API and SDK.
Provides pipeline TDs and DCC integrations with programmatic control over:
- Sequence and track creation
- Media clip insertion and retimes
- Soft Effects generation (CDL, Transform, Burn-in)
- Conform execution from EDL / XML / AAF
- OpenTimelineIO (.otio) import and export
"""
from __future__ import annotations

import logging
from typing import Optional, List, Dict, Any

from openreview.models.timeline_sequence import (
    TimelineSequence,
    Track,
    ClipItem,
    TrackType,
    SoftEffect,
    SoftEffectType,
    ShotStatus,
)
from openreview.core.conform import EDLParser, XMLParser, AAFParser, MediaRelinker

logger = logging.getLogger(__name__)


class TimelineAPI:
    """High-level Python API for programmatic timeline manipulation."""

    @staticmethod
    def create_sequence(
        name: str = "New Sequence",
        fps: float = 24.0,
        width: int = 1920,
        height: int = 1080,
        start_timecode: str = "00:00:00:00",
    ) -> TimelineSequence:
        """Create and return a new empty TimelineSequence."""
        seq = TimelineSequence(
            name=name,
            fps=fps,
            width=width,
            height=height,
            start_timecode=start_timecode,
        )
        return seq

    @staticmethod
    def add_video_track(sequence: TimelineSequence, name: str = "") -> Track:
        """Add a new video track to sequence."""
        return sequence.add_video_track(name)

    @staticmethod
    def add_audio_track(sequence: TimelineSequence, name: str = "") -> Track:
        """Add a new audio track to sequence."""
        return sequence.add_audio_track(name)

    @staticmethod
    def add_clip(
        track: Track,
        file_path: str,
        name: str = "",
        src_in: int = 1001,
        src_out: int = 1048,
        dst_in: int = 1,
        dst_out: Optional[int] = None,
        speed: float = 1.0,
        colorspace: str = "ACEScg",
        reel: str = "",
    ) -> ClipItem:
        """Append an editorial clip to a track."""
        import os
        clip_name = name or os.path.splitext(os.path.basename(file_path))[0]
        duration = max(1, src_out - src_in + 1)
        calc_dst_out = dst_out if dst_out is not None else int(round(dst_in + (duration / max(0.001, speed)) - 1))

        clip = ClipItem(
            id=f"clip_{track.id}_{len(track.clips)+1}",
            name=clip_name,
            file_path=file_path,
            src_in=src_in,
            src_out=src_out,
            dst_in=dst_in,
            dst_out=calc_dst_out,
            speed=speed,
            reel=reel,
            colorspace=colorspace,
            status=ShotStatus.MATCHED if os.path.exists(file_path) else ShotStatus.MISSING,
        )
        track.add_clip(clip)
        return clip

    @staticmethod
    def add_cdl_grade(
        clip: ClipItem,
        slope: float = 1.0,
        offset: float = 0.0,
        power: float = 1.0,
        saturation: float = 1.0,
    ) -> SoftEffect:
        """Apply a non-destructive GPU CDL Grade soft effect to clip."""
        return clip.add_soft_effect(
            SoftEffectType.GRADE_CDL,
            name="Grade CDL",
            params={
                "slope": slope,
                "offset": offset,
                "power": power,
                "saturation": saturation,
            }
        )

    @staticmethod
    def conform_edl(edl_path: str, fps: float = 24.0, search_paths: Optional[List[str]] = None) -> TimelineSequence:
        """Conform sequence from CMX 3600 EDL file with optional media relinking."""
        parser = EDLParser(fps=fps)
        events = parser.parse_file(edl_path)
        seq = parser.to_sequence(events)
        if search_paths:
            relinker = MediaRelinker(search_paths=search_paths)
            relinker.relink_sequence(seq)
        return seq

    @staticmethod
    def conform_xml(xml_path: str, fps: float = 24.0, search_paths: Optional[List[str]] = None) -> TimelineSequence:
        """Conform sequence from FCP 7 / Premiere XML."""
        parser = XMLParser(fps=fps)
        seq = parser.parse_file(xml_path)
        if search_paths:
            relinker = MediaRelinker(search_paths=search_paths)
            relinker.relink_sequence(seq)
        return seq

    @staticmethod
    def export_otio(sequence: TimelineSequence, file_path: str) -> None:
        """Export sequence to OpenTimelineIO format."""
        sequence.export_otio(file_path)

    @staticmethod
    def import_otio(file_path: str) -> TimelineSequence:
        """Import sequence from OpenTimelineIO format."""
        return TimelineSequence.import_otio(file_path)
