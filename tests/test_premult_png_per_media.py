"""
Tests for:
1. Premultiplied alpha handling (canvas, SourceItem, and UI synchronization).
2. PNG export optimization (multithreading, 8-bit default, and compression level).
3. PerMediaAnnotations (separating review notes across media and session persistence).
"""
import os
import tempfile
import numpy as np
import pytest
from pathlib import Path
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from openreview.models.annotations import AnnotationStore, PerMediaAnnotations, Stroke, ShapeType
from openreview.models.session_graph import SourceItem, SessionGraph, ViewType
from openreview.models.session import OpenReviewSession
from openreview.core.exporter import MediaExporter
from openreview.widgets.viewport import ViewportCanvas


@pytest.fixture
def sample_strokes():
    return [
        Stroke(shape_type=ShapeType.PEN, points=[(0.1, 0.1), (0.2, 0.2)], color=(255, 0, 0, 255), width=3.0),
        Stroke(shape_type=ShapeType.RECTANGLE, points=[(0.3, 0.3), (0.6, 0.6)], color=(0, 255, 0, 255), width=2.0),
    ]


def test_source_item_premultiplied_auto_detection():
    # PNG should default to False (Straight Alpha)
    png_source = SourceItem(name="shot_png", path="/path/to/shot.0101.png")
    assert png_source.premultiplied is False

    # EXR should default to True (Premultiplied Alpha)
    exr_source = SourceItem(name="shot_exr", path="/path/to/shot.0101.exr")
    assert exr_source.premultiplied is True

    # Explicit override
    override_src = SourceItem(name="shot_override", path="/path/to/shot.0101.png", premultiplied=True)
    assert override_src.premultiplied is True

    # Serialization round-trip
    data = override_src.to_dict()
    assert data["premultiplied"] is True
    restored = SourceItem.from_dict(data)
    assert restored.premultiplied is True


def test_viewport_canvas_premultiplied_alpha_toggle(qtbot):
    store = AnnotationStore()
    vp = ViewportCanvas(annotation_store=store)
    qtbot.addWidget(vp)

    assert vp.premultiplied_alpha is True
    vp.set_premultiplied_alpha(False)
    assert vp.premultiplied_alpha is False
    vp.set_premultiplied_alpha(True)
    assert vp.premultiplied_alpha is True


def test_per_media_annotations_stash_and_restore(sample_strokes):
    store = AnnotationStore()
    per_media = PerMediaAnnotations()

    # Annotate Media 1
    media1_id = "media_uuid_1"
    for s in sample_strokes:
        store.add_stroke(10, s)
    store.set_frame_note(10, "Media 1 note on frame 10")

    assert len(store.get_strokes(10)) == 2
    assert store.get_frame_note(10) == "Media 1 note on frame 10"

    # Switch away from Media 1 -> Stash
    per_media.stash(media1_id, store)
    assert per_media.has_annotations(media1_id) is True

    # Switch to Media 2 (has no prior annotations) -> Restore Media 2
    media2_id = "media_uuid_2"
    per_media.restore(media2_id, store)
    assert len(store.get_strokes(10)) == 0
    assert store.get_frame_note(10) == ""

    # Annotate Media 2 on the same frame (frame 10)
    store.set_frame_note(10, "Media 2 note on frame 10")
    per_media.stash(media2_id, store)

    # Switch back to Media 1 -> Notes for Media 1 are restored
    per_media.restore(media1_id, store)
    assert len(store.get_strokes(10)) == 2
    assert store.get_frame_note(10) == "Media 1 note on frame 10"

    # Switch back to Media 2 -> Notes for Media 2 are restored
    per_media.restore(media2_id, store)
    assert len(store.get_strokes(10)) == 0
    assert store.get_frame_note(10) == "Media 2 note on frame 10"


def test_per_media_annotations_session_persistence(sample_strokes):
    store = AnnotationStore()
    per_media = PerMediaAnnotations()

    for s in sample_strokes:
        store.add_stroke(5, s)
    store.set_frame_note(5, "Shot A feedback")
    per_media.stash("shot_A", store)

    store.clear_all()
    store.set_frame_note(8, "Shot B feedback")
    per_media.stash("shot_B", store)

    # Create OpenReviewSession snapshot
    sess = OpenReviewSession(
        annotations=store.to_dict(),
        per_media_annotations=per_media.to_dict(),
    )

    sess_dict = sess.to_dict()
    assert "per_media_annotations" in sess_dict
    assert "shot_A" in sess_dict["per_media_annotations"]
    assert "shot_B" in sess_dict["per_media_annotations"]

    # Restore in fresh session
    restored_sess = OpenReviewSession.from_dict(sess_dict)
    new_per_media = PerMediaAnnotations()
    new_per_media.from_dict(restored_sess.per_media_annotations)

    new_store = AnnotationStore()
    new_per_media.restore("shot_A", new_store)
    assert len(new_store.get_strokes(5)) == 2
    assert new_store.get_frame_note(5) == "Shot A feedback"


