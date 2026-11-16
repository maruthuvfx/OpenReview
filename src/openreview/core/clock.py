"""
High-Precision Master Playback Clock.
Controls playback timing, frame advancement, looping, and variable speed shuttle.
"""
from __future__ import annotations

import sys
import time
from typing import Optional, Iterable
from PySide6.QtCore import Qt, QObject, Signal, QTimer, QElapsedTimer


class PlaybackClock(QObject):
    """
    High-precision master playback clock supporting:
    - Target FPS (24, 25, 29.97, 30, 48, 60, etc.)
    - Sub-millisecond timing via QElapsedTimer
    - In/Out range looping and bounce modes
    - Real-time measured FPS calculation
    - Shuttle speeds (1x, 2x, 4x, -1x, -2x, -4x)
    """

    frame_changed = Signal(int)
    playback_state_changed = Signal(bool)
    fps_measured = Signal(float)
    fps_changed = Signal(float)
    range_changed = Signal(int, int)  # start, end
    in_out_changed = Signal(int, int)  # in_frame, out_frame
    shuttle_speed_changed = Signal(float)  # multiplier, e.g. 1.0, 2.0, -1.0
    loop_mode_changed = Signal(str)  # 'cycle', 'ping-pong', 'once'
    bookmarks_changed = Signal(set)  # set of bookmarked frame ints

    def __init__(
        self,
        start_frame: int = 1001,
        end_frame: int = 1200,
        fps: float = 24.0,
        play_all_frames: bool = True,
        parent: Optional[QObject] = None,
    ):
        super().__init__(parent)
        self._start_frame = start_frame
        self._end_frame = end_frame
        self._in_frame = start_frame
        self._out_frame = end_frame
        self._current_frame = start_frame
        self._fps = float(fps)
        self._shuttle_speed = 1.0  # multiplier: 1.0, 2.0, -1.0, etc.
        self._loop_mode = "cycle"  # 'cycle', 'once', 'ping-pong'
        self._ping_pong_forward = True
        self._bookmarks: set[int] = set()

        self._is_playing = False
        self._play_all_frames = play_all_frames

        # High-resolution timing
        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.timeout.connect(self._on_tick)

        self._elapsed_timer = QElapsedTimer()
        self._last_tick_time_ns = 0
        self._accumulated_ns = 0.0

        # FPS measurement
        self._frame_counter = 0
        self._fps_timer = QElapsedTimer()
        self._measured_fps = float(fps)

    # --------------------------------------------------------------------------
    # Properties
    # --------------------------------------------------------------------------
    @property
    def current_frame(self) -> int:
        return self._current_frame

    @property
    def start_frame(self) -> int:
        return self._start_frame

    @property
    def end_frame(self) -> int:
        return self._end_frame

    @property
    def in_frame(self) -> int:
        return self._in_frame

    @property
    def out_frame(self) -> int:
        return self._out_frame

    @property
    def fps(self) -> float:
        return self._fps

    @property
    def duration(self) -> int:
        """Total frame duration of the active timeline range (end_frame - start_frame + 1)."""
        return max(0, self._end_frame - self._start_frame + 1)

    @property
    def in_out_duration(self) -> int:
        """Total frame duration of the active in/out range (out_frame - in_frame + 1)."""
        return max(0, self._out_frame - self._in_frame + 1)

    @property
    def is_playing(self) -> bool:
        return self._is_playing

    @property
    def measured_fps(self) -> float:
        return self._measured_fps

    @property
    def loop_mode(self) -> str:
        return self._loop_mode

    @property
    def shuttle_speed(self) -> float:
        return self._shuttle_speed

    @property
    def play_all_frames(self) -> bool:
        return self._play_all_frames

    def set_play_all_frames(self, enabled: bool) -> None:
        self._play_all_frames = bool(enabled)

    # --------------------------------------------------------------------------
    # Playback Controls
    # --------------------------------------------------------------------------
    def play(self) -> None:
        """Start playback."""
        if self._is_playing:
            return
        self._is_playing = True
        self._frame_counter = 0
        self._fps_timer.restart()
        self._elapsed_timer.restart()
        self._last_tick_time_ns = self._elapsed_timer.nsecsElapsed()
        self._accumulated_ns = 0.0

        interval_ms = max(2, min(5, int(round(1000.0 / (self._fps * 8.0)))))  # 5ms tick gives smooth sub-frame pacing without event loop starvation
        if sys.platform == "win32":
            try:
                import ctypes
                ctypes.windll.winmm.timeBeginPeriod(1)
            except Exception:
                pass
        self._timer.start(interval_ms)
        self.playback_state_changed.emit(True)

    def pause(self) -> None:
        """Pause playback."""
        if not self._is_playing:
            return
        self._is_playing = False
        self._timer.stop()
        if sys.platform == "win32":
            try:
                import ctypes
                ctypes.windll.winmm.timeEndPeriod(1)
            except Exception:
                pass
        self.playback_state_changed.emit(False)

    def play_forward(self) -> None:
        """Start forward playback (1.0x). If already playing forward, pause."""
        if self._is_playing and self._shuttle_speed > 0:
            self.pause()
            return
        self._shuttle_speed = 1.0
        self.shuttle_speed_changed.emit(self._shuttle_speed)
        self.play()

    def play_backward(self) -> None:
        """Start reverse playback (-1.0x). If already playing in reverse, pause."""
        if self._is_playing and self._shuttle_speed < 0:
            self.pause()
            return
        self._shuttle_speed = -1.0
        self.shuttle_speed_changed.emit(self._shuttle_speed)
        self.play()

    def toggle_playback(self) -> None:
        """Toggle between play and pause."""
        if self._is_playing:
            self.pause()
        else:
            if self._shuttle_speed == 0:
                self._shuttle_speed = 1.0
                self.shuttle_speed_changed.emit(self._shuttle_speed)
            self.play()

    def set_fps(self, fps: float) -> None:
        """Set target frames per second."""
        if fps <= 0:
            return
        self._fps = float(fps)
        if self._is_playing:
            interval_ms = max(2, min(5, int(round(1000.0 / (self._fps * 8.0)))))
            self._timer.setInterval(interval_ms)
        self.fps_changed.emit(self._fps)

    def set_range(self, start: int, end: int, reset_in_out: bool = True) -> None:
        """
        Set total sequence frame range.
        If reset_in_out is True (default), In and Out marks are set to the full range [start, end].
        Otherwise, existing In/Out marks are clamped to [start, end].
        """
        if end < start:
            end = start
        self._start_frame = start
        self._end_frame = end
        if reset_in_out:
            self._in_frame = start
            self._out_frame = end
        else:
            self._in_frame = max(self._start_frame, min(self._in_frame, self._end_frame))
            self._out_frame = max(self._in_frame, min(self._out_frame, self._end_frame))
        self.range_changed.emit(self._start_frame, self._end_frame)
        self.in_out_changed.emit(self._in_frame, self._out_frame)

    def set_in_out(self, in_frame: int, out_frame: int) -> None:
        """Set active In / Out playback boundaries."""
        self._in_frame = max(self._start_frame, min(in_frame, self._end_frame))
        self._out_frame = max(self._in_frame, min(out_frame, self._end_frame))
        self.in_out_changed.emit(self._in_frame, self._out_frame)

    def set_in_mark(self, frame: Optional[int] = None) -> None:
        """Set In point to given frame or current playhead."""
        f = self._current_frame if (frame is None or isinstance(frame, bool)) else int(frame)
        out_f = self._out_frame
        if f > out_f:
            out_f = self._end_frame
        self.set_in_out(f, out_f)

    def set_out_mark(self, frame: Optional[int] = None) -> None:
        """Set Out point to given frame or current playhead."""
        f = self._current_frame if (frame is None or isinstance(frame, bool)) else int(frame)
        in_f = self._in_frame
        if f < in_f:
            in_f = self._start_frame
        self.set_in_out(in_f, f)

    def clear_in_out(self) -> None:
        """Reset In/Out marks to full sequence range."""
        self.set_in_out(self._start_frame, self._end_frame)

    def clear_in_mark(self) -> None:
        """Clear In point back to start frame."""
        self.set_in_out(self._start_frame, self._out_frame)

    def clear_out_mark(self) -> None:
        """Clear Out point back to end frame."""
        self.set_in_out(self._in_frame, self._end_frame)

    def mark_sequence_boundaries(self) -> None:
        """Mark full sequence boundaries as In/Out."""
        self.set_in_out(self._start_frame, self._end_frame)

    # --------------------------------------------------------------------------
    # Bookmark & Marker Management
    # --------------------------------------------------------------------------
    @property
    def bookmarks(self) -> set[int]:
        return set(self._bookmarks)

    def toggle_bookmark(self, frame: Optional[int] = None) -> bool:
        """Toggle bookmark flag at frame (default current playhead). Returns True if added."""
        f = self._current_frame if (frame is None or isinstance(frame, bool)) else int(frame)
        if f in self._bookmarks:
            self._bookmarks.remove(f)
            added = False
        else:
            self._bookmarks.add(f)
            added = True
        self.bookmarks_changed.emit(set(self._bookmarks))
        return added

    def add_bookmark(self, frame: int) -> None:
        if frame not in self._bookmarks:
            self._bookmarks.add(frame)
            self.bookmarks_changed.emit(set(self._bookmarks))

    def remove_bookmark(self, frame: int) -> None:
        if frame in self._bookmarks:
            self._bookmarks.remove(frame)
            self.bookmarks_changed.emit(set(self._bookmarks))

    def clear_all_bookmarks(self) -> None:
        if self._bookmarks:
            self._bookmarks.clear()
            self.bookmarks_changed.emit(set())

    def set_bookmarks(self, frames: Iterable[int]) -> None:
        self._bookmarks = set(int(x) for x in frames)
        self.bookmarks_changed.emit(set(self._bookmarks))

    def next_marker(self, extra_markers: Optional[set[int]] = None, direction: int = 1) -> Optional[int]:
        """
        Jump to the next (direction=1) or previous (direction=-1) marked frame.
        Combines bookmarks with optional extra_markers (e.g. annotated frames).
        """
        all_markers = set(self._bookmarks)
        if extra_markers:
            all_markers.update(extra_markers)

        if not all_markers:
            return None

        sorted_m = sorted(all_markers)
        curr = self._current_frame

        if direction > 0:
            # Find first marker strictly greater than current frame
            for m in sorted_m:
                if m > curr:
                    self.set_frame(m)
                    return m
            # Wrap to first
            self.set_frame(sorted_m[0])
            return sorted_m[0]
        else:
            # Find last marker strictly less than current frame
            for m in reversed(sorted_m):
                if m < curr:
                    self.set_frame(m)
                    return m
            # Wrap to last
            self.set_frame(sorted_m[-1])
            return sorted_m[-1]

    def step_bookmark_segment(self, forward: bool = True) -> tuple[int, int]:
        """
        Step active In/Out region forward (Page Down) or backward (Page Up) across
        intervals defined by bookmarks and sequence bounds, looping when limits are reached.
        """
        valid_bms = [b for b in self._bookmarks if self._start_frame < b < self._end_frame]
        points = sorted(list(set([self._start_frame, self._end_frame] + valid_bms)))
        if len(points) < 2:
            self.set_in_out(self._start_frame, self._end_frame)
            return (self._start_frame, self._end_frame)

        segments = [(points[i], points[i + 1]) for i in range(len(points) - 1)]

        # Check if current (in_frame, out_frame) matches an existing segment
        current_seg_idx = -1
        for idx, (s_in, s_out) in enumerate(segments):
            if self._in_frame == s_in and self._out_frame == s_out:
                current_seg_idx = idx
                break

        if current_seg_idx != -1:
            if forward:
                target_idx = (current_seg_idx + 1) % len(segments)
            else:
                target_idx = (current_seg_idx - 1) % len(segments)
        else:
            curr = self._current_frame
            target_idx = 0
            for idx, (s_in, s_out) in enumerate(segments):
                if s_in <= curr <= s_out:
                    target_idx = idx
                    break
                elif curr > s_out:
                    target_idx = idx

        target_in, target_out = segments[target_idx]
        self.set_in_out(target_in, target_out)
        if self._current_frame < target_in or self._current_frame > target_out:
            self.set_frame(target_in)
        return (target_in, target_out)

    def set_frame(self, frame: int) -> None:
        """Seek to a specific frame."""
        target = max(self._start_frame, min(frame, self._end_frame))
        if target != self._current_frame:
            self._current_frame = target
            self.frame_changed.emit(self._current_frame)

    set_current_frame = set_frame
    seek = set_frame

    def step_forward(self, count: int = 1, wrap: bool = True) -> None:
        """Step forward by N frames. If wrap is False, clamps at out_frame."""
        target = self._current_frame + count
        if target > self._out_frame:
            target = self._in_frame if wrap else self._out_frame
        self.set_frame(target)

    def step_backward(self, count: int = 1, wrap: bool = True) -> None:
        """Step backward by N frames. If wrap is False, clamps at in_frame."""
        target = self._current_frame - count
        if target < self._in_frame:
            target = self._out_frame if wrap else self._in_frame
        self.set_frame(target)

    def first_frame(self) -> None:
        """Jump to In mark or first frame."""
        self.set_frame(self._in_frame)

    def last_frame(self) -> None:
        """Jump to Out mark or last frame."""
        self.set_frame(self._out_frame)

    def set_shuttle_speed(self, speed: float) -> None:
        """Set shuttle multiplier (e.g., 1.0, 2.0, -1.0)."""
        self._shuttle_speed = speed
        self.shuttle_speed_changed.emit(self._shuttle_speed)

    def shuttle_forward(self) -> None:
        """4-tier forward shuttle: 1x -> 2x -> 4x -> 8x."""
        if not self._is_playing or self._shuttle_speed <= 0:
            self._shuttle_speed = 1.0
            self.play()
        elif self._shuttle_speed == 1.0:
            self._shuttle_speed = 2.0
        elif self._shuttle_speed == 2.0:
            self._shuttle_speed = 4.0
        elif self._shuttle_speed >= 4.0:
            self._shuttle_speed = 8.0

        if self._is_playing:
            interval_ms = max(2, min(5, int(round(1000.0 / (self._fps * abs(self._shuttle_speed) * 8.0)))))
            self._timer.setInterval(interval_ms)
        self.shuttle_speed_changed.emit(self._shuttle_speed)

    def shuttle_reverse(self) -> None:
        """4-tier reverse shuttle: -1x -> -2x -> -4x -> -8x."""
        if not self._is_playing or self._shuttle_speed >= 0:
            self._shuttle_speed = -1.0
            self.play()
        elif self._shuttle_speed == -1.0:
            self._shuttle_speed = -2.0
        elif self._shuttle_speed == -2.0:
            self._shuttle_speed = -4.0
        elif self._shuttle_speed <= -4.0:
            self._shuttle_speed = -8.0

        if self._is_playing:
            interval_ms = max(2, min(5, int(round(1000.0 / (self._fps * abs(self._shuttle_speed) * 8.0)))))
            self._timer.setInterval(interval_ms)
        self.shuttle_speed_changed.emit(self._shuttle_speed)

    def shuttle_stop(self) -> None:
        """Stop / pause immediately and reset shuttle speed to 1.0."""
        self._shuttle_speed = 1.0
        self.pause()
        self.shuttle_speed_changed.emit(self._shuttle_speed)

    def set_loop_mode(self, mode: str) -> None:
        """Set looping mode ('cycle', 'once', 'ping-pong')."""
        if mode in ("cycle", "once", "ping-pong"):
            self._loop_mode = mode
            self.loop_mode_changed.emit(self._loop_mode)

    def cycle_loop_mode(self) -> str:
        """Cycle through: 'cycle' -> 'ping-pong' -> 'once' -> 'cycle'."""
        modes = ["cycle", "ping-pong", "once"]
        idx = modes.index(self._loop_mode) if self._loop_mode in modes else 0
        self._loop_mode = modes[(idx + 1) % len(modes)]
        self._ping_pong_forward = True
        self.loop_mode_changed.emit(self._loop_mode)
        return self._loop_mode

    # --------------------------------------------------------------------------
    # Timer Tick Handler
    # --------------------------------------------------------------------------
    def _on_tick(self) -> None:
        if not self._is_playing:
            return

        now_ns = self._elapsed_timer.nsecsElapsed()
        delta_ns = now_ns - self._last_tick_time_ns
        self._last_tick_time_ns = now_ns

        frame_duration_ns = (1_000_000_000.0 / (self._fps * abs(self._shuttle_speed))) if self._shuttle_speed != 0 else float("inf")
        self._accumulated_ns += delta_ns

        # Half-tick threshold for zero-drift symmetric quantization
        timer_interval_ms = self._timer.interval()
        half_tick_ns = (timer_interval_ms * 1_000_000.0) * 0.5

        frames_to_advance = 0
        if self._play_all_frames:
            if self._accumulated_ns >= (frame_duration_ns - half_tick_ns):
                frames_to_advance = 1
                self._accumulated_ns -= frame_duration_ns
                # Guard against unbounded accumulation during heavy stalls without penalizing the next frame
                if self._accumulated_ns > frame_duration_ns * 1.5:
                    self._accumulated_ns = frame_duration_ns
        else:
            if self._accumulated_ns >= (frame_duration_ns - half_tick_ns):
                frames_to_advance = max(1, int(round(self._accumulated_ns / frame_duration_ns)))
                self._accumulated_ns -= frames_to_advance * frame_duration_ns

        if frames_to_advance > 0:
            shuttle_sign = 1 if self._shuttle_speed >= 0 else -1
            bounce_sign = 1 if self._ping_pong_forward else -1
            direction = shuttle_sign * bounce_sign if self._loop_mode == "ping-pong" else shuttle_sign
            new_frame = self._current_frame + (frames_to_advance * direction)

            # Loop boundary handling
            if self._loop_mode == "cycle":
                span = max(1, self._out_frame - self._in_frame + 1)
                if new_frame > self._out_frame:
                    new_frame = self._in_frame + ((new_frame - self._in_frame) % span)
                elif new_frame < self._in_frame:
                    new_frame = self._out_frame - ((self._out_frame - new_frame) % span)
            elif self._loop_mode == "ping-pong":
                if new_frame > self._out_frame:
                    new_frame = self._out_frame
                    self._ping_pong_forward = False
                elif new_frame < self._in_frame:
                    new_frame = self._in_frame
                    self._ping_pong_forward = True
            elif self._loop_mode == "once":
                if new_frame > self._out_frame or new_frame < self._in_frame:
                    new_frame = max(self._in_frame, min(new_frame, self._out_frame))
                    self.set_frame(new_frame)
                    self.pause()
                    return

            self.set_frame(new_frame)
            self._frame_counter += frames_to_advance

        # Update FPS readout once per 500ms
        elapsed_ms = self._fps_timer.elapsed()
        if elapsed_ms >= 500:
            self._measured_fps = (self._frame_counter * 1000.0) / elapsed_ms
            self._frame_counter = 0
            self._fps_timer.restart()
            self.fps_measured.emit(round(self._measured_fps, 2))
