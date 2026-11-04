"""
3DEqualizer-Style Utilitarian Timeline Widget.
Features:
- High-precision frame ruler with sub-frame ticks and In/Out brackets (positioned above transport).
- Interactive In/Out draggable bracket handles with resize cursor.
- Clickable Amber Keyframe Diamonds (Marker Flags) for annotated frames.
- Compact transport bar with integrated miniaturized RAM cache bar next to playback buttons.
- "Clear In/Out" button and Tri-state Loop Mode (Cycle, Ping-Pong, Once).
- Monospace frame readouts and FPS counter.
"""
from __future__ import annotations

import math
from typing import Optional, Set
from PySide6.QtCore import Qt, QRectF, QPointF, QPoint, Signal, QTimer, QEvent
from PySide6.QtGui import QPainter, QColor, QPen, QFont, QPolygonF, QPixmap, QPainterPath, QLinearGradient, QCursor
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QPushButton,
    QLabel,
    QSpinBox,
    QDoubleSpinBox,
    QComboBox,
    QFrame,
    QMenu,
    QLineEdit,
    QMessageBox,
)
from openreview.core.clock import PlaybackClock
from openreview.models.annotations import AnnotationStore
from openreview.resources.svg_icons import get_svg_icon


class TimelineRuler(QWidget):
    """
    Interactive custom-painted frame ruler:
    - Draggable In and Out boundary bracket handles
    - Clickable amber keyframe diamonds (marker flags)
    - Playhead scrubbing
    """

    seek_requested = Signal(int)
    seek_finished = Signal(int)

    def __init__(
        self,
        clock: PlaybackClock,
        annotation_store: Optional[AnnotationStore] = None,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.clock = clock
        self.annotation_store = annotation_store
        self.setFixedHeight(30)
        self.setMouseTracking(True)

        self._drag_mode: Optional[str] = None  # 'playhead', 'in_handle', 'out_handle', 'range_slide'
        self._drag_start_x = 0.0
        self._drag_start_in = clock.in_frame
        self._drag_start_out = clock.out_frame
        self._bg_cache: Optional[QPixmap] = None

        self.clock.frame_changed.connect(lambda _: self.update())
        self.clock.range_changed.connect(lambda _s, _e: self._invalidate_bg_cache())
        self.clock.in_out_changed.connect(lambda _i, _o: self._invalidate_bg_cache())
        self.clock.bookmarks_changed.connect(lambda _: self._invalidate_bg_cache())
        self.zoom_factor: float = 1.0
        self._cached_frames: Set[int] = set()
        self._ram_cached_frames: Set[int] = set()
        self._disk_cached_frames: Set[int] = set()

        if self.annotation_store:
            self.annotation_store.annotations_changed.connect(lambda _: self._invalidate_bg_cache())

    def _invalidate_bg_cache(self) -> None:
        """Invalidate static track background cache and trigger repaint."""
        self._bg_cache = None
        self.update()

    def resizeEvent(self, event) -> None:
        self._bg_cache = None
        super().resizeEvent(event)

    def set_cached_frames(self, frames: Set[int], disk_frames: Optional[Set[int]] = None) -> None:
        """Update cached frame sets (RAM and Temp Disk) and repaint timeline indicator."""
        self._ram_cached_frames = set(frames)
        if disk_frames is not None:
            self._disk_cached_frames = set(disk_frames)
        self._cached_frames = self._ram_cached_frames | self._disk_cached_frames
        self._invalidate_bg_cache()

    def set_disk_cached_frames(self, disk_frames: Set[int]) -> None:
        """Update temp disk cached frame set and repaint timeline indicator."""
        self._disk_cached_frames = set(disk_frames)
        self._cached_frames = self._ram_cached_frames | self._disk_cached_frames
        self._invalidate_bg_cache()

    def set_zoom_factor(self, factor: float) -> None:
        """Set timeline magnification factor (1.0 to 10.0x)."""
        self.zoom_factor = max(1.0, min(10.0, float(factor)))
        self._invalidate_bg_cache()

    def zoom_in(self) -> None:
        """Magnify timeline around playhead."""
        self.set_zoom_factor(self.zoom_factor * 1.5)

    def zoom_out(self) -> None:
        """De-magnify timeline."""
        self.set_zoom_factor(self.zoom_factor / 1.5)

    def reset_zoom(self) -> None:
        """Reset timeline magnification to full sequence range."""
        self.set_zoom_factor(1.0)

    def set_annotation_store(self, store: AnnotationStore) -> None:
        self.annotation_store = store
        self.annotation_store.annotations_changed.connect(lambda _: self._invalidate_bg_cache())
        self._invalidate_bg_cache()

    def _clear_annotations_with_confirm(self) -> None:
        parent_tl = self.parent()
        while parent_tl and not isinstance(parent_tl, TimelineWidget):
            parent_tl = parent_tl.parent()
        if parent_tl and hasattr(parent_tl, "request_clear_all_annotations"):
            parent_tl.request_clear_all_annotations()
            return

        ret = QMessageBox.question(
            self,
            "Clear All Annotations",
            "Are you sure you want to clear all drawing annotations and notes across all frames?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if ret == QMessageBox.StandardButton.Yes:
            if self.annotation_store:
                self.annotation_store.clear_all()
            self._invalidate_bg_cache()
            self.update()

    def create_context_menu(self) -> QMenu:
        """Create and populate the timeline right-click context menu."""
        parent_tl = self.parent()
        while parent_tl and not isinstance(parent_tl, TimelineWidget):
            parent_tl = parent_tl.parent()

        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu {
                background-color: #1e1e22;
                border: 1px solid #3a3a42;
                color: #dddddd;
                padding: 4px;
            }
            QMenu::item {
                padding: 5px 22px 5px 20px;
                border-radius: 2px;
            }
            QMenu::item:selected {
                background-color: #383842;
                color: #ffffff;
            }
            QMenu::separator {
                height: 1px;
                background-color: #333338;
                margin: 4px 6px;
            }
        """)

        # 1. Toggle Timeline Magnifier (Shift+T)
        action_mag = menu.addAction("Toggle Timeline Magnifier\tShift+T")
        if parent_tl:
            action_mag.triggered.connect(parent_tl.toggle_magnifier_widget)

        menu.addSeparator()

        # 3. Mark In ([)
        action_in = menu.addAction("Mark In\t[")
        action_in.triggered.connect(lambda: (self.clock.set_in_mark(self.clock.current_frame), self.update()))

        # 4. Mark Out (])
        action_out = menu.addAction("Mark Out\t]")
        action_out.triggered.connect(lambda: (self.clock.set_out_mark(self.clock.current_frame), self.update()))

        # 5. Mark Sequence Boundaries
        action_bounds = menu.addAction("Mark Sequence Boundaries")
        action_bounds.triggered.connect(lambda: (self.clock.mark_sequence_boundaries(), self.update()))

        # 6. Clear In and Out (\)
        action_clear_io = menu.addAction("Clear In and Out\t\\")
        action_clear_io.triggered.connect(lambda: (self.clock.clear_in_out(), self.update()))

        menu.addSeparator()

        # 7. Toggle Bookmark (M)
        has_bm = self.clock.current_frame in self.clock.bookmarks
        action_bm = menu.addAction("Remove Bookmark\tM" if has_bm else "Toggle Bookmark\tM")
        action_bm.triggered.connect(lambda: (self.clock.toggle_bookmark(self.clock.current_frame), self.update()))

        menu.addSeparator()

        # 8. Clear Cache
        action_clear_cache = menu.addAction("Clear Cache")
        if parent_tl and hasattr(parent_tl, "clear_cache_requested"):
            action_clear_cache.triggered.connect(parent_tl.clear_cache_requested.emit)

        # 9. Clear All Annotations
        action_clear_ann = menu.addAction("Clear All Annotations...")
        if parent_tl and hasattr(parent_tl, "request_clear_all_annotations"):
            action_clear_ann.triggered.connect(parent_tl.request_clear_all_annotations)
        else:
            action_clear_ann.triggered.connect(self._clear_annotations_with_confirm)

        return menu

    def contextMenuEvent(self, event) -> None:
        menu = self.create_context_menu()
        self._last_context_menu = menu
        menu.exec(event.globalPos())

    def _get_visible_range(self) -> tuple[float, float]:
        start = float(self.clock.start_frame)
        end = float(self.clock.end_frame)
        if self.zoom_factor <= 1.001:
            return start, end
        total_span = max(1.0, end - start)
        visible_span = total_span / self.zoom_factor
        curr = float(self.clock.current_frame)
        v_start = max(start, curr - visible_span / 2.0)
        v_end = v_start + visible_span
        if v_end > end:
            v_end = end
            v_start = max(start, v_end - visible_span)
        return v_start, v_end

    def _frame_to_x(self, frame: int) -> float:
        v_start, v_end = self._get_visible_range()
        span = max(1.0, v_end - v_start)
        pct = (float(frame) - v_start) / span
        return 12.0 + pct * (self.width() - 24.0)

    def _x_to_frame(self, x: float) -> int:
        v_start, v_end = self._get_visible_range()
        span = max(1.0, v_end - v_start)
        w = max(1.0, self.width() - 24.0)
        pct = max(0.0, min(1.0, (x - 12.0) / w))
        return int(round(v_start + pct * span))

    # --------------------------------------------------------------------------
    # Mouse Interaction (Dragging In/Out Handles, Markers, & Playhead)
    # --------------------------------------------------------------------------
    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            x = event.position().x()
            y = event.position().y()
            self._drag_start_x = x
            self._drag_start_in = self.clock.in_frame
            self._drag_start_out = self.clock.out_frame

            # Ruler lane interactions
            in_x = self._frame_to_x(self.clock.in_frame)
            out_x = self._frame_to_x(self.clock.out_frame)

            # Check if clicked near an amber keyframe diamond marker
            if self.annotation_store:
                for marker_frame in self.annotation_store.get_annotated_frames():
                    mx = self._frame_to_x(marker_frame)
                    if abs(x - mx) <= 6:
                        self.clock.set_frame(marker_frame)
                        self.seek_requested.emit(marker_frame)
                        self.update()
                        return

            # Check if clicked near a cyan bookmark diamond
            for bm_frame in self.clock.bookmarks:
                bx = self._frame_to_x(bm_frame)
                if abs(x - bx) <= 6:
                    self.clock.set_frame(bm_frame)
                    self.seek_requested.emit(bm_frame)
                    self.update()
                    return

            # Check if clicked on In handle ([)
            if abs(x - in_x) <= 6:
                self._drag_mode = "in_handle"
                return

            # Check if clicked on Out handle (])
            if abs(x - out_x) <= 6:
                self._drag_mode = "out_handle"
                return

            # Check if clicked inside range with Alt held -> slide entire range
            if (event.modifiers() & Qt.KeyboardModifier.AltModifier) and in_x < x < out_x:
                self._drag_mode = "range_slide"
                return

            # Otherwise: seek playhead
            self._drag_mode = "playhead"
            f = self._x_to_frame(x)
            self.clock.set_frame(f)
            self.seek_requested.emit(f)
            self.update()

    def mouseMoveEvent(self, event) -> None:
        x = event.position().x()
        y = event.position().y()

        # Update cursor on hover when not dragging
        if self._drag_mode is None:
            in_x = self._frame_to_x(self.clock.in_frame)
            out_x = self._frame_to_x(self.clock.out_frame)
            if abs(x - in_x) <= 6 or abs(x - out_x) <= 6:
                self.setCursor(Qt.CursorShape.SizeHorCursor)
            else:
                self.setCursor(Qt.CursorShape.ArrowCursor)

        # Handle active drag
        if self._drag_mode == "in_handle":
            new_in = self._x_to_frame(x)
            new_in = min(new_in, self.clock.out_frame)
            self.clock.set_in_out(new_in, self.clock.out_frame)
            self.update()

        elif self._drag_mode == "out_handle":
            new_out = self._x_to_frame(x)
            new_out = max(new_out, self.clock.in_frame)
            self.clock.set_in_out(self.clock.in_frame, new_out)
            self.update()

        elif self._drag_mode == "range_slide":
            delta_frame = self._x_to_frame(x) - self._x_to_frame(self._drag_start_x)
            duration = self._drag_start_out - self._drag_start_in
            new_in = max(self.clock.start_frame, min(self._drag_start_in + delta_frame, self.clock.end_frame - duration))
            new_out = new_in + duration
            self.clock.set_in_out(new_in, new_out)
            self.update()

        elif self._drag_mode == "playhead":
            f = self._x_to_frame(x)
            self.clock.set_frame(f)
            self.seek_requested.emit(f)
            self.update()

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            was_scrubbing = (self._drag_mode == "playhead")
            self._drag_mode = None
            self.setCursor(Qt.CursorShape.ArrowCursor)
            self.update()
            if was_scrubbing:
                self.seek_finished.emit(self.clock.current_frame)

    # --------------------------------------------------------------------------
    # Painting
    # --------------------------------------------------------------------------
    def paintEvent(self, event) -> None:
        w = max(1, self.width())
        h = max(1, self.height())
        dpr = self.devicePixelRatioF()

        if self._bg_cache is None or getattr(self, "_bg_cache_key", None) != (w, h, dpr):
            self._bg_cache = QPixmap(int(round(w * dpr)), int(round(h * dpr)))
            self._bg_cache.setDevicePixelRatio(dpr)
            self._bg_cache_key = (w, h, dpr)
            p = QPainter(self._bg_cache)
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)

            p.fillRect(0, 0, w, h, QColor(22, 22, 22))
            p.setPen(QPen(QColor(51, 51, 51), 1))
            p.drawLine(QPointF(0, 0), QPointF(w, 0))

            # In/Out shaded active range in ruler lane
            in_x = self._frame_to_x(self.clock.in_frame)
            out_x = self._frame_to_x(self.clock.out_frame)
            p.fillRect(QRectF(in_x, 0, max(1.0, out_x - in_x), h), QColor(36, 36, 36, 180))

            # In / Out Bracket Handles (Warm amber with grab handles)
            p.setPen(QPen(QColor(229, 168, 35), 2))
            top_bracket_y = 3.0
            p.drawLine(QPointF(in_x, top_bracket_y), QPointF(in_x, h - 3))
            p.drawLine(QPointF(in_x, top_bracket_y), QPointF(in_x + 5, top_bracket_y))
            p.drawLine(QPointF(in_x, h - 3), QPointF(in_x + 5, h - 3))

            p.drawLine(QPointF(out_x, top_bracket_y), QPointF(out_x, h - 3))
            p.drawLine(QPointF(out_x, top_bracket_y), QPointF(out_x - 5, top_bracket_y))
            p.drawLine(QPointF(out_x, h - 3), QPointF(out_x - 5, h - 3))

            # Traditional Ruler tick marks for all in-between frames
            start = self.clock.start_frame
            end = self.clock.end_frame
            span = max(1, end - start)
            px_per_frame = (w - 24.0) / float(span)

            # Adaptive steps to prevent overlapping frame numbers
            if px_per_frame >= 40.0:
                major_step, num_step = 5, 1
            elif px_per_frame >= 18.0:
                major_step, num_step = 5, 5
            elif px_per_frame >= 6.0:
                major_step, num_step = 10, 10
            elif px_per_frame >= 2.5:
                major_step, num_step = 25, 25
            elif px_per_frame >= 1.0:
                major_step, num_step = 50, 50
            else:
                major_step, num_step = 100, 100

            font = QFont("Consolas", 8)
            font.setStyleHint(QFont.StyleHint.Monospace)
            p.setFont(font)

            for f in range(start, end + 1):
                x = self._frame_to_x(f)
                if x < 0 or x > w:
                    continue

                is_major = (f % major_step == 0)
                is_medium = (f % 5 == 0)

                if is_major:
                    # Major tick mark with crisp frame readout
                    p.setPen(QPen(QColor(135, 135, 140), 1.2))
                    p.drawLine(QPointF(x, h - 14), QPointF(x, h - 2))
                    if f % num_step == 0:
                        p.setPen(QColor(180, 180, 185))
                        text_y = 2.0
                        p.drawText(QRectF(x - 24, text_y, 48, 12), Qt.AlignmentFlag.AlignCenter, str(f))
                elif is_medium:
                    # Medium 5-frame tick
                    p.setPen(QPen(QColor(80, 80, 85), 1.0))
                    p.drawLine(QPointF(x, h - 8), QPointF(x, h - 2))
                elif px_per_frame >= 1.8:
                    # Minor 1-frame tick for all inbetween frames
                    p.setPen(QPen(QColor(52, 52, 52), 1.0))
                    p.drawLine(QPointF(x, h - 4), QPointF(x, h - 2))

            # Clickable Amber Keyframe Diamonds (Annotation Marker Flags)
            diamond_top = 2.0
            if self.annotation_store:
                p.setPen(QPen(QColor(20, 20, 20), 1))
                p.setBrush(QColor(229, 168, 35))  # 3DE amber diamond
                for mf in self.annotation_store.get_annotated_frames():
                    if start <= mf <= end:
                        mx = self._frame_to_x(mf)
                        diamond = QPolygonF([
                            QPointF(mx, diamond_top),
                            QPointF(mx + 3.5, diamond_top + 4.0),
                            QPointF(mx, diamond_top + 10.0),
                            QPointF(mx - 3.5, diamond_top + 4.0),
                        ])
                        p.drawPolygon(diamond)

            # Clickable Cyan Bookmark Diamonds
            p.setPen(QPen(QColor(20, 20, 20), 1))
            p.setBrush(QColor(0, 210, 255))  # Studio cyan diamond
            for bf in self.clock.bookmarks:
                if start <= bf <= end:
                    bx = self._frame_to_x(bf)
                    diamond = QPolygonF([
                        QPointF(bx, diamond_top),
                        QPointF(bx + 3.5, diamond_top + 4.0),
                        QPointF(bx, diamond_top + 10.0),
                        QPointF(bx - 3.5, diamond_top + 4.0),
                    ])
                    p.drawPolygon(diamond)

            # Dual-Color Cached Frames Indicator
            # 1. Temp Drive Disk Frames (Amber/Orange: #e67e22)
            disk_only_frames = self._disk_cached_frames - self._ram_cached_frames
            if disk_only_frames:
                visible_disk = sorted(f for f in disk_only_frames if start <= f <= end)
                if visible_disk:
                    p.setPen(Qt.PenStyle.NoPen)
                    p.setBrush(QColor(230, 126, 34, 230))
                    v_start, v_end = self._get_visible_range()
                    v_span = max(1.0, v_end - v_start)
                    fw = max(1.5, (w - 24.0) / v_span)
                    spans = []
                    current_start = visible_disk[0]
                    prev_frame = current_start
                    for f in visible_disk[1:]:
                        if f == prev_frame + 1:
                            prev_frame = f
                        else:
                            spans.append((current_start, prev_frame))
                            current_start = f
                            prev_frame = f
                    spans.append((current_start, prev_frame))

                    for span_start, span_end in spans:
                        x1 = self._frame_to_x(span_start) - fw / 2.0
                        x2 = self._frame_to_x(span_end) + fw / 2.0
                        p.fillRect(QRectF(x1, h - 5, max(fw, x2 - x1), 3), QColor(230, 126, 34, 230))

            # 2. Tier-1 RAM Frames (Emerald Green: #2ecc71)
            ram_frames = self._ram_cached_frames if self._ram_cached_frames else (self._cached_frames if not self._disk_cached_frames else set())
            if ram_frames:
                visible_ram = sorted(f for f in ram_frames if start <= f <= end)
                if visible_ram:
                    p.setPen(Qt.PenStyle.NoPen)
                    p.setBrush(QColor(46, 204, 113, 230))
                    v_start, v_end = self._get_visible_range()
                    v_span = max(1.0, v_end - v_start)
                    fw = max(1.5, (w - 24.0) / v_span)
                    spans = []
                    current_start = visible_ram[0]
                    prev_frame = current_start
                    for f in visible_ram[1:]:
                        if f == prev_frame + 1:
                            prev_frame = f
                        else:
                            spans.append((current_start, prev_frame))
                            current_start = f
                            prev_frame = f
                    spans.append((current_start, prev_frame))

                    for span_start, span_end in spans:
                        x1 = self._frame_to_x(span_start) - fw / 2.0
                        x2 = self._frame_to_x(span_end) + fw / 2.0
                        p.fillRect(QRectF(x1, h - 5, max(fw, x2 - x1), 3), QColor(46, 204, 113, 230))

            # Bottom baseline
            p.setPen(QPen(QColor(42, 42, 45), 1))
            p.drawLine(0, h - 1, w, h - 1)
            p.end()

        # Blit cached background
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        painter.drawPixmap(0, 0, self._bg_cache)

        curr_x = self._frame_to_x(self.clock.current_frame)

        # Classic 3DE Amber indicator line and top triangle handle
        painter.setPen(QPen(QColor(229, 168, 35), 2))
        painter.drawLine(int(curr_x), 0, int(curr_x), h)

        poly = [
            QPointF(curr_x - 5, 0),
            QPointF(curr_x + 5, 0),
            QPointF(curr_x, 7),
        ]
        painter.setBrush(QColor(229, 168, 35))
        painter.drawPolygon(QPolygonF(poly))


class CompactCacheBar(QWidget):
    """
    Miniaturized RAM cache indicator bar placed directly beside the transport controls.
    Displays cache utilization and active cache mode (OFF, Play-Ahead, Region, Full).
    Clicking cycles through available cache modes.
    """

    cycle_mode_requested = Signal()

    def __init__(self, clock: PlaybackClock, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.clock = clock
        self._cached_frames: Set[int] = set()
        self._disk_cached_frames: Set[int] = set()
        self._percent_used = 0.0
        self._disk_percent_used = 0.0
        self._cache_mode = "off"
        self.setFixedSize(130, 18)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("RAM Cache: OFF (Click or use Cache menu to enable)")

    def update_cache_data(
        self,
        cached_frames: Set[int],
        pct_used: float,
        cache_mode: str = "off",
        disk_cached_frames: Optional[Set[int]] = None,
        disk_pct_used: float = 0.0,
        proxy_cached_frames: Optional[Set[int]] = None,
    ) -> None:
        self._cached_frames = cached_frames
        self._disk_cached_frames = set(disk_cached_frames) if disk_cached_frames is not None else set()
        self._proxy_cached_frames = set(proxy_cached_frames) if proxy_cached_frames is not None else set()
        self._percent_used = pct_used
        self._disk_percent_used = disk_pct_used
        self._cache_mode = cache_mode
        disk_str = f", Temp Disk: {int(disk_pct_used)}%" if disk_pct_used > 0 else ""
        if cache_mode == "off":
            self.setToolTip("RAM Cache: OFF (Click to toggle Play-Ahead / Region Cache)")
        elif cache_mode == "region":
            self.setToolTip(f"Cache: In/Out Region ({int(pct_used)}% RAM{disk_str}) - Click to toggle")
        elif cache_mode == "play_ahead":
            self.setToolTip(f"Cache: Play-Ahead ({int(pct_used)}% RAM{disk_str}) - Click to toggle")
        else:
            self.setToolTip(f"Cache: Full Sequence ({int(pct_used)}% RAM{disk_str}) - Click to toggle")
        self.update()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.cycle_mode_requested.emit()
            event.accept()
        else:
            super().mousePressEvent(event)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        w = self.width()
        h = self.height()

        # Border & background
        painter.fillRect(0, 0, w, h, QColor(20, 20, 20))
        painter.setPen(QPen(QColor(50, 50, 50), 1))
        painter.drawRect(0, 0, w - 1, h - 1)

        font = QFont("Consolas", 8, QFont.Weight.Bold)
        painter.setFont(font)

        if self._cache_mode == "off":
            painter.setPen(QColor(110, 110, 110))
            painter.drawText(0, 0, w, h, Qt.AlignmentFlag.AlignCenter, "Cache: OFF")
            return

        # Draw cached segments
        start = self.clock.start_frame
        end = self.clock.end_frame
        span = max(1, end - start + 1)

        inner_w = w - 4

        # 1. Draw Temp Disk cached frames (Amber)
        if self._disk_cached_frames:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(230, 126, 34, 200))
            for f in self._disk_cached_frames:
                if start <= f <= end:
                    x = 2 + int(((f - start) / float(span)) * inner_w)
                    seg_w = max(1, int(inner_w / float(span)) + 1)
                    painter.fillRect(x, 2, seg_w, h - 4, QColor(230, 126, 34, 200))

        # 1.5. Draw Proxy cached frames (Cyan / Sky Blue)
        proxy_frames = getattr(self, "_proxy_cached_frames", None)
        if proxy_frames:
            proxy_color = QColor(0, 188, 212, 210)  # Cyan
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(proxy_color)
            for f in proxy_frames:
                if start <= f <= end and f not in self._cached_frames:
                    x = 2 + int(((f - start) / float(span)) * inner_w)
                    seg_w = max(1, int(inner_w / float(span)) + 1)
                    painter.fillRect(x, 2, seg_w, h - 4, proxy_color)

        # 2. Draw RAM full-resolution cached frames (Emerald Green)
        painter.setPen(Qt.PenStyle.NoPen)
        seg_color = QColor(46, 204, 113, 220) if self._cache_mode != "region" else QColor(39, 174, 96, 230)
        painter.setBrush(seg_color)

        for f in self._cached_frames:
            if start <= f <= end:
                x = 2 + int(((f - start) / float(span)) * inner_w)
                seg_w = max(1, int(inner_w / float(span)) + 1)
                painter.fillRect(x, 2, seg_w, h - 4, seg_color)

        # Centered label
        painter.setPen(QColor(230, 230, 230))
        if self._cache_mode == "region":
            text = f"{int(self._percent_used)}% Region"
        elif self._cache_mode == "play_ahead":
            text = f"{int(self._percent_used)}% Ahead"
        elif self._cache_mode == "all":
            text = f"{int(self._percent_used)}% Full"
        else:
            text = f"{int(self._percent_used)}% RAM" if self._percent_used > 0 else "Cache"

        painter.drawText(0, 0, w, h, Qt.AlignmentFlag.AlignCenter, text)


class MagnifiedTimelinePopup(QFrame):
    """
    Floating mini-timeline strip anchored at the bottom-center of the viewport canvas.
    Enables high-precision scrubbing in an expanded window around the current frame (Shift+T).
    """
    seek_requested = Signal(int)
    seek_finished = Signal(int)

    def __init__(self, clock: PlaybackClock, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.clock = clock
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setFixedSize(400, 32)
        self.setStyleSheet("QFrame { background: transparent; border: none; }")
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.SizeHorCursor)
        self._is_scrubbing = False
        self._scrub_anchor_frame: int = 0
        self._scrub_anchor_x: float = 0.0

        self.clock.frame_changed.connect(lambda _: self.update() if self.isVisible() else None)

    def auto_center_in_parent(self) -> None:
        """Position bottom-center inside parent canvas with 8px margin above timeline."""
        parent = self.parentWidget()
        if parent:
            pw = parent.width()
            ph = parent.height()
            w = self.width()
            h = self.height()
            x = max(8, (pw - w) // 2)
            y = max(8, ph - h - 8)
            self.move(x, y)

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        w, h = float(self.width()), float(self.height())

        # Semi-transparent dark frosted glass window with rounded corners
        rect = QRectF(0.5, 0.5, w - 1.0, h - 1.0)
        path = QPainterPath()
        path.addRoundedRect(rect, 6.0, 6.0)

        p.save()
        p.setClipPath(path)

        # Frosted glass background
        p.fillPath(path, QColor(20, 20, 24, 195))

        cur = self.clock.current_frame
        radius = 16
        f_min = cur - radius
        f_max = cur + radius

        cx = w / 2.0

        # Center playhead subtle highlight strip
        p.fillRect(QRectF(cx - 1.5, 0.0, 3.0, h), QColor(229, 168, 35, 90))

        font = QFont("Consolas", 8)
        font.setBold(True)
        p.setFont(font)

        for f in range(f_min, f_max + 1):
            x = cx + ((f - cur) / float(radius)) * (w * 0.46)
            if 4 <= x <= w - 4:
                if f == cur:
                    p.setPen(QPen(QColor(229, 168, 35), 2))
                    p.drawLine(QPointF(x, h - 14), QPointF(x, h - 4))
                    p.setPen(QColor(229, 168, 35))
                    p.drawText(QRectF(x - 22, 2, 44, 13), Qt.AlignmentFlag.AlignCenter, str(f))
                elif f % 5 == 0:
                    p.setPen(QPen(QColor(160, 160, 165, 200), 1.5))
                    p.drawLine(QPointF(x, h - 10), QPointF(x, h - 4))
                    if abs(x - cx) >= 26:
                        p.setPen(QColor(160, 160, 165, 220))
                        p.drawText(QRectF(x - 20, 3, 40, 13), Qt.AlignmentFlag.AlignCenter, str(f))
                else:
                    p.setPen(QPen(QColor(100, 100, 110, 160), 1))
                    p.drawLine(QPointF(x, h - 6), QPointF(x, h - 4))

        p.restore()

        # Subtle warm gold border outline
        p.setPen(QPen(QColor(229, 168, 35, 150), 1.0))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(rect, 6.0, 6.0)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._is_scrubbing = True
            mx = event.position().x()
            w = float(self.width())
            cx = w / 2.0
            radius = 16.0
            delta = ((mx - cx) / (w * 0.46)) * radius
            target_f = int(round(self.clock.current_frame + delta))
            target_f = max(self.clock.start_frame, min(self.clock.end_frame, target_f))
            self.clock.set_frame(target_f)
            self.seek_requested.emit(target_f)
            self._scrub_anchor_frame = target_f
            self._scrub_anchor_x = mx
            self.update()
            event.accept()

    def mouseMoveEvent(self, event) -> None:
        if self._is_scrubbing and (event.buttons() & Qt.MouseButton.LeftButton):
            mx = event.position().x()
            w = float(self.width())
            radius = 16.0
            delta_frames = ((mx - self._scrub_anchor_x) / (w * 0.46)) * radius
            target_f = int(round(self._scrub_anchor_frame + delta_frames))
            target_f = max(self.clock.start_frame, min(self.clock.end_frame, target_f))
            if target_f != self.clock.current_frame:
                self.clock.set_frame(target_f)
                self.seek_requested.emit(target_f)
                self.update()
            event.accept()

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._is_scrubbing = False
            event.accept()


class TimelineWidget(QFrame):
    """
    Complete VFX timeline panel:
    - Top row: Ruler with In/Out drag handles and keyframe marker diamonds.
    - Bottom row: Transport controls, compact cache bar, FPS meter, Clear In/Out, and loop mode.
    """

    cache_mode_cycle_requested = Signal()
    cache_mode_selected = Signal(str)
    seek_requested = Signal(int)
    seek_finished = Signal(int)
    audio_clicked = Signal()
    focus_viewport_requested = Signal()
    clear_cache_requested = Signal()
    clear_annotations_requested = Signal()
    fps_dialog_requested = Signal()

    def __init__(
        self,
        clock: PlaybackClock,
        annotation_store: Optional[AnnotationStore] = None,
        parent: Optional[QWidget] = None,
        canvas_widget: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.clock = clock
        self.annotation_store = annotation_store
        self.canvas_widget = canvas_widget
        self.magnified_popup: Optional[MagnifiedTimelinePopup] = None
        self._setup_ui()
        self.clock.fps_changed.connect(self._on_clock_fps_changed)

    def set_canvas_widget(self, canvas: QWidget) -> None:
        """Set the canvas/viewport widget used as the overlay parent for the magnifier."""
        self.canvas_widget = canvas
        if self.magnified_popup is not None:
            self.magnified_popup.setParent(canvas)
            if self.magnified_popup.isVisible():
                self.magnified_popup.auto_center_in_parent()


    def _on_clock_fps_changed(self, fps: float) -> None:
        if hasattr(self, "btn_fps"):
            self.btn_fps.setText(f"{fps:.2f} fps")
        if hasattr(self, "fps_spin"):
            self.fps_spin.blockSignals(True)
            self.fps_spin.setValue(fps)
            self.fps_spin.blockSignals(False)

    def _setup_ui(self) -> None:
        from openreview.ui.theme import get_spinbox_arrow_stylesheet
        self.setStyleSheet("""
            TimelineWidget {
                background-color: #1a1a1a;
                border-top: 1px solid #333333;
            }
            QFrame {
                background-color: #1a1a1a;
                border-top: 1px solid #333333;
            }
            QPushButton {
                min-width: 24px;
                min-height: 20px;
                padding: 1px 4px;
                font-size: 11px;
            }
            QLabel {
                font-family: "Consolas", monospace;
                font-size: 11px;
            }
        """ + get_spinbox_arrow_stylesheet())

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 4)
        layout.setSpacing(2)

        # 1. Ruler with In/Out handles and marker diamonds
        self.ruler = TimelineRuler(self.clock, self.annotation_store, self)
        self.ruler.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.ruler.seek_requested.connect(self.seek_requested.emit)
        if hasattr(self.ruler, "seek_finished"):
            self.ruler.seek_finished.connect(self.seek_finished.emit)
        layout.addWidget(self.ruler)

        # 2. Bottom Row: Transport controls + Compact Cache Bar + Frame info
        transport_row = QHBoxLayout()
        transport_row.setContentsMargins(4, 0, 4, 0)
        transport_row.setSpacing(4)

        # Transport Buttons with SVG vector icons
        self.btn_first = QPushButton(self)
        self.btn_first.setIcon(get_svg_icon("first_frame", color_normal="#cccccc", size=14))
        self.btn_first.setToolTip("First / In Frame (Home)")
        self.btn_first.clicked.connect(self.clock.first_frame)
        transport_row.addWidget(self.btn_first)

        self.btn_prev = QPushButton(self)
        self.btn_prev.setIcon(get_svg_icon("step_backward", color_normal="#cccccc", size=14))
        self.btn_prev.setToolTip("Step Back 1 Frame (Left Arrow)")
        self.btn_prev.clicked.connect(lambda: self.clock.step_backward(1))
        transport_row.addWidget(self.btn_prev)

        self.btn_play_back = QPushButton(self)
        self.btn_play_back.setIcon(get_svg_icon("play_backward", color_normal="#cccccc", size=14))
        self.btn_play_back.setToolTip("Play Backward (J / Shift+Space)")
        self.btn_play_back.setStyleSheet("min-width: 26px;")
        self.btn_play_back.clicked.connect(self.clock.play_backward)
        transport_row.addWidget(self.btn_play_back)

        self.btn_play = QPushButton(self)
        self.btn_play.setIcon(get_svg_icon("play", color_normal="#e5a823", size=15))
        self.btn_play.setToolTip("Play Forward (Space / L)")
        self.btn_play.setStyleSheet("color: #e5a823; font-weight: bold; min-width: 30px;")
        self.btn_play.clicked.connect(self.clock.play_forward)
        transport_row.addWidget(self.btn_play)
        self.btn_play_fwd = self.btn_play

        self.btn_next = QPushButton(self)
        self.btn_next.setIcon(get_svg_icon("step_forward", color_normal="#cccccc", size=14))
        self.btn_next.setToolTip("Step Forward 1 Frame (Right Arrow)")
        self.btn_next.clicked.connect(lambda: self.clock.step_forward(1))
        transport_row.addWidget(self.btn_next)

        self.btn_last = QPushButton(self)
        self.btn_last.setIcon(get_svg_icon("last_frame", color_normal="#cccccc", size=14))
        self.btn_last.setToolTip("Last / Out Frame (End)")
        self.btn_last.clicked.connect(self.clock.last_frame)
        transport_row.addWidget(self.btn_last)

        # Tri-State Loop Button (Cycle, Ping-Pong, Once)
        self.btn_loop = QPushButton(self)
        self.btn_loop.setIcon(get_svg_icon("loop", color_normal="#cccccc", color_active="#e5a823", size=14))
        self.btn_loop.setToolTip("Loop Mode: Cycle (Repeat)")
        self.btn_loop.clicked.connect(self.clock.cycle_loop_mode)
        transport_row.addWidget(self.btn_loop)

        # Magnified Timeline Scrub Button (Shift+T) next to playback controls
        self.btn_magnifier = QPushButton(self)
        self.btn_magnifier.setIcon(get_svg_icon("magnifier", color_normal="#cccccc", color_active="#e5a823", size=14))
        self.btn_magnifier.setToolTip("Toggle Magnified Timeline Scrub Ruler (Shift+T)")
        self.btn_magnifier.clicked.connect(self.toggle_magnifier_widget)
        transport_row.addWidget(self.btn_magnifier)

        # Target FPS Display (Clickable studio badge)
        fps_lbl = QLabel("FPS:", self)
        fps_lbl.setStyleSheet("color: #777777; margin-left: 4px;")
        transport_row.addWidget(fps_lbl)

        self.btn_fps = QPushButton(self)
        self.btn_fps.setObjectName("fpsBadgeBtn")
        self.btn_fps.setText(f"{self.clock.fps:.2f} fps")
        self.btn_fps.setToolTip("Playback Frame Rate (Shift+F) - Click to change")
        self.btn_fps.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_fps.setStyleSheet("""
            QPushButton#fpsBadgeBtn {
                background-color: #222222;
                color: #e5a823;
                font-weight: bold;
                font-size: 11px;
                font-family: Consolas, monospace;
                border: 1px solid #3c3c3c;
                border-radius: 3px;
                padding: 1px 7px;
                min-height: 20px;
                max-height: 22px;
            }
            QPushButton#fpsBadgeBtn:hover {
                background-color: #333333;
                border-color: #e5a823;
                color: #ffffff;
            }
            QPushButton#fpsBadgeBtn:pressed {
                background-color: #1a1a1a;
                border-color: #c98e16;
            }
        """)
        self.btn_fps.clicked.connect(self.fps_dialog_requested.emit)
        transport_row.addWidget(self.btn_fps)

        self.fps_spin = QDoubleSpinBox(self)
        self.fps_spin.setRange(0.1, 240.0)
        self.fps_spin.setValue(self.clock.fps)
        self.fps_spin.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self.fps_spin.wheelEvent = lambda event: event.ignore()
        self.fps_spin.hide()
        self.fps_spin.valueChanged.connect(lambda v: self.clock.set_fps(float(v)))

        # Set Handles Dropdown next to FPS input
        handles_lbl = QLabel("Set Handles:", self)
        handles_lbl.setStyleSheet("color: #888888; font-weight: bold; margin-left: 4px;")
        transport_row.addWidget(handles_lbl)

        self.combo_handles = QComboBox(self)
        self.combo_handles.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self.combo_handles.setEditable(True)
        self.combo_handles.addItems(["8", "10", "16", "24"])
        self.combo_handles.setToolTip("Set In/Out points based on handle value (inward from sequence bounds)")
        self.combo_handles.setMinimumWidth(65)
        self.combo_handles.setMaximumWidth(80)
        self.combo_handles.activated.connect(self._on_handles_applied)
        if self.combo_handles.lineEdit():
            self.combo_handles.lineEdit().returnPressed.connect(self._on_handles_applied)
        transport_row.addWidget(self.combo_handles)

        # Divider
        transport_row.addSpacing(6)

        # Cache Status / Progress Bar on transport row
        self.cache_bar = CompactCacheBar(self.clock, self)
        self.compact_cache_bar = self.cache_bar
        self.cache_bar.cycle_mode_requested.connect(self.cache_mode_cycle_requested.emit)
        transport_row.addWidget(self.cache_bar)

        transport_row.addStretch()

        # In: spinbox
        lbl_in = QLabel("In:", self)
        lbl_in.setStyleSheet("color: #888888; font-weight: bold;")
        transport_row.addWidget(lbl_in)

        self.spin_in = QSpinBox(self)
        self.spin_in.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self.spin_in.setRange(self.clock.start_frame, self.clock.end_frame)
        self.spin_in.setValue(self.clock.in_frame)
        self.spin_in.setToolTip("Set Manual In Point ([)")
        self.spin_in.setKeyboardTracking(False)
        self.spin_in.setMinimumWidth(64)
        self.spin_in.valueChanged.connect(self._on_manual_in_changed)
        self.spin_in.editingFinished.connect(self._return_focus_to_viewport)
        transport_row.addWidget(self.spin_in)

        # Goto Frame Input (Between In: and Out:, without "Goto:" label)
        self.goto_frame_box = QSpinBox(self)
        self.goto_frame_box.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self.goto_frame_box.clearFocus()
        self.goto_frame_box.setRange(self.clock.start_frame, self.clock.end_frame)
        self.goto_frame_box.setValue(self.clock.current_frame)
        self.goto_frame_box.setToolTip("Current Frame / Goto (Ctrl+G)")
        self.goto_frame_box.setKeyboardTracking(False)
        from openreview.ui.theme import get_spinbox_arrow_stylesheet
        self.goto_frame_box.setStyleSheet(
            "QSpinBox { color: #e5a823; font-weight: bold; min-width: 55px; border: 1px solid #3d3d3d; border-radius: 2px; } "
            "QSpinBox:focus { border: 1px solid #e5a823; }"
            + get_spinbox_arrow_stylesheet()
        )
        self.goto_frame_box.valueChanged.connect(self._on_goto_frame_changed)
        self.goto_frame_box.editingFinished.connect(self._on_goto_frame_editing_finished)
        if self.goto_frame_box.lineEdit():
            self.goto_frame_box.lineEdit().returnPressed.connect(self._on_goto_frame_editing_finished)
        transport_row.addWidget(self.goto_frame_box)
        self.frame_box = self.goto_frame_box  # backward compatibility alias
        self.goto_frame_spin = self.goto_frame_box

        # Out: spinbox
        lbl_out = QLabel("Out:", self)
        lbl_out.setStyleSheet("color: #888888; font-weight: bold; margin-left: 2px;")
        transport_row.addWidget(lbl_out)

        self.spin_out = QSpinBox(self)
        self.spin_out.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self.spin_out.setRange(self.clock.start_frame, self.clock.end_frame)
        self.spin_out.setValue(self.clock.out_frame)
        self.spin_out.setToolTip("Set Manual Out Point (])")
        self.spin_out.setKeyboardTracking(False)
        self.spin_out.setMinimumWidth(64)
        self.spin_out.valueChanged.connect(self._on_manual_out_changed)
        self.spin_out.editingFinished.connect(self._return_focus_to_viewport)
        transport_row.addWidget(self.spin_out)

        # Number of frames label before Clear In/Out
        self.lbl_frame_count = QLabel(self)
        self.lbl_frame_count.setStyleSheet(
            "color: #a0a0a0; font-size: 10px; font-weight: bold; font-family: 'Segoe UI', sans-serif; padding: 0 4px;"
        )
        self.lbl_frame_count.setToolTip("Total media frame count / Selected In-Out frame range")
        transport_row.addWidget(self.lbl_frame_count)

        # Clear In/Out Button (Moved before TC toggle button!)
        self.btn_clear_in_out = QPushButton(self)
        self.btn_clear_in_out.setIcon(get_svg_icon("clear", color_normal="#e5a823", size=12))
        self.btn_clear_in_out.setText(" Clear")
        self.btn_clear_in_out.setToolTip("Clear In/Out Range (\\ or Alt+I / Alt+O)")
        self.btn_clear_in_out.setStyleSheet("color: #e5a823; font-size: 10px; padding: 2px 6px;")
        self.btn_clear_in_out.clicked.connect(self.clock.clear_in_out)
        self.btn_clear_in_out.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        transport_row.addWidget(self.btn_clear_in_out)

        # Extreme Right: SMPTE Timecode Display & Toggle
        transport_row.addSpacing(4)
        self.tc_label = QLabel("00:00:00:00", self)
        self.tc_label.setStyleSheet("color: #88bbff; font-weight: bold; font-family: 'Consolas', monospace;")
        self.tc_label.setVisible(False)
        transport_row.addWidget(self.tc_label)

        self.timecode_mode = False
        self.btn_timecode = QPushButton("TC", self)
        self.btn_timecode.setToolTip("Toggle SMPTE Timecode display (Ctrl+T)")
        self.btn_timecode.setStyleSheet("color: #888888; font-size: 10px; padding: 1px 4px; min-width: 24px;")
        self.btn_timecode.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_timecode.clicked.connect(self.toggle_timecode_mode)
        transport_row.addWidget(self.btn_timecode)

        # Extreme Right: Audio Settings & Volume HUD Button
        self.btn_audio = QPushButton(self)
        self.btn_audio.setIcon(get_svg_icon("audio", color_normal="#cccccc", size=14))
        self.btn_audio.setToolTip("Audio Settings & Offsets (Toggle HUD)")
        self.btn_audio.setStyleSheet("color: #cccccc; font-size: 11px; padding: 1px 4px; min-width: 24px;")
        self.btn_audio.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_audio.clicked.connect(self.audio_clicked.emit)
        transport_row.addWidget(self.btn_audio)

        # Ensure transport buttons don't steal focus from main keyboard shortcuts
        for btn in (
            self.btn_first,
            self.btn_prev,
            self.btn_play_back,
            self.btn_play,
            self.btn_next,
            self.btn_last,
            self.btn_loop,
            self.btn_magnifier,
            self.btn_clear_in_out,
            self.btn_timecode,
            self.btn_audio,
        ):
            btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        layout.addLayout(transport_row)

        # Signal connections
        self.clock.playback_state_changed.connect(self._on_playback_state_changed)
        self.clock.shuttle_speed_changed.connect(lambda _: self._on_playback_state_changed(self.clock.is_playing))
        self.clock.frame_changed.connect(self._on_frame_changed)
        self.clock.range_changed.connect(self._on_range_changed)
        self.clock.in_out_changed.connect(self._on_in_out_changed)
        self.clock.loop_mode_changed.connect(self._on_loop_mode_changed)

        self._update_frame_count_label()

    def toggle_magnifier_widget(self) -> None:
        """Toggle the magnified timeline scrub mini-ruler (Shift+T)."""
        target_parent = self.canvas_widget or self.parentWidget() or self
        if self.magnified_popup is None:
            self.magnified_popup = MagnifiedTimelinePopup(self.clock, target_parent)
            self.magnified_popup.seek_requested.connect(self.seek_requested.emit)
        elif self.magnified_popup.parent() != target_parent:
            self.magnified_popup.setParent(target_parent)

        if self.magnified_popup.isVisible():
            self.magnified_popup.hide()
        else:
            self.magnified_popup.auto_center_in_parent()
            self.magnified_popup.show()
            self.magnified_popup.raise_()
            self.magnified_popup.update()
        if target_parent and hasattr(target_parent, "children"):
            for child in target_parent.children():
                if hasattr(child, "auto_center_in_parent") and child != self.magnified_popup:
                    child.auto_center_in_parent()

    def focus_goto_frame(self) -> None:
        """Focus Goto Frame spinbox and select all text for immediate typing (Ctrl+G)."""
        self.goto_frame_box.setFocus()
        self.goto_frame_box.selectAll()

    def _on_playback_state_changed(self, is_playing: bool) -> None:
        if is_playing:
            if self.clock.shuttle_speed < 0:
                self.btn_play_back.setIcon(get_svg_icon("pause", color_normal="#e5a823", size=14))
                self.btn_play.setIcon(get_svg_icon("play", color_normal="#cccccc", size=15))
            else:
                self.btn_play_back.setIcon(get_svg_icon("play_backward", color_normal="#cccccc", size=14))
                self.btn_play.setIcon(get_svg_icon("pause", color_normal="#e5a823", size=15))
        else:
            self.btn_play_back.setIcon(get_svg_icon("play_backward", color_normal="#cccccc", size=14))
            self.btn_play.setIcon(get_svg_icon("play", color_normal="#e5a823", size=15))

        if not is_playing:
            if self.goto_frame_box.value() != self.clock.current_frame:
                self.goto_frame_box.blockSignals(True)
                self.goto_frame_box.setValue(self.clock.current_frame)
                self.goto_frame_box.blockSignals(False)
        if self.magnified_popup and self.magnified_popup.isVisible():
            self.magnified_popup.update()

    def toggle_timecode_mode(self) -> None:
        self.set_timecode_mode(not self.timecode_mode)

    def set_timecode_mode(self, enabled: bool) -> None:
        self.timecode_mode = bool(enabled)
        self.tc_label.setVisible(self.timecode_mode)
        self.btn_timecode.setStyleSheet(
            "color: #e5a823; font-weight: bold; font-size: 10px; padding: 1px 4px; min-width: 24px;"
            if self.timecode_mode
            else "color: #888888; font-size: 10px; padding: 1px 4px; min-width: 24px;"
        )
        self._on_frame_changed(self.clock.current_frame)

    def _return_focus_to_viewport(self) -> None:
        self.focus_viewport_requested.emit()
        win = self.window()
        if win and hasattr(win, "viewport") and win.viewport:
            win.viewport.setFocus()

    def _on_goto_frame_changed(self, frame: int) -> None:
        self.clock.set_frame(frame)
        self.seek_requested.emit(frame)
        self.goto_frame_box.clearFocus()
        if self.goto_frame_box.lineEdit():
            self.goto_frame_box.lineEdit().deselect()
            self.goto_frame_box.lineEdit().clearFocus()
        self._return_focus_to_viewport()

    def _on_goto_frame_editing_finished(self) -> None:
        val = self.goto_frame_box.value()
        if val != self.clock.current_frame:
            self.clock.set_frame(val)
            self.seek_requested.emit(val)
        self.goto_frame_box.clearFocus()
        if self.goto_frame_box.lineEdit():
            self.goto_frame_box.lineEdit().deselect()
            self.goto_frame_box.lineEdit().clearFocus()
        self._return_focus_to_viewport()

    def _on_manual_in_changed(self, val: int) -> None:
        out_val = max(val, self.clock.out_frame)
        self.clock.set_in_out(val, out_val)

    def _on_manual_out_changed(self, val: int) -> None:
        in_val = min(val, self.clock.in_frame)
        self.clock.set_in_out(in_val, val)

    def _update_frame_count_label(self) -> None:
        """Update total frames or selected in/out frame count in transport bar."""
        if not hasattr(self, "lbl_frame_count"):
            return
        total_f = max(1, self.clock.end_frame - self.clock.start_frame + 1)
        in_f = self.clock.in_frame
        out_f = self.clock.out_frame

        if in_f == self.clock.start_frame and out_f == self.clock.end_frame:
            self.lbl_frame_count.setText(f"{total_f} frames")
        else:
            sel_f = max(1, out_f - in_f + 1)
            self.lbl_frame_count.setText(f"{sel_f} / {total_f} frames")

    def _on_range_changed(self, start: int, end: int) -> None:
        self.goto_frame_box.blockSignals(True)
        self.goto_frame_box.setRange(start, end)
        self.goto_frame_box.blockSignals(False)

        self.spin_in.blockSignals(True)
        self.spin_in.setRange(start, end)
        self.spin_in.blockSignals(False)

        self.spin_out.blockSignals(True)
        self.spin_out.setRange(start, end)
        self.spin_out.blockSignals(False)

        self._update_frame_count_label()

    def _on_frame_changed(self, frame: int) -> None:
        import time as _t
        now = _t.perf_counter()
        if not self.clock.is_playing or (now - getattr(self, "_last_spin_sync_time", 0.0) >= 0.1):
            self._last_spin_sync_time = now
            if self.goto_frame_box.value() != frame:
                self.goto_frame_box.blockSignals(True)
                self.goto_frame_box.setValue(frame)
                self.goto_frame_box.blockSignals(False)

        if self.timecode_mode:
            from openreview.core.timecode import frame_to_timecode
            rel_frame = max(0, frame - self.clock.start_frame)
            self.tc_label.setText(frame_to_timecode(rel_frame, self.clock.fps))

    def _on_in_out_changed(self, in_f: int, out_f: int) -> None:
        if self.spin_in.value() != in_f:
            self.spin_in.blockSignals(True)
            self.spin_in.setValue(in_f)
            self.spin_in.blockSignals(False)
        if self.spin_out.value() != out_f:
            self.spin_out.blockSignals(True)
            self.spin_out.setValue(out_f)
            self.spin_out.blockSignals(False)

        self._update_frame_count_label()

    def _on_loop_mode_changed(self, mode: str) -> None:
        self.btn_loop.setText("")
        if mode == "cycle":
            self.btn_loop.setIcon(get_svg_icon("loop", color_normal="#cccccc", color_active="#e5a823", size=14))
            self.btn_loop.setToolTip("Loop Mode: Cycle (Repeat)")
        elif mode == "ping-pong":
            self.btn_loop.setIcon(get_svg_icon("loop_pingpong", color_normal="#cccccc", color_active="#e5a823", size=14))
            self.btn_loop.setToolTip("Loop Mode: Ping-Pong (Bounce)")
        elif mode == "once":
            self.btn_loop.setIcon(get_svg_icon("loop_once", color_normal="#cccccc", color_active="#e5a823", size=14))
            self.btn_loop.setToolTip("Loop Mode: Play Once (Stop at Out)")



    def _on_handles_applied(self) -> None:
        text = self.combo_handles.currentText().strip()
        try:
            h = int(text)
        except ValueError:
            h = None
        if h is not None and h >= 0:
            new_in = min(self.clock.end_frame, self.clock.start_frame + h)
            new_out = max(new_in, self.clock.end_frame - h)
            self.clock.set_in_out(new_in, new_out)
            self.spin_in.setValue(new_in)
            self.spin_out.setValue(new_out)
            self.ruler.update()
        self.combo_handles.clearFocus()
        if self.combo_handles.lineEdit():
            self.combo_handles.lineEdit().deselect()
            self.combo_handles.lineEdit().clearFocus()
        self._return_focus_to_viewport()
        QTimer.singleShot(0, self._cleanup_handles_focus)

    def _cleanup_handles_focus(self) -> None:
        self.combo_handles.clearFocus()
        if self.combo_handles.lineEdit():
            self.combo_handles.lineEdit().deselect()
            self.combo_handles.lineEdit().clearFocus()
        self._return_focus_to_viewport()

    def update_cache_display(
        self,
        cached_frames: Set[int],
        pct_used: float,
        cache_mode: str = "off",
        disk_cached_frames: Optional[Set[int]] = None,
        disk_pct_used: float = 0.0,
        proxy_cached_frames: Optional[Set[int]] = None,
    ) -> None:

        self.cache_bar.update_cache_data(
            cached_frames,
            pct_used,
            cache_mode,
            disk_cached_frames=disk_cached_frames,
            disk_pct_used=disk_pct_used,
            proxy_cached_frames=proxy_cached_frames,
        )
        self.ruler.set_cached_frames(cached_frames, disk_frames=disk_cached_frames)

    def zoom_in(self) -> None:
        """Magnify timeline ruler around playhead."""
        self.ruler.zoom_in()

    def zoom_out(self) -> None:
        """De-magnify timeline ruler."""
        self.ruler.zoom_out()

    def reset_zoom(self) -> None:
        """Reset timeline ruler magnification."""
        self.ruler.reset_zoom()

    def request_clear_all_annotations(self) -> None:
        """Prompt confirmation and clear all annotations."""
        ret = QMessageBox.question(
            self,
            "Clear All Annotations",
            "Are you sure you want to clear all drawing annotations and notes across all frames?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if ret == QMessageBox.StandardButton.Yes:
            if self.annotation_store:
                self.annotation_store.clear_all()
            self.ruler._invalidate_bg_cache()
            self.ruler.update()
            self.clear_annotations_requested.emit()

    def contextMenuEvent(self, event) -> None:
        """Delegate right-click context menu event on the timeline frame to ruler context menu."""
        self.ruler.contextMenuEvent(event)
