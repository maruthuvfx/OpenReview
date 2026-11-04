"""
Unit and Integration Tests for Interactive Metadata & ExifTool / OpenEXR Attribute Tree.
"""
import os
import tempfile
import pytest
import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from openreview.core.metadata_tree import (
    MetadataNode,
    find_exiftool_executable,
    format_imath_value,
    extract_exr_attribute_nodes,
    extract_pillow_exif_nodes,
    build_unified_metadata_tree,
    HAS_OPENEXR,
)
from openreview.widgets.metadata_tree_widget import MetadataTreeWidget, MetadataTreeItem
from openreview.ui.exr_metadata_dialog import EXRMetadataDialog

try:
    import OpenEXR
    import Imath
except ImportError:
    OpenEXR = None
    Imath = None


@pytest.fixture
def sample_exr_file(tmp_path):
    """Generate a valid test OpenEXR file with rich production metadata."""
    if not HAS_OPENEXR or OpenEXR is None or Imath is None:
        pytest.skip("OpenEXR or Imath not available")

    exr_path = str(tmp_path / "shot_010_comp_v001.exr")
    w, h = 64, 48

    header = OpenEXR.Header(w, h)
    header["focalLength"] = 35.0
    header["aperture"] = 2.8
    header["isoSpeedRatings"] = 800
    header["timeCode"] = Imath.TimeCode(10, 20, 30, 12)
    header["screenWindowCenter"] = Imath.V2f(0.5, 0.5)
    header["pixelAspectRatio"] = 1.0

    # Write RGB float32 channels
    raw_data = np.zeros((h, w), dtype=np.float32).tobytes()
    out = OpenEXR.OutputFile(exr_path, header)
    out.writePixels({"R": raw_data, "G": raw_data, "B": raw_data})
    out.close()

    yield exr_path

    if os.path.exists(exr_path):
        try:
            os.remove(exr_path)
        except Exception:
            pass


class TestMetadataTreeCore:
    def test_metadata_node_hierarchy(self):
        root = MetadataNode(key="Root", type_name="category", category="Test")
        child1 = MetadataNode(key="FocalLength", value=50.0, type_name="float", category="Test")
        child2 = MetadataNode(key="Aperture", value=1.8, type_name="float", category="Test")
        root.add_child(child1)
        root.add_child(child2)

        assert len(root.children) == 2
        d = root.to_dict()
        assert d["key"] == "Root"
        assert len(d["children"]) == 2
        assert d["children"][0]["key"] == "FocalLength"
        assert d["children"][0]["value"] == 50.0

    def test_find_exiftool_executable(self, tmp_path):
        # Test custom valid path
        fake_bin = tmp_path / "exiftool.exe"
        fake_bin.write_text("dummy binary", encoding="utf-8")
        found = find_exiftool_executable(str(fake_bin))
        assert found == str(fake_bin)

        # Test non-existent path falls through safely
        res = find_exiftool_executable(r"C:\non_existent_path_xyz\exiftool.exe")
        # Should either return system exiftool if installed or None
        assert res is None or os.path.isfile(res)

    def test_format_imath_value(self):
        if not HAS_OPENEXR or Imath is None:
            pytest.skip("Imath not available")

        # Timecode
        tc = Imath.TimeCode(1, 2, 3, 4)
        val_str, type_str = format_imath_value(tc)
        assert "01:02:03:04" in val_str
        assert type_str == "timecode"

        # Box2i
        box = Imath.Box2i(Imath.V2i(0, 0), Imath.V2i(1919, 1079))
        val_str, type_str = format_imath_value(box)
        assert "[1920 x 1080]" in val_str
        assert type_str == "box2i"

        # V2f
        v2 = Imath.V2f(0.5, 0.5)
        val_str, type_str = format_imath_value(v2)
        assert "(0.5000, 0.5000)" in val_str
        assert type_str == "v2f"

        # Primitives
        assert format_imath_value(100) == ("100", "int")
        assert format_imath_value(12.5) == ("12.5", "float")
        assert format_imath_value("ACEScg") == ("ACEScg", "string")

    def test_extract_exr_attribute_nodes(self, sample_exr_file):
        nodes = extract_exr_attribute_nodes(sample_exr_file)
        assert len(nodes) > 0

        keys = [n.key for n in nodes]
        assert "Header Specifications" in keys
        assert "Channels & AOVs" in keys
        assert "Camera & Lens Optics" in keys

        # Check camera node children
        cam_node = next(n for n in nodes if n.key == "Camera & Lens Optics")
        cam_keys = [c.key for c in cam_node.children]
        assert "focalLength" in cam_keys
        assert "aperture" in cam_keys
        assert "timeCode" in cam_keys

    def test_build_unified_metadata_tree(self, sample_exr_file):
        context = {
            "width": 1920,
            "height": 1080,
            "fps": 24.0,
            "input_space_name": "ACEScg",
            "view_transform_name": "sRGB - Display",
            "current_frame": 10,
        }
        tree = build_unified_metadata_tree(sample_exr_file, context=context)
        assert len(tree) >= 3

        top_categories = [n.key for n in tree]
        assert "Key Production Attributes" in top_categories
        assert "File System & Container" in top_categories

        key_node = next(n for n in tree if n.key == "Key Production Attributes")
        sub_keys = [c.key for c in key_node.children]
        assert "Resolution" in sub_keys
        assert "Input Colorspace" in sub_keys


