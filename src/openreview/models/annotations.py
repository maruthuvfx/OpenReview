"""
Annotation, Markup, and Vector Drawing Models.
Supports frame-keyed vector annotations with coordinate-independent resolution scaling,
per-frame review notes, and JSON / CSV / Markdown export and import.
"""
from __future__ import annotations

import json
from pathlib import Path
from enum import Enum
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Any
from PySide6.QtCore import QObject, Signal


class ShapeType(Enum):
    PEN = "pen"
    LINE = "line"
    ARROW = "arrow"
    RECTANGLE = "rectangle"
    CIRCLE = "circle"
    TEXT = "text"


@dataclass
class Stroke:
    shape_type: ShapeType
    points: List[Tuple[float, float]]  # Normalized (0.0 to 1.0) image coordinates
    color: Tuple[int, int, int, int] = (229, 168, 35, 255)  # 3DE amber default
    width: float = 3.0
    text: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "shape_type": self.shape_type.value,
            "points": [(float(p[0]), float(p[1])) for p in self.points],
            "color": list(self.color),
            "width": float(self.width),
            "text": str(self.text),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> Stroke:
        st_val = data.get("shape_type", data.get("type", "pen"))
        pts = [(float(p[0]), float(p[1])) for p in data.get("points", [])]
        color = tuple(data.get("color", (229, 168, 35, 255)))
        width = float(data.get("width", 3.0))
        text = str(data.get("text", ""))
        return cls(
            shape_type=ShapeType(st_val),
            points=pts,
            color=color,
            width=width,
            text=text,
        )


