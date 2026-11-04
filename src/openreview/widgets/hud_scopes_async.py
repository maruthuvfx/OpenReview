"""
Asynchronous, Non-Blocking Real-Time Scopes Compute Engine for OpenReview.
Offloads CPU-bound trace rasterization and histogram binning to a background
worker thread, decoupling scope updates from 60+ FPS viewport playback.
"""
from __future__ import annotations

import time
from typing import Optional, Tuple, Dict, Any
import numpy as np
import cv2

from PySide6.QtCore import QObject, Signal, QRunnable, QThreadPool
from PySide6.QtGui import QImage


def adaptive_downsample_for_scopes(image_data: np.ndarray) -> np.ndarray:
    """
    Produce an O(1) strided zero-copy view if frame dimensions are large (4K / 2K),
    dramatically reducing memory bandwidth and cache line thrashing during analysis.
    """
    if image_data is None or image_data.size == 0 or image_data.ndim < 2:
        return image_data

    ih, iw = image_data.shape[:2]
    # Slicing with step creates a view without copying pixels
    step = 4 if (iw >= 3840 or ih >= 2160) else (2 if (iw >= 1920 or ih >= 1080) else 1)
    if step > 1:
        return image_data[::step, ::step]
    return image_data


def compute_histogram_bins(
    image_data: np.ndarray,
) -> Optional[Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, float]]:
    """Compute RGB & Luminance histogram distributions."""
    if image_data is None or image_data.size == 0 or image_data.ndim < 2:
        return None

    sub = adaptive_downsample_for_scopes(image_data)
    if sub.dtype == np.uint8:
        u8 = sub[:, :, :3]
    else:
        u8 = np.clip(sub[:, :, :3] * 255.0, 0, 255).astype(np.uint8)

    r = u8[:, :, 0].ravel()
    g = u8[:, :, 1].ravel()
    b = u8[:, :, 2].ravel()

    hist_r = np.bincount(r, minlength=256).astype(np.float32)
    hist_g = np.bincount(g, minlength=256).astype(np.float32)
    hist_b = np.bincount(b, minlength=256).astype(np.float32)
    hist_y = (0.2126 * hist_r + 0.7152 * hist_g + 0.0722 * hist_b)

    mid_max = max(
        float(np.max(hist_r[1:255])) if hist_r.size > 2 else 1.0,
        float(np.max(hist_g[1:255])) if hist_g.size > 2 else 1.0,
        float(np.max(hist_b[1:255])) if hist_b.size > 2 else 1.0,
        1.0,
    )
    max_val = mid_max * 1.05
    return (hist_r, hist_g, hist_b, hist_y, max_val)


