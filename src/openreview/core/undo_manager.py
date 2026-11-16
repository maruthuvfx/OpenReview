"""
Global Undo and Redo Manager for OpenReview.
Built on PySide6's QUndoStack and QUndoCommand.
Provides seamless undo/redo for:
- Timeline clip movements, trimming, slipping, sliding, splitting, and deletions
- Timeline media ingestion (clip drops)
- Timeline review markers (add, edit, delete)
- Clip soft effects (add, remove, parameter adjustments)
- Viewport canvas annotations
"""
from __future__ import annotations

import logging
from typing import Optional, List, Dict, Any, Callable
from PySide6.QtGui import QUndoStack, QUndoCommand
from PySide6.QtCore import QObject, Signal

from openreview.models.timeline_sequence import TimelineSequence, Track, ClipItem, TimelineMarker, SoftEffect

logger = logging.getLogger(__name__)


class UndoManager(QObject):
    """
    Central Singleton Undo/Redo manager for the OpenReview application.
    """
    _instance: Optional[UndoManager] = None

    undo_state_changed = Signal()

    def __init__(self, parent: Optional[QObject] = None):
        super().__init__(parent)
        self._stack = QUndoStack(self)
        self._stack.setUndoLimit(100)
        self._stack.canUndoChanged.connect(self._on_stack_state_changed)
        self._stack.canRedoChanged.connect(self._on_stack_state_changed)
        self._stack.indexChanged.connect(self._on_stack_state_changed)
        self._callbacks_on_change: List[Callable[[], None]] = []

    def _on_stack_state_changed(self, *args: Any) -> None:
        try:
            self.undo_state_changed.emit()
        except (RuntimeError, Exception):
            pass

    @classmethod
    def instance(cls) -> UndoManager:
        try:
            if cls._instance is not None:
                cls._instance.objectName()
        except RuntimeError:
            cls._instance = None

        if cls._instance is None:
            cls._instance = UndoManager()
        return cls._instance

    @property
    def stack(self) -> QUndoStack:
        return self._stack

    def add_change_callback(self, callback: Callable[[], None]) -> None:
        """Register a callback that fires whenever undo or redo is performed."""
        if callback not in self._callbacks_on_change:
            self._callbacks_on_change.append(callback)

    def remove_change_callback(self, callback: Callable[[], None]) -> None:
        if callback in self._callbacks_on_change:
            self._callbacks_on_change.remove(callback)

    def _notify_change(self) -> None:
        for cb in list(self._callbacks_on_change):
            try:
                cb()
            except Exception as e:
                logger.error("Error executing undo change callback: %s", e)

    def push(self, cmd: QUndoCommand) -> None:
        """Push an executed command to the undo stack."""
        self._stack.push(cmd)

    def undo(self) -> None:
        """Undo the last command."""
        if self._stack.canUndo():
            self._stack.undo()
            self._notify_change()

    def redo(self) -> None:
        """Redo the last command."""
        if self._stack.canRedo():
            self._stack.redo()
            self._notify_change()

    def can_undo(self) -> bool:
        return self._stack.canUndo()

    def can_redo(self) -> bool:
        return self._stack.canRedo()

    def undo_text(self) -> str:
        return self._stack.undoText()

    def redo_text(self) -> str:
        return self._stack.redoText()

    def clear(self) -> None:
        self._stack.clear()


# ==============================================================================
# Concrete Undo Commands
# ==============================================================================

