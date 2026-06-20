"""
GraphTokenManager — OAuth2 client_credentials token acquisition and caching.

- Acquires app-only access tokens via the Microsoft identity platform.
- Caches the token in-memory; refreshes automatically when within 60s of expiry.
- Credentials read from settings (GRAPH_TENANT_ID / CLIENT_ID / CLIENT_SECRET).
- Token value is never logged.
"""

import time
import threading
from typing import Optional

import httpx

from utils.config import settings
from utils.logger import get_logger

logger = get_logger("services.graph.token_manager")

_SCOPE = "https://graph.microsoft.com/.default"
_TOKEN_ENDPOINT = "https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token"

_lock = threading.Lock()
_cached_token: Optional[str] = None
_token_expires_at: float = 0.0


def get_access_token() -> str:
    """
    Return a valid Graph access token, refreshing if needed.
    Thread-safe. Never logs the token value.
    """
    global _cached_token, _token_expires_at

    with _lock:
        if _cached_token and time.time() < _token_expires_at - 60:
            return _cached_token

        tenant = settings.GRAPH_TENANT_ID
        client_id = settings.GRAPH_CLIENT_ID
        client_secret = settings.GRAPH_CLIENT_SECRET

        if not all([tenant, client_id, client_secret]):
            raise RuntimeError(
                "Graph credentials not configured. Set GRAPH_TENANT_ID, "
                "GRAPH_CLIENT_ID, GRAPH_CLIENT_SECRET in .env"
            )

        url = _TOKEN_ENDPOINT.format(tenant=tenant)
        resp = httpx.post(url, data={
            "grant_type":    "client_credentials",
            "client_id":     client_id,
            "client_secret": client_secret,
            "scope":         _SCOPE,
        }, timeout=30)
        resp.raise_for_status()

        data = resp.json()
        _cached_token = data["access_token"]
        _token_expires_at = time.time() + int(data.get("expires_in", 3600))

        logger.info(
            f"Graph token acquired — expires_in={data.get('expires_in')}s "
            f"tenant={tenant[:8]}…"
        )
        return _cached_token


def graph_get(path: str, params: Optional[dict] = None) -> dict:
    """
    GET https://graph.microsoft.com/v1.0/{path}.
    Handles 429 rate-limit with Retry-After header.
    """
    token = get_access_token()
    url = f"https://graph.microsoft.com/v1.0/{path.lstrip('/')}"
    headers = {"Authorization": f"Bearer {token}", "ConsistencyLevel": "eventual"}

    for attempt in range(3):
        resp = httpx.get(url, headers=headers, params=params or {}, timeout=30)
        if resp.status_code == 429:
            retry_after = int(resp.headers.get("Retry-After", "5"))
            logger.warning(f"Graph 429 rate-limit — sleeping {retry_after}s")
            time.sleep(retry_after)
            continue
        resp.raise_for_status()
        return resp.json()

    raise RuntimeError(f"Graph GET {path} failed after 3 attempts due to rate-limiting.")


def graph_get_stream(path: str, params: Optional[dict] = None) -> httpx.Response:
    """
    GET https://graph.microsoft.com/v1.0/{path} and return the raw httpx.Response.
    Handles 429 rate-limit with Retry-After header.
    """
    token = get_access_token()
    url = f"https://graph.microsoft.com/v1.0/{path.lstrip('/')}"
    headers = {"Authorization": f"Bearer {token}", "ConsistencyLevel": "eventual"}

    for attempt in range(3):
        resp = httpx.get(url, headers=headers, params=params or {}, timeout=60)
        if resp.status_code == 429:
            retry_after = int(resp.headers.get("Retry-After", "5"))
            logger.warning(f"Graph 429 rate-limit — sleeping {retry_after}s")
            time.sleep(retry_after)
            continue
        resp.raise_for_status()
        return resp

    raise RuntimeError(f"Graph GET {path} failed after 3 attempts due to rate-limiting.")


def graph_post(
    path: str,
    json_body: Optional[dict] = None,
    params: Optional[dict] = None,
) -> dict:
    """
    POST https://graph.microsoft.com/v1.0/{path}.

    Mirrors graph_get's auth + 429/Retry-After retry loop verbatim (reuses
    get_access_token()). The token value is never logged.

    Returns the parsed JSON response body, or {} for empty (202/204/no-content)
    responses such as sendMail. Write half of the read/write boundary — the
    sender modules in services/graph/senders/ are the only callers.
    """
    token = get_access_token()
    url = f"https://graph.microsoft.com/v1.0/{path.lstrip('/')}"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    for attempt in range(3):
        resp = httpx.post(url, headers=headers, params=params or {}, json=json_body or {}, timeout=30)
        if resp.status_code == 429:
            retry_after = int(resp.headers.get("Retry-After", "5"))
            logger.warning(f"Graph 429 rate-limit — sleeping {retry_after}s")
            time.sleep(retry_after)
            continue
        resp.raise_for_status()
        if resp.status_code in (202, 204) or not resp.content:
            return {}
        if "application/json" in resp.headers.get("content-type", ""):
            return resp.json()
        return {}

    raise RuntimeError(f"Graph POST {path} failed after 3 attempts due to rate-limiting.")
