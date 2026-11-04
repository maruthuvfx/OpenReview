"""
GLSL Shader Generator for Viewport OpenGL Rendering.
Combines hardware texture sampling, A/B wipe comparison, exposure, gamma,
channel isolation, input color space linearization, and display view transforms.
"""
import re
from dataclasses import dataclass
from typing import Optional


@dataclass
class ShaderUniforms:
    exposure_ev: float = 0.0
    gamma: float = 1.0
    channel_mode: int = 0      # 0: RGB, 1: R, 2: G, 3: B, 4: A, 5: Luma (Y)
    comparison_mode: int = 0   # 0: Off, 1: Wipe, 2: Split, 3: Difference
    wipe_position: float = 0.5 # 0.0 to 1.0
    wipe_angle_rad: float = 1.5707963  # 90 degrees (vertical wipe line)
    diff_boost: float = 1.0
    input_space: int = 0       # 0: linear, 1: sRGB, 2: rec709, 3: Cineon, 4: Gamma 1.8, 5: Gamma 2.2, 6: Panalog, 7: REDLog, 8: ViperLog, 9: REDSpace, 10: Raw
    view_transform: int = 1    # 0: linear, 1: sRGB, 2: rec709, 3: Cineon, 4: Gamma 1.8, 5: Gamma 2.2, 6: Panalog, 7: REDLog, 8: ViperLog, 9: REDSpace, 10: Raw

    # Comp QC Toolset Uniforms
    qc_mode: int = 0
    qc_clipping_threshold: float = 50.0
    qc_negative_over_comp: bool = False
    qc_saturation_boost: float = 4.0
    qc_highpass_radius: float = 5.0
    qc_clamp_values: bool = False
    qc_check_clamped_alpha: bool = False
    qc_alpha_mask_enabled: bool = False
    qc_alpha_mask_color: tuple[float, float, float, float] = (0.86, 0.14, 0.14, 0.5)
    qc_alpha_mask_invert: bool = False
    qc_flop_comp: bool = False
    qc_hsl_mode: int = 0
    qc_grain_compare_mode: int = 0
    qc_grain_boost: float = 5.0

    # xtra QC Utils Uniforms
    qc_gamma: float = 1.0
    qc_gain: float = 1.0
    qc_softclip: float = 0.0
    qc_sat_multiplier: float = 1.0
    qc_edge_threshold: float = 0.0
    qc_compare_against_plate: bool = False
    qc_compare_plates: bool = False
    qc_matte_enabled: bool = False
    qc_matte_channels: tuple[float, float, float, float] = (1.0, 1.0, 1.0, 1.0)
    qc_mix_overlay: float = 1.0
    qc_overlay_matte: bool = False
    qc_greycheck_enabled: bool = False
    qc_checkerboard_enabled: bool = False
    qc_grey_value: float = 0.5
    qc_grid_scale: float = 32.0
    qc_checker_contrast: float = 0.2
    qc_hipass_value: float = 3.0
    current_frame: float = 1.0


VERTEX_SHADER_SOURCE = """#version 330 core
layout (location = 0) in vec2 aPos;
layout (location = 1) in vec2 aTexCoord;

uniform mat4 uTransform;

out vec2 TexCoord;

void main()
{
    gl_Position = uTransform * vec4(aPos, 0.0, 1.0);
    TexCoord = aTexCoord;
}
"""

