import tempfile
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import kicad_lib_importer as importer


class SymbolParsingTests(unittest.TestCase):
    def test_extracts_outer_symbol_with_nested_subsymbols(self):
        content = '''(kicad_symbol_lib
\t(version 20241209)
\t(symbol "C123"
\t\t(property "Footprint" "old.pretty:old")
\t\t(symbol "C123_1_1"
\t\t\t(property "Reference" "U")
\t\t)
\t)
)\n'''

        block = importer.extract_first_symbol(content)

        self.assertEqual(block.name, "C123")
        self.assertEqual(content[block.start : block.end], block.content)
        self.assertIn('(symbol "C123_1_1"', block.content)

    def test_updates_footprint_property_inside_symbol(self):
        block = '''(symbol "C123"
\t(property "Footprint" "old.pretty:old")
\t(symbol "C123_1_1")
)'''

        updated = importer.replace_footprint_property(
            block, "0_Supply:SOT-223-4P.kicad_mod"
        )

        self.assertIn(
            '(property "Footprint" "0_Supply:SOT-223-4P.kicad_mod")', updated
        )
        self.assertNotIn("old.pretty:old", updated)

    def test_appends_and_removes_symbol_block_without_dropping_header(self):
        source = '''(kicad_symbol_lib
\t(version 20241209)
\t(generator "kicad_symbol_editor")
\t(generator_version "9.0")
\t(symbol "C123"
\t\t(property "Footprint" "old")
\t)
)\n'''
        target = '''(kicad_symbol_lib
\t(version 20241209)
\t(generator "kicad_symbol_editor")
\t(generator_version "9.0")
)\n'''

        extracted = importer.extract_first_symbol(source)
        modified = importer.replace_footprint_property(extracted.content, "0_Supply:fp")
        target_after = importer.insert_symbol(target, modified, extracted.name)
        source_after = importer.remove_symbol(source, extracted)

        self.assertIn('(symbol "C123"', target_after)
        self.assertIn('(property "Footprint" "0_Supply:fp")', target_after)
        self.assertEqual(source_after, importer.EMPTY_SYMBOL_LIBRARY)


