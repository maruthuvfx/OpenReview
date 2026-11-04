#!/usr/bin/env python3
"""
Diagnostic script testing all Multi-Channel EXR options against the test media.
"""
import os
import sys
import time
import numpy as np

sys.path.insert(0, os.path.abspath("src"))

def run_diagnostics():
    from openreview.core.image_io import get_exr_channels, read_exr
    from openreview.core.exr_matrix import inspect_exr_matrix, build_layer_preset, read_exr_matrix_mapped, EXRMatrixMapping, SemanticPassType
    from openreview.core.exr_contact_sheet import build_exr_contact_sheet, extract_exr_detailed_metadata
    import openreview_engine

    test_file = os.path.abspath("tests/sample_media/multichannel_exr/multichannel_test.exr")
    assert os.path.exists(test_file), f"File not found: {test_file}"
    print(f"=== Testing Multi-Channel EXR: {test_file} ===")

    # 1. Channel Inspection
    ch_info = get_exr_channels(test_file)
    print(f"[get_exr_channels] Found {len(ch_info.get('all_channels', []))} channels, layers: {list(ch_info.get('layers', {}).keys())}")

    # 2. Matrix Inspection
    layers_dict, all_channels = inspect_exr_matrix(test_file)
    print(f"[inspect_exr_matrix] Layers: {list(layers_dict.keys())}")
    for lyr, chans in layers_dict.items():
        print(f"  Layer '{lyr}': {[c.name for c in chans]}")

    # 3. Read Beauty pass
    t0 = time.perf_counter()
    beauty = read_exr(test_file, layer="beauty")
    t1 = time.perf_counter()
    print(f"[read_exr: beauty] shape={beauty.shape}, dtype={beauty.dtype}, min={beauty.min():.3f}, max={beauty.max():.3f}, time={(t1-t0)*1000:.2f}ms")

    # 4. Read Diffuse pass
    t0 = time.perf_counter()
    diffuse = read_exr(test_file, layer="diffuse")
    t1 = time.perf_counter()
    print(f"[read_exr: diffuse] shape={diffuse.shape}, min={diffuse.min():.3f}, max={diffuse.max():.3f}, time={(t1-t0)*1000:.2f}ms")

    # 5. Read Specular pass
    t0 = time.perf_counter()
    specular = read_exr(test_file, layer="specular")
    t1 = time.perf_counter()
    print(f"[read_exr: specular] shape={specular.shape}, min={specular.min():.3f}, max={specular.max():.3f}, time={(t1-t0)*1000:.2f}ms")

    # 6. Read Normals with mapping
    preset_norm = build_layer_preset("N", layers_dict.get("N", []))
    t0 = time.perf_counter()
    normals = read_exr(test_file, mapping=preset_norm)
    t1 = time.perf_counter()
    print(f"[read_exr: normals] remap={preset_norm.remap_normals}, min={normals.min():.3f}, max={normals.max():.3f}, time={(t1-t0)*1000:.2f}ms")

    # 7. Read Depth with auto-normalization
    preset_depth = build_layer_preset("depth", layers_dict.get("depth", []))
    t0 = time.perf_counter()
    depth = read_exr(test_file, mapping=preset_depth)
    t1 = time.perf_counter()
    print(f"[read_exr: depth] norm={preset_depth.auto_normalize_depth}, min={depth[:,:,0].min():.3f}, max={depth[:,:,0].max():.3f}, time={(t1-t0)*1000:.2f}ms")

    # 8. Read Cryptomatte
    preset_crypto = build_layer_preset("crypto_object", layers_dict.get("crypto_object", []))
    t0 = time.perf_counter()
    crypto = read_exr(test_file, mapping=preset_crypto)
    t1 = time.perf_counter()
    print(f"[read_exr: crypto] shape={crypto.shape}, min={crypto.min():.3f}, max={crypto.max():.3f}, time={(t1-t0)*1000:.2f}ms")

    # 9. Contact Sheet
    try:
        from openreview.core.image_io import read_image_file
        t0 = time.perf_counter()
        layer_frames = {}
        for lyr in list(ch_info.get("layers", {}).keys()):
            arr = read_image_file(test_file, layer=lyr)
            if arr is not None:
                layer_frames[lyr] = arr
        print(f"[build_exr_contact_sheet] Loaded {len(layer_frames)} layers, calling build_exr_contact_sheet...")
        cs_img, tile_map = build_exr_contact_sheet(layer_frames, base_w=256, base_h=144)
        t1 = time.perf_counter()
        print(f"[build_exr_contact_sheet] tiles={len(tile_map)}, shape={cs_img.shape}, time={(t1-t0)*1000:.2f}ms")
        for lyr, rect in tile_map.items():
            print(f"  Tile '{lyr}': rect={rect}")
    except Exception as e:
        import traceback
        print(f"[build_exr_contact_sheet] ERROR: {e}")
        traceback.print_exc()

    # 10. Detailed Metadata
    try:
        meta = extract_exr_detailed_metadata(test_file)
        print(f"[extract_exr_detailed_metadata] compression={meta.get('compression')}, channels count={len(meta.get('channels', []))}")
    except Exception as e:
        print(f"[extract_exr_detailed_metadata] ERROR: {e}")

    # 11. Test Rust Engine on EXR
    try:
        eng = openreview_engine.OpenReviewEngine()
        t0 = time.perf_counter()
        rust_frame = eng.read_exr(test_file, as_f16=False)
        t1 = time.perf_counter()
        print(f"[openreview_engine.read_exr] SUCCESS: {rust_frame.width}x{rust_frame.height}, ch={rust_frame.channels}, time={(t1-t0)*1000:.2f}ms")
    except Exception as e:
        print(f"[openreview_engine.read_exr] FAILED: {e}")

if __name__ == "__main__":
    run_diagnostics()
