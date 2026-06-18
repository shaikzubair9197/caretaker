"""
Authenticated attachment downloader with temp-file caching.

    path, content_type, filename = AttachmentDownloadService.get(url, headers)
"""

import hashlib
import mimetypes
import re
import tempfile
import threading
from pathlib import Path

import requests


_CT_TO_SUFFIX: dict[str, str] = {
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
    "application/pdf": ".pdf",
    "text/plain": ".txt",
    "text/markdown": ".md",
}


class AttachmentDownloadService:
    """Downloads API attachments with X-API-Key auth and caches them to temp files."""

    _cache: dict[str, tuple[Path, str, str]] = {}
    _lock = threading.Lock()

    @classmethod
    def get(cls, url: str, headers: dict, filename_hint: str = "") -> tuple[Path, str, str]:
        """
        Returns (local_path, content_type, filename).
        Raises RuntimeError on network / auth failure.
        """
        key = hashlib.sha256(url.encode()).hexdigest()

        with cls._lock:
            if key in cls._cache:
                cached_path, ct, fname = cls._cache[key]
                if cached_path.exists():
                    return cached_path, ct, fname
                del cls._cache[key]

        resp = requests.get(url, headers=headers, timeout=60, stream=True)
        if resp.status_code != 200:
            raise RuntimeError(
                f"Attachment download failed ({resp.status_code}): {resp.text[:300]}"
            )

        ct = resp.headers.get("content-type", "application/octet-stream").split(";")[0].strip()
        fname = cls._extract_filename(resp.headers, url, ct, filename_hint)
        suffix = Path(fname).suffix or _CT_TO_SUFFIX.get(ct) or mimetypes.guess_extension(ct) or ""

        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
        try:
            for chunk in resp.iter_content(chunk_size=65_536):
                if chunk:
                    tmp.write(chunk)
        finally:
            tmp.close()

        path = Path(tmp.name)
        with cls._lock:
            cls._cache[key] = (path, ct, fname)

        return path, ct, fname

    @staticmethod
    def _extract_filename(headers, url: str, ct: str, filename_hint: str = "") -> str:
        cd = headers.get("content-disposition", "")
        m = re.search(r"filename\*?=[\"']?(?:UTF-8'')?([^\"';\r\n]+)", cd, re.IGNORECASE)
        if m:
            return m.group(1).strip().strip("\"'")

        hint_ext = Path(filename_hint).suffix.lower() if filename_hint else ""
        segment = url.rstrip("/").rsplit("/", 1)[-1]
        if "." in segment:
            return segment
        if hint_ext:
            return f"document{hint_ext}"
        ext = _CT_TO_SUFFIX.get(ct) or mimetypes.guess_extension(ct) or ""
        return f"document{ext}"
