"""
OpenReview High-Precision Audio Playback Engine.
Provides synced audio playback, volume controls, mute toggling,
timeline audio scrubbing, and frame/second audio track offsets.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional
from PySide6.QtCore import QObject, Signal, QUrl, QTimer
from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput

from openreview.audio.micro_scrubber import MicroBufferAudioScrubber


class AudioEngine(QObject):
    """
    Studio-grade Audio Playback Engine for OpenReview.
    Synchronizes audio output to video/sequence playback with sub-frame accuracy.
    """

    volume_changed = Signal(float)  # 0.0 to 1.0
    mute_changed = Signal(bool)
    offsets_changed = Signal(float, int)  # (offset_sec, offset_frames)
    scrubbing_changed = Signal(bool)
    track_loaded = Signal(str)
    track_unloaded = Signal()

    SUPPORTED_AUDIO_EXTENSIONS = {
        ".wav", ".mp3", ".aac", ".flac", ".ogg", ".aiff", ".m4a",
        ".mov", ".mp4", ".mkv", ".avi", ".webm", ".wmv", ".flv", ".m4v", ".mpg", ".mpeg", ".ts", ".m2ts",
    }

    def __init__(self, parent: Optional[QObject] = None):
        super().__init__(parent)
        self._media_path: Optional[str] = None
        self._volume: float = 1.0
        self._is_muted: bool = False
        self._scrubbing_enabled: bool = True
        self._offset_seconds: float = 0.0
        self._offset_frames: int = 0
        self._fps: float = 24.0
        self._start_frame: int = 1001
        self._last_synced_frame: Optional[int] = None

        # QtMultimedia Player & Audio Output
        self._player: Optional[QMediaPlayer] = None
        self._audio_output: Optional[QAudioOutput] = None
        self._init_multimedia()

        # Ultra-low latency micro-buffer audio scrubbing engine
        self._micro_scrubber = MicroBufferAudioScrubber(sample_rate=48000, parent=self)
        self._micro_scrubber.set_volume(self._volume)
        self._micro_scrubber.set_muted(self._is_muted)

        # Scrubbing burst timer (fallback)
        self._scrub_timer = QTimer(self)
        self._scrub_timer.setSingleShot(True)
        self._scrub_timer.timeout.connect(self._stop_scrub_burst)

    def _init_multimedia(self) -> None:
        try:
            self._player = QMediaPlayer(self)
            self._audio_output = QAudioOutput(self)
            self._player.setAudioOutput(self._audio_output)
            self._audio_output.setVolume(self._volume)
            self._audio_output.setMuted(self._is_muted)
        except Exception as e:
            print(f"[AudioEngine] Warning: QtMultimedia init failed: {e}")
            self._player = None
            self._audio_output = None

    # --------------------------------------------------------------------------
    # Track Loading & Verification
    # --------------------------------------------------------------------------
    def is_supported_file(self, file_path: str | Path) -> bool:
        ext = Path(file_path).suffix.lower()
        return ext in self.SUPPORTED_AUDIO_EXTENSIONS

    def load_audio(self, file_path: str | Path) -> bool:
        """Load an audio or video file with audio track into player."""
        p = Path(file_path)
        if not p.is_file():
            return False

        self._scrub_timer.stop()
        self._last_synced_frame = None
        self._media_path = str(p.resolve())
        if self._player:
            self._player.stop()
            self._player.setSource(QUrl.fromLocalFile(self._media_path))

        if self._micro_scrubber:
            self._micro_scrubber.load_audio(self._media_path)

        self.track_loaded.emit(self._media_path)
        return True

    def unload_audio(self) -> None:
        """Unload active audio file."""
        self._scrub_timer.stop()
        self._last_synced_frame = None
        if self._player:
            self._player.stop()
            self._player.setSource(QUrl())
        if self._micro_scrubber:
            self._micro_scrubber.unload()
        self._media_path = None
        self.track_unloaded.emit()

    def has_audio(self) -> bool:
        """True if an audio file or video with audio track is loaded."""
        return self._media_path is not None and self._player is not None

    @property
    def media_path(self) -> Optional[str]:
        return self._media_path

    # --------------------------------------------------------------------------
    # Volume & Mute Controls
    # --------------------------------------------------------------------------
    def get_volume(self) -> float:
        return self._volume

    def set_volume(self, volume: float) -> None:
        """Set volume (0.0 to 1.0)."""
        self._volume = max(0.0, min(1.0, float(volume)))
        if self._audio_output:
            self._audio_output.setVolume(self._volume)
        if hasattr(self, "_micro_scrubber") and self._micro_scrubber:
            self._micro_scrubber.set_volume(self._volume)
        self.volume_changed.emit(self._volume)

    def is_muted(self) -> bool:
        return self._is_muted

    def set_muted(self, muted: bool) -> None:
        """Set mute state."""
        self._is_muted = bool(muted)
        if self._audio_output:
            self._audio_output.setMuted(self._is_muted)
        if hasattr(self, "_micro_scrubber") and self._micro_scrubber:
            self._micro_scrubber.set_muted(self._is_muted)
        self.mute_changed.emit(self._is_muted)

    def toggle_mute(self) -> bool:
        """Toggle mute state. Returns new mute state."""
        self.set_muted(not self._is_muted)
        return self._is_muted

    # --------------------------------------------------------------------------
    # Scrubbing Controls
    # --------------------------------------------------------------------------
    def is_scrubbing_enabled(self) -> bool:
        return self._scrubbing_enabled

    def set_scrubbing_enabled(self, enabled: bool) -> None:
        self._scrubbing_enabled = bool(enabled)
        if not self._scrubbing_enabled:
            self._scrub_timer.stop()
        self.scrubbing_changed.emit(self._scrubbing_enabled)

    # --------------------------------------------------------------------------
    # Audio Offset Management
    # --------------------------------------------------------------------------
    def get_offset_seconds(self) -> float:
        return self._offset_seconds

    def set_offset_seconds(self, seconds: float) -> None:
        """Set track offset in fractional seconds."""
        self._offset_seconds = float(seconds)
        self.offsets_changed.emit(self._offset_seconds, self._offset_frames)

    def get_offset_frames(self) -> int:
        return self._offset_frames

    def set_offset_frames(self, frames: int) -> None:
        """Set track offset in whole frames."""
        self._offset_frames = int(frames)
        self.offsets_changed.emit(self._offset_seconds, self._offset_frames)

    def reset_offsets(self) -> None:
        """Reset all seconds and frame offsets back to zero."""
        self._offset_seconds = 0.0
        self._offset_frames = 0
        self.offsets_changed.emit(0.0, 0)

    # --------------------------------------------------------------------------
    # Time & Position Calculation
    # --------------------------------------------------------------------------
    def set_sequence_context(self, start_frame: int, fps: float) -> None:
        """Set project sequence context for frame-to-time conversion."""
        self._start_frame = start_frame
        self._fps = max(1.0, float(fps))

    def frame_to_ms(self, frame: int) -> int:
        """
        Calculate audio timestamp in milliseconds for given frame.
        Includes configured seconds and frame offsets.
        """
        fps = self._fps if self._fps > 0 else 24.0
        frame_delta = frame - self._start_frame
        base_sec = frame_delta / fps
        total_offset_sec = self._offset_seconds + (self._offset_frames / fps)
        target_sec = base_sec + total_offset_sec
        return max(0, int(round(target_sec * 1000.0)))

    # --------------------------------------------------------------------------
    # Playback Synchronization
    # --------------------------------------------------------------------------
    def pause(self, frame: Optional[int] = None) -> None:
        """Immediately pause audio playback and cancel any active scrub burst."""
        self._scrub_timer.stop()
        if hasattr(self, "_micro_scrubber") and self._micro_scrubber:
            self._micro_scrubber.stop()
        if not self.has_audio() or not self._player:
            return
        if self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self._player.pause()
        if frame is not None:
            target_ms = self.frame_to_ms(frame)
            self._player.setPosition(target_ms)
            self._last_synced_frame = frame

    def sync_to_frame(self, frame: int, is_playing: bool, shuttle_speed: float = 1.0) -> None:
        """
        Synchronize audio playback with video clock state.
        If playing forward between 0.1x and 2.0x speed, plays audio at scaled rate.
        If paused, stopped, in reverse, or shuttling at > 2.0x speed, immediately pauses audio.
        """
        if not self.has_audio() or not self._player:
            return

        target_ms = self.frame_to_ms(frame)

        if is_playing and 0.1 <= shuttle_speed <= 2.0:
            self._scrub_timer.stop()
            if hasattr(self, "_micro_scrubber") and self._micro_scrubber:
                self._micro_scrubber.stop()
            if abs(self._player.playbackRate() - shuttle_speed) > 0.01:
                self._player.setPlaybackRate(float(shuttle_speed))

            was_playing = self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
            is_discontinuous = (
                self._last_synced_frame is None
                or frame < self._last_synced_frame
                or (frame - self._last_synced_frame) > 2
            )
            current_pos = self._player.position()
            # Re-sync immediately when starting playback, looping/jumping, or drifting > 85ms (~2 frames)
            if not was_playing or is_discontinuous or abs(current_pos - target_ms) > 85:
                self._player.setPosition(target_ms)
            if not was_playing:
                self._player.play()
            self._last_synced_frame = frame
        else:
            # Paused, reverse playback, or extreme fast-forward (> 2x)
            self._scrub_timer.stop()
            if self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
                self._player.pause()
            self._player.setPosition(target_ms)
            self._last_synced_frame = frame

    def scrub_frame(self, frame: int) -> None:
        """
        Scrub audio at given frame: uses low-latency micro-buffer engine with smooth de-clicking.
        Falls back to short QMediaPlayer burst if PCM data is still decoding.
        """
        if not self.has_audio() or not self._scrubbing_enabled or self._is_muted:
            return

        target_ms = self.frame_to_ms(frame)
        self._last_synced_frame = frame

        # 1. Ultra-Low Latency Micro-Buffer Engine (Sub-millisecond, anti-click, reverse scrub)
        if hasattr(self, "_micro_scrubber") and self._micro_scrubber and self._micro_scrubber.has_pcm():
            self._scrub_timer.stop()
            if self._player and self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
                self._player.pause()
            if self._micro_scrubber.scrub(target_ms, frame, self._fps):
                return

        # 2. Fallback to QMediaPlayer burst
        if not self._player:
            return

        self._player.setPosition(target_ms)
        if abs(self._player.playbackRate() - 1.0) > 0.01:
            self._player.setPlaybackRate(1.0)
        self._player.play()
        self._scrub_timer.start(60)

    def _stop_scrub_burst(self) -> None:
        if self._player and self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self._player.pause()
