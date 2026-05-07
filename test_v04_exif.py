"""
Widget d'aperçu de la photo source v0.7.

Changements v0.7 :
- Mode CROP activé par défaut au chargement d'une nouvelle photo
- La rotation ne modifie PLUS le crop (rotation libre, indépendante)
- Le crop est borné à la zone safe (sans coins noirs) UNIQUEMENT lors d'un drag manuel
- Suppression du cadre "atelier" : fond uniforme, photo qui flotte directement
"""

from __future__ import annotations

from io import BytesIO

from PIL import Image
from PySide6.QtCore import QPoint, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (
    QBrush, QColor, QImage, QKeyEvent, QPainter, QPen, QPixmap, QTransform,
)
from PySide6.QtWidgets import QSizePolicy, QWidget

from printou.core import Adjustments, CropRect, Geometry
from printou.core.photo_ops import (
    apply_adjustments, largest_inscribed_rect_after_rotation, safe_crop_for_rotation,
)
from printou.ui.theme import Colors


HANDLE_SIZE = 16
HANDLE_GRAB = 24
CANVAS_PADDING = 60       # marge "respiration" autour de la photo
PHOTO_SIZE_FACTOR = 0.80  # facteur de réduction de la photo dans son atelier (-20%)
PREVIEW_MAX_DIM = 1400    # taille max du pixmap preview pour les retouches rapides
ADJUSTMENT_DEBOUNCE_MS = 80


def pil_to_qpixmap(pil: Image.Image) -> QPixmap:
    if pil.mode != "RGB":
        pil = pil.convert("RGB")
    data = pil.tobytes("raw", "RGB")
    qimg = QImage(data, pil.width, pil.height, pil.width * 3, QImage.Format_RGB888)
    return QPixmap.fromImage(qimg.copy())


def qpixmap_to_pil(pixmap: QPixmap) -> Image.Image:
    qimg = pixmap.toImage().convertToFormat(QImage.Format_RGB888)
    width = qimg.width()
    height = qimg.height()
    ptr = qimg.constBits()
    bpl = qimg.bytesPerLine()
    data = bytes(ptr)
    if bpl == width * 3:
        return Image.frombuffer("RGB", (width, height), data, "raw", "RGB", 0, 1)
    raw = bytearray()
    for y in range(height):
        line = data[y * bpl:y * bpl + width * 3]
        raw.extend(line)
    return Image.frombuffer("RGB", (width, height), bytes(raw), "raw", "RGB", 0, 1)


