"""
Generated Studio Vector SVG Icons for OpenReview UI.
Replaces all OS emojis with crisp, resolution-independent SVG icons.
"""
from __future__ import annotations

from typing import Dict, Optional
from PySide6.QtCore import QByteArray, QSize, Qt
from PySide6.QtGui import QIcon, QPixmap, QPainter
from PySide6.QtSvg import QSvgRenderer

SVG_TEMPLATES: Dict[str, str] = {
    "histogram": """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">
        <rect x="2" y="11" width="3.5" height="10" fill="{color}" rx="0.6"/>
        <rect x="7.5" y="5" width="3.5" height="16" fill="{color}" rx="0.6"/>
        <rect x="13" y="13" width="3.5" height="8" fill="{color}" rx="0.6"/>
        <rect x="18.5" y="2" width="3.5" height="19" fill="{color}" rx="0.6"/>
    </svg>""",

    "waveform": """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">
        <path d="M1 12 C3 4, 6 20, 9 12 C12 4, 15 20, 18 12 C20 7, 22 17, 23 12" 
              fill="none" stroke="{color}" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/>
    </svg>""",

    "vectorscope": """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">
        <circle cx="12" cy="12" r="9.5" fill="none" stroke="{color}" stroke-width="1.8"/>
        <circle cx="12" cy="12" r="5" fill="none" stroke="{color}" stroke-width="1" stroke-dasharray="2,2"/>
        <line x1="12" y1="2.5" x2="12" y2="21.5" stroke="{color}" stroke-width="1.2"/>
        <line x1="2.5" y1="12" x2="21.5" y2="12" stroke="{color}" stroke-width="1.2"/>
        <circle cx="12" cy="12" r="1.5" fill="{color}"/>
    </svg>""",

    "color": """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">
        <path d="M12 3 A9 9 0 0 0 3 12 A9 9 0 0 0 12 21 C13.5 21 14.5 20 14.5 18.5 C14.5 17.8 14.2 17.2 13.8 16.7 C13.4 16.2 13.2 15.6 13.2 15 C13.2 13.9 14.1 13 15.2 13 H17 C19.8 13 22 10.8 22 8 C22 5.2 17.5 3 12 3 Z" 
              fill="none" stroke="{color}" stroke-width="1.8"/>
        <circle cx="7.5" cy="10" r="1.3" fill="{color}"/>
        <circle cx="11" cy="7.5" r="1.3" fill="{color}"/>
        <circle cx="15.5" cy="8.5" r="1.3" fill="{color}"/>
        <circle cx="8" cy="14.5" r="1.3" fill="{color}"/>
    </svg>""",

    "inspector": """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">
        <circle cx="12" cy="12" r="7.5" fill="none" stroke="{color}" stroke-width="2"/>
        <line x1="12" y1="2" x2="12" y2="6.5" stroke="{color}" stroke-width="2" stroke-linecap="round"/>
        <line x1="12" y1="17.5" x2="12" y2="22" stroke="{color}" stroke-width="2" stroke-linecap="round"/>
        <line x1="2" y1="12" x2="6.5" y2="12" stroke="{color}" stroke-width="2" stroke-linecap="round"/>
        <line x1="17.5" y1="12" x2="22" y2="12" stroke="{color}" stroke-width="2" stroke-linecap="round"/>
        <circle cx="12" cy="12" r="2" fill="{color}"/>
    </svg>""",

    "details": """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">
        <circle cx="12" cy="12" r="9" fill="none" stroke="{color}" stroke-width="2"/>
        <circle cx="12" cy="7.5" r="1.3" fill="{color}"/>
        <line x1="12" y1="11" x2="12" y2="16.5" stroke="{color}" stroke-width="2" stroke-linecap="round"/>
        <line x1="10" y1="11" x2="12" y2="11" stroke="{color}" stroke-width="2" stroke-linecap="round"/>
    </svg>""",

    "matrix": """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">
        <rect x="3" y="3" width="7.5" height="7.5" rx="1.5" fill="{color}"/>
        <rect x="13.5" y="3" width="7.5" height="7.5" rx="1.5" fill="{color}" opacity="0.45"/>
        <rect x="3" y="13.5" width="7.5" height="7.5" rx="1.5" fill="{color}" opacity="0.45"/>
        <rect x="13.5" y="13.5" width="7.5" height="7.5" rx="1.5" fill="{color}"/>
    </svg>""",

    "layers": """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">
        <polygon points="12,2 22,7 12,12 2,7" fill="{color}" opacity="0.9"/>
        <polyline points="2,12 12,17 22,12" fill="none" stroke="{color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
        <polyline points="2,17 12,22 22,17" fill="none" stroke="{color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
    </svg>""",

    "notes": """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">
        <path d="M14 2 H6 A2 2 0 0 0 4 4 V20 A2 2 0 0 0 6 22 H18 A2 2 0 0 0 20 20 V8 Z" 
              fill="none" stroke="{color}" stroke-width="2"/>
        <polyline points="14,2 14,8 20,8" fill="none" stroke="{color}" stroke-width="2"/>
        <line x1="8" y1="13" x2="16" y2="13" stroke="{color}" stroke-width="1.8" stroke-linecap="round"/>
        <line x1="8" y1="17" x2="14" y2="17" stroke="{color}" stroke-width="1.8" stroke-linecap="round"/>
    </svg>""",

    "review": """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">
        <rect x="2" y="3" width="20" height="14" rx="2" fill="none" stroke="{color}" stroke-width="2"/>
        <line x1="8" y1="21" x2="16" y2="21" stroke="{color}" stroke-width="2" stroke-linecap="round"/>
        <line x1="12" y1="17" x2="12" y2="21" stroke="{color}" stroke-width="2"/>
    </svg>""",

    "cinema": """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">
        <rect x="2" y="5" width="20" height="14" rx="2" fill="none" stroke="{color}" stroke-width="2"/>
        <line x1="2" y1="9" x2="22" y2="9" stroke="{color}" stroke-width="1.6"/>
        <line x1="6.5" y1="5" x2="5.5" y2="9" stroke="{color}" stroke-width="1.6"/>
        <line x1="11.5" y1="5" x2="10.5" y2="9" stroke="{color}" stroke-width="1.6"/>
        <line x1="16.5" y1="5" x2="15.5" y2="9" stroke="{color}" stroke-width="1.6"/>
        <polygon points="10,12 15,14.5 10,17" fill="{color}"/>
    </svg>""",

    "timeline": """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">
        <rect x="2" y="3" width="20" height="18" rx="2" fill="none" stroke="{color}" stroke-width="1.8"/>
        <line x1="2" y1="9" x2="22" y2="9" stroke="{color}" stroke-width="1.4"/>
        <line x1="2" y1="15" x2="22" y2="15" stroke="{color}" stroke-width="1.4"/>
        <rect x="5" y="10.5" width="6" height="3" rx="0.8" fill="{color}"/>
        <rect x="13" y="10.5" width="7" height="3" rx="0.8" fill="{color}" opacity="0.6"/>
        <rect x="7" y="16.5" width="8" height="3" rx="0.8" fill="{color}" opacity="0.8"/>
        <line x1="12" y1="3" x2="12" y2="21" stroke="{color}" stroke-width="1.8"/>
    </svg>""",

    "spreadsheet": """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">
        <rect x="3" y="3" width="18" height="18" rx="2" fill="none" stroke="{color}" stroke-width="1.8"/>
        <line x1="3" y1="8" x2="21" y2="8" stroke="{color}" stroke-width="1.4"/>
        <line x1="3" y1="14" x2="21" y2="14" stroke="{color}" stroke-width="1.4"/>
        <line x1="9" y1="3" x2="9" y2="21" stroke="{color}" stroke-width="1.4"/>
        <line x1="15" y1="3" x2="15" y2="21" stroke="{color}" stroke-width="1.4"/>
    </svg>""",

    "select": """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">
        <path d="M4 3 L4 19 L8.5 15.5 L12 22 L14.5 20.5 L11 14.5 L16.5 14 Z" fill="{color}" stroke="{color}" stroke-width="0.8"/>
    </svg>""",

    "razor": """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">
        <rect x="3" y="6" width="18" height="12" rx="1.5" fill="none" stroke="{color}" stroke-width="1.8"/>
        <circle cx="6" cy="12" r="1.5" fill="{color}"/>
        <circle cx="18" cy="12" r="1.5" fill="{color}"/>
        <path d="M8.5 12 H15.5 M10 9.5 V14.5 M14 9.5 V14.5" stroke="{color}" stroke-width="1.6" stroke-linecap="round"/>
    </svg>""",

    "snap": """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">
        <path d="M4 4 V11 C4 15.4 7.6 19 12 19 C16.4 19 20 15.4 20 11 V4 H15 V11 C15 12.7 13.7 14 12 14 C10.3 14 9 12.7 9 11 V4 Z" fill="{color}"/>
        <rect x="4" y="4" width="5" height="3" fill="#ffffff" opacity="0.6"/>
        <rect x="15" y="4" width="5" height="3" fill="#ffffff" opacity="0.6"/>
    </svg>""",

    "delete": """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">
        <path d="M6 7 V19 C6 20.1 6.9 21 8 21 H16 C17.1 21 18 20.1 18 19 V7 Z" fill="none" stroke="{color}" stroke-width="1.8"/>
        <line x1="4" y1="7" x2="20" y2="7" stroke="{color}" stroke-width="1.8" stroke-linecap="round"/>
        <path d="M9 7 V4 C9 3.4 9.4 3 10 3 H14 C14.6 3 15 3.4 15 4 V7" fill="none" stroke="{color}" stroke-width="1.8"/>
        <line x1="10" y1="11" x2="10" y2="17" stroke="{color}" stroke-width="1.6" stroke-linecap="round"/>
        <line x1="14" y1="11" x2="14" y2="17" stroke="{color}" stroke-width="1.6" stroke-linecap="round"/>
    </svg>""",

    "slip": """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">
        <rect x="7" y="4" width="10" height="16" rx="1.5" fill="none" stroke="{color}" stroke-width="1.8"/>
        <polyline points="5,9 2,12 5,15" fill="none" stroke="{color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
        <polyline points="19,9 22,12 19,15" fill="none" stroke="{color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
        <line x1="2" y1="12" x2="7" y2="12" stroke="{color}" stroke-width="1.8"/>
        <line x1="17" y1="12" x2="22" y2="12" stroke="{color}" stroke-width="1.8"/>
    </svg>""",

    "slide": """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">
        <rect x="8" y="5" width="8" height="14" rx="1.5" fill="{color}" opacity="0.8"/>
        <polyline points="5,9 2,12 5,15" fill="none" stroke="{color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
        <polyline points="19,9 22,12 19,15" fill="none" stroke="{color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
        <line x1="2" y1="12" x2="22" y2="12" stroke="{color}" stroke-width="1.6"/>
    </svg>""",

    "marker": """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">
        <line x1="6" y1="3" x2="6" y2="21" stroke="{color}" stroke-width="2" stroke-linecap="round"/>
        <path d="M6 4 H18 L15 8.5 L18 13 H6 Z" fill="{color}"/>
    </svg>""",

    "prev_cut": """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">
        <rect x="3.5" y="4.5" width="2.5" height="15" rx="1" fill="{color}"/>
        <path d="M20 5.5 L8.5 12 L20 18.5 Z" fill="{color}"/>
    </svg>""",

    "next_cut": """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">
        <rect x="18" y="4.5" width="2.5" height="15" rx="1" fill="{color}"/>
        <path d="M4 5.5 L15.5 12 L4 18.5 Z" fill="{color}"/>
    </svg>""",

    "play": """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">
        <path d="M7 4.5 L20 12 L7 19.5 Z" fill="{color}"/>
    </svg>""",

    "pause": """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">
        <rect x="5.5" y="4.5" width="4" height="15" rx="1" fill="{color}"/>
        <rect x="14.5" y="4.5" width="4" height="15" rx="1" fill="{color}"/>
    </svg>""",

    "arrow_left": """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">
        <path d="M15 5 L8 12 L15 19" stroke="{color}" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" fill="none"/>
    </svg>""",

    "arrow_right": """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">
        <path d="M9 5 L16 12 L9 19" stroke="{color}" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" fill="none"/>
    </svg>""",

    "eye": """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">
        <path d="M2 12s4-7 10-7 10 7 10 7-4 7-10 7-10-7-10-7z" stroke="{color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" fill="none"/>
        <circle cx="12" cy="12" r="3" fill="{color}"/>
    </svg>""",

    "eye_off": """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">
        <path d="M17.94 17.94A10.07 10.07 0 0 1 12 19c-6 0-10-7-10-7a19.78 19.78 0 0 1 4.54-5.26M9.9 4.24A9.12 9.12 0 0 1 12 4c6 0 10 7 10 7a19.5 19.5 0 0 1-2.58 3.5" stroke="{color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" fill="none"/>
        <line x1="2" y1="2" x2="22" y2="22" stroke="{color}" stroke-width="2" stroke-linecap="round"/>
    </svg>""",

    "lock": """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">
        <rect x="5" y="10.5" width="14" height="11" rx="2" fill="{color}"/>
        <path d="M8 10.5 V7 a4 4 0 0 1 8 0 v3.5" stroke="{color}" stroke-width="2.2" fill="none" stroke-linecap="round"/>
    </svg>""",

    "lock_open": """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">
        <rect x="5" y="10.5" width="14" height="11" rx="2" fill="{color}"/>
        <path d="M8 10.5 V7 a4 4 0 0 1 8 0 v1" stroke="{color}" stroke-width="2.2" fill="none" stroke-linecap="round"/>
    </svg>""",

    "settings": """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">
        <path d="M12 15.5 A3.5 3.5 0 1 0 12 8.5 A3.5 3.5 0 1 0 12 15.5 Z" fill="none" stroke="{color}" stroke-width="2"/>
        <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" fill="none" stroke="{color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
    </svg>""",

    "gear": """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">
        <path d="M12 15.5 A3.5 3.5 0 1 0 12 8.5 A3.5 3.5 0 1 0 12 15.5 Z" fill="none" stroke="{color}" stroke-width="2"/>
        <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" fill="none" stroke="{color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
    </svg>""",

    "link": """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">
        <path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71" fill="none" stroke="{color}" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/>
        <path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71" fill="none" stroke="{color}" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/>
    </svg>""",

    "unlink": """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">
        <path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71" fill="none" stroke="{color}" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/>
        <path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71" fill="none" stroke="{color}" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/>
        <line x1="2" y1="2" x2="22" y2="22" stroke="#e74c3c" stroke-width="2.5" stroke-linecap="round"/>
    </svg>""",
}

