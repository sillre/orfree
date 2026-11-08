"""Finding and opening the programs a user wants to jump to.

CC Switch and the Codex desktop app are installed in ways that differ from
machine to machine, so their locations are discovered rather than hard-coded.
"""

from __future__ import annotations

import os
import subprocess
import sys

CC_SWITCH_NAMES = ("cc-switch.exe", "CC Switch.exe")

CC_SWITCH_DIRS = (
    r"F:\cc",
    r"C:\Program Files\cc-switch",
    r"C:\Program Files (x86)\cc-switch",
    os.path.join(os.path.expanduser("~"), "AppData", "Local", "Programs", "cc-switch"),
)


def _process_path(process_name: str) -> str | None:
    """The executable of a running process, if it is running."""
    if not sys.platform.startswith("win"):
        return None
    command = [
        "powershell",
        "-NoProfile",
        "-Command",
        "(Get-Process -Name '%s -ErrorAction SilentlyContinue).Path" % process_name,
    ]
    try:
        result = subprocess.run(command, capture_output=True, timeout=15, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    lines = [line.strip() for line in result.stdout.decode("utf-8", "replace").splitlines()]
    for line in lines:
        if line.lower().endswith(".exe") and os.path.exists(line):
            return line
    return None


def find_cc_switch() -> str | None:
    """Where CC Switch lives, if it can be found."""
    running = _process_path("cc-switch")
    if running:
        return running
    for folder in CC_SWITCH_DIRS:
        for name in CC_SWITCH_NAMES:
            candidate = os.path.join(folder, name)
            if os.path.exists(candidate):
                return candidate
    return None


def codex_app_id() -> str:
    """The identifier that launches the Codex desktop app.

    The app is a packaged (MSIX) program, so it is started through its
    package family and application id.
    """
    if sys.platform != "win32":
        return ""
    try:
        import winreg
    except ImportError:
        return ""

    key_path = r"SOFTWARE\Classes\ActivatableClasses\Package"
    try:
        root = winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path)
    except OSError:
        return ""

    index = 0
    try:
        while True:
            name = winreg.EnumKey(root, index)
            index += 1
            if not name.lower().startswith("openai.codex"):
                continue
            # The key looks like OpenAI.Codex_1.2.3.0_x64__publisher
            publisher = name.rsplit("__", 1)[-1]
            return "OpenAI.Codex_%s!App" % publisher
    except OSError:
        return ""
    finally:
        winreg.CloseKey(root)


def _launch(target: str) -> None:
    """Start a program or a shell link."""
    if sys.platform.startswith("win"):
        os.startfile(target)  # type: ignore[attr-defined]
    else:
        subprocess.Popen(["open", target])


def open_cc_switch() -> tuple[bool, str]:
    """Bring CC Switch to the front."""
    path = find_cc_switch()
    if not path:
        return False, "CC Switch was not found on this machine"
    _launch(path)
    return True, "Opened CC Switch"


def open_codex() -> tuple[bool, str]:
    """Bring the Codex desktop app to the front."""
    app_id = codex_app_id()
    if not app_id:
        return False, "The Codex desktop app was not found on this machine"
    _launch("shell:AppsFolder\\" + app_id)
    return True, "Opened Codex"
