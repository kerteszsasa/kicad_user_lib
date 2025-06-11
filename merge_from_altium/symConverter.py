import os
import sys
from pathlib import Path
import subprocess


def mergeLibs(IMPORT_DIR_PATH, OUTPUT_FILE_PATH):
    input_dir = Path(IMPORT_DIR_PATH)
    sym_files = sorted(str(p) for p in input_dir.glob('*.kicad_sym'))

    # Load header
    header = []
    with open(sym_files[0], 'r', encoding='utf-8') as f:
        lines = f.readlines()
        for i, line in enumerate(lines):
            if line.strip().startswith('(symbol '):
                break
            header.append(line)

    #Load symbols
    all_symbols = []
    for file in sym_files:
        with open(file, 'r', encoding='utf-8') as f:
            lines = f.readlines()
            #Add component data
            for i, line in enumerate(lines):
                if(i>3 and i < lines.__len__()-1):  # Skip header: first 3 and the last line
                    all_symbols.append(line)

    # Save component
    with open(OUTPUT_PATH, 'w', encoding='utf-8') as f:
        f.writelines(header)
        for sym in all_symbols:
            f.write(sym)
        f.write(')\n')  # End of header

        print(f"Saved sym lib: {OUTPUT_FILE_PATH}")

def convertFromAltiumToKicad(ALTIUM_FILES_PATH):
    files = os.listdir(ALTIUM_FILES_PATH)
    fileNum = files.__len__()
    print(fileNum) 

    for fileName in files:
        print(fileName)
        if fileName.endswith("Schlib"):
            altiumFilePath = IMPORT_DIR + "/" + fileName
            kicadFilePath = altiumFilePath.replace(".Schlib", ".kicad_sym")
            # print("From: " + altiumFilePath)
            # print("TO: " + kicadFilePath)
            output = subprocess.call(["kicad-cli", "sym", "upgrade", "--output", kicadFilePath, altiumFilePath])
            print(output)









def getConvertionLibName(CONVERT_DIR):
    files = os.listdir(CONVERT_DIR)
    for fileName in files:
        # print(fileName)
        if fileName.endswith("SchLib"):
            print(fileName)

            kicadFileName = fileName.replace(".SchLib", ".kicad_sym")
            print(kicadFileName)
            return kicadFileName
        


# IMPORT_DIR = "/home/kerteszs/hw/kicad_user_lib/from"
CONVERT_DIR = "/home/kerteszs/virtualbox vms/SHARED FOLDER/TMP"
IMPORT_DIR = CONVERT_DIR + "/out"
OUTPUT_PATH = "/home/kerteszs/hw/kicad_user_lib/symbols/" + getConvertionLibName(CONVERT_DIR)

convertFromAltiumToKicad(IMPORT_DIR)
mergeLibs(IMPORT_DIR, OUTPUT_PATH)
