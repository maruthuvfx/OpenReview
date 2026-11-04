"""
Interactive Metadata & ExifTool / OpenEXR Attribute Tree Widget.
Provides search, filtering, category grouping, nested inspection,
and JSON/text exporting for OpenEXR headers, ExifTool, and media metadata.
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

from PySide6.QtCore import Qt, QPoint, Signal
from PySide6.QtGui import QAction, QColor, QFont, QKeySequence
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QComboBox,
    QTreeWidget,
    QTreeWidgetItem,
    QHeaderView,
    QMenu,
    QFileDialog,
    QApplication,
    QFrame,
)

from openreview.core.metadata_tree import (
    MetadataNode,
    build_unified_metadata_tree,
    find_exiftool_executable,
    get_exiftool_version,
)


class MetadataTreeItem(QTreeWidgetItem):
    """Custom QTreeWidgetItem holding a MetadataNode reference."""

    def __init__(self, node: MetadataNode, parent=None):
        super().__init__(parent)
        self.node = node
        self._setup_item()

    def _setup_item(self) -> None:
        self.setText(0, self.node.key)
        val_str = ""
        if self.node.value not in ("", None):
            val_str = str(self.node.value)
        elif self.node.children and self.node.type_name == "category":
            val_str = f"({len(self.node.children)} attributes)"
        self.setText(1, val_str)

        type_label = f"[{self.node.type_name.upper()}]" if self.node.type_name and self.node.type_name != "category" else ""
        self.setText(2, type_label)

        # Style based on category or item
        if self.node.type_name == "category":
            f = self.font(0)
            f.setBold(True)
            self.setFont(0, f)
            self.setFont(1, f)
            if self.node.is_pinned:
                self.setForeground(0, QColor("#e5a823"))
                self.setForeground(1, QColor("#e5a823"))
            else:
                self.setForeground(0, QColor("#61afef"))
                self.setForeground(1, QColor("#888888"))
        else:
            # Color code type badge in column 2
            t_low = self.node.type_name.lower()
            if t_low in ("half", "float", "int", "number"):
                self.setForeground(2, QColor("#56b6c2"))
            elif t_low in ("timecode", "rational"):
                self.setForeground(2, QColor("#e5c07b"))
            elif t_low in ("box2i", "box", "v2f", "v3f"):
                self.setForeground(2, QColor("#98c379"))
            elif "exif" in t_low:
                self.setForeground(2, QColor("#c678dd"))
            else:
                self.setForeground(2, QColor("#7f848e"))


class MetadataTreeWidget(QWidget):
    """Interactive studio metadata tree widget supporting ExifTool, OpenEXR, and container streams."""

    media_inspected = Signal(str)

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._current_path: str = ""
        self._current_frame: int = 1
        self._current_context: Dict[str, Any] = {}
        self._exiftool_bin = find_exiftool_executable()
        self._exiftool_ver = get_exiftool_version(self._exiftool_bin) if self._exiftool_bin else None
        self._master_nodes: List[MetadataNode] = []

        self._setup_ui()

    def _setup_ui(self) -> None:
        self.setStyleSheet("""
            QWidget {
                background-color: #161616;
                color: #e0e0e0;
                font-family: 'Segoe UI', -apple-system, sans-serif;
            }
            QLineEdit#search_edit {
                background-color: #121212;
                border: 1px solid #333333;
                border-radius: 3px;
                padding: 4px 8px;
                color: #ffffff;
                font-size: 11px;
            }
            QLineEdit#search_edit:focus {
                border-color: #e5a823;
            }
            QComboBox#cat_combo {
                background-color: #1f1f1f;
                border: 1px solid #333333;
                border-radius: 3px;
                padding: 4px 10px;
                color: #dddddd;
                font-size: 11px;
                font-weight: bold;
            }
            QComboBox#cat_combo:hover {
                border-color: #555555;
            }
            QComboBox#cat_combo QAbstractItemView {
                background-color: #1f1f1f;
                color: #dddddd;
                selection-background-color: #e5a823;
                selection-color: #111111;
                border: 1px solid #333333;
            }
            QPushButton#btn_action {
                background-color: #222222;
                border: 1px solid #383838;
                border-radius: 3px;
                padding: 4px 10px;
                color: #cccccc;
                font-size: 11px;
                font-weight: bold;
            }
            QPushButton#btn_action:hover {
                background-color: #303030;
                border-color: #e5a823;
                color: #ffffff;
            }
            QTreeWidget#attr_tree {
                background-color: #121212;
                border: 1px solid #2a2a2a;
                font-size: 11px;
                alternate-background-color: #171717;
            }
            QTreeWidget#attr_tree::item {
                padding: 3px 2px;
            }
            QTreeWidget#attr_tree::item:selected {
                background-color: #2a3d52;
                color: #ffffff;
            }
            QHeaderView::section {
                background-color: #1a1a1a;
                color: #aaaaaa;
                padding: 5px;
                border: 1px solid #242424;
                font-weight: bold;
                font-size: 10px;
            }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        # 1. Top Search & Filter Bar
        top_bar = QHBoxLayout()
        top_bar.setSpacing(6)

        self.search_edit = QLineEdit(self)
        self.search_edit.setObjectName("search_edit")
        self.search_edit.setPlaceholderText("Search attributes or values (e.g. focal, aperture, timecode, rgba)...")
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.textChanged.connect(self._on_filter_changed)
        top_bar.addWidget(self.search_edit, stretch=1)

        self.cat_combo = QComboBox(self)
        self.cat_combo.setObjectName("cat_combo")
        self.cat_combo.addItems([
            "All Categories",
            "Key Attributes",
            "EXR Header",
            "Channels",
            "Camera / Optics",
            "Production / Pipeline",
            "Cryptomatte",
            "ExifTool / EXIF",
            "Streams",
            "File System",
        ])
        self.cat_combo.currentIndexChanged.connect(self._on_filter_changed)
        top_bar.addWidget(self.cat_combo)

        # Expand / Collapse toggle buttons
        self.btn_expand = QPushButton("Expand All", self)
        self.btn_expand.setObjectName("btn_action")
        self.btn_expand.clicked.connect(self.expandAll)
        top_bar.addWidget(self.btn_expand)

        self.btn_collapse = QPushButton("Collapse All", self)
        self.btn_collapse.setObjectName("btn_action")
        self.btn_collapse.clicked.connect(self.collapseAll)
        top_bar.addWidget(self.btn_collapse)

        self.btn_copy_json = QPushButton("Copy JSON", self)
        self.btn_copy_json.setObjectName("btn_action")
        self.btn_copy_json.clicked.connect(self._on_copy_all_json)
        top_bar.addWidget(self.btn_copy_json)

        self.btn_export = QPushButton("Export...", self)
        self.btn_export.setObjectName("btn_action")
        self.btn_export.clicked.connect(self._on_export_file)
        top_bar.addWidget(self.btn_export)

        layout.addLayout(top_bar)

        # 2. Main Attribute Tree
        self.tree = QTreeWidget(self)
        self.tree.setObjectName("attr_tree")
        self.tree.setColumnCount(3)
        self.tree.setHeaderLabels(["Attribute / Tag", "Value", "Type"])
        self.tree.setAlternatingRowColors(True)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._on_context_menu)

        hdr = self.tree.header()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.Interactive)
        self.tree.setColumnWidth(0, 240)
        self.tree.setColumnWidth(2, 90)

        layout.addWidget(self.tree, stretch=1)

        # 3. Bottom Status Strip
        bottom_bar = QHBoxLayout()
        bottom_bar.setContentsMargins(2, 2, 2, 2)
        bottom_bar.setSpacing(12)

        self.lbl_path = QLabel("No media loaded", self)
        self.lbl_path.setStyleSheet("color: #888888; font-size: 11px;")
        bottom_bar.addWidget(self.lbl_path, stretch=1)

        self.lbl_matches = QLabel("0 attributes", self)
        self.lbl_matches.setStyleSheet("color: #e5a823; font-weight: bold; font-size: 11px;")
        bottom_bar.addWidget(self.lbl_matches)

        # ExifTool Engine indicator
        if self._exiftool_bin:
            exif_text = f"● ExifTool Active (v{self._exiftool_ver or 'OK'})"
            exif_style = "color: #98c379; font-size: 11px; font-weight: bold;"
            exif_tip = f"ExifTool binary: {self._exiftool_bin}"
        else:
            exif_text = "○ ExifTool Not Installed (Native Fallback)"
            exif_style = "color: #e5c07b; font-size: 11px;"
            exif_tip = "Install ExifTool or set EXIFTOOL_PATH for enhanced MakerNotes & RAW decoding"

        self.lbl_exif = QLabel(exif_text, self)
        self.lbl_exif.setStyleSheet(exif_style)
        self.lbl_exif.setToolTip(exif_tip)
        bottom_bar.addWidget(self.lbl_exif)

        layout.addLayout(bottom_bar)

    # --------------------------------------------------------------------------
    # Tree Population & Updates
    # --------------------------------------------------------------------------
    def set_media(
        self,
        file_path: Optional[str],
        frame: int = 1,
        context: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        Extract full attribute tree and update widget.
        """
        self._current_path = file_path or ""
        self._current_frame = frame
        self._current_context = context or {}

        if not self._current_path or not os.path.isfile(self._current_path):
            self.tree.clear()
            self.lbl_path.setText(self._current_path or "No media loaded")
            self.lbl_matches.setText("0 attributes")
            return

        self.lbl_path.setText(f"{os.path.basename(self._current_path)} (Frame {frame})")
        self._master_nodes = build_unified_metadata_tree(
            self._current_path,
            context=self._current_context,
            exiftool_bin=self._exiftool_bin,
        )
        self._rebuild_tree()
        self.media_inspected.emit(self._current_path)

    def _rebuild_tree(self) -> None:
        """Populate QTreeWidget items from _master_nodes applying active search and category filters."""
        self.tree.clear()
        search = self.search_edit.text().strip().lower()
        selected_cat = self.cat_combo.currentText()

        total_visible = 0

        for cat_node in self._master_nodes:
            # Check category filter
            if selected_cat != "All Categories":
                if selected_cat == "Key Attributes" and not cat_node.is_pinned:
                    continue
                elif selected_cat != "Key Attributes" and cat_node.category != selected_cat:
                    # Also check if any child matches category
                    if not any(c.category == selected_cat for c in cat_node.children):
                        continue

            cat_item = self._create_tree_item(cat_node, search)
            if cat_item:
                self.tree.addTopLevelItem(cat_item)
                # Expand top-level categories by default
                cat_item.setExpanded(True)
                total_visible += self._count_visible_leaves(cat_item)

        self.lbl_matches.setText(f"{total_visible} attributes")

    def _create_tree_item(self, node: MetadataNode, search: str) -> Optional[MetadataTreeItem]:
        """Recursively build MetadataTreeItem if matching search filter."""
        matches_self = (not search) or (search in node.key.lower()) or (search in str(node.value).lower())

        child_items: List[MetadataTreeItem] = []
        for ch in node.children:
            ci = self._create_tree_item(ch, search)
            if ci:
                child_items.append(ci)

        # If neither self nor any descendant matches, omit
        if not matches_self and not child_items:
            return None

        item = MetadataTreeItem(node)
        for ci in child_items:
            item.addChild(ci)

        if search:
            item.setExpanded(True)

        return item

    def _count_visible_leaves(self, item: QTreeWidgetItem) -> int:
        if item.childCount() == 0:
            return 1
        cnt = 0
        for i in range(item.childCount()):
            cnt += self._count_visible_leaves(item.child(i))
        return cnt

    def _on_filter_changed(self) -> None:
        """Trigger tree rebuild when search text or category combo changes."""
        self._rebuild_tree()

    def expandAll(self) -> None:
        self.tree.expandAll()

    def collapseAll(self) -> None:
        self.tree.collapseAll()

    # --------------------------------------------------------------------------
    # Copy & Export Handlers
    # --------------------------------------------------------------------------
    def _on_copy_all_json(self) -> None:
        """Export full attribute tree to JSON and copy to system clipboard."""
        if not self._master_nodes:
            return
        data = [n.to_dict() for n in self._master_nodes]
        json_str = json.dumps(data, indent=2)
        QApplication.clipboard().setText(json_str)

    def _on_export_file(self) -> None:
        """Save full attribute tree to JSON or formatted text file."""
        if not self._master_nodes:
            return

        default_name = "metadata_export.json"
        if self._current_path:
            base = os.path.splitext(os.path.basename(self._current_path))[0]
            default_name = f"{base}_metadata.json"

        path, flt = QFileDialog.getSaveFileName(
            self,
            "Export Metadata Tree",
            default_name,
            "JSON (*.json);;Text / CSV (*.txt);;All Files (*)",
        )
        if not path:
            return

        try:
            if path.lower().endswith(".json"):
                data = [n.to_dict() for n in self._master_nodes]
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2)
            else:
                lines = []
                def _dump_node(node: MetadataNode, indent: int = 0):
                    prefix = "  " * indent
                    val = f": {node.value}" if node.value not in ("", None) else ""
                    typ = f" [{node.type_name}]" if node.type_name and node.type_name != "category" else ""
                    lines.append(f"{prefix}{node.key}{val}{typ}")
                    for ch in node.children:
                        _dump_node(ch, indent + 1)

                for n in self._master_nodes:
                    _dump_node(n, 0)
                with open(path, "w", encoding="utf-8") as f:
                    f.write("\n".join(lines))
        except Exception as e:
            logger.error("Failed exporting metadata tree to %s: %s", path, e)

    def _on_context_menu(self, pos: QPoint) -> None:
        """Right-click context menu on tree item."""
        item = self.tree.itemAt(pos)
        if not item or not isinstance(item, MetadataTreeItem):
            return

        menu = QMenu(self)
        val = item.text(1)
        key = item.text(0)

        act_copy_val = menu.addAction("Copy Value")
        act_copy_key = menu.addAction("Copy Attribute Name")
        act_copy_row = menu.addAction("Copy Row (Key: Value)")
        menu.addSeparator()
        act_copy_subtree = menu.addAction("Copy Subtree as JSON")

        action = menu.exec(self.tree.viewport().mapToGlobal(pos))
        if action == act_copy_val and val:
            QApplication.clipboard().setText(val)
        elif action == act_copy_key and key:
            QApplication.clipboard().setText(key)
        elif action == act_copy_row:
            QApplication.clipboard().setText(f"{key}: {val}")
        elif action == act_copy_subtree:
            sub_dict = item.node.to_dict()
            QApplication.clipboard().setText(json.dumps(sub_dict, indent=2))

    def keyPressEvent(self, event) -> None:
        """Handle standard Ctrl+C keyboard copy on selected tree items."""
        if event.matches(QKeySequence.StandardKey.Copy):
            items = self.tree.selectedItems()
            if items:
                lines = []
                for it in items:
                    lines.append(f"{it.text(0)}: {it.text(1)}")
                QApplication.clipboard().setText("\n".join(lines))
                event.accept()
                return
        super().keyPressEvent(event)
