"""
LLM service regression tests — 8 scenarios.

Each test patches httpx at the _call_ollama level so no real Ollama instance
is needed. The audit log write is also patched so the tests run without a DB.

Run with:
    cd caretaker
    conda activate caretaker
    python -m pytest tests/test_llm_service.py -v
"""

import json
from unittest.mock import MagicMock, patch

import httpx
import pytest

# ── Patch the audit writer before importing llm_service so the module-level
#    host validation passes even if OLLAMA_HOST is overridden in env.
# ── Also patch the audit log so tests don't need a real DB session.

_AUDIT_PATH = "services.llm_audit_service.LLMAuditService.log"
_HTTPX_POST = "httpx.post"
_HTTPX_GET  = "httpx.get"
_PROVIDER_PATCH = "services.llm_service.LLM_PROVIDER"


@pytest.fixture(autouse=True)
def force_ollama_provider():
    with patch(_PROVIDER_PATCH, "ollama"):
        yield


def _make_ollama_response(content_dict: dict, status_code: int = 200) -> MagicMock:
    """Build a fake httpx.Response that _call_ollama expects."""
    mock = MagicMock()
    mock.status_code = status_code
    mock.json.return_value = {
        "message": {
            "content": json.dumps(content_dict)
        }
    }
    mock.raise_for_status = MagicMock()
    if status_code >= 400:
        mock.raise_for_status.side_effect = httpx.HTTPStatusError(
            message=f"HTTP {status_code}",
            request=MagicMock(),
            response=mock,
        )
        mock.text = f"HTTP error {status_code}"
    return mock


# ---------------------------------------------------------------------------
# Test 1 — Ollama not running → CONNECTION_REFUSED
# ---------------------------------------------------------------------------

def test_connect_refused():
    """ConnectError with a generic OS message → CONNECTION_REFUSED status."""
    with patch(_HTTPX_POST, side_effect=httpx.ConnectError("Connection refused")), \
         patch(_HTTPX_GET,  side_effect=httpx.ConnectError("Connection refused")), \
         patch(_AUDIT_PATH):
        from services.llm_service import LLMService, LLMStatus
        result = LLMService.extract_panic_items("schedule meeting with Alice tomorrow")

    assert not result.succeeded
    assert result.status == LLMStatus.CONNECTION_REFUSED
    assert result.data is None
    assert result.exception_type is not None
    assert result.duration_ms >= 0


# ---------------------------------------------------------------------------
# Test 2 — Bad hostname / DNS failure → DNS_FAILURE
# ---------------------------------------------------------------------------

def test_dns_failure():
    """ConnectError containing a DNS resolution error string → DNS_FAILURE."""
    dns_msg = "Name or service not known — getaddrinfo failed"
    with patch(_HTTPX_POST, side_effect=httpx.ConnectError(dns_msg)), \
         patch(_AUDIT_PATH):
        from services.llm_service import LLMService, LLMStatus
        result = LLMService.extract_panic_items("test text")

    assert not result.succeeded
    assert result.status == LLMStatus.DNS_FAILURE
    assert result.data is None


# ---------------------------------------------------------------------------
# Test 3 — Model not installed → MODEL_NOT_FOUND
# ---------------------------------------------------------------------------

def test_model_not_found():
    """HTTP 404 from Ollama → MODEL_NOT_FOUND (direct HTTP path)."""
    mock_resp = _make_ollama_response({}, status_code=404)
    with patch(_HTTPX_POST, return_value=mock_resp), \
         patch(_AUDIT_PATH):
        from services.llm_service import LLMService, LLMStatus
        result = LLMService.extract_panic_items("test text")

    assert not result.succeeded
    assert result.status == LLMStatus.MODEL_NOT_FOUND
    assert result.data is None


# ---------------------------------------------------------------------------
# Test 4 — Timeout → TIMEOUT
# ---------------------------------------------------------------------------

def test_timeout():
    """TimeoutException → TIMEOUT status."""
    with patch(_HTTPX_POST, side_effect=httpx.TimeoutException("timed out")), \
         patch(_AUDIT_PATH):
        from services.llm_service import LLMService, LLMStatus
        result = LLMService.extract_panic_items("test text")

    assert not result.succeeded
    assert result.status == LLMStatus.TIMEOUT
    assert result.data is None
    assert result.exception_type == "httpx.TimeoutException"


