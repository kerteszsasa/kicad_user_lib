import importlib.util
import shlex
import unittest
from pathlib import Path


PLUGIN_PATH = Path(
    "/home/kerteszs/hw/kicad_user_lib/external_plugins/idata_release/"
    "idata_release_plugin.py"
)
SPEC = importlib.util.spec_from_file_location("idata_release_plugin", PLUGIN_PATH)
assert SPEC is not None and SPEC.loader is not None
PLUGIN = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PLUGIN)


class TerminalCommandTests(unittest.TestCase):
    def test_prefers_gnome_terminal(self):
        command = PLUGIN.find_terminal_command(
            executable_finder=lambda name: {
                "x-terminal-emulator": "/usr/bin/x-terminal-emulator",
                "gnome-terminal": "/usr/bin/gnome-terminal"
            }.get(name)
        )

        self.assertEqual(command, ["/usr/bin/gnome-terminal", "--"])

    def test_falls_back_to_system_x_terminal_emulator(self):
        command = PLUGIN.find_terminal_command(
            executable_finder=lambda name: {
                "x-terminal-emulator": "/usr/bin/x-terminal-emulator"
            }.get(name)
        )

        self.assertEqual(command, ["/usr/bin/x-terminal-emulator", "-x"])

    def test_reports_error_when_no_terminal_is_available(self):
        with self.assertRaisesRegex(ValueError, "terminal"):
            PLUGIN.find_terminal_command(executable_finder=lambda name: None)

    def test_builds_interactive_release_command(self):
        project_directory = Path("/tmp/Armfed_3v16")
        release_script = Path("/tmp/idata_release.py")
        command = PLUGIN.build_terminal_command(
            ["/usr/bin/gnome-terminal", "--"],
            project_directory,
            python_executable="/usr/bin/python3",
            release_script=release_script,
        )

        self.assertEqual(command[:2], ["/usr/bin/gnome-terminal", "--"])
        self.assertEqual(command[2:4], ["bash", "-lc"])
        shell_command = command[4]
        self.assertIn(
            shlex.join(["/usr/bin/python3", str(release_script), str(project_directory)]),
            shell_command,
        )
        self.assertIn("read -r -p", shell_command)
        self.assertIn("exit \"$status\"", shell_command)


if __name__ == "__main__":
    unittest.main()
