# KiCad LCSC alkatrész-importáló

Ez a repó egy olyan Python scriptet tartalmaz, amely az `easyeda2kicad` programmal letöltött LCSC alkatrészt a saját KiCad-könyvtárakba rendezi.

## Futtatás

LCSC cikkszámmal argumentumként:

```bash
python3 kicad_lib_importer.py C123456
```

Argumentum nélkül a script indulás után bekéri a cikkszámot:

```bash
python3 kicad_lib_importer.py
```

A script az `easyeda2kicad` parancsot a PATH-ból használja, és a default kimeneti könyvtárból futtatja:

```text
/home/kerteszs/Documents/Kicad/easyeda2kicad
```

A letöltési parancs:

```bash
easyeda2kicad --lcsc_id <LCSC_ID> --full
```

A script nem adja át a `--output` kapcsolót.

## Korábbi sikertelen futás kezelése

A script induláskor ellenőrzi, hogy maradt-e adat a korábbi default kimenetben:

- fájl az `easyeda2kicad.pretty/` könyvtárban;
- fájl az `easyeda2kicad.3dshapes/` könyvtárban;
- symbol blokk az `easyeda2kicad.kicad_sym` fájlban.

Ha a kimenet nem üres, a script interaktívan kérdez:

- `i` vagy `y`: törölje a korábbi kimeneti fájlokat és folytassa;
- más válasz: álljon le, a fájlokat hagyja meg.

Takarításkor a könyvtárak nem törlődnek. Csak a bennük lévő fájlok törlődnek. Az `easyeda2kicad.bak` fájlhoz a script nem nyúl.

Az üres symbol-fájl megőrzi a KiCad által megnyitható fejlécet:

```text
(kicad_symbol_lib
	(version 20241209)
	(generator "kicad_symbol_editor")
	(generator_version "9.0")
)
```

## 3D-modellek

Az `easyeda2kicad.3dshapes/` könyvtár minden `.wrl` és `.step` fájlja a repó `3dmodels/` könyvtárába kerül.

Ha a célfájl már létezik:

- a régi fájl nem íródik felül;
- a script jelzi, hogy a fájl már megvolt;
- az import folytatódik.

A footprint modellhivatkozásában a `.wrl` fájl élvez elsőbbséget. Ha nincs `.wrl`, a script a `.step` fájlt használja:

```text
${CUSTOM_3DMODEL_DIR}/modell-neve.wrl
```

Az összes letöltött 3D-fájl másolódik, akkor is, ha csak az egyikre kerül hivatkozás a footprintben.

## Footprint

A script listázza a repó `footprints/` könyvtárának `.pretty` alkönyvtárait. A választás curses-alapú terminálfelületen történik:

- fel/le nyíl vagy `j`/`k`: mozgatás;
- Enter: kiválasztás;
- `q` vagy Escape: megszakítás.

A letöltött footprint a kiválasztott könyvtárba kerül. Ha ugyanilyen nevű `.kicad_mod` már létezik, nem íródik felül.

A célhivatkozás a footprint fájlban:

```text
(model "${CUSTOM_3DMODEL_DIR}/modell-neve.wrl"
```

## Symbol

A script a letöltött `easyeda2kicad.kicad_sym` fájlból kiegyensúlyozott zárójel-feldolgozással emeli ki a teljes külső symbol blokkot. A benne lévő sub-symbolok is az átemelt blokk részei maradnak.

A script listázza a repó `symbols/` könyvtárának `.kicad_sym` fájljait. A `.bak` fájlok nem jelennek meg a listában.

A symbol `Footprint` property-je erre a formára változik:

```text
property "Footprint" "0_Supply:SOT-223"
```

Az első rész a kiválasztott footprint-könyvtár `.pretty` utótag nélküli neve, a második rész a footprint fájlneve kiterjesztés nélkül. A tényleges fájl természetesen `.kicad_mod` kiterjesztésű marad.

Ha a célkönyvtárban már létezik azonos nevű symbol, a script felülírási kérdést tesz fel. Nemleges válasz esetén a folyamat részleges eredménnyel leáll, és a letöltött forrásfájlokat nem takarítja ki.

## Sikeres befejezés utáni takarítás

Csak teljesen sikeres import után történik takarítás:

- az `easyeda2kicad.pretty/` könyvtár megmarad, de a fájljai törlődnek;
- az `easyeda2kicad.3dshapes/` könyvtár megmarad, de a fájljai törlődnek;
- az `easyeda2kicad.kicad_sym` fájl megmarad, de csak az üres KiCad-fejléc marad benne;
- az `easyeda2kicad.bak` változatlan marad.

Hiba, felhasználói megszakítás vagy symbol-kihagyás esetén a letöltött forrásfájlok megmaradnak hibakereséshez.

## Tesztelés

A standard library-alapú tesztek futtatása:

```bash
python3 -m unittest discover -s tests -v
```

A tesztek valódi hálózati letöltés nélkül ellenőrzik a symbol-parszolást, a footprint- és modellútvonalakat, a célfájlok ütközéskezelését, a korábbi sikertelen futás felismerését és a sikeres takarítást.

## KiCad External Plugin

A wrapper az `external_plugins/LCSC_import/` pluginmappában található. Ez a KiCad 9 PCB Editor `pcbnew.ActionPlugin` API-ját használja, grafikus `wx` ablakokkal váltja ki a parancssoros cikkszám-, footprint- és symbol-választást, valamint saját toolbar-ikont biztosít.

A KiCad pluginmappájába symlinkkel telepíthető:

```bash
ln -sfn \
  /home/kerteszs/hw/kicad_user_lib/external_plugins/LCSC_import \
  /home/kerteszs/.local/share/kicad/9.0/scripting/plugins/LCSC_import
```

KiCad újraindítása után a plugin a `External Plugins → LCSC_import` menüben jelenik meg. Részletesebb telepítési leírás az `external_plugins/README.md` fájlban van.
