"""AbstractRenderer protocol — renderers have no knowledge of QWidget."""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class AbstractRenderer(Protocol):
    def render_thumbnail(self): ...
