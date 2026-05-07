"""
Point d'entrée Printou.

Lancé par l'exécutable PyInstaller (Printou.exe sur Windows) ou directement avec
`python -m printou` depuis le dossier source.

Bootstrap :
1. Charge la configuration (config.json dans %APPDATA%\\Printou)
2. Initialise la base SQLite (printou.db dans %APPDATA%\\Printou)
3. Applique le thème
4. Lance la fenêtre principale
"""

from __future__ import annotations

import sys
import traceback
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import QApplication, QMessageBox

from printou import __app_name__, __version__
from printou.core import AppConfig, Database, get_app_data_dir
from printou.ui import MainWindow, apply_theme


def excepthook(exc_type, exc_value, exc_traceback):
    """Hook global pour capturer les crashs et afficher une boîte d'erreur."""
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc_value, exc_traceback)
        return
    msg = "".join(traceback.format_exception(exc_type, exc_value, exc_traceback))
    print(msg)
    try:
        QMessageBox.critical(None, "Erreur Printou",
                             f"Une erreur inattendue est survenue :\n\n{exc_value}\n\n"
                             f"Détails techniques :\n{msg[:2000]}")
    except Exception:
        pass


def main() -> int:
    sys.excepthook = excepthook

    # Note : AA_EnableHighDpiScaling / AA_UseHighDpiPixmaps sont activés par défaut
    # depuis Qt6. Plus besoin de les positionner explicitement.

    app = QApplication(sys.argv)
    app.setApplicationName(__app_name__)
    app.setApplicationVersion(__version__)
    app.setOrganizationName("Printou")

    # Icône applicative (cherchée à plusieurs endroits selon mode dev/prod)
    icon_path = _find_resource("printou.ico", "logo_printou.png")
    if icon_path:
        app.setWindowIcon(QIcon(str(icon_path)))

    apply_theme(app)

    # Setup data dir
    data_dir = get_app_data_dir()
    data_dir.mkdir(parents=True, exist_ok=True)

    # Config + DB
    config = AppConfig.load()
    db = Database(data_dir / "printou.db")

    # Fenêtre principale
    window = MainWindow(config, db)
    if icon_path:
        window.setWindowIcon(QIcon(str(icon_path)))
    window.show()

    return app.exec()


def _find_resource(*names: str) -> Path | None:
    """Cherche un fichier ressource (icône, etc.) dans plusieurs emplacements possibles."""
    candidates: list[Path] = []
    here = Path(__file__).parent.resolve()
    candidates.append(here / "assets")
    candidates.append(here.parent / "assets")
    # Mode PyInstaller (sys._MEIPASS)
    if getattr(sys, "frozen", False):
        meipass = Path(getattr(sys, "_MEIPASS", ""))
        if meipass.exists():
            candidates.append(meipass / "assets")
            candidates.append(meipass)
    # Dossier courant
    candidates.append(Path.cwd() / "assets")

    for d in candidates:
        for n in names:
            p = d / n
            if p.exists():
                return p
    return None


if __name__ == "__main__":
    sys.exit(main())
