import io
import json
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

from idata_release import (
    CheckExecutionError,
    find_fabrication_toolkit_command,
    find_project_files,
    find_board2pdf_command,
    find_interactive_html_bom_command,
    find_ssconvert_command,
    parse_report,
    run_check,
    run_release,
    validate_bom_preset,
)


def make_project(directory: Path) -> Path:
    project_directory = directory / "Demo"
    project_directory.mkdir()
    for suffix in ("kicad_sch", "kicad_pcb"):
        (project_directory / f"Demo.{suffix}").touch()
    (project_directory / "Demo.kicad_pro").write_text(
        json.dumps(
            {
                "schematic": {
                    "bom_presets": [
                        {
                            "name": "IDATA_BOM_VIEW",
                            "fields_ordered": [
                                {"name": "Reference", "label": "Reference", "show": True},
                                {"name": "Value", "label": "Value", "show": True},
                                {"name": "Footprint", "label": "Footprint", "show": True},
                                {"name": "PART NUMBER", "label": "PART NUMBER", "show": True},
                                {"name": "Description", "label": "Description", "show": True},
                                {"name": "${QUANTITY}", "label": "Qty", "show": True},
                            ],
                        }
                    ]
                }
            }
        ),
        encoding="utf-8",
    )
    return project_directory


class FakeRunner:
    def __init__(
        self,
        report_by_command,
        returncode=0,
        export_returncode=0,
        board2pdf_returncode=0,
        interactive_html_bom_returncode=0,
        fabrication_toolkit_returncode=0,
        fabrication_toolkit_create_outputs=True,
        fabrication_toolkit_archive_name=None,
        ssconvert_returncode=0,
    ):
        self.report_by_command = report_by_command
        self.returncode = returncode
        self.export_returncode = export_returncode
        self.board2pdf_returncode = board2pdf_returncode
        self.interactive_html_bom_returncode = interactive_html_bom_returncode
        self.fabrication_toolkit_returncode = fabrication_toolkit_returncode
        self.fabrication_toolkit_create_outputs = fabrication_toolkit_create_outputs
        self.fabrication_toolkit_archive_name = fabrication_toolkit_archive_name
        self.ssconvert_returncode = ssconvert_returncode
        self.commands = []
        self.report_paths = []

    def __call__(self, command, *, capture_output, text, check, **kwargs):
        self.commands.append(command)
        if command and Path(command[0]).name == "ssconvert":
            if self.ssconvert_returncode == 0:
                output_path = Path(command[-1])
                output_path.write_text("generated xls artifact\n", encoding="utf-8")
            return subprocess.CompletedProcess(
                command, self.ssconvert_returncode, "ssconvert output\n", ""
            )
        if any(str(part).endswith("generate_interactive_bom.py") for part in command):
            if self.interactive_html_bom_returncode == 0:
                pcb_path = next(Path(part) for part in command if str(part).endswith(".kicad_pcb"))
                destination = Path(command[command.index("--dest-dir") + 1])
                if not destination.is_absolute():
                    destination = pcb_path.parent / destination
                name = command[command.index("--name-format") + 1]
                destination.mkdir(parents=True, exist_ok=True)
                (destination / f"{name}.html").write_text(
                    "generated interactive html bom\n", encoding="utf-8"
                )
            return subprocess.CompletedProcess(
                command, self.interactive_html_bom_returncode, "interactive html bom output\n", ""
            )
        if any(str(part).endswith("board2pdf-cli.py") for part in command):
            output_path = Path(command[command.index("--output") + 1])
            output_path.write_text("generated assembly artifact\n", encoding="utf-8")
            return subprocess.CompletedProcess(
                command, self.board2pdf_returncode, "board2pdf output\n", ""
            )
        if is_fabrication_toolkit_command(command):
            if self.fabrication_toolkit_returncode == 0 and self.fabrication_toolkit_create_outputs:
                pcb_path = Path(command[command.index("--path") + 1])
                production_directory = pcb_path.parent / "production"
                production_directory.mkdir(parents=True, exist_ok=True)
                project_name = pcb_path.stem
                archive_name = self.fabrication_toolkit_archive_name or f"{project_name}.zip"
                (production_directory / archive_name).write_text(
                    "generated gerber archive\n", encoding="utf-8"
                )
                (production_directory / "positions.csv").write_text(
                    "generated positions\n", encoding="utf-8"
                )
            return subprocess.CompletedProcess(
                command, self.fabrication_toolkit_returncode, "fabrication toolkit output\n", ""
            )
        if command[1:3] == ["sch", "export"]:
            output_path = Path(command[command.index("--output") + 1])
            output_path.write_text("generated artifact\n", encoding="utf-8")
            return subprocess.CompletedProcess(
                command, self.export_returncode, "export output\n", ""
            )

        report_path = Path(command[command.index("--output") + 1])
        self.report_paths.append(report_path)
        report = self.report_by_command[command[1]]
        report_path.write_text(json.dumps(report), encoding="utf-8")
        return subprocess.CompletedProcess(command, self.returncode, "check output\n", "")


def available_interactive_html_bom_finder():
    return [sys.executable, "/tmp/generate_interactive_bom.py"]


def is_interactive_html_bom_command(command):
    return any(str(part).endswith("generate_interactive_bom.py") for part in command)


def available_fabrication_toolkit_finder():
    return [
        sys.executable,
        "-c",
        "fabrication toolkit wrapper",
        "/tmp/com_github_bennymeg_JLC-Plugin-for-KiCad",
    ]


