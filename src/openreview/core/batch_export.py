"""
Hardware-Accelerated Batch Export Engine for OpenReview.
Provides asynchronous multi-job queue execution supporting:
- NVIDIA NVENC, Intel QuickSync, AMD AMF, Apple VideoToolbox, and Windows MediaFoundation
- Apple ProRes 422 HQ, 422, Proxy, 4444, and 4444 XQ
- Multi-source batch rendering (entire Sources bin or selected assets)
- Multi-deliverable export packs (e.g. ProRes Master + H.264 Web Screener + EXR sequence)
"""
from __future__ import annotations

import os
import time
import uuid
import logging
from enum import Enum
from pathlib import Path
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Tuple, Callable, Any

import numpy as np
from PySide6.QtCore import QObject, Signal, QThread

from openreview.core.exporter import MediaExporter

logger = logging.getLogger(__name__)


class BatchExportStatus(str, Enum):
    QUEUED = "queued"
    ENCODING = "encoding"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    PAUSED = "paused"


@dataclass
class BatchExportJob:
    """Represents a discrete media render task in the batch queue."""
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    name: str = "Export Job"
    source_id: Optional[str] = None
    source_name: str = "Active View"
    output_path: str = ""
    export_type: str = "video"  # "video", "sequence", "single"
    format: str = "mov"         # "mov", "mp4", "seq_exr", "seq_dpx", "seq_tif", "seq_png", "seq_jpg", "single"
    codec: str = "Apple ProRes 422 HQ"
    hwaccel: str = "auto"       # "auto", "nvenc", "qsv", "amf", "videotoolbox", "mf", "cpu"
    frames: List[int] = field(default_factory=list)
    fps: float = 24.0
    target_resolution: Optional[Tuple[int, int]] = None  # None = native
    colorspace: str = "Current View (WYSIWYG)"
    burn_grade: bool = True
    burn_annotations: bool = True
    crf: int = 18
    bit_depth: str = "16"
    compression: str = "zip"
    quality: int = 95
    png_mode: str = "png8"
    dither: bool = True
    status: BatchExportStatus = BatchExportStatus.QUEUED
    progress: float = 0.0
    current_frame: int = 0
    total_frames: int = 0
    fps_current: float = 0.0
    error_message: Optional[str] = None
    elapsed_sec: float = 0.0
    frame_provider: Optional[Callable[[int], np.ndarray]] = None

    @property
    def is_done(self) -> bool:
        return self.status in (BatchExportStatus.COMPLETED, BatchExportStatus.FAILED, BatchExportStatus.CANCELLED)


# Predefined studio delivery profiles
BATCH_PRESETS: Dict[str, Dict[str, Any]] = {
    "prores_422_hq": {
        "name": "Apple ProRes 422 HQ (10-bit Master)",
        "format": "mov",
        "codec": "Apple ProRes 422 HQ",
        "hwaccel": "auto",
        "ext": "mov",
        "crf": 18,
        "burn_annotations": False,
        "burn_grade": True,
    },
    "prores_4444": {
        "name": "Apple ProRes 4444 (12-bit VFX Master)",
        "format": "mov",
        "codec": "Apple ProRes 4444",
        "hwaccel": "auto",
        "ext": "mov",
        "crf": 18,
        "burn_annotations": False,
        "burn_grade": True,
    },
    "prores_proxy": {
        "name": "Apple ProRes Proxy (Editorial Offline)",
        "format": "mov",
        "codec": "Apple ProRes 422 Proxy",
        "hwaccel": "auto",
        "ext": "mov",
        "crf": 24,
        "burn_annotations": True,
        "burn_grade": True,
    },
    "nvenc_h264": {
        "name": "H.264 (NVIDIA NVENC Hardware Fast)",
        "format": "mp4",
        "codec": "H.264 (NVIDIA NVENC)",
        "hwaccel": "nvenc",
        "ext": "mp4",
        "crf": 18,
        "burn_annotations": True,
        "burn_grade": True,
    },
    "qsv_h264": {
        "name": "H.264 (Intel QuickSync Hardware Fast)",
        "format": "mp4",
        "codec": "H.264 (Intel QuickSync)",
        "hwaccel": "qsv",
        "ext": "mp4",
        "crf": 18,
        "burn_annotations": True,
        "burn_grade": True,
    },
    "gpu_h264_screener": {
        "name": "H.264 Client Screener (GPU Accelerated)",
        "format": "mp4",
        "codec": "H.264 (Auto GPU)",
        "hwaccel": "auto",
        "ext": "mp4",
        "crf": 22,
        "burn_annotations": True,
        "burn_grade": True,
    },
    "gpu_hevc_master": {
        "name": "H.265 / HEVC High Efficiency (GPU Hardware)",
        "format": "mp4",
        "codec": "H.265 (HEVC)",
        "hwaccel": "auto",
        "ext": "mp4",
        "crf": 18,
        "burn_annotations": False,
        "burn_grade": True,
    },
    "exr_sequence": {
        "name": "OpenEXR Sequence (ZIP 16-bit Scene-Linear)",
        "format": "seq_exr",
        "codec": "OpenEXR",
        "ext": "exr",
        "compression": "zip",
        "bit_depth": "16",
        "colorspace": "Scene-Linear (ACEScg)",
        "burn_annotations": False,
        "burn_grade": False,
    },
    "dpx_sequence": {
        "name": "DPX Sequence (10-bit SMPTE 268M)",
        "format": "seq_dpx",
        "codec": "DPX",
        "ext": "dpx",
        "bit_depth": "10",
        "colorspace": "Cineon Log",
        "burn_annotations": False,
        "burn_grade": False,
    },
}

