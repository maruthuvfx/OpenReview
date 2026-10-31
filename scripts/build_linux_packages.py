"""
OpenReview Linux Package Builder (Debian .deb & RedHat .rpm Editions).
Generates standards-compliant binary packages inside Openreview_build/:
  1. Openreview_build/openreview_1.0.0_amd64.deb      (Debian / Ubuntu / Linux Mint 64-bit)
  2. Openreview_build/openreview-1.0.0-1.x86_64.rpm   (RedHat / RHEL / Rocky / Alma / Fedora 64-bit)
"""
from __future__ import annotations

import gzip
import hashlib
import io
import os
import struct
import sys
import tarfile
import time
from dataclasses import dataclass
from pathlib import Path


PKG_NAME = "openreview"
PKG_VERSION = "1.3.0"
PKG_RELEASE = "1"
PKG_ARCH_DEB = "amd64"
PKG_ARCH_RPM = "x86_64"
MAINTAINER = "Maruthu Chakravarthi <https://github.com/maruthuvfx/OpenReview>"
VENDOR = "Maruthu Chakravarthi"
HOMEPAGE = "https://github.com/maruthuvfx/OpenReview"
SUMMARY = "Studio-grade VFX sequence playback, HDR color inspection, and dailies review workstation"
DESCRIPTION = (
    "OpenReview is a studio-grade visual effects (VFX) sequence playback,\n"
    " high-dynamic-range (HDR) color inspection, multi-source compositing\n"
    " comparison, and collaborative dailies review desktop workstation\n"
    " application powered by Python 3, PySide6 (Qt6), OpenGL 3.3+ Core Profile,\n"
    " and OpenColorIO v2 (ACES 1.3 / 2.0)."
)


@dataclass
class PackageEntry:
    """Represents a file or symlink to be packaged into Linux .deb and .rpm archives."""
    install_path: str  # POSIX absolute path starting with '/', e.g. '/opt/openreview/run.py'
    data: bytes
    mode: int          # Permission bits, e.g. 0o755 or 0o644
    mtime: int


