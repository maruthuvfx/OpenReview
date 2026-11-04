"""
Maximized Viewport OpenGL Canvas.
Implements modern GLSL shader pipeline with dynamic texture uploads, zoom/pan transforms,
live pixel inspection, and layered vector annotation rendering.
"""
from __future__ import annotations

import sys
import math
import time
from typing import Optional, Tuple
import numpy as np

from PySide6.QtCore import Qt, QPoint, QPointF, QRect, QRectF, Signal, QTimer
from PySide6.QtGui import (
    QPainter,
    QPainterPath,
    QColor,
    QPen,
    QFont,
    QFontMetrics,
    QMatrix4x4,
    QSurfaceFormat,
    QImage,
    QAction,
    QKeyEvent,
    QCursor,
)
from PySide6.QtWidgets import QWidget, QInputDialog, QMenu
from PySide6.QtOpenGLWidgets import QOpenGLWidget
from PySide6.QtOpenGL import (
    QOpenGLShaderProgram,
    QOpenGLShader,
    QOpenGLTexture,
    QOpenGLBuffer,
    QOpenGLVertexArrayObject,
)

from dataclasses import dataclass, field
from enum import Enum
from openreview.color.shader_builder import VERTEX_SHADER_SOURCE, FRAGMENT_SHADER_SOURCE, build_fragment_shader
from openreview.color.lut_manager import CubeLut, AscCdl, ColorGradeSettings
from openreview.models.annotations import AnnotationStore, ShapeType, Stroke
from openreview.models.comparison import ComparisonState, ComparisonMode
from openreview.core.preferences import PreferencesManager
from openreview.widgets.canvas_text_editor import CanvasTextEditor

_gl_tex_sub_image_2d = None
_gl_tex_sub_image_2d_failed = False


def _get_gl_tex_sub_image_2d():
    global _gl_tex_sub_image_2d, _gl_tex_sub_image_2d_failed
    if _gl_tex_sub_image_2d is not None:
        return _gl_tex_sub_image_2d
    if _gl_tex_sub_image_2d_failed:
        return None
    try:
        import ctypes
        if sys.platform == "win32":
            lib = ctypes.windll.opengl32
        elif sys.platform == "darwin":
            lib = ctypes.CDLL("/System/Library/Frameworks/OpenGL.framework/OpenGL")
        else:
            try:
                lib = ctypes.CDLL("libGL.so.1")
            except Exception:
                lib = ctypes.CDLL("libGL.so")
        fn = lib.glTexSubImage2D
        fn.argtypes = [
            ctypes.c_uint,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_uint,
            ctypes.c_uint,
            ctypes.c_void_p,
        ]
        fn.restype = None
        _gl_tex_sub_image_2d = fn
        return _gl_tex_sub_image_2d
    except Exception:
        _gl_tex_sub_image_2d_failed = True
        return None



@dataclass
class ColorSampler:
    """Represents a pinned point/aperture or rectangular area color sampler on the viewport."""
    sampler_id: int
    norm_x: float
    norm_y: float
    norm_w: float = 0.0
    norm_h: float = 0.0
    aperture: int = 1
    x: int = 0
    y: int = 0
    w: int = 1
    h: int = 1
    r: float = 0.0
    g: float = 0.0
    b: float = 0.0
    a: float = 1.0
    min_rgba: Tuple[float, float, float, float] = (0.0, 0.0, 0.0, 1.0)
    max_rgba: Tuple[float, float, float, float] = (0.0, 0.0, 0.0, 1.0)
    avg_rgba: Tuple[float, float, float, float] = (0.0, 0.0, 0.0, 1.0)
    med_rgba: Tuple[float, float, float, float] = (0.0, 0.0, 0.0, 1.0)
    nan_count: int = 0
    inf_count: int = 0
    neg_count: int = 0
    nan_coords: list[Tuple[int, int]] = field(default_factory=list)
    inf_coords: list[Tuple[int, int]] = field(default_factory=list)
    neg_coords: list[Tuple[int, int]] = field(default_factory=list)


class PlaybackEngineMode(str, Enum):
    """Supported hardware and software playback rendering engines."""
    OPENGL_CORE = "opengl_core"      # Hardware-accelerated OpenGL Core Profile with direct texture transfers
    ASYNC_PBO = "async_pbo"          # Double-buffered Pixel Buffer Objects for non-blocking DMA streaming
    SOFTWARE_SAFE = "software_safe"  # Safe CPU-staged fallback for Remote Desktop / minimal GPU


