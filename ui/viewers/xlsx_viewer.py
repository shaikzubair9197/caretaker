"""
XlsxViewer — displays XLSX spreadsheets using QTableView + XlsxTableModel.

One tab per sheet.  The model uses fetchMore/canFetchMore for automatic
lazy row loading when the user scrolls to the bottom.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHeaderView,
    QTabWidget,
    QTableView,
    QVBoxLayout,
)

from ui.viewers.base_viewer import AbstractViewer

if TYPE_CHECKING:
    from ui.document_parser_service import ParsedXlsx
    from ui.document_session import DocumentSession
    from ui.renderers.xlsx_renderer import XlsxRenderer


class XlsxViewer(AbstractViewer):
    """QTabWidget + QTableView per worksheet."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self._tabs = QTabWidget()
        self._tabs.setTabPosition(QTabWidget.TabPosition.South)
        layout.addWidget(self._tabs)

        self._renderer = None

    # ── AbstractViewer ─────────────────────────────────────────────────────────

    def load(
        self,
        doc: "ParsedXlsx",
        renderer: "XlsxRenderer",
        session: "DocumentSession",
    ) -> None:
        self._renderer = renderer
        self._tabs.clear()

        for sheet in doc.sheets:
            model = renderer.render_sheet(sheet)
            view = self._build_table_view(model)
            self._tabs.addTab(view, sheet.name or "Sheet")

        # Restore active sheet
        active = session.active_sheet
        if 0 <= active < self._tabs.count():
            self._tabs.setCurrentIndex(active)

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
        session.active_sheet = self._tabs.currentIndex()
        view = self._current_view()
        if view:
            vsb = view.verticalScrollBar()
            session.scroll_y = vsb.value() / max(vsb.maximum(), 1)

    def restore_session(self, session: "DocumentSession") -> None:
        pass  # handled in load()

    # ── Helpers ────────────────────────────────────────────────────────────────

    def _build_table_view(self, model) -> QTableView:
        view = QTableView()
        view.setModel(model)
        view.setAlternatingRowColors(True)
        view.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        view.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        view.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        view.horizontalHeader().setStretchLastSection(True)
        view.verticalHeader().setDefaultSectionSize(24)
        view.setStyleSheet(
            "QTableView { border: none; font-size: 12px; }"
            "QHeaderView::section { font-weight: 600; font-size: 11px; }"
        )
        return view

    def _current_view(self) -> QTableView | None:
        w = self._tabs.currentWidget()
        return w if isinstance(w, QTableView) else None

    # ── Keyboard shortcuts ─────────────────────────────────────────────────────

    def keyPressEvent(self, event) -> None:
        key = event.key()
        mods = event.modifiers()
        if mods == Qt.KeyboardModifier.ControlModifier:
            if key == Qt.Key.Key_PageDown:
                idx = self._tabs.currentIndex()
                if idx + 1 < self._tabs.count():
                    self._tabs.setCurrentIndex(idx + 1)
                return
            if key == Qt.Key.Key_PageUp:
                idx = self._tabs.currentIndex()
                if idx > 0:
                    self._tabs.setCurrentIndex(idx - 1)
                return
        super().keyPressEvent(event)
