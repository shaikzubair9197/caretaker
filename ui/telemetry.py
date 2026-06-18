"""
Structured telemetry for the document viewer pipeline.

All events are emitted as JSON-serializable log records via Python's standard
logging framework. Attach a handler that writes to a file or sends to a backend
if you want persistence; by default they go to the root logger at DEBUG level.

Usage:
    from ui.telemetry import log_download, log_render, ...
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

_logger = logging.getLogger("caretaker.ui.document")


def _emit(event: str, **fields: Any) -> None:
    record = {"event": event, "ts": time.time(), **fields}
    _logger.debug(json.dumps(record))


# ── Public helpers ─────────────────────────────────────────────────────────────

def log_download(
    url: str,
    elapsed_ms: float,
    cached: bool,
    status_code: int | None = None,
    error: str | None = None,
) -> None:
    _emit(
        "download",
        url=url,
        elapsed_ms=round(elapsed_ms, 1),
        cached=cached,
        status_code=status_code,
        error=error,
    )


def log_parse(
    url: str,
    fmt: str,
    elapsed_ms: float,
    error: str | None = None,
) -> None:
    _emit("parse", url=url, fmt=fmt, elapsed_ms=round(elapsed_ms, 1), error=error)


def log_render(
    url: str,
    fmt: str,
    page_or_slide: int,
    elapsed_ms: float,
    cached: bool,
    error: str | None = None,
) -> None:
    _emit(
        "render",
        url=url,
        fmt=fmt,
        page_or_slide=page_or_slide,
        elapsed_ms=round(elapsed_ms, 1),
        cached=cached,
        error=error,
    )


def log_thumbnail(
    url: str,
    fmt: str,
    elapsed_ms: float,
    cached: bool,
    error: str | None = None,
) -> None:
    _emit(
        "thumbnail",
        url=url,
        fmt=fmt,
        elapsed_ms=round(elapsed_ms, 1),
        cached=cached,
        error=error,
    )


def log_cancel(url: str, stage: str) -> None:
    _emit("cancel", url=url, stage=stage)


def log_viewer_open(url: str, fmt: str, from_cache: bool) -> None:
    _emit("viewer_open", url=url, fmt=fmt, from_cache=from_cache)


def log_cache_miss(url: str, level: str) -> None:
    _emit("cache_miss", url=url, level=level)


def log_cache_hit(url: str, level: str) -> None:
    _emit("cache_hit", url=url, level=level)
