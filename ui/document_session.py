"""
DocumentSession — serializable per-document viewing state.

Stored in DocumentCache so reopening the same document restores position,
zoom, search query, and navigation history.  No Qt imports; pure data.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ui.viewers.base_viewer import AbstractViewer


@dataclass
class DocumentSession:
    url: str
    zoom: float = 1.0
    current_page: int = 0      # PDF page index or PPTX slide index (0-based)
    scroll_x: float = 0.0      # normalized 0–1
    scroll_y: float = 0.0      # normalized 0–1
    search_query: str = ""
    search_result_index: int = 0
    nav_history: list[int] = field(default_factory=list)
    active_sheet: int = 0      # XLSX sheet tab index

    # ── Persistence helpers ────────────────────────────────────────────────────

    def save_state(self, viewer: "AbstractViewer") -> None:
        """Read current state out of the viewer widget and store it here."""
        try:
            viewer.save_session(self)
        except Exception:
            pass

    def restore_state(self, viewer: "AbstractViewer") -> None:
        """Push stored state back into the viewer widget after it has loaded."""
        try:
            viewer.restore_session(self)
        except Exception:
            pass

    # ── Serialization (for optional file-based persistence) ───────────────────

    def to_dict(self) -> dict:
        return {
            "url": self.url,
            "zoom": self.zoom,
            "current_page": self.current_page,
            "scroll_x": self.scroll_x,
            "scroll_y": self.scroll_y,
            "search_query": self.search_query,
            "search_result_index": self.search_result_index,
            "nav_history": self.nav_history,
            "active_sheet": self.active_sheet,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "DocumentSession":
        return cls(
            url=d.get("url", ""),
            zoom=float(d.get("zoom", 1.0)),
            current_page=int(d.get("current_page", 0)),
            scroll_x=float(d.get("scroll_x", 0.0)),
            scroll_y=float(d.get("scroll_y", 0.0)),
            search_query=d.get("search_query", ""),
            search_result_index=int(d.get("search_result_index", 0)),
            nav_history=list(d.get("nav_history", [])),
            active_sheet=int(d.get("active_sheet", 0)),
        )
