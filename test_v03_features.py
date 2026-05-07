"""
Panneau droit v0.3.

Nouveautés :
- Sliders Hautes lumières (highlights) et Ombres (shadows)
- Bouton Auto-contraste qui analyse la photo et ajuste auto
- Throttle des sliders : émission throttlée à 60Hz max pendant le drag (super fluide)
- Émission finale au release (pour rendu HD)
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from printou.core import Adjustments, Commande, PhotoSource, Template
from printou.ui.theme import Colors


THROTTLE_MS = 16  # ~60 Hz pendant le drag


class _ThrottledSlider(QWidget):
    """Slider avec label + valeur + throttle.

    Émet `valueChanged(float)` à 60Hz max pendant un drag,
    et `valueCommitted(float)` au release.
    """
    valueChanged = Signal(float)
    valueCommitted = Signal(float)

    def __init__(self, label: str, min_val: float, max_val: float,
                 default: float, step: float = 0.01, parent=None):
        super().__init__(parent)
        self._min = min_val
        self._max = max_val
        self._default = default
        self._step = step

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)

        header = QHBoxLayout()
        self._label = QLabel(label)
        header.addWidget(self._label)
        header.addStretch()
        self._value_label = QLabel(self._format(default))
        self._value_label.setProperty("accent", True)
        self._value_label.setMinimumWidth(50)
        self._value_label.setAlignment(Qt.AlignRight)
        header.addWidget(self._value_label)
        layout.addLayout(header)

        self._slider = QSlider(Qt.Horizontal)
        self._slider.setMinimum(int(min_val / step))
        self._slider.setMaximum(int(max_val / step))
        self._slider.setValue(int(default / step))
        self._slider.valueChanged.connect(self._on_slider_changed)
        self._slider.sliderReleased.connect(self._on_slider_released)
        layout.addWidget(self._slider)

        # Throttle timer
        self._throttle = QTimer()
        self._throttle.setSingleShot(True)
        self._throttle.setInterval(THROTTLE_MS)
        self._throttle.timeout.connect(self._emit_throttled)
        self._pending_value: float | None = None

    def _format(self, val: float) -> str:
        if abs(val - 1.0) < 0.001 and self._default == 1.0:
            return "neutre"
        if abs(val) < 0.001 and self._default == 0.0:
            return "neutre"
        if self._default == 1.0:
            return f"×{val:.2f}"
        return f"{val:+.0f}"

    def _on_slider_changed(self, value: int):
        v = value * self._step
        self._value_label.setText(self._format(v))
        self._pending_value = v
        if not self._throttle.isActive():
            self._throttle.start()

    def _emit_throttled(self):
        if self._pending_value is not None:
            self.valueChanged.emit(self._pending_value)

    def _on_slider_released(self):
        if self._pending_value is not None:
            self.valueCommitted.emit(self._pending_value)

    def value(self) -> float:
        return self._slider.value() * self._step

    def reset(self):
        self._slider.setValue(int(self._default / self._step))

    def set_value(self, val: float):
        """Set programmatique sans émettre."""
        self._slider.blockSignals(True)
        self._slider.setValue(int(val / self._step))
        self._slider.blockSignals(False)
        self._value_label.setText(self._format(val))


class ControlsPanel(QWidget):
    rotation_changed = Signal(float)
    geometry_reset_requested = Signal()
    crop_mode_toggle_requested = Signal()  # toggle entre view et crop
    adjustments_changed = Signal(Adjustments)
    adjustments_reset_requested = Signal()
    auto_contrast_requested = Signal()
    format_selected = Signal(str)
    template_selected = Signal(str)
    print_all_requested = Signal()
    finish_commande_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._templates: list[Template] = []
        self._current_photo: PhotoSource | None = None
        self._init_ui()

    def _init_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(10)

        # Photo info
        self.photo_label = QLabel("Aucune photo sélectionnée")
        self.photo_label.setProperty("subheading", True)
        self.photo_label.setWordWrap(True)
        root.addWidget(self.photo_label)

        # Template
        tpl_box = QGroupBox("Template")
        tpl_layout = QVBoxLayout(tpl_box)
        self.template_combo = QComboBox()
        self.template_combo.currentTextChanged.connect(self._on_template_changed)
        tpl_layout.addWidget(self.template_combo)
        root.addWidget(tpl_box)

        # Géométrie
        geo_box = QGroupBox("Cadrage et rotation")
        geo_layout = QVBoxLayout(geo_box)

        # Bouton Recadrer (toggle mode)
        self.crop_btn = QPushButton("✂  Recadrer la photo")
        self.crop_btn.setCheckable(True)
        self.crop_btn.setToolTip("Active le mode recadrage (poignées). Entrée pour valider.")
        self.crop_btn.clicked.connect(self.crop_mode_toggle_requested.emit)
        geo_layout.addWidget(self.crop_btn)

        self.rotation_slider = _ThrottledSlider("Rotation", -15.0, 15.0, 0.0, step=0.1)
        self.rotation_slider.valueChanged.connect(self.rotation_changed.emit)
        geo_layout.addWidget(self.rotation_slider)

        self.reset_geo_btn = QPushButton("Réinitialiser cadrage et rotation")
        self.reset_geo_btn.clicked.connect(self.geometry_reset_requested.emit)
        geo_layout.addWidget(self.reset_geo_btn)
        root.addWidget(geo_box)

        # Retouches : sliders
        adj_box = QGroupBox("Retouche")
        adj_layout = QVBoxLayout(adj_box)
        adj_layout.setSpacing(8)

        self.brightness_slider = _ThrottledSlider("Luminosité", 0.5, 1.5, 1.0, step=0.01)
        self.contrast_slider = _ThrottledSlider("Contraste", 0.5, 1.5, 1.0, step=0.01)
        self.highlights_slider = _ThrottledSlider("Hautes lumières", -100.0, 100.0, 0.0, step=1.0)
        self.shadows_slider = _ThrottledSlider("Ombres", -100.0, 100.0, 0.0, step=1.0)
        self.saturation_slider = _ThrottledSlider("Saturation", 0.0, 2.0, 1.0, step=0.01)
        self.sharpness_slider = _ThrottledSlider("Netteté", 0.0, 2.0, 1.0, step=0.01)
        self.temperature_slider = _ThrottledSlider("Température", -50.0, 50.0, 0.0, step=1.0)

        self._all_adj_sliders = [
            self.brightness_slider, self.contrast_slider,
            self.highlights_slider, self.shadows_slider,
            self.saturation_slider, self.sharpness_slider,
            self.temperature_slider,
        ]
        for s in self._all_adj_sliders:
            s.valueChanged.connect(self._on_adjustment_changed)
            adj_layout.addWidget(s)

        # Boutons retouche
        adj_buttons = QHBoxLayout()
        self.auto_contrast_btn = QPushButton("✨ Auto-contraste")
        self.auto_contrast_btn.setToolTip("Analyse la photo et ajuste automatiquement contraste/luminosité")
        self.auto_contrast_btn.clicked.connect(self.auto_contrast_requested.emit)
        adj_buttons.addWidget(self.auto_contrast_btn)

        self.reset_adj_btn = QPushButton("Réinitialiser")
        self.reset_adj_btn.clicked.connect(self._on_reset_adjustments)
        adj_buttons.addWidget(self.reset_adj_btn)
        adj_layout.addLayout(adj_buttons)

        root.addWidget(adj_box)

        # Récap des tirages
        tirages_box = QGroupBox("Tirages à imprimer")
        tirages_layout = QVBoxLayout(tirages_box)
        self.tirages_table = QTableWidget()
        self.tirages_table.setColumnCount(2)
        self.tirages_table.setHorizontalHeaderLabels(["Format", "Quantité"])
        self.tirages_table.verticalHeader().setVisible(False)
        self.tirages_table.horizontalHeader().setStretchLastSection(True)
        self.tirages_table.setColumnWidth(0, 100)
        self.tirages_table.setMaximumHeight(120)
        self.tirages_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.tirages_table.itemSelectionChanged.connect(self._on_tirage_selected)
        tirages_layout.addWidget(self.tirages_table)
        root.addWidget(tirages_box)

        root.addStretch()

        # Séparateur
        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet(f"background-color: {Colors.BORDER}; max-height: 1px;")
        root.addWidget(sep)

        # GROS bouton imprimer
        self.print_all_btn = QPushButton("🖨  IMPRIMER (tous formats)")
        self.print_all_btn.setProperty("primary", True)
        self.print_all_btn.setMinimumHeight(54)
        self.print_all_btn.setStyleSheet(f"""
            QPushButton {{
                background: {Colors.ACCENT};
                color: {Colors.BG};
                border: none;
                border-radius: 8px;
                padding: 14px;
                font-size: 16px;
                font-weight: 700;
            }}
            QPushButton:hover {{
                background: {Colors.ACCENT_HOVER};
            }}
            QPushButton:disabled {{
                background: {Colors.BORDER};
                color: {Colors.TEXT_MUTED};
            }}
        """)
        self.print_all_btn.clicked.connect(self.print_all_requested.emit)
        root.addWidget(self.print_all_btn)

        self.finish_btn = QPushButton("✓ Marquer commande terminée")
        self.finish_btn.clicked.connect(self.finish_commande_requested.emit)
        root.addWidget(self.finish_btn)

        self.set_enabled_controls(False)

    def set_enabled_controls(self, enabled: bool):
        for w in [self.template_combo, self.reset_geo_btn,
                  self.tirages_table, self.print_all_btn, self.finish_btn,
                  self.reset_adj_btn, self.auto_contrast_btn, self.crop_btn]:
            w.setEnabled(enabled)
        self.rotation_slider.setEnabled(enabled)
        for s in self._all_adj_sliders:
            s.setEnabled(enabled)

    def set_crop_mode_active(self, active: bool):
        """Met à jour visuellement le bouton Recadrer."""
        self.crop_btn.blockSignals(True)
        self.crop_btn.setChecked(active)
        self.crop_btn.setText("✓  Valider le recadrage" if active else "✂  Recadrer la photo")
        self.crop_btn.blockSignals(False)

    def set_templates(self, templates: list[Template]):
        self._templates = templates
        self.template_combo.blockSignals(True)
        self.template_combo.clear()
        for t in templates:
            self.template_combo.addItem(t.name)
        self.template_combo.blockSignals(False)

    def set_current_template(self, template_name: str):
        idx = self.template_combo.findText(template_name)
        if idx >= 0:
            self.template_combo.setCurrentIndex(idx)

    def get_current_template(self) -> Template | None:
        name = self.template_combo.currentText()
        for t in self._templates:
            if t.name == name:
                return t
        return None

    def set_photo(self, commande: Commande, photo: PhotoSource):
        self._current_photo = photo
        client_part = commande.display_name
        self.photo_label.setText(
            f"<b>{photo.base_name}</b><br>"
            f"<span style='color:{Colors.TEXT_DIM}'>{client_part}</span>",
        )

        self.tirages_table.setRowCount(0)
        for fmt_code, tirage in photo.tirages.items():
            row = self.tirages_table.rowCount()
            self.tirages_table.insertRow(row)
            self.tirages_table.setItem(row, 0, QTableWidgetItem(fmt_code))
            self.tirages_table.setItem(row, 1, QTableWidgetItem(f"×{tirage.quantity}"))

        if self.tirages_table.rowCount() > 0:
            self.tirages_table.selectRow(0)

        self.set_enabled_controls(True)

    def reset_geometry_ui(self):
        self.rotation_slider.reset()

    def reset_adjustments_ui(self):
        for s in self._all_adj_sliders:
            s.reset()

    def apply_adjustments_to_ui(self, adj: Adjustments):
        """Applique des Adjustments aux sliders sans émettre de signal en cascade."""
        self.brightness_slider.set_value(adj.brightness)
        self.contrast_slider.set_value(adj.contrast)
        self.highlights_slider.set_value(adj.highlights)
        self.shadows_slider.set_value(adj.shadows)
        self.saturation_slider.set_value(adj.saturation)
        self.sharpness_slider.set_value(adj.sharpness)
        self.temperature_slider.set_value(adj.temperature)
        # Maintenant on émet UNE FOIS pour déclencher le re-render
        self.adjustments_changed.emit(self.get_current_adjustments())

    def get_current_adjustments(self) -> Adjustments:
        return Adjustments(
            brightness=self.brightness_slider.value(),
            contrast=self.contrast_slider.value(),
            saturation=self.saturation_slider.value(),
            sharpness=self.sharpness_slider.value(),
            temperature=self.temperature_slider.value(),
            highlights=self.highlights_slider.value(),
            shadows=self.shadows_slider.value(),
        )

    def clear_photo(self):
        self._current_photo = None
        self.photo_label.setText("Aucune photo sélectionnée")
        self.tirages_table.setRowCount(0)
        self.reset_geometry_ui()
        self.reset_adjustments_ui()
        self.set_enabled_controls(False)

    def _on_adjustment_changed(self, _value: float):
        self.adjustments_changed.emit(self.get_current_adjustments())

    def _on_reset_adjustments(self):
        self.reset_adjustments_ui()
        self.adjustments_changed.emit(self.get_current_adjustments())

    def _on_template_changed(self, name: str):
        if name:
            self.template_selected.emit(name)

    def _on_tirage_selected(self):
        row = self.tirages_table.currentRow()
        if row >= 0:
            fmt_item = self.tirages_table.item(row, 0)
            if fmt_item:
                self.format_selected.emit(fmt_item.text())
