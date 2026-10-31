import pytest
import numpy as np
from openreview.color.standard_colorspaces import (
    STANDARD_COLORSPACES,
    COLORSPACE_BY_ID,
    COLORSPACE_BY_NAME,
    apply_standard_to_linear,
    apply_linear_to_standard,
    srgb_to_linear,
    linear_to_srgb,
    rec709_to_linear,
    linear_to_rec709,
    cineon_to_linear,
    linear_to_cineon,
    gamma18_to_linear,
    linear_to_gamma18,
    gamma22_to_linear,
    linear_to_gamma22,
    panalog_to_linear,
    linear_to_panalog,
    redlog_to_linear,
    linear_to_redlog,
    viperlog_to_linear,
    linear_to_viperlog,
    redspace_to_linear,
    linear_to_redspace,
)
from openreview.core.preferences import UserPreferences, PreferencesManager
from openreview.color.ocio_manager import OCIOManager
from openreview.ui.preferences_dialog import PreferencesDialog
from openreview.widgets.top_bar import TopControlBar


def test_standard_colorspaces_catalog():
    """Verify all 10 requested standard colorspaces (+ Raw) exist in catalog."""
    expected_names = [
        "linear",
        "sRGB",
        "rec709",
        "Cineon",
        "Gamma 1.8",
        "Gamma 2.2",
        "Panalog",
        "REDLog",
        "ViperLog",
        "REDSpace",
        "Raw",
    ]
    catalog_names = [cs.name for cs in STANDARD_COLORSPACES]
    for name in expected_names:
        assert name in catalog_names, f"Expected {name} in standard colorspaces"

    assert len(STANDARD_COLORSPACES) == 11
    assert COLORSPACE_BY_ID[0].name == "linear"
    assert COLORSPACE_BY_ID[1].name == "sRGB"
    assert COLORSPACE_BY_ID[2].name == "rec709"
    assert COLORSPACE_BY_ID[3].name == "Cineon"
    assert COLORSPACE_BY_ID[4].name == "Gamma 1.8"
    assert COLORSPACE_BY_ID[5].name == "Gamma 2.2"
    assert COLORSPACE_BY_ID[6].name == "Panalog"
    assert COLORSPACE_BY_ID[7].name == "REDLog"
    assert COLORSPACE_BY_ID[8].name == "ViperLog"
    assert COLORSPACE_BY_ID[9].name == "REDSpace"
    assert COLORSPACE_BY_ID[10].name == "Raw"


def test_standard_transforms_roundtrip():
    """Verify forward and inverse mathematical curves for each colorspace."""
    test_vals = np.array([0.05, 0.18, 0.50, 0.85, 1.0], dtype=np.float32)

    # sRGB
    srgb_lin = srgb_to_linear(test_vals)
    srgb_back = linear_to_srgb(srgb_lin)
    assert np.allclose(test_vals, srgb_back, atol=1e-4)

    # rec709
    rec_lin = rec709_to_linear(test_vals)
    rec_back = linear_to_rec709(rec_lin)
    assert np.allclose(test_vals, rec_back, atol=1e-4)

    # Cineon (from valid cineon code values > black point 0.095)
    cineon_vals = np.array([0.15, 0.25, 0.50, 0.685, 0.90], dtype=np.float32)
    cineon_lin = cineon_to_linear(cineon_vals)
    cineon_back = linear_to_cineon(cineon_lin)
    assert np.allclose(cineon_vals, cineon_back, atol=1e-3)

    # Cineon from scene-linear values
    lin_test = np.array([0.02, 0.18, 0.50, 1.0], dtype=np.float32)
    assert np.allclose(lin_test, cineon_to_linear(linear_to_cineon(lin_test)), atol=1e-3)

    # Gamma 1.8
    g18_lin = gamma18_to_linear(test_vals)
    g18_back = linear_to_gamma18(g18_lin)
    assert np.allclose(test_vals, g18_back, atol=1e-4)

    # Gamma 2.2
    g22_lin = gamma22_to_linear(test_vals)
    g22_back = linear_to_gamma22(g22_lin)
    assert np.allclose(test_vals, g22_back, atol=1e-4)

    # Panalog
    pan_lin = panalog_to_linear(test_vals)
    pan_back = linear_to_panalog(pan_lin)
    assert np.allclose(test_vals, pan_back, atol=1e-3)

    # REDLog
    red_lin = redlog_to_linear(test_vals)
    red_back = linear_to_redlog(red_lin)
    assert np.allclose(test_vals, red_back, atol=1e-3)

    # ViperLog
    vip_lin = viperlog_to_linear(test_vals)
    vip_back = linear_to_viperlog(vip_lin)
    assert np.allclose(test_vals, vip_back, atol=1e-3)

    # REDSpace
    rgb_test = np.array([[0.2, 0.4, 0.6], [0.8, 0.1, 0.3]], dtype=np.float32)
    redsp_lin = redspace_to_linear(rgb_test)
    redsp_back = linear_to_redspace(redsp_lin)
    assert np.allclose(rgb_test, redsp_back, atol=1e-4)