class ClipMoveCommand(QUndoCommand):
    """Command to move a clip to a new time position or track."""
    def __init__(
        self,
        sequence: TimelineSequence,
        clip: ClipItem,
        old_track: Track,
        new_track: Track,
        old_dst_in: int,
        old_dst_out: int,
        new_dst_in: int,
        new_dst_out: int,
        on_change: Optional[Callable[[], None]] = None,
        text: str = "Move Clip"
    ):
        super().__init__(text)
        self.sequence = sequence
        self.clip = clip
        self.old_track = old_track
        self.new_track = new_track
        self.old_dst_in = old_dst_in
        self.old_dst_out = old_dst_out
        self.new_dst_in = new_dst_in
        self.new_dst_out = new_dst_out
        self.on_change = on_change
        self._is_first = True

    def undo(self) -> None:
        if self.new_track.id != self.old_track.id:
            self.new_track.remove_clip(self.clip.id)
            self.old_track.add_clip(self.clip)
        self.clip.dst_in = self.old_dst_in
        self.clip.dst_out = self.old_dst_out
        self.old_track.clips.sort(key=lambda c: c.dst_in)
        if self.on_change:
            self.on_change()

    def redo(self) -> None:
        if self._is_first:
            self._is_first = False
            return
        if self.new_track.id != self.old_track.id:
            self.old_track.remove_clip(self.clip.id)
            self.new_track.add_clip(self.clip)
        self.clip.dst_in = self.new_dst_in
        self.clip.dst_out = self.new_dst_out
        self.new_track.clips.sort(key=lambda c: c.dst_in)
        if self.on_change:
            self.on_change()


class ClipTrimCommand(QUndoCommand):
    """Command to trim head (trim_in) or tail (trim_out) of a clip, with ripple shift."""
    def __init__(
        self,
        sequence: TimelineSequence,
        track: Track,
        clip: ClipItem,
        old_dst_in: int,
        old_dst_out: int,
        old_src_in: int,
        old_src_out: int,
        new_dst_in: int,
        new_dst_out: int,
        new_src_in: int,
        new_src_out: int,
        shifted_clips_state: Optional[List[Dict[str, Any]]] = None,
        on_change: Optional[Callable[[], None]] = None,
        text: str = "Trim Clip"
    ):
        super().__init__(text)
        self.sequence = sequence
        self.track = track
        self.clip = clip
        self.old_dst_in = old_dst_in
        self.old_dst_out = old_dst_out
        self.old_src_in = old_src_in
        self.old_src_out = old_src_out
        self.new_dst_in = new_dst_in
        self.new_dst_out = new_dst_out
        self.new_src_in = new_src_in
        self.new_src_out = new_src_out
        self.shifted_clips_state = shifted_clips_state or []
        self.on_change = on_change
        self._is_first = True

    def undo(self) -> None:
        self.clip.dst_in = self.old_dst_in
        self.clip.dst_out = self.old_dst_out
        self.clip.src_in = self.old_src_in
        self.clip.src_out = self.old_src_out
        for sc in self.shifted_clips_state:
            c = self.track.get_clip_by_id(sc["id"])
            if c:
                c.dst_in = sc["old_dst_in"]
                c.dst_out = sc["old_dst_out"]
        self.track.clips.sort(key=lambda c: c.dst_in)
        if self.on_change:
            self.on_change()

    def redo(self) -> None:
        if self._is_first:
            self._is_first = False
            return
        self.clip.dst_in = self.new_dst_in
        self.clip.dst_out = self.new_dst_out
        self.clip.src_in = self.new_src_in
        self.clip.src_out = self.new_src_out
        for sc in self.shifted_clips_state:
            c = self.track.get_clip_by_id(sc["id"])
            if c:
                c.dst_in = sc["new_dst_in"]
                c.dst_out = sc["new_dst_out"]
        self.track.clips.sort(key=lambda c: c.dst_in)
        if self.on_change:
            self.on_change()


class ClipSlipCommand(QUndoCommand):
    """Command to slip source in/out of a clip without moving timeline bounds."""
    def __init__(
        self,
        clip: ClipItem,
        old_src_in: int,
        old_src_out: int,
        new_src_in: int,
        new_src_out: int,
        on_change: Optional[Callable[[], None]] = None,
        text: str = "Slip Clip"
    ):
        super().__init__(text)
        self.clip = clip
        self.old_src_in = old_src_in
        self.old_src_out = old_src_out
        self.new_src_in = new_src_in
        self.new_src_out = new_src_out
        self.on_change = on_change
        self._is_first = True

    def undo(self) -> None:
        self.clip.src_in = self.old_src_in
        self.clip.src_out = self.old_src_out
        if self.on_change:
            self.on_change()

    def redo(self) -> None:
        if self._is_first:
            self._is_first = False
            return
        self.clip.src_in = self.new_src_in
        self.clip.src_out = self.new_src_out
        if self.on_change:
            self.on_change()


