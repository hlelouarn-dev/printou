"""
Module de mise à jour de Printou via fichier ZIP local.

Comment ça marche :
1. L'utilisateur sélectionne un fichier ZIP (printou_v0.X.zip)
2. On lit la version cible dans le ZIP (printou/__init__.py)
3. On compare avec la version actuelle
4. Si OK : on extrait le ZIP dans un dossier temporaire à côté de l'install
5. On génère un script update.bat qui :
   - attend que Printou se ferme
   - sauvegarde l'ancien dossier (rollback en cas d'erreur)
   - copie les nouveaux fichiers
   - vide le __pycache__
   - relance Printou
6. On lance le .bat et on ferme Printou

Le venv et la config (sous APPDATA/Printou/) ne sont jamais touchés.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path


@dataclass
class UpdateInfo:
    """Info sur un ZIP de mise à jour."""
    zip_path: Path
    target_version: str
    current_version: str
    extracted_root: Path  # dossier qui contient printou/, tests/, etc.

    @property
    def is_newer(self) -> bool:
        return _version_tuple(self.target_version) > _version_tuple(self.current_version)

    @property
    def is_same(self) -> bool:
        return _version_tuple(self.target_version) == _version_tuple(self.current_version)


class UpdateError(Exception):
    pass


def _version_tuple(version: str) -> tuple[int, ...]:
    """Convertit '0.5.1' en (0, 5, 1) pour comparaison.

    Pad avec des zéros à droite pour que '0.6' soit égal à '0.6.0'
    (en Python pur, (0, 6) < (0, 6, 0) ce qui est faux sémantiquement).
    """
    parts = re.findall(r"\d+", version)
    if not parts:
        return (0, 0, 0)
    nums = [int(p) for p in parts]
    while len(nums) < 3:
        nums.append(0)
    return tuple(nums)


def is_frozen() -> bool:
    """v0.10 : True si on tourne depuis un .exe PyInstaller (mode 'frozen')."""
    return getattr(sys, "frozen", False)


def get_install_root() -> Path:
    """Retourne la racine de l'installation Printou.

    En mode SOURCE (lancé via `python -m printou`) :
        racine = dossier qui contient le dossier `printou/`
        (3 niveaux au-dessus de `printou/core/updater.py`)

    En mode FROZEN (.exe PyInstaller) :
        racine = dossier qui contient `Printou.exe`
        (typiquement `dist/Printou/`)
    """
    if is_frozen():
        # sys.executable = chemin du Printou.exe
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent.parent


def inspect_zip(zip_path: Path, current_version: str) -> UpdateInfo:
    """Extrait le ZIP dans un dossier temporaire et lit la version cible.

    v0.10 : gère 2 formats de ZIP :
        - SOURCE : contient un dossier 'printou/' avec '__init__.py' (version dedans)
        - FROZEN : contient 'Printou.exe' à la racine (ou dans un sous-dossier).
                   La version est tirée du nom du ZIP (Printou_v0.10.0_windows.zip)
                   ou du fichier _internal/printou/__init__.py si présent.
    """
    if not zip_path.exists():
        raise UpdateError(f"Fichier introuvable : {zip_path}")
    if not zipfile.is_zipfile(zip_path):
        raise UpdateError(f"Ce n'est pas un fichier ZIP valide : {zip_path}")

    extract_dir = Path(tempfile.mkdtemp(prefix="printou_update_"))
    try:
        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(extract_dir)
    except Exception as e:
        shutil.rmtree(extract_dir, ignore_errors=True)
        raise UpdateError(f"Échec d'extraction du ZIP : {e}")

    # Tenter d'abord le mode FROZEN (cherche Printou.exe)
    frozen_root = _find_frozen_root(extract_dir)
    if frozen_root is not None:
        # On a un ZIP windows
        target_version = _read_version_from_frozen(frozen_root, zip_path.name)
        if target_version is None:
            shutil.rmtree(extract_dir, ignore_errors=True)
            raise UpdateError(
                "Impossible de déterminer la version dans le ZIP windows. "
                "Le nom du ZIP doit contenir un numéro (ex: Printou_v0.10.0_windows.zip)."
            )
        return UpdateInfo(
            zip_path=zip_path,
            target_version=target_version,
            current_version=current_version,
            extracted_root=frozen_root,
        )

    # Sinon, mode SOURCE
    source_root = _find_printou_root(extract_dir)
    if source_root is None:
        shutil.rmtree(extract_dir, ignore_errors=True)
        raise UpdateError(
            "Le ZIP ne contient ni 'Printou.exe' ni 'printou/__init__.py'. "
            "Vérifie qu'il s'agit bien d'un ZIP de mise à jour Printou.",
        )

    init_file = source_root / "printou" / "__init__.py"
    target_version = _read_version(init_file)
    if target_version is None:
        shutil.rmtree(extract_dir, ignore_errors=True)
        raise UpdateError("Impossible de lire la version dans le ZIP source.")

    return UpdateInfo(
        zip_path=zip_path,
        target_version=target_version,
        current_version=current_version,
        extracted_root=source_root,
    )


def _find_printou_root(extract_dir: Path) -> Path | None:
    """Trouve le dossier qui contient printou/__init__.py (mode source)."""
    if (extract_dir / "printou" / "__init__.py").exists():
        return extract_dir
    for child in extract_dir.iterdir():
        if child.is_dir() and (child / "printou" / "__init__.py").exists():
            return child
    return None


def _find_frozen_root(extract_dir: Path) -> Path | None:
    """v0.10 : trouve le dossier qui contient Printou.exe (mode frozen)."""
    if (extract_dir / "Printou.exe").exists():
        return extract_dir
    for child in extract_dir.iterdir():
        if child.is_dir() and (child / "Printou.exe").exists():
            return child
    return None


def _read_version_from_frozen(frozen_root: Path, zip_filename: str) -> str | None:
    """v0.10 : essaie plusieurs sources pour la version d'un build frozen.

    Ordre :
    1. _internal/printou/__init__.py (PyInstaller place les .py compilés ici)
    2. Le nom du ZIP (Printou_v0.10.0_windows.zip → 0.10.0)
    """
    # 1. Tenter _internal/printou/__init__.py
    init_file = frozen_root / "_internal" / "printou" / "__init__.py"
    if init_file.exists():
        v = _read_version(init_file)
        if v is not None:
            return v

    # 2. Extraire du nom de fichier
    m = re.search(r"v?(\d+\.\d+(?:\.\d+)?)", zip_filename)
    if m:
        return m.group(1)
    return None


def _read_version(init_file: Path) -> str | None:
    """Extrait __version__ d'un fichier __init__.py."""
    try:
        content = init_file.read_text(encoding="utf-8")
        match = re.search(r'__version__\s*=\s*["\']([^"\']+)["\']', content)
        if match:
            return match.group(1)
    except Exception:
        pass
    return None