DELIVERABLE_PACKS: Dict[str, Dict[str, Any]] = {
    "dailies_pack": {
        "name": "Studio Dailies Pack",
        "description": "ProRes 422 HQ Master + H.264 GPU Screener with burn-in",
        "presets": ["prores_422_hq", "gpu_h264_screener"],
    },
    "vfx_turnover_pack": {
        "name": "VFX Turnover Pack",
        "description": "ProRes 4444 Master + OpenEXR Sequence + H.264 Screener",
        "presets": ["prores_4444", "exr_sequence", "gpu_h264_screener"],
    },
    "client_review_pack": {
        "name": "Client Review Pack",
        "description": "GPU H.264 Screener (1080p) + ProRes Proxy",
        "presets": ["gpu_h264_screener", "prores_proxy"],
    },
}


class _BatchWorkerThread(QThread):
    """Background worker executing batch export queue sequentially."""
    job_started = Signal(object)
    job_progress = Signal(str, int, int, float, str)
    job_completed = Signal(str, str)
    job_failed = Signal(str, str)
    queue_progress = Signal(int, int, float)
    queue_finished = Signal(int, int)

    def __init__(self, engine: BatchExportEngine):
        super().__init__()
        self.engine = engine
        self._is_cancelled = False
        self._is_paused = False

    def cancel(self) -> None:
        self._is_cancelled = True

    def set_paused(self, paused: bool) -> None:
        self._is_paused = bool(paused)

    def is_cancelled(self) -> bool:
        return self._is_cancelled

    def run(self) -> None:
        jobs = self.engine.get_jobs()
        total_jobs = len(jobs)
        completed_jobs = 0
        failed_jobs = 0

        for idx, job in enumerate(jobs):
            if self._is_cancelled:
                job.status = BatchExportStatus.CANCELLED
                break

            while self._is_paused and not self._is_cancelled:
                time.sleep(0.1)

            if job.status in (BatchExportStatus.COMPLETED, BatchExportStatus.CANCELLED):
                completed_jobs += 1
                continue

            job.status = BatchExportStatus.ENCODING
            job.error_message = None
            job.progress = 0.0
            self.job_started.emit(job)

            t0 = time.perf_counter()
            start_time = t0
            frames_done = 0

            def _progress_cb(cur: int, tot: int) -> None:
                nonlocal frames_done
                frames_done = cur
                pct = float(cur) / max(1, tot)
                job.progress = pct
                job.current_frame = cur
                job.total_frames = tot
                elapsed = max(0.001, time.perf_counter() - start_time)
                job.elapsed_sec = elapsed
                job.fps_current = float(cur) / elapsed
                msg = f"Encoding {cur}/{tot} ({int(pct * 100)}%) - {job.fps_current:.1f} FPS"
                self.job_progress.emit(job.id, cur, tot, job.fps_current, msg)

            try:
                provider = job.frame_provider
                if provider is None:
                    raise RuntimeError("No frame provider attached to export job.")

                frames = job.frames
                if not frames:
                    raise RuntimeError("No frames defined for export job.")

                # Process destination directory and path
                out_path = Path(job.output_path)
                out_path.parent.mkdir(parents=True, exist_ok=True)

                if job.export_type == "video" or job.format in ("mov", "mp4"):
                    MediaExporter.export_video_ffmpeg(
                        frame_provider=provider,
                        frames=frames,
                        output_path=out_path,
                        fps=job.fps,
                        codec=job.codec,
                        crf=job.crf,
                        hwaccel=job.hwaccel,
                        progress_callback=_progress_cb,
                        cancel_token=self.is_cancelled,
                    )
                elif job.export_type == "sequence" or job.format.startswith("seq_"):
                    ext = job.format.replace("seq_", "")
                    prefix = out_path.stem
                    MediaExporter.export_sequence(
                        frame_provider=provider,
                        frames=frames,
                        output_dir=out_path.parent,
                        filename_prefix=prefix,
                        file_ext=ext,
                        compression=job.compression,
                        bit_depth=job.bit_depth,
                        quality=job.quality,
                        progress_callback=_progress_cb,
                        cancel_token=self.is_cancelled,
                        dither=job.dither,
                        png_mode=job.png_mode,
                    )
                else:  # Single frame
                    first_f = frames[0]
                    img = provider(first_f)
                    MediaExporter.save_image_file(
                        img,
                        out_path,
                        compression=job.compression,
                        bit_depth=job.bit_depth,
                        quality=job.quality,
                        dither=job.dither,
                        png_mode=job.png_mode,
                    )

                if self._is_cancelled:
                    job.status = BatchExportStatus.CANCELLED
                    self.job_failed.emit(job.id, "Export cancelled by user")
                else:
                    job.status = BatchExportStatus.COMPLETED
                    job.progress = 1.0
                    job.elapsed_sec = time.perf_counter() - t0
                    completed_jobs += 1
                    self.job_completed.emit(job.id, str(out_path))

            except Exception as e:
                logger.exception("Batch export job '%s' failed: %s", job.name, e)
                job.status = BatchExportStatus.FAILED
                job.error_message = str(e)
                failed_jobs += 1
                self.job_failed.emit(job.id, str(e))

            overall_pct = float(idx + 1) / float(max(1, total_jobs))
            self.queue_progress.emit(completed_jobs, total_jobs, overall_pct)

        self.queue_finished.emit(completed_jobs, failed_jobs)


