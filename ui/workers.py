"""
QRunnable workers for the document viewer pipeline.

All workers:
  - Submit to QThreadPool.globalInstance() — no dedicated QThread per task.
  - Carry a threading.Event for cooperative cancellation.
  - Communicate results back to the main thread via _Signals (a QObject).

Worker chain for opening a document:
    DownloadRunnable → ParseRunnable → (viewer created on main thread)

Workers for lazy rendering (scheduled per visible page/slide):
    RenderPageRunnable
    RenderSlideRunnable
    ThumbnailRunnable
"""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, QRunnable, Signal

if TYPE_CHECKING:
    from ui.document_parser_service import ParsedDoc

# ── Lifetime guard ─────────────────────────────────────────────────────────────
# With setAutoDelete(True), Qt takes C++ ownership of the QRunnable but does NOT
# increment the Python ref-count.  The Python wrapper (and its .signals QObject)
# can therefore be GC'd while run() is still executing, causing
# "RuntimeError: Signal source has been deleted".
# Keeping every runnable in this set while it is running guarantees a Python
# reference and prevents premature GC.
_keep_alive: set = set()
_keep_alive_lock = threading.Lock()


# ── Signal carrier ─────────────────────────────────────────────────────────────

class _DownloadSignals(QObject):
    ready = Signal(str, object, str, str)   # url, Path, content_type, filename
    failed = Signal(str, str)               # url, error_message


class _ParseSignals(QObject):
    ready = Signal(str, object)             # url, ParsedDoc
    failed = Signal(str, str)               # url, error_message


class _RenderPageSignals(QObject):
    ready = Signal(str, int, float, object) # url, page_idx, zoom, QImage
    failed = Signal(str, int, str)          # url, page_idx, error_message


class _RenderSlideSignals(QObject):
    ready = Signal(str, int, object)        # url, slide_idx, QImage
    failed = Signal(str, int, str)          # url, slide_idx, error_message


class _ThumbnailSignals(QObject):
    ready = Signal(str, object)             # url, QImage (convert to QPixmap in main-thread slot)
    failed = Signal(str, str)               # url, error_message


# ── DownloadRunnable ───────────────────────────────────────────────────────────

class DownloadRunnable(QRunnable):
    """
    Calls AttachmentDownloadService.get(url, headers, filename_hint) off-thread.
    Emits ready(url, path, content_type, filename) or failed(url, msg).
    """

    def __init__(
        self,
        url: str,
        headers: dict,
        filename_hint: str,
        cancel_event: threading.Event,
    ) -> None:
        super().__init__()
        self.url = url
        self.headers = headers
        self.filename_hint = filename_hint
        self.cancel_event = cancel_event
        self.signals = _DownloadSignals()
        self.setAutoDelete(False)
        with _keep_alive_lock:
            _keep_alive.add(self)

    def run(self) -> None:
        try:
            if self.cancel_event.is_set():
                return
            t0 = time.monotonic()
            try:
                # Resolve root for direct imports when running as subprocess
                root = Path(__file__).resolve().parent.parent
                if str(root) not in sys.path:
                    sys.path.insert(0, str(root))

                from ui.attachment_download_service import AttachmentDownloadService
                from ui.telemetry import log_download

                path, ct, fname = AttachmentDownloadService.get(
                    self.url, self.headers, self.filename_hint
                )
                if self.cancel_event.is_set():
                    return
                elapsed = (time.monotonic() - t0) * 1000
                log_download(self.url, elapsed, cached=False)
                self.signals.ready.emit(self.url, path, ct, fname)
            except Exception as exc:
                if not self.cancel_event.is_set():
                    self.signals.failed.emit(self.url, str(exc))
        finally:
            with _keep_alive_lock:
                _keep_alive.discard(self)


# ── ParseRunnable ──────────────────────────────────────────────────────────────

class ParseRunnable(QRunnable):
    """
    Calls document_parser_service.parse(path, filename, content_type) off-thread.
    Emits ready(url, ParsedDoc) or failed(url, msg).
    """

    def __init__(
        self,
        url: str,
        path: Path,
        filename: str,
        content_type: str,
        cancel_event: threading.Event,
    ) -> None:
        super().__init__()
        self.url = url
        self.path = path
        self.filename = filename
        self.content_type = content_type
        self.cancel_event = cancel_event
        self.signals = _ParseSignals()
        self.setAutoDelete(False)
        with _keep_alive_lock:
            _keep_alive.add(self)

    def run(self) -> None:
        try:
            if self.cancel_event.is_set():
                return
            t0 = time.monotonic()
            try:
                root = Path(__file__).resolve().parent.parent
                if str(root) not in sys.path:
                    sys.path.insert(0, str(root))

                from ui.document_parser_service import parse
                from ui.telemetry import log_parse

                doc = parse(self.path, self.filename, self.content_type)
                if self.cancel_event.is_set():
                    return
                elapsed = (time.monotonic() - t0) * 1000
                log_parse(self.url, type(doc).__name__, elapsed)
                self.signals.ready.emit(self.url, doc)
            except Exception as exc:
                if not self.cancel_event.is_set():
                    self.signals.failed.emit(self.url, str(exc))
        finally:
            with _keep_alive_lock:
                _keep_alive.discard(self)


