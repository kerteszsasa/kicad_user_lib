#!/usr/bin/env python3
"""Run KiCad checks and generate iData release artifacts."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, TextIO


KICAD_CLI = "kicad-cli"
BOM_PRESET_NAME = "IDATA_BOM_VIEW"
REQUIRED_BOM_FIELDS = (
    ("Reference", "Reference"),
    ("Value", "Value"),
    ("Footprint", "Footprint"),
    ("PART NUMBER", "PART NUMBER"),
    ("Description", "Description"),
    ("${QUANTITY}", "Qty"),
)


class CheckExecutionError(RuntimeError):
    """Raised when KiCad cannot complete an ERC or DRC check."""


@dataclass(frozen=True)
class ProjectFiles:
    project_directory: Path
    project_file: Path
    schematic_file: Path
    pcb_file: Path


@dataclass(frozen=True)
class ArtifactPaths:
    output_directory: Path
    plot_file: Path
    bom_file: Path
    bom_xls_file: Path
    assembly_file: Path
    interactive_html_bom_file: Path
    production_directory: Path
    gerber_file: Path
    positions_file: Path
    release_archive_file: Path


@dataclass(frozen=True)
class Violation:
    severity: str
    description: str
    excluded: bool = False


@dataclass(frozen=True)
class CheckResult:
    name: str
    violations: tuple[Violation, ...]

    @property
    def warning_count(self) -> int:
        return sum(
            violation.severity == "warning" and not violation.excluded
            for violation in self.violations
        )

    @property
    def error_count(self) -> int:
        return sum(
            violation.severity == "error" and not violation.excluded
            for violation in self.violations
        )

    @property
    def excluded_warning_count(self) -> int:
        return sum(
            violation.severity == "warning" and violation.excluded
            for violation in self.violations
        )

    @property
    def excluded_error_count(self) -> int:
        return sum(
            violation.severity == "error" and violation.excluded
            for violation in self.violations
        )

    @property
    def has_violations(self) -> bool:
        return bool(self.error_count or self.warning_count)

    @property
    def has_excluded_violations(self) -> bool:
        return bool(self.excluded_error_count or self.excluded_warning_count)


Runner = Callable[..., subprocess.CompletedProcess[str]]
Board2PdfFinder = Callable[[], list[str]]
InteractiveHtmlBomFinder = Callable[[], list[str]]
FabricationToolkitFinder = Callable[[], list[str]]
SsconvertFinder = Callable[[], list[str]]

FABRICATION_TOOLKIT_PACKAGE = "com_github_bennymeg_JLC-Plugin-for-KiCad"
FABRICATION_TOOLKIT_RUNNER = (
    "import runpy,sys;"
    "sys.path.insert(0,sys.argv.pop(1));"
    f"runpy.run_module({FABRICATION_TOOLKIT_PACKAGE + '.cli'!r}, run_name='__main__')"
)


def find_board2pdf_command(
    *,
    executable_finder: Callable[[str], str | None] = shutil.which,
    home_directory: Path | None = None,
) -> list[str]:
    """Find the Board2Pdf CLI executable or installed KiCad plugin script."""

    executable = executable_finder("board2pdf")
    if executable:
        return [executable]

    home = home_directory or Path.home()
    plugin_script = (
        home
        / ".local/share/kicad/9.0/3rdparty/plugins"
        / "com_gitlab_dennevi_Board2Pdf"
        / "board2pdf-cli.py"
    )
    if plugin_script.is_file():
        return [sys.executable, str(plugin_script)]

    raise ValueError(
        "Board2Pdf plugin was not found. Please install the Board2Pdf plugin "
        "before running a release."
    )


def find_interactive_html_bom_command(
    *,
    home_directory: Path | None = None,
) -> list[str]:
    """Find the installed InteractiveHtmlBom plugin CLI script."""

    home = home_directory or Path.home()
    plugin_scripts = sorted(
        home.glob(
            ".local/share/kicad/*/3rdparty/plugins/"
            "org_openscopeproject_InteractiveHtmlBom/generate_interactive_bom.py"
        ),
        reverse=True,
    )
    for plugin_script in plugin_scripts:
        if plugin_script.is_file():
            return [sys.executable, str(plugin_script)]

    raise ValueError(
        "Interactive HTML BOM plugin was not found. Please install the "
        "InteractiveHtmlBom plugin before running a release."
    )


def find_fabrication_toolkit_command(
    *,
    home_directory: Path | None = None,
) -> list[str]:
    """Find the installed Fabrication Toolkit CLI and its Python package root."""

    home = home_directory or Path.home()
    plugin_scripts = sorted(
        home.glob(
            ".local/share/kicad/*/3rdparty/plugins/"
            f"{FABRICATION_TOOLKIT_PACKAGE}/cli.py"
        ),
        reverse=True,
    )
    for plugin_script in plugin_scripts:
        if plugin_script.is_file():
            return [
                sys.executable,
                "-c",
                FABRICATION_TOOLKIT_RUNNER,
                str(plugin_script.parent.parent),
            ]

    raise ValueError(
        "Fabrication Toolkit plugin was not found. Please install the "
        "Fabrication Toolkit plugin before running a release."
    )


def find_ssconvert_command(
    *,
    executable_finder: Callable[[str], str | None] = shutil.which,
) -> list[str]:
    """Find the ssconvert command provided by the Gnumeric package."""

    executable = executable_finder("ssconvert")
    if executable:
        return [executable]

    raise ValueError(
        "ssconvert was not found. Please install the gnumeric package "
        "before running a release."
    )


def _single_named_file(project_directory: Path, suffix: str, label: str) -> Path:
    candidates = sorted(project_directory.glob(f"*{suffix}"))
    expected = project_directory / f"{project_directory.name}{suffix}"
    if len(candidates) != 1 or candidates[0] != expected:
        names = ", ".join(path.name for path in candidates) or "none"
        raise ValueError(
            f"Expected exactly one {label} named {expected.name}; found: {names}."
        )
    return expected


def find_project_files(project_path: str | Path) -> ProjectFiles:
    """Resolve the root KiCad files in a project directory."""

    project_directory = Path(project_path).expanduser().resolve()
    if not project_directory.is_dir():
        raise ValueError(f"Project directory does not exist: {project_directory}")

    project_file = _single_named_file(project_directory, ".kicad_pro", "project file")
    pcb_file = _single_named_file(project_directory, ".kicad_pcb", "PCB file")
    schematic_file = project_directory / f"{project_directory.name}.kicad_sch"
    if not schematic_file.is_file():
        raise ValueError(f"Missing root schematic file: {schematic_file.name}")

    return ProjectFiles(project_directory, project_file, schematic_file, pcb_file)


def _required_bom_fields_text() -> str:
    return ", ".join(label for _, label in REQUIRED_BOM_FIELDS)


def validate_bom_preset(project_file: str | Path) -> str:
    """Validate the project BOM preset required by the release process."""

    project_path = Path(project_file)
    try:
        project = json.loads(project_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Could not read KiCad project file {project_path}: {exc}") from exc

    presets = None
    if isinstance(project, Mapping):
        for settings_name in ("schematic", "pcbnew"):
            settings = project.get(settings_name)
            if isinstance(settings, Mapping) and "bom_presets" in settings:
                presets = settings.get("bom_presets")
                break
        if presets is None:
            presets = project.get("bom_presets")
    preset = None
    if isinstance(presets, list):
        preset = next(
            (
                candidate
                for candidate in presets
                if isinstance(candidate, Mapping)
                and candidate.get("name") == BOM_PRESET_NAME
            ),
            None,
        )

    if preset is None:
        raise ValueError(
            f"Required BOM preset '{BOM_PRESET_NAME}' was not found in {project_path}. "
            f"Required fields: {_required_bom_fields_text()}."
        )

    fields = preset.get("fields_ordered")
    visible_fields = {
        (field.get("name"), field.get("label"))
        for field in fields or []
        if isinstance(field, Mapping) and field.get("show") is True
    }
    missing_fields = [
        label
        for name, label in REQUIRED_BOM_FIELDS
        if (name, label) not in visible_fields
    ]
    if missing_fields:
        raise ValueError(
            f"BOM preset '{BOM_PRESET_NAME}' is missing required fields: "
            f"{', '.join(missing_fields)}. Required fields: {_required_bom_fields_text()}."
        )

    return BOM_PRESET_NAME


def artifact_paths(files: ProjectFiles) -> ArtifactPaths:
    output_directory = files.project_directory / "DOC"
    project_name = files.project_directory.name
    production_directory = files.project_directory / "production"
    production_output_directory = files.project_directory / "PRODUCTION"
    return ArtifactPaths(
        output_directory=output_directory,
        plot_file=output_directory / f"{project_name}.pdf",
        bom_file=production_output_directory / f"{project_name}_BOM.csv",
        bom_xls_file=output_directory / f"{project_name}_BOM.xls",
        assembly_file=output_directory / f"{project_name}_Assembly.pdf",
        interactive_html_bom_file=output_directory / f"{project_name}.html",
        production_directory=production_directory,
        gerber_file=production_output_directory / f"{project_name}_Gerber.zip",
        positions_file=production_output_directory / f"{project_name}_Positions.csv",
        release_archive_file=files.project_directory / f"{project_name}.zip",
    )


def _report_violations(check_name: str, report: Mapping[str, Any]) -> Iterable[Mapping[str, Any]]:
    if check_name == "ERC":
        sheets = report.get("sheets")
        if not isinstance(sheets, list):
            raise ValueError("ERC report does not contain a valid 'sheets' list.")
        for sheet in sheets:
            if not isinstance(sheet, Mapping):
                raise ValueError("ERC report contains an invalid sheet entry.")
            violations = sheet.get("violations", [])
            if not isinstance(violations, list):
                raise ValueError("ERC report contains an invalid 'violations' list.")
            yield from violations
        return

    if check_name == "DRC":
        violations = report.get("violations")
        if not isinstance(violations, list):
            raise ValueError("DRC report does not contain a valid 'violations' list.")
        yield from violations
        return

    raise ValueError(f"Unknown check type: {check_name}")


def parse_report(check_name: str, report: Mapping[str, Any]) -> CheckResult:
    """Convert a KiCad JSON report into the release check result."""

    normalized_name = check_name.upper()
    violations: list[Violation] = []
    for entry in _report_violations(normalized_name, report):
        if not isinstance(entry, Mapping):
            raise ValueError(f"{normalized_name} report contains an invalid violation entry.")
        severity = str(entry.get("severity", "")).lower()
        if severity not in {"warning", "error"}:
            continue
        description = str(entry.get("description", "Unspecified violation"))
        violations.append(Violation(severity, description, entry.get("excluded") is True))
    return CheckResult(normalized_name, tuple(violations))


def _format_result(result: CheckResult) -> str:
    lines = [
        f"{result.name}: {result.error_count} error(s), "
        f"{result.warning_count} warning(s)."
    ]
    if result.has_excluded_violations:
        lines.append(
            f"Excluded: {result.excluded_error_count} error(s), "
            f"{result.excluded_warning_count} warning(s)."
        )
    for violation in result.violations:
        excluded_label = "excluded " if violation.excluded else ""
        lines.append(f"  [{excluded_label}{violation.severity}] {violation.description}")
    return "\n".join(lines)


def _check_command(check_name: str, input_file: Path, report_file: Path) -> list[str]:
    if check_name == "ERC":
        command = [KICAD_CLI, "sch", "erc"]
    elif check_name == "DRC":
        command = [KICAD_CLI, "pcb", "drc"]
    else:
        raise ValueError(f"Unknown check type: {check_name}")
    return command + [
        "--format",
        "json",
        "--severity-all",
        "--output",
        str(report_file),
        str(input_file),
    ]


def run_check(
    check_name: str,
    input_file: str | Path,
    *,
    runner: Runner = subprocess.run,
    output_stream: TextIO | None = None,
) -> CheckResult:
    """Run one KiCad check and parse its temporary JSON report."""

    output_stream = output_stream or sys.stdout
    normalized_name = check_name.upper()
    input_path = Path(input_file)

    with tempfile.NamedTemporaryFile(
        mode="w", prefix="idata_release_", suffix=f"_{normalized_name.lower()}.json", delete=False
    ) as temporary_report:
        report_path = Path(temporary_report.name)

    try:
        command = _check_command(normalized_name, input_path, report_path)
        try:
            completed = runner(command, capture_output=True, text=True, check=False)
        except OSError as exc:
            raise CheckExecutionError(
                f"Could not run {KICAD_CLI} for {normalized_name}: {exc}"
            ) from exc

        if completed.stdout:
            print(completed.stdout.rstrip(), file=output_stream)
        if completed.stderr:
            print(completed.stderr.rstrip(), file=output_stream)
        if completed.returncode != 0:
            raise CheckExecutionError(
                f"{normalized_name} failed with exit code {completed.returncode}."
            )

        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
            if not isinstance(report, Mapping):
                raise ValueError("Report root is not a JSON object.")
            result = parse_report(normalized_name, report)
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            raise CheckExecutionError(
                f"Could not parse the {normalized_name} report: {exc}"
            ) from exc

        print(_format_result(result), file=output_stream)
        return result
    finally:
        report_path.unlink(missing_ok=True)


def _ask_to_continue(
    result: CheckResult,
    *,
    input_stream: TextIO,
    output_stream: TextIO,
) -> bool:
    print(
        f"{result.name} found active violations. Continue? [y/N] ",
        end="",
        file=output_stream,
        flush=True,
    )
    answer = input_stream.readline().strip().lower()
    return answer in {"y", "yes"}


def _ask_about_excluded_violations(
    result: CheckResult,
    *,
    input_stream: TextIO,
    output_stream: TextIO,
) -> bool:
    print(
        f"{result.name} has excluded violations. Are they a problem? [Y/n] ",
        end="",
        file=output_stream,
        flush=True,
    )
    answer = input_stream.readline().strip().lower()
    return answer not in {"n", "no"}


def _run_export(
    export_type: str,
    input_file: Path,
    output_file: Path,
    options: list[str],
    *,
    runner: Runner,
    output_stream: TextIO,
) -> None:
    command = [
        KICAD_CLI,
        "sch",
        "export",
        export_type,
        *options,
        "--output",
        str(output_file),
        str(input_file),
    ]
    try:
        completed = runner(command, capture_output=True, text=True, check=False)
    except OSError as exc:
        raise CheckExecutionError(
            f"Could not run {KICAD_CLI} for {export_type} export: {exc}"
        ) from exc

    if completed.stdout:
        print(completed.stdout.rstrip(), file=output_stream)
    if completed.stderr:
        print(completed.stderr.rstrip(), file=output_stream)
    if completed.returncode != 0:
        raise CheckExecutionError(
            f"{export_type.upper()} export failed with exit code {completed.returncode}."
        )
    if not output_file.is_file() or output_file.stat().st_size == 0:
        raise CheckExecutionError(
            f"{export_type.upper()} export did not create a non-empty output file."
        )


def _run_board2pdf(
    command_prefix: list[str],
    input_file: Path,
    output_file: Path,
    *,
    runner: Runner,
    output_stream: TextIO,
) -> None:
    command = [*command_prefix, str(input_file), "--output", str(output_file)]
    try:
        completed = runner(command, capture_output=True, text=True, check=False)
    except OSError as exc:
        raise CheckExecutionError(f"Could not run Board2Pdf: {exc}") from exc

    if completed.stdout:
        print(completed.stdout.rstrip(), file=output_stream)
    if completed.stderr:
        print(completed.stderr.rstrip(), file=output_stream)
    if completed.returncode != 0:
        raise CheckExecutionError(
            f"Board2PDF export failed with exit code {completed.returncode}."
        )
    if not output_file.is_file() or output_file.stat().st_size == 0:
        raise CheckExecutionError(
            "Board2PDF export did not create a non-empty output file."
        )


def _run_interactive_html_bom(
    command_prefix: list[str],
    input_file: Path,
    output_file: Path,
    *,
    runner: Runner,
    output_stream: TextIO,
) -> None:
    command = [
        *command_prefix,
        str(input_file),
        "--no-browser",
        "--dest-dir",
        "DOC",
        "--name-format",
        output_file.stem,
    ]
    environment = os.environ.copy()
    environment["INTERACTIVE_HTML_BOM_NO_DISPLAY"] = "1"
    try:
        completed = runner(
            command,
            capture_output=True,
            text=True,
            check=False,
            env=environment,
        )
    except OSError as exc:
        raise CheckExecutionError(
            f"Could not run the Interactive HTML BOM plugin: {exc}"
        ) from exc

    if completed.stdout:
        print(completed.stdout.rstrip(), file=output_stream)
    if completed.stderr:
        print(completed.stderr.rstrip(), file=output_stream)
    if completed.returncode != 0:
        raise CheckExecutionError(
            "Interactive HTML BOM export failed with exit code "
            f"{completed.returncode}."
        )
    if not output_file.is_file() or output_file.stat().st_size == 0:
        raise CheckExecutionError(
            "Interactive HTML BOM export did not create a non-empty output file."
        )


def _run_ssconvert(
    command_prefix: list[str],
    input_file: Path,
    output_file: Path,
    *,
    runner: Runner,
    output_stream: TextIO,
) -> None:
    command = [
        *command_prefix,
        "--export-type=Gnumeric_Excel:excel_biff8",
        str(input_file),
        str(output_file),
    ]
    try:
        completed = runner(command, capture_output=True, text=True, check=False)
    except OSError as exc:
        raise CheckExecutionError(f"Could not run ssconvert: {exc}") from exc

    if completed.stdout:
        print(completed.stdout.rstrip(), file=output_stream)
    if completed.stderr:
        print(completed.stderr.rstrip(), file=output_stream)
    if completed.returncode != 0:
        raise CheckExecutionError(
            f"XLS conversion failed with exit code {completed.returncode}."
        )
    if not output_file.is_file() or output_file.stat().st_size == 0:
        raise CheckExecutionError(
            "XLS conversion did not create a non-empty output file."
        )


def _remove_known_path(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)


def _clean_previous_release_outputs(paths: ArtifactPaths) -> None:
    for path in (
        paths.output_directory,
        paths.bom_file.parent,
        paths.release_archive_file,
    ):
        if not path.exists() and not path.is_symlink():
            continue
        try:
            _remove_known_path(path)
            if path.exists() or path.is_symlink():
                raise CheckExecutionError(
                    f"Previous release output is not removable: {path}."
                )
        except OSError as exc:
            raise CheckExecutionError(
                f"Could not remove previous release output {path}: {exc}"
            ) from exc


def _restore_production_backup(
    production_directory: Path,
    production_backup_root: Path | None,
    production_backup: Path | None,
) -> None:
    if production_backup_root is None:
        return
    try:
        if production_directory.exists() or production_directory.is_symlink():
            _remove_known_path(production_directory)
        if production_backup is not None and production_backup.exists():
            shutil.move(str(production_backup), str(production_directory))
        if production_backup_root.exists():
            shutil.rmtree(production_backup_root)
    except OSError as exc:
        raise CheckExecutionError(
            f"Could not restore the original production directory: {exc}"
        ) from exc


def _run_fabrication_toolkit(
    command_prefix: list[str],
    input_file: Path,
    paths: ArtifactPaths,
    *,
    runner: Runner,
    output_stream: TextIO,
) -> None:
    production_backup_root: Path | None = None
    production_backup: Path | None = None
    temporary_output_directory: Path | None = None
    command = [
        *command_prefix,
        "--path",
        str(input_file),
        "--autoTranslate",
        "--autoFill",
        "--nonInteractive",
        "--noBackup",
    ]

    try:
        if paths.production_directory.is_symlink():
            raise CheckExecutionError(
                f"Fabrication Toolkit output directory must not be a symlink: "
                f"{paths.production_directory}."
            )
        if paths.production_directory.exists() and not paths.production_directory.is_dir():
            raise CheckExecutionError(
                f"Fabrication Toolkit output path is not a directory: "
                f"{paths.production_directory}."
            )
        if paths.production_directory.exists():
            production_backup_root = Path(
                tempfile.mkdtemp(
                    prefix=".idata_release_production_backup_",
                    dir=input_file.parent,
                )
            )
            production_backup = production_backup_root / "production"
            shutil.move(str(paths.production_directory), str(production_backup))

        try:
            completed = runner(command, capture_output=True, text=True, check=False)
        except OSError as exc:
            raise CheckExecutionError(
                f"Could not run the Fabrication Toolkit plugin: {exc}"
            ) from exc

        if completed.stdout:
            print(completed.stdout.rstrip(), file=output_stream)
        if completed.stderr:
            print(completed.stderr.rstrip(), file=output_stream)
        if completed.returncode != 0:
            raise CheckExecutionError(
                "Fabrication Toolkit failed with exit code "
                f"{completed.returncode}."
            )

        generated_archive = paths.production_directory / f"{input_file.stem}.zip"
        if not generated_archive.is_file():
            zip_candidates = sorted(paths.production_directory.glob("*.zip"))
            if len(zip_candidates) == 1:
                generated_archive = zip_candidates[0]
            else:
                found = ", ".join(path.name for path in zip_candidates) or "none"
                raise CheckExecutionError(
                    f"Fabrication Toolkit did not create the expected archive "
                    f"{paths.production_directory / (input_file.stem + '.zip')}; "
                    f"found: {found}."
                )
        generated_positions = paths.production_directory / "positions.csv"
        for generated_file, label in (
            (generated_archive, "Gerber archive"),
            (generated_positions, "positions file"),
        ):
            if not generated_file.is_file() or generated_file.stat().st_size == 0:
                raise CheckExecutionError(
                    f"Fabrication Toolkit did not create a non-empty {label}: "
                    f"{generated_file}."
                )

        paths.gerber_file.parent.mkdir(parents=True, exist_ok=True)
        temporary_output_directory = Path(
            tempfile.mkdtemp(
                prefix=".idata_release_production_output_",
                dir=paths.gerber_file.parent,
            )
        )
        temporary_gerber_file = temporary_output_directory / paths.gerber_file.name
        temporary_positions_file = temporary_output_directory / paths.positions_file.name
        shutil.copy2(generated_archive, temporary_gerber_file)
        shutil.copy2(generated_positions, temporary_positions_file)
        temporary_gerber_file.replace(paths.gerber_file)
        temporary_positions_file.replace(paths.positions_file)
        for copied_file, label in (
            (paths.gerber_file, "Gerber archive"),
            (paths.positions_file, "positions file"),
        ):
            if not copied_file.is_file() or copied_file.stat().st_size == 0:
                raise CheckExecutionError(
                    f"Could not verify copied {label}: {copied_file}."
                )

        shutil.rmtree(paths.production_directory)
        if production_backup_root is not None:
            shutil.rmtree(production_backup_root)
            production_backup_root = None
    except (CheckExecutionError, OSError) as exc:
        try:
            _restore_production_backup(
                paths.production_directory,
                production_backup_root,
                production_backup,
            )
        except CheckExecutionError as restore_error:
            raise CheckExecutionError(f"{exc} {restore_error}") from restore_error
        if isinstance(exc, CheckExecutionError):
            raise
        raise CheckExecutionError(
            f"Fabrication Toolkit file operation failed: {exc}"
        ) from exc
    finally:
        if temporary_output_directory is not None:
            shutil.rmtree(temporary_output_directory, ignore_errors=True)


def generate_artifacts(
    files: ProjectFiles,
    paths: ArtifactPaths,
    *,
    runner: Runner,
    board2pdf_command: list[str],
    interactive_html_bom_command: list[str],
    fabrication_toolkit_command: list[str],
    ssconvert_command: list[str],
    output_stream: TextIO,
) -> None:
    """Generate all release artifacts before replacing final artifacts."""

    bom_output_directory_existed = paths.bom_file.parent.exists()
    try:
        paths.output_directory.mkdir(parents=True, exist_ok=True)
        paths.bom_file.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise CheckExecutionError(
            f"Could not create release output directories: {exc}"
        ) from exc

    temporary_files: list[Path] = []
    release_completed = False
    try:
        temporary_outputs: list[tuple[Path, str, list[str]]] = []
        for suffix, export_type, options in (
            (".pdf", "pdf", []),
            (
                ".csv",
                "bom",
                [
                    "--preset",
                    BOM_PRESET_NAME,
                    "--format-preset",
                    "CSV",
                    "--exclude-dnp",
                ],
            ),
            (".pdf", "assembly", []),
        ):
            temporary_directory = (
                paths.bom_file.parent if export_type == "bom" else paths.output_directory
            )
            with tempfile.NamedTemporaryFile(
                mode="w",
                prefix=".idata_release_",
                suffix=suffix,
                dir=temporary_directory,
                delete=False,
            ) as temporary_file:
                temporary_path = Path(temporary_file.name)
            temporary_files.append(temporary_path)
            temporary_outputs.append((temporary_path, export_type, options))

        for temporary_path, export_type, options in temporary_outputs:
            if export_type == "assembly":
                _run_board2pdf(
                    board2pdf_command,
                    files.pcb_file,
                    temporary_path,
                    runner=runner,
                    output_stream=output_stream,
                )
            else:
                _run_export(
                    export_type,
                    files.schematic_file,
                    temporary_path,
                    options,
                    runner=runner,
                    output_stream=output_stream,
                )

        _run_interactive_html_bom(
            interactive_html_bom_command,
            files.pcb_file,
            paths.interactive_html_bom_file,
            runner=runner,
            output_stream=output_stream,
        )

        _run_fabrication_toolkit(
            fabrication_toolkit_command,
            files.pcb_file,
            paths,
            runner=runner,
            output_stream=output_stream,
        )

        try:
            temporary_files[0].replace(paths.plot_file)
            temporary_files[1].replace(paths.bom_file)
            temporary_files[2].replace(paths.assembly_file)
        except OSError as exc:
            raise CheckExecutionError(f"Could not install release artifacts: {exc}") from exc

        with tempfile.NamedTemporaryFile(
            mode="w",
            prefix=".idata_release_",
            suffix=".xls",
            dir=paths.bom_xls_file.parent,
            delete=False,
        ) as temporary_xls_file:
            temporary_xls_path = Path(temporary_xls_file.name)
        temporary_xls_path.unlink(missing_ok=True)
        temporary_files.append(temporary_xls_path)
        _run_ssconvert(
            ssconvert_command,
            paths.bom_file,
            temporary_xls_path,
            runner=runner,
            output_stream=output_stream,
        )
        try:
            temporary_xls_path.replace(paths.bom_xls_file)
        except OSError as exc:
            raise CheckExecutionError(f"Could not install XLS BOM artifact: {exc}") from exc
        release_completed = True
    finally:
        for temporary_file in temporary_files:
            try:
                temporary_file.unlink(missing_ok=True)
            except OSError:
                pass
        if not release_completed and not bom_output_directory_existed:
            try:
                paths.bom_file.parent.rmdir()
            except OSError:
                pass


def _create_release_archive(paths: ArtifactPaths) -> None:
    temporary_archive_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            prefix=".idata_release_",
            suffix=".zip",
            dir=paths.release_archive_file.parent,
            delete=False,
        ) as temporary_archive:
            temporary_archive_path = Path(temporary_archive.name)

        with zipfile.ZipFile(
            temporary_archive_path,
            mode="w",
            compression=zipfile.ZIP_DEFLATED,
        ) as archive:
            for output_directory in (paths.output_directory, paths.bom_file.parent):
                if not output_directory.is_dir():
                    raise CheckExecutionError(
                        f"Release output directory is missing: {output_directory}."
                    )
                for file_path in sorted(output_directory.rglob("*")):
                    if file_path.is_file() and not file_path.is_symlink():
                        archive.write(
                            file_path,
                            arcname=file_path.relative_to(paths.release_archive_file.parent),
                        )

        if (
            temporary_archive_path is None
            or not temporary_archive_path.is_file()
            or temporary_archive_path.stat().st_size == 0
        ):
            raise CheckExecutionError(
                "Release archive was not created as a non-empty file."
            )
        temporary_archive_path.replace(paths.release_archive_file)
    except CheckExecutionError:
        raise
    except (OSError, zipfile.LargeZipFile) as exc:
        raise CheckExecutionError(f"Could not create release archive: {exc}") from exc
    finally:
        if temporary_archive_path is not None:
            try:
                temporary_archive_path.unlink(missing_ok=True)
            except OSError:
                pass


def run_release(
    project_path: str | Path,
    *,
    input_stream: TextIO | None = None,
    output_stream: TextIO | None = None,
    runner: Runner = subprocess.run,
    board2pdf_finder: Board2PdfFinder = find_board2pdf_command,
    interactive_html_bom_finder: InteractiveHtmlBomFinder = find_interactive_html_bom_command,
    fabrication_toolkit_finder: FabricationToolkitFinder = find_fabrication_toolkit_command,
    ssconvert_finder: SsconvertFinder = find_ssconvert_command,
) -> int:
    """Run checks and release artifact generation, returning a process exit code."""

    input_stream = input_stream or sys.stdin
    output_stream = output_stream or sys.stdout

    try:
        files = find_project_files(project_path)
        paths = artifact_paths(files)
        _clean_previous_release_outputs(paths)
        erc_result = run_check(
            "ERC", files.schematic_file, runner=runner, output_stream=output_stream
        )
        if erc_result.has_violations and not _ask_to_continue(
            erc_result, input_stream=input_stream, output_stream=output_stream
        ):
            print("Release stopped.", file=output_stream)
            return 1
        if erc_result.has_excluded_violations and _ask_about_excluded_violations(
            erc_result, input_stream=input_stream, output_stream=output_stream
        ):
            print("Release stopped.", file=output_stream)
            return 1

        drc_result = run_check(
            "DRC", files.pcb_file, runner=runner, output_stream=output_stream
        )
        if drc_result.has_violations and not _ask_to_continue(
            drc_result, input_stream=input_stream, output_stream=output_stream
        ):
            print("Release stopped.", file=output_stream)
            return 1
        if drc_result.has_excluded_violations and _ask_about_excluded_violations(
            drc_result, input_stream=input_stream, output_stream=output_stream
        ):
            print("Release stopped.", file=output_stream)
            return 1

        validate_bom_preset(files.project_file)
        board2pdf_command = board2pdf_finder()
        interactive_html_bom_command = interactive_html_bom_finder()
        fabrication_toolkit_command = fabrication_toolkit_finder()
        ssconvert_command = ssconvert_finder()
    except (CheckExecutionError, ValueError) as exc:
        print(f"Error: {exc}", file=output_stream)
        return 2

    print("Starting release.", file=output_stream)
    try:
        generate_artifacts(
            files,
            paths,
            runner=runner,
            board2pdf_command=board2pdf_command,
            interactive_html_bom_command=interactive_html_bom_command,
            fabrication_toolkit_command=fabrication_toolkit_command,
            ssconvert_command=ssconvert_command,
            output_stream=output_stream,
        )
        _create_release_archive(paths)
    except CheckExecutionError as exc:
        print(f"Error: {exc}", file=output_stream)
        return 2

    print(
        f"Created {paths.plot_file}, {paths.bom_file}, {paths.bom_xls_file}, "
        f"{paths.assembly_file}, "
        f"{paths.interactive_html_bom_file}, {paths.gerber_file}, "
        f"{paths.positions_file}, and {paths.release_archive_file}.",
        file=output_stream,
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check a KiCad project before release.")
    parser.add_argument("project_directory", type=Path, help="Path to the KiCad project directory")
    args = parser.parse_args(argv)
    return run_release(args.project_directory)


if __name__ == "__main__":
    sys.exit(main())
