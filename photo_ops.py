"""Cœur métier de Printou."""

from .commande import (
    Commande,
    PhotoSource,
    TirageDemande,
    detect_format_code,
    extract_base_name,
    scan_commande,
)
from .config import (
    AppConfig, EventInfo, HotfolderConfig,
    get_app_data_dir, get_user_logos_dir, get_user_templates_dir,
    migrate_bundled_templates_if_needed,
)
from .database import Database
from .engine import (
    OrientationMismatchError,
    RenderRequest,
    render,
    render_and_save,
    render_preview,
)
from .hotfolder import dispatch_to_hotfolder
from .photo_ops import (
    PRESETS,
    Adjustments,
    CropRect,
    Geometry,
    apply_adjustments,
    apply_exif_orientation,
    apply_geometry,
    auto_contrast_adjustments,
    exif_orientation_to_qt_rotation,
    fit_photo_to_zone,
    get_exif_orientation,
)
from .services import HotfolderNotConfiguredError, TirageMultiResult, TirageResult, TirageService
from .template import (
    FORMATS,
    LogoLayer,
    Margins,
    Orientation,
    PrintFormat,
    Template,
    TextLayer,
    detect_orientation_from_size,
)
from .watcher import CommandesWatcher
from .updater import (
    UpdateError, UpdateInfo, get_install_root, inspect_zip, install_update,
)
from .github_releases import (
    GitHubError, GitHubRelease, download_release_zip, fetch_latest_release,
    humanize_size,
)

__all__ = [
    "Adjustments",
    "AppConfig",
    "Commande",
    "CommandesWatcher",
    "CropRect",
    "Database",
    "EventInfo",
    "FORMATS",
    "Geometry",
    "GitHubError",
    "GitHubRelease",
    "HotfolderConfig",
    "HotfolderNotConfiguredError",
    "LogoLayer",
    "Margins",
    "Orientation",
    "OrientationMismatchError",
    "PRESETS",
    "PhotoSource",
    "PrintFormat",
    "RenderRequest",
    "Template",
    "TextLayer",
    "TirageDemande",
    "TirageMultiResult",
    "TirageResult",
    "TirageService",
    "UpdateError",
    "UpdateInfo",
    "apply_adjustments",
    "apply_exif_orientation",
    "apply_geometry",
    "auto_contrast_adjustments",
    "detect_format_code",
    "detect_orientation_from_size",
    "dispatch_to_hotfolder",
    "download_release_zip",
    "exif_orientation_to_qt_rotation",
    "extract_base_name",
    "fetch_latest_release",
    "fit_photo_to_zone",
    "get_app_data_dir",
    "get_exif_orientation",
    "get_install_root",
    "get_user_logos_dir",
    "get_user_templates_dir",
    "humanize_size",
    "inspect_zip",
    "install_update",
    "migrate_bundled_templates_if_needed",
    "render",
    "render_and_save",
    "render_preview",
    "scan_commande",
]