class BatchExportEngine(QObject):
    """
    Central Batch Export Queue Manager.
    Coordinates asynchronous hardware-accelerated video & sequence rendering.
    """
    job_added = Signal(object)
    job_removed = Signal(str)
    queue_updated = Signal()
    job_started = Signal(object)
    job_progress = Signal(str, int, int, float, str)
    job_completed = Signal(str, str)
    job_failed = Signal(str, str)
    queue_progress = Signal(int, int, float)
    queue_finished = Signal(int, int)
    queue_state_changed = Signal(bool)  # is_running

    _instance: Optional[BatchExportEngine] = None

    @classmethod
    def instance(cls) -> BatchExportEngine:
        if cls._instance is None:
            cls._instance = BatchExportEngine()
        return cls._instance

    def __init__(self, parent: Optional[QObject] = None):
        super().__init__(parent)
        self._jobs: List[BatchExportJob] = []
        self._worker: Optional[_BatchWorkerThread] = None

    def get_jobs(self) -> List[BatchExportJob]:
        return list(self._jobs)

    def get_job(self, job_id: str) -> Optional[BatchExportJob]:
        for j in self._jobs:
            if j.id == job_id:
                return j
        return None

    def add_job(self, job: BatchExportJob) -> str:
        self._jobs.append(job)
        self.job_added.emit(job)
        self.queue_updated.emit()
        return job.id

    def remove_job(self, job_id: str) -> bool:
        for idx, j in enumerate(self._jobs):
            if j.id == job_id:
                if j.status == BatchExportStatus.ENCODING:
                    return False  # Cannot remove active running job
                self._jobs.pop(idx)
                self.job_removed.emit(job_id)
                self.queue_updated.emit()
                return True
        return False

    def clear_queue(self) -> None:
        if self.is_running():
            return
        self._jobs.clear()
        self.queue_updated.emit()

    def clear_completed(self) -> None:
        self._jobs = [j for j in self._jobs if j.status not in (BatchExportStatus.COMPLETED, BatchExportStatus.CANCELLED)]
        self.queue_updated.emit()

    def retry_failed(self) -> None:
        for j in self._jobs:
            if j.status == BatchExportStatus.FAILED:
                j.status = BatchExportStatus.QUEUED
                j.progress = 0.0
                j.error_message = None
        self.queue_updated.emit()

    def is_running(self) -> bool:
        return self._worker is not None and self._worker.isRunning()

    def start(self) -> None:
        if self.is_running():
            if self._worker and self._worker._is_paused:
                self._worker.set_paused(False)
            return

        # Check if there are queued jobs
        has_queued = any(j.status in (BatchExportStatus.QUEUED, BatchExportStatus.PAUSED) for j in self._jobs)
        if not has_queued:
            return

        self._worker = _BatchWorkerThread(self)
        self._worker.job_started.connect(self.job_started.emit)
        self._worker.job_progress.connect(self.job_progress.emit)
        self._worker.job_completed.connect(self._on_worker_job_completed)
        self._worker.job_failed.connect(self.job_failed.emit)
        self._worker.queue_progress.connect(self.queue_progress.emit)
        self._worker.queue_finished.connect(self._on_worker_queue_finished)

        self._worker.start()
        self.queue_state_changed.emit(True)

    start_queue = start

    def pause(self) -> None:
        if self._worker and self._worker.isRunning():
            self._worker.set_paused(True)

    def cancel(self) -> None:
        if self._worker and self._worker.isRunning():
            self._worker.cancel()
            self._worker.wait(timeout=2000)
            self._worker = None
        self.queue_state_changed.emit(False)

    def _on_worker_job_completed(self, job_id: str, out_path: str) -> None:
        self.job_completed.emit(job_id, out_path)
        self.queue_updated.emit()

    def _on_worker_queue_finished(self, completed: int, failed: int) -> None:
        self._worker = None
        self.queue_finished.emit(completed, failed)
        self.queue_state_changed.emit(False)
        self.queue_updated.emit()

    # --------------------------------------------------------------------------
    # Preset & Multi-Pack Helper Factory Methods
    # --------------------------------------------------------------------------
    def create_job_from_preset(
        self,
        name: str,
        output_dir: str | Path,
        frames: List[int],
        frame_provider: Callable[[int], np.ndarray],
        preset_key: str = "prores_422_hq",
        fps: float = 24.0,
        source_id: Optional[str] = None,
        source_name: str = "Media",
        target_resolution: Optional[Tuple[int, int]] = None,
        colorspace: str = "Current View (WYSIWYG)",
        burn_grade: bool = True,
        burn_annotations: bool = True,
    ) -> BatchExportJob:
        """Create a configured BatchExportJob from a standard preset."""
        preset = BATCH_PRESETS.get(preset_key, BATCH_PRESETS["prores_422_hq"])
        ext = preset.get("ext", "mov")
        safe_name = "".join(c for c in name if c.isalnum() or c in ("-", "_")).strip() or "export"
        filename = f"{safe_name}.{ext}" if not preset["format"].startswith("seq_") else f"{safe_name}.%04d.{ext}"
        out_path = Path(output_dir) / filename

        job = BatchExportJob(
            name=f"{name} [{preset['name']}]",
            source_id=source_id,
            source_name=source_name,
            output_path=str(out_path),
            export_type="sequence" if preset["format"].startswith("seq_") else "video",
            format=preset["format"],
            codec=preset.get("codec", "Apple ProRes 422 HQ"),
            hwaccel=preset.get("hwaccel", "auto"),
            frames=list(frames),
            fps=fps,
            target_resolution=target_resolution,
            colorspace=preset.get("colorspace", colorspace),
            burn_grade=preset.get("burn_grade", burn_grade),
            burn_annotations=preset.get("burn_annotations", burn_annotations),
            crf=preset.get("crf", 18),
            bit_depth=preset.get("bit_depth", "16"),
            compression=preset.get("compression", "zip"),
            quality=preset.get("quality", 95),
            frame_provider=frame_provider,
        )
        return job

    def add_multi_pack(
        self,
        name: str,
        output_dir: str | Path,
        frames: List[int],
        frame_provider: Callable[[int], np.ndarray],
        pack_key: str = "dailies_pack",
        fps: float = 24.0,
        source_id: Optional[str] = None,
        source_name: str = "Media",
        target_resolution: Optional[Tuple[int, int]] = None,
    ) -> List[str]:
        """Add all presets belonging to a deliverable pack as distinct jobs in the queue."""
        pack = DELIVERABLE_PACKS.get(pack_key, DELIVERABLE_PACKS["dailies_pack"])
        added_ids = []
        for p_key in pack["presets"]:
            job = self.create_job_from_preset(
                name=f"{name}_{p_key}",
                output_dir=output_dir,
                frames=frames,
                frame_provider=frame_provider,
                preset_key=p_key,
                fps=fps,
                source_id=source_id,
                source_name=source_name,
                target_resolution=target_resolution,
            )
            job_id = self.add_job(job)
            added_ids.append(job_id)
        return added_ids
