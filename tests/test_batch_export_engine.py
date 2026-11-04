"""
Unit tests for Hardware-Accelerated Batch Export (NVENC / QuickSync / ProRes).
Covers:
- Hardware encoder detection and probing (NVENC, QuickSync, AMF, VideoToolbox, MediaFoundation, ProRes, CPU fallback)
- Safe encoder resolution for MOV and MP4 containers
- BatchExportEngine queue lifecycle (add, remove, clear, start, pause, cancel)
- Multi-deliverable studio export pack generation (Dailies, VFX Turnover, Client Review)
- Real background batch execution for video and sequence outputs with progress tracking
- BatchExportDialog UI initialization and table integration
"""
import os
import sys
import tempfile
import time
import numpy as np
import pytest
from pathlib import Path

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from openreview.core.exporter import (
    detect_hardware_encoders,
    is_encoder_functional,
    resolve_video_encoder,
    MediaExporter,
)
from openreview.core.batch_export import (
    BatchExportEngine,
    BatchExportJob,
    BatchExportStatus,
    BATCH_PRESETS,
    DELIVERABLE_PACKS,
)


def test_hardware_encoder_detection_and_probing():
    """Verify hardware encoder detection dictionary and functional check."""
    encoders = detect_hardware_encoders()
    assert isinstance(encoders, dict)
    assert "nvenc" in encoders
    assert "qsv" in encoders
    assert "amf" in encoders
    assert "videotoolbox" in encoders
    assert "mf" in encoders
    assert "prores_ks" in encoders
    assert "libx264" in encoders

    # CPU encoder libx264 should be functional if ffmpeg is available
    if encoders.get("libx264"):
        assert is_encoder_functional("libx264") is True

    # Test an invalid encoder name
    assert is_encoder_functional("non_existent_gpu_encoder_xyz") is False


def test_resolve_video_encoder_prores():
    """Verify ProRes codec resolution with prores_ks profiles."""
    enc, args, desc = resolve_video_encoder("Apple ProRes 422 HQ", "auto", is_mov=True)
    assert enc in ("prores_ks", "prores_videotoolbox")
    assert "-profile:v" in args
    assert "3" in args  # HQ profile index is 3
    assert "-pix_fmt" in args
    assert "yuv422p10le" in args
    assert "422 HQ" in desc

    enc_4444, args_4444, desc_4444 = resolve_video_encoder("Apple ProRes 4444", "cpu", is_mov=True)
    assert enc_4444 == "prores_ks"
    assert "4" in args_4444  # 4444 profile index is 4
    assert "yuva444p10le" in args_4444
    assert "4444" in desc_4444

    enc_proxy, args_proxy, desc_proxy = resolve_video_encoder("Apple ProRes Proxy", "auto", is_mov=True)
    assert enc_proxy in ("prores_ks", "prores_videotoolbox")
    assert "0" in args_proxy  # Proxy profile index is 0
    assert "yuv422p10le" in args_proxy


def test_resolve_video_encoder_h264_and_hevc():
    """Verify H.264/HEVC encoder resolution across auto, cpu, and specific hw engines."""
    # Forced CPU Software
    enc_cpu, args_cpu, desc_cpu = resolve_video_encoder("H.264", "cpu", crf=20, is_mov=False)
    assert enc_cpu == "libx264"
    assert "-crf" in args_cpu
    assert "20" in args_cpu
    assert "-pix_fmt" in args_cpu
    assert "yuv420p" in args_cpu

    enc_hevc_cpu, args_hevc_cpu, desc_hevc = resolve_video_encoder("H.265 / HEVC", "cpu", crf=24, is_mov=False)
    assert enc_hevc_cpu == "libx265"
    assert "-crf" in args_hevc_cpu
    assert "24" in args_hevc_cpu

    # Auto HW resolution should choose either a working GPU encoder or libx264
    enc_auto, _, _ = resolve_video_encoder("H.264", "auto", is_mov=False)
    assert enc_auto in ("h264_nvenc", "h264_qsv", "h264_amf", "h264_videotoolbox", "h264_mf", "libx264")


def test_batch_export_engine_queue_management():
    """Verify adding, removing, clearing, and querying jobs in BatchExportEngine."""
    engine = BatchExportEngine()  # new isolated instance

    def dummy_provider(f: int) -> np.ndarray:
        return np.zeros((64, 64, 3), dtype=np.uint8)

    job1 = BatchExportJob(
        name="Job 1",
        output_path="/tmp/job1.mp4",
        format="mp4",
        export_type="video",
        frame_provider=dummy_provider,
        frames=[0, 1, 2],
        fps=24.0,
    )
    job2 = BatchExportJob(
        name="Job 2",
        output_path="/tmp/job2.mov",
        format="mov",
        export_type="video",
        frame_provider=dummy_provider,
        frames=[0, 1, 2],
        fps=24.0,
    )

    id1 = engine.add_job(job1)
    id2 = engine.add_job(job2)
    assert id1 == job1.id
    assert id2 == job2.id

    assert len(engine.get_jobs()) == 2
    assert engine.get_job(id1) is job1
    assert engine.get_job(id2) is job2

    # Remove job 1
    assert engine.remove_job(id1) is True
    assert len(engine.get_jobs()) == 1
    assert engine.get_job(id1) is None

    # Clear completed / all
    job2.status = BatchExportStatus.COMPLETED
    engine.clear_completed()
    assert len(engine.get_jobs()) == 0


