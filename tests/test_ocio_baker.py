"""
Tests for OCIO v2 Display Transform LUT Pre-Baking & 3D Texture Fast Path.
"""
import pytest
import numpy as np

from openreview.color.ocio_baker import (
    OcioLutBakeResult,
    OcioLutCache,
    bake_display_view_lut,
    _analytical_aces_bake,
    SHAPER_LINEAR,
    SHAPER_LOG2,
    SHAPER_RATIONAL,
)
from openreview.color.ocio_manager import OCIOManager


class TestOcioLutCache:
    def test_cache_put_and_get(self):
        cache = OcioLutCache(capacity=3)
        res1 = OcioLutBakeResult(
            size=16,
            data_3d=np.zeros((16, 16, 16, 4), dtype=np.float32),
            shaper_mode=SHAPER_LOG2,
            min_ev=-10.0,
            max_ev=10.0,
            cache_key="key1",
        )
        cache.put("key1", res1)
        assert cache.get("key1") is res1
        assert cache.size == 1

    def test_cache_lru_eviction(self):
        cache = OcioLutCache(capacity=2)
        for i in range(3):
            res = OcioLutBakeResult(
                size=16,
                data_3d=np.zeros((16, 16, 16, 4), dtype=np.float32),
                shaper_mode=SHAPER_LOG2,
                min_ev=-10.0,
                max_ev=10.0,
                cache_key=f"key{i}",
            )
            cache.put(f"key{i}", res)

        assert cache.size == 2
        # key0 should have been evicted
        assert cache.get("key0") is None
        assert cache.get("key1") is not None
        assert cache.get("key2") is not None

    def test_cache_clear(self):
        cache = OcioLutCache(capacity=5)
        res = OcioLutBakeResult(
            size=16,
            data_3d=np.zeros((16, 16, 16, 4), dtype=np.float32),
            shaper_mode=SHAPER_LOG2,
            min_ev=-10.0,
            max_ev=10.0,
            cache_key="test",
        )
        cache.put("test", res)
        assert cache.size == 1
        cache.clear()
        assert cache.size == 0
        assert cache.get("test") is None


class TestOcioBaker:
    def test_analytical_aces_bake_shape_and_range(self):
        lut_size = 16
        coords = np.linspace(0.0, 1.0, lut_size, dtype=np.float32)
        grid_b, grid_g, grid_r = np.meshgrid(coords, coords, coords, indexing="ij")
        lattice = np.stack([grid_r, grid_g, grid_b], axis=-1)

        result = _analytical_aces_bake(lattice)
        assert result.shape == (lut_size, lut_size, lut_size, 3)
        assert result.dtype == np.float32
        # Values should be within [0.0, 1.0]
        assert np.all(result >= 0.0)
        assert np.all(result <= 1.0001)
        # Deep blacks should be near 0
        assert np.isclose(result[0, 0, 0], 0.0, atol=0.01).all()
        # High values should be non-zero
        assert np.any(result[-1, -1, -1] > 0.5)

    def test_shaper_modes_evaluation(self):
        for mode in (SHAPER_LINEAR, SHAPER_LOG2, SHAPER_RATIONAL):
            bake = bake_display_view_lut(
                config=None,
                src_space="ACEScg",
                display="sRGB",
                view="ACES 1.0",
                size=16,
                shaper_mode=mode,
            )
            assert bake.shaper_mode == mode
            assert bake.data_3d.shape == (16, 16, 16, 4)
            assert not np.isnan(bake.data_3d).any()

    def test_bake_display_view_lut_with_pyopencolorio(self):
        try:
            import PyOpenColorIO as ocio
            cfg = ocio.Config.CreateRaw()
            bake = bake_display_view_lut(
                config=cfg,
                src_space="raw",
                display="raw",
                view="raw",
                size=16,
                shaper_mode=SHAPER_LOG2,
            )
            assert isinstance(bake, OcioLutBakeResult)
            assert bake.lut_size == 16
            assert bake.data_3d.shape == (16, 16, 16, 4)
            assert bake.data_3d.dtype == np.float32
        except ImportError:
            pytest.skip("PyOpenColorIO not installed")


class TestOcioManagerIntegration:
    def test_ocio_manager_lut_signal(self):
        manager = OCIOManager()
        received = []

        def on_lut_baked(res):
            received.append(res)

        manager.ocio_lut_baked.connect(on_lut_baked)
        manager.color_management_mode = "ocio"
        res = manager.bake_current_display_lut(lut_size=16)

        assert len(received) >= 1
        assert received[-1] is not None
        assert isinstance(received[-1], OcioLutBakeResult)
        assert received[-1].lut_size == 16

    def test_ocio_manager_switching_to_standard_clears_lut(self):
        manager = OCIOManager()
        received = []
        manager.ocio_lut_baked.connect(received.append)

        manager.color_management_mode = "ocio"
        assert len(received) >= 1
        assert received[-1] is not None

        manager.color_management_mode = "standard"
        assert received[-1] is None

    def test_prewarm_adjacent_views(self):
        manager = OCIOManager()
        # Prewarm should run gracefully without exceptions
        manager.prewarm_adjacent_views()


class TestViewportOcioLut:
    def test_viewport_set_ocio_lut(self, qapp):
        from openreview.widgets.viewport import ViewportCanvas
        viewport = ViewportCanvas()
        bake = OcioLutBakeResult(
            size=8,
            data_3d=np.zeros((8, 8, 8, 4), dtype=np.float32),
            shaper_mode=SHAPER_LOG2,
            min_ev=-10.0,
            max_ev=10.0,
            cache_key="test_viewport",
        )
        assert viewport._active_ocio_lut_bake is None
        assert not viewport._pending_upload_ocio_lut

        viewport.set_ocio_lut(bake)
        assert viewport._active_ocio_lut_bake is bake
        assert viewport._pending_upload_ocio_lut is True

        viewport.set_ocio_lut(None)
        assert viewport._active_ocio_lut_bake is None
        assert viewport._pending_upload_ocio_lut is True
