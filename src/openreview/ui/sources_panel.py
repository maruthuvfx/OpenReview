"""
Sources and Session Manager Dock Panel for OpenReview.
RV / Premiere-styled hierarchical media browser, folder manager,
multi-layer image stacker, editorial sequence builder, and multi-tile layout editor.
"""
from __future__ import annotations

import os
import sys
import subprocess
from typing import Optional, List, Dict, Any, Tuple
from pathlib import Path

from PySide6.QtCore import Qt, Signal, QPoint, QPointF, QRect, QSize, QMimeData, QModelIndex, QTimer, QEvent, QUrl
from PySide6.QtGui import QIcon, QAction, QCursor, QDragEnterEvent, QDropEvent, QPainter, QColor, QPen, QKeyEvent
from PySide6.QtWidgets import (
    QApplication,
    QWidget,
    QDockWidget,
    QVBoxLayout,
    QHBoxLayout,
    QGridLayout,
    QFormLayout,
    QTreeWidget,
    QTreeWidgetItem,
    QTabWidget,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QLabel,
    QLineEdit,
    QSpinBox,
    QDoubleSpinBox,
    QComboBox,
    QSlider,
    QCheckBox,
    QMenu,
    QFileDialog,
    QInputDialog,
    QMessageBox,
    QAbstractItemView,
    QFrame,
    QSplitter,
    QSizePolicy,
)

from openreview.resources import get_branch_closed_icon_path, get_branch_open_icon_path, get_vfx_icon
from openreview.resources.svg_icons import get_svg_icon
from openreview.models.session_graph import (
    SessionGraph,
    SourceItem,
    FolderItem,
    SequenceView,
    SequenceClip,
    StackView,
    LayoutView,
    ViewType,
)
from openreview.core.sequence_parser import detect_image_sequence
from openreview.ui.dock_title_bar import TabbedDockTitleBar


