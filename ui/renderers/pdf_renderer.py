"""
PdfRenderer — renders PDF pages to QImage via PyMuPDF (fitz).

Returns QImage instead of QPixmap because QPixmap can only be created on the
GUI thread; QImage is safe to create in any thread.  Callers convert to QPixmap
in the main-thread slot after the signal arrives.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtGui import QImage

if TYPE_CHECKING:
    from ui.document_parser_service import ParsedPdf


class PdfRenderer:
    """Wraps a fitz.Document and renders pages to QImage."""

    def __init__(self, doc: "ParsedPdf") -> None:
        self._path = doc.file_path
        self._total_pages = doc.total_pages
        self._fitz_doc = None  # opened lazily

    def _open(self):
        if self._fitz_doc is None:
            import fitz
            self._fitz_doc = fitz.open(str(self._path))
        return self._fitz_doc

    def page_count(self) -> int:
        return self._total_pages

    def render_page(self, page_idx: int, zoom: float = 1.0) -> QImage:
        """Render a single page at the given zoom and return a QImage (thread-safe)."""
        import fitz
        doc = self._open()
        page = doc[page_idx]
        mat = fitz.Matrix(zoom, zoom)
        pix = page.get_pixmap(matrix=mat, alpha=False)
        # Copy samples into a standalone bytes object so the QImage owns its data.
        samples = bytes(pix.samples)
        img = QImage(samples, pix.width, pix.height, pix.stride, QImage.Format.Format_RGB888)
        return img.copy()  # detach from the local bytes buffer

    def render_thumbnail(self) -> QImage | None:
        """Render page 0 at 0.2× zoom as a small preview (thread-safe)."""
        try:
            return self.render_page(0, zoom=0.2)
        except Exception:
            return None

    def close(self) -> None:
        if self._fitz_doc is not None:
            try:
                self._fitz_doc.close()
            except Exception:
                pass
            self._fitz_doc = None