class AnnotationStore(QObject):
    """
    Frame-keyed markup repository with undo/redo stack, per-frame review notes,
    and keyframe navigation.
    """

    annotations_changed = Signal(int)  # frame number (-1 for all)
    note_changed = Signal(int, str)    # frame, note text

    def __init__(self, parent: Optional[QObject] = None):
        super().__init__(parent)
        # frame_number -> list of Stroke
        self._strokes_by_frame: Dict[int, List[Stroke]] = {}
        # frame_number -> note text
        self._notes_by_frame: Dict[int, str] = {}
        # Undo / Redo stacks: frame -> list of states
        self._undo_stack: Dict[int, List[List[Stroke]]] = {}
        self._redo_stack: Dict[int, List[List[Stroke]]] = {}

    def get_strokes(self, frame: int) -> List[Stroke]:
        """Return list of strokes on given frame."""
        return list(self._strokes_by_frame.get(frame, []))

    def has_annotations(self, frame: int) -> bool:
        """True if frame has strokes or a non-empty note."""
        return bool(self._strokes_by_frame.get(frame)) or bool(self._notes_by_frame.get(frame))

    def get_annotated_frames(self) -> List[int]:
        """Return sorted list of frames that contain annotations or notes."""
        frames = set(f for f, strokes in self._strokes_by_frame.items() if strokes)
        frames.update(f for f, note in self._notes_by_frame.items() if note.strip())
        return sorted(list(frames))

    def get_stroke_count(self, frame: int) -> int:
        """Return number of strokes on the given frame."""
        return len(self._strokes_by_frame.get(frame, []))

    def get_shape_counts(self, frame: int) -> Dict[str, int]:
        """Return counts by shape type on the given frame."""
        counts: Dict[str, int] = {}
        for s in self._strokes_by_frame.get(frame, []):
            st = s.shape_type.value
            counts[st] = counts.get(st, 0) + 1
        return counts

    def get_frame_note(self, frame: int) -> str:
        """Return review note for given frame."""
        return self._notes_by_frame.get(frame, "")

    def set_frame_note(self, frame: int, note: str) -> None:
        """Set or update review note for given frame."""
        clean = note.strip()
        if clean:
            self._notes_by_frame[frame] = note
        else:
            self._notes_by_frame.pop(frame, None)
        self.note_changed.emit(frame, note)
        self.annotations_changed.emit(frame)

    def get_all_notes(self) -> Dict[int, str]:
        """Return dict of all frame notes."""
        return dict(self._notes_by_frame)

    def add_stroke(self, frame: int, stroke: Stroke) -> None:
        """Add a new stroke to a frame and push to undo history."""
        self._save_undo_state(frame)
        if frame not in self._strokes_by_frame:
            self._strokes_by_frame[frame] = []
        self._strokes_by_frame[frame].append(stroke)
        if frame in self._redo_stack:
            self._redo_stack[frame].clear()
        self.annotations_changed.emit(frame)

    def update_stroke_text(self, frame: int, stroke_index: int, new_text: str) -> bool:
        """Update the text content of a specific text stroke on given frame with undo support."""
        strokes = self._strokes_by_frame.get(frame)
        if not strokes or not (0 <= stroke_index < len(strokes)):
            return False
        if strokes[stroke_index].shape_type != ShapeType.TEXT:
            return False
        self._save_undo_state(frame)
        strokes[stroke_index].text = new_text
        if frame in self._redo_stack:
            self._redo_stack[frame].clear()
        self.annotations_changed.emit(frame)
        return True

    def remove_stroke(self, frame: int, stroke_index: int) -> bool:
        """Remove a specific stroke on given frame with undo support."""
        strokes = self._strokes_by_frame.get(frame)
        if not strokes or not (0 <= stroke_index < len(strokes)):
            return False
        self._save_undo_state(frame)
        strokes.pop(stroke_index)
        if frame in self._redo_stack:
            self._redo_stack[frame].clear()
        self.annotations_changed.emit(frame)
        return True

    def get_text_strokes(self, frame: int) -> List[Tuple[int, Stroke]]:
        """Return list of (original_index, stroke) for all text strokes on frame."""
        strokes = self._strokes_by_frame.get(frame, [])
        return [(i, s) for i, s in enumerate(strokes) if s.shape_type == ShapeType.TEXT]

    def clear_frame(self, frame: int, clear_note: bool = False) -> None:
        """Clear all strokes on the specified frame, optionally clearing note."""
        changed = False
        if frame in self._strokes_by_frame and self._strokes_by_frame[frame]:
            self._save_undo_state(frame)
            self._strokes_by_frame[frame] = []
            changed = True
        if clear_note and frame in self._notes_by_frame:
            self._notes_by_frame.pop(frame, None)
            changed = True
        if changed:
            self.annotations_changed.emit(frame)

    def undo(self, frame: int) -> None:
        """Undo last action on current frame."""
        if frame in self._undo_stack and self._undo_stack[frame]:
            current = [Stroke.from_dict(s.to_dict()) for s in self.get_strokes(frame)]
            if frame not in self._redo_stack:
                self._redo_stack[frame] = []
            self._redo_stack[frame].append(current)

            prev = self._undo_stack[frame].pop()
            self._strokes_by_frame[frame] = prev
            self.annotations_changed.emit(frame)

    def redo(self, frame: int) -> None:
        """Redo last undone action on current frame."""
        if frame in self._redo_stack and self._redo_stack[frame]:
            current = [Stroke.from_dict(s.to_dict()) for s in self.get_strokes(frame)]
            if frame not in self._undo_stack:
                self._undo_stack[frame] = []
            self._undo_stack[frame].append(current)

            nxt = self._redo_stack[frame].pop()
            self._strokes_by_frame[frame] = nxt
            self.annotations_changed.emit(frame)

    def get_next_annotated_frame(self, current_frame: int) -> Optional[int]:
        frames = self.get_annotated_frames()
        for f in frames:
            if f > current_frame:
                return f
        return frames[0] if frames else None

    def get_prev_annotated_frame(self, current_frame: int) -> Optional[int]:
        frames = self.get_annotated_frames()
        for f in reversed(frames):
            if f < current_frame:
                return f
        return frames[-1] if frames else None

    def _save_undo_state(self, frame: int) -> None:
        if frame not in self._undo_stack:
            self._undo_stack[frame] = []
        # Save deep copy of strokes so stroke property updates are properly restored on undo
        copied = [Stroke.from_dict(s.to_dict()) for s in self._strokes_by_frame.get(frame, [])]
        self._undo_stack[frame].append(copied)

    def clear_all(self) -> None:
        """Clear annotations and notes across all frames."""
        self._strokes_by_frame.clear()
        self._notes_by_frame.clear()
        self._undo_stack.clear()
        self._redo_stack.clear()
        self.annotations_changed.emit(-1)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize all strokes and notes by frame."""
        res: Dict[str, Any] = {}
        for f, strokes in self._strokes_by_frame.items():
            if strokes:
                res[str(f)] = [
                    {
                        "shape_type": s.shape_type.value,
                        "points": s.points,
                        "color": list(s.color),
                        "width": s.width,
                        "text": s.text,
                    }
                    for s in strokes
                ]
        if self._notes_by_frame:
            res["__notes__"] = dict(self._notes_by_frame)
        return res

    def from_dict(self, data: Dict[str, Any]) -> None:
        """Restore strokes and notes from dictionary."""
        self.clear_all()
        if not data:
            return

        # Restore notes if present
        if "__notes__" in data and isinstance(data["__notes__"], dict):
            for f_str, note in data["__notes__"].items():
                try:
                    self._notes_by_frame[int(f_str)] = str(note)
                except ValueError:
                    pass

        for f_str, stroke_list in data.items():
            if f_str == "__notes__" or not isinstance(stroke_list, list):
                continue
            try:
                f = int(f_str)
                self._strokes_by_frame[f] = []
                for s_data in stroke_list:
                    # Support both "shape_type" and "type" keys for maximum compatibility
                    st_val = s_data.get("shape_type", s_data.get("type", "pen"))
                    pts = [(float(p[0]), float(p[1])) for p in s_data.get("points", [])]
                    color = tuple(s_data.get("color", (229, 168, 35, 255)))
                    width = float(s_data.get("width", 3.0))
                    text = s_data.get("text", "")
                    st = Stroke(
                        shape_type=ShapeType(st_val),
                        points=pts,
                        color=color,
                        width=width,
                        text=text,
                    )
                    self._strokes_by_frame[f].append(st)
            except Exception as e:
                print(f"[AnnotationStore] Error restoring frame {f_str}: {e}")
        self.annotations_changed.emit(-1)

    def export_to_json(self, file_path: str | Path) -> None:
        """Export all vector annotations and notes to a JSON file."""
        p = Path(file_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        data = self.to_dict()
        with open(p, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

    def import_from_json(self, file_path: str | Path, merge: bool = False) -> int:
        """
        Import annotations and notes from JSON file.
        Returns number of frames imported.
        """
        p = Path(file_path)
        if not p.is_file():
            raise FileNotFoundError(f"Annotations file not found: {p}")
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)

        if not merge:
            self.from_dict(data)
            return len(self.get_annotated_frames())

        # Merge mode
        count = 0
        if "__notes__" in data and isinstance(data["__notes__"], dict):
            for f_str, note in data["__notes__"].items():
                try:
                    self._notes_by_frame[int(f_str)] = str(note)
                except ValueError:
                    pass

        for f_str, stroke_list in data.items():
            if f_str == "__notes__" or not isinstance(stroke_list, list):
                continue
            try:
                f = int(f_str)
                if f not in self._strokes_by_frame:
                    self._strokes_by_frame[f] = []
                for s_data in stroke_list:
                    st_val = s_data.get("shape_type", s_data.get("type", "pen"))
                    pts = [(float(p[0]), float(p[1])) for p in s_data.get("points", [])]
                    color = tuple(s_data.get("color", (229, 168, 35, 255)))
                    width = float(s_data.get("width", 3.0))
                    text = s_data.get("text", "")
                    st = Stroke(
                        shape_type=ShapeType(st_val),
                        points=pts,
                        color=color,
                        width=width,
                        text=text,
                    )
                    self._strokes_by_frame[f].append(st)
                count += 1
            except Exception as e:
                print(f"[AnnotationStore] Error merging frame {f_str}: {e}")
        self.annotations_changed.emit(-1)
        return count

    def generate_report_markdown(self, fps: float = 24.0) -> str:
        """Generate a formatted markdown review notes report."""
        frames = self.get_annotated_frames()
        lines = [
            "# OpenReview - Annotation & Review Notes Report",
            "",
            f"**Total Annotated Keyframes:** {len(frames)}  ",
            f"**Project FPS:** {fps:.2f}",
            "",
            "| Frame | Timecode | Strokes | Shape Breakdown | Review Notes |",
            "| :--- | :--- | :--- | :--- | :--- |",
        ]
        for f in frames:
            tc = frame_to_timecode(f, fps)
            st_count = self.get_stroke_count(f)
            shapes = self.get_shape_counts(f)
            shapes_str = ", ".join(f"{k.capitalize()}: {v}" for k, v in shapes.items()) if shapes else "None"
            note = self.get_frame_note(f).replace("\n", " ").replace("|", "\\|")
            lines.append(f"| {f} | `{tc}` | {st_count} | {shapes_str} | {note} |")

        lines.append("")
        return "\n".join(lines)

    def generate_report_csv(self, fps: float = 24.0) -> str:
        """Generate CSV review notes report."""
        import csv
        import io
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["Frame", "Timecode", "Stroke Count", "Shape Breakdown", "Review Note"])
        frames = self.get_annotated_frames()
        for f in frames:
            tc = frame_to_timecode(f, fps)
            st_count = self.get_stroke_count(f)
            shapes = self.get_shape_counts(f)
            shapes_str = "; ".join(f"{k}:{v}" for k, v in shapes.items())
            note = self.get_frame_note(f)
            writer.writerow([f, tc, st_count, shapes_str, note])
        return output.getvalue()


def frame_to_timecode(frame: int, fps: float = 24.0) -> str:
    """Format frame index into standard SMPTE timecode (HH:MM:SS:FF)."""
    if fps <= 0:
        fps = 24.0
    safe_frame = max(0, frame)
    total_seconds = int(safe_frame // fps)
    rem_frames = int(round(safe_frame % fps))
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    seconds = total_seconds % 60
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}:{rem_frames:02d}"


class PerMediaAnnotations(QObject):
    """
    Separates notes and annotations per media asset.
    On a switch, stashes the store's contents under the media being left
    and restores whatever was stashed for the media being opened.
    """

    def __init__(self, parent: Optional[QObject] = None):
        super().__init__(parent)
        self._stashes: Dict[str, Dict[str, Any]] = {}

    def stash(self, media_key: str, store: AnnotationStore) -> None:
        """Stash current annotation store state under media_key."""
        if not media_key or not store:
            return
        self._stashes[str(media_key)] = store.to_dict()

    def restore(self, media_key: str, store: AnnotationStore) -> None:
        """Restore stashed annotations for media_key, or clear if none exist."""
        if not store:
            return
        if not media_key:
            store.clear_all()
            return
        key_str = str(media_key)
        if key_str in self._stashes:
            store.from_dict(self._stashes[key_str])
        else:
            store.clear_all()

    def has_annotations(self, media_key: str) -> bool:
        """Check if annotations exist in stash for media_key."""
        key_str = str(media_key)
        data = self._stashes.get(key_str)
        if not data:
            return False
        for k, v in data.items():
            if k == "__notes__" and isinstance(v, dict) and any(v.values()):
                return True
            if isinstance(v, list) and len(v) > 0:
                return True
        return False

    def clear(self) -> None:
        """Clear all media stashes."""
        self._stashes.clear()

    def to_dict(self) -> Dict[str, Any]:
        """Serialize all media stashes for session persistence."""
        return dict(self._stashes)

    def from_dict(self, data: Dict[str, Any]) -> None:
        """Restore all media stashes from serialized dictionary."""
        self._stashes.clear()
        if isinstance(data, dict):
            for k, v in data.items():
                if isinstance(v, dict):
                    self._stashes[str(k)] = v

