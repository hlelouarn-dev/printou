"""
Moteur de rendu Printou V3.

Pipeline :
1. Charger la photo source
2. Appliquer la géométrie (rotation + crop manuel)
3. Vérifier l'orientation (refus si incompatible)
4. Appliquer les retouches tonales
5. Calculer les marges et la zone image (en pixels du canvas)
6. Adapter la photo (étirement intelligent + crop centré) pour remplir la zone
7. Composer le canvas final (fond + photo + calques)
8. Retourner l'image RGB
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .photo_ops import Adjustments, Geometry, apply_adjustments, apply_geometry, fit_photo_to_zone
from .template import (
    FORMATS,
    Margins,
    Orientation,
    PrintFormat,
    Template,
    detect_orientation_from_size,
)


class OrientationMismatchError(ValueError):
    """Photo orientation incompatible avec le template."""


# ============================================================
# Helpers couleurs et polices
# ============================================================

def hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    h = hex_color.lstrip("#")
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


# Cache de polices pour éviter de recharger 100 fois le même TTF
_FONT_CACHE: dict[tuple[str, int, bool, bool], ImageFont.FreeTypeFont] = {}


def find_font(family: str, size: int, bold: bool = False, italic: bool = False) -> ImageFont.FreeTypeFont:
    """Cherche une police par nom de famille. Cache le résultat."""
    key = (family, size, bold, italic)
    if key in _FONT_CACHE:
        return _FONT_CACHE[key]

    # Mapping de noms de famille vers des chemins potentiels
    candidates = []
    f_lower = family.lower().replace(" ", "")
    if "segoe" in f_lower:
        suffix = "z" if bold and italic else ("b" if bold else ("i" if italic else ""))
        candidates.append(f"C:\\Windows\\Fonts\\segoeui{suffix}.ttf")
    elif "arial" in f_lower:
        if bold and italic:
            candidates.append("C:\\Windows\\Fonts\\arialbi.ttf")
        elif bold:
            candidates.append("C:\\Windows\\Fonts\\arialbd.ttf")
        elif italic:
            candidates.append("C:\\Windows\\Fonts\\ariali.ttf")
        else:
            candidates.append("C:\\Windows\\Fonts\\arial.ttf")
    elif "calibri" in f_lower:
        if bold and italic:
            candidates.append("C:\\Windows\\Fonts\\calibriz.ttf")
        elif bold:
            candidates.append("C:\\Windows\\Fonts\\calibrib.ttf")
        elif italic:
            candidates.append("C:\\Windows\\Fonts\\calibrii.ttf")
        else:
            candidates.append("C:\\Windows\\Fonts\\calibri.ttf")
    elif "times" in f_lower:
        if bold:
            candidates.append("C:\\Windows\\Fonts\\timesbd.ttf")
        else:
            candidates.append("C:\\Windows\\Fonts\\times.ttf")
    elif "verdana" in f_lower:
        if bold:
            candidates.append("C:\\Windows\\Fonts\\verdanab.ttf")
        else:
            candidates.append("C:\\Windows\\Fonts\\verdana.ttf")
    elif "tahoma" in f_lower:
        if bold:
            candidates.append("C:\\Windows\\Fonts\\tahomabd.ttf")
        else:
            candidates.append("C:\\Windows\\Fonts\\tahoma.ttf")

    # Fallback Linux/Mac
    if bold:
        candidates += [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/System/Library/Fonts/Helvetica.ttc",
        ]
    else:
        candidates += [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/System/Library/Fonts/Helvetica.ttc",
        ]
    # Segoe UI fallback : si on est sur Windows mais que la famille n'est pas reconnue
    candidates.append(f"C:\\Windows\\Fonts\\segoeui{'b' if bold else ''}.ttf")

    for c in candidates:
        if Path(c).exists():
            try:
                font = ImageFont.truetype(c, size)
                _FONT_CACHE[key] = font
                return font
            except OSError:
                continue
    fallback = ImageFont.load_default()
    _FONT_CACHE[key] = fallback
    return fallback


# ============================================================
# Système d'ancrage : conversion (référence + pos %) → pixels papier absolus
# ============================================================

def reference_frame_pixels(
    reference: str,
    paper_w: int,
    paper_h: int,
    margins: Margins,
) -> tuple[int, int, int, int]:
    """Retourne (x, y, w, h) du cadre de référence en pixels papier.

    Les références possibles :
    - "paper"        → tout le papier
    - "image"        → la zone image (papier moins marges)
    - "top_band"     → le bandeau haut (rectangle complet, hauteur = marge top)
    - "bottom_band"  → le bandeau bas (rectangle complet, hauteur = marge bottom)
    - "left_band"    → le bandeau gauche
    - "right_band"   → le bandeau droite
    """
    top, bottom, left, right = margins.to_pixels(paper_w, paper_h)

    if reference == "paper":
        return (0, 0, paper_w, paper_h)
    elif reference == "image":
        return (left, top, paper_w - left - right, paper_h - top - bottom)
    elif reference == "top_band":
        return (0, 0, paper_w, top)
    elif reference == "bottom_band":
        return (0, paper_h - bottom, paper_w, bottom)
    elif reference == "left_band":
        return (0, 0, left, paper_h)
    elif reference == "right_band":
        return (paper_w - right, 0, right, paper_h)
    else:
        return (0, 0, paper_w, paper_h)


def apply_anchor_offset(x: int, y: int, w: int, h: int, anchor: str) -> tuple[int, int]:
    """Décale (x, y) selon l'ancrage du calque pour que x,y pointent au bon coin."""
    h_a = anchor[0] if len(anchor) >= 1 else "l"
    v_a = anchor[1] if len(anchor) >= 2 else "t"
    if h_a == "c":
        x -= w // 2
    elif h_a == "r":
        x -= w
    if v_a == "m":
        y -= h // 2
    elif v_a == "b":
        y -= h
    return (x, y)


