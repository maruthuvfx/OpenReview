"""
OpenReview Microsoft 64-bit Windows Installer (.msi) & Portable Archive Builder.
Builds:
1. Openreview_build/OpenReview-1.0.0-win64.msi (Full interactive 64-bit Windows Installer)
2. Openreview_build/OpenReview-1.0.0-win64-portable.zip (Portable 64-bit ZIP archive)
"""
from __future__ import annotations

import os
import sys
import uuid
import zipfile
import tempfile
import msilib
import msilib.schema
import msilib.sequence
import msilib.text
from pathlib import Path
from PIL import Image, ImageDraw


PRODUCT_NAME = "OpenReview"
PRODUCT_VERSION = "1.3.0"
MANUFACTURER = "OpenReview Technologies"
PRODUCT_CODE = "{199DB410-94A8-4CF6-B661-DA9722F38C9E}"
UPGRADE_CODE = "{8E971B39-5B26-4B07-B7D8-87D98935FE21}"

LICENSE_RTF = (
    r"{\rtf1\ansi\deff0{\fonttbl{\f0\fnil\fcharset0 Segoe UI;}}"
    r"\viewkind4\uc1\pard\lang1033\b\fs20 OpenReview Studio Workstation - End-User License Agreement\b0\fs18\par"
    r"\par"
    r"Copyright (c) 2026 Maruthu Chakravarthi / OpenReview Technologies. All rights reserved.\par"
    r"\par"
    r"1. GRANT OF LICENSE\par"
    r"Subject to the terms of this Agreement, OpenReview Technologies grants you a non-exclusive, "
    r"worldwide license to install and use OpenReview (64-bit Windows Edition) for visual effects (VFX) "
    r"sequence playback, HDR color inspection, compositing comparison, and collaborative dailies review.\par"
    r"\par"
    r"2. CUSTOM INSTALLATION & PORTABLE USE\par"
    r"You may install this software to any local or network workstation path of your choice, or run the "
    r"portable distribution directly without system modification.\par"
    r"\par"
    r"3. THIRD-PARTY COMPONENTS\par"
    r"OpenReview includes open-source libraries including Qt6 / PySide6, OpenColorIO v2, OpenEXR, NumPy, "
    r"Pillow, and OpenCV, which remain covered by their respective open-source licenses.\par"
    r"\par"
    r"4. DISCLAIMER OF WARRANTY\par"
    r"THE SOFTWARE IS PROVIDED ""AS IS"", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT "
    r"NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT.\par"
    r"}"
)


def generate_short_id(prefix: str, path_str: str) -> str:
    """Generate a valid unique MSI identifier <= 72 characters."""
    clean = "".join(c if c.isalnum() else "_" for c in path_str)
    if len(clean) > 36:
        h = str(uuid.uuid5(uuid.NAMESPACE_DNS, path_str)).replace("-", "")[:10]
        clean = clean[:26] + "_" + h
    res = f"{prefix}_{clean}"
    return res[:70]


def create_installer_bitmaps(temp_dir: Path, icon_png: Path) -> tuple[Path, Path]:
    """Create 24-bit BMP images for the MSI wizard header banner and full welcome/exit backdrop."""
    banner_path = temp_dir / "banner.bmp"
    dialog_path = temp_dir / "dialog.bmp"

    # 1. Top Banner (500 x 60) - Light clean header with dark accent stripe & app icon
    banner = Image.new("RGB", (500, 60), (245, 247, 250))
    b_draw = ImageDraw.Draw(banner)
    b_draw.rectangle([0, 57, 500, 60], fill=(37, 99, 235))
    if icon_png.is_file():
        try:
            logo = Image.open(icon_png).convert("RGBA").resize((40, 40), Image.Resampling.LANCZOS)
            banner.paste(logo, (445, 9), logo)
        except Exception:
            pass
    banner.save(banner_path, format="BMP")

    # 2. Full Welcome / Complete Dialog Bitmap (500 x 314) - Left studio sidebar (164px) + white content pane
    dialog = Image.new("RGB", (500, 314), (255, 255, 255))
    d_draw = ImageDraw.Draw(dialog)
    for y in range(314):
        r = int(15 + (y / 314) * 12)
        g = int(23 + (y / 314) * 20)
        b = int(42 + (y / 314) * 45)
        d_draw.line([(0, y), (164, y)], fill=(r, g, b))
    d_draw.rectangle([161, 0, 164, 314], fill=(37, 99, 235))
    if icon_png.is_file():
        try:
            logo_lg = Image.open(icon_png).convert("RGBA").resize((84, 84), Image.Resampling.LANCZOS)
            dialog.paste(logo_lg, (40, 42), logo_lg)
        except Exception:
            pass
    dialog.save(dialog_path, format="BMP")

    return banner_path, dialog_path


def add_standard_header(dlg: msilib.Dialog, title: str, subtitle: str) -> None:
    """Add standard top banner bitmap, title, subtitle, and divider line to an interior wizard dialog."""
    dlg.bitmap("BannerBitmap", 0, 0, 374, 44, "BannerBmp")
    dlg.text("BannerTitle", 15, 6, 290, 16, 0x30003, f"{{\\DlgFontBold8}}{title}")
    dlg.text("BannerSubTitle", 25, 20, 290, 20, 0x30003, f"{{\\DlgFont8}}{subtitle}")
    dlg.line("BannerLine", 0, 44, 374, 0)
    dlg.line("BottomLine", 0, 234, 374, 0)


