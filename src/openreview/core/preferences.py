"""
Persistent Preferences Management for OpenReview.
Stores user preferences in ~/.openreview/preferences.json with QSettings fallback.
"""
from __future__ import annotations

import json
import os
import sys
import logging
import shutil
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Optional
from PySide6.QtCore import QObject, Signal, QSettings

logger = logging.getLogger(__name__)


def get_app_config_dir() -> Path:
    """Return platform-standard user application config directory for OpenReview."""
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA")
        if appdata:
            win_dir = Path(appdata) / "OpenReview"
            legacy_dir = Path.home() / ".openreview"
            legacy_file = legacy_dir / "preferences.json"
            win_file = win_dir / "preferences.json"
            if legacy_file.is_file() and not win_file.is_file():
                try:
                    win_dir.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(legacy_file, win_file)
                except Exception as e:
                    logger.debug("Failed migrating legacy preferences: %s", e)
            return win_dir
    return Path.home() / ".openreview"


@dataclass
class UserPreferences:
    """Dataclass holding all configurable user settings."""
    default_fps: float = 24.0
    play_on_startup: bool = False
    start_fullscreen: bool = False
    canvas_scrub_enabled: bool = True
    auto_retime_mismatched_fps: bool = True
    play_all_frames: bool = True
    default_brush_width: float = 6.0

    # Caching
    default_cache_mode: str = "off"  # "off", "play_ahead", "region", "all"
    cache_toggle_mode: str = "region"  # "region", "play_ahead", "all", "last_active"
    auto_ram_budget: bool = True
    lookahead_cache_gb: float = 4.0
    region_cache_gb: float = 4.0
    lookahead_behind_pct: float = 20.0
    enable_disk_cache: bool = True
    disk_cache_dir: str = ""  # empty = default OS temp directory
    max_disk_cache_gb: float = 20.0

    # Playback Performance & Low-spec / GPU Workstation Optimization
    playback_texture_mode: str = "auto"  # "auto", "rgba8" (fast/laptop), "rgba16f", "rgba32f"
    prefetch_buffer_size: int = 64
    prefetch_worker_threads: int = 6
    fast_playback_laptop_mode: bool = False

    # GPU Performance & Playback Engines
    playback_engine: str = "opengl_core"  # "opengl_core", "async_pbo", "software_safe"
    selected_gpu_device: str = "auto"     # "auto" or specific GPU name
    hardware_video_decoding: bool = True
    enable_opencl_acceleration: bool = True
    enable_pbo_streaming: bool = True
    vsync_enabled: bool = False
    gpu_vram_limit_mb: float = 2048.0

    # Keyboard Shortcut Customizations: action_id -> key_sequence_str
    shortcuts: dict[str, str] = field(default_factory=dict)

    # Workflow & UI Enhancements
    recent_files: list[str] = field(default_factory=list)
    file_browser_bookmarks: list[dict[str, str]] = field(default_factory=list)
    timecode_mode: bool = False
    continuous_playback: bool = False
    safe_guides_enabled: bool = False
    safe_guides_matte_constrained: bool = True
    hud_opacity: float = 0.85  # 0.30 to 1.00 (30% to 100%)

    # Color Management Configuration
    color_management_mode: str = "standard"
    standard_view_transform: str = "sRGB"
    standard_input_space: str = "linear"
    auto_detect_input_colorspace: bool = True  # Auto-choose input colorspace from media metadata

    # Studio OpenColorIO Configuration
    ocio_config_path: str = ""
    ocio_display: str = ""
    ocio_view: str = ""
    ocio_input_space: str = ""
    ocio_lut_paths: list[str] = field(default_factory=list)

    # Multi-User Remote Dailies Sync
    sync_room_name: str = "VFX Review Main"
    sync_username: str = "Artist"
    sync_playlist_enabled: bool = True

    # Console & Diagnostics
    console_auto_open_on_error: bool = True

    # PDF Review Sheet Generation & Layouting
    pdf_title: str = "OpenReview - VFX Review Sheet"
    pdf_studio_name: str = ""
    pdf_author_name: str = ""
    pdf_logo_path: str = ""
    pdf_header_color: str = "#c28a10"
    pdf_show_date: bool = True
    pdf_show_fps: bool = True
    pdf_show_keyframe_count: bool = True
    pdf_page_size: str = "letter"  # "letter", "a4", "legal"
    pdf_page_orientation: str = "landscape"  # "landscape", "portrait"
    pdf_layout_mode: str = "detailed_row"  # "detailed_row", "dense_grid"
    pdf_show_frame_num: bool = True
    pdf_show_timecode: bool = True
    pdf_show_stroke_count: bool = True
    pdf_show_notes: bool = True
    pdf_show_source_path: bool = True

    # Export & Delivery Defaults
    default_export_image_format: str = "jpg"  # "jpg", "png"
    export_jpg_quality: int = 85  # 1..100
    export_downscale_greater_than_fhd: bool = True  # Downsize by half when media > 1920x1080
    export_png_mode: str = "png8"  # "png8", "png24_opt", "16"
    export_png_dither: bool = True
    export_png_compression: int = 4  # 0..9


