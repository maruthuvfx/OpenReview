"""
Custom OpenReview VFX Widgets.
"""
from openreview.widgets.viewport import ViewportCanvas
from openreview.widgets.floating_hud import FloatingHUDCard
from openreview.widgets.hud_histogram import HistogramHUD
from openreview.widgets.hud_waveform import WaveformHUD
from openreview.widgets.hud_vectorscope import VectorscopeHUD
from openreview.widgets.hud_wipe import WipeComparisonHUD
from openreview.widgets.markup_tools import MarkupToolPalette
from openreview.widgets.timeline import TimelineWidget
from openreview.widgets.top_bar import TopControlBar
from openreview.widgets.hud_layers import HUDLayersWidget
from openreview.widgets.hud_channel_matrix import HUDChannelMatrixWidget
from openreview.widgets.hud_stereo import HUDStereoWidget
from openreview.widgets.color_sliders_overlay import ColorSlidersOverlay, ColorRulerSlider

__all__ = [
    "ViewportCanvas",
    "FloatingHUDCard",
    "HistogramHUD",
    "WaveformHUD",
    "VectorscopeHUD",
    "WipeComparisonHUD",
    "MarkupToolPalette",
    "TimelineWidget",
    "TopControlBar",
    "HUDLayersWidget",
    "HUDChannelMatrixWidget",
    "HUDStereoWidget",
    "ColorSlidersOverlay",
    "ColorRulerSlider",
]