def configure_ui_wizard(db, banner_bmp: Path, dialog_bmp: Path) -> None:
    """Populate full Windows Installer UI tables so the MSI behaves like a standard desktop installer."""
    # Binary streams for bitmaps
    msilib.add_data(db, "Binary", [
        ("BannerBmp", msilib.Binary(str(banner_bmp))),
        ("DialogBmp", msilib.Binary(str(dialog_bmp))),
    ])

    # Text styles
    msilib.add_data(db, "TextStyle", [
        ("DlgFont8", "Segoe UI", 9, None, 0),
        ("DlgFontBold8", "Segoe UI", 9, None, 1),
        ("VerdanaBold13", "Segoe UI", 12, None, 1),
        ("TitleFont", "Segoe UI", 11, 0xFFFFFF, 1),
    ])

    # Standard ActionText and UIText tables for progress bar & folder browser
    msilib.add_tables(db, msilib.text)

    # Standard MSI Error table entries for UI prompts
    msilib.add_data(db, "Error", [
        (0, "{{Fatal error: }}"),
        (1, "{{Error [1]. }}"),
        (2, "Warning [1]. "),
        (4, "Info [1]. "),
        (5, "The installer has encountered an unexpected error installing this package. This may indicate a problem with this package. The error code is [1]. {{The arguments are: [2], [3], [4]}}"),
        (7, "{{Disk full: }}"),
        (8, "Action [Time]: [1]. [2]"),
        (9, "[ProductName]"),
        (10, "{[2]}{, [3]}{, [4]}"),
        (11, "Message type: [1], Argument: [2]"),
        (12, "=== Logging started: [Date]  [Time] ==="),
        (13, "=== Logging stopped: [Date]  [Time] ==="),
        (14, "Action start [Time]: [1]."),
        (15, "Action ended [Time]: [1]. Return value [2]."),
        (16, "Time remaining: {[1] minutes }{[2] seconds}"),
        (17, "Out of memory. Shut down other applications before retrying."),
        (18, "Installer is no longer responding."),
        (19, "Installer stopped prematurely."),
        (20, "Please wait while Windows configures [ProductName]"),
        (21, "Gathering required information..."),
        (22, "Removing older versions of this application..."),
        (23, "Preparing to remove older versions of this application..."),
        (32, "{[ProductName] }Setup completed successfully."),
        (33, "{[ProductName] }Setup failed."),
        (1301, "Cannot create the file '[2]'. A directory with this name already exists."),
        (1302, "Please insert the disk: [2]"),
        (1303, "The installer has insufficient privileges to access this directory: [2]. The installation cannot continue. Log on as administrator or contact your system administrator."),
        (1304, "Error writing to file: [2]. Verify that you have access to that directory."),
        (1306, "Another application has exclusive access to the file '[2]'. Please shut down all other applications, then click Retry."),
        (1307, "There is not enough disk space to install this file: [2]. Free some disk space and click Retry, or click Cancel to exit."),
        (1311, "Source file not found{{(cabinet)}}: [2]. Verify that the file exists and that you can access it."),
        (1602, "Are you sure you want to cancel?"),
        (1707, "Installation completed successfully."),
        (1708, "Installation failed."),
    ])

    # CheckBox table definitions
    msilib.add_data(db, "CheckBox", [
        ("DESKTOP_SHORTCUT", "1"),
        ("LAUNCHAPPONEXIT", "1"),
    ])

    x, y, w, h = 50, 50, 374, 270
    modal_attr = 3       # Visible (1) | Modal (2)
    modeless_attr = 1    # Visible (1)
    title_tpl = "[ProductName] [ProductVersion] Setup"

    # =========================================================================
    # 1. WelcomeDlg (First dialog for fresh installation)
    # =========================================================================
    welcome = msilib.Dialog(db, "WelcomeDlg", x, y, w, h, modal_attr, title_tpl, "Next", "Next", "Cancel")
    welcome.bitmap("BackBitmap", 0, 0, 374, 234, "DialogBmp")
    welcome.line("BottomLine", 0, 234, 374, 0)
    welcome.text(
        "Title", 135, 20, 225, 45, 0x30003,
        "{\\VerdanaBold13}Welcome to the [ProductName] [ProductVersion] Setup Wizard"
    )
    welcome.text(
        "Description", 135, 75, 225, 130, 0x30003,
        "{\\DlgFont8}The Setup Wizard will install [ProductName] (64-bit) on your computer.\r\n\r\n"
        "OpenReview is a studio-grade VFX sequence playback, HDR color inspection, "
        "and collaborative dailies review workstation.\r\n\r\n"
        "Click Next to continue, or Cancel to exit the Setup Wizard."
    )
    back_btn = welcome.pushbutton("Back", 180, 243, 56, 17, 1, "{\\DlgFont8}< &Back", "Next")
    next_btn = welcome.pushbutton("Next", 236, 243, 56, 17, 3, "{\\DlgFont8}&Next >", "Cancel")
    cancel_btn = welcome.pushbutton("Cancel", 304, 243, 56, 17, 3, "{\\DlgFont8}Cancel", "Back")
    next_btn.event("NewDialog", "LicenseAgreementDlg")
    cancel_btn.event("SpawnDialog", "CancelDlg")

    # =========================================================================
    # 2. LicenseAgreementDlg (EULA Acceptance)
    # =========================================================================
    license_dlg = msilib.Dialog(
        db, "LicenseAgreementDlg", x, y, w, h, modal_attr, title_tpl, "AgreeRadio", "Next", "Cancel"
    )
    add_standard_header(
        license_dlg,
        "End-User License Agreement",
        "Please read the following license agreement carefully."
    )
    license_dlg.control("LicenseText", "ScrollableText", 20, 55, 334, 125, 7, None, LICENSE_RTF, "AgreeRadio", None)
    rg = license_dlg.radiogroup("AgreeRadio", 20, 187, 330, 40, 3, "IAgree", None, "Back")
    rg.add("Yes", 0, 0, 320, 17, "{\\DlgFont8}I &accept the terms in the License Agreement", "Yes")
    rg.add("No", 0, 19, 320, 17, "{\\DlgFont8}I &do not accept the terms in the License Agreement", "No")

    l_back = license_dlg.pushbutton("Back", 180, 243, 56, 17, 3, "{\\DlgFont8}< &Back", "Next")
    l_next = license_dlg.pushbutton("Next", 236, 243, 56, 17, 3, "{\\DlgFont8}&Next >", "Cancel")
    l_cancel = license_dlg.pushbutton("Cancel", 304, 243, 56, 17, 3, "{\\DlgFont8}Cancel", "LicenseText")

    l_back.event("NewDialog", "WelcomeDlg")
    l_next.event("NewDialog", "InstallDirDlg", 'IAgree = "Yes"')
    l_next.condition("Disable", 'IAgree <> "Yes"')
    l_next.condition("Enable", 'IAgree = "Yes"')
    l_cancel.event("SpawnDialog", "CancelDlg")

    # =========================================================================
    # 3. InstallDirDlg (Custom Installation Path & Shortcut Options)
    # =========================================================================
    install_dir = msilib.Dialog(
        db, "InstallDirDlg", x, y, w, h, modal_attr, title_tpl, "Next", "Next", "Cancel"
    )
    add_standard_header(
        install_dir,
        "Destination Folder & Shortcuts",
        "Choose a custom installation folder and shortcut options."
    )
    install_dir.text(
        "FolderLabel", 20, 58, 334, 30, 0x30003,
        "{\\DlgFont8}Setup will install [ProductName] in the following folder. "
        "To install in a different folder, type a custom path below or click Browse."
    )
    install_dir.control("FolderEdit", "PathEdit", 20, 92, 268, 18, 3, "INSTALLDIR", None, "Browse", None)
    browse_btn = install_dir.pushbutton("Browse", 296, 91, 58, 19, 3, "{\\DlgFont8}B&rowse...", "DesktopCheck")
    install_dir.checkbox(
        "DesktopCheck", 20, 132, 300, 18, 3, "DESKTOP_SHORTCUT",
        "{\\DlgFont8}Create a &Desktop shortcut for OpenReview", "Back"
    )
    install_dir.text(
        "SpaceHint", 20, 165, 334, 32, 0x30003,
        "{\\DlgFont8} target architecture: 64-bit Windows (x64)\r\n"
        "A Start Menu shortcut will also be created automatically."
    )

    id_back = install_dir.pushbutton("Back", 180, 243, 56, 17, 3, "{\\DlgFont8}< &Back", "Next")
    id_next = install_dir.pushbutton("Next", 236, 243, 56, 17, 3, "{\\DlgFont8}&Next >", "Cancel")
    id_cancel = install_dir.pushbutton("Cancel", 304, 243, 56, 17, 3, "{\\DlgFont8}Cancel", "FolderEdit")

    browse_btn.event("[_BrowseProperty]", "INSTALLDIR", "1", 1)
    browse_btn.event("SpawnDialog", "BrowseDlg", "1", 2)
    id_back.event("NewDialog", "LicenseAgreementDlg")
    id_next.event("SetTargetPath", "INSTALLDIR", "1", 1)
    id_next.event("NewDialog", "VerifyReadyDlg", "1", 2)
    id_cancel.event("SpawnDialog", "CancelDlg")

    # =========================================================================
    # 4. BrowseDlg (Interactive Directory Tree Modal for Custom Path)
    # =========================================================================
    browse = msilib.Dialog(db, "BrowseDlg", x, y, w, h, modal_attr, title_tpl, "PathEdit", "OK", "Cancel")
    add_standard_header(
        browse,
        "Change Destination Folder",
        "Browse to the custom folder where [ProductName] should be installed."
    )
    browse.text("ComboLabel", 20, 56, 48, 14, 0x30003, "{\\DlgFont8}&Look in:")
    browse.control(
        "DirectoryCombo", "DirectoryCombo", 70, 54, 220, 80, 458763, "_BrowseProperty", None, "Up", None
    )
    up_btn = browse.pushbutton("Up", 296, 53, 28, 18, 3, "{\\DlgFont8}Up", "NewFolder")
    new_btn = browse.pushbutton("NewFolder", 328, 53, 28, 18, 3, "{\\DlgFont8}New", "DirectoryList")
    browse.control(
        "DirectoryList", "DirectoryList", 20, 77, 336, 118, 11, "_BrowseProperty", None, "PathEdit", None
    )
    browse.text("PathLabel", 20, 205, 65, 16, 0x30003, "{\\DlgFont8}&Folder name:")
    browse.control("PathEdit", "PathEdit", 88, 202, 268, 18, 11, "_BrowseProperty", None, "OK", None)

    b_ok = browse.pushbutton("OK", 236, 243, 56, 17, 3, "{\\DlgFont8}OK", "Cancel")
    b_cancel = browse.pushbutton("Cancel", 304, 243, 56, 17, 3, "{\\DlgFont8}Cancel", "DirectoryCombo")

    up_btn.event("DirectoryListUp", "0")
    new_btn.event("DirectoryListNew", "0")
    b_ok.event("SetTargetPath", "[_BrowseProperty]", "1", 1)
    b_ok.event("EndDialog", "Return", "1", 2)
    b_cancel.event("Reset", "0", "1", 1)
    b_cancel.event("EndDialog", "Return", "1", 2)

    # =========================================================================
    # 5. VerifyReadyDlg (Ready to Install Confirmation)
    # =========================================================================
    verify = msilib.Dialog(db, "VerifyReadyDlg", x, y, w, h, modal_attr, title_tpl, "Install", "Install", "Cancel")
    add_standard_header(
        verify,
        "Ready to Install [ProductName]",
        "The Setup Wizard is ready to begin installation."
    )
    verify.text(
        "VerifyText", 20, 60, 334, 65, 0x30003,
        "{\\DlgFont8}Click Install to begin the installation. "
        "If you want to review or change any of your installation settings, click Back. "
        "Click Cancel to exit the wizard."
    )
    verify.text("DestTitle", 20, 125, 334, 15, 0x30003, "{\\DlgFontBold8}Selected Destination Folder:")
    verify.text("DestValue", 20, 143, 334, 32, 0x30003, "{\\DlgFont8}[INSTALLDIR]")

    v_back = verify.pushbutton("Back", 180, 243, 56, 17, 3, "{\\DlgFont8}< &Back", "Install")
    # Attributes: 3 (Visible|Enabled) | 0x200000 (ElevationShield on UAC systems) -> use 3 for universal compatibility
    v_install = verify.pushbutton("Install", 236, 243, 56, 17, 3, "{\\DlgFont8}&Install", "Cancel")
    v_cancel = verify.pushbutton("Cancel", 304, 243, 56, 17, 3, "{\\DlgFont8}Cancel", "Back")

    v_back.event("NewDialog", "InstallDirDlg", "NOT Installed")
    v_back.event("NewDialog", "MaintenanceTypeDlg", "Installed")
    v_install.event("EndDialog", "Return")
    v_cancel.event("SpawnDialog", "CancelDlg")

    # =========================================================================
    # 6. ProgressDlg (Modeless Dialog showing live Installation Progress Bar)
    # =========================================================================
    progress = msilib.Dialog(db, "ProgressDlg", x, y, w, h, modeless_attr, title_tpl, "Cancel", "Cancel", "Cancel")
    add_standard_header(
        progress,
        "Installing [ProductName]",
        "Please wait while the Setup Wizard installs [ProductName]."
    )
    progress.text(
        "ProgressIntro", 20, 60, 334, 28, 0x30003,
        "{\\DlgFont8}This may take a few moments while application files and shortcuts are configured."
    )
    progress.text("StatusPrefix", 20, 100, 48, 15, 0x30003, "{\\DlgFontBold8}Status:")
    act_text = progress.text("ActionText", 70, 100, 284, 15, 0x30003, "{\\DlgFont8}Preparing installation...")
    act_text.mapping("ActionText", "Text")

    # ProgressBar: Attributes 65537 = Visible (1) | Progress95 (65536)
    pbar = progress.control("ProgressBar", "ProgressBar", 20, 122, 334, 18, 65537, None, None, None, None)
    pbar.mapping("SetProgress", "Progress")

    act_data = progress.text("ActionData", 20, 148, 334, 35, 0x30003, None)
    act_data.mapping("ActionData", "Text")

    p_back = progress.pushbutton("Back", 180, 243, 56, 17, 1, "{\\DlgFont8}< &Back", "Next")
    p_next = progress.pushbutton("Next", 236, 243, 56, 17, 1, "{\\DlgFont8}&Next >", "Cancel")
    p_cancel = progress.pushbutton("Cancel", 304, 243, 56, 17, 3, "{\\DlgFont8}Cancel", "Back")
    p_cancel.event("SpawnDialog", "CancelDlg")

    # =========================================================================
    # 7. ExitDialog (Installation Complete Dialog)
    # =========================================================================
    exit_dlg = msilib.Dialog(db, "ExitDialog", x, y, w, h, modal_attr, title_tpl, "Finish", "Finish", "Finish")
    exit_dlg.bitmap("BackBitmap", 0, 0, 374, 234, "DialogBmp")
    exit_dlg.line("BottomLine", 0, 234, 374, 0)
    exit_dlg.text(
        "Title", 135, 20, 225, 45, 0x30003,
        "{\\VerdanaBold13}Completed the [ProductName] Setup Wizard"
    )
    exit_dlg.text(
        "Description", 135, 75, 225, 70, 0x30003,
        "{\\DlgFont8}[ProductName] [ProductVersion] has been successfully installed.\r\n\r\n"
        "Click the Finish button to exit the Setup Wizard."
    )
    launch_cb = exit_dlg.checkbox(
        "LaunchCheck", 135, 160, 215, 18, 3, "LAUNCHAPPONEXIT",
        "{\\DlgFont8}&Launch OpenReview now", "Back"
    )
    launch_cb.condition("Hide", 'REMOVE = "ALL"')

    e_back = exit_dlg.pushbutton("Back", 180, 243, 56, 17, 1, "{\\DlgFont8}< &Back", "Cancel")
    e_finish = exit_dlg.pushbutton("Finish", 236, 243, 56, 17, 3, "{\\DlgFont8}&Finish", "LaunchCheck")
    e_cancel = exit_dlg.pushbutton("Cancel", 304, 243, 56, 17, 1, "{\\DlgFont8}Cancel", "Finish")

    e_finish.event("DoAction", "LaunchApplication", 'LAUNCHAPPONEXIT = "1" AND NOT (REMOVE = "ALL")', 1)
    e_finish.event("EndDialog", "Return", "1", 2)

    # =========================================================================
    # 8. MaintenanceTypeDlg (Repair or Remove when already installed)
    # =========================================================================
    maint = msilib.Dialog(
        db, "MaintenanceTypeDlg", x, y, w, h, modal_attr, title_tpl, "RepairBtn", "RepairBtn", "Cancel"
    )
    add_standard_header(
        maint,
        "Modify, Repair, or Remove Installation",
        "Select the operation you wish to perform on [ProductName]."
    )
    repair_btn = maint.pushbutton("RepairBtn", 30, 70, 80, 22, 3, "{\\DlgFontBold8}&Repair", "RemoveBtn")
    maint.text(
        "RepairDesc", 120, 70, 230, 35, 0x30003,
        "{\\DlgFont8}Repairs errors in the most recent installation by fixing missing or corrupt files and shortcuts."
    )
    remove_btn = maint.pushbutton("RemoveBtn", 30, 125, 80, 22, 3, "{\\DlgFontBold8}Re&move", "Back")
    maint.text(
        "RemoveDesc", 120, 125, 230, 35, 0x30003,
        "{\\DlgFont8}Removes [ProductName] from your computer."
    )
    m_back = maint.pushbutton("Back", 180, 243, 56, 17, 1, "{\\DlgFont8}< &Back", "Next")
    m_next = maint.pushbutton("Next", 236, 243, 56, 17, 1, "{\\DlgFont8}&Next >", "Cancel")
    m_cancel = maint.pushbutton("Cancel", 304, 243, 56, 17, 3, "{\\DlgFont8}Cancel", "RepairBtn")

    repair_btn.event("ReinstallMode", "ecmus", "1", 1)
    repair_btn.event("Reinstall", "ALL", "1", 2)
    repair_btn.event("EndDialog", "Return", "1", 3)

    remove_btn.event("Remove", "ALL", "1", 1)
    remove_btn.event("EndDialog", "Return", "1", 2)
    m_cancel.event("SpawnDialog", "CancelDlg")

    # =========================================================================
    # 9. CancelDlg, UserExit, FatalError, ErrorDlg
    # =========================================================================
    cancel_dlg = msilib.Dialog(db, "CancelDlg", 100, 100, 260, 85, modal_attr, title_tpl, "No", "No", "No")
    cancel_dlg.text(
        "Text", 20, 15, 220, 30, 0x30003,
        "{\\DlgFont8}Are you sure you want to cancel [ProductName] installation?"
    )
    c_yes = cancel_dlg.pushbutton("Yes", 72, 57, 56, 17, 3, "{\\DlgFont8}&Yes", "No")
    c_no = cancel_dlg.pushbutton("No", 132, 57, 56, 17, 3, "{\\DlgFont8}&No", "Yes")
    c_yes.event("EndDialog", "Exit")
    c_no.event("EndDialog", "Return")

    user_exit = msilib.Dialog(db, "UserExit", x, y, w, h, modal_attr, title_tpl, "Finish", "Finish", "Finish")
    user_exit.bitmap("BackBitmap", 0, 0, 374, 234, "DialogBmp")
    user_exit.line("BottomLine", 0, 234, 374, 0)
    user_exit.text(
        "Title", 135, 20, 225, 45, 0x30003,
        "{\\VerdanaBold13}[ProductName] Setup Wizard Was Interrupted"
    )
    user_exit.text(
        "Description", 135, 75, 225, 80, 0x30003,
        "{\\DlgFont8}[ProductName] setup was interrupted. Your system has not been modified. "
        "To install this program at a later time, please run the installation again.\r\n\r\n"
        "Click the Finish button to exit the Setup Wizard."
    )
    user_exit.pushbutton("Back", 180, 243, 56, 17, 1, "{\\DlgFont8}< &Back", "Finish")
    ue_finish = user_exit.pushbutton("Finish", 236, 243, 56, 17, 3, "{\\DlgFont8}&Finish", "Cancel")
    user_exit.pushbutton("Cancel", 304, 243, 56, 17, 1, "{\\DlgFont8}Cancel", "Back")
    ue_finish.event("EndDialog", "Exit")

    fatal_err = msilib.Dialog(db, "FatalError", x, y, w, h, modal_attr, title_tpl, "Finish", "Finish", "Finish")
    fatal_err.bitmap("BackBitmap", 0, 0, 374, 234, "DialogBmp")
    fatal_err.line("BottomLine", 0, 234, 374, 0)
    fatal_err.text(
        "Title", 135, 20, 225, 45, 0x30003,
        "{\\VerdanaBold13}[ProductName] Setup Wizard Ended Prematurely"
    )
    fatal_err.text(
        "Description", 135, 75, 225, 80, 0x30003,
        "{\\DlgFont8}[ProductName] setup ended prematurely because of an error. "
        "Your system has not been modified.\r\n\r\n"
        "Click the Finish button to exit the Setup Wizard."
    )
    fatal_err.pushbutton("Back", 180, 243, 56, 17, 1, "{\\DlgFont8}< &Back", "Finish")
    fe_finish = fatal_err.pushbutton("Finish", 236, 243, 56, 17, 3, "{\\DlgFont8}&Finish", "Cancel")
    fatal_err.pushbutton("Cancel", 304, 243, 56, 17, 1, "{\\DlgFont8}Cancel", "Back")
    fe_finish.event("EndDialog", "Exit")

    # Standard ErrorDlg (Attributes: 65539 = Visible | Modal | Error)
    err_dlg = msilib.Dialog(db, "ErrorDlg", 100, 100, 270, 105, 65539, title_tpl, "O", None, None)
    err_dlg.text("ErrorText", 20, 15, 230, 50, 0x30003, "Information text")
    err_dlg.pushbutton("O", 110, 75, 50, 17, 3, "{\\DlgFont8}&OK", "C").event("EndDialog", "ErrorOk")
    err_dlg.pushbutton("C", 130, 75, 50, 17, 3, "{\\DlgFont8}&Cancel", "A").event("EndDialog", "ErrorCancel")
    err_dlg.pushbutton("A", 20, 75, 50, 17, 3, "{\\DlgFont8}&Abort", "I").event("EndDialog", "ErrorAbort")
    err_dlg.pushbutton("I", 185, 75, 50, 17, 3, "{\\DlgFont8}&Ignore", "N").event("EndDialog", "ErrorIgnore")
    err_dlg.pushbutton("N", 20, 75, 50, 17, 3, "{\\DlgFont8}&No", "R").event("EndDialog", "ErrorNo")
    err_dlg.pushbutton("R", 75, 75, 50, 17, 3, "{\\DlgFont8}&Retry", "Y").event("EndDialog", "ErrorRetry")
    err_dlg.pushbutton("Y", 75, 75, 50, 17, 3, "{\\DlgFont8}&Yes", "O").event("EndDialog", "ErrorYes")

    # Schedule Wizard Dialogs in InstallUISequence
    msilib.add_data(db, "InstallUISequence", [
        ("WelcomeDlg", "NOT Installed", 1220),
        ("MaintenanceTypeDlg", "Installed AND NOT RESUME AND NOT Preselected", 1240),
        ("ProgressDlg", None, 1280),
    ])


