#!/usr/bin/env python3
"""Import an LCSC component downloaded by easyeda2kicad into this KiCad repo."""

from __future__ import annotations

import argparse
import curses
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Sequence


REPO_ROOT = Path(__file__).resolve().parent
DOWNLOAD_DIR = Path.home() / "Documents" / "Kicad" / "easyeda2kicad"
FOOTPRINTS_DIR = REPO_ROOT / "footprints"
SYMBOLS_DIR = REPO_ROOT / "symbols"
MODELS_DIR = REPO_ROOT / "3dmodels"

EMPTY_SYMBOL_LIBRARY = """(kicad_symbol_lib
\t(version 20241209)
\t(generator "kicad_symbol_editor")
\t(generator_version "9.0")
)
"""

_SYMBOL_START = re.compile(r'\(symbol\s+"((?:\\.|[^"\\])*)"')
_FOOTPRINT_PROPERTY = re.compile(
    r'(\(property\s+"Footprint"\s+)"(?:\\.|[^"\\])*"'
)
_MODEL_PATH = re.compile(r'(\(model\s+)"(?:\\.|[^"\\])*"')


class ImporterError(RuntimeError):
    """Expected, user-actionable import failure."""


class UserCancelled(ImporterError):
    """The user chose to stop an interactive operation."""


class SymbolExistsError(ImporterError):
    """The selected symbol library already contains the imported symbol."""


class PartialImportError(ImporterError):
    """The import produced files but did not move the symbol."""


@dataclass(frozen=True)
class SymbolBlock:
    name: str
    start: int
    end: int
    content: str


def _unescape_kicad_string(value: str) -> str:
    return re.sub(r"\\(.)", r"\1", value)


def _balanced_block_end(text: str, start: int) -> int:
    if start >= len(text) or text[start] != "(":
        raise ValueError("A KiCad block must start with an opening parenthesis")

    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        character = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
            continue

        if character == '"':
            in_string = True
        elif character == "(":
            depth += 1
        elif character == ")":
            depth -= 1
            if depth == 0:
                return index + 1
            if depth < 0:
                break

    raise ValueError("Unbalanced KiCad parentheses")


def extract_first_symbol(content: str) -> SymbolBlock:
    match = _SYMBOL_START.search(content)
    if match is None:
        raise ImporterError("A letöltött symbol-fájl nem tartalmaz symbol blokkot")
    end = _balanced_block_end(content, match.start())
    return SymbolBlock(
        name=_unescape_kicad_string(match.group(1)),
        start=match.start(),
        end=end,
        content=content[match.start() : end],
    )


def iter_top_level_symbols(content: str) -> Iterable[SymbolBlock]:
    """Yield only symbols directly inside the kicad_symbol_lib root block."""

    depth = 0
    in_string = False
    escaped = False
    index = 0
    while index < len(content):
        character = content[index]
        if in_string:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
            index += 1
            continue

        if character == '"':
            in_string = True
            index += 1
            continue

        if depth == 1 and content.startswith("(symbol", index):
            match = _SYMBOL_START.match(content, index)
            if match is not None:
                end = _balanced_block_end(content, index)
                yield SymbolBlock(
                    name=_unescape_kicad_string(match.group(1)),
                    start=index,
                    end=end,
                    content=content[index:end],
                )
                index = end
                continue

        if character == "(":
            depth += 1
        elif character == ")":
            depth -= 1
        index += 1


def replace_footprint_property(symbol_content: str, footprint_reference: str) -> str:
    escaped_reference = footprint_reference.replace("\\", "\\\\").replace('"', '\\"')
    replacement = rf'\1"{escaped_reference}"'
    updated, count = _FOOTPRINT_PROPERTY.subn(replacement, symbol_content, count=1)
    if count == 0:
        raise ImporterError('A symbol nem tartalmaz "Footprint" property-t')
    return updated