FRAGMENT_SHADER_SOURCE = """#version 330 core
in vec2 TexCoord;
out vec4 FragColor;

uniform sampler2D uTextureA;        // Primary media (A)
uniform sampler2D uTextureB;        // Reference media (B)
uniform bool uHasTextureB;

uniform float uExposure;            // Exposure in EV stops (linear gain = 2^uExposure)
uniform float uGain;                // Linear Gain multiplier (Foundry Nuke style, default 1.0)
uniform float uTotalGain;           // Precomputed fast gain = uGain * 2^uExposure
uniform float uSaturation;          // Linear Saturation multiplier (Foundry Nuke style, default 1.0)
uniform bool uApplySat;             // True if saturation != 1.0
uniform float uGamma;               // Display gamma (inverse power)
uniform float uGammaPower;          // Precomputed fast gamma power = 1.0 / max(0.001, abs(uGamma))
uniform bool uApplyGamma;           // True if gamma != 1.0
uniform int uChannelMode;           // 0: RGB, 1: R, 2: G, 3: B, 4: A, 5: Luma Y
uniform mat4 uChannelMatrix;        // 4x4 color matrix for channel swizzling / routing
uniform vec4 uChannelEnable;        // R, G, B, A channel enable masks (default vec4(1.0))
uniform int uChannelSoloMode;       // 0: Color Solo, 1: Greyscale Solo, 2: Normals Remap [-1, 1]->[0, 1]

// Colorspace & Viewer Uniforms
// Standard Colorspaces: 0: linear, 1: sRGB, 2: rec709, 3: Cineon, 4: Gamma 1.8, 5: Gamma 2.2, 6: Panalog, 7: REDLog, 8: ViperLog, 9: REDSpace, 10: Raw
uniform int uInputSpace;
uniform int uViewTransform;

// Stereo 3D Uniforms: 0: Off, 1: Left, 2: Right, 3: Anaglyph, 4: Side-by-Side, 5: Difference
uniform int uStereoMode;

// A/B Comparison Uniforms
uniform int uComparisonMode;        // 0: Off, 1: Wipe, 2: Split, 3: Difference
uniform float uWipePos;             // 0.0 to 1.0
uniform vec2 uWipeCenter;           // Center/pivot point of the wipe divider line (0.0 to 1.0)
uniform float uWipeAngle;           // Angle in radians
uniform float uAspect;              // Media aspect ratio (width / height)
uniform float uDiffBoost;           // Multiplier for difference mode
uniform float uStackMix;            // Onion skin mix (0.0 to 1.0)
uniform vec4 uLayerAChannels;       // Active channels for Layer A (x=R, y=G, z=B, w=A)
uniform bool uWipeShowLine;         // Render anti-aliased divider overlay line
uniform bool uDiffHeatmap;          // Render Turbo colormap for difference mode
uniform float uSpotlightRadius;     // Radius for spotlight inspection mode (default 0.25)

// ASC CDL Uniforms
uniform bool uHasCdl;
uniform vec3 uCdlSlope;
uniform vec3 uCdlOffset;
uniform vec3 uCdlPower;
uniform float uCdlSat;

// 3D LUT Uniforms
uniform sampler3D uLut3D;
uniform bool uHasLut3D;

// OCIO v2 Fast-Path 3D LUT Uniforms (Texture Unit 4)
uniform sampler3D uOcioLut3D;
uniform bool uHasOcioLut3D;
uniform int uOcioShaperMode;        // 0: Linear [0, 1], 1: Log2 [-10, 10] EV, 2: Rational
uniform float uOcioMinEv;           // -10.0
uniform float uOcioMaxEv;           // 10.0

// Interactive Grading Uniforms
uniform vec3 uGradingGain;
uniform vec3 uGradingOffset;
uniform float uGradingGamma;
uniform float uGradingContrast;
uniform float uGradingSat;
uniform float uGradingHue;
uniform float uDisplayBrightness;
uniform bool uInvertColors;
uniform bool uShowOutOfRange;

// Comp QC Toolset Uniforms
uniform int uQcMode;                // 0: Off, 1: Log, 2: NaN/Inf, 3: HP Comp, 4: HP Plate, 5: Clip, 6: Neg, 7: Grain, 8: Comp Diff Plate, 9: Sat Boost, 10: Clamped Alpha
uniform float uQcClippingThreshold; // Default 50.0
uniform bool uQcNegativeOverComp;   // Show pink over comp vs black
uniform float uQcSaturationBoost;   // Default 4.0
uniform float uQcHighpassRadius;    // Default 5.0
uniform bool uQcClampValues;        // Hard clamp plate values to [0.0, 1.0]
uniform bool uQcCheckClampedAlpha;  // Highlight alpha <0.0 in Cyan, >1.0 in Magenta
uniform bool uQcAlphaMaskEnabled;   // Enable embedded alpha mask
uniform vec4 uQcAlphaMaskColor;     // Mask overlay color & opacity
uniform bool uQcAlphaMaskInvert;    // Invert mask
uniform bool uQcFlopComp;           // Horizontal flip
uniform int uQcHslMode;             // 0: Off, 1: Full HSL, 2: L, 3: S, 4: H
uniform int uQcGrainCompareMode;    // 0: Comp, 1: Plate, 2: Diff
uniform float uQcGrainBoost;        // Default 5.0

// xtra QC Utils Uniforms
uniform float uQcGamma;             // Default 1.0
uniform float uQcGain;              // Default 1.0
uniform float uQcSoftclip;          // Default 0.0 (0.0: off)
uniform float uQcSatMultiplier;     // Default 1.0
uniform float uQcHueRotation;       // Default 0.0 (-180.0 to 180.0 degrees)
uniform float uQcEdgeThreshold;     // Default 0.0
uniform bool uQcCompareAgainstPlate;// Default false
uniform bool uQcComparePlates;      // Side-by-side compare
uniform bool uQcMatteEnabled;       // Default false
uniform sampler2D uTextureMatte;    // External RGBA matte
uniform bool uHasTextureMatte;      // True if external matte bound
uniform vec4 uQcMatteChannels;      // Enable mask: x=R, y=G, z=B, w=A
uniform float uQcMixOverlay;        // Default 1.0
uniform bool uQcOverlayMatte;       // Composite RGBA matte over comp
uniform bool uQcMatteIsPremult;     // Loaded matte is premultiplied
uniform bool uQcGreycheckEnabled;   // Grey background evaluation
uniform bool uQcCheckerboardEnabled;// Checkerboard background evaluation
uniform float uQcGreyValue;         // Grey background level (0.0 to 1.0)
uniform float uQcGridScale;         // Checkerboard scale
uniform float uQcCheckerContrast;   // Checkerboard contrast
uniform float uQcHipassValue;       // Highpass value (0.0 to 10.0)
uniform float uCurrentFrame;        // Timeline frame number for animated expressions

// sRGB to linear conversion
vec3 srgb_to_linear(vec3 c) {
    vec3 higher = pow(max((c + vec3(0.055)) / 1.055, vec3(0.0)), vec3(2.4));
    vec3 lower = c / 12.92;
    return mix(lower, higher, greaterThan(c, vec3(0.04045)));
}

// Cineon Log conversion (scene-linear to Cineon)
vec3 linear_to_cineon(vec3 c) {
    vec3 clamped = max(c, vec3(0.00001));
    vec3 x = log(clamped * 0.989202 + 0.010798) / 2.302585092994046; // log10
    return clamp((x * 300.0 + 685.0) / 1023.0, 0.0, 1.0);
}

// RGB to HSL conversion
vec3 rgb2hsl(vec3 c) {
    float maxc = max(max(c.r, c.g), c.b);
    float minc = min(min(c.r, c.g), c.b);
    float delta = maxc - minc;
    float l = (maxc + minc) * 0.5;
    float s = 0.0;
    if (delta > 0.00001) {
        s = delta / (1.0 - abs(2.0 * l - 1.0) + 0.00001);
    }
    float h = 0.0;
    if (delta > 0.00001) {
        if (maxc == c.r) {
            h = mod((c.g - c.b) / delta, 6.0);
        } else if (maxc == c.g) {
            h = ((c.b - c.r) / delta) + 2.0;
        } else {
            h = ((c.r - c.g) / delta) + 4.0;
        }
        h = h / 6.0;
        if (h < 0.0) h += 1.0;
    }
    return vec3(h, clamp(s, 0.0, 1.0), clamp(l, 0.0, 1.0));
}

// Scene-linear to display sRGB transfer
vec3 linear_to_srgb(vec3 c) {
    vec3 higher = 1.055 * pow(max(c, vec3(0.0)), vec3(1.0 / 2.4)) - 0.055;
    vec3 lower = 12.92 * c;
    return mix(lower, higher, greaterThan(c, vec3(0.0031308)));
}

// Cineon / DPX Log to Scene-Linear
vec3 cineon_to_linear(vec3 c) {
    vec3 x = (c * 1023.0 - 685.0) / 300.0;
    return max(vec3(0.0), (pow(vec3(10.0), x) - 0.010798) / 0.989202);
}

// ARRI ALEXA LogC v3 EI800 to Scene-Linear
vec3 alexa_logc_to_linear(vec3 x) {
    float cut = 0.1496582;
    float a = 0.247190;
    float b = 0.385537;
    float c = 0.078277;
    float d = 0.105910;
    float e = 5.253018;
    float f = 0.0400405;
    vec3 lin;
    for (int i = 0; i < 3; ++i) {
        if (x[i] > cut) {
            lin[i] = (pow(10.0, (x[i] - b) / a) - d) / c;
        } else {
            lin[i] = (x[i] - f) / e;
        }
    }
    return max(lin, vec3(0.0));
}

// ALEXA LogC Film to Linear (LogC + soft film contrast)
vec3 alexa_logc_film_to_linear(vec3 x) {
    vec3 lin = alexa_logc_to_linear(x);
    return pow(lin, vec3(1.15)) * 1.1;
}

// Rec.709 OETF to Scene-Linear
vec3 rec709_to_linear(vec3 c) {
    vec3 higher = pow(max((c + vec3(0.099)) / 1.099, vec3(0.0)), vec3(1.0 / 0.45));
    vec3 lower = c / 4.5;
    return mix(lower, higher, greaterThan(c, vec3(0.081)));
}

// Scene-Linear to Rec.709 Display
vec3 linear_to_rec709(vec3 c) {
    vec3 higher = 1.099 * pow(max(c, vec3(0.0)), vec3(0.45)) - 0.099;
    vec3 lower = 4.5 * c;
    return mix(lower, higher, greaterThan(c, vec3(0.018)));
}

// Gamma 1.8 to Scene-Linear
vec3 gamma18_to_linear(vec3 c) {
    return pow(max(c, vec3(0.0)), vec3(1.8));
}

// Scene-Linear to Gamma 1.8 Display
vec3 linear_to_gamma18(vec3 c) {
    return pow(max(c, vec3(0.0)), vec3(1.0 / 1.8));
}

// Gamma 2.2 to Scene-Linear
vec3 gamma22_to_linear(vec3 c) {
    return pow(max(c, vec3(0.0)), vec3(2.2));
}

// Scene-Linear to Gamma 2.2 Display
vec3 linear_to_gamma22(vec3 c) {
    return pow(max(c, vec3(0.0)), vec3(1.0 / 2.2));
}

// Panalog to Scene-Linear
vec3 panalog_to_linear(vec3 c) {
    vec3 x = (c * 1023.0 - 681.0) / 444.0;
    return max(vec3(0.0), (pow(vec3(10.0), x) - 0.002) / 0.998);
}

// Scene-Linear to Panalog Display
vec3 linear_to_panalog(vec3 lin) {
    vec3 norm = max(lin * 0.998 + 0.002, vec3(1e-6));
    vec3 x = log(norm) / 2.302585092994046;
    return clamp((x * 444.0 + 681.0) / 1023.0, 0.0, 1.0);
}

// REDLog to Scene-Linear
vec3 redlog_to_linear(vec3 c) {
    return max(vec3(0.0), (pow(vec3(10.0), c * 3.0 - 3.0) - 0.00001) / 0.99999);
}

// Scene-Linear to REDLog Display
vec3 linear_to_redlog(vec3 lin) {
    vec3 norm = max(lin * 0.99999 + 0.00001, vec3(1e-6));
    vec3 x = log(norm) / 2.302585092994046;
    return clamp((x + 3.0) / 3.0, 0.0, 1.0);
}

// ViperLog to Scene-Linear
vec3 viperlog_to_linear(vec3 c) {
    vec3 x = (c * 1023.0 - 1023.0) / 500.0;
    return max(vec3(0.0), (pow(vec3(10.0), x) - 0.01) / 0.99);
}

// Scene-Linear to ViperLog Display
vec3 linear_to_viperlog(vec3 lin) {
    vec3 norm = max(lin * 0.99 + 0.01, vec3(1e-6));
    vec3 x = log(norm) / 2.302585092994046;
    return clamp((x * 500.0 + 1023.0) / 1023.0, 0.0, 1.0);
}

// REDSpace 3x3 Conversion Matrices (column-major in GLSL)
const mat3 REDSPACE_TO_REC709_MAT = mat3(
    1.478, -0.063, -0.010,
    -0.389, 1.077, -0.168,
    -0.089, -0.014, 1.178
);

const mat3 REC709_TO_REDSPACE_MAT = mat3(
    0.6865, 0.0405, 0.0116,
    0.2541, 0.9490, 0.1376,
    0.0594, 0.0105, 0.8508
);

vec3 redspace_to_linear(vec3 c) {
    return REDSPACE_TO_REC709_MAT * c;
}

vec3 linear_to_redspace(vec3 lin) {
    return REC709_TO_REDSPACE_MAT * lin;
}

// Narkowicz ACES filmic tone mapping fit for HDR
vec3 aces_filmic(vec3 x) {
    float a = 2.51;
    float b = 0.03;
    float c = 2.43;
    float d = 0.59;
    float e = 0.14;
    return clamp((x * (a * x + b)) / (x * (c * x + d) + e), 0.0, 1.0);
}

// RGB <-> HSV color utilities for Hue adjustment
vec3 rgb2hsv(vec3 c) {
    vec4 K = vec4(0.0, -1.0 / 3.0, 2.0 / 3.0, -1.0);
    vec4 p = mix(vec4(c.bg, K.wz), vec4(c.gb, K.xy), step(c.b, c.g));
    vec4 q = mix(vec4(p.xyw, c.r), vec4(c.r, p.yzx), step(p.x, c.r));
    float d = q.x - min(q.w, q.y);
    float e = 1.0e-10;
    return vec3(abs(q.z + (q.w - q.y) / (6.0 * d + e)), d / (q.x + e), q.x);
}

vec3 hsv2rgb(vec3 c) {
    vec4 K = vec4(1.0, 2.0 / 3.0, 1.0 / 3.0, 3.0);
    vec3 p = abs(fract(c.xxx + K.xyz) * 6.0 - K.www);
    return c.z * mix(K.xxx, clamp(p - K.xxx, 0.0, 1.0), c.y);
}

// Convert input color sample to scene-linear space
// Google Turbo Colormap GLSL Approximation
vec3 turbo_colormap(float x) {
    x = clamp(x, 0.0, 1.0);
    const vec4 kRedVec4 = vec4(0.13572138, 4.61539260, -42.66032258, 132.13108234);
    const vec4 kGreenVec4 = vec4(0.09140261, 2.19418839, 4.84296658, -14.18503333);
    const vec4 kBlueVec4 = vec4(0.10667330, 12.64194608, -60.58204836, 110.36276771);
    const vec2 kRedVec2 = vec2(-152.94239396, 59.28637943);
    const vec2 kGreenVec2 = vec2(4.27729857, 2.82956604);
    const vec2 kBlueVec2 = vec2(-89.90310912, 27.34824973);

    vec4 v4 = vec4(1.0, x, x * x, x * x * x);
    vec2 v2 = v4.zw * v4.z;

    return clamp(vec3(
        dot(v4, kRedVec4) + dot(v2, kRedVec2),
        dot(v4, kGreenVec4) + dot(v2, kGreenVec2),
        dot(v4, kBlueVec4) + dot(v2, kBlueVec2)
    ), 0.0, 1.0);
}

vec3 to_linear_space(vec3 c) {
    if (uInputSpace == 0) {
        return c; // linear (working space)
    } else if (uInputSpace == 1) {
        return srgb_to_linear(c);
    } else if (uInputSpace == 2) {
        return rec709_to_linear(c);
    } else if (uInputSpace == 3) {
        return cineon_to_linear(c);
    } else if (uInputSpace == 4) {
        return gamma18_to_linear(c);
    } else if (uInputSpace == 5) {
        return gamma22_to_linear(c);
    } else if (uInputSpace == 6) {
        return panalog_to_linear(c);
    } else if (uInputSpace == 7) {
        return redlog_to_linear(c);
    } else if (uInputSpace == 8) {
        return viperlog_to_linear(c);
    } else if (uInputSpace == 9) {
        return redspace_to_linear(c);
    } else if (uInputSpace == 10) {
        return c; // Raw / Pass-through
    } else if (uInputSpace == 101) {
        return alexa_logc_to_linear(c);
    } else if (uInputSpace == 102) {
        return alexa_logc_film_to_linear(c);
    }
    return c;
}

// Convert scene-linear color to display space
vec3 to_display_space(vec3 lin) {
    if (uViewTransform == 0) {
        return lin; // linear (identity)
    } else if (uViewTransform == 1) {
        return linear_to_srgb(lin);
    } else if (uViewTransform == 2) {
        return linear_to_rec709(lin);
    } else if (uViewTransform == 3) {
        return linear_to_cineon(lin);
    } else if (uViewTransform == 4) {
        return linear_to_gamma18(lin);
    } else if (uViewTransform == 5) {
        return linear_to_gamma22(lin);
    } else if (uViewTransform == 6) {
        return linear_to_panalog(lin);
    } else if (uViewTransform == 7) {
        return linear_to_redlog(lin);
    } else if (uViewTransform == 8) {
        return linear_to_viperlog(lin);
    } else if (uViewTransform == 9) {
        return linear_to_redspace(lin);
    } else if (uViewTransform == 10) {
        return lin; // Raw / Pass-through
    } else if (uViewTransform == 100) {
        return linear_to_srgb(aces_filmic(lin)); // ACES filmic
    }
    return lin;
}

// Sample Layer A and isolate active channels (affects ONLY Layer A)
vec4 sample_layer_a(vec2 coord) {
    vec4 col = texture(uTextureA, coord);
    col.r = (uLayerAChannels.x > 0.5) ? col.r : 0.0;
    col.g = (uLayerAChannels.y > 0.5) ? col.g : 0.0;
    col.b = (uLayerAChannels.z > 0.5) ? col.b : 0.0;
    col.a = (uLayerAChannels.w > 0.5) ? col.a : 1.0;
    return col;
}

// Evaluate matte channel expressions (affects ONLY the RGBA matte layer)
// __EVAL_MATTE_CHANNELS_PLACEHOLDER__
vec4 eval_matte_channels(vec4 mRaw, vec2 sCoord) {
    return mRaw;
}

void main()
{
    vec2 tc = TexCoord;
    if (uQcFlopComp) {
        tc.x = 1.0 - tc.x;
    }

    bool isRightPlate = (uQcComparePlates && uHasTextureB && tc.x >= 0.5);
    vec2 sCoord = tc;
    if (uQcComparePlates && uHasTextureB) {
        sCoord = isRightPlate ? vec2((tc.x - 0.5) * 2.0, tc.y) : vec2(tc.x * 2.0, tc.y);
    }

    vec3 linearRgb = vec3(0.0);
    float finalAlpha = 1.0;

    // 0. Stereo 3D Display Logic (Texture A = Left Eye, Texture B = Right Eye)
    if (uStereoMode > 0) {
        vec4 colA = sample_layer_a(tc);
        vec4 colB = uHasTextureB ? texture(uTextureB, tc) : colA;
        vec3 linA = to_linear_space(colA.rgb);
        vec3 linB = to_linear_space(colB.rgb);

        if (uStereoMode == 1) {
            // Left Eye Only
            linearRgb = linA;
            finalAlpha = colA.a;
        } else if (uStereoMode == 2 && uHasTextureB) {
            // Right Eye Only
            linearRgb = linB;
            finalAlpha = colB.a;
        } else if (uStereoMode == 3 && uHasTextureB) {
            // Anaglyph Red-Cyan in linear space
            linearRgb = vec3(linA.r, linB.g, linB.b);
            finalAlpha = max(colA.a, colB.a);
        } else if (uStereoMode == 4 && uHasTextureB) {
            // Side-by-Side Stereo Split
            if (tc.x < 0.5) {
                vec4 sA = sample_layer_a(vec2(tc.x * 2.0, tc.y));
                linearRgb = to_linear_space(sA.rgb);
                finalAlpha = sA.a;
            } else {
                vec4 sB = texture(uTextureB, vec2((tc.x - 0.5) * 2.0, tc.y));
                linearRgb = to_linear_space(sB.rgb);
                finalAlpha = sB.a;
            }
        } else if (uStereoMode == 5 && uHasTextureB) {
            // Stereo Difference
            linearRgb = abs(linA - linB) * uDiffBoost;
            finalAlpha = 1.0;
        } else {
            linearRgb = linA;
            finalAlpha = colA.a;
        }
    }
    // 1. A/B Comparison Logic in Scene-Linear Space (when not in Stereo mode)
    else if (uComparisonMode == 1 && uHasTextureB) {
        // Isotropic linear wipe across angle with aspect ratio correction centered at uWipeCenter
        vec2 dir = vec2(cos(uWipeAngle), sin(uWipeAngle));
        float aspect = max(0.001, uAspect);
        float rMax = 0.5 * (aspect * abs(dir.x) + abs(dir.y));
        float p = (TexCoord.x - 0.5) * aspect * dir.x + (TexCoord.y - 0.5) * dir.y;
        float d = (tc.x - uWipeCenter.x) * aspect * dir.x + (tc.y - uWipeCenter.y) * dir.y;

        float delta = max(fwidth(d), 0.0005);
        float wipeFactor = smoothstep(-delta * 0.5, delta * 0.5, d);

        vec4 colA = sample_layer_a(tc);
        vec4 colB = texture(uTextureB, tc);
        vec3 linA = to_linear_space(colA.rgb);
        vec3 linB = to_linear_space(colB.rgb);

        linearRgb = mix(linA, linB, wipeFactor);
        finalAlpha = mix(colA.a, colB.a, wipeFactor);

        if (uWipeShowLine) {
            float distInPixels = abs(d) / delta;
            float lineMask = 1.0 - smoothstep(0.6, 1.25, distInPixels);
            float shadowMask = (1.0 - smoothstep(1.25, 3.0, distInPixels)) * 0.45;
            linearRgb = mix(linearRgb, vec3(0.0), shadowMask);
            linearRgb = mix(linearRgb, vec3(0.98), lineMask);
        }
    }
    else if (uComparisonMode == 2 && uHasTextureB) {
        // Side-by-side split: Compare Layer A and B side by side scaled 50% preserving original aspect ratio
        if (TexCoord.x < 0.5) {
            if (TexCoord.y >= 0.25 && TexCoord.y <= 0.75) {
                vec2 sampleCoord = vec2(TexCoord.x * 2.0, (TexCoord.y - 0.25) * 2.0);
                vec4 colA = sample_layer_a(sampleCoord);
                linearRgb = to_linear_space(colA.rgb);
                finalAlpha = colA.a;
            } else {
                linearRgb = vec3(0.0);
                finalAlpha = 1.0;
            }
        } else {
            if (TexCoord.y >= 0.25 && TexCoord.y <= 0.75) {
                vec2 sampleCoord = vec2((TexCoord.x - 0.5) * 2.0, (TexCoord.y - 0.25) * 2.0);
                vec4 colB = texture(uTextureB, sampleCoord);
                linearRgb = to_linear_space(colB.rgb);
                finalAlpha = colB.a;
            } else {
                linearRgb = vec3(0.0);
                finalAlpha = 1.0;
            }
        }
    }
    else if (uComparisonMode == 3 && uHasTextureB) {
        // Difference Mode in scene-linear space
        vec4 colA = sample_layer_a(tc);
        vec4 colB = texture(uTextureB, tc);
        vec3 linA = to_linear_space(colA.rgb);
        vec3 linB = to_linear_space(colB.rgb);
        vec3 diff = abs(linA - linB) * uDiffBoost;
        if (uDiffHeatmap) {
            float mag = max(diff.r, max(diff.g, diff.b));
            linearRgb = turbo_colormap(mag);
        } else {
            linearRgb = diff;
        }
        finalAlpha = 1.0;
    }
    else if (uComparisonMode == 4 && uHasTextureB) {
        // Over (Alpha Composite with Onion Skin Mix in scene-linear space)
        vec4 rawA = texture(uTextureA, tc);
        vec4 colB = texture(uTextureB, tc);
        vec3 linA = to_linear_space(rawA.rgb);
        vec3 linB = to_linear_space(colB.rgb);
        float alphaA = (uLayerAChannels.w > 0.5) ? rawA.a : 1.0;
        float effAlphaA = alphaA * uStackMix;
        vec3 mixedRgb;
        mixedRgb.r = (uLayerAChannels.x > 0.5) ? mix(linB.r, linA.r, effAlphaA) : linB.r;
        mixedRgb.g = (uLayerAChannels.y > 0.5) ? mix(linB.g, linA.g, effAlphaA) : linB.g;
        mixedRgb.b = (uLayerAChannels.z > 0.5) ? mix(linB.b, linA.b, effAlphaA) : linB.b;
        linearRgb = mixedRgb;
        finalAlpha = max(effAlphaA, colB.a);
    }
    else if (uComparisonMode == 5 && uHasTextureB) {
        // Add Mode in scene-linear space
        vec4 colA = sample_layer_a(tc);
        vec4 colB = texture(uTextureB, tc);
        vec3 linA = to_linear_space(colA.rgb);
        vec3 linB = to_linear_space(colB.rgb);
        linearRgb = (linA + linB) * uDiffBoost;
        finalAlpha = max(colA.a, colB.a);
    }
    else if (uComparisonMode == 6 && uHasTextureB) {
        // Difference (Inverted) Mode in scene-linear space
        vec4 colA = sample_layer_a(tc);
        vec4 colB = texture(uTextureB, tc);
        vec3 linA = to_linear_space(colA.rgb);
        vec3 linB = to_linear_space(colB.rgb);
        linearRgb = clamp(vec3(1.0) - abs(linA - linB) * uDiffBoost, 0.0, 1.0);
        finalAlpha = 1.0;
    }
    else if (uComparisonMode == 8 && uHasTextureB) {
        // Spotlight / Circle Inspection Wipe
        float aspect = max(0.001, uAspect);
        vec2 diffCoord = vec2((tc.x - uWipeCenter.x) * aspect, tc.y - uWipeCenter.y);
        float dist = length(diffCoord);
        float radius = max(0.005, uSpotlightRadius);
        float delta = max(fwidth(dist), 0.0005);
        float wipeFactor = smoothstep(radius - delta * 0.5, radius + delta * 0.5, dist);

        vec4 colA = sample_layer_a(tc);
        vec4 colB = texture(uTextureB, tc);
        vec3 linA = to_linear_space(colA.rgb);
        vec3 linB = to_linear_space(colB.rgb);

        // Inside circle (dist < radius): show Reference B. Outside: show Primary A
        linearRgb = mix(linB, linA, wipeFactor);
        finalAlpha = mix(colB.a, colA.a, wipeFactor);

        if (uWipeShowLine) {
            float ringDist = abs(dist - radius) / delta;
            float lineMask = 1.0 - smoothstep(0.6, 1.25, ringDist);
            float shadowMask = (1.0 - smoothstep(1.25, 3.0, ringDist)) * 0.45;
            linearRgb = mix(linearRgb, vec3(0.0), shadowMask);
            linearRgb = mix(linearRgb, vec3(0.98), lineMask);
        }
    }
    else if (uComparisonMode == 9 && uHasTextureB) {
        // Multiply Blend Mode in scene-linear space
        vec4 colA = sample_layer_a(tc);
        vec4 colB = texture(uTextureB, tc);
        vec3 linA = to_linear_space(colA.rgb);
        vec3 linB = to_linear_space(colB.rgb);
        linearRgb = linA * linB * uDiffBoost;
        finalAlpha = max(colA.a, colB.a);
    }
    else if (uComparisonMode == 10 && uHasTextureB) {
        // Screen Blend Mode in scene-linear space
        vec4 colA = sample_layer_a(tc);
        vec4 colB = texture(uTextureB, tc);
        vec3 linA = to_linear_space(colA.rgb);
        vec3 linB = to_linear_space(colB.rgb);
        linearRgb = (vec3(1.0) - (vec3(1.0) - clamp(linA, 0.0, 1.0)) * (vec3(1.0) - clamp(linB, 0.0, 1.0))) * uDiffBoost;
        finalAlpha = max(colA.a, colB.a);
    }
    else if (uComparisonMode == 11 && uHasTextureB) {
        // Overlay Blend Mode in scene-linear space
        vec4 colA = sample_layer_a(tc);
        vec4 colB = texture(uTextureB, tc);
        vec3 linA = to_linear_space(colA.rgb);
        vec3 linB = to_linear_space(colB.rgb);
        vec3 cA = clamp(linA, 0.0, 1.0);
        vec3 cB = clamp(linB, 0.0, 1.0);
        vec3 low = 2.0 * cA * cB;
        vec3 high = vec3(1.0) - 2.0 * (vec3(1.0) - cA) * (vec3(1.0) - cB);
        linearRgb = mix(low, high, step(vec3(0.5), cA)) * uDiffBoost;
        finalAlpha = max(colA.a, colB.a);
    }
    else {
        // Solo / Off Mode (isolates active channels on Layer A)
        vec4 colA = sample_layer_a(tc);
        linearRgb = to_linear_space(colA.rgb);
        finalAlpha = colA.a;
    }

    // xtra QC: Compare Plates side-by-side (Left: Comp, Right: Plate, 0 margin) or plate override
    if (uQcComparePlates && uHasTextureB) {
        if (isRightPlate) {
            vec4 cB = texture(uTextureB, sCoord);
            linearRgb = to_linear_space(cB.rgb);
            finalAlpha = cB.a;
        } else {
            vec4 cA = sample_layer_a(sCoord);
            linearRgb = to_linear_space(cA.rgb);
            finalAlpha = cA.a;
        }
    } else if (uQcCompareAgainstPlate && uHasTextureB) {
        vec4 colPlate = texture(uTextureB, tc);
        linearRgb = to_linear_space(colPlate.rgb);
        finalAlpha = colPlate.a;
    }

    // Plate Clamping: clamp linear RGB and Alpha values to [0.0, 1.0] (Left Comp only when Compare plates is on)
    if (!isRightPlate && uQcClampValues) {
        linearRgb = clamp(linearRgb, 0.0, 1.0);
        finalAlpha = clamp(finalAlpha, 0.0, 1.0);
    }

    // Comp QC High-Pass Filter Modes (applied to Left Comp and Right Plate when Compare plates is on)
    if (uQcMode == 3) {
        float rHp = (uQcHipassValue > 0.001) ? uQcHipassValue : uQcHighpassRadius;
        vec2 hpOff = vec2(max(0.5, rHp) * 0.001);
        vec3 b4;
        if (isRightPlate) {
            b4 = (texture(uTextureB, clamp(sCoord + vec2(hpOff.x, 0.0), vec2(0.0), vec2(1.0))).rgb +
                  texture(uTextureB, clamp(sCoord - vec2(hpOff.x, 0.0), vec2(0.0), vec2(1.0))).rgb +
                  texture(uTextureB, clamp(sCoord + vec2(0.0, hpOff.y), vec2(0.0), vec2(1.0))).rgb +
                  texture(uTextureB, clamp(sCoord - vec2(0.0, hpOff.y), vec2(0.0), vec2(1.0))).rgb) * 0.25;
        } else {
            b4 = (sample_layer_a(clamp(sCoord + vec2(hpOff.x, 0.0), vec2(0.0), vec2(1.0))).rgb +
                  sample_layer_a(clamp(sCoord - vec2(hpOff.x, 0.0), vec2(0.0), vec2(1.0))).rgb +
                  sample_layer_a(clamp(sCoord + vec2(0.0, hpOff.y), vec2(0.0), vec2(1.0))).rgb +
                  sample_layer_a(clamp(sCoord - vec2(0.0, hpOff.y), vec2(0.0), vec2(1.0))).rgb) * 0.25;
        }
        linearRgb = (linearRgb - to_linear_space(b4)) * 2.0 + vec3(0.5);
        if (uQcEdgeThreshold > 0.001) {
            if (length(linearRgb - vec3(0.5)) < uQcEdgeThreshold) {
                linearRgb = vec3(0.5);
            }
        }
    } else if (uQcMode == 4 && uHasTextureB) {
        vec2 hpOff = vec2(max(1.0, uQcHighpassRadius) * 0.001);
        vec4 colB = texture(uTextureB, sCoord);
        vec3 linB = to_linear_space(colB.rgb);
        vec3 b4B = (texture(uTextureB, clamp(sCoord + vec2(hpOff.x, 0.0), vec2(0.0), vec2(1.0))).rgb +
                    texture(uTextureB, clamp(sCoord - vec2(hpOff.x, 0.0), vec2(0.0), vec2(1.0))).rgb +
                    texture(uTextureB, clamp(sCoord + vec2(0.0, hpOff.y), vec2(0.0), vec2(1.0))).rgb +
                    texture(uTextureB, clamp(sCoord - vec2(0.0, hpOff.y), vec2(0.0), vec2(1.0))).rgb) * 0.25;
        linearRgb = (linB - to_linear_space(b4B)) * 2.0 + vec3(0.5);
        if (uQcEdgeThreshold > 0.001) {
            if (length(linearRgb - vec3(0.5)) < uQcEdgeThreshold) {
                linearRgb = vec3(0.5);
            }
        }
    } else if (uQcMode == 7) {
        // Grain Check Mode: difference from blurred version (applied to Left Comp and Right Plate when Compare plates is on)
        vec2 gOff = vec2(max(1.0, uQcHighpassRadius) * 0.0008);
        if (isRightPlate) {
            vec3 bB = (texture(uTextureB, clamp(sCoord + vec2(gOff.x, 0.0), vec2(0.0), vec2(1.0))).rgb +
                       texture(uTextureB, clamp(sCoord - vec2(gOff.x, 0.0), vec2(0.0), vec2(1.0))).rgb +
                       texture(uTextureB, clamp(sCoord + vec2(0.0, gOff.y), vec2(0.0), vec2(1.0))).rgb +
                       texture(uTextureB, clamp(sCoord - vec2(0.0, gOff.y), vec2(0.0), vec2(1.0))).rgb) * 0.25;
            linearRgb = abs(linearRgb - to_linear_space(bB)) * uQcGrainBoost;
        } else {
            if (uQcGrainCompareMode == 1 && uHasTextureB && !uQcComparePlates) {
                vec4 colB = texture(uTextureB, sCoord);
                vec3 linB = to_linear_space(colB.rgb);
                vec3 bB = (texture(uTextureB, clamp(sCoord + vec2(gOff.x, 0.0), vec2(0.0), vec2(1.0))).rgb +
                           texture(uTextureB, clamp(sCoord - vec2(gOff.x, 0.0), vec2(0.0), vec2(1.0))).rgb +
                           texture(uTextureB, clamp(sCoord + vec2(0.0, gOff.y), vec2(0.0), vec2(1.0))).rgb +
                           texture(uTextureB, clamp(sCoord - vec2(0.0, gOff.y), vec2(0.0), vec2(1.0))).rgb) * 0.25;
                linearRgb = abs(linB - to_linear_space(bB)) * uQcGrainBoost;
            } else if (uQcGrainCompareMode == 2 && uHasTextureB && !uQcComparePlates) {
                vec3 bA = (sample_layer_a(clamp(sCoord + vec2(gOff.x, 0.0), vec2(0.0), vec2(1.0))).rgb +
                           sample_layer_a(clamp(sCoord - vec2(gOff.x, 0.0), vec2(0.0), vec2(1.0))).rgb +
                           sample_layer_a(clamp(sCoord + vec2(0.0, gOff.y), vec2(0.0), vec2(1.0))).rgb +
                           sample_layer_a(clamp(sCoord - vec2(0.0, gOff.y), vec2(0.0), vec2(1.0))).rgb) * 0.25;
                vec3 gA = abs(linearRgb - to_linear_space(bA));

                vec4 colB = texture(uTextureB, sCoord);
                vec3 linB = to_linear_space(colB.rgb);
                vec3 bB = (texture(uTextureB, clamp(sCoord + vec2(gOff.x, 0.0), vec2(0.0), vec2(1.0))).rgb +
                           texture(uTextureB, clamp(sCoord - vec2(gOff.x, 0.0), vec2(0.0), vec2(1.0))).rgb +
                           texture(uTextureB, clamp(sCoord + vec2(0.0, gOff.y), vec2(0.0), vec2(1.0))).rgb +
                           texture(uTextureB, clamp(sCoord - vec2(0.0, gOff.y), vec2(0.0), vec2(1.0))).rgb) * 0.25;
                vec3 gB = abs(linB - to_linear_space(bB));
                linearRgb = abs(gA - gB) * uQcGrainBoost;
            } else {
                vec3 bA = (sample_layer_a(clamp(sCoord + vec2(gOff.x, 0.0), vec2(0.0), vec2(1.0))).rgb +
                           sample_layer_a(clamp(sCoord - vec2(gOff.x, 0.0), vec2(0.0), vec2(1.0))).rgb +
                           sample_layer_a(clamp(sCoord + vec2(0.0, gOff.y), vec2(0.0), vec2(1.0))).rgb +
                           sample_layer_a(clamp(sCoord - vec2(0.0, gOff.y), vec2(0.0), vec2(1.0))).rgb) * 0.25;
                linearRgb = abs(linearRgb - to_linear_space(bA)) * uQcGrainBoost;
            }
        }
    }

    // 2. Fast Exposure & Gain in Scene-Linear Space
    linearRgb = linearRgb * uTotalGain;

    // Fast Saturation in Scene-Linear Space
    if (uApplySat) {
        float luma = dot(linearRgb, vec3(0.2126, 0.7152, 0.0722));
        linearRgb = mix(vec3(luma), linearRgb, uSaturation);
    }

    // 3. ASC CDL Evaluation (SOP: Slope, Offset, Power + Saturation)
    if (uHasCdl) {
        vec3 sop = pow(max(linearRgb * uCdlSlope + uCdlOffset, vec3(0.0)), max(uCdlPower, vec3(0.0001)));
        float luma = dot(sop, vec3(0.2126, 0.7152, 0.0722));
        linearRgb = max(vec3(0.0), mix(vec3(luma), sop, uCdlSat));
    }

    // 4. Interactive Color Grading Edits
    linearRgb = linearRgb * uGradingGain + uGradingOffset;
    if (uGradingGamma > 0.001 && abs(uGradingGamma - 1.0) > 0.001) {
        linearRgb = pow(max(linearRgb, vec3(0.0)), vec3(1.0 / uGradingGamma));
    }
    if (abs(uGradingContrast - 1.0) > 0.001) {
        linearRgb = max(vec3(0.0), (linearRgb - vec3(0.18)) * uGradingContrast + vec3(0.18));
    }
    if (abs(uGradingSat - 1.0) > 0.001) {
        float luma = dot(linearRgb, vec3(0.2126, 0.7152, 0.0722));
        linearRgb = max(vec3(0.0), mix(vec3(luma), linearRgb, uGradingSat));
    }
    if (abs(uGradingHue) > 0.01) {
        vec3 hsv = rgb2hsv(linearRgb);
        hsv.x = fract(hsv.x + uGradingHue / 360.0);
        linearRgb = hsv2rgb(hsv);
    }

    // 5. 3D LUT Evaluation
    if (uHasLut3D) {
        vec3 lutCoord = clamp(linearRgb, 0.0, 1.0);
        linearRgb = texture(uLut3D, lutCoord).rgb;
    }

    // xtra QC Real-Time Diagnostic Grading (non-destructive - applied to Both sides)
    if (abs(uQcGain - 1.0) > 0.001) {
        linearRgb = linearRgb * uQcGain;
    }
    if (abs(uQcGamma - 1.0) > 0.001 && uQcGamma > 0.01) {
        linearRgb = pow(max(linearRgb, vec3(0.0)), vec3(1.0 / uQcGamma));
    }
    if (uQcSoftclip < 0.999) {
        float knee = max(0.001, uQcSoftclip);
        float headroom = 1.0 - knee;
        for (int i = 0; i < 3; ++i) {
            if (linearRgb[i] > knee) {
                linearRgb[i] = knee + headroom * tanh((linearRgb[i] - knee) / headroom);
            }
        }
    }
    if (abs(uQcSatMultiplier - 1.0) > 0.001) {
        float luma = dot(linearRgb, vec3(0.2126, 0.7152, 0.0722));
        linearRgb = max(vec3(0.0), mix(vec3(luma), linearRgb, uQcSatMultiplier));
    }
    if (abs(uQcHueRotation) > 0.01) {
        vec3 hsv = rgb2hsv(linearRgb);
        hsv.x = fract(hsv.x + uQcHueRotation / 360.0);
        linearRgb = hsv2rgb(hsv);
    }

    // xtra QC Scene-Linear Overlay Matte (Nuke-style Merge Over) - Left Comp only when Compare plates is on
    if (!isRightPlate) {
        if (uHasTextureMatte && uQcOverlayMatte) {
            vec4 rawMatte = texture(uTextureMatte, sCoord);
            vec4 colMatte = eval_matte_channels(rawMatte, sCoord);
            if (uQcCheckClampedAlpha) {
                colMatte.a = (colMatte.a >= 0.999) ? 1.0 : 0.0;
            }
            vec3 linMatte = to_linear_space(colMatte.rgb);
            float mA = clamp(colMatte.a, 0.0, 1.0);
            vec3 compLinear;
            if (uQcMatteIsPremult) {
                compLinear = linMatte + linearRgb * (1.0 - mA);
            } else {
                compLinear = linMatte * mA + linearRgb * (1.0 - mA);
            }
            linearRgb = mix(linearRgb, compLinear, clamp(uQcMixOverlay, 0.0, 1.0));
            finalAlpha = mix(finalAlpha, clamp(mA + finalAlpha * (1.0 - mA), 0.0, 1.0), clamp(uQcMixOverlay, 0.0, 1.0));
        }
    }

    // 6. Display / View Transform
    vec3 displayRgb;
    if (uHasOcioLut3D) {
        vec3 lutCoord = linearRgb;
        if (uOcioShaperMode == 1) {
            vec3 ev = log2(max(linearRgb, vec3(0.0009765625)));
            lutCoord = clamp((ev - vec3(uOcioMinEv)) / (uOcioMaxEv - uOcioMinEv), 0.0, 1.0);
        } else if (uOcioShaperMode == 2) {
            lutCoord = clamp(linearRgb / (vec3(1.0) + linearRgb), 0.0, 1.0);
        } else {
            lutCoord = clamp(linearRgb, 0.0, 1.0);
        }
        displayRgb = texture(uOcioLut3D, lutCoord).rgb;
    } else {
        displayRgb = to_display_space(linearRgb);
    }

    // 7. Fast Hardware-Accelerated Gamma & Display Brightness Trim
    if (uApplyGamma) {
        displayRgb = pow(max(displayRgb, vec3(0.0)), vec3(uGammaPower));
        if (uGamma < 0.0) {
            displayRgb = vec3(1.0) - displayRgb;
        }
    }
    if (abs(uDisplayBrightness) > 0.001) {
        displayRgb = displayRgb * pow(2.0, uDisplayBrightness);
    }

    // 8. Invert Colors
    if (uInvertColors) {
        displayRgb = vec3(1.0) - displayRgb;
    }

    // 9. Out-of-Range False-Color Zebra / Highlight
    if (uShowOutOfRange) {
        if (displayRgb.r > 1.0 || displayRgb.g > 1.0 || displayRgb.b > 1.0) {
            displayRgb = vec3(1.0, 0.0, 1.0); // Blown highlights in magenta
        } else if (displayRgb.r < 0.0 || displayRgb.g < 0.0 || displayRgb.b < 0.0) {
            displayRgb = vec3(0.0, 0.8, 1.0); // Crushed blacks in cyan
        }
    }

    // Comp QC Post-Transform Diagnostic Modes (applied to Both sides in Compare Plates, except Difference on Left only)
    // 1. Log Colorspace Mode (Inspect Black Levels) - applied to BOTH sides
    if (uQcMode == 1) {
        displayRgb = linear_to_cineon(linearRgb);
    }
    // 2. NaN / Inf Pixels Detection (Cyan pattern over invalid values) - applied to BOTH sides
    if (uQcMode == 2) {
        vec4 rawSample = isRightPlate ? texture(uTextureB, sCoord) : texture(uTextureA, sCoord);
        if (isnan(rawSample.r) || isnan(rawSample.g) || isnan(rawSample.b) || isnan(rawSample.a) ||
            isinf(rawSample.r) || isinf(rawSample.g) || isinf(rawSample.b) || isinf(rawSample.a)) {
            float checker = mod(floor(gl_FragCoord.x / 16.0) + floor(gl_FragCoord.y / 16.0), 2.0);
            displayRgb = (checker > 0.5) ? vec3(0.0, 1.0, 1.0) : vec3(0.0, 0.25, 0.35);
        }
    }
    // 3. Clipping Values Highlight - applied to BOTH sides
    if (uQcMode == 5) {
        float maxVal = max(linearRgb.r, max(linearRgb.g, linearRgb.b));
        if (maxVal >= uQcClippingThreshold) {
            float checker = mod(floor(gl_FragCoord.x / 8.0) + floor(gl_FragCoord.y / 8.0), 2.0);
            displayRgb = (checker > 0.5) ? vec3(1.0, 0.0, 0.0) : vec3(0.0, 1.0, 0.0);
        } else {
            displayRgb = displayRgb * 0.35;
        }
    }
    // 4. Negative Values Highlight - applied to BOTH sides
    if (uQcMode == 6) {
        if (linearRgb.r < 0.0 || linearRgb.g < 0.0 || linearRgb.b < 0.0) {
            displayRgb = vec3(1.0, 0.07, 0.57); // Vibrant hot pink
        } else if (!uQcNegativeOverComp) {
            displayRgb = vec3(0.0);
        }
    }
    // 5. Saturation Boost Mode - applied to BOTH sides
    if (uQcMode == 9) {
        float luma = dot(displayRgb, vec3(0.2126, 0.7152, 0.0722));
        displayRgb = mix(vec3(luma), displayRgb, uQcSaturationBoost);
    }
    // 6. Comp Difference with Plate Mode (Red Constant Overlay where plates differ) - Left Comp only!
    if (!isRightPlate && uQcMode == 8 && uHasTextureB) {
        vec4 colB = texture(uTextureB, sCoord);
        vec3 linB = to_linear_space(colB.rgb);
        float diffAmt = length(linearRgb - linB);
        if (diffAmt > 0.005) {
            displayRgb = mix(displayRgb, vec3(1.0, 0.0, 0.0), 0.75);
        }
    }
    // 7. Clamped Alpha Check (<0.0 in Cyan, >1.0 in Magenta) - Left Comp only
    if (!isRightPlate && uQcMode == 10) {
        if (finalAlpha < -0.0001) {
            displayRgb = vec3(0.0, 0.9, 1.0); // Cyan for negative alpha
        } else if (finalAlpha > 1.0001) {
            displayRgb = vec3(1.0, 0.1, 0.85); // Magenta for super-white alpha
        } else {
            displayRgb = vec3(finalAlpha);
        }
    }
    // 8. Temporary HSL Colorspace Inspection - applied to BOTH sides
    if (uQcHslMode > 0) {
        vec3 hsl = rgb2hsl(displayRgb);
        if (uQcHslMode == 1) {
            displayRgb = hsl; // Full HSL visualization
        } else if (uQcHslMode == 2) {
            displayRgb = vec3(hsl.z); // Lightness (L)
        } else if (uQcHslMode == 3) {
            displayRgb = vec3(hsl.y); // Saturation (S)
        } else if (uQcHslMode == 4) {
            displayRgb = vec3(hsl.x); // Hue (H)
        }
    }

    // 9. Overlay Matte, Alpha Tint, Greycheck & Checkerboard (Left Comp only when Compare plates is on)
    if (!isRightPlate) {
        if (uHasTextureMatte) {
            vec4 rawMatte = texture(uTextureMatte, sCoord);
            vec4 colMatte = eval_matte_channels(rawMatte, sCoord);

            // Clamp Alpha: strictly clamp matte alpha so everything less than 1.00 becomes 0.0
            if (uQcCheckClampedAlpha) {
                colMatte.a = (colMatte.a >= 0.999) ? 1.0 : 0.0;
            }

            if (uQcAlphaMaskEnabled) {
                float mVal = colMatte.a;
                if (uQcAlphaMaskInvert) {
                    mVal = 1.0 - mVal;
                }
                float blendFactor = clamp(mVal * uQcMixOverlay * uQcAlphaMaskColor.a, 0.0, 1.0);
                displayRgb = mix(displayRgb, uQcAlphaMaskColor.rgb, blendFactor);
            }
            if (uQcGreycheckEnabled) {
                float mAlpha = colMatte.a;
                displayRgb = mix(vec3(uQcGreyValue), displayRgb, clamp(mAlpha, 0.0, 1.0));
            } else if (uQcCheckerboardEnabled) {
                float mAlpha = colMatte.a;
                vec2 cCoord = floor(gl_FragCoord.xy / max(1.0, uQcGridScale));
                float pattern = mod(cCoord.x + cCoord.y, 2.0);
                float v1 = 0.5 - uQcCheckerContrast * 0.5;
                float v2 = 0.5 + uQcCheckerContrast * 0.5;
                vec3 cCol = vec3(mix(v1, v2, pattern));
                displayRgb = mix(cCol, displayRgb, clamp(mAlpha, 0.0, 1.0));
            }
        } else {
            if (uQcAlphaMaskEnabled) {
                float mVal = finalAlpha;
                if (uQcCheckClampedAlpha) {
                    mVal = (mVal >= 0.999) ? 1.0 : 0.0;
                }
                if (uQcAlphaMaskInvert) {
                    mVal = 1.0 - mVal;
                }
                float blendFactor = clamp(mVal * uQcMixOverlay * uQcAlphaMaskColor.a, 0.0, 1.0);
                displayRgb = mix(displayRgb, uQcAlphaMaskColor.rgb, blendFactor);
            }
            if (uQcGreycheckEnabled) {
                displayRgb = mix(vec3(uQcGreyValue), displayRgb, clamp(finalAlpha, 0.0, 1.0));
            } else if (uQcCheckerboardEnabled) {
                vec2 cCoord = floor(gl_FragCoord.xy / max(1.0, uQcGridScale));
                float pattern = mod(cCoord.x + cCoord.y, 2.0);
                float v1 = 0.5 - uQcCheckerContrast * 0.5;
                float v2 = 0.5 + uQcCheckerContrast * 0.5;
                vec3 cCol = vec3(mix(v1, v2, pattern));
                displayRgb = mix(cCol, displayRgb, clamp(finalAlpha, 0.0, 1.0));
            }
        }
    }

    // 10. Channel Swizzling & Matrix Transform
    vec4 routedColor = uChannelMatrix * vec4(displayRgb, finalAlpha);
    routedColor.rgb *= uChannelEnable.rgb;

    if (uChannelSoloMode == 2) {
        // Normals Remap: [-1, 1] -> [0, 1]
        routedColor.rgb = clamp(routedColor.rgb * 0.5 + vec3(0.5), 0.0, 1.0);
    }

    // Channel Solo Isolation (Greyscale vs Color)
    if (uChannelMode == 1) {
        // Red solo
        FragColor = (uChannelSoloMode == 1) ? vec4(vec3(routedColor.r), 1.0) : vec4(routedColor.r, 0.0, 0.0, 1.0);
    } else if (uChannelMode == 2) {
        // Green solo
        FragColor = (uChannelSoloMode == 1) ? vec4(vec3(routedColor.g), 1.0) : vec4(0.0, routedColor.g, 0.0, 1.0);
    } else if (uChannelMode == 3) {
        // Blue solo
        FragColor = (uChannelSoloMode == 1) ? vec4(vec3(routedColor.b), 1.0) : vec4(0.0, 0.0, routedColor.b, 1.0);
    } else if (uChannelMode == 4) {
        // Alpha solo
        FragColor = vec4(vec3(routedColor.a), 1.0);
    } else if (uChannelMode == 5) {
        // Luma Y (Rec.709)
        float y = dot(routedColor.rgb, vec3(0.2126, 0.7152, 0.0722));
        FragColor = vec4(vec3(y), 1.0);
    } else {
        FragColor = vec4(routedColor.rgb, routedColor.a);
    }
}
"""