def validate_msi_dialogs(db) -> None:
    """Strictly validate ICE23 tab-order cycle rules on all dialogs to prevent MSI Error 2809/2810."""
    def query(sql: str) -> list[list[str]]:
        v = db.OpenView(sql)
        v.Execute(None)
        rows = []
        while True:
            r = v.Fetch()
            if not r:
                break
            rows.append([r.GetString(i) for i in range(1, r.GetFieldCount() + 1)])
        v.Close()
        return rows

    dialogs = {r[0]: r[1] for r in query("SELECT Dialog, Control_First FROM Dialog")}
    controls: dict[str, dict[str, str]] = {}
    for d, c, nxt in query("SELECT Dialog_, Control, Control_Next FROM Control"):
        controls.setdefault(d, {})[c] = nxt

    for d, first in dialogs.items():
        dlg_ctrls = controls.get(d, {})
        if first not in dlg_ctrls:
            raise ValueError(f"ICE23 Error: Dialog {d} Control_First={first} does not exist")

        visited: list[str] = []
        curr = first
        while curr and curr not in visited:
            visited.append(curr)
            curr = dlg_ctrls.get(curr, "")

        if curr != first:
            raise ValueError(
                f"ICE23 / Error 2810 on {d}: Control_Next chain from {first} ended at {curr!r} instead of looping to {first} (visited={visited})"
            )

        for c, nxt in dlg_ctrls.items():
            if nxt and c not in visited:
                raise ValueError(
                    f"ICE23 / Error 2810 on {d}: Control {c} points to {nxt} but is outside the main cycle {visited}"
                )



