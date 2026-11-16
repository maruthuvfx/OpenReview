"""
Tests for OpenReview Option 4: Audio Playback Engine & Scrubbing.
Covers AudioEngine, sub-frame offsets, volume controls, mute toggling,
AudioSettingsDialog UI, and MainWindow audio integration.
"""
from __future__ import annotations

import os
from pathlib import Path
import pytest
from PySide6.QtCore import Qt

from openreview.audio.audio_engine import AudioEngine
from openreview.ui.audio_dialog import AudioSettingsDialog
from openreview.ui.main_window import MainWindow


def test_audio_engine_volume_and_mute(qtbot):
    """Test volume adjustment, clamping, and mute state toggling."""
    engine = AudioEngine()

    # Initial volume 1.0 (100%) and unmuted
    assert engine.get_volume() == 1.0
    assert not engine.is_muted()

    # Set volume within range
    engine.set_volume(0.65)
    assert abs(engine.get_volume() - 0.65) < 0.001

    # Clamping tests
    engine.set_volume(1.5)
    assert engine.get_volume() == 1.0

    engine.set_volume(-0.2)
    assert engine.get_volume() == 0.0

    # Toggle Mute
    assert engine.toggle_mute() is True
    assert engine.is_muted() is True

    assert engine.toggle_mute() is False
    assert engine.is_muted() is False


def test_audio_engine_offsets_calculation():
    """Verify exact lipsync audio offset calculation in seconds and frames."""
    engine = AudioEngine()
    engine.set_sequence_context(start_frame=1001, fps=24.0)

    # At start frame with 0 offset -> 0 ms
    assert engine.frame_to_ms(1001) == 0

    # Frame 1025 is exactly 24 frames later (1.0 second) -> 1000 ms
    assert engine.frame_to_ms(1025) == 1000

    # Apply +0.500 second offset
    engine.set_offset_seconds(0.5)
    assert engine.get_offset_seconds() == 0.5
    # Frame 1001 + 0.5s -> 500 ms
    assert engine.frame_to_ms(1001) == 500
    # Frame 1025 + 0.5s -> 1500 ms
    assert engine.frame_to_ms(1025) == 1500

    # Apply +12 frames offset (0.5 sec at 24 fps) in addition to 0.5s seconds offset -> 1.0 sec total
    engine.set_offset_frames(12)
    assert engine.get_offset_frames() == 12
    assert engine.frame_to_ms(1001) == 1000

    # Reset offsets -> back to original 0ms
    engine.reset_offsets()
    assert engine.get_offset_seconds() == 0.0
    assert engine.get_offset_frames() == 0
    assert engine.frame_to_ms(1001) == 0


def test_audio_engine_scrubbing_and_sync():
    """Test scrubbing enable toggle and synchronization state handling."""
    engine = AudioEngine()
    assert engine.is_scrubbing_enabled() is True

    engine.set_scrubbing_enabled(False)
    assert engine.is_scrubbing_enabled() is False

    # Sync to frame without loaded media should safely do nothing
    engine.sync_to_frame(frame=1050, is_playing=True, shuttle_speed=1.0)
    engine.sync_to_frame(frame=1050, is_playing=False)
    engine.scrub_frame(frame=1020)


def test_audio_settings_dialog_ui(qtbot):
    """Test AudioSettingsDialog slider updates, offsets, and reset button."""
    engine = AudioEngine()
    engine.set_volume(0.8)
    engine.set_offset_seconds(1.25)
    engine.set_offset_frames(5)

    dlg = AudioSettingsDialog(engine)
    qtbot.addWidget(dlg)
    dlg.show()

    # Initial UI values match engine
    assert dlg.slider_volume.value() == 80
    assert dlg.lbl_volume.text() == "80%"
    assert abs(dlg.spin_offset_sec.value() - 1.25) < 0.001
    assert dlg.spin_offset_frames.value() == 5

    # Changing slider updates engine
    dlg.slider_volume.setValue(45)
    assert abs(engine.get_volume() - 0.45) < 0.01

    # Changing spinbox updates engine
    dlg.spin_offset_sec.setValue(-0.25)
    assert abs(engine.get_offset_seconds() - (-0.25)) < 0.001

    # Reset offsets button
    dlg.btn_reset_offsets.click()
    assert engine.get_offset_seconds() == 0.0
    assert engine.get_offset_frames() == 0
    assert dlg.spin_offset_sec.value() == 0.0
    assert dlg.spin_offset_frames.value() == 0

    dlg.close()


