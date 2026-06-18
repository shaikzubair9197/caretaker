"""
DocxViewer — displays a ParsedDocx document using a QTextEdit driven by DocxRenderer.

The renderer produces a fully-populated QTextDocument; this viewer only manages
UI state: zoom, search, scroll position.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QKeySequence, QTextDocument
from PySide6.QtWidgets import QTextEdit, QVBoxLayout

from ui.viewers.base_viewer import AbstractViewer

if TYPE_CHECKING:
    from ui.document_parser_service import ParsedDocx
    from ui.document_session import DocumentSession
    from ui.renderers.docx_renderer import DocxRenderer
    from ui.theme import Theme


class DocxViewer(AbstractViewer):
    """QTextEdit-based viewer driven by DocxRenderer."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self._edit = QTextEdit()
        self._edit.setReadOnly(True)
        self._edit.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        layout.addWidget(self._edit)

        self._base_font_size = 13.0
        self._zoom = 1.0

    # ── AbstractViewer ─────────────────────────────────────────────────────────

    def load(self, doc: "ParsedDocx", renderer: "DocxRenderer", session: "DocumentSession") -> None:
        from ui.theme import current_theme
        theme = current_theme()
        tdoc: QTextDocument = renderer.render(doc, theme)
        self._edit.setDocument(tdoc)
        self._apply_theme_bg(theme)
        self.restore_session(session)

    def supports_zoom(self) -> bool:
        return True

    def supports_search(self) -> bool:
        return True

    def set_zoom(self, factor: float) -> None:
        factor = max(0.5, min(3.0, factor))
        self._zoom = factor
        font = self._edit.document().defaultFont()
        font.setPointSizeF(self._base_font_size * factor)
        self._edit.document().setDefaultFont(font)
        self.zoom_changed.emit(factor)

    def search(self, query: str) -> None:
        if not query:
            self._edit.moveCursor(self._edit.textCursor().MoveOperation.Start)
            self.search_result.emit(0)
            return
        # Count all occurrences for the result signal
        full_text = self._edit.toPlainText()
        count = full_text.lower().count(query.lower())
        self.search_result.emit(count)
        # Move to first match from top
        cursor = self._edit.textCursor()
        cursor.movePosition(cursor.MoveOperation.Start)
        self._edit.setTextCursor(cursor)
        self._edit.find(query)

    def go_to_page(self, n: int) -> None:
        pass  # DOCX is a single continuous document; no page navigation

    def save_session(self, session: "DocumentSession") -> None:
        sb = self._edit.verticalScrollBar()
        total = max(sb.maximum(), 1)
        session.scroll_y = sb.value() / total
        session.zoom = self._zoom

    def restore_session(self, session: "DocumentSession") -> None:
        if session.zoom != 1.0:
            self.set_zoom(session.zoom)
        if session.scroll_y > 0:
            sb = self._edit.verticalScrollBar()
            sb.setValue(int(session.scroll_y * sb.maximum()))
        if session.search_query:
            self.search(session.search_query)

    # ── Keyboard shortcuts ─────────────────────────────────────────────────────

    def keyPressEvent(self, event) -> None:
        key = event.key()
        mods = event.modifiers()
        if mods == Qt.KeyboardModifier.ControlModifier:
            if key == Qt.Key.Key_Equal or key == Qt.Key.Key_Plus:
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

    def _apply_theme_bg(self, theme) -> None:
        self._edit.setStyleSheet(
            f"QTextEdit {{ background-color: {theme.surface}; color: {theme.fg}; border: none; padding: 16px; }}"
        )
