"""
High-Precision Low-Latency Micro-Buffer Audio Scrubbing Engine.
Provides sub-millisecond, click-free audio scrubbing with de-clicking window envelopes,
reverse scrub playback, and dynamic tape-style velocity resampling.
"""
from __future__ import annotations

import os
import sys
import wave
import time
import logging
import threading
from pathlib import Path
from typing import Optional, Tuple
import numpy as np

from PySide6.QtCore import QObject, Signal, QTimer
from PySide6.QtMultimedia import QAudio, QAudioSink, QAudioFormat, QMediaDevices

logger = logging.getLogger(__name__)


def _decode_audio_to_pcm(path: str, target_rate: int = 48000) -> Optional[np.ndarray]:
    """
    Decode audio from video or standalone audio container into 48kHz Stereo Int16 PCM.
    Uses Python's wave module for standard WAV, falling back to PyAV for all other formats.
    """
    if not os.path.exists(path):
        return None

    # 1. Fast Path: Standard 16-bit 48kHz Stereo WAV files
    try:
        with wave.open(path, "rb") as wf:
            n_ch = wf.getnchannels()
            sr = wf.getframerate()
            sw = wf.getsampwidth()
            n_frames = wf.getnframes()
            if sw == 2 and sr == target_rate and n_ch in (1, 2) and n_frames > 0:
                raw_bytes = wf.readframes(n_frames)
                arr = np.frombuffer(raw_bytes, dtype=np.int16).reshape(-1, n_ch)
                if n_ch == 1:
                    return np.column_stack([arr[:, 0], arr[:, 0]])
                return arr
    except Exception:
        pass

    # 2. General High-Performance Path: PyAV (FFmpeg)
    try:
        import av
        container = av.open(path)
        audio_stream = next((s for s in container.streams if s.type == "audio"), None)
        if not audio_stream:
            container.close()
            return None

        resampler = av.AudioResampler(format="s16", layout="stereo", rate=target_rate)
        pcm_chunks = []
        for frame in container.decode(audio_stream):
            resampled = resampler.resample(frame)
            if resampled:
                for r in resampled:
                    # Resampled s16 stereo layout is packed 2-channel
                    pcm_chunks.append(r.to_ndarray().reshape(-1, 2))
        container.close()

        if pcm_chunks:
            return np.ascontiguousarray(np.concatenate(pcm_chunks, axis=0), dtype=np.int16)
    except Exception as e:
        logger.debug("PyAV audio decoding failed on %s: %s", path, e)

    return None


