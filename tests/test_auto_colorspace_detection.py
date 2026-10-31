import pytest
import os
from openreview.color.standard_colorspaces import (
    COLORSPACE_BY_ID,
    detect_media_input_colorspace,
    map_standard_to_ocio_space,
)
from openreview.core.preferences import UserPreferences, PreferencesManager
from openreview.color.ocio_manager import OCIOManager
from openreview.ui.preferences_dialog import PreferencesDialog


def test_detect_exr_and_hdr_linear():
    """EXR and HDR files must auto-detect as linear."""
    res_exr = detect_media_input_colorspace("shot_010.exr")
    assert res_exr.name == "linear"
    assert res_exr.id == 0

    res_hdr = detect_media_input_colorspace("/path/to/environment.hdr")
    assert res_hdr.name == "linear"
    assert res_hdr.id == 0


def test_detect_dpx_and_cineon():
    """DPX and CIN film scans must auto-detect as Cineon log."""
    res_dpx = detect_media_input_colorspace("plate.1001.dpx")
    assert res_dpx.name == "Cineon"
    assert res_dpx.id == 3

    res_cin = detect_media_input_colorspace("film_scan.cin")
    assert res_cin.name == "Cineon"
    assert res_cin.id == 3


def test_detect_red_camera_metadata():
    """RED raw or REDLog/REDSpace camera metadata tags."""
    # .r3d extension
    res_r3d = detect_media_input_colorspace("A001_C001.r3d")
    assert res_r3d.name == "REDLog"
    assert res_r3d.id == 7

    # Video with REDLog tag
    meta_redlog = {
        "all_metadata_tags": {"camera:log": "REDLogFilm", "make": "RED Digital Cinema"}
    }
    res_tag = detect_media_input_colorspace("clip.mov", meta_redlog)
    assert res_tag.name == "REDLog"

    # Video with REDSpace tag
    meta_redspace = {
        "all_metadata_tags": {"colorspace": "REDSpace"}
    }
    res_space = detect_media_input_colorspace("clip.mov", meta_redspace)
    assert res_space.name == "REDSpace"


def test_detect_panalog_and_viperlog():
    """Panavision Genesis and Viper FilmStream camera metadata tags."""
    meta_pana = {
        "all_metadata_tags": {"encoder": "Panavision Genesis Panalog"}
    }
    res_pana = detect_media_input_colorspace("shot.mov", meta_pana)
    assert res_pana.name == "Panalog"
    assert res_pana.id == 6

    meta_viper = {
        "all_metadata_tags": {"camera_profile": "ViperLog FilmStream"}
    }
    res_viper = detect_media_input_colorspace("shot.mov", meta_viper)
    assert res_viper.name == "ViperLog"
    assert res_viper.id == 8


def test_detect_video_explicit_transfer():
    """Video container with explicit color_transfer tags."""
    # bt709
    meta_709 = {"color_transfer": "bt709", "color_space": "bt709"}
    res_709 = detect_media_input_colorspace("broadcast.mp4", meta_709)
    assert res_709.name == "rec709"
    assert res_709.id == 2

    # sRGB
    meta_srgb = {"color_transfer": "iec61966-2-1"}
    res_srgb = detect_media_input_colorspace("capture.mov", meta_srgb)
    assert res_srgb.name == "sRGB"
    assert res_srgb.id == 1

    # linear
    meta_lin = {"color_transfer": "linear"}
    res_lin = detect_media_input_colorspace("render.mov", meta_lin)
    assert res_lin.name == "linear"
    assert res_lin.id == 0

    # Gamma 2.2
    meta_g22 = {"color_transfer": "gamma22"}
    res_g22 = detect_media_input_colorspace("movie.mp4", meta_g22)
    assert res_g22.name == "Gamma 2.2"
    assert res_g22.id == 5

    # Gamma 1.8
    meta_g18 = {"color_transfer": "gamma18"}
    res_g18 = detect_media_input_colorspace("movie.mov", meta_g18)
    assert res_g18.name == "Gamma 1.8"
    assert res_g18.id == 4


def test_detect_video_resolution_fallback():
    """Untagged video container defaults: HD/UHD -> rec709, SD -> sRGB."""
    # HD video 1920x1080 without transfer tags
    meta_hd = {"video_codec": "h264", "width": 1920, "height": 1080}
    res_hd = detect_media_input_colorspace("clip.mp4", meta_hd)
    assert res_hd.name == "rec709"
    assert res_hd.id == 2

    # 4K UHD video 3840x2160 without transfer tags
    meta_uhd = {"video_codec": "prores", "width": 3840, "height": 2160}
    res_uhd = detect_media_input_colorspace("dailies.mov", meta_uhd)
    assert res_uhd.name == "rec709"
    assert res_uhd.id == 2

    # SD video 640x480 without transfer tags
    meta_sd = {"video_codec": "h264", "width": 640, "height": 480}
    res_sd = detect_media_input_colorspace("web_video.mp4", meta_sd)
    assert res_sd.name == "sRGB"
    assert res_sd.id == 1


def test_detect_still_image_formats():
    """Standard still images default to sRGB."""
    for ext in (".png", ".jpg", ".jpeg", ".bmp", ".webp", ".tif", ".tiff"):
        res = detect_media_input_colorspace(f"texture{ext}")
        assert res.name == "sRGB"
        assert res.id == 1


def test_map_standard_to_ocio_space():
    """Verify intelligent mapping of standard spaces to active OCIO config."""
    ocio = OCIOManager()
    # Built-in config has ACEScg, sRGB, Rec.709, etc.
    # Linear -> ACEScg / scene_linear
    mapped_lin = map_standard_to_ocio_space(0, ocio)
    assert mapped_lin in ("ACEScg", "Linear sRGB", "scene_linear")

    # sRGB -> sRGB
    mapped_srgb = map_standard_to_ocio_space(1, ocio)
    assert "sRGB" in mapped_srgb

    # rec709 -> Rec.709
    mapped_709 = map_standard_to_ocio_space(2, ocio)
    assert "709" in mapped_709 or "sRGB" in mapped_709


def test_auto_detect_preference_toggle(qapp, tmp_path):
    """Verify auto_detect_input_colorspace preference defaults to True and persists."""
    prefs = UserPreferences()
    assert prefs.auto_detect_input_colorspace is True

    mgr = PreferencesManager(config_dir=tmp_path)
    assert mgr.prefs.auto_detect_input_colorspace is True

    dlg = PreferencesDialog(mgr)
    assert hasattr(dlg, "chk_auto_detect_input")
    assert dlg.chk_auto_detect_input.isChecked() is True

    # Disable auto-detect and save
    dlg.chk_auto_detect_input.setChecked(False)
    dlg._save_and_close()

    assert mgr.prefs.auto_detect_input_colorspace is False

    # Reload from disk
    mgr2 = PreferencesManager(config_dir=tmp_path)
    assert mgr2.prefs.auto_detect_input_colorspace is False
