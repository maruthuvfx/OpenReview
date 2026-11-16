"""
Avid AAF (Advanced Authoring Format) Conform Parser.
Parses Avid AAF turnover files extracting sequences, tracks, clips, and timecodes.
"""
from __future__ import annotations

import os
import logging
from typing import Optional, List, Dict, Any

from openreview.models.timeline_sequence import TimelineSequence, Track, ClipItem, TrackType, ShotStatus

logger = logging.getLogger(__name__)


class AAFParser:
    """AAF Parser supporting Avid Media Composer turnovers."""

    def __init__(self, fps: float = 24.0):
        self.fps = fps

    def parse_file(self, file_path: str) -> TimelineSequence:
        """Parse an AAF file into an OpenReview TimelineSequence."""
        seq_name = os.path.splitext(os.path.basename(file_path))[0]
        seq = TimelineSequence(name=seq_name, fps=self.fps)

        try:
            import aaf2
            with aaf2.open(file_path, "r") as f:
                # Find composition mob
                comp_mob = next(f.content.compositionmobs(), None)
                if comp_mob:
                    seq.name = comp_mob.name or seq_name
                    for slot in comp_mob.slots:
                        segment = slot.segment
                        is_video = "Picture" in str(slot.data_kind)
                        track_type = TrackType.VIDEO if is_video else TrackType.AUDIO
                        track = Track(
                            id=f"aaf_{slot.slot_id}",
                            name=slot.name or f"Track {slot.slot_id}",
                            track_type=track_type,
                        )

                        cur_frame = 1
                        # If sequence has components
                        if hasattr(segment, "components"):
                            for comp in segment.components:
                                length = comp.length or 0
                                if hasattr(comp, "mob") and comp.mob:
                                    clip_name = comp.mob.name or "AAF Clip"
                                    clip = ClipItem(
                                        id=f"clip_{len(track.clips)+1}",
                                        name=clip_name,
                                        file_path="",
                                        src_in=0,
                                        src_out=max(0, length - 1),
                                        dst_in=cur_frame,
                                        dst_out=cur_frame + max(0, length - 1),
                                        status=ShotStatus.MISSING,
                                    )
                                    track.add_clip(clip)
                                cur_frame += length

                        seq.tracks.append(track)
        except ImportError:
            logger.warning("[AAFParser] pyaaf2 module not installed. Creating placeholder conform tracks.")
            v1 = seq.add_video_track("Video 1")
            a1 = seq.add_audio_track("Audio 1")
        except Exception as e:
            logger.error("[AAFParser] Failed parsing AAF file %s: %s", file_path, e)
            seq.add_video_track("Video 1")

        return seq
