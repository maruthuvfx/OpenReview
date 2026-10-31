"""
Tests for Dedicated PDF Preferences Tab, Custom Branding, Layout Engine,
and Review Sheet Generation.
"""
from __future__ import annotations

import os
import tempfile
import numpy as np
import pytest
from pathlib import Path

from openreview.core.preferences import PreferencesManager, UserPreferences
from openreview.ui.preferences_dialog import PreferencesDialog
from openreview.core.exporter import MediaExporter


def test_user_preferences_pdf_defaults():
    prefs = UserPreferences()
    assert prefs.pdf_title == "OpenReview - VFX Review Sheet"
    assert prefs.pdf_studio_name == ""
    assert prefs.pdf_author_name == ""
    assert prefs.pdf_logo_path == ""
    assert prefs.pdf_header_color == "#c28a10"
    assert prefs.pdf_show_date is True
    assert prefs.pdf_show_fps is True
    assert prefs.pdf_show_keyframe_count is True
    assert prefs.pdf_page_size == "letter"
    assert prefs.pdf_page_orientation == "landscape"
    assert prefs.pdf_layout_mode == "detailed_row"
    assert prefs.pdf_show_frame_num is True
    assert prefs.pdf_show_timecode is True
    assert prefs.pdf_show_stroke_count is True
    assert prefs.pdf_show_notes is True
    assert prefs.pdf_show_source_path is True


def test_preferences_manager_pdf_json_persistence(tmp_path):
    mgr = PreferencesManager(config_dir=tmp_path)
    p = mgr.prefs
    p.pdf_title = "Studio Dailies - Shot 042"
    p.pdf_studio_name = "VFX Main Facility"
    p.pdf_author_name = "Lead Compositor"
    p.pdf_header_color = "#e5a823"
    p.pdf_page_size = "a4"
    p.pdf_page_orientation = "portrait"
    p.pdf_layout_mode = "dense_grid"
    p.pdf_show_stroke_count = False

    mgr.save(p)

    # Reload from disk
    mgr2 = PreferencesManager(config_dir=tmp_path)
    p2 = mgr2.prefs
    assert p2.pdf_title == "Studio Dailies - Shot 042"
    assert p2.pdf_studio_name == "VFX Main Facility"
    assert p2.pdf_author_name == "Lead Compositor"
    assert p2.pdf_header_color == "#e5a823"
    assert p2.pdf_page_size == "a4"
    assert p2.pdf_page_orientation == "portrait"
    assert p2.pdf_layout_mode == "dense_grid"
    assert p2.pdf_show_stroke_count is False


def test_preferences_dialog_pdf_tab_ui(qtbot):
    pref_mgr = PreferencesManager()
    p = pref_mgr.prefs
    p.pdf_title = "Review Session A"
    p.pdf_studio_name = "Pixar / ILM"
    p.pdf_author_name = "Director"
    p.pdf_page_size = "legal"
    p.pdf_page_orientation = "landscape"
    p.pdf_layout_mode = "dense_grid"

    dialog = PreferencesDialog(pref_mgr)
    qtbot.addWidget(dialog)

    # Verify tab exists and is the 4th tab ("Export & Delivery" consolidated tab)
    tab_titles = [dialog.tabs.tabText(i) for i in range(dialog.tabs.count())]
    assert "Export & Delivery" in tab_titles
    assert tab_titles[3] == "Export & Delivery"

    # Verify values loaded into controls
    assert dialog.edit_pdf_title.text() == "Review Session A"
    assert dialog.edit_pdf_studio.text() == "Pixar / ILM"
    assert dialog.edit_pdf_author.text() == "Director"
    assert dialog.combo_pdf_page_size.currentData() == "legal"
    assert dialog.combo_pdf_orientation.currentData() == "landscape"
    assert dialog.combo_pdf_layout_mode.currentData() == "dense_grid"

    # Modify values in UI
    dialog.edit_pdf_title.setText("Final Review B")
    dialog.chk_pdf_show_date.setChecked(False)
    dialog.combo_pdf_layout_mode.setCurrentIndex(dialog.combo_pdf_layout_mode.findData("detailed_row"))

    # Gather preferences
    gathered = dialog._gather_prefs()
    assert gathered.pdf_title == "Final Review B"
    assert gathered.pdf_show_date is False
    assert gathered.pdf_layout_mode == "detailed_row"


