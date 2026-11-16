"""
Export Template Builder & Pipeline Presets for Editorial & VFX Turnovers.
Provides:
- Visual draggable and editable folder hierarchy & filename breadcrumb builder
- Clickable and draggable token chips ({sequence}, {shot}, {clip_name}, {track}, etc.)
- Studio pipeline presets (VFX Turnovers, Editorial Conforms, Flat Subfolders, Dated Deliveries)
- User preset saving, persistence in preferences.json, and deletion
- Live dynamic preview of evaluated file paths for active sequence & clips
- Automatic recursive directory creation on export
"""
from __future__ import annotations

import os
import re
import json
import logging
from datetime import datetime
from typing import List, Tuple, Dict, Optional, Any

from PySide6.QtCore import Qt, Signal, QMimeData, QPoint, QByteArray
from PySide6.QtGui import QDrag, QFont, QColor, QPainter, QCursor
from PySide6.QtWidgets import (
    QDialog,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QFormLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QToolButton,
    QComboBox,
    QGroupBox,
    QFrame,
    QScrollArea,
    QProgressBar,
    QFileDialog,
    QMessageBox,
    QInputDialog,
    QSizePolicy,
)

from openreview.models.timeline_sequence import TimelineSequence, Track, ClipItem, TrackType
from openreview.core.timecode import frame_to_timecode
from openreview.core.preferences import PreferencesManager
from openreview.ui.svg_icons import get_svg_icon

logger = logging.getLogger(__name__)

STANDARD_PRESETS: Dict[str, str] = {
    "VFX Turnover": "{sequence}/{shot}/plates/{clip_name}_v{version}",
    "Editorial Conform": "{sequence}/{track}/{clip_name}_{dst_in}-{dst_out}",
    "Flat Subfolder per Clip": "{clip_name}/{clip_name}_{index}",
    "Dated Client Delivery": "{date}/{sequence}/{resolution}/{clip_name}",
    "Single File (Flat)": "{clip_name}",
}

AVAILABLE_TOKENS: List[Tuple[str, str]] = [
    ("{project}", "Project or Production Name"),
    ("{sequence}", "Active Sequence Name"),
    ("{shot}", "Shot Identifier (derived or clip name)"),
    ("{clip_name}", "Full Clip Item Name"),
    ("{track}", "Timeline Track Name"),
    ("{index}", "Sequential 3-digit Index (001, 002...)"),
    ("{version}", "Clip Version (e.g. 01, v02)"),
    ("{resolution}", "Video Frame Resolution (e.g. 1920x1080)"),
    ("{fps}", "Timebase / Frame Rate (e.g. 24)"),
    ("{date}", "Current Date (YYYYMMDD)"),
    ("{src_in}", "Source In Frame"),
    ("{src_out}", "Source Out Frame"),
    ("{dst_in}", "Timeline In Frame"),
    ("{dst_out}", "Timeline Out Frame"),
    ("{duration}", "Duration in Frames"),
    ("{colorspace}", "Working Colorspace / Input Space"),
]


def extract_shot_name(name: str) -> str:
    """Extract shot identifier from a typical VFX clip name (e.g. sq01_sh010_plate -> sh010)."""
    if not name:
        return "shot"
    # Matches common shot patterns like sh010, shot010, sc01_sh010, etc.
    m = re.search(r"(sh\d+|shot\d+|sc\d+_sh\d+|s\d+_c\d+)", name, re.IGNORECASE)
    if m:
        return m.group(1)
    parts = name.split("_")
    if len(parts) >= 2 and any(c.isdigit() for c in parts[1]):
        return parts[1]
    return parts[0] if parts else name


def sanitize_filename_segment(seg: str) -> str:
    """Sanitize individual folder or file segment by removing illegal OS characters."""
    # Replace illegal filename characters with underscores
    cleaned = re.sub(r'[<>:"|?*]', '_', seg)
    return cleaned.strip()