def test_png_export_optimization_size_and_multithreading(sample_strokes):
    with tempfile.TemporaryDirectory() as tmp_dir:
        out_dir = Path(tmp_dir)

        # Create dummy frame generator with realistic photographic/review texture & annotations
        w, h = 640, 360
        rng = np.random.RandomState(42)
        base_img = rng.randint(20, 240, (h, w, 3), dtype=np.uint8)
        annotated_img = MediaExporter.render_strokes_on_image(base_img, sample_strokes)

        def _provider(f: int) -> np.ndarray:
            return annotated_img

        frames = [1001, 1002, 1003, 1004]

        # 1. Export 8-bit PNG-24 Truecolor sequence (multithreaded, compression 4)
        out_8bit_paths = MediaExporter.export_sequence(
            frame_provider=_provider,
            frames=frames,
            output_dir=out_dir / "seq_8bit",
            filename_prefix="test_8bit",
            file_ext="png",
            png_mode="png24_opt",
            bit_depth="8",
            compression="4",
            max_workers=4,
        )
        assert len(out_8bit_paths) == 4
        size_8bit = os.path.getsize(out_8bit_paths[0])
        assert size_8bit > 0

        # 2. Export 16-bit PNG sequence for comparison
        out_16bit_paths = MediaExporter.export_sequence(
            frame_provider=_provider,
            frames=frames,
            output_dir=out_dir / "seq_16bit",
            filename_prefix="test_16bit",
            file_ext="png",
            png_mode="16",
            bit_depth="16",
            compression="4",
            max_workers=4,
        )
        assert len(out_16bit_paths) == 4
        size_16bit = os.path.getsize(out_16bit_paths[0])

        # 8-bit PNG should be significantly smaller than 16-bit
        assert size_8bit < size_16bit

        # 3. Export PNG-8 Quantized (Ultra-Compressed)
        out_png8_paths = MediaExporter.export_sequence(
            frame_provider=_provider,
            frames=frames,
            output_dir=out_dir / "seq_png8",
            filename_prefix="test_png8",
            file_ext="png",
            png_mode="png8",
            dither=True,
            compression="4",
            max_workers=4,
        )
        assert len(out_png8_paths) == 4
        size_png8 = os.path.getsize(out_png8_paths[0])
        assert size_png8 > 0
        # PNG-8 should be significantly smaller than 8-bit Truecolor
        assert size_png8 < size_8bit

        # 4. Export PNG-8 without dithering (maximum flat compression)
        out_png8_nodither = MediaExporter.export_sequence(
            frame_provider=_provider,
            frames=frames,
            output_dir=out_dir / "seq_png8_nodither",
            filename_prefix="test_png8_nodither",
            file_ext="png",
            png_mode="png8",
            dither=False,
            compression="4",
            max_workers=4,
        )
        assert len(out_png8_nodither) == 4
        size_nodither = os.path.getsize(out_png8_nodither[0])
        assert size_nodither > 0


def test_preferences_export_defaults(tmp_path):
    from openreview.core.preferences import PreferencesManager, UserPreferences
    mgr = PreferencesManager(config_dir=tmp_path)
    prefs = mgr.prefs
    assert prefs.export_png_mode == "png8"
    assert prefs.export_png_dither is True
    assert prefs.export_png_compression == 4

    # Update preferences
    prefs.export_png_mode = "png24_opt"
    prefs.export_png_dither = False
    prefs.export_png_compression = 9
    mgr.save(prefs)

    # Reload from disk
    new_mgr = PreferencesManager(config_dir=tmp_path)
    new_prefs = new_mgr.prefs
    assert new_prefs.export_png_mode == "png24_opt"
    assert new_prefs.export_png_dither is False
    assert new_prefs.export_png_compression == 9


def test_export_dialog_png_settings_ui(qtbot):
    from openreview.ui.export_dialog import ExportDialog
    from openreview.core.clock import PlaybackClock

    clock = PlaybackClock()
    dlg = ExportDialog(clock=clock, marked_frames=[], annotated_frames=[])
    qtbot.addWidget(dlg)

    # Format mode combo exists and has png8, png24_opt, 16
    assert hasattr(dlg, "combo_png_mode")
    assert dlg.combo_png_mode.count() == 3

    # Dither checkbox exists
    assert hasattr(dlg, "chk_png_dither")
    assert dlg.chk_png_dither.isEnabled() is True

    # Switch to 16-bit mode -> dither checkbox should be disabled
    idx_16 = dlg.combo_png_mode.findData("16")
    dlg.combo_png_mode.setCurrentIndex(idx_16)
    assert dlg.chk_png_dither.isEnabled() is False

    # Switch back to png8 -> dither checkbox should be enabled
    idx_png8 = dlg.combo_png_mode.findData("png8")
    dlg.combo_png_mode.setCurrentIndex(idx_png8)
    assert dlg.chk_png_dither.isEnabled() is True


