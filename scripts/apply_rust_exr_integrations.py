#!/usr/bin/env python3
"""
Integrate the Rust multi-channel EXR acceleration into image_io, exr_matrix, and exr_contact_sheet.
"""
import re

def patch_image_io():
    path = "src/openreview/core/image_io.py"
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()

    # 1. Update get_exr_channels
    old_get = '''    norm_path = os.path.normpath(os.path.abspath(path))
    if norm_path in _EXR_CHANNELS_CACHE:
        return _EXR_CHANNELS_CACHE[norm_path]

    try:
        import OpenEXR'''
    new_get = '''    norm_path = os.path.normpath(os.path.abspath(path))
    if norm_path in _EXR_CHANNELS_CACHE:
        return _EXR_CHANNELS_CACHE[norm_path]

    # 0. Accelerated Rust Engine Inspection (Zero-GIL, high speed)
    if _is_rust_io_enabled():
        try:
            import openreview_engine
            rust_eng = getattr(get_exr_channels, "_rust_engine", None)
            if rust_eng is None:
                rust_eng = openreview_engine.OpenReviewEngine()
                get_exr_channels._rust_engine = rust_eng
            meta = rust_eng.inspect_exr(path)
            if meta and meta.get("all_channels"):
                _EXR_CHANNELS_CACHE[norm_path] = meta
                return meta
        except Exception as _re:
            logger.debug("Rust inspect_exr failed: %s", _re)

    try:
        import OpenEXR'''
    assert old_get in content, "Failed to find get_exr_channels target in image_io.py"
    content = content.replace(old_get, new_get, 1)

    # 2. Update read_exr
    old_read = '''    # 0. Accelerated Rust Engine: Zero-GIL, parallel multi-threaded Rayon decoder
    if _is_rust_io_enabled() and (not layer or layer.lower() == "beauty"):
        try:
            import openreview_engine
            rust_eng = getattr(read_exr, "_rust_engine", None)
            if rust_eng is None:
                rust_eng = openreview_engine.OpenReviewEngine()
                read_exr._rust_engine = rust_eng
            native_frame = rust_eng.read_exr(path, as_f16)'''
    new_read = '''    # 0. Accelerated Rust Engine: Zero-GIL, parallel multi-threaded Rayon decoder
    if _is_rust_io_enabled():
        try:
            import openreview_engine
            rust_eng = getattr(read_exr, "_rust_engine", None)
            if rust_eng is None:
                rust_eng = openreview_engine.OpenReviewEngine()
                read_exr._rust_engine = rust_eng
            native_frame = rust_eng.read_exr(path, as_f16, layer=layer)'''
    assert old_read in content, "Failed to find read_exr target in image_io.py"
    content = content.replace(old_read, new_read, 1)

    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(content)
    print("Successfully patched image_io.py")

