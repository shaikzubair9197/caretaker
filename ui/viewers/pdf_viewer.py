"""
PdfViewer — lazy page-rendering PDF viewer using QScrollArea.

Only pages ±2 from the currently visible page are rendered at once.
Unrendered pages show a placeholder with dimensions.  Rendering is
dispatched to the global QThreadPool via RenderPageRunnable.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, QThreadPool
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QLabel,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ui.viewers.base_viewer import AbstractViewer
from ui.workers import RenderPageRunnable

if TYPE_CHECKING:
    from ui.document_cache import DocumentCache
    from ui.document_parser_service import ParsedPdf
    from ui.document_session import DocumentSession
    from ui.renderers.pdf_renderer import PdfRenderer

_PLACEHOLDER_H = 400
_BUFFER = 2  # pages before/after viewport to pre-render


class _PageLabel(QLabel):
    """Single-page widget: shows either a rendered pixmap or a placeholder."""

    def __init__(self, page_idx: int, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.page_idx = page_idx
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMinimumHeight(_PLACEHOLDER_H)
        self._show_placeholder()

    def _show_placeholder(self) -> None:
        self.setText(f"Page {self.page_idx + 1}")
        self.setStyleSheet(
            "QLabel { background-color: #21262d; color: #8b949e;"
            " font-size: 12px; border: 1px solid #30363d; margin: 4px 0; }"
        )

    def set_pixmap(self, pm: QPixmap) -> None:
        self.setPixmap(pm)
        self.setFixedHeight(pm.height() + 8)
        self.setStyleSheet("QLabel { background-color: #0d1117; margin: 4px 0; }")


class PdfViewer(AbstractViewer):
    """QScrollArea-based lazy PDF viewer."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        layout.addWidget(self._scroll)

        self._container = QWidget()
        self._container_layout = QVBoxLayout(self._container)
        self._container_layout.setContentsMargins(8, 8, 8, 8)
        self._container_layout.setSpacing(4)
        self._scroll.setWidget(self._container)

        self._page_labels: list[_PageLabel] = []
        self._renderer: PdfRenderer | None = None
        self._cache: DocumentCache | None = None
        self._url: str = ""
        self._zoom: float = 1.0
        self._total: int = 0
        self._cancel_tokens: list = []

        self._scroll.verticalScrollBar().valueChanged.connect(self._on_scroll)

    # ── AbstractViewer ─────────────────────────────────────────────────────────

    def load(
        self,
        doc: "ParsedPdf",
        renderer: "PdfRenderer",
        session: "DocumentSession",
    ) -> None:
        self._renderer = renderer
        self._total = renderer.page_count()
        self._url = doc.filename
        self._zoom = session.zoom

        # Build placeholder labels
        for lbl in self._page_labels:
            self._container_layout.removeWidget(lbl)
            lbl.deleteLater()
        self._page_labels = []

        for i in range(self._total):
            lbl = _PageLabel(i, self._container)
            self._container_layout.addWidget(lbl)
            self._page_labels.append(lbl)

        self.page_changed.emit(0, self._total)

        # Trigger initial visible range rendering
        self._update_visible_range()

        if session.scroll_y > 0:
            from PySide6.QtCore import QTimer
            QTimer.singleShot(100, lambda: self._restore_scroll(session.scroll_y))

    def _restore_scroll(self, y: float) -> None:
        sb = self._scroll.verticalScrollBar()
        sb.setValue(int(y * sb.maximum()))

    def supports_zoom(self) -> bool:
        return True

    def supports_search(self) -> bool:
        return False

    def set_zoom(self, factor: float) -> None:
        factor = max(0.25, min(4.0, factor))
        if abs(factor - self._zoom) < 0.01:
            return
        self._zoom = factor
        # Invalidate all page labels and re-render visible
        for lbl in self._page_labels:
            lbl._show_placeholder()
        self._update_visible_range()
        self.zoom_changed.emit(factor)

    def search(self, query: str) -> None:
        self.search_result.emit(0)

    def go_to_page(self, n: int) -> None:
        n = max(0, min(self._total - 1, n))
        if n < len(self._page_labels):
            self._scroll.ensureWidgetVisible(self._page_labels[n])
        self.page_changed.emit(n, self._total)

    def save_session(self, session: "DocumentSession") -> None:
        session.zoom = self._zoom
        sb = self._scroll.verticalScrollBar()
        session.scroll_y = sb.value() / max(sb.maximum(), 1)
        session.current_page = self._visible_page()

    def restore_session(self, session: "DocumentSession") -> None:
        pass  # handled in load()

    # ── Lazy rendering ─────────────────────────────────────────────────────────

    def _on_scroll(self) -> None:
        self._update_visible_range()
        page = self._visible_page()
        self.page_changed.emit(page, self._total)

    def _visible_page(self) -> int:
        if not self._page_labels:
            return 0
        mid_y = self._scroll.verticalScrollBar().value() + self._scroll.height() // 2
        for i, lbl in enumerate(self._page_labels):
            if lbl.y() + lbl.height() > mid_y:
                return i
        return len(self._page_labels) - 1

    def _update_visible_range(self) -> None:
        if self._renderer is None:
            return
        current = self._visible_page()
        lo = max(0, current - _BUFFER)
        hi = min(self._total - 1, current + _BUFFER)

        from ui.document_cache import DocumentCache
        for i in range(lo, hi + 1):
            lbl = self._page_labels[i]
            if lbl.pixmap() is not None and not lbl.pixmap().isNull():
                continue  # already rendered at some zoom
            self._schedule_render(i)

    def _schedule_render(self, page_idx: int) -> None:
        import threading
        cancel = threading.Event()
        self._cancel_tokens.append(cancel)
        worker = RenderPageRunnable(
            url=self._url,
            renderer=self._renderer,
            page_idx=page_idx,
            zoom=self._zoom,
            cancel_event=cancel,
        )
        worker.signals.ready.connect(self._on_page_ready)
        QThreadPool.globalInstance().start(worker)

    def _on_page_ready(self, url: str, page_idx: int, zoom: float, image: QImage) -> None:
        if abs(zoom - self._zoom) > 0.01:
            return  # stale render from a previous zoom
        if page_idx < len(self._page_labels):
            self._page_labels[page_idx].set_pixmap(QPixmap.fromImage(image))

    # ── Keyboard shortcuts ─────────────────────────────────────────────────────

    def keyPressEvent(self, event) -> None:
        mods = event.modifiers()
        key = event.key()
        if mods == Qt.KeyboardModifier.ControlModifier:
            if key in (Qt.Key.Key_Equal, Qt.Key.Key_Plus):
                self.set_zoom(self._zoom + 0.25)
                return
            if key == Qt.Key.Key_Minus:
                self.set_zoom(self._zoom - 0.25)
                return
            if key == Qt.Key.Key_0:
                self.set_zoom(1.0)
                return
        if key == Qt.Key.Key_PageDown:
            self.go_to_page(self._visible_page() + 1)
            return
        if key == Qt.Key.Key_PageUp:
            self.go_to_page(self._visible_page() - 1)
            return
        super().keyPressEvent(event)

    def wheelEvent(self, event) -> None:
        if event.modifiers() == Qt.KeyboardModifier.ControlModifier:
            delta = event.angleDelta().y()
            self.set_zoom(self._zoom + (0.25 if delta > 0 else -0.25))
            event.accept()
        else:
            super().wheelEvent(event)