# ============================================================
# Rendu des calques
# ============================================================

def draw_logo_layer(
    canvas: Image.Image,
    layer: dict,
    paper_w: int,
    paper_h: int,
    margins: Margins,
    logos_dir: Path,
) -> None:
    # v0.9 : on accepte 'asset_path' (canonique) ou 'asset_name' (rétro-compat)
    asset_value = layer.get("asset_path") or layer.get("asset_name") or ""
    if not asset_value:
        return

    # Résoudre le chemin :
    # - Si chemin absolu → on le prend tel quel (rétro-compat avec anciens templates)
    # - Sinon → relatif au logos_dir (le store user)
    asset_path_p = Path(asset_value)
    if not asset_path_p.is_absolute():
        asset_path_p = logos_dir / asset_value
    if not asset_path_p.exists():
        print(f"[Printou] Logo introuvable : {asset_path_p}")
        return

    logo = Image.open(asset_path_p).convert("RGBA")

    # Taille en % du papier
    width_pct = layer.get("width_pct", 10.0)
    height_pct = layer.get("height_pct", 0.0)
    target_w = round(paper_w * width_pct / 100) if width_pct > 0 else 0
    target_h = round(paper_h * height_pct / 100) if height_pct > 0 else 0

    if target_w > 0 and target_h == 0:
        target_h = round(logo.height * (target_w / logo.width))
    elif target_h > 0 and target_w == 0:
        target_w = round(logo.width * (target_h / logo.height))
    elif target_w == 0 and target_h == 0:
        target_w = logo.width
        target_h = logo.height

    if (target_w, target_h) != logo.size:
        logo = logo.resize((target_w, target_h), Image.LANCZOS)

    opacity = layer.get("opacity", 1.0)
    if opacity < 1.0:
        alpha = logo.split()[3]
        alpha = alpha.point(lambda v: round(v * opacity))
        logo.putalpha(alpha)

    # Calcul de la position via le système d'ancrage
    ref_x, ref_y, ref_w, ref_h = reference_frame_pixels(
        layer.get("reference", "paper"), paper_w, paper_h, margins,
    )
    pos_x_pct = layer.get("pos_x_pct", 0.0)
    pos_y_pct = layer.get("pos_y_pct", 0.0)
    abs_x = ref_x + round(ref_w * pos_x_pct / 100)
    abs_y = ref_y + round(ref_h * pos_y_pct / 100)

    anchor = layer.get("anchor", "lt")
    final_x, final_y = apply_anchor_offset(abs_x, abs_y, target_w, target_h, anchor)

    canvas.alpha_composite(logo, dest=(final_x, final_y))


