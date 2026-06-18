"""
TextViewer — displays plain text and Markdown using QTextBrowser.

QTextBrowser is lighter than QWebEngineView and sufficient for text content.
The TextRenderer converts content to themed HTML; this viewer just hosts it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QTextBrowser, QVBoxLayout

from ui.viewers.base_viewer import AbstractViewer

if TYPE_CHECKING:
    from ui.document_parser_service import ParsedText
    from ui.document_session import DocumentSession
    from ui.renderers.text_renderer import TextRenderer


class TextViewer(AbstractViewer):
    """QTextBrowser-based viewer for plain text and Markdown."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self._browser = QTextBrowser()
        self._browser.setOpenExternalLinks(True)
        layout.addWidget(self._browser)

        self._zoom = 1.0
        self._base_font_size = 13.0

    # ── AbstractViewer ─────────────────────────────────────────────────────────

    def load(
        self,
        doc: "ParsedText",
        renderer: "TextRenderer",
        session: "DocumentSession",
    ) -> None:
        from ui.theme import current_theme
        theme = current_theme()
        html = renderer.render(doc, theme)
        self._browser.setHtml(html)
        self._browser.setStyleSheet(
            f"QTextBrowser {{ background-color: {theme.surface}; border: none; padding: 4px; }}"
        )
        self.restore_session(session)

    def supports_zoom(self) -> bool:
        return True

    def supports_search(self) -> bool:
        return True

    def set_zoom(self, factor: float) -> None:
        factor = max(0.5, min(3.0, factor))
        self._zoom = factor
        font = self._browser.font()
        font.setPointSizeF(self._base_font_size * factor)
        self._browser.setFont(font)
        self.zoom_changed.emit(factor)

    def search(self, query: str) -> None:
        if not query:
            self.search_result.emit(0)
            return
        full_text = self._browser.toPlainText()
        count = full_text.lower().count(query.lower())
        self.search_result.emit(count)
        cursor = self._browser.textCursor()
        cursor.movePosition(cursor.MoveOperation.Start)
        self._browser.setTextCursor(cursor)
        self._browser.find(query)

    def go_to_page(self, n: int) -> None:
        pass  # no pagination

    def save_session(self, session: "DocumentSession") -> None:
        sb = self._browser.verticalScrollBar()
        session.scroll_y = sb.value() / max(sb.maximum(), 1)
        session.zoom = self._zoom

    def restore_session(self, session: "DocumentSession") -> None:
        if session.zoom != 1.0:
            self.set_zoom(session.zoom)
        if session.scroll_y > 0:
            from PySide6.QtCore import QTimer
            QTimer.singleShot(50, lambda: self._set_scroll(session.scroll_y))
        if session.search_query:
            self.search(session.search_query)

    def _set_scroll(self, y: float) -> None:
        sb = self._browser.verticalScrollBar()
        sb.setValue(int(y * sb.maximum()))

    # ── Keyboard shortcuts ─────────────────────────────────────────────────────

    def keyPressEvent(self, event) -> None:
        mods = event.modifiers()
        key = event.key()
        if mods == Qt.KeyboardModifier.ControlModifier:
            if key in (Qt.Key.Key_Equal, Qt.Key.Key_Plus):
                self.set_zoom(self._zoom + 0.1)
                return
            if key == Qt.Key.Key_Minus:
                self.set_zoom(self._zoom - 0.1)
                return
            if key == Qt.Key.Key_0:
                self.set_zoom(1.0)
                return
        super().keyPressEvent(event)

    def wheelEvent(self, event) -> None:
        if event.modifiers() == Qt.KeyboardModifier.ControlModifier:
            delta = event.angleDelta().y()
            self.set_zoom(self._zoom + (0.1 if delta > 0 else -0.1))
            event.accept()
        else:
            super().wheelEvent(event)
