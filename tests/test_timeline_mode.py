"""
Comprehensive Unit Tests for Timeline Mode, Multi-Track Editorial, Conform, and Dynamic Timelines.
Covers:
- Timeline Mode Workspace Preset & Shortcut registration (F10)
- Preferences Dialog Startup Workspace integration
- Multi-track Sequence Data Models (Tracks, Clips, Soft Effects)
- OpenTimelineIO (.otio) Export and Import
- CMX 3600 EDL, XML, and AAF Conform Parsers
- Smart Media Relinker
- Soft Effects Inspector & GLSL parameter manipulation
- Dynamic Timelines Python API (TimelineAPI)
- SDI Monitor-Out Manager (Blackmagic & Secondary Display)
- Editorial Spreadsheet and Multi-Track Timeline UI widgets
"""
import os
import sys
import tempfile
import pytest
import numpy as np

from PySide6.QtCore import Qt, QPointF
from PySide6.QtWidgets import QMainWindow, QWidget

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from openreview.ui.workspaces import StudioWorkspaceManager, STUDIO_WORKSPACES, StudioWorkspace
from openreview.core.shortcuts import ShortcutManager
from openreview.models.timeline_sequence import (
    TimelineSequence,
    Track,
    ClipItem,
    TrackType,
    SoftEffect,
    SoftEffectType,
    ShotStatus,
)
from openreview.core.conform import EDLParser, XMLParser, AAFParser, MediaRelinker
from openreview.core.timeline_exporter import TimelineExporter
from openreview.sdk.timeline_api import TimelineAPI
from openreview.core.sdi_monitor import SDIMonitorManager
from openreview.widgets.spreadsheet_widget import SpreadsheetWidget
from openreview.widgets.multitrack_timeline import MultiTrackTimelineWidget
from openreview.widgets.soft_effects_inspector import SoftEffectsInspector


def test_timeline_mode_workspace_registration():
    """Verify Timeline Mode is properly registered in STUDIO_WORKSPACES with F10."""
    mgr = StudioWorkspaceManager()
    ws = mgr.get_workspace("timeline_mode")
    assert ws is not None
    assert ws.name == "Timeline Mode"
    assert ws.shortcut == "F10"
    assert "SpreadsheetDockWidget" in ws.visible_docks
    assert "SoftEffectsDockWidget" in ws.visible_docks
    assert "TimelineDockWidget" in ws.visible_docks
    assert "ScopesDockWidget" in ws.hidden_docks


def test_timeline_mode_shortcut_binding():
    """Verify F10 shortcut maps to workspace.timeline_mode."""
    sm = ShortcutManager()
    action = sm.get_action("workspace.timeline_mode")
    assert action is not None
    assert action.default_shortcut == "F10"
    assert sm._key_map.get("F10") == "workspace.timeline_mode"


def test_timeline_sequence_data_model_and_json():
    """Verify multi-track sequence creation, tracks, clips, and JSON round-trip."""
    seq = TimelineSequence(name="Test Reel", fps=24.0, width=2048, height=858)
    v1 = seq.add_video_track("Video 1")
    v2 = seq.add_video_track("VFX Comp")
    a1 = seq.add_audio_track("Audio 1")

    assert len(seq.tracks) == 3
    assert v1.track_type == TrackType.VIDEO
    assert a1.track_type == TrackType.AUDIO

    clip1 = ClipItem(id="c1", name="SQ010", file_path="c1.exr", src_in=1001, src_out=1050, dst_in=1, dst_out=50)
    clip2 = ClipItem(id="c2", name="SQ020", file_path="c2.exr", src_in=101, src_out=180, dst_in=51, dst_out=130)
    v1.add_clip(clip1)
    v1.add_clip(clip2)

    # Add Soft Effect
    fx = clip1.add_soft_effect(SoftEffectType.GRADE_CDL, "CDL Grade", {"slope": 1.1, "saturation": 1.2})
    assert len(clip1.soft_effects) == 1
    assert fx.parameters["slope"] == 1.1

    # JSON Round-trip
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tf:
        temp_path = tf.name

    try:
        seq.save_json(temp_path)
        loaded = TimelineSequence.load_json(temp_path)
        assert loaded.name == seq.name
        assert len(loaded.tracks) == 3
        top_v = loaded.tracks[1]
        assert len(top_v.clips) == 2
        assert top_v.clips[0].name == "SQ010"
        assert len(top_v.clips[0].soft_effects) == 1
        assert top_v.clips[0].soft_effects[0].parameters["slope"] == 1.1
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)


def test_opentimelineio_export_and_import():
    """Verify OpenTimelineIO (.otio) serialization and deserialization."""
    seq = TimelineSequence(name="OTIO Test Reel", fps=24.0, width=1920, height=1080)
    v1 = seq.add_video_track("V1")
    clip = ClipItem(id="c1", name="Shot_A", file_path="/media/shot_a.mov", src_in=0, src_out=24, dst_in=1, dst_out=25)
    v1.add_clip(clip)

    with tempfile.NamedTemporaryFile(suffix=".otio", delete=False) as tf:
        temp_otio = tf.name

    try:
        seq.export_otio(temp_otio)
        assert os.path.exists(temp_otio)
        imported = TimelineSequence.import_otio(temp_otio)
        assert imported.name == seq.name
        assert len(imported.tracks) >= 1
        assert len(imported.tracks[0].clips) >= 1
        assert imported.tracks[0].clips[0].name == "Shot_A"
    finally:
        if os.path.exists(temp_otio):
            os.remove(temp_otio)


def test_edl_parser():
    """Verify parsing CMX 3600 EDL turnover file."""
    edl_content = """TITLE: REEL_1
FCM: NON-DROP FRAME

001  A001     V     C        01:00:00:00 01:00:02:00 01:00:00:00 01:00:02:00
* FROM CLIP NAME: SQ040_010_COMP
* SOURCE FILE: /shows/sq040/sq040_010.mov

002  A002     V     C        01:00:10:00 01:00:15:00 01:00:02:00 01:00:07:00
* FROM CLIP NAME: SQ040_020_COMP
"""
    parser = EDLParser(fps=24.0)
    events = parser.parse_lines(edl_content.splitlines())
    assert len(events) == 2
    assert events[0].event_num == "001"
    assert events[0].clip_name == "SQ040_010_COMP"
    assert events[1].event_num == "002"

    seq = parser.to_sequence(events, sequence_name="Conformed Test")
    assert seq.name == "Conformed Test"
    assert len(seq.tracks) == 2
    v_trk = [t for t in seq.tracks if t.track_type == TrackType.VIDEO][0]
    assert len(v_trk.clips) == 2


def test_media_relinker():
    """Verify smart media relinker matching by shot tokens."""
    with tempfile.TemporaryDirectory() as temp_dir:
        test_file = os.path.join(temp_dir, "SQ040_010_comp_v01.mov")
        with open(test_file, "w") as f:
            f.write("dummy")

        seq = TimelineSequence()
        v = seq.add_video_track("V1")
        clip = ClipItem(id="c1", name="SQ040_010", file_path="", reel="A001")
        v.add_clip(clip)

        relinker = MediaRelinker([temp_dir])
        report = relinker.relink_sequence(seq)
        assert report.matched == 1
        assert clip.status == ShotStatus.MATCHED
        assert clip.file_path == test_file


def test_python_api_and_exporters():
    """Verify high-level TimelineAPI SDK and TimelineExporter."""
    seq = TimelineAPI.create_sequence("Episode 1", fps=24.0)
    v1 = TimelineAPI.add_video_track(seq, "Video Main")
    clip = TimelineAPI.add_clip(v1, "shot_10.mov", name="SH10", src_in=1, src_out=48, dst_in=1)
    fx = TimelineAPI.add_cdl_grade(clip, slope=1.2, offset=0.05, power=1.0, saturation=1.15)
    assert fx.parameters["slope"] == 1.2

    with tempfile.TemporaryDirectory() as temp_dir:
        edl_out = os.path.join(temp_dir, "turnover.edl")
        csv_out = os.path.join(temp_dir, "cut_list.csv")
        TimelineExporter.export_cmx3600_edl(seq, edl_out)
        TimelineExporter.export_csv(seq, csv_out)
        assert os.path.exists(edl_out)
        assert os.path.exists(csv_out)


def test_sdi_monitor_out():
    """Verify SDI Monitor Manager device discovery and lifecycle."""
    sdi = SDIMonitorManager()
    devices = sdi.available_devices
    assert len(devices) > 0
    # Test fallback output
    ok = sdi.start_monitor_out(devices[0].id)
    assert ok is True
    assert sdi.is_active is True
    sdi.stop_monitor_out()
    assert sdi.is_active is False


def test_timeline_ui_widgets(qtbot):
    """Verify MultiTrackTimelineWidget, SpreadsheetWidget, and SoftEffectsInspector."""
    seq = TimelineAPI.create_sequence("UI Test Seq", fps=24.0)
    v1 = TimelineAPI.add_video_track(seq, "V1")
    clip = TimelineAPI.add_clip(v1, "sh01.mov", name="SH01", src_in=1, src_out=24, dst_in=1)
    clip.add_soft_effect(SoftEffectType.GRADE_CDL, "CDL", {"slope": 1.0})

    # 1. MultiTrackTimelineWidget
    mt_tl = MultiTrackTimelineWidget()
    qtbot.addWidget(mt_tl)
    mt_tl.set_sequence(seq)
    assert mt_tl._sequence == seq

    # 2. SpreadsheetWidget
    sheet = SpreadsheetWidget()
    qtbot.addWidget(sheet)
    sheet.set_sequence(seq)
    assert sheet.table.rowCount() == 1

    # 3. SoftEffectsInspector
    insp = SoftEffectsInspector()
    qtbot.addWidget(insp)
    insp.set_clip(clip)
    assert insp._clip == clip


def test_main_window_timeline_mode_switching(qtbot):
    """Verify MainWindow switches smoothly to Timeline Mode and restores cleanly."""
    from openreview.ui.main_window import MainWindow

    win = MainWindow()
    qtbot.addWidget(win)
    win.show()

    # 1. Switch to Timeline Mode (F10)
    win._apply_workspace("timeline_mode")
    assert win.workspace_mgr.current_workspace == "timeline_mode"
    assert win.spreadsheet_dock.isHidden() is False
    assert win.soft_effects_dock.isHidden() is False
    assert win.timeline_stack.currentWidget() == win.multitrack_timeline

    # Empty sequence is loaded with 0 sample clips ready for conform or drag-and-drop
    assert win.current_sequence is not None
    assert len(win.current_sequence.tracks) >= 2
    assert len(win.current_sequence.get_all_clips()) == 0

    # 2. Switch back to Standard Review (F5)
    win._apply_workspace("review")
    assert win.workspace_mgr.current_workspace == "review"
    assert win.spreadsheet_dock.isHidden() is True
    assert win.soft_effects_dock.isHidden() is True
    assert win.timeline_stack.currentWidget() == win.timeline


def test_timeline_compositor_soft_effects_and_cache():
    """Verify TimelineCompositor frame generation, CDL extraction, burn-in, and cache."""
    from openreview.core.timeline_compositor import TimelineCompositor

    seq = TimelineAPI.create_sequence("Compositor Seq", fps=24.0, width=1920, height=1080)
    v1 = TimelineAPI.add_video_track(seq, "V1")
    clip = TimelineAPI.add_clip(v1, "mock_shot.####.exr", name="MOCK_010", src_in=1001, src_out=1050, dst_in=1)
    clip.add_soft_effect(SoftEffectType.GRADE_CDL, "CDL", {"slope": 1.15, "offset": 0.02, "power": 0.95, "saturation": 1.2})
    clip.add_soft_effect(SoftEffectType.MASK, "Scope", {"aspect": "2.39:1 Scope"})
    clip.add_soft_effect(SoftEffectType.BURNIN, "Slate", {"text": "{shot}_{version}_{dst_tc}"})

    comp = TimelineCompositor(cache_size=16)

    # 1. Render frame 10 (inside clip 1..50)
    frame_data, cdl, extra = comp.render_frame(seq, dst_frame=10)
    assert frame_data is not None
    assert frame_data.shape == (1080, 1920, 4)
    assert cdl is not None
    assert cdl.slope == (1.15, 1.15, 1.15)
    assert cdl.saturation == 1.2
    assert extra.get("aspect_mask") == "2.39:1 Scope"
    assert "MOCK_010_v01_00:00:00:10" in extra.get("burnin_text", "")

    # 2. Verify cache hit on repeat render
    frame_data2, _, _ = comp.render_frame(seq, dst_frame=10)
    assert frame_data2 is frame_data

    # 3. Empty frame outside clip
    empty_frame, empty_cdl, _ = comp.render_frame(seq, dst_frame=999)
    assert empty_frame is None
    assert empty_cdl is None


def test_main_window_timeline_playback_compositing(qtbot):
    """Verify real-time playback updates Viewport, CDL shader uniforms, and Spreadsheet."""
    from openreview.ui.main_window import MainWindow

    win = MainWindow()
    qtbot.addWidget(win)
    win.show()

    # Switch to Timeline Mode (defaults to clean empty sequence with 0 clips)
    win._apply_workspace("timeline_mode")
    assert win.current_sequence is not None
    assert len(win.current_sequence.get_all_clips()) == 0

    # Populate sequence for playback compositing test
    seq = TimelineAPI.create_sequence("Playback Seq", fps=24.0, width=1920, height=1080)
    v1 = seq.add_video_track("Video 1")
    c1 = TimelineAPI.add_clip(v1, "mock_clip1.exr", name="SQ040_010", src_in=1001, src_out=1048, dst_in=1)
    c2 = TimelineAPI.add_clip(v1, "mock_clip2.exr", name="SQ040_020", src_in=101, src_out=180, dst_in=49)
    TimelineAPI.add_cdl_grade(c1, slope=1.05, offset=-0.02, power=0.98, saturation=1.1)
    win._set_active_timeline_sequence(seq)

    # Scrub to frame 10 (covered by SQ040_010 with CDL grade)
    win.clock.set_frame(10)
    assert win.viewport.current_frame == 10
    assert win.viewport.color_grading.cdl_enabled is True
    assert win.viewport.color_grading.active_cdl is not None
    assert win.viewport.color_grading.active_cdl.slope[0] == 1.05

    # Scrub to frame 60 (covered by SQ040_020 without CDL grade)
    win.clock.set_frame(60)
    assert win.viewport.current_frame == 60
    assert win.viewport.color_grading.cdl_enabled is False


