"""Génère les métadonnées Windows à partir de la version unique du projet."""
from __future__ import annotations

import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from version import APP_VERSION


def version_parts(version: str) -> tuple[int, int, int, int]:
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise ValueError("APP_VERSION doit respecter le format majeur.mineur.correctif")
    return (*[int(part) for part in version.split(".")], 0)


def generate(output_directory: Path) -> tuple[Path, Path]:
    output_directory.mkdir(parents=True, exist_ok=True)
    parts = version_parts(APP_VERSION)
    inno = output_directory / "version.iss"
    resource = output_directory / "version_info.txt"
    inno.write_text(f'#define AppVersion "{APP_VERSION}"\n', encoding="utf-8")
    resource.write_text(
        "VSVersionInfo(ffi=FixedFileInfo(filevers={parts}, prodvers={parts}, "
        "mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)), "
        "kids=[StringFileInfo([StringTable('040C04B0', ["
        "StringStruct('CompanyName', 'Carnet Emploi 42'), "
        "StringStruct('FileDescription', 'Carnet Emploi 42'), "
        f"StringStruct('FileVersion', '{APP_VERSION}'), "
        "StringStruct('InternalName', 'CarnetEmploi42'), "
        "StringStruct('OriginalFilename', 'CarnetEmploi42.exe'), "
        "StringStruct('ProductName', 'Carnet Emploi 42'), "
        f"StringStruct('ProductVersion', '{APP_VERSION}')"
        "])]), VarFileInfo([VarStruct('Translation', [1036, 1200])])])\n".format(parts=parts),
        encoding="utf-8",
    )
    return inno, resource


if __name__ == "__main__":
    generate(PROJECT_ROOT / "packaging")