class ViewportCanvas(QOpenGLWidget):
    """
    Maximized OpenGL Core Profile Viewport.
    - Hardware texture uploads for Primary (A) and Reference (B) media
    - Real-time GLSL evaluation of Exposure EV, Gamma, Channel Isolation, and A/B Wiping
    - Middle Mouse / Alt+LMB drag pan & Wheel smooth zoom centered on cursor
    - Live pixel inspection readout (X, Y, RGBA, HSV, Luma Y)
    - Transparent layered vector markup overlay
    """

    pixel_inspected = Signal(int, int, float, float, float, float)  # x, y, r, g, b, a
    samplers_changed = Signal(list)  # list[ColorSampler]
    eyedropper_mode_changed = Signal(bool)  # True when eyedropper picking mode is active
    zoom_changed = Signal(float)  # zoom factor
    resolution_changed = Signal(int, int)  # width, height
    file_dropped = Signal(str)  # dropped local file or folder path
    files_dropped = Signal(list)  # list of dropped local paths
    files_dropped_target = Signal(list, str, list)  # (paths: list[str], target: "stack" | "bin", source_ids: list[str])
    wipe_position_changed = Signal(float)  # 0.0 to 1.0 interactive wipe split position
    wipe_angle_changed = Signal(float)  # 0.0 to 360.0 interactive wipe rotation angle
    scrub_requested = Signal(int)  # delta frame scrub steps
    exit_presentation_requested = Signal()
    toggle_presentation_requested = Signal()
    exit_fullscreen_requested = Signal()
    toggle_fullscreen_requested = Signal()
    draw_tool_changed = Signal(object)  # Optional[ShapeType]
    contact_sheet_tile_double_clicked = Signal(str)  # emits clicked layer name in contact sheet mode

    def __init__(
        self,
        annotation_store: Optional[AnnotationStore] = None,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAcceptDrops(True)
        self.is_presentation_mode: bool = False

        # Set OpenGL 3.3 Core Profile format
        gl_format = QSurfaceFormat()
        gl_format.setVersion(3, 3)
        gl_format.setProfile(QSurfaceFormat.OpenGLContextProfile.CoreProfile)
        gl_format.setSwapInterval(0)
        self.setFormat(gl_format)

        self.annotation_store = annotation_store or AnnotationStore(self)
        self.current_frame = 1001

        # Viewport Navigation State
        self._zoom = 1.0
        self._pan_x = 0.0
        self._pan_y = 0.0
        self._last_mouse_pos = QPoint()
        self._current_mouse_pos = QPoint(300, 200)
        self._is_panning = False
        self._is_zooming_drag = False
        self.has_active_media: bool = False
        self.is_bad_frame: bool = False
        self._is_wiping = False
        self._is_rotating_wipe = False
        self._wipe_drag_start_angle = 0.0
        self._drag_overlay_active: bool = False
        self._drag_target: Optional[str] = None
        self._wipe_start_wipe_angle = 0.0
        self._wipe_hover_part: Optional[str] = None
        self._wipe_handle_center = QPointF(0, 0)
        self._wipe_rot_handle_center = QPointF(0, 0)
        self._wipe_screen_normal = QPointF(1, 0)
        self._wipe_screen_tangent = QPointF(0, 1)
        self.scrub_mode_enabled = True
        self._is_scrubbing = False
        self._scrub_accum_dx = 0.0

        # Color Inspector & Multi-Sampler Eyedropper State
        self._inspector_aperture: int = 1
        self._eyedropper_mode: bool = False
        self._color_samplers: list[ColorSampler] = []
        self._next_sampler_id: int = 1
        self._dragging_sampler_id: Optional[int] = None
        self._creating_sampler_drag: bool = False
        self._sampler_drag_start_screen: Optional[QPointF] = None
        self._sampler_drag_curr_screen: Optional[QPointF] = None
        self._active_analyzer_sampler_id: Optional[int] = None
        self.highlight_nan_pixels: bool = False
        self.highlight_inf_pixels: bool = False
        self.highlight_neg_pixels: bool = False

        # Current Drawing State
        self._active_draw_tool: Optional[ShapeType] = None
        self._active_draw_color: Tuple[int, int, int, int] = (229, 168, 35, 255)
        self._base_draw_color: Tuple[int, int, int] = (229, 168, 35)
        try:
            self._active_draw_width: float = getattr(PreferencesManager.instance().prefs, "default_brush_width", 6.0)
        except Exception:
            self._active_draw_width = 6.0
        self._active_draw_opacity: float = 1.0
        self._current_stroke_points: list[Tuple[float, float]] = []

        # In-Canvas Text Annotation Editor
        self._text_editor = CanvasTextEditor(self)
        self._text_editor.committed.connect(self._on_text_editor_committed)
        self._text_editor.cancelled.connect(self._on_text_editor_cancelled)
        self._editing_text_stroke_idx: Optional[int] = None

        # Color & Shader Uniforms
        self.exposure_ev: float = 0.0
        self.gain: float = 1.0
        self.gamma: float = 1.0
        self.saturation: float = 1.0
        self.channel_mode: int = 0  # 0: RGB, 1: R, 2: G, 3: B, 4: A, 5: Y
        self.channel_matrix: np.ndarray = np.eye(4, dtype=np.float32)
        self.channel_enable: tuple = (1.0, 1.0, 1.0, 1.0)
        self.channel_solo_mode: int = 1  # 0: Color Solo, 1: Greyscale Solo, 2: Normals Remap
        self.input_space: int = 0   # 0: sRGB, 1: ACEScg/Linear, 2: Raw
        self.view_transform: int = 0 # 0: sRGB Display, 1: ACES Filmic, 2: Raw
        self.stereo_mode: int = 0   # 0: Off, 1: Left, 2: Right, 3: Anaglyph, 4: SBS, 5: Diff
        self.comparison_state = ComparisonState()

        # Geometry Transforms
        self.flip_x: bool = False
        self.flip_y: bool = False
        self.rotation: int = 0  # 0, 90, 180, 270
        self.pixel_aspect_ratio: float = 1.0
        self.downsample_ratio: float = 1.0
        self.texture_filtering: str = "linear"  # "linear" or "nearest"
        self.premultiplied_alpha: bool = True

        # Matte Overlay
        self.matte_ratio: Optional[float] = None  # None = No matte, e.g. 1.33, 1.85, 2.35
        self.matte_opacity: float = 1.0

        # Comp QC Toolset State & Spline Overlays
        self.comp_qc_state: Optional[Any] = None
        self.comp_qc_splines: list = []

        # Viewport Background
        self.background_mode: str = "black"  # "black", "grey18", "grey50", "white", "checker"

        # Image Data Cache
        self._image_a: Optional[np.ndarray] = None
        self._image_b: Optional[np.ndarray] = None
        self._img_width = 1920
        self._img_height = 1080

        # Playback Engine & GPU PBO State
        self.playback_engine: PlaybackEngineMode = PlaybackEngineMode.OPENGL_CORE
        self.vsync_enabled: bool = False
        self._pbo_a: list[Optional[QOpenGLBuffer]] = [None, None]
        self._pbo_b: list[Optional[QOpenGLBuffer]] = [None, None]
        self._pbo_a_index: int = 0
        self._pbo_b_index: int = 0
        self._pbo_a_nbytes: int = 0
        self._pbo_b_nbytes: int = 0

        # OpenGL Objects
        self._shader_program: Optional[QOpenGLShaderProgram] = None
        self._checker_shader: Optional[QOpenGLShaderProgram] = None
        self._texture_a: Optional[QOpenGLTexture] = None
        self._texture_b: Optional[QOpenGLTexture] = None
        self._texture_a_fmt: Optional[QOpenGLTexture.TextureFormat] = None
        self._texture_b_fmt: Optional[QOpenGLTexture.TextureFormat] = None
        self._lut_texture: Optional[QOpenGLTexture] = None
        self._vao: Optional[QOpenGLVertexArrayObject] = None
        self._vbo: Optional[QOpenGLBuffer] = None
        self._uniform_loc_cache: dict[str, int] = {}

        self.color_grading = ColorGradeSettings()
        self._pending_upload_a = False
        self._pending_upload_b = False
        self._pending_upload_matte = False
        self._pending_upload_lut = False
        self._image_matte: Optional[np.ndarray] = None
        self._texture_matte: Optional[QOpenGLTexture] = None
        self._ocio_lut_texture: Optional[QOpenGLTexture] = None
        self._pending_upload_ocio_lut = False
        self._active_ocio_lut_bake: Optional[Any] = None

        # Info Strip State
        self.show_info_strip: bool = False
        self.info_strip_data: dict = {
            "media_name": "Synthetic Pattern",
            "start_frame": 1001,
            "end_frame": 1200,
            "fps": 24.0,
        }

        # Safe Guides Overlay (Action Safe 90%, Title Safe 80%, Crosshair, Rule of Thirds)
        self.show_safe_guides: bool = False
        self.show_action_safe: bool = True
        self.show_title_safe: bool = True
        self.show_crosshair: bool = True
        self.show_thirds_grid: bool = False
        self.safe_guides_matte_constrained: bool = True

        # Onion Skinning / Ghosting
        self.onion_skin_mode: str = "off"  # "off", "prev", "next", "both"
        self.onion_skin_opacity: float = 0.35

        # HUD Toast Notification System
        self._toast_message: Optional[str] = None
        self._toast_timer: Optional[QTimer] = None
        self._is_playing: bool = False

        # Multi-Channel EXR Contact Sheet Mode State
        self._contact_sheet_mode: bool = False
        self._contact_sheet_tile_map: dict[str, Tuple[int, int, int, int]] = {}

    def set_contact_sheet_mode(self, enabled: bool, tile_map: Optional[dict] = None) -> None:
        """Configure active contact sheet mode and coordinate hit-test tile map."""
        self._contact_sheet_mode = enabled
        self._contact_sheet_tile_map = tile_map or {}
        self.update()

    def set_is_playing(self, playing: bool) -> None:
        """Inform viewport of active playback state to enable low-latency proxy streaming."""
        was_playing = self._is_playing
        self._is_playing = bool(playing)
        if was_playing and not self._is_playing:
            # On pause, invalidate proxy texture to immediately restore full raw resolution
            self._pending_upload_a = True
            if self._image_b is not None:
                self._pending_upload_b = True
            self.update()

    # --------------------------------------------------------------------------
    # OpenGL Lifecycle
    # --------------------------------------------------------------------------
    def initializeGL(self) -> None:
        if not self.isValid():
            return
        self._uniform_loc_cache.clear()
        # Create Shader Program
        self._shader_program = QOpenGLShaderProgram(self)
        self._shader_program.addShaderFromSourceCode(QOpenGLShader.ShaderType.Vertex, VERTEX_SHADER_SOURCE)
        self._shader_program.addShaderFromSourceCode(QOpenGLShader.ShaderType.Fragment, FRAGMENT_SHADER_SOURCE)
        self._shader_program.link()

        # Background Checkerboard Shader Program
        checker_vshader = """#version 330 core
        layout (location = 0) in vec2 aPos;
        void main() {
            gl_Position = vec4(aPos, 0.0, 1.0);
        }
        """
        checker_fshader = """#version 330 core
        out vec4 FragColor;
        void main() {
            int cx = int(gl_FragCoord.x / 16.0);
            int cy = int(gl_FragCoord.y / 16.0);
            vec3 c = ((cx + cy) % 2 == 0) ? vec3(0.12, 0.12, 0.12) : vec3(0.18, 0.18, 0.18);
            FragColor = vec4(c, 1.0);
        }
        """
        self._checker_shader = QOpenGLShaderProgram(self)
        self._checker_shader.addShaderFromSourceCode(QOpenGLShader.ShaderType.Vertex, checker_vshader)
        self._checker_shader.addShaderFromSourceCode(QOpenGLShader.ShaderType.Fragment, checker_fshader)
        self._checker_shader.link()

        # Setup Quad Geometry: Position (vec2), TexCoord (vec2)
        quad_vertices = np.array([
            # pos.x, pos.y,  tex.u, tex.v
            -1.0, -1.0,      0.0, 1.0,
             1.0, -1.0,      1.0, 1.0,
             1.0,  1.0,      1.0, 0.0,
            -1.0, -1.0,      0.0, 1.0,
             1.0,  1.0,      1.0, 0.0,
            -1.0,  1.0,      0.0, 0.0,
        ], dtype=np.float32)

        self._vao = QOpenGLVertexArrayObject(self)
        self._vao.create()
        self._vao.bind()

        self._vbo = QOpenGLBuffer(QOpenGLBuffer.Type.VertexBuffer)
        self._vbo.create()
        self._vbo.bind()
        self._vbo.allocate(quad_vertices.tobytes(), quad_vertices.nbytes)

        # Attribute 0: Position
        self._shader_program.enableAttributeArray(0)
        self._shader_program.setAttributeBuffer(0, 0x1406, 0, 2, 4 * 4)  # GL_FLOAT = 0x1406

        # Attribute 1: TexCoord
        self._shader_program.enableAttributeArray(1)
        self._shader_program.setAttributeBuffer(1, 0x1406, 2 * 4, 2, 4 * 4)

        self._vao.release()
        self._vbo.release()

    def resizeGL(self, w: int, h: int) -> None:
        for child in self.children():
            if hasattr(child, "realign_docked"):
                child.realign_docked()
            elif hasattr(child, "auto_center_in_parent"):
                child.auto_center_in_parent()
        self.update()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        for child in self.children():
            if hasattr(child, "realign_docked"):
                child.realign_docked()
            elif hasattr(child, "auto_center_in_parent"):
                child.auto_center_in_parent()

    def set_channel_matrix(self, mat: np.ndarray) -> None:
        """Apply a 4x4 color transform / swizzling matrix to viewport rendering."""
        self.channel_matrix = np.asarray(mat, dtype=np.float32)
        self.update()

    def set_channel_enable(self, r: bool, g: bool, b: bool, a: bool) -> None:
        """Enable or disable individual color channels."""
        self.channel_enable = (1.0 if r else 0.0, 1.0 if g else 0.0, 1.0 if b else 0.0, 1.0 if a else 0.0)
        self.update()

    def set_channel_solo_mode(self, mode: int) -> None:
        """0: Color Solo, 1: Greyscale Solo, 2: Normals Remap [-1, 1]->[0, 1]."""
        self.channel_solo_mode = int(mode)
        self.update()

    def solo_channel(self, mode: int, greyscale: bool = True) -> None:
        """Convenience method to solo a channel (0: RGB, 1: R, 2: G, 3: B, 4: A, 5: Y)."""
        self.channel_mode = int(mode)
        self.channel_solo_mode = 1 if greyscale else 0
        self.update()

    def paintGL(self) -> None:
        if not self._shader_program or not self._shader_program.isLinked():
            return

        # Upload pending textures
        if self._pending_upload_a and self._image_a is not None:
            self._upload_texture_a()
            self._pending_upload_a = False

        if self._pending_upload_b and self._image_b is not None:
            self._upload_texture_b()
            self._pending_upload_b = False

        if getattr(self, "_pending_upload_matte", False) and getattr(self, "_image_matte", None) is not None:
            self._upload_texture_matte()
            self._pending_upload_matte = False

        if self._pending_upload_lut:
            self._upload_lut_texture()
            self._pending_upload_lut = False

        if getattr(self, "_pending_upload_ocio_lut", False):
            self._upload_ocio_lut_texture()
            self._pending_upload_ocio_lut = False

        ctx = self.context()
        if not ctx:
            return
        gl = ctx.functions()
        if not gl:
            return

        # Background Clear Color
        if self.background_mode == "grey18":
            gl.glClearColor(0.18, 0.18, 0.18, 1.0)
        elif self.background_mode == "grey50":
            gl.glClearColor(0.50, 0.50, 0.50, 1.0)
        elif self.background_mode == "white":
            gl.glClearColor(0.96, 0.96, 0.96, 1.0)
        elif self.background_mode == "checker":
            gl.glClearColor(0.12, 0.12, 0.12, 1.0)
        else:  # "black" default
            gl.glClearColor(0.0, 0.0, 0.0, 1.0)
        gl.glClear(0x00004000)  # GL_COLOR_BUFFER_BIT

        # If checkerboard background mode, draw full-screen checkerboard pattern in OpenGL first
        if self.background_mode == "checker" and self._checker_shader and self._checker_shader.isLinked():
            self._checker_shader.bind()
            self._vao.bind()
            gl.glDrawArrays(0x0004, 0, 6)
            self._vao.release()
            self._checker_shader.release()

        if self._texture_a is not None:
            # Enable standard alpha blending for transparent media over background
            gl.glEnable(0x0BE2)  # GL_BLEND
            if getattr(self, "premultiplied_alpha", True):
                gl.glBlendFunc(0x0001, 0x0303)  # GL_ONE, GL_ONE_MINUS_SRC_ALPHA (Premultiplied)
            else:
                gl.glBlendFunc(0x0302, 0x0303)  # GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA (Straight Alpha)

            self._shader_program.bind()

            # Bind primary texture unit 0
            self._texture_a.bind(0)
            self._set_uniform("uTextureA", 0)

            # Bind reference texture unit 1 if comparison active, stereo mode active, or QC plate compare
            qc = getattr(self, "comp_qc_state", None)
            is_stereo_active = (self.stereo_mode != 0)
            is_qc_using_b = (qc is not None and (getattr(qc, "compare_against_plate", False) or getattr(qc, "qc_mode", 0) in (4, 7, 8)))
            has_b = self._texture_b is not None and (self.comparison_state.mode != ComparisonMode.OFF or is_stereo_active or is_qc_using_b)
            if has_b and self._texture_b:
                self._texture_b.bind(1)
                self._set_uniform("uTextureB", 1)
            self._set_uniform("uHasTextureB", 1 if has_b else 0)

            # Bind optional Matte texture unit 3
            matte_tex = getattr(self, "_texture_matte", None)
            has_matte = matte_tex is not None and (
                qc is not None and (
                    getattr(qc, "alpha_overlay_enabled", False)
                    or getattr(qc, "overlay_matte_enabled", False)
                    or getattr(qc, "greycheck_enabled", False)
                    or getattr(qc, "checkerboard_enabled", False)
                )
            )
            if has_matte and matte_tex:
                matte_tex.bind(3)
                self._set_uniform("uTextureMatte", 3)
            self._set_uniform("uHasTextureMatte", 1 if has_matte else 0)

            # Pass Stereo 3D Uniform
            self._set_uniform("uStereoMode", int(self.stereo_mode))

            # Pass Color & Exposure Uniforms
            gain_val = float(getattr(self, "gain", 1.0))
            exp_val = float(self.exposure_ev)
            total_gain = gain_val * (2.0 ** exp_val)
            gamma_val = float(self.gamma)
            sat_val = float(getattr(self, "saturation", 1.0))

            self._set_uniform("uExposure", exp_val)
            self._set_uniform("uGain", gain_val)
            self._set_uniform("uTotalGain", total_gain)
            self._set_uniform("uSaturation", sat_val)
            self._set_uniform("uApplySat", abs(sat_val - 1.0) > 0.0001)
            self._set_uniform("uGamma", gamma_val)
            self._set_uniform("uGammaPower", 1.0 / max(0.0001, abs(gamma_val)))
            self._set_uniform("uApplyGamma", abs(gamma_val - 1.0) > 0.0001)
            self._set_uniform("uChannelMode", int(self.channel_mode))
            from PySide6.QtGui import QMatrix4x4
            c_mat = getattr(self, "channel_matrix", None)
            if c_mat is not None and len(c_mat.flatten()) == 16:
                q_mat = QMatrix4x4(*c_mat.flatten().tolist())
                self._shader_program.setUniformValue("uChannelMatrix", q_mat)
            else:
                self._shader_program.setUniformValue("uChannelMatrix", QMatrix4x4())

            ce = getattr(self, "channel_enable", (1.0, 1.0, 1.0, 1.0))
            self._shader_program.setUniformValue("uChannelEnable", float(ce[0]), float(ce[1]), float(ce[2]), float(ce[3]))
            self._set_uniform("uChannelSoloMode", int(getattr(self, "channel_solo_mode", 1)))
            self._set_uniform("uInputSpace", int(self.input_space))
            self._set_uniform("uViewTransform", int(self.view_transform))

            # Pass Comparison Uniforms
            self._set_uniform("uComparisonMode", int(self.comparison_state.mode))
            self._set_uniform("uWipePos", float(self.comparison_state.wipe_position))
            wc = getattr(self.comparison_state, "wipe_center", (0.5, 0.5))
            self._shader_program.setUniformValue("uWipeCenter", float(wc[0]), float(wc[1]))
            angle_rad = math.radians(self.comparison_state.wipe_angle_deg)
            self._set_uniform("uWipeAngle", float(angle_rad))
            img_aspect = (float(self._img_width) * float(self.pixel_aspect_ratio)) / max(1.0, float(self._img_height))
            self._set_uniform("uAspect", float(img_aspect))
            self._set_uniform("uDiffBoost", float(self.comparison_state.diff_boost))
            self._set_uniform("uStackMix", float(getattr(self.comparison_state, "stack_mix", 1.0)))
            show_wipe_line = bool(getattr(self.comparison_state, "wipe_show_line", True))
            self._set_uniform("uWipeShowLine", 1 if show_wipe_line else 0)
            diff_heatmap = bool(getattr(self.comparison_state, "diff_heatmap", False))
            self._set_uniform("uDiffHeatmap", 1 if diff_heatmap else 0)
            spotlight_radius = float(getattr(self.comparison_state, "spotlight_radius", 0.25))
            self._set_uniform("uSpotlightRadius", spotlight_radius)
            ch_a = getattr(self.comparison_state, "channels_a", (True, True, True, True))
            self._shader_program.setUniformValue(
                "uLayerAChannels",
                float(1.0 if ch_a[0] else 0.0),
                float(1.0 if ch_a[1] else 0.0),
                float(1.0 if ch_a[2] else 0.0),
                float(1.0 if ch_a[3] else 0.0),
            )

            # Pass ASC CDL Uniforms
            cdl = self.color_grading.active_cdl
            has_cdl = (cdl is not None and self.color_grading.cdl_enabled)
            self._set_uniform("uHasCdl", 1 if has_cdl else 0)
            if has_cdl and cdl:
                self._shader_program.setUniformValue("uCdlSlope", float(cdl.slope[0]), float(cdl.slope[1]), float(cdl.slope[2]))
                self._shader_program.setUniformValue("uCdlOffset", float(cdl.offset[0]), float(cdl.offset[1]), float(cdl.offset[2]))
                self._shader_program.setUniformValue("uCdlPower", float(cdl.power[0]), float(cdl.power[1]), float(cdl.power[2]))
                self._set_uniform("uCdlSat", float(cdl.saturation))

            # Pass Interactive Color Grading Uniforms
            cg = self.color_grading
            self._shader_program.setUniformValue("uGradingGain", float(cg.gain[0]), float(cg.gain[1]), float(cg.gain[2]))
            self._shader_program.setUniformValue("uGradingOffset", float(cg.offset[0]), float(cg.offset[1]), float(cg.offset[2]))
            self._set_uniform("uGradingGamma", float(cg.gamma))
            self._set_uniform("uGradingContrast", float(cg.contrast))
            self._set_uniform("uGradingSat", float(cg.saturation))
            self._set_uniform("uGradingHue", float(cg.hue))
            self._set_uniform("uDisplayBrightness", float(cg.brightness))
            self._set_uniform("uInvertColors", 1 if cg.invert_colors else 0)
            self._set_uniform("uShowOutOfRange", 1 if cg.show_out_of_range else 0)

            # Pass Comp QC Toolset Uniforms
            if qc is not None:
                self._set_uniform("uQcMode", int(qc.qc_mode))
                self._set_uniform("uQcClippingThreshold", float(qc.clipping_threshold))
                self._set_uniform("uQcNegativeOverComp", 1 if qc.negative_over_comp else 0)
                self._set_uniform("uQcSaturationBoost", float(qc.saturation_boost))
                self._set_uniform("uQcHighpassRadius", float(getattr(qc, "blur_radius", qc.highpass_radius)))
                self._set_uniform("uQcClampValues", 1 if qc.clamp_plate_values else 0)
                self._set_uniform("uQcCheckClampedAlpha", 1 if getattr(qc, "clamp_alpha", qc.check_clamped_alpha) else 0)
                self._set_uniform("uQcAlphaMaskEnabled", 1 if getattr(qc, "alpha_overlay_enabled", qc.alpha_mask_enabled) else 0)
                c_mask = getattr(qc, "overlay_color", qc.alpha_mask_color)
                self._shader_program.setUniformValue(
                    "uQcAlphaMaskColor",
                    float(c_mask.redF()),
                    float(c_mask.greenF()),
                    float(c_mask.blueF()),
                    float(getattr(qc, "mix_overlay", qc.alpha_mask_opacity)),
                )
                self._set_uniform("uQcAlphaMaskInvert", 1 if qc.alpha_mask_invert else 0)
                self._set_uniform("uQcFlopComp", 1 if qc.flop_comp else 0)
                self._set_uniform("uQcHslMode", int(qc.hsl_mode))
                self._set_uniform("uQcGrainCompareMode", int(qc.grain_compare_mode))
                self._set_uniform("uQcGrainBoost", float(qc.grain_boost))
                self._set_uniform("uQcGamma", float(getattr(qc, "qc_gamma", 1.0)))
                self._set_uniform("uQcGain", float(getattr(qc, "qc_gain", 1.0)))
                self._set_uniform("uQcSoftclip", float(getattr(qc, "qc_softclip", 0.0)))
                self._set_uniform("uQcSatMultiplier", float(getattr(qc, "qc_sat_multiplier", 1.0)))
                self._set_uniform("uQcHueRotation", float(getattr(qc, "qc_hue_rotation", 0.0)))
                self._set_uniform("uQcEdgeThreshold", float(getattr(qc, "edge_threshold", 0.0)))
                self._set_uniform("uQcCompareAgainstPlate", 1 if getattr(qc, "compare_against_plate", False) else 0)
                self._set_uniform("uQcComparePlates", 1 if getattr(qc, "compare_plates", False) else 0)
                self._set_uniform("uQcMixOverlay", float(getattr(qc, "mix_overlay", 1.0)))
                self._set_uniform("uQcOverlayMatte", 1 if getattr(qc, "overlay_matte_enabled", False) else 0)
                self._set_uniform("uQcMatteIsPremult", 1 if getattr(qc, "matte_is_premult", True) else 0)
                self._set_uniform("uQcGreycheckEnabled", 1 if getattr(qc, "greycheck_enabled", False) else 0)
                self._set_uniform("uQcCheckerboardEnabled", 1 if getattr(qc, "checkerboard_enabled", False) else 0)
                self._set_uniform("uQcGreyValue", float(getattr(qc, "grey_value", 0.5)))
                self._set_uniform("uQcGridScale", float(getattr(qc, "grid_scale", 32.0)))
                self._set_uniform("uQcCheckerContrast", float(getattr(qc, "checker_contrast", 0.2)))
                self._set_uniform("uQcHipassValue", float(getattr(qc, "hipass_value", getattr(qc, "blur_radius", 3.0))))
                self._shader_program.setUniformValue(
                    "uQcMatteChannels",
                    1.0 if getattr(qc, "enable_red", True) else 0.0,
                    1.0 if getattr(qc, "enable_green", True) else 0.0,
                    1.0 if getattr(qc, "enable_blue", True) else 0.0,
                    1.0 if getattr(qc, "enable_alpha", True) else 0.0,
                )
                cur_f = float(getattr(self.clock, "current_frame", 1)) if hasattr(self, "clock") and self.clock else 1.0
                self._set_uniform("uCurrentFrame", cur_f)
            else:
                self._set_uniform("uQcMode", 0)
                self._set_uniform("uQcClippingThreshold", 50.0)
                self._set_uniform("uQcNegativeOverComp", 0)
                self._set_uniform("uQcSaturationBoost", 4.0)
                self._set_uniform("uQcHighpassRadius", 5.0)
                self._set_uniform("uQcClampValues", 0)
                self._set_uniform("uQcCheckClampedAlpha", 0)
                self._set_uniform("uQcAlphaMaskEnabled", 0)
                self._shader_program.setUniformValue("uQcAlphaMaskColor", 0.86, 0.14, 0.14, 0.5)
                self._set_uniform("uQcAlphaMaskInvert", 0)
                self._set_uniform("uQcFlopComp", 0)
                self._set_uniform("uQcHslMode", 0)
                self._set_uniform("uQcGrainCompareMode", 0)
                self._set_uniform("uQcGrainBoost", 5.0)
                self._set_uniform("uQcGamma", 1.0)
                self._set_uniform("uQcGain", 1.0)
                self._set_uniform("uQcSoftclip", 0.0)
                self._set_uniform("uQcSatMultiplier", 1.0)
                self._set_uniform("uQcHueRotation", 0.0)
                self._set_uniform("uQcEdgeThreshold", 0.0)
                self._set_uniform("uQcCompareAgainstPlate", 0)
                self._set_uniform("uQcComparePlates", 0)
                self._set_uniform("uQcMixOverlay", 1.0)
                self._set_uniform("uQcOverlayMatte", 0)
                self._set_uniform("uQcMatteIsPremult", 1)
                self._set_uniform("uQcGreycheckEnabled", 0)
                self._set_uniform("uQcCheckerboardEnabled", 0)
                self._set_uniform("uQcGreyValue", 0.5)
                self._set_uniform("uQcGridScale", 32.0)
                self._set_uniform("uQcCheckerContrast", 0.2)
                self._set_uniform("uQcHipassValue", 3.0)
                self._shader_program.setUniformValue("uQcMatteChannels", 1.0, 1.0, 1.0, 1.0)

            # Pass 3D LUT Texture Binding & Uniform
            has_lut = (self._lut_texture is not None and self.color_grading.active_lut is not None and self.color_grading.lut_enabled)
            self._set_uniform("uHasLut3D", 1 if has_lut else 0)
            if has_lut and self._lut_texture:
                self._lut_texture.bind(2)
                self._set_uniform("uLut3D", 2)

            # Pass OCIO v2 3D LUT Texture Binding & Uniforms (Texture Unit 4)
            has_ocio_lut = (self._ocio_lut_texture is not None and self._active_ocio_lut_bake is not None)
            self._set_uniform("uHasOcioLut3D", 1 if has_ocio_lut else 0)
            if has_ocio_lut and self._ocio_lut_texture:
                self._ocio_lut_texture.bind(4)
                self._set_uniform("uOcioLut3D", 4)
                bake = self._active_ocio_lut_bake
                self._set_uniform("uOcioShaperMode", int(getattr(bake, "shaper_mode", 1)))
                self._set_uniform("uOcioMinEv", float(getattr(bake, "min_ev", -10.0)))
                self._set_uniform("uOcioMaxEv", float(getattr(bake, "max_ev", 10.0)))
            else:
                self._set_uniform("uOcioShaperMode", 0)
                self._set_uniform("uOcioMinEv", -10.0)
                self._set_uniform("uOcioMaxEv", 10.0)

            # Compute Model-View-Projection Matrix (Isotropic screen-pixel scaling + Rotation + Flip + Pan)
            viewport_w = max(1.0, float(self.width()))
            viewport_h = max(1.0, float(self.height()))

            img_w, img_h = self._effective_image_dimensions()

            is_rot_90 = (self.rotation % 180 != 0)
            disp_w = img_h if is_rot_90 else img_w
            disp_h = img_w if is_rot_90 else img_h

            fit_scale = min(viewport_w / max(1.0, disp_w), viewport_h / max(1.0, disp_h))
            w_px = (img_w / 2.0) * fit_scale
            h_px = (img_h / 2.0) * fit_scale

            transform = QMatrix4x4()
            # Map screen pixel coordinates to NDC [-1, 1]
            transform.scale(2.0 / viewport_w, 2.0 / viewport_h, 1.0)
            # Pan in screen pixels
            transform.translate(self._pan_x, -self._pan_y, 0.0)
            # Rigid rotation in isotropic screen pixels (preserves aspect ratio without squeezing)
            if self.rotation != 0:
                transform.rotate(float(self.rotation), 0.0, 0.0, 1.0)
            # Scale quad to screen pixels with zoom
            transform.scale(w_px * self._zoom, h_px * self._zoom, 1.0)
            # Flop / Flip
            scale_flip_x = -1.0 if self.flip_x else 1.0
            scale_flip_y = -1.0 if self.flip_y else 1.0
            transform.scale(scale_flip_x, scale_flip_y, 1.0)

            self._set_uniform("uTransform", transform)

            # Draw Quad
            self._vao.bind()
            gl.glDrawArrays(0x0004, 0, 6)  # GL_TRIANGLES = 0x0004
            self._vao.release()
            self._shader_program.release()

        # 2. Vector overlays & HUD rendering via QPainter inside OpenGL FBO context
        painter = QPainter(self)
        try:
            if self._image_a is None:
                if self.has_active_media or self.is_bad_frame:
                    self._paint_missing_frame_overlay(painter)
                else:
                    self._paint_empty_state(painter)
            else:
                if self.is_bad_frame:
                    self._paint_missing_frame_overlay(painter)
                self._paint_markup_overlay(painter)
                self._paint_color_samplers_overlay(painter)
                self._paint_matte_overlay(painter)
                self._paint_comp_qc_splines_overlay(painter)
                if self.comparison_state.mode == ComparisonMode.WIPE:
                    self._paint_wipe_overlay(painter)
                elif self.comparison_state.mode == ComparisonMode.SPLIT:
                    self._paint_split_overlay(painter)
                if self.show_safe_guides and self.comparison_state.mode != ComparisonMode.SPLIT:
                    self._paint_safe_guides(painter)
                if self.show_info_strip:
                    self._paint_info_strip(painter)
            if self._toast_message:
                self._paint_hud_toast(painter)
            if self._drag_overlay_active:
                self._paint_drag_drop_overlay(painter)
        finally:
            painter.end()

    def paintEvent(self, event) -> None:
        """Execute OpenGL rendering and layered vector markup."""
        super().paintEvent(event)

    def _paint_missing_frame_overlay(self, painter: QPainter) -> None:
        """Render missing/bad frame hazard warning border and center badge."""
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        vw = self.width()
        vh = self.height()
        rect = self.rect()

        # 1. Hazard hatched warning border (accent red/amber)
        pen = QPen(QColor(235, 60, 60, 230), 4, Qt.PenStyle.DashLine)
        painter.setPen(pen)
        painter.drawRect(rect.adjusted(2, 2, -3, -3))

        # 2. Translucent dark overlay with warning tint
        painter.fillRect(rect, QColor(24, 10, 10, 180))

        # 3. Center warning badge
        badge_w, badge_h = 320, 72
        badge_x = (vw - badge_w) // 2
        badge_y = (vh - badge_h) // 2
        badge_rect = QRectF(badge_x, badge_y, badge_w, badge_h)

        painter.setPen(QPen(QColor(240, 70, 70), 1.5))
        painter.setBrush(QColor(36, 14, 14, 240))
        painter.drawRoundedRect(badge_rect, 8.0, 8.0)

        # Danger title
        painter.setPen(QColor(255, 90, 90))
        font_title = QFont("Segoe UI", 12, QFont.Weight.Bold)
        painter.setFont(font_title)
        painter.drawText(
            QRectF(badge_x, badge_y + 10, badge_w, 24),
            Qt.AlignmentFlag.AlignCenter,
            f"MISSING FRAME #{self.current_frame}"
        )

        # Subtitle
        painter.setPen(QColor(190, 190, 190))
        font_sub = QFont("Segoe UI", 9)
        painter.setFont(font_sub)
        painter.drawText(
            QRectF(badge_x, badge_y + 38, badge_w, 22),
            Qt.AlignmentFlag.AlignCenter,
            "Frame data could not be read or decoded"
        )
        painter.restore()

    def _paint_empty_state(self, painter: QPainter) -> None:
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect()
        painter.setPen(QColor(120, 120, 120))
        font = QFont("Segoe UI", 12)
        font.setWeight(QFont.Weight.Medium)
        painter.setFont(font)
        text = "Drop media file / sequence here or press Ctrl+O"
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)
        painter.restore()

    def _get_wipe_geometry(self) -> Tuple[QPointF, QPointF, QPointF, QPointF, QPointF, QPointF]:
        """Compute screen-space geometry for the A/B wipe divider line and interactive handles."""
        w = max(1.0, float(self.width()))
        h = max(1.0, float(self.height()))
        pos = float(self.comparison_state.wipe_position)
        angle_deg = float(self.comparison_state.wipe_angle_deg)
        angle_rad = math.radians(angle_deg)

        has_img = (self._img_width > 0 and self._img_height > 0)
        aspect = (float(self._img_width) * float(self.pixel_aspect_ratio)) / max(1.0, float(self._img_height)) if has_img else 1.0

        dir_x = math.cos(angle_rad)
        dir_y = math.sin(angle_rad)
        r_max = 0.5 * (aspect * abs(dir_x) + abs(dir_y))

        # Center point along divider line (pinned rotation pivot)
        wc = getattr(self.comparison_state, "wipe_center", None)
        if wc is not None:
            norm_hx, norm_hy = wc
        else:
            dist = 2.0 * r_max * (pos - 0.5)
            norm_hx = 0.5 + (dist * dir_x) / aspect
            norm_hy = 0.5 + dist * dir_y
            self.comparison_state.wipe_center = (norm_hx, norm_hy)

        # Tangent along divider line (perpendicular to normal in isotropic image coordinates)
        tang_nx = -dir_y / aspect
        tang_ny = dir_x

        if has_img:
            handle_screen = self._image_norm_to_screen(norm_hx, norm_hy)
            p_tang = self._image_norm_to_screen(norm_hx + 0.1 * tang_nx, norm_hy + 0.1 * tang_ny)
            sx_t = p_tang.x() - handle_screen.x()
            sy_t = p_tang.y() - handle_screen.y()
        else:
            handle_screen = QPointF(w * norm_hx, h * norm_hy)
            sx_t = tang_nx
            sy_t = tang_ny

        len_t = math.hypot(sx_t, sy_t)
        if len_t > 1e-6:
            tx = sx_t / len_t
            ty = sy_t / len_t
        else:
            tx = -dir_y
            ty = dir_x

        # Screen normal vector is perpendicular to screen tangent
        nx = ty
        ny = -tx

        diag = math.hypot(w, h) * 2.5
        line_pt1 = QPointF(handle_screen.x() - tx * diag, handle_screen.y() - ty * diag)
        line_pt2 = QPointF(handle_screen.x() + tx * diag, handle_screen.y() + ty * diag)

        rot_offset = 26.0
        rot_screen = QPointF(handle_screen.x() + tx * rot_offset, handle_screen.y() + ty * rot_offset)

        return handle_screen, rot_screen, line_pt1, line_pt2, QPointF(nx, ny), QPointF(tx, ty)

    def _paint_wipe_overlay(self, painter: QPainter) -> None:
        """Render interactive split wipe divider line, center handle, and rotation knob."""
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Reduce opacity by 25% while actively dragging for unobstructed view
        if self._is_wiping or self._is_rotating_wipe:
            painter.setOpacity(0.75)

        (
            handle_screen,
            rot_screen,
            line_pt1,
            line_pt2,
            norm_dir,
            tangent_dir,
        ) = self._get_wipe_geometry()

        self._wipe_handle_center = handle_screen
        self._wipe_rot_handle_center = rot_screen
        self._wipe_screen_normal = norm_dir
        self._wipe_screen_tangent = tangent_dir

        hx, hy = handle_screen.x(), handle_screen.y()
        tx, ty = tangent_dir.x(), tangent_dir.y()
        handle_rot_deg = math.degrees(math.atan2(ty, tx))

        # 1. Draw glowing divider line (thinned by 50%)
        is_line_active = (self._wipe_hover_part in ("line", "handle", "rotate") or self._is_wiping or self._is_rotating_wipe)
        pen_glow = QPen(QColor(0, 0, 0, 180), 2.5)
        painter.setPen(pen_glow)
        painter.drawLine(line_pt1, line_pt2)

        pen_color = QColor(255, 215, 64, 255) if is_line_active else QColor(229, 168, 35, 220)
        pen = QPen(pen_color, 1.25 if is_line_active else 1.0)
        painter.setPen(pen)
        painter.drawLine(line_pt1, line_pt2)

        # 2. Draw Handle and Rotation Knob in local coordinate space
        painter.save()
        painter.translate(hx, hy)
        painter.rotate(handle_rot_deg)

        # Draw connecting stem to rotation knob
        painter.setPen(QPen(QColor(229, 168, 35, 180), 1.0, Qt.PenStyle.DashLine))
        painter.drawLine(QPointF(16, 0), QPointF(19, 0))

        # Center Pill Handle (Width 32, Height 10 - reduced thickness by 50%)
        is_handle_hot = (self._wipe_hover_part == "handle" or self._is_wiping)
        handle_bg = QColor(35, 30, 20, 245) if is_handle_hot else QColor(20, 20, 20, 235)
        handle_border = QColor(255, 215, 64) if is_handle_hot else QColor(229, 168, 35)
        painter.setBrush(handle_bg)
        painter.setPen(QPen(handle_border, 1.2 if is_handle_hot else 1.0))
        painter.drawRoundedRect(QRectF(-16, -5, 32, 10), 2.5, 2.5)

        # Handle center glyph
        painter.setPen(QColor(255, 255, 255, 220))
        glyph_font = QFont("Segoe UI", 6, QFont.Weight.Bold)
        painter.setFont(glyph_font)
        painter.drawText(QRectF(-16, -5, 32, 10), Qt.AlignmentFlag.AlignCenter, "◧")

        # Circular Rotation Knob (Center at 26, 0, Radius 7)
        is_rot_hot = (self._wipe_hover_part == "rotate" or self._is_rotating_wipe)
        rot_bg = QColor(50, 40, 15, 245) if is_rot_hot else QColor(25, 25, 25, 235)
        rot_border = QColor(255, 225, 80) if is_rot_hot else QColor(229, 168, 35)
        painter.setBrush(rot_bg)
        painter.setPen(QPen(rot_border, 1.2 if is_rot_hot else 1.0))
        painter.drawEllipse(QPointF(26, 0), 7, 7)

        # Rotation glyph '↻'
        painter.setPen(QColor(255, 215, 64) if is_rot_hot else QColor(220, 220, 220))
        rot_font = QFont("Segoe UI", 6, QFont.Weight.Bold)
        painter.setFont(rot_font)
        painter.drawText(QRectF(19, -7, 14, 14), Qt.AlignmentFlag.AlignCenter, "↻")

        # Live Info Badge (when active)
        if self._is_wiping or self._is_rotating_wipe or self._wipe_hover_part:
            pos_pct = int(round(self.comparison_state.wipe_position * 100))
            angle_val = int(round(self.comparison_state.wipe_angle_deg)) % 360
            badge_text = f"{pos_pct}% | {angle_val}°"
            badge_w = 64
            badge_h = 16
            badge_rect = QRectF(-badge_w / 2, -26, badge_w, badge_h)
            painter.setBrush(QColor(15, 15, 15, 230))
            painter.setPen(QPen(QColor(229, 168, 35), 1.0))
            painter.drawRoundedRect(badge_rect, 3, 3)

            painter.setPen(QColor(229, 168, 35))
            badge_font = QFont("Segoe UI", 7, QFont.Weight.DemiBold)
            painter.setFont(badge_font)
            painter.drawText(badge_rect, Qt.AlignmentFlag.AlignCenter, badge_text)

        painter.restore()

        # Draw 'A' and 'B' side markers along divider in screen space
        pt_a = QPointF(hx - norm_dir.x() * 38.0, hy - norm_dir.y() * 38.0)
        pt_b = QPointF(hx + norm_dir.x() * 38.0, hy + norm_dir.y() * 38.0)

        marker_size = 16.0
        rect_a = QRectF(pt_a.x() - marker_size / 2.0, pt_a.y() - marker_size / 2.0, marker_size, marker_size)
        rect_b = QRectF(pt_b.x() - marker_size / 2.0, pt_b.y() - marker_size / 2.0, marker_size, marker_size)

        painter.setFont(QFont("Segoe UI", 7, QFont.Weight.Bold))

        # Marker A (Layer A)
        painter.setBrush(QColor(24, 24, 24, 230))
        painter.setPen(QPen(QColor(229, 168, 35, 230), 1.0))
        painter.drawRoundedRect(rect_a, 3.0, 3.0)
        painter.setPen(QColor(255, 220, 80))
        painter.drawText(rect_a, Qt.AlignmentFlag.AlignCenter, "A")

        # Marker B (Layer B)
        painter.setBrush(QColor(24, 24, 24, 230))
        painter.setPen(QPen(QColor(60, 160, 240, 230), 1.0))
        painter.drawRoundedRect(rect_b, 3.0, 3.0)
        painter.setPen(QColor(100, 200, 255))
        painter.drawText(rect_b, Qt.AlignmentFlag.AlignCenter, "B")

        painter.restore()

    def _paint_split_overlay(self, painter: QPainter) -> None:
        """Render side-by-side split mode 0% margin divider line and flush Layer A / Layer B header badges."""
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        has_img = (self._img_width > 0 and self._img_height > 0)
        w = max(1.0, float(self.width()))
        h = max(1.0, float(self.height()))

        if has_img:
            p_a_tl = self._image_norm_to_screen(0.0, 0.25)
            p_a_br = self._image_norm_to_screen(0.5, 0.75)
            p_b_tl = self._image_norm_to_screen(0.5, 0.25)
            p_b_br = self._image_norm_to_screen(1.0, 0.75)
            p_div_top = self._image_norm_to_screen(0.5, 0.0)
            p_div_bot = self._image_norm_to_screen(0.5, 1.0)
        else:
            p_a_tl = QPointF(0.0, h * 0.25)
            p_a_br = QPointF(w * 0.5, h * 0.75)
            p_b_tl = QPointF(w * 0.5, h * 0.25)
            p_b_br = QPointF(w, h * 0.75)
            p_div_top = QPointF(w * 0.5, 0.0)
            p_div_bot = QPointF(w * 0.5, h)

        rect_a = QRectF(p_a_tl, p_a_br).normalized()
        rect_b = QRectF(p_b_tl, p_b_br).normalized()

        # 1. Crisp vertical dividing line between Layer A and B (0% margin flush seam)
        pen_shadow = QPen(QColor(0, 0, 0, 180), 2.0)
        painter.setPen(pen_shadow)
        painter.drawLine(p_div_top, p_div_bot)
        pen_div = QPen(QColor(180, 180, 180, 220), 1.0)
        painter.setPen(pen_div)
        painter.drawLine(p_div_top, p_div_bot)

        # 2. Badges for Layer A and Layer B (flush at top with 0% margin)
        badge_w, badge_h = 70.0, 20.0
        badge_font = QFont("Segoe UI", 8, QFont.Weight.Bold)
        painter.setFont(badge_font)

        # Layer A Badge (flush top-left)
        bx_a = max(0.0, rect_a.left())
        by_a = max(0.0, rect_a.top())
        badge_rect_a = QRectF(bx_a, by_a, badge_w, badge_h)
        painter.setBrush(QColor(20, 20, 20, 225))
        painter.setPen(QPen(QColor(229, 168, 35, 230), 1.0))
        painter.drawRoundedRect(badge_rect_a, 3.0, 3.0)
        painter.setPen(QColor(255, 215, 64))
        painter.drawText(badge_rect_a, Qt.AlignmentFlag.AlignCenter, "Layer A")

        # Layer B Badge (flush top-left of right half)
        bx_b = max(rect_a.right(), rect_b.left())
        by_b = max(0.0, rect_b.top())
        badge_rect_b = QRectF(bx_b, by_b, badge_w, badge_h)
        painter.setBrush(QColor(20, 20, 20, 225))
        painter.setPen(QPen(QColor(60, 160, 240, 230), 1.0))
        painter.drawRoundedRect(badge_rect_b, 3.0, 3.0)
        painter.setPen(QColor(100, 200, 255))
        painter.drawText(badge_rect_b, Qt.AlignmentFlag.AlignCenter, "Layer B")

        painter.restore()

    def _update_wipe_from_mouse(self, sx: float, sy: float) -> None:
        """Update comparison wipe position from screen coordinate."""
        has_img = (self._img_width > 0 and self._img_height > 0)
        if has_img:
            nx, ny = self._screen_to_image_norm(sx, sy)
            aspect = (float(self._img_width) * float(self.pixel_aspect_ratio)) / max(1.0, float(self._img_height))
        else:
            w = max(1.0, float(self.width()))
            h = max(1.0, float(self.height()))
            nx, ny = sx / w, sy / h
            aspect = 1.0

        angle_rad = math.radians(float(self.comparison_state.wipe_angle_deg))
        dir_x = math.cos(angle_rad)
        dir_y = math.sin(angle_rad)

        r_max = 0.5 * (aspect * abs(dir_x) + abs(dir_y))
        p = (nx - 0.5) * aspect * dir_x + (ny - 0.5) * dir_y
        proj = (p / (2.0 * r_max) + 0.5) if r_max > 0.0001 else 0.5
        new_pos = max(0.0, min(1.0, float(proj)))
        self.comparison_state.wipe_center = (max(0.0, min(1.0, float(nx))), max(0.0, min(1.0, float(ny))))
        self.comparison_state.wipe_position = new_pos
        self.wipe_position_changed.emit(new_pos)
        self.update()

    def _start_wipe_rotation(self, sx: float, sy: float) -> None:
        hx, hy = self._wipe_handle_center.x(), self._wipe_handle_center.y()
        self._wipe_drag_start_angle = math.degrees(math.atan2(sy - hy, sx - hx))
        self._wipe_start_wipe_angle = float(self.comparison_state.wipe_angle_deg)

    def _update_wipe_rotation_from_mouse(self, sx: float, sy: float, snap: bool = False) -> None:
        hx, hy = self._wipe_handle_center.x(), self._wipe_handle_center.y()
        curr_angle = math.degrees(math.atan2(sy - hy, sx - hx))
        delta_angle = curr_angle - self._wipe_drag_start_angle
        new_angle = (self._wipe_start_wipe_angle + delta_angle) % 360.0
        if snap:
            new_angle = (round(new_angle / 15.0) * 15.0) % 360.0
        self.comparison_state.wipe_angle_deg = float(new_angle)
        self.wipe_angle_changed.emit(float(new_angle))
        self.update()

    def _set_uniform(self, name: str, val) -> None:
        if not self._shader_program:
            return
        loc = self._uniform_loc_cache.get(name)
        if loc is None:
            loc = self._shader_program.uniformLocation(name)
            self._uniform_loc_cache[name] = loc
        if loc >= 0:
            ctx = self.context()
            gl = ctx.functions() if ctx else None
            if isinstance(val, (int, bool)):
                if gl:
                    gl.glUniform1i(loc, int(val))
                else:
                    self._shader_program.setUniformValue(loc, int(val))
            elif isinstance(val, (float, np.floating)):
                if gl:
                    gl.glUniform1f(loc, float(val))
                else:
                    self._shader_program.setUniformValue(loc, float(val))
            else:
                self._shader_program.setUniformValue(loc, val)

    # --------------------------------------------------------------------------
    # Playback Engine & Texture Upload Helpers
    # --------------------------------------------------------------------------
    def set_playback_engine(self, engine: str | PlaybackEngineMode) -> None:
        """Switch active playback engine dynamically in real time."""
        if isinstance(engine, str):
            try:
                engine = PlaybackEngineMode(engine)
            except ValueError:
                engine = PlaybackEngineMode.OPENGL_CORE
        if self.playback_engine != engine:
            self.playback_engine = engine
            self._pending_upload_a = True
            self._pending_upload_b = True
            self.update()

    def set_vsync_enabled(self, enabled: bool) -> None:
        """Enable or disable VSync presentation swapping."""
        self.vsync_enabled = enabled
        fmt = self.format()
        fmt.setSwapInterval(1 if enabled else 0)
        self.setFormat(fmt)

    def is_pbo_enabled(self) -> bool:
        """Return True if asynchronous PBO texture streaming is active and supported."""
        if self.playback_engine == PlaybackEngineMode.SOFTWARE_SAFE:
            return False
        if self.playback_engine == PlaybackEngineMode.ASYNC_PBO:
            return True
        try:
            return getattr(PreferencesManager.instance().prefs, "enable_pbo_streaming", True)
        except Exception:
            return True

    def _upload_with_pbo(
        self,
        texture: Optional[QOpenGLTexture],
        arr: np.ndarray,
        w: int,
        h: int,
        tex_fmt,
        px_type,
        is_layer_b: bool = False,
    ) -> None:
        """
        Asynchronously stream texture data via double-buffered Pixel Buffer Objects (DMA).
        Orphans buffer to eliminate GPU pipeline stalls and executes non-blocking DMA transfer.
        """
        ctx = self.context()
        gl = ctx.functions() if ctx else None
        if not gl or not texture or not texture.isCreated():
            if texture and texture.isCreated():
                texture.setData(QOpenGLTexture.PixelFormat.RGBA, px_type, arr)
            return

        GL_RGBA = 0x1908
        GL_UNSIGNED_BYTE = 0x1401
        GL_FLOAT = 0x1406
        GL_HALF_FLOAT = 0x140B
        GL_TEXTURE_2D = 0x0DE1
        GL_PIXEL_UNPACK_BUFFER = 0x88EC

        gl_px_type = (
            GL_UNSIGNED_BYTE
            if px_type == QOpenGLTexture.PixelType.UInt8
            else (GL_HALF_FLOAT if px_type == QOpenGLTexture.PixelType.Float16 else GL_FLOAT)
        )

        pbo_bound = False
        pbo = None
        try:
            pbos = getattr(self, "_pbo_b" if is_layer_b else "_pbo_a", None)
            if pbos is None:
                pbos = [None, None]
                if is_layer_b:
                    self._pbo_b = pbos
                else:
                    self._pbo_a = pbos

            curr_idx = getattr(self, "_pbo_b_index" if is_layer_b else "_pbo_a_index", 0)
            nbytes = arr.nbytes
            nbytes_attr = "_pbo_b_nbytes" if is_layer_b else "_pbo_a_nbytes"
            curr_nbytes = getattr(self, nbytes_attr, 0)

            # Reallocate or create PBOs only if uninitialized or byte count changed
            if pbos[0] is None or pbos[1] is None or curr_nbytes != nbytes:
                for i in range(2):
                    if pbos[i] is not None:
                        try:
                            pbos[i].destroy()
                        except Exception:
                            pass
                    p = QOpenGLBuffer(QOpenGLBuffer.Type.PixelUnpackBuffer)
                    if p.create():
                        p.setUsagePattern(QOpenGLBuffer.UsagePattern.StreamDraw)
                        p.bind()
                        p.allocate(nbytes)
                        p.release()
                        pbos[i] = p
                    else:
                        pbos[i] = None
                setattr(self, nbytes_attr, nbytes)

            next_idx = 1 - curr_idx
            pbo = pbos[curr_idx]

            if pbo is not None and pbo.isCreated():
                pbo.bind()
                pbo_bound = True
                pbo.allocate(nbytes)
                pbo.write(0, arr, nbytes)

                gl.glBindTexture(GL_TEXTURE_2D, texture.textureId())

                dma_ok = False
                fn_sub_image = _get_gl_tex_sub_image_2d()
                if fn_sub_image is not None:
                    import ctypes
                    gl.glGetError()
                    fn_sub_image(GL_TEXTURE_2D, 0, 0, 0, w, h, GL_RGBA, gl_px_type, ctypes.c_void_p(0))
                    if gl.glGetError() == 0:
                        dma_ok = True

                pbo.release()
                pbo_bound = False

                if dma_ok:
                    if is_layer_b:
                        self._pbo_b_index = next_idx
                    else:
                        self._pbo_a_index = next_idx
                    return
        except Exception:
            pass
        finally:
            if pbo_bound and pbo is not None:
                try:
                    pbo.release()
                except Exception:
                    pass
            if gl is not None and hasattr(gl, "glBindBuffer"):
                try:
                    gl.glBindBuffer(GL_PIXEL_UNPACK_BUFFER, 0)
                except Exception:
                    pass

        # Graceful fallback to direct texture upload
        texture.setData(QOpenGLTexture.PixelFormat.RGBA, px_type, arr)

    def _upload_texture_a(self) -> None:
        if not self.isValid() or self._image_a is None:
            return

        h, w = self._image_a.shape[:2]
        if not getattr(self, "_is_proxy", False):
            self._img_width, self._img_height = w, h

        mode = PreferencesManager.instance().get_effective_texture_mode()

        if self._image_a.dtype == np.uint8:
            tex_fmt = QOpenGLTexture.TextureFormat.RGBA8_UNorm
            px_type = QOpenGLTexture.PixelType.UInt8
            arr = self._image_a
        elif mode == "rgba32f":
            tex_fmt = QOpenGLTexture.TextureFormat.RGBA32F
            px_type = QOpenGLTexture.PixelType.Float32
            arr = self._image_a if self._image_a.dtype == np.float32 else self._image_a.astype(np.float32, copy=False)
        elif mode == "rgba8":
            tex_fmt = QOpenGLTexture.TextureFormat.RGBA8_UNorm
            px_type = QOpenGLTexture.PixelType.UInt8
            arr = (np.clip(self._image_a, 0.0, 1.0) * 255.0).astype(np.uint8)
        else:
            # Default: RGBA16F (Half Float) - native GPU format for HDR/VFX, 0ms CPU conversion, half VRAM
            tex_fmt = QOpenGLTexture.TextureFormat.RGBA16F
            px_type = QOpenGLTexture.PixelType.Float16
            arr = self._image_a if self._image_a.dtype == np.float16 else self._image_a.astype(np.float16, copy=False)

        if arr.ndim == 3 and arr.shape[2] == 3:
            if arr.dtype == np.uint8:
                alpha = np.full((h, w, 1), 255, dtype=np.uint8)
            elif arr.dtype == np.float16:
                alpha = np.ones((h, w, 1), dtype=np.float16)
            else:
                alpha = np.ones((h, w, 1), dtype=np.float32)
            arr = np.concatenate([arr, alpha], axis=2)

        if not arr.flags["C_CONTIGUOUS"]:
            arr = np.ascontiguousarray(arr)

        # Fast in-place zero-copy texture update if texture matches dimensions & format
        if (
            self._texture_a is not None
            and self._texture_a.isCreated()
            and self._texture_a.width() == w
            and self._texture_a.height() == h
            and self._texture_a_fmt == tex_fmt
        ):
            if self.is_pbo_enabled():
                self._upload_with_pbo(self._texture_a, arr, w, h, tex_fmt, px_type, is_layer_b=False)
            else:
                self._texture_a.setData(QOpenGLTexture.PixelFormat.RGBA, px_type, arr)
            return

        if self._texture_a is not None:
            self._texture_a.destroy()
            self._texture_a = None

        self._texture_a = QOpenGLTexture(QOpenGLTexture.Target.Target2D)
        if not self._texture_a.create():
            self._texture_a = None
            return

        self._texture_a.setSize(w, h)
        self._texture_a.setFormat(tex_fmt)
        self._texture_a.allocateStorage(QOpenGLTexture.PixelFormat.RGBA, px_type)
        if self.is_pbo_enabled():
            self._upload_with_pbo(self._texture_a, arr, w, h, tex_fmt, px_type, is_layer_b=False)
        else:
            self._texture_a.setData(QOpenGLTexture.PixelFormat.RGBA, px_type, arr)

        gl_flt = QOpenGLTexture.Filter.Nearest if self.texture_filtering == "nearest" else QOpenGLTexture.Filter.Linear
        self._texture_a.setMinMagFilters(gl_flt, gl_flt)
        self._texture_a.setWrapMode(QOpenGLTexture.WrapMode.ClampToEdge)
        self._texture_a_fmt = tex_fmt

    def _upload_texture_b(self) -> None:
        if not self.isValid() or self._image_b is None:
            return

        h, w = self._image_b.shape[:2]

        mode = PreferencesManager.instance().get_effective_texture_mode()

        if self._image_b.dtype == np.uint8:
            tex_fmt = QOpenGLTexture.TextureFormat.RGBA8_UNorm
            px_type = QOpenGLTexture.PixelType.UInt8
            arr = self._image_b
        elif mode == "rgba32f":
            tex_fmt = QOpenGLTexture.TextureFormat.RGBA32F
            px_type = QOpenGLTexture.PixelType.Float32
            arr = self._image_b if self._image_b.dtype == np.float32 else self._image_b.astype(np.float32, copy=False)
        elif mode == "rgba8":
            tex_fmt = QOpenGLTexture.TextureFormat.RGBA8_UNorm
            px_type = QOpenGLTexture.PixelType.UInt8
            arr = (np.clip(self._image_b, 0.0, 1.0) * 255.0).astype(np.uint8)
        else:
            # Default: RGBA16F (Half Float) - native GPU format for HDR/VFX, 0ms CPU conversion, half VRAM
            tex_fmt = QOpenGLTexture.TextureFormat.RGBA16F
            px_type = QOpenGLTexture.PixelType.Float16
            arr = self._image_b if self._image_b.dtype == np.float16 else self._image_b.astype(np.float16, copy=False)

        if arr.ndim == 3 and arr.shape[2] == 3:
            if arr.dtype == np.uint8:
                alpha = np.full((h, w, 1), 255, dtype=np.uint8)
            elif arr.dtype == np.float16:
                alpha = np.ones((h, w, 1), dtype=np.float16)
            else:
                alpha = np.ones((h, w, 1), dtype=np.float32)
            arr = np.concatenate([arr, alpha], axis=2)

        if not arr.flags["C_CONTIGUOUS"]:
            arr = np.ascontiguousarray(arr)

        if (
            self._texture_b is not None
            and self._texture_b.isCreated()
            and self._texture_b.width() == w
            and self._texture_b.height() == h
            and self._texture_b_fmt == tex_fmt
        ):
            if self.is_pbo_enabled():
                self._upload_with_pbo(self._texture_b, arr, w, h, tex_fmt, px_type, is_layer_b=True)
            else:
                self._texture_b.setData(QOpenGLTexture.PixelFormat.RGBA, px_type, arr)
            return

        if self._texture_b is not None:
            self._texture_b.destroy()
            self._texture_b = None

        self._texture_b = QOpenGLTexture(QOpenGLTexture.Target.Target2D)
        if not self._texture_b.create():
            self._texture_b = None
            return

        self._texture_b.setSize(w, h)
        self._texture_b.setFormat(tex_fmt)
        self._texture_b.allocateStorage(QOpenGLTexture.PixelFormat.RGBA, px_type)
        if self.is_pbo_enabled():
            self._upload_with_pbo(self._texture_b, arr, w, h, tex_fmt, px_type, is_layer_b=True)
        else:
            self._texture_b.setData(QOpenGLTexture.PixelFormat.RGBA, px_type, arr)

        gl_flt = QOpenGLTexture.Filter.Nearest if self.texture_filtering == "nearest" else QOpenGLTexture.Filter.Linear
        self._texture_b.setMinMagFilters(gl_flt, gl_flt)
        self._texture_b.setWrapMode(QOpenGLTexture.WrapMode.ClampToEdge)
        self._texture_b_fmt = tex_fmt


    def _upload_lut_texture(self) -> None:
        if not self.isValid():
            return
        if self._lut_texture is not None:
            self._lut_texture.destroy()
            self._lut_texture = None

        lut = self.color_grading.active_lut
        if lut is None or lut.data_3d is None or lut.size_3d <= 0:
            return

        sz = int(lut.size_3d)
        self._lut_texture = QOpenGLTexture(QOpenGLTexture.Target.Target3D)
        if not self._lut_texture.create():
            self._lut_texture = None
            return

        self._lut_texture.setSize(sz, sz, sz)
        self._lut_texture.setFormat(QOpenGLTexture.TextureFormat.RGBA32F)
        self._lut_texture.allocateStorage(QOpenGLTexture.PixelFormat.RGBA, QOpenGLTexture.PixelType.Float32)

        arr_3d = lut.data_3d
        rgba = np.ones((sz, sz, sz, 4), dtype=np.float32)
        rgba[..., :3] = arr_3d
        arr_contiguous = np.ascontiguousarray(rgba, dtype=np.float32)
        self._lut_texture.setData(QOpenGLTexture.PixelFormat.RGBA, QOpenGLTexture.PixelType.Float32, arr_contiguous)
        self._lut_texture.setMinMagFilters(QOpenGLTexture.Filter.Linear, QOpenGLTexture.Filter.Linear)
        self._lut_texture.setWrapMode(QOpenGLTexture.WrapMode.ClampToEdge)

    def _upload_ocio_lut_texture(self) -> None:
        if not self.isValid():
            return
        if self._ocio_lut_texture is not None:
            self._ocio_lut_texture.destroy()
            self._ocio_lut_texture = None

        bake = self._active_ocio_lut_bake
        if bake is None or getattr(bake, "data_3d", None) is None or getattr(bake, "lut_size", 0) <= 0:
            return

        sz = int(bake.lut_size)
        self._ocio_lut_texture = QOpenGLTexture(QOpenGLTexture.Target.Target3D)
        if not self._ocio_lut_texture.create():
            self._ocio_lut_texture = None
            return

        self._ocio_lut_texture.setSize(sz, sz, sz)
        self._ocio_lut_texture.setFormat(QOpenGLTexture.TextureFormat.RGBA32F)
        self._ocio_lut_texture.allocateStorage(QOpenGLTexture.PixelFormat.RGBA, QOpenGLTexture.PixelType.Float32)

        arr = bake.data_3d
        if arr.shape[-1] == 3:
            rgba = np.ones((sz, sz, sz, 4), dtype=np.float32)
            rgba[..., :3] = arr
        else:
            rgba = arr
        arr_contiguous = np.ascontiguousarray(rgba, dtype=np.float32)
        self._ocio_lut_texture.setData(QOpenGLTexture.PixelFormat.RGBA, QOpenGLTexture.PixelType.Float32, arr_contiguous)
        self._ocio_lut_texture.setMinMagFilters(QOpenGLTexture.Filter.Linear, QOpenGLTexture.Filter.Linear)
        self._ocio_lut_texture.setWrapMode(QOpenGLTexture.WrapMode.ClampToEdge)

    # --------------------------------------------------------------------------
    # Media & Frame Setting
    # --------------------------------------------------------------------------
    def set_primary_frame(
        self,
        frame_number: int,
        image_data: Optional[np.ndarray],
        logical_size: Optional[Tuple[int, int]] = None,
        is_proxy: bool = False,
    ) -> None:
        self._is_proxy = bool(is_proxy)
        if frame_number != self.current_frame and hasattr(self, "_text_editor") and self._text_editor.is_active():
            self._text_editor.commit()
        self.current_frame = frame_number
        if isinstance(image_data, (tuple, list)):
            if len(image_data) >= 2 and isinstance(image_data[1], np.ndarray):
                self._image_b = image_data[1]
                self._pending_upload_b = True
            image_data = next((x for x in image_data if isinstance(x, np.ndarray)), None)
        self._image_a = image_data
        if image_data is not None and hasattr(image_data, "shape") and len(image_data.shape) >= 2:
            self.has_active_media = True
            self.is_bad_frame = False
            if logical_size is not None and logical_size[0] > 0 and logical_size[1] > 0:
                new_w, new_h = int(logical_size[0]), int(logical_size[1])
            elif is_proxy and self._img_width > 0 and self._img_height > 0:
                new_w, new_h = self._img_width, self._img_height
            else:
                new_w = int(image_data.shape[1])
                new_h = int(image_data.shape[0])

            if new_w != self._img_width or new_h != self._img_height:
                self._img_width = new_w
                self._img_height = new_h
                self.resolution_changed.emit(self._img_width, self._img_height)
        else:
            if self.has_active_media:
                self.is_bad_frame = True
            self._img_width = 0
            self._img_height = 0
            if self._texture_a is not None:
                self._texture_a.destroy()
                self._texture_a = None
        self._pending_upload_a = True if image_data is not None else False
        if image_data is not None:
            self.sample_at_screen_pos(float(self._current_mouse_pos.x()), float(self._current_mouse_pos.y()))
            if self._color_samplers:
                self._refresh_all_samplers()
        self.update()

    def set_media_resolution(self, w: int, h: int) -> None:
        """Update viewport media resolution."""
        if w > 0 and h > 0:
            if w != self._img_width or h != self._img_height:
                self._img_width = int(w)
                self._img_height = int(h)
                self.resolution_changed.emit(self._img_width, self._img_height)
                self.update()

    def clear_canvas(self) -> None:
        """Clear all active textures, images, and reset canvas to empty state."""
        self._image_a = None
        self._image_b = None
        self._img_width = 0
        self._img_height = 0
        self.has_active_media = False
        self.is_bad_frame = False
        if self._texture_a is not None:
            self._texture_a.destroy()
            self._texture_a = None
        if self._texture_b is not None:
            self._texture_b.destroy()
            self._texture_b = None
        self._texture_a_fmt = None
        self._texture_b_fmt = None
        for pbos in (getattr(self, "_pbo_a", []), getattr(self, "_pbo_b", [])):
            for i in range(len(pbos)):
                if pbos[i] is not None:
                    try:
                        pbos[i].destroy()
                    except Exception:
                        pass
                    pbos[i] = None
        self._pbo_a_index = 0
        self._pbo_b_index = 0
        self._pbo_a_nbytes = 0
        self._pbo_b_nbytes = 0
        self._pending_upload_a = False
        self._pending_upload_b = False
        self.reset_transforms()
        self.update()

    def set_reference_frame(self, image_data: Optional[np.ndarray]) -> None:
        if isinstance(image_data, (tuple, list)):
            image_data = next((x for x in image_data if isinstance(x, np.ndarray)), None)
        self._image_b = image_data
        self._pending_upload_b = True if image_data is not None else False
        self.update()

    def set_matte_frame(self, image_data: Optional[np.ndarray]) -> None:
        self._image_matte = image_data
        self._pending_upload_matte = True if image_data is not None else False
        if image_data is None and self._texture_matte is not None:
            self._texture_matte.destroy()
            self._texture_matte = None
        self.update()

    def _upload_texture_matte(self) -> None:
        if not self.isValid() or self._image_matte is None:
            return

        arr = self._image_matte
        h, w = arr.shape[:2]
        if arr.dtype == np.uint8:
            tex_fmt = QOpenGLTexture.TextureFormat.RGBA8_UNorm
            px_type = QOpenGLTexture.PixelType.UInt8
        else:
            tex_fmt = QOpenGLTexture.TextureFormat.RGBA16F
            px_type = QOpenGLTexture.PixelType.Float16
            arr = arr if arr.dtype == np.float16 else arr.astype(np.float16, copy=False)

        # Ensure array is 4-channel RGBA
        if arr.ndim == 2:
            rgba = np.empty((h, w, 4), dtype=arr.dtype)
            rgba[..., :3] = arr[..., None]
            rgba[..., 3] = arr
            arr = rgba
        elif arr.ndim == 3:
            if arr.shape[2] == 1:
                rgba = np.empty((h, w, 4), dtype=arr.dtype)
                rgba[..., :3] = arr
                rgba[..., 3] = arr[..., 0]
                arr = rgba
            elif arr.shape[2] == 3:
                rgba = np.empty((h, w, 4), dtype=arr.dtype)
                rgba[..., :3] = arr
                rgba[..., 3] = 1.0 if arr.dtype != np.uint8 else 255
                arr = rgba
            elif arr.shape[2] > 4:
                arr = arr[..., :4]

        if not arr.flags["C_CONTIGUOUS"]:
            arr = np.ascontiguousarray(arr)

        if (
            self._texture_matte is not None
            and self._texture_matte.isCreated()
            and self._texture_matte.width() == w
            and self._texture_matte.height() == h
            and getattr(self, "_texture_matte_fmt", None) == tex_fmt
        ):
            self._texture_matte.setData(QOpenGLTexture.PixelFormat.RGBA, px_type, arr)
            return

        if self._texture_matte is not None:
            self._texture_matte.destroy()
            self._texture_matte = None

        self._texture_matte = QOpenGLTexture(QOpenGLTexture.Target.Target2D)
        if not self._texture_matte.create():
            self._texture_matte = None
            return

        self._texture_matte.setSize(w, h)
        self._texture_matte.setFormat(tex_fmt)
        self._texture_matte.allocateStorage(QOpenGLTexture.PixelFormat.RGBA, px_type)
        self._texture_matte.setData(QOpenGLTexture.PixelFormat.RGBA, px_type, arr)
        self._texture_matte.setMinMagFilters(QOpenGLTexture.Filter.Linear, QOpenGLTexture.Filter.Linear)
        self._texture_matte.setWrapMode(QOpenGLTexture.WrapMode.ClampToEdge)
        self._texture_matte_fmt = tex_fmt

    @property
    def image_width(self) -> int:
        return self._img_width

    @property
    def image_height(self) -> int:
        return self._img_height

    # --------------------------------------------------------------------------
    # Navigation Math (Zoom & Pan)
    # --------------------------------------------------------------------------
    def fit_image(self) -> None:
        """Reset pan and zoom to fit image within viewport."""
        self._zoom = 1.0
        self._pan_x = 0.0
        self._pan_y = 0.0
        self.zoom_changed.emit(self._zoom)
        self.update()

    def _effective_image_dimensions(self) -> Tuple[float, float]:
        """Compute effective image width and height, accounting for pixel aspect ratio and Compare Plates side-by-side."""
        img_w = float(self._img_width) * float(self.pixel_aspect_ratio)
        img_h = float(self._img_height)
        qc = getattr(self, "comp_qc_state", None)
        if qc is not None and getattr(qc, "compare_plates", False) and self._texture_b is not None:
            img_w *= 2.0
        return img_w, img_h

    def update_matte_expressions(self, expr_r: str, expr_g: str, expr_b: str, expr_a: str) -> None:
        """Recompile fragment shader with updated channel expressions."""
        new_src = build_fragment_shader(expr_r, expr_g, expr_b, expr_a)
        if getattr(self, "_current_frag_src", None) == new_src:
            return
        self._current_frag_src = new_src
        if not self.isValid():
            return
        self.makeCurrent()
        new_prog = QOpenGLShaderProgram(self)
        if not new_prog.addShaderFromSourceCode(QOpenGLShader.ShaderType.Vertex, VERTEX_SHADER_SOURCE):
            return
        if not new_prog.addShaderFromSourceCode(QOpenGLShader.ShaderType.Fragment, new_src):
            return
        if not new_prog.link():
            return
        self._uniform_loc_cache.clear()
        if self._shader_program is not None:
            self._shader_program.deleteLater()
        self._shader_program = new_prog
        self.update()

    def set_zoom_100(self) -> None:
        """Set zoom so 1 image pixel = 1 screen pixel."""
        viewport_w = max(1.0, float(self.width()))
        viewport_h = max(1.0, float(self.height()))
        img_w, img_h = self._effective_image_dimensions()
        is_rot_90 = (self.rotation % 180 != 0)
        disp_w = img_h if is_rot_90 else img_w
        disp_h = img_w if is_rot_90 else img_h

        fit_scale = min(viewport_w / max(1.0, disp_w), viewport_h / max(1.0, disp_h))
        self._zoom = 1.0 / max(1e-6, fit_scale)
        self._pan_x = 0.0
        self._pan_y = 0.0
        self.zoom_changed.emit(self._zoom)
        self.update()

    def set_scale_preset(self, factor: float) -> None:
        """Set zoom to a multiplier of 1:1 pixel scale (e.g. 1.0, 2.0, 4.0, 8.0)."""
        viewport_w = max(1.0, float(self.width()))
        viewport_h = max(1.0, float(self.height()))
        img_w, img_h = self._effective_image_dimensions()
        is_rot_90 = (self.rotation % 180 != 0)
        disp_w = img_h if is_rot_90 else img_w
        disp_h = img_w if is_rot_90 else img_h

        fit_scale = min(viewport_w / max(1.0, disp_w), viewport_h / max(1.0, disp_h))
        self._zoom = (1.0 / max(1e-6, fit_scale)) * float(factor)
        self._pan_x = 0.0
        self._pan_y = 0.0
        self.zoom_changed.emit(self._zoom)
        self.update()

    def fit_width(self) -> None:
        """Fit image width to viewport width (Alt+F)."""
        viewport_w = max(1.0, float(self.width()))
        viewport_h = max(1.0, float(self.height()))
        img_w, img_h = self._effective_image_dimensions()
        is_rot_90 = (self.rotation % 180 != 0)
        disp_w = img_h if is_rot_90 else img_w
        disp_h = img_w if is_rot_90 else img_h

        fit_scale = min(viewport_w / max(1.0, disp_w), viewport_h / max(1.0, disp_h))
        target_scale = viewport_w / max(1.0, disp_w)
        self._zoom = target_scale / max(1e-6, fit_scale)
        self._pan_x = 0.0
        self._pan_y = 0.0
        self.zoom_changed.emit(self._zoom)
        self.update()

    @property
    def zoom(self) -> float:
        return self._zoom

    @property
    def pan_x(self) -> float:
        return self._pan_x

    @property
    def pan_y(self) -> float:
        return self._pan_y

    def set_zoom(self, zoom: float) -> None:
        self._zoom = max(0.01, min(100.0, float(zoom)))
        self.zoom_changed.emit(self._zoom)
        self.update()

    def set_pan(self, x: float, y: float) -> None:
        self._pan_x = float(x)
        self._pan_y = float(y)
        self.update()

    def reset_transforms(self) -> None:
        """Reset all zoom, pan, flip, rotation, and aspect ratio transforms to default."""
        self._zoom = 1.0
        self._pan_x = 0.0
        self._pan_y = 0.0
        self.flip_x = False
        self.flip_y = False
        self.rotation = 0
        self.pixel_aspect_ratio = 1.0
        self.downsample_ratio = 1.0
        self.texture_filtering = "linear"
        self.zoom_changed.emit(self._zoom)
        self.update()

    # --------------------------------------------------------------------------
    # Geometry Transforms & Mattes API
    # --------------------------------------------------------------------------
    def set_flip_x(self, val: bool) -> None:
        self.flip_x = bool(val)
        self.update()

    def set_flip_y(self, val: bool) -> None:
        self.flip_y = bool(val)
        self.update()

    def toggle_flop_x(self) -> None:
        self.flip_x = not self.flip_x
        self.update()

    def toggle_flip_y(self) -> None:
        self.flip_y = not self.flip_y
        self.update()

    def set_rotation(self, degrees: int) -> None:
        self.rotation = int(degrees) % 360
        self.update()

    def set_pixel_aspect_ratio(self, par: float) -> None:
        self.pixel_aspect_ratio = max(0.1, float(par))
        self.update()

    def set_downsample_ratio(self, ratio: float) -> None:
        self.downsample_ratio = max(0.1, min(1.0, float(ratio)))
        self.update()

    def set_texture_filtering(self, mode: str | int) -> None:
        if isinstance(mode, int):
            self.texture_filtering = "nearest" if mode == 1 else "linear"
        else:
            self.texture_filtering = str(mode).lower()

        gl_flt = QOpenGLTexture.Filter.Nearest if self.texture_filtering == "nearest" else QOpenGLTexture.Filter.Linear
        if self._texture_a:
            self._texture_a.setMinMagFilters(gl_flt, gl_flt)
        if self._texture_b:
            self._texture_b.setMinMagFilters(gl_flt, gl_flt)
        self.update()

    def set_premultiplied_alpha(self, val: bool) -> None:
        """Set whether active media alpha is premultiplied (GL_ONE) or straight (GL_SRC_ALPHA)."""
        self.premultiplied_alpha = bool(val)
        self.update()

    def toggle_texture_filtering(self) -> None:
        """Toggle texture filtering between Linear and Nearest (Shift+N)."""
        new_mode = "nearest" if self.texture_filtering == "linear" else "linear"
        self.set_texture_filtering(new_mode)

    def set_matte(self, ratio: Optional[float], opacity: Optional[float] = None) -> None:
        self.matte_ratio = float(ratio) if ratio is not None else None
        if opacity is not None:
            self.matte_opacity = float(opacity)
        self.update()

    def set_matte_opacity(self, opacity: float) -> None:
        self.matte_opacity = max(0.0, min(1.0, float(opacity)))
        self.update()

    def set_background_mode(self, mode: str) -> None:
        self.background_mode = str(mode).lower()
        self.update()

    def set_presentation_mode(self, enabled: bool) -> None:
        self.is_presentation_mode = bool(enabled)

    def set_scrub_mode(self, enabled: bool) -> None:
        """Toggle left-mouse scrubbing across frames on the canvas."""
        self.scrub_mode_enabled = bool(enabled)
        if not self.scrub_mode_enabled:
            self._is_scrubbing = False

    def is_scrub_mode_active(self) -> bool:
        return self.scrub_mode_enabled

    def render_frame_to_image(self, native_res: bool = True) -> np.ndarray:
        """
        Capture the current viewport or native frame as an RGB numpy array.
        If native_res=True and native media is loaded, renders annotations directly
        at native source resolution, independent of canvas size and viewport zoom/pan.
        """
        if native_res and self._image_a is not None:
            return self.render_annotated_frame_native(self.current_frame)

        qimg = self.grabFramebuffer()
        qimg = qimg.convertToFormat(QImage.Format.Format_RGB888)
        w, h = qimg.width(), qimg.height()
        ptr = qimg.constBits()
        arr = np.frombuffer(ptr, dtype=np.uint8).reshape((h, w, 3)).copy()
        return arr

    def render_annotated_frame_native(self, frame_num: Optional[int] = None) -> np.ndarray:
        """Render annotations directly onto active native resolution frame buffer."""
        from openreview.core.exporter import MediaExporter
        target_f = frame_num if frame_num is not None else self.current_frame
        strokes = self.annotation_store.get_strokes(target_f)
        if isinstance(self._image_a, (tuple, list)):
            self._image_a = next((x for x in self._image_a if isinstance(x, np.ndarray)), None)
        if self._image_a is None:
            return np.zeros((1080, 1920, 3), dtype=np.uint8)

        if self._image_a.dtype != np.uint8:
            exp_mult = 2.0 ** self.exposure_ev
            gain = getattr(self, "gain", 1.0)
            gamma = getattr(self, "gamma", 1.0)
            processed = self._image_a[:, :, :3] * exp_mult * gain
            if gamma != 1.0 and gamma > 0:
                processed = np.power(np.maximum(processed, 0.0), 1.0 / gamma)
            rgb_u8 = np.clip(processed * 255.0, 0, 255).astype(np.uint8)
        else:
            rgb_u8 = self._image_a[:, :, :3].copy()

        return MediaExporter.render_strokes_on_image(rgb_u8, strokes)

    def _screen_to_image_coords(self, screen_x: float, screen_y: float) -> Tuple[int, int]:
        """Convert screen pixel coordinates to underlying image pixel coordinates."""
        nx, ny = self._screen_to_image_norm(screen_x, screen_y)
        ix = int(round(nx * self._img_width))
        iy = int(round(ny * self._img_height))
        return ix, iy

    def _screen_to_image_norm(self, screen_x: float, screen_y: float) -> Tuple[float, float]:
        """Convert screen pixel coordinates to normalized (0.0 to 1.0) image coordinates."""
        vw = max(1.0, float(self.width()))
        vh = max(1.0, float(self.height()))
        img_w, img_h = self._effective_image_dimensions()
        is_rot_90 = (self.rotation % 180 != 0)
        disp_w = img_h if is_rot_90 else img_w
        disp_h = img_w if is_rot_90 else img_h

        fit_scale = min(vw / max(1.0, disp_w), vh / max(1.0, disp_h))
        w_px = max(1e-6, (img_w / 2.0) * fit_scale * self._zoom)
        h_px = max(1e-6, (img_h / 2.0) * fit_scale * self._zoom)

        rx = screen_x - ((vw / 2.0) + self._pan_x)
        ry = -(screen_y - ((vh / 2.0) + self._pan_y))

        rad = math.radians(-float(self.rotation))
        cos_a = math.cos(rad)
        sin_a = math.sin(rad)
        lx = rx * cos_a - ry * sin_a
        ly = rx * sin_a + ry * cos_a

        p_x = (lx / w_px) * (-1.0 if self.flip_x else 1.0)
        p_y = (ly / h_px) * (-1.0 if self.flip_y else 1.0)

        norm_x = (p_x + 1.0) / 2.0
        norm_y = (1.0 - p_y) / 2.0
        return norm_x, norm_y

    def _image_norm_to_screen(self, nx: float, ny: float) -> QPointF:
        vw = max(1.0, float(self.width()))
        vh = max(1.0, float(self.height()))
        img_w, img_h = self._effective_image_dimensions()
        is_rot_90 = (self.rotation % 180 != 0)
        disp_w = img_h if is_rot_90 else img_w
        disp_h = img_w if is_rot_90 else img_h

        fit_scale = min(vw / max(1.0, disp_w), vh / max(1.0, disp_h))
        w_px = (img_w / 2.0) * fit_scale * self._zoom
        h_px = (img_h / 2.0) * fit_scale * self._zoom

        # Local quad coordinates in [-1, 1]
        lx = (nx * 2.0 - 1.0) * (-1.0 if self.flip_x else 1.0) * w_px
        ly = (1.0 - ny * 2.0) * (-1.0 if self.flip_y else 1.0) * h_px

        rad = math.radians(float(self.rotation))
        cos_a = math.cos(rad)
        sin_a = math.sin(rad)
        rx = lx * cos_a - ly * sin_a
        ry = lx * sin_a + ly * cos_a

        sx = (vw / 2.0) + rx + self._pan_x
        sy = (vh / 2.0) - ry + self._pan_y
        return QPointF(sx, sy)

    # --------------------------------------------------------------------------
    # Mouse & Keyboard Event Handlers
    # --------------------------------------------------------------------------
    def keyPressEvent(self, event: QKeyEvent) -> None:
        """Handle viewport keyboard events, direct text annotation typing, and delegation."""
        # If in-canvas text editor is active
        if hasattr(self, "_text_editor") and self._text_editor.is_active():
            if event.key() == Qt.Key.Key_Escape:
                self.cancel_text_editor()
                event.accept()
                return
            event.accept()
            return

        # If Text annotation tool is currently selected
        if self._active_draw_tool == ShapeType.TEXT:
            key = event.key()
            if key == Qt.Key.Key_Escape:
                # Deselect Text tool
                self.set_draw_tool(None)
                self.draw_tool_changed.emit(None)
                event.accept()
                return

            # Direct typing mode: any printable character or Space immediately spawns editor
            text = event.text()
            if text and (text.isprintable() or key == Qt.Key.Key_Space):
                spawn_pos = self._current_mouse_pos
                if not self.rect().contains(spawn_pos):
                    spawn_pos = QPoint(max(40, self.width() // 4), max(40, self.height() // 4))
                self.spawn_text_editor(spawn_pos, initial_text=text)
                event.accept()
                return

            # Suppress all shortcuts while in Text tool mode
            event.accept()
            return

        parent_win = self.window()
        if parent_win and hasattr(parent_win, "keyPressEvent") and parent_win is not self:
            parent_win.keyPressEvent(event)
            if event.isAccepted():
                return
        super().keyPressEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:
        if (
            event.button() == Qt.MouseButton.LeftButton
            and self.comparison_state.mode == ComparisonMode.WIPE
        ):
            pos = event.position()
            mx, my = pos.x(), pos.y()
            hx, hy = self._wipe_handle_center.x(), self._wipe_handle_center.y()
            rx, ry = self._wipe_rot_handle_center.x(), self._wipe_rot_handle_center.y()
            dist_handle = math.hypot(mx - hx, my - hy)
            dist_rot = math.hypot(mx - rx, my - ry)
            nx = self._wipe_screen_normal.x()
            ny = self._wipe_screen_normal.y()
            dist_line = abs((mx - hx) * nx + (my - hy) * ny)

            if dist_handle <= 35.0 or dist_rot <= 20.0 or dist_line <= 12.0:
                self.comparison_state.reset_wipe()
                self.wipe_position_changed.emit(self.comparison_state.wipe_position)
                self.wipe_angle_changed.emit(self.comparison_state.wipe_angle_deg)
                self.update()
                event.accept()
                return

        # Multi-Channel EXR Contact Sheet Tile Soloing
        if (
            event.button() == Qt.MouseButton.LeftButton
            and getattr(self, "_contact_sheet_mode", False)
            and getattr(self, "_contact_sheet_tile_map", None)
        ):
            pos = event.position()
            ix, iy = self._screen_to_image_coords(pos.x(), pos.y())
            from openreview.core.exr_contact_sheet import hit_test_contact_sheet
            hit_layer = hit_test_contact_sheet(ix, iy, self._contact_sheet_tile_map)
            if hit_layer:
                self.contact_sheet_tile_double_clicked.emit(hit_layer)
                event.accept()
                return

        super().mouseDoubleClickEvent(event)

    # --------------------------------------------------------------------------
    # Color Inspector & Multi-Sampler Eyedropper API
    # --------------------------------------------------------------------------
    def set_inspector_aperture(self, aperture: int) -> None:
        """Set live cursor and new point-sampler box aperture (1, 3, 5)."""
        self._inspector_aperture = max(1, int(aperture))
        self.sample_at_screen_pos(float(self._current_mouse_pos.x()), float(self._current_mouse_pos.y()))

    def set_eyedropper_mode(self, enabled: bool) -> None:
        """Toggle Eyedropper sampler placement mode on the viewport."""
        new_mode = bool(enabled)
        if self._eyedropper_mode != new_mode:
            self._eyedropper_mode = new_mode
            if new_mode:
                self.setCursor(Qt.CursorShape.CrossCursor)
            elif self._active_draw_tool is None:
                self.setCursor(Qt.CursorShape.ArrowCursor)
            self.eyedropper_mode_changed.emit(self._eyedropper_mode)
            self.update()

    def is_eyedropper_mode_active(self) -> bool:
        return self._eyedropper_mode

    def get_color_samplers(self) -> list[ColorSampler]:
        return list(self._color_samplers)

    def set_active_analyzer_sampler(self, sampler_id: int) -> None:
        """Set which pinned ColorSampler is currently focused in the Pixel Analyzer."""
        self._active_analyzer_sampler_id = int(sampler_id) if sampler_id > 0 else None
        self.update()

    def set_pixel_analyzer_checks(self, check_nan: bool, check_inf: bool, check_neg: bool) -> None:
        """Toggle viewport diagnostic overlays for NaN, Inf, and Negative (<0) pixels."""
        self.highlight_nan_pixels = bool(check_nan)
        self.highlight_inf_pixels = bool(check_inf)
        self.highlight_neg_pixels = bool(check_neg)
        self.update()

    def _sample_image_region(
        self, ix: int, iy: int, w: int = 1, h: int = 1, aperture: int = 1
    ) -> Tuple[float, float, float, float]:
        """Compute finite-safe average RGBA over a pixel aperture (1x1, 3x3, 5x5) or custom WxH rectangle."""
        if self._image_a is None or self._img_width <= 0 or self._img_height <= 0:
            return (0.0, 0.0, 0.0, 1.0)

        actual_h, actual_w = self._image_a.shape[:2]
        scale_x = actual_w / float(self._img_width)
        scale_y = actual_h / float(self._img_height)
        eff_ix = int(round(ix * scale_x))
        eff_iy = int(round(iy * scale_y))
        eff_w = max(1, int(round(w * scale_x)))
        eff_h = max(1, int(round(h * scale_y)))

        scale = 255.0 if self._image_a.dtype == np.uint8 else 1.0
        if eff_w > 1 or eff_h > 1:
            x0 = max(0, min(actual_w - 1, eff_ix))
            y0 = max(0, min(actual_h - 1, eff_iy))
            x1 = max(x0 + 1, min(actual_w, eff_ix + eff_w))
            y1 = max(y0 + 1, min(actual_h, eff_iy + eff_h))
        else:
            half = max(0, int(aperture) // 2)
            cx = max(0, min(actual_w - 1, eff_ix))
            cy = max(0, min(actual_h - 1, eff_iy))
            x0 = max(0, cx - half)
            y0 = max(0, cy - half)
            x1 = min(actual_w, cx + half + 1)
            y1 = min(actual_h, cy + half + 1)

        patch = self._image_a[y0:y1, x0:x1]
        if patch.size == 0:
            return (0.0, 0.0, 0.0, 1.0)

        arr = patch.astype(np.float32) / scale
        finite_arr = np.where(np.isfinite(arr), arr, np.nan)
        with np.errstate(all="ignore"):
            mean_vals = np.nanmean(finite_arr.reshape(-1, finite_arr.shape[-1]), axis=0)
        mean_vals = np.nan_to_num(mean_vals, nan=0.0, posinf=0.0, neginf=0.0)
        r = float(mean_vals[0])
        g = float(mean_vals[1]) if len(mean_vals) > 1 else r
        b = float(mean_vals[2]) if len(mean_vals) > 2 else r
        a = float(mean_vals[3]) if len(mean_vals) > 3 else 1.0
        return (r, g, b, a)

    def _evaluate_sampler(self, sampler: ColorSampler) -> None:
        """Compute pixel coordinates, Min/Max/Avg/Median RGBA, and NaN/Inf/Negative diagnostics for a ColorSampler."""
        img_w = max(1, self._img_width)
        img_h = max(1, self._img_height)
        if sampler.norm_w > 0.0 or sampler.norm_h > 0.0:
            x0 = int(round((sampler.norm_x - sampler.norm_w / 2.0) * img_w))
            y0 = int(round((sampler.norm_y - sampler.norm_h / 2.0) * img_h))
            w_px = max(2, int(round(sampler.norm_w * img_w)))
            h_px = max(2, int(round(sampler.norm_h * img_h)))
            sampler.x = max(0, min(img_w - 1, x0))
            sampler.y = max(0, min(img_h - 1, y0))
            sampler.w = w_px
            sampler.h = h_px
            bx0 = sampler.x
            by0 = sampler.y
            bx1 = max(bx0 + 1, min(img_w, bx0 + w_px))
            by1 = max(by0 + 1, min(img_h, by0 + h_px))
        else:
            ix = int(round(sampler.norm_x * img_w))
            iy = int(round(sampler.norm_y * img_h))
            sampler.x = max(0, min(img_w - 1, ix)) if self._image_a is not None else ix
            sampler.y = max(0, min(img_h - 1, iy)) if self._image_a is not None else iy
            sampler.w = 1
            sampler.h = 1
            half = max(0, int(sampler.aperture) // 2)
            bx0 = max(0, sampler.x - half)
            by0 = max(0, sampler.y - half)
            bx1 = min(img_w, sampler.x + half + 1)
            by1 = min(img_h, sampler.y + half + 1)

        if self._image_a is None or self._img_width <= 0 or self._img_height <= 0:
            sampler.r, sampler.g, sampler.b, sampler.a = (0.0, 0.0, 0.0, 1.0)
            sampler.min_rgba = (0.0, 0.0, 0.0, 1.0)
            sampler.max_rgba = (0.0, 0.0, 0.0, 1.0)
            sampler.avg_rgba = (0.0, 0.0, 0.0, 1.0)
            sampler.med_rgba = (0.0, 0.0, 0.0, 1.0)
            sampler.nan_count = 0
            sampler.inf_count = 0
            sampler.neg_count = 0
            sampler.nan_coords.clear()
            sampler.inf_coords.clear()
            sampler.neg_coords.clear()
            return

        patch = self._image_a[by0:by1, bx0:bx1]
        if patch.size == 0:
            return

        scale = 255.0 if self._image_a.dtype == np.uint8 else 1.0
        arr = patch.astype(np.float32) / scale
        if arr.ndim == 2:
            arr = arr[..., np.newaxis]

        # 1. Detect NaN, Inf, and Negative (< 0.0) pixels
        nan_mask = np.isnan(arr).any(axis=-1)
        inf_mask = np.isinf(arr).any(axis=-1)
        rgb_slice = arr[..., :3] if arr.shape[-1] >= 3 else arr
        neg_mask = (np.where(np.isfinite(rgb_slice), rgb_slice, 0.0) < 0.0).any(axis=-1)

        sampler.nan_count = int(np.count_nonzero(nan_mask))
        sampler.inf_count = int(np.count_nonzero(inf_mask))
        sampler.neg_count = int(np.count_nonzero(neg_mask))

        max_markers = 200
        if sampler.nan_count > 0:
            ys, xs = np.nonzero(nan_mask)
            sampler.nan_coords = [(int(bx0 + x), int(by0 + y)) for y, x in zip(ys[:max_markers], xs[:max_markers])]
        else:
            sampler.nan_coords = []

        if sampler.inf_count > 0:
            ys, xs = np.nonzero(inf_mask)
            sampler.inf_coords = [(int(bx0 + x), int(by0 + y)) for y, x in zip(ys[:max_markers], xs[:max_markers])]
        else:
            sampler.inf_coords = []

        if sampler.neg_count > 0:
            ys, xs = np.nonzero(neg_mask)
            sampler.neg_coords = [(int(bx0 + x), int(by0 + y)) for y, x in zip(ys[:max_markers], xs[:max_markers])]
        else:
            sampler.neg_coords = []

        # 2. Compute Finite-Safe Min, Max, Average, and Median per channel
        flat = np.where(np.isfinite(arr), arr, np.nan).reshape(-1, arr.shape[-1])
        with np.errstate(all="ignore"):
            c_min = np.nan_to_num(np.nanmin(flat, axis=0), nan=0.0)
            c_max = np.nan_to_num(np.nanmax(flat, axis=0), nan=0.0)
            c_avg = np.nan_to_num(np.nanmean(flat, axis=0), nan=0.0)
            c_med = np.nan_to_num(np.nanmedian(flat, axis=0), nan=0.0)

        def _to_rgba(vec: np.ndarray) -> Tuple[float, float, float, float]:
            r_v = float(vec[0])
            g_v = float(vec[1]) if len(vec) > 1 else r_v
            b_v = float(vec[2]) if len(vec) > 2 else r_v
            a_v = float(vec[3]) if len(vec) > 3 else 1.0
            return (r_v, g_v, b_v, a_v)

        sampler.min_rgba = _to_rgba(c_min)
        sampler.max_rgba = _to_rgba(c_max)
        sampler.avg_rgba = _to_rgba(c_avg)
        sampler.med_rgba = _to_rgba(c_med)
        sampler.r, sampler.g, sampler.b, sampler.a = sampler.avg_rgba

    def _refresh_all_samplers(self) -> None:
        """Re-evaluate all pinned color samplers against the current frame and notify listeners."""
        for s in self._color_samplers:
            self._evaluate_sampler(s)
        self.samplers_changed.emit(list(self._color_samplers))

    MAX_COLOR_SAMPLERS = 3

    def add_color_sampler(
        self,
        norm_x: float,
        norm_y: float,
        norm_w: float = 0.0,
        norm_h: float = 0.0,
        aperture: Optional[int] = None,
    ) -> ColorSampler:
        """Add a new pinned ColorSampler in normalized image coordinates (max 3 samplers, FIFO replacement)."""
        ap = self._inspector_aperture if aperture is None else max(1, int(aperture))
        if len(self._color_samplers) >= self.MAX_COLOR_SAMPLERS:
            evicted = self._color_samplers.pop(0)
            new_id = evicted.sampler_id
        else:
            used_ids = {s.sampler_id for s in self._color_samplers}
            new_id = next((i for i in range(1, self.MAX_COLOR_SAMPLERS + 1) if i not in used_ids), 1)

        sampler = ColorSampler(
            sampler_id=new_id,
            norm_x=max(0.0, min(1.0, float(norm_x))),
            norm_y=max(0.0, min(1.0, float(norm_y))),
            norm_w=max(0.0, min(1.0, float(norm_w))),
            norm_h=max(0.0, min(1.0, float(norm_h))),
            aperture=ap,
        )
        self._active_analyzer_sampler_id = sampler.sampler_id
        self._evaluate_sampler(sampler)
        self._color_samplers.append(sampler)
        self.samplers_changed.emit(list(self._color_samplers))
        self.update()
        return sampler

    def add_color_sampler_at_pixel(
        self, ix: int, iy: int, w: int = 1, h: int = 1, aperture: Optional[int] = None
    ) -> ColorSampler:
        """Add a pinned ColorSampler at explicit image pixel coordinates (or WxH pixel area)."""
        img_w = float(max(1, self._img_width))
        img_h = float(max(1, self._img_height))
        if w > 1 or h > 1:
            nx = (float(ix) + float(w) / 2.0) / img_w
            ny = (float(iy) + float(h) / 2.0) / img_h
            nw = float(w) / img_w
            nh = float(h) / img_h
            return self.add_color_sampler(nx, ny, nw, nh, aperture=1)
        nx = float(ix) / img_w
        ny = float(iy) / img_h
        return self.add_color_sampler(nx, ny, 0.0, 0.0, aperture=aperture)

    def remove_color_sampler(self, sampler_id: int) -> None:
        """Remove a specific pinned ColorSampler by ID."""
        before = len(self._color_samplers)
        self._color_samplers = [s for s in self._color_samplers if s.sampler_id != int(sampler_id)]
        if len(self._color_samplers) != before:
            if not self._color_samplers:
                self._next_sampler_id = 1
                self._active_analyzer_sampler_id = None
            elif self._active_analyzer_sampler_id == int(sampler_id):
                self._active_analyzer_sampler_id = self._color_samplers[-1].sampler_id
            self.samplers_changed.emit(list(self._color_samplers))
            self.update()

    def clear_color_samplers(self) -> None:
        """Remove all pinned ColorSamplers from the viewport."""
        if self._color_samplers:
            self._color_samplers.clear()
            self._next_sampler_id = 1
            self._active_analyzer_sampler_id = None
            self.samplers_changed.emit([])
            self.update()

    def _find_sampler_at_screen_pos(self, sx: float, sy: float, radius: float = 12.0) -> Optional[ColorSampler]:
        """Return the topmost ColorSampler whose screen handle is within `radius` pixels."""
        for s in reversed(self._color_samplers):
            pt = self._image_norm_to_screen(s.norm_x, s.norm_y)
            if math.hypot(sx - pt.x(), sy - pt.y()) <= radius:
                return s
        return None

    def sample_at_screen_pos(self, sx: float, sy: float) -> Tuple[int, int, float, float, float, float]:
        """Sample the image pixel/aperture at viewport screen coordinates (sx, sy) and emit pixel_inspected."""
        self._current_mouse_pos = QPoint(int(round(sx)), int(round(sy)))
        ix, iy = self._screen_to_image_coords(sx, sy)
        r, g, b, a = 0.0, 0.0, 0.0, 1.0
        if self._image_a is not None and 0 <= ix < self._img_width and 0 <= iy < self._img_height:
            r, g, b, a = self._sample_image_region(ix, iy, w=1, h=1, aperture=self._inspector_aperture)
        self.pixel_inspected.emit(ix, iy, r, g, b, a)
        return ix, iy, r, g, b, a

    def sample_at_global_pos(self, global_pos: QPoint) -> Tuple[int, int, float, float, float, float]:
        """Map a global screen position (e.g., cursor hovering over a FloatingHUDCard) to viewport and sample."""
        local_pt = self.mapFromGlobal(global_pos)
        return self.sample_at_screen_pos(float(local_pt.x()), float(local_pt.y()))

    def _paint_color_samplers_overlay(self, painter: QPainter) -> None:
        """Render pinned Eyedropper samplers (#1, #2, ...), active area box, and NaN/Inf/Neg diagnostic markers."""
        if not self._color_samplers and not self._creating_sampler_drag:
            return

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        # 1. Draw in-progress rectangular area drag box
        if self._creating_sampler_drag and self._sampler_drag_start_screen and self._sampler_drag_curr_screen:
            p0 = self._sampler_drag_start_screen
            p1 = self._sampler_drag_curr_screen
            if math.hypot(p1.x() - p0.x(), p1.y() - p0.y()) > 5.0:
                sel_rect = QRectF(
                    min(p0.x(), p1.x()),
                    min(p0.y(), p1.y()),
                    abs(p1.x() - p0.x()),
                    abs(p1.y() - p0.y()),
                )
                painter.setBrush(QColor(229, 168, 35, 35))
                painter.setPen(QPen(QColor(229, 168, 35, 230), 1.5, Qt.PenStyle.DashLine))
                painter.drawRect(sel_rect)

        # 2. Draw each pinned sampler and any enabled NaN/Inf/Negative diagnostic markers
        font = QFont("Segoe UI", 8, QFont.Weight.Bold)
        painter.setFont(font)
        img_w = float(max(1, self._img_width))
        img_h = float(max(1, self._img_height))

        for s in self._color_samplers:
            is_selected = (s.sampler_id == self._active_analyzer_sampler_id or self._dragging_sampler_id == s.sampler_id)
            center_pt = self._image_norm_to_screen(s.norm_x, s.norm_y)
            cx, cy = center_pt.x(), center_pt.y()

            # Draw area rectangle if this is an area sampler
            if s.norm_w > 0.0 or s.norm_h > 0.0:
                tl = self._image_norm_to_screen(s.norm_x - s.norm_w / 2.0, s.norm_y - s.norm_h / 2.0)
                br = self._image_norm_to_screen(s.norm_x + s.norm_w / 2.0, s.norm_y + s.norm_h / 2.0)
                area_rect = QRectF(
                    min(tl.x(), br.x()),
                    min(tl.y(), br.y()),
                    abs(br.x() - tl.x()),
                    abs(br.y() - tl.y()),
                )
                fill_c = QColor(229, 168, 35, 32) if is_selected else QColor(0, 210, 255, 25)
                pen_c = QColor(255, 200, 40, 235) if is_selected else QColor(0, 210, 255, 200)
                painter.setBrush(fill_c)
                painter.setPen(QPen(pen_c, 1.5 if is_selected else 1.2, Qt.PenStyle.DashLine))
                painter.drawRect(area_rect)

            # Highlight offending NaN / Inf / Negative pixels when toggles are checked
            if self.highlight_neg_pixels and s.neg_coords:
                painter.setPen(QPen(QColor(255, 70, 70, 230), 1.5))
                painter.setBrush(QColor(255, 70, 70, 90))
                for px, py in s.neg_coords:
                    spt = self._image_norm_to_screen((px + 0.5) / img_w, (py + 0.5) / img_h)
                    painter.drawEllipse(spt, 3.5, 3.5)

            if self.highlight_inf_pixels and s.inf_coords:
                painter.setPen(QPen(QColor(255, 180, 0, 240), 1.5))
                painter.setBrush(QColor(255, 180, 0, 110))
                for px, py in s.inf_coords:
                    spt = self._image_norm_to_screen((px + 0.5) / img_w, (py + 0.5) / img_h)
                    painter.drawRect(QRectF(spt.x() - 4.0, spt.y() - 4.0, 8.0, 8.0))

            if self.highlight_nan_pixels and s.nan_coords:
                painter.setPen(QPen(QColor(255, 0, 255, 250), 1.8))
                for px, py in s.nan_coords:
                    spt = self._image_norm_to_screen((px + 0.5) / img_w, (py + 0.5) / img_h)
                    painter.drawLine(QPointF(spt.x() - 4.5, spt.y() - 4.5), QPointF(spt.x() + 4.5, spt.y() + 4.5))
                    painter.drawLine(QPointF(spt.x() - 4.5, spt.y() + 4.5), QPointF(spt.x() + 4.5, spt.y() - 4.5))

            # Outer dark halo for high contrast on any plate
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor(0, 0, 0, 220), 3.0))
            painter.drawEllipse(center_pt, 6.0, 6.0)
            painter.drawLine(QPointF(cx - 10, cy), QPointF(cx - 3, cy))
            painter.drawLine(QPointF(cx + 3, cy), QPointF(cx + 10, cy))
            painter.drawLine(QPointF(cx, cy - 10), QPointF(cx, cy - 3))
            painter.drawLine(QPointF(cx, cy + 3), QPointF(cx, cy + 10))

            # Crisp amber/cyan crosshair
            accent = QColor(255, 200, 40) if is_selected else QColor(0, 225, 255)
            painter.setPen(QPen(accent, 1.5))
            painter.drawEllipse(center_pt, 6.0, 6.0)
            painter.drawLine(QPointF(cx - 10, cy), QPointF(cx - 3, cy))
            painter.drawLine(QPointF(cx + 3, cy), QPointF(cx + 10, cy))
            painter.drawLine(QPointF(cx, cy - 10), QPointF(cx, cy - 3))
            painter.drawLine(QPointF(cx, cy + 3), QPointF(cx, cy + 10))

            # Numbered label pill (#1, #2...)
            label_txt = f"#{s.sampler_id}"
            badge_rect = QRectF(cx + 8, cy - 18, 26, 15)
            painter.setBrush(QColor(18, 18, 18, 230))
            painter.setPen(QPen(accent, 1.0))
            painter.drawRoundedRect(badge_rect, 3.0, 3.0)
            painter.setPen(QColor(245, 245, 245))
            painter.drawText(badge_rect, Qt.AlignmentFlag.AlignCenter, label_txt)

        painter.restore()

    def mousePressEvent(self, event) -> None:
        pos = event.position()
        self._last_mouse_pos = pos.toPoint()
        self._current_mouse_pos = pos.toPoint()

        # If in-canvas text editor is open and clicked
        if hasattr(self, "_text_editor") and self._text_editor.is_active():
            click_pt = pos.toPoint()
            if self._text_editor.geometry().contains(click_pt):
                # Click is inside active text editor: let editor handle focus/cursor
                event.accept()
                return

            # Click is outside active text editor: commit if non-empty, otherwise cancel
            if self._text_editor.toPlainText().strip():
                self._text_editor.commit()
            else:
                self._text_editor.cancel()

            if self._active_draw_tool == ShapeType.TEXT and event.button() == Qt.MouseButton.LeftButton:
                self._current_stroke_points.clear()
                hit = self._find_text_stroke_at(click_pt)
                if hit is not None:
                    idx, stroke, p0 = hit
                    self._editing_text_stroke_idx = idx
                    self.spawn_text_editor(p0, initial_text=stroke.text)
                else:
                    self._editing_text_stroke_idx = None
                    self.spawn_text_editor(click_pt)
                event.accept()
                return

        # Right-click context menu press: accept so release triggers menu reliably across fullscreen
        if event.button() == Qt.MouseButton.RightButton:
            self._right_press_pos = pos.toPoint()
            event.accept()
            return

        # Pan navigation: Middle Button or Alt + Left Button
        if event.button() == Qt.MouseButton.MiddleButton or (
            event.button() == Qt.MouseButton.LeftButton and (event.modifiers() & Qt.KeyboardModifier.AltModifier)
        ):
            if event.button() == Qt.MouseButton.MiddleButton and (event.modifiers() & Qt.KeyboardModifier.ControlModifier):
                self._is_zooming_drag = True
                event.accept()
                return
            self._is_panning = True
            event.accept()
            return

        # Pinned Color Sampler Interaction: Drag existing sampler or Shift+Click to remove
        if event.button() == Qt.MouseButton.LeftButton and self._color_samplers:
            hit_sampler = self._find_sampler_at_screen_pos(pos.x(), pos.y())
            if hit_sampler is not None:
                if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                    self.remove_color_sampler(hit_sampler.sampler_id)
                else:
                    self._dragging_sampler_id = hit_sampler.sampler_id
                    self.update()
                event.accept()
                return

        # Eyedropper Mode: Click or Click-Drag to place a new point/area ColorSampler
        if (
            self._eyedropper_mode
            and event.button() == Qt.MouseButton.LeftButton
            and not (event.modifiers() & Qt.KeyboardModifier.AltModifier)
        ):
            self._creating_sampler_drag = True
            self._sampler_drag_start_screen = QPointF(pos.x(), pos.y())
            self._sampler_drag_curr_screen = QPointF(pos.x(), pos.y())
            self.update()
            event.accept()
            return

        # Scrub Mode Navigation: Left click drag when scrub mode is active
        if (
            self.scrub_mode_enabled
            and event.button() == Qt.MouseButton.LeftButton
            and self._active_draw_tool is None
            and not (event.modifiers() & Qt.KeyboardModifier.AltModifier)
            and self.comparison_state.mode not in (ComparisonMode.WIPE, ComparisonMode.SPLIT, ComparisonMode.SPOTLIGHT)
        ):
            self._is_scrubbing = True
            self._scrub_accum_dx = 0.0
            event.accept()
            return

        # Interactive Spotlight Dragging
        if (
            event.button() == Qt.MouseButton.LeftButton
            and self._active_draw_tool is None
            and self.comparison_state.mode == ComparisonMode.SPOTLIGHT
            and not (event.modifiers() & Qt.KeyboardModifier.AltModifier)
        ):
            nx, ny = self._screen_to_image_norm(pos.x(), pos.y())
            self.comparison_state.wipe_center = (max(0.0, min(1.0, nx)), max(0.0, min(1.0, ny)))
            self._is_dragging_spotlight = True
            self.update()
            event.accept()
            return

        # Interactive A/B Wipe / Rotation Dragging (Restricted to WIPE mode & handle/line hit-testing)
        if (
            event.button() == Qt.MouseButton.LeftButton
            and self._active_draw_tool is None
            and self.comparison_state.mode == ComparisonMode.WIPE
            and not (event.modifiers() & Qt.KeyboardModifier.AltModifier)
        ):
            mx, my = pos.x(), pos.y()
            rx, ry = self._wipe_rot_handle_center.x(), self._wipe_rot_handle_center.y()
            hx, hy = self._wipe_handle_center.x(), self._wipe_handle_center.y()
            dist_rot = math.hypot(mx - rx, my - ry)
            dist_handle = math.hypot(mx - hx, my - hy)
            nx = self._wipe_screen_normal.x()
            ny = self._wipe_screen_normal.y()
            dist_line = abs((mx - hx) * nx + (my - hy) * ny)

            # Rotation drag on rotation knob or Ctrl+Click near handle/line
            if dist_rot <= 16.0 or (event.modifiers() & Qt.KeyboardModifier.ControlModifier and (dist_handle <= 35.0 or dist_line <= 18.0)):
                self._is_rotating_wipe = True
                self._start_wipe_rotation(pos.x(), pos.y())
                event.accept()
                return

            # Shift+Click near handle or line toggles orientation between vertical (0°) and horizontal (90°)
            if (event.modifiers() & Qt.KeyboardModifier.ShiftModifier) and (dist_handle <= 25.0 or dist_line <= 18.0):
                self.comparison_state.flip_orientation()
                self.wipe_angle_changed.emit(float(self.comparison_state.wipe_angle_deg))
                self.update()
                event.accept()
                return

            # Wipe position drag only if clicking directly on handle or near divider line
            if dist_handle <= 22.0 or dist_line <= 18.0 or self._wipe_hover_part in ("line", "handle"):
                self._is_wiping = True
                self._update_wipe_from_mouse(pos.x(), pos.y())
                event.accept()
                return

        # Markup Drawing
        if event.button() == Qt.MouseButton.LeftButton and self._active_draw_tool is not None:
            if self._active_draw_tool == ShapeType.TEXT:
                self._current_stroke_points.clear()
                click_pt = pos.toPoint()
                hit = self._find_text_stroke_at(click_pt)
                if hit is not None:
                    idx, stroke, p0 = hit
                    self._editing_text_stroke_idx = idx
                    self.spawn_text_editor(p0, initial_text=stroke.text)
                else:
                    self._editing_text_stroke_idx = None
                    self.spawn_text_editor(click_pt)
                event.accept()
                return

            nx, ny = self._screen_to_image_norm(pos.x(), pos.y())
            self._current_stroke_points = [(nx, ny)]
            self.update()
            event.accept()
            return

        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        pos = event.position()
        self._current_mouse_pos = pos.toPoint()

        # Always sample live pixel under cursor first so Color Inspector never stalls
        self.sample_at_screen_pos(pos.x(), pos.y())

        # Handle Dragging an Existing Color Sampler
        if self._dragging_sampler_id is not None and (event.buttons() & Qt.MouseButton.LeftButton):
            nx, ny = self._screen_to_image_norm(pos.x(), pos.y())
            for s in self._color_samplers:
                if s.sampler_id == self._dragging_sampler_id:
                    s.norm_x = max(0.0, min(1.0, nx))
                    s.norm_y = max(0.0, min(1.0, ny))
                    self._evaluate_sampler(s)
                    break
            self.samplers_changed.emit(list(self._color_samplers))
            self.update()
            self._last_mouse_pos = pos.toPoint()
            event.accept()
            return

        # Handle Creating a New Area Sampler Drag
        if self._creating_sampler_drag and (event.buttons() & Qt.MouseButton.LeftButton):
            self._sampler_drag_curr_screen = QPointF(pos.x(), pos.y())
            self.update()
            self._last_mouse_pos = pos.toPoint()
            event.accept()
            return

        # Handle Ctrl + Middle Button Zoom Drag
        if self._is_zooming_drag and (event.buttons() & Qt.MouseButton.MiddleButton):
            delta = pos.toPoint() - self._last_mouse_pos
            zoom_factor = 1.0 + (delta.x() - delta.y()) * 0.005
            self.set_zoom(self._zoom * zoom_factor)
            self._last_mouse_pos = pos.toPoint()
            event.accept()
            return

        # Handle Pan Drag
        if self._is_panning:
            delta = pos.toPoint() - self._last_mouse_pos
            self._pan_x += delta.x()
            self._pan_y += delta.y()
            self._last_mouse_pos = pos.toPoint()
            self.update()
            event.accept()
            return

        # Handle Canvas Scrub Drag
        if self._is_scrubbing and (event.buttons() & Qt.MouseButton.LeftButton):
            dx = pos.toPoint().x() - self._last_mouse_pos.x()
            self._scrub_accum_dx += dx
            step_pixels = 8.0
            steps = int(self._scrub_accum_dx / step_pixels)
            if steps != 0:
                self.scrub_requested.emit(steps)
                self._scrub_accum_dx -= (steps * step_pixels)
            self._last_mouse_pos = pos.toPoint()
            event.accept()
            return

        # Handle Interactive Wipe Rotation Drag
        if self._is_rotating_wipe and (event.buttons() & Qt.MouseButton.LeftButton):
            snap = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
            self._update_wipe_rotation_from_mouse(pos.x(), pos.y(), snap=snap)
            event.accept()
            return

        # Handle Interactive Spotlight Drag
        if getattr(self, "_is_dragging_spotlight", False) and (event.buttons() & Qt.MouseButton.LeftButton):
            nx, ny = self._screen_to_image_norm(pos.x(), pos.y())
            self.comparison_state.wipe_center = (max(0.0, min(1.0, nx)), max(0.0, min(1.0, ny)))
            self.update()
            self._last_mouse_pos = pos.toPoint()
            event.accept()
            return

        # Handle Interactive Wipe Drag
        if self._is_wiping and (event.buttons() & Qt.MouseButton.LeftButton):
            self._update_wipe_from_mouse(pos.x(), pos.y())
            event.accept()
            return

        # Handle Markup Drawing Drag
        if (
            self._active_draw_tool is not None
            and self._active_draw_tool != ShapeType.TEXT
            and (event.buttons() & Qt.MouseButton.LeftButton)
        ):
            nx, ny = self._screen_to_image_norm(pos.x(), pos.y())
            self._current_stroke_points.append((nx, ny))
            self.update()
            event.accept()
            return

        # Wipe Overlay Hover Tracking (when no mouse buttons are pressed, restricted to WIPE mode)
        if (
            not event.buttons()
            and self.comparison_state.mode == ComparisonMode.WIPE
            and self._active_draw_tool is None
        ):
            hx, hy = self._wipe_handle_center.x(), self._wipe_handle_center.y()
            rx, ry = self._wipe_rot_handle_center.x(), self._wipe_rot_handle_center.y()
            mx, my = pos.x(), pos.y()

            dist_rot = math.hypot(mx - rx, my - ry)
            dist_handle = math.hypot(mx - hx, my - hy)

            nx = self._wipe_screen_normal.x()
            ny = self._wipe_screen_normal.y()
            dist_line = abs((mx - hx) * nx + (my - hy) * ny)

            old_hover = self._wipe_hover_part
            if dist_rot <= 14.0:
                self._wipe_hover_part = "rotate"
                self.setCursor(Qt.CursorShape.PointingHandCursor)
            elif dist_handle <= 22.0:
                self._wipe_hover_part = "handle"
                self.setCursor(Qt.CursorShape.SizeAllCursor)
            elif dist_line <= 14.0:
                self._wipe_hover_part = "line"
                self.setCursor(Qt.CursorShape.SplitHCursor if abs(nx) > abs(ny) else Qt.CursorShape.SplitVCursor)
            else:
                self._wipe_hover_part = None
                if self._eyedropper_mode:
                    self.setCursor(Qt.CursorShape.CrossCursor)
                else:
                    self.unsetCursor()

            if old_hover != self._wipe_hover_part:
                self.update()

        self._last_mouse_pos = pos.toPoint()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if self._dragging_sampler_id is not None:
            self._dragging_sampler_id = None
            self.update()
            event.accept()
            return

        if self._creating_sampler_drag:
            self._creating_sampler_drag = False
            pos = event.position()
            start_pt = self._sampler_drag_start_screen or QPointF(pos.x(), pos.y())
            self._sampler_drag_start_screen = None
            self._sampler_drag_curr_screen = None

            dist = math.hypot(pos.x() - start_pt.x(), pos.y() - start_pt.y())
            if dist > 5.0:
                nx0, ny0 = self._screen_to_image_norm(start_pt.x(), start_pt.y())
                nx1, ny1 = self._screen_to_image_norm(pos.x(), pos.y())
                cx = (nx0 + nx1) / 2.0
                cy = (ny0 + ny1) / 2.0
                nw = abs(nx1 - nx0)
                nh = abs(ny1 - ny0)
                self.add_color_sampler(cx, cy, nw, nh, aperture=1)
            else:
                nx, ny = self._screen_to_image_norm(pos.x(), pos.y())
                self.add_color_sampler(nx, ny, 0.0, 0.0, aperture=self._inspector_aperture)
            self.set_eyedropper_mode(False)
            event.accept()
            return

        if self._is_zooming_drag:
            self._is_zooming_drag = False
            event.accept()
            return

        if self._is_scrubbing:
            self._is_scrubbing = False
            event.accept()
            return

        if self._is_panning:
            self._is_panning = False
            event.accept()
            return

        if getattr(self, "_is_dragging_spotlight", False):
            self._is_dragging_spotlight = False
            self.update()
            event.accept()
            return

        if self._is_wiping:
            self._is_wiping = False
            self.update()
            event.accept()
            return

        if self._is_rotating_wipe:
            self._is_rotating_wipe = False
            self.update()
            event.accept()
            return

        # Finish Markup Stroke
        if self._active_draw_tool == ShapeType.TEXT:
            self._current_stroke_points.clear()

        if (
            self._active_draw_tool is not None
            and self._active_draw_tool != ShapeType.TEXT
            and self._current_stroke_points
        ):
            pts = list(self._current_stroke_points)
            if self._active_draw_tool == ShapeType.PEN and len(pts) >= 3:
                pts = self._smooth_points_chaikin(pts, iterations=2)

            stroke = Stroke(
                shape_type=self._active_draw_tool,
                points=pts,
                color=self._active_draw_color,
                width=self._active_draw_width,
            )
            self.annotation_store.add_stroke(self.current_frame, stroke)
            self._current_stroke_points.clear()
            self.update()
            event.accept()
            return

        if event.button() == Qt.MouseButton.RightButton:
            gpos = event.globalPosition().toPoint() if hasattr(event, "globalPosition") else event.globalPos()
            self._show_context_menu(gpos)
            event.accept()
            return

        super().mouseReleaseEvent(event)

    @staticmethod
    def _build_smooth_bezier_path(points: list[QPointF]) -> QPainterPath:
        """Construct a smooth Catmull-Rom to Cubic Bezier QPainterPath through points."""
        path = QPainterPath()
        if not points:
            return path
        if len(points) == 1:
            path.moveTo(points[0])
            path.lineTo(points[0])
            return path
        if len(points) == 2:
            path.moveTo(points[0])
            path.lineTo(points[1])
            return path

        # Filter out micro-jitter points that are too close (< 1.5 pixels)
        filtered = [points[0]]
        for p in points[1:]:
            dx = p.x() - filtered[-1].x()
            dy = p.y() - filtered[-1].y()
            if dx * dx + dy * dy >= 2.25:
                filtered.append(p)
        if len(filtered) < 2:
            filtered = [points[0], points[-1]]

        n = len(filtered)
        if n == 2:
            path.moveTo(filtered[0])
            path.lineTo(filtered[1])
            return path

        # Natural virtual endpoints for tangent continuity
        p_prev = QPointF(2.0 * filtered[0].x() - filtered[1].x(), 2.0 * filtered[0].y() - filtered[1].y())
        p_next = QPointF(2.0 * filtered[-1].x() - filtered[-2].x(), 2.0 * filtered[-1].y() - filtered[-2].y())
        pts = [p_prev] + filtered + [p_next]

        path.moveTo(filtered[0])
        for i in range(1, n):
            p0 = pts[i - 1]
            p1 = pts[i]
            p2 = pts[i + 1]
            p3 = pts[i + 2]

            # Catmull-Rom to Cubic Bezier control points (tension = 0.5)
            c1 = QPointF(p1.x() + (p2.x() - p0.x()) / 6.0, p1.y() + (p2.y() - p0.y()) / 6.0)
            c2 = QPointF(p2.x() - (p3.x() - p1.x()) / 6.0, p2.y() - (p3.y() - p1.y()) / 6.0)

            path.cubicTo(c1, c2, p2)

        return path

    @staticmethod
    def _smooth_points_chaikin(points: list[Tuple[float, float]], iterations: int = 2) -> list[Tuple[float, float]]:
        """Apply Chaikin's corner-cutting subdivision algorithm to smooth polyline points."""
        if len(points) < 3:
            return points
        cleaned = [points[0]]
        for p in points[1:]:
            if abs(p[0] - cleaned[-1][0]) > 1e-5 or abs(p[1] - cleaned[-1][1]) > 1e-5:
                cleaned.append(p)
        if len(cleaned) < 3:
            return cleaned

        pts = cleaned
        for _ in range(iterations):
            new_pts = [pts[0]]
            for i in range(len(pts) - 1):
                p0 = pts[i]
                p1 = pts[i + 1]
                q = (0.75 * p0[0] + 0.25 * p1[0], 0.75 * p0[1] + 0.25 * p1[1])
                r = (0.25 * p0[0] + 0.75 * p1[0], 0.25 * p0[1] + 0.75 * p1[1])
                new_pts.extend([q, r])
            new_pts.append(pts[-1])
            pts = new_pts
        return pts

    def leaveEvent(self, event) -> None:
        if self._wipe_hover_part is not None:
            self._wipe_hover_part = None
            self.unsetCursor()
            self.update()
        super().leaveEvent(event)

    def wheelEvent(self, event) -> None:
        """Smooth exponential zoom centered on mouse cursor position, protected against accidental zooming over opened widgets."""
        # Check accidental zoom scroll protection over any opened widget / HUD card
        pos_local = event.position().toPoint()
        if self.childAt(pos_local) is not None:
            event.ignore()
            return

        from PySide6.QtWidgets import QApplication
        pos_global = event.globalPosition().toPoint() if hasattr(event, "globalPosition") else self.mapToGlobal(pos_local)
        w_at_cursor = QApplication.widgetAt(pos_global)
        if w_at_cursor is not None and w_at_cursor != self and self.isAncestorOf(w_at_cursor):
            event.ignore()
            return

        angle = event.angleDelta().y()
        if angle == 0:
            return

        zoom_multiplier = 1.15 if angle > 0 else (1.0 / 1.15)
        old_zoom = self._zoom
        new_zoom = max(0.05, min(self._zoom * zoom_multiplier, 50.0))

        # Anchor zoom around cursor
        mouse_x = event.position().x()
        mouse_y = event.position().y()
        vw = float(self.width())
        vh = float(self.height())

        scale_factor = new_zoom / old_zoom
        self._pan_x = (mouse_x - vw / 2.0) - scale_factor * ((mouse_x - vw / 2.0) - self._pan_x)
        self._pan_y = (mouse_y - vh / 2.0) - scale_factor * ((mouse_y - vh / 2.0) - self._pan_y)
        self._zoom = new_zoom

        self.zoom_changed.emit(self._zoom)
        self.update()
        event.accept()

    # --------------------------------------------------------------------------
    # Drag-and-Drop Sequence Loading & Split Overlay
    # --------------------------------------------------------------------------
    def _is_accepted_drag_event(self, event) -> bool:
        mime = event.mimeData()
        if not mime:
            return False
        if mime.hasUrls() or mime.hasText() or mime.hasFormat("application/x-openreview-source-ids"):
            return True
        if event.source() is not None:
            return True
        return False

    def dragEnterEvent(self, event) -> None:
        if self._is_accepted_drag_event(event):
            event.acceptProposedAction()
            self._drag_overlay_active = True
            pos_y = event.position().y() if hasattr(event, "position") else event.pos().y()
            self._drag_target = "stack" if pos_y < (self.height() / 2.0) else "bin"
            self.update()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event) -> None:
        if self._is_accepted_drag_event(event):
            event.acceptProposedAction()
            self._drag_overlay_active = True
            pos_y = event.position().y() if hasattr(event, "position") else event.pos().y()
            new_target = "stack" if pos_y < (self.height() / 2.0) else "bin"
            if new_target != self._drag_target:
                self._drag_target = new_target
                self.update()
        else:
            super().dragMoveEvent(event)

    def dragLeaveEvent(self, event) -> None:
        if self._drag_overlay_active:
            self._drag_overlay_active = False
            self._drag_target = None
            self.update()
        super().dragLeaveEvent(event)

    def dropEvent(self, event) -> None:
        pos_y = event.position().y() if hasattr(event, "position") else event.pos().y()
        target = self._drag_target or ("stack" if pos_y < (self.height() / 2.0) else "bin")
        self._drag_overlay_active = False
        self._drag_target = None
        self.update()

        paths: list[str] = []
        source_ids: list[str] = []

        mime = event.mimeData()
        if mime:
            if mime.hasFormat("application/x-openreview-source-ids"):
                try:
                    import json
                    raw_sids = bytes(mime.data("application/x-openreview-source-ids")).decode("utf-8")
                    source_ids = json.loads(raw_sids)
                except Exception:
                    pass

            if mime.hasUrls():
                urls = mime.urls()
                paths = [u.toLocalFile() for u in urls if u.toLocalFile()]
            elif mime.hasText():
                t = mime.text().strip()
                if os.path.exists(t):
                    paths = [t]

        if paths or source_ids:
            event.acceptProposedAction()
            # Decouple asynchronously so Windows OLE immediately releases drag cursor without freezing
            QTimer.singleShot(0, lambda p=paths, t=target, s=source_ids: self._emit_drop(p, t, s))
        else:
            super().dropEvent(event)

    def _emit_drop(self, paths: list[str], target: str, source_ids: list[str]) -> None:
        self.files_dropped_target.emit(paths, target, source_ids)
        if paths:
            self.files_dropped.emit(paths)

    # --------------------------------------------------------------------------
    # Markup Overlay Painting
    # --------------------------------------------------------------------------
    def _paint_markup_overlay(self, painter: QPainter) -> None:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        # Draw committed strokes for current frame (smooth & anti-aliased)
        strokes = self.annotation_store.get_strokes(self.current_frame)
        for stroke in strokes:
            self._render_stroke(painter, stroke, is_draft=False)

        # Draw active in-progress stroke (first draw draft shape as it is)
        if self._active_draw_tool and self._active_draw_tool != ShapeType.TEXT and self._current_stroke_points:
            temp_stroke = Stroke(
                shape_type=self._active_draw_tool,
                points=self._current_stroke_points,
                color=self._active_draw_color,
                width=self._active_draw_width,
            )
            self._render_stroke(painter, temp_stroke, is_draft=True)

    def _render_stroke(self, painter: QPainter, stroke: Stroke, is_draft: bool = False) -> None:
        if not stroke.points:
            return

        if is_draft and stroke.shape_type == ShapeType.PEN:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        else:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        color = QColor(*stroke.color)
        pen = QPen(color, stroke.width, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)

        screen_pts = [self._image_norm_to_screen(p[0], p[1]) for p in stroke.points]

        if stroke.shape_type == ShapeType.PEN:
            if len(screen_pts) == 1:
                painter.drawPoint(screen_pts[0])
            elif not is_draft:
                path = self._build_smooth_bezier_path(screen_pts)
                painter.drawPath(path)
            else:
                for i in range(len(screen_pts) - 1):
                    painter.drawLine(screen_pts[i], screen_pts[i + 1])

        elif stroke.shape_type == ShapeType.LINE and len(screen_pts) >= 2:
            painter.drawLine(screen_pts[0], screen_pts[-1])

        elif stroke.shape_type == ShapeType.ARROW and len(screen_pts) >= 2:
            p0 = screen_pts[0]
            p1 = screen_pts[-1]
            painter.drawLine(p0, p1)
            # Draw arrowhead
            dx = p1.x() - p0.x()
            dy = p1.y() - p0.y()
            length = math.hypot(dx, dy)
            if length > 5:
                ux = dx / length
                uy = dy / length
                arrow_sz = 14.0
                left_x = p1.x() - arrow_sz * (ux + uy * 0.5)
                left_y = p1.y() - arrow_sz * (uy - ux * 0.5)
                right_x = p1.x() - arrow_sz * (ux - uy * 0.5)
                right_y = p1.y() - arrow_sz * (uy + ux * 0.5)
                painter.drawLine(p1, QPointF(left_x, left_y))
                painter.drawLine(p1, QPointF(right_x, right_y))

        elif stroke.shape_type == ShapeType.RECTANGLE and len(screen_pts) >= 2:
            p0 = screen_pts[0]
            p1 = screen_pts[-1]
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(QRectF(min(p0.x(), p1.x()), min(p0.y(), p1.y()), abs(p1.x() - p0.x()), abs(p1.y() - p0.y())))

        elif stroke.shape_type == ShapeType.CIRCLE and len(screen_pts) >= 2:
            p0 = screen_pts[0]
            p1 = screen_pts[-1]
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(QRectF(min(p0.x(), p1.x()), min(p0.y(), p1.y()), abs(p1.x() - p0.x()), abs(p1.y() - p0.y())))

        elif stroke.shape_type == ShapeType.TEXT and screen_pts:
            txt = (stroke.text or "").strip()
            if not txt:
                return
            p0 = screen_pts[0]
            font_size = max(10, int(stroke.width * 3.5)) if stroke.width > 1.0 else 12
            font = QFont("Segoe UI", font_size, QFont.Weight.Bold)
            painter.setFont(font)
            metrics = painter.fontMetrics()

            lines = txt.split("\n")
            line_height = metrics.lineSpacing()
            max_line_w = max((metrics.horizontalAdvance(line) for line in lines), default=40)
            total_h = line_height * len(lines)

            pad_x = 8
            pad_y = 6
            box_w = max_line_w + pad_x * 2
            box_h = total_h + pad_y * 2

            box_x = int(p0.x())
            box_y = int(p0.y())
            text_rect = QRect(box_x, box_y, box_w, box_h)

            # Translucent plate with rounded corners
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            painter.setBrush(QColor(18, 18, 18, 220))
            pen_color = QColor(*stroke.color)
            painter.setPen(QPen(pen_color, 1.5))
            painter.drawRoundedRect(text_rect, 4.0, 4.0)

            # Draw multi-line text cleanly
            painter.setPen(pen_color)
            cur_y = box_y + pad_y + metrics.ascent()
            for line in lines:
                painter.drawText(box_x + pad_x, cur_y, line)
                cur_y += line_height

    # --------------------------------------------------------------------------
    # Public Slots for Tool & Color Setting & Text Editing
    # --------------------------------------------------------------------------
    @property
    def active_draw_tool(self) -> Optional[ShapeType]:
        return self._active_draw_tool

    def is_typing_active(self) -> bool:
        """Return True if the in-canvas text editor is currently active and editing text."""
        return hasattr(self, "_text_editor") and self._text_editor.is_active()

    def commit_text_editor(self) -> None:
        """Commit active text editor note to frame."""
        if hasattr(self, "_text_editor") and self._text_editor.is_active():
            self._text_editor.commit()

    def cancel_text_editor(self) -> None:
        """Cancel and dismiss active in-canvas text editor."""
        if hasattr(self, "_text_editor") and self._text_editor.is_active():
            self._text_editor.cancel()

    def _find_text_stroke_at(self, screen_pos: QPoint) -> Optional[Tuple[int, Stroke, QPoint]]:
        """
        Hit-test text annotations on current frame against screen_pos.
        Returns (stroke_idx, stroke, p0_screen) if clicked within text bounding box, else None.
        """
        if not hasattr(self, "annotation_store") or self.annotation_store is None:
            return None
        strokes = self.annotation_store.get_strokes(self.current_frame)
        # Search backwards so top-most stroke is hit first
        for idx in range(len(strokes) - 1, -1, -1):
            stroke = strokes[idx]
            if stroke.shape_type != ShapeType.TEXT or not stroke.points:
                continue
            txt = (stroke.text or "").strip()
            if not txt:
                continue

            nx, ny = stroke.points[0]
            pt = self._image_norm_to_screen(nx, ny)
            p0 = QPoint(int(round(pt.x())), int(round(pt.y())))

            font_size = max(10, int(stroke.width * 3.5)) if stroke.width > 1.0 else 12
            font = QFont("Segoe UI", font_size, QFont.Weight.Bold)
            metrics = QFontMetrics(font)

            lines = txt.split("\n")
            line_height = metrics.lineSpacing()
            max_line_w = max((metrics.horizontalAdvance(line) for line in lines), default=40)
            total_h = line_height * len(lines)

            pad_x = 8
            pad_y = 6
            box_w = max_line_w + pad_x * 2
            box_h = total_h + pad_y * 2

            text_rect = QRect(p0.x(), p0.y(), box_w, box_h)
            if text_rect.contains(screen_pos):
                return (idx, stroke, p0)
        return None

    def spawn_text_editor(self, screen_pos: Optional[QPoint] = None, initial_text: str = "") -> None:
        """Spawn the in-canvas text editor at screen coordinates with active stroke styling."""
        if screen_pos is None:
            screen_pos = self._current_mouse_pos
        font_pt = max(11, int(self._active_draw_width * 3.5)) if self._active_draw_width > 1.0 else 12
        self._text_editor.set_stroke_color(self._active_draw_color, font_pt=font_pt)
        self._text_editor.spawn_at(screen_pos, initial_text=initial_text)

    def _on_text_editor_committed(self, text: str) -> None:
        """Called when user commits text in the in-canvas editor."""
        clean_text = text.strip()
        editing_idx = getattr(self, "_editing_text_stroke_idx", None)
        self._editing_text_stroke_idx = None

        if editing_idx is not None:
            # Editing an existing text annotation
            if clean_text:
                self.annotation_store.update_stroke_text(self.current_frame, editing_idx, clean_text)
            else:
                # User cleared all text -> remove the annotation stroke with undo support
                self.annotation_store.remove_stroke(self.current_frame, editing_idx)
            self.update()
            self.setFocus()
            return

        if clean_text:
            pos = self._text_editor.spawn_screen_pos
            nx, ny = self._screen_to_image_norm(pos.x(), pos.y())
            stroke = Stroke(
                shape_type=ShapeType.TEXT,
                points=[(nx, ny)],
                color=self._active_draw_color,
                width=self._active_draw_width,
                text=clean_text,
            )
            self.annotation_store.add_stroke(self.current_frame, stroke)

            # Auto-sync typed annotation into frame's review note
            existing_note = self.annotation_store.get_frame_note(self.current_frame)
            if existing_note:
                if clean_text not in existing_note:
                    self.annotation_store.set_frame_note(self.current_frame, f"{existing_note}\n{clean_text}")
            else:
                self.annotation_store.set_frame_note(self.current_frame, clean_text)

            self.update()
        self.setFocus()

    def _on_text_editor_cancelled(self) -> None:
        """Called when user cancels in-canvas text editing."""
        self._editing_text_stroke_idx = None
        self.setFocus()

    def set_draw_tool(self, tool: Optional[ShapeType]) -> None:
        if self._active_draw_tool == ShapeType.TEXT and tool != ShapeType.TEXT:
            if hasattr(self, "_text_editor") and self._text_editor.isVisible():
                self._text_editor.commit()
        self._active_draw_tool = tool
        if tool == ShapeType.TEXT:
            self.setCursor(Qt.CursorShape.IBeamCursor)
        elif tool is not None:
            self.setCursor(Qt.CursorShape.CrossCursor)
        else:
            self.setCursor(Qt.CursorShape.ArrowCursor)
        self.update()

    def set_draw_color(self, color: Tuple[int, int, int, int]) -> None:
        self._base_draw_color = color[:3]
        alpha = int(round(255 * getattr(self, "_active_draw_opacity", 1.0)))
        self._active_draw_color = (color[0], color[1], color[2], alpha)

    def set_draw_width(self, width: float) -> None:
        self._active_draw_width = max(1.0, float(width))
        try:
            PreferencesManager.instance().prefs.default_brush_width = self._active_draw_width
        except Exception:
            pass
        self.update()

    def set_draw_opacity(self, opacity: float) -> None:
        self._active_draw_opacity = max(0.05, min(1.0, float(opacity)))
        base = getattr(self, "_base_draw_color", self._active_draw_color[:3])
        alpha = int(round(255 * self._active_draw_opacity))
        self._active_draw_color = (base[0], base[1], base[2], alpha)
        self.update()

    def set_color_transforms(self, input_space: int, view_transform: int) -> None:
        """Update active input colorspace and view display transform."""
        self.input_space = input_space
        self.view_transform = view_transform
        self.update()

    def set_input_space(self, input_space: int) -> None:
        """Set source input colorspace index."""
        self.input_space = int(input_space)
        self.update()

    def set_view_transform(self, view_transform: int) -> None:
        """Set display view transform index."""
        self.view_transform = int(view_transform)
        self.update()

    def set_lut(self, lut: Optional[CubeLut]) -> None:
        """Set active 3D or 1D Cube LUT."""
        self.color_grading.active_lut = lut
        self._pending_upload_lut = True
        self.update()

    def set_ocio_lut(self, bake: Optional[Any]) -> None:
        """Set active baked OCIO v2 3D LUT (OcioLutBakeResult) for real-time GPU evaluation."""
        self._active_ocio_lut_bake = bake
        self._pending_upload_ocio_lut = True
        self.update()

    def set_cdl(self, cdl: Optional[AscCdl]) -> None:
        """Set active ASC CDL."""
        self.color_grading.active_cdl = cdl
        self.update()

    def set_lut_enabled(self, enabled: bool) -> None:
        """Toggle active LUT application."""
        self.color_grading.lut_enabled = bool(enabled)
        self.update()

    def set_cdl_enabled(self, enabled: bool) -> None:
        """Toggle active CDL application."""
        self.color_grading.cdl_enabled = bool(enabled)
        self.update()

    def set_invert_colors(self, invert: bool) -> None:
        """Invert RGB channels in display space."""
        self.color_grading.invert_colors = bool(invert)
        self.update()

    def set_show_out_of_range(self, show: bool) -> None:
        """Highlight values < 0.0 (crushed black) and > 1.0 (blown highlight)."""
        self.color_grading.show_out_of_range = bool(show)
        self.update()

    def set_color_grading(self, settings: ColorGradeSettings) -> None:
        """Apply complete color grading settings dataclass."""
        self.color_grading = settings
        self._pending_upload_lut = True
        self.update()

    def reset_color_grading(self) -> None:
        """Reset all interactive grading parameters to neutral defaults."""
        self.color_grading.reset()
        self.update()

    # --------------------------------------------------------------------------
    # Matte Overlay & Checkerboard Painting
    # --------------------------------------------------------------------------
    def _paint_matte_overlay(self, painter: QPainter) -> None:
        if self.matte_ratio is None or self.matte_ratio <= 0.0:
            return

        target_ar = self.matte_ratio

        # Image screen boundaries
        tl = self._image_norm_to_screen(0.0, 0.0)
        tr = self._image_norm_to_screen(1.0, 0.0)
        bl = self._image_norm_to_screen(0.0, 1.0)
        br = self._image_norm_to_screen(1.0, 1.0)
        left = min(tl.x(), tr.x(), bl.x(), br.x())
        right = max(tl.x(), tr.x(), bl.x(), br.x())
        top = min(tl.y(), tr.y(), bl.y(), br.y())
        bottom = max(tl.y(), tr.y(), bl.y(), br.y())

        img_w = right - left
        img_h = bottom - top
        if img_w <= 2 or img_h <= 2:
            return

        current_ar = img_w / img_h
        mask_color = QColor(0, 0, 0, int(round(self.matte_opacity * 255)))
        painter.setBrush(mask_color)
        painter.setPen(Qt.PenStyle.NoPen)

        if target_ar > current_ar:
            # Letterbox: top & bottom bars
            active_h = img_w / target_ar
            bar_h = (img_h - active_h) / 2.0
            if bar_h > 0:
                painter.fillRect(QRectF(left, top, img_w, bar_h), mask_color)
                painter.fillRect(QRectF(left, bottom - bar_h, img_w, bar_h), mask_color)
                painter.setPen(QPen(QColor(140, 140, 140, 160), 1))
                painter.drawLine(QPointF(left, top + bar_h), QPointF(right, top + bar_h))
                painter.drawLine(QPointF(left, bottom - bar_h), QPointF(right, bottom - bar_h))
        else:
            # Pillarbox: left & right bars
            active_w = img_h * target_ar
            bar_w = (img_w - active_w) / 2.0
            if bar_w > 0:
                painter.fillRect(QRectF(left, top, bar_w, img_h), mask_color)
                painter.fillRect(QRectF(right - bar_w, top, bar_w, img_h), mask_color)
                painter.setPen(QPen(QColor(140, 140, 140, 160), 1))
                painter.drawLine(QPointF(left + bar_w, top), QPointF(left + bar_w, bottom))
                painter.drawLine(QPointF(right - bar_w, top), QPointF(right - bar_w, bottom))

    def _paint_comp_qc_splines_overlay(self, painter: QPainter) -> None:
        """Render imported Silhouette FX and Nuke spline overlays in image coordinates."""
        qc = getattr(self, "comp_qc_state", None)
        if not qc or not getattr(qc, "spline_overlay_enabled", False):
            return
        shapes = getattr(self, "comp_qc_splines", None)
        if not shapes or self._img_width <= 0 or self._img_height <= 0:
            return

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        color = qc.spline_stroke_color
        stroke_w = float(qc.spline_stroke_width)
        pen = QPen(color, stroke_w, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)

        fill_op = float(qc.spline_fill_opacity)
        if fill_op > 0.001:
            fill_col = QColor(color.red(), color.green(), color.blue(), int(round(fill_op * 255)))
            painter.setBrush(fill_col)
        else:
            painter.setBrush(Qt.BrushStyle.NoBrush)

        img_w = float(self._img_width)
        img_h = float(self._img_height)

        for shape in shapes:
            pts = shape.get_points_for_frame(self.current_frame)
            if len(pts) < 2:
                continue

            path = QPainterPath()
            first = True
            for pt in pts:
                # Convert pixel or normalized coordinate to normalized [0, 1]
                nx = pt.x / img_w if pt.x > 1.5 else pt.x
                # In Nuke/Silhouette coordinate space, Y=0 is bottom
                raw_y = pt.y if pt.y > 1.5 else pt.y * img_h
                ny = (img_h - raw_y) / img_h if pt.y > 1.5 else (1.0 - pt.y)

                screen_pt = self._image_norm_to_screen(nx, ny)
                if first:
                    path.moveTo(screen_pt)
                    first = False
                else:
                    path.lineTo(screen_pt)

            if shape.is_closed and len(pts) >= 3:
                path.closeSubpath()

            painter.drawPath(path)

            # Draw shape label
            if pts:
                first_nx = pts[0].x / img_w if pts[0].x > 1.5 else pts[0].x
                first_raw_y = pts[0].y if pts[0].y > 1.5 else pts[0].y * img_h
                first_ny = (img_h - first_raw_y) / img_h if pts[0].y > 1.5 else (1.0 - pts[0].y)
                sp = self._image_norm_to_screen(first_nx, first_ny)
                painter.setPen(QColor(color.red(), color.green(), color.blue(), 190))
                font = QFont("Segoe UI", 8)
                painter.setFont(font)
                painter.drawText(QPointF(sp.x() + 4, sp.y() - 4), shape.name)
                painter.setPen(pen)

        painter.restore()

    def _paint_checker_overlay(self, painter: QPainter) -> None:
        """Render a subtle alternating checkerboard background behind media."""
        w = self.width()
        h = self.height()
        tile_sz = 20
        c1 = QColor(25, 25, 25)
        c2 = QColor(35, 35, 35)

        for y in range(0, h, tile_sz):
            for x in range(0, w, tile_sz):
                color = c1 if ((x // tile_sz + y // tile_sz) % 2 == 0) else c2
                painter.fillRect(x, y, tile_sz, tile_sz, color)

    def _paint_info_strip(self, painter: QPainter) -> None:
        """Render floating HUD info strip banner at top of viewport."""
        vw = self.width()
        strip_h = 24
        painter.save()
        # Semi-transparent dark background
        painter.fillRect(0, 0, vw, strip_h, QColor(18, 18, 18, 220))
        painter.setPen(QColor(48, 48, 48, 255))
        painter.drawLine(0, strip_h, vw, strip_h)

        font = QFont("Segoe UI", 9)
        font.setBold(True)
        painter.setFont(font)

        # Left item: File / Media name + resolution + zoom
        name = self.info_strip_data.get("media_name", "Media")
        res_str = f"{self._img_width}x{self._img_height}"
        zoom_pct = int(round(self._zoom * 100))
        left_txt = f"  {name}  |  {res_str}  |  Zoom: {zoom_pct}%"
        painter.setPen(QColor(200, 200, 200))
        painter.drawText(0, 0, vw // 3, strip_h, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, left_txt)

        # Center item: Frame number & Timecode
        f_curr = self.current_frame
        f_start = self.info_strip_data.get("start_frame", 1001)
        f_end = self.info_strip_data.get("end_frame", 1200)
        fps = self.info_strip_data.get("fps", 24.0)
        dur = max(0.0, (f_curr - f_start) / max(0.1, fps))
        tc = f"{int(dur // 3600):02d}:{int((dur % 3600) // 60):02d}:{int(dur % 60):02d}:{int((dur - int(dur)) * fps):02d}"
        center_txt = f"Frame: {f_curr} / {f_end}  [{tc}]"
        painter.setPen(QColor(229, 168, 35))  # 3DE amber
        painter.drawText(vw // 3, 0, vw // 3, strip_h, Qt.AlignmentFlag.AlignCenter, center_txt)

        # Right item: Exp + Gam + Channel + Colorspace
        exp_txt = f"Exp: {self.exposure_ev:+.2f} EV"
        gam_txt = f"Gam: {self.gamma:.2f}"
        chan_names = {0: "RGB", 1: "R", 2: "G", 3: "B", 4: "A", 5: "Y"}
        chan_txt = chan_names.get(self.channel_mode, "RGB")
        right_txt = f"{exp_txt}  |  {gam_txt}  |  Chan: {chan_txt}  "
        painter.setPen(QColor(180, 180, 180))
        painter.drawText(vw * 2 // 3, 0, vw // 3, strip_h, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, right_txt)

        painter.restore()

    def set_show_info_strip(self, show: bool) -> None:
        self.show_info_strip = bool(show)
        self.update()

    def toggle_info_strip(self) -> None:
        self.show_info_strip = not self.show_info_strip
        self.update()

    def update_info_strip_data(self, **kwargs) -> None:
        self.info_strip_data.update(kwargs)
        if self.show_info_strip:
            self.update()

    # --------------------------------------------------------------------------
    # Safe Guides & Broadcast Overlays
    # --------------------------------------------------------------------------
    def set_show_safe_guides(self, show: bool) -> None:
        self.show_safe_guides = bool(show)
        self.update()

    def toggle_safe_guides(self) -> None:
        self.show_safe_guides = not self.show_safe_guides
        status = "ON" if self.show_safe_guides else "OFF"
        self.show_toast(f"Safe Area Guides: {status}")
        self.update()

    def set_safe_guides_matte_constrained(self, constrained: bool) -> None:
        self.safe_guides_matte_constrained = bool(constrained)
        self.update()

    def toggle_safe_guides_matte_constraint(self) -> None:
        self.safe_guides_matte_constrained = not self.safe_guides_matte_constrained
        status = "Constrained to Matte" if self.safe_guides_matte_constrained else "Full Image Frame"
        self.show_toast(f"Safe Guides: {status}")
        self.update()

    def _paint_safe_guides(self, painter: QPainter) -> None:
        tl = self._image_norm_to_screen(0.0, 0.0)
        tr = self._image_norm_to_screen(1.0, 0.0)
        bl = self._image_norm_to_screen(0.0, 1.0)
        br = self._image_norm_to_screen(1.0, 1.0)
        left = min(tl.x(), tr.x(), bl.x(), br.x())
        right = max(tl.x(), tr.x(), bl.x(), br.x())
        top = min(tl.y(), tr.y(), bl.y(), br.y())
        bottom = max(tl.y(), tr.y(), bl.y(), br.y())

        img_w = right - left
        img_h = bottom - top
        if img_w <= 10 or img_h <= 10:
            return

        # Constrain to active matte aperture if matte is active and option is enabled
        if self.safe_guides_matte_constrained and self.matte_ratio is not None and self.matte_ratio > 0.0:
            target_ar = self.matte_ratio
            current_ar = img_w / img_h
            if target_ar > current_ar:
                # Letterbox: top & bottom bars
                active_h = img_w / target_ar
                bar_h = (img_h - active_h) / 2.0
                top += bar_h
                bottom -= bar_h
                img_h = bottom - top
            else:
                # Pillarbox: left & right bars
                active_w = img_h * target_ar
                bar_w = (img_w - active_w) / 2.0
                left += bar_w
                right -= bar_w
                img_w = right - left

        if img_w <= 10 or img_h <= 10:
            return

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        center_x = (left + right) / 2.0
        center_y = (top + bottom) / 2.0

        # 1. 90% Action Safe (Subtle Cyan dashed box)
        if self.show_action_safe:
            margin_x = img_w * 0.05
            margin_y = img_h * 0.05
            pen_action = QPen(QColor(0, 210, 255, 140), 1, Qt.PenStyle.DashLine)
            painter.setPen(pen_action)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(QRectF(left + margin_x, top + margin_y, img_w - 2 * margin_x, img_h - 2 * margin_y))

        # 2. 80% Title Safe (Subtle Yellow dashed box)
        if self.show_title_safe:
            margin_x = img_w * 0.10
            margin_y = img_h * 0.10
            pen_title = QPen(QColor(255, 204, 0, 140), 1, Qt.PenStyle.DotLine)
            painter.setPen(pen_title)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(QRectF(left + margin_x, top + margin_y, img_w - 2 * margin_x, img_h - 2 * margin_y))

        # 3. Center Crosshair
        if self.show_crosshair:
            pen_cross = QPen(QColor(255, 255, 255, 120), 1)
            painter.setPen(pen_cross)
            arm = min(15.0, min(img_w, img_h) * 0.04)
            painter.drawLine(QPointF(center_x - arm, center_y), QPointF(center_x + arm, center_y))
            painter.drawLine(QPointF(center_x, center_y - arm), QPointF(center_x, center_y + arm))

        # 4. Rule-of-Thirds Grid
        if self.show_thirds_grid:
            pen_grid = QPen(QColor(200, 200, 200, 80), 1, Qt.PenStyle.DashLine)
            painter.setPen(pen_grid)
            w3 = img_w / 3.0
            h3 = img_h / 3.0
            painter.drawLine(QPointF(left + w3, top), QPointF(left + w3, bottom))
            painter.drawLine(QPointF(left + 2 * w3, top), QPointF(left + 2 * w3, bottom))
            painter.drawLine(QPointF(left, top + h3), QPointF(right, top + h3))
            painter.drawLine(QPointF(left, top + 2 * h3), QPointF(right, top + 2 * h3))

        painter.restore()

    # --------------------------------------------------------------------------
    # In-Canvas HUD Toast System
    # --------------------------------------------------------------------------
    def show_toast(self, message: str, duration_ms: int = 1800) -> None:
        """Display a temporary translucent notification banner on the viewport."""
        self._toast_message = str(message)
        if self._toast_timer is None:
            self._toast_timer = QTimer(self)
            self._toast_timer.setSingleShot(True)
            self._toast_timer.timeout.connect(self._hide_toast)
        self._toast_timer.stop()
        self._toast_timer.start(duration_ms)
        self.update()

    def _hide_toast(self) -> None:
        self._toast_message = None
        self.update()

    def _paint_hud_toast(self, painter: QPainter) -> None:
        if not self._toast_message:
            return

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        font = QFont("Segoe UI", 11, QFont.Weight.DemiBold)
        painter.setFont(font)
        fm = QFontMetrics(font)
        text_w = fm.horizontalAdvance(self._toast_message)
        text_h = fm.height()

        padding_x = 20
        padding_y = 8
        rect_w = text_w + padding_x * 2
        rect_h = text_h + padding_y * 2
        x = (self.width() - rect_w) / 2.0
        y = 36.0

        bg_rect = QRectF(x, y, rect_w, rect_h)
        painter.setBrush(QColor(20, 20, 20, 220))
        painter.setPen(QPen(QColor(229, 168, 35, 200), 1.5))
        painter.drawRoundedRect(bg_rect, 6, 6)

        painter.setPen(QColor(240, 240, 240))
        painter.drawText(bg_rect, Qt.AlignmentFlag.AlignCenter, self._toast_message)
        painter.restore()

    def _paint_drag_drop_overlay(self, painter: QPainter) -> None:
        """Render horizontal split overlay (Top: Add Media to stack, Bottom: Add to source bin)."""
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        vw = self.width()
        vh = self.height()

        # Canvas veil dimming background
        painter.fillRect(self.rect(), QColor(0, 0, 0, 140))

        margin_x = max(24.0, vw * 0.05)
        margin_y = max(20.0, vh * 0.05)
        gap = 16.0

        card_w = max(120.0, vw - 2.0 * margin_x)
        card_h = max(60.0, (vh - 2.0 * margin_y - gap) / 2.0)

        top_rect = QRectF(margin_x, margin_y, card_w, card_h)
        bottom_rect = QRectF(margin_x, margin_y + card_h + gap, card_w, card_h)
        corner_radius = 12.0

        is_top_active = (self._drag_target == "stack")
        is_bottom_active = (self._drag_target == "bin")

        font_size = max(13, min(22, int(card_h * 0.16)))
        font_title = QFont("Segoe UI", font_size, QFont.Weight.DemiBold)
        painter.setFont(font_title)

        # 1. Top Card: Add Media to the stack
        if is_top_active:
            painter.setBrush(QColor(36, 32, 22, 240))
            painter.setPen(QPen(QColor(245, 158, 11, 255), 2.0))
        else:
            painter.setBrush(QColor(22, 22, 22, 215))
            painter.setPen(QPen(QColor(85, 85, 85, 180), 1.2))
        painter.drawRoundedRect(top_rect, corner_radius, corner_radius)

        painter.setPen(QColor(255, 255, 255, 255) if is_top_active else QColor(190, 190, 190, 210))
        painter.drawText(top_rect, Qt.AlignmentFlag.AlignCenter, "Add Media to the stack")

        # 2. Bottom Card: Just add the media to source bin
        if is_bottom_active:
            painter.setBrush(QColor(36, 32, 22, 240))
            painter.setPen(QPen(QColor(245, 158, 11, 255), 2.0))
        else:
            painter.setBrush(QColor(22, 22, 22, 215))
            painter.setPen(QPen(QColor(85, 85, 85, 180), 1.2))
        painter.drawRoundedRect(bottom_rect, corner_radius, corner_radius)

        painter.setPen(QColor(255, 255, 255, 255) if is_bottom_active else QColor(190, 190, 190, 210))
        painter.drawText(bottom_rect, Qt.AlignmentFlag.AlignCenter, "Just add the media to source bin")

        painter.restore()

    # --------------------------------------------------------------------------
    # Right-Click Context Menu
    # --------------------------------------------------------------------------
    def _create_context_menu(self) -> QMenu:
        parent_w = self.window() if self.window() else self
        is_fullscreen = (
            self.isFullScreen()
            or (self.window() is not None and self.window().isFullScreen())
            or getattr(self, "is_presentation_mode", False)
        )

        menu = QMenu(parent_w)
        self._last_context_menu = menu

        menu.setStyleSheet("""
            QMenu {
                background-color: #242424;
                color: #e0e0e0;
                border: 1px solid #3c3c3c;
            }
            QMenu::item:selected {
                background-color: #e5a823;
                color: #121212;
            }
        """)

        # Framing
        act_fit = menu.addAction("Fit View (F)")
        act_fit.triggered.connect(self.fit_image)
        act_100 = menu.addAction("Actual Size (100% / 1)")
        act_100.triggered.connect(self.set_zoom_100)
        act_fit_w = menu.addAction("Fit Width (ctrl+f)")
        act_fit_w.triggered.connect(self.fit_width)

        # Scale Presets
        scale_menu = menu.addMenu("Scale")
        for label, factor in [("1:1 (Ctrl+1)", 1.0), ("2:1 (Ctrl+2)", 2.0), ("4:1 (Ctrl+4)", 4.0), ("8:1 (Ctrl+8)", 8.0)]:
            act = scale_menu.addAction(label)
            act.triggered.connect(lambda checked=False, f=factor: self.set_scale_preset(f))

        menu.addSeparator()

        # Flop / Flip
        act_flop = menu.addAction("Flop Horizontal (Shift+X)")
        act_flop.setCheckable(True)
        act_flop.setChecked(self.flip_x)
        act_flop.triggered.connect(self.toggle_flop_x)

        act_flip = menu.addAction("Flip Vertical (Shift+Y)")
        act_flip.setCheckable(True)
        act_flip.setChecked(self.flip_y)
        act_flip.triggered.connect(self.toggle_flip_y)

        # Rotation
        rot_menu = menu.addMenu("Rotation")
        for deg in [0, 90, 180, 270]:
            act = rot_menu.addAction(f"{deg}°" if deg != 0 else "Reset Rotation (0°)")
            act.setCheckable(True)
            if self.rotation == deg:
                act.setChecked(True)
            act.triggered.connect(lambda checked=False, d=deg: self.set_rotation(d))

        # Pixel Aspect Ratio
        par_menu = menu.addMenu("Pixel Aspect Ratio")
        for label, par_val in [
            ("Square (1.0)", 1.0),
            ("NTSC 4:3 (0.91)", 0.912),
            ("NTSC 16:9 (1.21)", 1.212),
            ("PAL 4:3 (1.09)", 1.093),
            ("PAL 16:9 (1.46)", 1.457),
            ("Anamorphic 2:1 (2.0)", 2.0),
        ]:
            act = par_menu.addAction(label)
            act.setCheckable(True)
            if abs(self.pixel_aspect_ratio - par_val) < 0.01:
                act.setChecked(True)
            act.triggered.connect(lambda checked=False, p=par_val: self.set_pixel_aspect_ratio(p))

        menu.addSeparator()

        # Mattes
        matte_menu = menu.addMenu("Matte Overlay")
        for label, m_ratio in [
            ("No Matte", None),
            ("1.33:1 (4:3)", 1.333),
            ("1.66:1", 1.666),
            ("1.77:1 (16:9)", 1.777),
            ("1.85:1 (Flat)", 1.85),
            ("2.35:1 (Scope)", 2.35),
            ("2.40:1", 2.40),
        ]:
            act = matte_menu.addAction(label)
            act.setCheckable(True)
            if (self.matte_ratio is None and m_ratio is None) or (self.matte_ratio is not None and m_ratio is not None and abs(self.matte_ratio - m_ratio) < 0.01):
                act.setChecked(True)
            act.triggered.connect(lambda checked=False, r=m_ratio: self.set_matte(r))

        # Background
        bg_menu = menu.addMenu("Background")
        for label, bg_val in [
            ("Solid Black", "black"),
            ("18% Grey", "grey18"),
            ("50% Grey", "grey50"),
            ("White", "white"),
            ("Checkerboard", "checker"),
        ]:
            act = bg_menu.addAction(label)
            act.setCheckable(True)
            if self.background_mode == bg_val:
                act.setChecked(True)
            act.triggered.connect(lambda checked=False, b=bg_val: self.set_background_mode(b))

        # Presentation Mode & Fullscreen Controls
        menu.addSeparator()
        if self.is_presentation_mode:
            act_pres = menu.addAction("Exit Presentation Mode (Esc)")
            act_pres.triggered.connect(self.exit_presentation_requested.emit)
        elif is_fullscreen:
            act_fs = menu.addAction("Exit Fullscreen (F12 / Esc)")
            act_fs.triggered.connect(self.exit_fullscreen_requested.emit)
            act_pres = menu.addAction("Presentation Mode (ctrl+p)")
            act_pres.triggered.connect(self.toggle_presentation_requested.emit)
        else:
            act_fs = menu.addAction("Fullscreen (F12)")
            act_fs.triggered.connect(self.toggle_fullscreen_requested.emit)
            act_pres = menu.addAction("Presentation Mode (ctrl+p)")
            act_pres.triggered.connect(self.toggle_presentation_requested.emit)

        return menu

    def _show_context_menu(self, global_pos: Optional[QPoint] = None) -> None:
        now = time.perf_counter()
        if now - getattr(self, "_last_context_menu_time", 0.0) < 0.35:
            return
        self._last_context_menu_time = now

        menu = self._create_context_menu()
        pos_to_use = (
            global_pos
            if (isinstance(global_pos, QPoint) and not global_pos.isNull())
            else QCursor.pos()
        )
        menu.exec(pos_to_use)

    def contextMenuEvent(self, event) -> None:
        self._show_context_menu(event.globalPos())
        event.accept()

