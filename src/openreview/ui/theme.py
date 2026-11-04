"""
3DEqualizer-Inspired Dark Slate & Warm Amber VFX Theme.
Replicates the high-contrast utilitarian aesthetic of 3DEqualizer4.
"""

THEME_STYLESHEET = """
/* Global Application Style */
QWidget {
    background-color: #1e1e1e;
    color: #e0e0e0;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    font-size: 11px;
    selection-background-color: #e5a823;
    selection-color: #121212;
}

/* ToolTips */
QToolTip {
    background-color: #242426;
    color: #e0e0e0;
    border: 1px solid #444448;
    border-radius: 3px;
    padding: 4px 8px;
    font-size: 11px;
}

/* Main Window & Menus */
QMainWindow {
    background-color: #181818;
}

QMenuBar {
    background-color: #1e1e1e;
    border-bottom: 1px solid #333333;
    padding: 2px 4px;
}

QMenuBar::item {
    background-color: transparent;
    padding: 4px 8px;
    border-radius: 2px;
}

QMenuBar::item:selected {
    background-color: #2e2e2e;
    color: #e5a823;
}

QMenu {
    background-color: #242424;
    border: 1px solid #3d3d3d;
    padding: 4px 0px;
}

QMenu::item {
    padding: 4px 24px 4px 16px;
}

QMenu::item:selected {
    background-color: #e5a823;
    color: #121212;
}

QMenu::separator {
    height: 1px;
    background-color: #383838;
    margin: 4px 6px;
}

QMenu::section {
    color: #e5a823;
    background-color: #1c1c1c;
    padding: 5px 16px 3px 16px;
    font-size: 10px;
    font-weight: bold;
    letter-spacing: 0.5px;
}

/* Toolbars */
QToolBar {
    background-color: #222222;
    border: none;
    border-bottom: 1px solid #333333;
    spacing: 4px;
    padding: 3px 6px;
}

/* Buttons */
QPushButton {
    background-color: #2c2c2c;
    color: #d8d8d8;
    border: 1px solid #3d3d3d;
    border-radius: 2px;
    padding: 3px 8px;
    min-height: 18px;
    font-weight: 500;
}

QPushButton:hover {
    background-color: #383838;
    border-color: #555555;
    color: #ffffff;
}

QPushButton:pressed {
    background-color: #e5a823;
    color: #121212;
    border-color: #e5a823;
}

QPushButton:checked {
    background-color: #e5a823;
    color: #121212;
    border-color: #f0b83b;
    font-weight: bold;
}

/* Discrete Channel / Filter Buttons */
QPushButton#channelBtn {
    min-width: 24px;
    padding: 2px 6px;
    font-size: 10px;
}

QPushButton#channelBtn:checked {
    background-color: #e5a823;
    color: #111111;
}

/* Labels */
QLabel {
    background-color: transparent;
    color: #b0b0b0;
}

QLabel#titleLabel {
    color: #e5a823;
    font-weight: bold;
}

QLabel#valueLabel {
    color: #ffffff;
    font-family: "Consolas", "Courier New", monospace;
    font-weight: bold;
}

/* Numeric Inputs / SpinBoxes */
QSpinBox, QDoubleSpinBox {
    background-color: #161616;
    color: #f0f0f0;
    border: 1px solid #3d3d3d;
    border-radius: 2px;
    padding: 2px 4px;
    font-family: "Consolas", "Courier New", monospace;
    font-size: 11px;
}

QSpinBox:focus, QDoubleSpinBox:focus {
    border-color: #e5a823;
}

/* Dropdowns / ComboBoxes */
QComboBox {
    background-color: #262626;
    color: #e0e0e0;
    border: 1px solid #3d3d3d;
    border-radius: 2px;
    padding: 2px 8px;
    min-height: 18px;
}

QComboBox:hover {
    border-color: #555555;
}

QComboBox:focus {
    border-color: #e5a823;
}

QComboBox::drop-down {
    subcontrol-origin: border;
    subcontrol-position: top right;
    width: 15px;
    border-left: 1px solid #3d3d3d;
    background-color: #262626;
}

QComboBox::drop-down:hover {
    background-color: #383838;
}

QComboBox QAbstractItemView {
    background-color: #242424;
    border: 1px solid #444444;
    selection-background-color: #e5a823;
    selection-color: #121212;
}

/* ToolTips */
QToolTip {
    background-color: #242426;
    color: #e0e0e0;
    border: 1px solid #444448;
    border-radius: 3px;
    padding: 4px 8px;
    font-size: 11px;
}

/* Sliders */
QSlider:horizontal {
    min-height: 22px;
    max-height: 22px;
}

QSlider#nukeSlider {
    min-height: 34px;
    max-height: 34px;
    height: 34px;
    border: none;
    background: transparent;
}

QSlider#nukeSlider::groove:horizontal {
    height: 0px;
    background: transparent;
    border: none;
}

QSlider#nukeSlider::handle:horizontal {
    width: 0px;
    height: 0px;
    background: transparent;
    border: none;
}

QSlider::groove:horizontal {
    height: 4px;
    background: #252525;
    border: 1px solid #383838;
    border-radius: 2px;
}

QSlider::handle:horizontal {
    background: #b0b0b0;
    border: 1px solid #161616;
    width: 12px;
    height: 16px;
    margin: -6px 0;
    border-radius: 2px;
}

QSlider::handle:horizontal:hover {
    background: #e5a823;
    border-color: #f0b83b;
}

QSlider::handle:horizontal:pressed {
    background: #f0b83b;
}

/* Status Bar */
QStatusBar {
    background-color: #181818;
    border-top: 1px solid #2d2d2d;
    color: #999999;
    font-size: 10px;
    min-height: 22px;
}

QStatusBar::item {
    border: none;
    padding: 0px 8px;
}

/* Modern Minimal Rounded ScrollBars (Floating 6px capsule pill) */
QScrollBar:vertical {
    background: transparent;
    width: 6px;
    margin: 0px;
}

QScrollBar::handle:vertical {
    background: #3d3d3d;
    min-height: 24px;
    border-radius: 3px;
}

QScrollBar::handle:vertical:hover, QScrollBar::handle:vertical:pressed {
    background: #e5a823;
}

QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0px;
    width: 0px;
    border: none;
    background: none;
}

QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
    background: transparent;
}

QScrollBar:horizontal {
    background: transparent;
    height: 6px;
    margin: 0px;
}

QScrollBar::handle:horizontal {
    background: #3d3d3d;
    min-width: 24px;
    border-radius: 3px;
}

QScrollBar::handle:horizontal:hover, QScrollBar::handle:horizontal:pressed {
    background: #e5a823;
}

QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
    height: 0px;
    width: 0px;
    border: none;
    background: none;
}

QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {
    background: transparent;
}

QScrollBar::corner {
    background: transparent;
}

/* Liquid Interactive Dock & Splitter Dividers */
QMainWindow::separator {
    background: #232323;
    width: 6px;
    height: 6px;
    border: none;
}

QMainWindow::separator:hover {
    background: #e5a823;
}

QSplitter::handle {
    background: #232323;
}

QSplitter::handle:hover {
    background: #e5a823;
}

/* Tabified Dock Tabs */
QDockWidget {
    color: #d8d8d8;
}

QTabBar::tab {
    background: #1e1e1e;
    color: #888888;
    border: 1px solid #333333;
    border-bottom: 2px solid transparent;
    padding: 4px 12px;
    font-size: 11px;
    font-weight: bold;
    min-width: 60px;
}

QTabBar::tab:selected {
    background: #282828;
    color: #e5a823;
    border-bottom: 2px solid #e5a823;
}

QTabBar::tab:hover:!selected {
    background: #242424;
    color: #cccccc;
}

/* Color Dialog (QColorDialog) */
QColorDialog {
    background-color: #1e1e1e;
}

QColorDialog QPushButton {
    background-color: #2c2c2c;
    color: #e0e0e0;
    border: 1px solid #444444;
    border-radius: 3px;
    padding: 4px 12px;
    min-height: 22px;
    min-width: 60px;
}

QColorDialog QPushButton:hover {
    background-color: #383838;
    border-color: #e5a823;
    color: #ffffff;
}

QColorDialog QLineEdit, QColorDialog QSpinBox {
    background-color: #141414;
    color: #ffffff;
    border: 1px solid #3d3d3d;
    border-radius: 2px;
    padding: 2px 4px;
}
"""


