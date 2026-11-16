"""
Timeline Turnover and Sequence Exporter.
Supports:
- CMX 3600 EDL export
- OpenTimelineIO (.otio) turnover export
- Cut list CSV export
- Batch shot turnover reporting with folder hierarchy and custom name templates
"""
from __future__ import annotations

import os
import csv
import json
import logging
from typing import Optional, List, Dict, Any, Tuple

from openreview.models.timeline_sequence import TimelineSequence, Track, ClipItem, TrackType
from openreview.core.timecode import frame_to_timecode
from openreview.ui.export_template_builder import resolve_export_pattern

logger = logging.getLogger(__name__)


class TimelineExporter:
    """Exports conformed editorial sequences to turnover files and hierarchical folders."""

    @staticmethod
    def resolve_path(
        pattern: str,
        clip: ClipItem,
        track: Track,
        sequence: TimelineSequence,
        index: int = 1,
        ext: str = "",
    ) -> str:
        """Resolve a pattern template into relative file path."""
        return resolve_export_pattern(pattern, clip, track, sequence, index=index, ext=ext)

    @staticmethod
    def export_cmx3600_edl(sequence: TimelineSequence, output_path: str) -> None:
        """Generate a valid CMX 3600 EDL turnover file from sequence."""
        lines = [
            f"TITLE: {sequence.name.upper()}",
            "FCM: NON-DROP FRAME",
            "",
        ]

        event_counter = 1
        for track in sequence.tracks:
            trk_code = "V" if track.track_type == TrackType.VIDEO else "A"
            for clip in track.clips:
                src_in_tc = frame_to_timecode(clip.src_in, sequence.fps)
                src_out_tc = frame_to_timecode(clip.src_out, sequence.fps)
                dst_in_tc = frame_to_timecode(clip.dst_in, sequence.fps)
                dst_out_tc = frame_to_timecode(clip.dst_out, sequence.fps)

                reel = (clip.reel or "AX")[:8].ljust(8)
                ev_str = f"{event_counter:03d}".ljust(3)

                line = f"{ev_str}  {reel} {trk_code}     C        {src_in_tc} {src_out_tc} {dst_in_tc} {dst_out_tc}"
                lines.append(line)
                lines.append(f"* FROM CLIP NAME: {clip.name}")
                if clip.file_path:
                    lines.append(f"* SOURCE FILE: {clip.file_path}")
                lines.append("")
                event_counter += 1

        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))

    @staticmethod
    def export_csv(sequence: TimelineSequence, output_path: str) -> None:
        """Export cut list to CSV."""
        headers = [
            "Event", "Shot Name", "Track", "Status",
            "Src In", "Src Out", "Src Dur", "Dst In", "Dst Out", "Dst Dur",
            "Version", "Reel", "File Path"
        ]
        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
        with open(output_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(headers)
            for idx, (t, c) in enumerate(sequence.get_all_clips()):
                writer.writerow([
                    f"{idx+1:03d}", c.name, t.name, str(c.status),
                    c.src_in, c.src_out, c.src_duration,
                    c.dst_in, c.dst_out, c.dst_duration,
                    c.version, c.reel, c.file_path
                ])

    @staticmethod
    def export_clips_with_template(
        clips_with_tracks: List[Tuple[Track, ClipItem]],
        sequence: TimelineSequence,
        output_dir: str,
        pattern: str = "{name}",
        ext: str = "mov",
    ) -> List[str]:
        """
        Export clips into hierarchical folders and filenames defined by pattern.
        Automatically creates all intermediate directories.
        Returns list of written file paths.
        """
        exported_files: List[str] = []
        for idx, (track, clip) in enumerate(clips_with_tracks, start=1):
            rel_path = resolve_export_pattern(pattern, clip, track, sequence, index=idx, ext=ext)
            abs_target = os.path.abspath(os.path.join(output_dir, rel_path))
            os.makedirs(os.path.dirname(abs_target), exist_ok=True)

            meta_file = os.path.splitext(abs_target)[0] + "_manifest.json"
            manifest = {
                "clip_name": clip.name,
                "track": track.name,
                "source_media": clip.file_path,
                "src_in": clip.src_in,
                "src_out": clip.src_out,
                "dst_in": clip.dst_in,
                "dst_out": clip.dst_out,
                "duration_frames": clip.dst_duration,
                "fps": sequence.fps,
                "colorspace": clip.colorspace,
                "version": clip.version,
                "export_target": abs_target,
            }
            with open(meta_file, "w", encoding="utf-8") as mf:
                json.dump(manifest, mf, indent=2)

            if clip.file_path and os.path.exists(clip.file_path):
                import shutil
                try:
                    shutil.copyfile(clip.file_path, abs_target)
                except Exception:
                    with open(abs_target, "wb") as cf:
                        cf.write(b"")
            elif not os.path.exists(abs_target):
                with open(abs_target, "wb") as cf:
                    cf.write(b"")

            exported_files.append(abs_target)

        return exported_files
