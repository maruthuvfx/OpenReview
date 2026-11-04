"""
Image Info & Source Details Dialog.
Provides comprehensive VFX metadata readouts: resolution, color depth, pixel aspect ratio,
file paths, frame counts, timecode, codecs, bitrates, audio streams, and container attributes.
"""
from __future__ import annotations

import os
import json
import time
from typing import Optional, Dict, Any
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import (
    QDialog,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QGridLayout,
    QLabel,
    QPushButton,
    QGroupBox,
    QFrame,
    QScrollArea,
    QApplication,
    QLineEdit,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QMenu,
    QFileDialog,
    QSizePolicy,
)

from openreview.widgets.draggable_scroll_area import BlenderScrollArea


class MetaTableWidget(QTableWidget):
    """Custom table widget supporting keyboard copy (Ctrl+C)."""

    def keyPressEvent(self, event) -> None:
        if event.matches(QKeySequence.StandardKey.Copy):
            selected = self.selectedRanges()
            if selected:
                lines = []
                for rng in selected:
                    for r in range(rng.topRow(), rng.bottomRow() + 1):
                        if not self.isRowHidden(r):
                            k = self.item(r, 0).text() if self.item(r, 0) else ""
                            v = self.item(r, 1).text() if self.item(r, 1) else ""
                            lines.append(f"{k}: {v}")
                if lines:
                    QApplication.clipboard().setText("\n".join(lines))
                    event.accept()
                    return
        super().keyPressEvent(event)


