# iData release ellenőrző

Az `idata_release.py` a KiCad-projekt release előtti első ellenőrzési lépéseit végzi el.

## Használat

```bash
python3 idata_release.py /elérési/út/a/projektmappához
```

A megadott mappa nevének meg kell egyeznie a projekt gyökérfájljainak alapnevével. Például:

```text
Armfed_3v15/
├── Armfed_3v15.kicad_pro
├── Armfed_3v15.kicad_sch
├── Armfed_3v15.kicad_pcb
├── Controller.kicad_sch
├── GPS.kicad_sch
└── ...
```

A további `.kicad_sch` fájlok megengedettek, mert ezek hierarchikus schematic-lapok lehetnek. Az ERC a projekt nevét viselő gyökér-schematicon fut le.

## Folyamat

1. A script ellenőrzi a projektmappát, a projektfájlt, a gyökér-schematicot és a PCB-fájlt.
2. Lefuttatja a KiCad Electrical Rules Checkert (`kicad-cli sch erc`).
3. Lefuttatja a KiCad Design Rules Checkert (`kicad-cli pcb drc`).
4. Aktív warning vagy error esetén megkérdezi:

   ```text
   Continue? [y/N]
   ```

   Csak a `y` vagy `yes` válasz engedi a folytatást. Üres, ismeretlen vagy `n` válasz esetén a folyamat leáll.

   A KiCad által exclusionként jelölt eltéréseket külön kezeli. Ezeknél a kérdés:

   ```text
   Are excluded violations a problem? [Y/n]
   ```

   Az alapértelmezett válasz `yes`: üres vagy ismeretlen válasz esetén a folyamat leáll. Csak az explicit `n` vagy `no` válasz esetén számít az exclusion elfogadottnak és folytatódik a folyamat.

5. Ellenőrzi, hogy a projektben létezik-e az `IDATA_BOM_VIEW` BOM preset, és tartalmazza-e a következő látható mezőket:

   ```text
   Reference, Value, Footprint, PART NUMBER, Description, Qty
   ```

   A `Qty` mező KiCad-belső neve `${QUANTITY}` lehet. Hiányzó preset vagy mező esetén a script hibát ír ki, és a folyamat leáll.

6. Ha mindkét ellenőrzés sikeres, vagy a felhasználó minden problémánál engedélyezte a folytatást, és a BOM preset is érvényes, kiírja:

   ```text
   Starting release.
   ```

   A release indulása előtt ellenőrzi a `Board2Pdf`, a `Generate Interactive HTML BOM`, a Fabrication Toolkit és az `ssconvert` program/plugin meglétét. Ha az `ssconvert` hiányzik, a script hibával leáll, és jelzi, hogy a `gnumeric` csomagot kell telepíteni.

7. A release első lépéseként törli a projekt korábbi `DOC/`, `PRODUCTION/` mappáit és a projekt gyökerében levő `<projekt>.zip` fájlt, ha ezek léteznek.
8. A projektmappán belül létrehozza a `DOC/` és `PRODUCTION/` mappákat.
9. Elkészíti a teljes, hierarchikus schematic PDF plotot:

   ```text
   DOC/<projekt>.pdf
   ```

10. Elkészíti a projekt `IDATA_BOM_VIEW` presetjét használó CSV BOM-ot:

   ```text
   PRODUCTION/<projekt>_BOM.csv
   ```

   A DNP és `Exclude from BOM` jelölésű alkatrészek kimaradnak.

11. A CSV BOM-ból `ssconvert` segítségével elkészíti a régi Excel `.xls` formátumú BOM-ot:

   ```text
   DOC/<projekt>_BOM.xls
   ```

   A konverzió a `Gnumeric_Excel:excel_biff8` exporterrel történik. Sikertelen konverzió esetén a release hibával leáll.

12. Meghívja a Board2Pdf plugint, és elkészíti a PCB Assembly PDF-et:

   ```text
   DOC/<projekt>_Assembly.pdf
   ```

   A script először a `board2pdf` parancsot keresi a `PATH`-ban, majd a KiCad 9 felhasználói pluginját. Ha a plugin nem található, a release hibával leáll, és jelzi, hogy a Board2Pdf plugint telepíteni kell.

13. Meghívja a `Generate Interactive HTML BOM` plugint, böngésző megnyitása nélkül, és elkészíti az interaktív HTML BOM-ot:

   ```text
   DOC/<projekt>.html
   ```

   A plugin futtatásakor a script rögzíti a `--no-browser`, `--dest-dir DOC` és `--name-format <projekt>` opciókat. Ha a plugin nem található, a release még a `Starting release.` üzenet előtt hibával leáll. A plugin telepített példányát a KiCad felhasználói pluginmappáiban keresi.

14. Meghívja a Fabrication Toolkit plugint a PCB gyártási fájljainak elkészítéséhez. A script a plugin GUI-alapértékeit követő automatikus fordítást és zónafeltöltést használja, nem készít plugin-backupot, és nem nyit meg böngészőt.

   A plugin alapértelmezett `production/` kimenetéből a következő fájlok kerülnek a nagybetűs `PRODUCTION/` mappába:

   ```text
   production/<projekt>.zip       → PRODUCTION/<projekt>_Gerber.zip
   production/positions.csv       → PRODUCTION/<projekt>_Positions.csv
   ```

   Elsődlegesen a projekt nevű ZIP-et keresi. Ha a plugin a PCB title/revision beállítása miatt más nevet ad, akkor csak egyetlen friss ZIP esetén használja azt forrásként. A másolások és a célfájlok ellenőrzése után a kisbetűs `production/` mappa törlődik. Hiányzó Fabrication Toolkit plugin, hiányzó kimeneti fájl vagy sikertelen másolás esetén a release hibával leáll, és a `production/` mappát megőrzi a hiba kivizsgálásához.

15. Ha minden korábbi lépés sikeres, a `DOC/` és `PRODUCTION/` mappák tartalmából elkészíti a projekt gyökerébe kerülő release-archívumot:

   ```text
   <projekt>.zip
   ```

   Az archívum a `DOC/` és `PRODUCTION/` mappákat is tartalmazza.

Az ERC- és DRC-riportok JSON formátumban, ideiglenes fájlba készülnek, majd feldolgozás után törlődnek. Az aktív és excluded eltérések külön összesítésben jelennek meg a terminálon.

A release minden futás elején eltávolítja a korábbi release-kimeneteket, ezért nincs külön felülírási kérdés.

## Kilépési kódok

- `0`: az ellenőrzések, a schematic PDF, a CSV és XLS BOM, a Board2Pdf Assembly PDF, az interaktív HTML BOM, a Fabrication Toolkit kimenetei és a release-archívum generálása is sikeres.
- `1`: a felhasználó nem engedte tovább a folyamatot warning/error után.
- `2`: hibás projektútvonal, hiányzó vagy nem egyértelmű projektfájl, hibás BOM preset, illetve KiCad CLI-, riport- vagy artifact-generálási hiba.

## Fejlesztői ellenőrzés

```bash
python3 -m unittest -v test_idata_release.py
```

## Jelenlegi hatókör

Ez a verzió az ERC/DRC ellenőrzést, a schematic PDF plotot, a CSV és XLS BOM, a Board2Pdf Assembly PDF, az interaktív HTML BOM, a Fabrication Toolkit gyártási kimeneteinek és a teljes release-archívum generálását végzi el. A további release-lépéseket később adjuk hozzá.
