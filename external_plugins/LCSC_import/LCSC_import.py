#!/usr/bin/env python3
"""KiCad PCB Editor External Plugin wrapper for the LCSC importer."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Sequence


PLUGIN_ROOT = Path(__file__).resolve().parents[2]
IMPORTER_PATH = PLUGIN_ROOT / "kicad_lib_importer.py"
ICON_PATH = Path(__file__).resolve().with_name("LCSC_import.png")
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

from kicad_lib_importer import (  # noqa: E402
    ImporterError,
    PartialImportError,
    UserCancelled,
    import_component,
)

try:  # KiCad supplies these modules when it loads an External Plugin.
    import pcbnew  # type: ignore[import-not-found]
    import wx  # type: ignore[import-not-found]
except ImportError:  # Keep the wrapper importable for tests and static checks.
    pcbnew = None
    wx = None


PLUGIN_NAME = "LCSC_import"


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
        raise RuntimeError("A LCSC_import csak KiCad PCB Editorből futtatható")


def _dialog_parent():
    _require_gui()
    app = wx.GetApp()
    get_top_window = getattr(app, "GetTopWindow", None) if app is not None else None
    if callable(get_top_window):
        top_window = get_top_window()
        if top_window is not None:
            return top_window
    return wx.GetActiveWindow()


def ask_lcsc_id() -> str | None:
    _require_gui()
    dialog = wx.TextEntryDialog(
        _dialog_parent(),
        "Add meg az LCSC cikkszámot (például C123456):",
        PLUGIN_NAME,
    )
    try:
        if dialog.ShowModal() != wx.ID_OK:
            return None
        value = dialog.GetValue().strip()
        return value or None
    finally:
        dialog.Destroy()


def ask_yes_no(prompt: str) -> str:
    _require_gui()
    dialog = wx.MessageDialog(
        _dialog_parent(),
        prompt,
        PLUGIN_NAME,
        wx.YES_NO | wx.ICON_QUESTION,
    )
    try:
        return "i" if dialog.ShowModal() == wx.ID_YES else "n"
    finally:
        dialog.Destroy()


def select_from_list(options: Sequence[str], title: str) -> str:
    _require_gui()
    dialog = wx.SingleChoiceDialog(
        _dialog_parent(),
        title,
        PLUGIN_NAME,
        list(options),
    )
    try:
        if dialog.ShowModal() != wx.ID_OK:
            raise UserCancelled("A felhasználó megszakította a választást")
        return dialog.GetStringSelection()
    finally:
        dialog.Destroy()


def show_message(message: str, *, warning: bool = False) -> None:
    _require_gui()
    style = wx.OK | (wx.ICON_WARNING if warning else wx.ICON_INFORMATION)
    dialog = wx.MessageDialog(_dialog_parent(), message, PLUGIN_NAME, style)
    try:
        dialog.ShowModal()
    finally:
        dialog.Destroy()


def format_result(result: dict[str, object]) -> str:
    lines = [
        f"Sikeres import: {result['lcsc_id']} → {result['symbol']}",
        f"Symbol: {result['symbol_library']}",
        f"Footprint: {result['footprint_library']}/{result['footprint']}",
    ]
    for filename, status in result["models"].items():  # type: ignore[union-attr]
        state = "már létezett" if status == "exists" else "másolva"
        lines.append(f"3D modell: {filename} ({state})")
    return "\n".join(lines)


class LCSCImportPlugin(pcbnew.ActionPlugin if pcbnew is not None else object):
    def defaults(self) -> None:
        self.name = PLUGIN_NAME
        self.category = "LCSC"
        self.description = "LCSC alkatrész importálása a saját KiCad-könyvtárakba"
        self.pcbnew_icon_support = hasattr(self, "show_toolbar_button")
        self.show_toolbar_button = True
        self.icon_file_name = str(ICON_PATH)
        self.dark_icon_file_name = str(ICON_PATH)

    def Run(self) -> None:
        lcsc_id = ask_lcsc_id()
        if not lcsc_id:
            return

        try:
            result = import_component(
                lcsc_id,
                selector=select_from_list,
                input_fn=ask_yes_no,
            )
        except UserCancelled:
            return
        except PartialImportError as error:
            show_message(f"Részleges import:\n{error}", warning=True)
        except subprocess.CalledProcessError as error:
            show_message(f"Az easyeda2kicad hibával állt le:\n{error}", warning=True)
        except FileNotFoundError:
            show_message("Nem található az easyeda2kicad program a PATH-ban", warning=True)
        except (ImporterError, OSError, ValueError) as error:
            show_message(f"Import hiba:\n{error}", warning=True)
        else:
            show_message(format_result(result))


if is_kicad_runtime():
    LCSCImportPlugin().register()
