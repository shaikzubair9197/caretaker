"""
Pytest wrapper for the Phase 2A security boundary validation.

Runs the REAL validation suite (no mocks of masking / vault / threat /
classification / LLM logic — only the Ollama transport is intercepted) and
asserts the security guarantees as individual, CI-gating test cases.

Run:
    cd caretaker && conda activate caretaker
    python -m pytest phase2_security_validation/tests/ -v
"""

import sys
from pathlib import Path

import pytest

# Make the harness importable
HARNESS_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HARNESS_DIR))

import run_security_validation as rsv  # noqa: E402


@pytest.fixture(scope="module")
def results():
    """Execute every test once and share the outputs across assertions."""
    rsv.RESULTS.mkdir(exist_ok=True)
    return {
        "leak":        rsv.test_1_llm_leak(),
        "vault":       rsv.test_2_vault_roundtrip(),
        "unmask":      rsv.test_3_unmask_authorization(),
        "injection":   rsv.test_4_injection(),
        "correlation": rsv.test_5_correlation(),
        "threat":      rsv.test_6_threat(),
        "audit":       rsv.test_7_audit(),
        "boundary":    rsv.test_8_boundary_proof(),
    }


def test_no_secret_reaches_llm(results):
    r = results["leak"]
    assert r["passed"], f"Secrets/identities leaked to LLM: {r['leaks']}"


def test_vault_full_recovery(results):
    r = results["vault"]
    assert r["recovery_percentage"] == 100.0, f"Vault recovery {r['recovery_percentage']}%"


def test_unauthorized_unmask_blocked(results):
    r = results["unmask"]
    assert r["passed"], f"Unmask authorization failed: {r.get('steps')}"


def test_injection_contained(results):
    r = results["injection"]
    assert r["passed"], f"Injection not contained: {r['failures']}"


def test_cross_source_correlation_with_masking(results):
    r = results["correlation"]
    assert r["passed"], "Correlation broke or identities leaked"


def test_threat_engine_detection(results):
    r = results["threat"]
    assert r["passed"], f"Threat mismatches: {r['mismatches']}"


def test_audit_critical_events_present(results):
    r = results["audit"]
    assert r["passed"], f"Critical audit event missing: {r.get('matrix')}"


def test_boundary_proof_zero_violations(results):
    r = results["boundary"]
    assert r["passed"], f"LLM-facing secret violations: {r['llm_facing_violations']}"


def test_overall_go(results):
    report = rsv.build_report(list(results.values()))
    assert report["go_no_go"] == "GO", f"NO-GO: {report['critical_findings']}"