def patch_exr_matrix():
    path = "src/openreview/core/exr_matrix.py"
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()

    # 1. Update inspect_exr_matrix
    old_inspect = '''    if not path or not os.path.exists(path):
        return {}, []

    try:
        import OpenEXR'''
    new_inspect = '''    if not path or not os.path.exists(path):
        return {}, []

    # 0. Rust-accelerated metadata inspection
    try:
        from openreview.core.image_io import _is_rust_io_enabled
        if _is_rust_io_enabled():
            import openreview_engine
            rust_eng = getattr(inspect_exr_matrix, "_rust_engine", None)
            if rust_eng is None:
                rust_eng = openreview_engine.OpenReviewEngine()
                inspect_exr_matrix._rust_engine = rust_eng
            meta = rust_eng.inspect_exr(path)
            if meta and meta.get("all_channels"):
                all_channels = [classify_channel(ch) for ch in meta["all_channels"]]
                layers_dict = {}
                for ch_info in all_channels:
                    layers_dict.setdefault(ch_info.layer, []).append(ch_info)
                return layers_dict, all_channels
    except Exception:
        pass

    try:
        import OpenEXR'''
    assert old_inspect in content, "Failed to find inspect_exr_matrix target in exr_matrix.py"
    content = content.replace(old_inspect, new_inspect, 1)

    # 2. Update read_exr_matrix_mapped
    old_read_mapped = '''def read_exr_matrix_mapped(
    path: str,
    mapping: EXRMatrixMapping,
    as_f16: bool = True,
) -> Optional[np.ndarray]:
    """
    Read specific OpenEXR channels according to an EXRMatrixMapping, using the
    recycled FrameBufferPool for zero heap allocations.
    """
    if not path or not os.path.exists(path):
        return None

    try:
        import OpenEXR'''
    new_read_mapped = '''def read_exr_matrix_mapped(
    path: str,
    mapping: EXRMatrixMapping,
    as_f16: bool = True,
) -> Optional[np.ndarray]:
    """
    Read specific OpenEXR channels according to an EXRMatrixMapping, using the
    recycled FrameBufferPool for zero heap allocations.
    """
    if not path or not os.path.exists(path):
        return None

    # 0. Accelerated Rust Engine: Zero-GIL, parallel multi-threaded extraction
    try:
        from openreview.core.image_io import _is_rust_io_enabled
        if _is_rust_io_enabled():
            import openreview_engine
            rust_eng = getattr(read_exr_matrix_mapped, "_rust_engine", None)
            if rust_eng is None:
                rust_eng = openreview_engine.OpenReviewEngine()
                read_exr_matrix_mapped._rust_engine = rust_eng

            native_frame = rust_eng.read_exr(
                path,
                as_f16=as_f16,
                layer=None,
                r_ch=mapping.r_channel,
                g_ch=mapping.g_channel,
                b_ch=mapping.b_channel,
                a_ch=mapping.a_channel,
                auto_normalize_depth=mapping.auto_normalize_depth,
                remap_normals=mapping.remap_normals,
                crypto_layer=mapping.crypto_layer,
            )
            dtype = np.float16 if as_f16 else np.float32
            shape = (native_frame.height, native_frame.width, 4)
            out = FrameBufferPool.instance().acquire(shape, dtype)
            np.copyto(out, np.frombuffer(native_frame.as_memoryview(), dtype=dtype).reshape(shape))
            return out
    except Exception:
        pass

    try:
        import OpenEXR'''
    assert old_read_mapped in content, "Failed to find read_exr_matrix_mapped target in exr_matrix.py"
    content = content.replace(old_read_mapped, new_read_mapped, 1)

    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(content)
    print("Successfully patched exr_matrix.py")

def patch_exr_contact_sheet():
    path = "src/openreview/core/exr_contact_sheet.py"
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()

    old_draw = '''    h, w = canvas_rgba_float.shape[:2]
    if h <= 0 or w <= 0 or not tile_rects:
        return canvas_rgba_float

    # Convert float32 [0..1] to uint8 RGBA QImage for high-quality antialiased text rendering'''
    new_draw = '''    h, w = canvas_rgba_float.shape[:2]
    if h <= 0 or w <= 0 or not tile_rects:
        return canvas_rgba_float

    from PySide6.QtGui import QGuiApplication
    if not QGuiApplication.instance():
        return canvas_rgba_float

    # Convert float32 [0..1] to uint8 RGBA QImage for high-quality antialiased text rendering'''
    assert old_draw in content, "Failed to find draw_contact_sheet_badges target in exr_contact_sheet.py"
    content = content.replace(old_draw, new_draw, 1)

    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(content)
    print("Successfully patched exr_contact_sheet.py")

if __name__ == "__main__":
    patch_image_io()
    patch_exr_matrix()
    patch_exr_contact_sheet()

def patch_main_window():
    path = "src/openreview/ui/main_window.py"
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()

    old_cf = '''        layer_frames = {}
        for lyr in lyrs:
            arr = read_image_file(f_path, layer=lyr)
            if arr is not None:
                layer_frames[lyr] = arr'''
    new_cf = '''        layer_frames = {}
        from openreview.core.image_io import _is_rust_io_enabled
        if _is_rust_io_enabled():
            try:
                import openreview_engine
                rust_eng = getattr(self, "_rust_engine", None)
                if rust_eng is None:
                    rust_eng = openreview_engine.OpenReviewEngine()
                    self._rust_engine = rust_eng
                all_raw = rust_eng.read_all_exr_layers(f_path, False)
                for lyr in lyrs:
                    if lyr in all_raw:
                        nf = all_raw[lyr]
                        layer_frames[lyr] = np.frombuffer(nf.as_memoryview(), dtype=np.float32).reshape((nf.height, nf.width, 4))
            except Exception:
                pass

        if not layer_frames:
            for lyr in lyrs:
                arr = read_image_file(f_path, layer=lyr)
                if arr is not None:
                    layer_frames[lyr] = arr'''
    assert old_cf in content, "Failed to find contact sheet loop in main_window.py"
    content = content.replace(old_cf, new_cf, 1)

    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(content)
    print("Successfully patched main_window.py")

if __name__ == "__main__":
    patch_main_window()