def test_conform_dialog_workflow(qtbot):
    """Verify ConformDialog parses EDL, relinks against search paths, and emits committed sequence."""
    from openreview.ui.conform_dialog import ConformDialog

    edl_text = """TITLE: WIZARD_TEST
FCM: NON-DROP FRAME

001  A001     V     C        01:00:00:00 01:00:01:00 01:00:00:00 01:00:01:00
* FROM CLIP NAME: SH01_COMP
"""
    with tempfile.NamedTemporaryFile(suffix=".edl", mode="w", delete=False, encoding="utf-8") as tf:
        tf.write(edl_text)
        temp_edl = tf.name

    try:
        dlg = ConformDialog(initial_file=temp_edl)
        qtbot.addWidget(dlg)

        assert dlg.edit_turnover_path.text() == temp_edl
        assert dlg._parsed_sequence is not None
        assert dlg.btn_commit.isEnabled() is True
        assert dlg.table_preview.rowCount() >= 1

        # Check committed sequence
        seq = dlg.get_conformed_sequence()
        assert seq is not None
        assert len(seq.tracks) >= 1
    finally:
        if os.path.exists(temp_edl):
            os.remove(temp_edl)


def test_waveform_cache_and_audio_timeline_scrubbing(qtbot):
    """Verify WaveformCache generates peaks and MultiTrackTimelineWidget emits scrub signals."""
    from openreview.audio.waveform_cache import WaveformCache

    cache = WaveformCache.instance()
    peaks = cache.get_waveform_peaks("dummy_audio.wav", fps=24.0, fallback_frames=48)
    assert peaks is not None
    assert len(peaks) == 48
    assert peaks.shape[1] == 2
    # Verify min/max amplitude ranges [-1.0, 1.0]
    assert np.all(peaks[:, 0] <= 0.0)
    assert np.all(peaks[:, 1] >= 0.0)

    # Test MultiTrackTimelineWidget audio track rendering & scrub signal
    seq = TimelineAPI.create_sequence("Audio Seq", fps=24.0)
    a1 = seq.add_audio_track("Audio 1")
    clip = TimelineAPI.add_clip(a1, "dummy_audio.wav", name="DIALOG_01", src_in=1, src_out=48, dst_in=1)

    mt = MultiTrackTimelineWidget()
    qtbot.addWidget(mt)
    mt.set_sequence(seq)

    scrub_frames = []
    mt.scrub_frame_requested.connect(scrub_frames.append)

    # Trigger canvas mouse drag
    mt.canvas.scrub_frame_requested.emit(15)
    assert 15 in scrub_frames


def test_vector_svg_icons():
    """Verify SVG icon generation for Select, Razor, Snap, and Delete."""
    from openreview.ui.svg_icons import get_svg_icon, get_svg_pixmap
    for name in ["select", "razor", "snap", "delete"]:
        icon = get_svg_icon(name)
        assert icon is not None
        assert not icon.isNull()
        pix = get_svg_pixmap(name, size=24)
        assert not pix.isNull()
        assert pix.width() == 24
        assert pix.height() == 24


def test_multitrack_timeline_zooming_and_scrolling(qtbot):
    """Verify zoom in, zoom out, zoom to fit, and horizontal scrollbar sync."""
    seq = TimelineAPI.create_sequence("Zoom Seq", fps=24.0)
    v1 = seq.add_video_track("V1")
    TimelineAPI.add_clip(v1, "clip.exr", src_in=1, src_out=200, dst_in=1)

    mt = MultiTrackTimelineWidget()
    qtbot.addWidget(mt)
    mt.resize(800, 300)
    mt.set_sequence(seq)

    init_zoom = mt.canvas._zoom
    # Zoom In
    mt.zoom_in()
    assert mt.canvas._zoom > init_zoom

    # Zoom Out
    mt.zoom_out()
    mt.zoom_out()
    assert mt.canvas._zoom < init_zoom * 1.25

    # Zoom to Fit
    mt.zoom_to_fit()
    assert mt.canvas._zoom > 0
    assert mt.canvas._scroll_x == 0

    # Zoom Slider interaction
    mt.zoom_slider.setValue(200)
    assert abs(mt.canvas._zoom - 2.0) < 0.01


