# Graph Ingestion Pipeline Validation — Teams + Outlook (REAL Microsoft Graph data)

Production-style validation of Caretaker's **Microsoft Teams *and* Outlook**
ingestion boundary using **actual messages fetched live from Microsoft Graph**
(burner account `care.taker@amperatech.ai`) — no synthetic data.

Coverage on the last run: **10 real Teams messages + 5 real Outlook emails = 15
items, all PASS**. The real corpus included a Teams **prompt injection**, a real
**API key**, and — from the mailbox — a **BEC ("this is the CEO")** and a
**phishing** email (classified RESTRICTED).

```
Teams Message → Graph Connector → Normalizer → HTML Cleanup → Preprocessing
 → PII Masking → Threat Engine → Classification → Vault (AES-256-GCM)
 → PostgreSQL persistence → Knowledge-extraction prep → Audit events
```

Persistence runs through the **real** `api/graph_sync._ingest_one` path; the
persisted rows are then read back and validated. Only the Ollama network
transport is intercepted (to inspect the exact LLM-facing payload).

## Run

```bash
cd caretaker && conda activate caretaker
python phase2_teams_pipeline_validation/run_pipeline_validation.py
```

Outputs: `results/<message_id>.json`, `pipeline_validation_report.json`,
`pipeline_summary.json`, plus a console dashboard
(MESSAGE → MASKED? → THREAT → CLASS → VAULT? → DB? → LLM SAFE? → STATUS).

## Result on real data

**10/10 real Teams messages PASS** the defined GO criteria
(zero masking failures, zero LLM-boundary leaks, zero vault round-trip failures,
zero missing audit events, zero DB-persistence failures) → **GO**.

The real dataset happened to include a genuine **prompt injection** ("Ignore all
previous instructions…") and a real **API key** (`sk-live-…`), so the threat and
masking paths were exercised on authentic adversarial content:
- injection → classified `CONFIDENTIAL`, threat `SUSPICIOUS`, no execution/unmask;
- API key → masked to `<S{id}_API_KEY_1>`, classified `RESTRICTED`, vaulted.

## What this validation discovered (the point of the exercise)

Running real Graph data exposed **six real bugs** — the Phase 2A pipeline had in
fact **never executed end-to-end** before this. All six were fixed:

| Sev | Bug | File(s) | Fix |
|---|---|---|---|
| CRITICAL | **Vault token global-namespace collision** — per-document tokens (`PERSON_1`, `API_KEY_1`) repeat across messages but `vault_tokens.token` is globally unique; later messages' entities were dropped and their masked placeholders resolved to an *earlier* message's secret | `api/graph_sync.py` | Re-key tokens per source (`<S{id}_PERSON_1>`) before vaulting |
| HIGH | **Prompt injection classified PUBLIC** | `api/graph_sync.py` | Floor sensitivity to CONFIDENTIAL when `injection_score > 0` |
| HIGH | **Ingestion crashed on dedup** — `.astext` on a generic JSON column raises `AttributeError` | `api/graph_sync.py` | Dialect-safe Python-side `external_id` dedup |
| HIGH | **Normalizer crashed on real chat** — `channelIdentity:null` defeated `.get(k, {})` | `services/graph/normalizer.py` | Coalesce null with `or {}` |
| MEDIUM | **No HTML cleanup** — all Teams bodies are `contentType=html` | `services/preprocessing_service.py`, `api/graph_sync.py` | Added `clean_html()`, applied before masking |
| MEDIUM | **Graph creds not loaded** — `.env` lowercase vs code `GRAPH_*` | `utils/config.py` | Accept lowercase fallbacks |

## Open findings — REQUIRED before Phase 2B

| Sev | Finding | File(s) | Minimal fix |
|---|---|---|---|
| HIGH | **Raw payload stored plaintext at rest** — `source_items.raw_text` holds the full raw Graph payload incl. secrets. Masked columns are clean and the LLM boundary holds, but req #1–3 (no raw secret in PostgreSQL) is violated by the archive column | `api/graph_sync.py`, `database/models.py` | Encrypt `raw_text` (vault/AES) or store a reference; enable PG at-rest encryption |
| HIGH | **No persistent `VAULT_MASTER_KEY`** — each process mints an ephemeral key, so vaulted secrets are unrecoverable after restart (within-run round-trip passes; cross-process fails) | `.env`, `utils/config.py` | Provision a persistent 64-hex key in secret storage; document backup/rotation |
| MEDIUM | **ThreatEngine under-flags injection/BEC** — aggregate stays below FLAGGED (mitigated here by the classification floor + input-guard neutralisation) | `services/threat_engine.py` | Elevate category when injection/credential signals fire |

## Verdict

- **GO** on the five defined GO criteria (masking / LLM-boundary / vault round-trip
  / audit / DB persistence) — verified on real Graph data.
- **Not yet production-ready** until the two HIGH at-rest/key-management findings
  are resolved: raw-payload-at-rest encryption and a persistent vault master key.
  These do not breach the LLM boundary but are required for a Fortune-500 at-rest
  posture and for the unmask flow to survive restarts.
