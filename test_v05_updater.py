"""
Widget d'aperçu du rendu final (avec cadre, logos, texte composés).

À chaque modification de geometry/adjustments/format, on lance un rendu
en basse résolution (max 1200 px de plus grand côté) sur un thread
de fond, et on affiche le résultat.

Le rendu est debounced : si plusieurs changements arrivent en moins de
200 ms, on n'en garde que le dernier.
"""

from __future__ import annotations

from io import BytesIO
from pathlib import Path

from PIL import Image
from PySide6.QtCore import QObject, QThread, QTimer, Qt, Signal
from PySide6.QtGui import QImage, QPainter, QPixmap
from PySide6.QtWidgets import QLabel, QSizePolicy, QVBoxLayout, QWidget

from printou.core import (
    FORMATS,
    Adjustments,
    Geometry,
    OrientationMismatchError,
    RenderRequest,
    Template,
    render_preview,
)
from printou.ui.theme import Colors


def pil_to_qpixmap(pil_img: Image.Image) -> QPixmap:
    """Convertit une image PIL RGB en QPixmap."""
    if pil_img.mode != "RGB":
        pil_img = pil_img.convert("RGB")
    data = pil_img.tobytes("raw", "RGB")
    qimg = QImage(data, pil_img.width, pil_img.height,
                  pil_img.width * 3, QImage.Format_RGB888)
    return QPixmap.fromImage(qimg.copy())


class _RenderWorker(QObject):
    """Worker thread qui exécute un rendu PIL en arrière-plan."""

    finished = Signal(QPixmap, str)  # pixmap, format_code
    failed = Signal(str, str)        # message, format_code

    def __init__(self):
        super().__init__()
        self._next_request: RenderRequest | None = None
        self._next_format: str = ""
        self._busy = False

    def submit(self, req: RenderRequest, format_code: str):
        self._next_request = req
        self._next_format = format_code
        if not self._busy:
            self._run_next()

    def _run_next(self):
        if self._next_request is None:
            return
        req = self._next_request
        fmt = self._next_format
        self._next_request = None
        self._busy = True

        try:
            img = render_preview(req, max_dim=1100)
            pix = pil_to_qpixmap(img)
            self.finished.emit(pix, fmt)
        except OrientationMismatchError as e:
            self.failed.emit(str(e), fmt)
        except Exception as e:
            self.failed.emit(f"Erreur de rendu : {e}", fmt)
        finally:
            self._busy = False
            # Si une nouvelle requête est arrivée pendant qu'on rendait, on l'enchaîne
            if self._next_request is not None:
                self._run_next()


class RenderPreviewWidget(QWidget):
    """Aperçu du rendu final avec mise à jour automatique."""

    render_failed = Signal(str)  # émis si erreur (pour status bar)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMinimumSize(400, 300)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self._image_label = QLabel()
        self._image_label.setAlignment(Qt.AlignCenter)
        self._image_label.setStyleSheet(f"background: {Colors.BG};")
        self._image_label.setText("Aperçu du rendu final…")
        layout.addWidget(self._image_label)

        # Worker de rendu sur thread de fond
        self._thread = QThread()
        self._worker = _RenderWorker()
        self._worker.moveToThread(self._thread)
        self._worker.finished.connect(self._on_render_finished)
        self._worker.failed.connect(self._on_render_failed)
        self._thread.start()

        # Debounce : on attend 200 ms sans changement avant de rendre
        self._debounce = QTimer()
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(120)
        self._debounce.timeout.connect(self._do_render)

        # État courant
        self._photo_path: Path | None = None
        self._template: Template | None = None
        self._format_code: str = "20x30"
        self._geometry = Geometry()
        self._adjustments = Adjustments()
        self._placeholders: dict[str, str] = {}
        self._logos_dir = Path(".")
        self._last_pixmap: QPixmap | None = None

    def closeEvent(self, event):
        self._thread.quit()
        self._thread.wait()
        super().closeEvent(event)

    # ─── API publique ───

    def set_render_inputs(
        self,
        photo_path: Path | None,
        template: Template | None,
        format_code: str,
        geometry: Geometry,
        adjustments: Adjustments,
        placeholders: dict[str, str],
        logos_dir: Path,
    ):
        self._photo_path = photo_path
        self._template = template
        self._format_code = format_code
        self._geometry = geometry
        self._adjustments = adjustments
        self._placeholders = placeholders
        self._logos_dir = logos_dir
        self._schedule_render()

    def update_geometry(self, geometry: Geometry):
        self._geometry = geometry
        self._schedule_render()

    def update_adjustments(self, adjustments: Adjustments):
        self._adjustments = adjustments
        self._schedule_render()

    def update_format(self, format_code: str):
        self._format_code = format_code
        self._schedule_render()

    def update_template(self, template: Template):
        self._template = template
        self._schedule_render()

    def clear(self):
        self._photo_path = None
        self._last_pixmap = None
        self._image_label.setText("Sélectionnez une photo")

    # ─── Logique interne ───

    def _schedule_render(self):
        if self._photo_path is None or self._template is None:
            self._image_label.setText("Sélectionnez une photo")
            return
        if self._format_code not in FORMATS:
            self._image_label.setText(f"Format {self._format_code} inconnu")
            return
        self._debounce.start()

    def _do_render(self):
        if self._photo_path is None or self._template is None:
            return
        req = RenderRequest(
            photo_path=self._photo_path,
            template=self._template,
            print_format=FORMATS[self._format_code],
            geometry=self._geometry,
            adjustments=self._adjustments,
            placeholders=self._placeholders,
            logos_dir=self._logos_dir,
            strict_orientation=False,  # en preview on tolère pour pas frustrer
        )
        self._worker.submit(req, self._format_code)

    def _on_render_finished(self, pixmap: QPixmap, format_code: str):
        if format_code != self._format_code:
            return  # rendu obsolète
        self._last_pixmap = pixmap
        self._update_displayed_pixmap()

    def _on_render_failed(self, message: str, format_code: str):
        self._image_label.setText(f"⚠ {message}")
        self.render_failed.emit(message)

    def _update_displayed_pixmap(self):
        if self._last_pixmap is None:
            return
        scaled = self._last_pixmap.scaled(
            self._image_label.size(),
            Qt.KeepAspectRatio,
            Qt.SmoothTransformation,
        )
        self._image_label.setPixmap(scaled)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update_displayed_pixmap()
