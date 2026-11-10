import unittest
from unittest import mock
from types import SimpleNamespace

from external_plugins.LCSC_import import LCSC_import


class LcscImportPluginTests(unittest.TestCase):
    def test_wrapper_is_importable_without_kicad(self):
        self.assertIsNotNone(LCSC_import.LCSCImportPlugin)
        self.assertTrue(LCSC_import.IMPORTER_PATH.is_file())

    def test_wrapper_does_not_register_without_running_kicad_app(self):
        self.assertFalse(LCSC_import.is_kicad_runtime())

    def test_dialog_parent_supports_kicad_app_console(self):
        app_console = SimpleNamespace()
        fake_wx = SimpleNamespace(
            GetApp=lambda: app_console,
            GetActiveWindow=lambda: "active-window",
        )

        with mock.patch.object(LCSC_import, "wx", fake_wx):
            self.assertEqual(LCSC_import._dialog_parent(), "active-window")

    def test_plugin_metadata_uses_requested_name(self):
        plugin = LCSC_import.LCSCImportPlugin()

        plugin.defaults()

        self.assertEqual(plugin.name, "LCSC_import")
        self.assertEqual(plugin.category, "LCSC")
        self.assertTrue(plugin.show_toolbar_button)
        self.assertEqual(plugin.icon_file_name, str(LCSC_import.ICON_PATH))
        self.assertEqual(plugin.dark_icon_file_name, str(LCSC_import.ICON_PATH))
        self.assertTrue(LCSC_import.ICON_PATH.is_file())

    def test_result_summary_mentions_imported_component(self):
        result = {
            "lcsc_id": "C123",
            "symbol": "C123",
            "symbol_library": "Supply.kicad_sym",
            "footprint_library": "Supply.pretty",
            "footprint": "SOT-223.kicad_mod",
            "models": {"part.wrl": "copied", "part.step": "exists"},
        }

        summary = LCSC_import.format_result(result)

        self.assertIn("C123", summary)
        self.assertIn("Supply.kicad_sym", summary)
        self.assertIn("part.wrl", summary)
        self.assertIn("part.step", summary)

    def test_run_uses_gui_callbacks_and_importer(self):
        plugin = LCSC_import.LCSCImportPlugin()
        fake_result = {
            "lcsc_id": "C123",
            "symbol": "C123",
            "symbol_library": "Supply.kicad_sym",
            "footprint_library": "Supply.pretty",
            "footprint": "SOT-223.kicad_mod",
            "models": {},
        }

        with mock.patch.object(
            LCSC_import, "ask_lcsc_id", return_value="C123"
        ) as ask_id, mock.patch.object(
            LCSC_import, "import_component", return_value=fake_result
        ) as import_component, mock.patch.object(
            LCSC_import, "show_message"
        ) as show_message:
            plugin.Run()

        ask_id.assert_called_once()
        import_component.assert_called_once()
        self.assertEqual(import_component.call_args.args, ("C123",))
        self.assertEqual(import_component.call_args.kwargs["selector"], LCSC_import.select_from_list)
        self.assertEqual(import_component.call_args.kwargs["input_fn"], LCSC_import.ask_yes_no)
        show_message.assert_called_once()


if __name__ == "__main__":
    unittest.main()
