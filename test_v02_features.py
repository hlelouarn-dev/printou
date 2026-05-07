"""
Panneau gauche v0.4 : liste commandes avec FIX anti-flicker.

Bug corrigé v0.4 : le watcher déclenchait un refresh complet à chaque modif fichier
(et Windows touche les fichiers tout le temps), ce qui provoquait un clignotement
visible. Fix en 2 temps :

1. Debounce 500 ms : les rafales de notifications sont coalescées
2. Refresh "diff" : on compare l'arbre actuel avec le scan, et on n'ajoute/supprime
   QUE les commandes/photos qui ont vraiment changé. Pas de clear+rebuild brutal.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from printou.core import Commande, PhotoSource, scan_commande
from printou.ui.theme import Colors


REFRESH_DEBOUNCE_MS = 500


class CommandeTreeItem(QTreeWidgetItem):
    def __init__(self, commande: Commande):
        super().__init__()
        self.commande = commande
        self._setup_text()
        font = QFont()
        font.setBold(True)
        self.setFont(0, font)

    def _setup_text(self):
        cmd = self.commande
        date_str = ""
        if cmd.creation_time > 0:
            date_str = datetime.fromtimestamp(cmd.creation_time).strftime("%d/%m %H:%M")
        self.setText(0, f"📁 {cmd.display_name}")
        self.setText(1, f"{len(cmd.photos)} photo(s) • {cmd.total_tirages} tirage(s) • {date_str}")

    def update_from(self, commande: Commande):
        self.commande = commande
        self._setup_text()


class PhotoTreeItem(QTreeWidgetItem):
    def __init__(self, photo: PhotoSource, commande: Commande):
        super().__init__()
        self.photo = photo
        self.commande = commande
        self._traitee = False
        self._setup_text()

    def _setup_text(self):
        formats_str = " · ".join(
            f"{fmt} ×{t.quantity}" for fmt, t in self.photo.tirages.items()
        )
        prefix = "  ✓" if self._traitee else "  📸"
        self.setText(0, f"{prefix} {self.photo.base_name}")
        self.setText(1, formats_str)

    def mark_traitee(self):
        self._traitee = True
        self._setup_text()


class CommandesPanel(QWidget):
    photo_selected = Signal(object, object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._init_ui()
        self._commandes_root: Path | None = None

        # Debounce du refresh
        self._refresh_timer = QTimer()
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.setInterval(REFRESH_DEBOUNCE_MS)
        self._refresh_timer.timeout.connect(self._do_refresh)

        # Index des items courants : nom_dossier → CommandeTreeItem
        # Permet le diff rapide sans clear+rebuild
        self._cmd_index: dict[str, CommandeTreeItem] = {}

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        header = QHBoxLayout()
        title = QLabel("Commandes en attente")
        title.setProperty("heading", True)
        header.addWidget(title)
        header.addStretch()
        self.refresh_btn = QPushButton("↻")
        self.refresh_btn.setFixedSize(40, 32)
        self.refresh_btn.setToolTip("Rafraîchir la liste (F5)")
        self.refresh_btn.clicked.connect(self.refresh_now)
        header.addWidget(self.refresh_btn)
        layout.addLayout(header)

        self.search = QLineEdit()
        self.search.setPlaceholderText("🔍 Filtrer (nom client ou photo)…")
        self.search.textChanged.connect(self._apply_filter)
        layout.addWidget(self.search)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Commande / Photo", "Détails"])
        self.tree.header().setSectionResizeMode(0, QHeaderView.Stretch)
        self.tree.header().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.tree.setRootIsDecorated(False)
        self.tree.setIndentation(0)
        self.tree.setAlternatingRowColors(True)
        # Important : désactiver les animations qui pourraient causer du flicker
        self.tree.setAnimated(False)
        self.tree.itemClicked.connect(self._on_item_clicked)
        layout.addWidget(self.tree, stretch=1)

        self.empty_label = QLabel(
            "Configurez le dossier des commandes\ndans Paramètres > Dossiers."
        )
        self.empty_label.setAlignment(Qt.AlignCenter)
        self.empty_label.setProperty("muted", True)
        self.empty_label.setVisible(False)
        layout.addWidget(self.empty_label)

        self.status_label = QLabel("")
        self.status_label.setProperty("muted", True)
        layout.addWidget(self.status_label)

    def set_commandes_root(self, root: Path | None):
        self._commandes_root = root
        # Reset complet quand on change de dossier
        self.tree.clear()
        self._cmd_index.clear()
        self.refresh_now()

    def refresh(self):
        """Refresh debouncé : à appeler depuis le watcher (peut spammer)."""
        if not self._refresh_timer.isActive():
            self._refresh_timer.start()

    def refresh_now(self):
        """Refresh immédiat : à appeler depuis F5 ou un changement de config."""
        self._refresh_timer.stop()
        self._do_refresh()

    def _do_refresh(self):
        """Exécute le refresh effectif avec diff intelligent (pas de clear/rebuild)."""
        if self._commandes_root is None or not self._commandes_root.is_dir():
            self.tree.clear()
            self._cmd_index.clear()
            self.empty_label.setText("Configurez le dossier des commandes\ndans Paramètres > Dossiers.")
            self.empty_label.setVisible(True)
            self.status_label.setText("")
            return

        # Scan
        commande_dirs = [p for p in self._commandes_root.iterdir() if p.is_dir()]
        scanned: list[Commande] = []
        for cmd_dir in commande_dirs:
            cmd = scan_commande(cmd_dir)
            if cmd.photos:
                scanned.append(cmd)
        scanned.sort(key=lambda c: c.creation_time)

        # Diff
        new_names = {c.name for c in scanned}
        existing_names = set(self._cmd_index.keys())

        # 1. Suppressions : commandes qui ne sont plus là
        to_remove = existing_names - new_names
        for name in to_remove:
            item = self._cmd_index.pop(name, None)
            if item is not None:
                idx = self.tree.indexOfTopLevelItem(item)
                if idx >= 0:
                    self.tree.takeTopLevelItem(idx)

        # 2. Mises à jour des commandes existantes (pas d'ajout dans l'arbre, on touche que les enfants)
        for cmd in scanned:
            existing_item = self._cmd_index.get(cmd.name)
            if existing_item is not None:
                if len(existing_item.commande.photos) != len(cmd.photos):
                    while existing_item.childCount():
                        existing_item.takeChild(0)
                    for photo in cmd.photos:
                        existing_item.addChild(PhotoTreeItem(photo, cmd))
                existing_item.update_from(cmd)

        # 3. Ré-organisation complète sans flicker : on calcule l'ordre voulu,
        # on prend les items existants + on crée les nouveaux, et on les insère
        # dans le bon ordre. Pour minimiser le flash, on désactive les updates Qt.
        self.tree.setUpdatesEnabled(False)
        try:
            current_selection = self.tree.currentItem()

            # Détacher tous les items top-level dans un mapping {name: item}
            detached: dict[str, CommandeTreeItem] = {}
            while self.tree.topLevelItemCount():
                item = self.tree.takeTopLevelItem(0)
                if isinstance(item, CommandeTreeItem):
                    detached[item.commande.name] = item

            # Re-insérer dans l'ordre désiré, en créant les nouveaux items à la volée
            for cmd in scanned:
                if cmd.name in detached:
                    item = detached[cmd.name]
                else:
                    # Item nouveau : on le crée maintenant (s'il n'a pas été créé via _cmd_index)
                    if cmd.name in self._cmd_index:
                        item = self._cmd_index[cmd.name]
                    else:
                        item = CommandeTreeItem(cmd)
                        for photo in cmd.photos:
                            item.addChild(PhotoTreeItem(photo, cmd))
                        self._cmd_index[cmd.name] = item
                self.tree.addTopLevelItem(item)
                item.setExpanded(True)

            if current_selection is not None:
                self.tree.setCurrentItem(current_selection)
        finally:
            self.tree.setUpdatesEnabled(True)

        # 4. Status
        if not scanned:
            self.empty_label.setText("Aucune commande dans le dossier surveillé.")
            self.empty_label.setVisible(True)
            self.status_label.setText("")
        else:
            self.empty_label.setVisible(False)
            nb_tirages = sum(c.total_tirages for c in scanned)
            self.status_label.setText(
                f"{len(scanned)} commande(s) • {nb_tirages} tirage(s) • triées par ancienneté"
            )

    def add_commande(self, folder: Path):
        """Appelé par le watcher quand un nouveau dossier est créé."""
        # On déclenche le refresh debouncé. Le watcher peut spammer, c'est pas grave.
        self.refresh()

    def select_next_photo(self, after_photo: PhotoSource | None = None) -> bool:
        items: list[PhotoTreeItem] = []
        for i in range(self.tree.topLevelItemCount()):
            cmd_item = self.tree.topLevelItem(i)
            for j in range(cmd_item.childCount()):
                child = cmd_item.child(j)
                if isinstance(child, PhotoTreeItem) and not child._traitee:
                    items.append(child)

        if not items:
            return False

        if after_photo is None:
            target = items[0]
        else:
            try:
                idx = next(
                    i for i, it in enumerate(items)
                    if it.photo.base_name == after_photo.base_name
                )
                target = items[idx + 1] if idx + 1 < len(items) else items[0]
            except StopIteration:
                target = items[0]

        self.tree.setCurrentItem(target)
        self.photo_selected.emit(target.commande, target.photo)
        return True

    def mark_current_photo_traitee(self):
        current = self.tree.currentItem()
        if isinstance(current, PhotoTreeItem):
            current.mark_traitee()

    def _apply_filter(self, text: str):
        text = text.lower().strip()
        for i in range(self.tree.topLevelItemCount()):
            cmd_item = self.tree.topLevelItem(i)
            cmd_match = text in cmd_item.text(0).lower()
            any_photo_match = False
            for j in range(cmd_item.childCount()):
                photo_item = cmd_item.child(j)
                photo_match = text in photo_item.text(0).lower()
                photo_item.setHidden(bool(text) and not (cmd_match or photo_match))
                if photo_match:
                    any_photo_match = True
            cmd_item.setHidden(bool(text) and not (cmd_match or any_photo_match))

    def _on_item_clicked(self, item, _column):
        if isinstance(item, PhotoTreeItem):
            self.photo_selected.emit(item.commande, item.photo)