# ── RenderPageRunnable ─────────────────────────────────────────────────────────

class RenderPageRunnable(QRunnable):
    """
    Calls PdfRenderer.render_page(page_idx, zoom) off-thread.
    Emits ready(url, page_idx, zoom, QPixmap) or failed(url, page_idx, msg).
    """

    def __init__(
        self,
        url: str,
        renderer,
        page_idx: int,
        zoom: float,
        cancel_event: threading.Event,
    ) -> None:
        super().__init__()
        self.url = url
        self.renderer = renderer
        self.page_idx = page_idx
        self.zoom = zoom
        self.cancel_event = cancel_event
        self.signals = _RenderPageSignals()
        self.setAutoDelete(False)
        with _keep_alive_lock:
            _keep_alive.add(self)

    def run(self) -> None:
        try:
            if self.cancel_event.is_set():
                return
            t0 = time.monotonic()
            try:
                image = self.renderer.render_page(self.page_idx, self.zoom)
                if self.cancel_event.is_set():
                    return
                elapsed = (time.monotonic() - t0) * 1000
                from ui.telemetry import log_render
                log_render(self.url, "pdf", self.page_idx, elapsed, cached=False)
                self.signals.ready.emit(self.url, self.page_idx, self.zoom, image)
            except Exception as exc:
                if not self.cancel_event.is_set():
                    self.signals.failed.emit(self.url, self.page_idx, str(exc))
        finally:
            with _keep_alive_lock:
                _keep_alive.discard(self)


# ── RenderSlideRunnable ────────────────────────────────────────────────────────

class RenderSlideRunnable(QRunnable):
    """
    Calls PptxRenderer.render_slide(slide_idx) off-thread.
    Emits ready(url, slide_idx, QGraphicsScene) or failed(url, slide_idx, msg).
    """

    def __init__(
        self,
        url: str,
        renderer,
        slide_idx: int,
        width_px: int,
        cancel_event: threading.Event,
    ) -> None:
        super().__init__()
        self.url = url
        self.renderer = renderer
        self.slide_idx = slide_idx
        self.width_px = width_px
        self.cancel_event = cancel_event
        self.signals = _RenderSlideSignals()
        self.setAutoDelete(False)
        with _keep_alive_lock:
            _keep_alive.add(self)

    def run(self) -> None:
        try:
            if self.cancel_event.is_set():
                return
            t0 = time.monotonic()
            try:
                image = self.renderer.render_slide(self.slide_idx, self.width_px)
                if self.cancel_event.is_set():
                    return
                elapsed = (time.monotonic() - t0) * 1000
                from ui.telemetry import log_render
                log_render(self.url, "pptx", self.slide_idx, elapsed, cached=False)
                self.signals.ready.emit(self.url, self.slide_idx, image)
            except Exception as exc:
                if not self.cancel_event.is_set():
                    self.signals.failed.emit(self.url, self.slide_idx, str(exc))
        finally:
            with _keep_alive_lock:
                _keep_alive.discard(self)


# ── ThumbnailRunnable ──────────────────────────────────────────────────────────

class ThumbnailRunnable(QRunnable):
    """
    Generates a small thumbnail for a document's first page/slide.
    Emits ready(url, QPixmap) or failed(url, msg).
    """

    def __init__(
        self,
        url: str,
        renderer,
        cancel_event: threading.Event,
    ) -> None:
        super().__init__()
        self.url = url
        self.renderer = renderer
        self.cancel_event = cancel_event
        self.signals = _ThumbnailSignals()
        self.setAutoDelete(False)
        with _keep_alive_lock:
            _keep_alive.add(self)

    def run(self) -> None:
        try:
            if self.cancel_event.is_set():
                return
            t0 = time.monotonic()
            try:
                image = self.renderer.render_thumbnail()
                if self.cancel_event.is_set():
                    return
                if image is not None:
                    elapsed = (time.monotonic() - t0) * 1000
                    fmt = type(self.renderer).__name__.replace("Renderer", "").lower()
                    from ui.telemetry import log_thumbnail
                    log_thumbnail(self.url, fmt, elapsed, cached=False)
                    self.signals.ready.emit(self.url, image)
            except Exception as exc:
                if not self.cancel_event.is_set():
                    self.signals.failed.emit(self.url, str(exc))
        finally:
            with _keep_alive_lock:
                _keep_alive.discard(self)