class FilesystemTests(unittest.TestCase):
    @staticmethod
    def _create_library_fixture(root: Path) -> tuple[Path, Path, Path, Path, Path]:
        download = root / "download"
        footprints = root / "footprints"
        symbols = root / "symbols"
        models = root / "3dmodels"
        generated_pretty = download / "easyeda2kicad.pretty"
        generated_models = download / "easyeda2kicad.3dshapes"
        generated_pretty.mkdir(parents=True)
        generated_models.mkdir(parents=True)
        (footprints / "Supply.pretty").mkdir(parents=True)
        symbols.mkdir()
        models.mkdir()
        (symbols / "Supply.kicad_sym").write_text(
            importer.EMPTY_SYMBOL_LIBRARY, encoding="utf-8"
        )
        return download, footprints, symbols, models, generated_pretty

    def test_full_import_copies_assets_updates_paths_and_cleans_source(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            download, footprints, symbols, models, generated_pretty = (
                self._create_library_fixture(root)
            )
            generated_models = download / "easyeda2kicad.3dshapes"

            def fake_runner(command, *, cwd, check):
                self.assertEqual(
                    command, ["easyeda2kicad", "--lcsc_id", "C123", "--full"]
                )
                self.assertEqual(cwd, str(download))
                (generated_pretty / "SOT-223.kicad_mod").write_text(
                    '(footprint "SOT-223"\n'
                    '  (model "${EASYEDA2KICAD}/easyeda2kicad.3dshapes/model.wrl")\n'
                    ")\n",
                    encoding="utf-8",
                )
                (generated_models / "model.wrl").write_text("wrl", encoding="utf-8")
                (generated_models / "model.step").write_text("step", encoding="utf-8")
                (download / "easyeda2kicad.kicad_sym").write_text(
                    '''(kicad_symbol_lib
\t(version 20241209)
\t(generator "kicad_symbol_editor")
\t(generator_version "9.0")
\t(symbol "C123"
\t\t(property "Footprint" "old")
\t)
)\n''',
                    encoding="utf-8",
                )

            def selector(options, title):
                return "Supply.pretty" if "footprint" in title else "Supply.kicad_sym"

            result = importer.import_component(
                "C123",
                download_dir=download,
                footprints_dir=footprints,
                symbols_dir=symbols,
                models_dir=models,
                selector=selector,
                runner=fake_runner,
            )

            copied_footprint = footprints / "Supply.pretty" / "SOT-223.kicad_mod"
            self.assertEqual(result["footprint_status"], "copied")
            self.assertIn(
                '${CUSTOM_3DMODEL_DIR}/model.wrl',
                copied_footprint.read_text(encoding="utf-8"),
            )
            self.assertEqual((models / "model.wrl").read_text(), "wrl")
            self.assertEqual((models / "model.step").read_text(), "step")
            target_symbol = (symbols / "Supply.kicad_sym").read_text(encoding="utf-8")
            self.assertIn('(property "Footprint" "0_Supply:SOT-223")', target_symbol)
            self.assertEqual(
                (download / "easyeda2kicad.kicad_sym").read_text(encoding="utf-8"),
                importer.EMPTY_SYMBOL_LIBRARY,
            )
            self.assertEqual(list(generated_pretty.iterdir()), [])
            self.assertEqual(list(generated_models.iterdir()), [])

    def test_non_empty_previous_output_can_be_declined_without_cleanup(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            download, footprints, symbols, models, generated_pretty = (
                self._create_library_fixture(root)
            )
            previous = generated_pretty / "previous.kicad_mod"
            previous.write_text("old", encoding="utf-8")

            with self.assertRaises(importer.UserCancelled):
                importer.import_component(
                    "C123",
                    download_dir=download,
                    footprints_dir=footprints,
                    symbols_dir=symbols,
                    models_dir=models,
                    input_fn=lambda prompt: "n",
                    runner=lambda *args, **kwargs: self.fail("runner should not run"),
                )

            self.assertEqual(previous.read_text(), "old")

    def test_existing_symbol_decline_leaves_partial_source_for_debugging(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            download, footprints, symbols, models, generated_pretty = (
                self._create_library_fixture(root)
            )
            generated_models = download / "easyeda2kicad.3dshapes"
            (symbols / "Supply.kicad_sym").write_text(
                '''(kicad_symbol_lib
\t(version 20241209)
\t(symbol "C123")
)\n''',
                encoding="utf-8",
            )

            def fake_runner(command, *, cwd, check):
                (generated_pretty / "SOT-223.kicad_mod").write_text(
                    '(footprint "SOT-223"\n'
                    '  (model "old/model.wrl")\n'
                    ")\n",
                    encoding="utf-8",
                )
                (generated_models / "model.wrl").write_text("wrl", encoding="utf-8")
                (download / "easyeda2kicad.kicad_sym").write_text(
                    '''(kicad_symbol_lib
\t(version 20241209)
\t(symbol "C123"
\t\t(property "Footprint" "old")
\t)
)\n''',
                    encoding="utf-8",
                )

            def selector(options, title):
                return "Supply.pretty" if "footprint" in title else "Supply.kicad_sym"

            with self.assertRaises(importer.PartialImportError):
                importer.import_component(
                    "C123",
                    download_dir=download,
                    footprints_dir=footprints,
                    symbols_dir=symbols,
                    models_dir=models,
                    selector=selector,
                    input_fn=lambda prompt: "n",
                    runner=fake_runner,
                )

            self.assertTrue((models / "model.wrl").exists())
            self.assertTrue((footprints / "Supply.pretty" / "SOT-223.kicad_mod").exists())
            self.assertTrue(list(generated_pretty.iterdir()))
            self.assertTrue(list(generated_models.iterdir()))

    def test_existing_models_are_not_overwritten(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "source"
            destination = Path(temp) / "destination"
            source.mkdir()
            destination.mkdir()
            (source / "part.wrl").write_text("new", encoding="utf-8")
            (destination / "part.wrl").write_text("old", encoding="utf-8")
            (source / "part.step").write_text("step", encoding="utf-8")

            results = importer.copy_3d_models(source, destination)

            self.assertEqual((destination / "part.wrl").read_text(), "old")
            self.assertEqual((destination / "part.step").read_text(), "step")
            self.assertEqual(results["part.wrl"], "exists")
            self.assertEqual(results["part.step"], "copied")

    def test_cleanup_preserves_directories_bak_and_symbol_header(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            pretty = root / "easyeda2kicad.pretty"
            models = root / "easyeda2kicad.3dshapes"
            pretty.mkdir()
            models.mkdir()
            (pretty / "part.kicad_mod").write_text("footprint", encoding="utf-8")
            (models / "part.wrl").write_text("model", encoding="utf-8")
            (root / "easyeda2kicad.bak").write_text("backup", encoding="utf-8")
            symbol_file = root / "easyeda2kicad.kicad_sym"
            symbol_file.write_text(
                importer.EMPTY_SYMBOL_LIBRARY.replace(")\n", '\t(symbol "C123")\n)\n'),
                encoding="utf-8",
            )

            self.assertTrue(importer.has_stale_download_output(root))
            importer.clean_download_output(root)

            self.assertTrue(pretty.is_dir())
            self.assertTrue(models.is_dir())
            self.assertFalse((pretty / "part.kicad_mod").exists())
            self.assertFalse((models / "part.wrl").exists())
            self.assertEqual((root / "easyeda2kicad.bak").read_text(), "backup")
            self.assertEqual(symbol_file.read_text(), importer.EMPTY_SYMBOL_LIBRARY)

    def test_clean_download_output_is_not_triggered_by_header_only_library(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "easyeda2kicad.kicad_sym").write_text(
                importer.EMPTY_SYMBOL_LIBRARY, encoding="utf-8"
            )
            (root / "easyeda2kicad.pretty").mkdir()
            (root / "easyeda2kicad.3dshapes").mkdir()

            self.assertFalse(importer.has_stale_download_output(root))


if __name__ == "__main__":
    unittest.main()
