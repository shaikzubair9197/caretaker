import os

from dotenv import load_dotenv

load_dotenv()


def _csv_list(raw: str, lower: bool = False) -> list[str]:
    """Parse a comma-separated env value into a clean list (empties dropped)."""
    items = [part.strip() for part in (raw or "").split(",") if part.strip()]
    return [item.lower() for item in items] if lower else items


def _csv_exts(raw: str) -> list[str]:
    """Parse comma-separated extensions, normalising to lower-case with a leading dot."""
    out: list[str] = []
    for part in _csv_list(raw):
        ext = part.lower()
        if not ext.startswith("."):
            ext = "." + ext
        if ext not in out:
            out.append(ext)
    return out


def _csv_int_map(raw: str) -> dict[str, int]:
    """Parse a comma-separated "KEY:int" env value into a dict (bad pairs skipped)."""
    out: dict[str, int] = {}
    for pair in (raw or "").split(","):
        pair = pair.strip()
        if not pair or ":" not in pair:
            continue
        key, _, value = pair.partition(":")
        try:
            out[key.strip().upper()] = int(value.strip())
        except ValueError:
            continue
    return out


class Settings:

    APP_ENV = os.getenv("APP_ENV", "development")

    DB_HOST = os.getenv("DB_HOST")
    DB_PORT = os.getenv("DB_PORT")
    DB_NAME = os.getenv("DB_NAME")
    DB_USER = os.getenv("DB_USER")
    DB_PASSWORD = os.getenv("DB_PASSWORD")

    # ── Microsoft Graph (OAuth2 client_credentials) ───────────────────────────
    # Accept both GRAPH_* and the lowercase tenant_id/client_id/client_secret
    # names so existing .env files (lowercase) are picked up without renaming.
    GRAPH_TENANT_ID     = os.getenv("GRAPH_TENANT_ID")     or os.getenv("tenant_id", "")
    GRAPH_CLIENT_ID     = os.getenv("GRAPH_CLIENT_ID")     or os.getenv("client_id", "")
    GRAPH_CLIENT_SECRET = os.getenv("GRAPH_CLIENT_SECRET") or os.getenv("client_secret", "")
    GRAPH_SERVICE_UPN   = os.getenv("GRAPH_SERVICE_UPN", "care.taker@amperatech.ai")

    # ── Permanent sender identity (Pass 2 — Phase 1) ──────────────────────────
    # The ONE canonical "from" identity for every outbound Caretaker action
    # (Outlook drafts, Teams messages, reminders, calendar actions, and all
    # future Graph writes). It is a fixed system constant — NEVER inferred from
    # transcript participants, the meeting organizer, the logged-in user, a Graph
    # profile, recipient resolution, or any LLM output. Recipients stay dynamic;
    # the sender never changes. Execution validates against this value before any
    # send. Intentionally a hard constant (not env-derived) to make it permanent.
    SENDER_IDENTITY     = "care.taker@amperatech.ai"

    # ── Transcript provider ───────────────────────────────────────────────────
    # "mock" (default) uses MockTranscriptProvider; "graph" will use
    # GraphTranscriptProvider once implemented in a future plan.
    TRANSCRIPT_PROVIDER = os.getenv("TRANSCRIPT_PROVIDER", "mock")

    # ── Vault (AES-256-GCM) ───────────────────────────────────────────────────
    # 64 hex chars = 32 bytes = 256-bit key. Generate with:
    #   python -c "import secrets; print(secrets.token_hex(32))"
    # Store in .env as VAULT_MASTER_KEY=<hex>
    VAULT_MASTER_KEY         = os.getenv("VAULT_MASTER_KEY", "")
    VAULT_KEY_VERSION        = int(os.getenv("VAULT_KEY_VERSION", "1"))

    # ── Secure credential resolution (Follow-up Center plan — Phase 4) ────────
    # Confidence at/above which resolve_secure_reference() auto-resolves a
    # credential request to a single latest-active version with no clarification.
    # Below this (or a near-tie / zero matches) the draft becomes a clarification
    # card instead. The LLM never sees the value either way.
    SECURE_RESOLUTION_THRESHOLD = float(os.getenv("SECURE_RESOLUTION_THRESHOLD", "0.8"))

    # ── Reminder auto-scheduling (Follow-up Center plan — Phase 6) ────────────
    # Default lead time (minutes before a deadline) used to derive a reminder's
    # remind_at from its due_date when a deadline-bearing reminder commitment is
    # created. Snapshotted onto the Commitment; the daemon never recomputes it.
    REMINDER_DEFAULT_OFFSET_MINUTES = int(os.getenv("REMINDER_DEFAULT_OFFSET_MINUTES", "60"))

    # ── Retention (days) ─────────────────────────────────────────────────────
    RETENTION_RAW_DAYS         = int(os.getenv("RETENTION_RAW_DAYS", "90"))
    RETENTION_TRANSCRIPT_DAYS  = int(os.getenv("RETENTION_TRANSCRIPT_DAYS", "30"))
    RETENTION_PRESENCE_DAYS    = int(os.getenv("RETENTION_PRESENCE_DAYS", "7"))
    RETENTION_KNOWLEDGE_DAYS   = int(os.getenv("RETENTION_KNOWLEDGE_DAYS", "365"))

    # ── Content Retrieval layer (Document Retrieval plan — Phase 1) ───────────
    # A generic, multi-source content catalog + ranking layer. Meeting Prep is the
    # first consumer (snapshot["related_content"]); Search/Chat/Copilot reuse the
    # same retrieval API later with no API change. LOCAL folder is the first
    # provider; cloud sources (SharePoint/Teams) plug in behind the same catalog.

    # Where content lives. Comma-separated → multiple roots. Relative paths are
    # resolved against the caretaker/ working directory at scan time.
    MEETING_DOCS_ROOT           = os.getenv("MEETING_DOCS_ROOT", "MeetingDocs")
    MEETING_DOCS_ROOTS          = _csv_list(os.getenv("MEETING_DOCS_ROOT", "MeetingDocs"))

    # Extracted plain text is cached on disk (never stored in Postgres — design
    # rule #1); the DB keeps only metadata/keywords/entities/embedding/hashes.
    CONTENT_TEXT_CACHE_DIR      = os.getenv("CONTENT_TEXT_CACHE_DIR", ".cache/content_index")

    # Formats we can extract plain text from with libraries already vendored
    # (python-docx, PyMuPDF, python-pptx, openpyxl, markdown, plain text).
    CONTENT_SUPPORTED_EXTS      = _csv_exts(os.getenv("CONTENT_SUPPORTED_EXTS", ".pdf,.docx,.pptx,.xlsx,.txt,.md"))
    CONTENT_MAX_FILE_MB         = float(os.getenv("CONTENT_MAX_FILE_MB", "25"))

    # Deterministic entity extractors are code-registered; config only TOGGLES
    # them off by registry name (comma-separated).
    CONTENT_ENTITY_EXTRACTORS_DISABLED = _csv_list(os.getenv("CONTENT_ENTITY_EXTRACTORS_DISABLED", ""), lower=True)

    # ── Retrieval ranking (consumed in Phase 2; centralised here) ─────────────
    CONTENT_CANDIDATE_MULTIPLIER = int(os.getenv("CONTENT_CANDIDATE_MULTIPLIER", "10"))
    CONTENT_CANDIDATE_MIN        = int(os.getenv("CONTENT_CANDIDATE_MIN", "50"))
    CONTENT_TOP_N                = int(os.getenv("CONTENT_TOP_N", "5"))
    CONTENT_MIN_SCORE            = float(os.getenv("CONTENT_MIN_SCORE", "0.1"))
    # Relative gate: drop results scoring below this fraction of the top result's
    # score, so a few strong matches are shown but the weak long tail is dropped.
    CONTENT_REL_SCORE_RATIO      = float(os.getenv("CONTENT_REL_SCORE_RATIO", "0.5"))

    # ── Strict relevance (a document is shown only on a REAL match) ───────────
    # Semantic cosine below this gives NO score credit (kills "everything is
    # vaguely similar" filler). Semantic ALONE qualifies a doc for display only
    # at/above the higher match floor — otherwise a lexical match (fuzzy keyword/
    # filename or entity) is required. Fuzzy ratio catches typos (appolo→apollo).
    # Generic query terms appearing in more than GENERIC_DF_RATIO of documents are
    # treated as non-distinctive (zero weight), so words like "using" don't match.
    CONTENT_SEMANTIC_FLOOR       = float(os.getenv("CONTENT_SEMANTIC_FLOOR", "0.50"))
    CONTENT_SEMANTIC_MATCH_FLOOR = float(os.getenv("CONTENT_SEMANTIC_MATCH_FLOOR", "0.62"))
    CONTENT_FUZZY_RATIO          = float(os.getenv("CONTENT_FUZZY_RATIO", "0.82"))
    CONTENT_GENERIC_DF_RATIO     = float(os.getenv("CONTENT_GENERIC_DF_RATIO", "0.6"))

    # Per-signal weights (weighted sum after each signal is normalised to 0–1).
    CONTENT_W_SEMANTIC          = float(os.getenv("CONTENT_W_SEMANTIC", "0.40"))
    CONTENT_W_KEYWORD           = float(os.getenv("CONTENT_W_KEYWORD", "0.20"))
    CONTENT_W_ENTITY            = float(os.getenv("CONTENT_W_ENTITY", "0.15"))
    CONTENT_W_FILENAME          = float(os.getenv("CONTENT_W_FILENAME", "0.10"))
    CONTENT_W_FOLDER            = float(os.getenv("CONTENT_W_FOLDER", "0.05"))
    CONTENT_W_RECENCY           = float(os.getenv("CONTENT_W_RECENCY", "0.05"))
    CONTENT_W_SOURCE            = float(os.getenv("CONTENT_W_SOURCE", "0.05"))

    # Source-priority scoring signal: higher = preferred when otherwise equal.
    CONTENT_SOURCE_PRIORITY     = _csv_int_map(os.getenv("CONTENT_SOURCE_PRIORITY", "SHAREPOINT:3,TEAMS:2,LOCAL:1"))

    # Diversification (avoid one folder/source dominating the top-N).
    CONTENT_DIVERSIFIER         = os.getenv("CONTENT_DIVERSIFIER", "folder_cluster")
    CONTENT_FOLDER_BOOST        = float(os.getenv("CONTENT_FOLDER_BOOST", "0.05"))

    # In-process retrieval cache (keyed by query_hash + catalog generation).
    CONTENT_CACHE_TTL_SECONDS         = int(os.getenv("CONTENT_CACHE_TTL_SECONDS", "300"))
    CONTENT_GENERATION_REFRESH_SECONDS = int(os.getenv("CONTENT_GENERATION_REFRESH_SECONDS", "5"))
    CONTENT_SNIPPET_CHARS             = int(os.getenv("CONTENT_SNIPPET_CHARS", "320"))
    # Max characters of document text summarised: the head is masked (PII/creds
    # redacted) then sent to the LLM, bounding prompt size for large documents.
    CONTENT_SUMMARY_MAX_CHARS         = int(os.getenv("CONTENT_SUMMARY_MAX_CHARS", "12000"))

    # Known-entity vocabularies the deterministic extractors match against.
    CONTENT_KNOWN_PROJECTS      = _csv_list(os.getenv("CONTENT_KNOWN_PROJECTS", ""))
    CONTENT_KNOWN_CUSTOMERS     = _csv_list(os.getenv("CONTENT_KNOWN_CUSTOMERS", ""))
    CONTENT_KNOWN_TECHNOLOGIES  = _csv_list(os.getenv("CONTENT_KNOWN_TECHNOLOGIES", ""))

    # ── Indexer worker / daemon ───────────────────────────────────────────────
    CONTENT_WORKER_COUNT         = int(os.getenv("CONTENT_WORKER_COUNT", "1"))
    CONTENT_WORKER_POLL_SECONDS  = float(os.getenv("CONTENT_WORKER_POLL_SECONDS", "5"))
    CONTENT_MAX_INDEX_RETRIES    = int(os.getenv("CONTENT_MAX_INDEX_RETRIES", "5"))
    CONTENT_RETRY_BACKOFF_SECONDS = int(os.getenv("CONTENT_RETRY_BACKOFF_SECONDS", "30"))
    CONTENT_WATCH_DEBOUNCE_SECONDS = float(os.getenv("CONTENT_WATCH_DEBOUNCE_SECONDS", "2"))
    CONTENT_FALLBACK_SCAN_SECONDS = int(os.getenv("CONTENT_FALLBACK_SCAN_SECONDS", "300"))


settings = Settings()