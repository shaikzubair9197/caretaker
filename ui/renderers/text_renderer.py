"""
TextRenderer — converts ParsedText to HTML for display in QTextBrowser.

Markdown files are converted via the `markdown` library with a minimal CSS
matching the active theme.  Plain text is wrapped in a monospace <pre> block.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ui.document_parser_service import ParsedText
    from ui.theme import Theme


class TextRenderer:
    """Converts a ParsedText to an HTML string ready for QTextBrowser.setHtml()."""

    def render(self, doc: "ParsedText", theme: "Theme") -> str:
        is_md = Path(doc.filename).suffix.lower() in (".md", ".markdown")
        content = doc.content

        css = f"""
        body {{
            background-color: {theme.surface};
            color: {theme.fg};
            font-family: "Inter", "Segoe UI", "SF Pro Text", sans-serif;
            font-size: 13px;
            line-height: 1.7;
            margin: 12px 16px;
        }}
        h1, h2, h3, h4 {{
            color: {theme.fg};
            border-bottom: 1px solid {theme.border};
            padding-bottom: 4px;
        }}
        code {{
            background-color: {theme.surface2};
            color: {theme.accent};
            padding: 2px 5px;
            border-radius: 4px;
            font-family: "JetBrains Mono", "Fira Code", "Cascadia Code", monospace;
            font-size: 12px;
        }}
        pre {{
            background-color: {theme.surface2};
            padding: 12px;
            border-radius: 6px;
            overflow-x: auto;
            font-family: "JetBrains Mono", "Fira Code", monospace;
            font-size: 12px;
        }}
        blockquote {{
            border-left: 3px solid {theme.accent};
            margin: 0;
            padding-left: 14px;
            color: {theme.subtle};
        }}
        a {{ color: {theme.accent}; text-decoration: underline; }}
        table {{
            border-collapse: collapse;
            width: 100%;
        }}
        th, td {{
            border: 1px solid {theme.border};
            padding: 6px 10px;
            text-align: left;
        }}
        th {{ background-color: {theme.table_hdr}; color: {theme.subtle}; font-weight: 600; }}
        tr:nth-child(even) {{ background-color: {theme.surface2}; }}
        """

        if is_md:
            try:
                import markdown
                body = markdown.markdown(
                    content,
                    extensions=["extra", "codehilite", "tables", "fenced_code"],
                )
            except ImportError:
                # Fallback: escape and wrap
                escaped = content.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                body = f'<pre style="white-space: pre-wrap;">{escaped}</pre>'
        else:
            escaped = content.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            body = (
                f'<pre style="white-space: pre-wrap; font-family: \'JetBrains Mono\','
                f'\'Fira Code\', monospace; font-size: 12px;">{escaped}</pre>'
            )

        return f"<html><head><style>{css}</style></head><body>{body}</body></html>"

    def render_thumbnail(self) -> None:
        return None