def collect_linux_payload(project_root: Path) -> list[PackageEntry]:
    """Collect all OpenReview application files, launcher scripts, desktop entries, and icons."""
    now = int(time.time())
    entries: list[PackageEntry] = []

    def add_bytes(install_path: str, content: bytes, mode: int = 0o644) -> None:
        entries.append(PackageEntry(install_path=install_path, data=content, mode=mode, mtime=now))

    def add_tree(src_root: Path, target_prefix: str) -> None:
        if not src_root.exists():
            return
        for root, dirs, files in os.walk(src_root):
            dirs[:] = [d for d in sorted(dirs) if d != "__pycache__" and not d.endswith(".egg-info")]
            root_path = Path(root)
            for fname in sorted(files):
                if fname.endswith((".pyc", ".pyo", ".pyd", ".dll", ".exe", ".msi", ".zip", ".cab")):
                    continue
                fpath = root_path / fname
                rel = fpath.relative_to(src_root).as_posix()
                dest = f"{target_prefix}/{rel}"
                raw = fpath.read_bytes()
                # Normalize text files to LF for Linux
                if fname.endswith((".py", ".toml", ".md", ".txt", ".html", ".css", ".js", ".json", ".svg", ".glsl", ".vert", ".frag")):
                    raw = raw.replace(b"\r\n", b"\n")
                mode = 0o755 if fname.endswith(".sh") else 0o644
                add_bytes(dest, raw, mode=mode)

    # 1. Core Python source package & resources
    add_tree(project_root / "src" / "openreview", "/opt/openreview/src/openreview")

    # 2. Offline HTML Documentation portal
    add_tree(project_root / "docs" / "html", "/opt/openreview/docs/html")

    # 3. Root entry points and metadata
    for root_file in ("run.py", "pyproject.toml", "README.md", "ARCHITECTURE.md", "openreview-primary-logo.svg", "openreview-logo-horizontal.svg"):
        p = project_root / root_file
        if p.is_file():
            raw = p.read_bytes().replace(b"\r\n", b"\n")
            mode = 0o755 if root_file == "run.py" else 0o644
            add_bytes(f"/opt/openreview/{root_file}", raw, mode=mode)

    # 4. Executable /usr/bin/openreview launcher script
    launcher_sh = (
        "#!/bin/sh\n"
        "# OpenReview 64-bit Linux Workstation Launcher\n"
        "set -e\n"
        "APP_DIR=\"/opt/openreview\"\n"
        "export PYTHONPATH=\"${APP_DIR}/src:${PYTHONPATH}\"\n"
        "if [ -x \"${APP_DIR}/.venv/bin/python3\" ]; then\n"
        "    exec \"${APP_DIR}/.venv/bin/python3\" -m openreview.app \"$@\"\n"
        "else\n"
        "    exec /usr/bin/env python3 -m openreview.app \"$@\"\n"
        "fi\n"
    ).encode("utf-8")
    add_bytes("/usr/bin/openreview", launcher_sh, mode=0o755)

    # 5. Freedesktop .desktop entry
    desktop_entry = (
        "[Desktop Entry]\n"
        "Version=1.0\n"
        "Type=Application\n"
        "Name=OpenReview\n"
        "GenericName=VFX Sequence & Dailies Review Workstation\n"
        f"Comment={SUMMARY}\n"
        "Exec=/usr/bin/openreview %F\n"
        "Icon=openreview\n"
        "Terminal=false\n"
        "Categories=Graphics;2DGraphics;RasterGraphics;AudioVideo;Video;Viewer;\n"
        "MimeType=image/x-exr;image/x-dpx;image/tiff;image/png;image/jpeg;video/mp4;video/quicktime;\n"
        "StartupWMClass=OpenReview\n"
        "Keywords=VFX;EXR;DPX;ACES;OpenColorIO;Dailies;Playback;Sequence;\n"
    ).encode("utf-8")
    add_bytes("/usr/share/applications/openreview.desktop", desktop_entry, mode=0o644)

    # 6. Hicolor application icons (PNG + SVG)
    icon_png = project_root / "src" / "openreview" / "resources" / "icons" / "openreview.png"
    if icon_png.is_file():
        add_bytes("/usr/share/icons/hicolor/256x256/apps/openreview.png", icon_png.read_bytes(), mode=0o644)
    icon_svg = project_root / "openreview-primary-logo.svg"
    if icon_svg.is_file():
        add_bytes(
            "/usr/share/icons/hicolor/scalable/apps/openreview.svg",
            icon_svg.read_bytes().replace(b"\r\n", b"\n"),
            mode=0o644,
        )

    # 7. Linux package documentation & copyright
    copyright_txt = (
        "Format: https://www.debian.org/doc/packaging-manuals/copyright-format/1.0/\n"
        "Upstream-Name: openreview\n"
        f"Source: {HOMEPAGE}\n\n"
        "Files: *\n"
        "Copyright: 2026 Maruthu Chakravarthi / OpenReview Technologies\n"
        "License: OpenReview-Studio-License\n"
        " Subject to the OpenReview Studio Workstation End-User License Agreement.\n"
    ).encode("utf-8")
    add_bytes("/usr/share/doc/openreview/copyright", copyright_txt, mode=0o644)

    entries.sort(key=lambda e: e.install_path)
    return entries


# =============================================================================
# 1. Debian (.deb) Package Builder
# =============================================================================