class TestMetadataTreeWidget:
    def test_tree_widget_population(self, qapp, sample_exr_file):
        widget = MetadataTreeWidget()
        widget.set_media(sample_exr_file, frame=10, context={"width": 1920, "height": 1080})

        assert widget.tree.topLevelItemCount() > 0
        # First category should be expanded by default
        assert widget.tree.topLevelItem(0).isExpanded()

    def test_tree_widget_search_filter(self, qapp, sample_exr_file):
        widget = MetadataTreeWidget()
        widget.set_media(sample_exr_file, frame=10, context={"width": 1920, "height": 1080})

        initial_count = widget.tree.topLevelItemCount()
        assert initial_count > 0

        # Filter by "focal"
        widget.search_edit.setText("focal")
        filtered_count = widget.tree.topLevelItemCount()
        assert filtered_count > 0

        # Filter by impossible string
        widget.search_edit.setText("xyz_impossible_query_999")
        assert widget.tree.topLevelItemCount() == 0

        # Clear filter
        widget.search_edit.clear()
        assert widget.tree.topLevelItemCount() == initial_count

    def test_tree_widget_category_filter(self, qapp, sample_exr_file):
        widget = MetadataTreeWidget()
        widget.set_media(sample_exr_file, frame=10, context={"width": 1920, "height": 1080})

        # Set category combo to Camera / Optics
        idx = widget.cat_combo.findText("Camera / Optics")
        if idx >= 0:
            widget.cat_combo.setCurrentIndex(idx)
            assert widget.tree.topLevelItemCount() == 1
            assert widget.tree.topLevelItem(0).text(0) == "Camera & Lens Optics"

    def test_tree_widget_copy_json(self, qapp, sample_exr_file):
        widget = MetadataTreeWidget()
        widget.set_media(sample_exr_file, frame=10, context={"width": 1920, "height": 1080})
        widget._on_copy_all_json()

        clipboard_text = QApplication.clipboard().text()
        assert "focalLength" in clipboard_text or "Resolution" in clipboard_text


class TestDialogAndMainWindowIntegration:
    def test_exr_metadata_dialog(self, qapp, sample_exr_file):
        dlg = EXRMetadataDialog(sample_exr_file)
        assert dlg.tree_widget is not None
        assert dlg.tree_widget.tree.topLevelItemCount() > 0

    def test_main_window_metadata_integration(self, qapp, sample_exr_file):
        from openreview.ui.main_window import MainWindow
        win = MainWindow()

        # HUD Metadata Card
        assert hasattr(win, "hud_metadata")
        assert hasattr(win, "metadata_tree_widget")
        assert win.hud_metadata in win._get_all_hud_cards()

        # Toggle Metadata Tree
        assert win.hud_metadata.isHidden()
        win._on_toggle_metadata_tree()
        assert not win.hud_metadata.isHidden()
        win._on_toggle_metadata_tree()
        assert win.hud_metadata.isHidden()