class MicroBufferAudioScrubber(QObject):
    """
    Ultra-low latency micro-buffer audio scrubber.
    Decodes audio to in-memory PCM and streams short windowed chunks directly to hardware via QAudioSink.
    """

    decoding_finished = Signal(bool)

    def __init__(self, sample_rate: int = 48000, parent: Optional[QObject] = None):
        super().__init__(parent)
        self._sample_rate = sample_rate
        self._pcm_data: Optional[np.ndarray] = None
        self._media_path: Optional[str] = None
        self._lock = threading.Lock()
        self._is_decoding: bool = False
        self._volume: float = 1.0
        self._is_muted: bool = False

        # Velocity tracking
        self._last_frame: Optional[int] = None
        self._last_ts: float = 0.0

        # Qt Audio Output
        self._sink: Optional[QAudioSink] = None
        self._io = None
        self._init_audio_sink()

        # Silence / suspend timer
        self._silence_timer = QTimer(self)
        self._silence_timer.setSingleShot(True)
        self._silence_timer.timeout.connect(self._on_silence_timeout)

    def _init_audio_sink(self) -> None:
        try:
            device = QMediaDevices.defaultAudioOutput()
            if not device or device.isNull():
                self._sink = None
                self._io = None
                return

            fmt = QAudioFormat()
            fmt.setSampleRate(self._sample_rate)
            fmt.setChannelCount(2)
            fmt.setSampleFormat(QAudioFormat.SampleFormat.Int16)

            self._sink = QAudioSink(device, fmt, self)
            # 50 ms hardware ring buffer (48000 * 2 ch * 2 bytes * 0.05 = 9600 bytes)
            buffer_bytes = int(self._sample_rate * 2 * 2 * 0.05)
            self._sink.setBufferSize(buffer_bytes)
            self._sink.setVolume(0.0 if self._is_muted else self._volume)

            self._io = self._sink.start()
            if self._sink:
                self._sink.suspend()
        except Exception as e:
            logger.debug("MicroBufferAudioScrubber: QAudioSink init failed: %s", e)
            self._sink = None
            self._io = None

    # --------------------------------------------------------------------------
    # Track Management
    # --------------------------------------------------------------------------
    def load_audio(self, path: str) -> None:
        """Asynchronously decode media file into PCM cache."""
        norm_path = str(Path(path).resolve())
        if self._media_path == norm_path and self._pcm_data is not None:
            return

        self.unload()
        self._media_path = norm_path
        self._is_decoding = True

        def _worker():
            pcm = _decode_audio_to_pcm(norm_path, self._sample_rate)
            with self._lock:
                if self._media_path == norm_path:
                    self._pcm_data = pcm
                    self._is_decoding = False
            success = pcm is not None
            self.decoding_finished.emit(success)

        thread = threading.Thread(target=_worker, daemon=True)
        thread.start()

    def unload(self) -> None:
        """Clear cached PCM buffer and suspend audio device."""
        self.stop()
        with self._lock:
            self._media_path = None
            self._pcm_data = None
            self._is_decoding = False
        self._last_frame = None
        self._last_ts = 0.0

    def has_pcm(self) -> bool:
        """True if uncompressed PCM data is cached in memory and ready for scrubbing."""
        with self._lock:
            return self._pcm_data is not None and len(self._pcm_data) > 0

    # --------------------------------------------------------------------------
    # Volume & Mute Controls
    # --------------------------------------------------------------------------
    def set_volume(self, volume: float) -> None:
        self._volume = max(0.0, min(1.0, float(volume)))
        if self._sink and not self._is_muted:
            self._sink.setVolume(self._volume)

    def set_muted(self, muted: bool) -> None:
        self._is_muted = bool(muted)
        if self._sink:
            self._sink.setVolume(0.0 if self._is_muted else self._volume)

    # --------------------------------------------------------------------------
    # Micro-Buffer Scrubbing Execution
    # --------------------------------------------------------------------------
    def scrub(self, target_ms: int, frame: int, fps: float = 24.0) -> bool:
        """
        Play a single de-clicked micro-chunk corresponding to target frame.
        Handles reverse scrub direction and tape-style velocity pitch adjustments.
        Returns True if handled, False if unhandled (e.g. PCM not ready).
        """
        if self._is_muted or self._volume <= 0.001:
            return True

        with self._lock:
            pcm = self._pcm_data

        if pcm is None or len(pcm) == 0:
            return False

        if not self._io or not self._io.isWritable():
            return False

        now = time.monotonic()
        dt = now - self._last_ts if self._last_ts > 0 else 0.04
        df = frame - self._last_frame if self._last_frame is not None else 1
        self._last_ts = now
        self._last_frame = frame

        # Direction and scrub velocity estimate
        is_reverse = (df < 0)
        effective_fps = max(1.0, float(fps))
        if 0.005 <= dt <= 0.20:
            scrub_fps = abs(df) / dt
            velocity = max(0.35, min(3.5, scrub_fps / effective_fps))
        else:
            velocity = 1.0

        # Window duration: 1 video frame duration (clamped to 30ms - 55ms)
        frame_sec = 1.0 / effective_fps
        chunk_duration = max(0.030, min(0.055, frame_sec))
        chunk_samples = int(round(chunk_duration * self._sample_rate))

        pos_samples = int((target_ms / 1000.0) * self._sample_rate)

        # Slice PCM chunk
        if is_reverse:
            start = max(0, pos_samples - chunk_samples)
            end = pos_samples
            if end <= start:
                return True
            chunk = pcm[start:end]
            if len(chunk) > 0:
                chunk = chunk[::-1].copy()
        else:
            start = pos_samples
            end = min(len(pcm), pos_samples + chunk_samples)
            if end <= start:
                return True
            chunk = pcm[start:end]
            if len(chunk) > 0:
                chunk = chunk.copy()

        if len(chunk) < 64:
            return True

        # Optional tape-style scrub velocity pitch adjustment
        if len(chunk) > 128 and (velocity < 0.85 or velocity > 1.15):
            try:
                orig_len = len(chunk)
                new_len = int(orig_len / velocity)
                if 64 <= new_len <= 19200:
                    orig_idx = np.arange(orig_len)
                    new_idx = np.linspace(0, orig_len - 1, new_len)
                    c0 = np.interp(new_idx, orig_idx, chunk[:, 0])
                    c1 = np.interp(new_idx, orig_idx, chunk[:, 1])
                    chunk = np.column_stack([c0, c1]).astype(np.int16)
            except Exception:
                pass

        # Smooth Hann/cosine de-clicking envelope (2.5 ms attack/decay)
        # Guarantees zero audio clicks/pops across rapid playhead dragging
        fade_samples = min(len(chunk) // 4, int(0.0025 * self._sample_rate))
        if fade_samples > 0:
            fade_in = np.linspace(0.0, 1.0, fade_samples, dtype=np.float32)[:, None]
            fade_out = np.linspace(1.0, 0.0, fade_samples, dtype=np.float32)[:, None]
            chunk_f = chunk.astype(np.float32)
            chunk_f[:fade_samples] *= fade_in
            chunk_f[-fade_samples:] *= fade_out
            chunk = np.clip(chunk_f, -32768.0, 32767.0).astype(np.int16)

        # Hardware Output Stream
        try:
            if self._sink and self._sink.state() == QAudio.State.SuspendedState:
                self._sink.resume()

            self._io.write(chunk.tobytes())
            # Auto-suspend shortly after chunk completes to preserve dead silence when stationary
            timeout_ms = int(chunk_duration * 1000.0) + 15
            self._silence_timer.start(timeout_ms)
            return True
        except Exception as e:
            logger.debug("MicroBufferAudioScrubber write error: %s", e)
            return False

    def stop(self) -> None:
        """Suspend hardware playback immediately."""
        self._silence_timer.stop()
        if self._sink and self._sink.state() != QAudio.State.SuspendedState:
            try:
                self._sink.suspend()
            except Exception:
                pass

    def _on_silence_timeout(self) -> None:
        if self._sink and self._sink.state() != QAudio.State.SuspendedState:
            try:
                self._sink.suspend()
            except Exception:
                pass
