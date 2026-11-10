#!/usr/bin/env python3
"""KiCad PCB Editor External Plugin wrapper for the iData release script."""

from __future__ import annotations

import shlex
import shutil
import subprocess
import sys
from pathlib import Path


PLUGIN_NAME = "idata_release"
PLUGIN_ROOT = Path(__file__).resolve().parents[2]
RELEASE_SCRIPT = PLUGIN_ROOT / "scripts" / "idata_release" / "idata_release.py"
ICON_PATH = Path(__file__).resolve().with_name("idata_release.png")
TERMINAL_CANDIDATES = (
    ("gnome-terminal", "--"),
    ("x-terminal-emulator", "-x"),
)


def find_terminal_command(
    *,
    executable_finder=shutil.which,
) -> list[str]:
    """Find a terminal emulator and its command execution option."""

    for executable_name, option in TERMINAL_CANDIDATES:
        executable = executable_finder(executable_name)
        if executable:
            return [executable, option]
    raise ValueError(
        "Nem található terminálprogram. Telepítsd az x-terminal-emulator vagy "
        "gnome-terminal csomagot."
    )


def build_terminal_command(
    terminal_command: list[str],
    project_directory: Path,
    *,
    python_executable: str = sys.executable,
    release_script: Path = RELEASE_SCRIPT,
) -> list[str]:
    """Build a terminal command that keeps the window open after the release."""

    release_command = shlex.join(
        [python_executable, str(release_script), str(project_directory)]
    )
    shell_command = (
        f"{release_command}; "
        "status=$?; "
        "echo; "
        "if [ \"$status\" -eq 0 ]; then "
        "read -r -p 'Release finished. Press Enter to close...' _; "
        "else "
        "read -r -p 'Release failed. Press Enter to close...' _; "
        "fi; "
        "exit \"$status\""
    )
    return [*terminal_command, "bash", "-lc", shell_command]


try:  # KiCad supplies these modules when it loads an External Plugin.
    import pcbnew  # type: ignore[import-not-found]
    import wx  # type: ignore[import-not-found]
except ImportError:  # Keep the wrapper importable for tests and static checks.
    pcbnew = None
    wx = None


def is_kicad_runtime() -> bool:
    """Return whether pcbnew is hosted by a running KiCad wx application."""

    if pcbnew is None or wx is None:
        return False
    try:
        return wx.App.Get() is not None
    except (AttributeError, RuntimeError):
        return False


def _require_gui() -> None:
    if wx is None:
        raise RuntimeError("Az idata_release csak KiCad PCB Editorből futtatható")


def _dialog_parent():
    _require_gui()
    app = wx.GetApp()
    get_top_window = getattr(app, "GetTopWindow", None) if app is not None else None
    if callable(get_top_window):
        top_window = get_top_window()
        if top_window is not None:
            return top_window
    return wx.GetActiveWindow()


def _show_message(message: str, *, warning: bool = False) -> None:
    _require_gui()
    style = wx.OK | (wx.ICON_WARNING if warning else wx.ICON_INFORMATION)
    dialog = wx.MessageDialog(_dialog_parent(), message, PLUGIN_NAME, style)
    try:
        dialog.ShowModal()
    finally:
        dialog.Destroy()


def _project_directory() -> Path:
    _require_gui()
    board = pcbnew.GetBoard()
    if board is None:
        raise RuntimeError("Nincs megnyitott PCB a KiCad PCB Editorban.")
    board_filename = board.GetFileName()
    if not board_filename:
        raise RuntimeError("A PCB-t előbb mentsd el egy KiCad projekt mappájába.")
    return Path(board_filename).expanduser().resolve().parent


class IdataReleasePlugin(pcbnew.ActionPlugin if pcbnew is not None else object):
    def defaults(self) -> None:
        self.name = PLUGIN_NAME
        self.category = "iData"
        self.description = "KiCad projekt release generálása"
        self.pcbnew_icon_support = hasattr(self, "show_toolbar_button")
        self.show_toolbar_button = True
        self.icon_file_name = str(ICON_PATH)
        self.dark_icon_file_name = str(ICON_PATH)

    def Run(self) -> None:
        try:
            project_directory = _project_directory()
            terminal_command = build_terminal_command(
                find_terminal_command(),
                project_directory,
            )
            subprocess.Popen(terminal_command, cwd=project_directory)
        except (OSError, RuntimeError, ValueError) as error:
            _show_message(f"Release terminál indítási hiba:\n{error}", warning=True)


if is_kicad_runtime():
    IdataReleasePlugin().register()
