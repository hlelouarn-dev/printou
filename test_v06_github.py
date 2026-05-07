"""
Widget d'aperçu interactif d'un template (v0.8).

Affiche :
- Le papier (fond)
- La zone image (papier - marges) avec la photo demo
- Les calques (logos + textes) dessinés selon leur ancrage

Permet :
- Sélection d'un calque par clic
- Déplacement par drag (à l'intérieur du référentiel d'ancrage)
- Redimensionnement via la poignée du coin bas-droit

Signaux :
- layer_selected(int) : index sélectionné, -1 si déselectionné
- layer_modified(int, dict) : modifications en cours (pendant drag)
- layer_dropped(int, dict) : commit final (à la fin du drag)
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image
from PySide6.QtCore import QPoint, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import (
    QBrush, QColor, QFont, QImage, QPainter, QPen, QPixmap,
)
from PySide6.QtWidgets import QSizePolicy, QWidget

from printou.core import FORMATS, Template, apply_exif_orientation
from printou.ui.theme import Colors


HANDLE_SIZE = 12
HANDLE_GRAB = 18
CANVAS_PADDING = 30
SELECTION_PEN_WIDTH = 2
GUIDES_COLOR = QColor(0, 200, 200, 220)


def _pil_to_qpixmap(pil: Image.Image) -> QPixmap:
    if pil.mode != "RGB":
        pil = pil.convert("RGB")
    data = pil.tobytes("raw", "RGB")
    qimg = QImage(data, pil.width, pil.height, pil.width * 3, QImage.Format_RGB888)
    return QPixmap.fromImage(qimg.copy())


class TemplateCanvas(QWidget):
    """Aperçu interactif du template avec drag & drop."""

    layer_selected = Signal(int)
    layer_modified = Signal(int, dict)
    layer_dropped = Signal(int, dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMinimumSize(500, 400)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)  # v0.9 : pour recevoir keyPressEvent

        self._template: Template | None = None
        self._format_code: str = "20x30"
        self._demo_pixmap_paysage: QPixmap | None = None
        self._demo_pixmap_portrait: QPixmap | None = None
        self._logos_dir: Path | None = None
        self._placeholders: dict[str, str] = {
            "event_name": "Concours Demo",
            "event_location": "Demo Location",
            "event_date": "01/01/2026",
            "client_name": "Demo Client",
        }

        self._selected_index: int = -1
        self._drag_mode: str | None = None
        self._drag_start_pos: QPoint | None = None
        self._drag_start_layer: dict | None = None

        # v0.9 : zoom + pan
        self._zoom: float = 1.0           # facteur de zoom (1.0 = 100%)
        self._pan_offset: QPointF = QPointF(0, 0)  # décalage en px du widget
        self._space_pressed: bool = False
        self._panning: bool = False
        self._pan_start_mouse: QPoint | None = None
        self._pan_start_offset: QPointF | None = None

    # ─── API ───

    def set_template(self, template: Template | None):
        self._template = template
        self._selected_index = -1
        self.update()

    def set_format_code(self, code: str):
        if code != self._format_code:
            self._format_code = code
            self.update()

    def set_demo_photos(self, paysage_path: Path, portrait_path: Path):
        for path, attr in [(paysage_path, "_demo_pixmap_paysage"),
                           (portrait_path, "_demo_pixmap_portrait")]:
            if path.exists():
                try:
                    with Image.open(str(path)) as img:
                        img = apply_exif_orientation(img).convert("RGB")
                        img.thumbnail((1200, 1200), Image.LANCZOS)
                        setattr(self, attr, _pil_to_qpixmap(img))
                except Exception as e:
                    print(f"[TemplateCanvas] Erreur demo {path}: {e}")
        self.update()

    def set_logos_dir(self, logos_dir: Path | None):
        self._logos_dir = logos_dir
        self.update()

    def select_layer(self, index: int):
        self._selected_index = index
        self.update()

    # ─── Calculs géométriques ───

    def _paper_aspect_ratio(self) -> float:
        if self._template is None:
            return 3 / 2
        fmt = FORMATS.get(self._format_code)
        if fmt is None:
            return 3 / 2
        if self._template.orientation == "paysage":
            return max(fmt.width_mm, fmt.height_mm) / min(fmt.width_mm, fmt.height_mm)
        return min(fmt.width_mm, fmt.height_mm) / max(fmt.width_mm, fmt.height_mm)

    def _paper_rect(self) -> QRectF:
        widget_w = max(1, self.width() - 2 * CANVAS_PADDING)
        widget_h = max(1, self.height() - 2 * CANVAS_PADDING)
        ratio = self._paper_aspect_ratio()
        # Taille "fit" de base (sans zoom)
        if widget_w / widget_h > ratio:
            base_h = widget_h
            base_w = base_h * ratio
        else:
            base_w = widget_w
            base_h = base_w / ratio
        # v0.9 : appliquer le zoom
        paper_w = base_w * self._zoom
        paper_h = base_h * self._zoom
        # Centrer + appliquer le pan
        x = (self.width() - paper_w) / 2 + self._pan_offset.x()
        y = (self.height() - paper_h) / 2 + self._pan_offset.y()
        return QRectF(x, y, paper_w, paper_h)

    def _image_rect(self) -> QRectF:
        if self._template is None:
            return QRectF()
        paper = self._paper_rect()
        m = self._template.margins
        left = paper.left() + (m.left / 100) * paper.width()
        top = paper.top() + (m.top / 100) * paper.height()
        right = paper.right() - (m.right / 100) * paper.width()
        bottom = paper.bottom() - (m.bottom / 100) * paper.height()
        return QRectF(left, top, max(0, right - left), max(0, bottom - top))

    def _reference_rect_for_anchor(self, anchor: str) -> QRectF:
        """Référentiel utilisé pour positionner un calque selon son ancrage."""
        if anchor == "paper":
            return self._paper_rect()
        if anchor == "top_band":
            paper = self._paper_rect()
            img = self._image_rect()
            return QRectF(paper.left(), paper.top(),
                          paper.width(), max(0, img.top() - paper.top()))
        if anchor == "bottom_band":
            paper = self._paper_rect()
            img = self._image_rect()
            return QRectF(paper.left(), img.bottom(),
                          paper.width(), max(0, paper.bottom() - img.bottom()))
        return self._image_rect()  # défaut "image"

    def _layer_rect(self, layer: dict) -> QRectF:
        ref = self._reference_rect_for_anchor(layer.get("anchor", "image"))
        if ref.width() <= 0 or ref.height() <= 0:
            return QRectF()

        x = ref.left() + (layer.get("x_pct", 0.0) / 100) * ref.width()
        y = ref.top() + (layer.get("y_pct", 0.0) / 100) * ref.height()
        w = (layer.get("width_pct", 10.0) / 100) * ref.width()

        if layer.get("type") == "logo":
            h_pct = layer.get("height_pct")
            if h_pct is not None:
                h = (h_pct / 100) * ref.height()
            else:
                h = w  # carré par défaut
        else:  # texte
            font_pct = layer.get("font_pct", 4.0)
            h = (font_pct / 100) * ref.height() * 1.4

        return QRectF(x, y, w, h)

    # ─── Hit testing ───

    def _handle_at(self, pos: QPoint) -> tuple[int, str] | None:
        if self._template is None:
            return None
        # On parcourt du dernier au premier (z-order le plus haut prioritaire)
        for idx in range(len(self._template.layers) - 1, -1, -1):
            r = self._layer_rect(self._template.layers[idx])
            br = QPointF(r.right(), r.bottom())
            if abs(pos.x() - br.x()) <= HANDLE_GRAB / 2 and abs(pos.y() - br.y()) <= HANDLE_GRAB / 2:
                return (idx, "resize_br")
            if r.contains(pos):
                return (idx, "move")
        return None

    # ─── Souris / Clavier (avec zoom & pan v0.9) ───

    def wheelEvent(self, event):
        """v0.9 : molette = zoom autour du curseur."""
        delta = event.angleDelta().y()
        if delta == 0:
            return
        # Sensibilité : 120 = 1 cran de molette → ×1.15 ou ÷1.15
        factor = 1.15 if delta > 0 else (1 / 1.15)
        new_zoom = max(0.2, min(8.0, self._zoom * factor))
        if new_zoom == self._zoom:
            return
        # Zoom autour du curseur : on garde le pixel sous la souris fixe
        cursor = event.position()
        # Position dans le widget AVANT zoom
        cx, cy = cursor.x(), cursor.y()
        # Centre du widget
        wcx, wcy = self.width() / 2, self.height() / 2
        # Position relative au centre + offset
        rel_x = cx - wcx - self._pan_offset.x()
        rel_y = cy - wcy - self._pan_offset.y()
        # Après le changement de zoom, on doit ajuster le pan pour que (cx, cy)
        # reste pointé sur le même endroit du papier
        scale_change = new_zoom / self._zoom
        new_pan_x = self._pan_offset.x() + rel_x * (1 - scale_change)
        new_pan_y = self._pan_offset.y() + rel_y * (1 - scale_change)
        self._zoom = new_zoom
        self._pan_offset = QPointF(new_pan_x, new_pan_y)
        self.update()
        event.accept()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Space and not event.isAutoRepeat():
            self._space_pressed = True
            self.setCursor(Qt.OpenHandCursor)
            event.accept()
            return
        # Raccourcis zoom : Ctrl+0 = reset, Ctrl++ / Ctrl+- = zoom
        if event.modifiers() & Qt.ControlModifier:
            if event.key() == Qt.Key_0:
                self.reset_view()
                event.accept()
                return
            if event.key() in (Qt.Key_Plus, Qt.Key_Equal):
                self._zoom = min(8.0, self._zoom * 1.2)
                self.update()
                event.accept()
                return
            if event.key() == Qt.Key_Minus:
                self._zoom = max(0.2, self._zoom / 1.2)
                self.update()
                event.accept()
                return
        super().keyPressEvent(event)

    def keyReleaseEvent(self, event):
        if event.key() == Qt.Key_Space and not event.isAutoRepeat():
            self._space_pressed = False
            self._panning = False
            self.setCursor(Qt.ArrowCursor)
            event.accept()
            return
        super().keyReleaseEvent(event)

    def reset_view(self):
        """Zoom 100% + pan centré."""
        self._zoom = 1.0
        self._pan_offset = QPointF(0, 0)
        self.update()

    def mousePressEvent(self, event):
        if event.button() != Qt.LeftButton:
            return
        # v0.9 : si Espace est pressé → pan au lieu de sélection
        if self._space_pressed:
            self._panning = True
            self._pan_start_mouse = event.position().toPoint()
            self._pan_start_offset = QPointF(self._pan_offset)
            self.setCursor(Qt.ClosedHandCursor)
            return

        pos = event.position().toPoint()
        hit = self._handle_at(pos)
        if hit is None:
            self._selected_index = -1
            self.layer_selected.emit(-1)
            self.update()
            return
        idx, mode = hit
        self._selected_index = idx
        self._drag_mode = "resize" if "resize" in mode else "move"
        self._drag_start_pos = pos
        self._drag_start_layer = dict(self._template.layers[idx])
        self.layer_selected.emit(idx)
        self.update()

    def mouseMoveEvent(self, event):
        pos = event.position().toPoint()

        # v0.9 : panning actif
        if self._panning and self._pan_start_mouse is not None:
            dx = pos.x() - self._pan_start_mouse.x()
            dy = pos.y() - self._pan_start_mouse.y()
            self._pan_offset = QPointF(
                self._pan_start_offset.x() + dx,
                self._pan_start_offset.y() + dy,
            )
            self.update()
            return

        if self._drag_mode is None:
            # Cursor hint
            if self._space_pressed:
                self.setCursor(Qt.OpenHandCursor)
                return
            hit = self._handle_at(pos)
            if hit is None:
                self.setCursor(Qt.ArrowCursor)
            elif hit[1] == "move":
                self.setCursor(Qt.SizeAllCursor)
            else:
                self.setCursor(Qt.SizeFDiagCursor)
            return

        if self._template is None or self._selected_index < 0:
            return
        layer = self._template.layers[self._selected_index]
        ref = self._reference_rect_for_anchor(layer.get("anchor", "image"))
        if ref.width() <= 0 or ref.height() <= 0:
            return

        dx = pos.x() - self._drag_start_pos.x()
        dy = pos.y() - self._drag_start_pos.y()
        dx_pct = dx / ref.width() * 100
        dy_pct = dy / ref.height() * 100
        sl = self._drag_start_layer

        if self._drag_mode == "move":
            new_x = max(0.0, min(100.0 - sl.get("width_pct", 10.0),
                                 sl.get("x_pct", 0.0) + dx_pct))
            new_y = max(0.0, min(100.0, sl.get("y_pct", 0.0) + dy_pct))
            layer["x_pct"] = new_x
            layer["y_pct"] = new_y
        else:  # resize
            new_w = max(2.0, sl.get("width_pct", 10.0) + dx_pct)
            new_w = min(new_w, 100.0 - sl.get("x_pct", 0.0))
            layer["width_pct"] = new_w
            if layer.get("type") == "text":
                ratio = new_w / max(0.01, sl.get("width_pct", 10.0))
                layer["font_pct"] = sl.get("font_pct", 4.0) * ratio

        self.update()
        self.layer_modified.emit(self._selected_index, dict(layer))

    def mouseReleaseEvent(self, event):
        # v0.9 : fin du panning
        if self._panning:
            self._panning = False
            self._pan_start_mouse = None
            self._pan_start_offset = None
            self.setCursor(Qt.OpenHandCursor if self._space_pressed else Qt.ArrowCursor)
            return
        if self._drag_mode is not None:
            idx = self._selected_index
            if idx >= 0 and self._template is not None:
                self.layer_dropped.emit(idx, dict(self._template.layers[idx]))
            self._drag_mode = None
            self._drag_start_pos = None
            self._drag_start_layer = None

    # ─── Peinture ───

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        painter.fillRect(self.rect(), QColor(Colors.BG))

        if self._template is None:
            painter.setPen(QColor(Colors.TEXT_MUTED))
            painter.drawText(self.rect(), Qt.AlignCenter,
                             "Sélectionne ou crée un template à éditer")
            return

        # 1. Papier
        paper = self._paper_rect()
        painter.fillRect(paper, QColor(self._template.background_color))
        painter.setPen(QPen(QColor(Colors.TEXT_DIM), 1, Qt.DashLine))
        painter.setBrush(Qt.NoBrush)
        painter.drawRect(paper)

        # 2. Photo demo dans la zone image
        img_rect = self._image_rect()
        if img_rect.width() > 0 and img_rect.height() > 0:
            demo = (self._demo_pixmap_paysage if self._template.orientation == "paysage"
                    else self._demo_pixmap_portrait)
            if demo is not None and not demo.isNull():
                # Centrer en préservant le ratio (cover/contain au choix : on fait contain)
                pix_ratio = demo.width() / demo.height()
                zone_ratio = img_rect.width() / img_rect.height()
                if pix_ratio > zone_ratio:
                    h = img_rect.width() / pix_ratio
                    target = QRectF(img_rect.left(),
                                    img_rect.top() + (img_rect.height() - h) / 2,
                                    img_rect.width(), h)
                else:
                    w = img_rect.height() * pix_ratio
                    target = QRectF(img_rect.left() + (img_rect.width() - w) / 2,
                                    img_rect.top(), w, img_rect.height())
                painter.drawPixmap(target, demo, QRectF(demo.rect()))

            painter.setPen(QPen(QColor(Colors.ACCENT), 1))
            painter.setBrush(Qt.NoBrush)
            painter.drawRect(img_rect)

        # 3. Calques
        for i, layer in enumerate(self._template.layers):
            self._draw_layer(painter, i, layer)

        # v0.9 : indicateur de zoom en haut à droite
        if abs(self._zoom - 1.0) > 0.01 or self._pan_offset != QPointF(0, 0):
            zoom_text = f"  Zoom : {int(self._zoom * 100)}%   ·   Espace = pan   ·   Ctrl+0 = reset  "
            font = QFont()
            font.setPixelSize(11)
            painter.setFont(font)
            metrics = painter.fontMetrics()
            text_w = metrics.horizontalAdvance(zoom_text) + 16
            text_h = metrics.height() + 8
            badge_rect = QRectF(self.width() - text_w - 8, 8, text_w, text_h)
            painter.fillRect(badge_rect, QColor(0, 0, 0, 160))
            painter.setPen(QColor(255, 255, 255, 220))
            painter.drawText(badge_rect, Qt.AlignCenter, zoom_text)

    def _draw_layer(self, painter: QPainter, idx: int, layer: dict):
        rect = self._layer_rect(layer)
        if rect.width() <= 0 or rect.height() <= 0:
            return
        is_selected = (idx == self._selected_index)

        if layer.get("type") == "logo":
            asset = layer.get("asset_path", "")
            pix = self._load_logo_pixmap(asset)
            if pix is not None and not pix.isNull():
                pix_ratio = pix.width() / pix.height()
                rect_ratio = rect.width() / max(0.01, rect.height())
                if pix_ratio > rect_ratio:
                    h = rect.width() / pix_ratio
                    target = QRectF(rect.left(), rect.top() + (rect.height() - h) / 2,
                                    rect.width(), h)
                else:
                    w = rect.height() * pix_ratio
                    target = QRectF(rect.left() + (rect.width() - w) / 2, rect.top(),
                                    w, rect.height())
                painter.drawPixmap(target, pix, QRectF(pix.rect()))
            else:
                painter.fillRect(rect, QColor(120, 120, 120, 200))
                painter.setPen(QColor("white"))
                short = Path(asset).name if asset else "(à définir)"
                painter.drawText(rect, Qt.AlignCenter, f"[Logo]\n{short}")
        else:  # text
            text = self._render_placeholders(layer.get("content", ""))
            font = QFont(layer.get("font_family", "Arial"))
            font.setPixelSize(max(6, int(rect.height() / 1.4)))
            font.setBold(layer.get("bold", False))
            painter.setFont(font)
            painter.setPen(QColor(layer.get("color", "#000000")))
            align = self._text_alignment(layer.get("align", "left"))
            painter.drawText(rect, align | Qt.TextWordWrap, text)

        # Cadre + poignée si sélectionné
        if is_selected:
            painter.setPen(QPen(GUIDES_COLOR, SELECTION_PEN_WIDTH))
            painter.setBrush(Qt.NoBrush)
            painter.drawRect(rect)
            painter.setBrush(QBrush(GUIDES_COLOR))
            painter.setPen(QPen(QColor(Colors.BG), 1))
            painter.drawRect(QRectF(
                rect.right() - HANDLE_SIZE / 2, rect.bottom() - HANDLE_SIZE / 2,
                HANDLE_SIZE, HANDLE_SIZE,
            ))

    def _load_logo_pixmap(self, asset_path: str) -> QPixmap | None:
        if not asset_path:
            return None
        path = Path(asset_path)
        if not path.is_absolute() and self._logos_dir is not None:
            path = self._logos_dir / asset_path
        if not path.exists():
            return None
        return QPixmap(str(path))

    def _render_placeholders(self, content: str) -> str:
        out = content
        for k, v in self._placeholders.items():
            out = out.replace("{" + k + "}", v)
        return out

    def _text_alignment(self, align: str):
        return {
            "left": Qt.AlignLeft | Qt.AlignVCenter,
            "center": Qt.AlignCenter,
            "right": Qt.AlignRight | Qt.AlignVCenter,
        }.get(align, Qt.AlignLeft | Qt.AlignVCenter)
