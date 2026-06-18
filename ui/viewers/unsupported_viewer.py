"""UnsupportedViewer — error state for unknown / unrenderable file types."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QVBoxLayout

from ui.viewers.base_viewer import AbstractViewer

if TYPE_CHECKING:
    from ui.document_parser_service import ParsedUnsupported
    from ui.document_session import DocumentSession


class UnsupportedViewer(AbstractViewer):
    """Shows a centered error message for unsupported formats."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._label = QLabel()
        self._label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._label.setWordWrap(True)
        self._label.setStyleSheet("QLabel { color: #f85149; font-size: 14px; }")
        layout.addWidget(self._label)

        self._hint = QLabel("This file type cannot be previewed in the app.")
        self._hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._hint.setStyleSheet("QLabel { color: #8b949e; font-size: 12px; margin-top: 8px; }")
        layout.addWidget(self._hint)

    def load(self, doc: "ParsedUnsupported", renderer, session: "DocumentSession") -> None:
        self._label.setText(doc.reason or "Unsupported file format")

    def supports_zoom(self) -> bool:
        return False

    def supports_search(self) -> bool:
        return False

    def set_zoom(self, factor: float) -> None:
        pass

    def search(self, query: str) -> None:
        self.search_result.emit(0)

    def go_to_page(self, n: int) -> None:
        pass

    def save_session(self, session: "DocumentSession") -> None:
        pass
