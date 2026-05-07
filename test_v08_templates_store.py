"""
Onglet "Éditeur de templates" (v0.8) - 3ème onglet de la fenêtre principale.

Layout : 3 colonnes
┌──────────────┬─────────────────┬───────────────┐
│ Templates    │   Aperçu live   │   Édition     │
│ - liste      │   (drag & drop) │   - props tpl │
│ - +/-/dup    │                 │   - calques   │
│ - export ZIP │                 │   - props cq  │
│ - import ZIP │                 │               │
└──────────────┴─────────────────┴───────────────┘
"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox, QColorDialog, QComboBox, QDoubleSpinBox, QFileDialog,
    QFormLayout, QFrame, QGroupBox, QHBoxLayout, QInputDialog, QLabel,
    QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QPushButton,
    QSplitter, QTextEdit, QVBoxLayout, QWidget,
)

from printou.core import Margins, Template
from printou.core.templates_store import (
    delete_template, duplicate_template, export_to_zip, import_from_zip,
    list_user_templates, save_template,
)
from printou.ui.theme import Colors
from printou.ui.widgets.template_canvas import TemplateCanvas


PLACEHOLDERS = ["event_name", "event_location", "event_date", "client_name"]


class TemplateEditorTab(QWidget):
    """Onglet d'édition de templates (intégré à MainWindow)."""

    template_list_changed = Signal()  # appelé quand la liste change

    def __init__(self, demo_paysage_path: Path, demo_portrait_path: Path,
                 logos_dir: Path | None = None, parent=None):
        super().__init__(parent)
        self._demo_paysage = demo_paysage_path
        self._demo_portrait = demo_portrait_path
        self._logos_dir = logos_dir
        self._current_template: Template | None = None
        self._original_name: str | None = None
        self._dirty: bool = False
        self._init_ui()
        self.refresh_list()

    # ─── UI setup ───

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.splitter = QSplitter(Qt.Horizontal)

        # Colonne 1 : liste
        col_list = QWidget()
        cl_layout = QVBoxLayout(col_list)
        cl_layout.setContentsMargins(8, 8, 8, 8)
        cl_layout.setSpacing(6)

        title = QLabel("<b>Templates</b>")
        cl_layout.addWidget(title)

        self.templates_list = QListWidget()
        self.templates_list.itemSelectionChanged.connect(self._on_template_selected)
        cl_layout.addWidget(self.templates_list, stretch=1)

        # Boutons CRUD
        btn_row = QHBoxLayout()
        self.btn_new = QPushButton("➕ Nouveau")
        self.btn_new.clicked.connect(self._on_new_template)
        btn_row.addWidget(self.btn_new)
        self.btn_dup = QPushButton("🗐 Dupliquer")
        self.btn_dup.clicked.connect(self._on_duplicate_template)
        btn_row.addWidget(self.btn_dup)
        cl_layout.addLayout(btn_row)

        self.btn_delete = QPushButton("🗑 Supprimer")
        self.btn_delete.clicked.connect(self._on_delete_template)
        cl_layout.addWidget(self.btn_delete)

        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet(f"background:{Colors.BORDER}; max-height:1px;")
        cl_layout.addWidget(sep)

        self.btn_export = QPushButton("📤 Exporter en ZIP")
        self.btn_export.setToolTip("Génère un ZIP que tu peux uploader sur GitHub")
        self.btn_export.clicked.connect(self._on_export_zip)
        cl_layout.addWidget(self.btn_export)

        self.btn_import = QPushButton("📥 Importer ZIP")
        self.btn_import.setToolTip("Importe des templates depuis un ZIP (téléchargé depuis GitHub par ex.)")
        self.btn_import.clicked.connect(self._on_import_zip)
        cl_layout.addWidget(self.btn_import)

        self.splitter.addWidget(col_list)

        # Colonne 2 : Canvas
        col_canvas = QWidget()
        cc_layout = QVBoxLayout(col_canvas)
        cc_layout.setContentsMargins(4, 4, 4, 4)
        canvas_title = QLabel("Aperçu (drag & drop pour positionner les calques)")
        canvas_title.setStyleSheet(f"color: {Colors.TEXT_DIM};")
        cc_layout.addWidget(canvas_title)
        self.canvas = TemplateCanvas()
        self.canvas.set_demo_photos(self._demo_paysage, self._demo_portrait)
        self.canvas.set_logos_dir(self._logos_dir)
        self.canvas.layer_selected.connect(self._on_canvas_layer_selected)
        self.canvas.layer_modified.connect(self._on_canvas_layer_modified)
        self.canvas.layer_dropped.connect(self._on_canvas_layer_dropped)
        cc_layout.addWidget(self.canvas, stretch=1)
        self.splitter.addWidget(col_canvas)

        # Colonne 3 : Édition
        col_edit = QWidget()
        ce_layout = QVBoxLayout(col_edit)
        ce_layout.setContentsMargins(8, 8, 8, 8)
        ce_layout.setSpacing(8)

        # Bloc Template
        tpl_box = QGroupBox("Template")
        tpl_form = QFormLayout(tpl_box)
        self.name_edit = QLineEdit()
        self.name_edit.editingFinished.connect(self._on_template_name_changed)
        tpl_form.addRow("Nom :", self.name_edit)
        self.orientation_combo = QComboBox()
        self.orientation_combo.addItems(["paysage", "portrait"])
        self.orientation_combo.currentTextChanged.connect(self._on_orientation_changed)
        tpl_form.addRow("Orientation :", self.orientation_combo)
        ce_layout.addWidget(tpl_box)

        # Bloc Marges
        margin_box = QGroupBox("Marges (% du papier)")
        m_form = QFormLayout(margin_box)
        self.margin_top = self._make_pct_spin(0, 30)
        self.margin_bottom = self._make_pct_spin(0, 30)
        self.margin_left = self._make_pct_spin(0, 30)
        self.margin_right = self._make_pct_spin(0, 30)
        for w in (self.margin_top, self.margin_bottom, self.margin_left, self.margin_right):
            w.valueChanged.connect(self._on_margin_changed)
        m_form.addRow("Haut :", self.margin_top)
        m_form.addRow("Bas :", self.margin_bottom)
        m_form.addRow("Gauche :", self.margin_left)
        m_form.addRow("Droite :", self.margin_right)
        ce_layout.addWidget(margin_box)

        # Bloc Calques
        layers_box = QGroupBox("Calques")
        l_layout = QVBoxLayout(layers_box)
        self.layers_list = QListWidget()
        self.layers_list.setMaximumHeight(120)
        self.layers_list.itemSelectionChanged.connect(self._on_layer_list_selected)
        l_layout.addWidget(self.layers_list)
        l_btns = QHBoxLayout()
        self.btn_add_logo = QPushButton("➕ Logo")
        self.btn_add_logo.clicked.connect(self._on_add_logo)
        l_btns.addWidget(self.btn_add_logo)
        self.btn_add_text = QPushButton("➕ Texte")
        self.btn_add_text.clicked.connect(self._on_add_text)
        l_btns.addWidget(self.btn_add_text)
        self.btn_del_layer = QPushButton("🗑")
        self.btn_del_layer.clicked.connect(self._on_delete_layer)
        l_btns.addWidget(self.btn_del_layer)
        l_layout.addLayout(l_btns)
        ce_layout.addWidget(layers_box)

        # Bloc Propriétés du calque
        self.props_box = QGroupBox("Propriétés du calque")
        self.props_layout = QFormLayout(self.props_box)
        self._build_props_widgets()
        ce_layout.addWidget(self.props_box)
        self.props_box.setVisible(False)

        ce_layout.addStretch()

        self.btn_save = QPushButton("💾 Enregistrer le template")
        self.btn_save.setStyleSheet(f"""
            QPushButton {{ background:{Colors.ACCENT}; color:{Colors.BG}; padding:10px;
                            font-weight:bold; border-radius:6px; }}
            QPushButton:hover {{ background:{Colors.ACCENT_HOVER}; }}
            QPushButton:disabled {{ background:{Colors.BORDER}; color:{Colors.TEXT_MUTED}; }}
        """)
        self.btn_save.clicked.connect(self._on_save)
        ce_layout.addWidget(self.btn_save)

        self.splitter.addWidget(col_edit)

        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setStretchFactor(2, 0)
        self.splitter.setSizes([220, 800, 320])
        layout.addWidget(self.splitter)

        self._set_editing_enabled(False)

    def _make_pct_spin(self, mn: float = 0.0, mx: float = 100.0) -> QDoubleSpinBox:
        s = QDoubleSpinBox()
        s.setRange(mn, mx)
        s.setDecimals(1)
        s.setSingleStep(0.5)
        s.setSuffix(" %")
        return s

    def _build_props_widgets(self):
        self._prop_x = self._make_pct_spin()
        self._prop_x.valueChanged.connect(self._on_layer_prop_changed)
        self._prop_y = self._make_pct_spin()
        self._prop_y.valueChanged.connect(self._on_layer_prop_changed)
        self._prop_w = self._make_pct_spin(0.5, 100)
        self._prop_w.valueChanged.connect(self._on_layer_prop_changed)
        self._prop_h = self._make_pct_spin(0.5, 100)
        self._prop_h.valueChanged.connect(self._on_layer_prop_changed)
        self._prop_anchor = QComboBox()
        self._prop_anchor.addItems(["image", "paper", "top_band", "bottom_band"])
        self._prop_anchor.currentTextChanged.connect(self._on_layer_prop_changed)

        self._row_x = ("X :", self._prop_x)
        self._row_y = ("Y :", self._prop_y)
        self._row_w = ("Largeur :", self._prop_w)
        self._row_h = ("Hauteur :", self._prop_h)
        self._row_anchor = ("Ancrage :", self._prop_anchor)

        self.props_layout.addRow(*self._row_x)
        self.props_layout.addRow(*self._row_y)
        self.props_layout.addRow(*self._row_w)
        self.props_layout.addRow(*self._row_h)
        self.props_layout.addRow(*self._row_anchor)

        # Logo
        self._prop_logo_path = QLineEdit()
        self._prop_logo_path.editingFinished.connect(self._on_layer_prop_changed)
        self._prop_logo_btn = QPushButton("…")
        self._prop_logo_btn.setMaximumWidth(36)
        self._prop_logo_btn.clicked.connect(self._on_pick_logo_file)
        logo_w = QWidget()
        logo_l = QHBoxLayout(logo_w)
        logo_l.setContentsMargins(0, 0, 0, 0)
        logo_l.addWidget(self._prop_logo_path)
        logo_l.addWidget(self._prop_logo_btn)
        self._row_logo = ("Fichier :", logo_w)
        self.props_layout.addRow(*self._row_logo)

        # Texte
        self._prop_text = QTextEdit()
        self._prop_text.setMaximumHeight(70)
        self._prop_text.textChanged.connect(self._on_layer_prop_changed)
        self._row_text = ("Texte :", self._prop_text)
        self.props_layout.addRow(*self._row_text)

        self._prop_placeholders = QComboBox()
        self._prop_placeholders.addItems(["Insérer placeholder…"] + [f"{{{p}}}" for p in PLACEHOLDERS])
        self._prop_placeholders.currentTextChanged.connect(self._on_insert_placeholder)
        self._row_placeholders = ("", self._prop_placeholders)
        self.props_layout.addRow(*self._row_placeholders)

        self._prop_font_pct = self._make_pct_spin(1, 100)
        self._prop_font_pct.valueChanged.connect(self._on_layer_prop_changed)
        self._row_font = ("Taille texte :", self._prop_font_pct)
        self.props_layout.addRow(*self._row_font)

        self._prop_align = QComboBox()
        self._prop_align.addItems(["left", "center", "right"])
        self._prop_align.currentTextChanged.connect(self._on_layer_prop_changed)
        self._row_align = ("Alignement :", self._prop_align)
        self.props_layout.addRow(*self._row_align)

        self._prop_color_btn = QPushButton("Couleur…")
        self._prop_color_btn.clicked.connect(self._on_pick_color)
        self._row_color = ("Couleur :", self._prop_color_btn)
        self.props_layout.addRow(*self._row_color)

        self._prop_bold = QCheckBox("Gras")
        self._prop_bold.toggled.connect(self._on_layer_prop_changed)
        self._row_bold = ("", self._prop_bold)
        self.props_layout.addRow(*self._row_bold)

    def _show_row(self, label_text: str, visible: bool):
        for i in range(self.props_layout.rowCount()):
            label_item = self.props_layout.itemAt(i, QFormLayout.LabelRole)
            field_item = self.props_layout.itemAt(i, QFormLayout.FieldRole)
            if label_item and label_item.widget() and label_item.widget().text() == label_text:
                if label_item.widget():
                    label_item.widget().setVisible(visible)
                if field_item and field_item.widget():
                    field_item.widget().setVisible(visible)

    # ─── Liste templates ───

    def refresh_list(self):
        self.templates_list.blockSignals(True)
        self.templates_list.clear()
        for tpl in list_user_templates():
            label = f"{tpl.name}  ({tpl.orientation[0].upper()})"
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, tpl)
            self.templates_list.addItem(item)
        self.templates_list.blockSignals(False)

    def _on_template_selected(self):
        item = self.templates_list.currentItem()
        if item is None:
            self._set_editing_enabled(False)
            self._current_template = None
            self.canvas.set_template(None)
            return
        tpl = item.data(Qt.UserRole)
        self._current_template = deepcopy(tpl)
        self._original_name = tpl.name
        self._dirty = False
        self._populate_editor()
        self.canvas.set_template(self._current_template)
        self._set_editing_enabled(True)

    def _populate_editor(self):
        if self._current_template is None:
            return
        t = self._current_template
        for w in [self.name_edit, self.orientation_combo,
                  self.margin_top, self.margin_bottom, self.margin_left, self.margin_right]:
            w.blockSignals(True)
        self.name_edit.setText(t.name)
        self.orientation_combo.setCurrentText(t.orientation)
        self.margin_top.setValue(t.margins.top)
        self.margin_bottom.setValue(t.margins.bottom)
        self.margin_left.setValue(t.margins.left)
        self.margin_right.setValue(t.margins.right)
        for w in [self.name_edit, self.orientation_combo,
                  self.margin_top, self.margin_bottom, self.margin_left, self.margin_right]:
            w.blockSignals(False)
        self._refresh_layers_list()

    def _refresh_layers_list(self):
        self.layers_list.blockSignals(True)
        self.layers_list.clear()
        if self._current_template is not None:
            for i, layer in enumerate(self._current_template.layers):
                if layer.get("type") == "logo":
                    asset = Path(layer.get("asset_path", "")).name or "(non défini)"
                    label = f"🏷 Logo : {asset}"
                else:
                    content = layer.get("content", "").split("\n")[0][:30]
                    label = f"📝 Texte : {content}"
                item = QListWidgetItem(label)
                item.setData(Qt.UserRole, i)
                self.layers_list.addItem(item)
        self.layers_list.blockSignals(False)
        self.props_box.setVisible(False)

    def _set_editing_enabled(self, enabled: bool):
        for w in [self.name_edit, self.orientation_combo,
                  self.margin_top, self.margin_bottom, self.margin_left, self.margin_right,
                  self.btn_add_logo, self.btn_add_text, self.btn_del_layer,
                  self.btn_save, self.btn_dup, self.btn_delete]:
            w.setEnabled(enabled)

    # ─── Actions templates ───

    def _on_new_template(self):
        if not self._confirm_discard():
            return
        name, ok = QInputDialog.getText(self, "Nouveau template", "Nom :")
        if not ok or not name.strip():
            return
        tpl = Template(name=name.strip(), orientation="paysage",
                       margins=Margins(4, 8, 4, 4))
        save_template(tpl)
        self.refresh_list()
        self.template_list_changed.emit()
        for i in range(self.templates_list.count()):
            if self.templates_list.item(i).data(Qt.UserRole).name == name.strip():
                self.templates_list.setCurrentRow(i)
                break

    def _on_duplicate_template(self):
        item = self.templates_list.currentItem()
        if item is None:
            return
        src = item.data(Qt.UserRole).name
        new_name, ok = QInputDialog.getText(
            self, "Dupliquer", f"Nouveau nom (basé sur '{src}') :",
            text=f"{src} (copie)",
        )
        if not ok or not new_name.strip():
            return
        if duplicate_template(src, new_name.strip()) is None:
            QMessageBox.warning(self, "Erreur", "Duplication impossible.")
            return
        self.refresh_list()
        self.template_list_changed.emit()

    def _on_delete_template(self):
        item = self.templates_list.currentItem()
        if item is None:
            return
        name = item.data(Qt.UserRole).name
        ret = QMessageBox.question(
            self, "Supprimer", f"Supprimer le template '{name}' ?",
            QMessageBox.Yes | QMessageBox.No,
        )
        if ret != QMessageBox.Yes:
            return
        if delete_template(name):
            self.refresh_list()
            self.template_list_changed.emit()
            self._current_template = None
            self.canvas.set_template(None)
            self._set_editing_enabled(False)

    def _on_export_zip(self):
        try:
            zip_path = export_to_zip()
            QMessageBox.information(
                self, "Export réussi",
                f"Tes templates ont été exportés dans :\n\n{zip_path}\n\n"
                "Tu peux uploader ce ZIP comme asset d'une release "
                "'templates' sur ton GitHub.",
            )
        except Exception as e:
            QMessageBox.critical(self, "Erreur d'export", str(e))

    def _on_import_zip(self):
        zip_str, _ = QFileDialog.getOpenFileName(
            self, "Choisir un ZIP de templates",
            str(Path.home()), "Archives ZIP (*.zip)",
        )
        if not zip_str:
            return
        ret = QMessageBox.question(
            self, "Conflits",
            "Si un template du ZIP a le même nom qu'un template existant, "
            "veux-tu écraser l'existant ?",
            QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel, QMessageBox.No,
        )
        if ret == QMessageBox.Cancel:
            return
        result = import_from_zip(Path(zip_str), overwrite=(ret == QMessageBox.Yes))
        msg = (
            f"Importés : {len(result['imported'])}\n"
            f"Ignorés (existants) : {len(result['skipped'])}\n"
            f"Erreurs : {len(result['errors'])}"
        )
        if result["errors"]:
            msg += "\n\n" + "\n".join(result["errors"][:5])
        QMessageBox.information(self, "Import terminé", msg)
        self.refresh_list()
        self.template_list_changed.emit()

    # ─── Édition template ───

    def _on_template_name_changed(self):
        if self._current_template is None:
            return
        new_name = self.name_edit.text().strip()
        if not new_name or new_name == self._current_template.name:
            return
        self._current_template.name = new_name
        self._dirty = True

    def _on_orientation_changed(self, value: str):
        if self._current_template is None:
            return
        self._current_template.orientation = value
        self._dirty = True
        self.canvas.set_template(self._current_template)

    def _on_margin_changed(self):
        if self._current_template is None:
            return
        self._current_template.margins = Margins(
            top=self.margin_top.value(), bottom=self.margin_bottom.value(),
            left=self.margin_left.value(), right=self.margin_right.value(),
        )
        self._dirty = True
        self.canvas.set_template(self._current_template)

    # ─── Calques ───

    def _on_add_logo(self):
        if self._current_template is None:
            return
        path_str, _ = QFileDialog.getOpenFileName(
            self, "Choisir un logo", str(self._logos_dir or Path.home()),
            "Images (*.png *.jpg *.jpeg *.gif *.bmp);;Tous (*.*)",
        )
        if not path_str:
            return
        # v0.9 : on copie le logo dans %APPDATA%\Printou\logos\ et on stocke
        # juste le NOM RELATIF dans le template (pour la portabilité entre PCs)
        from printou.core.logos_store import import_logo
        try:
            relative_name = import_logo(Path(path_str))
        except Exception as e:
            QMessageBox.warning(self, "Erreur logo", f"Impossible d'importer le logo : {e}")
            return
        layer = {
            "type": "logo", "asset_path": relative_name, "anchor": "image",
            "x_pct": 5.0, "y_pct": 5.0, "width_pct": 12.0,
            "z_order": len(self._current_template.layers),
        }
        self._current_template.layers.append(layer)
        self._dirty = True
        self.canvas.set_template(self._current_template)
        self._refresh_layers_list()
        self.layers_list.setCurrentRow(self.layers_list.count() - 1)

    def _on_add_text(self):
        if self._current_template is None:
            return
        layer = {
            "type": "text", "content": "{event_name}",
            "anchor": "bottom_band",
            "x_pct": 5.0, "y_pct": 25.0, "width_pct": 90.0,
            "font_pct": 30.0, "font_family": "Arial",
            "color": "#FFFFFF", "align": "center", "bold": False,
            "z_order": len(self._current_template.layers),
        }
        self._current_template.layers.append(layer)
        self._dirty = True
        self.canvas.set_template(self._current_template)
        self._refresh_layers_list()
        self.layers_list.setCurrentRow(self.layers_list.count() - 1)

    def _on_delete_layer(self):
        item = self.layers_list.currentItem()
        if item is None or self._current_template is None:
            return
        idx = item.data(Qt.UserRole)
        del self._current_template.layers[idx]
        self._dirty = True
        self.canvas.set_template(self._current_template)
        self._refresh_layers_list()

    def _on_layer_list_selected(self):
        item = self.layers_list.currentItem()
        if item is None:
            self.props_box.setVisible(False)
            self.canvas.select_layer(-1)
            return
        idx = item.data(Qt.UserRole)
        self.canvas.select_layer(idx)
        self._populate_props(idx)

    def _on_canvas_layer_selected(self, idx: int):
        if idx < 0:
            self.layers_list.clearSelection()
            self.props_box.setVisible(False)
            return
        for i in range(self.layers_list.count()):
            if self.layers_list.item(i).data(Qt.UserRole) == idx:
                self.layers_list.setCurrentRow(i)
                break

    def _on_canvas_layer_modified(self, idx: int, layer: dict):
        if self._current_template is None:
            return
        self._dirty = True
        cur = self.layers_list.currentItem()
        if cur and cur.data(Qt.UserRole) == idx:
            self._populate_props(idx)

    def _on_canvas_layer_dropped(self, idx: int, layer: dict):
        # commit final, on rafraîchit le label
        self._refresh_layers_list_labels_only()

    def _populate_props(self, idx: int):
        if self._current_template is None:
            return
        layer = self._current_template.layers[idx]

        widgets = [self._prop_x, self._prop_y, self._prop_w, self._prop_h,
                   self._prop_anchor, self._prop_logo_path, self._prop_text,
                   self._prop_font_pct, self._prop_align, self._prop_bold]
        for w in widgets:
            w.blockSignals(True)

        self._prop_x.setValue(layer.get("x_pct", 0.0))
        self._prop_y.setValue(layer.get("y_pct", 0.0))
        self._prop_w.setValue(layer.get("width_pct", 10.0))
        self._prop_h.setValue(layer.get("height_pct", 10.0))
        self._prop_anchor.setCurrentText(layer.get("anchor", "image"))

        is_logo = (layer.get("type") == "logo")

        # Visibilité conditionnelle
        self._show_row("Fichier :", is_logo)
        self._show_row("Hauteur :", is_logo)
        self._show_row("Texte :", not is_logo)
        self._show_row("Taille texte :", not is_logo)
        self._show_row("Alignement :", not is_logo)
        self._show_row("Couleur :", not is_logo)
        # Le combo placeholders et bold n'ont pas de label texte (rangé "")
        self._prop_placeholders.setVisible(not is_logo)
        self._prop_bold.setVisible(not is_logo)

        if is_logo:
            self._prop_logo_path.setText(layer.get("asset_path", ""))
        else:
            self._prop_text.setPlainText(layer.get("content", ""))
            self._prop_font_pct.setValue(layer.get("font_pct", 4.0))
            self._prop_align.setCurrentText(layer.get("align", "left"))
            self._prop_bold.setChecked(layer.get("bold", False))

        for w in widgets:
            w.blockSignals(False)
        self.props_box.setVisible(True)

    def _on_layer_prop_changed(self):
        item = self.layers_list.currentItem()
        if item is None or self._current_template is None:
            return
        idx = item.data(Qt.UserRole)
        layer = self._current_template.layers[idx]

        layer["x_pct"] = self._prop_x.value()
        layer["y_pct"] = self._prop_y.value()
        layer["width_pct"] = self._prop_w.value()
        layer["anchor"] = self._prop_anchor.currentText()

        if layer.get("type") == "logo":
            layer["height_pct"] = self._prop_h.value()
            layer["asset_path"] = self._prop_logo_path.text().strip()
        else:
            layer["content"] = self._prop_text.toPlainText()
            layer["font_pct"] = self._prop_font_pct.value()
            layer["align"] = self._prop_align.currentText()
            layer["bold"] = self._prop_bold.isChecked()

        self._dirty = True
        self.canvas.set_template(self._current_template)
        self.canvas.select_layer(idx)
        self._refresh_layers_list_labels_only()

    def _refresh_layers_list_labels_only(self):
        cur_row = self.layers_list.currentRow()
        for i in range(self.layers_list.count()):
            item = self.layers_list.item(i)
            idx = item.data(Qt.UserRole)
            layer = self._current_template.layers[idx]
            if layer.get("type") == "logo":
                asset = Path(layer.get("asset_path", "")).name or "(non défini)"
                item.setText(f"🏷 Logo : {asset}")
            else:
                content = layer.get("content", "").split("\n")[0][:30]
                item.setText(f"📝 Texte : {content}")
        self.layers_list.setCurrentRow(cur_row)

    def _on_pick_logo_file(self):
        path_str, _ = QFileDialog.getOpenFileName(
            self, "Choisir un logo", str(self._logos_dir or Path.home()),
            "Images (*.png *.jpg *.jpeg *.gif *.bmp);;Tous (*.*)",
        )
        if not path_str:
            return
        # v0.9 : import dans le store local et stockage du nom relatif
        from printou.core.logos_store import import_logo
        try:
            relative_name = import_logo(Path(path_str))
        except Exception as e:
            QMessageBox.warning(self, "Erreur logo", f"Impossible d'importer : {e}")
            return
        self._prop_logo_path.setText(relative_name)
        self._on_layer_prop_changed()

    def _on_insert_placeholder(self, value: str):
        if not value.startswith("{"):
            return
        cursor = self._prop_text.textCursor()
        cursor.insertText(value)
        self._prop_placeholders.blockSignals(True)
        self._prop_placeholders.setCurrentIndex(0)
        self._prop_placeholders.blockSignals(False)
        self._on_layer_prop_changed()

    def _on_pick_color(self):
        item = self.layers_list.currentItem()
        if item is None or self._current_template is None:
            return
        idx = item.data(Qt.UserRole)
        layer = self._current_template.layers[idx]
        current = QColor(layer.get("color", "#000000"))
        color = QColorDialog.getColor(current, self, "Couleur du texte")
        if color.isValid():
            layer["color"] = color.name()
            self._dirty = True
            self.canvas.set_template(self._current_template)

    # ─── Save ───

    def _on_save(self):
        if self._current_template is None:
            return
        try:
            save_template(self._current_template, original_name=self._original_name)
            self._original_name = self._current_template.name
            self._dirty = False
            self.refresh_list()
            self.template_list_changed.emit()
            for i in range(self.templates_list.count()):
                if self.templates_list.item(i).data(Qt.UserRole).name == self._current_template.name:
                    self.templates_list.blockSignals(True)
                    self.templates_list.setCurrentRow(i)
                    self.templates_list.blockSignals(False)
                    break
            QMessageBox.information(
                self, "Enregistré",
                f"Template '{self._current_template.name}' sauvegardé.",
            )
        except Exception as e:
            QMessageBox.critical(self, "Erreur", str(e))

    def _confirm_discard(self) -> bool:
        if not self._dirty:
            return True
        ret = QMessageBox.question(
            self, "Modifications non sauvegardées",
            "Tu as des modifications non sauvegardées. Continuer ?",
            QMessageBox.Yes | QMessageBox.No,
        )
        return ret == QMessageBox.Yes