def replace_model_references(footprint_content: str, model_filename: str) -> str:
    model_path = f"${{CUSTOM_3DMODEL_DIR}}/{model_filename}"
    escaped_path = model_path.replace("\\", "\\\\").replace('"', '\\"')
    replacement = rf'\1"{escaped_path}"'
    updated, count = _MODEL_PATH.subn(replacement, footprint_content)
    if count == 0:
        raise ImporterError("A letöltött footprint nem tartalmaz 3D model hivatkozást")
    return updated


def insert_symbol(
    library_content: str,
    symbol_content: str,
    symbol_name: str,
    *,
    overwrite: bool = False,
) -> str:
    existing = next(
        (symbol for symbol in iter_top_level_symbols(library_content) if symbol.name == symbol_name),
        None,
    )
    if existing is not None:
        if not overwrite:
            raise SymbolExistsError(
                f'A(z) "{symbol_name}" symbol már létezik a célkönyvtárban'
            )
        return (
            library_content[: existing.start]
            + symbol_content.rstrip()
            + library_content[existing.end :]
        )

    closing_parenthesis = library_content.rfind(")")
    if closing_parenthesis < 0:
        raise ImporterError("A cél symbol-fájl érvénytelen: hiányzik a záró zárójel")
    prefix = library_content[:closing_parenthesis].rstrip("\n")
    suffix = library_content[closing_parenthesis:]
    return f"{prefix}\n{symbol_content.rstrip()}\n{suffix}"


def remove_symbol(source_content: str, symbol: SymbolBlock) -> str:
    remaining = source_content[: symbol.start] + source_content[symbol.end :]
    if not any(iter_top_level_symbols(remaining)):
        return EMPTY_SYMBOL_LIBRARY
    return remaining


def _iter_files_recursively(directory: Path) -> Iterable[Path]:
    if not directory.exists():
        return
    for path in directory.rglob("*"):
        if path.is_file() or path.is_symlink():
            yield path


def has_stale_download_output(download_dir: Path) -> bool:
    for directory_name in ("easyeda2kicad.pretty", "easyeda2kicad.3dshapes"):
        if any(_iter_files_recursively(download_dir / directory_name)):
            return True

    symbol_file = download_dir / "easyeda2kicad.kicad_sym"
    if symbol_file.is_file():
        content = symbol_file.read_text(encoding="utf-8")
        try:
            if any(iter_top_level_symbols(content)):
                return True
        except ValueError:
            return bool(content.strip())
    return False


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, delete=False
    ) as temporary:
        temporary.write(content)
        temporary_path = Path(temporary.name)
    os.replace(temporary_path, path)


def clean_download_output(download_dir: Path) -> None:
    for directory_name in ("easyeda2kicad.pretty", "easyeda2kicad.3dshapes"):
        directory = download_dir / directory_name
        directory.mkdir(parents=True, exist_ok=True)
        for path in _iter_files_recursively(directory):
            path.unlink()

    _atomic_write(download_dir / "easyeda2kicad.kicad_sym", EMPTY_SYMBOL_LIBRARY)


def copy_3d_models(source_dir: Path, destination_dir: Path) -> dict[str, str]:
    destination_dir.mkdir(parents=True, exist_ok=True)
    results: dict[str, str] = {}
    if not source_dir.is_dir():
        return results

    for source in sorted(source_dir.iterdir(), key=lambda item: item.name.lower()):
        if not source.is_file() or source.suffix.lower() not in {".wrl", ".step"}:
            continue
        destination = destination_dir / source.name
        if destination.exists():
            results[source.name] = "exists"
            continue
        shutil.copy2(source, destination)
        results[source.name] = "copied"
    return results


def find_footprint_file(footprint_dir: Path) -> Path:
    footprints = sorted(footprint_dir.glob("*.kicad_mod"), key=lambda item: item.name.lower())
    if not footprints:
        raise ImporterError("A letöltött pretty könyvtár nem tartalmaz .kicad_mod fájlt")
    if len(footprints) > 1:
        raise ImporterError("A letöltött pretty könyvtár több footprintet tartalmaz")
    return footprints[0]


