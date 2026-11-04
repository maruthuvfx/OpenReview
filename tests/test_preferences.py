import os
import json
import pytest
from PySide6.QtWidgets import QApplication
from openreview.core.preferences import UserPreferences, PreferencesManager
from openreview.ui.preferences_dialog import PreferencesDialog


def test_user_preferences_defaults():
    prefs = UserPreferences()
    assert prefs.default_fps == 24.0
    assert prefs.play_on_startup is False
    assert prefs.start_fullscreen is False
    assert prefs.canvas_scrub_enabled is True
    assert prefs.auto_retime_mismatched_fps is True
    assert prefs.play_all_frames is True
    assert prefs.default_cache_mode == "play_ahead"
    assert prefs.lookahead_cache_gb == 4.0
    assert prefs.region_cache_gb == 4.0
    assert prefs.lookahead_behind_pct == 20.0


def test_preferences_persistence(tmp_path):
    mgr = PreferencesManager(config_dir=tmp_path)

    # Modify preferences
    mgr.prefs.default_fps = 30.0
    mgr.prefs.lookahead_cache_gb = 8.0
    mgr.prefs.start_fullscreen = True
    mgr.prefs.default_cache_mode = "play_ahead"
    mgr.save()

    json_path = tmp_path / "preferences.json"
    assert json_path.exists()

    # Load in new manager pointing to same folder
    mgr2 = PreferencesManager(config_dir=tmp_path)
    assert mgr2.prefs.default_fps == 30.0
    assert mgr2.prefs.lookahead_cache_gb == 8.0
    assert mgr2.prefs.start_fullscreen is True
    assert mgr2.prefs.default_cache_mode == "play_ahead"


def test_preferences_dialog(qapp, tmp_path):
    mgr = PreferencesManager(config_dir=tmp_path)

    dlg = PreferencesDialog(mgr)
    assert dlg.spin_fps.value() == 24.0
    assert dlg.combo_cache_mode.currentData() == "play_ahead"

    # Edit dialog fields
    dlg.spin_fps.setValue(60.0)
    dlg.combo_cache_mode.setCurrentIndex(1)  # Play-Ahead
    dlg.chk_fullscreen.setChecked(True)

    # Save via dialog
    dlg._save_and_close()

    assert mgr.prefs.default_fps == 60.0
    assert mgr.prefs.start_fullscreen is True
    assert mgr.prefs.default_cache_mode == "play_ahead"


def test_preferences_clear_disk_cache(qapp, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    monkeypatch.setattr(QMessageBox, "information", lambda *args, **kwargs: None)
    monkeypatch.setattr(QMessageBox, "warning", lambda *args, **kwargs: None)

    mgr = PreferencesManager(config_dir=tmp_path)
    dlg = PreferencesDialog(mgr)
    # Trigger clear disk cache button handler
    dlg._on_clear_disk_cache_clicked()


def test_mainwindow_cache_play_ahead_by_default(qtbot, tmp_path, monkeypatch):
    from openreview.ui.main_window import MainWindow
    from openreview.core.cache import CacheMode

    # Ensure isolated empty preferences directory
    monkeypatch.setattr(PreferencesManager, "instance", lambda: PreferencesManager(config_dir=tmp_path))
    win = MainWindow()
    qtbot.addWidget(win)

    assert win.cache.mode == CacheMode.PLAY_AHEAD
    assert win.timeline.cache_bar._cache_mode == "play_ahead"
    win.close()


def test_preferences_rust_engine_ui(qapp, tmp_path):
    mgr = PreferencesManager(config_dir=tmp_path)
    dlg = PreferencesDialog(mgr)

    # Check initial values loaded from default preferences
    assert dlg.chk_enable_rust_engine.isChecked() is True
    assert dlg.spin_rust_worker_threads.value() == 0
    assert dlg.chk_enable_proxy_caching.isChecked() is True
    assert dlg.combo_proxy_scale.currentData() == 2

    # Change values in the UI
    dlg.spin_rust_worker_threads.setValue(8)
    idx_4x = dlg.combo_proxy_scale.findData(4)
    dlg.combo_proxy_scale.setCurrentIndex(idx_4x)

    # Save
    dlg._save_and_close()

    assert mgr.prefs.enable_rust_engine is True
    assert mgr.prefs.rust_worker_threads == 8
    assert mgr.prefs.proxy_resolution_scale == 4

    # Test toggling rust engine disables sub-controls
    dlg2 = PreferencesDialog(mgr)
    assert dlg2.spin_rust_worker_threads.value() == 8
    assert dlg2.combo_proxy_scale.currentData() == 4

    dlg2.chk_enable_rust_engine.setChecked(False)
    assert dlg2.spin_rust_worker_threads.isEnabled() is False
    assert dlg2.chk_enable_proxy_caching.isEnabled() is False
    assert dlg2.combo_proxy_scale.isEnabled() is False

    dlg2._save_and_close()
    assert mgr.prefs.enable_rust_engine is False
