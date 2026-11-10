# idata_release KiCad External Plugin

Ez a wrapper a `scripts/idata_release/idata_release.py` release scriptet teszi elérhetővé a KiCad 9 PCB Editor `External Plugins` menüjéből.

## Telepítés symlinkkel

A KiCad saját External Plugin könyvtárába a teljes pluginmappára készíts symlinket:

```bash
mkdir -p /home/kerteszs/.local/share/kicad/9.0/scripting/plugins
ln -sfn \
  /home/kerteszs/hw/kicad_user_lib/external_plugins/idata_release \
  /home/kerteszs/.local/share/kicad/9.0/scripting/plugins/idata_release
```

Ezután indítsd újra a KiCad PCB Editort. A plugin itt jelenik meg:

```text
External Plugins → idata_release
```

A symlinket a teljes `idata_release` mappára kell létrehozni, nem csak a Python-fájlra. A wrapper a symlink feloldott céljából keresi meg a repóban levő `scripts/idata_release/idata_release.py` scriptet.

## Használat

1. Nyisd meg a projekt PCB-fájlját a KiCad PCB Editorban.
2. Mentsd el a PCB-t, ha még nincs fájlútvonala.
3. Indítsd el az `External Plugins → idata_release` menüpontot.
4. A wrapper külön terminálablakot nyit, és abban futtatja az eredeti release scriptet.
5. Az ERC/DRC kérdésekre ebben a terminálablakban lehet válaszolni.
6. A teljes script-kimenet futás közben folyamatosan látható.
7. A terminál a release végén Enter lenyomásáig nyitva marad, így a sikeres vagy hibás eredmény elolvasható.

A release script a projekt mappáját a megnyitott `.kicad_pcb` fájlból határozza meg. A folyamat elején törli a projekt saját `DOC/`, `PRODUCTION/` mappáit és a `<projekt>.zip` fájlt. Sikeres futás után létrejönnek a PDF, CSV BOM, XLS BOM, HTML BOM, gyártási fájlok és a projekt release ZIP-archívuma.

## Követelmények

- KiCad 9 PCB Editor
- Elérhető grafikus terminálprogram: elsődlegesen `gnome-terminal`, fallbackként `x-terminal-emulator`
- A `Board2Pdf`, `Generate Interactive HTML BOM` és Fabrication Toolkit pluginek telepítve
- Az `ssconvert` parancs a PATH-ban; Debian/Ubuntu alatt a `gnumeric` csomag biztosítja
- A projektben érvényes `IDATA_BOM_VIEW` BOM preset

Ha a wrapper nem találja az `ssconvert` programot vagy valamelyik szükséges plugint, hibaüzenetet jelenít meg.

## Ikon

A KiCad plugin ikonja az `idata_release.png` fájl. Ez az eredeti `images.jpg` ikon KiCadhez megfelelő PNG formátumú változata.
