"""
OpenColorIO v2 Manager.
Handles OCIO v2 configurations, custom config loading, displays, views, colorspaces, and pixel conversions.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import List, Optional, Tuple, Dict, Any
import numpy as np

from PySide6.QtCore import QObject, Signal
from openreview.color.standard_colorspaces import (
    apply_standard_to_linear,
    apply_linear_to_standard,
    COLORSPACE_BY_NAME,
    COLORSPACE_BY_ID,
)

try:
    import PyOpenColorIO as ocio
    HAS_OCIO = True
except ImportError:
    try:
        import opencolorio as ocio
        HAS_OCIO = True
    except ImportError:
        HAS_OCIO = False
        ocio = None


class OCIOManager(QObject):
    """
    Manages OCIO v2 environment, custom config loading, active config, displays, views, and transforms.
    Falls back gracefully to analytical sRGB / Linear / ACEScg math if OCIO is absent.
    """

    config_changed = Signal()
    context_changed = Signal()
    ocio_lut_baked = Signal(object)  # Emits OcioLutBakeResult

    def __init__(self, config_path: Optional[str] = None, parent: Optional[QObject] = None):
        super().__init__(parent)
        self._config = None
        self._config_path: Optional[str] = None
        self._current_input_space: str = "ACEScg"
        self._current_display: str = "sRGB - Display"
        self._current_view: str = "ACES 1.0 - SDR Video"
        self._exposure_ev: float = 0.0
        self._gamma: float = 1.0
        self._custom_search_paths: List[str] = []
        self._context_vars: Dict[str, str] = {}
        self._color_management_mode: str = "standard"
        self._standard_input_id: int = 0   # linear
        self._standard_view_id: int = 1    # sRGB
        self._active_bake = None
        self._gpu_fast_path_enabled: bool = True
        self._lut_3d_size: int = 32
        self._shaper_mode: int = 1  # 1: Log2 20-stop [-10, 10] EV

        # Parsed text cache (works even without PyOpenColorIO binary)
        self._parsed_info: Dict[str, Any] = {
            "name": "Built-in ACEScg",
            "description": "Standard VFX ACEScg Scene-Linear Pipeline",
            "ocio_version": 2,
            "search_paths": [],
            "color_spaces": ["ACEScg", "ACES2065-1", "Linear sRGB", "sRGB", "Rec.709", "Raw"],
            "displays": {"sRGB - Display": ["ACES 1.0 - SDR Video", "Un-tone-mapped", "Raw"]},
            "looks": [],
            "roles": {"scene_linear": "ACEScg", "color_picking": "sRGB"},
        }

        self.initialize(config_path)

    def initialize(self, config_path: Optional[str] = None) -> None:
        """Load OCIO config from path, $OCIO environment, or built-in ACES 1.3 / 2.0."""
        if config_path and os.path.exists(config_path):
            self.load_config(config_path)
            return

        if "OCIO" in os.environ and os.path.exists(os.environ["OCIO"]):
            self.load_config(os.environ["OCIO"])
            return

        if not HAS_OCIO:
            return

        try:
            # Try built-in ACES Studio config
            reg = ocio.BuiltinConfigRegistry()
            configs = [c[0] for c in reg.getBuiltinConfigs()]
            aces_cfg = next((c for c in configs if "studio-config" in c and "aces-v1.3" in c), None)
            if not aces_cfg and configs:
                aces_cfg = configs[0]
            if aces_cfg:
                self._config = ocio.Config.CreateFromBuiltinConfig(aces_cfg)
            else:
                self._config = ocio.Config.CreateRaw()
        except Exception:
            self._config = ocio.Config.CreateRaw() if HAS_OCIO else None

        self._resolve_defaults()

    def load_config(self, config_path: str | Path) -> bool:
        """
        Load custom studio OCIO config file (.ocio).
        Returns True on successful load.
        """
        expanded = os.path.expandvars(str(config_path))
        p = Path(expanded)
        if not p.is_file():
            return False

        self._config_path = str(p.resolve())

        # 1. Parse text metadata directly
        try:
            self._parsed_info = self._parse_ocio_file(self._config_path)
        except Exception as e:
            print(f"[OCIOManager] Warning parsing text config: {e}")

        # 2. Try loading via official PyOpenColorIO if available
        if HAS_OCIO and ocio:
            try:
                self._config = ocio.Config.CreateFromFile(self._config_path)
                for sp in self._custom_search_paths:
                    try:
                        self._config.addSearchPath(sp)
                    except Exception:
                        pass
                for k, v in self._context_vars.items():
                    try:
                        self._config.addEnvironmentVar(k, v)
                    except Exception:
                        pass
            except Exception as e:
                print(f"[OCIOManager] PyOpenColorIO CreateFromFile error: {e}")
                self._config = None

        self._resolve_defaults()
        self.config_changed.emit()
        return True

    def reset_to_default(self) -> None:
        """Reset to built-in ACES studio config."""
        self._config_path = None
        self._parsed_info = {
            "name": "Built-in ACEScg",
            "description": "Standard VFX ACEScg Scene-Linear Pipeline",
            "ocio_version": 2,
            "search_paths": [],
            "color_spaces": ["ACEScg", "ACES2065-1", "Linear sRGB", "sRGB", "Rec.709", "Raw"],
            "displays": {"sRGB - Display": ["ACES 1.0 - SDR Video", "Un-tone-mapped", "Raw"]},
            "looks": [],
            "roles": {"scene_linear": "ACEScg", "color_picking": "sRGB"},
        }
        self.initialize(None)
        self.config_changed.emit()

    def bake_current_display_lut(self, force: bool = False, size: Optional[int] = None, lut_size: Optional[int] = None) -> Any:
        """Pre-bake the current OCIO display/view color transform into a 3D LUT for GPU upload."""
        from openreview.color.ocio_baker import bake_display_view_lut
        cfg_id = self.config_name or "builtin"
        bake_sz = lut_size or size or self._lut_3d_size
        bake = bake_display_view_lut(
            config=self._config,
            src_space=self._current_input_space,
            display=self._current_display,
            view=self._current_view,
            size=bake_sz,
            shaper_mode=self._shaper_mode,
            config_name=cfg_id,
        )
        self._active_bake = bake
        self.ocio_lut_baked.emit(bake)
        return bake

    def prewarm_adjacent_views(self) -> None:
        """Asynchronously pre-bake adjacent views for the current display to warm the cache."""
        import threading
        views = self.get_views(self._current_display)
        if not views:
            return
        def _worker():
            for v in views:
                if v != self._current_view:
                    try:
                        from openreview.color.ocio_baker import bake_display_view_lut
                        cfg_id = self.config_name or "builtin"
                        bake_display_view_lut(
                            config=self._config,
                            src_space=self._current_input_space,
                            display=self._current_display,
                            view=v,
                            size=self._lut_3d_size,
                            shaper_mode=self._shaper_mode,
                            config_name=cfg_id,
                        )
                    except Exception:
                        pass
        t = threading.Thread(target=_worker, daemon=True)
        t.start()

    def _resolve_defaults(self) -> None:
        displays = self.get_displays()
        if displays:
            self._current_display = displays[0]
            views = self.get_views(self._current_display)
            if views:
                self._current_view = views[0]

        spaces = self.get_color_spaces()
        if "ACEScg" in spaces:
            self._current_input_space = "ACEScg"
        elif "scene_linear" in self.get_roles():
            self._current_input_space = self.get_roles()["scene_linear"]
        elif spaces:
            self._current_input_space = spaces[0]

    # --------------------------------------------------------------------------
    # Fallback Parser for Custom .ocio Files
    # --------------------------------------------------------------------------
    @staticmethod
    def _parse_ocio_file(file_path: str) -> Dict[str, Any]:
        """Parse colorspaces, displays, views, roles, and looks from .ocio text."""
        info: Dict[str, Any] = {
            "name": Path(file_path).stem,
            "description": "",
            "ocio_version": 2,
            "search_paths": [],
            "color_spaces": [],
            "displays": {},
            "looks": [],
            "roles": {},
        }
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()

        # Version
        m_ver = re.search(r"ocio_profile_version:\s*(\d+)", content)
        if m_ver:
            info["ocio_version"] = int(m_ver.group(1))

        # Description
        m_desc = re.search(r"description:\s*([^\n]+)", content)
        if m_desc:
            info["description"] = m_desc.group(1).strip().strip('"\'')

        # Search Paths
        m_sp = re.search(r"search_path:\s*([^\n]+)", content)
        if m_sp:
            raw_sp = m_sp.group(1).strip().strip('"\'')
            parent_dir = Path(file_path).parent
            for token in re.split(r"[:;]", raw_sp):
                token = token.strip()
                if token:
                    candidate = Path(os.path.expandvars(token))
                    if not candidate.is_absolute():
                        candidate = parent_dir / candidate
                    info["search_paths"].append(str(candidate))

        # Roles
        m_roles = re.search(r"roles:\s*\n((?:\s+[a-zA-Z0-9_]+:\s*[^\n]+\n)+)", content)
        if m_roles:
            for line in m_roles.group(1).strip().splitlines():
                if ":" in line:
                    k, v = line.split(":", 1)
                    info["roles"][k.strip()] = v.strip().strip('"\'')

        # ColorSpaces
        cs_matches = re.findall(r"name:\s*([^\n]+)", content)
        seen_spaces = set()
        for name in cs_matches:
            clean = name.strip().strip('"\'')
            if clean and clean not in seen_spaces and not clean.startswith("{"):
                seen_spaces.add(clean)
                info["color_spaces"].append(clean)

        # Displays & Views
        disp_blocks = re.findall(r"-\s*!<(?:View|Display)>[^\n]*\n(?:\s+[^\n]+\n)+", content)
        for block in disp_blocks:
            m_disp = re.search(r"display:\s*([^\n]+)", block)
            m_view = re.search(r"name:\s*([^\n]+)", block)
            if m_disp and m_view:
                d = m_disp.group(1).strip().strip('"\'')
                v = m_view.group(1).strip().strip('"\'')
                if d not in info["displays"]:
                    info["displays"][d] = []
                if v not in info["displays"][d]:
                    info["displays"][d].append(v)

        if not info["displays"]:
            info["displays"]["sRGB"] = ["ACES 1.0 - SDR Video", "Raw"]
        if not info["color_spaces"]:
            info["color_spaces"] = ["ACEScg", "ACES2065-1", "sRGB", "Raw"]

        return info

    # --------------------------------------------------------------------------
    # Discovery API
    # --------------------------------------------------------------------------
    @property
    def has_ocio(self) -> bool:
        return HAS_OCIO and self._config is not None

    @property
    def config_path(self) -> Optional[str]:
        return self._config_path

    @property
    def config_name(self) -> str:
        if self._config_path:
            return Path(self._config_path).name
        if self.has_ocio and self._config:
            return self._config.getName() or "Built-in ACES Studio Config"
        return self._parsed_info.get("name", "Built-in Analytical ACEScg")

    @property
    def description(self) -> str:
        if self.has_ocio and self._config:
            return self._config.getDescription() or self._parsed_info.get("description", "")
        return self._parsed_info.get("description", "")

    def get_color_spaces(self) -> List[str]:
        if self.has_ocio and self._config:
            try:
                return [cs.getName() for cs in self._config.getColorSpaces()]
            except Exception:
                pass
        return list(self._parsed_info.get("color_spaces", ["ACEScg", "sRGB", "Raw"]))

    def get_displays(self) -> List[str]:
        if self.has_ocio and self._config:
            try:
                return list(self._config.getDisplays())
            except Exception:
                pass
        return list(self._parsed_info.get("displays", {}).keys())

    def get_views(self, display: Optional[str] = None) -> List[str]:
        disp = display or self._current_display
        if self.has_ocio and self._config:
            try:
                views = list(self._config.getViews(disp))
                if views:
                    return views
            except Exception:
                pass
        return list(self._parsed_info.get("displays", {}).get(disp, ["Raw"]))

    def get_roles(self) -> Dict[str, str]:
        if self.has_ocio and self._config:
            try:
                roles = {}
                for r in self._config.getRoles():
                    roles[r] = self._config.getRoleColorSpace(r)
                if roles:
                    return roles
            except Exception:
                pass
        return dict(self._parsed_info.get("roles", {}))

    def get_looks(self) -> List[str]:
        if self.has_ocio and self._config:
            try:
                return [l.getName() for l in self._config.getLooks()]
            except Exception:
                pass
        return list(self._parsed_info.get("looks", []))

    # --------------------------------------------------------------------------
    # Search Paths & Context Variables
    # --------------------------------------------------------------------------
    def get_search_paths(self) -> List[str]:
        """Return combined list of config search paths and user-added directories."""
        paths: List[str] = []
        if self.has_ocio and self._config:
            try:
                paths = [str(Path(p).resolve()) for p in self._config.getSearchPaths()]
            except Exception:
                pass
        if not paths:
            paths = list(self._parsed_info.get("search_paths", []))
        for p in self._custom_search_paths:
            if p not in paths:
                paths.append(p)
        return paths

    def add_search_path(self, path: str) -> bool:
        """Add a directory to the OCIO LUT / transform search path."""
        if not path:
            return False
        clean_p = str(Path(os.path.expandvars(path)).resolve())
        if clean_p not in self._custom_search_paths:
            self._custom_search_paths.append(clean_p)
        if self.has_ocio and self._config:
            try:
                self._config.addSearchPath(clean_p)
            except Exception as e:
                print(f"[OCIOManager] Error adding search path: {e}")
        self.config_changed.emit()
        return True

    def remove_search_path(self, path: str) -> None:
        """Remove a directory from the user-added search paths."""
        clean_p = str(Path(path).resolve())
        if clean_p in self._custom_search_paths:
            self._custom_search_paths.remove(clean_p)
        if self.has_ocio and self._config:
            try:
                self._config.clearSearchPaths()
                for p in self.get_search_paths():
                    self._config.addSearchPath(p)
            except Exception:
                pass
        self.config_changed.emit()

    def clear_search_paths(self) -> None:
        """Clear all search paths."""
        self._custom_search_paths.clear()
        if self.has_ocio and self._config:
            try:
                self._config.clearSearchPaths()
            except Exception:
                pass
        self.config_changed.emit()

    def set_context_var(self, key: str, value: str) -> None:
        """Set an OCIO context variable (e.g., SHOT=sh010)."""
        k = key.strip()
        v = value.strip()
        if not k:
            return
        self._context_vars[k] = v
        if self.has_ocio and self._config:
            try:
                self._config.addEnvironmentVar(k, v)
            except Exception:
                pass
        self.context_changed.emit()

    def get_context_vars(self) -> Dict[str, str]:
        """Return copy of active context variables."""
        return dict(self._context_vars)

    def remove_context_var(self, key: str) -> None:
        """Remove a context variable."""
        if key in self._context_vars:
            del self._context_vars[key]
            self.context_changed.emit()

    # --------------------------------------------------------------------------
    # Active Selections
    # --------------------------------------------------------------------------
    @property
    def color_management_mode(self) -> str:
        return self._color_management_mode

    @color_management_mode.setter
    def color_management_mode(self, mode: str) -> None:
        self._color_management_mode = str(mode)
        self.context_changed.emit()
        if self._color_management_mode == "ocio":
            self.bake_current_display_lut()
        else:
            self.ocio_lut_baked.emit(None)

    @property
    def standard_input_id(self) -> int:
        return self._standard_input_id

    @standard_input_id.setter
    def standard_input_id(self, val: int) -> None:
        self._standard_input_id = int(val)
        self.context_changed.emit()

    @property
    def standard_view_id(self) -> int:
        return self._standard_view_id

    @standard_view_id.setter
    def standard_view_id(self, val: int) -> None:
        self._standard_view_id = int(val)
        self.context_changed.emit()

    @property
    def input_space(self) -> str:
        return self._current_input_space

    @input_space.setter
    def input_space(self, name: str) -> None:
        self._current_input_space = name
        self.context_changed.emit()
        if self._color_management_mode == "ocio":
            self.bake_current_display_lut()

    @property
    def display(self) -> str:
        return self._current_display

    @display.setter
    def display(self, name: str) -> None:
        self._current_display = name
        self.context_changed.emit()
        if self._color_management_mode == "ocio":
            self.bake_current_display_lut()
            self.prewarm_adjacent_views()

    @property
    def view(self) -> str:
        return self._current_view

    @view.setter
    def view(self, name: str) -> None:
        self._current_view = name
        self.context_changed.emit()
        if self._color_management_mode == "ocio":
            self.bake_current_display_lut()

    @property
    def exposure_ev(self) -> float:
        return self._exposure_ev

    @exposure_ev.setter
    def exposure_ev(self, ev: float) -> None:
        self._exposure_ev = float(ev)

    @property
    def gamma(self) -> float:
        return self._gamma

    @gamma.setter
    def gamma(self, val: float) -> None:
        self._gamma = max(0.05, float(val))

    # --------------------------------------------------------------------------
    # CPU Pixel Inspection Conversion
    # --------------------------------------------------------------------------
    def process_rgb(self, r: float, g: float, b: float) -> Tuple[float, float, float]:
        """Convert single RGB float pixel from input colorspace to display view + exposure/gamma."""
        gain = 2.0 ** self._exposure_ev

        if self._color_management_mode == "standard":
            pixel = np.array([r, g, b], dtype=np.float32)
            lin = apply_standard_to_linear(pixel, self._standard_input_id)
            lin = lin * gain
            disp = apply_linear_to_standard(lin, self._standard_view_id)
            r_out, g_out, b_out = float(disp[0]), float(disp[1]), float(disp[2])
        elif self.has_ocio and self._config:
            r_lin = r * gain
            g_lin = g * gain
            b_lin = b * gain
            try:
                transform = ocio.DisplayViewTransform(
                    src=self._current_input_space,
                    display=self._current_display,
                    view=self._current_view,
                )
                processor = self._config.getProcessor(transform)
                cpu = processor.getDefaultCPUProcessor()
                pixel = np.array([r_lin, g_lin, b_lin], dtype=np.float32)
                cpu.applyRGB(pixel)
                r_out, g_out, b_out = float(pixel[0]), float(pixel[1]), float(pixel[2])
            except Exception:
                r_out, g_out, b_out = self._fallback_tone_map(r_lin, g_lin, b_lin)
        else:
            r_out, g_out, b_out = self._fallback_tone_map(r_lin, g_lin, b_lin)

        if abs(self._gamma - 1.0) > 0.001 and self._gamma > 0.0:
            inv_gamma = 1.0 / self._gamma
            r_out = math_pow_safe(r_out, inv_gamma)
            g_out = math_pow_safe(g_out, inv_gamma)
            b_out = math_pow_safe(b_out, inv_gamma)

        return r_out, g_out, b_out

    def _fallback_tone_map(self, r: float, g: float, b: float) -> Tuple[float, float, float]:
        """ACES-inspired analytical Narkowicz fit curve for realistic highlight compression."""
        def fit(x: float) -> float:
            x = max(0.0, x)
            a = 2.51
            b = 0.03
            c = 2.43
            d = 0.59
            e = 0.14
            return np.clip((x * (a * x + b)) / (x * (c * x + d) + e), 0.0, 1.0)

        def lin_to_srgb(v: float) -> float:
            v = fit(v)
            return (1.055 * (v ** (1.0 / 2.4)) - 0.055) if v > 0.0031308 else (12.92 * v)

        return lin_to_srgb(r), lin_to_srgb(g), lin_to_srgb(b)


def math_pow_safe(base: float, exponent: float) -> float:
    if base <= 0.0:
        return 0.0
    return float(base ** exponent)
