"""
Application Bootstrap and Entry Point for OpenReview.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

_SRC_DIR = str(Path(__file__).resolve().parent.parent)
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)

from PySide6.QtCore import Qt
from PySide6.QtGui import QSurfaceFormat
from PySide6.QtWidgets import QApplication

from openreview.ui.main_window import MainWindow


def create_application() -> QApplication:
    # Set Windows Process AppUserModelID for taskbar icon association
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("openreview.vfx.app")
        except Exception:
            pass

    # Configure high-DPI behavior
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )

    # Configure Default OpenGL Core Profile Surface Format
    gl_format = QSurfaceFormat()
    gl_format.setVersion(3, 3)
    gl_format.setProfile(QSurfaceFormat.OpenGLContextProfile.CoreProfile)
    gl_format.setRenderableType(QSurfaceFormat.RenderableType.OpenGL)
    gl_format.setSwapInterval(0)  # Master clock-driven frame pacing (disables 60Hz integer divisor locks)
    QSurfaceFormat.setDefaultFormat(gl_format)

    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
        app.setApplicationName("OpenReview")
        app.setOrganizationName("OpenReview")
    app.setStyle("Fusion")

    from openreview.resources import get_app_icon
    app.setWindowIcon(get_app_icon())

    return app


def main() -> int:
    import os

    # Command-line registration flags
    if "--register-associations" in sys.argv:
        from openreview.core.file_associations import register_file_associations
        success = register_file_associations()
        print("File associations registered successfully." if success else "Failed to register file associations.")
        return 0 if success else 1

    if "--unregister-associations" in sys.argv:
        from openreview.core.file_associations import unregister_file_associations
        success = unregister_file_associations()
        print("File associations unregistered." if success else "Failed to unregister file associations.")
        return 0 if success else 1

    # Extract initial file path if launched via Explorer double-click or CLI argument
    initial_file = None
    for arg in sys.argv[1:]:
        clean_arg = arg.strip("\"'")
        if clean_arg and not clean_arg.startswith("-") and os.path.exists(clean_arg):
            initial_file = clean_arg
            break

    # Single-instance enforcement: Forward to running OpenReview instance if available
    if "--new-instance" not in sys.argv and "--multi-instance" not in sys.argv:
        try:
            import json
            import socket
            with socket.create_connection(("127.0.0.1", 45124), timeout=0.25) as s:
                if initial_file:
                    abs_path = os.path.abspath(initial_file)
                    req = {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "load_media",
                        "params": {"path": abs_path, "reload": True, "activate": True},
                    }
                    s.sendall((json.dumps(req) + "\n").encode("utf-8"))
                    s.settimeout(5.0)
                    try:
                        s.recv(4096)
                    except Exception:
                        pass
                else:
                    req = {"jsonrpc": "2.0", "id": 1, "method": "activate_window", "params": {}}
                    s.sendall((json.dumps(req) + "\n").encode("utf-8"))
                    s.settimeout(2.0)
                    try:
                        s.recv(4096)
                    except Exception:
                        pass
                return 0
        except (ConnectionRefusedError, socket.timeout, OSError):
            pass

    app = create_application()
    window = MainWindow()
    window.show()

    if initial_file:
        window.load_initial_file(initial_file)

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
