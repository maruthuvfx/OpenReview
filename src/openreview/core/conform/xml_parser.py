"""
Final Cut Pro 7 / Premiere XML (xmeml) Parser.
Parses multi-track sequences, clips, media paths, timecodes, and retime information.
"""
from __future__ import annotations

import os
import logging
import xml.etree.ElementTree as ET
from typing import Optional, List, Dict, Any

from openreview.models.timeline_sequence import TimelineSequence, Track, ClipItem, TrackType, ShotStatus

logger = logging.getLogger(__name__)


class XMLParser:
    """Parser for Apple Final Cut Pro 7 / Adobe Premiere XML export files."""

    def __init__(self, fps: float = 24.0):
        self.fps = fps

    def parse_file(self, file_path: str) -> TimelineSequence:
        tree = ET.parse(file_path)
        root = tree.getroot()

        # Find sequence element
        seq_elem = root.find(".//sequence")
        if seq_elem is None:
            seq_elem = root

        seq_name = seq_elem.findtext("name") or os.path.splitext(os.path.basename(file_path))[0]

        rate_elem = seq_elem.find(".//rate/timebase")
        if rate_elem is not None and rate_elem.text:
            try:
                self.fps = float(rate_elem.text)
            except ValueError:
                pass

        seq = TimelineSequence(name=seq_name, fps=self.fps)

        # Parse Video Tracks
        for t_idx, track_elem in enumerate(seq_elem.findall(".//media/video/track")):
            track = Track(
                id=f"v_track_{t_idx+1}",
                name=f"Video {t_idx+1}",
                track_type=TrackType.VIDEO,
            )
            self._parse_track_clips(track_elem, track)
            seq.tracks.append(track)

        # Parse Audio Tracks
        for a_idx, track_elem in enumerate(seq_elem.findall(".//media/audio/track")):
            track = Track(
                id=f"a_track_{a_idx+1}",
                name=f"Audio {a_idx+1}",
                track_type=TrackType.AUDIO,
            )
            self._parse_track_clips(track_elem, track)
            seq.tracks.append(track)

        if not seq.tracks:
            seq.add_video_track("Video 1")

        return seq

    def _parse_track_clips(self, track_elem: ET.Element, track: Track) -> None:
        for clip_elem in track_elem.findall("clipitem"):
            clip_id = clip_elem.get("id", f"clip_{len(track.clips)+1}")
            name = clip_elem.findtext("name") or "Clip"
            in_val = int(clip_elem.findtext("in") or 0)
            out_val = int(clip_elem.findtext("out") or 0)
            start_val = int(clip_elem.findtext("start") or 0)
            end_val = int(clip_elem.findtext("end") or 0)

            # File path
            file_path = ""
            path_url = clip_elem.findtext(".//file/pathurl")
            if path_url:
                # Decode file:// path
                if path_url.startswith("file://localhost/"):
                    file_path = path_url.replace("file://localhost/", "")
                elif path_url.startswith("file://"):
                    file_path = path_url.replace("file://", "")
                import urllib.parse
                file_path = urllib.parse.unquote(file_path)

            reel = clip_elem.findtext(".//file/media/video/samplecharacteristics/reel") or ""

            clip = ClipItem(
                id=clip_id,
                name=name,
                file_path=file_path,
                src_in=in_val,
                src_out=out_val,
                dst_in=start_val,
                dst_out=end_val,
                reel=reel,
                status=ShotStatus.MATCHED if (file_path and os.path.exists(file_path)) else ShotStatus.MISSING,
            )
            track.add_clip(clip)
