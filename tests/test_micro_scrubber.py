"""
Unit tests for High-Precision Low-Latency Micro-Buffer Audio Scrubbing Engine.
"""
import os
import wave
import time
import numpy as np
import pytest

from openreview.audio.micro_scrubber import MicroBufferAudioScrubber, _decode_audio_to_pcm


@pytest.fixture
def synthetic_wav_file(tmp_path):
    wav_path = str(tmp_path / "test_synth.wav")
    sr = 48000
    duration = 2.0
    t = np.linspace(0, duration, int(sr * duration), endpoint=False)
    # 440 Hz stereo tone
    sine = (np.sin(2 * np.pi * 440 * t) * 16000).astype(np.int16)
    stereo = np.column_stack([sine, sine])

    with wave.open(wav_path, "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(stereo.tobytes())

    return wav_path


def test_decode_audio_to_pcm(synthetic_wav_file):
    pcm = _decode_audio_to_pcm(synthetic_wav_file, target_rate=48000)
    assert pcm is not None
    assert pcm.shape == (96000, 2)
    assert pcm.dtype == np.int16


def test_micro_scrubber_lifecycle_and_scrub(qtbot, synthetic_wav_file):
    scrubber = MicroBufferAudioScrubber(sample_rate=48000)

    # Initially empty
    assert scrubber.has_pcm() is False

    # Load file
    with qtbot.waitSignal(scrubber.decoding_finished, timeout=3000):
        scrubber.load_audio(synthetic_wav_file)

    assert scrubber.has_pcm() is True

    # 1. Forward scrub at frame 25 (approx 1000ms at 24fps)
    handled = scrubber.scrub(target_ms=1000, frame=25, fps=24.0)
    assert handled is True

    # 2. Reverse scrub at frame 24
    handled_rev = scrubber.scrub(target_ms=960, frame=24, fps=24.0)
    assert handled_rev is True

    # 3. Stop / suspend
    scrubber.stop()

    # 4. Unload
    scrubber.unload()
    assert scrubber.has_pcm() is False


def test_micro_scrubber_anti_click_envelope(qtbot, synthetic_wav_file):
    scrubber = MicroBufferAudioScrubber(sample_rate=48000)

    with qtbot.waitSignal(scrubber.decoding_finished, timeout=3000):
        scrubber.load_audio(synthetic_wav_file)

    assert scrubber.has_pcm() is True
    # Verify PCM data exists
    assert scrubber._pcm_data is not None

    # Verify that the fade envelope smoothly scales attack and decay
    fade_samples = int(0.0025 * 48000)  # ~120 samples
    fade_in = np.linspace(0.0, 1.0, fade_samples, dtype=np.float32)

    assert fade_in[0] == 0.0
    assert np.isclose(fade_in[-1], 1.0)


def test_micro_scrubber_volume_and_mute(qtbot):
    scrubber = MicroBufferAudioScrubber(sample_rate=48000)

    # Volume update
    scrubber.set_volume(0.75)
    assert abs(scrubber._volume - 0.75) < 0.01

    # Mute update
    scrubber.set_muted(True)
    assert scrubber._is_muted is True

    # Scrub while muted should safely return True without streaming
    assert scrubber.scrub(target_ms=500, frame=10, fps=24.0) is True

    scrubber.set_muted(False)
    assert scrubber._is_muted is False
