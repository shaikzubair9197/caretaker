"""
AbstractViewer — the interface all document viewers must implement.

Viewers are QWidget subclasses that:
  - Receive a ParsedDoc + renderer + DocumentSession via load()
  - Emit signals when zoom/page changes
  - Support save_session() / restore_session() for state persistence
  - Handle keyboard shortcuts at the widget level (super() chains to dialog)

Note: PySide6's Shiboken metaclass is incompatible with ABCMeta, so we use
      NotImplementedError guards instead of abc.abstractmethod.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QWidget

if TYPE_CHECKING:
    from ui.document_session import DocumentSession


class AbstractViewer(QWidget):
    zoom_changed = Signal(float)      # emitted when zoom factor changes
    page_changed = Signal(int, int)   # (current_index, total_count)
    search_result = Signal(int)       # number of matches found

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

    def load(self, doc, renderer, session: "DocumentSession") -> None:
        raise NotImplementedError

    def supports_zoom(self) -> bool:
        raise NotImplementedError

    def supports_search(self) -> bool:
        raise NotImplementedError

    def set_zoom(self, factor: float) -> None:
        raise NotImplementedError

    def search(self, query: str) -> None:
        raise NotImplementedError

    def go_to_page(self, n: int) -> None:
        raise NotImplementedError

    def save_session(self, session: "DocumentSession") -> None:
        raise NotImplementedError

    def restore_session(self, session: "DocumentSession") -> None:
        """Apply session state to viewer after load(). Default: no-op."""