def install_update(info: UpdateInfo) -> Path:
    """Génère le script update.bat et le lance. Retourne le chemin du bat.

    Le bat attend que Printou se ferme, puis fait la mise à jour, puis relance Printou.
    L'app appelante doit se fermer juste après cet appel.

    v0.10 : gère 2 modes :
        - SOURCE : remplace le code Python source dans printou/, tests/, etc.
                   Relance via `python -m printou` depuis le venv.
        - FROZEN : remplace tout le contenu du dossier de l'exe (dist/Printou/).
                   Relance via Printou.exe.
    """
    install_root = get_install_root()
    bat_path = install_root.parent / "printou_update.bat"
    pid = os.getpid()

    if is_frozen():
        # Mode FROZEN : on remplace TOUT le contenu de l'install_root
        # (qui est dist/Printou/) par le contenu de extracted_root du ZIP windows
        # Le ZIP windows contient directement le contenu de dist/Printou/, pas un wrapper printou/
        bat_content = _generate_update_bat_frozen(
            pid=pid,
            install_root=install_root,
            extracted_root=info.extracted_root,
            current_version=info.current_version,
            target_version=info.target_version,
        )
    else:
        # Mode SOURCE
        target_subfolders = ["printou", "tests", "build_tools", "templates"]
        bat_content = _generate_update_bat_source(
            pid=pid,
            install_root=install_root,
            extracted_root=info.extracted_root,
            target_subfolders=target_subfolders,
            current_version=info.current_version,
            target_version=info.target_version,
        )

    bat_path.write_text(bat_content, encoding="cp1252", errors="replace")

    flags = 0
    if sys.platform == "win32":
        flags = subprocess.CREATE_NEW_PROCESS_GROUP | 0x00000008  # DETACHED_PROCESS

    subprocess.Popen(
        ["cmd.exe", "/c", "start", "", str(bat_path)],
        creationflags=flags,
        close_fds=True,
        cwd=str(install_root.parent),  # parent pour que le bat puisse modifier install_root
    )

    return bat_path