def compute_waveform_trace(
    image_data: np.ndarray,
    mode: str,
    pw: int = 270,
    ph: int = 153,
) -> Optional[QImage]:
    """Compute phosphor trace accumulation QImage for Waveform monitor."""
    if image_data is None or image_data.size == 0 or image_data.ndim < 2:
        return None

    sub = adaptive_downsample_for_scopes(image_data)
    if sub.dtype == np.uint8:
        u8 = sub[:, :, :3]
    else:
        u8 = np.clip(sub[:, :, :3] * 255.0, 0, 255).astype(np.uint8)

    if mode == "RGB Parade":
        w_ch = pw // 3
        samp = cv2.resize(u8, (w_ch, 120), interpolation=cv2.INTER_AREA)
        r = samp[:, :, 0]
        g = samp[:, :, 1]
        b = samp[:, :, 2]

        rgba = np.zeros((ph, pw, 4), dtype=np.uint8)

        def build_ch(data: np.ndarray, x_offset: int, color_idx: int) -> None:
            y = (ph - 1) - (data.astype(np.float32) / 255.0 * (ph - 1)).astype(np.int32)
            x = np.broadcast_to(np.arange(w_ch) + x_offset, y.shape).ravel()
            acc = np.zeros((ph, pw), dtype=np.float32)
            np.add.at(acc, (y.ravel(), x), 1.0)
            intensity = np.clip(acc * 22.0, 0, 255).astype(np.uint8)
            rgba[:, :, color_idx] = np.maximum(rgba[:, :, color_idx], intensity)
            rgba[:, :, 3] = np.maximum(
                rgba[:, :, 3], np.clip(intensity.astype(int) * 1.4, 0, 245).astype(np.uint8)
            )

        build_ch(r, 0, 0)
        build_ch(g, w_ch, 1)
        build_ch(b, w_ch * 2, 2)
        return QImage(rgba.data, pw, ph, pw * 4, QImage.Format.Format_RGBA8888).copy()

    elif mode == "Luma (Y)":
        samp = cv2.resize(u8, (pw, 120), interpolation=cv2.INTER_AREA)
        r = samp[:, :, 0].astype(np.float32)
        g = samp[:, :, 1].astype(np.float32)
        b = samp[:, :, 2].astype(np.float32)
        y_val = 0.2126 * r + 0.7152 * g + 0.0722 * b

        y_pos = (ph - 1) - (y_val / 255.0 * (ph - 1)).astype(np.int32)
        x_pos = np.broadcast_to(np.arange(pw), y_pos.shape).ravel()
        acc = np.zeros((ph, pw), dtype=np.float32)
        np.add.at(acc, (y_pos.ravel(), x_pos), 1.0)

        intensity = np.clip(acc * 22.0, 0, 255).astype(np.uint8)
        rgba = np.zeros((ph, pw, 4), dtype=np.uint8)
        rgba[:, :, 0] = (intensity * 0.45).astype(np.uint8)
        rgba[:, :, 1] = intensity
        rgba[:, :, 2] = (intensity * 0.75).astype(np.uint8)
        rgba[:, :, 3] = np.clip(intensity.astype(int) * 1.5, 0, 245).astype(np.uint8)
        return QImage(rgba.data, pw, ph, pw * 4, QImage.Format.Format_RGBA8888).copy()

    else:  # RGB Overlay
        samp = cv2.resize(u8, (pw, 120), interpolation=cv2.INTER_AREA)
        r = samp[:, :, 0]
        g = samp[:, :, 1]
        b = samp[:, :, 2]

        y_r = (ph - 1) - (r.astype(np.float32) / 255.0 * (ph - 1)).astype(np.int32)
        y_g = (ph - 1) - (g.astype(np.float32) / 255.0 * (ph - 1)).astype(np.int32)
        y_b = (ph - 1) - (b.astype(np.float32) / 255.0 * (ph - 1)).astype(np.int32)

        x_pos = np.broadcast_to(np.arange(pw), y_r.shape).ravel()

        acc_r = np.zeros((ph, pw), dtype=np.float32)
        acc_g = np.zeros((ph, pw), dtype=np.float32)
        acc_b = np.zeros((ph, pw), dtype=np.float32)

        np.add.at(acc_r, (y_r.ravel(), x_pos), 1.0)
        np.add.at(acc_g, (y_g.ravel(), x_pos), 1.0)
        np.add.at(acc_b, (y_b.ravel(), x_pos), 1.0)

        r_u8 = np.clip(acc_r * 22.0, 0, 255).astype(np.uint8)
        g_u8 = np.clip(acc_g * 22.0, 0, 255).astype(np.uint8)
        b_u8 = np.clip(acc_b * 22.0, 0, 255).astype(np.uint8)

        rgba = np.zeros((ph, pw, 4), dtype=np.uint8)
        rgba[:, :, 0] = r_u8
        rgba[:, :, 1] = g_u8
        rgba[:, :, 2] = b_u8
        alpha = np.clip(r_u8.astype(int) + g_u8.astype(int) + b_u8.astype(int), 0, 245).astype(np.uint8)
        rgba[:, :, 3] = alpha
        return QImage(rgba.data, pw, ph, pw * 4, QImage.Format.Format_RGBA8888).copy()


