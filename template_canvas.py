"""
Fenêtre principale Printou v0.4.

Workflow simplifié :
- Onglet Source : photo + retouches en temps réel (PhotoPreviewWidget gère)
- Onglet Rendu final : rendu LAZY (calculé seulement quand on clique sur l'onglet)

Le PhotoPreviewWidget applique lui-même les retouches sur le pixmap downscalé.
Le RenderPreviewWidget ne tourne plus en arrière-plan.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QIcon, QKeySequence, QPixmap
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QSplitter,
    QStatusBar,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from printou import __app_name__, __version__
from printou.core import (
    Adjustments,
    AppConfig,
    Commande,
    CommandesWatcher,
    Database,
    Geometry,
    HotfolderNotConfiguredError,
    OrientationMismatchError,
    PhotoSource,
    Template,
    TirageService,
    detect_orientation_from_size,
)
from printou.ui.parametres_dialog import ParametresDialog
from printou.ui.theme import Colors
from printou.ui.widgets import (
    CommandesPanel,
    ControlsPanel,
    PhotoPreviewWidget,
    RenderPreviewWidget,
)


class MainWindow(QMainWindow):
    def __init__(self, config: AppConfig, db: Database):
        super().__init__()
        self.config = config
        self.db = db
        self.service = TirageService(config, db)

        self._current_commande: Commande | None = None
        self._current_photo: PhotoSource | None = None
        self._templates: list[Template] = []
        self._render_dirty = True  # le rendu final doit être recalculé quand on switche dessus

        self.setWindowTitle(f"{__app_name__} v{__version__} — Avec Printou, t'imprimes tout")
        self.resize(1700, 1050)

        self._init_ui()
        self._init_menu()
        self._init_statusbar()
        self._init_shortcuts()

        self.watcher: CommandesWatcher | None = None

        self._load_templates()
        self._setup_watcher()
        self._refresh_status()

        if not config.is_configured():
            self._notify(
                "Bienvenue ! Configurez d'abord les dossiers via Outils > Paramètres."
            )

        # Vérification silencieuse de mise à jour au démarrage
        self._check_for_updates_silently()

    def _check_for_updates_silently(self):
        """Vérifie en arrière-plan s'il y a une mise à jour, sans déranger l'utilisateur.

        Si une nouvelle version est trouvée, affiche une petite notification cliquable
        dans la barre de statut.
        """
        from PySide6.QtCore import QObject, QThread, Signal
        from printou.core import GitHubError, fetch_latest_release
        from printou import __github_repo__, __version__

        class _SilentChecker(QObject):
            update_found = Signal(str, str)  # current, target
            failed = Signal()

            def run(self):
                try:
                    from printou.core.updater import is_frozen
                    rel = fetch_latest_release(__github_repo__, timeout=8,
                                               prefer_frozen=is_frozen())
                    if rel.is_newer_than(__version__):
                        self.update_found.emit(__version__, rel.version)
                    else:
                        self.failed.emit()  # rien à faire, on tait
                except GitHubError:
                    self.failed.emit()
                except Exception:
                    self.failed.emit()

        self._update_check_thread = QThread()
        self._update_check_worker = _SilentChecker()
        self._update_check_worker.moveToThread(self._update_check_thread)
        self._update_check_thread.started.connect(self._update_check_worker.run)
        self._update_check_worker.update_found.connect(self._on_silent_update_found)
        self._update_check_worker.update_found.connect(self._update_check_thread.quit)
        self._update_check_worker.failed.connect(self._update_check_thread.quit)
        self._update_check_thread.start()

    def _on_silent_update_found(self, current: str, target: str):
        """Affiche une notif discrète quand une MAJ est dispo."""
        msg = (
            f"🎉 Une nouvelle version de Printou est disponible : {current} → {target}"
            "  ·  Outils > Mettre à jour Printou…"
        )
        self.statusbar.showMessage(msg, 0)  # 0 = jusqu'à message suivant

    def _init_ui(self):
        splitter = QSplitter(Qt.Horizontal)

        self.commandes_panel = CommandesPanel()
        self.commandes_panel.photo_selected.connect(self._on_photo_selected)
        splitter.addWidget(self.commandes_panel)

        center = QWidget()
        center_layout = QVBoxLayout(center)
        center_layout.setContentsMargins(4, 4, 4, 4)

        self.tabs = QTabWidget()
        self.tabs.setStyleSheet(
            "QTabBar::tab { min-width: 160px; padding: 10px 16px; }"
        )
        self.tabs.currentChanged.connect(self._on_tab_changed)

        # Onglet 1 : Source (photo retouchée en temps réel par le widget lui-même)
        self.photo_preview = PhotoPreviewWidget()
        self.photo_preview.geometry_changed.connect(self._on_geometry_changed)
        self.photo_preview.crop_mode_changed.connect(self._on_crop_mode_changed)
        self.photo_preview.crop_clamped_to_safe.connect(self._on_crop_clamped)
        self.tabs.addTab(self.photo_preview, "✂  Source (cadrage et retouche)")

        # Onglet 2 : Rendu final (lazy)
        self.render_preview = RenderPreviewWidget()
        self.render_preview.render_failed.connect(self._on_render_failed)
        self.tabs.addTab(self.render_preview, "🖼  Rendu final (vérification)")

        # Onglet 3 : Éditeur de templates (v0.8+)
        from printou.core import get_install_root, get_user_logos_dir
        from printou.ui.widgets.template_editor import TemplateEditorTab
        demo_dir = get_install_root() / "assets" / "demo"
        # v0.9 : on pointe vers le dossier user des logos (plus le dossier assets/)
        # comme ça les logos importés via "Choisir un logo" sont retrouvés
        logos_dir = get_user_logos_dir()
        self.template_editor = TemplateEditorTab(
            demo_paysage_path=demo_dir / "demo_paysage.jpg",
            demo_portrait_path=demo_dir / "demo_portrait.jpg",
            logos_dir=logos_dir,
        )
        self.template_editor.template_list_changed.connect(self._on_templates_changed)
        self.tabs.addTab(self.template_editor, "📐  Éditeur de templates")

        center_layout.addWidget(self.tabs)
        splitter.addWidget(center)

        self.controls_panel = ControlsPanel()
        self.controls_panel.rotation_changed.connect(self._on_rotation_changed)
        self.controls_panel.geometry_reset_requested.connect(self._on_geometry_reset)
        self.controls_panel.crop_mode_toggle_requested.connect(self._on_crop_mode_toggle)
        self.controls_panel.adjustments_changed.connect(self._on_adjustments_changed)
        self.controls_panel.auto_contrast_requested.connect(self._on_auto_contrast)
        self.controls_panel.format_selected.connect(self._on_format_selected)
        self.controls_panel.template_selected.connect(self._on_template_selected)
        self.controls_panel.print_all_requested.connect(self._on_print_all_requested)
        self.controls_panel.finish_commande_requested.connect(self._on_finish_commande)
        splitter.addWidget(self.controls_panel)

        splitter.setSizes([320, 1000, 380])
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setStretchFactor(2, 0)

        self.setCentralWidget(splitter)

    def _init_menu(self):
        menubar = self.menuBar()

        m_fichier = menubar.addMenu("&Fichier")
        act_refresh = QAction("Rafraîchir les commandes", self)
        act_refresh.setShortcut("F5")
        act_refresh.triggered.connect(self.commandes_panel.refresh_now)
        m_fichier.addAction(act_refresh)

        act_next = QAction("Photo suivante", self)
        act_next.setShortcut("Ctrl+Right")
        act_next.triggered.connect(lambda: self.commandes_panel.select_next_photo(self._current_photo))
        m_fichier.addAction(act_next)

        m_fichier.addSeparator()
        act_quit = QAction("Quitter", self)
        act_quit.setShortcut("Ctrl+Q")
        act_quit.triggered.connect(self.close)
        m_fichier.addAction(act_quit)

        m_edit = menubar.addMenu("&Édition")
        act_undo = QAction("Annuler", self)
        act_undo.setShortcut(QKeySequence.Undo)  # Ctrl+Z natif
        act_undo.triggered.connect(self._on_undo)
        m_edit.addAction(act_undo)

        m_templates = menubar.addMenu("&Templates")
        act_reload = QAction("Recharger les templates", self)
        act_reload.triggered.connect(self._load_templates)
        m_templates.addAction(act_reload)

        m_outils = menubar.addMenu("&Outils")
        act_params = QAction("Paramètres…", self)
        act_params.setShortcut("Ctrl+,")
        act_params.triggered.connect(self._open_parametres)
        m_outils.addAction(act_params)

        m_outils.addSeparator()
        act_update = QAction("🔄 Mettre à jour Printou…", self)
        act_update.triggered.connect(self._open_update_dialog)
        m_outils.addAction(act_update)

        m_aide = menubar.addMenu("&Aide")
        act_about = QAction("À propos", self)
        act_about.triggered.connect(self._show_about)
        m_aide.addAction(act_about)

    def _init_shortcuts(self):
        # Le menu Edit a déjà Ctrl+Z. Ici on peut ajouter d'autres raccourcis si besoin.
        pass

    def _init_statusbar(self):
        self.statusbar = QStatusBar()
        self.setStatusBar(self.statusbar)
        self.status_commandes = QLabel("📁 -")
        self.status_hotfolder = QLabel("🖨 -")
        self.status_db = QLabel("💾 -")
        self.statusbar.addPermanentWidget(self.status_commandes)
        self.statusbar.addPermanentWidget(QLabel("•"))
        self.statusbar.addPermanentWidget(self.status_hotfolder)
        self.statusbar.addPermanentWidget(QLabel("•"))
        self.statusbar.addPermanentWidget(self.status_db)

    def _load_templates(self):
        """v0.8 : charge les templates depuis le store user (APPDATA/Printou/templates).

        Au premier lancement, on migre les templates fournis avec l'app.
        """
        from printou.core import (
            get_install_root, migrate_bundled_templates_if_needed,
        )
        from printou.core.templates_store import list_user_templates, save_template

        # Migration : si dossier user vide, copier les templates fournis dans l'app
        bundled = get_install_root() / "templates"
        # Si l'utilisateur a configuré un dossier templates personnel
        if self.config.templates_dir:
            custom = Path(self.config.templates_dir)
            if custom.is_dir():
                bundled = custom

        try:
            n = migrate_bundled_templates_if_needed(bundled)
            if n > 0:
                print(f"[Printou] {n} template(s) migré(s) depuis {bundled}")
        except Exception as e:
            print(f"[Printou] Erreur migration : {e}")

        self._templates = list_user_templates()

        # Si toujours vide, créer 2 templates par défaut
        if not self._templates:
            from printou.core import Margins
            t1 = Template(
                name="Défaut paysage", orientation="paysage",
                margins=Margins(top=4.0, bottom=8.0, left=4.0, right=4.0),
            )
            t2 = Template(
                name="Défaut portrait", orientation="portrait",
                margins=Margins(top=4.0, bottom=8.0, left=4.0, right=4.0),
            )
            save_template(t1)
            save_template(t2)
            self._templates = list_user_templates()

        self.controls_panel.set_templates(self._templates)
        # Refresh aussi l'éditeur si présent
        if hasattr(self, "template_editor"):
            self.template_editor.refresh_list()

    def _on_templates_changed(self):
        """v0.8 : appelé par l'éditeur quand un template a été ajouté/supprimé/modifié."""
        self._load_templates()

    def _resolve_logos_dir(self) -> Path:
        """v0.9 : retourne le dossier de logos, en priorisant le store user."""
        from printou.core import get_user_logos_dir
        if self.config.logos_dir:
            p = Path(self.config.logos_dir)
            if p.is_dir():
                return p
        return get_user_logos_dir()

    def _setup_watcher(self):
        if self.watcher:
            self.watcher.stop()
            self.watcher = None
        if self.config.commandes_root:
            root = Path(self.config.commandes_root)
            if root.is_dir():
                self.commandes_panel.set_commandes_root(root)
                self.watcher = CommandesWatcher(root)
                self.watcher.on_new_commande = self.commandes_panel.add_commande
                self.watcher.start()
            else:
                self.commandes_panel.set_commandes_root(None)
        else:
            self.commandes_panel.set_commandes_root(None)

    def _refresh_status(self):
        nb_cmd = self.commandes_panel.tree.topLevelItemCount()
        self.status_commandes.setText(f"📁 {nb_cmd} commande(s)")
        nb_hf = sum(1 for hf in self.config.hotfolders
                    if hf.get("path") and Path(hf["path"]).is_dir())
        self.status_hotfolder.setText(f"🖨 {nb_hf} hotfolder(s)")
        self.status_db.setText("💾 OK")

    # ─── Sélection photo ───

    def _on_photo_selected(self, commande: Commande, photo: PhotoSource):
        self._current_commande = commande
        self._current_photo = photo

        photo_path = photo.representative_file
        # IMPORTANT : on charge via PIL d'abord pour gérer l'EXIF orientation,
        # sinon les photos portrait apparaissent couchées (les capteurs photo
        # enregistrent toujours en paysage natif, l'EXIF dit comment afficher).
        pixmap = self._load_pixmap_with_exif(photo_path)
        if pixmap is None or pixmap.isNull():
            self._notify(f"Impossible de charger : {photo_path}")
            return
        self.photo_preview.set_pixmap(pixmap)
        self.controls_panel.set_photo(commande, photo)
        self.controls_panel.reset_geometry_ui()
        self.controls_panel.reset_adjustments_ui()
        # v0.7 : set_pixmap active le mode crop par défaut, on laisse le signal
        # crop_mode_changed faire le boulot d'aligner le bouton du panel

        photo_orientation = detect_orientation_from_size(pixmap.width(), pixmap.height())
        matching = [t for t in self._templates if t.orientation == photo_orientation]
        template = matching[0] if matching else (self._templates[0] if self._templates else None)
        if template:
            self.controls_panel.set_current_template(template.name)

        format_code = next(iter(photo.tirages.keys()), "20x30")
        self._set_crop_ratio_from_format(format_code, photo_orientation)
        self._render_dirty = True
        self.tabs.setCurrentIndex(0)

    def _load_pixmap_with_exif(self, path):
        """Charge une photo via PIL (qui gère l'EXIF) puis convertit en QPixmap."""
        from PIL import Image
        from printou.core import apply_exif_orientation
        from PySide6.QtGui import QImage
        try:
            with Image.open(str(path)) as img:
                # Appliquer EXIF Orientation pour que les portraits ne soient pas couchés
                img = apply_exif_orientation(img).convert("RGB")
                # Conversion PIL -> QPixmap
                data = img.tobytes("raw", "RGB")
                qimg = QImage(
                    data, img.width, img.height,
                    img.width * 3, QImage.Format_RGB888,
                )
                return QPixmap.fromImage(qimg.copy())
        except Exception as e:
            print(f"[Printou] Erreur chargement photo : {e}")
            return None

    def _set_crop_ratio_from_format(self, format_code: str, orientation: str):
        from printou.core import FORMATS
        if format_code not in FORMATS:
            return
        fmt = FORMATS[format_code]
        if orientation == "paysage":
            ratio = max(fmt.width_mm, fmt.height_mm) / min(fmt.width_mm, fmt.height_mm)
        else:
            ratio = min(fmt.width_mm, fmt.height_mm) / max(fmt.width_mm, fmt.height_mm)
        self.photo_preview.set_aspect_ratio(ratio, lock=True)

    # ─── Onglets ───

    def _on_tab_changed(self, index: int):
        # Lazy : on ne calcule le rendu final que quand on est dessus
        if index == 1 and self._render_dirty and self._current_photo is not None:
            self._update_render_preview()
            self._render_dirty = False

    def _update_render_preview(self, format_code: str | None = None):
        if self._current_photo is None:
            return
        template = self.controls_panel.get_current_template()
        if template is None:
            return

        info = self.config.get_event_info()
        placeholders = {
            "event_name": info.name,
            "event_location": info.location,
            "event_date": info.date,
            "client_name": self._current_commande.display_name if self._current_commande else "",
        }

        # Trouver le format actuellement sélectionné dans le tableau de tirages
        if format_code is None:
            row = self.controls_panel.tirages_table.currentRow()
            if row >= 0:
                fmt_item = self.controls_panel.tirages_table.item(row, 0)
                format_code = fmt_item.text() if fmt_item else "20x30"
            else:
                format_code = "20x30"

        self.render_preview.set_render_inputs(
            photo_path=self._current_photo.representative_file,
            template=template,
            format_code=format_code,
            geometry=self.photo_preview.get_geometry(),
            adjustments=self.controls_panel.get_current_adjustments(),
            placeholders=placeholders,
            logos_dir=self._resolve_logos_dir(),
        )

    # ─── Contrôles ───

    def _on_rotation_changed(self, deg: float):
        self.photo_preview.set_rotation(deg)
        self._render_dirty = True

    def _on_geometry_reset(self):
        self.photo_preview.reset_geometry()
        self.controls_panel.reset_geometry_ui()
        self._render_dirty = True

    def _on_crop_mode_toggle(self):
        if self.photo_preview.is_crop_mode():
            self.photo_preview.exit_crop_mode()
        else:
            self.photo_preview.enter_crop_mode()
        # Reprendre le focus pour les raccourcis clavier
        self.photo_preview.setFocus()

    def _on_crop_mode_changed(self, active: bool):
        self.controls_panel.set_crop_mode_active(active)
        if not active:
            self._render_dirty = True

    def _on_crop_clamped(self):
        """v0.8 : émis quand le crop a été ajusté à la zone safe (rotation active)."""
        self.statusbar.showMessage(
            "✂  Cadrage ajusté automatiquement à la zone sans coins noirs",
            4000,
        )

    def _on_adjustments_changed(self, adj: Adjustments):
        self.photo_preview.set_adjustments(adj)
        self._render_dirty = True

    def _on_auto_contrast(self):
        if self._current_photo is None:
            return
        from PIL import Image
        from printou.core import auto_contrast_adjustments
        try:
            with Image.open(self._current_photo.representative_file) as img:
                img.thumbnail((800, 800))
                adj = auto_contrast_adjustments(img.copy())
            self.photo_preview.push_adjustments_history()
            self.controls_panel.apply_adjustments_to_ui(adj)
            self.statusbar.showMessage(
                f"✨ Auto-contraste : contraste ×{adj.contrast:.2f}, "
                f"luminosité ×{adj.brightness:.2f}",
                4000,
            )
        except Exception as e:
            self.statusbar.showMessage(f"Erreur auto-contraste : {e}", 4000)

    def _on_format_selected(self, format_code: str):
        if self._current_photo is not None:
            pix = self.photo_preview._original_pixmap
            if pix is not None:
                orientation = detect_orientation_from_size(pix.width(), pix.height())
                self._set_crop_ratio_from_format(format_code, orientation)
        self._render_dirty = True
        # Si on est sur l'onglet rendu final, on rafraîchit immédiatement
        if self.tabs.currentIndex() == 1:
            self._update_render_preview(format_code)
            self._render_dirty = False

    def _on_template_selected(self, _template_name: str):
        self._render_dirty = True
        if self.tabs.currentIndex() == 1:
            self._update_render_preview()
            self._render_dirty = False

    def _on_geometry_changed(self, _geometry: Geometry):
        self._render_dirty = True

    def _on_render_failed(self, message: str):
        self.statusbar.showMessage(f"⚠ {message}", 5000)

    def _on_undo(self):
        """Annule la dernière action sur la photo courante."""
        if self.photo_preview.undo():
            # Synchronise les sliders sur l'état après undo
            adj = self.photo_preview.get_adjustments()
            self.controls_panel.brightness_slider.set_value(adj.brightness)
            self.controls_panel.contrast_slider.set_value(adj.contrast)
            self.controls_panel.highlights_slider.set_value(adj.highlights)
            self.controls_panel.shadows_slider.set_value(adj.shadows)
            self.controls_panel.saturation_slider.set_value(adj.saturation)
            self.controls_panel.sharpness_slider.set_value(adj.sharpness)
            self.controls_panel.temperature_slider.set_value(adj.temperature)
            geo = self.photo_preview.get_geometry()
            self.controls_panel.rotation_slider.set_value(geo.rotation_deg)
            self.controls_panel.set_crop_mode_active(self.photo_preview.is_crop_mode())
            self._render_dirty = True
            self.statusbar.showMessage("⟲ Annulation", 1500)
        else:
            self.statusbar.showMessage("Rien à annuler", 1500)

    # ─── Pipeline impression ───

    def _on_print_all_requested(self):
        if self._current_commande is None or self._current_photo is None:
            return
        template = self.controls_panel.get_current_template()
        if template is None:
            self._notify("Sélectionnez un template.")
            return

        photo = self._current_photo
        commande = self._current_commande

        result = self.service.imprimer_tous_formats(
            commande, photo, template,
            self.photo_preview.get_geometry(),
            self.controls_panel.get_current_adjustments(),
        )

        nb_ok = len(result.par_format)
        nb_err = len(result.erreurs)
        if nb_err:
            err_msg = "\n".join(f"• {fmt} : {msg}" for fmt, msg in result.erreurs.items())
            QMessageBox.warning(
                self, "Impression partielle",
                f"{nb_ok} format(s) imprimé(s), {nb_err} en erreur :\n\n{err_msg}",
            )
        else:
            total_files = sum(len(r.fichiers_dispatches) for r in result.par_format.values())
            self.statusbar.showMessage(
                f"✓ {nb_ok} format(s) traité(s) — {total_files} fichier(s) envoyé(s)", 5000,
            )

        self.commandes_panel.mark_current_photo_traitee()
        if not self.commandes_panel.select_next_photo(after_photo=photo):
            ret = QMessageBox.question(
                self, "Toutes les photos traitées",
                "Toutes les photos en attente ont été traitées.\n\n"
                "Marquer la commande courante comme terminée ?",
                QMessageBox.Yes | QMessageBox.No,
            )
            if ret == QMessageBox.Yes:
                self._on_finish_commande()

    def _on_finish_commande(self):
        if self._current_commande is None:
            return
        try:
            target = self.service.deplacer_commande_traitee(self._current_commande)
            if target:
                self.statusbar.showMessage(f"✓ Commande déplacée vers {target.parent.name}", 5000)
                self._current_commande = None
                self._current_photo = None
                self.controls_panel.clear_photo()
                self.photo_preview.set_pixmap(None)
                self.render_preview.clear()
                self.commandes_panel.refresh_now()
                self._refresh_status()
                self.commandes_panel.select_next_photo()
            else:
                self._notify("Configurez le dossier 'Commandes traitées' dans Paramètres.")
        except Exception as e:
            QMessageBox.critical(self, "Erreur", str(e))

    def _open_parametres(self):
        dialog = ParametresDialog(self.config, self)
        if dialog.exec():
            self._setup_watcher()
            self._load_templates()
            self._refresh_status()

    def _open_update_dialog(self):
        from printou.ui.update_dialog import UpdateDialog
        dialog = UpdateDialog(self)
        dialog.exec()

    def _show_about(self):
        QMessageBox.about(
            self, f"À propos de {__app_name__}",
            f"<h2>{__app_name__} v{__version__}</h2>"
            f"<p><i>Avec Printou, t'imprimes tout</i></p>",
        )

    def _notify(self, msg: str):
        self.statusbar.showMessage(msg, 8000)

    def closeEvent(self, event):
        if self.watcher:
            self.watcher.stop()
        super().closeEvent(event)