def get_spinbox_arrow_stylesheet() -> str:
    """Return QSS subcontrol rules for QSpinBox and QDoubleSpinBox up/down buttons and solid triangle arrows."""
    from openreview.resources import get_spin_up_icon_path, get_spin_down_icon_path
    up_p = get_spin_up_icon_path(pressed=False).as_posix()
    up_pressed_p = get_spin_up_icon_path(pressed=True).as_posix()
    down_p = get_spin_down_icon_path(pressed=False).as_posix()
    down_pressed_p = get_spin_down_icon_path(pressed=True).as_posix()
    return f"""
/* SpinBox Up/Down Buttons & Solid Triangle Arrows */
QSpinBox::up-button, QDoubleSpinBox::up-button {{
    subcontrol-origin: border;
    subcontrol-position: top right;
    width: 15px;
    background-color: #262626;
    border-left: 1px solid #3d3d3d;
    border-bottom: 1px solid #333333;
}}
QSpinBox::up-button:hover, QDoubleSpinBox::up-button:hover {{
    background-color: #383838;
}}
QSpinBox::up-button:pressed, QDoubleSpinBox::up-button:pressed {{
    background-color: #e5a823;
}}
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {{
    image: url("{up_p}");
    width: 8px;
    height: 6px;
}}
QSpinBox::up-arrow:pressed, QDoubleSpinBox::up-arrow:pressed {{
    image: url("{up_pressed_p}");
}}
QSpinBox::down-button, QDoubleSpinBox::down-button {{
    subcontrol-origin: border;
    subcontrol-position: bottom right;
    width: 15px;
    background-color: #262626;
    border-left: 1px solid #3d3d3d;
}}
QSpinBox::down-button:hover, QDoubleSpinBox::down-button:hover {{
    background-color: #383838;
}}
QSpinBox::down-button:pressed, QDoubleSpinBox::down-button:pressed {{
    background-color: #e5a823;
}}
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {{
    image: url("{down_p}");
    width: 8px;
    height: 6px;
}}
QSpinBox::down-arrow:pressed, QDoubleSpinBox::down-arrow:pressed {{
    image: url("{down_pressed_p}");
}}
QComboBox::drop-down {{
    subcontrol-origin: border;
    subcontrol-position: top right;
    width: 15px;
    border-left: 1px solid #3d3d3d;
    background-color: #262626;
}}
QComboBox::drop-down:hover {{
    background-color: #383838;
}}
QComboBox::down-arrow {{
    image: url("{down_p}");
    width: 8px;
    height: 6px;
}}
"""


def get_3dequalizer_stylesheet() -> str:
    """Return the complete 3DEqualizer dark theme stylesheet with high-visibility branch and spinbox indicators."""
    from openreview.resources import get_branch_closed_icon_path, get_branch_open_icon_path
    closed_p = get_branch_closed_icon_path().as_posix()
    open_p = get_branch_open_icon_path().as_posix()
    branch_css = f"""
/* QTreeView High-Visibility Bright Amber Branch Indicators */
QTreeView::branch:has-children:!has-siblings:closed,
QTreeView::branch:closed:has-children:has-siblings {{
    border-image: none;
    image: url("{closed_p}");
}}
QTreeView::branch:open:has-children:!has-siblings,
QTreeView::branch:open:has-children:has-siblings {{
    border-image: none;
    image: url("{open_p}");
}}
"""
    return THEME_STYLESHEET + branch_css + get_spinbox_arrow_stylesheet()


