"""
DCC Integration Helpers for OpenReview SDK.
Provides ready-to-use helpers for Foundry Nuke, Autodesk Maya, and SideFX Houdini.
"""
from __future__ import annotations

import os
import subprocess
from typing import Optional
from openreview.sdk.client import OpenReviewClient, OpenReviewError


def review_in_openreview(media_path: str, in_frame: Optional[int] = None, out_frame: Optional[int] = None) -> bool:
    """
    Open media in an active OpenReview session, or launch OpenReview if not currently running.
    """
    client = OpenReviewClient()
    connected = client.ping()
    if not connected:
        # Check standard Windows / macOS / Linux executable locations
        candidates = [
            "openreview",
            r"C:\Program Files\OpenReview\OpenReview.exe",
            r"C:\Program Files (x86)\OpenReview\OpenReview.exe",
            os.path.expandvars(r"%LOCALAPPDATA%\Programs\OpenReview\OpenReview.exe"),
        ]
        launched = False
        for exe in candidates:
            if os.path.isabs(exe) and not os.path.isfile(exe):
                continue
            try:
                subprocess.Popen([exe, media_path])
                launched = True
                break
            except (FileNotFoundError, OSError):
                continue

        if not launched:
            try:
                subprocess.Popen(["python", "-m", "openreview.app", media_path])
                launched = True
            except (FileNotFoundError, OSError):
                pass

        return launched

    client.load_media(media_path)
    if in_frame is not None and out_frame is not None:
        client.set_in_out(in_frame, out_frame)
    client.play()
    return True


def _resolve_nuke_node_path(node) -> tuple[str, int, int]:
    """Resolve an actual existing file path from a Nuke Read or Write node."""
    import re
    import nuke  # type: ignore

    first = int(node["first"].value()) if "first" in node.knobs() else int(nuke.root()["first_frame"].value())
    last = int(node["last"].value()) if "last" in node.knobs() else int(nuke.root()["last_frame"].value())
    raw_path = nuke.filename(node) or (node["file"].value() if "file" in node.knobs() else "")

    if not raw_path:
        return "", first, last

    # If it is a video file or single image and already exists on disk
    if os.path.exists(raw_path):
        return os.path.normpath(raw_path), first, last

    def sub_frame(path_str: str, f: int) -> str:
        if "%" in path_str:
            path_str = re.sub(r"%0?(\d*)d", lambda m: f"{int(f):0{int(m.group(1)) if m.group(1) else 1}d}", path_str)
        if "#" in path_str:
            path_str = re.sub(r"#+", lambda m: f"{int(f):0{len(m.group(0))}d}", path_str)
        return path_str

    # Test candidate frames: current Nuke frame, first frame, last frame
    for frame_candidate in [int(nuke.frame()), first, last]:
        resolved = sub_frame(raw_path, frame_candidate)
        if os.path.exists(resolved):
            return os.path.normpath(resolved), first, last

    # Fallback to first frame formatted
    return os.path.normpath(sub_frame(raw_path, first)), first, last


# ------------------------------------------------------------------------------
# Foundry Nuke Helper
# ------------------------------------------------------------------------------
def nuke_send_selected_read() -> None:
    """
    Nuke script: Send currently selected Read node or Write node render to OpenReview.
    Add this function to ~/.nuke/menu.py:
        import nuke
        from openreview.sdk.dcc import nuke_send_selected_read
        nuke.menu('Nuke').addCommand('Pipeline/Review in OpenReview', nuke_send_selected_read, 'Ctrl+Alt+R')
    """
    try:
        import nuke  # type: ignore
    except ImportError:
        raise RuntimeError("nuke_send_selected_read must be called inside Foundry Nuke.")

    selected = nuke.selectedNodes()
    if not selected:
        nuke.message("Please select a Read or Write node to review.")
        return

    node = selected[0]
    if node.Class() in ("Read", "Write"):
        file_path, first, last = _resolve_nuke_node_path(node)
        if not file_path:
            nuke.message("Could not resolve filename from selected node.")
            return
        review_in_openreview(file_path, first, last)
    else:
        nuke.message("Selected node is not a Read or Write node.")


# ------------------------------------------------------------------------------
# Autodesk Maya Helper
# ------------------------------------------------------------------------------
def maya_review_latest_playblast() -> None:
    """
    Maya script: Send playblast to OpenReview.
    Add to Maya shelf:
        from openreview.sdk.dcc import maya_review_latest_playblast
        maya_review_latest_playblast()
    """
    try:
        import maya.cmds as cmds  # type: ignore
    except ImportError:
        raise RuntimeError("maya_review_latest_playblast must be called inside Autodesk Maya.")

    start_frame = int(cmds.playbackOptions(q=True, min=True))
    end_frame = int(cmds.playbackOptions(q=True, max=True))
    # Trigger playblast
    playblast_path = cmds.playblast(
        format="qt",
        compression="H.264",
        forceOverwrite=True,
        showOrnaments=True,
        viewer=False,
    )
    if playblast_path and os.path.exists(playblast_path):
        review_in_openreview(playblast_path, start_frame, end_frame)


