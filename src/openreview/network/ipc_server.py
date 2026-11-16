"""
Local IPC Server for OpenReview.
Enables programmatic external control via JSON-RPC 2.0 over TCP (e.g. from Nuke, Maya, CLI scripts).
"""
from __future__ import annotations

import json
import logging
from typing import Optional, Any, Dict
from PySide6.QtCore import QObject, Signal, QByteArray
from PySide6.QtNetwork import QTcpServer, QTcpSocket, QHostAddress

logger = logging.getLogger(__name__)

DEFAULT_IPC_PORT = 45124


class IpcServer(QObject):
    """
    Asynchronous JSON-RPC 2.0 TCP server running on localhost inside OpenReview's main Qt loop.
    Dispatches incoming automation and control commands directly to the active MainWindow instance.
    """

    command_executed = Signal(str, dict)

    def __init__(self, main_window: Optional[Any] = None, parent: Optional[QObject] = None):
        super().__init__(parent)
        self.main_window = main_window
        self._server = QTcpServer(self)
        self._server.newConnection.connect(self._on_new_connection)
        self._clients: list[QTcpSocket] = []
        self._buffers: dict[QTcpSocket, QByteArray] = {}

    @property
    def is_listening(self) -> bool:
        return self._server.isListening()

    @property
    def server_port(self) -> int:
        return self._server.serverPort() if self._server.isListening() else 0

    def start(self, host: str = "127.0.0.1", port: int = DEFAULT_IPC_PORT) -> bool:
        """Start listening for IPC connections on localhost."""
        if self._server.isListening():
            return True

        address = QHostAddress(host)
        success = self._server.listen(address, port)
        if success:
            logger.info("OpenReview IPC Server listening on %s:%d", host, self._server.serverPort())
        else:
            logger.warning("Failed to start OpenReview IPC Server on %s:%d: %s", host, port, self._server.errorString())
        return success

    def stop(self) -> None:
        """Stop listening and disconnect all clients."""
        for client in list(self._clients):
            client.disconnectFromHost()
        self._clients.clear()
        self._buffers.clear()
        if self._server.isListening():
            self._server.close()
            logger.info("OpenReview IPC Server stopped.")

    def _on_new_connection(self) -> None:
        while self._server.hasPendingConnections():
            socket = self._server.nextPendingConnection()
            self._clients.append(socket)
            self._buffers[socket] = QByteArray()
            socket.readyRead.connect(lambda s=socket: self._on_ready_read(s))
            socket.disconnected.connect(lambda s=socket: self._on_disconnected(s))

    def _on_disconnected(self, socket: QTcpSocket) -> None:
        if socket in self._clients:
            self._clients.remove(socket)
        if socket in self._buffers:
            del self._buffers[socket]
        socket.deleteLater()

    def _on_ready_read(self, socket: QTcpSocket) -> None:
        buf = self._buffers.get(socket)
        if buf is None:
            return

        buf.append(socket.readAll())

        # Process newline-delimited JSON-RPC messages
        while b"\n" in buf:
            idx = buf.indexOf(b"\n")
            line = bytes(buf.left(idx)).decode("utf-8", errors="replace").strip()
            buf.remove(0, idx + 1)

            if line:
                try:
                    request = json.loads(line)
                    response = self._handle_rpc_request(request)
                except Exception as e:
                    response = {
                        "jsonrpc": "2.0",
                        "id": None,
                        "error": {"code": -32700, "message": f"Parse error: {e}"},
                    }
                resp_bytes = (json.dumps(response) + "\n").encode("utf-8")
                socket.write(resp_bytes)
                socket.flush()

    def _handle_rpc_request(self, req: dict) -> dict:
        req_id = req.get("id")
        method = req.get("method")
        params = req.get("params", {})
        if not isinstance(params, dict):
            params = {}

        if not method or not isinstance(method, str):
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32600, "message": "Invalid Request: method is required"},
            }

        handler = getattr(self, f"_rpc_{method}", None)
        if handler is None:
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32601, "message": f"Method not found: '{method}'"},
            }

        try:
            result = handler(params)
            self.command_executed.emit(method, params)
            return {"jsonrpc": "2.0", "id": req_id, "result": result}
        except Exception as e:
            logger.exception("Error executing IPC method '%s'", method)
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32000, "message": str(e)},
            }

    # --------------------------------------------------------------------------
    # RPC Handlers
    # --------------------------------------------------------------------------
    def _rpc_ping(self, params: dict) -> dict:
        return {"status": "pong", "app": "OpenReview", "version": "0.1.0"}

    def _rpc_get_status(self, params: dict) -> dict:
        win = self.main_window
        if not win:
            return {"connected": True, "window": None}

        current_media = getattr(win, "_current_media_path", None)
        return {
            "media_path": current_media,
            "media_type": getattr(win, "_current_media_type", "none"),
            "current_frame": win.clock.current_frame,
            "start_frame": win.clock.start_frame,
            "end_frame": win.clock.end_frame,
            "in_frame": win.clock.in_frame,
            "out_frame": win.clock.out_frame,
            "fps": win.clock.fps,
            "is_playing": win.clock.is_playing,
            "loop_mode": win.clock.loop_mode,
            "exposure_ev": win.viewport.exposure_ev,
            "gain": win.viewport.gain,
            "gamma": win.viewport.gamma,
            "saturation": win.viewport.saturation,
            "channel_mode": win.viewport.channel_mode,
            "playback_engine": str(getattr(win.viewport, "playback_engine", "opengl_core")),
            "bookmarks": sorted(list(win.clock.bookmarks)),
        }

    def _rpc_activate_window(self, params: dict) -> dict:
        win = self.main_window
        if not win:
            return {"status": "error", "message": "MainWindow not attached"}

        from PySide6.QtCore import Qt
        if win.isMinimized():
            win.setWindowState(win.windowState() & ~Qt.WindowState.WindowMinimized | Qt.WindowState.WindowActive)
        win.show()
        win.raise_()
        win.activateWindow()

        import sys
        if sys.platform == "win32":
            try:
                import ctypes
                hwnd = int(win.winId())
                ctypes.windll.user32.SetForegroundWindow(hwnd)
            except Exception:
                pass

        return {"status": "activated"}

    def _rpc_load_media(self, params: dict) -> dict:
        path = params.get("path")
        if not path:
            raise ValueError("Missing 'path' argument")

        reload_flag = bool(params.get("reload", True))
        activate_flag = bool(params.get("activate", True))

        win = self.main_window
        if win and hasattr(win, "_load_path"):
            win._load_path(path, force_reload=reload_flag)
            if activate_flag:
                self._rpc_activate_window({})
            return {"status": "loaded", "path": path}
        return {"status": "error", "message": "MainWindow not attached"}

    def _rpc_reload_media(self, params: dict) -> dict:
        path = params.get("path")
        win = self.main_window
        if not win:
            return {"status": "error", "message": "MainWindow not attached"}
        target_path = path or getattr(win, "_current_media_path", None)
        if target_path and hasattr(win, "_load_path"):
            win._load_path(target_path, force_reload=True)
            self._rpc_activate_window({})
            return {"status": "reloaded", "path": target_path}
        return {"status": "error", "message": "No media to reload"}

    def _rpc_play(self, params: dict) -> dict:
        if self.main_window and hasattr(self.main_window, "clock"):
            self.main_window.clock.play()
            return {"is_playing": True}
        return {"is_playing": False}

    def _rpc_pause(self, params: dict) -> dict:
        if self.main_window and hasattr(self.main_window, "clock"):
            self.main_window.clock.pause()
            return {"is_playing": False}
        return {"is_playing": False}

    def _rpc_toggle_play(self, params: dict) -> dict:
        if self.main_window and hasattr(self.main_window, "clock"):
            self.main_window.clock.toggle_playback()
            return {"is_playing": self.main_window.clock.is_playing}
        return {"is_playing": False}

    def _rpc_seek(self, params: dict) -> dict:
        frame = params.get("frame")
        if frame is None:
            raise ValueError("Missing 'frame' argument")
        if self.main_window and hasattr(self.main_window, "clock"):
            self.main_window.clock.set_frame(int(frame))
            return {"current_frame": self.main_window.clock.current_frame}
        return {"current_frame": None}

    def _rpc_set_fps(self, params: dict) -> dict:
        fps = params.get("fps")
        if fps is None:
            raise ValueError("Missing 'fps' argument")
        if self.main_window and hasattr(self.main_window, "clock"):
            self.main_window.clock.set_fps(float(fps))
            return {"fps": self.main_window.clock.fps}
        return {"fps": None}

    def _rpc_set_in_out(self, params: dict) -> dict:
        in_f = params.get("in_frame")
        out_f = params.get("out_frame")
        if in_f is None or out_f is None:
            raise ValueError("Missing 'in_frame' or 'out_frame'")
        if self.main_window and hasattr(self.main_window, "clock"):
            self.main_window.clock.set_in_out(int(in_f), int(out_f))
            return {"in_frame": self.main_window.clock.in_frame, "out_frame": self.main_window.clock.out_frame}
        return {}

    def _rpc_set_color(self, params: dict) -> dict:
        win = self.main_window
        if not win or not hasattr(win, "viewport"):
            return {"status": "error"}

        if "exposure" in params:
            ev = float(params["exposure"])
            win.viewport.exposure_ev = ev
            win.top_bar.set_exposure(ev)

        if "gain" in params:
            g = float(params["gain"])
            win.viewport.gain = g
            win.top_bar.gain_slider.set_value(g)

        if "gamma" in params:
            gam = float(params["gamma"])
            win.viewport.gamma = gam
            win.top_bar.gamma_slider.set_value(gam)

        if "saturation" in params:
            sat = float(params["saturation"])
            win.viewport.saturation = sat
            win.top_bar.sat_slider.set_value(sat)

        win.viewport.update()
        return {
            "exposure": win.viewport.exposure_ev,
            "gain": win.viewport.gain,
            "gamma": win.viewport.gamma,
            "saturation": win.viewport.saturation,
        }

    def _rpc_add_bookmark(self, params: dict) -> dict:
        frame = params.get("frame")
        win = self.main_window
        if win and hasattr(win, "clock"):
            f = int(frame) if frame is not None else win.clock.current_frame
            win.clock.add_bookmark(f)
            return {"bookmarks": sorted(list(win.clock.bookmarks))}
        return {}

    def _rpc_clear_bookmarks(self, params: dict) -> dict:
        win = self.main_window
        if win and hasattr(win, "clock"):
            win.clock.clear_all_bookmarks()
            return {"bookmarks": []}
        return {}

    def _rpc_export_frame(self, params: dict) -> dict:
        out_path = params.get("destination")
        if not out_path:
            raise ValueError("Missing 'destination' filepath")
        win = self.main_window
        if win and hasattr(win, "viewport"):
            pix = win.viewport.grab()
            pix.save(out_path)
            return {"status": "exported", "destination": out_path}
        return {"status": "error"}