def build_msi(dist_dir: Path, project_root: Path) -> Path:
    """Build the 64-bit Windows Installer (.msi) inside Openreview_build."""
    output_msi = dist_dir / f"{PRODUCT_NAME}-{PRODUCT_VERSION}-win64.msi"

    main_exe = dist_dir / "OpenReview.exe"
    if not main_exe.exists():
        raise FileNotFoundError(f"OpenReview.exe not found in {dist_dir}")

    if output_msi.exists():
        output_msi.unlink()

    print(f"Building Microsoft 64-bit Windows Installer: {output_msi}")

    db = msilib.init_database(
        str(output_msi),
        msilib.schema,
        PRODUCT_NAME,
        PRODUCT_CODE,
        PRODUCT_VERSION,
        MANUFACTURER,
    )

    msilib.add_tables(db, msilib.sequence)

    # Summary Information Stream
    summary = db.GetSummaryInformation(20)
    summary.SetProperty(1, 1252)  # Codepage ANSI
    summary.SetProperty(2, f"{PRODUCT_NAME} 64-bit Windows Installer")  # Title
    summary.SetProperty(3, f"Installation Database for {PRODUCT_NAME}")  # Subject
    summary.SetProperty(4, MANUFACTURER)  # Author
    summary.SetProperty(7, "x64;1033")  # Platform 64-bit x64, Language English
    summary.SetProperty(9, "{" + str(uuid.uuid4()).upper() + "}")  # Package Code
    summary.SetProperty(14, 200)  # Min Windows Installer version (2.0)
    summary.SetProperty(15, 2)  # Word Count: 2 = compressed in cabinet files
    summary.Persist()

    # Stream application icon into Icon table for Add/Remove Programs & Shortcuts
    icon_file = project_root / "src" / "openreview" / "resources" / "icons" / "openreview.ico"
    icon_png = project_root / "src" / "openreview" / "resources" / "icons" / "openreview.png"
    if icon_file.is_file():
        msilib.add_data(db, "Icon", [
            ("openreview.ico", msilib.Binary(str(icon_file)))
        ])

    # Properties
    msilib.add_data(db, "Property", [
        ("UpgradeCode", UPGRADE_CODE),
        ("ALLUSERS", "1"),
        ("SecureCustomProperties", "INSTALLDIR;DESKTOP_SHORTCUT;LAUNCHAPPONEXIT;OLDPRODUCTS"),
        ("ARPPRODUCTICON", "openreview.ico"),
        ("ARPHELPLINK", "https://openreview.local/docs"),
        ("DefaultUIFont", "DlgFont8"),
        ("ErrorDialog", "ErrorDlg"),
        ("_BrowseProperty", "INSTALLDIR"),
        ("IAgree", "No"),
        ("DESKTOP_SHORTCUT", "1"),
        ("LAUNCHAPPONEXIT", "1"),
    ])

    # Directory Hierarchy (64-bit Program Files default, customizable via InstallDirDlg)
    directories = [
        ("TARGETDIR", None, "SourceDir"),
        ("ProgramFiles64Folder", "TARGETDIR", "."),
        ("INSTALLDIR", "ProgramFiles64Folder", "OPENRE~1|OpenReview"),
        ("ProgramMenuFolder", "TARGETDIR", "."),
        ("DesktopFolder", "TARGETDIR", "."),
    ]

    dir_map = {"": "INSTALLDIR"}
    all_files = []
    dir_short_names: dict[str, set[str]] = {}

    def get_short_name(name: str, used_set: set[str]) -> str:
        orig = name
        clean = name.replace("+", "_")
        clean = "".join(c for c in clean if c not in r' "/\[]:;=,')
        parts = clean.split(".")
        if len(parts) > 1:
            prefix = "".join(parts[:-1]).upper()
            suffix = parts[-1].upper()
            if not prefix:
                prefix = suffix
                suffix = ""
        else:
            prefix = clean.upper()
            suffix = ""

        if len(parts) < 3 and len(prefix) <= 8 and clean == orig and (not suffix or len(suffix) <= 3):
            cand = f"{prefix}.{suffix}" if suffix else prefix
        else:
            cand = None

        if not cand or cand in used_set:
            pref = prefix[:6]
            suf = suffix[:3] if suffix else ""
            pos = 1
            while True:
                cand = f"{pref}~{pos}.{suf}" if suf else f"{pref}~{pos}"
                if cand not in used_set:
                    break
                pos += 1
                if pos in (10, 100, 1000):
                    pref = pref[:-1]
        used_set.add(cand)
        return cand

    # Map subdirectories and collect all portable files
    for root, _dirs, files in os.walk(dist_dir):
        root_path = Path(root)
        rel_root = root_path.relative_to(dist_dir)
        if rel_root.parts:
            parent_rel = rel_root.parent
            parent_key = str(parent_rel).replace("\\", "/") if str(parent_rel) != "." else ""
            parent_dir_id = dir_map[parent_key]

            if parent_key not in dir_short_names:
                dir_short_names[parent_key] = set()

            dir_id = generate_short_id("DIR", str(rel_root))
            dir_map[str(rel_root).replace("\\", "/")] = dir_id
            dir_name = rel_root.name
            short_dir = get_short_name(dir_name, dir_short_names[parent_key])
            dir_entry = f"{short_dir}|{dir_name}" if short_dir != dir_name else dir_name
            directories.append((dir_id, parent_dir_id, dir_entry))

        for file_name in files:
            if root_path == dist_dir and file_name.endswith((".msi", ".cab", ".zip", ".deb", ".rpm")):
                continue
            file_path = root_path / file_name
            rel_file = file_path.relative_to(dist_dir)
            parent_key = str(rel_file.parent).replace("\\", "/") if str(rel_file.parent) != "." else ""
            all_files.append((file_path, rel_file, dir_map[parent_key], parent_key))

    rel_paths_set = {str(r[1]).replace("\\", "/") for r in all_files}
    if "_internal/base_library.zip" not in rel_paths_set:
        raise RuntimeError("Critical error: _internal/base_library.zip missing from MSI file list!")

    msilib.add_data(db, "Directory", directories)

    # Upgrade table to automatically replace any previous OpenReview installation
    msilib.add_data(db, "Upgrade", [
        (UPGRADE_CODE, "0.0.0", "1.0.0", None, 257, None, "OLDPRODUCTS"),
    ])

    # Feature table
    msilib.add_data(db, "Feature", [
        ("MainFeature", None, "OpenReview", "Complete OpenReview 64-bit Application and SDK", 1, 1, "INSTALLDIR", 0)
    ])

    # Components and Files
    components = []
    feature_components = []
    file_records = []
    cab_files = []
    comp_seq = 1
    file_short_names: dict[str, set[str]] = {}
    main_exe_file_id = generate_short_id("FL", "OpenReview.exe")

    for file_path, rel_file, dir_id, parent_key in all_files:
        comp_id = generate_short_id("CMP", str(rel_file))
        file_id = generate_short_id("FL", str(rel_file))
        guid = "{" + str(uuid.uuid5(uuid.NAMESPACE_DNS, str(rel_file))).upper() + "}"
        size = file_path.stat().st_size

        if parent_key not in file_short_names:
            file_short_names[parent_key] = set()

        short_fname = get_short_name(rel_file.name, file_short_names[parent_key])
        file_entry = f"{short_fname}|{rel_file.name}" if short_fname != rel_file.name else rel_file.name

        # Attributes 256 = msidbComponentAttributes64bit
        components.append((comp_id, guid, dir_id, 256, None, file_id))
        feature_components.append(("MainFeature", comp_id))
        # Attributes: 512 (Vital) | 16384 (Compressed in cabinet) = 16896
        file_records.append((file_id, comp_id, file_entry, size, None, None, 16896, comp_seq))

        cab_files.append((str(file_path), file_id))
        comp_seq += 1

    # Conditional Desktop Shortcut component (controlled by DESKTOP_SHORTCUT checkbox)
    desktop_comp_id = "CMP_DesktopShortcut"
    desktop_guid = "{" + str(uuid.uuid5(uuid.NAMESPACE_DNS, "OpenReview.DesktopShortcut")).upper() + "}"
    # Attributes: 256 (64-bit) | 4 (Registry KeyPath) = 260
    components.append((desktop_comp_id, desktop_guid, "DesktopFolder", 260, "DESKTOP_SHORTCUT", "REG_DesktopSC"))
    feature_components.append(("MainFeature", desktop_comp_id))

    msilib.add_data(db, "Component", components)
    msilib.add_data(db, "FeatureComponents", feature_components)
    msilib.add_data(db, "File", file_records)

    msilib.add_data(db, "Registry", [
        ("REG_DesktopSC", 1, r"Software\OpenReview\Installer", "DesktopShortcut", "1", desktop_comp_id),
    ])

    # Shortcuts (Start Menu always + Desktop conditional on DESKTOP_SHORTCUT)
    main_comp = generate_short_id("CMP", "OpenReview.exe")
    msilib.add_data(db, "Shortcut", [
        (
            "SC_StartMenu", "ProgramMenuFolder", "OPENRE~1|OpenReview", main_comp,
            "[INSTALLDIR]OpenReview.exe", None, "OpenReview VFX Workstation",
            None, "openreview.ico", 0, None, "INSTALLDIR"
        ),
        (
            "SC_Desktop", "DesktopFolder", "OPENRE~1|OpenReview", desktop_comp_id,
            "[INSTALLDIR]OpenReview.exe", None, "OpenReview VFX Workstation",
            None, "openreview.ico", 0, None, "INSTALLDIR"
        ),
    ])

    # Custom Action to launch OpenReview when Finish is clicked on ExitDialog
    # Type 210 = Exe (2) | File (16) | Continue (64) | Async (128)
    msilib.add_data(db, "CustomAction", [
        ("LaunchApplication", 210, main_exe_file_id, ""),
    ])

    # Windows Explorer File Association for .ovr (OpenReview Session)
    msilib.add_data(db, "ProgId", [
        ("OpenReview.Session", None, None, "OpenReview Session File", "openreview.ico", 0),
    ])
    msilib.add_data(db, "Extension", [
        ("ovr", main_comp, "OpenReview.Session", "application/x-openreview", "MainFeature"),
    ])
    msilib.add_data(db, "Verb", [
        ("ovr", "open", 0, "&Open with OpenReview", '"%1"'),
    ])

    # Media table
    msilib.add_data(db, "Media", [
        (1, len(cab_files), "Disk 1", "#media1.cab", None, None)
    ])

    # Configure Wizard UI Dialogs and Sequences
    with tempfile.TemporaryDirectory() as tmp_dir:
        banner_bmp, dialog_bmp = create_installer_bitmaps(Path(tmp_dir), icon_png)
        configure_ui_wizard(db, banner_bmp, dialog_bmp)
        validate_msi_dialogs(db)
        db.Commit()

    # Create internal cabinet file and embed into MSI
    temp_cab = project_root / "media1.cab"
    if temp_cab.exists():
        temp_cab.unlink()

    print(f"Compressing {len(cab_files)} application files (including _internal/base_library.zip) into cabinet...")
    msilib.FCICreate(str(temp_cab), cab_files)

    print("Embedding cabinet into MSI database...")
    msilib.add_stream(db, "media1.cab", str(temp_cab))
    db.Commit()
    db.Close()

    if temp_cab.exists():
        temp_cab.unlink()

    size_mb = output_msi.stat().st_size / (1024 * 1024)
    print(f"Successfully generated 64-bit MSI Installer: {output_msi} ({size_mb:.1f} MB)")
    return output_msi