class ClipSlideCommand(QUndoCommand):
    """Command to slide a clip and roll adjacent boundary clips."""
    def __init__(
        self,
        clip: ClipItem,
        old_dst_in: int,
        old_dst_out: int,
        new_dst_in: int,
        new_dst_out: int,
        adjacent_changes: List[Dict[str, Any]],
        on_change: Optional[Callable[[], None]] = None,
        text: str = "Slide Clip"
    ):
        super().__init__(text)
        self.clip = clip
        self.old_dst_in = old_dst_in
        self.old_dst_out = old_dst_out
        self.new_dst_in = new_dst_in
        self.new_dst_out = new_dst_out
        self.adjacent_changes = adjacent_changes  # list of {"clip": ClipItem, "old": (dst_in, dst_out, src_in, src_out), "new": (..)}
        self.on_change = on_change
        self._is_first = True

    def undo(self) -> None:
        self.clip.dst_in = self.old_dst_in
        self.clip.dst_out = self.old_dst_out
        for ch in self.adjacent_changes:
            c: ClipItem = ch["clip"]
            c.dst_in, c.dst_out, c.src_in, c.src_out = ch["old"]
        if self.on_change:
            self.on_change()

    def redo(self) -> None:
        if self._is_first:
            self._is_first = False
            return
        self.clip.dst_in = self.new_dst_in
        self.clip.dst_out = self.new_dst_out
        for ch in self.adjacent_changes:
            c: ClipItem = ch["clip"]
            c.dst_in, c.dst_out, c.src_in, c.src_out = ch["new"]
        if self.on_change:
            self.on_change()


class ClipSplitCommand(QUndoCommand):
    """Command to split/razor a clip into two clips."""
    def __init__(
        self,
        track: Track,
        clip_original: ClipItem,
        original_dst_out: int,
        original_src_out: int,
        new_split_clip: ClipItem,
        on_change: Optional[Callable[[], None]] = None,
        text: str = "Split Clip"
    ):
        super().__init__(text)
        self.track = track
        self.clip_original = clip_original
        self.original_dst_out = original_dst_out
        self.original_src_out = original_src_out
        self.new_split_clip = new_split_clip
        self.on_change = on_change
        self._is_first = True

    def undo(self) -> None:
        self.track.remove_clip(self.new_split_clip.id)
        self.clip_original.dst_out = self.original_dst_out
        self.clip_original.src_out = self.original_src_out
        self.track.clips.sort(key=lambda c: c.dst_in)
        if self.on_change:
            self.on_change()

    def redo(self) -> None:
        if self._is_first:
            self._is_first = False
            return
        self.clip_original.dst_out = self.new_split_clip.dst_in - 1
        delta = self.clip_original.dst_out - self.clip_original.dst_in
        self.clip_original.src_out = self.clip_original.src_in + delta
        self.track.add_clip(self.new_split_clip)
        self.track.clips.sort(key=lambda c: c.dst_in)
        if self.on_change:
            self.on_change()


class ClipDeleteCommand(QUndoCommand):
    """Command to delete one or more clips, with optional ripple shift."""
    def __init__(
        self,
        sequence: TimelineSequence,
        deleted_clips_info: List[Dict[str, Any]],  # [{"track": Track, "clip": ClipItem, "index": int}]
        shifted_clips_state: Optional[List[Dict[str, Any]]] = None,
        on_change: Optional[Callable[[], None]] = None,
        text: str = "Delete Clips"
    ):
        super().__init__(text)
        self.sequence = sequence
        self.deleted_clips_info = deleted_clips_info
        self.shifted_clips_state = shifted_clips_state or []
        self.on_change = on_change
        self._is_first = True

    def undo(self) -> None:
        for item in self.deleted_clips_info:
            trk: Track = item["track"]
            clp: ClipItem = item["clip"]
            if not trk.get_clip_by_id(clp.id):
                trk.add_clip(clp)
            trk.clips.sort(key=lambda c: c.dst_in)
        for sc in self.shifted_clips_state:
            trk: Track = sc["track"]
            clp: ClipItem = trk.get_clip_by_id(sc["id"])
            if clp:
                clp.dst_in = sc["old_dst_in"]
                clp.dst_out = sc["old_dst_out"]
            trk.clips.sort(key=lambda c: c.dst_in)
        if self.on_change:
            self.on_change()

    def redo(self) -> None:
        if self._is_first:
            self._is_first = False
            return
        for item in self.deleted_clips_info:
            trk: Track = item["track"]
            clp: ClipItem = item["clip"]
            trk.remove_clip(clp.id)
        for sc in self.shifted_clips_state:
            trk: Track = sc["track"]
            clp: ClipItem = trk.get_clip_by_id(sc["id"])
            if clp:
                clp.dst_in = sc["new_dst_in"]
                clp.dst_out = sc["new_dst_out"]
            trk.clips.sort(key=lambda c: c.dst_in)
        if self.on_change:
            self.on_change()