def test_export_downscale_greater_than_fhd(tmp_path):
    """Verify that media > Full HD is downscaled by 50% when enabled."""
    from openreview.ui.export_dialog import ExportWorker
    from PIL import Image

    # 1. 4K media (3840 x 2160) -> downscaled to 1920 x 1080
    dest_4k = tmp_path / "frame_4k.jpg"
    raw_4k = np.zeros((2160, 3840, 3), dtype=np.uint8)
    raw_4k[:500, :500] = [255, 128, 0]

    cfg_4k = {
        "export_type": "single",
        "destination": dest_4k,
        "frames": [1001],
        "target_width": 3840,
        "target_height": 2160,
        "downscale_gt_fhd": True,
        "quality": 85,
    }
    worker_4k = ExportWorker(cfg_4k, raw_frame_reader=lambda f: raw_4k)
    worker_4k.run()
    assert dest_4k.exists()
    with Image.open(dest_4k) as img:
        assert img.size == (1920, 1080)

    # 2. FHD media (1920 x 1080) -> NOT downscaled (stays 1920 x 1080)
    dest_fhd = tmp_path / "frame_fhd.jpg"
    raw_fhd = np.zeros((1080, 1920, 3), dtype=np.uint8)
    cfg_fhd = {
        "export_type": "single",
        "destination": dest_fhd,
        "frames": [1001],
        "target_width": 1920,
        "target_height": 1080,
        "downscale_gt_fhd": True,
        "quality": 85,
    }
    worker_fhd = ExportWorker(cfg_fhd, raw_frame_reader=lambda f: raw_fhd)
    worker_fhd.run()
    assert dest_fhd.exists()
    with Image.open(dest_fhd) as img:
        assert img.size == (1920, 1080)

    # 3. 4K media with downscale disabled -> stays 3840 x 2160
    dest_4k_full = tmp_path / "frame_4k_full.jpg"
    cfg_4k_full = {
        "export_type": "single",
        "destination": dest_4k_full,
        "frames": [1001],
        "target_width": 3840,
        "target_height": 2160,
        "downscale_gt_fhd": False,
        "quality": 85,
    }
    worker_4k_full = ExportWorker(cfg_4k_full, raw_frame_reader=lambda f: raw_4k)
    worker_4k_full.run()
    assert dest_4k_full.exists()
    with Image.open(dest_4k_full) as img:
        assert img.size == (3840, 2160)


def test_export_dialog_jpg_default_and_downscale_ui(qtbot):
    """Verify ExportDialog default format is JPG and downscale toggle exists."""
    from openreview.ui.export_dialog import ExportDialog
    from openreview.core.clock import PlaybackClock
    from openreview.core.preferences import PreferencesManager

    pm = PreferencesManager.instance()
    pm.prefs.default_export_image_format = "jpg"
    pm.prefs.export_jpg_quality = 85
    pm.prefs.export_downscale_greater_than_fhd = True

    clock = PlaybackClock()
    dlg = ExportDialog(clock=clock, marked_frames=[], annotated_frames=[], input_resolution=(3840, 2160))
    qtbot.addWidget(dlg)

    # Single frame default format is jpg
    assert dlg.combo_single_fmt.currentData() == "jpg"
    # Sequence format default is seq_jpg
    assert dlg.combo_format.currentData() == "seq_jpg"
    # JPEG quality default is 85
    assert dlg.spin_jpg_quality.value() == 85
    # Downscale toggle is present and checked
    assert hasattr(dlg, "chk_downscale_gt_fhd")
    assert dlg.chk_downscale_gt_fhd.isChecked() is True

    # Output summary shows 50% downscale
    assert "Downscale > Full HD" in dlg.lbl_res_summary.text()
    assert "1920 x 1080" in dlg.lbl_res_summary.text()

    # Build config check
    dlg.edit_path.setText("dummy_export_dest")
    cfg = dlg._build_export_config()
    assert cfg is not None
    assert cfg["downscale_gt_fhd"] is True
    assert cfg["quality"] == 85
    assert cfg["ext"] == "jpg"


