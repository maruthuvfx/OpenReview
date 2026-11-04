# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path
import sys

block_cipher = None

project_root = Path.cwd().resolve()
src_dir = project_root / "src"

datas = [
    (str(src_dir / "openreview" / "resources" / "icons"), "openreview/resources/icons"),
    (str(src_dir / "openreview" / "packages" / "builtin"), "openreview/packages/builtin"),
    (str(project_root / "docs" / "html"), "docs/html"),
]

for svg_name in [
    "openreview-primary-logo.svg",
    "openreview-logo-horizontal.svg",
]:
    svg_file = project_root / svg_name
    if svg_file.exists():
        datas.append((str(svg_file), "."))
        datas.append((str(svg_file), "openreview/resources"))

hidden_imports = [
    "PySide6.QtCore",
    "PySide6.QtGui",
    "PySide6.QtWidgets",
    "PySide6.QtNetwork",
    "PySide6.QtOpenGL",
    "PySide6.QtOpenGLWidgets",
    "PySide6.QtMultimedia",
    "PySide6.QtSvg",
    "PySide6.QtSvgWidgets",
    "openreview",
    "openreview.app",
    "openreview.core",
    "openreview.core.stereo",
    "openreview.core.image_io",
    "openreview.models",
    "openreview.widgets",
    "openreview.ui",
    "openreview.network",
    "openreview.packages",
    "openreview.sdk",
    "openreview.resources",
    "openreview.color",
    "numpy",
    "PIL",
    "PIL.Image",
    "cv2",
    "pypdf",
]

# Check optional libraries
try:
    import opencolorio
    hidden_imports.append("opencolorio")
except ImportError:
    pass

try:
    import OpenEXR
    hidden_imports.extend(["OpenEXR", "Imath"])
except ImportError:
    pass

# High-Performance Video Engine (PyAV / FFmpeg)
try:
    import av
    hidden_imports.extend(["av", "av.video", "av.codec", "av.format", "av.packet", "av.stream"])
except ImportError:
    pass

# Native Rust Acceleration Engine
try:
    import openreview_engine
    hidden_imports.append("openreview_engine")
except ImportError:
    pass

binaries = []
site_packages = Path(sys.prefix) / "Lib" / "site-packages"
if site_packages.is_dir():
    for dll_path in site_packages.glob("msvcp140*.dll"):
        binaries.append((str(dll_path), "."))

# Bundle native openreview_engine binaries and package files
try:
    import openreview_engine
    engine_pkg_dir = Path(openreview_engine.__file__).parent
    for pyd_file in engine_pkg_dir.glob("*.pyd"):
        binaries.append((str(pyd_file), "openreview_engine"))
    init_py = engine_pkg_dir / "__init__.py"
    if init_py.exists():
        datas.append((str(init_py), "openreview_engine"))
except Exception:
    pass

a = Analysis(
    [str(src_dir / "openreview" / "app.py")],
    pathex=[str(src_dir)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "scipy"],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="OpenReview",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,  # GUI application, no console window
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(src_dir / "openreview" / "resources" / "icons" / "openreview.ico"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="Openreview_build",
)