ALLOWED_EXPR_IDENTS = {
    # Channels
    "r", "g", "b", "a", "red", "green", "blue", "alpha", "mraw",
    "mraw.r", "mraw.g", "mraw.b", "mraw.a",
    # Coordinates & dimensions
    "cx", "cy", "x", "y", "w", "h",
    # Frame
    "f", "frame",
    # Math functions
    "clamp", "min", "max", "abs", "pow", "sqrt", "exp", "log",
    "sin", "cos", "tan", "asin", "acos", "atan",
    "floor", "ceil", "fract", "round", "sign",
    "step", "smoothstep", "mix", "lerp",
    "distance", "length", "mod", "avg", "average",
    "vec2", "vec3", "vec4", "float",
}


def validate_and_compile_expression(expr: str, channel: str) -> tuple[str, Optional[str]]:
    """
    Validate and compile a Nuke-style channel expression into safe GLSL.
    Returns:
        (compiled_glsl, error_message_or_none)
    """
    raw = (expr or "").strip()
    if not raw:
        return f"mRaw.{channel}", None

    token_pattern = re.compile(r"\s*(<=|>=|==|!=|&&|\|\||[a-zA-Z_]\w*(?:\.[a-zA-Z_]\w*)*|\d+(?:\.\d+)?|\.\d+|[+\-*/%(),?:<>=!])\s*")
    tokens = []
    pos = 0
    while pos < len(raw):
        m = token_pattern.match(raw, pos)
        if not m:
            return f"mRaw.{channel}", f"Invalid character at column {pos + 1}: '{raw[pos]}'"
        tokens.append(m.group(1))
        pos = m.end()

    # Check identifiers
    for t in tokens:
        if re.match(r"^[a-zA-Z_]\w*(?:\.[a-zA-Z_]\w*)*$", t):
            tl = t.lower()
            if tl not in ALLOWED_EXPR_IDENTS:
                parts = tl.split(".")
                if not (len(parts) == 2 and parts[0] == "mraw" and all(c in "rgbaxyzw" for c in parts[1])):
                    return f"mRaw.{channel}", f"Unknown identifier: '{t}'"

    # Check parentheses balancing and depth
    depth = 0
    for t in tokens:
        if t == "(":
            depth += 1
        elif t == ")":
            depth -= 1
            if depth < 0:
                return f"mRaw.{channel}", "Unmatched closing parenthesis ')'"
    if depth != 0:
        return f"mRaw.{channel}", "Unclosed parenthesis '('"

    # Check ternary operator balancing
    q_count = tokens.count("?")
    c_count = tokens.count(":")
    if q_count != c_count:
        return f"mRaw.{channel}", "Ternary operator must have matching '?' and ':'"

    res = " ".join(tokens)
    res = re.sub(r"\blerp\b", "mix", res, flags=re.IGNORECASE)
    res = re.sub(r"\baverage\b", "avg", res, flags=re.IGNORECASE)
    while "avg (" in res or "avg(" in res:
        res = re.sub(r"\bavg\s*\(\s*([^,]+?)\s*,\s*([^)]+?)\s*\)", r"((\1 + \2) * 0.5)", res)

    # Convert r, g, b, a to mRaw.r, mRaw.g, mRaw.b, mRaw.a (excluding already qualified member access like mRaw.r)
    def repl_var(m):
        v = m.group(0).lower()
        if v in ("r", "red"): return "mRaw.r"
        if v in ("g", "green"): return "mRaw.g"
        if v in ("b", "blue"): return "mRaw.b"
        if v in ("a", "alpha"): return "mRaw.a"
        return m.group(0)

    res = re.sub(r"(?<!\.)\b(red|green|blue|alpha|[rgba])\b", repl_var, res, flags=re.IGNORECASE)

    # Float promotion: convert integer numbers like 1, 0, 5 to 1.0, 0.0, 5.0 for strict GLSL
    res = re.sub(r"(?<![.\w])(\d+)(?![.\w])", r"\1.0", res)
    return res, None