def test_main_window_audio_integration(qtbot):
    """Test MainWindow Audio menu, mute toggle, and settings dialog trigger."""
    win = MainWindow()
    qtbot.addWidget(win)
    win.show()

    assert hasattr(win, "audio_engine")
    assert win.audio_engine is not None

    # Test Mute toggle shortcut (Ctrl+M)
    assert not win.audio_engine.is_muted()
    win._on_toggle_audio_mute()
    assert win.audio_engine.is_muted()
    win._on_toggle_audio_mute()
    assert not win.audio_engine.is_muted()

    # Test volume preset action
    win.audio_engine.set_volume(0.5)
    assert abs(win.audio_engine.get_volume() - 0.5) < 0.01

    win.close()


class _DummyPlayer:
    """Lightweight QMediaPlayer stub for deterministic sync state testing."""

    def __init__(self):
        from PySide6.QtMultimedia import QMediaPlayer
        self._state = QMediaPlayer.PlaybackState.StoppedState
        self._pos = 0
        self._rate = 1.0
        self.seek_calls: list[int] = []

    def playbackState(self):
        return self._state

    def play(self):
        from PySide6.QtMultimedia import QMediaPlayer
        self._state = QMediaPlayer.PlaybackState.PlayingState

    def pause(self):
        from PySide6.QtMultimedia import QMediaPlayer
        self._state = QMediaPlayer.PlaybackState.PausedState

    def stop(self):
        from PySide6.QtMultimedia import QMediaPlayer
        self._state = QMediaPlayer.PlaybackState.StoppedState

    def position(self) -> int:
        return self._pos

    def setPosition(self, pos: int) -> None:
        self._pos = int(pos)
        self.seek_calls.append(int(pos))

    def playbackRate(self) -> float:
        return self._rate

    def setPlaybackRate(self, rate: float) -> None:
        self._rate = float(rate)

    def setSource(self, _url) -> None:
        pass


def test_audio_engine_play_pause_shuttle_and_loop_sync():
    """Verify AudioEngine syncs playhead jumps, scales shuttle speeds, and pauses immediately."""
    from PySide6.QtMultimedia import QMediaPlayer

    engine = AudioEngine()
    dummy = _DummyPlayer()
    engine._player = dummy
    engine._media_path = "dummy.wav"
    engine.set_sequence_context(start_frame=1, fps=25.0)  # 40ms per frame

    # 1. Play start at frame 11 (400ms) -> must seek and play at 1.0x
    engine.sync_to_frame(frame=11, is_playing=True, shuttle_speed=1.0)
    assert dummy.playbackState() == QMediaPlayer.PlaybackState.PlayingState
    assert dummy.position() == 400
    assert abs(dummy.playbackRate() - 1.0) < 0.001

    # 2. Sequential advance to frame 12 (440ms) with small drift (dummy at 415ms -> 25ms diff <= 85ms)
    dummy._pos = 415
    prev_seeks = len(dummy.seek_calls)
    engine.sync_to_frame(frame=12, is_playing=True, shuttle_speed=1.0)
    assert len(dummy.seek_calls) == prev_seeks  # No stutter seek on normal sequential tick

    # 3. Loop wrap-around jump (frame 12 -> frame 1) -> must immediately seek to 0ms even if drift were small
    engine.sync_to_frame(frame=1, is_playing=True, shuttle_speed=1.0)
    assert dummy.position() == 0

    # 4. Forward shuttle 0.5x and 2.0x -> scales playbackRate and stays playing
    engine.sync_to_frame(frame=2, is_playing=True, shuttle_speed=0.5)
    assert dummy.playbackState() == QMediaPlayer.PlaybackState.PlayingState
    assert abs(dummy.playbackRate() - 0.5) < 0.001

    engine.sync_to_frame(frame=3, is_playing=True, shuttle_speed=2.0)
    assert dummy.playbackState() == QMediaPlayer.PlaybackState.PlayingState
    assert abs(dummy.playbackRate() - 2.0) < 0.001

    # 5. Reverse shuttle (-1.0x) and extreme fast-forward (4.0x) -> pauses audio
    engine.sync_to_frame(frame=2, is_playing=True, shuttle_speed=-1.0)
    assert dummy.playbackState() == QMediaPlayer.PlaybackState.PausedState

    engine.sync_to_frame(frame=3, is_playing=True, shuttle_speed=1.0)
    assert dummy.playbackState() == QMediaPlayer.PlaybackState.PlayingState
    engine.sync_to_frame(frame=4, is_playing=True, shuttle_speed=4.0)
    assert dummy.playbackState() == QMediaPlayer.PlaybackState.PausedState

    # 6. Explicit pause(frame=26) -> stops scrub timer, pauses player, parks at 1000ms
    engine.sync_to_frame(frame=25, is_playing=True, shuttle_speed=1.0)
    assert dummy.playbackState() == QMediaPlayer.PlaybackState.PlayingState
    engine.pause(frame=26)
    assert dummy.playbackState() == QMediaPlayer.PlaybackState.PausedState
    assert dummy.position() == 1000