def choose_model_file(model_dir: Path) -> Path:
    models = [
        path
        for path in model_dir.iterdir()
        if path.is_file() and path.suffix.lower() in {".wrl", ".step"}
    ] if model_dir.is_dir() else []
    models.sort(key=lambda path: (path.suffix.lower() != ".wrl", path.name.lower()))
    if not models:
        raise ImporterError("A letöltés nem tartalmaz .wrl vagy .step 3D-modellt")
    return models[0]


def _selector_options(directory: Path, suffix: str) -> list[str]:
    return sorted(
        [path.name for path in directory.iterdir() if path.is_dir() and path.name.endswith(suffix)],
        key=str.casefold,
    )


def select_from_list(options: Sequence[str], title: str) -> str:
    if not options:
        raise ImporterError(f"Nincs választható elem: {title}")

    def menu(stdscr: object) -> str:
        window = stdscr  # type: ignore[assignment]
        curses.curs_set(0)
        selected = 0
        offset = 0
        while True:
            window.erase()
            height, width = window.getmaxyx()
            window.addnstr(0, 0, title, max(1, width - 1), curses.A_BOLD)
            visible_rows = max(1, height - 3)
            if selected < offset:
                offset = selected
            if selected >= offset + visible_rows:
                offset = selected - visible_rows + 1
            for row, option_index in enumerate(
                range(offset, min(len(options), offset + visible_rows)), start=1
            ):
                prefix = "> " if option_index == selected else "  "
                attributes = curses.A_REVERSE if option_index == selected else curses.A_NORMAL
                window.addnstr(
                    row,
                    0,
                    prefix + options[option_index],
                    max(1, width - 1),
                    attributes,
                )
            window.addnstr(
                height - 1,
                0,
                "Fel/le: választás | Enter: kiválasztás | q/Esc: megszakítás",
                max(1, width - 1),
            )
            window.refresh()
            key = window.getch()
            if key in (curses.KEY_UP, ord("k")):
                selected = (selected - 1) % len(options)
            elif key in (curses.KEY_DOWN, ord("j")):
                selected = (selected + 1) % len(options)
            elif key in (curses.KEY_ENTER, 10, 13):
                return options[selected]
            elif key in (27, ord("q"), ord("Q")):
                raise UserCancelled("A felhasználó megszakította a választást")

    return curses.wrapper(menu)


def _yes(answer: str) -> bool:
    return answer.strip().lower() in {"y", "yes", "i", "igen"}


def _validate_lcsc_id(lcsc_id: str) -> str:
    value = lcsc_id.strip()
    if not re.fullmatch(r"C\d+", value, flags=re.IGNORECASE):
        raise ImporterError("Az LCSC cikkszámnak C betűvel és számokkal kell kezdődnie")
    return value.upper()


