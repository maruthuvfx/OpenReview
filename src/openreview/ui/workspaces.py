"""
Studio Workspace Presets & Dockable Panels Layout System.
Provides:
- Native QDockWidget panels with custom studio dark titlebars (Float/Undock, Tabify, Close)
- Scopes Dock (Histogram, Waveform, Vectorscope, All-Scopes Stack)
- Inspector Dock (Metadata Attribute Tree, Source Media Details, Color Inspector)
- VFX & Multi-Channel EXR Dock (Channel Matrix & Swizzler, Layer Passes)
- Color Adjustments & Grading Dock
- Annotations & Notes Manager Dock
- Studio Workspace Presets (Standard Review, Color & QC, VFX Breakdown, Cinema Theater, Supervisor Review)
- User custom workspace saving, persistence, and one-click layout switching
"""
from __future__ import annotations

from openreview.ui.svg_icons import get_svg_icon, get_svg_pixmap

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Any

from PySide6.QtCore import Qt, Signal, QObject, QByteArray
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QMainWindow,
    QDockWidget,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QToolButton,
    QPushButton,
    QTabWidget,
    QSplitter,
    QSizePolicy,
    QComboBox,
    QMenu,
    QInputDialog,
    QMessageBox,
    QScrollArea,
    QFrame,
)

logger = logging.getLogger(__name__)


# ------------------------------------------------------------------------------
# Sleek Dark Studio Titlebar for QDockWidgets
# ------------------------------------------------------------------------------
class StudioDockTitleBar(QWidget):
    """
    Compact, professional studio title bar for dock widgets:
    - Gold/white section header with vector SVG icon
    - Undock / Float toggle button (↗)
    - Close / Hide button (✕)
    """
    float_requested = Signal()
    close_requested = Signal()

    def __init__(self, title: str, icon_name: str = "", parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setFixedHeight(24)
        self.setStyleSheet("""
            QWidget {
                background-color: #1e1e1e;
                border-bottom: 1px solid #2d2d2d;
            }
            QLabel {
                color: #e5a823;
                font-weight: bold;
                font-size: 11px;
                padding-left: 4px;
            }
            QToolButton {
                color: #888888;
                background: transparent;
                border: none;
                font-size: 11px;
                font-weight: bold;
                padding: 1px 4px;
                border-radius: 2px;
            }
            QToolButton:hover {
                color: #ffffff;
                background-color: #333333;
            }
            QToolButton#closeBtn:hover {
                color: #ffffff;
                background-color: #a82323;
            }
        """)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 0, 4, 0)
        layout.setSpacing(4)

        if icon_name:
            self.lbl_icon = QLabel(self)
            self.lbl_icon.setPixmap(get_svg_pixmap(icon_name, "#e5a823", 14))
            layout.addWidget(self.lbl_icon)

        # Title Label
        self.lbl_title = QLabel(title, self)
        layout.addWidget(self.lbl_title)
        layout.addStretch()

        # Float / Undock Button
        self.btn_float = QToolButton(self)
        self.btn_float.setText("↗")
        self.btn_float.setToolTip("Float / Undock panel to free window")
        self.btn_float.clicked.connect(self.float_requested.emit)
        layout.addWidget(self.btn_float)

        # Close Button
        self.btn_close = QToolButton(self)
        self.btn_close.setObjectName("closeBtn")
        self.btn_close.setText("✕")
        self.btn_close.setToolTip("Close / Hide panel")
        self.btn_close.clicked.connect(self.close_requested.emit)
        layout.addWidget(self.btn_close)

    def set_title(self, title: str) -> None:
        self.lbl_title.setText(title)


