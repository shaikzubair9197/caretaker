"""
DocumentController — orchestrates the document open lifecycle.

Responsibilities:
  - Cancel in-flight requests when a new document is requested
  - Check DocumentCache at all levels; deliver immediately on full hit
  - Submit DownloadRunnable → ParseRunnable via QThreadPool
  - Create the appropriate viewer via ViewerRegistry
  - Load the viewer with the parsed document and a restored DocumentSession
  - Emit signals so the UI can swap the viewer widget
  - prefetch() — background download+parse+thumbnail for high-confidence docs
  - save_session() — persist viewer state back to DocumentCache on close
"""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

from PySide6.QtCore import QObject, QThreadPool, Signal

from ui.document_cache import DocumentCache
from ui.document_session import DocumentSession
from ui.viewer_registry import ViewerRegistry
from ui.workers import DownloadRunnable, ParseRunnable, ThumbnailRunnable


class DocumentController(QObject):
    loading_started = Signal(str)               # url
    loading_progress = Signal(str, str)         # url, stage
    document_ready = Signal(str, object, object)  # url, AbstractViewer, DocumentSession
    document_failed = Signal(str, str)          # url, error_message
    thumbnail_ready = Signal(str, object)       # url, QImage (convert to QPixmap in main-thread slot)

    def __init__(
        self,
        cache: DocumentCache | None = None,
        pool: QThreadPool | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._cache = cache or DocumentCache()
        self._pool = pool or QThreadPool.globalInstance()
        self._cancel: threading.Event = threading.Event()
        self._active_url: str = ""

    # ── Public API ─────────────────────────────────────────────────────────────

    def open(self, url: str, headers: dict, filename_hint: str = "") -> None:
        """
        Open a document.  Cancels any in-flight request first.
        Delivers via document_ready signal; shows loading_progress during work.
        """
        self._cancel.set()
        self._cancel = threading.Event()
        self._active_url = url
        cancel = self._cancel

        self.loading_started.emit(url)

        # ── Full cache hit: bytes + parsed ────────────────────────────────────
        if self._cache.has_download(url) and self._cache.has_parsed(url):
            from ui.telemetry import log_cache_hit, log_viewer_open
            log_cache_hit(url, "full")
            parsed = self._cache.get_parsed(url)
            session = self._cache.get_session(url) or DocumentSession(url=url)
            viewer = self._build_viewer(url, parsed, session)
            if viewer is not None:
                log_viewer_open(url, type(parsed).__name__, from_cache=True)
                self.document_ready.emit(url, viewer, session)
            return

        # ── Download ──────────────────────────────────────────────────────────
        if self._cache.has_download(url):
            path = self._cache.get_download(url)
            self._submit_parse(url, path, filename_hint, "", cancel)
        else:
            dl = DownloadRunnable(url, headers, filename_hint, cancel)
            dl.signals.ready.connect(lambda u, p, ct, fn: self._on_download_ready(u, p, ct, fn, cancel))
            dl.signals.failed.connect(self._on_failed)
            self._pool.start(dl)
            self.loading_progress.emit(url, "downloading")

    def prefetch(self, url: str, headers: dict, filename_hint: str = "") -> None:
        """
        Background download + parse + thumbnail.  Does NOT emit loading/ready signals.
        """
        if self._cache.has_download(url) and self._cache.has_parsed(url):
            return
        cancel = threading.Event()  # independent cancel; never cancelled by open()

        dl = DownloadRunnable(url, headers, filename_hint, cancel)
        dl.signals.ready.connect(lambda u, p, ct, fn: self._on_prefetch_download(u, p, ct, fn, cancel))
        self._pool.start(dl)

    def cancel_current(self) -> None:
        from ui.telemetry import log_cancel
        log_cancel(self._active_url, "user_close")
        self._cancel.set()

    def save_session(self, url: str, viewer) -> None:
        session = self._cache.get_session(url) or DocumentSession(url=url)
        try:
            viewer.save_session(session)
        except Exception:
            pass
        self._cache.store_session(url, session)

    # ── Internal: open flow ───────────────────────────────────────────────────

    def _on_download_ready(
        self, url: str, path: Path, content_type: str, filename: str, cancel: threading.Event
    ) -> None:
        if cancel.is_set():
            return
        self._cache.store_download(url, path)
        self.loading_progress.emit(url, "parsing")
        self._submit_parse(url, path, filename, content_type, cancel)

    def _submit_parse(
        self, url: str, path: Path, filename: str, content_type: str, cancel: threading.Event
    ) -> None:
        p = ParseRunnable(url, path, filename, content_type, cancel)
        p.signals.ready.connect(lambda u, doc: self._on_parse_ready(u, doc, cancel))
        p.signals.failed.connect(self._on_failed)
        self._pool.start(p)

    def _on_parse_ready(self, url: str, parsed, cancel: threading.Event) -> None:
        if cancel.is_set():
            return
        self._cache.store_parsed(url, parsed)
        self.loading_progress.emit(url, "rendering")
        session = self._cache.get_session(url) or DocumentSession(url=url)
        viewer = self._build_viewer(url, parsed, session)
        if viewer is None:
            return
        from ui.telemetry import log_viewer_open
        log_viewer_open(url, type(parsed).__name__, from_cache=False)
        self.document_ready.emit(url, viewer, session)
        self._maybe_generate_thumbnail(url, parsed, cancel)

    def _build_viewer(self, url: str, parsed, session: DocumentSession):
        try:
            viewer = ViewerRegistry.create(parsed)
            renderer = self._make_renderer(parsed)
            viewer.load(parsed, renderer, session)
            return viewer
        except Exception as exc:
            self.document_failed.emit(url, str(exc))
            return None

    @staticmethod
    def _make_renderer(parsed):
        from ui.document_parser_service import (
            ParsedDocx,
            ParsedPdf,
            ParsedPptx,
            ParsedText,
            ParsedXlsx,
        )
        if isinstance(parsed, ParsedDocx):
            from ui.renderers.docx_renderer import DocxRenderer
            return DocxRenderer()
        if isinstance(parsed, ParsedPdf):
            from ui.renderers.pdf_renderer import PdfRenderer
            return PdfRenderer(parsed)
        if isinstance(parsed, ParsedPptx):
            from ui.renderers.pptx_renderer import PptxRenderer
            return PptxRenderer(parsed)
        if isinstance(parsed, ParsedXlsx):
            from ui.renderers.xlsx_renderer import XlsxRenderer
            return XlsxRenderer(parsed)
        if isinstance(parsed, ParsedText):
            from ui.renderers.text_renderer import TextRenderer
            return TextRenderer()
        return None

    def _on_failed(self, url: str, msg: str) -> None:
        if url == self._active_url:
            self.document_failed.emit(url, msg)

    def _maybe_generate_thumbnail(self, url: str, parsed, cancel: threading.Event) -> None:
        renderer = self._make_renderer(parsed)
        if renderer is None:
            return
        if not hasattr(renderer, "render_thumbnail"):
            return
        th = ThumbnailRunnable(url, renderer, cancel)
        th.signals.ready.connect(self.thumbnail_ready)
        self._pool.start(th)

    # ── Internal: prefetch flow ───────────────────────────────────────────────

    def _on_prefetch_download(
        self, url: str, path: Path, content_type: str, filename: str, cancel: threading.Event
    ) -> None:
        if cancel.is_set():
            return
        self._cache.store_download(url, path)
        p = ParseRunnable(url, path, filename, content_type, cancel)
        p.signals.ready.connect(lambda u, doc: self._on_prefetch_parsed(u, doc, cancel))
        self._pool.start(p)

    def _on_prefetch_parsed(self, url: str, parsed, cancel: threading.Event) -> None:
        if cancel.is_set():
            return
        self._cache.store_parsed(url, parsed)
        renderer = self._make_renderer(parsed)
        if renderer is None:
            return
        if hasattr(renderer, "render_thumbnail"):
            th = ThumbnailRunnable(url, renderer, cancel)
            th.signals.ready.connect(self.thumbnail_ready)
            self._pool.start(th)
