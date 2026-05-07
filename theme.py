"""
Services métier de Printou.

Le service `TirageService` orchestre tout le pipeline d'un tirage validé :
1. Rendre l'image finale
2. Dispatcher dans le bon hotfolder DNP
3. Enregistrer en base SQLite
4. Marquer la photo comme traitée

C'est ce que l'UI appelle quand l'opérateur clique "Imprimer".
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from .commande import Commande, PhotoSource
from .config import AppConfig
from .database import Database
from .engine import RenderRequest, render_and_save
from .hotfolder import dispatch_to_hotfolder
from .photo_ops import Adjustments, Geometry
from .template import FORMATS, PrintFormat, Template


class HotfolderNotConfiguredError(RuntimeError):
    """Levée quand on tente d'imprimer dans un format sans hotfolder configuré."""


@dataclass
class TirageResult:
    """Résultat d'une impression."""
    fichiers_dispatches: list[Path]
    rendered_path: Path
    db_tirage_id: int


@dataclass
class TirageMultiResult:
    """Résultat d'une impression multi-formats (tous les formats d'une photo)."""
    par_format: dict[str, TirageResult]
    erreurs: dict[str, str]  # format_code → message d'erreur


class TirageService:
    """Service principal d'impression."""

    def __init__(self, config: AppConfig, db: Database):
        self.config = config
        self.db = db

    def _resolve_logos_dir(self) -> Path:
        """v0.9 : retourne le dossier de logos, en priorisant le store user."""
        from .config import get_user_logos_dir
        if self.config.logos_dir:
            p = Path(self.config.logos_dir)
            if p.is_dir():
                return p
        return get_user_logos_dir()

    def imprimer_tirage(
        self,
        commande: Commande,
        photo: PhotoSource,
        format_code: str,
        quantity: int,
        template: Template,
        geometry: Geometry,
        adjustments: Adjustments,
    ) -> TirageResult:
        """Pipeline complet pour imprimer N exemplaires d'une photo dans un format."""

        # 1. Vérifier que le hotfolder est configuré
        hotfolder = self.config.get_hotfolder(format_code)
        if hotfolder is None or not hotfolder.is_dir():
            raise HotfolderNotConfiguredError(
                f"Hotfolder DNP non configuré pour le format {format_code}. "
                f"Configurez-le dans Paramètres > Hotfolders."
            )

        # 2. Rendre l'image finale
        cache_dir = Path(self.config.rendered_cache_dir or "rendered_cache")
        cache_dir.mkdir(parents=True, exist_ok=True)
        rendered_path = cache_dir / f"{commande.name}_{photo.base_name}_{format_code}.jpg"

        event_info = self.config.get_event_info()
        placeholders = {
            "event_name": event_info.name,
            "event_location": event_info.location,
            "event_date": event_info.date,
            "client_name": commande.display_name,
        }

        req = RenderRequest(
            photo_path=photo.representative_file,
            template=template,
            print_format=FORMATS[format_code],
            geometry=geometry,
            adjustments=adjustments,
            placeholders=placeholders,
            logos_dir=self._resolve_logos_dir(),
            strict_orientation=True,
        )
        render_and_save(req, rendered_path)

        # 3. Dispatch vers le hotfolder DNP
        dispatched = dispatch_to_hotfolder(
            rendered_path, hotfolder, photo.base_name, quantity,
        )

        # 4. Enregistrer en base
        commande_id = self.db.create_or_get_commande(
            commande.name, commande.folder, commande.display_name,
        )
        photo_id = self.db.record_photo(
            commande_id, photo.base_name, photo.representative_file,
            geometry.to_dict(), adjustments.to_dict(),
        )
        tirage_id = self.db.record_tirage(
            photo_id, format_code, quantity, template.name,
            rendered_path, hotfolder, dispatched,
        )

        return TirageResult(
            fichiers_dispatches=dispatched,
            rendered_path=rendered_path,
            db_tirage_id=tirage_id,
        )

    def imprimer_tous_formats(
        self,
        commande: Commande,
        photo: PhotoSource,
        template: Template,
        geometry: Geometry,
        adjustments: Adjustments,
    ) -> TirageMultiResult:
        """Imprime TOUS les formats demandés pour cette photo, en une fois.

        Pour chaque format dans photo.tirages :
        - Si DNP (15x23, 20x30) → dispatch hotfolder
        - Si A3/A2 → export JPEG dans le dossier exports
        - Erreurs collectées séparément (un format peut échouer sans bloquer les autres)
        """
        result = TirageMultiResult(par_format={}, erreurs={})

        for format_code, tirage_demande in photo.tirages.items():
            try:
                if format_code in ("A3", "A2"):
                    output = self.exporter_jpeg(
                        commande, photo, template, geometry, adjustments,
                        format_code=format_code,
                    )
                    result.par_format[format_code] = TirageResult(
                        fichiers_dispatches=[output],
                        rendered_path=output,
                        db_tirage_id=0,
                    )
                else:
                    result.par_format[format_code] = self.imprimer_tirage(
                        commande, photo, format_code, tirage_demande.quantity,
                        template, geometry, adjustments,
                    )
            except Exception as e:
                result.erreurs[format_code] = str(e)

        return result

    def exporter_jpeg(
        self,
        commande: Commande,
        photo: PhotoSource,
        template: Template,
        geometry: Geometry,
        adjustments: Adjustments,
        format_code: str = "A3",
    ) -> Path:
        """Exporte le JPEG final dans le dossier exports (pour Canon A3/A2)."""
        exports = Path(self.config.exports_dir or "exports")
        exports.mkdir(parents=True, exist_ok=True)
        output_path = exports / f"{commande.name}_{photo.base_name}_{format_code}.jpg"

        event_info = self.config.get_event_info()
        placeholders = {
            "event_name": event_info.name,
            "event_location": event_info.location,
            "event_date": event_info.date,
            "client_name": commande.display_name,
        }

        req = RenderRequest(
            photo_path=photo.representative_file,
            template=template,
            print_format=FORMATS[format_code],
            geometry=geometry,
            adjustments=adjustments,
            placeholders=placeholders,
            logos_dir=self._resolve_logos_dir(),
            strict_orientation=True,
        )
        render_and_save(req, output_path)

        # Trace en DB aussi
        commande_id = self.db.create_or_get_commande(
            commande.name, commande.folder, commande.display_name,
        )
        photo_id = self.db.record_photo(
            commande_id, photo.base_name, photo.representative_file,
            geometry.to_dict(), adjustments.to_dict(),
        )
        self.db.record_tirage(
            photo_id, format_code, 1, template.name,
            output_path, exports, [output_path],
        )

        return output_path

    def deplacer_commande_traitee(self, commande: Commande) -> Path | None:
        """Déplace la commande traitée vers le dossier `commandes_traitees`."""
        traitees_dir = Path(self.config.commandes_traitees or "")
        if not traitees_dir or not traitees_dir.is_dir():
            return None

        target = traitees_dir / commande.folder.name
        # Si déjà existant, ajouter un suffixe
        if target.exists():
            from datetime import datetime
            target = traitees_dir / f"{commande.folder.name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

        shutil.move(str(commande.folder), str(target))

        # Marquer en DB
        commande_id = self.db.create_or_get_commande(
            commande.name, commande.folder, commande.display_name,
        )
        self.db.mark_commande_traitee(commande_id)

        return target