class ClipAddCommand(QUndoCommand):
    """Command to add a clip to a track (e.g., dropped from Sources panel)."""
    def __init__(
        self,
        track: Track,
        clip: ClipItem,
        on_change: Optional[Callable[[], None]] = None,
        text: str = "Add Clip"
    ):
        super().__init__(text)
        self.track = track
        self.clip = clip
        self.on_change = on_change
        self._is_first = True

    def undo(self) -> None:
        self.track.remove_clip(self.clip.id)
        if self.on_change:
            self.on_change()

    def redo(self) -> None:
        if self._is_first:
            self._is_first = False
            return
        self.track.add_clip(self.clip)
        self.track.clips.sort(key=lambda c: c.dst_in)
        if self.on_change:
            self.on_change()


class MarkerAddCommand(QUndoCommand):
    """Command to add a review marker."""
    def __init__(
        self,
        sequence: TimelineSequence,
        marker: TimelineMarker,
        on_change: Optional[Callable[[], None]] = None,
        text: str = "Add Review Marker"
    ):
        super().__init__(text)
        self.sequence = sequence
        self.marker = marker
        self.on_change = on_change
        self._is_first = True

    def undo(self) -> None:
        self.sequence.remove_marker_at_frame(self.marker.frame)
        if self.on_change:
            self.on_change()

    def redo(self) -> None:
        if self._is_first:
            self._is_first = False
            return
        self.sequence.markers.append(self.marker)
        self.sequence.markers.sort(key=lambda m: m.frame)
        if self.on_change:
            self.on_change()


class MarkerDeleteCommand(QUndoCommand):
    """Command to delete a review marker."""
    def __init__(
        self,
        sequence: TimelineSequence,
        marker: TimelineMarker,
        on_change: Optional[Callable[[], None]] = None,
        text: str = "Delete Review Marker"
    ):
        super().__init__(text)
        self.sequence = sequence
        self.marker = marker
        self.on_change = on_change
        self._is_first = True

    def undo(self) -> None:
        self.sequence.markers.append(self.marker)
        self.sequence.markers.sort(key=lambda m: m.frame)
        if self.on_change:
            self.on_change()

    def redo(self) -> None:
        if self._is_first:
            self._is_first = False
            return
        self.sequence.remove_marker_at_frame(self.marker.frame)
        if self.on_change:
            self.on_change()


class MarkerEditCommand(QUndoCommand):
    """Command to edit review marker title, comment, category, or color."""
    def __init__(
        self,
        marker: TimelineMarker,
        old_data: Dict[str, Any],
        new_data: Dict[str, Any],
        on_change: Optional[Callable[[], None]] = None,
        text: str = "Edit Review Marker"
    ):
        super().__init__(text)
        self.marker = marker
        self.old_data = old_data
        self.new_data = new_data
        self.on_change = on_change
        self._is_first = True

    def undo(self) -> None:
        self.marker.name = self.old_data.get("name", self.marker.name)
        self.marker.comment = self.old_data.get("comment", self.marker.comment)
        self.marker.color = self.old_data.get("color", self.marker.color)
        self.marker.category = self.old_data.get("category", self.marker.category)
        if self.on_change:
            self.on_change()

    def redo(self) -> None:
        if self._is_first:
            self._is_first = False
            return
        self.marker.name = self.new_data.get("name", self.marker.name)
        self.marker.comment = self.new_data.get("comment", self.marker.comment)
        self.marker.color = self.new_data.get("color", self.marker.color)
        self.marker.category = self.new_data.get("category", self.marker.category)
        if self.on_change:
            self.on_change()