def _make_tar_gz(members: list[tuple[str, bytes, int, int, int]]) -> bytes:
    """Create a deterministic gzipped POSIX tar archive in memory.
    Each tuple: (arcname, data_bytes, mode, mtime, tar_type) where tar_type 0=file, 5=dir.
    """
    raw_tar = io.BytesIO()
    with tarfile.open(fileobj=raw_tar, mode="w", format=tarfile.GNU_FORMAT) as tf:
        for arcname, data, mode, mtime, is_dir in members:
            ti = tarfile.TarInfo(name=arcname)
            ti.mtime = mtime
            ti.uid = 0
            ti.gid = 0
            ti.uname = "root"
            ti.gname = "root"
            if is_dir:
                ti.type = tarfile.DIRTYPE
                ti.mode = 0o755
                ti.size = 0
                tf.addfile(ti)
            else:
                ti.type = tarfile.REGTYPE
                ti.mode = mode
                ti.size = len(data)
                tf.addfile(ti, io.BytesIO(data))

    gz_buf = io.BytesIO()
    with gzip.GzipFile(filename="", mode="wb", fileobj=gz_buf, compresslevel=9, mtime=0) as gz:
        gz.write(raw_tar.getvalue())
    return gz_buf.getvalue()


def _ar_member(name: str, data: bytes, mtime: int, mode: int = 0o100644) -> bytes:
    """Pack a single file into a standard Unix System V 'ar' archive member."""
    header = (
        f"{name:<16}"
        f"{mtime:<12}"
        f"{0:<6}"
        f"{0:<6}"
        f"{mode:o}".ljust(8)
        + f"{len(data):<10}"
        + "`\n"
    ).encode("ascii")
    pad = b"\n" if (len(data) % 2 == 1) else b""
    return header + data + pad


