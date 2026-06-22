"""
Azure OpenAI backend regression tests — mirrors tests/test_llm_service.py's
8 Ollama scenarios, adapted for Azure's response shape and status codes,
plus two dispatch-proof tests for the LLM_PROVIDER switch itself.

Each test patches httpx at the _call_azure_openai level so no real Azure
OpenAI deployment is needed, and patches the audit log so tests run without
a DB. LLM_PROVIDER, AZURE_OPENAI_API_KEY, AZURE_DEPLOYMENT,
OPENAI_API_VERSION, and OPENAI_ENDPOINT are read once at module-import time
in services.llm_service, so each test patches the **module attributes**
directly (not the environment) — only ever with dummy, non-secret test
values, never anything from the real .env.

Run with:
    cd caretaker
    conda activate caretaker
    python -m pytest tests/test_llm_service_azure_openai.py -v
"""

import json
from unittest.mock import MagicMock, patch

import httpx
import pytest

_AUDIT_PATH      = "services.llm_audit_service.LLMAuditService.log"
_HTTPX_POST      = "httpx.post"
_PROVIDER_PATCH  = "services.llm_service.LLM_PROVIDER"
_AZURE_PATCHES = {
    "services.llm_service.AZURE_OPENAI_API_KEY": "test-dummy-key",
    "services.llm_service.AZURE_DEPLOYMENT":     "test-deployment",
    "services.llm_service.OPENAI_API_VERSION":   "2024-08-01-preview",
    "services.llm_service.OPENAI_ENDPOINT":      "https://test-resource.openai.azure.com",
}


def _azure_active():
    """Context manager stack patching LLM_PROVIDER + all Azure config to
    dummy test values. Used by every test in this file."""
    patches = [patch(_PROVIDER_PATCH, "azure_openai")]
    for target, value in _AZURE_PATCHES.items():
        patches.append(patch(target, value))
    return patches


def _enter_all(patches):
    return [p.__enter__() for p in patches], patches


def _exit_all(patches):
    for p in reversed(patches):
        p.__exit__(None, None, None)


