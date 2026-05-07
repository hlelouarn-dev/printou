"""Dialogue Paramètres : configuration de l'application."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from printou.core import FORMATS, AppConfig, EventInfo


class _PathField(QWidget):
    """Champ chemin avec bouton 'Parcourir'."""

    def __init__(self, placeholder: str = "", is_dir: bool = True, parent=None):
        super().__init__(parent)
        self._is_dir = is_dir
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.line = QLineEdit()
        self.line.setPlaceholderText(placeholder)
        layout.addWidget(self.line)
        self.btn = QPushButton("Parcourir…")
        self.btn.clicked.connect(self._browse)
        layout.addWidget(self.btn)

    def _browse(self):
        if self._is_dir:
            path = QFileDialog.getExistingDirectory(self, "Choisir un dossier", self.line.text())
        else:
            path, _ = QFileDialog.getOpenFileName(self, "Choisir un fichier", self.line.text())
        if path:
            self.line.setText(path)

    def text(self) -> str:
        return self.line.text()

    def setText(self, text: str):
        self.line.setText(text)


class ParametresDialog(QDialog):
    """Dialogue de configuration."""

    def __init__(self, config: AppConfig, parent=None):
        super().__init__(parent)
        self.config = config
        self.setWindowTitle("Paramètres - Printou")
        self.resize(720, 600)
        self._init_ui()
        self._load_config()

    def _init_ui(self):
        layout = QVBoxLayout(self)

        tabs = QTabWidget()
        layout.addWidget(tabs)

        # ─── Onglet Dossiers ───
        dossiers_widget = QWidget()
        dossiers_layout = QFormLayout(dossiers_widget)
        dossiers_layout.setSpacing(12)

        self.commandes_root = _PathField("Dossier surveillé contenant les commandes clients")
        dossiers_layout.addRow("Commandes entrantes", self.commandes_root)

        self.commandes_traitees = _PathField("Dossier où déplacer les commandes une fois traitées")
        dossiers_layout.addRow("Commandes traitées", self.commandes_traitees)

        self.logos_dir = _PathField("Bibliothèque de logos PNG")
        dossiers_layout.addRow("Logos", self.logos_dir)

        self.templates_dir = _PathField("Dossier des templates JSON")
        dossiers_layout.addRow("Templates", self.templates_dir)

        self.exports_dir = _PathField("Dossier de sortie pour les exports A3/A2 Canon")
        dossiers_layout.addRow("Exports A3/A2", self.exports_dir)

        tabs.addTab(dossiers_widget, "📁 Dossiers")

        # ─── Onglet Hotfolders DNP ───
        hotfolders_widget = QWidget()
        hotfolders_layout = QFormLayout(hotfolders_widget)
        hotfolders_layout.setSpacing(12)

        intro = QLabel(
            "Configurez les dossiers surveillés par DNP Hot Folder Print Utility.\n"
            "Printou y déposera les JPEG finaux qui seront imprimés automatiquement."
        )
        intro.setProperty("muted", True)
        intro.setWordWrap(True)
        hotfolders_layout.addRow(intro)

        self.hotfolder_fields: dict[str, _PathField] = {}
        for code, fmt in FORMATS.items():
            if code in ("A3", "A2"):
                continue  # ces formats partent vers Exports, pas un hotfolder DNP
            field = _PathField(f"C:\\DNP\\Hot Folder\\{code}")
            self.hotfolder_fields[code] = field
            hotfolders_layout.addRow(f"Hotfolder {fmt.label}", field)

        tabs.addTab(hotfolders_widget, "🖨 Hotfolders DNP")

        # ─── Onglet Événement ───
        event_widget = QWidget()
        event_layout = QFormLayout(event_widget)
        event_layout.setSpacing(12)

        intro2 = QLabel(
            "Métadonnées affichées sur les tirages via les placeholders {event_name}, "
            "{event_location}, {event_date} dans le template."
        )
        intro2.setProperty("muted", True)
        intro2.setWordWrap(True)
        event_layout.addRow(intro2)

        self.event_name = QLineEdit()
        self.event_name.setPlaceholderText("Ex: 10° ÉDITION DU CONCOURS HARCOUR")
        event_layout.addRow("Nom de l'événement", self.event_name)

        self.event_location = QLineEdit()
        self.event_location.setPlaceholderText("Ex: LE MANS")
        event_layout.addRow("Lieu", self.event_location)

        self.event_date = QLineEdit()
        self.event_date.setPlaceholderText("Ex: AVRIL 2026")
        event_layout.addRow("Date", self.event_date)

        tabs.addTab(event_widget, "🎪 Événement")

        # ─── Boutons ───
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._save_and_close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _load_config(self):
        self.commandes_root.setText(self.config.commandes_root)
        self.commandes_traitees.setText(self.config.commandes_traitees)
        self.logos_dir.setText(self.config.logos_dir)
        self.templates_dir.setText(self.config.templates_dir)
        self.exports_dir.setText(self.config.exports_dir)

        for code, field in self.hotfolder_fields.items():
            hf = self.config.get_hotfolder(code)
            if hf:
                field.setText(str(hf))

        info = self.config.get_event_info()
        self.event_name.setText(info.name)
        self.event_location.setText(info.location)
        self.event_date.setText(info.date)

    def _save_and_close(self):
        self.config.commandes_root = self.commandes_root.text()
        self.config.commandes_traitees = self.commandes_traitees.text()
        self.config.logos_dir = self.logos_dir.text()
        self.config.templates_dir = self.templates_dir.text()
        self.config.exports_dir = self.exports_dir.text()

        for code, field in self.hotfolder_fields.items():
            self.config.set_hotfolder(code, field.text())

        self.config.set_event_info(EventInfo(
            name=self.event_name.text(),
            location=self.event_location.text(),
            date=self.event_date.text(),
        ))

        self.config.save()
        self.accept()