def test_batch_export_multipack_generation():
    """Verify studio multi-pack helper creates deliverable jobs."""
    engine = BatchExportEngine()

    def dummy_provider(f: int) -> np.ndarray:
        return np.zeros((64, 64, 3), dtype=np.uint8)

    with tempfile.TemporaryDirectory() as tmpdir:
        job_ids = engine.add_multi_pack(
            name="Shot_0010",
            output_dir=tmpdir,
            frames=[0, 1, 2, 3],
            frame_provider=dummy_provider,
            pack_key="dailies_pack",
            fps=24.0,
        )
        assert len(job_ids) >= 2
        jobs = engine.get_jobs()
        assert len(jobs) >= 2
        # Verify ProRes and H264 outputs exist in pack
        outputs = [j.output_path for j in jobs]
        assert any(".mov" in o for o in outputs)
        assert any(".mp4" in o for o in outputs)


def test_batch_export_execution_video_and_sequence(qtbot):
    """Verify live background queue execution of both video and image sequence jobs."""
    engine = BatchExportEngine()

    def dummy_provider(f: int) -> np.ndarray:
        arr = np.zeros((64, 64, 3), dtype=np.uint8)
        arr[:, :, 0] = (f * 50) % 255
        arr[:, :, 1] = 120
        arr[:, :, 2] = 200
        return arr

    with tempfile.TemporaryDirectory() as tmpdir:
        # Job 1: MP4 video
        video_out = os.path.join(tmpdir, "test_batch.mp4")
        job_video = BatchExportJob(
            name="Video Test",
            output_path=video_out,
            format="mp4",
            export_type="video",
            frame_provider=dummy_provider,
            frames=[0, 1, 2, 3, 4],
            fps=24.0,
            codec="H.264",
            hwaccel="cpu",  # software to ensure universal environment test
        )

        # Job 2: Image Sequence
        seq_out = os.path.join(tmpdir, "test_seq.%04d.png")
        job_seq = BatchExportJob(
            name="Sequence Test",
            output_path=seq_out,
            format="seq_png",
            export_type="sequence",
            frame_provider=dummy_provider,
            frames=[0, 1, 2],
            fps=24.0,
        )

        engine.add_job(job_video)
        engine.add_job(job_seq)

        finished_signals = []
        engine.queue_finished.connect(lambda comp, fail: finished_signals.append((comp, fail)))

        engine.start_queue()

        # Wait for queue to finish with qtbot
        t0 = time.time()
        while not finished_signals and (time.time() - t0 < 25.0):
            qtbot.wait(100)

        assert len(finished_signals) > 0, "Queue did not complete within timeout"
        assert job_video.status == BatchExportStatus.COMPLETED
        assert job_seq.status == BatchExportStatus.COMPLETED
        assert os.path.exists(video_out)
        assert os.path.getsize(video_out) > 0

        # Check sequence files
        seq_dir = os.path.dirname(seq_out)
        png_files = [f for f in os.listdir(seq_dir) if f.endswith(".png")]
        assert len(png_files) >= 3


def test_batch_export_dialog_initialization(qtbot):
    """Verify BatchExportDialog sets up correctly with engine and table items."""
    from openreview.ui.batch_export_dialog import BatchExportDialog

    engine = BatchExportEngine()

    def dummy_provider(f: int) -> np.ndarray:
        return np.zeros((64, 64, 3), dtype=np.uint8)

    job = BatchExportJob(
        name="UI Test Job",
        output_path="/tmp/ui_test.mov",
        format="mov",
        export_type="video",
        frame_provider=dummy_provider,
        frames=[0, 1, 2],
        fps=24.0,
    )
    engine.add_job(job)

    active_info = {
        "frame_provider": dummy_provider,
        "frames": [0, 1, 2, 3],
        "name": "Test_Shot",
        "fps": 24.0,
    }

    dlg = BatchExportDialog(
        engine=engine,
        active_view_info=active_info,
    )
    qtbot.addWidget(dlg)

    assert dlg.table.rowCount() == 1
    assert dlg.table.item(0, 0).text() == "QUEUED"
    assert dlg.table.item(0, 1).text() == "UI Test Job"
    assert dlg.combo_preset.count() >= len(BATCH_PRESETS)
    assert dlg.combo_hwaccel.count() >= 5
    assert dlg.btn_add_pack is not None

    dlg.close()