# ------------------------------------------------------------------------------
# Base Studio Dock Widget
# ------------------------------------------------------------------------------
class StudioDockWidget(QDockWidget):
    """Base QDockWidget configured with dark studio styling and custom title bar."""

    def __init__(self, title: str, object_name: str, icon_name: str = "", parent: Optional[QMainWindow] = None):
        super().__init__(title, parent)
        self.setObjectName(object_name)
        self.setAllowedAreas(
            Qt.DockWidgetArea.LeftDockWidgetArea
            | Qt.DockWidgetArea.RightDockWidgetArea
            | Qt.DockWidgetArea.BottomDockWidgetArea
            | Qt.DockWidgetArea.TopDockWidgetArea
        )

        # Custom Title Bar
        self.title_bar = StudioDockTitleBar(title, icon_name=icon_name, parent=self)
        self.title_bar.float_requested.connect(self._toggle_floating)
        self.title_bar.close_requested.connect(self.hide)
        self.setTitleBarWidget(self.title_bar)

        self.setStyleSheet("""
            QDockWidget {
                color: #dddddd;
                font-size: 11px;
                border: 1px solid #2a2a2a;
            }
            QDockWidget::title {
                background: #1e1e1e;
                padding: 4px;
            }
            QTabWidget::pane {
                border: 1px solid #252525;
                background-color: #141414;
            }
            QTabBar::tab {
                background: #181818;
                color: #888888;
                padding: 4px 10px;
                font-weight: bold;
                font-size: 10.5px;
                border: 1px solid #252525;
                border-bottom: none;
                margin-right: 2px;
                border-top-left-radius: 3px;
                border-top-right-radius: 3px;
            }
            QTabBar::tab:hover {
                color: #e0e0e0;
                background: #222222;
            }
            QTabBar::tab:selected {
                color: #e5a823;
                background: #141414;
                border-top: 2px solid #e5a823;
            }
        """)

    def _toggle_floating(self) -> None:
        self.setFloating(not self.isFloating())


# ------------------------------------------------------------------------------
# 1. Scopes Dock (Histogram, Waveform, Vectorscope, All-Scopes Stack)
# ------------------------------------------------------------------------------
# ------------------------------------------------------------------------------
# Collapsible Section Component & Tabs Compatibility Shim
# ------------------------------------------------------------------------------
class _TabsShim:
    """Compatibility shim so legacy calls to dock.tabs continue functioning smoothly."""
    def __init__(self, tab_names: List[str]):
        self._tab_names = tab_names
        self._current_index = 0

    def count(self) -> int:
        return len(self._tab_names)

    def currentIndex(self) -> int:
        return self._current_index

    def setCurrentIndex(self, idx: int) -> None:
        self._current_index = max(0, min(idx, len(self._tab_names) - 1))

    def tabText(self, idx: int) -> str:
        if 0 <= idx < len(self._tab_names):
            return self._tab_names[idx]
        return ""


