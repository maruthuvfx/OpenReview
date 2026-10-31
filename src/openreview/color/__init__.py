"""
Color Management and OpenColorIO v2 Integration.
"""
from openreview.color.ocio_manager import OCIOManager
from openreview.color.shader_builder import build_fragment_shader, ShaderUniforms
from openreview.color.standard_colorspaces import (
    STANDARD_COLORSPACES,
    COLORSPACE_BY_ID,
    COLORSPACE_BY_NAME,
    StandardColorSpaceInfo,
    apply_standard_to_linear,
    apply_linear_to_standard,
    detect_media_input_colorspace,
    map_standard_to_ocio_space,
)

__all__ = [
    "OCIOManager",
    "build_fragment_shader",
    "ShaderUniforms",
    "STANDARD_COLORSPACES",
    "COLORSPACE_BY_ID",
    "COLORSPACE_BY_NAME",
    "StandardColorSpaceInfo",
    "apply_standard_to_linear",
    "apply_linear_to_standard",
    "detect_media_input_colorspace",
    "map_standard_to_ocio_space",
]
