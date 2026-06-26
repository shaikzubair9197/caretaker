"""
content_providers — where content lives (Document Retrieval plan — Phase 1).

A ContentProvider enumerates content as IndexedContentCandidate objects: the
metadata needed to catalog a piece of content plus a LAZY byte accessor (so a
scan does not read file bodies — bytes are pulled only when the indexer actually
processes an item). LocalFolderProvider (a local MeetingDocs/ folder) is the only
provider in Phase 1; SharePoint/Teams providers plug in behind the same interface
later with no change to the processor, catalog, indexer or retrieval layers.

LOCAL source_metadata is {root, relative_path}; the catalog re-derives and
path-confines the absolute path from it (no filesystem path ever reaches the UI).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterator, Optional

from utils.config import settings
from utils.logger import get_logger

logger = get_logger("services.content_providers")

# Stable base for resolving relative roots (e.g. "MeetingDocs") regardless of the
# process cwd: services/ -> caretaker/.
_BASE_DIR = Path(__file__).resolve().parents[1]


@dataclass
class IndexedContentCandidate:
    """One unit of content discovered by a provider. Carries identity + file
    metadata and a lazy `read_bytes()` accessor; the body is read only when the
    indexer decides the item needs (re)processing."""

    source_type: str
    source_identifier: str          # stable identity within the source (abs path for LOCAL)
    source_metadata: dict           # LOCAL: {root, relative_path}
    root_label: str
    filename: str
    folder: str                     # immediate parent folder name (diversification signal)
    folder_path: str                # relative folder path from the root (server-side only)
    extension: str
    size_bytes: int
    modified_at: Optional[datetime]
    _reader: Optional[Callable[[], bytes]] = field(default=None, repr=False)

    def read_bytes(self) -> bytes:
        if self._reader is None:
            raise RuntimeError(f"candidate {self.source_identifier} has no byte reader")
        return self._reader()


class ContentProvider(ABC):
    """Enumerates content for one source type."""

    source_type: str = ""

    @abstractmethod
    def iter_candidates(self) -> Iterator[IndexedContentCandidate]:  # pragma: no cover - interface
        ...


def _to_naive_utc(epoch_seconds: float) -> datetime:
    """File mtimes are epoch seconds — store as naive UTC to match the DB
    convention (utils.time_utils.utcnow)."""
    return datetime.fromtimestamp(epoch_seconds, tz=timezone.utc).replace(tzinfo=None)


class LocalFolderProvider(ContentProvider):
    """Walks one or more local root folders and yields a candidate per supported,
    in-bounds file. Symlinks that escape a root are rejected (path confinement);
    unreadable entries are logged and skipped so one bad file never aborts a scan."""

    source_type = "LOCAL"

    def __init__(
        self,
        roots: Optional[list[str]] = None,
        supported_exts: Optional[list[str]] = None,
    ) -> None:
        raw_roots = roots if roots is not None else settings.MEETING_DOCS_ROOTS
        self._roots: list[tuple[str, Path]] = []
        for raw in raw_roots:
            p = Path(raw)
            resolved = p if p.is_absolute() else (_BASE_DIR / p)
            self._roots.append((resolved.name, resolved.resolve()))
        exts = supported_exts if supported_exts is not None else settings.CONTENT_SUPPORTED_EXTS
        self._supported = {e.lower() for e in exts}

    @property
    def roots(self) -> list[Path]:
        return [root for _, root in self._roots]

    def iter_candidates(self) -> Iterator[IndexedContentCandidate]:
        for label, root in self._roots:
            if not root.exists():
                logger.warning(f"Content root does not exist, skipping: {root}")
                continue
            if not root.is_dir():
                logger.warning(f"Content root is not a directory, skipping: {root}")
                continue
            yield from self._iter_root(label, root)

    def candidate_for_path(self, path) -> Optional[IndexedContentCandidate]:
        """Build a candidate for a single path (used by the daemon's filesystem
        events). Returns None if the path is not under a configured root or is not
        an indexable file."""
        try:
            resolved = Path(path).resolve()
        except (OSError, ValueError) as e:
            logger.warning(f"Cannot resolve event path {path}: {e}")
            return None
        for label, root in self._roots:
            if resolved.is_relative_to(root):
                return self._candidate_for(label, root, resolved)
        return None

    def _iter_root(self, label: str, root: Path) -> Iterator[IndexedContentCandidate]:
        try:
            entries = root.rglob("*")
        except Exception as e:  # noqa: BLE001 - unreadable root
            logger.warning(f"Failed to list content root {root}: {e}")
            return
        for path in entries:
            candidate = self._candidate_for(label, root, path)
            if candidate is not None:
                yield candidate

    def _candidate_for(self, label: str, root: Path, path: Path) -> Optional[IndexedContentCandidate]:
        try:
            if not path.is_file():
                return None
            ext = path.suffix.lower()
            if self._supported and ext not in self._supported:
                return None
            # Path confinement: reject symlinks (or anything) resolving outside root.
            resolved = path.resolve()
            if not resolved.is_relative_to(root):
                logger.warning(f"Skipping path outside root (symlink escape?): {path}")
                return None
            stat = resolved.stat()
            relative = resolved.relative_to(root)
            parent_rel = relative.parent
            folder_path = "" if str(parent_rel) == "." else str(parent_rel)
            folder = root.name if str(parent_rel) == "." else parent_rel.name
            return IndexedContentCandidate(
                source_type=self.source_type,
                source_identifier=str(resolved),
                source_metadata={"root": str(root), "relative_path": str(relative)},
                root_label=label,
                filename=resolved.name,
                folder=folder,
                folder_path=folder_path,
                extension=ext,
                size_bytes=stat.st_size,
                modified_at=_to_naive_utc(stat.st_mtime),
                _reader=lambda p=resolved: p.read_bytes(),
            )
        except (OSError, ValueError) as e:
            logger.warning(f"Skipping unreadable content entry {path}: {e}")
            return None