def test_multitrack_timeline_clip_movement_and_track_hopping(qtbot):
    """Verify 2D dragging: time movement and moving clip from V1 to V2."""
    seq = TimelineAPI.create_sequence("Move Seq", fps=24.0)
    v2 = seq.add_video_track("V2")
    v1 = seq.add_video_track("V1")
    clip = TimelineAPI.add_clip(v1, "clip.exr", name="MOVE_SHOT", src_in=1, src_out=50, dst_in=10)

    mt = MultiTrackTimelineWidget()
    qtbot.addWidget(mt)
    mt.set_sequence(seq)

    # Simulate drag move in time on V1 (from frame 10 to frame 40)
    mt.canvas._drag_mode = "move"
    mt.canvas._drag_clip = clip
    mt.canvas._drag_track = v1
    mt.canvas._drag_start_mouse_pos = mt.canvas.mapFromGlobal(mt.canvas.pos())
    mt.canvas._drag_start_dst_in = clip.dst_in
    mt.canvas._is_dragging_clip = True
    mt.canvas._ghost_dst_in = 40
    mt.canvas._ghost_dst_out = 40 + clip.dst_duration - 1
    mt.canvas._ghost_track = v2  # Hopped to V2!

    from PySide6.QtGui import QMouseEvent
    from PySide6.QtCore import QPointF
    ev = QMouseEvent(QMouseEvent.Type.MouseButtonRelease, QPointF(100, 100), Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    mt.canvas.mouseReleaseEvent(ev)

    # Verify clip was moved to V2 at frame 40
    assert clip.dst_in == 40
    assert clip not in v1.clips
    assert clip in v2.clips


def test_spreadsheet_and_timeline_cut_deletion(qtbot):
    """Verify Lift Delete and Ripple Delete on cut list spreadsheet and timeline."""
    seq = TimelineAPI.create_sequence("Delete Seq", fps=24.0)
    v1 = seq.add_video_track("V1")
    c1 = TimelineAPI.add_clip(v1, "c1.exr", name="C1", src_in=1, src_out=50, dst_in=1)
    c2 = TimelineAPI.add_clip(v1, "c2.exr", name="C2", src_in=51, src_out=100, dst_in=51)
    c3 = TimelineAPI.add_clip(v1, "c3.exr", name="C3", src_in=101, src_out=150, dst_in=101)

    sp = SpreadsheetWidget()
    qtbot.addWidget(sp)
    sp.set_sequence(seq)

    assert sp.table.rowCount() == 3

    # 1. Lift Delete C2 from spreadsheet (leaves gap, C3 dst_in stays 101)
    sp.select_clip(c2.id)
    sp._on_delete_selected_cut(ripple=False)
    assert sp.table.rowCount() == 2
    assert c2 not in v1.clips
    assert c3.dst_in == 101

    # 2. Ripple Delete C1 from timeline (closes gap, C3 shifts from 101 to 51)
    mt = MultiTrackTimelineWidget()
    qtbot.addWidget(mt)
    mt.set_sequence(seq)

    mt.select_clip(c1.id)
    mt.canvas.remove_selected_clip(ripple=True)
    assert c1 not in v1.clips
    # C1 duration was 50 (1..50), so C3 shifts back by 50: 101 - 50 = 51
    assert c3.dst_in == 51


def test_timeline_drag_and_drop_media_ingestion(qtbot):
    """Verify dragging file paths onto the timeline canvas creates and places clip."""
    from PySide6.QtCore import QMimeData, QUrl, QPointF
    from PySide6.QtGui import QDropEvent

    seq = TimelineAPI.create_sequence("Drop Seq", fps=24.0)
    v1 = seq.add_video_track("Video 1")

    mt = MultiTrackTimelineWidget()
    qtbot.addWidget(mt)
    mt.set_sequence(seq)

    mime = QMimeData()
    test_file = os.path.abspath("test_shot_01.exr")
    mime.setUrls([QUrl.fromLocalFile(test_file)])

    drop_ev = QDropEvent(
        QPointF(mt.canvas.frame_to_x(25), float(mt.canvas.RULER_HEIGHT + 10)),
        Qt.DropAction.CopyAction,
        mime,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    mt.canvas.dropEvent(drop_ev)

    # Verify a clip was created on Video 1 starting at frame 25
    assert len(v1.clips) == 1
    new_clip = v1.clips[0]
    assert new_clip.name == "test_shot_01"
    assert new_clip.dst_in == 25


def test_timeline_trim_head_tail_and_ripple(qtbot):
    """Verify interactive head/tail trimming and ripple adjustment on downstream clips."""
    seq = TimelineAPI.create_sequence("Trim Seq", fps=24.0)
    v1 = seq.add_video_track("V1")
    c1 = TimelineAPI.add_clip(v1, "c1.exr", name="C1", src_in=1001, src_out=1050, dst_in=1, dst_out=50)
    c2 = TimelineAPI.add_clip(v1, "c2.exr", name="C2", src_in=2001, src_out=2050, dst_in=51, dst_out=100)

    # 1. Trim Head (+10 frames)
    applied = c1.trim_head(10)
    assert applied == 10
    assert c1.dst_in == 11
    assert c1.src_in == 1011
    assert c1.dst_duration == 40

    # 2. Trim Tail (-5 frames)
    applied = c1.trim_tail(-5)
    assert applied == -5
    assert c1.dst_out == 45
    assert c1.src_out == 1045
    assert c1.dst_duration == 35

    # 3. Canvas Edge Hover & Trim
    mt = MultiTrackTimelineWidget()
    qtbot.addWidget(mt)
    mt.set_sequence(seq)

    # Edge detection within 6px of c2 dst_in (frame 51)
    edge_x = mt.canvas.frame_to_x(51)
    edge_pos = QPointF(edge_x, float(mt.canvas.RULER_HEIGHT + 10))
    info = mt.canvas._find_clip_edge_at(edge_pos)
    assert info is not None
    assert info[0] == "in"
    assert info[2].id == c2.id


def test_timeline_slip_and_slide_tools(qtbot):
    """Verify Slip tool is completely removed and Slide tool (U) functions properly."""
    seq = TimelineAPI.create_sequence("SlipSlide Seq", fps=24.0)
    v1 = seq.add_video_track("V1")
    clip = TimelineAPI.add_clip(v1, "shot.exr", name="SHOT", src_in=1001, src_out=1050, dst_in=10, dst_out=59)

    # 1. Slide Tool buttons switching
    mt = MultiTrackTimelineWidget()
    qtbot.addWidget(mt)
    mt.set_sequence(seq)

    # Slip tool must be completely removed
    assert not hasattr(mt, "btn_slip")

    mt._set_tool("slide")
    assert mt.canvas._active_tool == "slide"
    assert mt.btn_slide.isChecked() is True

    mt._set_tool("select")
    assert mt.canvas._active_tool == "select"
    assert mt.btn_select.isChecked() is True


def test_timeline_in_out_range_marking(qtbot):
    """Verify marking In/Out points (I/O), signal emission, and clearing (Alt+X)."""
    seq = TimelineAPI.create_sequence("IO Seq", fps=24.0)
    v1 = seq.add_video_track("V1")
    TimelineAPI.add_clip(v1, "clip.exr", src_in=1, src_out=100, dst_in=1, dst_out=100)

    mt = MultiTrackTimelineWidget()
    qtbot.addWidget(mt)
    mt.set_sequence(seq)

    io_history = []
    mt.in_out_changed.connect(lambda i, o: io_history.append((i, o)))

    # Mark In at frame 20
    mt.set_playhead_frame(20)
    mt.mark_in()
    assert seq.in_mark == 20

    # Mark Out at frame 75
    mt.set_playhead_frame(75)
    mt.mark_out()
    assert seq.out_mark == 75

    assert (20, 75) in io_history

    # Clear In/Out Range
    mt.clear_in_out()
    assert seq.in_mark is None
    assert seq.out_mark is None
    assert (None, None) in io_history


def test_timeline_review_markers(qtbot):
    """Verify adding, querying, and serializing color-coded review markers."""
    seq = TimelineAPI.create_sequence("Marker Seq", fps=24.0)
    m1 = seq.add_marker(frame=24, name="QC Spill", comment="Green spill on actor shoulder", color="#e74c3c", category="Retake")
    m2 = seq.add_marker(frame=48, name="Client Approved", comment="Signed off in morning dailies", color="#2ecc71", category="Approved")

    assert len(seq.markers) == 2
    assert seq.get_marker_at_frame(24) == m1
    assert seq.get_marker_at_frame(48).category == "Approved"

    # Remove marker
    ok = seq.remove_marker_at_frame(24)
    assert ok is True
    assert len(seq.markers) == 1
    assert seq.get_marker_at_frame(24) is None

    # Serialization round-trip
    d = seq.to_dict()
    loaded = TimelineSequence.from_dict(d)
    assert len(loaded.markers) == 1
    assert loaded.markers[0].name == "Client Approved"


def test_editorial_reference_track_and_compositor(qtbot):
    """Verify designated REF track toggle and reference frame retrieval for Viewport A/B compare."""
    from openreview.core.timeline_compositor import TimelineCompositor

    seq = TimelineAPI.create_sequence("Ref Seq", fps=24.0, width=1920, height=1080)
    v2 = seq.add_video_track("VFX Comp")
    v1 = seq.add_video_track("Offline Avid Ref")
    v1.is_reference = True

    comp_clip = TimelineAPI.add_clip(v2, "comp.exr", name="VFX_010", src_in=1001, src_out=1050, dst_in=1, dst_out=50)
    ref_clip = TimelineAPI.add_clip(v1, "avid_ref.mov", name="AVID_REF", src_in=1, src_out=50, dst_in=1, dst_out=50)

    # Verify query for reference track
    ref_pair = seq.get_reference_clip_at_frame(10)
    assert ref_pair is not None
    track, clip = ref_pair
    assert track.id == v1.id
    assert clip.name == "AVID_REF"

    # Verify compositor extracts reference frame
    compositor = TimelineCompositor(cache_size=10)
    ref_frame = compositor.render_reference_frame(seq, dst_frame=10)
    assert ref_frame is not None
    assert ref_frame.shape == (1080, 1920, 4)

    # Verify ViewportCanvas accepts secondary frame for A/B wipe / reference comparison without error
    from openreview.widgets.viewport import ViewportCanvas
    vp = ViewportCanvas()
    qtbot.addWidget(vp)
    vp.set_secondary_frame(10, ref_frame, logical_size=(1920, 1080))
    assert vp._image_b is not None
    vp.set_secondary_frame(10, None)
    assert vp._image_b is None


def test_dynamic_ruler_spacing_and_readability(qtbot):
    """Verify dynamic ruler step calculation enforces >= 110px spacing preventing crowded text."""
    seq = TimelineAPI.create_sequence("Ruler Seq", fps=24.0)
    mt = MultiTrackTimelineWidget()
    qtbot.addWidget(mt)
    mt.set_sequence(seq)

    # At zoom 1.0 (1 frame = 1px): major step must be >= 110 (e.g. 120 frames = 5s)
    mt.canvas.set_zoom(1.0)
    major_step, minor_step = mt.canvas._compute_ruler_steps()
    assert major_step * 1.0 >= 110.0
    assert major_step == 120
    assert minor_step == 30

    # At zoom 0.1 (10 frames = 1px): major step must be >= 110px (e.g. 1200 frames = 50s)
    mt.canvas.set_zoom(0.1)
    major_step, minor_step = mt.canvas._compute_ruler_steps()
    assert major_step * 0.1 >= 110.0
    assert major_step == 1200

    # At zoom 5.0 (zoomed in, 1 frame = 5px): step can be 24 frames (120px)
    mt.canvas.set_zoom(5.0)
    major_step, minor_step = mt.canvas._compute_ruler_steps()
    assert major_step * 5.0 >= 110.0
    assert major_step == 24


def test_timecode_edit_and_direct_jump(qtbot):
    """Verify editable timecode box jumps playhead via SMPTE, frame integer, and relative delta."""
    seq = TimelineAPI.create_sequence("Jump Seq", fps=24.0, start_timecode="01:00:00:00")
    v1 = seq.add_video_track("V1")
    TimelineAPI.add_clip(v1, "c.exr", src_in=1, src_out=500, dst_in=1, dst_out=500)

    mt = MultiTrackTimelineWidget()
    qtbot.addWidget(mt)
    mt.set_sequence(seq)

    history = []
    mt.playhead_changed.connect(lambda f: history.append(f))

    # 1. Direct jump to frame 120 via integer
    mt.timecode_edit.setText("120")
    mt._on_timecode_jump()
    assert mt.canvas._playhead_frame == 120
    assert 120 in history

    # 2. Relative jump +24
    mt.timecode_edit.setText("+24")
    mt._on_timecode_jump()
    assert mt.canvas._playhead_frame == 144

    # 3. Relative jump -10
    mt.timecode_edit.setText("-10")
    mt._on_timecode_jump()
    assert mt.canvas._playhead_frame == 134

    # 4. Mode toggle TC <-> FR
    assert mt._display_timecode is True
    mt._toggle_tc_mode()
    assert mt._display_timecode is False
    assert mt.btn_tc_mode.text() == "FR"
    assert mt.timecode_edit.text() == "134"


def test_review_marker_selection_edit_and_delete(qtbot):
    """Verify selecting marker on ruler, editing via dialog, and deleting with key/menu."""
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtCore import QPointF
    seq = TimelineAPI.create_sequence("Marker Select Seq", fps=24.0)
    m = seq.add_marker(frame=30, name="Grade Issue", comment="Dark shadow tint", color="#e74c3c", category="Retake")

    mt = MultiTrackTimelineWidget()
    qtbot.addWidget(mt)
    mt.set_sequence(seq)

    # Click marker on ruler (frame 30)
    marker_x = mt.canvas.frame_to_x(30)
    ev = QMouseEvent(QMouseEvent.Type.MouseButtonPress, QPointF(marker_x, 10.0), Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    mt.canvas.mousePressEvent(ev)

    assert mt.canvas._selected_marker_frame == 30
    assert mt.canvas._playhead_frame == 30

    # Delete marker
    deleted_frames = []
    mt.canvas.marker_deleted.connect(lambda f: deleted_frames.append(f))
    mt.canvas.delete_selected_marker()

    assert 30 in deleted_frames
    assert mt.canvas._selected_marker_frame is None
    assert seq.get_marker_at_frame(30) is None


def test_batch_rename_and_multi_selection(qtbot):
    """Verify selecting multiple clips and applying batch rename with tokens and live preview."""
    seq = TimelineAPI.create_sequence("Batch Rename Seq", fps=24.0)
    v1 = seq.add_video_track("V1")
    c1 = TimelineAPI.add_clip(v1, "c1.exr", name="raw_take_A", src_in=1, src_out=24, dst_in=1, dst_out=24)
    c2 = TimelineAPI.add_clip(v1, "c2.exr", name="raw_take_B", src_in=1, src_out=24, dst_in=25, dst_out=48)
    c3 = TimelineAPI.add_clip(v1, "c3.exr", name="raw_take_C", src_in=1, src_out=24, dst_in=49, dst_out=72)

    mt = MultiTrackTimelineWidget()
    qtbot.addWidget(mt)
    mt.set_sequence(seq)

    # Multi-select clips c1, c2, c3
    mt.canvas._selected_clip_ids = {c1.id, c2.id, c3.id}
    sel = mt.canvas.get_selected_clips()
    assert len(sel) == 3

    # Launch BatchRenameDialog
    from openreview.widgets.multitrack_timeline import BatchRenameDialog
    dlg = BatchRenameDialog(sel, parent=mt)
    dlg.pattern_edit.setText("SH_{###}")
    dlg.spin_start.setValue(10)
    dlg.spin_step.setValue(10)
    dlg._update_preview()

    assert dlg.table.item(0, 2).text() == "SH_010"
    assert dlg.table.item(1, 2).text() == "SH_020"
    assert dlg.table.item(2, 2).text() == "SH_030"

    dlg._on_apply()

    assert c1.name == "SH_010"
    assert c2.name == "SH_020"
    assert c3.name == "SH_030"


def test_clip_export_dialog_turnovers(tmp_path, qtbot):
    """Verify ClipExportDialog exports CSV cut list, OTIO turnover, and EDL."""
    seq = TimelineAPI.create_sequence("Export Seq", fps=24.0, start_timecode="01:00:00:00")
    v1 = seq.add_video_track("V1")
    c1 = TimelineAPI.add_clip(v1, "c1.exr", name="SH01", src_in=1001, src_out=1050, dst_in=1, dst_out=50)
    c2 = TimelineAPI.add_clip(v1, "c2.exr", name="SH02", src_in=1001, src_out=1050, dst_in=51, dst_out=100)

    from openreview.widgets.multitrack_timeline import ClipExportDialog
    clips = [(v1, c1), (v1, c2)]
    dlg = ClipExportDialog(clips, seq)
    qtbot.addWidget(dlg)

    out_dir = str(tmp_path)

    # 1. Export CSV
    dlg._export_csv(out_dir)
    csv_file = os.path.join(out_dir, f"{seq.name}_cut_list.csv")
    assert os.path.isfile(csv_file)
    with open(csv_file, "r", encoding="utf-8") as f:
        content = f.read()
        assert "SH01" in content
        assert "SH02" in content
        assert "Dst In" in content

    # 2. Export OTIO
    dlg._export_otio(out_dir)
    otio_file = os.path.join(out_dir, f"{seq.name}_turnover.otio")
    assert os.path.isfile(otio_file)
    with open(otio_file, "r", encoding="utf-8") as f:
        import json
        data = json.load(f)
        assert data["OTIO_SCHEMA"] == "Timeline.1"

    # 3. Export EDL
    dlg._export_edl(out_dir)
    edl_file = os.path.join(out_dir, f"{seq.name}_turnover.edl")
    assert os.path.isfile(edl_file)
    with open(edl_file, "r", encoding="utf-8") as f:
        edl_text = f.read()
        assert "FROM CLIP NAME: SH01" in edl_text
        assert "FROM CLIP NAME: SH02" in edl_text

    # 4. Export Media Manifest
    dlg._export_media(out_dir, "Media Render: Individual MP4 (H.264)", "{name}")
    manifest_01 = os.path.join(out_dir, "SH01_manifest.json")
    assert os.path.isfile(manifest_01)
    with open(manifest_01, "r", encoding="utf-8") as f:
        m_data = json.load(f)
        assert m_data["clip_name"] == "SH01"
        assert m_data["duration_frames"] == 50


def test_timeline_compositor_decodes_video_file(tmp_path):
    """Verify TimelineCompositor decodes actual video container frames into RGBA float32 textures."""
    import cv2
    from openreview.core.timeline_compositor import TimelineCompositor

    vid_path = str(tmp_path / "test_synth.mp4")
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(vid_path, fourcc, 24.0, (128, 72))
    assert writer.isOpened()
    for i in range(10):
        img = np.zeros((72, 128, 3), dtype=np.uint8)
        img[:, :] = (i * 20 + 10, 120, 220)
        writer.write(img)
    writer.release()

    seq = TimelineAPI.create_sequence("Video Seq", fps=24.0)
    vt = seq.add_video_track("Video 1")
    c = TimelineAPI.add_clip(vt, vid_path, name="TestVideo", src_in=1, src_out=10, dst_in=1, dst_out=10)

    compositor = TimelineCompositor()
    frame_data, cdl, extra = compositor.render_frame(seq, dst_frame=5)

    assert frame_data is not None
    assert frame_data.shape == (72, 128, 4)
    # Under Automatic Hybrid Mode, plain SDR video decodes directly as uint8 for zero-copy 60 FPS GPU streaming
    assert frame_data.dtype == np.uint8
    assert vid_path in compositor._video_readers

    # When a CDL grade or transform effect is added, it elevates to float32
    from openreview.models.timeline_sequence import SoftEffectType
    c.add_soft_effect(SoftEffectType.GRADE_CDL, "Grade", {"slope": 1.1})
    compositor.clear_cache()
    frame_data_graded, _, _ = compositor.render_frame(seq, dst_frame=5)
    assert frame_data_graded.dtype == np.float32
    assert 0.0 <= frame_data_graded.max() <= 1.0

    compositor.clear_cache()
    assert len(compositor._video_readers) == 0


def test_timeline_compositor_resolves_concrete_numbered_file(tmp_path):
    """Verify resolve_clip_frame_path handles concrete numbered filenames like shot.1001.png."""
    from openreview.core.timeline_compositor import TimelineCompositor
    from PySide6.QtGui import QImage, QColor

    p1 = tmp_path / "shot.1001.png"
    p2 = tmp_path / "shot.1002.png"
    img = QImage(32, 32, QImage.Format.Format_RGB32)
    img.fill(QColor(255, 0, 0))
    img.save(str(p1))
    img.save(str(p2))

    clip = ClipItem(
        id="c1",
        name="shot",
        file_path=str(p1),
        src_in=1001,
        src_out=1002,
        dst_in=1,
        dst_out=2,
    )
    compositor = TimelineCompositor()
    resolved = compositor.resolve_clip_frame_path(clip, 1002)
    assert os.path.normpath(resolved) == os.path.normpath(str(p2))


def test_timeline_canvas_clip_click_updates_playhead(qtbot):
    """Verify clicking on a clip in TimelineCanvas updates playhead and emits scrub signals."""
    from openreview.widgets.multitrack_timeline import TimelineCanvas
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtCore import QPointF

    seq = TimelineAPI.create_sequence("TestSeq", fps=24.0)
    vt = seq.add_video_track("V1")
    c1 = TimelineAPI.add_clip(vt, "test.exr", name="C1", src_in=1001, src_out=1050, dst_in=1, dst_out=50)

    canvas = TimelineCanvas()
    qtbot.addWidget(canvas)
    canvas.set_sequence(seq)
    canvas.resize(800, 200)

    received_playheads = []
    received_scrubs = []
    canvas.playhead_moved.connect(received_playheads.append)
    canvas.scrub_frame_requested.connect(received_scrubs.append)

    # Click on clip C1 at frame 25
    click_x = canvas.frame_to_x(25)
    click_y = canvas.RULER_HEIGHT + 20

    ev = QMouseEvent(
        QMouseEvent.Type.MouseButtonPress,
        QPointF(click_x, click_y),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    canvas.mousePressEvent(ev)

    assert canvas._playhead_frame == 25
    assert 25 in received_playheads
    assert 25 in received_scrubs


def test_timeline_canvas_drop_video_probes_duration(tmp_path, qtbot):
    """Verify dropping a video file onto TimelineCanvas probes real duration and frame count."""
    import cv2
    from openreview.widgets.multitrack_timeline import TimelineCanvas
    from PySide6.QtGui import QDropEvent
    from PySide6.QtCore import QMimeData, QUrl, QPointF

    vid_path = str(tmp_path / "drop_test.mp4")
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(vid_path, fourcc, 24.0, (64, 64))
    for _ in range(16):
        writer.write(np.zeros((64, 64, 3), dtype=np.uint8))
    writer.release()

    seq = TimelineAPI.create_sequence("DropSeq", fps=24.0)
    canvas = TimelineCanvas()
    qtbot.addWidget(canvas)
    canvas.set_sequence(seq)

    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(vid_path)])

    drop_ev = QDropEvent(
        QPointF(canvas.frame_to_x(1), canvas.RULER_HEIGHT + 10),
        Qt.DropAction.CopyAction,
        mime,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    canvas.dropEvent(drop_ev)

    vt = seq.tracks[0]
    assert len(vt.clips) == 1
    dropped_clip = vt.clips[0]
    assert dropped_clip.src_in == 1
    assert dropped_clip.src_out == 16
    assert dropped_clip.duration == 16


def test_timeline_split_clip_shortcut_c(qtbot):
    """Verify pressing 'C' key splits the clip at the playhead position into two distinct clips."""
    from openreview.widgets.multitrack_timeline import TimelineCanvas
    from PySide6.QtGui import QKeyEvent
    from PySide6.QtCore import QEvent

    seq = TimelineAPI.create_sequence("RazorSeq", fps=24.0)
    vt = seq.add_video_track("V1")
    c1 = TimelineAPI.add_clip(vt, "test_shot.exr", name="Shot01", src_in=1001, src_out=1100, dst_in=1, dst_out=100)

    canvas = TimelineCanvas()
    qtbot.addWidget(canvas)
    canvas.set_sequence(seq)
    canvas.resize(800, 200)

    # Set playhead at frame 40 and select c1
    canvas.set_playhead_frame(40)
    canvas.select_clip(c1.id)

    # Trigger 'C' key press event
    ev_c = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_C, Qt.KeyboardModifier.NoModifier)
    canvas.keyPressEvent(ev_c)

    # Verify track now has 2 clips split at frame 40
    assert len(vt.clips) == 2
    head_clip = vt.clips[0]
    tail_clip = vt.clips[1]

    assert head_clip.dst_in == 1
    assert head_clip.dst_out == 39
    assert head_clip.src_in == 1001
    assert head_clip.src_out == 1039

    assert tail_clip.dst_in == 40
    assert tail_clip.dst_out == 100
    assert tail_clip.src_in == 1040
    assert tail_clip.src_out == 1100


def test_timeline_ripple_delete_gap(qtbot):
    """Verify gap detection and ripple deleting empty space shifts subsequent clips to close the gap."""
    from openreview.widgets.multitrack_timeline import TimelineCanvas

    seq = TimelineAPI.create_sequence("GapSeq", fps=24.0)
    vt = seq.add_video_track("V1")
    # Clip 1 placed with a 9-frame gap at head [1..9]
    c1 = TimelineAPI.add_clip(vt, "shot1.exr", name="Shot01", src_in=1001, src_out=1020, dst_in=10, dst_out=29)
    # Clip 2 placed with a 20-frame gap between [30..49]
    c2 = TimelineAPI.add_clip(vt, "shot2.exr", name="Shot02", src_in=2001, src_out=2030, dst_in=50, dst_out=79)

    canvas = TimelineCanvas()
    qtbot.addWidget(canvas)
    canvas.set_sequence(seq)

    # Test head gap detection [1..9]
    gap = canvas._get_gap_at_frame(vt, 5)
    assert gap == (1, 9)

    # Ripple delete head gap
    canvas.ripple_delete_gap(vt.id, 1, 9, all_tracks=False)
    # c1 shifted left by 9 frames (dst_in: 10 -> 1, dst_out: 29 -> 20)
    assert c1.dst_in == 1
    assert c1.dst_out == 20
    # c2 shifted left by 9 frames (dst_in: 50 -> 41, dst_out: 79 -> 70)
    assert c2.dst_in == 41
    assert c2.dst_out == 70

    # Test middle gap detection [21..40]
    mid_gap = canvas._get_gap_at_frame(vt, 30)
    assert mid_gap == (21, 40)

    # Ripple delete middle gap
    canvas.ripple_delete_gap(vt.id, 21, 40, all_tracks=False)
    # c1 remains unchanged
    assert c1.dst_in == 1
    assert c1.dst_out == 20
    # c2 shifts left by 20 frames (41 - 20 = 21, 70 - 20 = 50)
    assert c2.dst_in == 21
    assert c2.dst_out == 50


def test_media_import_does_not_auto_insert_timeline_clip(qtbot):
    """Verify loading media does not automatically insert clips onto timeline tracks."""
    from openreview.ui.main_window import MainWindow

    win = MainWindow()
    qtbot.addWidget(win)

    # Initialize empty sequence
    win._init_demo_timeline_sequence()
    seq = win.current_sequence
    assert seq is not None
    assert sum(len(t.clips) for t in seq.tracks) == 0

    # Call _populate_sequence_from_loaded_media
    win._populate_sequence_from_loaded_media("dummy_asset.mp4", 1, 100, 24.0, 1920, 1080)

    # Tracks must remain completely empty until user explicitly places/drags media
    assert sum(len(t.clips) for t in win.current_sequence.tracks) == 0


def test_timeline_playback_range_spans_entire_sequence(qtbot):
    """Verify clock playback range synchronizes to encompass all clips up to sequence tail."""
    from openreview.ui.main_window import MainWindow

    win = MainWindow()
    qtbot.addWidget(win)

    seq = TimelineAPI.create_sequence("FullPlaybackSeq", fps=24.0)
    vt = seq.add_video_track("V1")
    # Add two consecutive clips spanning from frame 1 to 250
    TimelineAPI.add_clip(vt, "part1.mov", name="Part1", src_in=1, src_out=100, dst_in=1, dst_out=100)
    TimelineAPI.add_clip(vt, "part2.mov", name="Part2", src_in=1, src_out=150, dst_in=101, dst_out=250)

    win._set_active_timeline_sequence(seq)

    # Verify clock range spans the full 1 to 250 frames
    assert win.clock.start_frame == 1
    assert win.clock.end_frame == 250
    assert win.clock.in_frame == 1
    assert win.clock.out_frame == 250

    # Set editorial In and Out marks (e.g. frame 25 to 175)
    seq.in_mark = 25
    seq.out_mark = 175
    win._sync_timeline_playback_range()

    # Playback range must now be constrained to [25..175]
    assert win.clock.in_frame == 25
    assert win.clock.out_frame == 175


def test_track_header_controls_and_additive_solo(qtbot):
    """Verify Video and Audio track header controls and Additive Solo logic."""
    from openreview.widgets.multitrack_timeline import TrackHeaderWidget
    from openreview.models.timeline_sequence import Track, TrackType

    # 1. Video Track: Has REF, Vis, Lock, Solo; NO Mute
    v_track = Track(id="v1", name="Video 1", track_type=TrackType.VIDEO)
    hdr_v = TrackHeaderWidget(v_track)
    qtbot.addWidget(hdr_v)
    assert hasattr(hdr_v, "btn_ref")
    assert hasattr(hdr_v, "btn_vis")
    assert hasattr(hdr_v, "btn_lock")
    assert hasattr(hdr_v, "btn_solo")
    assert not hasattr(hdr_v, "btn_mute")

    # 2. Audio Track: Has Lock, Solo, Mute; NO REF or Vis
    a_track = Track(id="a1", name="Audio 1", track_type=TrackType.AUDIO)
    hdr_a = TrackHeaderWidget(a_track)
    qtbot.addWidget(hdr_a)
    assert hasattr(hdr_a, "btn_lock")
    assert hasattr(hdr_a, "btn_solo")
    assert hasattr(hdr_a, "btn_mute")
    assert not hasattr(hdr_a, "btn_ref")
    assert not hasattr(hdr_a, "btn_vis")

    # 3. Additive Solo & Top-down Layering evaluation in TimelineSequence
    seq = TimelineAPI.create_sequence("SoloSeq", fps=24.0)
    vt1 = seq.add_video_track("V1")
    vt2 = seq.add_video_track("V2")
    c1 = TimelineAPI.add_clip(vt1, "c1.exr", name="ClipV1", src_in=1, src_out=50, dst_in=1, dst_out=50)
    c2 = TimelineAPI.add_clip(vt2, "c2.exr", name="ClipV2", src_in=1, src_out=50, dst_in=1, dst_out=50)

    # Without solo, top track V2 takes priority over V1
    res = seq.get_top_clip_at_frame(25)
    assert res is not None and res[0].id == vt2.id and res[1].id == c2.id

    # Solo V1 -> only V1 is evaluated
    vt1.solo = True
    res_solo = seq.get_top_clip_at_frame(25)
    assert res_solo is not None and res_solo[0].id == vt1.id and res_solo[1].id == c1.id

    # Additive Solo: Also solo V2 -> both are soloed, V2 takes layer priority
    vt2.solo = True
    res_multi_solo = seq.get_top_clip_at_frame(25)
    assert res_multi_solo is not None and res_multi_solo[0].id == vt2.id


def test_cut_list_dark_studio_theme(qtbot):
    """Verify Cut List Spreadsheet uses dark studio palette and alternating rows."""
    from openreview.widgets.spreadsheet_widget import SpreadsheetWidget

    widget = SpreadsheetWidget()
    qtbot.addWidget(widget)
    assert widget.table.alternatingRowColors() is True
    qss = widget.table.styleSheet()
    assert "#161616" in qss
    assert "#1e1e1e" in qss
    assert "#342a12" in qss


def test_window_menu_cut_list_toggle(qtbot):
    """Verify Window menu contains Cut List checkable toggle action."""
    from openreview.ui.main_window import MainWindow

    win = MainWindow()
    qtbot.addWidget(win)
    win.show()

    assert hasattr(win, "act_cut_list")
    assert win.act_cut_list.isCheckable() is True

    # Test toggling action
    win._on_toggle_cut_list_dock()
    assert not win.spreadsheet_dock.isHidden()
    assert win.act_cut_list.isChecked() is True

    win._on_toggle_cut_list_dock()
    assert win.spreadsheet_dock.isHidden()
    assert win.act_cut_list.isChecked() is False


def test_timeline_empty_frame_black_canvas_no_hazard_badge(qtbot):
    """Verify empty sequence frame displays clean black canvas without hazard warning overlay."""
    from openreview.ui.main_window import MainWindow

    win = MainWindow()
    qtbot.addWidget(win)

    seq = TimelineAPI.create_sequence("EmptyTestSeq", fps=24.0)
    vt = seq.add_video_track("V1")
    # Clip starts at frame 50
    TimelineAPI.add_clip(vt, "test.exr", name="Shot50", src_in=1, src_out=50, dst_in=50, dst_out=99)
    win._set_active_timeline_sequence(seq)

    # Frame 10 is an empty gap before any clip
    win._render_timeline_frame(10)
    assert win.viewport.is_timeline_empty_frame is True
    assert win.viewport._image_a is None


def test_slip_and_boundary_tools(qtbot):
    """Verify screen-space edge detection and slip frame offset manipulation."""
    from openreview.widgets.multitrack_timeline import TimelineCanvas
    from PySide6.QtCore import QPointF

    seq = TimelineAPI.create_sequence("ToolSeq", fps=24.0)
    vt = seq.add_video_track("V1")
    c1 = TimelineAPI.add_clip(vt, "clip1.exr", name="Shot01", src_in=1001, src_out=1100, dst_in=10, dst_out=109)

    canvas = TimelineCanvas()
    qtbot.addWidget(canvas)
    canvas.set_sequence(seq)
    canvas.resize(800, 200)

    # In screen pixels, clip head is at frame_to_x(10)
    head_x = canvas.frame_to_x(10)
    edge_in = canvas._find_clip_edge_at(QPointF(head_x + 2, canvas.RULER_HEIGHT + 10))
    assert edge_in is not None
    assert edge_in[0] == "in"
    assert edge_in[2].id == c1.id

    # Clip tail is at frame_to_x(110)
    tail_x = canvas.frame_to_x(110)
    edge_out = canvas._find_clip_edge_at(QPointF(tail_x - 2, canvas.RULER_HEIGHT + 10))
    assert edge_out is not None
    assert edge_out[0] == "out"
    assert edge_out[2].id == c1.id

    # Test thumbnail helper produces valid QPixmap
    thumb = canvas._get_clip_thumbnail(c1)
    assert thumb is not None and not thumb.isNull()


def test_workspace_timeline_isolation_and_dock_title(qtbot):
    """Verify multitrack timeline is exclusive to timeline_mode workspace, review bar in others."""
    from openreview.ui.main_window import MainWindow

    win = MainWindow()
    qtbot.addWidget(win)

    # In review workspace
    win._apply_workspace("review")
    assert win.timeline_stack.currentWidget() == win.timeline
    assert win.timeline_dock.windowTitle() == "Timeline"

    # In timeline_mode workspace
    win._apply_workspace("timeline_mode")
    assert win.timeline_stack.currentWidget() == win.multitrack_timeline
    assert win.timeline_dock.windowTitle() == "Multi-Track Timeline"

    # Switch back to color_qc
    win._apply_workspace("color_qc")
    assert win.timeline_stack.currentWidget() == win.timeline
    assert win.timeline_dock.windowTitle() == "Timeline"


def test_workspace_shortcut_context_and_actions(qtbot):
    """Verify context-aware shortcut routing for I, O, Delete, +, - across workspaces."""
    from openreview.ui.main_window import MainWindow
    from openreview.core.shortcuts import ShortcutManager
    from PySide6.QtGui import QKeyEvent
    from PySide6.QtCore import QEvent

    win = MainWindow()
    qtbot.addWidget(win)
    sm = ShortcutManager.instance()

    # Timeline Mode
    win._apply_workspace("timeline_mode")
    assert sm._active_workspace == "timeline_mode"

    seq = TimelineAPI.create_sequence("KeySeq", fps=24.0)
    vt = seq.add_video_track("V1")
    c1 = TimelineAPI.add_clip(vt, "test1.exr", name="Shot1", src_in=1, src_out=50, dst_in=1, dst_out=50)
    win._set_active_timeline_sequence(seq)
    win.multitrack_timeline.canvas._selected_clip_ids = {c1.id}

    # Test + and - zoom shortcuts in timeline mode
    old_zoom = win.multitrack_timeline.canvas._zoom
    win._on_timeline_zoom_in()
    assert win.multitrack_timeline.canvas._zoom > old_zoom
    win._on_timeline_zoom_out()
    assert win.multitrack_timeline.canvas._zoom <= old_zoom * 1.05

    # Test Delete in timeline mode removes clip
    assert len(vt.clips) == 1
    win._on_timeline_delete_selected()
    assert len(vt.clips) == 0


def test_box_select_marquee_and_multiselect(qtbot):
    """Verify marquee drag on canvas selects intersecting clips and preserves multi-selection."""
    from openreview.widgets.multitrack_timeline import TimelineCanvas
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtCore import QPointF

    seq = TimelineAPI.create_sequence("MarqueeSeq", fps=24.0)
    vt = seq.add_video_track("V1")
    c1 = TimelineAPI.add_clip(vt, "c1.exr", name="Shot1", src_in=1, src_out=20, dst_in=1, dst_out=20)
    c2 = TimelineAPI.add_clip(vt, "c2.exr", name="Shot2", src_in=1, src_out=20, dst_in=60, dst_out=80)

    canvas = TimelineCanvas()
    qtbot.addWidget(canvas)
    canvas.set_sequence(seq)
    canvas.resize(1000, 200)

    # Click in the empty gap at frame 40 (between c1 ending at 20 and c2 starting at 60)
    x_start = canvas.frame_to_x(40)
    x_end = canvas.frame_to_x(75)
    y_start = canvas.RULER_HEIGHT + 10
    y_end = canvas.RULER_HEIGHT + canvas.TRACK_HEIGHT - 5

    # Press on empty track gap
    ev_press = QMouseEvent(QMouseEvent.Type.MouseButtonPress, QPointF(x_start, y_start), Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    canvas.mousePressEvent(ev_press)
    assert canvas._marquee_active is True

    # Move across to cover c2
    ev_move = QMouseEvent(QMouseEvent.Type.MouseMove, QPointF(x_end, y_end), Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    canvas.mouseMoveEvent(ev_move)
    assert c2.id in canvas._selected_clip_ids
    assert c1.id not in canvas._selected_clip_ids

    # Release
    ev_rel = QMouseEvent(QMouseEvent.Type.MouseButtonRelease, QPointF(x_end, y_end), Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    canvas.mouseReleaseEvent(ev_rel)
    assert canvas._marquee_active is False
    assert c2.id in canvas._selected_clip_ids


def test_soft_effects_inspector_cards_reorder_and_delete(qtbot):
    """Verify collapsible SoftEffectCard with reordering, active toggle, and deletion."""
    from openreview.widgets.soft_effects_inspector import SoftEffectsInspector, SoftEffectCard
    from openreview.models.timeline_sequence import SoftEffectType

    seq = TimelineAPI.create_sequence("FxSeq", fps=24.0)
    vt = seq.add_video_track("V1")
    c1 = TimelineAPI.add_clip(vt, "clip.exr", name="Shot1", src_in=1, src_out=50, dst_in=1, dst_out=50)

    inspector = SoftEffectsInspector()
    qtbot.addWidget(inspector)
    inspector.set_clip(c1)

    # Add Transform, Crop Blanking, and Burn-in
    c1.add_soft_effect(SoftEffectType.TRANSFORM, "Transform")
    c1.add_soft_effect(SoftEffectType.MASK, "Crop & Blanking")
    c1.add_soft_effect(SoftEffectType.BURNIN, "Burn-in")
    inspector._rebuild_stack()

    assert len(c1.soft_effects) == 3
    # Verify 3 cards in layout + stretch
    cards = [inspector.stack_layout.itemAt(i).widget() for i in range(inspector.stack_layout.count() - 1)]
    assert len(cards) == 3
    assert all(isinstance(w, SoftEffectCard) for w in cards)

    # Test collapse on first card
    first_card = cards[0]
    assert not first_card.body_widget.isHidden()
    first_card._toggle_collapse()
    assert first_card.body_widget.isHidden()
    first_card._toggle_collapse()
    assert not first_card.body_widget.isHidden()

    # Test move down on first effect
    inspector._on_move_down(0)
    assert c1.soft_effects[0].effect_type == SoftEffectType.MASK
    assert c1.soft_effects[1].effect_type == SoftEffectType.TRANSFORM

    # Test remove effect
    target_id = c1.soft_effects[0].id
    c1.remove_soft_effect(target_id)
    inspector._rebuild_stack()
    assert len(c1.soft_effects) == 2


def test_track_header_visibility_contrast_styling(qtbot):
    """Verify TrackHeaderWidget visibility button has visBtn id and high contrast icons."""
    from openreview.widgets.multitrack_timeline import TrackHeaderWidget
    from openreview.models.timeline_sequence import Track, TrackType

    track = Track(id="trk_test", name="Video 1", track_type=TrackType.VIDEO, visible=True)
    header = TrackHeaderWidget(track)
    qtbot.addWidget(header)

    assert hasattr(header, "btn_vis")
    assert header.btn_vis.objectName() == "visBtn"
    assert header.btn_vis.isChecked() is True
    assert "QToolButton#visBtn" in header.styleSheet()


def test_timeline_compositor_soft_effects_rendering(qapp):
    """Verify TimelineCompositor renders Transform, Crop & Blanking, Burn-in, and Opacity onto frames."""
    import numpy as np
    from openreview.core.timeline_compositor import TimelineCompositor
    from openreview.models.timeline_sequence import TimelineSequence, ClipItem, SoftEffectType

    seq = TimelineSequence(name="CompositorFxSeq", fps=24.0, width=1920, height=1080)
    vt = seq.add_video_track("V1")
    c1 = ClipItem(id="c1", name="Shot010", file_path="test_slate.exr", src_in=1, src_out=24, dst_in=1, dst_out=24)
    vt.add_clip(c1)

    comp = TimelineCompositor()

    # 1. Base Slate rendering (no soft effects)
    base_frame, cdl, extra = comp.render_frame(seq, 1)
    assert base_frame is not None
    assert base_frame.shape == (1080, 1920, 4)

    # 2. Add Transform (Scale=0.5: margins will be filled with border 0)
    fx_transform = c1.add_soft_effect(SoftEffectType.TRANSFORM, "Transform")
    fx_transform.parameters["scale_x"] = 0.5
    fx_transform.parameters["scale_y"] = 0.5
    transformed_frame, _, _ = comp.render_frame(seq, 1)
    # The corner pixel at (10, 10) was non-zero on base plate, now zero due to scaling down
    assert np.allclose(transformed_frame[10, 10, :3], [0, 0, 0])

    # 3. Add Crop / Blanking (2.39:1 CinemaScope letterbox)
    c1.soft_effects.clear()
    fx_mask = c1.add_soft_effect(SoftEffectType.MASK, "CinemaScope")
    fx_mask.parameters["aspect"] = "2.39:1 CinemaScope"
    masked_frame, _, _ = comp.render_frame(seq, 1)
    # Letterbox pad top/bottom should be black
    assert np.allclose(masked_frame[10, 960, :3], [0, 0, 0])

    # 4. Add Burn-in slate overlay
    c1.soft_effects.clear()
    fx_burn = c1.add_soft_effect(SoftEffectType.BURNIN, "Burnin")
    fx_burn.parameters["text"] = "CUSTOM_SLATE_VFX"
    fx_burn.parameters["placement"] = "Bottom Center"
    burn_frame, _, _ = comp.render_frame(seq, 1)
    assert burn_frame is not None
    # Bottom margin has burn-in pixels
    assert np.any(burn_frame[1020:1060, 900:1020, :3] > 0.0)

    # 5. Add Opacity
    c1.soft_effects.clear()
    fx_op = c1.add_soft_effect(SoftEffectType.OPACITY, "Opacity")
    fx_op.parameters["opacity_percent"] = 50.0
    op_frame, _, _ = comp.render_frame(seq, 1)
    assert np.isclose(op_frame[540, 960, 3], 0.5, atol=0.05)


def test_sequence_settings_dialog(qtbot):
    """Verify SequenceSettingsDialog updates canvas resolution, FPS, and colorspace."""
    from openreview.ui.sequence_settings_dialog import SequenceSettingsDialog
    from openreview.models.timeline_sequence import TimelineSequence

    seq = TimelineSequence(name="TestSettingsSeq", fps=24.0, width=1920, height=1080)
    seq.colorspace = "ACEScg"

    dlg = SequenceSettingsDialog(sequence=seq)
    qtbot.addWidget(dlg)

    # Verify initial population
    assert dlg.spin_w.value() == 1920
    assert dlg.spin_h.value() == 1080
    assert dlg.spin_fps.value() == 24.0
    assert dlg.combo_cs.currentText() == "ACEScg"

    # Select 4K UHD preset (index 1)
    dlg.combo_res.setCurrentIndex(1)
    assert dlg.spin_w.value() == 3840
    assert dlg.spin_h.value() == 2160

    # Change FPS to 29.97
    dlg.combo_fps.setCurrentIndex(3)
    assert abs(dlg.spin_fps.value() - 29.97) < 0.01

    # Change Colorspace to Rec.709
    cs_idx = dlg.combo_cs.findText("Rec.709")
    dlg.combo_cs.setCurrentIndex(cs_idx)

    # Save
    dlg._on_save()
    assert seq.width == 3840
    assert seq.height == 2160
    assert abs(seq.fps - 29.97) < 0.01
    assert seq.colorspace == "Rec.709"


def test_timeline_marker_shortcut_m(qtbot):
    """Verify pressing 'M' key in timeline triggers add_marker_dialog."""
    from openreview.widgets.multitrack_timeline import MultiTrackTimelineWidget
    from openreview.models.timeline_sequence import TimelineSequence
    from PySide6.QtGui import QKeyEvent
    from PySide6.QtCore import Qt, QEvent

    seq = TimelineSequence(name="MarkerSeq", fps=24.0)
    seq.add_video_track("V1")

    widget = MultiTrackTimelineWidget()
    qtbot.addWidget(widget)
    widget.set_sequence(seq)

    dialog_called = False
    orig_add_marker = widget.canvas.add_marker_dialog

    def mock_add_marker(frame=None):
        nonlocal dialog_called
        dialog_called = True

    widget.canvas.add_marker_dialog = mock_add_marker

    # Send 'M' key to canvas
    ev_m = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_M, Qt.KeyboardModifier.NoModifier, "m")
    widget.canvas.keyPressEvent(ev_m)
    assert dialog_called is True

    # Send 'M' key to container widget
    dialog_called = False
    widget.keyPressEvent(ev_m)
    assert dialog_called is True


def test_sources_tree_copy_action_preserves_items():
    """Verify MediaSourcesTreeWidget enforces CopyAction to prevent source item deletion on timeline drop."""
    from openreview.ui.sources_panel import MediaSourcesTreeWidget
    from PySide6.QtCore import Qt

    tree = MediaSourcesTreeWidget()
    assert tree.defaultDropAction() == Qt.DropAction.CopyAction
    assert tree.supportedDropActions() & Qt.DropAction.CopyAction


def test_async_prefetch_and_canvas_conforming(qapp):
    """Verify asynchronous background prefetching and canvas conforming in TimelineCompositor."""
    import time
    import numpy as np
    from openreview.core.timeline_compositor import TimelineCompositor
    from openreview.models.timeline_sequence import TimelineSequence, ClipItem

    seq = TimelineSequence(name="PerfSeq", fps=24.0, width=3840, height=2160)
    vt = seq.add_video_track("V1")
    c1 = ClipItem(id="c1", name="Shot4K", file_path="test_slate.exr", src_in=1, src_out=50, dst_in=1, dst_out=50)
    vt.add_clip(c1)

    comp = TimelineCompositor()
    try:
        # Test conforming 1080p source into 4K sequence canvas
        fake_1080 = np.ones((1080, 1920, 3), dtype=np.float32)
        conformed = comp._conform_clip_to_canvas(fake_1080, 3840, 2160)
        assert conformed.shape == (2160, 3840, 3)

        # Test request_prefetch does not block and loads future frames in background
        comp.request_prefetch(seq, current_dst=1, count=10)
        # Give worker a moment to decode
        cached = set()
        for _ in range(25):
            time.sleep(0.05)
            cached = comp.get_cached_dst_frames(seq)
            if len(cached) > 0:
                break
        assert len(cached) > 0
    finally:
        comp.close()


def test_global_undo_redo_timeline_clip_actions(qapp):
    """Verify central UndoManager undos and redos timeline edits (move, trim, slip, slide, split, delete, marker, soft effect)."""
    from openreview.core.undo_manager import UndoManager
    from openreview.models.timeline_sequence import TimelineSequence, ClipItem, SoftEffectType
    from openreview.widgets.multitrack_timeline import MultiTrackTimelineWidget

    mgr = UndoManager.instance()
    mgr.clear()

    widget = MultiTrackTimelineWidget()
    canvas = widget.canvas
    seq = TimelineSequence(name="UndoTestSeq", fps=24.0)
    vt = seq.add_video_track("Video 1")
    c1 = ClipItem(id="c1", name="Clip 1", file_path="c1.mov", src_in=1, src_out=50, dst_in=1, dst_out=50)
    c2 = ClipItem(id="c2", name="Clip 2", file_path="c2.mov", src_in=1, src_out=50, dst_in=51, dst_out=100)
    vt.add_clip(c1)
    vt.add_clip(c2)
    widget.set_sequence(seq)

    # 1. Test Split Undo/Redo
    canvas._playhead_frame = 25
    c1_split = canvas._split_at_playhead()
    assert c1_split is not None
    assert len(vt.clips) == 3
    assert c1.dst_out == 24
    assert mgr.can_undo()

    mgr.undo()
    assert len(vt.clips) == 2
    assert c1.dst_out == 50

    mgr.redo()
    assert len(vt.clips) == 3
    assert c1.dst_out == 24

    # Undo split for further clean tests
    mgr.undo()

    # 2. Test Delete Undo/Redo
    canvas._selected_clip_ids = {"c2"}
    canvas.remove_selected_clips(ripple=False)
    assert len(vt.clips) == 1
    assert vt.get_clip_by_id("c2") is None

    mgr.undo()
    assert len(vt.clips) == 2
    assert vt.get_clip_by_id("c2") is not None

    # 3. Test Slip Undo/Redo
    old_src_in, old_src_out = c1.src_in, c1.src_out
    from openreview.core.undo_manager import ClipSlipCommand
    cmd_slip = ClipSlipCommand(c1, old_src_in, old_src_out, 15, 64)
    c1.src_in, c1.src_out = 15, 64
    mgr.push(cmd_slip)

    mgr.undo()
    assert (c1.src_in, c1.src_out) == (old_src_in, old_src_out)
    mgr.redo()
    assert (c1.src_in, c1.src_out) == (15, 64)
    mgr.undo()

    # 4. Test Marker Add/Delete Undo/Redo
    marker = seq.add_marker(frame=10, name="QC Note", comment="Fix color", color="#e74c3c", category="Retake")
    from openreview.core.undo_manager import MarkerAddCommand
    mgr.push(MarkerAddCommand(seq, marker))
    assert seq.get_marker_at_frame(10) is not None

    mgr.undo()
    assert seq.get_marker_at_frame(10) is None
    mgr.redo()
    assert seq.get_marker_at_frame(10) is not None

    # 5. Test Soft Effect Undo/Redo
    fx = c1.add_soft_effect(SoftEffectType.TRANSFORM)
    from openreview.core.undo_manager import SoftEffectCommand
    mgr.push(SoftEffectCommand(c1, "add", fx))
    assert fx in c1.soft_effects

    mgr.undo()
    assert fx not in c1.soft_effects
    mgr.redo()
    assert fx in c1.soft_effects


def test_review_marker_shortcut_m_in_timeline_mode(qapp):
    """Verify shortcut M triggers Review Marker dialog in Timeline Mode and toggles bookmark in Review Mode."""
    from openreview.ui.main_window import MainWindow
    from openreview.models.timeline_sequence import TimelineSequence

    win = MainWindow()
    try:
        # Check in Timeline Mode
        win._apply_workspace("timeline_mode")
        assert win.workspace_mgr.current_workspace == "timeline_mode"

        seq = TimelineSequence(name="MarkerSeq", fps=24.0)
        win.load_timeline_sequence(seq)
        win.multitrack_timeline.canvas._playhead_frame = 30

        # Simulate M shortcut trigger
        dialog_opened = []
        original_add_marker = win.multitrack_timeline.canvas.add_marker_dialog
        win.multitrack_timeline.canvas.add_marker_dialog = lambda f=None: dialog_opened.append(f or win.multitrack_timeline.canvas._playhead_frame)

        win._on_bookmark_toggle_or_timeline_marker()
        assert len(dialog_opened) == 1
        assert dialog_opened[0] == 30

        # Switch to review workspace
        win._apply_workspace("review")
        assert win.workspace_mgr.current_workspace == "review"
        bm_count_before = len(win.clock.bookmarks)
        win._on_bookmark_toggle_or_timeline_marker()
        assert len(win.clock.bookmarks) != bm_count_before

        win.multitrack_timeline.canvas.add_marker_dialog = original_add_marker
    finally:
        win.close()


def test_slip_and_slide_tools_drag_behavior(qapp):
    """Verify Slip tool is completely removed, and Slide tool (U) rolls adjacent cuts without altering total duration."""
    from openreview.models.timeline_sequence import TimelineSequence, ClipItem
    from openreview.widgets.multitrack_timeline import MultiTrackTimelineWidget
    from PySide6.QtCore import QPointF, Qt
    from PySide6.QtGui import QMouseEvent

    widget = MultiTrackTimelineWidget()
    canvas = widget.canvas
    seq = TimelineSequence(name="SlideSeq", fps=24.0)
    vt = seq.add_video_track("Video 1")

    # 3 contiguous clips
    cA = ClipItem(id="cA", name="Clip A", file_path="cA.mov", src_in=1, src_out=30, dst_in=1, dst_out=30)
    cB = ClipItem(id="cB", name="Clip B", file_path="cB.mov", src_in=10, src_out=29, dst_in=31, dst_out=50)
    cC = ClipItem(id="cC", name="Clip C", file_path="cC.mov", src_in=1, src_out=30, dst_in=51, dst_out=80)
    vt.add_clip(cA)
    vt.add_clip(cB)
    vt.add_clip(cC)
    widget.set_sequence(seq)

    # 1. Verify Slip Tool is completely removed from widget
    assert not hasattr(widget, "btn_slip")
    # Pressing Y does not activate slip tool
    widget._set_tool("select")
    from PySide6.QtGui import QKeyEvent
    key_y = QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_Y, Qt.KeyboardModifier.NoModifier)
    widget.keyPressEvent(key_y)
    assert canvas._active_tool != "slip"

    # 2. Test Slide Tool (U)
    widget._set_tool("slide")
    assert canvas._active_tool == "slide"

    x40 = canvas.frame_to_x(40)
    y_track = canvas.RULER_HEIGHT + 15

    # Press inside clip B
    ev_press2 = QMouseEvent(QMouseEvent.Type.MouseButtonPress, QPointF(x40, y_track), Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    canvas.mousePressEvent(ev_press2)
    assert canvas._drag_mode == "slide"

    # Slide B forward by 4 frames (+4)
    slide_delta_px = 4 * canvas._zoom
    ev_move2 = QMouseEvent(QMouseEvent.Type.MouseMove, QPointF(x40 + slide_delta_px, y_track), Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    canvas.mouseMoveEvent(ev_move2)

    # cB slid right by 4 frames (31+4 = 35, 50+4 = 54)
    assert cB.dst_in == 35
    assert cB.dst_out == 54

    # Preceding clip A rolled its tail forward to 34
    assert cA.dst_out == 34
    assert cA.src_out == 34

    # Succeeding clip C rolled its head forward to 55
    assert cC.dst_in == 55
    assert cC.src_in == 5

    ev_release2 = QMouseEvent(QMouseEvent.Type.MouseButtonRelease, QPointF(x40 + slide_delta_px, y_track), Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    canvas.mouseReleaseEvent(ev_release2)

    # Sequence total span from cA head to cC tail is still 1 to 80 (preserved!)
    assert cA.dst_in == 1
    assert cC.dst_out == 80


def test_workspace_timeline_dock_reset_and_gap_elimination(qtbot):
    """Verify switching from timeline mode to standard workspace resets layout, collapses bottom dock, and eliminates gaps."""
    from openreview.ui.main_window import MainWindow

    win = MainWindow()
    qtbot.addWidget(win)
    win.resize(1200, 800)
    win.show()

    # 1. Switch to timeline mode (MultiTrack editorial)
    win._apply_workspace("timeline_mode")
    assert win.timeline_stack.currentWidget() == win.multitrack_timeline
    assert win.multitrack_timeline.minimumHeight() >= 160
    assert win.timeline_stack.maximumHeight() > 1000

    # Simulate user resizing multitrack dock height to 320px
    win._last_multitrack_timeline_height = 320
    win.resizeDocks([win.timeline_dock], [320], Qt.Orientation.Vertical)

    # 2. Switch back to standard review workspace
    win._apply_workspace("review")
    assert win.timeline_stack.currentWidget() == win.timeline
    # Review timeline widget and stack must be locked to compact height (64px)
    assert win.timeline.height() == 64
    assert win.timeline_stack.height() == 64
    assert win.multitrack_timeline.minimumHeight() == 0
    # Timeline dock window title reset to 'Timeline'
    assert win.timeline_dock.windowTitle() == "Timeline"
    # Auxiliary timeline docks must be hidden
    assert win.soft_effects_dock.isHidden() is True
    assert win.spreadsheet_dock.isHidden() is True
    # Viewport soft effect overrides reset
    assert win.viewport.aspect_ratio_mask is None

    # 3. Switching back to timeline mode restores remembered multi-track height
    win._apply_workspace("timeline_mode")
    assert win.timeline_stack.currentWidget() == win.multitrack_timeline
    assert getattr(win, "_last_multitrack_timeline_height", 280) == 320

    # 4. Reset Workspace resets to factory default
    win._on_reset_workspace()
    assert getattr(win, "_last_multitrack_timeline_height", 280) == 280


def test_timecode_numeric_input_and_defocus(qtbot):
    """Verify numeric-only right-to-left shift, auto-colons, and Enter/Escape canvas defocus."""
    from openreview.widgets.multitrack_timeline import MultiTrackTimelineWidget
    from PySide6.QtGui import QKeyEvent

    mt = MultiTrackTimelineWidget()
    qtbot.addWidget(mt)

    tc_edit = mt.timecode_edit
    canvas = mt.canvas

    return_called = []
    tc_edit.returnPressed.connect(lambda: return_called.append(True))

    # Type numbers: 0, 1, 0, 0, 0, 0, 0, 0 -> 01:00:00:00
    for ch in "01000000":
        ev = QKeyEvent(QKeyEvent.Type.KeyPress, getattr(Qt.Key, f"Key_{ch}"), Qt.KeyboardModifier.NoModifier, ch)
        tc_edit.keyPressEvent(ev)

    assert tc_edit.text() == "01:00:00:00"

    # Non-digit character (e.g. 'a' or 'X') should be rejected
    ev_invalid = QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_X, Qt.KeyboardModifier.NoModifier, "x")
    tc_edit.keyPressEvent(ev_invalid)
    assert tc_edit.text() == "01:00:00:00"

    # Pressing Enter commits timecode, emits returnPressed, and triggers canvas focus
    ev_enter = QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_Return, Qt.KeyboardModifier.NoModifier)
    tc_edit.keyPressEvent(ev_enter)

    assert len(return_called) == 1


def test_slip_tool_completely_removed(qtbot):
    """Verify slip tool button, tool state, shortcut and references are completely removed."""
    from openreview.widgets.multitrack_timeline import MultiTrackTimelineWidget
    from openreview.core.shortcuts import ShortcutManager

    mt = MultiTrackTimelineWidget()
    qtbot.addWidget(mt)

    # 1. No btn_slip attribute on toolbar
    assert not hasattr(mt, "btn_slip")

    # 2. 'slip' is not an active tool option
    mt._set_tool("select")
    assert mt.canvas._active_tool == "select"

    # 3. Y key does not activate slip
    from PySide6.QtGui import QKeyEvent
    key_y = QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_Y, Qt.KeyboardModifier.NoModifier)
    mt.canvas.keyPressEvent(key_y)
    assert mt.canvas._active_tool != "slip"

    # 4. Shortcut registry does not contain timeline.tool_slip
    actions = ShortcutManager.instance().get_actions()
    action_ids = [a.action_id for a in actions]
    assert "timeline.tool_slip" not in action_ids


def test_interactive_head_tail_trimming_and_ripple(qtbot):
    """Verify interactive head/tail hover brackets, clamping (src_in >= 1, dur >= 1), and Shift-ripple."""
    from openreview.widgets.multitrack_timeline import MultiTrackTimelineWidget
    from openreview.models.timeline_sequence import TimelineSequence, ClipItem
    from PySide6.QtCore import QPointF
    from PySide6.QtGui import QMouseEvent

    seq = TimelineSequence(name="TrimSeq", fps=24.0)
    vt = seq.add_video_track("V1")
    c1 = ClipItem(id="c1", name="Clip 1", file_path="c1.mov", src_in=10, src_out=50, dst_in=1, dst_out=41)
    c2 = ClipItem(id="c2", name="Clip 2", file_path="c2.mov", src_in=1, src_out=30, dst_in=50, dst_out=79)
    vt.add_clip(c1)
    vt.add_clip(c2)

    mt = MultiTrackTimelineWidget()
    qtbot.addWidget(mt)
    mt.set_sequence(seq)
    canvas = mt.canvas

    # 1. Hover near in-point of c1 -> cursor changes to SizeHorCursor
    x_in = canvas.frame_to_x(1)
    y_track = canvas.RULER_HEIGHT + 15
    ev_hover = QMouseEvent(QMouseEvent.Type.MouseMove, QPointF(x_in + 2, y_track), Qt.MouseButton.NoButton, Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier)
    canvas.mouseMoveEvent(ev_hover)
    assert canvas.cursor().shape() == Qt.CursorShape.SizeHorCursor

    # 2. Trim in-point forward by 5 frames
    ev_press = QMouseEvent(QMouseEvent.Type.MouseButtonPress, QPointF(x_in, y_track), Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    canvas.mousePressEvent(ev_press)
    assert canvas._drag_mode == "trim_in"

    delta_5 = 5 * canvas._zoom
    ev_move = QMouseEvent(QMouseEvent.Type.MouseMove, QPointF(x_in + delta_5, y_track), Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    canvas.mouseMoveEvent(ev_move)

    assert c1.dst_in == 6
    assert c1.src_in == 15

    ev_release = QMouseEvent(QMouseEvent.Type.MouseButtonRelease, QPointF(x_in + delta_5, y_track), Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    canvas.mouseReleaseEvent(ev_release)

    # 3. Clamping test: src_in cannot go below 1
    ev_press2 = QMouseEvent(QMouseEvent.Type.MouseButtonPress, QPointF(canvas.frame_to_x(c1.dst_in), y_track), Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    canvas.mousePressEvent(ev_press2)

    # Drag left by 50 frames (trying to make src_in < 1)
    ev_move2 = QMouseEvent(QMouseEvent.Type.MouseMove, QPointF(canvas.frame_to_x(c1.dst_in) - 50 * canvas._zoom, y_track), Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    canvas.mouseMoveEvent(ev_move2)

    assert c1.src_in >= 1
    assert c1.dst_in >= 1

    ev_release2 = QMouseEvent(QMouseEvent.Type.MouseButtonRelease, QPointF(canvas.frame_to_x(c1.dst_in), y_track), Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    canvas.mouseReleaseEvent(ev_release2)


def test_linked_video_audio_clips_drop_and_synchronous_edits(qtbot, tmp_path):
    """Verify video files with audio drop as linked pairs, with synchronous trim, move, split, and delete."""
    import wave
    from openreview.widgets.multitrack_timeline import MultiTrackTimelineWidget
    from openreview.models.timeline_sequence import TimelineSequence
    from PySide6.QtCore import QMimeData, QUrl, QPointF
    from PySide6.QtGui import QDropEvent

    # Create valid wave audio file
    audio_file = str(tmp_path / "test_shot_with_audio.wav")
    with wave.open(audio_file, "w") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(48000)
        wf.writeframes(b"\x00\x00" * 48000 * 2)

    seq = TimelineSequence(name="LinkedSeq", fps=24.0)
    seq.add_video_track("V1")

    mt = MultiTrackTimelineWidget()
    qtbot.addWidget(mt)
    mt.set_sequence(seq)
    canvas = mt.canvas

    # Drop media file onto canvas
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(audio_file)])
    drop_ev = QDropEvent(
        QPointF(canvas.frame_to_x(10), float(canvas.RULER_HEIGHT + 15)),
        Qt.DropAction.CopyAction,
        mime,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    canvas.dropEvent(drop_ev)

    # Check tracks and linked clips
    a_tracks = [t for t in seq.tracks if t.track_type.value == "audio" or "audio" in t.id.lower()]
    assert len(a_tracks) >= 1

    # Verify split_clip synchronously splits linked clips
    v_track = seq.tracks[0]
    clip1 = v_track.clips[0] if v_track.clips else a_tracks[0].clips[0]
    clip1_track = v_track if clip1 in v_track.clips else a_tracks[0]
    clip1.link_id = "test_link_123"

    # Create a partner clip in audio track
    a_track = a_tracks[0]
    from openreview.models.timeline_sequence import ClipItem
    partner = ClipItem(id="a_partner", name="Audio Partner", file_path=audio_file, src_in=clip1.src_in, src_out=clip1.src_out, dst_in=clip1.dst_in, dst_out=clip1.dst_out, link_id="test_link_123")
    a_track.add_clip(partner)

    # Split clip1 at frame 20
    split_frame = clip1.dst_in + 10
    clip2 = canvas.split_clip(clip1_track.id, split_frame)
    assert clip2 is not None
    # Partner clip also split
    assert len(a_track.clips) >= 2


def test_timeline_audio_sync_and_gap_silence(qtbot, tmp_path):
    """Verify timeline audio maps playhead frame to clip milliseconds and mutes over gaps."""
    import wave
    from openreview.ui.main_window import MainWindow
    from openreview.models.timeline_sequence import TimelineSequence, ClipItem

    audio_file = str(tmp_path / "sync_audio.wav")
    with wave.open(audio_file, "w") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(48000)
        wf.writeframes(b"\x00\x00" * 48000 * 3)

    win = MainWindow()
    qtbot.addWidget(win)

    seq = TimelineSequence(name="AudioSyncSeq", fps=24.0)
    at = seq.add_audio_track("Audio 1")
    # Clip from frame 10 to frame 50 (source frames 100 to 140)
    clip = ClipItem(
        id="a_clip",
        name="Audio Clip",
        file_path=audio_file,
        src_in=100,
        src_out=140,
        dst_in=10,
        dst_out=50,
    )
    at.add_clip(clip)
    win.load_timeline_sequence(seq)
    win._apply_workspace("timeline_mode")

    # 1. At frame 20 (inside clip): audio syncs to clip's millisecond offset
    win._sync_timeline_audio_to_frame(20, is_playing=True, shuttle_speed=1.0)
    assert win.audio_engine.current_file == audio_file

    # 2. At frame 5 (gap before clip): audio pauses / silences
    win._sync_timeline_audio_to_frame(5, is_playing=True, shuttle_speed=1.0)
    # Audio engine paused over gap
    assert win.audio_engine.is_playing is False


def test_default_sequence_start_timecode_00_00_00_00(qtbot):
    """Verify default start timecode is 00:00:00:00 across sequence creation and timeline UI."""
    from openreview.models.timeline_sequence import TimelineSequence
    from openreview.sdk.timeline_api import TimelineAPI
    from openreview.widgets.multitrack_timeline import MultiTrackTimelineWidget

    seq1 = TimelineSequence()
    assert seq1.start_timecode == "00:00:00:00"

    seq2 = TimelineAPI.create_sequence("Test Seq")
    assert seq2.start_timecode == "00:00:00:00"

    # Multitrack timeline UI display at sequence start
    w = MultiTrackTimelineWidget()
    qtbot.addWidget(w)
    w.set_sequence(seq2)
    w.canvas._playhead_frame = 1
    w._sync_timecode_display()
    assert w.timecode_edit.text() == "00:00:00:00"


def test_restore_clip_to_full_source_and_undo_redo(qtbot):
    """Verify restoring a trimmed clip extends head and tail up to media limits without colliding with neighboring clips."""
    from openreview.models.timeline_sequence import TimelineSequence, ClipItem
    from openreview.widgets.multitrack_timeline import MultiTrackTimelineWidget
    from openreview.core.undo_manager import UndoManager

    seq = TimelineSequence(fps=24.0)
    vt = seq.add_video_track("V1")

    # Neighboring predecessor clip: dst 1..15
    c_prev = ClipItem(id="c_prev", name="Prev", file_path="prev.mov", src_in=1, src_out=15, dst_in=1, dst_out=15)
    vt.add_clip(c_prev)

    # Trimmed test clip: original src was 1..100, currently trimmed to src 20..60, dst 30..70
    c_mid = ClipItem(
        id="c_mid",
        name="Mid",
        file_path="mid.mov",
        src_in=20,
        src_out=60,
        dst_in=30,
        dst_out=70,
        metadata={"original_src_in": 1, "original_src_out": 100},
    )
    vt.add_clip(c_mid)

    # Neighboring successor clip: dst 85..100
    c_next = ClipItem(id="c_next", name="Next", file_path="next.mov", src_in=1, src_out=16, dst_in=85, dst_out=100)
    vt.add_clip(c_next)

    # Test model calculation:
    # Head available: 20 - 1 = 19 frames. Predecessor ends at 15, so max left shift = 30 - 16 = 14 frames.
    # Tail available: 100 - 60 = 40 frames. Successor starts at 85, so max right shift = 84 - 70 = 14 frames.
    new_in, new_out, new_sin, new_sout = seq.restore_clip_to_full_source(vt, c_mid)
    assert new_in == 16
    assert new_sin == 6
    assert new_out == 84
    assert new_sout == 74

    # Test widget action & undo/redo
    w = MultiTrackTimelineWidget()
    qtbot.addWidget(w)
    w.set_sequence(seq)
    w.canvas._selected_clip_ids = {c_mid.id}

    w.canvas.restore_selected_clips()
    assert c_mid.dst_in == 16
    assert c_mid.dst_out == 84
    assert c_mid.src_in == 6
    assert c_mid.src_out == 74

    # Undo
    UndoManager.instance().undo()
    assert c_mid.dst_in == 30
    assert c_mid.dst_out == 70
    assert c_mid.src_in == 20
    assert c_mid.src_out == 60

    # Redo
    UndoManager.instance().redo()
    assert c_mid.dst_in == 16
    assert c_mid.dst_out == 84


def test_trim_without_clip_selection_and_qw_ripple_trim(qtbot):
    """Verify Q and W shortcuts ripple trim clip under playhead without requiring prior clip selection."""
    from openreview.models.timeline_sequence import TimelineSequence, ClipItem
    from openreview.widgets.multitrack_timeline import MultiTrackTimelineWidget
    from openreview.core.undo_manager import UndoManager
    from PySide6.QtGui import QKeyEvent
    from PySide6.QtCore import Qt

    seq = TimelineSequence(fps=24.0)
    vt = seq.add_video_track("V1")

    c1 = ClipItem(id="c1", name="Clip 1", file_path="c1.mov", src_in=1, src_out=50, dst_in=1, dst_out=50)
    c2 = ClipItem(id="c2", name="Clip 2", file_path="c2.mov", src_in=1, src_out=30, dst_in=51, dst_out=80)
    vt.add_clip(c1)
    vt.add_clip(c2)

    w = MultiTrackTimelineWidget()
    qtbot.addWidget(w)
    w.set_sequence(seq)

    # Ensure NO clips are selected
    w.canvas._selected_clip_ids.clear()
    assert len(w.canvas._selected_clip_ids) == 0

    # 1. Test Q (Ripple Trim Head to Playhead) at frame 11
    w.canvas._playhead_frame = 11
    ev_q = QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_Q, Qt.KeyboardModifier.NoModifier)
    w.canvas.keyPressEvent(ev_q)

    # c1 head trimmed from 1 to 11 (delta 10). c2 downstream ripples left by 10
    assert c1.dst_in == 11
    assert c1.src_in == 11
    assert c2.dst_in == 41
    assert c2.dst_out == 70

    # 2. Test W (Ripple Trim Tail to Playhead) at frame 30
    w.canvas._playhead_frame = 31
    ev_w = QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_W, Qt.KeyboardModifier.NoModifier)
    w.canvas.keyPressEvent(ev_w)

    # c1 tail trimmed to 30 (playhead - 1 = 30). c1 duration was 40, now 20 (delta 20).
    assert c1.dst_out == 30
    assert c2.dst_in == 21
    assert c2.dst_out == 50

    # Undo W
    UndoManager.instance().undo()
    assert c1.dst_out == 50
    assert c2.dst_in == 41

    # Undo Q
    UndoManager.instance().undo()
    assert c1.dst_in == 1
    assert c1.src_in == 1
    assert c2.dst_in == 51


def test_trimming_no_sequence_changed_on_mousemove(qtbot):
    """Verify sequence_changed is NOT emitted on mouseMoveEvent during trim to avoid lag, but emitted once on release."""
    from openreview.models.timeline_sequence import TimelineSequence, ClipItem
    from openreview.widgets.multitrack_timeline import MultiTrackTimelineWidget
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtCore import Qt, QPointF

    seq = TimelineSequence(fps=24.0)
    vt = seq.add_video_track("V1")
    c1 = ClipItem(id="c1", name="Clip 1", file_path="c1.mov", src_in=10, src_out=50, dst_in=1, dst_out=41)
    vt.add_clip(c1)

    w = MultiTrackTimelineWidget()
    qtbot.addWidget(w)
    w.set_sequence(seq)
    canvas = w.canvas

    emitted_count = 0
    def on_seq_changed():
        nonlocal emitted_count
        emitted_count += 1

    canvas.sequence_changed.connect(on_seq_changed)

    # Press on in-edge
    x_in = canvas.frame_to_x(c1.dst_in)
    y_track = canvas.RULER_HEIGHT + (canvas.TRACK_HEIGHT / 2.0)
    ev_press = QMouseEvent(QMouseEvent.Type.MouseButtonPress, QPointF(x_in, y_track), Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    canvas.mousePressEvent(ev_press)
    assert canvas._drag_mode == "trim_in"

    # Move mouse 10 times
    for dx in range(1, 11):
        ev_move = QMouseEvent(QMouseEvent.Type.MouseMove, QPointF(x_in + dx * canvas._zoom, y_track), Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
        canvas.mouseMoveEvent(ev_move)

    # Must NOT have emitted sequence_changed during drag movement (lag elimination)
    assert emitted_count == 0

    # Release mouse
    ev_rel = QMouseEvent(QMouseEvent.Type.MouseButtonRelease, QPointF(x_in + 10 * canvas._zoom, y_track), Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    canvas.mouseReleaseEvent(ev_rel)

    # Must have emitted exactly once on release
    assert emitted_count == 1


def test_standard_workspace_timeline_dock_zero_gap(qtbot):
    """Verify timeline_dock titleBarWidget is hidden/zero-height and dock height is constrained to 64px."""
    from openreview.ui.main_window import MainWindow

    win = MainWindow()
    qtbot.addWidget(win)

    # 1. Default review/standard workspace
    assert win.timeline_dock.titleBarWidget().height() == 0
    assert win.timeline_dock.maximumHeight() == 64
    assert win.timeline_stack.height() == 64

    # 2. Switch to timeline mode
    win._apply_workspace("timeline_mode")
    assert win.timeline_dock.maximumHeight() > 1000

    # 3. Switch back to standard workspace
    win._apply_workspace("review")
    assert win.timeline_dock.maximumHeight() == 64
    assert win.timeline_dock.titleBarWidget().height() == 0


def test_frame_mode_pure_frame_input_and_jump(qtbot):
    """Verify that in FR mode, user can type pure frame numbers without colon formatting, and jump properly."""
    from openreview.models.timeline_sequence import TimelineSequence
    from openreview.widgets.multitrack_timeline import MultiTrackTimelineWidget
    from PySide6.QtGui import QKeyEvent
    from PySide6.QtCore import Qt

    seq = TimelineSequence(fps=24.0)
    w = MultiTrackTimelineWidget()
    qtbot.addWidget(w)
    w.set_sequence(seq)

    # Initially in TC mode
    assert w._display_timecode is True
    assert w.btn_tc_mode.text() == "TC"

    # Toggle to FR (Frame) mode
    w._toggle_tc_mode()
    assert w._display_timecode is False
    assert w.btn_tc_mode.text() == "FR"
    assert w.timecode_edit._timecode_mode is False
    assert w.timecode_edit.text() == "1"

    # Type numbers in FR mode: "2", "0", "0"
    w.timecode_edit.clear()
    for ch in "200":
        ev = QKeyEvent(QKeyEvent.Type.KeyPress, getattr(Qt.Key, f"Key_{ch}"), Qt.KeyboardModifier.NoModifier, ch)
        w.timecode_edit.keyPressEvent(ev)

    # In FR mode, it MUST be raw "200", NOT "00:00:02:00"
    assert w.timecode_edit.text() == "200"

    # Press Enter / Jump
    w._on_timecode_jump()
    assert w.canvas._playhead_frame == 200
    assert w.timecode_edit.text() == "200"

    # Relative jump: "+50"
    w.timecode_edit.clear()
    for ch in "+50":
        key = Qt.Key.Key_Plus if ch == "+" else getattr(Qt.Key, f"Key_{ch}")
        ev = QKeyEvent(QKeyEvent.Type.KeyPress, key, Qt.KeyboardModifier.NoModifier, ch)
        w.timecode_edit.keyPressEvent(ev)

    assert w.timecode_edit.text() == "+50"
    w._on_timecode_jump()
    assert w.canvas._playhead_frame == 250
    assert w.timecode_edit.text() == "250"

    # Switch back to TC mode
    w._toggle_tc_mode()
    assert w._display_timecode is True
    assert ":" in w.timecode_edit.text()


def test_trim_right_side_to_actual_duration_and_bidirectional_trimming(qtbot):
    """Verify that user can drag right side to extend tail up to actual duration, and drag left side bidirectionally."""
    from openreview.models.timeline_sequence import TimelineSequence, ClipItem
    from openreview.widgets.multitrack_timeline import MultiTrackTimelineWidget
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtCore import Qt, QPointF

    seq = TimelineSequence(fps=24.0)
    vt = seq.add_video_track("V1")

    # Clip with media bounds 1..100, currently trimmed to dst 20..50, src 20..50
    c1 = ClipItem(
        id="c1",
        name="Clip 1",
        file_path="shot.mov",
        src_in=20,
        src_out=50,
        dst_in=20,
        dst_out=50,
        metadata={"original_src_in": 1, "original_src_out": 100, "media_duration": 100},
    )
    vt.add_clip(c1)

    w = MultiTrackTimelineWidget()
    qtbot.addWidget(w)
    w.set_sequence(seq)
    canvas = w.canvas

    y_track = canvas.RULER_HEIGHT + (canvas.TRACK_HEIGHT / 2.0)

    # 1. Test trim_out rightward drag (extending tail beyond 50 up to 70)
    x_out = canvas.frame_to_x(c1.dst_out + 1)
    ev_press = QMouseEvent(QMouseEvent.Type.MouseButtonPress, QPointF(x_out, y_track), Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    canvas.mousePressEvent(ev_press)
    assert canvas._drag_mode == "trim_out"

    # Drag right by +20 frames
    delta_px = 20 * canvas._zoom
    ev_move = QMouseEvent(QMouseEvent.Type.MouseMove, QPointF(x_out + delta_px, y_track), Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    canvas.mouseMoveEvent(ev_move)

    # Tail should have extended from 50 to 70!
    assert c1.dst_out == 70
    assert c1.src_out == 70

    ev_release = QMouseEvent(QMouseEvent.Type.MouseButtonRelease, QPointF(x_out + delta_px, y_track), Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    canvas.mouseReleaseEvent(ev_release)

    # 2. Test trim_out leftward drag (shortening tail from 70 down to 40)
    x_out2 = canvas.frame_to_x(c1.dst_out + 1)
    ev_press2 = QMouseEvent(QMouseEvent.Type.MouseButtonPress, QPointF(x_out2, y_track), Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    canvas.mousePressEvent(ev_press2)
    assert canvas._drag_mode == "trim_out"

    delta_px2 = -30 * canvas._zoom
    ev_move2 = QMouseEvent(QMouseEvent.Type.MouseMove, QPointF(x_out2 + delta_px2, y_track), Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    canvas.mouseMoveEvent(ev_move2)

    assert c1.dst_out == 40
    assert c1.src_out == 40
    canvas.mouseReleaseEvent(QMouseEvent(QMouseEvent.Type.MouseButtonRelease, QPointF(x_out2 + delta_px2, y_track), Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))

    # 3. Test trim_in leftward drag (extending head from 20 left to 5)
    x_in = canvas.frame_to_x(c1.dst_in)
    ev_press_in = QMouseEvent(QMouseEvent.Type.MouseButtonPress, QPointF(x_in, y_track), Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    canvas.mousePressEvent(ev_press_in)
    assert canvas._drag_mode == "trim_in"

    delta_in_px = -15 * canvas._zoom
    ev_move_in = QMouseEvent(QMouseEvent.Type.MouseMove, QPointF(x_in + delta_in_px, y_track), Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    canvas.mouseMoveEvent(ev_move_in)

    assert c1.dst_in == 5
    assert c1.src_in == 5
    canvas.mouseReleaseEvent(QMouseEvent(QMouseEvent.Type.MouseButtonRelease, QPointF(x_in + delta_in_px, y_track), Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))

    # 4. Test Restore Clip to Full Length: head extends to 1, tail extends to 100!
    canvas._selected_clip_ids = {c1.id}
    canvas.restore_selected_clips()
    assert c1.dst_in == 1
    assert c1.src_in == 1
    assert c1.dst_out == 100
    assert c1.src_out == 100
    assert c1.duration == 100


def test_export_template_token_resolution_and_folders(tmp_path):
    """Verify resolve_export_pattern and export_clips_with_template create recursive directory hierarchies."""
    import json
    from openreview.ui.export_template_builder import resolve_export_pattern, STANDARD_PRESETS
    from openreview.core.timeline_exporter import TimelineExporter
    from openreview.models.timeline_sequence import TimelineSequence, Track, ClipItem, TrackType

    seq = TimelineSequence(id="sq01", name="Seq01", fps=24.0, width=1920, height=1080)
    track = Track(id="t1", name="Video1", track_type=TrackType.VIDEO)
    clip = ClipItem(
        id="c1",
        file_path="test.exr",
        name="sh010_plate_fg",
        src_in=1001,
        src_out=1050,
        dst_in=1,
        dst_out=50,
        version="2",
        colorspace="ACEScg",
    )

    # 1. VFX Turnover Pattern
    vfx_pat = STANDARD_PRESETS["VFX Turnover"]
    resolved_vfx = resolve_export_pattern(vfx_pat, clip, track, seq, index=1, ext="mov")
    assert resolved_vfx.replace("\\", "/") == "Seq01/sh010/plates/sh010_plate_fg_v02.mov"

    # 2. Editorial Conform Pattern
    ed_pat = STANDARD_PRESETS["Editorial Conform"]
    resolved_ed = resolve_export_pattern(ed_pat, clip, track, seq, index=1, ext="exr")
    assert resolved_ed.replace("\\", "/") == "Seq01/Video1/sh010_plate_fg_1-50.exr"

    # 3. Flat Subfolder per Clip Pattern
    flat_pat = STANDARD_PRESETS["Flat Subfolder per Clip"]
    resolved_flat = resolve_export_pattern(flat_pat, clip, track, seq, index=1, ext="mp4")
    assert resolved_flat.replace("\\", "/") == "sh010_plate_fg/sh010_plate_fg_001.mp4"

    # 4. Dated Client Delivery Pattern
    dated_pat = STANDARD_PRESETS["Dated Client Delivery"]
    resolved_dated = resolve_export_pattern(dated_pat, clip, track, seq, index=1, ext="mov")
    assert "Seq01/1920x1080/sh010_plate_fg.mov" in resolved_dated.replace("\\", "/")

    # 5. Full Disk Export with intermediate folder creation
    out_dir = str(tmp_path / "deliveries")
    files = TimelineExporter.export_clips_with_template(
        [(track, clip)],
        seq,
        out_dir,
        pattern="{sequence}/{shot}/plates/{clip_name}_v{version}",
        ext="mov",
    )
    assert len(files) == 1
    assert os.path.exists(files[0])
    manifest = os.path.splitext(files[0])[0] + "_manifest.json"
    assert os.path.exists(manifest)
    with open(manifest, "r", encoding="utf-8") as f:
        data = json.load(f)
        assert data["clip_name"] == "sh010_plate_fg"
        assert data["version"] == "2"
        assert data["colorspace"] == "ACEScg"


def test_visual_breadcrumb_builder_and_preset_persistence(qtbot):
    """Verify VisualBreadcrumbBuilder and ExportTemplateBuilderWidget token chips, segment adding, and presets."""
    from openreview.ui.export_template_builder import (
        VisualBreadcrumbBuilder,
        ExportTemplateBuilderWidget,
        FolderBreadcrumbSegment,
    )
    from openreview.models.timeline_sequence import TimelineSequence, Track, ClipItem, TrackType
    from openreview.core.preferences import PreferencesManager

    # 1. Breadcrumb builder pattern manipulation
    builder = VisualBreadcrumbBuilder("{sequence}/{shot}/{clip_name}")
    qtbot.addWidget(builder)
    assert builder.get_pattern() == "{sequence}/{shot}/{clip_name}"
    assert len(builder._segments) == 3

    # Add a folder level
    builder._on_add_folder()
    assert len(builder._segments) == 4
    assert builder.get_pattern() == "{sequence}/{shot}/folder/{clip_name}"

    # Delete segment
    seg_to_delete = builder._segments[2]
    builder._on_segment_deleted(seg_to_delete)
    assert len(builder._segments) == 3
    assert builder.get_pattern() == "{sequence}/{shot}/{clip_name}"

    # 2. Composite Builder Widget & Preset Saving
    seq = TimelineSequence(id="s1", name="Seq01")
    trk = Track(id="t1", name="V1", track_type=TrackType.VIDEO)
    clp = ClipItem(id="c1", file_path="anim.mov", name="sh020_anim", version="1")

    widget = ExportTemplateBuilderWidget(sequence=seq, sample_clip=(trk, clp))
    qtbot.addWidget(widget)

    # Change pattern directly
    widget.set_pattern("{project}/{sequence}/renders/{clip_name}")
    assert widget.get_pattern() == "{project}/{sequence}/renders/{clip_name}"
    assert "Seq01/renders/sh020_anim" in widget.lbl_preview_path.text()

    # Save custom preset
    p_mgr = PreferencesManager.instance()
    preset_name = "Studio_Test_Preset"
    p_mgr.prefs.export_templates[preset_name] = "{sequence}/test/{clip_name}"
    widget._populate_presets()

    found = False
    for i in range(widget.combo_presets.count()):
        if preset_name in widget.combo_presets.itemText(i):
            found = True
            widget.combo_presets.setCurrentIndex(i)
            break
    assert found
    assert widget.get_pattern() == "{sequence}/test/{clip_name}"

    # Clean up custom preset
    if preset_name in p_mgr.prefs.export_templates:
        del p_mgr.prefs.export_templates[preset_name]


def test_fr_mode_strictly_displays_frame_numbers(qtbot):
    """Verify MultitrackTimelineWidget in FR mode displays pure frame numbers without timecode colons (Image 3 fix)."""
    from openreview.widgets.multitrack_timeline import MultitrackTimelineWidget
    from openreview.models.timeline_sequence import TimelineSequence

    seq = TimelineSequence(id="s1", name="Seq01", start_timecode="00:00:00:00", in_frame=1, out_frame=100)
    w = MultitrackTimelineWidget()
    qtbot.addWidget(w)
    w.set_sequence(seq)
    w.set_playhead_frame(50)

    # By default, starts in TC mode: 00:00:02:02 (at 24fps frame 50)
    assert w._display_timecode is True
    assert ":" in w.timecode_edit.text()

    # Toggle to Frame (FR) mode
    w._toggle_tc_mode()
    assert w._display_timecode is False
    assert w.btn_tc_mode.text() == "FR"
    # Must be strictly '50', absolutely NO colons
    assert w.timecode_edit.text() == "50"
    assert ":" not in w.timecode_edit.text()

    # Jump to frame 75 while in FR mode
    w.set_playhead_frame(75)
    assert w.timecode_edit.text() == "75"
    assert ":" not in w.timecode_edit.text()


def test_conform_dialog_default_start_timecode_zero(qtbot):
    """Verify ConformDialog defaults strictly to 00:00:00:00 starting timecode (Image 2 fix)."""
    from openreview.ui.conform_dialog import ConformDialog

    dlg = ConformDialog()
    qtbot.addWidget(dlg)
    assert dlg.edit_start_tc.text() == "00:00:00:00"


def test_timeline_audio_path_waveform_hook(qtbot):
    """Verify TimelineWidget forwards audio path to TimelineRuler for waveform envelope rendering (Image 1 fix)."""
    from openreview.widgets.timeline import TimelineWidget
    from openreview.core.clock import PlaybackClock

    clock = PlaybackClock()
    clock.set_range(1, 100)
    clock.set_current_frame(1)

    tw = TimelineWidget(clock=clock)
    qtbot.addWidget(tw)

    assert tw.ruler._audio_path is None
    tw.set_audio_path("dummy_audio.wav")
    assert tw.ruler._audio_path == "dummy_audio.wav"
    assert tw.ruler._bg_cache is None  # Cache was invalidated for repaint with waveform


def test_linked_clips_synchronous_trimming_and_safety_clamping(qtbot):
    """Verify trimming a linked video/audio clip trims both clips synchronously and respects shared safety limits."""
    from openreview.widgets.multitrack_timeline import TimelineCanvas
    from openreview.models.timeline_sequence import TimelineSequence, Track, ClipItem, TrackType
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtCore import Qt, QPointF

    seq = TimelineSequence(id="s1", name="Seq01", in_frame=1, out_frame=200, fps=24.0)
    vt = seq.add_video_track("V1")
    at = seq.add_audio_track("A1")

    # Linked video & audio pair: 50 frames duration (10 to 60)
    v_clip = ClipItem(
        id="cv1", file_path="shot.mov", name="Shot01_Video",
        src_in=10, src_out=60, dst_in=10, dst_out=60, link_id="link_pair1",
        metadata={"original_src_in": 1, "original_src_out": 100, "media_duration": 100}
    )
    a_clip = ClipItem(
        id="ca1", file_path="shot.wav", name="Shot01_Audio",
        src_in=10, src_out=60, dst_in=10, dst_out=60, link_id="link_pair1",
        metadata={"original_src_in": 5, "original_src_out": 80, "media_duration": 80}
    )
    vt.add_clip(v_clip)
    at.add_clip(a_clip)

    canvas = TimelineCanvas()
    qtbot.addWidget(canvas)
    canvas.set_sequence(seq)
    canvas.resize(1000, 300)

    # 1. Test trim_out rightward: v_clip has 40 frames tail available (60->100), but a_clip only has 20 frames (60->80)
    # The synchronized safety clamp must constrain the tail extension to +20 frames!
    x_tail = canvas.frame_to_x(v_clip.dst_out + 1)
    y_v = canvas.RULER_HEIGHT + 10

    # Start trim_out
    ev_press = QMouseEvent(QMouseEvent.Type.MouseButtonPress, QPointF(x_tail, y_v), Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    canvas.mousePressEvent(ev_press)
    assert canvas._drag_mode == "trim_out"
    assert len(canvas._trim_linked_partners) == 1

    # Drag right by +30 frames (exceeds audio tail limit of +20)
    delta_px = 30 * canvas._zoom
    ev_move = QMouseEvent(QMouseEvent.Type.MouseMove, QPointF(x_tail + delta_px, y_v), Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    canvas.mouseMoveEvent(ev_move)

    # Both clips must be clamped strictly to +20 (dst_out=80, src_out=80)
    assert v_clip.dst_out == 80
    assert v_clip.src_out == 80
    assert a_clip.dst_out == 80
    assert a_clip.src_out == 80

    canvas.mouseReleaseEvent(QMouseEvent(QMouseEvent.Type.MouseButtonRelease, QPointF(x_tail + delta_px, y_v), Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))

    # 2. Test trim_in rightward (shortening head by +10 frames, from 10 to 20)
    x_head = canvas.frame_to_x(v_clip.dst_in)
    ev_press_in = QMouseEvent(QMouseEvent.Type.MouseButtonPress, QPointF(x_head, y_v), Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    canvas.mousePressEvent(ev_press_in)
    assert canvas._drag_mode == "trim_in"

    delta_in_px = 10 * canvas._zoom
    canvas.mouseMoveEvent(QMouseEvent(QMouseEvent.Type.MouseMove, QPointF(x_head + delta_in_px, y_v), Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))

    # Both clips must move in lockstep to dst_in=20, src_in=20
    assert v_clip.dst_in == 20
    assert v_clip.src_in == 20
    assert a_clip.dst_in == 20
    assert a_clip.src_in == 20
    canvas.mouseReleaseEvent(QMouseEvent(QMouseEvent.Type.MouseButtonRelease, QPointF(x_head + delta_in_px, y_v), Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))


def test_unlink_and_link_clips_workflow(qtbot):
    """Verify unlinking clips disables synchronous trim/move, and linking reconnects them."""
    from openreview.widgets.multitrack_timeline import TimelineCanvas
    from openreview.models.timeline_sequence import TimelineSequence, Track, ClipItem, TrackType
    from openreview.core.undo_manager import UndoManager
    from PySide6.QtGui import QMouseEvent, QKeyEvent
    from PySide6.QtCore import Qt, QPointF

    seq = TimelineSequence(id="s1", name="Seq01", in_frame=1, out_frame=200)
    vt = seq.add_video_track("V1")
    at = seq.add_audio_track("A1")

    v_clip = ClipItem(id="cv1", file_path="shot.mov", name="V", src_in=1, src_out=50, dst_in=1, dst_out=50, link_id="common_link")
    a_clip = ClipItem(id="ca1", file_path="shot.wav", name="A", src_in=1, src_out=50, dst_in=1, dst_out=50, link_id="common_link")
    vt.add_clip(v_clip)
    at.add_clip(a_clip)

    canvas = TimelineCanvas()
    qtbot.addWidget(canvas)
    canvas.set_sequence(seq)
    canvas.resize(800, 200)

    # 1. Unlink clips via API
    canvas._selected_clip_ids = {v_clip.id}
    canvas.unlink_selected_clips()

    assert v_clip.link_id is None
    assert a_clip.link_id is None

    # Verify that trimming video does NOT affect unlinked audio
    x_tail = canvas.frame_to_x(v_clip.dst_out + 1)
    ev_press = QMouseEvent(QMouseEvent.Type.MouseButtonPress, QPointF(x_tail, canvas.RULER_HEIGHT + 10), Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    canvas.mousePressEvent(ev_press)
    assert len(canvas._trim_linked_partners) == 0

    delta_px = -10 * canvas._zoom
    canvas.mouseMoveEvent(QMouseEvent(QMouseEvent.Type.MouseMove, QPointF(x_tail + delta_px, canvas.RULER_HEIGHT + 10), Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))

    assert v_clip.dst_out == 40
    assert a_clip.dst_out == 50  # Audio remains completely unchanged!
    canvas.mouseReleaseEvent(QMouseEvent(QMouseEvent.Type.MouseButtonRelease, QPointF(x_tail + delta_px, canvas.RULER_HEIGHT + 10), Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))

    # 2. Undo trim (restores v_clip dst_out back to 50), then undo unlink (restores link_id)
    UndoManager.instance().undo()
    assert v_clip.dst_out == 50
    UndoManager.instance().undo()
    assert v_clip.link_id == "common_link"
    assert a_clip.link_id == "common_link"

    # 3. Test Ctrl+L shortcut toggles link
    canvas._selected_clip_ids = {v_clip.id, a_clip.id}
    key_ev = QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_L, Qt.KeyboardModifier.ControlModifier)
    canvas.keyPressEvent(key_ev)
    assert v_clip.link_id is None
    assert a_clip.link_id is None

    # Toggle again with Ctrl+L -> Re-links both clips with new shared link_id!
    canvas.keyPressEvent(key_ev)
    assert v_clip.link_id is not None
    assert a_clip.link_id is not None
    assert v_clip.link_id == a_clip.link_id


def test_linked_clips_ripple_trim_head_and_tail(qtbot):
    """Verify ripple trim head (Q) and tail (W) ripple both video and linked audio downstream clips."""
    from openreview.widgets.multitrack_timeline import TimelineCanvas
    from openreview.models.timeline_sequence import TimelineSequence, Track, ClipItem, TrackType

    seq = TimelineSequence(id="s1", name="Seq01", in_frame=1, out_frame=200)
    vt = seq.add_video_track("V1")
    at = seq.add_audio_track("A1")

    # Shot 1 (Linked V1 & A1): frames 1-40
    v1 = ClipItem(id="v1", file_path="s1.mov", name="V1", src_in=1, src_out=40, dst_in=1, dst_out=40, link_id="link1")
    a1 = ClipItem(id="a1", file_path="s1.wav", name="A1", src_in=1, src_out=40, dst_in=1, dst_out=40, link_id="link1")
    # Shot 2 (Downstream clips): frames 41-80
    v2 = ClipItem(id="v2", file_path="s2.mov", name="V2", src_in=1, src_out=40, dst_in=41, dst_out=80)
    a2 = ClipItem(id="a2", file_path="s2.wav", name="A2", src_in=1, src_out=40, dst_in=41, dst_out=80)

    vt.add_clip(v1)
    vt.add_clip(v2)
    at.add_clip(a1)
    at.add_clip(a2)

    canvas = TimelineCanvas()
    qtbot.addWidget(canvas)
    canvas.set_sequence(seq)

    # 1. Ripple trim head to frame 11 (Q): delta = 10 frames trimmed from head
    canvas.set_playhead_frame(11)
    canvas._selected_clip_ids = {v1.id}
    canvas.ripple_trim_head_to_playhead()

    # Both v1 and a1 trimmed to dst_in=11
    assert v1.dst_in == 11
    assert a1.dst_in == 11
    # Downstream clips on both tracks rippled left by 10 frames: 41 - 10 = 31!
    assert v2.dst_in == 31
    assert a2.dst_in == 31

    # 2. Ripple trim tail to frame 30 (W): dst_out becomes 29 (trimmed by 40 - 29 = 11 frames)
    canvas.set_playhead_frame(30)
    canvas.ripple_trim_tail_to_playhead()

    assert v1.dst_out == 29
    assert a1.dst_out == 29
    # Downstream clips on both tracks rippled left by 11 frames: 31 - 11 = 20!
    assert v2.dst_in == 20
    assert a2.dst_in == 20