def get_nuke_menu_script(port: int = 45124) -> str:
    """Generate Python boilerplate code for Nuke menu.py integration."""
    return f"""# OpenReview Nuke Integration
import os
import re
import json
import time
import socket
import subprocess
import nuke

OPENREVIEW_IPC_HOST = "127.0.0.1"
OPENREVIEW_IPC_PORT = {port}

def resolve_nuke_node_path(node):
    if not node:
        return None, None, None
    raw_path = nuke.filename(node) or (node["file"].value() if "file" in node.knobs() else "")
    if not raw_path:
        return None, None, None
    first = int(node["first"].value()) if "first" in node.knobs() else int(nuke.root()["first_frame"].value())
    last = int(node["last"].value()) if "last" in node.knobs() else int(nuke.root()["last_frame"].value())
    if os.path.isfile(raw_path):
        return os.path.normpath(raw_path), first, last

    def sub_frame(path_str, frame_num):
        if "%" in path_str:
            path_str = re.sub(r"%0?(\d*)d", lambda m: f"{{int(frame_num):0{{int(m.group(1)) if m.group(1) else 1}}d}}", path_str)
        if "#" in path_str:
            path_str = re.sub(r"#+", lambda m: f"{{int(frame_num):0{{len(m.group(0))}}d}}", path_str)
        return path_str

    candidates = [int(nuke.frame()), first, last]
    for cand in candidates:
        cand_path = sub_frame(raw_path, cand)
        if os.path.exists(cand_path):
            return os.path.normpath(cand_path), first, last

    return os.path.normpath(sub_frame(raw_path, first)), first, last

def _send_rpc(sock, method, params=None, msg_id=1, timeout=5.0):
    payload = {{"jsonrpc": "2.0", "id": msg_id, "method": method, "params": params or {{}}}}
    data = (json.dumps(payload) + "\\n").encode("utf-8")
    sock.settimeout(timeout)
    sock.sendall(data)
    resp_bytes = b""
    while not resp_bytes.endswith(b"\\n"):
        chunk = sock.recv(4096)
        if not chunk:
            break
        resp_bytes += chunk
    if not resp_bytes:
        return None
    try:
        resp = json.loads(resp_bytes.decode("utf-8", errors="replace"))
        return resp.get("result")
    except Exception:
        return None

def review_in_openreview(media_path, in_frame=None, out_frame=None):
    if not os.path.exists(media_path):
        nuke.message(f"File not found on disk:\\n{{media_path}}\\n\\nPlease check if the frames have been rendered.")
        return False
    norm_path = os.path.normpath(os.path.abspath(media_path))
    def _try_connect():
        try:
            return socket.create_connection((OPENREVIEW_IPC_HOST, OPENREVIEW_IPC_PORT), timeout=0.5)
        except (ConnectionRefusedError, socket.timeout, OSError):
            return None

    sock = _try_connect()
    if sock is None:
        possible_exes = [
            r"C:\\Program Files\\OpenReview\\OpenReview.exe",
            r"C:\\Program Files (x86)\\OpenReview\\OpenReview.exe",
            os.path.expandvars(r"%LOCALAPPDATA%\\Programs\\OpenReview\\OpenReview.exe"),
        ]
        exe = next((p for p in possible_exes if os.path.isfile(p)), "OpenReview.exe")
        try:
            subprocess.Popen([exe])
        except Exception as err:
            nuke.message(f"Failed to launch OpenReview: {{err}}")
            return False
        t_start = time.time()
        while time.time() - t_start < 6.0:
            time.sleep(0.2)
            sock = _try_connect()
            if sock is not None:
                break

    if sock is None:
        nuke.message("Could not connect to OpenReview IPC Server.")
        return False

    with sock:
        _send_rpc(sock, "load_media", {{"path": norm_path, "reload": True, "activate": True}}, msg_id=1, timeout=8.0)
        if in_frame is not None and out_frame is not None:
            _send_rpc(sock, "set_in_out", {{"in_frame": int(in_frame), "out_frame": int(out_frame)}}, msg_id=2, timeout=3.0)
        try:
            _send_rpc(sock, "seek", {{"frame": int(nuke.frame())}}, msg_id=3, timeout=2.0)
        except Exception:
            pass
        _send_rpc(sock, "activate_window", {{}}, msg_id=4, timeout=2.0)
    return True

def _openreview_selected():
    nodes = nuke.selectedNodes()
    if not nodes:
        nuke.message("Select a Read or Write node first.")
        return
    node = nodes[0]
    if node.Class() in ("Read", "Write", "DeepRead", "DeepWrite"):
        path, first, last = resolve_nuke_node_path(node)
        if not path:
            nuke.message("Selected node does not have a valid file path.")
            return
        review_in_openreview(path, in_frame=first, out_frame=last)
    else:
        nuke.message("Select a Read or Write node.")

nuke.menu('Nuke').addCommand('Review/Review in OpenReview', _openreview_selected, 'Ctrl+Alt+R')
"""


def get_maya_shelf_script(port: int = 45124) -> str:
    """Generate Python boilerplate code for Maya shelf button."""
    return f"""# OpenReview Maya Shelf Integration
import maya.cmds as cmds
from openreview.sdk import OpenReviewClient

client = OpenReviewClient(port={port})
start = int(cmds.playbackOptions(q=True, min=True))
end = int(cmds.playbackOptions(q=True, max=True))
pb = cmds.playblast(format="qt", compression="H.264", forceOverwrite=True, viewer=False)
if pb:
    client.load_media(pb)
    client.set_in_out(start, end)
    client.play()
"""

