"""
Surveillance du dossier racine des commandes.

Utilise watchdog pour détecter en temps réel l'arrivée de nouveaux dossiers
clients. Pas bloquant : émet des callbacks.

Utilisation typique :
    watcher = CommandesWatcher(Path("C:/Printou/Commandes"))
    watcher.on_new_commande = lambda folder: print(f"Nouvelle commande : {folder}")
    watcher.start()
    # ... plus tard ...
    watcher.stop()
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Callable

try:
    from watchdog.events import FileSystemEventHandler
    from watchdog.observers import Observer
    WATCHDOG_AVAILABLE = True
except ImportError:
    WATCHDOG_AVAILABLE = False
    FileSystemEventHandler = object
    Observer = None


class _Handler(FileSystemEventHandler):
    def __init__(self, root: Path, on_new: Callable[[Path], None]):
        super().__init__()
        self.root = root
        self.on_new = on_new

    def on_created(self, event):
        if event.is_directory:
            path = Path(event.src_path)
            # Seulement les sous-dossiers DIRECTS de la racine
            if path.parent == self.root:
                # Petit délai pour que le dossier soit complet (les fichiers arrivent après)
                time.sleep(2.0)
                self.on_new(path)


class CommandesWatcher:
    """Surveille un dossier racine et notifie l'arrivée de nouvelles commandes."""

    def __init__(self, root: Path):
        self.root = root
        self.on_new_commande: Callable[[Path], None] = lambda p: None
        self._observer: Observer | None = None

    def start(self) -> bool:
        """Démarre la surveillance. Retourne True si OK."""
        if not WATCHDOG_AVAILABLE:
            print("[Printou] watchdog non installé : surveillance dossier indisponible")
            return False
        if not self.root.is_dir():
            print(f"[Printou] Dossier surveillé inexistant : {self.root}")
            return False

        handler = _Handler(self.root, lambda p: self.on_new_commande(p))
        self._observer = Observer()
        self._observer.schedule(handler, str(self.root), recursive=False)
        self._observer.start()
        return True

    def stop(self) -> None:
        if self._observer is not None:
            self._observer.stop()
            self._observer.join(timeout=2)
            self._observer = None

    def list_existing(self) -> list[Path]:
        """Liste les dossiers déjà présents dans le dossier racine."""
        if not self.root.is_dir():
            return []
        return sorted([p for p in self.root.iterdir() if p.is_dir()])