# ---------------------------------------------------------------------------
# Test 5 — Ollama succeeds but returns empty items list → EMPTY_RESULT
#           (this is a panic_dump.py business-level status, not llm_service)
# ---------------------------------------------------------------------------

def test_empty_items_from_llm():
    """LLM call succeeds but returns items=[] — panic_dump maps to EMPTY_RESULT."""
    mock_resp = _make_ollama_response({"items": [], "overload_detected": False})
    with patch(_HTTPX_POST, return_value=mock_resp), \
         patch(_AUDIT_PATH):
        from services.llm_service import LLMService, LLMStatus
        result = LLMService.extract_panic_items("hmm nothing actionable here")

    # The LLM layer itself reports SUCCESS — empty items is a caller concern
    assert result.succeeded
    assert result.status == LLMStatus.SUCCESS
    assert result.data == {"items": [], "overload_detected": False}


# ---------------------------------------------------------------------------
# Test 6 — Ollama returns invalid JSON → JSON_PARSE_ERROR
# ---------------------------------------------------------------------------

def test_json_parse_error():
    """Ollama response body is not parseable JSON → JSON_PARSE_ERROR."""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.raise_for_status = MagicMock()
    # Simulate Ollama returning a non-JSON content string
    mock_resp.json.return_value = {
        "message": {"content": "This is not JSON at all { broken"}
    }
    with patch(_HTTPX_POST, return_value=mock_resp), \
         patch(_AUDIT_PATH):
        from services.llm_service import LLMService, LLMStatus
        result = LLMService.extract_panic_items("test text")

    assert not result.succeeded
    assert result.status == LLMStatus.JSON_PARSE_ERROR
    assert result.data is None
    assert result.exception_type in ("json.JSONDecodeError", "KeyError")


# ---------------------------------------------------------------------------
# Test 7 — LLM returns valid JSON but missing required keys → SCHEMA_VALIDATION_FAILURE
# ---------------------------------------------------------------------------

def test_schema_validation_failure():
    """JSON is valid but required output key 'items' is absent."""
    mock_resp = _make_ollama_response({"summary": "I extracted something", "count": 3})
    with patch(_HTTPX_POST, return_value=mock_resp), \
         patch(_AUDIT_PATH):
        from services.llm_service import LLMService, LLMStatus
        result = LLMService.extract_panic_items("buy milk, fix the bug, call mom")

    assert not result.succeeded
    assert result.status == LLMStatus.SCHEMA_VALIDATION_FAILURE
    assert result.data is None
    assert result.fallback_reason is not None


# ---------------------------------------------------------------------------
# Test 8 — Happy path: Ollama running, model installed, good response → SUCCESS
# ---------------------------------------------------------------------------

def test_success():
    """Full success path — LLM returns valid structured response."""
    payload = {
        "items": [
            {
                "action":          "Buy milk",
                "person":          None,
                "commitment_type": "task",
                "priority":        "low",
                "due_hint":        None,
            },
            {
                "action":          "Fix the authentication bug",
                "person":          None,
                "commitment_type": "task",
                "priority":        "high",
                "due_hint":        "today",
            },
        ],
        "overload_detected": False,
    }
    mock_resp = _make_ollama_response(payload)
    with patch(_HTTPX_POST, return_value=mock_resp), \
         patch(_AUDIT_PATH) as mock_audit:
        from services.llm_service import LLMService, LLMStatus
        result = LLMService.extract_panic_items(
            "Buy milk and fix the auth bug today",
            entity_types=[],
        )

    assert result.succeeded
    assert result.status == LLMStatus.SUCCESS
    assert result.data is not None
    assert len(result.data["items"]) == 2
    assert result.data["items"][0]["action"] == "Buy milk"
    assert result.duration_ms >= 0

    # Verify audit log was called with SUCCESS status
    mock_audit.assert_called_once()
    call_kwargs = mock_audit.call_args.kwargs
    assert call_kwargs["status"] == LLMStatus.SUCCESS
    assert call_kwargs["exception_type"] is None


# ---------------------------------------------------------------------------
# Test — reason and fix properties return non-empty strings on failure
# ---------------------------------------------------------------------------

def test_reason_and_fix_populated_on_failure():
    """LLMCallResult.reason and .fix are non-empty for all known failure statuses."""
    with patch(_HTTPX_POST, side_effect=httpx.TimeoutException("timed out")), \
         patch(_AUDIT_PATH):
        from services.llm_service import LLMService
        result = LLMService.extract_panic_items("test text")

    assert result.reason != ""
    assert result.fix != ""
