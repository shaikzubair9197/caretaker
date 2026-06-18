"""
DocxRenderer — converts ParsedDocx into a QTextDocument.

The viewer just calls setDocument() and renders nothing itself; all document
structure (headings, bullets, tables, images, hyperlinks, headers/footers) is
assembled here using QTextCursor so that the DOCX viewer stays a thin wrapper.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import (
    QColor,
    QFont,
    QImage,
    QTextBlockFormat,
    QTextCharFormat,
    QTextCursor,
    QTextDocument,
    QTextFrameFormat,
    QTextLength,
    QTextListFormat,
    QTextTableFormat,
)

if TYPE_CHECKING:
    from ui.document_parser_service import DocxBlock, DocxRun, ParsedDocx
    from ui.theme import Theme


_HEADING_SIZES = {1: 24, 2: 20, 3: 17, 4: 15, 5: 13, 6: 12}
_INDENT_PX = 20


def _apply_run_fmt(fmt: QTextCharFormat, run: "DocxRun", theme: "Theme") -> None:
    if run.bold:
        fmt.setFontWeight(QFont.Weight.Bold)
    if run.italic:
        fmt.setFontItalic(True)
    if run.underline:
        fmt.setFontUnderline(True)
    if run.strike:
        fmt.setFontStrikeOut(True)
    if run.font_name:
        fmt.setFontFamilies([run.font_name])
    if run.font_size and run.font_size > 0:
        fmt.setFontPointSize(run.font_size)
    if run.color:
        fmt.setForeground(QColor(run.color))
    if run.hyperlink:
        fmt.setForeground(QColor(theme.accent))
        fmt.setFontUnderline(True)
        fmt.setAnchor(True)
        fmt.setAnchorHref(run.hyperlink)


class DocxRenderer:
    """Converts a ParsedDocx into a fully-populated QTextDocument."""

    def render(self, doc: "ParsedDocx", theme: "Theme") -> QTextDocument:
        tdoc = QTextDocument()
        tdoc.setDefaultStyleSheet(
            f"body {{ color: {theme.fg}; font-size: 13pt; }}"
            f"a {{ color: {theme.accent}; }}"
        )
        # Use a dark base color for the document background
        cursor = QTextCursor(tdoc)

        default_char = QTextCharFormat()
        default_char.setForeground(QColor(theme.fg))
        default_char.setFontPointSize(13)
        cursor.setCharFormat(default_char)

        first = True
        for block in doc.blocks:
            if first:
                first = False
            else:
                cursor.insertBlock()

            if block.kind == "heading":
                self._insert_heading(cursor, block, theme)
            elif block.kind == "bullet":
                self._insert_bullet(cursor, block, theme)
            elif block.kind in ("paragraph",):
                self._insert_paragraph(cursor, block, theme)
            elif block.kind == "table":
                self._insert_table(cursor, block, theme)
            elif block.kind == "image":
                self._insert_image(cursor, block)
            elif block.kind in ("header", "footer"):
                self._insert_band(cursor, block, theme)

        return tdoc

    # ── Helpers ────────────────────────────────────────────────────────────────

    def _insert_heading(self, cursor: QTextCursor, block: "DocxBlock", theme: "Theme") -> None:
        bf = QTextBlockFormat()
        bf.setTopMargin(block.spacing_before or 12)
        bf.setBottomMargin(block.spacing_after or 6)
        bf.setAlignment(self._alignment(block.alignment))
        cursor.setBlockFormat(bf)

        cf = QTextCharFormat()
        cf.setFontWeight(QFont.Weight.Bold)
        cf.setFontPointSize(_HEADING_SIZES.get(block.level, 13))
        cf.setForeground(QColor(theme.fg))

        if block.runs:
            for run in block.runs:
                rcf = QTextCharFormat(cf)
                _apply_run_fmt(rcf, run, theme)
                cursor.insertText(run.text, rcf)
        else:
            cursor.insertText(block.text, cf)

    def _insert_bullet(self, cursor: QTextCursor, block: "DocxBlock", theme: "Theme") -> None:
        list_fmt = QTextListFormat()
        list_fmt.setStyle(QTextListFormat.Style.ListDisc)
        indent_level = max(0, block.level)
        list_fmt.setIndent(indent_level + 1)

        # Check if we're continuing an existing list or starting a new one
        current_list = cursor.currentList()
        if current_list is None:
            cursor.createList(list_fmt)
        else:
            cursor.insertBlock()
            cursor.currentList().add(cursor.block()) if cursor.currentList() else cursor.createList(list_fmt)

        cf = QTextCharFormat()
        cf.setForeground(QColor(theme.fg))
        cf.setFontPointSize(13)
        if block.runs:
            for run in block.runs:
                rcf = QTextCharFormat(cf)
                _apply_run_fmt(rcf, run, theme)
                cursor.insertText(run.text, rcf)
        else:
            cursor.insertText(block.text, cf)

    def _insert_paragraph(self, cursor: QTextCursor, block: "DocxBlock", theme: "Theme") -> None:
        bf = QTextBlockFormat()
        bf.setTopMargin(block.spacing_before or 2)
        bf.setBottomMargin(block.spacing_after or 2)
        bf.setAlignment(self._alignment(block.alignment))
        if block.indent:
            bf.setLeftMargin(block.indent * _INDENT_PX)
        cursor.setBlockFormat(bf)

        cf = QTextCharFormat()
        cf.setForeground(QColor(theme.fg))
        cf.setFontPointSize(13)

        if block.runs:
            for run in block.runs:
                rcf = QTextCharFormat(cf)
                _apply_run_fmt(rcf, run, theme)
                cursor.insertText(run.text, rcf)
        else:
            cursor.insertText(block.text or "", cf)

    def _insert_table(self, cursor: QTextCursor, block: "DocxBlock", theme: "Theme") -> None:
        rows = block.rows
        if not rows:
            return
        n_rows = len(rows)
        n_cols = max(len(r) for r in rows) if rows else 0
        if n_cols == 0:
            return

        table_fmt = QTextTableFormat()
        table_fmt.setCellPadding(6)
        table_fmt.setCellSpacing(0)
        table_fmt.setBorderStyle(QTextTableFormat.BorderStyle.BorderStyle_Solid)
        table_fmt.setBorder(1)
        table_fmt.setBorderBrush(QColor(theme.border))
        col_widths = [QTextLength(QTextLength.Type.PercentageLength, 100.0 / n_cols)] * n_cols
        table_fmt.setColumnWidthConstraints(col_widths)

        table = cursor.insertTable(n_rows, n_cols, table_fmt)

        for r_idx, row in enumerate(rows):
            for c_idx, cell_text in enumerate(row):
                if c_idx >= n_cols:
                    break
                cell = table.cellAt(r_idx, c_idx)
                cell_cursor = cell.firstCursorPosition()

                header_fmt = QTextCharFormat()
                if r_idx == 0:
                    header_fmt.setFontWeight(QFont.Weight.Bold)
                    header_fmt.setBackground(QColor(theme.table_hdr))
                header_fmt.setForeground(QColor(theme.fg))
                header_fmt.setFontPointSize(12)
                cell_cursor.insertText(str(cell_text), header_fmt)

        # Move cursor past the table
        cursor.movePosition(QTextCursor.MoveOperation.End)

    def _insert_image(self, cursor: QTextCursor, block: "DocxBlock") -> None:
        if not block.image_data:
            return
        try:
            img = QImage()
            img.loadFromData(block.image_data)
            if img.isNull():
                return
            # Scale down if too wide
            max_w = 760
            if img.width() > max_w:
                img = img.scaledToWidth(max_w, Qt.TransformationMode.SmoothTransformation)
            # Use a resource name to embed in document
            name = f"img_{id(block)}"
            cursor.document().addResource(
                QTextDocument.ResourceType.ImageResource, QUrl(name), img
            )
            cf = QTextCharFormat()
            cf.setObjectType(QTextCharFormat.ObjectTypes.ImageObject)
            cf.setProperty(QTextCharFormat.Property.ImageName, name)
            cursor.insertText("￼", cf)
        except Exception:
            pass

    def _insert_band(self, cursor: QTextCursor, block: "DocxBlock", theme: "Theme") -> None:
        bf = QTextBlockFormat()
        bf.setBackground(QColor(theme.surface2))
        bf.setTopMargin(4)
        bf.setBottomMargin(4)
        cursor.setBlockFormat(bf)
        cf = QTextCharFormat()
        cf.setForeground(QColor(theme.subtle))
        cf.setFontPointSize(11)
        cursor.insertText(block.text or "", cf)

    @staticmethod
    def _alignment(align_str: str) -> Qt.AlignmentFlag:
        return {
            "center": Qt.AlignmentFlag.AlignHCenter,
            "right": Qt.AlignmentFlag.AlignRight,
            "justify": Qt.AlignmentFlag.AlignJustify,
        }.get(align_str, Qt.AlignmentFlag.AlignLeft)

    def render_thumbnail(self):
        """Return None — DOCX thumbnail is not currently generated."""
        return None