def test_standard_array_batch_processing():
    """Verify multidimensional image arrays are processed correctly."""
    img = np.random.uniform(0.0, 1.0, (16, 16, 3)).astype(np.float32)
    for space_id in range(11):
        lin = apply_standard_to_linear(img, space_id)
        assert lin.shape == img.shape
        disp = apply_linear_to_standard(lin, space_id)
        assert disp.shape == img.shape


def test_preferences_defaults_and_mode(tmp_path):
    """Verify default preference is standard colorspace with sRGB display view."""
    prefs = UserPreferences()
    assert prefs.color_management_mode == "standard"
    assert prefs.standard_view_transform == "sRGB"
    assert prefs.standard_input_space == "linear"

    mgr = PreferencesManager(config_dir=tmp_path)
    assert mgr.prefs.color_management_mode == "standard"
    assert mgr.prefs.standard_view_transform == "sRGB"

    mgr.prefs.color_management_mode = "ocio"
    mgr.prefs.standard_view_transform = "rec709"
    mgr.save()

    mgr2 = PreferencesManager(config_dir=tmp_path)
    assert mgr2.prefs.color_management_mode == "ocio"
    assert mgr2.prefs.standard_view_transform == "rec709"


def test_ocio_manager_standard_processing():
    """Verify OCIOManager CPU pixel processor handles standard mode correctly."""
    ocio = OCIOManager()
    assert ocio.color_management_mode == "standard"

    # Default: input=linear (0), view=sRGB (1)
    ocio.standard_input_id = 0
    ocio.standard_view_id = 1
    r_out, g_out, b_out = ocio.process_rgb(0.5, 0.5, 0.5)
    expected_srgb = float(linear_to_srgb(0.5))
    assert abs(r_out - expected_srgb) < 0.01

    # Switch view to Cineon
    ocio.standard_view_id = 3
    r_out, g_out, b_out = ocio.process_rgb(0.18, 0.18, 0.18)
    expected_cineon = float(linear_to_cineon(0.18))
    assert abs(r_out - expected_cineon) < 0.01


def test_preferences_dialog_color_management(qapp, tmp_path):
    """Verify PreferencesDialog Color Management tab allows toggling modes."""
    mgr = PreferencesManager(config_dir=tmp_path)
    ocio = OCIOManager()
    dlg = PreferencesDialog(mgr, ocio_manager=ocio)

    # Check Color Management tab exists
    assert dlg.combo_cm_mode.currentData() == "standard"
    assert not dlg.grp_std_cm.isHidden()
    assert dlg.grp_ocio_cm.isHidden()

    # Switch to OCIO mode
    dlg.combo_cm_mode.setCurrentIndex(1)  # ocio
    assert dlg.grp_std_cm.isHidden()
    assert not dlg.grp_ocio_cm.isHidden()

    # Change standard defaults
    idx_rec = dlg.combo_std_default_view.findData("rec709")
    dlg.combo_std_default_view.setCurrentIndex(idx_rec)

    dlg._save_and_close()
    assert mgr.prefs.color_management_mode == "ocio"
    assert mgr.prefs.standard_view_transform == "rec709"


def test_top_bar_standard_and_ocio_switching(qapp):
    """Verify TopControlBar populates standard colorspaces and switches modes."""
    ocio = OCIOManager()
    bar = TopControlBar(ocio)

    # Default is standard spaces
    assert bar.input_combo.count() == 11
    assert bar.view_combo.count() == 11
    # Check default view is sRGB (item with data=1)
    assert bar.view_combo.currentData() == 1

    # Verify all items in view_combo
    view_labels = [bar.view_combo.itemText(i) for i in range(bar.view_combo.count())]
    assert "linear" in view_labels
    assert "sRGB" in view_labels
    assert "rec709" in view_labels
    assert "Cineon" in view_labels
    assert "Gamma 1.8" in view_labels
    assert "Gamma 2.2" in view_labels
    assert "Panalog" in view_labels
    assert "REDLog" in view_labels
    assert "ViperLog" in view_labels
    assert "REDSpace" in view_labels
    assert "Raw / Unmanaged" in view_labels

    # Switch to custom OCIO config
    bar.update_ocio_spaces(["ACEScg", "Linear sRGB"], ["ACES 1.0 - SDR Video", "Raw"])
    assert bar.input_combo.count() == 2
    assert bar.view_combo.count() == 2

    # Switch back to standard mode
    bar.set_color_management_mode("standard", default_view="rec709")
    assert bar.input_combo.count() == 11
    assert bar.view_combo.count() == 11
    assert bar.view_combo.currentData() == 2  # rec709
