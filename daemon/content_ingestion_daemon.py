"""
Content ingestion daemon (Document Retrieval plan — Phase 1).

Maintains the Content Retrieval catalog continuously:

  • On startup: reclaim any rows stuck in INDEXING (previous crash), run a full
    initial scan (enqueue new/changed, deactivate vanished), then drain the queue.
  • A watchdog Observer watches every configured root. Filesystem events do NO
    heavy work — they only record the changed path (rule #2). After a short
    debounce the path is reconciled (cheap stat/version check → sets work state).
  • A ContentWorker drains the DB work-queue each tick (the only place file
    bytes are read / text extracted / embeddings computed).
  • A periodic fallback FULL scan catches anything the watcher missed and handles
    deletions (events for deletes are intentionally left to the full scan).

Runs as its own process (like daemon/meeting_scheduler.py) and talks to the DB
directly — there is no API dependency.

Usage:
    python -m daemon.content_ingestion_daemon
    # or
    python daemon/content_ingestion_daemon.py
"""

import threading
import time
from pathlib import Path

from dotenv import load_dotenv

# Load caretaker/.env so DB_* (and EMBEDDING_MODEL etc.) are available when this
# daemon runs as its own process — same rationale as daemon/meeting_scheduler.py.
load_dotenv()

from watchdog.events import FileSystemEventHandler  # noqa: E402
from watchdog.observers import Observer  # noqa: E402

from services.content_indexer import ContentIndexer, ContentWorker  # noqa: E402
from utils.config import settings  # noqa: E402
from utils.logger import get_logger  # noqa: E402

logger = get_logger("daemon.content_ingestion")


class _DebouncedHandler(FileSystemEventHandler):
    """Records changed paths only — never reconciles or processes in the event
    callback (rule #2: no heavy work in the fs event). The main loop flushes the
    recorded paths after they have been quiet for the debounce interval, so a
    long file copy reconciles once it settles rather than on every write."""

    def __init__(self, pending: dict, lock: threading.Lock, supported_exts: set[str]) -> None:
        super().__init__()
        self._pending = pending
        self._lock = lock
        self._supported = supported_exts

    def _record(self, path: str) -> None:
        if self._supported and Path(path).suffix.lower() not in self._supported:
            return
        with self._lock:
            self._pending[str(path)] = time.monotonic()

    def on_created(self, event) -> None:
        if not event.is_directory:
            self._record(event.src_path)

    def on_modified(self, event) -> None:
        if not event.is_directory:
            self._record(event.src_path)

    def on_moved(self, event) -> None:
        if not event.is_directory:
            self._record(event.dest_path)
        # The old path's removal is reconciled by the periodic full scan.


def main() -> None:
    indexer = ContentIndexer()
    worker = ContentWorker()
    roots = list(indexer.provider.roots)

    logger.info(
        f"Content ingestion daemon starting — roots={[str(r) for r in roots]} "
        f"poll={settings.CONTENT_WORKER_POLL_SECONDS}s "
        f"debounce={settings.CONTENT_WATCH_DEBOUNCE_SECONDS}s "
        f"fallback_scan={settings.CONTENT_FALLBACK_SCAN_SECONDS}s"
    )

    # ── Startup: reclaim, full scan, drain ────────────────────────────────────
    worker.initialize()
    indexer.initial_scan()
    drained = worker.run_until_idle()
    logger.info(f"Startup drain complete: {drained} item(s) processed")

    # ── Watch roots (events only record paths) ────────────────────────────────
    pending: dict[str, float] = {}
    lock = threading.Lock()
    handler = _DebouncedHandler(pending, lock, set(settings.CONTENT_SUPPORTED_EXTS))
    observer = Observer()
    watched = 0
    for root in roots:
        if root.exists() and root.is_dir():
            observer.schedule(handler, str(root), recursive=True)
            watched += 1
        else:
            logger.warning(f"Root not watchable (missing): {root}")
    if watched:
        observer.start()
        logger.info(f"Watching {watched} root(s)")
    else:
        logger.warning("No watchable roots — relying on periodic fallback scan only")

    debounce = settings.CONTENT_WATCH_DEBOUNCE_SECONDS
    poll = settings.CONTENT_WORKER_POLL_SECONDS
    fallback = settings.CONTENT_FALLBACK_SCAN_SECONDS
    last_fallback = time.monotonic()

    try:
        while True:
            now = time.monotonic()

            # Flush debounced paths that have settled → cheap reconcile.
            due: list[str] = []
            with lock:
                for path, stamp in list(pending.items()):
                    if now - stamp >= debounce:
                        due.append(path)
                        del pending[path]
            for path in due:
                try:
                    indexer.reconcile_path(path)
                except Exception as e:  # noqa: BLE001
                    logger.warning(f"Debounced reconcile failed for {path}: {e}")

            # Process whatever is queued.
            worker.run_until_idle()

            # Periodic full scan (missed events + deletions).
            if now - last_fallback >= fallback:
                logger.info("Running periodic fallback full scan")
                indexer.initial_scan()
                worker.run_until_idle()
                last_fallback = now

            time.sleep(poll)
    except KeyboardInterrupt:
        logger.info("Content ingestion daemon stopping")
    finally:
        if watched:
            observer.stop()
            observer.join()
        worker.shutdown()


if __name__ == "__main__":
    main()
