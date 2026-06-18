"""
PptxViewer — lazy PPTX slide viewer using QLabel per slide.

Slides are rendered to QImage off-thread by RenderSlideRunnable (which calls
PptxRenderer.render_slide).  The QImage is converted to QPixmap on the main
thread inside _on_slide_ready so no Qt GUI objects are created in workers.
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, QThreadPool
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (
    QLabel,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
)

from ui.viewers.base_viewer import AbstractViewer
from ui.workers import RenderSlideRunnable

if TYPE_CHECKING:
    from ui.document_parser_service import ParsedPptx
    from ui.document_session import DocumentSession
    from ui.renderers.pptx_renderer import PptxRenderer


_SLIDE_WIDTH = 920


class PptxViewer(AbstractViewer):
    """QStackedWidget of QLabel per slide; slides rendered to QImage off-thread."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._scroll)

        self._stack = QStackedWidget()
        self._scroll.setWidget(self._stack)

        self._renderer: PptxRenderer | None = None
        self._total: int = 0
        self._url: str = ""
        self._labels: list[QLabel] = []
        self._image_cache: dict[int, QImage] = {}
        self._pending: set[int] = set()
        self._cancel_event = threading.Event()
        self._current_idx: int = 0

    # ── AbstractViewer ─────────────────────────────────────────────────────────

    def load(
        self,
        doc: "ParsedPptx",
        renderer: "PptxRenderer",
        session: "DocumentSession",
    ) -> None:
        self._renderer = renderer
        self._total = renderer.slide_count()
        self._url = doc.filename
        self._cancel_event = threading.Event()

        # Clear previous state
        while self._stack.count() > 0:
            w = self._stack.widget(0)
            self._stack.removeWidget(w)
            w.deleteLater()
        self._labels = []
        self._image_cache = {}
        self._pending = set()

        for i in range(self._total):
            lbl = QLabel(f"Slide {i + 1}")
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl.setStyleSheet(
                "QLabel { background-color: #21262d; color: #8b949e; font-size: 14px; }"
            )
            self._stack.addWidget(lbl)
            self._labels.append(lbl)

        self.page_changed.emit(0, self._total)
        start = session.current_page if session.current_page < self._total else 0
        self._show_slide(start)

    def supports_zoom(self) -> bool:
        return False

    def supports_search(self) -> bool:
        return False

    def set_zoom(self, factor: float) -> None:
        pass

    def search(self, query: str) -> None:
        self.search_result.emit(0)

    def go_to_page(self, n: int) -> None:
        n = max(0, min(self._total - 1, n))
        self._show_slide(n)

    def save_session(self, session: "DocumentSession") -> None:
        session.current_page = self._current_idx

    def restore_session(self, session: "DocumentSession") -> None:
        pass  # handled in load()

    # ── Slide rendering ────────────────────────────────────────────────────────

    def _show_slide(self, idx: int) -> None:
        if idx < 0 or idx >= self._total:
            return
        self._current_idx = idx
        self._stack.setCurrentIndex(idx)
        self.page_changed.emit(idx, self._total)

        if idx in self._image_cache:
            self._apply_image(idx, self._image_cache[idx])
            return

        if idx not in self._pending:
            self._pending.add(idx)
            self._schedule_render(idx)

    def _schedule_render(self, slide_idx: int) -> None:
        worker = RenderSlideRunnable(
            url=self._url,
            renderer=self._renderer,
            slide_idx=slide_idx,
            width_px=_SLIDE_WIDTH,
            cancel_event=self._cancel_event,
        )
        worker.signals.ready.connect(self._on_slide_ready)
        QThreadPool.globalInstance().start(worker)

    def _on_slide_ready(self, _url: str, slide_idx: int, image: QImage) -> None:
        self._pending.discard(slide_idx)
        self._image_cache[slide_idx] = image
        self._apply_image(slide_idx, image)

    def _apply_image(self, slide_idx: int, image: QImage) -> None:
        if slide_idx >= len(self._labels):
            return
        lbl = self._labels[slide_idx]
        w = max(self.width() - 24, 400)
        h = max(self.height() - 24, 300)
        # QPixmap.fromImage is safe here — this slot always runs on the main thread
        pixmap = QPixmap.fromImage(image).scaled(
            w, h,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        lbl.setPixmap(pixmap)
        lbl.setFixedSize(pixmap.size())
        lbl.setStyleSheet("QLabel { background-color: #0d1117; }")

    # ── Keyboard shortcuts ─────────────────────────────────────────────────────

    def keyPressEvent(self, event) -> None:
        key = event.key()
        if key in (Qt.Key.Key_Right, Qt.Key.Key_Space):
            self.go_to_page(self._current_idx + 1)
            return
        if key == Qt.Key.Key_Left:
            self.go_to_page(self._current_idx - 1)
            return
        if key == Qt.Key.Key_Home:
            self.go_to_page(0)
            return
        if key == Qt.Key.Key_End:
            self.go_to_page(self._total - 1)
            return
        super().keyPressEvent(event)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        idx = self._current_idx
        if idx in self._image_cache:
            self._apply_image(idx, self._image_cache[idx])