class SoftEffectCommand(QUndoCommand):
    """Command to add, remove, or modify parameters of a clip soft effect."""
    def __init__(
        self,
        clip: ClipItem,
        action: str,  # "add", "remove", "param_change", "reorder"
        effect: SoftEffect,
        old_params: Optional[Dict[str, Any]] = None,
        new_params: Optional[Dict[str, Any]] = None,
        old_index: int = -1,
        new_index: int = -1,
        on_change: Optional[Callable[[], None]] = None,
        text: str = "Soft Effect Change"
    ):
        super().__init__(text)
        self.clip = clip
        self.action = action
        self.effect = effect
        self.old_params = dict(old_params or {})
        self.new_params = dict(new_params or {})
        self.old_index = old_index
        self.new_index = new_index
        self.on_change = on_change
        self._is_first = True

    def undo(self) -> None:
        if self.action == "add":
            self.clip.remove_soft_effect(self.effect.id)
        elif self.action == "remove":
            if self.old_index >= 0 and self.old_index <= len(self.clip.soft_effects):
                self.clip.soft_effects.insert(self.old_index, self.effect)
            else:
                self.clip.soft_effects.append(self.effect)
        elif self.action == "param_change":
            self.effect.parameters.update(self.old_params)
        elif self.action == "reorder":
            if self.effect in self.clip.soft_effects:
                self.clip.soft_effects.remove(self.effect)
                self.clip.soft_effects.insert(self.old_index, self.effect)
        if self.on_change:
            self.on_change()

    def redo(self) -> None:
        if self._is_first:
            self._is_first = False
            return
        if self.action == "add":
            if self.new_index >= 0 and self.new_index <= len(self.clip.soft_effects):
                self.clip.soft_effects.insert(self.new_index, self.effect)
            else:
                self.clip.soft_effects.append(self.effect)
        elif self.action == "remove":
            self.clip.remove_soft_effect(self.effect.id)
        elif self.action == "param_change":
            self.effect.parameters.update(self.new_params)
        elif self.action == "reorder":
            if self.effect in self.clip.soft_effects:
                self.clip.soft_effects.remove(self.effect)
                self.clip.soft_effects.insert(self.new_index, self.effect)
        if self.on_change:
            self.on_change()


class AnnotationStrokeCommand(QUndoCommand):
    """Command to integrate canvas drawing strokes into global undo stack."""
    def __init__(
        self,
        annotation_store: Any,
        frame: int,
        on_change: Optional[Callable[[], None]] = None,
        text: str = "Drawing Stroke"
    ):
        super().__init__(text)
        self.annotation_store = annotation_store
        self.frame = frame
        self.on_change = on_change
        self._is_first = True

    def undo(self) -> None:
        self.annotation_store.undo(self.frame)
        if self.on_change:
            self.on_change()

    def redo(self) -> None:
        if self._is_first:
            self._is_first = False
            return
        self.annotation_store.redo(self.frame)
        if self.on_change:
            self.on_change()


class ClipLinkCommand(QUndoCommand):
    """Command to link or unlink clips with global undo/redo support."""

    def __init__(
        self,
        sequence: Any,
        links_data: List[Dict[str, Any]],
        description: str = "Link/Unlink Clips",
        on_change: Optional[Callable[[], None]] = None,
        parent: Optional[QUndoCommand] = None,
    ):
        super().__init__(description, parent)
        self.sequence = sequence
        self.links_data = links_data
        self.on_change = on_change
        self._is_first = True

    def undo(self) -> None:
        for item in self.links_data:
            c = item.get("clip")
            if c:
                c.link_id = item.get("old_link_id")
        if self.on_change:
            self.on_change()

    def redo(self) -> None:
        if self._is_first:
            self._is_first = False
            return
        for item in self.links_data:
            c = item.get("clip")
            if c:
                c.link_id = item.get("new_link_id")
        if self.on_change:
            self.on_change()