def test_preferences_export_delivery_tab(qtbot, tmp_path):
    """Verify dedicated Export & Delivery tab in PreferencesDialog."""
    from openreview.ui.preferences_dialog import PreferencesDialog
    from openreview.core.preferences import PreferencesManager, UserPreferences

    mgr = PreferencesManager(config_dir=tmp_path)
    dlg = PreferencesDialog(mgr)
    qtbot.addWidget(dlg)

    # Tab 4 is Export & Delivery
    tab_titles = [dlg.tabs.tabText(i) for i in range(dlg.tabs.count())]
    assert "Export & Delivery" in tab_titles
    assert tab_titles[3] == "Export & Delivery"

    # All expected controls exist on dlg
    assert hasattr(dlg, "combo_default_export_fmt")
    assert hasattr(dlg, "chk_downscale_greater_than_fhd")
    assert hasattr(dlg, "spin_jpg_quality")
    assert hasattr(dlg, "slider_jpg_quality")
    assert hasattr(dlg, "combo_default_png_mode")
    assert hasattr(dlg, "chk_default_png_dither")
    assert hasattr(dlg, "combo_default_png_comp")
    assert hasattr(dlg, "edit_pdf_title")

    # Modify preferences through UI
    idx_png = dlg.combo_default_export_fmt.findData("png")
    dlg.combo_default_export_fmt.setCurrentIndex(idx_png)
    dlg.chk_downscale_greater_than_fhd.setChecked(False)
    dlg.spin_jpg_quality.setValue(90)
    assert dlg.slider_jpg_quality.value() == 90

    gathered = dlg._gather_prefs()
    assert gathered.default_export_image_format == "png"
    assert gathered.export_downscale_greater_than_fhd is False
    assert gathered.export_jpg_quality == 90


def test_pdf_export_dynamic_format_prompt_and_companion_images(qtbot, monkeypatch, tmp_path):
    """Verify PDF export companion images dynamically match preference format and downscale if > Full HD."""
    from openreview.ui.annotation_manager_dialog import AnnotationManagerDialog, AnnotationExportWorker
    from openreview.models.annotations import AnnotationStore, Stroke, ShapeType
    from openreview.core.clock import PlaybackClock
    from openreview.core.preferences import PreferencesManager
    from PySide6.QtWidgets import QMessageBox
    from PIL import Image

    pm = PreferencesManager.instance()
    pm.prefs.default_export_image_format = "jpg"
    pm.prefs.export_downscale_greater_than_fhd = True
    pm.prefs.export_jpg_quality = 85

    store = AnnotationStore()
    store.add_stroke(1001, Stroke(shape_type=ShapeType.PEN, points=[(0.1, 0.1)]))
    store.set_frame_note(1001, "Test 4K Companion Downscale")

    clock = PlaybackClock(start_frame=1001, end_frame=1001, fps=24.0)
    raw_4k = np.zeros((2160, 3840, 3), dtype=np.uint8)

    # 1. Test worker directly with JPG and 4K downscale
    target_pdf = tmp_path / "sheet_jpg.pdf"
    worker_jpg = AnnotationExportWorker(
        export_mode="pdf",
        target_path=target_pdf,
        frames=[1001],
        store=store,
        fps=24.0,
        render_frame_callback=lambda f: raw_4k,
        export_image_frames=True,
        image_format="jpg",
    )
    worker_jpg.run()
    assert target_pdf.exists()
    frames_dir = tmp_path / "sheet_jpg_frames"
    assert frames_dir.exists()
    companion_jpg = frames_dir / "frame_1001.jpg"
    assert companion_jpg.exists()
    with Image.open(companion_jpg) as img:
        # Halved from 3840x2160 to 1920x1080
        assert img.size == (1920, 1080)

    # 2. Test dynamic prompt text in AnnotationManagerDialog
    dlg = AnnotationManagerDialog(store, clock, render_frame_callback=lambda f: raw_4k)
    qtbot.addWidget(dlg)

    prompt_question_captured = []
    def _mock_question(parent, title, text, *args, **kwargs):
        prompt_question_captured.append((title, text))
        return QMessageBox.StandardButton.Yes

    monkeypatch.setattr(QMessageBox, "question", _mock_question)
    monkeypatch.setattr(QMessageBox, "information", lambda *args, **kwargs: None)
    monkeypatch.setattr(QMessageBox, "critical", lambda *args, **kwargs: None)
    target_dialog_pdf = tmp_path / "dialog_sheet.pdf"
    monkeypatch.setattr(
        "PySide6.QtWidgets.QFileDialog.getSaveFileName",
        lambda *args, **kwargs: (str(target_dialog_pdf), "PDF Review Sheet (*.pdf)"),
    )

    dlg._on_export_clicked()
    assert len(prompt_question_captured) == 1
    # Verify title and prompt dynamically use JPG
    assert "Export JPG Frames?" in prompt_question_captured[0][0]
    assert "individual annotated frames as JPG images?" in prompt_question_captured[0][1]
    assert (tmp_path / "dialog_sheet_frames" / "frame_1001.jpg").exists()