def _generate_update_bat_source(
    pid: int,
    install_root: Path,
    extracted_root: Path,
    target_subfolders: list[str],
    current_version: str,
    target_version: str,
) -> str:
    """Script de MAJ pour le mode SOURCE (remplace les .py dans l'install)."""

    # Liste des commandes pour copier les sous-dossiers
    copy_commands = []
    for folder in target_subfolders:
        src = extracted_root / folder
        if src.exists():
            dst = install_root / folder
            copy_commands.append(f'echo   - {folder}/')
            copy_commands.append(f'if exist "{dst}" rmdir /s /q "{dst}"')
            copy_commands.append(f'xcopy /E /I /Y /Q "{src}" "{dst}" >nul')

    # Fichiers à la racine à copier (s'ils existent dans le zip)
    root_files = [
        "Compiler.bat", "Lancer.bat", "INSTALL.md", "README.md",
        "CHANGELOG.md", "requirements.txt",
    ]
    for fname in root_files:
        src = extracted_root / fname
        if src.exists():
            dst = install_root / fname
            copy_commands.append(f'echo   - {fname}')
            copy_commands.append(f'copy /Y "{src}" "{dst}" >nul')

    copy_block = "\n".join(copy_commands)

    return f"""@echo off
chcp 1252 >nul
setlocal

echo ============================================================
echo    Mise a jour Printou
echo    {current_version} -^> {target_version}
echo ============================================================
echo.

REM 1. Attendre que Printou (PID {pid}) se ferme
echo [1/4] Attente de la fermeture de Printou...
:wait_loop
tasklist /FI "PID eq {pid}" 2>nul | find "{pid}" >nul
if not errorlevel 1 (
    timeout /t 1 /nobreak >nul
    goto wait_loop
)
echo  Printou ferme.
echo.

REM 2. Sauvegarde de securite (au cas ou)
echo [2/4] Sauvegarde de securite...
set "BACKUP=%TEMP%\\printou_backup_{current_version}"
if exist "%BACKUP%" rmdir /s /q "%BACKUP%"
mkdir "%BACKUP%"
xcopy /E /I /Y /Q "{install_root}\\printou" "%BACKUP%\\printou" >nul 2>&1
echo  Sauvegarde dans %BACKUP%
echo.

REM 3. Copie des nouveaux fichiers
echo [3/4] Copie des nouveaux fichiers...
{copy_block}
echo.

REM 4. Nettoyage cache Python
echo [4/4] Nettoyage du cache Python...
for /d /r "{install_root}" %%d in (__pycache__) do (
    if exist "%%d" rmdir /s /q "%%d"
)
echo.

echo ============================================================
echo    Mise a jour terminee : {target_version}
echo ============================================================
echo.
echo Relancement de Printou...
timeout /t 2 /nobreak >nul

REM Essayer plusieurs emplacements pour le venv
set "INSTALL_DIR={install_root}"
set "PY_EXE="

if exist "%INSTALL_DIR%\\.venv\\Scripts\\python.exe" (
    set "PY_EXE=%INSTALL_DIR%\\.venv\\Scripts\\python.exe"
    echo  venv trouve : %INSTALL_DIR%\\.venv
) else if exist "%INSTALL_DIR%\\venv\\Scripts\\python.exe" (
    set "PY_EXE=%INSTALL_DIR%\\venv\\Scripts\\python.exe"
    echo  venv trouve : %INSTALL_DIR%\\venv
) else if exist "%INSTALL_DIR%\\env\\Scripts\\python.exe" (
    set "PY_EXE=%INSTALL_DIR%\\env\\Scripts\\python.exe"
    echo  venv trouve : %INSTALL_DIR%\\env
)

if defined PY_EXE (
    echo  Lancement de Printou via %PY_EXE%...
    cd /d "%INSTALL_DIR%"
    start "" "%PY_EXE%" -m printou
    echo.
    echo  Printou lance ! Cette fenetre va se fermer.
    timeout /t 3 /nobreak >nul
) else (
    echo.
    echo ATTENTION : venv introuvable dans :
    echo   - %INSTALL_DIR%\\.venv
    echo   - %INSTALL_DIR%\\venv
    echo   - %INSTALL_DIR%\\env
    echo.
    echo La mise a jour a bien ete installee, mais tu dois lancer Printou
    echo manuellement. Ouvre une invite de commandes et tape :
    echo.
    echo   cd %INSTALL_DIR%
    echo   .venv\\Scripts\\activate
    echo   python -m printou
    echo.
    pause
)

REM Auto-suppression de ce script
(goto) 2>nul & del "%~f0"
"""


