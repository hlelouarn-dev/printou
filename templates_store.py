"""
Base SQLite pour l'historique des tirages.

Tables :
- commandes  : 1 ligne par dossier client traité
- photos     : 1 ligne par PhotoSource (= base_name unique dans la commande)
- tirages    : 1 ligne par fichier envoyé au hotfolder DNP

Permet : recherche, réimpression, détection doublons, statistiques.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


SCHEMA = """
CREATE TABLE IF NOT EXISTS commandes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nom_dossier TEXT NOT NULL,
    chemin_dossier TEXT NOT NULL,
    nom_client TEXT,
    date_creation TEXT NOT NULL,
    statut TEXT NOT NULL DEFAULT 'en_cours'
);

CREATE TABLE IF NOT EXISTS photos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    commande_id INTEGER NOT NULL,
    base_name TEXT NOT NULL,
    fichier_source TEXT NOT NULL,
    geometry_json TEXT,
    adjustments_json TEXT,
    date_traitement TEXT NOT NULL,
    FOREIGN KEY (commande_id) REFERENCES commandes(id)
);

CREATE TABLE IF NOT EXISTS tirages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    photo_id INTEGER NOT NULL,
    format_code TEXT NOT NULL,
    quantite INTEGER NOT NULL,
    template_name TEXT,
    fichier_rendu TEXT,
    hotfolder_cible TEXT,
    fichiers_dispatches_json TEXT,
    date_impression TEXT NOT NULL,
    FOREIGN KEY (photo_id) REFERENCES photos(id)
);

CREATE INDEX IF NOT EXISTS idx_commandes_nom ON commandes(nom_dossier);
CREATE INDEX IF NOT EXISTS idx_photos_commande ON photos(commande_id);
CREATE INDEX IF NOT EXISTS idx_tirages_photo ON tirages(photo_id);
"""


class Database:
    """Wrapper SQLite minimaliste pour l'historique."""

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as conn:
            conn.executescript(SCHEMA)

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    # ─── Commandes ───

    def create_or_get_commande(self, nom_dossier: str, chemin: Path, nom_client: str = "") -> int:
        """Crée une nouvelle entrée commande, ou retourne l'id existant."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT id FROM commandes WHERE nom_dossier = ? AND chemin_dossier = ?",
                (nom_dossier, str(chemin)),
            ).fetchone()
            if row:
                return row["id"]
            cur = conn.execute(
                "INSERT INTO commandes (nom_dossier, chemin_dossier, nom_client, date_creation, statut) "
                "VALUES (?, ?, ?, ?, 'en_cours')",
                (nom_dossier, str(chemin), nom_client, datetime.now().isoformat()),
            )
            return cur.lastrowid

    def mark_commande_traitee(self, commande_id: int) -> None:
        with self._conn() as conn:
            conn.execute(
                "UPDATE commandes SET statut = 'traitee' WHERE id = ?",
                (commande_id,),
            )

    def list_commandes(self, limit: int = 200) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM commandes ORDER BY date_creation DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]

    # ─── Photos ───

    def record_photo(
        self,
        commande_id: int,
        base_name: str,
        fichier_source: Path,
        geometry: dict,
        adjustments: dict,
    ) -> int:
        with self._conn() as conn:
            cur = conn.execute(
                "INSERT INTO photos (commande_id, base_name, fichier_source, "
                "geometry_json, adjustments_json, date_traitement) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    commande_id,
                    base_name,
                    str(fichier_source),
                    json.dumps(geometry),
                    json.dumps(adjustments),
                    datetime.now().isoformat(),
                ),
            )
            return cur.lastrowid

    # ─── Tirages ───

    def record_tirage(
        self,
        photo_id: int,
        format_code: str,
        quantite: int,
        template_name: str,
        fichier_rendu: Path,
        hotfolder_cible: Path,
        fichiers_dispatches: list[Path],
    ) -> int:
        with self._conn() as conn:
            cur = conn.execute(
                "INSERT INTO tirages (photo_id, format_code, quantite, template_name, "
                "fichier_rendu, hotfolder_cible, fichiers_dispatches_json, date_impression) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    photo_id,
                    format_code,
                    quantite,
                    template_name,
                    str(fichier_rendu),
                    str(hotfolder_cible),
                    json.dumps([str(p) for p in fichiers_dispatches]),
                    datetime.now().isoformat(),
                ),
            )
            return cur.lastrowid

    def list_tirages_for_commande(self, commande_id: int) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT t.*, p.base_name, p.fichier_source
                   FROM tirages t
                   JOIN photos p ON p.id = t.photo_id
                   WHERE p.commande_id = ?
                   ORDER BY t.date_impression DESC""",
                (commande_id,),
            ).fetchall()
            return [dict(r) for r in rows]

    def search_tirages(self, query: str, limit: int = 100) -> list[dict]:
        """Recherche dans nom_client, base_name, format."""
        with self._conn() as conn:
            like = f"%{query}%"
            rows = conn.execute(
                """SELECT t.*, p.base_name, p.fichier_source, c.nom_client, c.nom_dossier
                   FROM tirages t
                   JOIN photos p ON p.id = t.photo_id
                   JOIN commandes c ON c.id = p.commande_id
                   WHERE c.nom_client LIKE ? OR c.nom_dossier LIKE ?
                      OR p.base_name LIKE ? OR t.format_code LIKE ?
                   ORDER BY t.date_impression DESC LIMIT ?""",
                (like, like, like, like, limit),
            ).fetchall()
            return [dict(r) for r in rows]
