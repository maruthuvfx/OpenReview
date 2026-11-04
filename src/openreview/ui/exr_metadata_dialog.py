"""
EXR Metadata & Header Details Dialog.
Provides comprehensive breakdown of OpenEXR header attributes, data/display windows,
compression, and embedded channel specifications via an interactive attribute tree.
"""
from __future__ import annotations

from typing import Optional
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTabWidget,
    QWidget,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QFormLayout,
    QLineEdit,
)

from openreview.core.exr_contact_sheet import extract_exr_detailed_metadata
from openreview.resources.svg_icons import get_svg_icon
from openreview.widgets.metadata_tree_widget import MetadataTreeWidget


class EXRMetadataDialog(QDialog):
    """Modal inspection dialog for viewing detailed OpenEXR file headers and channel specs."""

    def __init__(self, exr_path: str, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle(f"EXR Header Details - {exr_path.split('/')[-1].split(chr(92))[-1]}")
        self.resize(780, 560)
        self._exr_path = exr_path
        self._meta = extract_exr_detailed_metadata(exr_path)
        self._setup_ui()

    def _setup_ui(self) -> None:
        self.setStyleSheet("""
            QDialog {
                background-color: #1a1a1a;
                color: #e0e0e0;
            }
            QTabWidget::pane {
                border: 1px solid #333333;
                background-color: #161616;
            }
            QTabBar::tab {
                background-color: #141414;
                color: #888888;
                padding: 6px 14px;
                border: 1px solid #2a2a2a;
                font-size: 11px;
            }
            QTabBar::tab:selected {
                background-color: #242424;
                color: #e5a823;
                font-weight: bold;
                border-bottom: 2px solid #e5a823;
            }
            QTableWidget {
                background-color: #121212;
                border: 1px solid #2c2c2c;
                color: #dddddd;
                gridline-color: #262626;
                font-size: 11px;
            }
            QHeaderView::section {
                background-color: #1f1f1f;
                color: #e5a823;
                font-weight: bold;
                padding: 4px;
                border: 1px solid #2c2c2c;
                font-size: 11px;
            }
            QLineEdit {
                background-color: #121212;
                border: 1px solid #2f2f2f;
                color: #e0e0e0;
                padding: 3px 6px;
                font-size: 11px;
            }
            QPushButton {
                background-color: #282828;
                border: 1px solid #3d3d3d;
                color: #e0e0e0;
                padding: 6px 16px;
                border-radius: 3px;
                font-size: 11px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #383838;
                border-color: #e5a823;
            }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        # Header Title
        title_box = QHBoxLayout()
        icon_lbl = QLabel(self)
        icon_lbl.setPixmap(get_svg_icon("reveal", color_normal="#e5a823", size=18).pixmap(18, 18))
        title_box.addWidget(icon_lbl)

        title_lbl = QLabel(f"OpenEXR Header Attributes: {self._meta.get('filename', '')}", self)
        title_lbl.setStyleSheet("font-size: 13px; font-weight: bold; color: #e5a823;")
        title_box.addWidget(title_lbl, stretch=1)
        layout.addLayout(title_box)

        # Tabs
        tabs = QTabWidget(self)

        # Tab 1: Interactive Attribute Tree
        self.tree_widget = MetadataTreeWidget(self)
        self.tree_widget.set_media(self._exr_path)
        tabs.addTab(self.tree_widget, "Interactive Attribute Tree")

        # Tab 2: Summary & Windows
        tab_summary = QWidget()
        form = QFormLayout(tab_summary)
        form.setContentsMargins(12, 12, 12, 12)
        form.setSpacing(8)

        def _add_row(label: str, val: str):
            edit = QLineEdit(val, tab_summary)
            edit.setReadOnly(True)
            lbl = QLabel(label, tab_summary)
            lbl.setStyleSheet("font-weight: bold; color: #cccccc;")
            form.addRow(lbl, edit)

        _add_row("File Path:", self._meta.get("path", ""))

        dw = self._meta.get("data_window")
        dw_str = f"min=({dw[0]}, {dw[1]}) max=({dw[2]}, {dw[3]}) | {dw[2]-dw[0]+1} x {dw[3]-dw[1]+1}" if dw else "N/A"
        _add_row("Data Window:", dw_str)

        disp = self._meta.get("display_window")
        disp_str = f"min=({disp[0]}, {disp[1]}) max=({disp[2]}, {disp[3]}) | {disp[2]-disp[0]+1} x {disp[3]-disp[1]+1}" if disp else "N/A"
        _add_row("Display Window:", disp_str)

        _add_row("Compression:", self._meta.get("compression", "Unknown"))
        _add_row("Pixel Aspect Ratio:", str(self._meta.get("pixel_aspect", 1.0)))
        _add_row("Line Order:", self._meta.get("line_order", "IncreasingY"))
        _add_row("Total Channels:", str(len(self._meta.get("channels", []))))
        _add_row("Total AOVs / Layers:", str(len(self._meta.get("layers", []))))

        tabs.addTab(tab_summary, "Summary")

        # Tab 3: Channels Table
        tab_channels = QWidget()
        chan_layout = QVBoxLayout(tab_channels)
        chan_layout.setContentsMargins(4, 4, 4, 4)

        tbl_chans = QTableWidget(tab_channels)
        channels = self._meta.get("channels", [])
        tbl_chans.setRowCount(len(channels))
        tbl_chans.setColumnCount(3)
        tbl_chans.setHorizontalHeaderLabels(["Channel Name", "Data Type", "Sampling"])
        tbl_chans.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        tbl_chans.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        tbl_chans.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)

        for r, ch in enumerate(channels):
            tbl_chans.setItem(r, 0, QTableWidgetItem(ch["name"]))
            tbl_chans.setItem(r, 1, QTableWidgetItem(ch["type"]))
            tbl_chans.setItem(r, 2, QTableWidgetItem(ch["sampling"]))

        chan_layout.addWidget(tbl_chans)
        tabs.addTab(tab_channels, f"Channels ({len(channels)})")

        # Tab 4: Custom Attributes Table
        tab_attrs = QWidget()
        attr_layout = QVBoxLayout(tab_attrs)
        attr_layout.setContentsMargins(4, 4, 4, 4)

        tbl_attrs = QTableWidget(tab_attrs)
        attrs = self._meta.get("attributes", {})
        tbl_attrs.setRowCount(len(attrs))
        tbl_attrs.setColumnCount(2)
        tbl_attrs.setHorizontalHeaderLabels(["Attribute Key", "Value"])
        tbl_attrs.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        tbl_attrs.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)

        for r, (k, v) in enumerate(sorted(attrs.items())):
            tbl_attrs.setItem(r, 0, QTableWidgetItem(str(k)))
            tbl_attrs.setItem(r, 1, QTableWidgetItem(str(v)))

        attr_layout.addWidget(tbl_attrs)
        tabs.addTab(tab_attrs, f"Raw Attributes ({len(attrs)})")

        layout.addWidget(tabs, stretch=1)

        # Bottom Button
        btn_box = QHBoxLayout()
        btn_close = QPushButton("Close", self)
        btn_close.clicked.connect(self.accept)
        btn_box.addStretch(1)
        btn_box.addWidget(btn_close)
        layout.addLayout(btn_box)