def compile_channel_expression(expr: str, channel: str) -> str:
    """Compile user channel expression to safe GLSL, returning fallback on error."""
    glsl, _ = validate_and_compile_expression(expr, channel)
    return glsl


def build_fragment_shader(
    expr_r: str = "r",
    expr_g: str = "g",
    expr_b: str = "b",
    expr_a: str = "a",
) -> str:
    """Return the GLSL fragment shader source code, optionally compiled with matte channel expressions."""
    cr = compile_channel_expression(expr_r, "r")
    cg = compile_channel_expression(expr_g, "g")
    cb = compile_channel_expression(expr_b, "b")
    ca = compile_channel_expression(expr_a, "a")

    func_def = f"""vec4 eval_matte_channels(vec4 mRaw, vec2 sCoord) {{
    float r = mRaw.r;
    float g = mRaw.g;
    float b = mRaw.b;
    float a = mRaw.a;
    float red = r;
    float green = g;
    float blue = b;
    float alpha = a;
    float cx = sCoord.x;
    float cy = sCoord.y;
    float x = sCoord.x * 1920.0;
    float y = sCoord.y * 1080.0;
    float w = 1920.0;
    float h = 1080.0;
    float f = uCurrentFrame;
    float frame = uCurrentFrame;

    float out_r = (uQcMatteChannels.x > 0.5) ? ({cr}) : r;
    float out_g = (uQcMatteChannels.y > 0.5) ? ({cg}) : g;
    float out_b = (uQcMatteChannels.z > 0.5) ? ({cb}) : b;
    float out_a = (uQcMatteChannels.w > 0.5) ? ({ca}) : a;
    return vec4(out_r, out_g, out_b, out_a);
}}"""
    # Replace default implementation if placeholder exists
    if "// __EVAL_MATTE_CHANNELS_PLACEHOLDER__" in FRAGMENT_SHADER_SOURCE:
        pattern = r"// __EVAL_MATTE_CHANNELS_PLACEHOLDER__\s*vec4 eval_matte_channels\(vec4 mRaw,\s*vec2 sCoord\)\s*\{[^}]*\}"
        if re.search(pattern, FRAGMENT_SHADER_SOURCE):
            return re.sub(pattern, f"// __EVAL_MATTE_CHANNELS_PLACEHOLDER__\n{func_def}", FRAGMENT_SHADER_SOURCE)
        pattern_old = r"// __EVAL_MATTE_CHANNELS_PLACEHOLDER__\s*vec4 eval_matte_channels\(vec4 mRaw\)\s*\{[^}]*\}"
        return re.sub(pattern_old, f"// __EVAL_MATTE_CHANNELS_PLACEHOLDER__\n{func_def}", FRAGMENT_SHADER_SOURCE)
    return FRAGMENT_SHADER_SOURCE

