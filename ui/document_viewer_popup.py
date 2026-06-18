"""
DocumentViewerDialog — thin shell dialog for embedded document viewing.

All download/parse/render logic is delegated to DocumentController.
This file only manages the toolbar, loading/error states, and keyboard
shortcuts that relay to the active viewer.

Backwards-compatible entry point preserved:
    DocumentViewerPopup(parent, url, headers, filename_hint)
"""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

# Support direct execution from ui/ directory
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from ui.document_cache import DocumentCache
from ui.document_controller import DocumentController
from ui.document_session import DocumentSession

_ZOOM_STEPS = [0.5, 0.75, 1.0, 1.25, 1.5, 2.0]
_ZOOM_LABELS = ["50%", "75%", "100%", "125%", "150%", "200%"]


# ── Loading widget ─────────────────────────────────────────────────────────────

class _LoadingWidget(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._label = QLabel("Loading…")
        self._label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._label.setStyleSheet("QLabel { color: #8b949e; font-size: 14px; }")
        layout.addWidget(self._label)
        self._dots = 0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(400)

    def set_stage(self, stage: str) -> None:
        stages = {"downloading": "Downloading", "parsing": "Parsing", "rendering": "Rendering"}
        base = stages.get(stage, stage.capitalize())
        self._label.setText(f"{base}…")
        self._dots = 0

    def _tick(self) -> None:
        self._dots = (self._dots + 1) % 4
        text = self._label.text().rstrip(". ")
        self._label.setText(text + " " + "." * self._dots)

    def stop(self) -> None:
        self._timer.stop()


# ── Error widget ───────────────────────────────────────────────────────────────

class _ErrorWidget(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.retry_requested = None
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._msg = QLabel("Failed to load document.")
        self._msg.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._msg.setWordWrap(True)
        self._msg.setStyleSheet("QLabel { color: #f85149; font-size: 13px; }")
        layout.addWidget(self._msg)

        self._retry_btn = QPushButton("Retry")
        self._retry_btn.setProperty("class", "ghost")
        self._retry_btn.clicked.connect(self._on_retry)
        layout.addWidget(self._retry_btn, alignment=Qt.AlignmentFlag.AlignCenter)

    def set_error(self, msg: str) -> None:
        self._msg.setText(msg)

    def _on_retry(self) -> None:
        if callable(self.retry_requested):
            self.retry_requested()


# ── Toolbar ────────────────────────────────────────────────────────────────────

class _ViewerToolBar(QFrame):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setProperty("class", "toolbar")
        self.setFixedHeight(44)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(8)

        self._filename = QLabel()
        self._filename.setStyleSheet("QLabel { font-weight: 600; font-size: 12px; }")
        layout.addWidget(self._filename)
        layout.addStretch()

        # Search bar
        self._search = QLineEdit()
        self._search.setPlaceholderText("Search…")
        self._search.setFixedWidth(180)
        self._search.setVisible(False)
        layout.addWidget(self._search)

        self._search_count = QLabel()
        self._search_count.setStyleSheet("QLabel { color: #8b949e; font-size: 11px; }")
        self._search_count.setVisible(False)
        layout.addWidget(self._search_count)

        # Navigation
        self._prev_btn = QPushButton("‹")
        self._prev_btn.setFixedWidth(28)
        self._prev_btn.setProperty("class", "ghost")
        self._prev_btn.setVisible(False)
        layout.addWidget(self._prev_btn)

        self._page_label = QLabel("1 / 1")
        self._page_label.setStyleSheet(
            "QLabel { color: #8b949e; font-size: 12px; min-width: 60px; }"
        )
        self._page_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._page_label.setVisible(False)
        layout.addWidget(self._page_label)

        self._next_btn = QPushButton("›")
        self._next_btn.setFixedWidth(28)
        self._next_btn.setProperty("class", "ghost")
        self._next_btn.setVisible(False)
        layout.addWidget(self._next_btn)

        # Zoom combo
        self._zoom_combo = QComboBox()
        for lbl in _ZOOM_LABELS:
            self._zoom_combo.addItem(lbl)
        self._zoom_combo.setCurrentIndex(2)
        self._zoom_combo.setVisible(False)
        self._zoom_combo.setFixedWidth(72)
        layout.addWidget(self._zoom_combo)

        # Close button
        self._close_btn = QPushButton("✕")
        self._close_btn.setFixedSize(28, 28)
        self._close_btn.setProperty("class", "ghost")
        layout.addWidget(self._close_btn)

    def set_filename(self, name: str) -> None:
        self._filename.setText(name)

    def set_page(self, current: int, total: int) -> None:
        self._page_label.setText(f"{current + 1} / {total}")

    def set_search_count(self, count: int) -> None:
        self._search_count.setText(f"{count} match{'es' if count != 1 else ''}")

    def show_zoom(self, visible: bool) -> None:
        self._zoom_combo.setVisible(visible)

    def show_navigation(self, visible: bool) -> None:
        self._prev_btn.setVisible(visible)
        self._page_label.setVisible(visible)
        self._next_btn.setVisible(visible)

    def show_search_bar(self, visible: bool) -> None:
        self._search.setVisible(visible)
        self._search_count.setVisible(visible)

    def focus_search(self) -> None:
        self._search.setVisible(True)
        self._search_count.setVisible(True)
        self._search.setFocus()
        self._search.selectAll()


# ── DocumentViewerDialog ───────────────────────────────────────────────────────

class DocumentViewerDialog(QDialog):
    """
    Thin shell dialog wrapping DocumentController.
    All document download/parse/render logic lives in the controller layer.
    """

    def __init__(
        self,
        parent: QWidget | None,
        url: str,
        headers: dict,
        filename_hint: str = "",
        cache: DocumentCache | None = None,
    ) -> None:
        super().__init__(parent)
        self._url = url
        self._headers = headers
        self._filename_hint = filename_hint
        self._viewer = None
        self._current_page = 0

        self._cache = cache or DocumentCache()
        self._controller = DocumentController(cache=self._cache, parent=self)

        self._setup_window()
        self._build_ui()
        self._connect_controller()
        self._connect_shortcuts()

        # Start loading immediately
        self._controller.open(url, headers, filename_hint)

    # ── Window setup ──────────────────────────────────────────────────────────

    def _setup_window(self) -> None:
        self.setWindowTitle(self._filename_hint or "Document Viewer")
        self.setMinimumSize(900, 650)
        self.resize(1100, 740)
        from ui.theme import build_stylesheet, current_theme
        self.setStyleSheet(build_stylesheet(current_theme()))

    # ── UI layout ─────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self._toolbar = _ViewerToolBar()
        self._toolbar.set_filename(self._filename_hint)
        root.addWidget(self._toolbar)

        self._stack = QStackedWidget()
        root.addWidget(self._stack)

        self._loading_widget = _LoadingWidget()
        self._error_widget = _ErrorWidget()

        self._stack.addWidget(self._loading_widget)   # index 0
        self._stack.addWidget(self._error_widget)     # index 1
        self._stack.setCurrentIndex(0)

    # ── Controller signals ────────────────────────────────────────────────────

    def _connect_controller(self) -> None:
        self._controller.loading_started.connect(lambda _: self._stack.setCurrentIndex(0))
        self._controller.loading_progress.connect(
            lambda _, stage: self._loading_widget.set_stage(stage)
        )
        self._controller.document_ready.connect(self._on_document_ready)
        self._controller.document_failed.connect(self._on_document_failed)
        self._error_widget.retry_requested = self._retry
        self._toolbar._close_btn.clicked.connect(self.close)

    def _on_document_ready(self, url: str, viewer, session: DocumentSession) -> None:
        self._loading_widget.stop()
        self._viewer = viewer

        # Insert viewer into stack at index 2 (or replace existing index-2 widget)
        if self._stack.count() > 2:
            old = self._stack.widget(2)
            self._stack.removeWidget(old)
            old.deleteLater()
        self._stack.insertWidget(2, viewer)
        self._stack.setCurrentIndex(2)

        # Zoom toolbar
        if viewer.supports_zoom():
            self._toolbar.show_zoom(True)
            self._toolbar._zoom_combo.currentIndexChanged.connect(self._on_zoom_combo_changed)
            viewer.zoom_changed.connect(self._sync_zoom_combo)
        else:
            self._toolbar.show_zoom(False)

        # Search toolbar
        if viewer.supports_search():
            self._toolbar.show_search_bar(True)
            self._toolbar._search.textChanged.connect(viewer.search)
            viewer.search_result.connect(self._toolbar.set_search_count)
        else:
            self._toolbar.show_search_bar(False)

        # Page navigation (only for PDF/PPTX)
        from ui.viewers.pdf_viewer import PdfViewer
        from ui.viewers.pptx_viewer import PptxViewer
        if isinstance(viewer, (PdfViewer, PptxViewer)):
            self._toolbar.show_navigation(True)
            self._toolbar._prev_btn.clicked.connect(
                lambda: viewer.go_to_page(self._current_page - 1)
            )
            self._toolbar._next_btn.clicked.connect(
                lambda: viewer.go_to_page(self._current_page + 1)
            )
        else:
            self._toolbar.show_navigation(False)

        viewer.page_changed.connect(self._on_page_changed)

    def _on_document_failed(self, url: str, msg: str) -> None:
        self._loading_widget.stop()
        self._error_widget.set_error(f"Could not open document:\n{msg}")
        self._stack.setCurrentIndex(1)

    def _on_page_changed(self, current: int, total: int) -> None:
        self._current_page = current
        self._toolbar.set_page(current, total)

    def _on_zoom_combo_changed(self, idx: int) -> None:
        if self._viewer and self._viewer.supports_zoom():
            self._viewer.set_zoom(_ZOOM_STEPS[idx])

    def _sync_zoom_combo(self, factor: float) -> None:
        label = f"{int(round(factor * 100))}%"
        for i, lbl in enumerate(_ZOOM_LABELS):
            if lbl == label:
                self._toolbar._zoom_combo.blockSignals(True)
                self._toolbar._zoom_combo.setCurrentIndex(i)
                self._toolbar._zoom_combo.blockSignals(False)
                break

    def _retry(self) -> None:
        self._stack.setCurrentIndex(0)
        self._controller.open(self._url, self._headers, self._filename_hint)

    # ── Keyboard shortcuts ─────────────────────────────────────────────────────

    def _connect_shortcuts(self) -> None:
        QShortcut(QKeySequence("Escape"), self).activated.connect(self.close)
        QShortcut(QKeySequence("Ctrl+F"), self).activated.connect(self._toolbar.focus_search)
        QShortcut(QKeySequence("Ctrl++"), self).activated.connect(self._zoom_in)
        QShortcut(QKeySequence("Ctrl+="), self).activated.connect(self._zoom_in)
        QShortcut(QKeySequence("Ctrl+-"), self).activated.connect(self._zoom_out)
        QShortcut(QKeySequence("Ctrl+0"), self).activated.connect(self._zoom_reset)

    def keyPressEvent(self, event) -> None:
        key = event.key()
        mods = event.modifiers()
        if mods == Qt.KeyboardModifier.ControlModifier:
            if key in (Qt.Key.Key_Equal, Qt.Key.Key_Plus):
                self._zoom_in(); return
            if key == Qt.Key.Key_Minus:
                self._zoom_out(); return
            if key == Qt.Key.Key_0:
                self._zoom_reset(); return
        if key == Qt.Key.Key_Left:
            self._page_prev(); return
        if key == Qt.Key.Key_Right:
            self._page_next(); return
        if key == Qt.Key.Key_PageUp:
            self._page_prev(); return
        if key == Qt.Key.Key_PageDown:
            self._page_next(); return
        super().keyPressEvent(event)

    def wheelEvent(self, event) -> None:
        if event.modifiers() == Qt.KeyboardModifier.ControlModifier:
            if event.angleDelta().y() > 0:
                self._zoom_in()
            else:
                self._zoom_out()
            event.accept()
        else:
            super().wheelEvent(event)

    def _zoom_in(self) -> None:
        idx = self._toolbar._zoom_combo.currentIndex()
        self._toolbar._zoom_combo.setCurrentIndex(min(idx + 1, len(_ZOOM_STEPS) - 1))

    def _zoom_out(self) -> None:
        idx = self._toolbar._zoom_combo.currentIndex()
        self._toolbar._zoom_combo.setCurrentIndex(max(idx - 1, 0))

    def _zoom_reset(self) -> None:
        self._toolbar._zoom_combo.setCurrentIndex(2)

    def _page_prev(self) -> None:
        if self._viewer:
            self._viewer.go_to_page(self._current_page - 1)

    def _page_next(self) -> None:
        if self._viewer:
            self._viewer.go_to_page(self._current_page + 1)

    # ── Close ─────────────────────────────────────────────────────────────────

    def closeEvent(self, event) -> None:
        if self._viewer is not None:
            self._controller.save_session(self._url, self._viewer)
        self._controller.cancel_current()
        super().closeEvent(event)


# ── Backwards-compatible alias ─────────────────────────────────────────────────
DocumentViewerPopup = DocumentViewerDialog
