"""
Centralized Keyboard Shortcuts Registry and Dispatch Manager for OpenReview.
Handles customizable keybindings, conflict detection, persistence via Preferences,
and robust application-wide event filtering.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict, List, Optional
from PySide6.QtCore import QObject, Signal, QEvent, Qt, QKeyCombination
from PySide6.QtGui import QKeyEvent, QKeySequence
from PySide6.QtWidgets import (
    QWidget,
    QLineEdit,
    QTextEdit,
    QPlainTextEdit,
    QSpinBox,
    QDoubleSpinBox,
    QAbstractSpinBox,
    QApplication,
    QDialog,
)

from openreview.core.preferences import PreferencesManager


@dataclass
class ShortcutAction:
    """Represents a customizable user action with default and active shortcuts."""
    action_id: str
    name: str
    category: str
    default_shortcut: str
    current_shortcut: str
    description: str = ""
    callback: Optional[Callable[[], None]] = None

    @property
    def effective_shortcut(self) -> str:
        return self.current_shortcut if self.current_shortcut else self.default_shortcut

    def is_customized(self) -> bool:
        return self.current_shortcut != self.default_shortcut


class ShortcutManager(QObject):
    """
    Centralized Keyboard Shortcut Manager singleton.
    Maintains the master registry of shortcuts, provides key conflict detection,
    interacts with UserPreferences, and filters global keypresses.
    """
    shortcuts_changed = Signal()  # Emitted when any shortcut keybinding is modified

    _instance: Optional[ShortcutManager] = None

    @classmethod
    def instance(cls) -> ShortcutManager:
        if cls._instance is None:
            cls._instance = ShortcutManager()
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        if cls._instance is not None:
            app = QApplication.instance()
            if app:
                try:
                    app.removeEventFilter(cls._instance)
                except Exception:
                    pass
        cls._instance = None

    def __init__(self, parent: Optional[QObject] = None):
        super().__init__(parent)
        self._actions: Dict[str, ShortcutAction] = {}
        # Fast lookup mapping: normalized_key_sequence -> action_id
        self._key_map: Dict[str, str] = {}
        self._aliases: Dict[str, str] = {}
        self._suppress_predicate: Optional[Callable[[], bool]] = None
        self._init_default_actions()
        self.load_from_preferences()

    def set_suppress_predicate(self, predicate: Optional[Callable[[], bool]]) -> None:
        """Register a callback that returns True when global shortcuts should be suppressed (e.g. typing)."""
        self._suppress_predicate = predicate

    def is_suppressed(self) -> bool:
        """Return True if shortcut dispatch is currently suppressed."""
        if self._suppress_predicate is not None:
            try:
                return bool(self._suppress_predicate())
            except Exception:
                return False
        return False

    def _init_default_actions(self) -> None:
        """Register the complete VFX-grade studio shortcut registry."""
        defs = [
            # Playback & Transport
            ("playback.toggle", "Toggle Playback", "Playback & Transport", "Space", "Play or pause the active sequence"),
            ("playback.play_fwd", "Play Forward", "Playback & Transport", "", "Start forward normal playback"),
            ("playback.play_back", "Play Backward", "Playback & Transport", "", "Start reverse normal playback"),
            ("playback.step_fwd", "Step Forward 1 Frame", "Playback & Transport", "Right", "Advance 1 frame"),
            ("playback.step_back", "Step Backward 1 Frame", "Playback & Transport", "Left", "Go back 1 frame"),
            ("playback.step_fwd_10", "Step Forward 10 Frames", "Playback & Transport", "Shift+Right", "Advance 10 frames"),
            ("playback.step_back_10", "Step Backward 10 Frames", "Playback & Transport", "Shift+Left", "Go back 10 frames"),
            ("playback.first_frame", "Go to First Frame", "Playback & Transport", "Home", "Jump to sequence start frame"),
            ("playback.last_frame", "Go to Last Frame", "Playback & Transport", "End", "Jump to sequence end frame"),
            ("playback.shuttle_rev", "Shuttle Reverse (J)", "Playback & Transport", "J", "Shuttle playback backward (-1x, -2x, -4x)"),
            ("playback.shuttle_stop", "Shuttle Stop (K)", "Playback & Transport", "K", "Stop shuttle playback"),
            ("playback.shuttle_fwd", "Shuttle Forward (L)", "Playback & Transport", "L", "Shuttle playback forward (1x, 2x, 4x)"),
            ("playback.set_fps", "Set Frame Rate...", "Playback & Transport", "Shift+F", "Choose playback frame rate via dialog"),

            # Channels
            ("channel.rgb", "Channel: Full RGB Color", "Channels", "C", "Display full color RGB buffer"),
            ("channel.red", "Channel: Red (Isolated)", "Channels", "R", "Isolate red color channel"),
            ("channel.green", "Channel: Green (Isolated)", "Channels", "G", "Isolate green color channel"),
            ("channel.blue", "Channel: Blue (Isolated)", "Channels", "B", "Isolate blue color channel"),
            ("channel.alpha", "Channel: Alpha (Isolated)", "Channels", "A", "Isolate alpha channel"),
            ("channel.luma", "Channel: Luminance (Isolated)", "Channels", "Y", "Isolate luminance / grey buffer"),

            # View & Display
            ("view.fit", "Fit Image to Window", "View & Display", "F", "Fit current image framing inside viewport"),
            ("view.actual_size", "Actual Size (100%)", "View & Display", "1", "Zoom image to 100% 1:1 pixel scale"),
            ("view.fit_width", "Fit Width", "View & Display", "Ctrl+F", "Fit image horizontally to viewport width"),
            ("view.zoom_100", "Zoom 100% Preset", "View & Display", "Ctrl+1", "Set 100% zoom"),
            ("view.zoom_200", "Zoom 200% Preset", "View & Display", "Ctrl+2", "Set 200% zoom"),
            ("view.zoom_400", "Zoom 400% Preset", "View & Display", "Ctrl+4", "Set 400% zoom"),
            ("view.zoom_800", "Zoom 800% Preset", "View & Display", "Ctrl+8", "Set 800% zoom"),
            ("view.flop_x", "Flop (Horizontal Mirror)", "View & Display", "Shift+X", "Mirror image along horizontal X axis"),
            ("view.flip_y", "Flip (Vertical Invert)", "View & Display", "Shift+Y", "Flip image along vertical Y axis"),
            ("view.filter_toggle", "Toggle Texture Filtering", "View & Display", "N", "Toggle Bilinear smoothing vs Nearest-Neighbor pixels"),
            ("view.fullscreen", "Toggle Fullscreen", "View & Display", "F12", "Toggle borderless fullscreen display"),
            ("view.presentation", "Toggle Presentation Mode", "View & Display", "Ctrl+P", "Hide all toolbars, timelines, and panels for clean client presentation"),
            ("view.matte_cycle", "Cycle Matte Aspect Ratio", "View & Display", "Ctrl+M", "Cycle between cinema aspect ratio matte overlays"),
            ("view.matte_opacity_cycle", "Cycle Matte Opacity", "View & Display", "Shift+M", "Cycle matte masking opacity (25%, 35%, 50%, 100%)"),
            ("view.safe_guides", "Toggle Safe Area Guides", "View & Display", "Shift+G", "Show/hide 90% Action Safe, 80% Title Safe and Crosshair guides"),
            ("view.onion_skin", "Toggle Onion Skinning", "View & Display", "Shift+O", "Show/hide frame ghosting overlay"),

            # Color & Exposure
            ("color.gain_up", "Gain: Increase (+0.10)", "Color & Exposure", "+", "Increase exposure gain multiplier by 0.10"),
            ("color.gain_down", "Gain: Decrease (-0.10)", "Color & Exposure", "-", "Decrease exposure gain multiplier by 0.10"),
            ("color.exp_up", "Exposure: Increase (+0.25 EV)", "Color & Exposure", "Alt++", "Increase exposure compensation by 0.25 EV"),
            ("color.exp_down", "Exposure: Decrease (-0.25 EV)", "Color & Exposure", "Alt+-", "Decrease exposure compensation by 0.25 EV"),
            ("color.gamma_up", "Gamma: Increase (+0.05)", "Color & Exposure", "Shift++", "Increase display gamma by 0.05"),
            ("color.gamma_down", "Gamma: Decrease (-0.05)", "Color & Exposure", "Shift+-", "Decrease display gamma by 0.05"),
            ("color.sat_up", "Saturation: Increase (+0.05)", "Color & Exposure", "Ctrl+Shift++", "Increase saturation by 0.05"),
            ("color.sat_down", "Saturation: Decrease (-0.05)", "Color & Exposure", "Ctrl+Shift+-", "Decrease saturation by 0.05"),
            ("color.invert_toggle", "Toggle Invert Colors", "Color & Exposure", "Shift+I", "Toggle RGB color inversion"),
            ("color.exp_reset", "Reset Exposure (0 EV)", "Color & Exposure", "Ctrl+Alt+E", "Reset exposure compensation to 0.0 EV"),
            ("color.gamma_reset", "Reset Gamma (1.0)", "Color & Exposure", "Ctrl+Alt+G", "Reset display gamma to 1.0"),
            ("color.reset_all", "Reset All Color Adjustments", "Color & Exposure", "Ctrl+Alt+R", "Reset gain, gamma, saturation, and color adjustments"),
            ("color.adjustments", "Interactive Color Adjustments", "Color & Exposure", "/", "Open Interactive Color Adjustments panel"),

            # Timeline
            ("timeline.zoom_in", "Timeline Zoom In", "Timeline", "Ctrl+=", "Zoom in timeline view"),
            ("timeline.zoom_out", "Timeline Zoom Out", "Timeline", "Ctrl+-", "Zoom out timeline view"),
            ("timeline.magnifier_toggle", "Toggle Magnified Timeline", "Timeline", "Shift+T", "Toggle magnified timeline scrub mini-ruler"),
            ("timeline.toggle_timecode", "Toggle SMPTE Timecode", "Timeline", "Ctrl+T", "Switch between Frame numbers and SMPTE Timecode HH:MM:SS:FF"),
            ("timeline.goto_frame", "Goto Frame", "Timeline", "Ctrl+G", "Open numeric Go to frame dialog"),

            # Marking & Bookmarks
            ("mark.in", "Set In Mark", "Marking & Bookmarks", "[", "Set sequence in boundary"),
            ("mark.out", "Set Out Mark", "Marking & Bookmarks", "]", "Set sequence out boundary"),
            ("mark.clear_in_out", "Clear In/Out Range", "Marking & Bookmarks", "\\", "Clear timeline In and Out boundaries"),
            ("mark.bookmark_toggle", "Toggle Bookmark", "Marking & Bookmarks", "M", "Add or remove marker at current frame"),
            ("mark.bookmark_next", "Next Marker / Bookmark", "Marking & Bookmarks", "Alt+Right", "Jump to next marker or bookmark"),
            ("mark.bookmark_prev", "Previous Marker / Bookmark", "Marking & Bookmarks", "Alt+Left", "Jump to previous marker or bookmark"),
            ("mark.bookmark_segment_next", "Next Bookmark Segment", "Marking & Bookmarks", "PageDown", "Step In/Out boundaries forward across bookmark intervals"),
            ("mark.bookmark_segment_prev", "Previous Bookmark Segment", "Marking & Bookmarks", "PageUp", "Step In/Out boundaries backward across bookmark intervals"),
            ("mark.clear_in", "Clear In Mark", "Marking & Bookmarks", "Alt+I", "Clear marked in point"),
            ("mark.clear_out", "Clear Out Mark", "Marking & Bookmarks", "Alt+O", "Clear marked out point"),

            # Media Reloading
            ("media.reload_frame", "Reload Current Frame", "File & Sessions", "Ctrl+R", "Evict and reload active frame from disk"),
            ("media.reload_sequence", "Reload Sequence / Flush Cache", "File & Sessions", "Ctrl+Shift+R", "Flush cached frames for active sequence and reload"),

            # Media Stacking & Layout
            ("stack.cycle_fwd", "Cycle Stack Layer Forward", "Media Stacking & Layout", "Z", "Cycle to next layer in active stack"),
            ("stack.cycle_bwd", "Cycle Stack Layer Backward", "Media Stacking & Layout", "Shift+Z", "Cycle to previous layer in active stack"),

            # HUDs & Floating Panels
            ("hud.histogram", "Toggle Live RGB Histogram", "HUDs & Panels", "H", "Show/hide real-time color histogram"),
            ("hud.waveform", "Toggle Waveform Monitor", "HUDs & Panels", "V", "Show/hide real-time Waveform scope"),
            ("hud.vectorscope", "Toggle Vectorscope Monitor", "HUDs & Panels", "Shift+V", "Show/hide real-time Vectorscope scope"),
            ("hud.color_inspector", "Toggle Color Inspector HUD", "HUDs & Panels", "F5", "Show/hide floating pixel color inspector"),
            ("hud.wipe", "Toggle A/B Wipe Controller", "HUDs & Panels", "W", "Show/hide interactive split wipe comparison"),
            ("hud.wipe_flip_orientation", "Flip Wipe Orientation", "HUDs & Panels", "Shift+W", "Flip wipe split between Horizontal and Vertical"),
            ("hud.wipe_reset", "Reset Wipe Controls", "HUDs & Panels", "Alt+W", "Reset wipe position to center and angle to default"),
            ("hud.source_details", "Toggle Source Media Details", "HUDs & Panels", "I", "Inspect media metadata, codec, resolution, and color profile"),
            ("hud.info_strip", "Toggle Info Strip", "HUDs & Panels", "Ctrl+I", "Show/hide HUD metadata overlay strip"),
            ("hud.dailies_sync", "Remote Dailies Sync Panel", "HUDs & Panels", "Ctrl+Shift+D", "Open multi-user sync collaboration panel"),
            ("hud.layers", "Toggle EXR Passes HUD", "HUDs & Panels", "", "Show/hide multi-channel EXR render passes panel"),
            ("hud.stereo", "Toggle Stereoscopic 3D HUD", "HUDs & Panels", "", "Show/hide stereo 3D display panel"),
            ("hud.toggle_file_browser", "Toggle File Browser", "HUDs & Panels", "Ctrl+B", "Show/hide dockable file and media browser"),
            ("hud.console", "Toggle Console Window", "HUDs & Panels", "Ctrl+Shift+L", "Show/hide diagnostic console and error log window"),
            ("hud.comp_qc", "Toggle xtra QC Utils", "HUDs & Panels", "Q", "Show/hide dockable xtra QC Utils panel (Hold Q to peek)"),

            # Annotations
            ("annot.toggle_pen", "Toggle Pen Drawing Tool", "Annotations", "D", "Enable or disable freehand pencil draw"),
            ("annot.undo", "Undo Annotation", "Annotations", "Ctrl+Z", "Undo last drawing action on active frame"),
            ("annot.redo", "Redo Annotation", "Annotations", "Ctrl+Shift+Z", "Redo last undone drawing action on active frame"),
            ("annot.clear_frame", "Clear Frame Annotations", "Annotations", "Delete", "Erase all drawings on the current frame"),
            ("annot.next_keyframe", "Next Annotated Frame", "Annotations", ".", "Jump to next frame containing drawing notes"),
            ("annot.prev_keyframe", "Previous Annotated Frame", "Annotations", ",", "Jump to previous frame containing drawing notes"),
            ("annot.toggle_palette", "Toggle Annotation Toolbox", "Annotations", "X", "Show/hide top annotation toolbox"),
            ("annot.manager", "Open Annotation Manager", "Annotations", "Ctrl+Shift+A", "Open review note and frame export dialog"),
            ("annot.export", "Export Annotations...", "Annotations", "Ctrl+E", "Export annotation drawings and review notes"),

            # Sources & Session Manager
            ("playlist.toggle", "Toggle Sources & Session Manager", "Playlist & Dailies", "`", "Show/hide Sources & Session Manager dock"),

            # Pipeline Versioning
            ("version.up", "Version Up (Next Version)", "Pipeline Versioning", "Alt+Up", "Advance to next iteration/version (e.g. v001 -> v002)"),
            ("version.down", "Version Down (Previous Version)", "Pipeline Versioning", "Alt+Down", "Revert to previous iteration/version (e.g. v002 -> v001)"),

            # Sessions & File
            ("session.new", "New Session", "File & Sessions", "Ctrl+N", "Start clean session"),
            ("session.close", "Close / Clear Session", "File & Sessions", "Ctrl+Shift+N", "Close active session and clear canvas, player, and memory"),
            ("session.open_media", "Open Media File...", "File & Sessions", "Ctrl+O", "Load video or image sequence"),
            ("session.open", "Open Session...", "File & Sessions", "Ctrl+Shift+O", "Load OpenReview session .json file"),
            ("session.save", "Save Session", "File & Sessions", "Ctrl+S", "Save session state"),
            ("session.save_as", "Save Session As...", "File & Sessions", "Ctrl+Shift+S", "Save session under new file path"),
            ("session.export", "Export Media...", "File & Sessions", "Ctrl+Shift+E", "Export sequence or frame render"),
            ("app.preferences", "Preferences...", "File & Sessions", "Ctrl+,", "Open Preferences dialog"),
            ("app.clear_cache", "Clear RAM Cache", "File & Sessions", "Ctrl+Shift+C", "Flush all cached image frames from memory"),
            ("app.clear_all_cache", "Clear All Source Cache", "File & Sessions", "Ctrl+Delete", "Flush all cached image frames across all sources from memory"),
            ("app.toggle_cache", "Start In/Out Region Cache", "Playback & Transport", "Shift+C", "Start In/Out Region RAM caching"),
            ("app.quit", "Quit OpenReview", "File & Sessions", "Ctrl+Q", "Exit application"),
            ("app.escape", "Cancel / Deselect Tool", "File & Sessions", "Escape", "Deselect active tool or exit drawing mode"),
        ]

        for action_id, name, cat, default_key, desc in defs:
            self._actions[action_id] = ShortcutAction(
                action_id=action_id,
                name=name,
                category=cat,
                default_shortcut=default_key,
                current_shortcut=default_key,
                description=desc,
            )

        self._aliases = {
            "Ctrl+B": "hud.toggle_file_browser",
            "Ctrl+b": "hud.toggle_file_browser",
            "Ctrl+G": "timeline.goto_frame",
            "N": "view.filter_toggle",
            "n": "view.filter_toggle",
            "M": "mark.bookmark_toggle",
            "m": "mark.bookmark_toggle",
            "J": "playback.shuttle_rev",
            "j": "playback.shuttle_rev",
            "K": "playback.shuttle_stop",
            "k": "playback.shuttle_stop",
            "L": "playback.shuttle_fwd",
            "l": "playback.shuttle_fwd",
            "Shift+L": "playback.shuttle_fwd",
            "Shift+l": "playback.shuttle_fwd",
            "PageDown": "mark.bookmark_segment_next",
            "PageUp": "mark.bookmark_segment_prev",
            "Alt+C": "hud.color_inspector",
            "F5": "hud.color_inspector",
            "Y": "channel.luma",
            "y": "channel.luma",
            "E": "color.exp_reset",
            "e": "color.exp_reset",
            "+": "color.gain_up",
            "=": "color.gain_up",
            "Plus": "color.gain_up",
            "Equal": "color.gain_up",
            "-": "color.gain_down",
            "Minus": "color.gain_down",
            "_": "color.gain_down",
            "Underscore": "color.gain_down",
            "Alt++": "color.exp_up",
            "Alt+=": "color.exp_up",
            "Alt+Plus": "color.exp_up",
            "Alt+Equal": "color.exp_up",
            "Alt+-": "color.exp_down",
            "Alt+_": "color.exp_down",
            "Alt+Minus": "color.exp_down",
            "Alt+Underscore": "color.exp_down",
            "Shift++": "color.gamma_up",
            "Shift+=": "color.gamma_up",
            "Shift+Plus": "color.gamma_up",
            "Shift+-": "color.gamma_down",
            "Shift+_": "color.gamma_down",
            "Shift+Minus": "color.gamma_down",
            "Ctrl+Shift++": "color.sat_up",
            "Ctrl+Shift+=": "color.sat_up",
            "Ctrl+Shift+Plus": "color.sat_up",
            "Ctrl+Shift+-": "color.sat_down",
            "Ctrl+Shift+_": "color.sat_down",
            "Ctrl+Shift+Minus": "color.sat_down",
            # Numberpad +/- Aliases
            "Num++": "color.gain_up",
            "Num+-": "color.gain_down",
            "Alt+Num++": "color.exp_up",
            "Alt+Num+-": "color.exp_down",
            "Shift+Num++": "color.gamma_up",
            "Shift+Num+-": "color.gamma_down",
            "Ctrl+Shift+Num++": "color.sat_up",
            "Ctrl+Shift+Num+-": "color.sat_down",
            "Shift+I": "color.invert_toggle",
            "Shift+i": "color.invert_toggle",
            "Ctrl+F": "view.fit_width",
            "Ctrl+f": "view.fit_width",
            "Alt+F": "view.fit_width",
            "Alt+f": "view.fit_width",
            "Shift+F": "playback.set_fps",
            "Shift+f": "playback.set_fps",
            "Ctrl+Alt+E": "color.exp_reset",
            "Ctrl+E": "annot.export",
            "Ctrl+e": "annot.export",
            "Ctrl+Alt+G": "color.gamma_reset",
            "Ctrl+Alt+R": "color.reset_all",
            "Shift+Home": "color.reset_all",
            "Ctrl+Delete": "app.clear_all_cache",
            "Ctrl+Del": "app.clear_all_cache",
            "/": "color.adjustments",
            "Slash": "color.adjustments",
            "Shift+T": "timeline.magnifier_toggle",
            "Shift+t": "timeline.magnifier_toggle",
            "Ctrl+=": "timeline.zoom_in",
            "Ctrl+-": "timeline.zoom_out",
            "\\": "mark.clear_in_out",
            "Backslash": "mark.clear_in_out",
            "`": "playlist.toggle",
            "QuoteLeft": "playlist.toggle",
            "Ctrl+R": "media.reload_frame",
            "Ctrl+Shift+R": "media.reload_sequence",
            "Esc": "app.escape",
            "Escape": "app.escape",
            "Del": "annot.clear_frame",
            "Delete": "annot.clear_frame",
            "Backspace": "annot.clear_frame",
            "X": "annot.toggle_palette",
            "x": "annot.toggle_palette",
            "Ctrl+Z": "annot.undo",
            "Ctrl+z": "annot.undo",
            "Ctrl+Shift+Z": "annot.redo",
            "Ctrl+Shift+z": "annot.redo",
            "Ctrl+Y": "annot.redo",
            "Ctrl+y": "annot.redo",
            "Ctrl+Shift+N": "session.close",
            "Ctrl+Shift+n": "session.close",
            "V": "hud.waveform",
            "v": "hud.waveform",
            "Shift+V": "hud.vectorscope",
            "Shift+v": "hud.vectorscope",
        }

    def _rebuild_key_map(self) -> None:
        """Rebuild the fast lookup key-to-action dictionary."""
        self._key_map.clear()
        for act in self._actions.values():
            key = act.effective_shortcut.strip()
            if key:
                norm = self.normalize_key_sequence(key)
                if norm:
                    self._key_map[norm] = act.action_id

    @staticmethod
    def normalize_key_sequence(seq_str: str) -> str:
        """Normalize a key sequence string to canonical Qt format."""
        if not seq_str:
            return ""
        # Clean up common variations like 'ctrl +' -> 'Ctrl+Plus'
        seq = QKeySequence(seq_str)
        norm = seq.toString(QKeySequence.SequenceFormat.PortableText)
        return norm if norm else seq_str

    def get_actions(self) -> List[ShortcutAction]:
        """Return list of all registered actions."""
        return list(self._actions.values())

    def get_action(self, action_id: str) -> Optional[ShortcutAction]:
        return self._actions.get(action_id)

    def bind_callback(self, action_id: str, callback: Callable[[], None]) -> None:
        """Bind an executable callback function to an action ID."""
        if action_id in self._actions:
            self._actions[action_id].callback = callback

    def set_shortcut(self, action_id: str, key_str: str) -> None:
        """Set a custom shortcut for an action."""
        if action_id in self._actions:
            norm = self.normalize_key_sequence(key_str)
            self._actions[action_id].current_shortcut = norm
            self._rebuild_key_map()
            self._save_to_preferences()
            self.shortcuts_changed.emit()

    def set_shortcuts_batch(self, shortcuts: Dict[str, str], persist: bool = True) -> None:
        """Set multiple custom shortcuts in batch, rebuilding keymap and persisting once."""
        changed = False
        for action_id, key_str in shortcuts.items():
            if action_id in self._actions:
                norm = self.normalize_key_sequence(key_str)
                if self._actions[action_id].current_shortcut != norm:
                    self._actions[action_id].current_shortcut = norm
                    changed = True
        if changed:
            self._rebuild_key_map()
            if persist:
                self._save_to_preferences()
            self.shortcuts_changed.emit()

    def reset_to_default(self, action_id: str) -> None:
        """Reset a specific action to its default shortcut."""
        if action_id in self._actions:
            act = self._actions[action_id]
            act.current_shortcut = act.default_shortcut
            self._rebuild_key_map()
            self._save_to_preferences()
            self.shortcuts_changed.emit()

    def reset_all_defaults(self) -> None:
        """Reset all actions to their system defaults."""
        for act in self._actions.values():
            act.current_shortcut = act.default_shortcut
        self._rebuild_key_map()
        self._save_to_preferences()
        self.shortcuts_changed.emit()

    def find_conflict(self, key_str: str, exclude_action_id: Optional[str] = None) -> Optional[ShortcutAction]:
        """Check if key_str is already assigned to another action."""
        norm = self.normalize_key_sequence(key_str)
        if not norm:
            return None
        existing_id = self._key_map.get(norm)
        if existing_id and existing_id != exclude_action_id:
            return self._actions.get(existing_id)
        return None

    def load_from_preferences(self) -> None:
        """Load stored shortcut overrides from UserPreferences."""
        prefs = PreferencesManager.instance().prefs
        if hasattr(prefs, "shortcuts") and isinstance(prefs.shortcuts, dict):
            # Clear all color shortcuts from previous sessions
            color_keys = [k for k in prefs.shortcuts if k.startswith("color.")]
            for ck in color_keys:
                del prefs.shortcuts[ck]
            if prefs.shortcuts.get("channel.luma") in ("L", "l"):
                del prefs.shortcuts["channel.luma"]
            if prefs.shortcuts.get("playback.shuttle_fwd") in ("Shift+L", "Shift+l"):
                del prefs.shortcuts["playback.shuttle_fwd"]
            for action_id, custom_key in prefs.shortcuts.items():
                if action_id in self._actions and custom_key is not None:
                    self._actions[action_id].current_shortcut = self.normalize_key_sequence(custom_key)
        self._rebuild_key_map()

    def _save_to_preferences(self) -> None:
        """Persist customized shortcuts into UserPreferences."""
        mgr = PreferencesManager.instance()
        customized = {}
        for action_id, act in self._actions.items():
            if act.is_customized():
                customized[action_id] = act.current_shortcut
        mgr.prefs.shortcuts = customized
        mgr.save()

    def trigger(self, action_id: str) -> bool:
        """Manually trigger an action by ID."""
        act = self._actions.get(action_id)
        if act and act.callback:
            act.callback()
            return True
        return False

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        """
        Global application event filter for robust keyboard shortcut handling.
        Dispatches shortcuts regardless of which child widget currently holds focus,
        while safely respecting text editing widgets.
        """
        if event.type() != QEvent.Type.KeyPress:
            return False

        assert isinstance(event, QKeyEvent)

        # Ignore standalone modifier key presses (Shift, Ctrl, Alt alone)
        key = event.key()
        if key in (Qt.Key.Key_Shift, Qt.Key.Key_Control, Qt.Key.Key_Alt, Qt.Key.Key_Meta):
            return False

        # Check if event target or focused widget belongs to a dialog or if a modal dialog is active.
        # Dialogs must retain native keyboard navigation (Single Enter = Accept, Esc = Reject).
        app = QApplication.instance()
        if app and app.activeModalWidget() is not None:
            return False

        for candidate in (watched, app.focusWidget() if app else None):
            cur_chk = candidate
            while cur_chk is not None:
                if isinstance(cur_chk, QDialog):
                    return False
                cur_chk = cur_chk.parent() if hasattr(cur_chk, "parent") else None

        # Check if watched or focused widget is an editable widget or child of one
        target = watched
        is_text_input = False
        is_canvas_editor = False
        while target is not None:
            if target.__class__.__name__ == "CanvasTextEditor":
                is_canvas_editor = True
                is_text_input = True
                break
            if isinstance(target, (QLineEdit, QTextEdit, QPlainTextEdit, QSpinBox, QDoubleSpinBox)):
                is_text_input = True
                break
            target = target.parent() if hasattr(target, "parent") else None

        if not is_text_input:
            app = QApplication.instance()
            focus_w = app.focusWidget() if app else None
            target = focus_w
            while target is not None:
                if target.__class__.__name__ == "CanvasTextEditor":
                    is_canvas_editor = True
                    is_text_input = True
                    break
                if isinstance(target, (QLineEdit, QTextEdit, QPlainTextEdit, QSpinBox, QDoubleSpinBox)):
                    is_text_input = True
                    break
                target = target.parent() if hasattr(target, "parent") else None

        if is_text_input:
            # If typing inside CanvasTextEditor, do not intercept any keys (it manages Enter/Escape itself)
            if is_canvas_editor:
                return False

            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                app = QApplication.instance()
                cur_focus = app.focusWidget() if app else None

                # Locate spinbox or line edit to commit
                spin_w = None
                curr = cur_focus or target
                while curr is not None:
                    if isinstance(curr, (QSpinBox, QDoubleSpinBox, QAbstractSpinBox)):
                        spin_w = curr
                        break
                    curr = curr.parent() if hasattr(curr, "parent") else None

                if spin_w is not None:
                    try:
                        spin_w.interpretText()
                    except Exception:
                        pass
                    try:
                        spin_w.editingFinished.emit()
                    except Exception:
                        pass
                elif isinstance(cur_focus, QLineEdit):
                    try:
                        cur_focus.returnPressed.emit()
                    except Exception:
                        pass
                    try:
                        cur_focus.editingFinished.emit()
                    except Exception:
                        pass

                # Automatically deselect input field
                if cur_focus:
                    cur_focus.clearFocus()
                if target and hasattr(target, "clearFocus"):
                    target.clearFocus()

                # Transfer focus to viewport for seamless playback and hotkey control
                main_win = None
                if app:
                    for top_w in app.topLevelWidgets():
                        if top_w.__class__.__name__ == "MainWindow":
                            main_win = top_w
                            break
                if main_win and hasattr(main_win, "viewport") and main_win.viewport:
                    main_win.viewport.setFocus()

                event.accept()
                return True

            if key == Qt.Key.Key_Escape:
                app = QApplication.instance()
                cur_focus = app.focusWidget() if app else None
                if cur_focus:
                    cur_focus.clearFocus()
                if target and hasattr(target, "clearFocus"):
                    target.clearFocus()
                main_win = None
                if app:
                    for top_w in app.topLevelWidgets():
                        if top_w.__class__.__name__ == "MainWindow":
                            main_win = top_w
                            break
                if main_win and hasattr(main_win, "viewport") and main_win.viewport:
                    main_win.viewport.setFocus()
                event.accept()
                return True

            return False

        # If a suppression predicate is active (e.g. Text annotation tool active on canvas),
        # suppress all shortcuts except Escape
        if self.is_suppressed():
            if key != Qt.Key.Key_Escape:
                return False

        # Convert the key event to a portable normalized sequence string
        key_combo = event.keyCombination()
        seq = QKeySequence(key_combo)
        key_str = seq.toString(QKeySequence.SequenceFormat.PortableText)

        # Fallback / special-case handling for single characters (e.g. '/', '=', '-')
        if not key_str and event.text():
            key_str = event.text()

        norm = self.normalize_key_sequence(key_str)
        action_id = self._key_map.get(norm)
        if not action_id:
            action_id = self._aliases.get(norm)

        # Dynamic keypad fallback: strip KeypadModifier to match standard shortcuts transparently
        if not action_id and (event.modifiers() & Qt.KeyboardModifier.KeypadModifier):
            try:
                clean_mods = event.modifiers() & ~Qt.KeyboardModifier.KeypadModifier
                clean_combo = QKeyCombination(clean_mods, Qt.Key(event.key()))
                clean_seq = QKeySequence(clean_combo).toString(QKeySequence.SequenceFormat.PortableText)
                clean_norm = self.normalize_key_sequence(clean_seq)
                action_id = self._key_map.get(clean_norm) or self._aliases.get(clean_norm)
            except Exception:
                pass

        # Secondary fallback: match directly by key text if modifier is none (e.g. '/' or 'c' or 'e')
        if not action_id and event.text() and not (event.modifiers() & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier)):
            direct_norm = self.normalize_key_sequence(event.text().upper())
            action_id = self._key_map.get(direct_norm) or self._aliases.get(direct_norm)
            if not action_id:
                action_id = self._key_map.get(event.text()) or self._aliases.get(event.text())

        # Also fallback by key name if single key
        if not action_id:
            try:
                key_enum_name = Qt.Key(key).name.replace("Key_", "")
                action_id = self._key_map.get(key_enum_name) or self._aliases.get(key_enum_name)
            except Exception:
                pass

        if action_id:
            act = self._actions.get(action_id)
            if act and act.callback:
                act.callback()
            event.accept()
            return True

        return False
