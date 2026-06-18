"""
ViewerRegistry — plugin-style mapping from ParsedDoc type → AbstractViewer class.

Adding a new document format:
    1. Create ParsedFoo in document_parser_service.py
    2. Create FooRenderer in renderers/foo_renderer.py
    3. Create FooViewer in viewers/foo_viewer.py
    4. Call ViewerRegistry.register(ParsedFoo, FooViewer)

No existing code needs modification.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtWidgets import QWidget

if TYPE_CHECKING:
    from ui.document_parser_service import ParsedDoc
    from ui.viewers.base_viewer import AbstractViewer


class ViewerRegistry:
    _registry: dict[type, type] = {}

    @classmethod
    def register(cls, doc_type: type, viewer_cls: type) -> None:
        cls._registry[doc_type] = viewer_cls

    @classmethod
    def create(cls, doc: "ParsedDoc", parent: QWidget | None = None) -> "AbstractViewer":
        from ui.viewers.unsupported_viewer import UnsupportedViewer
        viewer_cls = cls._registry.get(type(doc), UnsupportedViewer)
        return viewer_cls(parent=parent)

    @classmethod
    def registered_types(cls) -> list[type]:
        return list(cls._registry.keys())


def _register_defaults() -> None:
    from ui.document_parser_service import (
        ParsedDocx,
        ParsedPdf,
        ParsedPptx,
        ParsedText,
        ParsedUnsupported,
        ParsedXlsx,
    )
    from ui.viewers.docx_viewer import DocxViewer
    from ui.viewers.pdf_viewer import PdfViewer
    from ui.viewers.pptx_viewer import PptxViewer
    from ui.viewers.text_viewer import TextViewer
    from ui.viewers.unsupported_viewer import UnsupportedViewer
    from ui.viewers.xlsx_viewer import XlsxViewer

    ViewerRegistry.register(ParsedDocx, DocxViewer)
    ViewerRegistry.register(ParsedPdf, PdfViewer)
    ViewerRegistry.register(ParsedPptx, PptxViewer)
    ViewerRegistry.register(ParsedXlsx, XlsxViewer)
    ViewerRegistry.register(ParsedText, TextViewer)
    ViewerRegistry.register(ParsedUnsupported, UnsupportedViewer)


_register_defaults()
