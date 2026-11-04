"""
Tests for GPU Performance Features in OpenReview.
Validates GPU detection, preferences serialization, playback engine modes,
preferences dialog GPU tab, and main window dynamic hot-switching.
"""
import os
import pytest
import numpy as np
from PySide6.QtWidgets import QApplication

from openreview.core.gpu_detector import (
    GpuDevice,
    detect_system_gpus,
    get_active_gpu_device,
)
from openreview.core.preferences import PreferencesManager, UserPreferences
from openreview.widgets.viewport import ViewportCanvas, PlaybackEngineMode
from openreview.ui.preferences_dialog import PreferencesDialog
from openreview.models.annotations import AnnotationStore


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


class TestGpuDetection:
    def test_detect_system_gpus(self):
        gpus = detect_system_gpus()
        assert isinstance(gpus, list)
        assert len(gpus) >= 1
        dev = gpus[0]
        assert isinstance(dev, GpuDevice)
        assert len(dev.name) > 0
        assert len(dev.vendor) > 0

    def test_get_active_gpu_device_default(self):
        active = get_active_gpu_device()
        assert isinstance(active, GpuDevice)
        assert active.name is not None
        assert active.vram_mb >= 0

    def test_get_active_gpu_device_override(self):
        gpus = detect_system_gpus()
        first_gpu = gpus[0]
        selected = get_active_gpu_device(first_gpu.name)
        assert selected.name == first_gpu.name


class TestGpuPreferences:
    def test_gpu_preferences_defaults(self, tmp_path):
        prefs = UserPreferences()
        assert prefs.playback_engine == "opengl_core"
        assert prefs.selected_gpu_device == "auto"
        assert prefs.hardware_video_decoding is True
        assert prefs.enable_opencl_acceleration is True
        assert prefs.enable_pbo_streaming is True
        assert prefs.vsync_enabled is False
        assert prefs.gpu_vram_limit_mb == 2048.0

    def test_gpu_preferences_roundtrip(self, tmp_path):
        mgr = PreferencesManager(config_dir=tmp_path)
        new_prefs = UserPreferences(
            playback_engine="async_pbo",
            selected_gpu_device="NVIDIA GeForce GT 710",
            hardware_video_decoding=True,
            enable_opencl_acceleration=False,
            enable_pbo_streaming=True,
            vsync_enabled=True,
            gpu_vram_limit_mb=4096.0,
        )
        mgr.save(new_prefs)
        loaded = mgr.load()
        assert loaded.playback_engine == "async_pbo"
        assert loaded.selected_gpu_device == "NVIDIA GeForce GT 710"
        assert loaded.hardware_video_decoding is True
        assert loaded.enable_opencl_acceleration is False
        assert loaded.enable_pbo_streaming is True
        assert loaded.vsync_enabled is True
        assert loaded.gpu_vram_limit_mb == 4096.0


class TestViewportPlaybackEngine:
    def test_viewport_engine_modes(self, qapp):
        store = AnnotationStore()
        vp = ViewportCanvas(store)
        assert vp.playback_engine == PlaybackEngineMode.OPENGL_CORE

        vp.set_playback_engine("async_pbo")
        assert vp.playback_engine == PlaybackEngineMode.ASYNC_PBO

        vp.set_playback_engine("software_safe")
        assert vp.playback_engine == PlaybackEngineMode.SOFTWARE_SAFE

        vp.set_playback_engine("opengl_core")
        assert vp.playback_engine == PlaybackEngineMode.OPENGL_CORE

    def test_viewport_vsync_toggle(self, qapp):
        store = AnnotationStore()
        vp = ViewportCanvas(store)
        vp.set_vsync_enabled(True)
        assert vp.vsync_enabled is True
        vp.set_vsync_enabled(False)
        assert vp.vsync_enabled is False


class TestPreferencesDialogGpuTab:
    def test_preferences_dialog_gpu_tab(self, qapp, tmp_path):
        mgr = PreferencesManager(config_dir=tmp_path)
        dlg = PreferencesDialog(mgr)

        # Verify GPU Acceleration tab exists
        tab_names = [dlg.tabs.tabText(i) for i in range(dlg.tabs.count())]
        assert "GPU Acceleration" in tab_names

        # Verify switching tab by name
        dlg.open_tab("GPU Acceleration")
        assert dlg.tabs.tabText(dlg.tabs.currentIndex()) == "GPU Acceleration"

        # Verify controls exist and are populated
        assert dlg.combo_playback_engine.count() >= 3
        assert dlg.combo_gpu_device.count() >= 1
        assert "Device:" in dlg.lbl_gpu_telemetry.text()

        # Modify values and gather
        dlg.combo_playback_engine.setCurrentIndex(dlg.combo_playback_engine.findData("async_pbo"))
        dlg.chk_vsync.setChecked(True)
        dlg.spin_gpu_vram.setValue(3072)

        gathered = dlg._gather_prefs()
        assert gathered.playback_engine == "async_pbo"
        assert gathered.vsync_enabled is True
        assert gathered.gpu_vram_limit_mb == 3072.0


class TestMainWindowGpuIntegration:
    def test_status_bar_badge_and_hot_switching(self, qapp):
        from openreview.ui.main_window import MainWindow

        win = MainWindow()
        try:
            # Verify GPU engine status label was removed from status bar
            assert not hasattr(win, "gpu_engine_status_label")

            # Simulate dynamic preferences change to ASYNC_PBO
            new_prefs = win.pref_mgr.prefs
            new_prefs.playback_engine = "async_pbo"
            new_prefs.vsync_enabled = True
            win.pref_mgr.preferences_changed.emit(new_prefs)

            assert win.viewport.playback_engine == PlaybackEngineMode.ASYNC_PBO
            assert win.viewport.vsync_enabled is True

            # Switch to SOFTWARE_SAFE
            new_prefs.playback_engine = "software_safe"
            win.pref_mgr.preferences_changed.emit(new_prefs)
            assert win.viewport.playback_engine == PlaybackEngineMode.SOFTWARE_SAFE

            # Switch back to OPENGL_CORE
            new_prefs.playback_engine = "opengl_core"
            win.pref_mgr.preferences_changed.emit(new_prefs)
            assert win.viewport.playback_engine == PlaybackEngineMode.OPENGL_CORE
        finally:
            win.close()
