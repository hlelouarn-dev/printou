"""
Modèle de template Printou V3.

Nouveautés V3 (sur retour Hervé) :
- Marges 4 côtés paramétrables en % (haut/bas/gauche/droite)
- Algorithme de remplissage : étirement max 3% + crop centré
- Logos collés au bord de l'image (paddings paramétrables)
- Texte près de l'image (pas centré dans bandeau)
- Logo vedette en haut optionnel (présent ou pas selon template)

Tout est en proportions relatives (0.0 à 1.0) pour rester adaptatif aux formats.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Literal


Orientation = Literal["portrait", "paysage"]


# ============================================================
# Marges du template (4 côtés)
# ============================================================

@dataclass
class Margins:
    """Marges du template, en pourcentages du papier (0.0 à 100.0)."""
    top: float = 4.0       # % de la hauteur
    bottom: float = 8.0    # % de la hauteur
    left: float = 4.0      # % de la largeur
    right: float = 4.0     # % de la largeur

    def to_pixels(self, paper_w: int, paper_h: int) -> tuple[int, int, int, int]:
        """Retourne (top, bottom, left, right) en pixels."""
        return (
            round(paper_h * self.top / 100),
            round(paper_h * self.bottom / 100),
            round(paper_w * self.left / 100),
            round(paper_w * self.right / 100),
        )

    def image_zone_pixels(self, paper_w: int, paper_h: int) -> tuple[int, int, int, int]:
        """Retourne (x, y, width, height) de la zone image en pixels."""
        top, bottom, left, right = self.to_pixels(paper_w, paper_h)
        return (left, top, paper_w - left - right, paper_h - top - bottom)


# ============================================================
# Calques (logos et textes)
# ============================================================

# Système d'ancrage : un calque peut être positionné par rapport à 9 points de
# référence soit du papier, soit de la zone image, soit du bandeau bas.
# C'est ce qui permet d'avoir des templates qui s'adaptent automatiquement.

ReferenceFrame = Literal[
    "paper",        # repère = papier entier
    "image",        # repère = zone image
    "bottom_band",  # repère = bandeau bas (sous l'image)
    "top_band",     # repère = bandeau haut (au-dessus de l'image)
    "left_band",    # repère = bandeau gauche (à gauche de l'image)
    "right_band",   # repère = bandeau droite (à droite de l'image)
]

# Anchor : 2 lettres pour le coin du calque (ex: "lt" = left-top)
# h: l(eft) c(enter) r(ight)
# v: t(op) m(iddle) b(ottom)
Anchor = Literal[
    "lt", "ct", "rt",
    "lm", "cm", "rm",
    "lb", "cb", "rb",
]


@dataclass
class LogoLayer:
    """Logo PNG sur le tirage."""
    kind: Literal["logo"] = "logo"
    name: str = ""                          # nom de référence (pour l'éditeur)
    asset_name: str = ""                    # nom du fichier dans la bibliothèque de logos
    reference: ReferenceFrame = "paper"     # par rapport à quel cadre
    pos_x_pct: float = 0.0                  # position X en % du cadre de référence
    pos_y_pct: float = 0.0                  # position Y en % du cadre de référence
    anchor: Anchor = "lt"                   # quel coin du logo va à (pos_x, pos_y)
    width_pct: float = 10.0                 # largeur en % de la largeur du PAPIER
    height_pct: float = 0.0                 # 0 = auto (ratio natif PNG)
    opacity: float = 1.0
    z_order: int = 0


@dataclass
class TextLayer:
    """Texte sur le tirage. Supporte les placeholders {event_name}, {event_date}, etc."""
    kind: Literal["text"] = "text"
    name: str = ""
    content: str = ""
    reference: ReferenceFrame = "paper"
    pos_x_pct: float = 0.0
    pos_y_pct: float = 0.0
    anchor: Anchor = "lt"
    font_family: str = "Segoe UI"
    font_size_pct: float = 2.5              # taille en % de la HAUTEUR du papier
    color: str = "#FFFFFF"
    bold: bool = True
    italic: bool = False
    z_order: int = 0


# ============================================================
# Template
# ============================================================

@dataclass
class Template:
    """Template Printou V3.

    Un template = une combinaison de marges + une liste de calques.
    Le même template peut être appliqué sur n'importe quel format papier
    grâce aux proportions relatives.
    """
    name: str
    orientation: Orientation                # le template est portrait OU paysage
    margins: Margins = field(default_factory=Margins)
    background_color: str = "#000000"
    max_stretch_pct: float = 3.0            # tolérance de déformation avant crop
    layers: list[dict] = field(default_factory=list)  # mix LogoLayer/TextLayer sérialisés

    def to_json(self, path: Path) -> None:
        path.write_text(
            json.dumps(asdict(self), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    @classmethod
    def from_json(cls, path: Path) -> "Template":
        data = json.loads(path.read_text(encoding="utf-8"))
        data["margins"] = Margins(**data["margins"])
        return cls(**data)

    def get_layers_sorted(self) -> list[dict]:
        return sorted(self.layers, key=lambda layer: layer.get("z_order", 0))


# ============================================================
# Formats de tirage
# ============================================================

@dataclass(frozen=True)
class PrintFormat:
    code: str
    label: str
    width_mm: float
    height_mm: float
    dpi: int = 300

    @property
    def ratio(self) -> float:
        return self.width_mm / self.height_mm

    def pixel_size(self, orientation: Orientation) -> tuple[int, int]:
        w_mm, h_mm = self.width_mm, self.height_mm
        if orientation == "portrait":
            w_mm, h_mm = min(w_mm, h_mm), max(w_mm, h_mm)
        else:
            w_mm, h_mm = max(w_mm, h_mm), min(w_mm, h_mm)
        return (
            round(w_mm / 25.4 * self.dpi),
            round(h_mm / 25.4 * self.dpi),
        )


FORMATS: dict[str, PrintFormat] = {
    "15x23":  PrintFormat("15x23",  "15×23 cm (A5)", 152.0, 229.0),
    "20x30":  PrintFormat("20x30",  "20×30 cm (A4)", 203.0, 305.0),
    "A3":     PrintFormat("A3",     "A3 (29.7×42 cm)", 297.0, 420.0),
    "A2":     PrintFormat("A2",     "A2 (42×59.4 cm)", 420.0, 594.0),
}


def detect_orientation_from_size(width: int, height: int) -> Orientation:
    return "portrait" if height > width else "paysage"
