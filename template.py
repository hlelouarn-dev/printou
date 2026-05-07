"""
Configuration de l'application Printou.

Stockée dans %APPDATA%\\Printou\\config.json (Windows) ou ~/.config/printou/config.json
Persistée entre les lancements.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path


def get_app_data_dir() -> Path:
    """Retourne le dossier de données utilisateur (config, base SQLite, logs)."""
    if sys.platform == "win32":
        base = os.environ.get("APPDATA")
        if base:
            return Path(base) / "Printou"
    elif sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Printou"
    return Path.home() / ".config" / "printou"


def get_user_templates_dir() -> Path:
    """v0.8 : dossier user des templates (préservé entre les MAJ).

    À la différence de templates/ dans l'install, ce dossier n'est jamais
    écrasé lors d'une mise à jour de Printou.
    """
    d = get_app_data_dir() / "templates"
    d.mkdir(parents=True, exist_ok=True)
    return d


def get_user_logos_dir() -> Path:
    """v0.9 : dossier des logos référencés par les templates.

    Quand un logo est ajouté à un template, son fichier est copié ici sous un
    nom stable (slug du nom de fichier original). Le template référence
    seulement le nom relatif (ex: 'harcour.png'), pas un chemin absolu, pour
    rester portable entre PCs.
    """
    d = get_app_data_dir() / "logos"
    d.mkdir(parents=True, exist_ok=True)
    return d


def migrate_bundled_templates_if_needed(bundled_templates_dir: Path) -> int:
    """v0.8 : copie les templates fournis avec l'app vers le dossier user (1 seule fois).

    Les templates user existants ne sont JAMAIS écrasés. Si un fichier
    homonyme existe déjà côté user, on ne copie pas. Retourne le nombre copié.
    """
    user_dir = get_user_templates_dir()
    if not bundled_templates_dir.is_dir():
        return 0
    import shutil
    nb_copied = 0
    for src in bundled_templates_dir.glob("*.json"):
        dst = user_dir / src.name
        if not dst.exists():
            try:
                shutil.copy2(src, dst)
                nb_copied += 1
            except Exception:
                pass
    return nb_copied


@dataclass
class HotfolderConfig:
    """Configuration d'un hotfolder DNP par format."""
    format_code: str    # "15x23", "20x30", ...
    path: str           # chemin absolu du dossier surveillé par DNP

    def is_valid(self) -> bool:
        return bool(self.path) and Path(self.path).is_dir()


@dataclass
class EventInfo:
    """Métadonnées de l'événement en cours (utilisées dans les placeholders du template)."""
    name: str = ""          # ex: "10° ÉDITION DU CONCOURS HARCOUR"
    location: str = ""      # ex: "LE MANS"
    date: str = ""          # ex: "AVRIL 2026"


@dataclass
class AppConfig:
    """Configuration complète de l'app."""
    # Dossier racine surveillé pour les commandes entrantes
    commandes_root: str = ""

    # Dossier où déplacer les commandes traitées
    commandes_traitees: str = ""

    # Bibliothèque de logos (PNG)
    logos_dir: str = ""

    # Bibliothèque de templates JSON
    templates_dir: str = ""

    # Dossier "exports" pour les A3/A2 (Canon, sans hotfolder)
    exports_dir: str = ""

    # Dossier où Printou stocke les rendus intermédiaires (cache temporaire)
    rendered_cache_dir: str = ""

    # Hotfolders DNP par format
    hotfolders: list[dict] = field(default_factory=list)

    # Template actif par défaut (nom du fichier sans extension)
    default_template_paysage: str = ""
    default_template_portrait: str = ""

    # Métadonnées de l'événement actuel
    event: dict = field(default_factory=lambda: asdict(EventInfo()))

    # Préférences d'interface
    ui_theme: str = "dark"
    ui_language: str = "fr"

    @classmethod
    def load(cls, path: Path | None = None) -> "AppConfig":
        if path is None:
            path = get_app_data_dir() / "config.json"
        if not path.exists():
            return cls()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            # On filtre les clés inconnues pour rester rétro-compatible
            valid_keys = set(cls.__dataclass_fields__.keys())
            data = {k: v for k, v in data.items() if k in valid_keys}
            return cls(**data)
        except (json.JSONDecodeError, TypeError, ValueError):
            return cls()

    def save(self, path: Path | None = None) -> None:
        if path is None:
            path = get_app_data_dir() / "config.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(asdict(self), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def get_hotfolder(self, format_code: str) -> Path | None:
        for hf in self.hotfolders:
            if hf.get("format_code") == format_code:
                p = hf.get("path", "")
                if p:
                    return Path(p)
        return None

    def set_hotfolder(self, format_code: str, path: str) -> None:
        for hf in self.hotfolders:
            if hf.get("format_code") == format_code:
                hf["path"] = path
                return
        self.hotfolders.append({"format_code": format_code, "path": path})

    def is_configured(self) -> bool:
        """L'app est-elle prête à fonctionner ?"""
        return (
            bool(self.commandes_root) and Path(self.commandes_root).is_dir()
            and any(hf.get("path") for hf in self.hotfolders)
        )

    def get_event_info(self) -> EventInfo:
        return EventInfo(**self.event)

    def set_event_info(self, info: EventInfo) -> None:
        self.event = asdict(info)