def _make_azure_response(content_dict: dict, status_code: int = 200) -> MagicMock:
    """Build a fake httpx.Response shaped like Azure OpenAI's chat completions API."""
    mock = MagicMock()
    mock.status_code = status_code
    mock.json.return_value = {
        "choices": [{"message": {"content": json.dumps(content_dict)}}]
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
# Test 1 — Azure unreachable → CONNECTION_REFUSED
# ---------------------------------------------------------------------------

def test_azure_connect_refused():
    patches = _azure_active()
    _enter_all(patches)
    try:
        with patch(_HTTPX_POST, side_effect=httpx.ConnectError("Connection refused")), \
             patch(_AUDIT_PATH):
            from services.llm_service import LLMService, LLMStatus
            result = LLMService.extract_panic_items("schedule meeting with Alice tomorrow")
        assert not result.succeeded
        assert result.status == LLMStatus.CONNECTION_REFUSED
        assert result.data is None
    finally:
        _exit_all(patches)


# ---------------------------------------------------------------------------
# Test 2 — Bad hostname / DNS failure → DNS_FAILURE
# ---------------------------------------------------------------------------

def test_azure_dns_failure():
    patches = _azure_active()
    _enter_all(patches)
    try:
        dns_msg = "Name or service not known — getaddrinfo failed"
        with patch(_HTTPX_POST, side_effect=httpx.ConnectError(dns_msg)), \
             patch(_AUDIT_PATH):
            from services.llm_service import LLMService, LLMStatus
            result = LLMService.extract_panic_items("test text")
        assert not result.succeeded
        assert result.status == LLMStatus.DNS_FAILURE
        assert result.data is None
    finally:
        _exit_all(patches)


# ---------------------------------------------------------------------------
# Test 3 — Bad API key → AUTH_ERROR
# ---------------------------------------------------------------------------

def test_azure_auth_error():
    patches = _azure_active()
    _enter_all(patches)
    try:
        mock_resp = _make_azure_response({}, status_code=401)
        with patch(_HTTPX_POST, return_value=mock_resp), \
             patch(_AUDIT_PATH):
            from services.llm_service import LLMService, LLMStatus
            result = LLMService.extract_panic_items("test text")
        assert not result.succeeded
        assert result.status == LLMStatus.AUTH_ERROR
        assert result.data is None
    finally:
        _exit_all(patches)


# ---------------------------------------------------------------------------
# Test 4 — Rate limited → RATE_LIMITED
# ---------------------------------------------------------------------------

def test_azure_rate_limited():
    patches = _azure_active()
    _enter_all(patches)
    try:
        mock_resp = _make_azure_response({}, status_code=429)
        with patch(_HTTPX_POST, return_value=mock_resp), \
             patch(_AUDIT_PATH):
            from services.llm_service import LLMService, LLMStatus
            result = LLMService.extract_panic_items("test text")
        assert not result.succeeded
        assert result.status == LLMStatus.RATE_LIMITED
        assert result.data is None
    finally:
        _exit_all(patches)


# ---------------------------------------------------------------------------
# Test 5 — Deployment not found → MODEL_NOT_FOUND
# ---------------------------------------------------------------------------

def test_azure_model_not_found():
    patches = _azure_active()
    _enter_all(patches)
    try:
        mock_resp = _make_azure_response({}, status_code=404)
        with patch(_HTTPX_POST, return_value=mock_resp), \
             patch(_AUDIT_PATH):
            from services.llm_service import LLMService, LLMStatus
            result = LLMService.extract_panic_items("test text")
        assert not result.succeeded
        assert result.status == LLMStatus.MODEL_NOT_FOUND
        assert result.data is None
    finally:
        _exit_all(patches)


# ---------------------------------------------------------------------------
# Test 6 — Timeout → TIMEOUT
# ---------------------------------------------------------------------------

def test_azure_timeout():
    patches = _azure_active()
    _enter_all(patches)
    try:
        with patch(_HTTPX_POST, side_effect=httpx.TimeoutException("timed out")), \
             patch(_AUDIT_PATH):
            from services.llm_service import LLMService, LLMStatus
            result = LLMService.extract_panic_items("test text")
        assert not result.succeeded
        assert result.status == LLMStatus.TIMEOUT
        assert result.data is None
        assert result.exception_type == "httpx.TimeoutException"
    finally:
        _exit_all(patches)


# ---------------------------------------------------------------------------
# Test 7 — Invalid JSON in response → JSON_PARSE_ERROR
# ---------------------------------------------------------------------------

def test_azure_json_parse_error():
    patches = _azure_active()
    _enter_all(patches)
    try:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {
            "choices": [{"message": {"content": "This is not JSON at all { broken"}}]
        }
        with patch(_HTTPX_POST, return_value=mock_resp), \
             patch(_AUDIT_PATH):
            from services.llm_service import LLMService, LLMStatus
            result = LLMService.extract_panic_items("test text")
        assert not result.succeeded
        assert result.status == LLMStatus.JSON_PARSE_ERROR
        assert result.data is None
    finally:
        _exit_all(patches)


# ---------------------------------------------------------------------------
# Test 8 — Valid JSON but missing required keys → SCHEMA_VALIDATION_FAILURE
# ---------------------------------------------------------------------------

def test_azure_schema_validation_failure():
    patches = _azure_active()
    _enter_all(patches)
    try:
        mock_resp = _make_azure_response({"summary": "extracted something", "count": 3})
        with patch(_HTTPX_POST, return_value=mock_resp), \
             patch(_AUDIT_PATH):
            from services.llm_service import LLMService, LLMStatus
            result = LLMService.extract_panic_items("buy milk, fix the bug, call mom")
        assert not result.succeeded
        assert result.status == LLMStatus.SCHEMA_VALIDATION_FAILURE
        assert result.data is None
        assert result.fallback_reason is not None
    finally:
        _exit_all(patches)


# ---------------------------------------------------------------------------
# Test 9 — Happy path → SUCCESS
# ---------------------------------------------------------------------------

def test_azure_success():
    patches = _azure_active()
    _enter_all(patches)
    try:
        payload = {
            "items": [
                {
                    "action":          "Buy milk",
                    "person":          None,
                    "commitment_type": "task",
                    "priority":        "low",
                    "due_hint":        None,
                },
            ],
            "overload_detected": False,
        }
        mock_resp = _make_azure_response(payload)
        with patch(_HTTPX_POST, return_value=mock_resp), \
             patch(_AUDIT_PATH) as mock_audit:
            from services.llm_service import LLMService, LLMStatus
            result = LLMService.extract_panic_items("Buy milk", entity_types=[])

        assert result.succeeded
        assert result.status == LLMStatus.SUCCESS
        assert result.data is not None
        assert len(result.data["items"]) == 1
        assert result.duration_ms >= 0

        mock_audit.assert_called_once()
        call_kwargs = mock_audit.call_args.kwargs
        assert call_kwargs["status"] == LLMStatus.SUCCESS
        assert call_kwargs["model"] == "test-deployment"
    finally:
        _exit_all(patches)


# ---------------------------------------------------------------------------
# Dispatch proof — the actual safety claim of this whole feature
# ---------------------------------------------------------------------------

def test_provider_dispatch_azure_openai():
    """LLM_PROVIDER=azure_openai routes through _call_azure_openai, never
    touches the Ollama chain."""
    with patch(_PROVIDER_PATCH, "azure_openai"), \
         patch("services.llm_service._call_azure_openai") as mock_azure, \
         patch("services.llm_service._call_ollama") as mock_ollama:
        from services.llm_service import LLMCallResult, LLMStatus, LLMService
        mock_azure.return_value = LLMCallResult(data={"items": []}, status=LLMStatus.SUCCESS)
        LLMService.extract_panic_items("test text")

    mock_azure.assert_called_once()
    mock_ollama.assert_not_called()


def test_provider_dispatch_ollama_default():
    """LLM_PROVIDER unset/ollama (default) never touches _call_azure_openai —
    proves the existing Ollama path is completely unaffected by this change."""
    mock_resp = _make_ollama_like_success()
    with patch("services.llm_service.LLM_PROVIDER", "ollama"), \
         patch("services.llm_service._call_azure_openai") as mock_azure, \
         patch(_HTTPX_POST, return_value=mock_resp), \
         patch(_AUDIT_PATH):
        from services.llm_service import LLMService
        LLMService.extract_panic_items("test text")

    mock_azure.assert_not_called()


def _make_ollama_like_success() -> MagicMock:
    mock = MagicMock()
    mock.status_code = 200
    mock.json.return_value = {
        "message": {"content": json.dumps({"items": [], "overload_detected": False})}
    }
    mock.raise_for_status = MagicMock()
    return mock
