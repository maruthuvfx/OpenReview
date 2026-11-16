"""
Smart Media Relinker for Conform Workflows.
Matches editorial cut clips to high-res media files on disk by:
- Reel name and timecode overlap
- Exact filename matching
- Directory recursive scan
- Token replacement ({shot}, {version})
"""
from __future__ import annotations

import os
import re
import logging
from dataclasses import dataclass
from typing import List, Dict, Optional, Tuple, Callable

from openreview.models.timeline_sequence import TimelineSequence, ClipItem, ShotStatus

logger = logging.getLogger(__name__)


@dataclass
class RelinkReport:
    total_clips: int = 0
    matched: int = 0
    missing: int = 0
    conflicts: int = 0
    matched_paths: Dict[str, str] = None  # clip_id -> file_path

    def __post_init__(self):
        if self.matched_paths is None:
            self.matched_paths = {}


class MediaRelinker:
    """Smart Media Relinker for editorial turnovers."""

    SUPPORTED_EXTENSIONS = {
        ".exr", ".dpx", ".mov", ".mp4", ".mxf", ".jpg", ".jpeg", ".png", ".tif", ".tiff"
    }

    def __init__(self, search_paths: Optional[List[str]] = None):
        self.search_paths: List[str] = search_paths or []

    def scan_directory(self, root_path: str, progress_callback: Optional[Callable[[int], None]] = None) -> List[str]:
        """Scan root directory and collect all candidate media files."""
        found_files = []
        if not os.path.exists(root_path):
            return found_files

        count = 0
        for root, _, files in os.walk(root_path):
            for f in files:
                ext = os.path.splitext(f)[1].lower()
                if ext in self.SUPPORTED_EXTENSIONS:
                    found_files.append(os.path.join(root, f))
                    count += 1
                    if progress_callback and count % 100 == 0:
                        progress_callback(count)
        return found_files

    def relink_sequence(self, sequence: TimelineSequence, candidate_files: Optional[List[str]] = None) -> RelinkReport:
        """Attempt to match and relink all clips in sequence against search paths or candidate files."""
        report = RelinkReport()
        if candidate_files is None:
            candidate_files = []
            for sp in self.search_paths:
                candidate_files.extend(self.scan_directory(sp))

        # Index candidate files by basename (without extension and frame numbers)
        index: Dict[str, List[str]] = {}
        for path in candidate_files:
            base = os.path.basename(path)
            clean_base = re.sub(r'[\._]\d{3,8}(?=\.[a-zA-Z0-9]+$)', '', base)
            clean_base_no_ext = os.path.splitext(clean_base)[0].lower()
            index.setdefault(clean_base_no_ext, []).append(path)

        all_clips = sequence.get_all_clips()
        report.total_clips = len(all_clips)

        for track, clip in all_clips:
            # If already valid path, mark matched
            if clip.file_path and os.path.exists(clip.file_path):
                clip.status = ShotStatus.MATCHED
                report.matched += 1
                report.matched_paths[clip.id] = clip.file_path
                continue

            matched_file = self._find_best_match(clip, index, candidate_files)
            if matched_file:
                clip.file_path = matched_file
                clip.status = ShotStatus.MATCHED
                report.matched += 1
                report.matched_paths[clip.id] = matched_file
            else:
                clip.status = ShotStatus.MISSING
                report.missing += 1

        return report

    def _find_best_match(self, clip: ClipItem, index: Dict[str, List[str]], all_files: List[str]) -> Optional[str]:
        # 1. Match by clip name
        clean_clip_name = os.path.splitext(clip.name)[0].lower()
        if clean_clip_name in index:
            candidates = index[clean_clip_name]
            return candidates[0]

        # 2. Match by reel name if present
        if clip.reel:
            clean_reel = clip.reel.lower()
            if clean_reel in index:
                return index[clean_reel][0]
            for path in all_files:
                if clean_reel in os.path.basename(path).lower():
                    return path

        # 3. Match by partial shot name (e.g. SQ040_010)
        shot_match = re.search(r'([A-Za-z0-9]+_[A-Za-z0-9]+)', clip.name)
        if shot_match:
            shot_code = shot_match.group(1).lower()
            for key, paths in index.items():
                if shot_code in key:
                    return paths[0]

        return None
