# LCSC_import KiCad External Plugin

Ez a wrapper a repó `scripts/kicad_lib_importer/kicad_lib_importer.py` importálóját teszi elérhetővé a KiCad 9 PCB Editor `External Plugins` menüjéből. A plugin teljes csomagja az `external_plugins/LCSC_import/` könyvtárban van.

## Telepítés KiCad 9-hez

A KiCad a saját plugin könyvtárát tölti be. A repóban lévő wrapperhez készíts symlinket:

```bash
ln -sfn \
  /home/kerteszs/hw/kicad_user_lib/external_plugins/LCSC_import \
  /home/kerteszs/.local/share/kicad/9.0/scripting/plugins/LCSC_import
```

Ezután indítsd újra a PCB Editort. A menüben ez jelenik meg:

```text
External Plugins → LCSC_import
```

Az ikon a plugin mappában található `LCSC_import.png` fájl. A symlinket a teljes pluginmappára kell létrehozni, nem csak a Python-fájlra.

## Használat

1. Indítsd el az `LCSC_import` plugint.
2. Add meg az LCSC cikkszámot, például `C123456`.
3. Válaszd ki a cél footprint-könyvtárat a grafikus listából.
4. Válaszd ki a cél symbol-fájlt.
5. Ütközés esetén a plugin kérdez, mielőtt felülírna egy symbolt.

A wrapper ugyanazt az `import_component()` függvényt használja, mint a parancssoros script. A CLI-s működés és a curses-alapú választók változatlanok maradnak.

## Fontos

- A plugin a `pcbnew.ActionPlugin` API-t használja, ezért a PCB Editorból indítható.
- A letöltés közben a KiCad főablaka átmenetileg várakozhat.
- Az `easyeda2kicad` programnak a PATH-ban elérhetőnek kell lennie.
- A wrapper nem másolja a teljes importáló scriptet; a repó gyökeréből importálja.