def test_main_window_pause_stops_audio_immediately(qtbot):
    """Ensure pausing MainWindow playback clock immediately pauses AudioEngine."""
    from PySide6.QtMultimedia import QMediaPlayer

    win = MainWindow()
    qtbot.addWidget(win)
    win.show()

    dummy = _DummyPlayer()
    win.audio_engine._player = dummy
    win.audio_engine._media_path = "dummy.wav"
    win.clock.set_fps(24.0)
    win.clock.set_range(1, 100)
    win.clock.set_frame(10)

    # Start playback -> audio starts playing immediately
    win.clock.play()
    assert dummy.playbackState() == QMediaPlayer.PlaybackState.PlayingState

    # Pause playback -> audio must immediately transition to PausedState at current frame
    win.clock.pause()
    assert dummy.playbackState() == QMediaPlayer.PlaybackState.PausedState
    assert dummy.position() == win.audio_engine.frame_to_ms(win.clock.current_frame)

    # Paused scrub burst starts playing briefly, then _on_scrub_settled guarantees pause
    win._on_timeline_seek(25)
    assert dummy.playbackState() == QMediaPlayer.PlaybackState.PlayingState
    win._on_scrub_settled()
    assert dummy.playbackState() == QMediaPlayer.PlaybackState.PausedState

    win.close()


def test_micro_scrubber_deleted_qiodevice_recovery():
    """Verify micro-scrubber gracefully recovers and does not raise RuntimeError if QIODevice is deleted."""
    from openreview.audio.micro_scrubber import MicroBufferAudioScrubber
    import numpy as np

    scrubber = MicroBufferAudioScrubber(sample_rate=48000)

    # Provide synthetic PCM buffer
    scrubber._pcm_data = np.zeros((48000, 2), dtype=np.int16)

    # Simulate underlying QIODevice deletion/invalidation
    class DeletedMockIODevice:
        def isWritable(self):
            raise RuntimeError("libshiboken: Internal C++ object (PySide6.QtCore.QIODevice) already deleted.")

    scrubber._io = DeletedMockIODevice()

    # Calling scrub must not raise RuntimeError and must gracefully handle/recover
    result = scrubber.scrub(target_ms=100, frame=5, fps=24.0)
    assert isinstance(result, bool)

    # Also test AudioEngine scrub_frame does not raise
    engine = AudioEngine()
    engine._micro_scrubber = scrubber
    engine.scrub_frame(10)