def test_media_exporter_pdf_detailed_row_generation(tmp_path):
    output_pdf = tmp_path / "detailed_review.pdf"

    entries = [
        {
            "frame": 1001,
            "timecode": "01:00:00:01",
            "note": "Paint out wire rig on left actor shoulder.",
            "strokes_count": 3,
            "source_path": "/shots/seq01/sh010/comp.0001.exr",
            "image": np.full((180, 320, 3), 120, dtype=np.uint8),
        },
        {
            "frame": 1024,
            "timecode": "01:00:01:00",
            "note": "Adjust rotoscoping around hair edges.",
            "strokes_count": 1,
            "source_path": "/shots/seq01/sh010/comp.0024.exr",
            "image": np.full((180, 320, 3), 80, dtype=np.uint8),
        },
    ]

    custom_cfg = {
        "title": "Custom Show Dailies",
        "studio_name": "Antigravity VFX",
        "author_name": "Lead Supervisor",
        "header_color": "#27ae60",
        "show_date": True,
        "show_fps": True,
        "show_keyframe_count": True,
        "page_size": "letter",
        "page_orientation": "landscape",
        "layout_mode": "detailed_row",
        "show_frame_num": True,
        "show_timecode": True,
        "show_stroke_count": True,
        "show_notes": True,
        "show_source_path": True,
    }

    result_path = MediaExporter.export_pdf_review_sheet(
        output_pdf_path=output_pdf,
        entries=entries,
        fps=24.0,
        pdf_config=custom_cfg,
    )

    assert os.path.isfile(result_path)
    assert os.path.getsize(result_path) > 1000

    with open(result_path, "rb") as f:
        head = f.read(10)
        assert head.startswith(b"%PDF-")


def test_pdf_review_sheet_4k_downsampling_and_progress_callback(tmp_path):
    output_pdf = tmp_path / "4k_review.pdf"

    # Simulate 4K frames
    img_4k = np.full((2160, 3840, 3), 100, dtype=np.uint8)
    entries = [
        {
            "frame": 101,
            "timecode": "01:00:04:05",
            "note": "4K frame review note",
            "strokes_count": 2,
            "source_path": "/renders/shot01/comp.0101.exr",
            "image": img_4k,
        },
        {
            "frame": 102,
            "timecode": "01:00:04:06",
            "note": "4K frame review note 2",
            "strokes_count": 0,
            "source_path": "/renders/shot01/comp.0102.exr",
            "image": img_4k,
        },
    ]

    progress_events = []
    def on_prog(cur, tot, msg):
        progress_events.append((cur, tot, msg))

    result_path = MediaExporter.export_pdf_review_sheet(
        output_pdf_path=output_pdf,
        entries=entries,
        fps=24.0,
        progress_callback=on_prog,
    )

    assert os.path.isfile(result_path)
    # The PDF size should be very compact (< 1MB) because thumbnails were downscaled and encoded as JPEG
    file_size = os.path.getsize(result_path)
    assert file_size < 1_000_000

    # Ensure progress callback was invoked
    assert len(progress_events) >= 3
    assert any("Preparing PDF thumbnails" in ev[2] for ev in progress_events)
    assert any("Compiling PDF document" in ev[2] for ev in progress_events)
    assert progress_events[-1][2] == "Done"


def test_save_image_file_png_and_jpg_fast_path(tmp_path):
    arr = np.full((120, 160, 3), 180, dtype=np.uint8)

    png_path = tmp_path / "test_fast.png"
    MediaExporter.save_image_file(arr, png_path)
    assert png_path.exists()
    assert png_path.stat().st_size > 0

    jpg_path = tmp_path / "test_fast.jpg"
    MediaExporter.save_image_file(arr, jpg_path, quality=85)
    assert jpg_path.exists()
    assert jpg_path.stat().st_size > 0



