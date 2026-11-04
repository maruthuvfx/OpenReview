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