class MediaSourcesTreeWidget(QTreeWidget):
    """QTreeWidget supporting drag & drop of media files/folders directly from OS Explorer/Finder
    as well as internal drag-and-drop parenting of sources, sequences, stacks, layouts and folders."""
    files_dropped = Signal(list)  # list of str file/folder paths
    internal_move_requested = Signal(dict, dict)  # (dragged_data, target_data)

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragDrop)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.setDragEnabled(True)

    def mimeData(self, items: List[QTreeWidgetItem]) -> QMimeData:
        mime = super().mimeData(items)
        if not mime:
            mime = QMimeData()
        urls = []
        paths = []
        source_ids = []
        for item in items:
            data = item.data(0, Qt.ItemDataRole.UserRole)
            if isinstance(data, dict):
                iid = data.get("id")
                if data.get("type") == "source" and iid:
                    source_ids.append(iid)
                # Look up source path from session graph if available
                panel = self.parent()
                while panel and not hasattr(panel, "session_graph") and not hasattr(panel, "graph"):
                    panel = panel.parent() if hasattr(panel, "parent") else None
                graph = getattr(panel, "graph", None) or getattr(panel, "session_graph", None) if panel else None
                if graph and iid:
                    src = graph.sources.get(iid)
                    if src and src.path:
                        paths.append(src.path)
                        urls.append(QUrl.fromLocalFile(src.path))
        if urls:
            mime.setUrls(urls)
        if paths:
            mime.setText(paths[0])
        if source_ids:
            import json
            mime.setData("application/x-openreview-source-ids", json.dumps(source_ids).encode("utf-8"))
        return mime


    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if event.mimeData().hasUrls() or event.source() is self:
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event) -> None:
        if event.mimeData().hasUrls() or event.source() is self:
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event: QDropEvent) -> None:
        if event.mimeData().hasUrls():
            paths = []
            for url in event.mimeData().urls():
                lp = url.toLocalFile()
                if lp and os.path.exists(lp):
                    paths.append(lp)
            if paths:
                event.acceptProposedAction()
                QTimer.singleShot(0, lambda p=paths: self.files_dropped.emit(p))
                return

        if event.source() is self:
            target_item = self.itemAt(event.position().toPoint())
            target_data = target_item.data(0, Qt.ItemDataRole.UserRole) if target_item else {"type": "group", "id": "sources"}
            for item in self.selectedItems():
                src_data = item.data(0, Qt.ItemDataRole.UserRole)
                if src_data:
                    self.internal_move_requested.emit(src_data, target_data)
            event.acceptProposedAction()
            return

        super().dropEvent(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_F2:
            panel = self.parent()
            while panel and not hasattr(panel, "_on_rename_item"):
                panel = panel.parent() if hasattr(panel, "parent") else None
            if panel and hasattr(panel, "_on_rename_item"):
                items = self.selectedItems()
                if items:
                    panel._on_rename_item(items[0])
                    event.accept()
                    return
        super().keyPressEvent(event)


class SourcesPanelWidget(QWidget):
    """
    Core interactive widget for the Sources & Session Manager panel.
    """
    media_load_requested = Signal(list)  # list of file paths
    active_view_changed = Signal(str, str)  # (view_type, view_id)
    view_updated = Signal()
    sources_cleared_to_fresh_start = Signal()
    reload_source_requested = Signal(str)  # source_id
    reload_source_update_requested = Signal(str)  # source_id
    exr_contact_sheet_requested = Signal()
    exr_view_single_requested = Signal()
    exr_export_sheet_requested = Signal()
    exr_show_inspector_requested = Signal()
    exr_metadata_requested = Signal(str)  # exr file path

    def __init__(self, graph: SessionGraph, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.graph = graph
        self.setMinimumWidth(240)
        self._suppress_tree_events = False
        self._source_selection_order: List[str] = []

        self._setup_ui()
        self._connect_signals()
        self.refresh_tree()

    def _setup_ui(self) -> None:
        panel_css = """
            QWidget {
                background-color: #1a1a1a;
                color: #e0e0e0;
                font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            }
            QTreeWidget, QListWidget {
                background-color: #141414;
                border: 1px solid #2e2e2e;
                color: #cccccc;
                font-size: 12px;
            }
            QTreeWidget::item, QListWidget::item {
                padding: 4px 2px;
                border-bottom: 1px solid #1f1f1f;
            }
            QTreeWidget::item:selected, QListWidget::item:selected {
                background-color: #2c281e;
                color: #e5a823;
                font-weight: bold;
            }
            QTreeWidget::item:hover, QListWidget::item:hover {
                background-color: #242424;
            }
            QHeaderView::section {
                background-color: #222222;
                color: #888888;
                padding: 4px;
                border: 1px solid #2e2e2e;
                font-weight: bold;
                font-size: 11px;
            }
            QTabWidget::pane {
                border: 1px solid #333333;
                background-color: #191919;
            }
            QTabBar::tab {
                background-color: #141414;
                color: #888888;
                padding: 5px 16px;
                border: 1px solid #2a2a2a;
                font-size: 11px;
            }
            QTabBar::tab:selected {
                background-color: #282828;
                color: #e5a823;
                font-weight: bold;
                border-bottom: 2px solid #e5a823;
            }
            QPushButton {
                background-color: #282828;
                border: 1px solid #3a3a3a;
                color: #e0e0e0;
                padding: 4px 8px;
                border-radius: 2px;
                font-size: 11px;
            }
            QPushButton:hover {
                background-color: #353535;
                border-color: #e5a823;
            }
            QPushButton:pressed {
                background-color: #e5a823;
                color: #121212;
            }
            QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {
                background-color: #111111;
                border: 1px solid #383838;
                color: #ffffff;
                padding: 3px 5px;
                border-radius: 2px;
                font-size: 11px;
            }
            QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {
                border-color: #e5a823;
            }
        """
        closed_p = get_branch_closed_icon_path().as_posix()
        open_p = get_branch_open_icon_path().as_posix()
        branch_rules = f"""
            QTreeView::branch:has-children:closed,
            QTreeWidget::branch:has-children:closed {{
                border-image: none;
                image: url("{closed_p}");
            }}
            QTreeView::branch:has-children:open,
            QTreeWidget::branch:has-children:open {{
                border-image: none;
                image: url("{open_p}");
            }}
        """
        from openreview.ui.theme import get_spinbox_arrow_stylesheet
        self.setStyleSheet(panel_css + branch_rules + get_spinbox_arrow_stylesheet())

        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(0)

        # Main Vertical Splitter dividing Tree/Tools and Inputs/Edit Tabs
        self.splitter = QSplitter(Qt.Orientation.Vertical, self)
        self.splitter.setHandleWidth(4)

        # Top Section Container: Header Navigation, Tree, and Action Toolbar
        top_container = QWidget(self.splitter)
        top_layout = QVBoxLayout(top_container)
        top_layout.setContentsMargins(4, 4, 4, 4)
        top_layout.setSpacing(4)

        # 1. Header with View Navigation & Title
        header_box = QHBoxLayout()
        header_box.setSpacing(4)
        self.btn_nav_prev = QPushButton(self)
        self.btn_nav_prev.setIcon(get_svg_icon("arrow_left", color_normal="#cccccc", color_active="#e5a823", size=14))
        self.btn_nav_prev.setFixedSize(26, 22)
        self.btn_nav_prev.setToolTip("Previous Viewable")
        self.btn_nav_prev.clicked.connect(self._on_nav_prev)
        header_box.addWidget(self.btn_nav_prev)

        self.lbl_active_title = QLabel("Default Sequence", self)
        self.lbl_active_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_active_title.setStyleSheet("font-weight: bold; color: #e5a823; font-size: 12px;")
        header_box.addWidget(self.lbl_active_title, stretch=1)

        self.btn_nav_next = QPushButton(self)
        self.btn_nav_next.setIcon(get_svg_icon("arrow_right", color_normal="#cccccc", color_active="#e5a823", size=14))
        self.btn_nav_next.setFixedSize(26, 22)
        self.btn_nav_next.setToolTip("Next Viewable")
        self.btn_nav_next.clicked.connect(self._on_nav_next)
        header_box.addWidget(self.btn_nav_next)
        top_layout.addLayout(header_box)

        # 2. Main Tree Widget
        self.tree = MediaSourcesTreeWidget(self)
        self.tree.setHeaderLabels(["Name"])
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._on_tree_context_menu)
        self.tree.itemSelectionChanged.connect(self._on_tree_selection_changed)
        self.tree.itemClicked.connect(self._on_tree_item_clicked)
        self.tree.itemDoubleClicked.connect(self._on_tree_item_double_clicked)
        self.tree.files_dropped.connect(lambda paths: self.media_load_requested.emit(paths))
        self.tree.internal_move_requested.connect(self._on_internal_move)
        top_layout.addWidget(self.tree, stretch=1)

        # 2.5 Dedicated Multi-Channel EXR Properties Bar (Red marked area)
        self.exr_properties_bar = QFrame(self)
        self.exr_properties_bar.setStyleSheet("""
            QFrame {
                background-color: #171717;
                border: 1px solid #2a2a2a;
                border-radius: 3px;
                padding: 2px 4px;
            }
            QPushButton {
                background-color: #242424;
                border: 1px solid #383838;
                color: #dcdcdc;
                padding: 3px 6px;
                border-radius: 2px;
                font-size: 10px;
                font-weight: 500;
            }
            QPushButton:hover {
                background-color: #333333;
                border-color: #e5a823;
                color: #ffffff;
            }
            QPushButton:pressed {
                background-color: #e5a823;
                color: #121212;
            }
        """)
        exr_bar_layout = QHBoxLayout(self.exr_properties_bar)
        exr_bar_layout.setContentsMargins(4, 2, 4, 2)
        exr_bar_layout.setSpacing(4)

        self.lbl_exr_badge = QLabel("EXR", self.exr_properties_bar)
        self.lbl_exr_badge.setStyleSheet("font-weight: bold; color: #e5a823; font-size: 10px;")
        exr_bar_layout.addWidget(self.lbl_exr_badge)

        self.btn_exr_contact_sheet = QPushButton("Contact Sheet", self.exr_properties_bar)
        self.btn_exr_contact_sheet.setIcon(get_svg_icon("layout", color_normal="#cccccc", color_active="#e5a823", size=13))
        self.btn_exr_contact_sheet.setToolTip("View all EXR passes in Contact Sheet mode")
        self.btn_exr_contact_sheet.clicked.connect(self.exr_contact_sheet_requested.emit)
        exr_bar_layout.addWidget(self.btn_exr_contact_sheet)

        self.btn_exr_view_single = QPushButton("View Single", self.exr_properties_bar)
        self.btn_exr_view_single.setIcon(get_svg_icon("media_image", color_normal="#cccccc", color_active="#e5a823", size=13))
        self.btn_exr_view_single.setToolTip("Exit contact sheet and return to beauty pass")
        self.btn_exr_view_single.clicked.connect(self.exr_view_single_requested.emit)
        exr_bar_layout.addWidget(self.btn_exr_view_single)

        self.btn_exr_export_sheet = QPushButton("Export Sheet", self.exr_properties_bar)
        self.btn_exr_export_sheet.setIcon(get_svg_icon("export", color_normal="#38d9c6", color_active="#e5a823", size=13))
        self.btn_exr_export_sheet.setToolTip("Export Contact Sheet frame snapshot or sequence")
        self.btn_exr_export_sheet.clicked.connect(self.exr_export_sheet_requested.emit)
        exr_bar_layout.addWidget(self.btn_exr_export_sheet)

        self.btn_exr_inspector = QPushButton("Passes ▾", self.exr_properties_bar)
        self.btn_exr_inspector.setToolTip("Open EXR Multilayer Pass Inspector HUD")
        self.btn_exr_inspector.clicked.connect(self.exr_show_inspector_requested.emit)
        exr_bar_layout.addWidget(self.btn_exr_inspector)

        top_layout.addWidget(self.exr_properties_bar)
        self.exr_properties_bar.hide()

        # 3. Action Toolbar (SVG vector icons)
        tools_box = QHBoxLayout()
        tools_box.setSpacing(3)
        self.btn_import = QPushButton("Import ▾", self)
        self.btn_import.setStyleSheet("font-weight: bold; color: #e5a823; padding: 4px 6px;")
        self.btn_import.setToolTip("Import Footages, Image Sequences, or Directories...")
        self.btn_import.clicked.connect(self._show_import_menu)
        tools_box.addWidget(self.btn_import)
        self.btn_add = self.btn_import

        self.btn_new_folder = QPushButton(self)
        self.btn_new_folder.setIcon(get_svg_icon("folder", color_normal="#e5a823", size=15))
        self.btn_new_folder.setToolTip("New Folder")
        self.btn_new_folder.setFixedSize(26, 24)
        self.btn_new_folder.clicked.connect(self._on_create_folder)
        tools_box.addWidget(self.btn_new_folder)

        self.btn_expand_all = QPushButton(self)
        self.btn_expand_all.setIcon(get_svg_icon("expand", color_normal="#cccccc", size=15))
        self.btn_expand_all.setToolTip("Expand All Folders & Sections")
        self.btn_expand_all.setFixedSize(26, 24)
        self.btn_expand_all.clicked.connect(self.tree.expandAll)
        tools_box.addWidget(self.btn_expand_all)

        self.btn_collapse_all = QPushButton(self)
        self.btn_collapse_all.setIcon(get_svg_icon("collapse", color_normal="#cccccc", size=15))
        self.btn_collapse_all.setToolTip("Collapse All Folders & Sections")
        self.btn_collapse_all.setFixedSize(26, 24)
        self.btn_collapse_all.clicked.connect(self.tree.collapseAll)
        tools_box.addWidget(self.btn_collapse_all)

        self.btn_delete = QPushButton(self)
        self.btn_delete.setIcon(get_svg_icon("trash", color_normal="#ff6b6b", size=15))
        self.btn_delete.setToolTip("Delete Selected Source or View")
        self.btn_delete.setFixedSize(26, 24)
        self.btn_delete.clicked.connect(self._on_delete_selected)
        tools_box.addWidget(self.btn_delete)

        self.btn_info = QPushButton(self)
        self.btn_info.setIcon(get_svg_icon("info", color_normal="#38d9c6", size=15))
        self.btn_info.setToolTip("Edit Info / Properties")
        self.btn_info.setFixedSize(26, 24)
        self.btn_info.clicked.connect(lambda: self.tabs.setCurrentIndex(1))
        tools_box.addWidget(self.btn_info)

        self.btn_make_active = QPushButton(self)
        self.btn_make_active.setIcon(get_svg_icon("home", color_normal="#e5a823", size=15))
        self.btn_make_active.setToolTip("Set as Active View in Canvas")
        self.btn_make_active.setFixedSize(26, 24)
        self.btn_make_active.clicked.connect(self._on_make_selected_active)
        tools_box.addWidget(self.btn_make_active)

        tools_box.addStretch()

        self.btn_menu_new = QPushButton("▾ New Viewable", self)
        self.btn_menu_new.setStyleSheet("font-weight: bold; color: #e5a823;")
        self.btn_menu_new.clicked.connect(self._show_new_viewable_menu)
        tools_box.addWidget(self.btn_menu_new)

        top_layout.addLayout(tools_box)
        self.splitter.addWidget(top_container)

        # 4. Bottom Dual Subpanels: [Inputs] and [Edit] inside Splitter (Draggable room space)
        self.tabs = QTabWidget(self.splitter)
        self.tabs.setMinimumHeight(130)

        # TAB 1: [Inputs]
        self.tab_inputs = QWidget(self)
        inputs_layout = QVBoxLayout(self.tab_inputs)
        inputs_layout.setContentsMargins(4, 4, 4, 4)
        inputs_layout.setSpacing(4)

        inputs_top_row = QHBoxLayout()
        self.lbl_inputs_view = QLabel("Inputs for: None", self.tab_inputs)
        self.lbl_inputs_view.setStyleSheet("font-size: 11px; color: #aaaaaa;")
        inputs_top_row.addWidget(self.lbl_inputs_view)
        inputs_top_row.addStretch()
        inputs_layout.addLayout(inputs_top_row)

        inputs_body = QHBoxLayout()
        self.list_inputs = QListWidget(self.tab_inputs)
        self.list_inputs.itemSelectionChanged.connect(self._on_input_selected)
        self.list_inputs.itemClicked.connect(self._on_input_item_clicked)
        self.list_inputs.itemDoubleClicked.connect(self._on_input_item_double_clicked)
        self.list_inputs.viewport().installEventFilter(self)
        self.list_inputs.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list_inputs.customContextMenuRequested.connect(self._on_inputs_context_menu)
        inputs_body.addWidget(self.list_inputs, stretch=1)

        # Reorder and delete controls with SVG icons
        order_box = QVBoxLayout()
        order_box.setSpacing(4)
        self.btn_input_up = QPushButton(self.tab_inputs)
        self.btn_input_up.setIcon(get_svg_icon("arrow_up", color_normal="#cccccc", size=14))
        self.btn_input_up.setToolTip("Move Input Up")
        self.btn_input_up.setFixedSize(28, 26)
        self.btn_input_up.clicked.connect(self._on_input_move_up)
        order_box.addWidget(self.btn_input_up)

        self.btn_input_down = QPushButton(self.tab_inputs)
        self.btn_input_down.setIcon(get_svg_icon("arrow_down", color_normal="#cccccc", size=14))
        self.btn_input_down.setToolTip("Move Input Down")
        self.btn_input_down.setFixedSize(28, 26)
        self.btn_input_down.clicked.connect(self._on_input_move_down)
        order_box.addWidget(self.btn_input_down)

        self.btn_input_delete = QPushButton(self.tab_inputs)
        self.btn_input_delete.setIcon(get_svg_icon("trash", color_normal="#ff6b6b", size=14))
        self.btn_input_delete.setToolTip("Remove Input from Viewable")
        self.btn_input_delete.setFixedSize(28, 26)
        self.btn_input_delete.clicked.connect(self._on_input_remove)
        order_box.addWidget(self.btn_input_delete)
        order_box.addStretch()
        inputs_body.addLayout(order_box)
        inputs_layout.addLayout(inputs_body)

        # Stack Specific Comparison Tools (visible when active view is Stack)
        self.stack_tools_frame = QFrame(self.tab_inputs)
        stack_tools_vbox = QVBoxLayout(self.stack_tools_frame)
        stack_tools_vbox.setContentsMargins(0, 2, 0, 0)
        stack_tools_vbox.setSpacing(4)

        # Top row: Onion Skin Mix slider (0-1 float) and Layer A channel selection (R, G, B, A)
        onion_row = QHBoxLayout()
        onion_row.setContentsMargins(0, 0, 0, 0)
        onion_row.setSpacing(4)

        lbl_mix = QLabel("Mix:", self.stack_tools_frame)
        lbl_mix.setStyleSheet("color: #cccccc; font-weight: bold; font-size: 11px;")
        onion_row.addWidget(lbl_mix)

        self.slider_stack_mix = QSlider(Qt.Orientation.Horizontal, self.stack_tools_frame)
        self.slider_stack_mix.setRange(0, 100)
        self.slider_stack_mix.setValue(100)
        self.slider_stack_mix.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.slider_stack_mix.setToolTip("Onion Skin Mix: Controls how much of Layer A mixes over Layer B (0.0 - 1.0)")
        self.slider_stack_mix.valueChanged.connect(self._on_stack_mix_slider_changed)
        onion_row.addWidget(self.slider_stack_mix, stretch=1)

        self.spin_stack_mix = QDoubleSpinBox(self.stack_tools_frame)
        self.spin_stack_mix.setRange(0.0, 1.0)
        self.spin_stack_mix.setSingleStep(0.05)
        self.spin_stack_mix.setDecimals(2)
        self.spin_stack_mix.setValue(1.0)
        self.spin_stack_mix.setFixedWidth(52)
        self.spin_stack_mix.valueChanged.connect(self._on_stack_mix_spin_changed)
        onion_row.addWidget(self.spin_stack_mix)

        stack_tools_vbox.addLayout(onion_row)

        # Bottom row: Set Layer A / B buttons and compare mode dropdown
        layers_row = QHBoxLayout()
        layers_row.setContentsMargins(0, 0, 0, 0)
        layers_row.setSpacing(4)

        self.btn_set_a = QPushButton("Set Layer A", self.stack_tools_frame)
        self.btn_set_a.clicked.connect(self._on_stack_set_a)
        layers_row.addWidget(self.btn_set_a)

        self.btn_set_b = QPushButton("Set Layer B", self.stack_tools_frame)
        self.btn_set_b.clicked.connect(self._on_stack_set_b)
        layers_row.addWidget(self.btn_set_b)

        self.combo_stack_mode = QComboBox(self.stack_tools_frame)
        self.combo_stack_mode.addItems(["Wipe", "A/B Switch", "Difference", "Inverted Diff", "Split", "Over", "Add"])
        self.combo_stack_mode.setCurrentText("A/B Switch")
        self.combo_stack_mode.currentIndexChanged.connect(self._on_stack_mode_changed)
        layers_row.addWidget(self.combo_stack_mode)

        stack_tools_vbox.addLayout(layers_row)
        inputs_layout.addWidget(self.stack_tools_frame)

        self.tabs.addTab(self.tab_inputs, "Inputs")

        # TAB 2: [Edit]
        self.tab_edit = QWidget(self)
        edit_layout = QVBoxLayout(self.tab_edit)
        edit_layout.setContentsMargins(6, 6, 6, 6)
        edit_layout.setSpacing(4)

        # Form for Clip/Source properties
        self.frame_clip_edit = QFrame(self.tab_edit)
        form_clip = QFormLayout(self.frame_clip_edit)
        form_clip.setContentsMargins(0, 0, 0, 0)
        form_clip.setSpacing(4)

        in_out_box = QHBoxLayout()
        in_out_box.setSpacing(4)
        self.spin_in = QSpinBox(self.frame_clip_edit)
        self.spin_in.setRange(0, 999999)
        self.spin_in.valueChanged.connect(self._on_clip_in_changed)
        in_out_box.addWidget(QLabel("In:"))
        in_out_box.addWidget(self.spin_in)

        self.spin_out = QSpinBox(self.frame_clip_edit)
        self.spin_out.setRange(0, 999999)
        self.spin_out.valueChanged.connect(self._on_clip_out_changed)
        in_out_box.addWidget(QLabel("Out:"))
        in_out_box.addWidget(self.spin_out)

        self.btn_clear_in_out = QPushButton("Clear In/Out", self.frame_clip_edit)
        self.btn_clear_in_out.setToolTip("Reset clip in/out range back to original full media duration")
        self.btn_clear_in_out.setStyleSheet("padding: 2px 6px; font-size: 11px;")
        self.btn_clear_in_out.clicked.connect(self._on_clear_clip_in_out)
        in_out_box.addWidget(self.btn_clear_in_out)

        form_clip.addRow("Range:", in_out_box)

        # Start at : selection menu and numeric spinbox
        start_at_box = QHBoxLayout()
        start_at_box.setSpacing(4)
        self.combo_start_at = QComboBox(self.frame_clip_edit)
        self.combo_start_at.addItem("Custom", "custom")
        self.combo_start_at.addItem("Frame 1", 1)
        self.combo_start_at.addItem("Frame 1001", 1001)
        self.combo_start_at.addItem("Current Playhead", "playhead")
        self.combo_start_at.addItem("Source Original", "original")
        self.combo_start_at.currentIndexChanged.connect(self._on_start_at_combo_changed)
        start_at_box.addWidget(self.combo_start_at, stretch=2)

        self.spin_start_at = QSpinBox(self.frame_clip_edit)
        self.spin_start_at.setRange(0, 999999)
        self.spin_start_at.setValue(1001)
        self.spin_start_at.valueChanged.connect(self._on_start_at_spin_changed)
        start_at_box.addWidget(self.spin_start_at, stretch=1)
        form_clip.addRow("Start at :", start_at_box)

        fps_box = QHBoxLayout()
        self.spin_fps = QDoubleSpinBox(self.frame_clip_edit)
        self.spin_fps.setRange(1.0, 240.0)
        self.spin_fps.setValue(24.0)
        self.spin_fps.valueChanged.connect(self._on_clip_fps_changed)
        fps_box.addWidget(self.spin_fps)

        for preset_fps in (24.0, 25.0, 30.0, 60.0):
            p_btn = QPushButton(f"{int(preset_fps)}", self.frame_clip_edit)
            p_btn.setFixedWidth(28)
            p_btn.clicked.connect(lambda chk=False, f=preset_fps: self.spin_fps.setValue(f))
            fps_box.addWidget(p_btn)
        form_clip.addRow("FPS:", fps_box)

        self.combo_colorspace = QComboBox(self.frame_clip_edit)
        from openreview.color.standard_colorspaces import STANDARD_COLORSPACES
        for cs in STANDARD_COLORSPACES:
            self.combo_colorspace.addItem(cs.label, cs.id)
        self.combo_colorspace.currentIndexChanged.connect(self._on_clip_colorspace_changed)
        form_clip.addRow("Colorspace:", self.combo_colorspace)

        self.chk_premultiplied = QCheckBox("Premultiplied", self.frame_clip_edit)
        self.chk_premultiplied.setToolTip("Enable Premultiplied alpha composite (GL_ONE) or uncheck for Straight alpha (GL_SRC_ALPHA).")
        self.chk_premultiplied.toggled.connect(self._on_clip_premultiplied_changed)
        form_clip.addRow("Alpha:", self.chk_premultiplied)

        self.lbl_res_info = QLabel("Resolution: -", self.frame_clip_edit)
        self.lbl_res_info.setStyleSheet("color: #aaaaaa; font-size: 11px;")
        form_clip.addRow("", self.lbl_res_info)

        self.lbl_codec_info = QLabel("Codec: -", self.frame_clip_edit)
        self.lbl_codec_info.setStyleSheet("color: #888888; font-size: 11px;")
        form_clip.addRow("", self.lbl_codec_info)

        self.btn_inspect_details = QPushButton("Inspect Full Details (I)", self.frame_clip_edit)
        self.btn_inspect_details.setIcon(get_vfx_icon("search"))
        self.btn_inspect_details.setStyleSheet("color: #e5a823; font-weight: bold; padding: 4px; margin-top: 4px;")
        self.btn_inspect_details.setToolTip("Open Source Media Details floating panel (I)")
        self.btn_inspect_details.clicked.connect(self._on_open_source_details)
        form_clip.addRow("", self.btn_inspect_details)

        edit_layout.addWidget(self.frame_clip_edit)

        # Form for Layout properties (Scale %)
        self.frame_layout_edit = QFrame(self.tab_edit)
        form_lay = QFormLayout(self.frame_layout_edit)
        form_lay.setContentsMargins(0, 0, 0, 0)
        form_lay.setSpacing(4)

        self.combo_scale_pct = QComboBox(self.frame_layout_edit)
        self.combo_scale_pct.addItems(["100%", "50%", "25%", "10%"])
        self.combo_scale_pct.currentIndexChanged.connect(self._on_layout_scale_changed)
        form_lay.addRow("Layout Scale %:", self.combo_scale_pct)

        self.combo_layout_type = QComboBox(self.frame_layout_edit)
        self.combo_layout_type.addItems(["Auto-Fit Grid", "Side-by-Side", "Over-Under", "2x2 Grid", "3x3 Grid"])
        self.combo_layout_type.currentIndexChanged.connect(self._on_layout_type_changed)
        form_lay.addRow("Grid Arrangement:", self.combo_layout_type)

        self.combo_layout_align = QComboBox(self.frame_layout_edit)
        self.combo_layout_align.addItem("Frame 1 (Synchronized)", "frame_1")
        self.combo_layout_align.addItem("Min Source Frame", "min_source")
        self.combo_layout_align.addItem("Source Native Frames", "native")
        self.combo_layout_align.currentIndexChanged.connect(self._on_layout_align_changed)
        form_lay.addRow("Align Start at :", self.combo_layout_align)
        edit_layout.addWidget(self.frame_layout_edit)

        edit_layout.addStretch()
        self.tabs.addTab(self.tab_edit, "Edit")

        self.splitter.addWidget(self.tabs)
        self.splitter.setStretchFactor(0, 3)
        self.splitter.setStretchFactor(1, 2)
        layout.addWidget(self.splitter)

    def _connect_signals(self) -> None:
        self.graph.graph_changed.connect(self.refresh_tree)
        self.graph.active_view_changed.connect(self._on_active_view_changed)

    # --------------------------------------------------------------------------
    # Tree Population & Management
    # --------------------------------------------------------------------------
    def _populate_section(
        self,
        root_item: QTreeWidgetItem,
        folder_type: str,
        item_type: str,
        items_dict: dict,
        label_func,
        icon_func=None,
        expanded_states: Optional[Dict[str, bool]] = None,
    ) -> None:
        """Populate hierarchical folders and items for a specific section (source, sequence, stack, layout)."""
        section_folders = {
            fid: f for fid, f in self.graph.folders.items()
            if getattr(f, "folder_type", "source") == folder_type
        }

        folder_widgets = {}
        for fid, f in section_folders.items():
            f_item = QTreeWidgetItem([f.name])
            f_item.setIcon(0, get_vfx_icon("folder"))
            f_item.setData(0, Qt.ItemDataRole.UserRole, {"type": "folder", "id": fid, "folder_type": folder_type})
            f_item.setFlags(f_item.flags() | Qt.ItemFlag.ItemIsDragEnabled | Qt.ItemFlag.ItemIsDropEnabled)
            folder_widgets[fid] = f_item

        # Wire folder parent-child hierarchy
        for fid, f in section_folders.items():
            f_item = folder_widgets[fid]
            if f.parent_id and f.parent_id in folder_widgets and f.parent_id != fid:
                folder_widgets[f.parent_id].addChild(f_item)
            else:
                root_item.addChild(f_item)

        # Place items inside their parent folder or directly under root section
        for iid, obj in items_dict.items():
            text = label_func(obj)
            item_widget = QTreeWidgetItem([text])
            if icon_func is not None:
                try:
                    ic = icon_func(obj)
                    if ic and not ic.isNull():
                        item_widget.setIcon(0, ic)
                except Exception:
                    pass
            item_widget.setData(0, Qt.ItemDataRole.UserRole, {"type": item_type, "id": iid})
            item_widget.setFlags(item_widget.flags() | Qt.ItemFlag.ItemIsDragEnabled | Qt.ItemFlag.ItemIsDropEnabled)
            parent_fid = getattr(obj, "parent_folder_id", None)
            if parent_fid and parent_fid in folder_widgets:
                folder_widgets[parent_fid].addChild(item_widget)
            else:
                root_item.addChild(item_widget)

        # Apply expansion state after items and hierarchy are fully attached
        if expanded_states is not None:
            for fid, f_item in folder_widgets.items():
                f_item.setExpanded(expanded_states.get(fid, True))
        else:
            for f_item in folder_widgets.values():
                f_item.setExpanded(True)

    def refresh_tree(self) -> None:
        """Rebuild the hierarchical Sources, Sequences, Stacks, Layouts tree with folders and distinct category icons."""
        self._suppress_tree_events = True

        # Capture expanded state of groups and folders before clearing
        expanded_states: Dict[str, bool] = {}
        def _capture_expansion(item: QTreeWidgetItem) -> None:
            d = item.data(0, Qt.ItemDataRole.UserRole)
            if d and "id" in d:
                expanded_states[d["id"]] = item.isExpanded()
            for idx in range(item.childCount()):
                _capture_expansion(item.child(idx))

        for top_idx in range(self.tree.topLevelItemCount()):
            _capture_expansion(self.tree.topLevelItem(top_idx))

        self.tree.clear()

        # 1. Root Group: SOURCES
        self.root_sources = QTreeWidgetItem(self.tree, ["SOURCES"])
        self.root_sources.setExpanded(expanded_states.get("sources", True))
        self.root_sources.setData(0, Qt.ItemDataRole.UserRole, {"type": "group", "id": "sources"})
        self.root_sources.setFlags((self.root_sources.flags() | Qt.ItemFlag.ItemIsDropEnabled) & ~Qt.ItemFlag.ItemIsDragEnabled)

        def _source_label(s: SourceItem) -> str:
            return s.name

        def _source_icon(s: SourceItem) -> QIcon:
            if s.media_type == "video":
                return get_vfx_icon("media_video")
            elif s.media_type == "sequence":
                return get_vfx_icon("media_sequence")
            return get_vfx_icon("media_image")

        self._populate_section(
            self.root_sources, "source", "source", self.graph.sources,
            _source_label, _source_icon, expanded_states
        )

        # 2. Root Group: SEQUENCES
        self.root_sequences = QTreeWidgetItem(self.tree, ["SEQUENCES"])
        self.root_sequences.setExpanded(expanded_states.get("sequences", True))
        self.root_sequences.setData(0, Qt.ItemDataRole.UserRole, {"type": "group", "id": "sequences"})
        self.root_sequences.setFlags((self.root_sequences.flags() | Qt.ItemFlag.ItemIsDropEnabled) & ~Qt.ItemFlag.ItemIsDragEnabled)
        self._populate_section(
            self.root_sequences, "sequence", "sequence", self.graph.sequences,
            lambda q: f"{q.name} ({len(q.clips)} clips)",
            lambda q: get_vfx_icon("media_sequence"),
            expanded_states
        )

        # 3. Root Group: STACKS
        self.root_stacks = QTreeWidgetItem(self.tree, ["STACKS"])
        self.root_stacks.setExpanded(expanded_states.get("stacks", True))
        self.root_stacks.setData(0, Qt.ItemDataRole.UserRole, {"type": "group", "id": "stacks"})
        self.root_stacks.setFlags((self.root_stacks.flags() | Qt.ItemFlag.ItemIsDropEnabled) & ~Qt.ItemFlag.ItemIsDragEnabled)
        self._populate_section(
            self.root_stacks, "stack", "stack", self.graph.stacks,
            lambda k: f"{k.name} ({len(k.source_ids)} layers)",
            lambda k: get_vfx_icon("media_stack"),
            expanded_states
        )

        # 4. Root Group: LAYOUTS
        self.root_layouts = QTreeWidgetItem(self.tree, ["LAYOUTS"])
        self.root_layouts.setExpanded(expanded_states.get("layouts", True))
        self.root_layouts.setData(0, Qt.ItemDataRole.UserRole, {"type": "group", "id": "layouts"})
        self.root_layouts.setFlags((self.root_layouts.flags() | Qt.ItemFlag.ItemIsDropEnabled) & ~Qt.ItemFlag.ItemIsDragEnabled)
        self._populate_section(
            self.root_layouts, "layout", "layout", self.graph.layouts,
            lambda l: f"{l.name} ({len(l.source_ids)} views, {l.scale_pct}%)",
            lambda l: get_vfx_icon("layout"),
            expanded_states
        )

        self._suppress_tree_events = False
        self._update_header_title()
        self._refresh_subpanels()

    def _update_header_title(self) -> None:
        self.lbl_active_title.setText(self.graph.get_active_view_name())

    # --------------------------------------------------------------------------
    # Tree Events & Context Menu
    # --------------------------------------------------------------------------
    def _on_tree_selection_changed(self) -> None:
        if self._suppress_tree_events:
            return
        curr_selected = set()
        for item in self.tree.selectedItems():
            d = item.data(0, Qt.ItemDataRole.UserRole)
            if d and d.get("type") == "source" and "id" in d:
                curr_selected.add(d["id"])
        if hasattr(self, "_source_selection_order"):
            self._source_selection_order = [sid for sid in self._source_selection_order if sid in curr_selected]
            for sid in curr_selected:
                if sid not in self._source_selection_order:
                    self._source_selection_order.append(sid)
        self._refresh_subpanels()

    def _on_tree_item_clicked(self, item: QTreeWidgetItem, column: int) -> None:
        """Single click selects the media/folder and updates subpanels; toggles folders/groups."""
        if self._suppress_tree_events:
            return
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if data:
            item_type = data.get("type")
            if item_type in ("group", "folder"):
                item.setExpanded(not item.isExpanded())
        else:
            item.setExpanded(not item.isExpanded())
        self._refresh_subpanels()

    def _on_tree_item_double_clicked(self, item: QTreeWidgetItem, column: int) -> None:
        """Double clicking a source or viewable sets it to active ('Select current view')."""
        if self._suppress_tree_events:
            return
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if not data:
            return
        item_type = data.get("type")
        item_id = data.get("id")

        if item_type in ("source", "sequence", "stack", "layout"):
            self.graph.set_active_view(ViewType(item_type), item_id)
            self._update_header_title()
            self._refresh_subpanels()
            self.active_view_changed.emit(item_type, item_id)
        elif item_type in ("group", "folder"):
            item.setExpanded(not item.isExpanded())

    def _find_item_by_id(self, item_id: str, root_item: Optional[QTreeWidgetItem] = None) -> Optional[QTreeWidgetItem]:
        """Recursive helper to locate a tree widget item by its graph UUID."""
        if root_item is None:
            for i in range(self.tree.topLevelItemCount()):
                res = self._find_item_by_id(item_id, self.tree.topLevelItem(i))
                if res:
                    return res
            return None
        data = root_item.data(0, Qt.ItemDataRole.UserRole)
        if data and data.get("id") == item_id:
            return root_item
        for i in range(root_item.childCount()):
            res = self._find_item_by_id(item_id, root_item.child(i))
            if res:
                return res
        return None

    def _on_rename_item(self, item: Optional[QTreeWidgetItem] = None) -> None:
        """Prompt user with modal dialog to rename source, folder, sequence, stack, or layout."""
        if not item:
            items = self.tree.selectedItems()
            if items:
                item = items[0]
        if not item:
            return

        data = item.data(0, Qt.ItemDataRole.UserRole)
        if not data:
            return
        item_type = data.get("type")
        item_id = data.get("id")

        if item_type == "group":
            return

        target_obj = None
        if item_type == "source":
            target_obj = self.graph.sources.get(item_id)
        elif item_type == "folder":
            target_obj = self.graph.folders.get(item_id)
        elif item_type == "sequence":
            target_obj = self.graph.sequences.get(item_id)
        elif item_type == "stack":
            target_obj = self.graph.stacks.get(item_id)
        elif item_type == "layout":
            target_obj = self.graph.layouts.get(item_id)

        if not target_obj:
            return

        current_name = getattr(target_obj, "name", "")
        type_title = item_type.capitalize()
        new_name, ok = QInputDialog.getText(
            self,
            f"Rename {type_title}",
            f"Enter new name for {type_title}:",
            QLineEdit.EchoMode.Normal,
            current_name,
        )
        if not ok:
            return

        new_name = new_name.strip()
        if not new_name or new_name == current_name:
            return

        target_obj.name = new_name

        if item_type == "source":
            self.graph.source_properties_changed.emit(item_id)
        elif item_type in ("sequence", "stack", "layout"):
            self.graph.view_properties_changed.emit(item_type, item_id)

        self.graph.graph_changed.emit()

        self.refresh_tree()
        self._update_header_title()
        self._refresh_subpanels()

        renamed_item = self._find_item_by_id(item_id)
        if renamed_item:
            self.tree.setCurrentItem(renamed_item)

    def _on_tree_context_menu(self, pos: QPoint) -> None:
        clicked_item = self.tree.itemAt(pos)
        if clicked_item and clicked_item not in self.tree.selectedItems():
            self.tree.clearSelection()
            clicked_item.setSelected(True)
            self.tree.setCurrentItem(clicked_item)

        menu = QMenu(self)

        # Selected items
        selected_nodes = self._get_selected_nodes()
        selected_sources = [d["id"] for d in selected_nodes if d.get("type") == "source"]

        # 1. Rename (for single non-group item)
        if len(selected_nodes) == 1 and selected_nodes[0].get("type") != "group":
            target_item = clicked_item or (self.tree.selectedItems()[0] if self.tree.selectedItems() else None)
            act_rename = menu.addAction(get_vfx_icon("rename"), "Rename (F2)")
            act_rename.triggered.connect(lambda: self._on_rename_item(target_item))
            menu.addSeparator()

        # 2. New Folder
        act_folder = menu.addAction(get_vfx_icon("folder"), "New Folder")
        act_folder.triggered.connect(self._on_create_folder)

        # 3. Reveal in Explorer (for Source Media items)
        if selected_sources:
            act_reveal = menu.addAction(get_vfx_icon("reveal"), "Reveal in Explorer")
            act_reveal.triggered.connect(lambda: self._on_reveal_in_explorer(selected_sources[0]))

        # 4. New Viewable Submenu
        view_menu = menu.addMenu(get_vfx_icon("layout"), "New Viewable")
        act_seq = view_menu.addAction(get_vfx_icon("media_sequence"), "Sequence")
        act_seq.triggered.connect(lambda: self._create_viewable_from_selection("sequence"))

        act_stack = view_menu.addAction(get_vfx_icon("media_stack"), "Stack (A/B & Difference)")
        act_stack.triggered.connect(lambda: self._create_viewable_from_selection("stack"))

        act_layout = view_menu.addAction(get_vfx_icon("layout"), "Layout")
        act_layout.triggered.connect(lambda: self._create_viewable_from_selection("layout"))

        menu.addSeparator()

        # 5. Delete View / Source
        if selected_nodes and selected_nodes[0].get("type") != "group":
            act_del = menu.addAction(get_vfx_icon("delete"), "Delete")
            act_del.triggered.connect(self._on_delete_selected)

        # 6. Make Active
        if len(selected_nodes) == 1 and selected_nodes[0].get("type") in ("source", "sequence", "stack", "layout"):
            act_active = menu.addAction(get_vfx_icon("view_home"), "Select Current View")
            act_active.triggered.connect(self._on_make_selected_active)

        # 7. Reload Media & Reload Update
        if selected_sources:
            src_id = selected_sources[0]
            menu.addSeparator()
            act_reload = menu.addAction(get_vfx_icon("refresh"), "Reload Media")
            act_reload.triggered.connect(lambda: self.reload_source_requested.emit(src_id))

            act_reload_up = menu.addAction(get_vfx_icon("reload_update"), "Reload Update")
            act_reload_up.triggered.connect(lambda: self.reload_source_update_requested.emit(src_id))

            # 8. EXR Multi-Channel Review Actions
            src_item = self.graph.sources.get(src_id)
            src_p = getattr(src_item, "path", getattr(src_item, "file_path", "")) if src_item else ""
            if src_p and str(src_p).lower().endswith(".exr"):
                menu.addSeparator()
                act_cs = menu.addAction(get_vfx_icon("layout"), "View Contact Sheet")
                act_cs.triggered.connect(self.exr_contact_sheet_requested.emit)

                act_vs = menu.addAction(get_vfx_icon("media_image"), "View Single (Beauty)")
                act_vs.triggered.connect(self.exr_view_single_requested.emit)

                act_exp_cs = menu.addAction(get_vfx_icon("export"), "Export Contact Sheet...")
                act_exp_cs.triggered.connect(self.exr_export_sheet_requested.emit)

                act_meta = menu.addAction(get_vfx_icon("reveal"), "EXR Metadata & Header Details...")
                act_meta.triggered.connect(lambda p=src_p: self.exr_metadata_requested.emit(str(p)))

        self._execute_menu(menu, self.tree.viewport().mapToGlobal(pos))

    def _execute_menu(self, menu: QMenu, pos: QPoint) -> None:
        menu.exec(pos)

    def _show_new_viewable_menu(self) -> None:
        menu = QMenu(self)
        act_seq = menu.addAction(get_vfx_icon("media_sequence"), "Sequence from Selection")
        act_seq.triggered.connect(lambda: self._create_viewable_from_selection("sequence"))
        act_stack = menu.addAction(get_vfx_icon("media_stack"), "Stack from Selection")
        act_stack.triggered.connect(lambda: self._create_viewable_from_selection("stack"))
        act_layout = menu.addAction(get_vfx_icon("layout"), "Layout from Selection")
        act_layout.triggered.connect(lambda: self._create_viewable_from_selection("layout"))
        menu.exec(QCursor.pos())

    def _get_selected_nodes(self) -> List[Dict[str, Any]]:
        nodes = []
        for item in self.tree.selectedItems():
            d = item.data(0, Qt.ItemDataRole.UserRole)
            if d:
                nodes.append(d)
        return nodes

    def _get_selected_source_ids(self) -> List[str]:
        curr_selected = set(d["id"] for d in self._get_selected_nodes() if d.get("type") == "source")
        if hasattr(self, "_source_selection_order") and self._source_selection_order:
            ordered = [sid for sid in self._source_selection_order if sid in curr_selected]
            for sid in curr_selected:
                if sid not in ordered:
                    ordered.append(sid)
            return ordered
        return [d["id"] for d in self._get_selected_nodes() if d.get("type") == "source"]

    # --------------------------------------------------------------------------
    # Viewable Creation Actions
    # --------------------------------------------------------------------------
    def _create_viewable_from_selection(self, kind: str) -> None:
        sids = self._get_selected_source_ids()
        if not sids and self.graph.sources:
            # Fallback to all sources if none selected
            sids = list(self.graph.sources.keys())

        if kind == "sequence":
            name, ok = QInputDialog.getText(self, "New Sequence", "Sequence Name:", text="New Sequence")
            if ok and name.strip():
                seq = self.graph.create_sequence(name=name.strip(), source_ids=sids)
                self.graph.set_active_view(ViewType.SEQUENCE, seq.id)
                self.refresh_tree()
        elif kind == "stack":
            name, ok = QInputDialog.getText(self, "New Stack", "Stack Name:", text="New Stack")
            if ok and name.strip():
                stk = self.graph.create_stack(name=name.strip(), source_ids=sids)
                self.graph.set_active_view(ViewType.STACK, stk.id)
                self.refresh_tree()
        elif kind == "layout":
            name, ok = QInputDialog.getText(self, "New Layout", "Layout Name:", text="New Layout")
            if ok and name.strip():
                lay = self.graph.create_layout(name=name.strip(), source_ids=sids, scale_pct=100)
                self.graph.set_active_view(ViewType.LAYOUT, lay.id)
                self.refresh_tree()

    def _on_create_folder(self) -> None:
        selected_nodes = self._get_selected_nodes()
        target_folder_type = "source"
        parent_folder_id = None

        if selected_nodes:
            node = selected_nodes[0]
            ntype = node.get("type")
            nid = node.get("id")
            if ntype == "group":
                if nid in ("sources", "sequences", "stacks", "layouts"):
                    target_folder_type = nid.rstrip("s")
            elif ntype == "folder":
                f = self.graph.folders.get(nid)
                if f:
                    target_folder_type = getattr(f, "folder_type", "source")
                    parent_folder_id = nid
            elif ntype in ("source", "sequence", "stack", "layout"):
                target_folder_type = ntype
                if ntype == "source":
                    s = self.graph.sources.get(nid)
                    parent_folder_id = s.parent_folder_id if s else None
                elif ntype == "sequence":
                    q = self.graph.sequences.get(nid)
                    parent_folder_id = q.parent_folder_id if q else None
                elif ntype == "stack":
                    k = self.graph.stacks.get(nid)
                    parent_folder_id = k.parent_folder_id if k else None
                elif ntype == "layout":
                    l = self.graph.layouts.get(nid)
                    parent_folder_id = l.parent_folder_id if l else None

        name, ok = QInputDialog.getText(
            self,
            "New Folder",
            f"New {target_folder_type.capitalize()} Folder Name:",
            text="New Folder",
        )
        if ok and name.strip():
            folder = self.graph.create_folder(
                name=name.strip(),
                parent_folder_id=parent_folder_id,
                folder_type=target_folder_type,
            )
            # Automatically add selected items matching target_folder_type into the created folder
            for node in selected_nodes:
                nt = node.get("type")
                nid = node.get("id")
                if nt == target_folder_type and nid:
                    self.graph.move_item_to_folder(nt, nid, folder.id)
            self.refresh_tree()

    def _on_reveal_in_explorer(self, source_id: str) -> None:
        """Reveal selected source media file or first frame of sequence in OS File Explorer."""
        source = self.graph.sources.get(source_id)
        if not source or not source.path:
            return

        target_path = source.path
        if source.media_type == "sequence" or "#" in target_path or "%" in target_path or not os.path.exists(target_path):
            try:
                seq_info = detect_image_sequence(source.path)
                if seq_info and seq_info.available_frames:
                    first_frame = min(seq_info.available_frames)
                    first_path = seq_info.frame_path(first_frame)
                    if os.path.exists(first_path):
                        target_path = first_path
            except Exception:
                pass

        from openreview.core.system_utils import reveal_in_os_file_manager
        success = reveal_in_os_file_manager(target_path)
        if not success:
            QMessageBox.warning(self, "Reveal in Explorer", f"Unable to reveal in explorer:\n{target_path}")

    def _on_delete_selected(self) -> None:
        for node in self._get_selected_nodes():
            ntype = node.get("type")
            nid = node.get("id")
            if ntype == "source":
                self.graph.delete_source(nid)
            elif ntype == "folder":
                self.graph.delete_folder(nid)
            elif ntype in ("sequence", "stack", "layout"):
                self.graph.delete_view(ViewType(ntype), nid)

        self.refresh_tree()
        if (
            len(self.graph.sources) == 0
            and len(self.graph.sequences) == 0
            and len(self.graph.stacks) == 0
            and len(self.graph.layouts) == 0
        ):
            self.sources_cleared_to_fresh_start.emit()
        else:
            self.view_updated.emit()

    def _on_internal_move(self, src_data: dict, target_data: dict) -> None:
        """Handle internal drag-and-drop parenting of items and folders."""
        src_type = src_data.get("type")
        src_id = src_data.get("id")
        target_type = target_data.get("type")
        target_id = target_data.get("id")

        if not src_type or not src_id or src_id == target_id:
            return

        dest_folder_id = None
        if target_type == "folder":
            dest_folder_id = target_id
        elif target_type == "group":
            dest_folder_id = None
        elif target_type in ("source", "sequence", "stack", "layout"):
            if target_type == "source":
                s = self.graph.sources.get(target_id)
                dest_folder_id = s.parent_folder_id if s else None
            elif target_type == "sequence":
                q = self.graph.sequences.get(target_id)
                dest_folder_id = q.parent_folder_id if q else None
            elif target_type == "stack":
                k = self.graph.stacks.get(target_id)
                dest_folder_id = k.parent_folder_id if k else None
            elif target_type == "layout":
                l = self.graph.layouts.get(target_id)
                dest_folder_id = l.parent_folder_id if l else None

        if src_type == "folder":
            self.graph.move_folder_to_folder(src_id, dest_folder_id)
        elif src_type in ("source", "sequence", "stack", "layout"):
            self.graph.move_item_to_folder(src_type, src_id, dest_folder_id)

        self.refresh_tree()
        self.view_updated.emit()

    def _show_import_menu(self) -> None:
        menu = QMenu(self)
        act_files = menu.addAction(get_vfx_icon("media_video"), "Import Media Files...")
        act_files.triggered.connect(self._on_add_media_files)

        act_seq = menu.addAction(get_vfx_icon("media_sequence"), "Import Image Sequence...")
        act_seq.triggered.connect(self._on_import_image_sequence)

        act_dir = menu.addAction(get_vfx_icon("folder"), "Import Directory / Footage Folder...")
        act_dir.triggered.connect(self._on_import_directory)

        menu.exec(QCursor.pos())

    def _on_import_image_sequence(self) -> None:
        from openreview.core.image_io import IMAGE_EXTENSIONS
        img_globs = " ".join(f"*{ext}" for ext in sorted(IMAGE_EXTENSIONS))
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Any Frame from Sequence",
            "",
            f"Image Sequences ({img_globs});;All Files (*.*)",
        )
        if file_path:
            self.graph.add_sources_from_paths([file_path])
            self.media_load_requested.emit([file_path])

    def _on_import_directory(self) -> None:
        dir_path = QFileDialog.getExistingDirectory(
            self,
            "Select Media / Footage Directory",
            "",
            QFileDialog.Option.ShowDirsOnly,
        )
        if dir_path:
            from openreview.core.sequence_parser import detect_image_sequence
            from openreview.core.video_reader import is_video_file
            from openreview.core.image_io import is_image_file
            found = []
            visited_patterns = set()
            try:
                for root, _, files in os.walk(dir_path):
                    for f in sorted(files):
                        full = os.path.join(root, f)
                        if is_video_file(full):
                            found.append(full)
                        else:
                            seq = detect_image_sequence(full)
                            if seq and len(seq.available_frames) > 1:
                                if seq.pattern not in visited_patterns:
                                    visited_patterns.add(seq.pattern)
                                    found.append(full)
                            elif is_image_file(full):
                                found.append(full)
            except Exception:
                pass

            if found:
                self.media_load_requested.emit(found)

    def _on_make_selected_active(self) -> None:
        nodes = self._get_selected_nodes()
        if not nodes:
            return
        node = nodes[0]
        ntype = node.get("type")
        nid = node.get("id")
        if ntype in ("source", "sequence", "stack", "layout"):
            self.graph.set_active_view(ViewType(ntype), nid)
            self._update_header_title()
            self._refresh_subpanels()

    def _on_nav_prev(self) -> None:
        views = self._get_all_viewables_list()
        if not views:
            return
        cur_pair = (self.graph.active_view_type.value, self.graph.active_view_id)
        idx = views.index(cur_pair) if cur_pair in views else 0
        new_pair = views[(idx - 1) % len(views)]
        self.graph.set_active_view(ViewType(new_pair[0]), new_pair[1])

    def _on_nav_next(self) -> None:
        views = self._get_all_viewables_list()
        if not views:
            return
        cur_pair = (self.graph.active_view_type.value, self.graph.active_view_id)
        idx = views.index(cur_pair) if cur_pair in views else 0
        new_pair = views[(idx + 1) % len(views)]
        self.graph.set_active_view(ViewType(new_pair[0]), new_pair[1])

    def _get_all_viewables_list(self) -> List[Tuple[str, str]]:
        out = []
        for sid in self.graph.sources:
            out.append(("source", sid))
        for qid in self.graph.sequences:
            out.append(("sequence", qid))
        for kid in self.graph.stacks:
            out.append(("stack", kid))
        for lid in self.graph.layouts:
            out.append(("layout", lid))
        return out

    def _on_add_media_files(self) -> None:
        from openreview.core.image_io import IMAGE_EXTENSIONS
        from openreview.core.video_reader import VIDEO_EXTENSIONS
        vid_globs = " ".join(f"*{ext}" for ext in sorted(VIDEO_EXTENSIONS)) + " *.mx*n *.mx*p"
        img_globs = " ".join(f"*{ext}" for ext in sorted(IMAGE_EXTENSIONS))
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "Load Review Media Sources",
            "",
            f"All Media ({vid_globs} {img_globs} *.wav *.mp3);;Video Files ({vid_globs});;Image Files ({img_globs});;All Files (*.*)",
        )
        if paths:
            self.media_load_requested.emit(paths)

    # --------------------------------------------------------------------------
    # Subpanels: [Inputs] and [Edit]
    # --------------------------------------------------------------------------
    def _refresh_subpanels(self) -> None:
        """Update [Inputs] and [Edit] tabs to match currently selected or active view."""
        viewable = self.graph.get_active_viewable()
        vtype = self.graph.active_view_type

        # 1. Update [Inputs] Tab
        prev_input_row = self.list_inputs.currentRow()
        self.list_inputs.blockSignals(True)
        self.list_inputs.clear()
        if vtype == ViewType.SEQUENCE and isinstance(viewable, SequenceView):
            self.lbl_inputs_view.setText(f"Sequence: {viewable.name}")
            self.stack_tools_frame.hide()
            for idx, clip in enumerate(viewable.clips):
                src = self.graph.sources.get(clip.source_id)
                name = src.name if src else "Missing Source"
                item = QListWidgetItem(f"{idx + 1}. {name} [{clip.trim_in}-{clip.trim_out}]")
                item.setData(Qt.ItemDataRole.UserRole, idx)
                self.list_inputs.addItem(item)

        elif vtype == ViewType.STACK and isinstance(viewable, StackView):
            self.lbl_inputs_view.setText(f"Stack: {viewable.name}")
            self.stack_tools_frame.show()
            for idx, sid in enumerate(viewable.source_ids):
                src = self.graph.sources.get(sid)
                name = src.name if src else "Missing Source"
                tag = ""
                if idx == viewable.index_a and idx == viewable.index_b:
                    tag += " [A/B]"
                elif idx == viewable.index_a:
                    tag += " [A]"
                elif idx == viewable.index_b:
                    tag += " [B]"
                if idx == viewable.current_layer_index and viewable.compare_mode == "switch":
                    tag += " (Active)"
                item = QListWidgetItem(f"{name}{tag}")
                item.setData(Qt.ItemDataRole.UserRole, idx)
                self.list_inputs.addItem(item)

            if 0 <= prev_input_row < self.list_inputs.count():
                target_row = prev_input_row
            elif viewable.compare_mode == "switch":
                target_row = viewable.current_layer_index
            else:
                target_row = viewable.index_a

            if 0 <= target_row < self.list_inputs.count():
                self.list_inputs.setCurrentRow(target_row)

            # Match mode in combo
            modes_map = {
                "wipe": "Wipe",
                "switch": "A/B Switch",
                "difference": "Difference",
                "difference_inverted": "Inverted Diff",
                "split": "Split",
                "over": "Over",
                "add": "Add",
            }
            cur_mode = modes_map.get(viewable.compare_mode, "A/B Switch")
            idx = self.combo_stack_mode.findText(cur_mode)
            if idx >= 0:
                self.combo_stack_mode.blockSignals(True)
                self.combo_stack_mode.setCurrentIndex(idx)
                self.combo_stack_mode.blockSignals(False)

            # Sync Onion Skin mix and channel checkboxes
            mix_val = getattr(viewable, "stack_mix", 1.0)
            self.slider_stack_mix.blockSignals(True)
            self.slider_stack_mix.setValue(int(round(mix_val * 100)))
            self.slider_stack_mix.blockSignals(False)

            self.spin_stack_mix.blockSignals(True)
            self.spin_stack_mix.setValue(mix_val)
            self.spin_stack_mix.blockSignals(False)

        elif vtype == ViewType.LAYOUT and isinstance(viewable, LayoutView):
            self.lbl_inputs_view.setText(f"Layout: {viewable.name}")
            self.stack_tools_frame.hide()
            for idx, sid in enumerate(viewable.source_ids):
                src = self.graph.sources.get(sid)
                name = src.name if src else "Missing Source"
                item = QListWidgetItem(f"Tile {idx + 1}: {name}")
                item.setData(Qt.ItemDataRole.UserRole, idx)
                self.list_inputs.addItem(item)
        else:
            self.lbl_inputs_view.setText("Single Source View")
            self.stack_tools_frame.hide()
            if isinstance(viewable, SourceItem):
                self.list_inputs.addItem(QListWidgetItem(f"Source: {viewable.name}"))
        self.list_inputs.blockSignals(False)

        # 2. Update [Edit] Tab
        selected_nodes = self._get_selected_nodes()
        target_source: Optional[SourceItem] = None
        target_layout: Optional[LayoutView] = None

        if selected_nodes:
            first = selected_nodes[0]
            if first.get("type") == "source":
                target_source = self.graph.sources.get(first.get("id"))
            elif first.get("type") == "layout":
                target_layout = self.graph.layouts.get(first.get("id"))

        if not target_source:
            target_source = self._get_target_source()
        if not target_layout and isinstance(viewable, LayoutView):
            target_layout = viewable

        # Show/hide forms accordingly
        if target_layout:
            self.frame_layout_edit.show()
            self.frame_clip_edit.hide()
            scale_str = f"{target_layout.scale_pct}%"
            s_idx = self.combo_scale_pct.findText(scale_str)
            if s_idx >= 0:
                self.combo_scale_pct.blockSignals(True)
                self.combo_scale_pct.setCurrentIndex(s_idx)
                self.combo_scale_pct.blockSignals(False)

            type_rev_map = {
                "auto_grid": "Auto-Fit Grid",
                "side_by_side": "Side-by-Side",
                "over_under": "Over-Under",
                "grid_2x2": "2x2 Grid",
                "grid_3x3": "3x3 Grid",
            }
            t_text = type_rev_map.get(target_layout.layout_type, "Auto-Fit Grid")
            t_idx = self.combo_layout_type.findText(t_text)
            if t_idx >= 0:
                self.combo_layout_type.blockSignals(True)
                self.combo_layout_type.setCurrentIndex(t_idx)
                self.combo_layout_type.blockSignals(False)

            align_val = getattr(target_layout, "align_mode", "frame_1")
            a_idx = self.combo_layout_align.findData(align_val)
            if a_idx >= 0:
                self.combo_layout_align.blockSignals(True)
                self.combo_layout_align.setCurrentIndex(a_idx)
                self.combo_layout_align.blockSignals(False)
        else:
            if target_source:
                self._populate_clip_edit(target_source)
            else:
                self.frame_layout_edit.hide()
                self.frame_clip_edit.hide()

    def _on_open_source_details(self) -> None:
        """Trigger Source Media Details panel from Edit tab."""
        w = self.window()
        if w and hasattr(w, "_on_show_source_details"):
            w._on_show_source_details()

    def refresh_properties(self) -> None:
        """Refresh clip / viewable edit inspector for current selection or active viewable."""
        s = self._get_target_source()
        if s:
            self._populate_clip_edit(s)

    def _populate_clip_edit(self, target_source: SourceItem) -> None:
        self.frame_layout_edit.hide()
        self.frame_clip_edit.show()
        if not target_source:
            return

        self.spin_in.blockSignals(True)
        self.spin_out.blockSignals(True)
        self.spin_start_at.blockSignals(True)
        self.spin_fps.blockSignals(True)
        self.combo_colorspace.blockSignals(True)
        if hasattr(self, "chk_premultiplied"):
            self.chk_premultiplied.blockSignals(True)

        self.spin_in.setValue(target_source.start_frame)
        self.spin_out.setValue(target_source.end_frame)
        self.spin_start_at.setValue(target_source.start_frame)
        self._sync_start_at_combo_preset(target_source.start_frame)
        self.spin_fps.setValue(target_source.fps)
        in_id = getattr(target_source, "input_space", None)
        c_idx = -1
        if in_id is not None:
            c_idx = self.combo_colorspace.findData(in_id)
        if c_idx < 0:
            c_idx = self.combo_colorspace.findText(target_source.colorspace, Qt.MatchFlag.MatchContains)
        if c_idx >= 0:
            self.combo_colorspace.setCurrentIndex(c_idx)
        self.lbl_res_info.setText(f"Resolution: {target_source.resolution_str} | {target_source.fps:.2f} fps")
        self.lbl_codec_info.setText(f"Codec: {target_source.codec} | Range: [{target_source.start_frame}..{target_source.end_frame}]")

        self.spin_in.blockSignals(False)
        self.spin_out.blockSignals(False)
        self.spin_start_at.blockSignals(False)
        self.spin_fps.blockSignals(False)
        self.combo_colorspace.blockSignals(False)
        if hasattr(self, "chk_premultiplied"):
            self.chk_premultiplied.setChecked(getattr(target_source, "premultiplied", True))
            self.chk_premultiplied.blockSignals(False)

        # Dynamic EXR Properties Bar visibility
        exr_path = None
        target_p = getattr(target_source, "path", getattr(target_source, "file_path", None))
        if target_p and str(target_p).lower().endswith(".exr"):
            exr_path = str(target_p)

        # Unify in dedicated VFX EXR dock; keep redundant mini-bar hidden
        if hasattr(self, "exr_properties_bar"):
            self.exr_properties_bar.hide()

    def _on_input_selected(self) -> None:
        if self.list_inputs.signalsBlocked() or getattr(self, "_suppress_input_events", False):
            return
        row = self.list_inputs.currentRow()
        if row >= 0:
            viewable = self.graph.get_active_viewable()
            target_source = None
            if isinstance(viewable, StackView) and 0 <= row < len(viewable.source_ids):
                target_source = self.graph.sources.get(viewable.source_ids[row])
            elif isinstance(viewable, SequenceView) and 0 <= row < len(viewable.clips):
                target_source = self.graph.sources.get(viewable.clips[row].source_id)
            elif isinstance(viewable, LayoutView) and 0 <= row < len(viewable.source_ids):
                target_source = self.graph.sources.get(viewable.source_ids[row])

            if target_source:
                self._populate_clip_edit(target_source)
            self.view_updated.emit()

    def _on_input_item_clicked(self, item: QListWidgetItem) -> None:
        """Clicking an input highlights it in the list and updates Edit tab & details."""
        row = self.list_inputs.row(item)
        if row >= 0:
            self.list_inputs.setCurrentRow(row)
            self._on_input_selected()

    def _on_inputs_context_menu(self, pos: QPoint) -> None:
        """Right-click menu on stack inputs list to assign Layer A or Layer B."""
        item = self.list_inputs.itemAt(pos)
        if not item:
            return
        row = self.list_inputs.row(item)
        viewable = self.graph.get_active_viewable()
        if isinstance(viewable, StackView) and row >= 0:
            self.list_inputs.setCurrentRow(row)
            menu = QMenu(self)
            act_a = menu.addAction("Set as Layer A")
            act_b = menu.addAction("Set as Layer B")
            chosen = menu.exec(self.list_inputs.mapToGlobal(pos))
            if chosen == act_a:
                viewable.index_a = row
                viewable.current_layer_index = row
                self._refresh_subpanels()
                self.view_updated.emit()
            elif chosen == act_b:
                viewable.index_b = row
                viewable.current_layer_index = row
                self._refresh_subpanels()
                self.view_updated.emit()

    def eventFilter(self, watched, event) -> bool:
        """Intercept right-click on list_inputs to show context menu cleanly."""
        if hasattr(self, "list_inputs") and watched == self.list_inputs.viewport():
            if event.type() == QEvent.Type.MouseButtonPress:
                item = self.list_inputs.itemAt(event.pos())
                if item:
                    row = self.list_inputs.row(item)
                    if event.button() == Qt.MouseButton.RightButton and row >= 0:
                        self.list_inputs.setCurrentRow(row)
                        self._on_inputs_context_menu(event.pos())
                        return True
        return super().eventFilter(watched, event)

    def _on_input_move_up(self) -> None:
        cur_row = self.list_inputs.currentRow()
        if cur_row <= 0:
            return
        viewable = self.graph.get_active_viewable()
        if isinstance(viewable, SequenceView):
            viewable.move_clip(cur_row, cur_row - 1)
        elif isinstance(viewable, StackView):
            viewable.move_layer(cur_row, cur_row - 1)
        elif isinstance(viewable, LayoutView):
            viewable.move_tile(cur_row, cur_row - 1)
        self._refresh_subpanels()
        self.list_inputs.setCurrentRow(cur_row - 1)
        self.view_updated.emit()

    def _on_input_move_down(self) -> None:
        cur_row = self.list_inputs.currentRow()
        if cur_row < 0 or cur_row >= self.list_inputs.count() - 1:
            return
        viewable = self.graph.get_active_viewable()
        if isinstance(viewable, SequenceView):
            viewable.move_clip(cur_row, cur_row + 1)
        elif isinstance(viewable, StackView):
            viewable.move_layer(cur_row, cur_row + 1)
        elif isinstance(viewable, LayoutView):
            viewable.move_tile(cur_row, cur_row + 1)
        self._refresh_subpanels()
        self.list_inputs.setCurrentRow(cur_row + 1)
        self.view_updated.emit()

    def _on_input_remove(self) -> None:
        cur_row = self.list_inputs.currentRow()
        if cur_row < 0:
            return
        viewable = self.graph.get_active_viewable()
        if isinstance(viewable, SequenceView):
            viewable.remove_clip(cur_row)
        elif isinstance(viewable, StackView):
            viewable.remove_layer(cur_row)
        elif isinstance(viewable, LayoutView):
            viewable.remove_tile(cur_row)
        self._refresh_subpanels()
        self.view_updated.emit()

    def _on_stack_set_a(self) -> None:
        cur_row = self.list_inputs.currentRow()
        viewable = self.graph.get_active_viewable()
        if isinstance(viewable, StackView) and cur_row >= 0:
            viewable.index_a = cur_row
            viewable.current_layer_index = cur_row
            self._refresh_subpanels()
            self.view_updated.emit()

    def _on_stack_set_b(self) -> None:
        cur_row = self.list_inputs.currentRow()
        viewable = self.graph.get_active_viewable()
        if isinstance(viewable, StackView) and cur_row >= 0:
            viewable.index_b = cur_row
            viewable.current_layer_index = cur_row
            self._refresh_subpanels()
            self.view_updated.emit()

    def _on_stack_mode_changed(self) -> None:
        mode_text = self.combo_stack_mode.currentText()
        modes_map = {
            "Wipe": "wipe",
            "A/B Switch": "switch",
            "Difference": "difference",
            "Inverted Diff": "difference_inverted",
            "Split": "split",
            "Over": "over",
            "Add": "add",
        }
        viewable = self.graph.get_active_viewable()
        if isinstance(viewable, StackView):
            viewable.compare_mode = modes_map.get(mode_text, "switch")
            self.view_updated.emit()

    def _on_stack_mix_slider_changed(self, val: int) -> None:
        fval = val / 100.0
        self.spin_stack_mix.blockSignals(True)
        self.spin_stack_mix.setValue(fval)
        self.spin_stack_mix.blockSignals(False)
        viewable = self.graph.get_active_viewable()
        if isinstance(viewable, StackView):
            viewable.stack_mix = fval
            if viewable.compare_mode == "switch" and viewable.current_layer_index != viewable.index_a:
                viewable.current_layer_index = viewable.index_a
                self._refresh_subpanels()
            self.view_updated.emit()

    def _on_stack_mix_spin_changed(self, fval: float) -> None:
        self.slider_stack_mix.blockSignals(True)
        self.slider_stack_mix.setValue(int(round(fval * 100)))
        self.slider_stack_mix.blockSignals(False)
        viewable = self.graph.get_active_viewable()
        if isinstance(viewable, StackView):
            viewable.stack_mix = fval
            if viewable.compare_mode == "switch" and viewable.current_layer_index != viewable.index_a:
                viewable.current_layer_index = viewable.index_a
                self._refresh_subpanels()
            self.view_updated.emit()


    def _get_target_source(self) -> Optional[SourceItem]:
        selected_nodes = self._get_selected_nodes()
        if selected_nodes and selected_nodes[0].get("type") == "source":
            return self.graph.sources.get(selected_nodes[0].get("id"))

        viewable = self.graph.get_active_viewable()
        if isinstance(viewable, SourceItem):
            return viewable

        if isinstance(viewable, StackView) and viewable.source_ids:
            cur_row = self.list_inputs.currentRow()
            if 0 <= cur_row < len(viewable.source_ids):
                sid = viewable.source_ids[cur_row]
                src = self.graph.sources.get(sid)
                if src:
                    return src
            if viewable.compare_mode == "switch":
                src = viewable.get_current_layer(self.graph.sources)
                if src:
                    return src
            src = viewable.get_layer_a(self.graph.sources)
            if src:
                return src
            return self.graph.sources.get(viewable.source_ids[0])

        if self.graph.active_view_type == ViewType.SOURCE and self.graph.active_view_id:
            return self.graph.sources.get(self.graph.active_view_id)

        return None

    def _sync_start_at_combo_preset(self, val: int) -> None:
        self.combo_start_at.blockSignals(True)
        if val == 1:
            idx = self.combo_start_at.findData(1)
        elif val == 1001:
            idx = self.combo_start_at.findData(1001)
        else:
            idx = self.combo_start_at.findData("custom")
        if idx >= 0:
            self.combo_start_at.setCurrentIndex(idx)
        self.combo_start_at.blockSignals(False)

    def _apply_start_frame_shift(self, new_start: int) -> None:
        s = self._get_target_source()
        if not s:
            return
        duration = max(1, s.end_frame - s.start_frame + 1)
        s.start_frame = int(new_start)
        s.end_frame = int(new_start + duration - 1)

        self.spin_in.blockSignals(True)
        self.spin_out.blockSignals(True)
        self.spin_start_at.blockSignals(True)

        self.spin_in.setValue(s.start_frame)
        self.spin_out.setValue(s.end_frame)
        self.spin_start_at.setValue(s.start_frame)

        self.spin_in.blockSignals(False)
        self.spin_out.blockSignals(False)
        self.spin_start_at.blockSignals(False)

        self._sync_start_at_combo_preset(s.start_frame)
        self.lbl_codec_info.setText(f"Codec: {s.codec} | Range: [{s.start_frame}..{s.end_frame}]")
        self.view_updated.emit()

    def _on_start_at_combo_changed(self, idx: int) -> None:
        val = self.combo_start_at.itemData(idx)
        if val == "custom":
            return
        elif val == 1 or val == 1001:
            self._apply_start_frame_shift(int(val))
        elif val == "playhead":
            w = self.window()
            playhead = 1001
            if w and hasattr(w, "clock") and hasattr(w.clock, "current_frame"):
                playhead = int(w.clock.current_frame)
            elif w and hasattr(w, "timeline") and hasattr(w.timeline, "current_frame"):
                playhead = int(w.timeline.current_frame)
            self._apply_start_frame_shift(playhead)
        elif val == "original":
            s = self._get_target_source()
            orig_start = s.orig_start_frame if (s and s.orig_start_frame is not None) else 1001
            self._apply_start_frame_shift(orig_start)

    def _on_start_at_spin_changed(self, val: int) -> None:
        self._apply_start_frame_shift(val)

    def _on_clip_in_changed(self, val: int) -> None:
        s = self._get_target_source()
        if s:
            s.start_frame = val
            self.spin_start_at.blockSignals(True)
            self.spin_start_at.setValue(val)
            self.spin_start_at.blockSignals(False)
            self._sync_start_at_combo_preset(val)
            self.lbl_codec_info.setText(f"Codec: {s.codec} | Range: [{s.start_frame}..{s.end_frame}]")
            self.view_updated.emit()

    def _on_clip_out_changed(self, val: int) -> None:
        s = self._get_target_source()
        if s:
            s.end_frame = val
            self.lbl_codec_info.setText(f"Codec: {s.codec} | Range: [{s.start_frame}..{s.end_frame}]")
            self.view_updated.emit()

    def _on_clear_clip_in_out(self) -> None:
        """Reset selected or active clip in/out range back to original detected media bounds."""
        s = self._get_target_source()
        if s:
            s.reset_in_out()
            self.spin_in.blockSignals(True)
            self.spin_out.blockSignals(True)
            self.spin_start_at.blockSignals(True)
            self.spin_in.setValue(s.start_frame)
            self.spin_out.setValue(s.end_frame)
            self.spin_start_at.setValue(s.start_frame)
            self.spin_in.blockSignals(False)
            self.spin_out.blockSignals(False)
            self.spin_start_at.blockSignals(False)
            self._sync_start_at_combo_preset(s.start_frame)
            self.lbl_codec_info.setText(f"Codec: {s.codec} | Range: [{s.start_frame}..{s.end_frame}]")
            self.view_updated.emit()

    def _on_clip_fps_changed(self, val: float) -> None:
        s = self._get_target_source()
        if s:
            s.fps = val
            self.view_updated.emit()

    def _on_clip_premultiplied_changed(self, checked: bool) -> None:
        s = self._get_target_source()
        if s:
            s.premultiplied = checked
            self.graph.source_properties_changed.emit(s.id)
            self.view_updated.emit()

    def _on_clip_colorspace_changed(self) -> None:
        s = self._get_target_source()
        if s:
            cs_id = self.combo_colorspace.currentData()
            if cs_id is not None:
                s.input_space = int(cs_id)
                from openreview.color.standard_colorspaces import COLORSPACE_BY_ID
                if cs_id in COLORSPACE_BY_ID:
                    s.colorspace = COLORSPACE_BY_ID[cs_id].name
            else:
                s.colorspace = self.combo_colorspace.currentText()
            self.graph.source_properties_changed.emit(s.id)
            self.view_updated.emit()

    def set_color_management_mode(self, mode: str, ocio_spaces: Optional[List[str]] = None) -> None:
        """Switch sources panel colorspace combo between standard spaces and OCIO spaces."""
        self.combo_colorspace.blockSignals(True)
        self.combo_colorspace.clear()
        if mode == "standard":
            from openreview.color.standard_colorspaces import STANDARD_COLORSPACES
            for cs in STANDARD_COLORSPACES:
                self.combo_colorspace.addItem(cs.label, cs.id)
        else:
            spaces = ocio_spaces or []
            for idx, sp in enumerate(spaces):
                self.combo_colorspace.addItem(sp, idx)
        self.combo_colorspace.blockSignals(False)

    def _on_layout_scale_changed(self) -> None:
        text = self.combo_scale_pct.currentText().replace("%", "")
        try:
            pct = int(text)
        except ValueError:
            pct = 100
        viewable = self.graph.get_active_viewable()
        if isinstance(viewable, LayoutView):
            viewable.scale_pct = pct
            self.view_updated.emit()

    def _on_layout_type_changed(self) -> None:
        text = self.combo_layout_type.currentText()
        type_map = {
            "Auto-Fit Grid": "auto_grid",
            "Side-by-Side": "side_by_side",
            "Over-Under": "over_under",
            "2x2 Grid": "grid_2x2",
            "3x3 Grid": "grid_3x3",
        }
        viewable = self.graph.get_active_viewable()
        if isinstance(viewable, LayoutView):
            viewable.layout_type = type_map.get(text, "auto_grid")
            self.view_updated.emit()

    def _on_layout_align_changed(self, idx: int) -> None:
        align_mode = self.combo_layout_align.itemData(idx) or "frame_1"
        viewable = self.graph.get_active_viewable()
        if isinstance(viewable, LayoutView):
            viewable.align_mode = align_mode
            self.view_updated.emit()

    def _on_input_item_double_clicked(self, item: QListWidgetItem) -> None:
        """Double clicking an input source inside sequence/stack/layout jumps to that single source view."""
        cur_row = self.list_inputs.row(item)
        if cur_row < 0:
            return
        viewable = self.graph.get_active_viewable()
        sid = None
        if isinstance(viewable, SequenceView):
            if 0 <= cur_row < len(viewable.clips):
                sid = viewable.clips[cur_row].source_id
        elif isinstance(viewable, StackView):
            if 0 <= cur_row < len(viewable.source_ids):
                sid = viewable.source_ids[cur_row]
        elif isinstance(viewable, LayoutView):
            if 0 <= cur_row < len(viewable.source_ids):
                sid = viewable.source_ids[cur_row]
        elif isinstance(viewable, SourceItem):
            sid = viewable.id

        if sid and sid in self.graph.sources:
            self.graph.set_active_view(ViewType.SOURCE, sid)

    def _on_active_view_changed(self, vtype: str, vid: str) -> None:
        self._update_header_title()
        self._refresh_subpanels()
        self.active_view_changed.emit(vtype, vid)


class SourcesDockWidget(QDockWidget):
    """
    Host QDockWidget wrapper enabling Sources & Session Manager to dock to the left,
    right, or float freely across studio monitors.
    """
    def __init__(self, graph: SessionGraph, parent: Optional[QWidget] = None):
        super().__init__("Sources & Session Manager", parent)
        self.setObjectName("SourcesDockWidget")
        self.setAllowedAreas(Qt.DockWidgetArea.LeftDockWidgetArea | Qt.DockWidgetArea.RightDockWidgetArea)
        self.setTitleBarWidget(TabbedDockTitleBar(self))
        self.panel = SourcesPanelWidget(graph, self)
        self.setWidget(self.panel)

    def __getattr__(self, name):
        if hasattr(self, "panel") and hasattr(self.panel, name):
            return getattr(self.panel, name)
        raise AttributeError(f"'{type(self).__name__}' object has no attribute '{name}'")
