"""
Opérations sur les photos v0.2 :
- Réglages tonals enrichis (sliders manuels)
- Géométrie : rotation + crop manuel
- Math du plus grand rectangle inscrit dans rectangle tourné (auto-shrink crop)
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field

from PIL import Image, ImageEnhance


# ============================================================
# Réglages tonals (sliders manuels)
# ============================================================

@dataclass
class Adjustments:
    """Réglages tonals.

    - brightness : 0.5 (sombre) à 1.5 (clair). Neutre = 1.0
    - contrast   : 0.5 à 1.5. Neutre = 1.0
    - saturation : 0.0 (N&B) à 2.0. Neutre = 1.0
    - sharpness  : 0.0 (flou) à 2.0. Neutre = 1.0
    - temperature: -50 (froid/bleu) à +50 (chaud/orange). Neutre = 0.0
    - highlights : -100 (assombrit hautes lumières) à +100 (éclaircit). Neutre = 0.0
    - shadows    : -100 (assombrit ombres) à +100 (déboucher ombres). Neutre = 0.0
    """
    brightness: float = 1.0
    contrast: float = 1.0
    saturation: float = 1.0
    sharpness: float = 1.0
    temperature: float = 0.0
    highlights: float = 0.0
    shadows: float = 0.0

    def is_neutral(self) -> bool:
        return (self.brightness == 1.0 and self.contrast == 1.0
                and self.saturation == 1.0 and self.sharpness == 1.0
                and self.temperature == 0.0
                and self.highlights == 0.0 and self.shadows == 0.0)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Adjustments":
        valid = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        return cls(**valid)


def apply_adjustments(img: Image.Image, adj: Adjustments) -> Image.Image:
    if adj.is_neutral():
        return img

    out = img
    if adj.brightness != 1.0:
        out = ImageEnhance.Brightness(out).enhance(adj.brightness)
    if adj.contrast != 1.0:
        out = ImageEnhance.Contrast(out).enhance(adj.contrast)
    if adj.saturation != 1.0:
        out = ImageEnhance.Color(out).enhance(adj.saturation)
    if adj.sharpness != 1.0:
        out = ImageEnhance.Sharpness(out).enhance(adj.sharpness)
    if adj.temperature != 0.0:
        out = _apply_temperature(out, adj.temperature)
    if adj.highlights != 0.0 or adj.shadows != 0.0:
        out = _apply_highlights_shadows(out, adj.highlights, adj.shadows)
    return out


def _apply_highlights_shadows(img: Image.Image, highlights: float, shadows: float) -> Image.Image:
    """Ajuste les hautes lumières et les ombres séparément.

    - highlights : agit sur les pixels lumineux (luminance > 50%)
    - shadows    : agit sur les pixels sombres (luminance < 50%)

    Algo simple : on calcule une LUT pour chaque canal en fonction de la valeur.
    Plus la valeur est claire (resp. sombre), plus on applique fortement le shift highlights
    (resp. shadows).
    """
    if img.mode != "RGB":
        img = img.convert("RGB")

    h_factor = highlights / 100.0  # ∈ [-1, +1]
    s_factor = shadows / 100.0

    # Construit une LUT 0..255 → 0..255
    lut = []
    for v in range(256):
        v_norm = v / 255.0  # 0..1

        # Poids highlights : 0 dans les ombres, 1 dans les lumières
        # On utilise une transition douce centrée sur 0.5
        # Highlight weight : (v - 0.5) clampé positif puis lissé
        h_weight = max(0.0, (v_norm - 0.5) * 2)  # 0..1
        h_weight = h_weight ** 1.5  # plus doux

        # Shadow weight : symétrique, max dans les ombres
        s_weight = max(0.0, (0.5 - v_norm) * 2)  # 0..1
        s_weight = s_weight ** 1.5

        # Décalage total
        delta = (h_factor * h_weight * 80) + (s_factor * s_weight * 80)
        # 80 = amplitude max en valeurs RGB (sur 255)

        new_v = int(round(v + delta))
        new_v = max(0, min(255, new_v))
        lut.append(new_v)

    # Appliquer la LUT à chaque canal
    full_lut = lut * 3  # même LUT pour R, G, B
    return img.point(full_lut)


def auto_contrast_adjustments(img: Image.Image) -> "Adjustments":
    """Analyse l'image et retourne des Adjustments qui boostent le contraste/luminosité.

    Algo : on regarde l'histogramme et on calcule :
    - Si l'image est globalement sombre/claire → ajuster brightness
    - Si la dynamique est faible (peu de noir et peu de blanc) → booster contrast
    - Si les hautes lumières sont écrasées → tirer un peu sur highlights
    - Si les ombres sont bouchées → débloquer un peu shadows
    """
    if img.mode != "RGB":
        img = img.convert("RGB")

    # Convertir en niveaux de gris pour l'analyse
    gray = img.convert("L")
    histogram = gray.histogram()
    total = sum(histogram)
    if total == 0:
        return Adjustments()

    # Calculer les percentiles 5% et 95%
    cumulative = 0
    p5 = 0
    p95 = 255
    for v, count in enumerate(histogram):
        cumulative += count
        if cumulative / total >= 0.05 and p5 == 0:
            p5 = v
        if cumulative / total >= 0.95:
            p95 = v
            break

    # Moyenne pondérée pour brightness
    mean = sum(v * c for v, c in enumerate(histogram)) / total

    # Décisions
    adj = Adjustments()

    # Contraste : si la dynamique est < 200 sur 255, on booste
    dynamic_range = p95 - p5
    if dynamic_range < 200:
        # Plus la dynamique est faible, plus on booste
        contrast_boost = (200 - dynamic_range) / 200.0 * 0.3  # max +0.3
        adj.contrast = 1.0 + contrast_boost

    # Luminosité : si moyenne très sombre/claire, ajuster légèrement
    if mean < 100:
        adj.brightness = 1.0 + (100 - mean) / 200.0  # max +0.5
    elif mean > 160:
        adj.brightness = 1.0 - (mean - 160) / 300.0  # max -0.32

    # Highlights : si p95 est très haut (>240), on tire un peu pour récupérer
    if p95 > 240:
        adj.highlights = -15.0  # tire les hautes lumières

    # Shadows : si p5 est très bas (<15), on débouche un peu
    if p5 < 15:
        adj.shadows = +20.0

    return adj


def _apply_temperature(img: Image.Image, temp: float) -> Image.Image:
    """Décalage de température : positif = chaud (+R, -B), négatif = froid (-R, +B)."""
    if img.mode != "RGB":
        img = img.convert("RGB")
    r, g, b = img.split()

    factor = temp / 50.0 * 0.15  # ±15% max sur R et B
    if factor > 0:
        r = r.point(lambda v: min(255, int(v * (1 + factor))))
        b = b.point(lambda v: max(0, int(v * (1 - factor))))
    else:
        r = r.point(lambda v: max(0, int(v * (1 + factor))))
        b = b.point(lambda v: min(255, int(v * (1 - factor))))

    return Image.merge("RGB", (r, g, b))


# Presets gardés pour rétro-compat
PRESETS: dict[str, Adjustments] = {
    "neutre":         Adjustments(),
    "clair_+1":       Adjustments(brightness=1.10),
    "clair_+2":       Adjustments(brightness=1.20),
    "fonce_-1":       Adjustments(brightness=0.92),
    "fonce_-2":       Adjustments(brightness=0.85),
    "punchy":         Adjustments(contrast=1.15, saturation=1.10, sharpness=1.20),
    "doux":           Adjustments(contrast=0.92, saturation=0.95),
    "noir_et_blanc":  Adjustments(saturation=0.0),
}


# ============================================================
# Géométrie (rotation + crop manuel)
# ============================================================

@dataclass
class CropRect:
    """Rectangle de crop en proportions relatives (0.0 à 1.0)."""
    x: float = 0.0
    y: float = 0.0
    width: float = 1.0
    height: float = 1.0

    def is_full(self) -> bool:
        return self.x == 0.0 and self.y == 0.0 and self.width == 1.0 and self.height == 1.0

    def to_pixels(self, img_w: int, img_h: int) -> tuple[int, int, int, int]:
        left = round(self.x * img_w)
        top = round(self.y * img_h)
        right = round((self.x + self.width) * img_w)
        bottom = round((self.y + self.height) * img_h)
        return (left, top, right, bottom)


@dataclass
class Geometry:
    rotation_deg: float = 0.0
    crop: CropRect = field(default_factory=CropRect)

    def is_identity(self) -> bool:
        return self.rotation_deg == 0.0 and self.crop.is_full()

    def to_dict(self) -> dict:
        return {"rotation_deg": self.rotation_deg, "crop": asdict(self.crop)}

    @classmethod
    def from_dict(cls, data: dict) -> "Geometry":
        return cls(
            rotation_deg=data.get("rotation_deg", 0.0),
            crop=CropRect(**data.get("crop", {})),
        )


def apply_geometry(img: Image.Image, geo: Geometry) -> Image.Image:
    if geo.is_identity():
        return img
    out = img
    if geo.rotation_deg != 0.0:
        # Convention de Printou (alignée sur Qt) : angle positif = rotation horaire.
        # Mais PIL.rotate utilise la convention mathématique (antihoraire).
        # Donc on inverse le signe pour rester cohérent avec la rotation Qt côté UI.
        out = out.rotate(
            -geo.rotation_deg,
            resample=Image.BICUBIC,
            expand=False,
            fillcolor=(0, 0, 0),
        )
    if not geo.crop.is_full():
        box = geo.crop.to_pixels(out.width, out.height)
        out = out.crop(box)
    return out


# ============================================================
# Math : plus grand rectangle inscrit dans rectangle tourné
# ============================================================

def largest_inscribed_rect_after_rotation(
    img_w: int, img_h: int, rotation_deg: float,
) -> tuple[float, float, float, float]:
    """Calcule la zone "safe" (sans bord noir) après rotation.

    Pour un rectangle WxH tourné de theta, on cherche le plus grand rectangle
    AXIS-ALIGNED inscrit, de MÊME ratio que l'original.

    Démonstration :
        On veut un rect (w', h') axis-aligned avec w'/h' = W/H qui tienne dans
        le rectangle tourné. Ce rectangle a pour bbox élargie :
            bb_w = W cos + H sin
            bb_h = W sin + H cos
        Mais le rect inscrit est plus petit que la bbox. Les contraintes de
        non-débordement projeté (sur les 4 côtés du rect tourné) donnent :
            w' cos + h' sin <= W
            w' sin + h' cos <= H
        En posant h' = w' * H/W, on obtient :
            w' <= W² / (W cos + H sin)
            w' <= W H / (W sin + H cos)
        On prend le min des deux.

    Retourne (x_rel, y_rel, width_rel, height_rel) en proportions de l'image
    originale (non tournée).
    """
    if rotation_deg == 0.0:
        return (0.0, 0.0, 1.0, 1.0)

    angle = math.radians(abs(rotation_deg))
    if angle >= math.pi / 4:
        # Au-delà de 45° le calcul s'effondre, on plafonne par sécurité
        angle = math.pi / 4 - 0.001

    cos_a = math.cos(angle)
    sin_a = math.sin(angle)

    # Formule (voir docstring)
    w_max_1 = (img_w * img_w) / (img_w * cos_a + img_h * sin_a)
    w_max_2 = (img_h * img_w) / (img_w * sin_a + img_h * cos_a)
    w_prime = min(w_max_1, w_max_2)
    h_prime = w_prime * img_h / img_w

    # On clamp au cas où la formule donnerait > taille originale (impossible mais sécu)
    w_prime = min(w_prime, float(img_w))
    h_prime = min(h_prime, float(img_h))

    x_rel = (img_w - w_prime) / 2 / img_w
    y_rel = (img_h - h_prime) / 2 / img_h
    return (x_rel, y_rel, w_prime / img_w, h_prime / img_h)


def safe_crop_for_rotation(
    img_w: int, img_h: int, rotation_deg: float,
    desired_crop: CropRect,
) -> CropRect:
    """Intersecte le crop souhaité avec la zone safe (sans bord noir)."""
    if rotation_deg == 0.0:
        return desired_crop

    safe_x, safe_y, safe_w, safe_h = largest_inscribed_rect_after_rotation(
        img_w, img_h, rotation_deg,
    )
    safe_right = safe_x + safe_w
    safe_bottom = safe_y + safe_h

    new_x = max(desired_crop.x, safe_x)
    new_y = max(desired_crop.y, safe_y)
    new_right = min(desired_crop.x + desired_crop.width, safe_right)
    new_bottom = min(desired_crop.y + desired_crop.height, safe_bottom)

    new_w = max(0.05, new_right - new_x)
    new_h = max(0.05, new_bottom - new_y)

    return CropRect(x=new_x, y=new_y, width=new_w, height=new_h)


# ============================================================
# Algorithme étirement + crop (inchangé v0.1)
# ============================================================

def fit_photo_to_zone(
    photo: Image.Image,
    zone_w: int,
    zone_h: int,
    max_stretch_pct: float = 3.0,
) -> Image.Image:
    src_w, src_h = photo.size
    src_ratio = src_w / src_h
    target_ratio = zone_w / zone_h
    ratio_diff_pct = abs(target_ratio - src_ratio) / src_ratio * 100

    if ratio_diff_pct <= max_stretch_pct:
        return photo.resize((zone_w, zone_h), Image.LANCZOS)

    if target_ratio > src_ratio:
        intermediate_ratio = src_ratio * (1 + max_stretch_pct / 100)
        intermediate_h = round(zone_w / intermediate_ratio)
        stretched = photo.resize((zone_w, intermediate_h), Image.LANCZOS)
        crop_top = (intermediate_h - zone_h) // 2
        return stretched.crop((0, crop_top, zone_w, crop_top + zone_h))
    else:
        intermediate_ratio = src_ratio / (1 + max_stretch_pct / 100)
        intermediate_w = round(zone_h * intermediate_ratio)
        stretched = photo.resize((intermediate_w, zone_h), Image.LANCZOS)
        crop_left = (intermediate_w - zone_w) // 2
        return stretched.crop((crop_left, 0, crop_left + zone_w, zone_h))


# ============================================================
# EXIF orientation
# ============================================================

def get_exif_orientation(image_path) -> int:
    """Lit le tag EXIF Orientation d'une photo. Retourne 1..8.

    1 = normal (pas de rotation)
    3 = 180°
    6 = rotation 90° dans le sens horaire (portrait)
    8 = rotation 90° dans le sens antihoraire (portrait)
    """
    try:
        with Image.open(image_path) as img:
            exif = img.getexif()
            if exif is not None:
                return exif.get(274, 1)  # 274 = tag Orientation
    except Exception:
        pass
    return 1


def exif_orientation_to_qt_rotation(orientation: int) -> int:
    """Convertit un tag EXIF en degrés de rotation à appliquer (sens horaire).

    Retourne 0, 90, 180 ou 270.
    """
    if orientation == 3:
        return 180
    if orientation == 6:
        return 90
    if orientation == 8:
        return 270
    return 0


def apply_exif_orientation(img: Image.Image) -> Image.Image:
    """Applique le tag EXIF Orientation à une image PIL si présent.

    Utilise PIL.ImageOps.exif_transpose qui gère tous les cas (rotations + miroirs).
    """
    from PIL import ImageOps
    return ImageOps.exif_transpose(img)