def build_deb(entries: list[PackageEntry], dist_dir: Path) -> Path:
    """Build Debian/Ubuntu 64-bit package: Openreview_build/openreview_1.0.0_amd64.deb"""
    output_deb = dist_dir / f"{PKG_NAME}_{PKG_VERSION}_{PKG_ARCH_DEB}.deb"
    if output_deb.exists():
        output_deb.unlink()

    print(f"Building Linux Debian 64-bit Package (.deb): {output_deb}")
    now = int(time.time())

    # Collect directory hierarchy for data.tar.gz
    dirs_seen: set[str] = set()
    data_members: list[tuple[str, bytes, int, int, int]] = []
    md5_lines: list[str] = []
    total_bytes = 0

    for entry in entries:
        rel_path = entry.install_path.lstrip("/")
        parts = rel_path.split("/")
        for i in range(1, len(parts)):
            dpath = "./" + "/".join(parts[:i]) + "/"
            if dpath not in dirs_seen:
                dirs_seen.add(dpath)
                data_members.append((dpath, b"", 0o755, entry.mtime, 1))
        data_members.append(("./" + rel_path, entry.data, entry.mode, entry.mtime, 0))
        md5_hex = hashlib.md5(entry.data).hexdigest()
        md5_lines.append(f"{md5_hex}  {rel_path}\n")
        total_bytes += len(entry.data)

    installed_kb = max(1, (total_bytes + 1023) // 1024)

    control_text = (
        f"Package: {PKG_NAME}\n"
        f"Version: {PKG_VERSION}\n"
        "Section: graphics\n"
        "Priority: optional\n"
        f"Architecture: {PKG_ARCH_DEB}\n"
        "Depends: python3 (>= 3.10), libgl1, libxkbcommon-x11-0, libegl1\n"
        "Recommends: python3-pyside6.qtwidgets, python3-numpy, python3-pil, python3-opencv\n"
        f"Installed-Size: {installed_kb}\n"
        f"Maintainer: {MAINTAINER}\n"
        f"Homepage: {HOMEPAGE}\n"
        f"Description: {SUMMARY}\n"
        f" {DESCRIPTION}\n"
    ).encode("utf-8")

    postinst_sh = (
        "#!/bin/sh\n"
        "set -e\n"
        "chmod 755 /usr/bin/openreview || true\n"
        "if command -v update-desktop-database >/dev/null 2>&1; then\n"
        "    update-desktop-database -q /usr/share/applications || true\n"
        "fi\n"
        "if command -v gtk-update-icon-cache >/dev/null 2>&1; then\n"
        "    gtk-update-icon-cache -q -t -f /usr/share/icons/hicolor || true\n"
        "fi\n"
        "exit 0\n"
    ).encode("utf-8")

    prerm_sh = (
        "#!/bin/sh\n"
        "set -e\n"
        "if [ -d /opt/openreview ]; then\n"
        "    find /opt/openreview -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null || true\n"
        "fi\n"
        "exit 0\n"
    ).encode("utf-8")

    md5sums_bytes = "".join(md5_lines).encode("utf-8")

    control_members = [
        ("./", b"", 0o755, now, 1),
        ("./control", control_text, 0o644, now, 0),
        ("./md5sums", md5sums_bytes, 0o644, now, 0),
        ("./postinst", postinst_sh, 0o755, now, 0),
        ("./prerm", prerm_sh, 0o755, now, 0),
    ]

    control_tar_gz = _make_tar_gz(control_members)
    data_tar_gz = _make_tar_gz(data_members)

    ar_stream = io.BytesIO()
    ar_stream.write(b"!<arch>\n")
    ar_stream.write(_ar_member("debian-binary", b"2.0\n", now))
    ar_stream.write(_ar_member("control.tar.gz", control_tar_gz, now))
    ar_stream.write(_ar_member("data.tar.gz", data_tar_gz, now))

    output_deb.write_bytes(ar_stream.getvalue())
    size_kb = output_deb.stat().st_size / 1024
    print(f"Successfully generated Linux Debian Package: {output_deb} ({size_kb:.1f} KB)")
    return output_deb


# =============================================================================
# 2. RedHat (.rpm) Package Builder (RPM v4 Binary Specification + SVR4 CPIO)
# =============================================================================

RPM_TYPE_BIN = 7
RPM_TYPE_INT16 = 3
RPM_TYPE_INT32 = 4
RPM_TYPE_STRING = 6
RPM_TYPE_STRING_ARRAY = 8
RPM_TYPE_I18NSTRING = 9


def _build_cpio_gz(entries: list[PackageEntry]) -> tuple[bytes, bytes]:
    """Create SVR4 New ASCII ('070701') CPIO archive and return (uncompressed_cpio, gzipped_cpio)."""
    cpio = io.BytesIO()

    def write_cpio_record(
        name: str, data: bytes, ino: int, mode: int, mtime: int, nlink: int = 1
    ) -> None:
        name_bytes = name.encode("utf-8") + b"\x00"
        namesize = len(name_bytes)
        filesize = len(data)
        header = (
            "070701"
            f"{ino:08X}"
            f"{mode:08X}"
            f"{0:08X}"          # uid = 0 (root)
            f"{0:08X}"          # gid = 0 (root)
            f"{nlink:08X}"
            f"{mtime:08X}"
            f"{filesize:08X}"
            f"{0:08X}"          # devmajor
            f"{0:08X}"          # devminor
            f"{0:08X}"          # rdevmajor
            f"{0:08X}"          # rdevminor
            f"{namesize:08X}"
            f"{0:08X}"          # check
        ).encode("ascii")
        cpio.write(header)
        cpio.write(name_bytes)
        # Pad header + filename to 4-byte boundary
        rem = (110 + namesize) % 4
        if rem:
            cpio.write(b"\x00" * (4 - rem))
        if filesize:
            cpio.write(data)
            drem = filesize % 4
            if drem:
                cpio.write(b"\x00" * (4 - drem))

    for idx, entry in enumerate(entries, start=1):
        # CPIO paths inside RPM start with './'
        cpio_name = "." + entry.install_path
        full_mode = 0o100000 | entry.mode  # S_IFREG | mode
        write_cpio_record(cpio_name, entry.data, ino=idx, mode=full_mode, mtime=entry.mtime)

    # Mandatory CPIO trailer
    write_cpio_record("TRAILER!!!", b"", ino=0, mode=0, mtime=0, nlink=1)
    raw_cpio = cpio.getvalue()

    gz_buf = io.BytesIO()
    with gzip.GzipFile(filename="", mode="wb", fileobj=gz_buf, compresslevel=9, mtime=0) as gz:
        gz.write(raw_cpio)
    return raw_cpio, gz_buf.getvalue()


def _pack_rpm_header(tags: list[tuple[int, int, object]], region_tag: int) -> bytes:
    """Serialize an RPM v4 Header structure with an immutable region tag (62 or 63)."""
    tags_sorted = sorted(tags, key=lambda x: x[0])
    store = bytearray()
    index_entries: list[tuple[int, int, int, int]] = []

    for tag, dtype, val in tags_sorted:
        # Align store offset for integer types
        if dtype == RPM_TYPE_INT32 and (len(store) % 4 != 0):
            store.extend(b"\x00" * (4 - (len(store) % 4)))
        elif dtype == RPM_TYPE_INT16 and (len(store) % 2 != 0):
            store.extend(b"\x00")

        offset = len(store)
        if dtype == RPM_TYPE_STRING or dtype == RPM_TYPE_I18NSTRING:
            bval = str(val).encode("utf-8") + b"\x00"
            store.extend(bval)
            count = 1
        elif dtype == RPM_TYPE_STRING_ARRAY:
            arr = list(val)
            for s in arr:
                store.extend(str(s).encode("utf-8") + b"\x00")
            count = len(arr)
        elif dtype == RPM_TYPE_INT32:
            arr = val if isinstance(val, (list, tuple)) else [val]
            for v in arr:
                store.extend(struct.pack("!I", int(v) & 0xFFFFFFFF))
            count = len(arr)
        elif dtype == RPM_TYPE_INT16:
            arr = val if isinstance(val, (list, tuple)) else [val]
            for v in arr:
                store.extend(struct.pack("!H", int(v) & 0xFFFF))
            count = len(arr)
        elif dtype == RPM_TYPE_BIN:
            bval = bytes(val)
            store.extend(bval)
            count = len(bval)
        else:
            raise ValueError(f"Unsupported RPM tag type: {dtype}")

        index_entries.append((tag, dtype, offset, count))

    # Append immutable region trailer (16 bytes) at the end of store
    num_tags_total = len(index_entries) + 1
    region_offset = len(store)
    neg_offset = -16 * num_tags_total
    region_trailer = struct.pack("!IIiI", region_tag, RPM_TYPE_BIN, neg_offset, 16)
    store.extend(region_trailer)

    # Region tag goes first in index array
    all_indices = [(region_tag, RPM_TYPE_BIN, region_offset, 16)] + index_entries

    out = bytearray()
    out.extend(b"\x8e\xad\xe8\x01\x00\x00\x00\x00")
    out.extend(struct.pack("!II", len(all_indices), len(store)))
    for tag, dtype, offset, count in all_indices:
        out.extend(struct.pack("!IIII", tag, dtype, offset, count))
    out.extend(store)
    return bytes(out)


def build_rpm(entries: list[PackageEntry], dist_dir: Path) -> Path:
    """Build RedHat/RHEL/Rocky/Alma/Fedora 64-bit package: Openreview_build/openreview-1.0.0-1.x86_64.rpm"""
    output_rpm = dist_dir / f"{PKG_NAME}-{PKG_VERSION}-{PKG_RELEASE}.{PKG_ARCH_RPM}.rpm"
    if output_rpm.exists():
        output_rpm.unlink()

    print(f"Building Linux RedHat 64-bit Package (.rpm): {output_rpm}")
    now = int(time.time())
    raw_cpio, gz_cpio = _build_cpio_gz(entries)

    # Prepare file tables (DIRNAMES, BASENAMES, DIRINDEXES, FILESIZES, FILEMODES, FILEMD5S, etc.)
    dir_list: list[str] = []
    dir_index_map: dict[str, int] = {}
    dir_indexes: list[int] = []
    basenames: list[str] = []
    file_sizes: list[int] = []
    file_modes: list[int] = []
    file_rdevs: list[int] = []
    file_mtimes: list[int] = []
    file_md5s: list[str] = []
    file_linktos: list[str] = []
    file_flags: list[int] = []
    file_usernames: list[str] = []
    file_groupnames: list[str] = []
    file_devices: list[int] = []
    file_inodes: list[int] = []
    file_langs: list[str] = []

    for idx, entry in enumerate(entries, start=1):
        p_dirname = os.path.dirname(entry.install_path)
        if not p_dirname.endswith("/"):
            p_dirname += "/"
        p_basename = os.path.basename(entry.install_path)

        if p_dirname not in dir_index_map:
            dir_index_map[p_dirname] = len(dir_list)
            dir_list.append(p_dirname)

        dir_indexes.append(dir_index_map[p_dirname])
        basenames.append(p_basename)
        file_sizes.append(len(entry.data))
        file_modes.append(0o100000 | entry.mode)
        file_rdevs.append(0)
        file_mtimes.append(entry.mtime)
        file_md5s.append(hashlib.md5(entry.data).hexdigest())
        file_linktos.append("")
        file_flags.append(0)
        file_usernames.append("root")
        file_groupnames.append("root")
        file_devices.append(1)
        file_inodes.append(idx)
        file_langs.append("")

    postin_script = (
        "chmod 755 /usr/bin/openreview || true\n"
        "if command -v update-desktop-database >/dev/null 2>&1; then\n"
        "    update-desktop-database -q /usr/share/applications || true\n"
        "fi\n"
        "if command -v gtk-update-icon-cache >/dev/null 2>&1; then\n"
        "    gtk-update-icon-cache -q -t -f /usr/share/icons/hicolor || true\n"
        "fi\n"
    )
    preun_script = (
        "if [ \"$1\" = \"0\" ] && [ -d /opt/openreview ]; then\n"
        "    find /opt/openreview -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null || true\n"
        "fi\n"
    )

    header_tags: list[tuple[int, int, object]] = [
        (100, RPM_TYPE_STRING_ARRAY, ["C"]),
        (1000, RPM_TYPE_STRING, PKG_NAME),
        (1001, RPM_TYPE_STRING, PKG_VERSION),
        (1002, RPM_TYPE_STRING, PKG_RELEASE),
        (1004, RPM_TYPE_I18NSTRING, SUMMARY),
        (1005, RPM_TYPE_I18NSTRING, DESCRIPTION.replace("\n ", "\n")),
        (1006, RPM_TYPE_INT32, now),
        (1007, RPM_TYPE_STRING, "workstation.openreview.io"),
        (1009, RPM_TYPE_INT32, sum(file_sizes)),
        (1011, RPM_TYPE_STRING, VENDOR),
        (1014, RPM_TYPE_STRING, "OpenReview-Studio-License"),
        (1015, RPM_TYPE_STRING, MAINTAINER),
        (1016, RPM_TYPE_I18NSTRING, "Applications/Multimedia"),
        (1020, RPM_TYPE_STRING, HOMEPAGE),
        (1021, RPM_TYPE_STRING, "linux"),
        (1022, RPM_TYPE_STRING, PKG_ARCH_RPM),
        (1024, RPM_TYPE_STRING, postin_script),
        (1025, RPM_TYPE_STRING, preun_script),
        (1028, RPM_TYPE_INT32, file_sizes),
        (1030, RPM_TYPE_INT16, file_modes),
        (1033, RPM_TYPE_INT16, file_rdevs),
        (1034, RPM_TYPE_INT32, file_mtimes),
        (1035, RPM_TYPE_STRING_ARRAY, file_md5s),
        (1036, RPM_TYPE_STRING_ARRAY, file_linktos),
        (1037, RPM_TYPE_INT32, file_flags),
        (1039, RPM_TYPE_STRING_ARRAY, file_usernames),
        (1040, RPM_TYPE_STRING_ARRAY, file_groupnames),
        (1047, RPM_TYPE_STRING_ARRAY, [PKG_NAME, f"{PKG_NAME}(x86-64)"]),
        (1048, RPM_TYPE_INT32, [0x0A, (1 << 24) | 0x0A, 0]),
        (1049, RPM_TYPE_STRING_ARRAY, ["/bin/sh", "rpmlib(CompressedFileNames)", "rpmlib(PayloadFilesHavePrefix)"]),
        (1050, RPM_TYPE_STRING_ARRAY, ["", "3.0.4-1", "4.0-1"]),
        (1085, RPM_TYPE_STRING, "/bin/sh"),
        (1086, RPM_TYPE_STRING, "/bin/sh"),
        (1095, RPM_TYPE_INT32, file_devices),
        (1096, RPM_TYPE_INT32, file_inodes),
        (1097, RPM_TYPE_STRING_ARRAY, file_langs),
        (1112, RPM_TYPE_INT32, [8, 8]),
        (1113, RPM_TYPE_STRING_ARRAY, [f"{PKG_VERSION}-{PKG_RELEASE}", f"{PKG_VERSION}-{PKG_RELEASE}"]),
        (1116, RPM_TYPE_INT32, dir_indexes),
        (1117, RPM_TYPE_STRING_ARRAY, basenames),
        (1118, RPM_TYPE_STRING_ARRAY, dir_list),
        (1124, RPM_TYPE_STRING, "cpio"),
        (1125, RPM_TYPE_STRING, "gzip"),
        (1126, RPM_TYPE_STRING, "9"),
        (1132, RPM_TYPE_STRING, "x86_64-redhat-linux-gnu"),
    ]

    header_bytes = _pack_rpm_header(header_tags, region_tag=63)
    header_and_payload = header_bytes + gz_cpio

    # Signature Header (SHA1 of header, MD5 of header+payload, sizes)
    sha1_hex = hashlib.sha1(header_bytes).hexdigest()
    md5_digest = hashlib.md5(header_and_payload).digest()
    sig_tags: list[tuple[int, int, object]] = [
        (269, RPM_TYPE_STRING, sha1_hex),
        (1000, RPM_TYPE_INT32, len(header_and_payload)),
        (1004, RPM_TYPE_BIN, md5_digest),
        (1007, RPM_TYPE_INT32, len(raw_cpio)),
    ]
    sig_bytes = _pack_rpm_header(sig_tags, region_tag=62)
    if len(sig_bytes) % 8 != 0:
        sig_bytes += b"\x00" * (8 - (len(sig_bytes) % 8))

    # 96-byte RPM Lead
    lead_name = f"{PKG_NAME}-{PKG_VERSION}-{PKG_RELEASE}.{PKG_ARCH_RPM}".encode("ascii")[:65]
    lead_name_padded = lead_name + b"\x00" * (66 - len(lead_name))
    lead = struct.pack(
        "!4sBBhh66shh16s",
        b"\xed\xab\xee\xdb",  # RPM magic
        3, 0,                 # Version 3.0
        0,                    # Binary package (0)
        1,                    # Arch x86_64/i386 (1)
        lead_name_padded,
        1,                    # OS Linux (1)
        5,                    # Signature type RPMSIGTYPE_HEADERSIG (5)
        b"\x00" * 16,
    )

    output_rpm.write_bytes(lead + sig_bytes + header_and_payload)
    size_kb = output_rpm.stat().st_size / 1024
    print(f"Successfully generated Linux RedHat Package: {output_rpm} ({size_kb:.1f} KB)")
    return output_rpm


def main() -> None:
    project_root = Path(__file__).resolve().parents[1]
    dist_dir = project_root / "Openreview_build"
    dist_dir.mkdir(parents=True, exist_ok=True)

    entries = collect_linux_payload(project_root)
    build_deb(entries, dist_dir)
    build_rpm(entries, dist_dir)


if __name__ == "__main__":
    main()