class PreferencesManager(QObject):
    """
    Singleton-style Preferences Manager emitting signals upon modification.
    """
    preferences_changed = Signal(object)  # Emits UserPreferences

    _instance: Optional[PreferencesManager] = None

    def __init__(self, config_dir: Optional[Path] = None, parent: Optional[QObject] = None):
        super().__init__(parent)
        self._use_qsettings = (config_dir is None)
        self.config_dir = config_dir or get_app_config_dir()
        self.config_file = self.config_dir / "preferences.json"
        self._prefs = UserPreferences()
        self.load()

    @classmethod
    def instance(cls) -> PreferencesManager:
        if cls._instance is None:
            cls._instance = PreferencesManager()
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        cls._instance = None

    @property
    def prefs(self) -> UserPreferences:
        return self._prefs

    @property
    def preferences(self) -> UserPreferences:
        return self._prefs

    def save_preferences(self) -> None:
        self.save()

    def load(self) -> UserPreferences:
        """Load preferences from JSON file, falling back to QSettings if available."""
        if self.config_file.is_file():
            try:
                with open(self.config_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self._prefs = UserPreferences(
                    default_fps=float(data.get("default_fps", 24.0)),
                    play_on_startup=bool(data.get("play_on_startup", False)),
                    start_fullscreen=bool(data.get("start_fullscreen", False)),
                    canvas_scrub_enabled=bool(data.get("canvas_scrub_enabled", True)),
                    auto_retime_mismatched_fps=bool(data.get("auto_retime_mismatched_fps", True)),
                    play_all_frames=bool(data.get("play_all_frames", True)),
                    default_cache_mode=str(data.get("default_cache_mode", "off")),
                    cache_toggle_mode=str(data.get("cache_toggle_mode", "region")),
                    auto_ram_budget=bool(data.get("auto_ram_budget", True)),
                    lookahead_cache_gb=float(data.get("lookahead_cache_gb", 4.0)),
                    region_cache_gb=float(data.get("region_cache_gb", 4.0)),
                    lookahead_behind_pct=float(data.get("lookahead_behind_pct", 20.0)),
                    playback_texture_mode=str(data.get("playback_texture_mode", "auto")),
                    prefetch_buffer_size=int(data.get("prefetch_buffer_size", 48)),
                    prefetch_worker_threads=int(data.get("prefetch_worker_threads", max(2, min(os.cpu_count() or 4, 8)))),
                    fast_playback_laptop_mode=bool(data.get("fast_playback_laptop_mode", False)),
                    playback_engine=str(data.get("playback_engine", "opengl_core")),
                    selected_gpu_device=str(data.get("selected_gpu_device", "auto")),
                    hardware_video_decoding=bool(data.get("hardware_video_decoding", True)),
                    enable_opencl_acceleration=bool(data.get("enable_opencl_acceleration", True)),
                    enable_pbo_streaming=bool(data.get("enable_pbo_streaming", True)),
                    vsync_enabled=bool(data.get("vsync_enabled", False)),
                    gpu_vram_limit_mb=float(data.get("gpu_vram_limit_mb", 2048.0)),
                    shortcuts=dict(data.get("shortcuts", {})),
                    recent_files=list(data.get("recent_files", [])),
                    file_browser_bookmarks=list(data.get("file_browser_bookmarks", [])),
                    timecode_mode=bool(data.get("timecode_mode", False)),
                    continuous_playback=bool(data.get("continuous_playback", False)),
                    safe_guides_enabled=bool(data.get("safe_guides_enabled", False)),
                    safe_guides_matte_constrained=bool(data.get("safe_guides_matte_constrained", True)),
                    hud_opacity=float(data.get("hud_opacity", 0.85)),
                    color_management_mode=str(data.get("color_management_mode", "standard")),
                    standard_view_transform=str(data.get("standard_view_transform", "sRGB")),
                    standard_input_space=str(data.get("standard_input_space", "linear")),
                    auto_detect_input_colorspace=bool(data.get("auto_detect_input_colorspace", True)),
                    ocio_config_path=str(data.get("ocio_config_path", "")),
                    ocio_display=str(data.get("ocio_display", "")),
                    ocio_view=str(data.get("ocio_view", "")),
                    ocio_input_space=str(data.get("ocio_input_space", "")),
                    ocio_lut_paths=list(data.get("ocio_lut_paths", [])),
                    sync_room_name=str(data.get("sync_room_name", "VFX Review Main")),
                    sync_username=str(data.get("sync_username", "Artist")),
                    sync_playlist_enabled=bool(data.get("sync_playlist_enabled", True)),
                    console_auto_open_on_error=bool(data.get("console_auto_open_on_error", True)),
                    default_brush_width=float(data.get("default_brush_width", 6.0)),
                    pdf_title=str(data.get("pdf_title", "OpenReview - VFX Review Sheet")),
                    pdf_studio_name=str(data.get("pdf_studio_name", "")),
                    pdf_author_name=str(data.get("pdf_author_name", "")),
                    pdf_logo_path=str(data.get("pdf_logo_path", "")),
                    pdf_header_color=str(data.get("pdf_header_color", "#c28a10")),
                    pdf_show_date=bool(data.get("pdf_show_date", True)),
                    pdf_show_fps=bool(data.get("pdf_show_fps", True)),
                    pdf_show_keyframe_count=bool(data.get("pdf_show_keyframe_count", True)),
                    pdf_page_size=str(data.get("pdf_page_size", "letter")),
                    pdf_page_orientation=str(data.get("pdf_page_orientation", "landscape")),
                    pdf_layout_mode=str(data.get("pdf_layout_mode", "detailed_row")),
                    pdf_show_frame_num=bool(data.get("pdf_show_frame_num", True)),
                    pdf_show_timecode=bool(data.get("pdf_show_timecode", True)),
                    pdf_show_stroke_count=bool(data.get("pdf_show_stroke_count", True)),
                    pdf_show_notes=bool(data.get("pdf_show_notes", True)),
                    pdf_show_source_path=bool(data.get("pdf_show_source_path", True)),
                    default_export_image_format=str(data.get("default_export_image_format", "jpg")),
                    export_jpg_quality=int(data.get("export_jpg_quality", 85)),
                    export_downscale_greater_than_fhd=bool(data.get("export_downscale_greater_than_fhd", True)),
                    export_png_mode=str(data.get("export_png_mode", "png8")),
                    export_png_dither=bool(data.get("export_png_dither", True)),
                    export_png_compression=int(data.get("export_png_compression", 4)),
                )
                return self._prefs
            except Exception as e:
                logger.warning("[PreferencesManager] Failed to read JSON config: %s. Falling back.", e)

        # Fallback to QSettings only for default user config
        if self._use_qsettings:
            try:
                settings = QSettings("OpenReview", "OpenReview")
                self._prefs.default_fps = float(settings.value("default_fps", 24.0))
                self._prefs.play_on_startup = settings.value("play_on_startup", False, type=bool)
                self._prefs.start_fullscreen = settings.value("start_fullscreen", False, type=bool)
                self._prefs.canvas_scrub_enabled = settings.value("canvas_scrub_enabled", True, type=bool)
                self._prefs.auto_retime_mismatched_fps = settings.value("auto_retime_mismatched_fps", True, type=bool)
                self._prefs.default_cache_mode = str(settings.value("default_cache_mode", "off"))
                self._prefs.cache_toggle_mode = str(settings.value("cache_toggle_mode", "region"))
                self._prefs.auto_ram_budget = settings.value("auto_ram_budget", True, type=bool)
                self._prefs.lookahead_cache_gb = float(settings.value("lookahead_cache_gb", 4.0))
                self._prefs.region_cache_gb = float(settings.value("region_cache_gb", 4.0))
                self._prefs.lookahead_behind_pct = float(settings.value("lookahead_behind_pct", 20.0))
                self._prefs.playback_texture_mode = str(settings.value("playback_texture_mode", "auto"))
                self._prefs.prefetch_buffer_size = int(settings.value("prefetch_buffer_size", 48))
                self._prefs.fast_playback_laptop_mode = settings.value("fast_playback_laptop_mode", False, type=bool)
                self._prefs.console_auto_open_on_error = settings.value("console_auto_open_on_error", True, type=bool)
                self._prefs.default_brush_width = float(settings.value("default_brush_width", 6.0))
                self._prefs.hud_opacity = float(settings.value("hud_opacity", 0.85))
                self._prefs.pdf_title = str(settings.value("pdf_title", "OpenReview - VFX Review Sheet"))
                self._prefs.pdf_studio_name = str(settings.value("pdf_studio_name", ""))
                self._prefs.pdf_author_name = str(settings.value("pdf_author_name", ""))
                self._prefs.pdf_logo_path = str(settings.value("pdf_logo_path", ""))
                self._prefs.pdf_header_color = str(settings.value("pdf_header_color", "#c28a10"))
                self._prefs.pdf_show_date = settings.value("pdf_show_date", True, type=bool)
                self._prefs.pdf_show_fps = settings.value("pdf_show_fps", True, type=bool)
                self._prefs.pdf_show_keyframe_count = settings.value("pdf_show_keyframe_count", True, type=bool)
                self._prefs.pdf_page_size = str(settings.value("pdf_page_size", "letter"))
                self._prefs.pdf_page_orientation = str(settings.value("pdf_page_orientation", "landscape"))
                self._prefs.pdf_layout_mode = str(settings.value("pdf_layout_mode", "detailed_row"))
                self._prefs.pdf_show_frame_num = settings.value("pdf_show_frame_num", True, type=bool)
                self._prefs.pdf_show_timecode = settings.value("pdf_show_timecode", True, type=bool)
                self._prefs.pdf_show_stroke_count = settings.value("pdf_show_stroke_count", True, type=bool)
                self._prefs.pdf_show_notes = settings.value("pdf_show_notes", True, type=bool)
                self._prefs.pdf_show_source_path = settings.value("pdf_show_source_path", True, type=bool)
                self._prefs.default_export_image_format = str(settings.value("default_export_image_format", "jpg"))
                self._prefs.export_jpg_quality = int(settings.value("export_jpg_quality", 85))
                self._prefs.export_downscale_greater_than_fhd = settings.value("export_downscale_greater_than_fhd", True, type=bool)
                self._prefs.export_png_mode = str(settings.value("export_png_mode", "png8"))
                self._prefs.export_png_dither = settings.value("export_png_dither", True, type=bool)
                self._prefs.export_png_compression = int(settings.value("export_png_compression", 4))
            except Exception:
                pass
        return self._prefs

    def save(self, prefs: Optional[UserPreferences] = None) -> None:
        """Persist preferences to preferences.json and QSettings."""
        if prefs is not None:
            self._prefs = prefs

        try:
            self.config_dir.mkdir(parents=True, exist_ok=True)
            with open(self.config_file, "w", encoding="utf-8") as f:
                json.dump(asdict(self._prefs), f, indent=4)
        except Exception as e:
            logger.warning("[PreferencesManager] Could not write JSON: %s", e)

        # Sync QSettings only for default user config
        if self._use_qsettings:
            try:
                settings = QSettings("OpenReview", "OpenReview")
                for k, v in asdict(self._prefs).items():
                    settings.setValue(k, v)
                settings.sync()
            except Exception as e:
                logger.warning("[PreferencesManager] Could not write QSettings: %s", e)

        self.preferences_changed.emit(self._prefs)

    def add_recent_file(self, path: str, max_items: int = 12) -> None:
        """Add a path to recent files list (deduplicated, MRU order)."""
        if not path:
            return
        norm_path = os.path.normpath(path)
        current = [os.path.normpath(p) for p in self._prefs.recent_files]
        if norm_path in current:
            current.remove(norm_path)
        current.insert(0, norm_path)
        self._prefs.recent_files = current[:max_items]
        self.save()

    def clear_recent_files(self) -> None:
        """Clear all recent file paths."""
        self._prefs.recent_files.clear()
        self.save()

    def get_recent_files(self) -> list[str]:
        """Return list of recent files that exist on disk."""
        return [p for p in self._prefs.recent_files if os.path.exists(p)]

    def is_low_spec_gpu(self) -> bool:
        """Check if active GPU has constrained memory or bandwidth (<=2GB VRAM or entry-level GPU)."""
        vram = getattr(self._prefs, "gpu_vram_limit_mb", 2048.0)
        gpu = str(getattr(self._prefs, "selected_gpu_device", "")).lower()
        if "gt 710" in gpu or "gt 730" in gpu or "intel" in gpu or "uhd" in gpu or vram <= 2048.0:
            return True
        return False

    def get_effective_texture_mode(self) -> str:
        """Resolve texture mode: 'rgba16f' provides zero-copy GPU upload for HDR/float media."""
        mode = getattr(self._prefs, "playback_texture_mode", "auto")
        if mode in ("rgba8", "rgba16f", "rgba32f"):
            return mode
        return "rgba16f"