class PhotoPreviewWidget(QWidget):
    geometry_changed = Signal(Geometry)
    geometry_dragging = Signal(Geometry)
    crop_mode_changed = Signal(bool)
    crop_clamped_to_safe = Signal()  # v0.8 : émis si le crop a été ajusté à la zone safe

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMinimumSize(400, 300)
        self.setFocusPolicy(Qt.StrongFocus)

        self._original_pixmap: QPixmap | None = None
        self._preview_pixmap: QPixmap | None = None
        self._preview_pil: Image.Image | None = None
        self._adjusted_pil: Image.Image | None = None
        self._adjusted_pixmap: QPixmap | None = None
        self._displayed_pixmap: QPixmap | None = None

        self._original_size: tuple[int, int] = (0, 0)
        self._rotation_deg: float = 0.0
        self._crop = CropRect()
        self._crop_aspect_ratio: float = 2 / 3
        self._lock_aspect: bool = True

        self._adjustments = Adjustments()

        # v0.7 : mode "crop" activé par défaut
        self._mode: str = "crop"

        self._drag_handle: str | None = None
        self._drag_start_pos: QPoint | None = None
        self._drag_start_crop: CropRect | None = None

        self._adj_timer = QTimer()
        self._adj_timer.setSingleShot(True)
        self._adj_timer.setInterval(ADJUSTMENT_DEBOUNCE_MS)
        self._adj_timer.timeout.connect(self._recompute_adjusted)

        self._history: list[dict] = []
        self._max_history = 30

        self.setMouseTracking(True)

    # ─── API publique ───

    def set_pixmap(self, pixmap: QPixmap | None):
        self._original_pixmap = pixmap
        if pixmap and not pixmap.isNull():
            self._original_size = (pixmap.width(), pixmap.height())
            if max(pixmap.width(), pixmap.height()) > PREVIEW_MAX_DIM:
                self._preview_pixmap = pixmap.scaled(
                    PREVIEW_MAX_DIM, PREVIEW_MAX_DIM,
                    Qt.KeepAspectRatio, Qt.SmoothTransformation,
                )
            else:
                self._preview_pixmap = pixmap
            self._preview_pil = qpixmap_to_pil(self._preview_pixmap)
        else:
            self._original_size = (0, 0)
            self._preview_pixmap = None
            self._preview_pil = None

        self._rotation_deg = 0.0
        self._crop = self._make_centered_locked_crop()
        self._adjustments = Adjustments()
        self._adjusted_pil = None
        self._adjusted_pixmap = None
        # v0.7 : on revient toujours en mode crop par défaut
        self._mode = "crop"
        self._history.clear()
        self._recompute_displayed()
        self.update()
        self.crop_mode_changed.emit(True)

    def set_aspect_ratio(self, ratio: float, lock: bool = True):
        self._crop_aspect_ratio = ratio
        self._lock_aspect = lock
        if self._original_pixmap is not None:
            self._crop = self._make_centered_locked_crop()
            # v0.7 : on clamp à la zone safe si rotation active
            if self._rotation_deg != 0.0:
                self._crop = safe_crop_for_rotation(
                    self._original_size[0], self._original_size[1],
                    self._rotation_deg, self._crop,
                )
            self._recompute_displayed()
            self.update()
            self._emit_geometry_changed()

    def set_rotation(self, deg: float):
        """v0.7 : la rotation ne modifie PLUS le crop. Elle tourne juste l'image affichée."""
        deg = max(-15.0, min(15.0, deg))
        if deg == self._rotation_deg:
            return
        self._push_history()
        self._rotation_deg = deg
        # On NE TOUCHE PLUS au crop ici. Si l'utilisateur a un crop qui dépasse
        # la zone safe, on le clampe seulement quand il essaiera de l'éditer.
        self._recompute_displayed()
        self.update()
        self._emit_geometry_changed()

    def set_adjustments(self, adj: Adjustments):
        if self._adjustments == adj:
            return
        self._adjustments = adj
        self._adjusted_pil = None
        self._adjusted_pixmap = None
        self._adj_timer.start()

    def push_adjustments_history(self):
        self._push_history()

    def get_geometry(self) -> Geometry:
        return Geometry(rotation_deg=self._rotation_deg, crop=self._crop)

    def get_adjustments(self) -> Adjustments:
        return self._adjustments

    def reset_geometry(self):
        self._push_history()
        self._rotation_deg = 0.0
        self._crop = self._make_centered_locked_crop()
        self._recompute_displayed()
        self.update()
        self._emit_geometry_changed()

    def enter_crop_mode(self):
        if self._mode == "crop":
            return
        self._mode = "crop"
        self._push_history()
        self._recompute_displayed()
        self.update()
        self.crop_mode_changed.emit(True)

    def exit_crop_mode(self):
        if self._mode == "view":
            return
        # v0.7+ : avant de sortir du mode crop, on s'assure que le crop est dans la zone safe
        # (au cas où la rotation a écrasé la zone valide, ou si l'utilisateur a réduit
        # la rotation sans réajuster son crop).
        clamped = False
        if self._rotation_deg != 0.0:
            old_crop = CropRect(self._crop.x, self._crop.y,
                                self._crop.width, self._crop.height)
            self._crop = safe_crop_for_rotation(
                self._original_size[0], self._original_size[1],
                self._rotation_deg, self._crop,
            )
            if self._lock_aspect:
                self._crop = self._enforce_ratio(self._crop)
            # Détection de changement effectif (tolerance 0.001)
            if (abs(self._crop.x - old_crop.x) > 0.001
                or abs(self._crop.y - old_crop.y) > 0.001
                or abs(self._crop.width - old_crop.width) > 0.001
                or abs(self._crop.height - old_crop.height) > 0.001):
                clamped = True
        self._mode = "view"
        self._recompute_displayed()
        self.update()
        self.crop_mode_changed.emit(False)
        self._emit_geometry_changed()
        if clamped:
            self.crop_clamped_to_safe.emit()

    def is_crop_mode(self) -> bool:
        return self._mode == "crop"

    def undo(self) -> bool:
        if not self._history:
            return False
        snapshot = self._history.pop()
        self._rotation_deg = snapshot["rotation_deg"]
        self._crop = CropRect(**snapshot["crop"])
        self._adjustments = Adjustments(**snapshot["adjustments"])
        self._mode = snapshot["mode"]
        self._adjusted_pil = None
        self._adjusted_pixmap = None
        self._recompute_adjusted()
        self.update()
        self._emit_geometry_changed()
        return True

    # ─── Calcul du pixmap affiché ───

    def _recompute_adjusted(self):
        if self._preview_pil is None:
            self._adjusted_pil = None
            self._adjusted_pixmap = None
            return
        if self._adjustments.is_neutral():
            self._adjusted_pil = self._preview_pil
            self._adjusted_pixmap = self._preview_pixmap
        else:
            self._adjusted_pil = apply_adjustments(self._preview_pil, self._adjustments)
            self._adjusted_pixmap = pil_to_qpixmap(self._adjusted_pil)
        self._recompute_displayed()
        self.update()

    def _recompute_displayed(self):
        if self._adjusted_pixmap is None and self._preview_pixmap is not None:
            if self._adjustments.is_neutral():
                self._adjusted_pixmap = self._preview_pixmap
            else:
                self._recompute_adjusted()
                return

        src = self._adjusted_pixmap
        if src is None or src.isNull():
            self._displayed_pixmap = None
            return

        # Tourner
        if self._rotation_deg != 0.0:
            transform = QTransform().rotate(self._rotation_deg)
            src = src.transformed(transform, Qt.SmoothTransformation)

        # En mode view, on applique le crop. En mode crop, on garde la photo entière (avec coins noirs si rotation).
        if self._mode == "view" and not self._crop.is_full():
            if self._rotation_deg == 0.0:
                w, h = src.width(), src.height()
                left = round(self._crop.x * w)
                top = round(self._crop.y * h)
                right = round((self._crop.x + self._crop.width) * w)
                bottom = round((self._crop.y + self._crop.height) * h)
                src = src.copy(left, top, right - left, bottom - top)
            else:
                bbox_w, bbox_h = src.width(), src.height()
                orig_w = self._adjusted_pixmap.width() if self._adjusted_pixmap else bbox_w
                orig_h = self._adjusted_pixmap.height() if self._adjusted_pixmap else bbox_h
                offset_x = (bbox_w - orig_w) // 2
                offset_y = (bbox_h - orig_h) // 2
                left = offset_x + round(self._crop.x * orig_w)
                top = offset_y + round(self._crop.y * orig_h)
                w_pix = round(self._crop.width * orig_w)
                h_pix = round(self._crop.height * orig_h)
                src = src.copy(left, top, w_pix, h_pix)

        self._displayed_pixmap = src

    # ─── Helpers crop ───

    def _make_centered_locked_crop(self) -> CropRect:
        if self._original_size[0] == 0:
            return CropRect()
        if not self._lock_aspect:
            return CropRect()
        img_w, img_h = self._original_size
        img_ratio = img_w / img_h
        if img_ratio > self._crop_aspect_ratio:
            crop_h_pix = img_h
            crop_w_pix = crop_h_pix * self._crop_aspect_ratio
        else:
            crop_w_pix = img_w
            crop_h_pix = crop_w_pix / self._crop_aspect_ratio
        w_rel = crop_w_pix / img_w
        h_rel = crop_h_pix / img_h
        return CropRect(
            x=(1.0 - w_rel) / 2, y=(1.0 - h_rel) / 2,
            width=w_rel, height=h_rel,
        )

    def _enforce_ratio(self, crop: CropRect) -> CropRect:
        if not self._lock_aspect or self._original_size[0] == 0:
            return crop
        img_w, img_h = self._original_size
        w_pix = crop.width * img_w
        h_pix = crop.height * img_h
        current_ratio = w_pix / h_pix if h_pix > 0 else 1
        if abs(current_ratio - self._crop_aspect_ratio) < 0.001:
            return crop
        cx = crop.x + crop.width / 2
        cy = crop.y + crop.height / 2
        if current_ratio > self._crop_aspect_ratio:
            new_w_pix = h_pix * self._crop_aspect_ratio
            new_h_pix = h_pix
        else:
            new_w_pix = w_pix
            new_h_pix = w_pix / self._crop_aspect_ratio
        new_w_rel = new_w_pix / img_w
        new_h_rel = new_h_pix / img_h
        new_x = max(0.0, min(1.0 - new_w_rel, cx - new_w_rel / 2))
        new_y = max(0.0, min(1.0 - new_h_rel, cy - new_h_rel / 2))
        return CropRect(x=new_x, y=new_y, width=new_w_rel, height=new_h_rel)

    # ─── Layout ───

    def _displayed_image_rect(self) -> QRectF:
        """Position du pixmap affiché, centré, avec marges de respiration."""
        if self._displayed_pixmap is None or self._displayed_pixmap.isNull():
            return QRectF()
        widget_w = self.width()
        widget_h = self.height()
        # Espace dispo après marges
        avail_w = max(0, widget_w - 2 * CANVAS_PADDING)
        avail_h = max(0, widget_h - 2 * CANVAS_PADDING)
        if avail_w <= 0 or avail_h <= 0:
            return QRectF()
        pix_w = self._displayed_pixmap.width()
        pix_h = self._displayed_pixmap.height()
        # La photo prend PHOTO_SIZE_FACTOR de l'espace dispo
        max_w = avail_w * PHOTO_SIZE_FACTOR
        max_h = avail_h * PHOTO_SIZE_FACTOR
        ratio = min(max_w / pix_w, max_h / pix_h)
        disp_w = pix_w * ratio
        disp_h = pix_h * ratio
        x = (widget_w - disp_w) / 2
        y = (widget_h - disp_h) / 2
        return QRectF(x, y, disp_w, disp_h)

    def _crop_handles_rect_in_widget(self) -> QRectF:
        """En mode crop : rectangle des poignées en coordonnées du widget."""
        if self._displayed_pixmap is None:
            return QRectF()
        disp_rect = self._displayed_image_rect()
        if self._rotation_deg == 0.0:
            return QRectF(
                disp_rect.x() + self._crop.x * disp_rect.width(),
                disp_rect.y() + self._crop.y * disp_rect.height(),
                self._crop.width * disp_rect.width(),
                self._crop.height * disp_rect.height(),
            )
        else:
            bbox_w = self._displayed_pixmap.width()
            bbox_h = self._displayed_pixmap.height()
            orig_w = self._adjusted_pixmap.width() if self._adjusted_pixmap else bbox_w
            orig_h = self._adjusted_pixmap.height() if self._adjusted_pixmap else bbox_h
            scale = disp_rect.width() / bbox_w
            offset_x = (bbox_w - orig_w) / 2 * scale
            offset_y = (bbox_h - orig_h) / 2 * scale
            orig_disp_w = orig_w * scale
            orig_disp_h = orig_h * scale
            return QRectF(
                disp_rect.x() + offset_x + self._crop.x * orig_disp_w,
                disp_rect.y() + offset_y + self._crop.y * orig_disp_h,
                self._crop.width * orig_disp_w,
                self._crop.height * orig_disp_h,
            )

    def _safe_zone_rect_in_widget(self) -> QRectF | None:
        """v0.7 : rectangle de la zone safe (sans coins noirs) en coords du widget.
        Retourne None s'il n'y a pas de rotation."""
        if self._rotation_deg == 0.0 or self._displayed_pixmap is None:
            return None
        disp_rect = self._displayed_image_rect()
        bbox_w = self._displayed_pixmap.width()
        bbox_h = self._displayed_pixmap.height()
        orig_w = self._adjusted_pixmap.width() if self._adjusted_pixmap else bbox_w
        orig_h = self._adjusted_pixmap.height() if self._adjusted_pixmap else bbox_h
        scale = disp_rect.width() / bbox_w
        offset_x = (bbox_w - orig_w) / 2 * scale
        offset_y = (bbox_h - orig_h) / 2 * scale
        orig_disp_w = orig_w * scale
        orig_disp_h = orig_h * scale

        sx, sy, sw, sh = largest_inscribed_rect_after_rotation(
            self._original_size[0], self._original_size[1], self._rotation_deg,
        )
        return QRectF(
            disp_rect.x() + offset_x + sx * orig_disp_w,
            disp_rect.y() + offset_y + sy * orig_disp_h,
            sw * orig_disp_w,
            sh * orig_disp_h,
        )

    # ─── Souris ───

    def _handle_at(self, pos: QPoint) -> str | None:
        if self._mode != "crop":
            return None
        crop_rect = self._crop_handles_rect_in_widget()
        if crop_rect.isNull():
            return None
        px, py = pos.x(), pos.y()
        for name, (cx, cy) in [
            ("tl", (crop_rect.left(), crop_rect.top())),
            ("tr", (crop_rect.right(), crop_rect.top())),
            ("bl", (crop_rect.left(), crop_rect.bottom())),
            ("br", (crop_rect.right(), crop_rect.bottom())),
        ]:
            if abs(px - cx) <= HANDLE_GRAB / 2 and abs(py - cy) <= HANDLE_GRAB / 2:
                return name
        if crop_rect.contains(pos):
            return "center"
        return None

    def _push_history(self):
        snapshot = {
            "rotation_deg": self._rotation_deg,
            "crop": {"x": self._crop.x, "y": self._crop.y,
                     "width": self._crop.width, "height": self._crop.height},
            "adjustments": self._adjustments.to_dict(),
            "mode": self._mode,
        }
        self._history.append(snapshot)
        if len(self._history) > self._max_history:
            self._history.pop(0)

    def _emit_geometry_changed(self):
        self.geometry_changed.emit(self.get_geometry())

    def _emit_geometry_dragging(self):
        self.geometry_dragging.emit(self.get_geometry())

    def mousePressEvent(self, event):
        if event.button() != Qt.LeftButton or self._mode != "crop":
            return
        handle = self._handle_at(event.position().toPoint())
        if handle:
            self._drag_handle = handle
            self._drag_start_pos = event.position().toPoint()
            self._drag_start_crop = CropRect(
                self._crop.x, self._crop.y, self._crop.width, self._crop.height,
            )

    def mouseMoveEvent(self, event):
        pos = event.position().toPoint()
        if self._drag_handle is None:
            if self._mode == "crop":
                handle = self._handle_at(pos)
                cursors = {
                    "tl": Qt.SizeFDiagCursor, "br": Qt.SizeFDiagCursor,
                    "tr": Qt.SizeBDiagCursor, "bl": Qt.SizeBDiagCursor,
                    "center": Qt.SizeAllCursor,
                }
                self.setCursor(cursors.get(handle, Qt.ArrowCursor))
            return

        if self._rotation_deg == 0.0:
            orig_rect = self._displayed_image_rect()
        else:
            disp_rect = self._displayed_image_rect()
            bbox_w = self._displayed_pixmap.width() if self._displayed_pixmap else 1
            orig_w = self._adjusted_pixmap.width() if self._adjusted_pixmap else 1
            orig_h = self._adjusted_pixmap.height() if self._adjusted_pixmap else 1
            scale = disp_rect.width() / bbox_w
            offset_x = (bbox_w - orig_w) / 2 * scale
            offset_y = (self._displayed_pixmap.height() - orig_h) / 2 * scale
            orig_rect = QRectF(
                disp_rect.x() + offset_x, disp_rect.y() + offset_y,
                orig_w * scale, orig_h * scale,
            )

        if orig_rect.width() <= 0:
            return

        dx_widget = pos.x() - self._drag_start_pos.x()
        dy_widget = pos.y() - self._drag_start_pos.y()
        dx_rel = dx_widget / orig_rect.width()
        dy_rel = dy_widget / orig_rect.height()

        sc = self._drag_start_crop
        new_crop = self._compute_new_crop_from_drag(sc, self._drag_handle, dx_rel, dy_rel)

        # v0.7 : clamp à la zone safe si rotation active
        if self._rotation_deg != 0.0:
            new_crop = safe_crop_for_rotation(
                self._original_size[0], self._original_size[1],
                self._rotation_deg, new_crop,
            )
        if self._lock_aspect and self._drag_handle != "center":
            new_crop = self._enforce_ratio(new_crop)

        self._crop = new_crop
        self.update()
        self._emit_geometry_dragging()

    def _compute_new_crop_from_drag(self, sc, handle, dx_rel, dy_rel):
        new_crop = CropRect(sc.x, sc.y, sc.width, sc.height)
        if handle == "center":
            new_crop.x = max(0.0, min(1.0 - sc.width, sc.x + dx_rel))
            new_crop.y = max(0.0, min(1.0 - sc.height, sc.y + dy_rel))
            return new_crop
        if not self._lock_aspect:
            if handle == "tl":
                new_x = max(0.0, min(sc.x + sc.width - 0.05, sc.x + dx_rel))
                new_y = max(0.0, min(sc.y + sc.height - 0.05, sc.y + dy_rel))
                new_crop.width = sc.width - (new_x - sc.x)
                new_crop.height = sc.height - (new_y - sc.y)
                new_crop.x = new_x
                new_crop.y = new_y
            elif handle == "tr":
                new_w = max(0.05, min(1.0 - sc.x, sc.width + dx_rel))
                new_y = max(0.0, min(sc.y + sc.height - 0.05, sc.y + dy_rel))
                new_crop.width = new_w
                new_crop.height = sc.height - (new_y - sc.y)
                new_crop.y = new_y
            elif handle == "bl":
                new_x = max(0.0, min(sc.x + sc.width - 0.05, sc.x + dx_rel))
                new_h = max(0.05, min(1.0 - sc.y, sc.height + dy_rel))
                new_crop.width = sc.width - (new_x - sc.x)
                new_crop.x = new_x
                new_crop.height = new_h
            elif handle == "br":
                new_crop.width = max(0.05, min(1.0 - sc.x, sc.width + dx_rel))
                new_crop.height = max(0.05, min(1.0 - sc.y, sc.height + dy_rel))
            return new_crop

        img_w, img_h = self._original_size
        ratio = self._crop_aspect_ratio
        if handle == "br":
            new_w_rel = max(0.05, min(1.0 - sc.x, sc.width + dx_rel))
            new_h_pix = (new_w_rel * img_w) / ratio
            new_h_rel = new_h_pix / img_h
            if sc.y + new_h_rel > 1.0:
                new_h_rel = 1.0 - sc.y
                new_w_rel = (new_h_rel * img_h) * ratio / img_w
            new_crop.width = new_w_rel
            new_crop.height = new_h_rel
        elif handle == "tl":
            anchor_x = sc.x + sc.width
            anchor_y = sc.y + sc.height
            new_w_rel = max(0.05, anchor_x - max(0.0, sc.x + dx_rel))
            new_h_pix = (new_w_rel * img_w) / ratio
            new_h_rel = new_h_pix / img_h
            if anchor_y - new_h_rel < 0:
                new_h_rel = anchor_y
                new_w_rel = (new_h_rel * img_h) * ratio / img_w
            new_crop.x = anchor_x - new_w_rel
            new_crop.y = anchor_y - new_h_rel
            new_crop.width = new_w_rel
            new_crop.height = new_h_rel
        elif handle == "tr":
            anchor_x = sc.x
            anchor_y = sc.y + sc.height
            new_w_rel = max(0.05, min(1.0 - anchor_x, sc.width + dx_rel))
            new_h_pix = (new_w_rel * img_w) / ratio
            new_h_rel = new_h_pix / img_h
            if anchor_y - new_h_rel < 0:
                new_h_rel = anchor_y
                new_w_rel = (new_h_rel * img_h) * ratio / img_w
            new_crop.x = anchor_x
            new_crop.y = anchor_y - new_h_rel
            new_crop.width = new_w_rel
            new_crop.height = new_h_rel
        elif handle == "bl":
            anchor_x = sc.x + sc.width
            anchor_y = sc.y
            new_w_rel = max(0.05, anchor_x - max(0.0, sc.x + dx_rel))
            new_h_pix = (new_w_rel * img_w) / ratio
            new_h_rel = new_h_pix / img_h
            if anchor_y + new_h_rel > 1.0:
                new_h_rel = 1.0 - anchor_y
                new_w_rel = (new_h_rel * img_h) * ratio / img_w
            new_crop.x = anchor_x - new_w_rel
            new_crop.y = anchor_y
            new_crop.width = new_w_rel
            new_crop.height = new_h_rel
        return new_crop

    def mouseReleaseEvent(self, event):
        if self._drag_handle is not None:
            self._drag_handle = None
            self._emit_geometry_changed()

    def keyPressEvent(self, event: QKeyEvent):
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            if self._mode == "crop":
                self.exit_crop_mode()
                return
        elif event.key() == Qt.Key_Z and event.modifiers() & Qt.ControlModifier:
            if self.undo():
                return
        super().keyPressEvent(event)

    # ─── Peinture ───

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        # v0.7 : un seul fond uniforme, plus de cadre intérieur
        painter.fillRect(self.rect(), QColor(Colors.BG))

        if self._displayed_pixmap is None or self._displayed_pixmap.isNull():
            painter.setPen(QColor(Colors.TEXT_MUTED))
            painter.drawText(self.rect(), Qt.AlignCenter, "Sélectionnez une photo")
            return

        # v0.8 : fond gris clair sous la photo pour mieux voir les bords / coins noirs
        disp_rect = self._displayed_image_rect()
        pad = 8  # marge gris clair autour de la photo
        bg_pad_rect = QRectF(
            disp_rect.left() - pad, disp_rect.top() - pad,
            disp_rect.width() + 2 * pad, disp_rect.height() + 2 * pad,
        )
        painter.fillRect(bg_pad_rect, QColor("#cfd6e0"))

        # Image
        painter.drawPixmap(disp_rect, self._displayed_pixmap, QRectF(self._displayed_pixmap.rect()))

        # Mode crop : afficher la zone safe (si rotation), overlay et poignées
        if self._mode == "crop":
            crop_rect = self._crop_handles_rect_in_widget()

            # v0.7 : indication visuelle de la zone safe (sans coins noirs)
            safe_rect = self._safe_zone_rect_in_widget()
            if safe_rect is not None:
                # Fine ligne pointillée pour indiquer la zone "imprimable"
                painter.setPen(QPen(QColor(255, 255, 255, 120), 1, Qt.DashLine))
                painter.setBrush(Qt.NoBrush)
                painter.drawRect(safe_rect)

            # Overlay sombre hors crop
            overlay = QColor(0, 0, 0, 140)
            painter.fillRect(QRectF(disp_rect.left(), disp_rect.top(),
                                    disp_rect.width(), crop_rect.top() - disp_rect.top()), overlay)
            painter.fillRect(QRectF(disp_rect.left(), crop_rect.bottom(),
                                    disp_rect.width(), disp_rect.bottom() - crop_rect.bottom()), overlay)
            painter.fillRect(QRectF(disp_rect.left(), crop_rect.top(),
                                    crop_rect.left() - disp_rect.left(), crop_rect.height()), overlay)
            painter.fillRect(QRectF(crop_rect.right(), crop_rect.top(),
                                    disp_rect.right() - crop_rect.right(), crop_rect.height()), overlay)

            # Cadre crop
            painter.setPen(QPen(QColor(Colors.ACCENT), 2))
            painter.setBrush(Qt.NoBrush)
            painter.drawRect(crop_rect)

            # Tiers
            painter.setPen(QPen(QColor(255, 255, 255, 80), 1, Qt.DashLine))
            for i in (1, 2):
                x = crop_rect.left() + crop_rect.width() * i / 3
                painter.drawLine(QPointF(x, crop_rect.top()), QPointF(x, crop_rect.bottom()))
                y = crop_rect.top() + crop_rect.height() * i / 3
                painter.drawLine(QPointF(crop_rect.left(), y), QPointF(crop_rect.right(), y))

            # Poignées
            painter.setBrush(QBrush(QColor(Colors.ACCENT)))
            painter.setPen(QPen(QColor(Colors.BG), 2))
            for cx, cy in [
                (crop_rect.left(), crop_rect.top()),
                (crop_rect.right(), crop_rect.top()),
                (crop_rect.left(), crop_rect.bottom()),
                (crop_rect.right(), crop_rect.bottom()),
            ]:
                painter.drawRect(QRectF(
                    cx - HANDLE_SIZE / 2, cy - HANDLE_SIZE / 2,
                    HANDLE_SIZE, HANDLE_SIZE,
                ))

            # Hint
            painter.setPen(QColor(Colors.TEXT_DIM))
            hint = "Entrée : valider · Ctrl+Z : annuler"
            painter.drawText(self.rect().adjusted(0, 0, 0, -10), Qt.AlignBottom | Qt.AlignHCenter, hint)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.update()