def compute_vectorscope_trace(
    image_data: np.ndarray,
    cx: float = 110.0,
    cy: float = 110.0,
    scale: float = 160.0,
    w: int = 220,
    h: int = 220,
) -> Optional[QImage]:
    """Compute chrominance trace accumulation QImage for Vectorscope monitor."""
    if image_data is None or image_data.size == 0 or image_data.ndim < 2:
        return None

    sub = adaptive_downsample_for_scopes(image_data)
    if sub.dtype == np.uint8:
        u8 = sub[:, :, :3]
    else:
        u8 = np.clip(sub[:, :, :3] * 255.0, 0, 255).astype(np.uint8)

    samp = cv2.resize(u8, (140, 140), interpolation=cv2.INTER_AREA)
    r = samp[:, :, 0].astype(np.float32) / 255.0
    g = samp[:, :, 1].astype(np.float32) / 255.0
    b = samp[:, :, 2].astype(np.float32) / 255.0

    y = 0.2126 * r + 0.7152 * g + 0.0722 * b
    cb = (b - y) / 1.8556
    cr = (r - y) / 1.5748

    px = np.clip(np.round(cx + cb * scale).astype(int), 0, w - 1).ravel()
    py = np.clip(np.round(cy - cr * scale).astype(int), 0, h - 1).ravel()

    acc = np.zeros((h, w), dtype=np.float32)
    np.add.at(acc, (py, px), 1.0)

    intensity = np.clip(acc * 24.0, 0, 255).astype(np.uint8)
    rgba = np.zeros((h, w, 4), dtype=np.uint8)
    rgba[:, :, 0] = (intensity * 0.40).astype(np.uint8)
    rgba[:, :, 1] = intensity
    rgba[:, :, 2] = (intensity * 0.70).astype(np.uint8)
    rgba[:, :, 3] = np.clip(intensity.astype(int) * 1.5, 0, 245).astype(np.uint8)

    return QImage(rgba.data, w, h, w * 4, QImage.Format.Format_RGBA8888).copy()


class ScopeWorkerSignals(QObject):
    """Signals emitted across threads when background scope computations complete."""
    scopes_ready = Signal(object)


class ScopeComputeTask(QRunnable):
    """Background task executed on QThreadPool."""

    def __init__(
        self,
        signals: ScopeWorkerSignals,
        image_data: Optional[np.ndarray],
        do_hist: bool,
        do_waveform: bool,
        waveform_mode: str,
        do_vectorscope: bool,
    ):
        super().__init__()
        self.signals = signals
        self.image_data = image_data
        self.do_hist = do_hist
        self.do_waveform = do_waveform
        self.waveform_mode = waveform_mode
        self.do_vectorscope = do_vectorscope

    def run(self) -> None:
        try:
            results: Dict[str, Any] = {}
            if self.image_data is None:
                results["hist"] = None
                results["waveform"] = None
                results["vectorscope"] = None
                self.signals.scopes_ready.emit(results)
                return

            if self.do_hist:
                results["hist"] = compute_histogram_bins(self.image_data)
            if self.do_waveform:
                results["waveform"] = compute_waveform_trace(self.image_data, self.waveform_mode)
            if self.do_vectorscope:
                results["vectorscope"] = compute_vectorscope_trace(self.image_data)

            self.signals.scopes_ready.emit(results)
        except Exception:
            pass