def test_media_exporter_pdf_dense_grid_with_logo(tmp_path):
    output_pdf = tmp_path / "grid_review.pdf"
    logo_file = tmp_path / "mock_logo.png"

    # Create dummy logo
    dummy_logo = np.full((40, 80, 3), 200, dtype=np.uint8)
    MediaExporter.save_image_file(dummy_logo, logo_file)

    entries = [
        {
            "frame": f,
            "timecode": f"01:00:00:{f:02d}",
            "note": f"Note for frame {f}",
            "strokes_count": f % 4,
            "source_path": f"/path/shot.{f:04d}.exr",
            "image": np.full((100, 100, 3), (f * 30) % 255, dtype=np.uint8),
        }
        for f in range(1, 5)
    ]

    custom_cfg = {
        "title": "Grid Review Sheet",
        "studio_name": "Studio Grid",
        "author_name": "Artist B",
        "logo_path": str(logo_file),
        "header_color": "#c28a10",
        "page_size": "a4",
        "page_orientation": "landscape",
        "layout_mode": "dense_grid",
        "show_frame_num": True,
        "show_timecode": True,
        "show_stroke_count": False,
        "show_notes": True,
        "show_source_path": True,
    }

    result_path = MediaExporter.export_pdf_review_sheet(
        output_pdf_path=output_pdf,
        entries=entries,
        fps=25.0,
        pdf_config=custom_cfg,
    )

    assert os.path.isfile(result_path)
    assert os.path.getsize(result_path) > 1000

    with open(result_path, "rb") as f:
        head = f.read(10)
        assert head.startswith(b"%PDF-")


def test_pdf_review_sheet_4k_downsampling_and_progress_callback(tmp_path):
    output_pdf = tmp_path / "4k_review.pdf"

    # Simulate 4K frames
    img_4k = np.full((2160, 3840, 3), 100, dtype=np.uint8)
    entries = [
        {
            "frame": 101,
            "timecode": "01:00:04:05",
            "note": "4K frame review note",
            "strokes_count": 2,
            "source_path": "/renders/shot01/comp.0101.exr",
            "image": img_4k,
        },
        {
            "frame": 102,
            "timecode": "01:00:04:06",
            "note": "4K frame review note 2",
            "strokes_count": 0,
            "source_path": "/renders/shot01/comp.0102.exr",
            "image": img_4k,
        },
    ]

    progress_events = []
    def on_prog(cur, tot, msg):
        progress_events.append((cur, tot, msg))

    result_path = MediaExporter.export_pdf_review_sheet(
        output_pdf_path=output_pdf,
        entries=entries,
        fps=24.0,
        progress_callback=on_prog,
    )

    assert os.path.isfile(result_path)
    # The PDF size should be very compact (< 1MB) because thumbnails were downscaled and encoded as JPEG
    file_size = os.path.getsize(result_path)
    assert file_size < 1_000_000

    # Ensure progress callback was invoked
    assert len(progress_events) >= 3
    assert any("Preparing PDF thumbnails" in ev[2] for ev in progress_events)
    assert any("Compiling PDF document" in ev[2] for ev in progress_events)
    assert progress_events[-1][2] == "Done"


def test_save_image_file_png_and_jpg_fast_path(tmp_path):
    arr = np.full((120, 160, 3), 180, dtype=np.uint8)

    png_path = tmp_path / "test_fast.png"
    MediaExporter.save_image_file(arr, png_path)
    assert png_path.exists()
    assert png_path.stat().st_size > 0

    jpg_path = tmp_path / "test_fast.jpg"
    MediaExporter.save_image_file(arr, jpg_path, quality=85)
    assert jpg_path.exists()
    assert jpg_path.stat().st_size > 0

