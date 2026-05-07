"""
Service de gestion des templates utilisateur (v0.8).

Stockage : APPDATA/Printou/templates/<slug>.json (1 fichier JSON par template).

Fonctions principales :
- list_user_templates() : liste tous les templates de l'utilisateur
- save_template(template, original_name=None) : sauvegarde (gère le renommage)
- delete_template(name) : supprime un template
- duplicate_template(src_name, new_name) : crée une copie
- export_to_zip(target=None) : génère un ZIP de tous les templates
- import_from_zip(path, overwrite=False) : extrait un ZIP de templates
"""

from __future__ import annotations

import re
import shutil
import tempfile
import unicodedata
import zipfile
from datetime import datetime
from pathlib import Path

from .config import get_user_templates_dir, get_user_logos_dir
from .template import Template


def slugify(text: str) -> str:
    """'Le Mans 2026 - Paysage' → 'le_mans_2026_paysage' (pour nom de fichier)."""
    text = unicodedata.normalize("NFD", text)
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    text = re.sub(r"[^a-zA-Z0-9]+", "_", text).lower().strip("_")
    return text or "template"


def get_template_path(template_name: str) -> Path:
    """Chemin du fichier JSON pour un template donné."""
    return get_user_templates_dir() / f"{slugify(template_name)}.json"


def list_user_templates() -> list[Template]:
    """Charge tous les templates user, triés par nom."""
    user_dir = get_user_templates_dir()
    templates = []
    for path in sorted(user_dir.glob("*.json")):
        try:
            templates.append(Template.from_json(path))
        except Exception as e:
            print(f"[Printou] Erreur chargement template {path.name} : {e}")
    return templates


def save_template(template: Template, original_name: str | None = None) -> Path:
    """Sauvegarde un template. Si renommé (original_name != template.name), supprime l'ancien."""
    target = get_template_path(template.name)
    if original_name and slugify(original_name) != slugify(template.name):
        old_path = get_template_path(original_name)
        if old_path.exists():
            try:
                old_path.unlink()
            except OSError:
                pass
    target.parent.mkdir(parents=True, exist_ok=True)
    template.to_json(target)
    return target


def delete_template(template_name: str) -> bool:
    """Supprime un template. Retourne True si réussi."""
    path = get_template_path(template_name)
    if path.exists():
        try:
            path.unlink()
            return True
        except OSError:
            pass
    return False


def duplicate_template(source_name: str, new_name: str) -> Template | None:
    """Duplique un template avec un nouveau nom. Retourne le nouveau template ou None."""
    source_path = get_template_path(source_name)
    if not source_path.exists():
        return None
    try:
        tpl = Template.from_json(source_path)
        tpl.name = new_name
        save_template(tpl)
        return tpl
    except Exception:
        return None


def export_to_zip(target_zip: Path | None = None) -> Path:
    """Exporte tous les templates user dans un ZIP. Inclut aussi les logos
    référencés par les templates (v0.9 : portage entre PCs).

    Le ZIP a la structure :
        templates/<slug>.json
        logos/<logo_name>.png  (uniquement les logos effectivement utilisés)
    """
    user_dir = get_user_templates_dir()
    logos_dir = get_user_logos_dir()

    if target_zip is None:
        downloads = Path.home() / "Downloads"
        if not downloads.is_dir():
            downloads = Path.home()
        date_str = datetime.now().strftime("%Y-%m-%d")
        target_zip = downloads / f"printou_templates_{date_str}.zip"

    # Déterminer quels logos sont effectivement utilisés
    used_logos: set[str] = set()
    for tpl_path in user_dir.glob("*.json"):
        try:
            tpl = Template.from_json(tpl_path)
            for layer in tpl.layers:
                if layer.get("type") == "logo":
                    asset = layer.get("asset_path", "")
                    # Si c'est un nom relatif (pas un chemin absolu), c'est un logo géré
                    if asset and "/" not in asset and "\\" not in asset and ":" not in asset:
                        used_logos.add(asset)
        except Exception:
            pass

    with zipfile.ZipFile(target_zip, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        # Templates
        for path in user_dir.glob("*.json"):
            zf.write(path, arcname=f"templates/{path.name}")
        # Logos référencés
        for logo_name in used_logos:
            logo_path = logos_dir / logo_name
            if logo_path.is_file():
                zf.write(logo_path, arcname=f"logos/{logo_name}")

    return target_zip


def import_from_zip(zip_path: Path, overwrite: bool = False) -> dict:
    """Importe les templates ET les logos d'un ZIP vers les dossiers user.

    Returns:
        dict avec keys :
        - 'imported' (list) : noms des templates importés
        - 'skipped' (list) : templates ignorés (déjà présents et overwrite=False)
        - 'errors' (list)
        - 'logos_imported' (int) : nombre de logos copiés
    """
    result = {"imported": [], "skipped": [], "errors": [], "logos_imported": 0}

    if not zipfile.is_zipfile(zip_path):
        result["errors"].append(f"{zip_path.name} n'est pas un ZIP valide")
        return result

    user_dir = get_user_templates_dir()
    logos_dir = get_user_logos_dir()
    user_dir.mkdir(parents=True, exist_ok=True)
    logos_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        try:
            with zipfile.ZipFile(zip_path) as zf:
                zf.extractall(tmp_path)
        except Exception as e:
            result["errors"].append(f"Erreur extraction : {e}")
            return result

        # Templates JSON
        json_files = list(tmp_path.rglob("*.json"))
        for src in json_files:
            try:
                Template.from_json(src)
            except Exception as e:
                result["errors"].append(f"{src.name} : template invalide ({e})")
                continue
            dst = user_dir / src.name
            if dst.exists() and not overwrite:
                result["skipped"].append(src.name)
                continue
            try:
                shutil.copy2(src, dst)
                result["imported"].append(src.name)
            except Exception as e:
                result["errors"].append(f"{src.name} : copie impossible ({e})")

        # Logos (toutes les images du ZIP)
        for ext in ("*.png", "*.jpg", "*.jpeg", "*.gif", "*.bmp"):
            for src in tmp_path.rglob(ext):
                # Ne traiter que les fichiers du dossier "logos/" ou de la racine
                if "logos" in src.parts or src.parent == tmp_path:
                    dst = logos_dir / src.name
                    if dst.exists() and not overwrite:
                        continue  # silencieux pour les logos
                    try:
                        shutil.copy2(src, dst)
                        result["logos_imported"] += 1
                    except Exception as e:
                        result["errors"].append(f"logo {src.name} : {e}")

    return result


def get_default_template_for_orientation(orientation: str, templates: list[Template]) -> Template | None:
    """Premier template qui matche l'orientation, sinon le premier de la liste."""
    for t in templates:
        if t.orientation == orientation:
            return t
    return templates[0] if templates else None