def available_ssconvert_finder():
    return ["ssconvert"]


def is_fabrication_toolkit_command(command):
    return any("com_github_bennymeg_JLC-Plugin-for-KiCad" in str(part) for part in command)


class ProjectFileTests(unittest.TestCase):
    def test_finds_root_files_and_allows_hierarchical_schematics(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            project_directory = make_project(Path(temporary_directory))
            child_schematic = project_directory / "Power.kicad_sch"
            child_schematic.touch()

            files = find_project_files(project_directory)

            self.assertEqual(files.project_file.name, "Demo.kicad_pro")
            self.assertEqual(files.schematic_file.name, "Demo.kicad_sch")
            self.assertEqual(files.pcb_file.name, "Demo.kicad_pcb")

    def test_rejects_multiple_project_or_pcb_candidates(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            project_directory = make_project(Path(temporary_directory))
            (project_directory / "Other.kicad_pro").touch()

            with self.assertRaises(ValueError):
                find_project_files(project_directory)


class Board2PdfDiscoveryTests(unittest.TestCase):
    def test_prefers_board2pdf_executable_on_path(self):
        command = find_board2pdf_command(
            executable_finder=lambda name: "/usr/local/bin/board2pdf",
            home_directory=Path("/tmp/no-board2pdf-home"),
        )

        self.assertEqual(command, ["/usr/local/bin/board2pdf"])

    def test_finds_installed_kicad_plugin_script(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            home_directory = Path(temporary_directory)
            plugin_path = (
                home_directory
                / ".local/share/kicad/9.0/3rdparty/plugins"
                / "com_gitlab_dennevi_Board2Pdf"
                / "board2pdf-cli.py"
            )
            plugin_path.parent.mkdir(parents=True)
            plugin_path.touch()

            command = find_board2pdf_command(
                executable_finder=lambda name: None,
                home_directory=home_directory,
            )

        self.assertEqual(command, [sys.executable, str(plugin_path)])

    def test_reports_installation_error_when_plugin_is_missing(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            with self.assertRaisesRegex(ValueError, "Board2Pdf.*install"):
                find_board2pdf_command(
                    executable_finder=lambda name: None,
                    home_directory=Path(temporary_directory),
                )


class InteractiveHtmlBomDiscoveryTests(unittest.TestCase):
    def test_finds_installed_kicad_plugin_script(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            home_directory = Path(temporary_directory)
            plugin_path = (
                home_directory
                / ".local/share/kicad/9.0/3rdparty/plugins"
                / "org_openscopeproject_InteractiveHtmlBom"
                / "generate_interactive_bom.py"
            )
            plugin_path.parent.mkdir(parents=True)
            plugin_path.touch()

            command = find_interactive_html_bom_command(
                home_directory=home_directory,
            )

        self.assertEqual(command, [sys.executable, str(plugin_path)])

    def test_reports_installation_error_when_plugin_is_missing(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            with self.assertRaisesRegex(ValueError, "Interactive HTML BOM.*install"):
                find_interactive_html_bom_command(
                    home_directory=Path(temporary_directory),
                )


class FabricationToolkitDiscoveryTests(unittest.TestCase):
    def test_finds_installed_kicad_plugin_script(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            home_directory = Path(temporary_directory)
            plugin_path = (
                home_directory
                / ".local/share/kicad/9.0/3rdparty/plugins"
                / "com_github_bennymeg_JLC-Plugin-for-KiCad"
                / "cli.py"
            )
            plugin_path.parent.mkdir(parents=True)
            plugin_path.touch()

            command = find_fabrication_toolkit_command(
                home_directory=home_directory,
            )

        self.assertEqual(command[0], sys.executable)
        self.assertEqual(command[1], "-c")
        self.assertIn(str(plugin_path.parent.parent), command)

    def test_reports_installation_error_when_plugin_is_missing(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            with self.assertRaisesRegex(ValueError, "Fabrication Toolkit.*install"):
                find_fabrication_toolkit_command(
                    home_directory=Path(temporary_directory),
                )


class SsconvertDiscoveryTests(unittest.TestCase):
    def test_finds_ssconvert_executable(self):
        command = find_ssconvert_command(
            executable_finder=lambda name: "/usr/bin/ssconvert"
        )

        self.assertEqual(command, ["/usr/bin/ssconvert"])

    def test_reports_gnumeric_installation_error_when_ssconvert_is_missing(self):
        with self.assertRaisesRegex(ValueError, "ssconvert.*gnumeric"):
            find_ssconvert_command(executable_finder=lambda name: None)


class ReportTests(unittest.TestCase):
    def test_parses_nested_erc_warning_and_error_counts(self):
        report = {
            "sheets": [
                {
                    "violations": [
                        {"severity": "warning", "description": "warning"},
                        {"severity": "error", "description": "error"},
                    ]
                },
                {"violations": []},
            ]
        }

        result = parse_report("ERC", report)

        self.assertEqual(result.warning_count, 1)
        self.assertEqual(result.error_count, 1)
        self.assertTrue(result.has_violations)

    def test_parses_drc_warning_and_error_counts(self):
        report = {
            "violations": [
                {"severity": "warning", "description": "warning"},
                {"severity": "error", "description": "error"},
            ]
        }

        result = parse_report("DRC", report)

        self.assertEqual(result.warning_count, 1)
        self.assertEqual(result.error_count, 1)
        self.assertTrue(result.has_violations)

    def test_keeps_excluded_violations_separate_from_active_violations(self):
        report = {
            "violations": [
                {"severity": "error", "description": "excluded", "excluded": True},
                {"severity": "warning", "description": "active"},
            ]
        }

        result = parse_report("DRC", report)

        self.assertEqual(result.error_count, 0)
        self.assertEqual(result.warning_count, 1)
        self.assertTrue(result.has_violations)
        self.assertTrue(result.has_excluded_violations)
        self.assertEqual(result.excluded_error_count, 1)


class BomPresetTests(unittest.TestCase):
    def test_validates_required_idata_bom_view_fields(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            project_directory = make_project(Path(temporary_directory))

            preset = validate_bom_preset(project_directory / "Demo.kicad_pro")

        self.assertEqual(preset, "IDATA_BOM_VIEW")

    def test_rejects_missing_bom_preset_with_required_fields_in_error(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            project_directory = make_project(Path(temporary_directory))
            (project_directory / "Demo.kicad_pro").write_text(
                json.dumps({"schematic": {"bom_presets": []}}), encoding="utf-8"
            )

            with self.assertRaisesRegex(ValueError, "IDATA_BOM_VIEW.*Reference.*Qty"):
                validate_bom_preset(project_directory / "Demo.kicad_pro")

    def test_rejects_missing_required_bom_field(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            project_directory = make_project(Path(temporary_directory))
            project = json.loads(
                (project_directory / "Demo.kicad_pro").read_text(encoding="utf-8")
            )
            project["schematic"]["bom_presets"][0]["fields_ordered"] = project[
                "schematic"
            ]["bom_presets"][0][
                "fields_ordered"
            ][:-1]
            (project_directory / "Demo.kicad_pro").write_text(
                json.dumps(project), encoding="utf-8"
            )

            with self.assertRaisesRegex(ValueError, "Qty"):
                validate_bom_preset(project_directory / "Demo.kicad_pro")


class CheckExecutionTests(unittest.TestCase):
    def test_runs_erc_with_json_report_and_cleans_up_report(self):
        runner = FakeRunner(
            {
                "sch": {
                    "sheets": [{"violations": [{"severity": "warning"}]}]
                }
            }
        )
        with tempfile.TemporaryDirectory() as temporary_directory:
            schematic = Path(temporary_directory) / "Demo.kicad_sch"
            schematic.touch()
            output = io.StringIO()

            result = run_check("ERC", schematic, runner=runner, output_stream=output)

        self.assertEqual(result.warning_count, 1)
        self.assertEqual(runner.commands[0][0], "kicad-cli")
        self.assertEqual(runner.commands[0][1:3], ["sch", "erc"])
        self.assertIn("--format", runner.commands[0])
        self.assertIn("json", runner.commands[0])
        self.assertIn("check output", output.getvalue())
        self.assertFalse(runner.report_paths[0].exists())

    def test_fails_when_kicad_cli_returns_an_error(self):
        runner = FakeRunner({"sch": {"sheets": []}}, returncode=2)
        with tempfile.TemporaryDirectory() as temporary_directory:
            schematic = Path(temporary_directory) / "Demo.kicad_sch"
            schematic.touch()

            with self.assertRaises(CheckExecutionError):
                run_check("ERC", schematic, runner=runner)


class ReleaseFlowTests(unittest.TestCase):
    def test_stops_after_erc_violations_when_user_declines(self):
        runner = FakeRunner(
            {
                "sch": {"sheets": [{"violations": [{"severity": "error"}]}]},
                "pcb": {"violations": []},
            }
        )
        with tempfile.TemporaryDirectory() as temporary_directory:
            project_directory = make_project(Path(temporary_directory))
            output = io.StringIO()

            exit_code = run_release(
                project_directory,
                input_stream=io.StringIO("n\n"),
                output_stream=output,
                runner=runner,
                interactive_html_bom_finder=available_interactive_html_bom_finder,
                fabrication_toolkit_finder=available_fabrication_toolkit_finder,
                ssconvert_finder=available_ssconvert_finder,
            )

        self.assertNotEqual(exit_code, 0)
        self.assertEqual(len(runner.commands), 1)
        self.assertNotIn("Starting release.", output.getvalue())

    def test_runs_drc_and_starts_release_after_clean_checks(self):
        runner = FakeRunner(
            {
                "sch": {"sheets": [{"violations": []}]},
                "pcb": {"violations": []},
            }
        )
        with tempfile.TemporaryDirectory() as temporary_directory:
            project_directory = make_project(Path(temporary_directory))
            output = io.StringIO()

            exit_code = run_release(
                project_directory,
                input_stream=io.StringIO(),
                output_stream=output,
                runner=runner,
                interactive_html_bom_finder=available_interactive_html_bom_finder,
                fabrication_toolkit_finder=available_fabrication_toolkit_finder,
                ssconvert_finder=available_ssconvert_finder,
            )

        check_commands = [
            command
            for command in runner.commands
            if command[1:3] != ["sch", "export"]
            and not any(str(part).endswith("board2pdf-cli.py") for part in command)
            and not is_interactive_html_bom_command(command)
            and not is_fabrication_toolkit_command(command)
            and Path(command[0]).name != "ssconvert"
        ]
        self.assertEqual(exit_code, 0)
        self.assertEqual([command[1] for command in check_commands], ["sch", "pcb"])
        self.assertIn("Starting release.", output.getvalue())

    def test_continues_after_warning_when_user_confirms(self):
        runner = FakeRunner(
            {
                "sch": {"sheets": [{"violations": [{"severity": "warning"}]}]},
                "pcb": {"violations": []},
            }
        )
        with tempfile.TemporaryDirectory() as temporary_directory:
            project_directory = make_project(Path(temporary_directory))
            output = io.StringIO()

            exit_code = run_release(
                project_directory,
                input_stream=io.StringIO("yes\n"),
                output_stream=output,
                runner=runner,
                interactive_html_bom_finder=available_interactive_html_bom_finder,
                fabrication_toolkit_finder=available_fabrication_toolkit_finder,
                ssconvert_finder=available_ssconvert_finder,
            )

        check_commands = [
            command
            for command in runner.commands
            if command[1:3] != ["sch", "export"]
            and not any(str(part).endswith("board2pdf-cli.py") for part in command)
            and not is_interactive_html_bom_command(command)
            and not is_fabrication_toolkit_command(command)
            and Path(command[0]).name != "ssconvert"
        ]
        self.assertEqual(exit_code, 0)
        self.assertEqual(len(check_commands), 2)
        self.assertIn("Starting release.", output.getvalue())

    def test_stops_when_user_says_excluded_violations_are_a_problem(self):
        runner = FakeRunner(
            {
                "sch": {"sheets": []},
                "pcb": {
                    "violations": [
                        {"severity": "error", "excluded": True, "description": "known"}
                    ]
                },
            }
        )
        with tempfile.TemporaryDirectory() as temporary_directory:
            project_directory = make_project(Path(temporary_directory))
            output = io.StringIO()

            exit_code = run_release(
                project_directory,
                input_stream=io.StringIO("yes\n"),
                output_stream=output,
                runner=runner,
                interactive_html_bom_finder=available_interactive_html_bom_finder,
                fabrication_toolkit_finder=available_fabrication_toolkit_finder,
                ssconvert_finder=available_ssconvert_finder,
            )

        self.assertNotEqual(exit_code, 0)
        self.assertNotIn("Starting release.", output.getvalue())
        self.assertIn("excluded violations", output.getvalue())
        self.assertIn("[Y/n]", output.getvalue())

    def test_stops_when_user_accepts_default_for_excluded_violations(self):
        runner = FakeRunner(
            {
                "sch": {"sheets": []},
                "pcb": {
                    "violations": [
                        {"severity": "warning", "excluded": True, "description": "known"}
                    ]
                },
            }
        )
        with tempfile.TemporaryDirectory() as temporary_directory:
            project_directory = make_project(Path(temporary_directory))
            output = io.StringIO()

            exit_code = run_release(
                project_directory,
                input_stream=io.StringIO("\n"),
                output_stream=output,
                runner=runner,
                interactive_html_bom_finder=available_interactive_html_bom_finder,
                fabrication_toolkit_finder=available_fabrication_toolkit_finder,
                ssconvert_finder=available_ssconvert_finder,
            )

        self.assertNotEqual(exit_code, 0)
        self.assertNotIn("Starting release.", output.getvalue())

    def test_continues_when_user_says_excluded_violations_are_not_a_problem(self):
        runner = FakeRunner(
            {
                "sch": {"sheets": [{"violations": []}]},
                "pcb": {
                    "violations": [
                        {"severity": "error", "excluded": True, "description": "known"}
                    ]
                },
            }
        )
        with tempfile.TemporaryDirectory() as temporary_directory:
            project_directory = make_project(Path(temporary_directory))
            output = io.StringIO()

            exit_code = run_release(
                project_directory,
                input_stream=io.StringIO("no\n"),
                output_stream=output,
                runner=runner,
                interactive_html_bom_finder=available_interactive_html_bom_finder,
                fabrication_toolkit_finder=available_fabrication_toolkit_finder,
                ssconvert_finder=available_ssconvert_finder,
            )

        self.assertEqual(exit_code, 0)
        self.assertIn("Starting release.", output.getvalue())

    def test_generates_pdf_and_bom_using_project_preset(self):
        runner = FakeRunner(
            {
                "sch": {"sheets": []},
                "pcb": {"violations": []},
            }
        )
        with tempfile.TemporaryDirectory() as temporary_directory:
            project_directory = make_project(Path(temporary_directory))
            output = io.StringIO()

            exit_code = run_release(
                project_directory,
                input_stream=io.StringIO(),
                output_stream=output,
                runner=runner,
                interactive_html_bom_finder=available_interactive_html_bom_finder,
                fabrication_toolkit_finder=available_fabrication_toolkit_finder,
                ssconvert_finder=available_ssconvert_finder,
            )

            doc_directory = project_directory / "DOC"
            export_commands = [
                command for command in runner.commands if command[1:3] == ["sch", "export"]
            ]
            board2pdf_commands = [
                command
                for command in runner.commands
                if any(str(part).endswith("board2pdf-cli.py") for part in command)
            ]
            interactive_html_bom_commands = [
                command for command in runner.commands if is_interactive_html_bom_command(command)
            ]
            fabrication_toolkit_commands = [
                command for command in runner.commands if is_fabrication_toolkit_command(command)
            ]
            ssconvert_commands = [
                command
                for command in runner.commands
                if Path(command[0]).name == "ssconvert"
            ]

            self.assertTrue((doc_directory / "Demo.pdf").is_file())
            self.assertTrue((doc_directory / "Demo_Assembly.pdf").is_file())
            self.assertTrue((doc_directory / "Demo.html").is_file())
            self.assertTrue((doc_directory / "Demo_BOM.xls").is_file())
            production_directory = project_directory / "PRODUCTION"
            self.assertTrue((production_directory / "Demo_BOM.csv").is_file())
            self.assertTrue((production_directory / "Demo_Gerber.zip").is_file())
            self.assertTrue((production_directory / "Demo_Positions.csv").is_file())
            self.assertFalse((project_directory / "production").exists())
            archive_file = project_directory / "Demo.zip"
            self.assertTrue(archive_file.is_file())
            with zipfile.ZipFile(archive_file) as archive:
                self.assertEqual(
                    set(archive.namelist()),
                    {
                        "DOC/Demo.pdf",
                        "DOC/Demo_Assembly.pdf",
                        "DOC/Demo.html",
                        "DOC/Demo_BOM.xls",
                        "PRODUCTION/Demo_BOM.csv",
                        "PRODUCTION/Demo_Gerber.zip",
                        "PRODUCTION/Demo_Positions.csv",
                    },
                )

        self.assertEqual(exit_code, 0)
        self.assertEqual([command[3] for command in export_commands], ["pdf", "bom"])
        bom_command = export_commands[1]
        self.assertIn("--preset", bom_command)
        self.assertIn("IDATA_BOM_VIEW", bom_command)
        self.assertIn("--exclude-dnp", bom_command)
        self.assertNotIn("--include-excluded-from-bom", bom_command)
        self.assertEqual(
            Path(bom_command[bom_command.index("--output") + 1]).parent,
            production_directory,
        )
        self.assertEqual(len(board2pdf_commands), 1)
        self.assertTrue(
            any(str(part).endswith("Demo.kicad_pcb") for part in board2pdf_commands[0])
        )
        self.assertTrue(
            board2pdf_commands[0][board2pdf_commands[0].index("--output") + 1].endswith(
                ".pdf"
            )
        )
        self.assertEqual(len(interactive_html_bom_commands), 1)
        interactive_command = interactive_html_bom_commands[0]
        self.assertIn("--no-browser", interactive_command)
        self.assertEqual(
            interactive_command[interactive_command.index("--dest-dir") + 1], "DOC"
        )
        self.assertEqual(
            interactive_command[interactive_command.index("--name-format") + 1], "Demo"
        )
        self.assertIn("Starting release.", output.getvalue())
        self.assertIn("Demo_Assembly.pdf", output.getvalue())
        self.assertIn("Demo.html", output.getvalue())
        self.assertIn("Demo_BOM.xls", output.getvalue())
        self.assertIn("Demo_Gerber.zip", output.getvalue())
        self.assertIn("Demo_Positions.csv", output.getvalue())
        self.assertEqual(len(fabrication_toolkit_commands), 1)
        fabrication_command = fabrication_toolkit_commands[0]
        self.assertIn("--autoTranslate", fabrication_command)
        self.assertIn("--autoFill", fabrication_command)
        self.assertIn("--nonInteractive", fabrication_command)
        self.assertIn("--noBackup", fabrication_command)
        self.assertNotIn("--archiveName", fabrication_command)
        self.assertEqual(len(ssconvert_commands), 1)
        ssconvert_command = ssconvert_commands[0]
        self.assertIn("--export-type=Gnumeric_Excel:excel_biff8", ssconvert_command)
        self.assertEqual(
            Path(ssconvert_command[-2]), production_directory / "Demo_BOM.csv"
        )
        self.assertEqual(Path(ssconvert_command[-1]).parent, doc_directory)
        self.assertEqual(Path(ssconvert_command[-1]).suffix, ".xls")

    def test_stops_before_starting_release_when_ssconvert_is_missing(self):
        runner = FakeRunner(
            {
                "sch": {"sheets": []},
                "pcb": {"violations": []},
            }
        )

        def missing_ssconvert():
            raise ValueError(
                "ssconvert was not found. Please install the gnumeric package."
            )

        with tempfile.TemporaryDirectory() as temporary_directory:
            project_directory = make_project(Path(temporary_directory))
            output = io.StringIO()

            exit_code = run_release(
                project_directory,
                input_stream=io.StringIO(),
                output_stream=output,
                runner=runner,
                interactive_html_bom_finder=available_interactive_html_bom_finder,
                fabrication_toolkit_finder=available_fabrication_toolkit_finder,
                ssconvert_finder=missing_ssconvert,
            )

        self.assertEqual(exit_code, 2)
        self.assertNotIn("Starting release.", output.getvalue())
        self.assertIn("Please install the gnumeric package", output.getvalue())

    def test_stops_before_starting_release_when_interactive_html_bom_is_missing(self):
        runner = FakeRunner(
            {
                "sch": {"sheets": []},
                "pcb": {"violations": []},
            }
        )

        def missing_interactive_html_bom():
            raise ValueError(
                "Interactive HTML BOM plugin was not found. Please install the "
                "InteractiveHtmlBom plugin."
            )

        with tempfile.TemporaryDirectory() as temporary_directory:
            project_directory = make_project(Path(temporary_directory))
            output = io.StringIO()

            exit_code = run_release(
                project_directory,
                input_stream=io.StringIO(),
                output_stream=output,
                runner=runner,
                interactive_html_bom_finder=missing_interactive_html_bom,
                fabrication_toolkit_finder=available_fabrication_toolkit_finder,
                ssconvert_finder=available_ssconvert_finder,
            )

        self.assertEqual(exit_code, 2)
        self.assertNotIn("Starting release.", output.getvalue())
        self.assertIn("Please install the InteractiveHtmlBom plugin", output.getvalue())

    def test_stops_with_error_when_interactive_html_bom_generation_fails(self):
        runner = FakeRunner(
            {
                "sch": {"sheets": []},
                "pcb": {"violations": []},
            },
            interactive_html_bom_returncode=1,
        )
        with tempfile.TemporaryDirectory() as temporary_directory:
            project_directory = make_project(Path(temporary_directory))
            output = io.StringIO()

            exit_code = run_release(
                project_directory,
                input_stream=io.StringIO(),
                output_stream=output,
                runner=runner,
                interactive_html_bom_finder=available_interactive_html_bom_finder,
                fabrication_toolkit_finder=available_fabrication_toolkit_finder,
                ssconvert_finder=available_ssconvert_finder,
            )

            doc_directory = project_directory / "DOC"
            production_directory = project_directory / "PRODUCTION"
            self.assertFalse((doc_directory / "Demo.pdf").exists())
            self.assertFalse((production_directory / "Demo_BOM.csv").exists())
            self.assertFalse((doc_directory / "Demo_Assembly.pdf").exists())
            self.assertFalse((doc_directory / "Demo.html").exists())

        self.assertEqual(exit_code, 2)
        self.assertIn("Starting release.", output.getvalue())
        self.assertIn("Interactive HTML BOM export failed", output.getvalue())

    def test_stops_before_starting_release_when_fabrication_toolkit_is_missing(self):
        runner = FakeRunner(
            {
                "sch": {"sheets": []},
                "pcb": {"violations": []},
            }
        )

        def missing_fabrication_toolkit():
            raise ValueError(
                "Fabrication Toolkit plugin was not found. Please install the "
                "Fabrication Toolkit plugin."
            )

        with tempfile.TemporaryDirectory() as temporary_directory:
            project_directory = make_project(Path(temporary_directory))
            output = io.StringIO()

            exit_code = run_release(
                project_directory,
                input_stream=io.StringIO(),
                output_stream=output,
                runner=runner,
                interactive_html_bom_finder=available_interactive_html_bom_finder,
                fabrication_toolkit_finder=missing_fabrication_toolkit,
            )

        self.assertEqual(exit_code, 2)
        self.assertNotIn("Starting release.", output.getvalue())
        self.assertIn("Please install the Fabrication Toolkit plugin", output.getvalue())

    def test_copies_fabrication_outputs_and_removes_lowercase_production(self):
        runner = FakeRunner(
            {
                "sch": {"sheets": []},
                "pcb": {"violations": []},
            }
        )
        with tempfile.TemporaryDirectory() as temporary_directory:
            project_directory = make_project(Path(temporary_directory))
            output = io.StringIO()

            exit_code = run_release(
                project_directory,
                input_stream=io.StringIO(),
                output_stream=output,
                runner=runner,
                interactive_html_bom_finder=available_interactive_html_bom_finder,
                fabrication_toolkit_finder=available_fabrication_toolkit_finder,
                ssconvert_finder=available_ssconvert_finder,
            )

            production_directory = project_directory / "PRODUCTION"
            self.assertEqual(
                (production_directory / "Demo_Gerber.zip").read_text(encoding="utf-8"),
                "generated gerber archive\n",
            )
            self.assertEqual(
                (production_directory / "Demo_Positions.csv").read_text(encoding="utf-8"),
                "generated positions\n",
            )
            self.assertFalse((project_directory / "production").exists())

        self.assertEqual(exit_code, 0)
        self.assertIn("Demo_Gerber.zip", output.getvalue())
        self.assertIn("Demo_Positions.csv", output.getvalue())

    def test_stops_with_error_and_keeps_production_when_fabrication_output_is_missing(self):
        runner = FakeRunner(
            {
                "sch": {"sheets": []},
                "pcb": {"violations": []},
            },
            fabrication_toolkit_create_outputs=False,
        )
        with tempfile.TemporaryDirectory() as temporary_directory:
            project_directory = make_project(Path(temporary_directory))
            output = io.StringIO()

            exit_code = run_release(
                project_directory,
                input_stream=io.StringIO(),
                output_stream=output,
                runner=runner,
                interactive_html_bom_finder=available_interactive_html_bom_finder,
                fabrication_toolkit_finder=available_fabrication_toolkit_finder,
                ssconvert_finder=available_ssconvert_finder,
            )

            self.assertTrue((project_directory / "DOC").exists())
            self.assertFalse((project_directory / "PRODUCTION").exists())
            self.assertFalse((project_directory / "production").exists())

        self.assertEqual(exit_code, 2)
        self.assertIn("Fabrication Toolkit did not create", output.getvalue())

    def test_does_not_release_stale_production_outputs_after_toolkit_failure(self):
        runner = FakeRunner(
            {
                "sch": {"sheets": []},
                "pcb": {"violations": []},
            },
            fabrication_toolkit_create_outputs=False,
        )
        with tempfile.TemporaryDirectory() as temporary_directory:
            project_directory = make_project(Path(temporary_directory))
            stale_production = project_directory / "production"
            stale_production.mkdir()
            (stale_production / "Demo.zip").write_text("stale archive\n", encoding="utf-8")
            (stale_production / "positions.csv").write_text("stale positions\n", encoding="utf-8")
            output = io.StringIO()

            exit_code = run_release(
                project_directory,
                input_stream=io.StringIO(),
                output_stream=output,
                runner=runner,
                interactive_html_bom_finder=available_interactive_html_bom_finder,
                fabrication_toolkit_finder=available_fabrication_toolkit_finder,
                ssconvert_finder=available_ssconvert_finder,
            )

            self.assertEqual(exit_code, 2)
            self.assertEqual(
                (stale_production / "Demo.zip").read_text(encoding="utf-8"),
                "stale archive\n",
            )
            self.assertEqual(
                (stale_production / "positions.csv").read_text(encoding="utf-8"),
                "stale positions\n",
            )
            self.assertFalse((project_directory / "PRODUCTION").exists())

    def test_uses_the_only_fresh_zip_when_plugin_default_name_is_not_project_name(self):
        runner = FakeRunner(
            {
                "sch": {"sheets": []},
                "pcb": {"violations": []},
            },
            fabrication_toolkit_archive_name="BoardTitle_Rev3.zip",
        )
        with tempfile.TemporaryDirectory() as temporary_directory:
            project_directory = make_project(Path(temporary_directory))
            output = io.StringIO()

            exit_code = run_release(
                project_directory,
                input_stream=io.StringIO(),
                output_stream=output,
                runner=runner,
                interactive_html_bom_finder=available_interactive_html_bom_finder,
                fabrication_toolkit_finder=available_fabrication_toolkit_finder,
                ssconvert_finder=available_ssconvert_finder,
            )

            self.assertEqual(
                (project_directory / "PRODUCTION" / "Demo_Gerber.zip").read_text(
                    encoding="utf-8"
                ),
                "generated gerber archive\n",
            )
            self.assertFalse((project_directory / "production").exists())

        self.assertEqual(exit_code, 0)

    def test_stops_with_error_when_fabrication_toolkit_fails(self):
        runner = FakeRunner(
            {
                "sch": {"sheets": []},
                "pcb": {"violations": []},
            },
            fabrication_toolkit_returncode=1,
        )
        with tempfile.TemporaryDirectory() as temporary_directory:
            project_directory = make_project(Path(temporary_directory))
            output = io.StringIO()

            exit_code = run_release(
                project_directory,
                input_stream=io.StringIO(),
                output_stream=output,
                runner=runner,
                interactive_html_bom_finder=available_interactive_html_bom_finder,
                fabrication_toolkit_finder=available_fabrication_toolkit_finder,
                ssconvert_finder=available_ssconvert_finder,
            )

        self.assertEqual(exit_code, 2)
        self.assertIn("Fabrication Toolkit failed", output.getvalue())

    def test_stops_before_starting_release_when_board2pdf_is_missing(self):
        runner = FakeRunner(
            {
                "sch": {"sheets": []},
                "pcb": {"violations": []},
            }
        )

        def missing_board2pdf():
            raise ValueError(
                "Board2Pdf plugin was not found. Please install the Board2Pdf plugin."
            )

        with tempfile.TemporaryDirectory() as temporary_directory:
            project_directory = make_project(Path(temporary_directory))
            output = io.StringIO()

            exit_code = run_release(
                project_directory,
                input_stream=io.StringIO(),
                output_stream=output,
                runner=runner,
                board2pdf_finder=missing_board2pdf,
                interactive_html_bom_finder=available_interactive_html_bom_finder,
                fabrication_toolkit_finder=available_fabrication_toolkit_finder,
                ssconvert_finder=available_ssconvert_finder,
            )

        self.assertEqual(exit_code, 2)
        self.assertNotIn("Starting release.", output.getvalue())
        self.assertIn("Please install the Board2Pdf plugin", output.getvalue())

    def test_stops_with_error_when_board2pdf_export_fails(self):
        runner = FakeRunner(
            {
                "sch": {"sheets": []},
                "pcb": {"violations": []},
            },
            board2pdf_returncode=1,
        )
        with tempfile.TemporaryDirectory() as temporary_directory:
            project_directory = make_project(Path(temporary_directory))
            output = io.StringIO()

            exit_code = run_release(
                project_directory,
                input_stream=io.StringIO(),
                output_stream=output,
                runner=runner,
                interactive_html_bom_finder=available_interactive_html_bom_finder,
                fabrication_toolkit_finder=available_fabrication_toolkit_finder,
                ssconvert_finder=available_ssconvert_finder,
            )

            doc_directory = project_directory / "DOC"
            self.assertFalse((doc_directory / "Demo.pdf").exists())
            self.assertFalse((doc_directory / "Demo_BOM.csv").exists())
            self.assertFalse((doc_directory / "Demo_Assembly.pdf").exists())

        self.assertEqual(exit_code, 2)
        self.assertIn("Starting release.", output.getvalue())
        self.assertIn("Board2PDF export failed", output.getvalue())

    def test_stops_before_starting_release_when_bom_preset_is_missing(self):
        runner = FakeRunner(
            {
                "sch": {"sheets": []},
                "pcb": {"violations": []},
            }
        )
        with tempfile.TemporaryDirectory() as temporary_directory:
            project_directory = make_project(Path(temporary_directory))
            (project_directory / "Demo.kicad_pro").write_text(
                json.dumps({"schematic": {"bom_presets": []}}), encoding="utf-8"
            )
            output = io.StringIO()

            exit_code = run_release(
                project_directory,
                input_stream=io.StringIO(),
                output_stream=output,
                runner=runner,
                interactive_html_bom_finder=available_interactive_html_bom_finder,
                fabrication_toolkit_finder=available_fabrication_toolkit_finder,
                ssconvert_finder=available_ssconvert_finder,
            )

        self.assertEqual(exit_code, 2)
        self.assertNotIn("Starting release.", output.getvalue())
        self.assertIn("IDATA_BOM_VIEW", output.getvalue())
        self.assertEqual(len([c for c in runner.commands if c[1:3] == ["sch", "export"]]), 0)

    def test_rebuilds_existing_artifacts_after_cleanup(self):
        runner = FakeRunner(
            {
                "sch": {"sheets": []},
                "pcb": {"violations": []},
            }
        )
        with tempfile.TemporaryDirectory() as temporary_directory:
            project_directory = make_project(Path(temporary_directory))
            doc_directory = project_directory / "DOC"
            production_directory = project_directory / "PRODUCTION"
            doc_directory.mkdir()
            production_directory.mkdir()
            (doc_directory / "Demo.pdf").write_text("old pdf", encoding="utf-8")
            (doc_directory / "Demo_BOM.xls").write_text("old xls", encoding="utf-8")
            (production_directory / "Demo_BOM.csv").write_text("old csv", encoding="utf-8")
            (doc_directory / "Demo_Assembly.pdf").write_text("old assembly", encoding="utf-8")
            (doc_directory / "Demo.html").write_text("old html", encoding="utf-8")

            exit_code = run_release(
                project_directory,
                input_stream=io.StringIO("\n"),
                output_stream=io.StringIO(),
                runner=runner,
                interactive_html_bom_finder=available_interactive_html_bom_finder,
                fabrication_toolkit_finder=available_fabrication_toolkit_finder,
                ssconvert_finder=available_ssconvert_finder,
            )

            self.assertEqual((doc_directory / "Demo.pdf").read_text(encoding="utf-8"), "generated artifact\n")
            self.assertEqual((doc_directory / "Demo_BOM.xls").read_text(encoding="utf-8"), "generated xls artifact\n")
            self.assertEqual((production_directory / "Demo_BOM.csv").read_text(encoding="utf-8"), "generated artifact\n")
            self.assertEqual((doc_directory / "Demo_Assembly.pdf").read_text(encoding="utf-8"), "generated assembly artifact\n")
            self.assertEqual((doc_directory / "Demo.html").read_text(encoding="utf-8"), "generated interactive html bom\n")

        self.assertEqual(exit_code, 0)

    def test_cleans_previous_release_outputs_before_checks(self):
        runner = FakeRunner(
            {
                "sch": {"sheets": [{"violations": [{"severity": "error"}]}]},
                "pcb": {"violations": []},
            }
        )
        with tempfile.TemporaryDirectory() as temporary_directory:
            project_directory = make_project(Path(temporary_directory))
            doc_directory = project_directory / "DOC"
            production_directory = project_directory / "PRODUCTION"
            doc_directory.mkdir()
            production_directory.mkdir()
            (doc_directory / "Demo.pdf").write_text("old pdf", encoding="utf-8")
            (production_directory / "Demo_BOM.csv").write_text("old csv", encoding="utf-8")
            (project_directory / "Demo.zip").write_text("old archive", encoding="utf-8")

            exit_code = run_release(
                project_directory,
                input_stream=io.StringIO("n\n"),
                output_stream=io.StringIO(),
                runner=runner,
                interactive_html_bom_finder=available_interactive_html_bom_finder,
                fabrication_toolkit_finder=available_fabrication_toolkit_finder,
                ssconvert_finder=available_ssconvert_finder,
            )

            self.assertFalse(doc_directory.exists())
            self.assertFalse(production_directory.exists())
            self.assertFalse((project_directory / "Demo.zip").exists())

        self.assertEqual(exit_code, 1)

    def test_stops_with_error_when_xls_conversion_fails(self):
        runner = FakeRunner(
            {
                "sch": {"sheets": []},
                "pcb": {"violations": []},
            },
            ssconvert_returncode=1,
        )
        with tempfile.TemporaryDirectory() as temporary_directory:
            project_directory = make_project(Path(temporary_directory))
            output = io.StringIO()

            exit_code = run_release(
                project_directory,
                input_stream=io.StringIO(),
                output_stream=output,
                runner=runner,
                interactive_html_bom_finder=available_interactive_html_bom_finder,
                fabrication_toolkit_finder=available_fabrication_toolkit_finder,
                ssconvert_finder=available_ssconvert_finder,
            )

            self.assertFalse((project_directory / "DOC" / "Demo_BOM.xls").exists())

        self.assertEqual(exit_code, 2)
        self.assertIn("XLS conversion failed", output.getvalue())

    def test_stops_with_error_when_artifact_export_fails(self):
        runner = FakeRunner(
            {
                "sch": {"sheets": []},
                "pcb": {"violations": []},
            },
            export_returncode=1,
        )
        with tempfile.TemporaryDirectory() as temporary_directory:
            project_directory = make_project(Path(temporary_directory))
            output = io.StringIO()

            exit_code = run_release(
                project_directory,
                input_stream=io.StringIO(),
                output_stream=output,
                runner=runner,
                interactive_html_bom_finder=available_interactive_html_bom_finder,
                fabrication_toolkit_finder=available_fabrication_toolkit_finder,
                ssconvert_finder=available_ssconvert_finder,
            )

            self.assertFalse((project_directory / "DOC" / "Demo.pdf").exists())
            self.assertFalse((project_directory / "PRODUCTION" / "Demo_BOM.csv").exists())
            self.assertFalse((project_directory / "DOC" / "Demo_Assembly.pdf").exists())

        self.assertEqual(exit_code, 2)
        self.assertIn("Starting release.", output.getvalue())


if __name__ == "__main__":
    unittest.main()