class CollapsibleSection(QWidget):
    """
    Sleek studio accordion card with clickable header (▼ / ▶) and collapsible content container.
    Features resolution-independent vector SVG icons.
    """
    def __init__(
        self,
        title: str,
        icon_name: str = "",
        content_widget: Optional[QWidget] = None,
        expanded: bool = True,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self._expanded: bool = expanded
        self._title: str = title
        self._icon_name: str = icon_name

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # Header Frame (Clickable button)
        self.header_btn = QPushButton(self)
        self.header_btn.setCheckable(True)
        self.header_btn.setChecked(expanded)
        self.header_btn.setFixedHeight(26)
        self.header_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        if icon_name:
            self.header_btn.setIcon(get_svg_icon(icon_name, "#e5a823", 14))
            from PySide6.QtCore import QSize
            self.header_btn.setIconSize(QSize(14, 14))
        self.header_btn.setStyleSheet("""
            QPushButton {
                background-color: #1c1c1c;
                color: #e5a823;
                font-size: 10.5px;
                font-weight: bold;
                text-align: left;
                padding-left: 8px;
                border: 1px solid #282828;
                border-radius: 3px;
            }
            QPushButton:hover {
                background-color: #242424;
                border-color: #3d3d3d;
                color: #f5b833;
            }
        """)
        self._update_header_text()
        self.header_btn.clicked.connect(self._on_toggle)
        main_layout.addWidget(self.header_btn)

        # Content container
        self.content_frame = QFrame(self)
        self.content_frame.setStyleSheet("""
            QFrame {
                background-color: #141414;
                border: 1px solid #222222;
                border-top: none;
                border-bottom-left-radius: 3px;
                border-bottom-right-radius: 3px;
            }
        """)
        content_layout = QVBoxLayout(self.content_frame)
        content_layout.setContentsMargins(4, 4, 4, 4)
        content_layout.setSpacing(4)
        if content_widget is not None:
            content_layout.addWidget(content_widget)
        main_layout.addWidget(self.content_frame)

        self.content_frame.setVisible(expanded)

    def _update_header_text(self) -> None:
        arrow = "▼" if self._expanded else "▶"
        self.header_btn.setText(f"{arrow}   {self._title}")

    def _on_toggle(self) -> None:
        self.set_expanded(not self._expanded)

    def set_expanded(self, expanded: bool) -> None:
        self._expanded = expanded
        self.header_btn.setChecked(expanded)
        self.content_frame.setVisible(expanded)
        self._update_header_text()

class ScopesDockWidget(StudioDockWidget):
    """
    Studio Scopes Dock:
    Hosts real-time Histogram, Waveform, Vectorscope, and Interactive Color Adjustments
    in a clean, individually collapsible studio accordion stack.
    """

    def __init__(self, color_grading=None, parent: Optional[QMainWindow] = None):
        super().__init__("Scopes", "ScopesDockWidget", icon_name="histogram", parent=parent)

        # Eliminate redundant dock titlebar so the top dock tab bar serves as the sleek header
        self.empty_title = QWidget(self)
        self.empty_title.setFixedHeight(0)
        self.setTitleBarWidget(self.empty_title)

        from openreview.widgets.hud_histogram import HistogramHUD
        from openreview.widgets.hud_waveform import WaveformHUD
        from openreview.widgets.hud_vectorscope import VectorscopeHUD
        from openreview.ui.color_dialog import ColorAdjustmentsWidget

        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet("""
            QScrollArea {
                background: #141414;
                border: none;
            }
            QScrollBar:vertical {
                background: #181818;
                width: 8px;
                margin: 0px;
            }
            QScrollBar::handle:vertical {
                background: #333333;
                min-height: 20px;
                border-radius: 4px;
            }
            QScrollBar::handle:vertical:hover {
                background: #e5a823;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                height: 0px;
            }
        """)

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(6)

        # Scope & Grading Widgets
        self.hist_widget = HistogramHUD()
        self.all_hist = self.hist_widget
        self.hist_widget.setMinimumHeight(110)
        self.hist_widget.setMaximumHeight(160)

        self.waveform_widget = WaveformHUD()
        self.all_wave = self.waveform_widget
        self.waveform_widget.setMinimumHeight(130)
        self.waveform_widget.setMaximumHeight(200)

        self.vectorscope_widget = VectorscopeHUD()
        self.all_vec = self.vectorscope_widget
        self.vectorscope_widget.setMinimumHeight(180)
        self.vectorscope_widget.setMaximumHeight(250)

        self.color_adj_widget = ColorAdjustmentsWidget(color_grading)

        # 4 Collapsible Accordion Sections (Color adjustments collapsed by default)
        self.sec_hist = CollapsibleSection("HISTOGRAM", icon_name="histogram", content_widget=self.hist_widget, expanded=True)
        self.sec_wave = CollapsibleSection("WAVEFORM", icon_name="waveform", content_widget=self.waveform_widget, expanded=True)
        self.sec_vec = CollapsibleSection("VECTORSCOPE", icon_name="vectorscope", content_widget=self.vectorscope_widget, expanded=True)
        self.sec_color = CollapsibleSection("COLOR ADJUSTMENTS", icon_name="color", content_widget=self.color_adj_widget, expanded=False)

        layout.addWidget(self.sec_hist)
        layout.addWidget(self.sec_wave)
        layout.addWidget(self.sec_vec)
        layout.addWidget(self.sec_color)
        layout.addStretch(1)

        scroll.setWidget(container)
        self.setWidget(scroll)

        # Tabs compatibility shim
        self.tabs = _TabsShim(["Histogram", "Waveform", "Vectorscope", "All Scopes"])

    def is_histogram_visible(self) -> bool:
        return self.isVisible() and self.sec_hist._expanded

    def is_waveform_visible(self) -> bool:
        return self.isVisible() and self.sec_wave._expanded

    def is_vectorscope_visible(self) -> bool:
        return self.isVisible() and self.sec_vec._expanded


# ------------------------------------------------------------------------------
# 2. Inspector Dock (Metadata Tree, Source Details, Color Inspector)
# ------------------------------------------------------------------------------
class InspectorDockWidget(StudioDockWidget):
    """
    Studio Inspector & Metadata Dock:
    Hosts interactive Pixel Color Inspector and Source Media Details
    in a clean, individually collapsible studio accordion stack.
    """

    def __init__(self, parent: Optional[QMainWindow] = None):
        super().__init__("Inspector", "InspectorDockWidget", icon_name="inspector", parent=parent)

        # Eliminate redundant dock titlebar
        self.empty_title = QWidget(self)
        self.empty_title.setFixedHeight(0)
        self.setTitleBarWidget(self.empty_title)

        from openreview.ui.image_info_dialog import ImageInfoWidget
        from openreview.widgets.hud_color_inspector import ColorInspectorHUD
        from openreview.widgets.metadata_tree_widget import MetadataTreeWidget

        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet("""
            QScrollArea {
                background: #141414;
                border: none;
            }
            QScrollBar:vertical {
                background: #181818;
                width: 8px;
                margin: 0px;
            }
            QScrollBar::handle:vertical {
                background: #333333;
                min-height: 20px;
                border-radius: 4px;
            }
            QScrollBar::handle:vertical:hover {
                background: #e5a823;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                height: 0px;
            }
        """)

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(6)

        # 1. Color Inspector (Top Half)
        self.inspector_widget = ColorInspectorHUD()
        self.sec_color_insp = CollapsibleSection("COLOR INSPECTOR", icon_name="inspector", content_widget=self.inspector_widget, expanded=True)
        layout.addWidget(self.sec_color_insp)

        # 2. Source Details (Bottom Half)
        self.details_widget = ImageInfoWidget()
        self.sec_source_details = CollapsibleSection("SOURCE DETAILS", icon_name="details", content_widget=self.details_widget, expanded=True)
        layout.addWidget(self.sec_source_details)

        # Standalone metadata widget reference for compatibility
        self.metadata_widget = MetadataTreeWidget()

        layout.addStretch(1)
        scroll.setWidget(container)
        self.setWidget(scroll)

        # Tabs compatibility shim
        self.tabs = _TabsShim(["Metadata Tree", "Source Details", "Color Inspector"])


# ------------------------------------------------------------------------------
# 3. VFX & Multi-Channel EXR Dock (Channel Matrix & Swizzler, Layer Passes)
# ------------------------------------------------------------------------------
class VFXEXRDockWidget(StudioDockWidget):
    """
    Studio VFX & Multi-Channel EXR Dock:
    Hosts EXR Channel Matrix / Swizzler and Multilayer Pass Inspector
    in a clean, individually collapsible studio accordion stack.
    """

    def __init__(self, parent: Optional[QMainWindow] = None):
        super().__init__("VFX EXR", "VFXEXRDockWidget", icon_name="matrix", parent=parent)

        # Eliminate redundant dock titlebar so the top dock tab bar serves as the sleek header
        self.empty_title = QWidget(self)
        self.empty_title.setFixedHeight(0)
        self.setTitleBarWidget(self.empty_title)

        from openreview.widgets.hud_channel_matrix import HUDChannelMatrixWidget
        from openreview.widgets.hud_layers import HUDLayersWidget

        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet("""
            QScrollArea {
                background: #141414;
                border: none;
            }
            QScrollBar:vertical {
                background: #181818;
                width: 8px;
                margin: 0px;
            }
            QScrollBar::handle:vertical {
                background: #333333;
                min-height: 20px;
                border-radius: 4px;
            }
            QScrollBar::handle:vertical:hover {
                background: #e5a823;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                height: 0px;
            }
        """)

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(6)

        # 1. Channel Matrix & Swizzler (Expanded by default)
        self.channel_matrix_widget = HUDChannelMatrixWidget()
        self.sec_channel_matrix = CollapsibleSection("CHANNEL MATRIX & SWIZZLER", icon_name="matrix", content_widget=self.channel_matrix_widget, expanded=True)
        layout.addWidget(self.sec_channel_matrix)

        # 2. Multilayer Pass Inspector (Collapsed by default per user choice)
        self.layers_widget = HUDLayersWidget()
        self.sec_layers = CollapsibleSection("LAYER PASSES", icon_name="layers", content_widget=self.layers_widget, expanded=False)
        layout.addWidget(self.sec_layers)

        layout.addStretch(1)
        scroll.setWidget(container)
        self.setWidget(scroll)

        # Tabs compatibility shim
        self.tabs = _TabsShim(["Channel Matrix", "Layer Passes"])


# ------------------------------------------------------------------------------
# 4. Color Grading Dock
# ------------------------------------------------------------------------------
class ColorGradingDockWidget(StudioDockWidget):
    """
    Studio Color Grading Dock:
    Hosts interactive color grading adjustments (Lift, Gamma, Gain, Saturation, Contrast).
    """

    def __init__(self, color_grading=None, parent: Optional[QMainWindow] = None):
        super().__init__("Color", "ColorGradingDockWidget", icon_name="color", parent=parent)

        from openreview.ui.color_dialog import ColorAdjustmentsWidget

        container = QWidget(self)
        layout = QVBoxLayout(container)
        layout.setContentsMargins(4, 4, 4, 4)

        self.color_adj_widget = ColorAdjustmentsWidget(color_grading)
        layout.addWidget(self.color_adj_widget)

        self.setWidget(container)


# ------------------------------------------------------------------------------
# 5. Annotations & Notes Dock
# ------------------------------------------------------------------------------
class AnnotationsDockWidget(StudioDockWidget):
    """
    Studio Annotations & Review Notes Dock:
    Hosts review comments, marked frames, and frame jump actions.
    """

    def __init__(
        self,
        annotation_store=None,
        clock=None,
        render_frame_callback=None,
        parent: Optional[QMainWindow] = None,
    ):
        super().__init__("Notes", "AnnotationsDockWidget", icon_name="notes", parent=parent)

        from openreview.ui.annotation_manager_dialog import AnnotationManagerWidget

        self.annotation_manager_widget = AnnotationManagerWidget(
            annotation_store=annotation_store,
            clock=clock,
            render_frame_callback=render_frame_callback,
            parent=self,
        )
        if hasattr(self.annotation_manager_widget, "btn_close"):
            self.annotation_manager_widget.btn_close.hide()

        self.setWidget(self.annotation_manager_widget)


# ------------------------------------------------------------------------------
# Studio Workspace Definition Data Model
# ------------------------------------------------------------------------------
@dataclass
class StudioWorkspace:
    """Configuration definition for a studio workspace layout."""
    id: str
    name: str
    icon: str
    description: str
    shortcut: str = ""
    visible_docks: List[str] = field(default_factory=list)
    hidden_docks: List[str] = field(default_factory=list)
    dock_areas: Dict[str, Qt.DockWidgetArea] = field(default_factory=dict)
    tab_groups: List[Tuple[str, str]] = field(default_factory=list)
    show_top_bar: bool = True
    show_timeline: bool = True
    show_status_bar: bool = True
    custom_state: Optional[str] = None  # Hex-encoded QByteArray of saved layout state


# Standard Predefined Studio Workspaces
STUDIO_WORKSPACES: Dict[str, StudioWorkspace] = {
    "review": StudioWorkspace(
        id="review",
        name="Standard Review",
        icon="review",
        description="Clean layout optimized for playback, review notes, and A/B comparison.",
        shortcut="F5",
        visible_docks=["SourcesDockWidget", "TimelineDockWidget"],
        hidden_docks=[
            "ScopesDockWidget",
            "InspectorDockWidget",
            "VFXEXRDockWidget",
            "ColorGradingDockWidget",
            "AnnotationsDockWidget",
            "FileBrowserDockWidget",
            "CompQCDockWidget",
        ],
        dock_areas={
            "SourcesDockWidget": Qt.DockWidgetArea.LeftDockWidgetArea,
            "TimelineDockWidget": Qt.DockWidgetArea.BottomDockWidgetArea,
        },
        show_top_bar=True,
        show_timeline=True,
    ),
    "color_qc": StudioWorkspace(
        id="color_qc",
        name="Color & QC Studio",
        icon="color",
        description="Dual scopes and inspector panels for broadcast QC, exposure balancing, and grading.",
        shortcut="F6",
        visible_docks=[
            "SourcesDockWidget",
            "ScopesDockWidget",
            "InspectorDockWidget",
            "TimelineDockWidget",
        ],
        hidden_docks=[
            "ColorGradingDockWidget",
            "VFXEXRDockWidget",
            "AnnotationsDockWidget",
            "FileBrowserDockWidget",
            "CompQCDockWidget",
        ],
        dock_areas={
            "SourcesDockWidget": Qt.DockWidgetArea.LeftDockWidgetArea,
            "ScopesDockWidget": Qt.DockWidgetArea.RightDockWidgetArea,
            "InspectorDockWidget": Qt.DockWidgetArea.RightDockWidgetArea,
            "TimelineDockWidget": Qt.DockWidgetArea.BottomDockWidgetArea,
        },
        tab_groups=[
            ("ScopesDockWidget", "InspectorDockWidget"),
        ],
        show_top_bar=True,
        show_timeline=True,
    ),
    "vfx_breakdown": StudioWorkspace(
        id="vfx_breakdown",
        name="VFX Breakdown & EXR",
        icon="matrix",
        description="Channel matrix, multilayer passes, and OpenEXR attribute tree for compositing review.",
        shortcut="F7",
        visible_docks=[
            "SourcesDockWidget",
            "VFXEXRDockWidget",
            "InspectorDockWidget",
            "TimelineDockWidget",
        ],
        hidden_docks=[
            "ScopesDockWidget",
            "ColorGradingDockWidget",
            "AnnotationsDockWidget",
            "FileBrowserDockWidget",
            "CompQCDockWidget",
        ],
        dock_areas={
            "SourcesDockWidget": Qt.DockWidgetArea.LeftDockWidgetArea,
            "VFXEXRDockWidget": Qt.DockWidgetArea.RightDockWidgetArea,
            "InspectorDockWidget": Qt.DockWidgetArea.RightDockWidgetArea,
            "TimelineDockWidget": Qt.DockWidgetArea.BottomDockWidgetArea,
        },
        tab_groups=[
            ("VFXEXRDockWidget", "InspectorDockWidget"),
        ],
        show_top_bar=True,
        show_timeline=True,
    ),
    "cinema": StudioWorkspace(
        id="cinema",
        name="Cinema / Dailies Theater",
        icon="cinema",
        description="Distraction-free theater mode: 100% maximized viewport with all chrome collapsed.",
        shortcut="F8",
        visible_docks=[],
        hidden_docks=[
            "SourcesDockWidget",
            "ScopesDockWidget",
            "InspectorDockWidget",
            "VFXEXRDockWidget",
            "ColorGradingDockWidget",
            "AnnotationsDockWidget",
            "FileBrowserDockWidget",
            "CompQCDockWidget",
            "TimelineDockWidget",
        ],
        show_top_bar=False,
        show_timeline=False,
    ),
    "annotations": StudioWorkspace(
        id="annotations",
        name="Supervisor Review & Notes",
        icon="notes",
        description="Review comments, annotated frames list, and markup palette for director turnovers.",
        shortcut="F9",
        visible_docks=[
            "SourcesDockWidget",
            "AnnotationsDockWidget",
            "TimelineDockWidget",
        ],
        hidden_docks=[
            "ScopesDockWidget",
            "InspectorDockWidget",
            "VFXEXRDockWidget",
            "ColorGradingDockWidget",
            "FileBrowserDockWidget",
            "CompQCDockWidget",
        ],
        dock_areas={
            "SourcesDockWidget": Qt.DockWidgetArea.LeftDockWidgetArea,
            "AnnotationsDockWidget": Qt.DockWidgetArea.RightDockWidgetArea,
            "TimelineDockWidget": Qt.DockWidgetArea.BottomDockWidgetArea,
        },
        show_top_bar=True,
        show_timeline=True,
    ),
}


# ------------------------------------------------------------------------------
# Studio Workspace Manager
# ------------------------------------------------------------------------------
class StudioWorkspaceManager(QObject):
    """
    Central Controller for Studio Workspace Presets.
    Manages dock visibility, positioning, tabification, and custom layout persistence.
    """
    workspace_changed = Signal(str)

    def __init__(self, parent: Optional[QObject] = None):
        super().__init__(parent)
        self._current_workspace: str = "review"
        self._custom_workspaces: Dict[str, StudioWorkspace] = {}

    @property
    def current_workspace(self) -> str:
        return self._current_workspace

    def list_workspaces(self) -> List[StudioWorkspace]:
        """Return list of standard presets followed by any user-saved custom workspaces."""
        res = list(STUDIO_WORKSPACES.values())
        res.extend(self._custom_workspaces.values())
        return res

    def get_workspace(self, workspace_id: str) -> Optional[StudioWorkspace]:
        if workspace_id in STUDIO_WORKSPACES:
            return STUDIO_WORKSPACES[workspace_id]
        return self._custom_workspaces.get(workspace_id)

    def apply_workspace(self, workspace_id: str, main_window: QMainWindow) -> bool:
        """Apply the specified workspace layout to QMainWindow."""
        ws = self.get_workspace(workspace_id)
        if ws is None:
            logger.warning("[StudioWorkspaceManager] Unknown workspace: %s", workspace_id)
            return False

        self._current_workspace = workspace_id

        # 1. If custom saved state is present, restore directly via Qt state bytes
        if ws.custom_state:
            try:
                state_bytes = bytes.fromhex(ws.custom_state)
                qbarr = QByteArray(state_bytes)
                main_window.restoreState(qbarr)
                if hasattr(main_window, "top_toolbar"):
                    main_window.top_toolbar.setVisible(ws.show_top_bar)
                if hasattr(main_window, "timeline_dock"):
                    main_window.timeline_dock.setVisible(ws.show_timeline)
                self.workspace_changed.emit(workspace_id)
                return True
            except Exception as e:
                logger.warning("[StudioWorkspaceManager] Failed restoring custom state: %s", e)

        # 2. Apply standard preset configuration
        # A. UI Chrome (Top toolbar, timeline dock)
        if hasattr(main_window, "top_toolbar"):
            main_window.top_toolbar.setVisible(ws.show_top_bar)
        if hasattr(main_window, "timeline_dock"):
            main_window.timeline_dock.setVisible(ws.show_timeline)

        # B. Close all in-viewport floating HUD cards for clean docked presentation
        if hasattr(main_window, "_on_close_all_panels"):
            main_window._on_close_all_panels()

        # C. Dock Positioning & Visibility
        dock_map = self._get_dock_map(main_window)

        # Hide explicitly hidden docks
        for d_name in ws.hidden_docks:
            d = dock_map.get(d_name)
            if d is not None:
                d.hide()
                if d_name == "ColorGradingDockWidget":
                    main_window.removeDockWidget(d)

        # Position and show visible docks
        for d_name in ws.visible_docks:
            d = dock_map.get(d_name)
            if d is not None:
                target_area = ws.dock_areas.get(d_name, Qt.DockWidgetArea.RightDockWidgetArea)
                main_window.addDockWidget(target_area, d)
                d.show()
                d.setFloating(False)

        # Tabify specified dock groups
        for (d1_name, d2_name) in ws.tab_groups:
            d1 = dock_map.get(d1_name)
            d2 = dock_map.get(d2_name)
            if d1 is not None and d2 is not None and d1.isVisible() and d2.isVisible():
                main_window.tabifyDockWidget(d1, d2)
                d1.raise_()

        # D. Workspace specific tool triggers
        if workspace_id == "annotations":
            if hasattr(main_window, "markup_palette"):
                main_window.markup_palette.show()
        else:
            if hasattr(main_window, "markup_palette"):
                main_window.markup_palette.hide()

        # Initial default dock size allocation
        if hasattr(main_window, "resizeDocks"):
            right_docks = [d for d_name, d in dock_map.items() if d.isVisible() and ws.dock_areas.get(d_name) == Qt.DockWidgetArea.RightDockWidgetArea]
            if right_docks:
                main_window.resizeDocks(right_docks, [380] * len(right_docks), Qt.Orientation.Horizontal)
            left_docks = [d for d_name, d in dock_map.items() if d.isVisible() and ws.dock_areas.get(d_name) == Qt.DockWidgetArea.LeftDockWidgetArea]
            if left_docks:
                main_window.resizeDocks(left_docks, [280] * len(left_docks), Qt.Orientation.Horizontal)

        self.workspace_changed.emit(workspace_id)
        return True

    def save_custom_workspace(self, name: str, main_window: QMainWindow) -> str:
        """Save the current window layout as a custom named workspace."""
        clean_name = name.strip() or "Custom Workspace"
        ws_id = "custom_" + "".join(c if c.isalnum() else "_" for c in clean_name.lower())

        qbarr = main_window.saveState()
        hex_data = bytes(qbarr.data()).hex()

        top_vis = main_window.top_toolbar.isVisible() if hasattr(main_window, "top_toolbar") else True
        tl_vis = main_window.timeline_dock.isVisible() if hasattr(main_window, "timeline_dock") else True

        ws = StudioWorkspace(
            id=ws_id,
            name=clean_name,
            icon="⭐",
            description=f"User-saved studio layout ({clean_name})",
            show_top_bar=top_vis,
            show_timeline=tl_vis,
            custom_state=hex_data,
        )
        self._custom_workspaces[ws_id] = ws
        self._current_workspace = ws_id
        self.workspace_changed.emit(ws_id)
        return ws_id

    def delete_custom_workspace(self, workspace_id: str) -> bool:
        if workspace_id in self._custom_workspaces:
            del self._custom_workspaces[workspace_id]
            if self._current_workspace == workspace_id:
                self._current_workspace = "review"
            self.workspace_changed.emit(self._current_workspace)
            return True
        return False

    def export_custom_workspaces(self) -> Dict[str, str]:
        """Export dictionary of custom workspaces for Preferences persistence."""
        return {ws.name: ws.custom_state for ws in self._custom_workspaces.values() if ws.custom_state}

    def import_custom_workspaces(self, data: Dict[str, str]) -> None:
        """Load persisted custom workspaces from Preferences."""
        for name, hex_str in data.items():
            ws_id = "custom_" + "".join(c if c.isalnum() else "_" for c in name.lower())
            self._custom_workspaces[ws_id] = StudioWorkspace(
                id=ws_id,
                name=name,
                icon="⭐",
                description=f"User-saved studio layout ({name})",
                custom_state=hex_str,
            )

    @staticmethod
    def _get_dock_map(main_window: QMainWindow) -> Dict[str, QDockWidget]:
        """Extract all known QDockWidgets by objectName."""
        docks = {}
        for child in main_window.findChildren(QDockWidget):
            name = child.objectName()
            if name:
                docks[name] = child
        return docks