class ScopeAsyncEngine(QObject):
    """
    Decoupled Asynchronous Scopes Controller.
    Throttles and offloads scope rasterization without blocking the Qt UI thread.
    """

    def __init__(self, parent: Optional[QObject] = None):
        super().__init__(parent)
        self._thread_pool = QThreadPool()
        self._thread_pool.setMaxThreadCount(1)  # Single worker preserves strict FIFO order
        self.signals = ScopeWorkerSignals()
        self.signals.scopes_ready.connect(self._on_scopes_computed)

        self._is_busy = False
        self._pending_data: Optional[np.ndarray] = None
        self._pending_visibilities = (False, False, False, "RGB Overlay")
        self._last_dispatch_time = 0.0
        self._min_interval = 0.030  # Max 33 FPS refresh for human eye perception

        # Target consumer widgets
        self.hist_widget: Optional[Any] = None
        self.waveform_widget: Optional[Any] = None
        self.vectorscope_widget: Optional[Any] = None
        self._hist_widgets: List[Any] = []
        self._waveform_widgets: List[Any] = []
        self._vectorscope_widgets: List[Any] = []

    def register_widgets(self, hist: Any, waveform: Any, vectorscope: Any) -> None:
        if hist is not None and hist not in self._hist_widgets:
            self._hist_widgets.append(hist)
        if waveform is not None and waveform not in self._waveform_widgets:
            self._waveform_widgets.append(waveform)
        if vectorscope is not None and vectorscope not in self._vectorscope_widgets:
            self._vectorscope_widgets.append(vectorscope)
        self.hist_widget = hist
        self.waveform_widget = waveform
        self.vectorscope_widget = vectorscope

    def dispatch(
        self,
        image_data: Optional[np.ndarray],
        show_hist: bool,
        show_waveform: bool,
        show_vectorscope: bool,
    ) -> None:
        """Non-blocking entry point called from main thread."""
        if not (show_hist or show_waveform or show_vectorscope):
            return

        now = time.perf_counter()
        wf_mode = getattr(self.waveform_widget, "_mode", "RGB Overlay") if self.waveform_widget else "RGB Overlay"

        # If background worker is still active, coalesce to the latest frame
        if self._is_busy:
            self._pending_data = image_data
            self._pending_visibilities = (show_hist, show_waveform, show_vectorscope, wf_mode)
            return

        # Throttle to prevent flooding worker during high-refresh playback
        if now - self._last_dispatch_time < self._min_interval and image_data is not None:
            self._pending_data = image_data
            self._pending_visibilities = (show_hist, show_waveform, show_vectorscope, wf_mode)
            return

        self._last_dispatch_time = now
        self._is_busy = True
        task = ScopeComputeTask(
            self.signals,
            image_data,
            show_hist,
            show_waveform,
            wf_mode,
            show_vectorscope,
        )
        self._thread_pool.start(task)

    def _on_scopes_computed(self, results: Dict[str, Any]) -> None:
        """Executed on main Qt GUI thread when worker completes."""
        try:
            if "hist" in results:
                hist_res = results["hist"]
                target_hists = self._hist_widgets if self._hist_widgets else ([self.hist_widget] if self.hist_widget else [])
                for hw in target_hists:
                    if hw is None:
                        continue
                    if hist_res is None:
                        hw.set_histogram_data(None, None, None, None, 1.0)
                    else:
                        hr, hg, hb, hy, mv = hist_res
                        hw.set_histogram_data(hr, hg, hb, hy, mv)

            if "waveform" in results:
                wf_res = results["waveform"]
                target_waves = self._waveform_widgets if self._waveform_widgets else ([self.waveform_widget] if self.waveform_widget else [])
                for ww in target_waves:
                    if ww is not None:
                        ww.set_trace_image(wf_res)

            if "vectorscope" in results:
                vec_res = results["vectorscope"]
                target_vecs = self._vectorscope_widgets if self._vectorscope_widgets else ([self.vectorscope_widget] if self.vectorscope_widget else [])
                for vw in target_vecs:
                    if vw is not None:
                        vw.set_trace_image(vec_res)
        finally:
            self._is_busy = False
            # If a newer frame arrived while we were computing, process it immediately
            if self._pending_data is not None:
                p_data = self._pending_data
                p_vis = self._pending_visibilities
                self._pending_data = None
                self.dispatch(p_data, p_vis[0], p_vis[1], p_vis[2])

    def stop(self) -> None:
        """Clean shutdown waiting for active background tasks to finish."""
        self._pending_data = None
        self._thread_pool.waitForDone(250)
