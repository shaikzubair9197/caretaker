"""
Graph sender error taxonomy (Plan 3 refinement H3).

Senders are pure Graph I/O. When a write fails, they raise GraphSendError with a
specific `reason` code so the executor (api/agent.py::_execute_action) can record
a precise ACTION_FAILED outcome — matching llm_service.py's "every failure has a
specific, audited outcome" discipline — instead of an opaque, truncated string.

No secrets are ever placed in a reason or message: only HTTP status, a short
truncation of Graph's own error body, and the original exception text (which is
status/diagnostic text, never our decrypted payload).
"""

from typing import Optional

import httpx


class GraphSendError(Exception):
    """A classified Graph write failure carrying a stable `reason` code."""

    def __init__(self, reason: str, message: str, status_code: Optional[int] = None):
        self.reason = reason
        self.status_code = status_code
        super().__init__(message)


def classify_graph_error(exc: Exception) -> GraphSendError:
    """
    Map a raw exception from the Graph write path to a GraphSendError with a
    specific reason code:
      - 401 → auth_error
      - 403 → permission_denied
      - 404 → not_found        (chat or user/mailbox missing)
      - 429 → rate_limited
      - other 4xx/5xx → http_<status>
      - token_manager retry exhaustion (RuntimeError) → rate_limited
      - connection/transport errors → network_error
      - anything else → unknown_error
    """
    # token_manager's 429 retry loop raises RuntimeError("... rate-limiting.")
    if isinstance(exc, RuntimeError) and "rate-limiting" in str(exc):
        return GraphSendError("rate_limited", str(exc))

    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code if exc.response is not None else None
        mapping = {401: "auth_error", 403: "permission_denied", 404: "not_found", 429: "rate_limited"}
        reason = mapping.get(status, f"http_{status}")
        detail = (exc.response.text[:200] if exc.response is not None else "").strip()
        return GraphSendError(reason, f"Graph {status}: {detail}", status_code=status)

    if isinstance(exc, httpx.RequestError):
        return GraphSendError("network_error", str(exc))

    return GraphSendError("unknown_error", str(exc))