def build_portable_zip(dist_dir: Path) -> Path:
    """Create a self-contained 64-bit Windows portable ZIP archive inside Openreview_build."""
    output_zip = dist_dir / f"{PRODUCT_NAME}-{PRODUCT_VERSION}-win64-portable.zip"
    if output_zip.exists():
        output_zip.unlink()

    print(f"Creating 64-bit Windows Portable ZIP archive: {output_zip}")
    file_count = 0
    has_base_lib = False
    with zipfile.ZipFile(output_zip, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for root, _dirs, files in os.walk(dist_dir):
            root_path = Path(root)
            for file_name in files:
                if root_path == dist_dir and file_name.endswith((".msi", ".cab", ".zip", ".deb", ".rpm")):
                    continue
                file_path = root_path / file_name
                rel_inner = file_path.relative_to(dist_dir)
                if str(rel_inner).replace("\\", "/") == "_internal/base_library.zip":
                    has_base_lib = True
                rel_path = Path(f"{PRODUCT_NAME}-{PRODUCT_VERSION}-win64-portable") / rel_inner
                zf.write(file_path, arcname=str(rel_path))
                file_count += 1

    if not has_base_lib:
        raise RuntimeError("Critical error: _internal/base_library.zip missing from Portable ZIP!")

    size_mb = output_zip.stat().st_size / (1024 * 1024)
    print(f"Successfully packed {file_count} files into Portable ZIP: {output_zip} ({size_mb:.1f} MB)")
    return output_zip


def main() -> None:
    project_root = Path(__file__).resolve().parents[1]
    dist_dir = project_root / "Openreview_build"

    if not dist_dir.exists():
        print(f"Error: Build directory not found: {dist_dir}")
        sys.exit(1)

    build_msi(dist_dir, project_root)
    build_portable_zip(dist_dir)


if __name__ == "__main__":
    main()