class ImageInfoWidget(QWidget):
    """Studio Image Info and Source Media Details Widget."""

    tree_requested = Signal()

    def __init__(self, media_info: Optional[Dict[str, Any]] = None, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.info = media_info or {}
        self.setMinimumWidth(0)
        self.setMinimumHeight(180)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.filter_edit: Optional[QLineEdit] = None
        self.meta_table: Optional[MetaTableWidget] = None
        self.tag_grp: Optional[QGroupBox] = None

        self.setStyleSheet("""
            QWidget {
                background-color: transparent;
                color: #e0e0e0;
            }
            QGroupBox {
                border: 1px solid #333333;
                border-radius: 4px;
                margin-top: 14px;
                padding-top: 14px;
                padding-bottom: 8px;
                padding-left: 8px;
                padding-right: 8px;
                font-size: 11px;
                font-weight: bold;
                color: #e5a823;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                subcontrol-position: top left;
                left: 10px;
                padding: 0 4px;
            }
            QLabel#hdr {
                color: #888888;
                font-size: 11px;
                font-weight: bold;
                min-width: 80px;
            }
            QLabel#val {
                color: #e0e0e0;
                font-size: 11px;
                font-family: Consolas, monospace;
            }
            QPushButton#btn {
                background-color: #2c2c2c;
                color: #dddddd;
                border: 1px solid #444444;
                padding: 5px 14px;
                border-radius: 3px;
                font-size: 11px;
                font-weight: bold;
            }
            QPushButton#btn:hover {
                background-color: #e5a823;
                color: #111111;
            }
            QPushButton#btn_copy {
                background-color: #242424;
                color: #aaaaaa;
                border: 1px solid #3a3a3a;
                padding: 5px 12px;
                border-radius: 3px;
                font-size: 11px;
            }
            QPushButton#btn_copy:hover {
                background-color: #333333;
                color: #ffffff;
                border-color: #666666;
            }
            QLineEdit#meta_filter {
                background-color: #141414;
                color: #ffffff;
                border: 1px solid #3a3a3a;
                border-radius: 3px;
                padding: 4px 8px;
                font-size: 11px;
            }
            QLineEdit#meta_filter:focus {
                border-color: #e5a823;
            }
            QTableWidget#meta_table {
                background-color: #151515;
                alternate-background-color: #1b1b1b;
                color: #d0d0d0;
                border: 1px solid #2e2e2e;
                gridline-color: #242424;
                font-family: Consolas, monospace;
                font-size: 11px;
            }
            QTableWidget#meta_table::item {
                padding: 3px 6px;
            }
            QTableWidget#meta_table::item:selected {
                background-color: #383838;
                color: #ffffff;
            }
            QHeaderView::section {
                background-color: #222222;
                color: #aaaaaa;
                padding: 4px;
                border: 1px solid #2e2e2e;
                font-weight: bold;
                font-size: 10px;
            }
            QScrollArea {
                border: none;
                background-color: transparent;
            }
        """)

        # Main Layout created ONCE
        self._main_layout = QVBoxLayout(self)
        self._main_layout.setContentsMargins(10, 8, 10, 8)
        self._main_layout.setSpacing(8)

        # Scroll Area created ONCE
        self._scroll_area = BlenderScrollArea(self)
        self._scroll_area.setWidgetResizable(True)
        self._scroll_area.setFrameShape(QScrollArea.Shape.NoFrame)
        self._main_layout.addWidget(self._scroll_area, stretch=1)

        # Bottom Action Buttons Bar (Compact 2-row layout for dock panels)
        btn_layout = QVBoxLayout()
        btn_layout.setSpacing(4)
        btn_layout.setContentsMargins(0, 4, 0, 0)

        row1 = QHBoxLayout()
        row1.setSpacing(6)
        self.btn_copy = QPushButton("Copy All to Clipboard", self)
        self.btn_copy.setObjectName("btn_copy")
        self.btn_copy.clicked.connect(self._on_copy_all)
        row1.addWidget(self.btn_copy)

        self.btn_export = QPushButton("Export JSON", self)
        self.btn_export.setObjectName("btn_copy")
        self.btn_export.clicked.connect(self._on_export_json)
        row1.addWidget(self.btn_export)
        btn_layout.addLayout(row1)

        row2 = QHBoxLayout()
        row2.setSpacing(6)
        self.btn_tree = QPushButton("View Metadata Tree", self)
        self.btn_tree.setObjectName("btn_copy")
        self.btn_tree.setToolTip("Open Interactive OpenEXR & ExifTool Attribute Tree (Alt+I)")
        self.btn_tree.clicked.connect(self.tree_requested.emit)
        row2.addWidget(self.btn_tree)

        self.btn_close = QPushButton("Close", self)
        self.btn_close.setObjectName("btn")
        self.btn_close.clicked.connect(self._on_close_clicked)
        self.btn_close.hide()  # Hidden by default when embedded inside dock
        row2.addWidget(self.btn_close)
        btn_layout.addLayout(row2)

        self._main_layout.addLayout(btn_layout)

        # Populate initial content
        self._rebuild_content()

    def update_info(self, media_info: Dict[str, Any]) -> None:
        """Update media info dictionary and dynamically refresh all widgets."""
        self.info = media_info or {}
        self._rebuild_content()

    def _rebuild_content(self) -> None:
        """Construct rich metadata view inside scroll area."""
        old_w = self._scroll_area.takeWidget()
        if old_w:
            old_w.deleteLater()

        content_w = QWidget()
        content_w.setStyleSheet("background: transparent;")
        c_layout = QVBoxLayout(content_w)
        c_layout.setContentsMargins(6, 6, 6, 6)
        c_layout.setSpacing(12)

        def add_row(grid: QGridLayout, r: int, label: str, value: str):
            h = QLabel(label)
            h.setObjectName("hdr")
            v = QLabel(value)
            v.setObjectName("val")
            v.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            grid.addWidget(h, r, 0)
            grid.addWidget(v, r, 1)

        file_path = self.info.get("file_path", "")
        has_file = bool(file_path and os.path.exists(file_path))
        file_name = self.info.get("file_name", os.path.basename(file_path) if has_file else "Synthetic Stream")
        file_dir = self.info.get("file_dir", os.path.dirname(file_path) if has_file else "In-Memory")

        # 1. Source File / Container
        file_grp = QGroupBox("Source File / Media Container", content_w)
        fg_layout = QGridLayout(file_grp)
        fg_layout.setSpacing(5)

        add_row(fg_layout, 0, "Name:", file_name)
        add_row(fg_layout, 1, "Directory:", file_dir)
        add_row(fg_layout, 2, "Media Type:", self.info.get("media_type", "Video / Image Sequence"))

        if has_file:
            size_str = self.info.get("file_size_str")
            if not size_str:
                size_bytes = os.path.getsize(file_path)
                size_mb = size_bytes / (1024.0 * 1024.0)
                size_str = f"{size_mb:.2f} MB ({size_bytes:,} bytes)"
            mod_time = self.info.get("modified_time")
            if not mod_time:
                mod_time = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(os.path.getmtime(file_path)))

            add_row(fg_layout, 3, "File Size:", size_str)
            add_row(fg_layout, 4, "Date Modified:", mod_time)
            if self.info.get("created_time"):
                add_row(fg_layout, 5, "Date Created:", self.info["created_time"])
            if self.info.get("format_name"):
                add_row(fg_layout, 6, "Container:", self.info["format_name"])
            if self.info.get("overall_bitrate_str"):
                add_row(fg_layout, 7, "Total Bitrate:", self.info["overall_bitrate_str"])
        else:
            add_row(fg_layout, 3, "Storage:", "In-Memory RAM Frame Buffer")

        c_layout.addWidget(file_grp)

        # 2. Comprehensive Embedded Metadata Table (EXR header tags, FFprobe, PIL EXIF, IPTC)
        exr_details = self.info.get("exr_details")
        tags = dict(self.info.get("all_metadata_tags", {}))
        if not tags:
            tags.update(self.info.get("stream_tags", {}))
            tags.update(self.info.get("format_tags", {}))
            tags.update(self.info.get("image_info_tags", {}))
            tags.update(self.info.get("exif_tags", {}))
            if exr_details and "attributes" in exr_details:
                tags.update(exr_details["attributes"])

        if tags:
            tag_grp = QGroupBox(f"Embedded Metadata && Header Attributes ({len(tags)})", content_w)
            self.tag_grp = tag_grp
            tg_vbox = QVBoxLayout(tag_grp)
            tg_vbox.setSpacing(6)

            # Search Filter Bar
            filter_box = QHBoxLayout()
            filter_box.setSpacing(6)
            lbl_filter = QLabel("Filter:")
            lbl_filter.setObjectName("hdr")
            lbl_filter.setFixedWidth(40)
            self.filter_edit = QLineEdit(tag_grp)
            self.filter_edit.setObjectName("meta_filter")
            self.filter_edit.setPlaceholderText("Search attributes or values...")
            self.filter_edit.setClearButtonEnabled(True)
            self.filter_edit.textChanged.connect(self._on_filter_changed)
            filter_box.addWidget(lbl_filter)
            filter_box.addWidget(self.filter_edit)
            tg_vbox.addLayout(filter_box)

            # 2-column Metadata Table
            self.meta_table = MetaTableWidget(tag_grp)
            self.meta_table.setObjectName("meta_table")
            self.meta_table.setAlternatingRowColors(True)
            self.meta_table.setColumnCount(2)
            self.meta_table.setHorizontalHeaderLabels(["Attribute Key", "Value"])
            self.meta_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
            self.meta_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
            self.meta_table.setColumnWidth(0, 180)
            self.meta_table.verticalHeader().setVisible(False)
            self.meta_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
            self.meta_table.setSelectionMode(QTableWidget.SelectionMode.ExtendedSelection)
            self.meta_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            self.meta_table.customContextMenuRequested.connect(self._on_table_context_menu)

            sorted_keys = sorted(tags.keys(), key=lambda x: str(x).lower())
            self.meta_table.setRowCount(len(sorted_keys))
            for row_idx, key in enumerate(sorted_keys):
                val_str = str(tags[key])
                item_key = QTableWidgetItem(str(key))
                item_key.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
                item_val = QTableWidgetItem(val_str)
                item_val.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
                self.meta_table.setItem(row_idx, 0, item_key)
                self.meta_table.setItem(row_idx, 1, item_val)

            # Sizing: table dynamically expands with stretch=1
            table_height = min(380, max(160, len(sorted_keys) * 25 + 32))
            self.meta_table.setMinimumHeight(table_height)
            tg_vbox.addWidget(self.meta_table, 1)

            c_layout.addWidget(tag_grp, 1)
        else:
            self.meta_table = None
            self.filter_edit = None
            self.tag_grp = None

        # 3. OpenEXR Header Specifications (if present)
        if exr_details:
            exr_grp = QGroupBox("OpenEXR Specifications", content_w)
            exr_layout = QGridLayout(exr_grp)
            exr_layout.setSpacing(5)

            disp_w = exr_details.get("display_window")
            if disp_w:
                dw_w = disp_w[2] - disp_w[0] + 1
                dw_h = disp_w[3] - disp_w[1] + 1
                add_row(exr_layout, 0, "Display Window:", f"({disp_w[0]}, {disp_w[1]}) - ({disp_w[2]}, {disp_w[3]}) [{dw_w}x{dw_h}]")

            dw = exr_details.get("data_window")
            if dw:
                bbox_w = dw[2] - dw[0] + 1
                bbox_h = dw[3] - dw[1] + 1
                add_row(exr_layout, 1, "Data Window (BBox):", f"({dw[0]}, {dw[1]}) - ({dw[2]}, {dw[3]}) [{bbox_w}x{bbox_h}]")

            if exr_details.get("compression"):
                add_row(exr_layout, 2, "Compression:", str(exr_details["compression"]))

            if exr_details.get("line_order"):
                add_row(exr_layout, 3, "Line Order:", str(exr_details["line_order"]))

            if exr_details.get("pixel_aspect") is not None:
                add_row(exr_layout, 4, "Pixel Aspect:", f"{float(exr_details['pixel_aspect']):.4f}")

            channels = exr_details.get("channels", [])
            if channels:
                ch_strs = [f"{c.get('name', '')} ({c.get('type', '')})" if c.get('type') else c.get('name', '') for c in channels]
                ch_display = ", ".join(ch_strs[:16]) + (" ..." if len(ch_strs) > 16 else "")
                add_row(exr_layout, 5, f"Channels ({len(channels)}):", ch_display)

            layers = exr_details.get("layers", [])
            if layers:
                layers_display = ", ".join(layers[:16]) + (" ..." if len(layers) > 16 else "")
                add_row(exr_layout, 6, f"AOVs / Layers ({len(layers)}):", layers_display)

            cryptos = exr_details.get("cryptomatte_layers", [])
            if cryptos:
                add_row(exr_layout, 7, f"Cryptomatte ({len(cryptos)}):", ", ".join(cryptos))

            c_layout.addWidget(exr_grp)

        # 4. Video & Codec Specifications
        v_grp = QGroupBox("Video & Codec Specifications", content_w)
        vg_layout = QGridLayout(v_grp)
        vg_layout.setSpacing(5)

        w = self.info.get("width", 1920)
        h = self.info.get("height", 1080)
        mp = (w * h) / 1_000_000.0 if w and h else 0.0
        aspect = w / max(1.0, float(h)) if w and h else 1.778

        # Standard resolution tag (FHD, UHD, 4K DCI, 2K, etc.)
        res_tag = ""
        if w == 1920 and h == 1080:
            res_tag = " (1080p Full HD)"
        elif w == 3840 and h == 2160:
            res_tag = " (4K UHD-1)"
        elif w == 4096 and h == 2160:
            res_tag = " (4K DCI Native)"
        elif w == 2048 and h == 1080:
            res_tag = " (2K DCI)"
        elif w == 1280 and h == 720:
            res_tag = " (720p HD)"

        add_row(vg_layout, 0, "Resolution:", f"{w} x {h} ({mp:.2f} MP){res_tag}")
        add_row(vg_layout, 1, "Image Aspect:", f"{aspect:.3f}:1")
        add_row(vg_layout, 2, "Pixel Aspect:", f"{self.info.get('pixel_aspect', 1.0):.3f}")

        v_codec = self.info.get("video_codec", "")
        if v_codec:
            codec_tag = self.info.get("video_codec_tag", "")
            codec_display = f"{v_codec} [{codec_tag}]" if codec_tag else v_codec
            add_row(vg_layout, 3, "Video Codec:", codec_display)

        if self.info.get("video_profile"):
            lvl = f", Level {self.info['video_level']}" if self.info.get("video_level") else ""
            add_row(vg_layout, 4, "Profile & Level:", f"{self.info['video_profile']}{lvl}")

        if self.info.get("pixel_format"):
            b_sample = f", {self.info['bits_per_sample']}-bit" if self.info.get("bits_per_sample") else ""
            add_row(vg_layout, 5, "Pixel Format:", f"{self.info['pixel_format']}{b_sample}")
        elif self.info.get("bit_depth"):
            add_row(vg_layout, 5, "Precision / Depth:", str(self.info["bit_depth"]))

        if self.info.get("video_bitrate_str"):
            add_row(vg_layout, 6, "Stream Bitrate:", self.info["video_bitrate_str"])

        if self.info.get("color_space") or self.info.get("color_primaries"):
            cs = self.info.get("color_space", "bt709")
            cp = self.info.get("color_primaries", "")
            cr = self.info.get("color_range", "")
            cs_str = f"{cs}" + (f" (Primaries: {cp})" if cp and cp != cs else "") + (f" | {cr}" if cr else "")
            add_row(vg_layout, 7, "Colorspace / Range:", cs_str)

        if self.info.get("color_transfer"):
            add_row(vg_layout, 8, "Transfer Function:", str(self.info["color_transfer"]))

        if self.info.get("image_format"):
            add_row(vg_layout, 9, "Image Format:", f"{self.info['image_format']} ({self.info.get('image_mode', 'RGBA')})")

        c_layout.addWidget(v_grp)

        # 4. Audio Stream Specifications (if present)
        if self.info.get("audio_codec"):
            a_grp = QGroupBox("Audio Stream Specifications", content_w)
            ag_layout = QGridLayout(a_grp)
            ag_layout.setSpacing(5)

            add_row(ag_layout, 0, "Audio Codec:", self.info.get("audio_codec", "AAC"))
            sr = self.info.get("audio_sample_rate", "")
            sr_str = f"{int(sr):,} Hz" if sr and str(sr).isdigit() else str(sr)
            add_row(ag_layout, 1, "Sample Rate:", sr_str)
            ch = self.info.get("audio_channels", 2)
            ch_layout = self.info.get("audio_channel_layout", "")
            ch_str = f"{ch} Channels" + (f" ({ch_layout})" if ch_layout else "")
            add_row(ag_layout, 2, "Channels:", ch_str)
            if self.info.get("audio_bitrate_str"):
                add_row(ag_layout, 3, "Audio Bitrate:", self.info["audio_bitrate_str"])

            c_layout.addWidget(a_grp)

        # 5. Timeline & Playback
        t_grp = QGroupBox("Timeline & Playback", content_w)
        tg_layout = QGridLayout(t_grp)
        tg_layout.setSpacing(5)

        start = self.info.get("start_frame", 1)
        end = self.info.get("end_frame", 100)
        total = max(1, end - start + 1)
        fps = self.info.get("fps", 24.0)
        dur_sec = total / max(0.1, fps)

        hours = int(dur_sec // 3600)
        mins = int((dur_sec % 3600) // 60)
        secs = int(dur_sec % 60)
        frames_tc = int((dur_sec - int(dur_sec)) * fps)
        tc_str = f"{hours:02d}:{mins:02d}:{secs:02d}:{frames_tc:02d}"

        curr_f = self.info.get("current_frame", start)
        curr_dur = (curr_f - start) / max(0.1, fps)
        curr_tc = f"{int(curr_dur // 3600):02d}:{int((curr_dur % 3600) // 60):02d}:{int(curr_dur % 60):02d}:{int((curr_dur - int(curr_dur)) * fps):02d}"

        add_row(tg_layout, 0, "Sequence Range:", f"[{start} .. {end}] ({total} frames)")
        add_row(tg_layout, 1, "Native FPS:", f"{fps:.2f} fps")
        add_row(tg_layout, 2, "Total Duration:", f"{dur_sec:.2f} sec ({tc_str})")
        add_row(tg_layout, 3, "Current Playhead:", f"Frame {curr_f} ({curr_tc})")
        add_row(tg_layout, 4, "Active In / Out:", f"[{self.info.get('in_frame', start)} .. {self.info.get('out_frame', end)}]")

        c_layout.addWidget(t_grp)

        # 6. Studio OCIO & Viewport Color
        ocio_grp = QGroupBox("Studio OCIO & Viewport Color", content_w)
        og_layout = QGridLayout(ocio_grp)
        og_layout.setSpacing(5)

        add_row(og_layout, 0, "Input Colorspace:", self.info.get("input_space_name", "sRGB (Texture/Video)"))
        add_row(og_layout, 1, "View Transform:", self.info.get("view_transform_name", "sRGB - Display"))
        add_row(og_layout, 2, "Exposure EV:", f"{self.info.get('exposure_ev', 0.0):+.2f} EV")
        add_row(og_layout, 3, "Display Gamma:", f"{self.info.get('gamma', 1.0):.2f}")
        add_row(og_layout, 4, "Channel Mode:", self.info.get("channel_mode_str", "RGB (All Channels)"))

        c_layout.addWidget(ocio_grp)

        c_layout.addStretch()
        self._scroll_area.setWidget(content_w)

    def _on_filter_changed(self, text: str) -> None:
        """Filter table rows matching attribute key or value."""
        if not self.meta_table:
            return
        search = text.lower().strip()
        visible_count = 0
        total = self.meta_table.rowCount()

        for r in range(total):
            k_item = self.meta_table.item(r, 0)
            v_item = self.meta_table.item(r, 1)
            k_str = k_item.text().lower() if k_item else ""
            v_str = v_item.text().lower() if v_item else ""
            match = (not search) or (search in k_str) or (search in v_str)
            self.meta_table.setRowHidden(r, not match)
            if match:
                visible_count += 1

        if self.tag_grp:
            if search:
                self.tag_grp.setTitle(f"Embedded Metadata ({visible_count}/{total} matching)")
            else:
                self.tag_grp.setTitle(f"Embedded Metadata && Header Attributes ({total})")

    def _on_table_context_menu(self, pos) -> None:
        """Right-click context menu on metadata table."""
        if not self.meta_table:
            return
        item = self.meta_table.itemAt(pos)
        if not item:
            return
        r = item.row()
        k_item = self.meta_table.item(r, 0)
        v_item = self.meta_table.item(r, 1)
        k_text = k_item.text() if k_item else ""
        v_text = v_item.text() if v_item else ""

        menu = QMenu(self)
        act_copy_val = menu.addAction("Copy Value")
        act_copy_key = menu.addAction("Copy Key")
        act_copy_row = menu.addAction("Copy Key & Value")

        action = menu.exec(self.meta_table.viewport().mapToGlobal(pos))
        if action == act_copy_val:
            QApplication.clipboard().setText(v_text)
        elif action == act_copy_key:
            QApplication.clipboard().setText(k_text)
        elif action == act_copy_row:
            QApplication.clipboard().setText(f"{k_text}: {v_text}")

    def _on_copy_all(self) -> None:
        """Copy formatted metadata string to system clipboard."""
        if not self.info:
            app = QApplication.instance()
            if app:
                for w in app.topLevelWidgets():
                    if hasattr(w, "_get_current_source_info"):
                        self.info = w._get_current_source_info()
                        if self.info:
                            self._rebuild_content()
                            break

        lines = ["=== OpenReview Source Media Details ==="]
        for k, v in sorted(self.info.items(), key=lambda x: str(x[0]).lower()):
            if isinstance(v, dict):
                lines.append(f"\n[{k}]")
                for sub_k, sub_v in sorted(v.items(), key=lambda x: str(x[0]).lower()):
                    lines.append(f"  {sub_k}: {sub_v}")
            elif isinstance(v, list):
                lines.append(f"\n[{k}]")
                for item in v:
                    lines.append(f"  - {item}")
            else:
                lines.append(f"{k}: {v}")
        text = "\n".join(lines)
        QApplication.clipboard().setText(text)
        self.btn_copy.setText("✓ Copied!")
        from PySide6.QtCore import QTimer
        def _safe_reset_copy():
            try:
                if hasattr(self, 'btn_copy') and self.btn_copy:
                    self.btn_copy.setText("Copy All to Clipboard")
            except RuntimeError:
                pass
        QTimer.singleShot(1800, _safe_reset_copy)

    def _on_export_json(self) -> None:
        """Export all source metadata to a JSON file."""
        if not self.info:
            app = QApplication.instance()
            if app:
                for w in app.topLevelWidgets():
                    if hasattr(w, "_get_current_source_info"):
                        self.info = w._get_current_source_info()
                        if self.info:
                            self._rebuild_content()
                            break

        initial_name = "metadata.json"
        if self.info.get("file_name"):
            base = os.path.splitext(self.info["file_name"])[0]
            initial_name = f"{base}_metadata.json"

        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "Export Metadata JSON",
            initial_name,
            "JSON Files (*.json);;All Files (*)",
        )
        if file_path:
            try:
                with open(file_path, "w", encoding="utf-8") as f:
                    json.dump(self.info, f, indent=2, default=str)
                self.btn_export.setText("✓ Exported!")
                from PySide6.QtCore import QTimer
                def _safe_reset_exp():
                    try:
                        if hasattr(self, 'btn_export') and self.btn_export:
                            self.btn_export.setText("Export JSON")
                    except RuntimeError:
                        pass
                QTimer.singleShot(1800, _safe_reset_exp)
            except Exception:
                self.btn_export.setText("Export Failed")
                from PySide6.QtCore import QTimer
                def _safe_reset_fail():
                    try:
                        if hasattr(self, 'btn_export') and self.btn_export:
                            self.btn_export.setText("Export JSON")
                    except RuntimeError:
                        pass
                QTimer.singleShot(1800, _safe_reset_fail)

    def _on_close_clicked(self) -> None:
        p = self.parent()
        if p and hasattr(p, "close_panel"):
            p.close_panel()
        elif p and hasattr(p, "accept"):
            p.accept()
        else:
            self.close()

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self._on_close_clicked()
            event.accept()
            return
        if event.key() == Qt.Key.Key_I:
            # Prevent closing when user is actively searching in the filter box
            if self.filter_edit and self.filter_edit.hasFocus():
                super().keyPressEvent(event)
                return
            self._on_close_clicked()
            event.accept()
            return
        super().keyPressEvent(event)


class ImageInfoDialog(QDialog):
    """Studio Image Info and Source Media Details Dialog wrapper."""

    def __init__(self, media_info: Dict[str, Any], parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle("Source Media & Image Details")
        self.resize(500, 580)
        self.setStyleSheet("background-color: #1a1a1a;")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        self.widget = ImageInfoWidget(media_info, self)
        layout.addWidget(self.widget)
        self.info = media_info

    def __getattr__(self, name):
        if name != "widget" and hasattr(self, "widget"):
            return getattr(self.widget, name)
        raise AttributeError(f"'{type(self).__name__}' object has no attribute '{name}'")