_ICON_CACHE: Dict[str, QIcon] = {}
_PIXMAP_CACHE: Dict[str, QPixmap] = {}


def get_svg_pixmap(name: str, color: str = "#e5a823", size: int = 14) -> QPixmap:
    """Render an SVG template to a high-DPI crisp QPixmap."""
    cache_key = f"{name}_{color}_{size}"
    if cache_key in _PIXMAP_CACHE:
        return _PIXMAP_CACHE[cache_key]

    template = SVG_TEMPLATES.get(name.lower(), SVG_TEMPLATES["inspector"])
    svg_str = template.format(color=color)
    renderer = QSvgRenderer(QByteArray(svg_str.encode("utf-8")))

    pix = QPixmap(size, size)
    pix.fill(Qt.GlobalColor.transparent)

    painter = QPainter(pix)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    renderer.render(painter)
    painter.end()

    _PIXMAP_CACHE[cache_key] = pix
    return pix


def get_svg_icon(name: str, color: str = "#e5a823", size: int = 14) -> QIcon:
    """Return a QIcon for the named studio SVG icon."""
    cache_key = f"{name}_{color}_{size}"
    if cache_key in _ICON_CACHE:
        return _ICON_CACHE[cache_key]

    pix = get_svg_pixmap(name, color=color, size=size)
    icon = QIcon(pix)
    _ICON_CACHE[cache_key] = icon
    return icon
