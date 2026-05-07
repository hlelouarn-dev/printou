"""
Service de gestion des logos référencés par les templates (v0.9).

Stratégie : tous les logos utilisés dans des templates sont copiés
dans APPDATA/Printou/logos/ sous un nom slugifié.

- import_logo(source_path) : copie le fichier dans le dossier logos, retourne
  le NOM RELATIF (ex: 'harcour_pole_european.png')
- get_logo_path(name) : retourne le chemin absolu d'un logo par son nom
- list_logos() : liste tous les logos disponibles

Les templates stockent juste le NOM RELATIF dans asset_path (pas le chemin
absolu) pour rester portables entre PCs et inclus dans les ZIPs.
"""

from __future__ import annotations

import re
import shutil
import unicodedata
from pathlib import Path

from .config import get_user_logos_dir


def slugify_filename(name: str) -> str:
    """'Logo Harcour 2026.PNG' → 'logo_harcour_2026.png' (préserve l'extension)."""
    p = Path(name)
    stem, ext = p.stem, p.suffix.lower()
    stem = unicodedata.normalize("NFD", stem)
    stem = "".join(c for c in stem if unicodedata.category(c) != "Mn")
    stem = re.sub(r"[^a-zA-Z0-9]+", "_", stem).lower().strip("_")
    return (stem or "logo") + ext


def import_logo(source_path: Path | str, overwrite: bool = True) -> str:
    """Copie un fichier logo dans le dossier user, retourne son nom relatif.

    Args:
        source_path: chemin du fichier logo (n'importe où sur le disque)
        overwrite: si True, écrase un logo du même nom déjà présent.
                   Si False et un fichier existe déjà, on ajoute un suffixe _2, _3...

    Returns:
        Le NOM RELATIF du logo dans le dossier user (ex: 'harcour.png').
        À stocker dans `asset_path` du calque template.

    Raises:
        FileNotFoundError si le fichier source n'existe pas.
    """
    src = Path(source_path)
    if not src.is_file():
        raise FileNotFoundError(f"Logo introuvable : {source_path}")

    target_name = slugify_filename(src.name)
    user_dir = get_user_logos_dir()
    target = user_dir / target_name

    # Si le fichier existe déjà avec exactement la même taille → on ne recopie pas
    if target.exists() and target.stat().st_size == src.stat().st_size:
        return target_name

    if target.exists() and not overwrite:
        # Trouver un nom unique : nom_2.png, nom_3.png, etc.
        stem, ext = target.stem, target.suffix
        i = 2
        while (user_dir / f"{stem}_{i}{ext}").exists():
            i += 1
        target = user_dir / f"{stem}_{i}{ext}"
        target_name = target.name

    shutil.copy2(src, target)
    return target_name


def get_logo_path(name: str) -> Path | None:
    """Retourne le chemin absolu d'un logo, ou None s'il n'existe pas."""
    if not name:
        return None
    p = get_user_logos_dir() / name
    return p if p.is_file() else None


def list_logos() -> list[str]:
    """Liste tous les logos dispo (par nom)."""
    user_dir = get_user_logos_dir()
    return sorted([p.name for p in user_dir.iterdir() if p.is_file()])


def is_managed_logo_name(value: str) -> bool:
    """True si la valeur ressemble à un nom relatif géré par notre store
    (juste un nom de fichier, pas un chemin absolu)."""
    if not value:
        return False
    return "/" not in value and "\\" not in value and ":" not in value
