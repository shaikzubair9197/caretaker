"""
XlsxRenderer — converts XlsxSheet data into an XlsxTableModel (QAbstractTableModel).

Lazy row loading is provided automatically via fetchMore/canFetchMore so that
large spreadsheets don't block the UI on initial load.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from PySide6.QtCore import (
    QAbstractTableModel,
    QModelIndex,
    Qt,
)
from PySide6.QtGui import QBrush, QColor, QFont, QImage, QPainter

if TYPE_CHECKING:
    from ui.document_parser_service import XlsxCell, XlsxSheet

_PAGE_SIZE = 200


class XlsxTableModel(QAbstractTableModel):
    """
    Model for one XlsxSheet.  Uses fetchMore/canFetchMore for lazy row loading.
    """

    def __init__(self, sheet: "XlsxSheet", page_size: int = _PAGE_SIZE) -> None:
        super().__init__()
        self._sheet = sheet
        self._page_size = page_size
        self._loaded_rows = min(page_size, len(sheet.rows))

    # ── QAbstractTableModel interface ──────────────────────────────────────────

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        if parent.isValid():
            return 0
        return self._loaded_rows

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        if parent.isValid():
            return 0
        return len(self._sheet.columns)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid():
            return None
        r, c = index.row(), index.column()
        if r >= self._loaded_rows or r >= len(self._sheet.rows):
            return None
        row_cells = self._sheet.rows[r]
        if c >= len(row_cells):
            return None
        cell: "XlsxCell" = row_cells[c]

        if role == Qt.ItemDataRole.DisplayRole:
            return cell.value

        if role == Qt.ItemDataRole.FontRole:
            font = QFont()
            if cell.font_name:
                font.setFamily(cell.font_name)
            if cell.font_size and cell.font_size > 0:
                font.setPointSize(cell.font_size)
            if cell.bold:
                font.setBold(True)
            if cell.italic:
                font.setItalic(True)
            if cell.underline:
                font.setUnderline(True)
            return font

        if role == Qt.ItemDataRole.BackgroundRole:
            if cell.fill_color:
                try:
                    return QBrush(QColor(cell.fill_color))
                except Exception:
                    pass
            return None

        if role == Qt.ItemDataRole.ForegroundRole:
            if cell.font_color:
                try:
                    return QBrush(QColor(cell.font_color))
                except Exception:
                    pass
            return None

        if role == Qt.ItemDataRole.TextAlignmentRole:
            h_map = {
                "center": Qt.AlignmentFlag.AlignHCenter,
                "right": Qt.AlignmentFlag.AlignRight,
                "justify": Qt.AlignmentFlag.AlignJustify,
            }
            v_map = {
                "center": Qt.AlignmentFlag.AlignVCenter,
                "bottom": Qt.AlignmentFlag.AlignBottom,
            }
            h = h_map.get(cell.align, Qt.AlignmentFlag.AlignLeft)
            v = v_map.get(cell.valign, Qt.AlignmentFlag.AlignTop)
            return h | v

        return None

    def headerData(
        self,
        section: int,
        orientation: Qt.Orientation,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> Any:
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        if orientation == Qt.Orientation.Horizontal:
            cols = self._sheet.columns
            return cols[section] if section < len(cols) else str(section + 1)
        return str(section + 1)

    def canFetchMore(self, parent: QModelIndex = QModelIndex()) -> bool:
        return self._loaded_rows < len(self._sheet.rows)

    def fetchMore(self, parent: QModelIndex = QModelIndex()) -> None:
        remaining = len(self._sheet.rows) - self._loaded_rows
        batch = min(self._page_size, remaining)
        if batch <= 0:
            return
        self.beginInsertRows(QModelIndex(), self._loaded_rows, self._loaded_rows + batch - 1)
        self._loaded_rows += batch
        self.endInsertRows()


class XlsxRenderer:
    """Creates an XlsxTableModel for one sheet and optionally a thumbnail.

    Pass the ParsedXlsx doc at construction so render_thumbnail() can be called
    with no arguments from ThumbnailRunnable (which uses the first sheet).
    """

    def __init__(self, doc=None) -> None:
        self._doc = doc

    def render_sheet(self, sheet: "XlsxSheet") -> XlsxTableModel:
        return XlsxTableModel(sheet)

    def render_thumbnail(self, sheet: "XlsxSheet | None" = None) -> QImage | None:
        """Render top-left 5×5 cells as a QImage (thread-safe)."""
        try:
            if sheet is None:
                if self._doc is None or not self._doc.sheets:
                    return None
                sheet = self._doc.sheets[0]
            n_rows = min(5, len(sheet.rows))
            n_cols = min(5, len(sheet.columns))
            if n_rows == 0 or n_cols == 0:
                return None
            cell_w, cell_h = 40, 20
            img = QImage(n_cols * cell_w, n_rows * cell_h, QImage.Format.Format_ARGB32)
            img.fill(QColor("#161b22"))
            painter = QPainter(img)
            from PySide6.QtCore import QRect
            for r in range(n_rows):
                for c in range(n_cols):
                    row_cells = sheet.rows[r]
                    text = row_cells[c].value if c < len(row_cells) else ""
                    rect_x = c * cell_w
                    rect_y = r * cell_h
                    painter.fillRect(QRect(rect_x, rect_y, cell_w, cell_h),
                                     QColor("#21262d") if r == 0 else QColor("#161b22"))
                    painter.setPen(QColor("#30363d"))
                    painter.drawRect(rect_x, rect_y, cell_w - 1, cell_h - 1)
                    painter.setPen(QColor("#e6edf3"))
                    painter.drawText(QRect(rect_x + 2, rect_y, cell_w - 4, cell_h),
                                     Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                                     text[:6])
            painter.end()
            return img
        except Exception:
            return None
