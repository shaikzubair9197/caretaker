# Phase 2A — Security Boundary Validation

Final pre-production security gate for Caretaker. Proves — against the **real
production implementations** — that the core guarantee holds:

> **The LLM must NEVER see raw secrets, raw identities, credentials, or other
> sensitive enterprise information.**

Nothing in the security-relevant path is mocked. Masking (Presidio), the vault
(AES-256-GCM), the threat engine, classification, and the LLM call pipeline
(`_call_ollama` — input guard, security preamble, payload construction) all run
for real. The **only** interception point is the network transport to Ollama
(`httpx.post`), so the exact bytes destined for the model can be captured and
inspected without requiring a live Ollama.

## Folder structure

```
phase2_security_validation/
├── run_security_validation.py     # 8-test harness + aggregate report
├── dashboard.py                   # Streamlit GO/NO-GO dashboard
├── README.md
├── security_report.json           # generated — verdict + findings
├── fixtures/
│   ├── leak_payloads.json         # all 6 sources, embedded secrets + identities
│   ├── injection_payloads.json    # prompt-injection / jailbreak attacks
│   ├── correlation_payloads.json  # correlated multi-source workflow
│   └── threat_payloads.json       # phishing / BEC / typosquat / cred-harvest / injection / malicious URL
├── results/                       # generated — one JSON per test
│   ├── llm_leak_validation.json
│   ├── vault_roundtrip.json
│   ├── unmask_validation.json
│   ├── injection_validation.json
│   ├── correlation_validation.json
│   ├── threat_validation.json
│   ├── audit_validation.json
│   └── boundary_proof.json
└── tests/
    └── test_security_boundary.py  # pytest wrapper (CI gate)
```

## Run

```bash
cd caretaker
conda activate caretaker

# Full suite + report + verdict (exits 0 on GO, 1 on NO-GO)
python phase2_security_validation/run_security_validation.py

# As a CI gate
python -m pytest phase2_security_validation/tests/ -v

# Visual dashboard
pip install streamlit          # one-time
streamlit run phase2_security_validation/dashboard.py
```

> TEST 3 (unmask authorization) and TEST 7 (audit completeness) exercise the
> **real** `VaultService` + `AgentAction` approval flow against the live
> PostgreSQL database. They create a handful of clearly-tagged
> (`source_type='security_validation'`) rows and delete them again — the suite
> is self-cleaning and leaves zero residue.

## The eight tests

| # | Test | Proves | Output |
|---|---|---|---|
| 1 | LLM data-leak | No secret/raw-email reaches the captured Ollama payload; only `<ENTITY_N>` placeholders do | `llm_leak_validation.json` |
| 2 | Vault round-trip | Every masked entity is AES-256-GCM reversible (100% recovery) | `vault_roundtrip.json` |
| 3 | Unmask authorization | `decrypt` is denied without an approved `AgentAction` (403), allowed with one (200); audited | `unmask_validation.json` |
| 4 | Prompt-injection resistance | Injection elevates threat ≥ SUSPICIOUS, classification ≥ RESTRICTED, no secret in prompt, no unmask/execution | `injection_validation.json` |
| 5 | Cross-source correlation | Business concepts survive masking across email/chat/calendar/transcript while identities stay tokenised | `correlation_validation.json` |
| 6 | Threat engine | Phishing/BEC/typosquat/cred-harvest/injection/malicious-URL meet or exceed expected severity | `threat_validation.json` |
| 7 | Audit completeness | Security-critical events (MASK, UNMASK, ACCESS_DENIED) are durably emitted | `audit_validation.json` |
| 8 | Security boundary proof | Zero credential-VALUE-shaped tokens in any LLM-facing payload | `boundary_proof.json` |

## Verdict logic

- **GO** — all CRITICAL gates pass: no secret reaches the LLM, no unauthorized
  unmask succeeds, and every security-critical audit event is durably written.
- **NO-GO** — any raw secret reaches the LLM, any unauthorized unmask succeeds,
  or a security-critical audit event is missing.

Severity: **CRITICAL** = boundary violated · **HIGH** = unauthorized disclosure
possible · **MEDIUM** = observability gap · **LOW** = operational improvement.

## Findings from this validation run

The suite found, and the run records, the following:

1. **[FIXED] MASK audit events were silently dropped.** `VaultService.store_tokens`
   writes the `MASK` audit via an independent session while the `source_item` is
   only flushed (not committed) by the caller — so the `audit_events.source_id`
   FK failed and the event was lost (confirmed empirically). The audit layer's
   "survives caller rollback" design is incompatible with a hard FK to an
   uncommitted row. **Fixed** in `services/vault_service.py`: `_write_audit` now
   retries with the FK column null and the reference preserved in
   `event_data.source_id_ref`, so an audit event is never silently lost.
2. **[MEDIUM] Idealized audit taxonomy has granularity gaps** — discrete
   `INGEST`, `CLASSIFY`, and `UNMASK_REQUEST` events are not emitted (`graph_sync`
   never writes `INGEST`; approval is recorded on `AgentAction.approved_at`
   rather than a discrete `UNMASK_APPROVED` event). Security-critical events are
   present; this is an observability granularity gap.
3. **[LOW] Azure Storage connection strings** — the `AccountKey` value is masked
   (via the generic `API_KEY` pattern) but the `AccountName` partially survives.
   No credential leaks; recommend a dedicated Azure recognizer for
   defense-in-depth.

All credential **values** across every source type (OpenAI keys, AWS keys,
bearer tokens, DB connection strings, embedded document secrets) are masked
before any LLM call — the boundary holds.
