"""
DocumentCache — four-level LRU cache for the document viewer pipeline.

Levels:
  1. bytes   — downloaded temp-file paths          (max 20 entries)
  2. parsed  — ParsedDoc model objects              (max 10 entries)
  3. rendered
       pages  — (url, page_idx, zoom) → QPixmap    (max 300 entries)
       scenes — (url, slide_idx) → QGraphicsScene  (max 50  entries)
       models — url → XlsxTableModel               (max 5   entries)
  4. sessions — url → DocumentSession              (max 30  entries)

All operations are thread-safe via a single RLock.
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from pathlib import Path
from typing import Generic, Hashable, TypeVar

V = TypeVar("V")


# ── Minimal LRU container ──────────────────────────────────────────────────────

class _LRU(Generic[V]):
    """Thread-UNsafe LRU dict; locking done by DocumentCache."""

    def __init__(self, maxsize: int) -> None:
        self._max = maxsize
        self._store: OrderedDict[Hashable, V] = OrderedDict()

    def get(self, key: Hashable) -> V | None:
        if key not in self._store:
            return None
        self._store.move_to_end(key)
        return self._store[key]

    def put(self, key: Hashable, value: V) -> None:
        if key in self._store:
            self._store.move_to_end(key)
        self._store[key] = value
        while len(self._store) > self._max:
            self._store.popitem(last=False)

    def has(self, key: Hashable) -> bool:
        return key in self._store

    def remove(self, key: Hashable) -> None:
        self._store.pop(key, None)

    def keys_starting_with(self, prefix) -> list:
        return [k for k in list(self._store.keys()) if isinstance(k, tuple) and k[0] == prefix]

    def clear(self) -> None:
        self._store.clear()


# ── DocumentCache ──────────────────────────────────────────────────────────────

class DocumentCache:
    """
    Singleton-friendly four-level cache.  Instantiate once and share across
    DocumentController and viewer widgets.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._bytes: _LRU[Path] = _LRU(20)
        self._parsed: _LRU = _LRU(10)
        self._pages: _LRU = _LRU(300)
        self._scenes: _LRU = _LRU(50)
        self._models: _LRU = _LRU(5)
        self._sessions: _LRU = _LRU(30)

    # ── Level 1: downloaded bytes ──────────────────────────────────────────────

    def has_download(self, url: str) -> bool:
        with self._lock:
            if not self._bytes.has(url):
                return False
            p = self._bytes.get(url)
            if p is None or not p.exists():
                self._bytes.remove(url)
                return False
            return True

    def get_download(self, url: str) -> Path | None:
        with self._lock:
            p = self._bytes.get(url)
            if p is not None and not p.exists():
                self._bytes.remove(url)
                return None
            return p

    def store_download(self, url: str, path: Path) -> None:
        with self._lock:
            self._bytes.put(url, path)

    # ── Level 2: parsed models ─────────────────────────────────────────────────

    def has_parsed(self, url: str) -> bool:
        with self._lock:
            return self._parsed.has(url)

    def get_parsed(self, url: str):
        with self._lock:
            return self._parsed.get(url)

    def store_parsed(self, url: str, doc) -> None:
        with self._lock:
            self._parsed.put(url, doc)

    # ── Level 3a: rendered PDF pages ──────────────────────────────────────────

    def get_page(self, url: str, page_idx: int, zoom: float):
        key = (url, page_idx, round(zoom, 2))
        with self._lock:
            return self._pages.get(key)

    def store_page(self, url: str, page_idx: int, zoom: float, pixmap) -> None:
        key = (url, page_idx, round(zoom, 2))
        with self._lock:
            self._pages.put(key, pixmap)

    # ── Level 3b: rendered PPTX scenes ────────────────────────────────────────

    def get_scene(self, url: str, slide_idx: int):
        key = (url, slide_idx)
        with self._lock:
            return self._scenes.get(key)

    def store_scene(self, url: str, slide_idx: int, scene) -> None:
        key = (url, slide_idx)
        with self._lock:
            self._scenes.put(key, scene)

    # ── Level 3c: XLSX table models ────────────────────────────────────────────

    def get_model(self, url: str):
        with self._lock:
            return self._models.get(url)

    def store_model(self, url: str, model) -> None:
        with self._lock:
            self._models.put(url, model)

    # ── Level 4: session state ─────────────────────────────────────────────────

    def get_session(self, url: str):
        with self._lock:
            return self._sessions.get(url)

    def store_session(self, url: str, session) -> None:
        with self._lock:
            self._sessions.put(url, session)

    # ── Eviction ──────────────────────────────────────────────────────────────

    def evict(self, url: str) -> None:
        """Remove all cache levels for a given URL."""
        with self._lock:
            self._bytes.remove(url)
            self._parsed.remove(url)
            # Remove all page entries for this url
            for key in self._pages.keys_starting_with(url):
                self._pages.remove(key)
            for key in self._scenes.keys_starting_with(url):
                self._scenes.remove(key)
            self._models.remove(url)
            self._sessions.remove(url)

    def clear(self) -> None:
        with self._lock:
            self._bytes.clear()
            self._parsed.clear()
            self._pages.clear()
            self._scenes.clear()
            self._models.clear()
            self._sessions.clear()
