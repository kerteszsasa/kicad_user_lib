import os
import sys
from pathlib import Path
import subprocess


def convertFromAltiumToKicad(ALTIUM_FILES_PATH, OUTPUT_FILES_PATH):
    files = os.listdir(ALTIUM_FILES_PATH)
    fileNum = files.__len__()
    print(fileNum) 

    for fileName in files:
        print(fileName)
        if fileName.endswith("PcbLib"):
            altiumFilePath = ALTIUM_FILES_PATH + "/" + fileName
            kicadFilePath = OUTPUT_FILES_PATH + fileName.replace(".PcbLib", ".pretty")
            # print("From: " + altiumFilePath)
            # print("TO: " + kicadFilePath)
            output = subprocess.call(["kicad-cli", "fp", "upgrade", "--output", kicadFilePath, altiumFilePath])
            print(output)


CONVERT_DIR = "/home/kerteszs/virtualbox vms/SHARED FOLDER/TMP"
OUTPUT_PATH = "/home/kerteszs/hw/kicad_user_lib/footprints/"

convertFromAltiumToKicad(CONVERT_DIR, OUTPUT_PATH)
