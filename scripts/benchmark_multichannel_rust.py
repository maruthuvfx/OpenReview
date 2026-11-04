#!/usr/bin/env python3
"""
Benchmark comparison: Python OpenEXR vs Optimized Rust OpenReviewEngine
on Multi-Channel EXR operations (metadata inspection, pass extraction, contact sheet, routing).
"""
import os
import sys
import time
import numpy as np

sys.path.insert(0, os.path.abspath("src"))

STANDALONE_EXR = os.path.abspath("tests/sample_media/multichannel_exr/multichannel_test.exr")

def benchmark_multichannel():
    import openreview_engine
    import OpenEXR
    import Imath
    from openreview.core.image_io import read_exr, get_exr_channels
    from openreview.core.exr_matrix import inspect_exr_matrix, build_layer_preset, read_exr_matrix_mapped, EXRMatrixMapping

    print("=" * 60)
    print("MULTI-CHANNEL EXR PERFORMANCE BENCHMARK (PYTHON vs RUST)")
    print("=" * 60)
    print(f"Dataset: {STANDALONE_EXR} ({os.path.getsize(STANDALONE_EXR)} bytes, 23 channels)")
    print()

    rust_eng = openreview_engine.OpenReviewEngine()
    iterations = 20

    # 1. Header & Channel Inspection
    # Python C++ binding:
    t0 = time.perf_counter()
    for _ in range(iterations):
        inp = OpenEXR.InputFile(STANDALONE_EXR)
        h = inp.header()
        chans = list(h.get("channels", {}).keys())
        inp.close()
    py_inspect_time = (time.perf_counter() - t0) / iterations * 1000.0

    # Rust Native:
    t0 = time.perf_counter()
    for _ in range(iterations):
        meta = rust_eng.inspect_exr(STANDALONE_EXR)
    rust_inspect_time = (time.perf_counter() - t0) / iterations * 1000.0

    print(f"[Metadata Inspection] Python: {py_inspect_time:.3f} ms | Rust: {rust_inspect_time:.3f} ms | Speedup: {py_inspect_time / max(0.001, rust_inspect_time):.1f}x")

    # 2. Beauty Pass Decode
    t0 = time.perf_counter()
    for _ in range(iterations):
        # Force Python path
        inp = OpenEXR.InputFile(STANDALONE_EXR)
        header = inp.header()
        dw = header["dataWindow"]
        w = dw.max.x - dw.min.x + 1
        h = dw.max.y - dw.min.y + 1
        pt = Imath.PixelType(Imath.PixelType.FLOAT)
        r = np.frombuffer(inp.channel("R", pt), dtype=np.float32).reshape((h, w))
        g = np.frombuffer(inp.channel("G", pt), dtype=np.float32).reshape((h, w))
        b = np.frombuffer(inp.channel("B", pt), dtype=np.float32).reshape((h, w))
        a = np.frombuffer(inp.channel("A", pt), dtype=np.float32).reshape((h, w))
        inp.close()
        _ = np.stack([r, g, b, a], axis=-1)
    py_beauty_time = (time.perf_counter() - t0) / iterations * 1000.0

    t0 = time.perf_counter()
    for _ in range(iterations):
        f = rust_eng.read_exr(STANDALONE_EXR, False, layer="beauty")
        _ = np.frombuffer(f.as_memoryview(), dtype=np.float32).reshape((f.height, f.width, 4))
    rust_beauty_time = (time.perf_counter() - t0) / iterations * 1000.0

    print(f"[Beauty Pass Decode] Python: {py_beauty_time:.3f} ms ({1000.0/py_beauty_time:.1f} FPS) | Rust: {rust_beauty_time:.3f} ms ({1000.0/rust_beauty_time:.1f} FPS) | Speedup: {py_beauty_time / max(0.001, rust_beauty_time):.1f}x")

    # 3. Normals Remapped Decode ([-1..1] -> [0..1])
    t0 = time.perf_counter()
    for _ in range(iterations):
        inp = OpenEXR.InputFile(STANDALONE_EXR)
        header = inp.header()
        dw = header["dataWindow"]
        w = dw.max.x - dw.min.x + 1
        h = dw.max.y - dw.min.y + 1
        pt = Imath.PixelType(Imath.PixelType.FLOAT)
        nx = np.frombuffer(inp.channel("N.X", pt), dtype=np.float32).reshape((h, w))
        ny = np.frombuffer(inp.channel("N.Y", pt), dtype=np.float32).reshape((h, w))
        nz = np.frombuffer(inp.channel("N.Z", pt), dtype=np.float32).reshape((h, w))
        inp.close()
        norm = np.stack([nx, ny, nz, np.ones_like(nx)], axis=-1)
        norm[:, :, :3] = np.clip(norm[:, :, :3] * 0.5 + 0.5, 0.0, 1.0)
    py_norm_time = (time.perf_counter() - t0) / iterations * 1000.0

    t0 = time.perf_counter()
    for _ in range(iterations):
        f = rust_eng.read_exr(STANDALONE_EXR, False, layer="N", remap_normals=True)
        _ = np.frombuffer(f.as_memoryview(), dtype=np.float32).reshape((f.height, f.width, 4))
    rust_norm_time = (time.perf_counter() - t0) / iterations * 1000.0

    print(f"[Normals Remap Pass] Python: {py_norm_time:.3f} ms ({1000.0/py_norm_time:.1f} FPS) | Rust: {rust_norm_time:.3f} ms ({1000.0/rust_norm_time:.1f} FPS) | Speedup: {py_norm_time / max(0.001, rust_norm_time):.1f}x")

    # 4. Depth Auto-Normalized Decode ([15..250] -> [0..1])
    t0 = time.perf_counter()
    for _ in range(iterations):
        inp = OpenEXR.InputFile(STANDALONE_EXR)
        header = inp.header()
        dw = header["dataWindow"]
        w = dw.max.x - dw.min.x + 1
        h = dw.max.y - dw.min.y + 1
        pt = Imath.PixelType(Imath.PixelType.FLOAT)
        z = np.frombuffer(inp.channel("depth.Z", pt), dtype=np.float32).reshape((h, w))
        inp.close()
        z_min = float(np.min(z))
        z_max = float(np.max(z))
        norm_z = (z - z_min) / (z_max - z_min)
        _ = np.stack([norm_z, norm_z, norm_z, np.ones_like(norm_z)], axis=-1)
    py_depth_time = (time.perf_counter() - t0) / iterations * 1000.0

    t0 = time.perf_counter()
    for _ in range(iterations):
        f = rust_eng.read_exr(STANDALONE_EXR, False, layer="depth", auto_normalize_depth=True)
        _ = np.frombuffer(f.as_memoryview(), dtype=np.float32).reshape((f.height, f.width, 4))
    rust_depth_time = (time.perf_counter() - t0) / iterations * 1000.0

    print(f"[Depth Auto-Norm]   Python: {py_depth_time:.3f} ms ({1000.0/py_depth_time:.1f} FPS) | Rust: {rust_depth_time:.3f} ms ({1000.0/rust_depth_time:.1f} FPS) | Speedup: {py_depth_time / max(0.001, rust_depth_time):.1f}x")

    # 5. Contact Sheet Multi-Pass Extraction (All 8 passes)
    passes = ["beauty", "diffuse", "specular", "N", "depth", "motion", "AO", "crypto_object00"]
    t0 = time.perf_counter()
    for _ in range(5):
        frames = {}
        for p in passes:
            frames[p] = read_exr(STANDALONE_EXR, layer=p)
    py_all_passes_time = (time.perf_counter() - t0) / 5 * 1000.0

    t0 = time.perf_counter()
    for _ in range(5):
        all_raw = rust_eng.read_all_exr_layers(STANDALONE_EXR, False)
    rust_all_passes_time = (time.perf_counter() - t0) / 5 * 1000.0

    print(f"[All 8 Passes (Sheet)] Python (8 reads): {py_all_passes_time:.3f} ms | Rust (1 parse): {rust_all_passes_time:.3f} ms | Speedup: {py_all_passes_time / max(0.001, rust_all_passes_time):.1f}x")
    print("=" * 60)

if __name__ == "__main__":
    benchmark_multichannel()