def import_component(
    lcsc_id: str,
    *,
    download_dir: Path = DOWNLOAD_DIR,
    footprints_dir: Path = FOOTPRINTS_DIR,
    symbols_dir: Path = SYMBOLS_DIR,
    models_dir: Path = MODELS_DIR,
    selector: Callable[[Sequence[str], str], str] = select_from_list,
    input_fn: Callable[[str], str] = input,
    runner: Callable[..., object] = subprocess.run,
) -> dict[str, object]:
    component_id = _validate_lcsc_id(lcsc_id)
    download_dir.mkdir(parents=True, exist_ok=True)

    if has_stale_download_output(download_dir):
        answer = input_fn(
            "A korábbi sikertelen futás fájljai nem üresek. Takarítsam ki és folytassam? [i/N]: "
        )
        if not _yes(answer):
            raise UserCancelled("A korábbi EasyEDA-kimenet megmaradt, az import leállt")
        clean_download_output(download_dir)

    runner(
        ["easyeda2kicad", "--lcsc_id", component_id, "--full"],
        cwd=str(download_dir),
        check=True,
    )

    generated_footprint_dir = download_dir / "easyeda2kicad.pretty"
    generated_model_dir = download_dir / "easyeda2kicad.3dshapes"
    generated_symbol_file = download_dir / "easyeda2kicad.kicad_sym"
    generated_footprint = find_footprint_file(generated_footprint_dir)
    model_file = choose_model_file(generated_model_dir)
    footprint_content = replace_model_references(
        generated_footprint.read_text(encoding="utf-8"), model_file.name
    )

    model_results = copy_3d_models(generated_model_dir, models_dir)

    footprint_options = _selector_options(footprints_dir, ".pretty")
    selected_footprint_library = selector(
        footprint_options, "Válaszd ki a footprint célkönyvtárát"
    )
    footprint_library_name = Path(selected_footprint_library).name
    if footprint_library_name not in footprint_options:
        raise ImporterError("Érvénytelen footprint-könyvtár választás")
    footprint_destination_dir = footprints_dir / footprint_library_name
    footprint_destination = footprint_destination_dir / generated_footprint.name
    if footprint_destination.exists():
        footprint_status = "exists"
    else:
        _atomic_write(footprint_destination, footprint_content)
        footprint_status = "copied"

    source_symbol_content = generated_symbol_file.read_text(encoding="utf-8")
    source_symbol = extract_first_symbol(source_symbol_content)
    symbol_content = replace_footprint_property(
        source_symbol.content,
        f"0_{footprint_library_name.removesuffix('.pretty')}:{generated_footprint.stem}",
    )

    symbol_options = sorted(
        [
            path.name
            for path in symbols_dir.iterdir()
            if path.is_file() and path.suffix == ".kicad_sym"
        ],
        key=str.casefold,
    )
    selected_symbol_file = selector(symbol_options, "Válaszd ki a symbol célfájlját")
    if selected_symbol_file not in symbol_options:
        raise ImporterError("Érvénytelen symbol-fájl választás")
    symbol_destination = symbols_dir / selected_symbol_file
    target_symbol_content = symbol_destination.read_text(encoding="utf-8")
    existing_symbol = next(
        (
            symbol
            for symbol in iter_top_level_symbols(target_symbol_content)
            if symbol.name == source_symbol.name
        ),
        None,
    )
    overwrite = False
    if existing_symbol is not None:
        overwrite = _yes(
            input_fn(
                f'A(z) "{source_symbol.name}" symbol már létezik. Felülírjam? [i/N]: '
            )
        )
        if not overwrite:
            raise PartialImportError(
                "A footprint és a 3D-modellek feldolgozódtak, de a symbol nem került át"
            )

    updated_target = insert_symbol(
        target_symbol_content,
        symbol_content,
        source_symbol.name,
        overwrite=overwrite,
    )
    _atomic_write(symbol_destination, updated_target)
    _atomic_write(generated_symbol_file, remove_symbol(source_symbol_content, source_symbol))
    clean_download_output(download_dir)

    return {
        "lcsc_id": component_id,
        "symbol": source_symbol.name,
        "footprint": generated_footprint.name,
        "footprint_status": footprint_status,
        "models": model_results,
        "symbol_library": selected_symbol_file,
        "footprint_library": footprint_library_name,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="LCSC alkatrész importálása easyeda2kicad segítségével"
    )
    parser.add_argument("lcsc_id", nargs="?", help="LCSC cikkszám, például C123456")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    lcsc_id = args.lcsc_id or input("LCSC cikkszám (például C123456): ")
    try:
        result = import_component(lcsc_id)
    except UserCancelled as error:
        print(f"Megszakítva: {error}", file=sys.stderr)
        return 2
    except PartialImportError as error:
        print(f"Részleges import: {error}", file=sys.stderr)
        return 1
    except subprocess.CalledProcessError as error:
        print(f"Az easyeda2kicad hibával állt le: {error}", file=sys.stderr)
        return error.returncode or 1
    except FileNotFoundError:
        print("Nem található az easyeda2kicad program a PATH-ban", file=sys.stderr)
        return 1
    except (ImporterError, OSError, ValueError) as error:
        print(f"Import hiba: {error}", file=sys.stderr)
        return 1

    print(f"Sikeres import: {result['lcsc_id']} → {result['symbol']}")
    print(f"  Symbol: {result['symbol_library']}")
    print(f"  Footprint: {result['footprint_library']}/{result['footprint']}")
    for filename, status in result["models"].items():
        message = "már létezett" if status == "exists" else "másolva"
        print(f"  3D modell: {filename} ({message})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
