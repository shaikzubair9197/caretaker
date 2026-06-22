import os

from dotenv import load_dotenv

load_dotenv()


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

    # ── Retention (days) ─────────────────────────────────────────────────────
    RETENTION_RAW_DAYS         = int(os.getenv("RETENTION_RAW_DAYS", "90"))
    RETENTION_TRANSCRIPT_DAYS  = int(os.getenv("RETENTION_TRANSCRIPT_DAYS", "30"))
    RETENTION_PRESENCE_DAYS    = int(os.getenv("RETENTION_PRESENCE_DAYS", "7"))
    RETENTION_KNOWLEDGE_DAYS   = int(os.getenv("RETENTION_KNOWLEDGE_DAYS", "365"))


settings = Settings()