def resolve_export_pattern(
    pattern: str,
    clip: Optional[ClipItem],
    track: Optional[Track],
    sequence: Optional[TimelineSequence],
    index: int = 1,
    ext: str = "",
) -> str:
    """
    Resolve template pattern containing tokens and folder slashes into a concrete relative path.
    Supports tokens: {project}, {sequence}, {shot}, {clip_name}, {name}, {track}, {index},
    {version}, {resolution}, {fps}, {date}, {src_in}, {src_out}, {dst_in}, {dst_out}, {duration},
    {colorspace}, {ext}.
    """
    seq_name = sequence.name if sequence else "Sequence"
    seq_fps = sequence.fps if sequence else 24.0
    seq_res = f"{sequence.width}x{sequence.height}" if sequence else "1920x1080"
    track_name = track.name if track else "Video1"

    clip_name = clip.name if clip else "Clip"
    shot_name = extract_shot_name(clip_name)

    version_str = "01"
    if clip and clip.version:
        v = str(clip.version).strip()
        version_str = f"{int(v):02d}" if v.isdigit() else v

    src_in = str(clip.src_in) if clip else "1"
    src_out = str(clip.src_out) if clip else "100"
    dst_in = str(clip.dst_in) if clip else "1"
    dst_out = str(clip.dst_out) if clip else "100"
    dur = str(clip.dst_duration) if clip else "100"
    cs = clip.colorspace if clip and clip.colorspace else "Rec.709"

    date_str = datetime.now().strftime("%Y%m%d")
    idx_str = f"{index:03d}"

    res_pat = pattern.replace("{project}", "Project")
    res_pat = res_pat.replace("{sequence}", seq_name)
    res_pat = res_pat.replace("{shot}", shot_name)
    res_pat = res_pat.replace("{clip_name}", clip_name)
    res_pat = res_pat.replace("{name}", clip_name)
    res_pat = res_pat.replace("{track}", track_name)
    res_pat = res_pat.replace("{index}", idx_str)
    res_pat = res_pat.replace("{version}", version_str)
    res_pat = res_pat.replace("{resolution}", seq_res)
    res_pat = res_pat.replace("{fps}", f"{seq_fps:g}")
    res_pat = res_pat.replace("{date}", date_str)
    res_pat = res_pat.replace("{src_in}", src_in)
    res_pat = res_pat.replace("{src_out}", src_out)
    res_pat = res_pat.replace("{dst_in}", dst_in)
    res_pat = res_pat.replace("{dst_out}", dst_out)
    res_pat = res_pat.replace("{duration}", dur)
    res_pat = res_pat.replace("{colorspace}", cs)

    # Normalize slashes and clean segments
    res_pat = res_pat.replace("\\", "/")
    segments = [s.strip() for s in res_pat.split("/") if s.strip()]
    if not segments:
        segments = [clip_name]

    sanitized = [sanitize_filename_segment(s) for s in segments]
    rel_path = "/".join(sanitized)

    if ext:
        clean_ext = ext.lstrip(".")
        if not rel_path.lower().endswith(f".{clean_ext.lower()}"):
            rel_path = f"{rel_path}.{clean_ext}"

    return rel_path


