import os

def createSymbolTable(folder_path, table_path, prefix):
    files = os.listdir(folder_path)
    fileNum = files.__len__()
    print(fileNum) 
    header = []

    for fileName in files:
        header.append('  (lib (name "')
        header.append(prefix)
        header.append(fileName.replace(".kicad_sym", ""))
        header.append('")(type "KiCad")(uri "${')
        header.append('CUSTOM_SYMBOL_DIR}/')
        header.append(fileName)
        header.append('")(options "")(descr ""))')
        header.append('\n')

    with open(table_path, 'w', encoding='utf-8') as f:
        f.writelines(header)
        print(f"Saved extension: {table_path}")





def createFootprintTable(folder_path, table_path, prefix):
    files = os.listdir(folder_path)
    fileNum = files.__len__()
    print(fileNum) 
    header = []

    for fileName in files:
        header.append('  (lib (name "')
        header.append(prefix)
        header.append(fileName.replace(".pretty", ""))
        header.append('")(type "KiCad")(uri "${')
        header.append('CUSTOM_FOOTPRINT_DIR}/')
        header.append(fileName)
        header.append('")(options "")(descr ""))')
        header.append('\n')

    with open(table_path, 'w', encoding='utf-8') as f:
        f.writelines(header)
        print(f"Saved extension: {table_path}")


SYM_FOLDER_PATH = "/home/kerteszs/hw/kicad_user_lib/symbols/"
SYM_EXTENSION_PATH = "/home/kerteszs/hw/kicad_user_lib/sym-lib-table-extension"
SYM_PREFIX = "0_"
createSymbolTable(SYM_FOLDER_PATH, SYM_EXTENSION_PATH, SYM_PREFIX)

FP_FOLDER_PATH = "/home/kerteszs/hw/kicad_user_lib/footprints/"
FP_EXTENSION_PATH = "/home/kerteszs/hw/kicad_user_lib/fp-lib-table-extension"
FP_PREFIX = "0_"
createFootprintTable(FP_FOLDER_PATH, FP_EXTENSION_PATH, FP_PREFIX)