def _generate_update_bat_frozen(
    pid: int,
    install_root: Path,
    extracted_root: Path,
    current_version: str,
    target_version: str,
) -> str:
    """Script de MAJ pour le mode FROZEN (.exe PyInstaller).

    Stratégie : on ne peut pas remplacer Printou.exe pendant qu'il tourne.
    Donc on attend qu'il se ferme, puis on remplace l'INTÉGRALITÉ du contenu
    de install_root (= dist/Printou/) par le contenu du extracted_root.

    On ne sauvegarde QUE l'exe et le dossier _internal (le runtime), pas
    les éventuels fichiers utilisateur (qui ne devraient pas être ici de toute façon).
    """
    # Lister les sources : on liste tout ce qui est à la racine du extracted_root,
    # car le ZIP windows contient directement le contenu du dossier dist/Printou/
    return f"""@echo off
chcp 1252 >nul
setlocal

echo ============================================================
echo    Mise a jour Printou (mode .exe)
echo    {current_version} -^> {target_version}
echo ============================================================
echo.

REM 1. Attendre que Printou (PID {pid}) se ferme
echo [1/4] Attente de la fermeture de Printou...
:wait_loop
tasklist /FI "PID eq {pid}" 2>nul | find "{pid}" >nul
if not errorlevel 1 (
    timeout /t 1 /nobreak >nul
    goto wait_loop
)
REM Marge supplementaire : Windows libere parfois les fichiers avec un peu de retard
timeout /t 2 /nobreak >nul
echo  Printou ferme.
echo.

set "INSTALL_DIR={install_root}"
set "BACKUP=%TEMP%\\printou_exe_backup_{current_version}"
set "EXTRACTED={extracted_root}"

REM 2. Sauvegarde de securite
echo [2/4] Sauvegarde de securite...
if exist "%BACKUP%" rmdir /s /q "%BACKUP%"
mkdir "%BACKUP%"
xcopy /E /I /Y /Q "%INSTALL_DIR%" "%BACKUP%" >nul 2>&1
echo  Sauvegarde dans %BACKUP%
echo.

REM 3. Remplacement complet du contenu de l'install
echo [3/4] Remplacement du contenu de %INSTALL_DIR%...
REM On supprime tout sauf les fichiers users s'il y en a (ne devrait pas)
del /f /q "%INSTALL_DIR%\\*" >nul 2>&1
for /d %%d in ("%INSTALL_DIR%\\*") do rmdir /s /q "%%d"
REM Copier le nouveau contenu
xcopy /E /I /Y /Q "%EXTRACTED%\\*" "%INSTALL_DIR%" >nul
if errorlevel 1 (
    echo.
    echo ERREUR pendant la copie. Restauration depuis le backup...
    xcopy /E /I /Y /Q "%BACKUP%\\*" "%INSTALL_DIR%" >nul
    echo Sauvegarde restauree. La mise a jour a echoue.
    pause
    exit /b 1
)
echo  Copie terminee.
echo.

REM 4. Relancement
echo [4/4] Relancement de Printou...
timeout /t 1 /nobreak >nul

if exist "%INSTALL_DIR%\\Printou.exe" (
    cd /d "%INSTALL_DIR%"
    start "" "%INSTALL_DIR%\\Printou.exe"
    echo Printou v{target_version} lance !
    timeout /t 3 /nobreak >nul
) else (
    echo.
    echo ATTENTION : Printou.exe introuvable apres mise a jour.
    echo Verifie le dossier %INSTALL_DIR%
    echo.
    pause
)

REM Auto-suppression de ce script
(goto) 2>nul & del "%~f0"
"""


def cleanup_extraction_dir(info: UpdateInfo):
    """À appeler en cas d'annulation pour nettoyer le dossier temporaire."""
    if info.extracted_root.parent.name.startswith("printou_update_"):
        shutil.rmtree(info.extracted_root.parent, ignore_errors=True)