# ------------------------------------------------------------------------------
# 1. Clickable and Draggable Token Chip Button
# ------------------------------------------------------------------------------
class TokenChipButton(QToolButton):
    """Clickable and draggable token pill for insertion into path template segments."""
    token_inserted = Signal(str)

    def __init__(self, token: str, tooltip: str, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.token = token
        self.setText(token)
        self.setToolTip(f"{token}\n{tooltip}\n(Click or Drag to insert)")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setStyleSheet("""
            QToolButton {
                background-color: #242424;
                color: #e5a823;
                border: 1px solid #3a3a3a;
                border-radius: 10px;
                padding: 3px 8px;
                font-size: 10px;
                font-family: 'Consolas', monospace;
                font-weight: bold;
            }
            QToolButton:hover {
                background-color: #2f2f2f;
                border-color: #e5a823;
                color: #ffffff;
            }
            QToolButton:pressed {
                background-color: #e5a823;
                color: #000000;
            }
        """)
        self._drag_start_pos: Optional[QPoint] = None
        self.clicked.connect(lambda: self.token_inserted.emit(self.token))

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_start_pos = event.pos()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._drag_start_pos and (event.pos() - self._drag_start_pos).manhattanLength() > 6:
            drag = QDrag(self)
            mime = QMimeData()
            mime.setText(self.token)
            drag.setMimeData(mime)
            drag.exec(Qt.DropAction.CopyAction)
            self._drag_start_pos = None
            return
        super().mouseMoveEvent(event)


# ------------------------------------------------------------------------------
# 2. Editable & Reorderable Folder Breadcrumb Segment
# ------------------------------------------------------------------------------
class FolderBreadcrumbSegment(QFrame):
    """Visual block representing a single folder level or file pattern."""
    segment_changed = Signal()
    segment_deleted = Signal(QWidget)

    def __init__(self, text: str, is_last: bool = False, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.is_last = is_last
        self.setAcceptDrops(True)
        self.setStyleSheet("""
            FolderBreadcrumbSegment {
                background-color: #1a1a1a;
                border: 1px solid #333333;
                border-radius: 4px;
                padding: 2px 4px;
            }
            FolderBreadcrumbSegment:hover {
                border-color: #4a4a4a;
            }
            QLineEdit {
                background-color: #121212;
                color: #ffffff;
                border: 1px solid #2a2a2a;
                border-radius: 3px;
                padding: 2px 6px;
                font-family: 'Consolas', monospace;
                font-size: 11px;
                min-width: 90px;
            }
            QLineEdit:focus {
                border-color: #e5a823;
                background-color: #1e1e1e;
            }
        """)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.setSpacing(4)

        # Drag handle icon
        self.lbl_handle = QLabel("⋮⋮", self)
        self.lbl_handle.setStyleSheet("color: #666666; font-size: 11px; font-weight: bold;")
        self.lbl_handle.setCursor(Qt.CursorShape.SizeAllCursor)
        layout.addWidget(self.lbl_handle)

        # Folder icon or File icon
        icon_name = "file" if is_last else "folder"
        self.lbl_icon = QLabel(self)
        self.lbl_icon.setPixmap(get_svg_icon(icon_name, color="#e5a823" if not is_last else "#38d9c6", size=13).pixmap(13, 13))
        layout.addWidget(self.lbl_icon)

        # Editable segment text
        self.edit = QLineEdit(text, self)
        self.edit.textChanged.connect(lambda _: self.segment_changed.emit())
        layout.addWidget(self.edit)

        # Delete button
        self.btn_del = QToolButton(self)
        self.btn_del.setText("✕")
        self.btn_del.setToolTip("Remove folder level")
        self.btn_del.setStyleSheet("""
            QToolButton {
                background: transparent;
                border: none;
                color: #666666;
                font-size: 10px;
                padding: 0px 2px;
            }
            QToolButton:hover {
                color: #e74c3c;
            }
        """)
        self.btn_del.clicked.connect(lambda: self.segment_deleted.emit(self))
        layout.addWidget(self.btn_del)

        self.lbl_slash = QLabel("/" if not is_last else "", self)
        self.lbl_slash.setStyleSheet("color: #e5a823; font-weight: bold; font-size: 14px; margin-left: 2px;")
        layout.addWidget(self.lbl_slash)

    def text(self) -> str:
        return self.edit.text().strip()

    def setText(self, text: str) -> None:
        self.edit.setText(text)

    def insert_token(self, token: str) -> None:
        self.edit.insert(token)
        self.edit.setFocus()

    def set_is_last(self, is_last: bool) -> None:
        self.is_last = is_last
        self.lbl_slash.setText("" if is_last else "/")
        icon_name = "file" if is_last else "folder"
        self.lbl_icon.setPixmap(get_svg_icon(icon_name, color="#e5a823" if not is_last else "#38d9c6", size=13).pixmap(13, 13))

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasText():
            event.acceptProposedAction()
            self.setStyleSheet("""
                FolderBreadcrumbSegment {
                    background-color: #262626;
                    border: 1px solid #e5a823;
                    border-radius: 4px;
                    padding: 2px 4px;
                }
            """)

    def dragLeaveEvent(self, event) -> None:
        self.setStyleSheet("""
            FolderBreadcrumbSegment {
                background-color: #1a1a1a;
                border: 1px solid #333333;
                border-radius: 4px;
                padding: 2px 4px;
            }
        """)

    def dropEvent(self, event) -> None:
        text = event.mimeData().text()
        if text:
            self.edit.insert(text)
            self.segment_changed.emit()
            event.acceptProposedAction()
        self.setStyleSheet("""
            FolderBreadcrumbSegment {
                background-color: #1a1a1a;
                border: 1px solid #333333;
                border-radius: 4px;
                padding: 2px 4px;
            }
        """)


# ------------------------------------------------------------------------------
# 3. Visual Breadcrumb Builder
# ------------------------------------------------------------------------------
class VisualBreadcrumbBuilder(QWidget):
    """Visual horizontal breadcrumb editor allowing adding, reordering, and editing folder segments."""
    pattern_changed = Signal(str)

    def __init__(self, pattern: str = "{name}", parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._updating: bool = False
        self._segments: List[FolderBreadcrumbSegment] = []

        outer_layout = QHBoxLayout(self)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        outer_layout.setSpacing(6)

        # Scroll area for long folder hierarchies
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFixedHeight(44)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet("""
            QScrollArea {
                background-color: #141414;
                border: 1px solid #2a2a2a;
                border-radius: 4px;
            }
            QScrollBar:horizontal {
                height: 4px;
                background: #141414;
            }
            QScrollBar::handle:horizontal {
                background: #333333;
                border-radius: 2px;
            }
        """)

        self.container = QWidget(scroll)
        self.container.setStyleSheet("background-color: transparent;")
        self.row_layout = QHBoxLayout(self.container)
        self.row_layout.setContentsMargins(6, 4, 6, 4)
        self.row_layout.setSpacing(6)
        scroll.setWidget(self.container)
        outer_layout.addWidget(scroll, 1)

        # Add Folder Segment Button
        self.btn_add_folder = QToolButton(self)
        self.btn_add_folder.setText("+ Folder")
        self.btn_add_folder.setToolTip("Add intermediate directory level")
        self.btn_add_folder.setFixedHeight(34)
        self.btn_add_folder.setStyleSheet("""
            QToolButton {
                background-color: #242424;
                color: #e5a823;
                border: 1px dashed #444444;
                border-radius: 4px;
                padding: 4px 8px;
                font-size: 10.5px;
                font-weight: bold;
            }
            QToolButton:hover {
                background-color: #2f2f2f;
                border-color: #e5a823;
            }
        """)
        self.btn_add_folder.clicked.connect(self._on_add_folder)
        outer_layout.addWidget(self.btn_add_folder)

        self.set_pattern(pattern)

    def set_pattern(self, pattern: str) -> None:
        self._updating = True
        # Clear existing segments
        for seg in self._segments:
            self.row_layout.removeWidget(seg)
            seg.deleteLater()
        self._segments.clear()

        # Split pattern by slashes
        clean_pat = pattern.replace("\\", "/")
        raw_parts = [p.strip() for p in clean_pat.split("/") if p.strip()]
        if not raw_parts:
            raw_parts = ["{clip_name}"]

        for idx, part in enumerate(raw_parts):
            is_last = (idx == len(raw_parts) - 1)
            seg = FolderBreadcrumbSegment(part, is_last=is_last, parent=self.container)
            seg.segment_changed.connect(self._on_segment_changed)
            seg.segment_deleted.connect(self._on_segment_deleted)
            self._segments.append(seg)
            self.row_layout.addWidget(seg)

        self.row_layout.addStretch()
        self._updating = False
        self._update_is_last_flags()

    def get_pattern(self) -> str:
        parts = [s.text() for s in self._segments if s.text()]
        return "/".join(parts) if parts else "{clip_name}"

    def insert_token_into_active(self, token: str) -> None:
        """Insert token into currently focused segment, or append to filename segment."""
        for s in self._segments:
            if s.edit.hasFocus():
                s.insert_token(token)
                return
        if self._segments:
            self._segments[-1].insert_token(token)

    def _on_add_folder(self) -> None:
        # Insert a new folder before the last segment (filename)
        new_seg = FolderBreadcrumbSegment("folder", is_last=False, parent=self.container)
        new_seg.segment_changed.connect(self._on_segment_changed)
        new_seg.segment_deleted.connect(self._on_segment_deleted)

        if len(self._segments) > 0:
            insert_idx = len(self._segments) - 1
            self._segments.insert(insert_idx, new_seg)
            self.row_layout.insertWidget(insert_idx, new_seg)
        else:
            self._segments.append(new_seg)
            self.row_layout.addWidget(new_seg)

        self._update_is_last_flags()
        self._on_segment_changed()
        new_seg.edit.selectAll()
        new_seg.edit.setFocus()

    def _on_segment_deleted(self, widget: QWidget) -> None:
        if len(self._segments) <= 1:
            QMessageBox.information(self, "Export Template", "At least one segment (the filename) is required.")
            return
        if widget in self._segments:
            self._segments.remove(widget)
            self.row_layout.removeWidget(widget)
            widget.deleteLater()
            self._update_is_last_flags()
            self._on_segment_changed()

    def _update_is_last_flags(self) -> None:
        for idx, s in enumerate(self._segments):
            is_last = (idx == len(self._segments) - 1)
            s.set_is_last(is_last)

    def _on_segment_changed(self) -> None:
        if not self._updating:
            self.pattern_changed.emit(self.get_pattern())


# ------------------------------------------------------------------------------
# 4. Complete Export Template Builder Widget
# ------------------------------------------------------------------------------
class ExportTemplateBuilderWidget(QWidget):
    """Composite widget combining Presets, Breadcrumb Builder, Token Chips, and Live Preview."""
    pattern_changed = Signal(str)

    def __init__(
        self,
        sequence: Optional[TimelineSequence] = None,
        sample_clip: Optional[Tuple[Track, ClipItem]] = None,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.sequence = sequence
        self.sample_clip = sample_clip
        self.pref_mgr = PreferencesManager.instance()

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(8)

        # 1. Presets Header Row
        preset_row = QHBoxLayout()
        lbl_preset = QLabel("Pipeline Preset:", self)
        lbl_preset.setStyleSheet("color: #aaaaaa; font-size: 11px;")
        preset_row.addWidget(lbl_preset)

        self.combo_presets = QComboBox(self)
        self.combo_presets.setStyleSheet("min-width: 220px; font-weight: bold;")
        self._populate_presets()
        self.combo_presets.currentIndexChanged.connect(self._on_preset_selected)
        preset_row.addWidget(self.combo_presets, 1)

        self.btn_save_preset = QToolButton(self)
        self.btn_save_preset.setText("Save Preset...")
        self.btn_save_preset.setToolTip("Save current folder & filename structure for future exports")
        self.btn_save_preset.setStyleSheet("font-size: 10px; padding: 3px 6px;")
        self.btn_save_preset.clicked.connect(self._on_save_preset)
        preset_row.addWidget(self.btn_save_preset)

        self.btn_del_preset = QToolButton(self)
        self.btn_del_preset.setText("Delete")
        self.btn_del_preset.setToolTip("Delete selected custom preset")
        self.btn_del_preset.setStyleSheet("font-size: 10px; padding: 3px 6px; color: #ff8888;")
        self.btn_del_preset.clicked.connect(self._on_delete_preset)
        preset_row.addWidget(self.btn_del_preset)

        main_layout.addLayout(preset_row)

        # 2. Visual Breadcrumb Builder
        default_pat = getattr(self.pref_mgr.prefs, "last_export_template", "{sequence}/{shot}/plates/{clip_name}_v{version}")
        self.builder = VisualBreadcrumbBuilder(default_pat, self)
        self.builder.pattern_changed.connect(self._on_builder_pattern_changed)
        main_layout.addWidget(self.builder)

        # 3. Raw Pattern Text & Direct Sync
        raw_row = QHBoxLayout()
        lbl_raw = QLabel("Pattern Path:", self)
        lbl_raw.setStyleSheet("color: #888888; font-size: 10.5px;")
        raw_row.addWidget(lbl_raw)

        self.edit_raw = QLineEdit(default_pat, self)
        self.edit_raw.setStyleSheet("font-family: 'Consolas', monospace; font-size: 11px; color: #38d9c6;")
        self.edit_raw.textChanged.connect(self._on_raw_pattern_changed)
        raw_row.addWidget(self.edit_raw, 1)
        main_layout.addLayout(raw_row)

        # 4. Token Chips Container
        grp_tokens = QGroupBox("Available Name & Structure Tokens (Click or Drag to insert)", self)
        grp_tokens.setStyleSheet("""
            QGroupBox {
                color: #e5a823;
                font-size: 10.5px;
                font-weight: bold;
                border: 1px solid #2a2a2a;
                border-radius: 4px;
                margin-top: 6px;
                padding-top: 10px;
            }
        """)
        token_layout = QHBoxLayout(grp_tokens)
        token_layout.setContentsMargins(6, 6, 6, 6)
        token_layout.setSpacing(4)

        # Wrap in scroll or flow
        scroll_tokens = QScrollArea(grp_tokens)
        scroll_tokens.setWidgetResizable(True)
        scroll_tokens.setFixedHeight(48)
        scroll_tokens.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll_tokens.setStyleSheet("border: none; background: transparent;")

        tokens_inner = QWidget(scroll_tokens)
        tokens_inner.setStyleSheet("background: transparent;")
        tokens_row = QHBoxLayout(tokens_inner)
        tokens_row.setContentsMargins(0, 0, 0, 0)
        tokens_row.setSpacing(4)

        for tok, desc in AVAILABLE_TOKENS:
            btn = TokenChipButton(tok, desc, tokens_inner)
            btn.token_inserted.connect(self._on_insert_token)
            tokens_row.addWidget(btn)
        tokens_row.addStretch()

        tokens_inner.setLayout(tokens_row)
        scroll_tokens.setWidget(tokens_inner)
        token_layout.addWidget(scroll_tokens)
        main_layout.addWidget(grp_tokens)

        # 5. Live Destination Path Preview
        self.grp_preview = QGroupBox("Live Evaluated Export Path Preview", self)
        self.grp_preview.setStyleSheet("""
            QGroupBox {
                color: #38d9c6;
                font-size: 10.5px;
                font-weight: bold;
                border: 1px solid #2a2a2a;
                border-radius: 4px;
                margin-top: 6px;
                padding-top: 10px;
            }
        """)
        prev_layout = QVBoxLayout(self.grp_preview)
        prev_layout.setContentsMargins(8, 6, 8, 6)
        prev_layout.setSpacing(2)

        self.lbl_preview_path = QLabel(self.grp_preview)
        self.lbl_preview_path.setStyleSheet("color: #ffffff; font-family: 'Consolas', monospace; font-size: 11px; font-weight: bold;")
        self.lbl_preview_path.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        prev_layout.addWidget(self.lbl_preview_path)

        self.lbl_preview_info = QLabel(self.grp_preview)
        self.lbl_preview_info.setStyleSheet("color: #888888; font-size: 10px;")
        prev_layout.addWidget(self.lbl_preview_info)

        main_layout.addWidget(self.grp_preview)

        self._update_preview()

    def get_pattern(self) -> str:
        return self.edit_raw.text().strip() or "{clip_name}"

    def set_pattern(self, pattern: str) -> None:
        self.builder.set_pattern(pattern)
        self.edit_raw.setText(pattern)
        self._update_preview()

    def _on_insert_token(self, token: str) -> None:
        self.builder.insert_token_into_active(token)

    def _on_builder_pattern_changed(self, pat: str) -> None:
        self.edit_raw.blockSignals(True)
        self.edit_raw.setText(pat)
        self.edit_raw.blockSignals(False)
        self._update_preview()
        self.pattern_changed.emit(pat)

    def _on_raw_pattern_changed(self, pat: str) -> None:
        self.builder.blockSignals(True)
        self.builder.set_pattern(pat)
        self.builder.blockSignals(False)
        self._update_preview()
        self.pattern_changed.emit(pat)

    def _update_preview(self) -> None:
        pat = self.get_pattern()
        clip = self.sample_clip[1] if self.sample_clip else None
        track = self.sample_clip[0] if self.sample_clip else None

        rel_path = resolve_export_pattern(pat, clip, track, self.sequence, index=1, ext="")
        folders = [s for s in rel_path.split("/")[:-1] if s]
        file_part = rel_path.split("/")[-1] if "/" in rel_path else rel_path

        self.lbl_preview_path.setText(rel_path)
        if folders:
            folder_hierarchy = " > ".join(folders)
            self.lbl_preview_info.setText(f"📁 Creates {len(folders)} subfolder(s): {folder_hierarchy}  |  File: {file_part}")
        else:
            self.lbl_preview_info.setText(f"📄 Flat export in destination directory (No subfolders).")

    def _populate_presets(self) -> None:
        self.combo_presets.blockSignals(True)
        self.combo_presets.clear()
        # 1. Standard Presets
        for name, pat in STANDARD_PRESETS.items():
            self.combo_presets.addItem(f"{name} ({pat})", pat)

        # 2. User Presets from Preferences
        custom_presets = getattr(self.pref_mgr.prefs, "export_templates", {})
        if custom_presets:
            self.combo_presets.insertSeparator(self.combo_presets.count())
            for c_name, c_pat in custom_presets.items():
                self.combo_presets.addItem(f"⭐ {c_name} ({c_pat})", c_pat)

        self.combo_presets.blockSignals(False)

    def _on_preset_selected(self, index: int) -> None:
        pat = self.combo_presets.currentData()
        if pat:
            self.set_pattern(str(pat))

    def _on_save_preset(self) -> None:
        name, ok = QInputDialog.getText(self, "Save Pipeline Preset", "Enter a name for this export pipeline preset:")
        if ok and name.strip():
            c_name = name.strip()
            cur_pat = self.get_pattern()
            self.pref_mgr.prefs.export_templates[c_name] = cur_pat
            self.pref_mgr.save()
            self._populate_presets()
            # Select the newly saved preset
            for i in range(self.combo_presets.count()):
                if c_name in self.combo_presets.itemText(i):
                    self.combo_presets.setCurrentIndex(i)
                    break
            QMessageBox.information(self, "Preset Saved", f"Successfully saved pipeline preset '{c_name}'.")

    def _on_delete_preset(self) -> None:
        cur_text = self.combo_presets.currentText()
        if not cur_text.startswith("⭐ "):
            QMessageBox.information(self, "Delete Preset", "Only custom user presets can be deleted.")
            return
        preset_name = cur_text.split(" (")[0].replace("⭐ ", "").strip()
        confirm = QMessageBox.question(
            self,
            "Confirm Deletion",
            f"Are you sure you want to delete custom preset '{preset_name}'?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if confirm == QMessageBox.StandardButton.Yes:
            if preset_name in self.pref_mgr.prefs.export_templates:
                del self.pref_mgr.prefs.export_templates[preset_name]
                self.pref_mgr.save()
                self._populate_presets()
                self._on_preset_selected(0)
