"""
Modèle d'une commande Printou.

Voir docstring complète dans la version Session 1 : c'est inchangé.
Une commande = un dossier client avec sous-dossiers de format,
matching cross-dossiers des doublons par nom de base.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path


SUBFOLDER_FORMAT_HINTS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"15\s*x\s*23", re.IGNORECASE), "15x23"),
    (re.compile(r"20\s*x\s*30", re.IGNORECASE), "20x30"),
    (re.compile(r"\bA3\b",      re.IGNORECASE), "A3"),
    (re.compile(r"\bA2\b",      re.IGNORECASE), "A2"),
]

IGNORED_SUBFOLDER_PATTERNS = [
    re.compile(r"photo\s*num", re.IGNORECASE),
    re.compile(r"^\.", re.IGNORECASE),
]

PHOTO_EXTENSIONS = {".jpg", ".jpeg", ".JPG", ".JPEG", ".png", ".PNG"}

DUPLICATE_SUFFIX_RE = re.compile(r"^(.+?)(?:_(\d+))?$")


def detect_format_code(subfolder_name: str) -> str | None:
    for ignored in IGNORED_SUBFOLDER_PATTERNS:
        if ignored.search(subfolder_name):
            return None
    for pattern, code in SUBFOLDER_FORMAT_HINTS:
        if pattern.search(subfolder_name):
            return code
    return None


def extract_base_name(filename: str) -> tuple[str, int]:
    """Retourne (base_name, copy_index)."""
    stem = Path(filename).stem
    match = DUPLICATE_SUFFIX_RE.match(stem)
    if not match:
        return (stem, 0)
    base, suffix = match.group(1), match.group(2)
    if suffix is None:
        return (stem, 0)
    if len(suffix) <= 2:
        return (base, int(suffix))
    return (stem, 0)


@dataclass
class TirageDemande:
    format_code: str
    quantity: int = 0
    source_files: list[Path] = field(default_factory=list)


@dataclass
class PhotoSource:
    base_name: str
    representative_file: Path
    tirages: dict[str, TirageDemande] = field(default_factory=dict)

    @property
    def total_quantity(self) -> int:
        return sum(t.quantity for t in self.tirages.values())

    @property
    def formats(self) -> list[str]:
        return list(self.tirages.keys())


@dataclass
class Commande:
    folder: Path
    name: str
    photos: list[PhotoSource] = field(default_factory=list)
    unrecognized_subfolders: list[str] = field(default_factory=list)
    creation_time: float = 0.0  # timestamp Unix de création du dossier (FIFO)

    @property
    def total_tirages(self) -> int:
        return sum(p.total_quantity for p in self.photos)

    @property
    def display_name(self) -> str:
        parts = self.name.split("_")
        if len(parts) >= 3:
            human_parts = []
            for p in parts:
                if p.isalpha() or " " in p:
                    human_parts.append(p)
            if human_parts:
                return " ".join(human_parts).strip().title()
        return self.name


def scan_commande(folder: Path) -> Commande:
    try:
        ctime = folder.stat().st_ctime
    except OSError:
        ctime = 0.0
    commande = Commande(folder=folder, name=folder.name, creation_time=ctime)
    if not folder.is_dir():
        return commande

    photo_index: dict[str, PhotoSource] = {}

    for subfolder in sorted(folder.iterdir()):
        if not subfolder.is_dir():
            continue

        format_code = detect_format_code(subfolder.name)
        if format_code is None:
            commande.unrecognized_subfolders.append(subfolder.name)
            continue

        for photo_file in sorted(subfolder.iterdir()):
            if photo_file.suffix not in PHOTO_EXTENSIONS:
                continue

            base_name, copy_index = extract_base_name(photo_file.name)

            if base_name not in photo_index:
                photo_index[base_name] = PhotoSource(
                    base_name=base_name,
                    representative_file=photo_file,
                )

            photo_source = photo_index[base_name]

            if copy_index == 0:
                photo_source.representative_file = photo_file

            if format_code not in photo_source.tirages:
                photo_source.tirages[format_code] = TirageDemande(format_code=format_code)
            tirage = photo_source.tirages[format_code]
            tirage.quantity += 1
            tirage.source_files.append(photo_file)

    commande.photos = sorted(photo_index.values(), key=lambda p: p.base_name)
    return commande
