"""
PptxRenderer — renders PPTX slides to QImage via QPainter.

Using QPainter on QImage is fully thread-safe.  The previous QGraphicsScene
approach was dropped because QPixmap (required for QGraphicsPixmapItem) can
only be created on the GUI thread.  Callers convert QImage → QPixmap in their
main-thread signal slot.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QRect, QRectF, Qt
from PySide6.QtGui import (
    QColor,
    QFont,
    QImage,
    QPainter,
    QPen,
    QTextDocument,
)

if TYPE_CHECKING:
    from ui.document_parser_service import DocxRun, ParsedPptx, PptxShape


_DARK_BG = "#1e1e2e"
_FG = "#e6edf3"


def _run_html(run: "DocxRun") -> str:
    """Convert a single DocxRun to an HTML span fragment."""
    if run.text == "\n":
        return "<br/>"

    styles = []
    if run.bold:
        styles.append("font-weight:bold")
    if run.italic:
        styles.append("font-style:italic")
    if run.underline:
        styles.append("text-decoration:underline")
    if run.color:
        styles.append(f"color:{run.color}")
    if run.font_size and run.font_size > 0:
        styles.append(f"font-size:{run.font_size}pt")
    if run.font_name:
        styles.append(f"font-family:'{run.font_name}'")
    style_str = ";".join(styles)
    text = run.text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    # Defensive: any run carrying a literal newline (not just the synthetic
    # paragraph/line-break separators) must still render as a break — Qt's
    # rich-text engine doesn't reliably honor "white-space: pre-wrap".
    text = text.replace("\n", "<br/>")
    if style_str:
        return f'<span style="{style_str}">{text}</span>'
    return text


class PptxRenderer:
    """Converts PptxSlide objects to QImage via QPainter (thread-safe)."""

    def __init__(self, doc: "ParsedPptx") -> None:
        self._slides = doc.slides

    def slide_count(self) -> int:
        return len(self._slides)

    def render_slide(self, slide_idx: int, width_px: int = 960) -> QImage:
        """Render a slide to QImage using QPainter.  Safe to call from any thread."""
        slide = self._slides[slide_idx]

        if slide.slide_width > 0 and slide.slide_height > 0:
            aspect = slide.slide_height / slide.slide_width
        else:
            aspect = 9 / 16
        height_px = int(width_px * aspect)

        img = QImage(width_px, height_px, QImage.Format.Format_ARGB32)
        bg = QColor(slide.background_color) if slide.background_color else QColor(_DARK_BG)
        img.fill(bg if bg.isValid() else QColor(_DARK_BG))

        if not slide.shapes:
            return img

        scale_x = width_px / max(slide.slide_width, 1)
        scale_y = height_px / max(slide.slide_height, 1)

        painter = QPainter(img)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)

        for shape in slide.shapes:
            x = int(shape.left * scale_x)
            y = int(shape.top * scale_y)
            w = int(shape.width * scale_x)
            h = int(shape.height * scale_y)
            if w <= 0 or h <= 0:
                continue

            if shape.image_data:
                self._paint_image(painter, shape.image_data, x, y, w, h)
            elif shape.table is not None:
                self._paint_table(painter, shape.table, x, y, w, h)
            elif shape.runs or shape.text:
                self._paint_text(painter, shape, x, y, w, h)

        painter.end()
        return img

    # ── Shape painters ─────────────────────────────────────────────────────────

    def _paint_text(
        self, painter: QPainter, shape: "PptxShape", x: int, y: int, w: int, h: int
    ) -> None:
        doc = QTextDocument()
        doc.setDocumentMargin(0)
        # Only constrain the wrap width, not the page height: setPageSize(w, h)
        # would put the document in paginated mode, which truncates content
        # taller than h instead of letting it lay out fully.
        doc.setTextWidth(w)
        alignment = getattr(shape, "alignment", "left")
        style = (
            f"color:{_FG};font-size:12pt;text-align:{alignment};"
            "white-space:pre-wrap;word-wrap:break-word;overflow-wrap:break-word;"
        )
        if shape.runs:
            html_parts = [_run_html(r) for r in shape.runs]
            html = f'<div style="{style}">' + "".join(html_parts) + "</div>"
            doc.setHtml(html)
        else:
            doc.setDefaultStyleSheet(f"* {{ {style} }}")
            doc.setPlainText(shape.text or "")
        # Text that doesn't fit the shape's nominal box (autofit-shrunk text,
        # under-measured boxes, etc.) must overflow visibly rather than be
        # clipped away — draw at the document's actual laid-out height.
        content_height = max(float(h), doc.size().height())
        painter.save()
        painter.translate(x, y)
        doc.drawContents(painter, QRectF(0, 0, w, content_height))
        painter.restore()

    def _paint_image(
        self, painter: QPainter, image_data: bytes, x: int, y: int, w: int, h: int
    ) -> None:
        try:
            src = QImage()
            src.loadFromData(image_data)
            if src.isNull():
                return
            scaled = src.scaled(
                w,
                h,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            painter.drawImage(x, y, scaled)
        except Exception:
            pass

    def _paint_table(
        self, painter: QPainter, table, x: int, y: int, w: int, h: int
    ) -> None:
        n_rows = len(table)
        n_cols = max((len(r) for r in table), default=0)
        if n_rows == 0 or n_cols == 0:
            return

        cell_w = w / n_cols
        cell_h = h / n_rows

        painter.save()
        for r_idx, row in enumerate(table):
            for c_idx, cell_text in enumerate(row):
                cx = x + int(c_idx * cell_w)
                cy = y + int(r_idx * cell_h)
                cw = int(cell_w)
                ch = int(cell_h)

                bg = QColor("#21262d") if r_idx == 0 else QColor("#161b22")
                painter.fillRect(cx, cy, cw, ch, bg)
                painter.setPen(QPen(QColor("#30363d"), 1))
                painter.drawRect(cx, cy, cw - 1, ch - 1)

                font = QFont()
                font.setPointSize(10)
                if r_idx == 0:
                    font.setBold(True)
                painter.setFont(font)
                painter.setPen(QColor(_FG))
                painter.drawText(
                    QRect(cx + 4, cy, cw - 8, ch),
                    Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                    str(cell_text)[:30],
                )
        painter.restore()

    def render_thumbnail(self) -> QImage | None:
        """Render slide 0 at a small size for thumbnail display (thread-safe)."""
        try:
            return self.render_slide(0, width_px=240)
        except Exception:
            return None
