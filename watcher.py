"""
Module d'interrogation de l'API GitHub Releases.

Pas de dépendance externe : uniquement urllib (stdlib Python).

API utilisée :
    GET https://api.github.com/repos/{owner}/{repo}/releases/latest

Limite GitHub : 60 requêtes/heure sans authentification.
On ajoute un timeout court pour ne pas freezer l'app si pas d'internet.
"""

from __future__ import annotations

import json
import re
import shutil
import socket
import ssl
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .updater import _version_tuple


GITHUB_API_BASE = "https://api.github.com"
DEFAULT_TIMEOUT = 8  # secondes
USER_AGENT = "Printou-Updater"


class GitHubError(Exception):
    """Erreur lors de la communication avec GitHub."""


@dataclass
class GitHubRelease:
    tag_name: str          # "v0.6.0"
    version: str           # "0.6.0" (sans le v)
    name: str              # "Printou v0.6.0 - ..."
    body: str              # description markdown
    published_at: str      # ISO 8601
    zip_url: str | None    # URL de téléchargement directe du premier .zip attaché
    zip_filename: str | None
    zip_size: int          # taille en octets

    def is_newer_than(self, current: str) -> bool:
        return _version_tuple(self.version) > _version_tuple(current)

    def is_same_as(self, current: str) -> bool:
        return _version_tuple(self.version) == _version_tuple(current)


def fetch_latest_release(repo: str, timeout: int = DEFAULT_TIMEOUT,
                         prefer_frozen: bool = False) -> GitHubRelease:
    """Interroge l'API GitHub pour récupérer la dernière release publiée.

    Args:
        repo: au format "owner/name", par exemple "hlelouarn-dev/printou"
        timeout: secondes max d'attente
        prefer_frozen: v0.10 : si True, préfère un asset 'windows' (.exe pré-compilé)
                       au lieu du code source.

    Raises:
        GitHubError: en cas d'erreur réseau, repo inexistant, pas de release, etc.
    """
    if "/" not in repo:
        raise GitHubError(f"Format invalide pour repo : '{repo}' (attendu : 'owner/name')")

    url = f"{GITHUB_API_BASE}/repos/{repo}/releases/latest"
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/vnd.github+json",
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ssl.create_default_context()) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise GitHubError(
                f"Aucune release trouvée pour {repo}.\n"
                "Vérifie que le repository existe et qu'au moins une release est publiée."
            ) from e
        if e.code == 403:
            raise GitHubError(
                "GitHub a refusé la requête (limite de 60 requêtes/heure dépassée).\n"
                "Réessaye dans 1 heure ou utilise la mise à jour par fichier ZIP local."
            ) from e
        raise GitHubError(f"Erreur HTTP {e.code} de GitHub : {e.reason}") from e
    except urllib.error.URLError as e:
        raise GitHubError(
            f"Impossible de joindre GitHub. Vérifie ta connexion Internet.\nDétail : {e.reason}"
        ) from e
    except socket.timeout:
        raise GitHubError(
            "GitHub ne répond pas (délai dépassé). Réessaye plus tard."
        )
    except (ValueError, json.JSONDecodeError) as e:
        raise GitHubError(f"Réponse GitHub invalide : {e}") from e

    return _parse_release(data, prefer_frozen=prefer_frozen)


def _parse_release(data: dict, prefer_frozen: bool = False) -> GitHubRelease:
    """Parse une release GitHub.

    Args:
        prefer_frozen: si True, on préfère un asset dont le nom contient 'windows'
                       ou 'frozen' (cas du .exe pré-compilé). Sinon on prend
                       le premier .zip qui n'a pas ces marqueurs (= code source).
    """
    tag_name = data.get("tag_name", "")
    version_match = re.match(r"^v?(.+)$", tag_name)
    version = version_match.group(1) if version_match else tag_name

    assets = data.get("assets", [])

    # Catégoriser les assets
    windows_assets = []
    source_assets = []
    for asset in assets:
        name = (asset.get("name") or "").lower()
        if not name.endswith(".zip"):
            continue
        if "windows" in name or "frozen" in name or "_exe" in name:
            windows_assets.append(asset)
        else:
            source_assets.append(asset)

    # Choisir selon le mode
    chosen = None
    if prefer_frozen and windows_assets:
        chosen = windows_assets[0]
    elif not prefer_frozen and source_assets:
        chosen = source_assets[0]
    else:
        # Fallback : ce qu'on a sous la main
        chosen = (windows_assets + source_assets)[0] if (windows_assets or source_assets) else None

    zip_url = chosen.get("browser_download_url") if chosen else None
    zip_filename = chosen.get("name") if chosen else None
    zip_size = chosen.get("size", 0) if chosen else 0

    return GitHubRelease(
        tag_name=tag_name,
        version=version,
        name=data.get("name") or tag_name,
        body=data.get("body") or "",
        published_at=data.get("published_at", ""),
        zip_url=zip_url,
        zip_filename=zip_filename,
        zip_size=zip_size,
    )


def download_release_zip(
    release: GitHubRelease,
    target_dir: Path,
    progress_callback: Callable[[int, int], None] | None = None,
    timeout: int = 60,
) -> Path:
    """Télécharge le ZIP d'une release vers target_dir/zip_filename.

    Args:
        release: doit avoir un zip_url
        target_dir: dossier de destination (sera créé)
        progress_callback: fonction(bytes_lus, bytes_totaux) appelée régulièrement
        timeout: timeout par requête HTTP (le téléchargement peut être long)

    Returns:
        Le chemin du fichier ZIP téléchargé.
    """
    if not release.zip_url:
        raise GitHubError(
            f"La release {release.tag_name} ne contient aucun fichier ZIP attaché."
        )

    target_dir.mkdir(parents=True, exist_ok=True)
    target_path = target_dir / (release.zip_filename or "printou_release.zip")

    req = urllib.request.Request(
        release.zip_url,
        headers={"User-Agent": USER_AGENT},
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            total = release.zip_size or int(resp.headers.get("Content-Length", 0))
            chunk_size = 64 * 1024
            downloaded = 0

            with open(target_path, "wb") as f:
                while True:
                    chunk = resp.read(chunk_size)
                    if not chunk:
                        break
                    f.write(chunk)
                    downloaded += len(chunk)
                    if progress_callback:
                        progress_callback(downloaded, total)
    except urllib.error.URLError as e:
        # Nettoyer le fichier partiel
        if target_path.exists():
            try:
                target_path.unlink()
            except OSError:
                pass
        raise GitHubError(
            f"Échec du téléchargement : {e.reason}"
        ) from e
    except socket.timeout:
        if target_path.exists():
            try:
                target_path.unlink()
            except OSError:
                pass
        raise GitHubError("Téléchargement interrompu (timeout). Réessaye plus tard.")

    return target_path


def humanize_size(num_bytes: int) -> str:
    """Formate une taille en octets → '1.2 Mo' / '450 ko' etc."""
    if num_bytes < 1024:
        return f"{num_bytes} o"
    if num_bytes < 1024 * 1024:
        return f"{num_bytes / 1024:.0f} ko"
    return f"{num_bytes / (1024 * 1024):.1f} Mo"
