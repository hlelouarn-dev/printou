"""Dispatch des tirages vers les hotfolders DNP."""

from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path


def dispatch_to_hotfolder(
    rendered_jpeg: Path,
    hotfolder: Path,
    base_name: str,
    quantity: int,
) -> list[Path]:
    """Copie le JPEG rendu N fois dans le hotfolder DNP avec suffixes _2, _3..."""
    if not rendered_jpeg.exists():
        raise FileNotFoundError(f"Fichier rendu introuvable : {rendered_jpeg}")

    hotfolder.mkdir(parents=True, exist_ok=True)
    extension = rendered_jpeg.suffix
    created: list[Path] = []

    for i in range(quantity):
        if i == 0:
            target_name = f"{base_name}{extension}"
        else:
            target_name = f"{base_name}_{i + 1}{extension}"
        target = hotfolder / target_name

        if target.exists():
            ts = datetime.now().strftime("%H%M%S")
            target = hotfolder / f"{base_name}_{ts}_{i + 1}{extension}"

        shutil.copy2(rendered_jpeg, target)
        created.append(target)

    return created