def draw_text_layer(
    canvas: Image.Image,
    layer: dict,
    paper_w: int,
    paper_h: int,
    margins: Margins,
    placeholders: dict[str, str],
) -> None:
    content = layer["content"]
    for key, value in placeholders.items():
        content = content.replace("{" + key + "}", value)

    font_size_pct = layer.get("font_size_pct", 2.5)
    font_size_px = max(8, round(paper_h * font_size_pct / 100))

    font = find_font(
        layer.get("font_family", "Segoe UI"),
        font_size_px,
        bold=layer.get("bold", False),
        italic=layer.get("italic", False),
    )

    color = hex_to_rgb(layer.get("color", "#FFFFFF"))

    # Position via système d'ancrage (référence + pos%)
    ref_x, ref_y, ref_w, ref_h = reference_frame_pixels(
        layer.get("reference", "paper"), paper_w, paper_h, margins,
    )
    pos_x_pct = layer.get("pos_x_pct", 0.0)
    pos_y_pct = layer.get("pos_y_pct", 0.0)
    abs_x = ref_x + round(ref_w * pos_x_pct / 100)
    abs_y = ref_y + round(ref_h * pos_y_pct / 100)

    anchor = layer.get("anchor", "lt")
    # Pour PIL.draw.text, l'anchor est différent : "l/m/r" + "t/m/b" (m = baseline)
    # On utilise nos anchors directement, ils sont compatibles
    pil_anchor = anchor.replace("c", "m")

    draw = ImageDraw.Draw(canvas)
    draw.text((abs_x, abs_y), content, font=font, fill=color + (255,), anchor=pil_anchor)


# ============================================================
# Rendu principal
# ============================================================

@dataclass
class RenderRequest:
    photo_path: Path
    template: Template
    print_format: PrintFormat
    geometry: Geometry
    adjustments: Adjustments
    placeholders: dict[str, str]
    logos_dir: Path
    strict_orientation: bool = True


def render(req: RenderRequest) -> Image.Image:
    """Pipeline de rendu complet. Retourne une image PIL en RGB."""

    # 1. Charger la photo + appliquer EXIF orientation (les portraits sont stockés
    #    en paysage natif sur le capteur, avec un tag EXIF qui dit "tourne").
    photo = Image.open(req.photo_path)
    from .photo_ops import apply_exif_orientation
    photo = apply_exif_orientation(photo).convert("RGB")

    # 2. Géométrie (rotation + crop manuel)
    photo = apply_geometry(photo, req.geometry)

    # 3. Vérification orientation
    photo_orientation = detect_orientation_from_size(photo.width, photo.height)
    if req.strict_orientation and photo_orientation != req.template.orientation:
        raise OrientationMismatchError(
            f"Photo {photo_orientation} incompatible avec template {req.template.orientation}"
        )

    # 4. Retouches tonales
    photo = apply_adjustments(photo, req.adjustments)

    # 5. Canvas papier
    paper_w, paper_h = req.print_format.pixel_size(req.template.orientation)
    bg_color = hex_to_rgb(req.template.background_color)
    canvas = Image.new("RGBA", (paper_w, paper_h), bg_color + (255,))

    # 6. Zone image et photo adaptée
    img_x, img_y, img_w, img_h = req.template.margins.image_zone_pixels(paper_w, paper_h)
    fitted = fit_photo_to_zone(
        photo, img_w, img_h, max_stretch_pct=req.template.max_stretch_pct
    )
    canvas.paste(fitted.convert("RGBA"), (img_x, img_y))

    # 7. Calques (logos + textes) par z_order
    for layer in req.template.get_layers_sorted():
        kind = layer.get("kind")
        if kind == "logo":
            draw_logo_layer(canvas, layer, paper_w, paper_h, req.template.margins, req.logos_dir)
        elif kind == "text":
            draw_text_layer(canvas, layer, paper_w, paper_h, req.template.margins, req.placeholders)

    return canvas.convert("RGB")


def render_and_save(req: RenderRequest, output_path: Path, jpeg_quality: int = 95) -> None:
    img = render(req)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(output_path, "JPEG", quality=jpeg_quality, subsampling=0)


def render_preview(req: RenderRequest, max_dim: int = 1200) -> Image.Image:
    """Rend une version basse résolution pour l'aperçu UI (rapide)."""
    img = render(req)
    if max(img.size) > max_dim:
        ratio = max_dim / max(img.size)
        new_size = (round(img.width * ratio), round(img.height * ratio))
        img = img.resize(new_size, Image.LANCZOS)
    return img
