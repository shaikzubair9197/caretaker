# Caretaker — Feature File Map (Meeting Prep & Follow-up Center)

> This is a **reference index**, not a reorg. The codebase stays in its **layered**
> structure (`api/`, `services/`, `ui/`, `prompts/`, `dashboard/`, `daemon/`) —
> files are grouped by *type*. This doc groups the same files by *feature* so you
> can find everything for Meeting Prep or the Follow-up Center in one place.
> Tags: **NEW** = added for the feature, **EXTENDED** = existing file with feature
> code added, **REUSED** = used unchanged.

---

## Quick run commands

```bash
cd caretaker
uvicorn app:app --reload                      # API + dashboard (http://localhost:8000/dashboard/)
python -m daemon.meeting_scheduler            # daemon: pops Meeting Prep before meetings + Follow-up Center when drafts appear

# Meeting Prep
python ui/meeting_prep_popup.py               # the desktop prep popup (next meeting)
python scripts/check_meeting_prep.py          # in-process end-to-end prep check

# Follow-up Center
python scripts/seed_followup_demo.py --reset  # ingest scenario + generate fresh drafts + pop the GUI
python ui/followup_center_popup.py            # the desktop Follow-up Center popup
```

---

## 1. Meeting Prep  (precomputed, deterministic, NO LLM at meeting time)

Surfaces "what's my next meeting + relevant context" as a fullscreen desktop popup before a meeting.

### Desktop UI
| File | What it does |
|---|---|
| [ui/meeting_prep_popup.py](../ui/meeting_prep_popup.py) | The fullscreen, frameless, always-on-top **PySide6 popup**. Fetches `/meeting/prep/...` via a `_FetchWorker(QThread)`, renders attendees, related emails, documents, join link. Launched as its own process. |

### Backend
| File | What it does |
|---|---|
| [api/meeting_prep.py](../api/meeting_prep.py) | HTTP routes: `GET /meeting/prep/next`, `/{event_id}`, `/upcoming`, `/recently-completed`, `/attachment/...`, `POST /meeting/prep/sync`. Thin handlers over the service. |
| [services/meeting_prep_service.py](../services/meeting_prep_service.py) | Builds the deterministic prep **snapshot** (`build_snapshot` / `next_snapshot` / `snapshot_for`) — attendees, related emails, documents, commitments. Plaintext by design, no vault, no LLM. (Plan 3 deliberately does NOT reuse this for drafts — it would leak plaintext into an LLM prompt.) |

### Trigger
| File | What it does |
|---|---|
| [daemon/meeting_scheduler.py](../daemon/meeting_scheduler.py) | **Shared daemon.** `_poll_upcoming()` launches the prep popup when a meeting enters the 15-min window; `_poll_recently_completed()` triggers transcript sync after meetings. (Also hosts the Follow-up trigger — see §2.) |

### Helper / one-off
| File | What it does |
|---|---|
| [scripts/check_meeting_prep.py](../scripts/check_meeting_prep.py) | In-process end-to-end prep check (sync → build snapshot → print), no server needed. |

---

## 2. Follow-up Center  (Plan 3 — turns meeting knowledge into reviewable drafts)

After a meeting, AI generates **masked** follow-up drafts (email / Teams / reminder / calendar / suggestion), grouped per meeting, that you Approve → which decrypts and executes.

### Desktop UI (the production review surface)
| File | What it does |
|---|---|
| [ui/followup_center_popup.py](../ui/followup_center_popup.py) | **NEW.** Right-docked, frameless, always-on-top **PySide6 popup** (Copilot-style). Reuses `ui/theme.py` + the `_HttpWorker(QThread)` pattern from Meeting Prep. Renders meeting → status groups → draft cards; actions Approve / Reject / Edit / Regenerate / Approve Selected / Approve All hit the `/agent` + `/drafts` API. |
| [daemon/meeting_scheduler.py](../daemon/meeting_scheduler.py) | **EXTENDED.** `_poll_followups()` polls `/drafts/counts` and `subprocess.Popen`s the popup when pending drafts rise (proactive, reopenable) — same launch pattern as the prep popup. |

### Web debug/admin (secondary)
| File | What it does |
|---|---|
| [dashboard/components/followup_center.js](../dashboard/components/followup_center.js) | **NEW.** Browser slide-out version of the Center — kept only as an **internal debug/admin** view. |
| [dashboard/followup_center.css](../dashboard/followup_center.css) | **NEW.** Styles for the web slide-out. |
| [dashboard/pages/agent.js](../dashboard/pages/agent.js) | **REUSED (unchanged).** The raw AgentAction table — debug/admin. |

### API
| File | What it does |
|---|---|
| [api/drafts.py](../api/drafts.py) | **NEW.** `POST /drafts/generate/{mt_id}`, grouped/filtered `GET /drafts`, `GET /drafts/counts` (badge), `GET /drafts/{id}/version-history`, `GET /drafts/{id}/audit-trail` (timeline), `PATCH /drafts/{id}/payload` (edit), `POST /drafts/{id}/regenerate`. Masked-only, never decrypts. |
| [api/agent.py](../api/agent.py) | **EXTENDED.** Status-gate fix (`status="approved"` committed before decrypt), `_execute_action` draft branches + `_rehydrate()` + stale guard, `ACTION_APPROVED/DISMISSED/EXECUTED/FAILED` audit, `approve-batch` + `dismiss-batch`. |

### Draft generation + LLM
| File | What it does |
|---|---|
| [services/draft_generation_service.py](../services/draft_generation_service.py) | **NEW.** Orchestrator: open `KnowledgeItem`s → classify draft type → Plan 2 `retrieve()` gate → `generate_draft` → `_verify_citations` → shape masked payload → persist pending `AgentAction`. Plus `regenerate()`. Audits `DRAFT_GATE_DECISION/GENERATED/FAILED`. |
| [services/llm_service.py](../services/llm_service.py) | **EXTENDED.** `generate_draft(draft_type, masked_context, retrieval_results)` via `_call_with_fallback` (provider-agnostic: gpt-5-nano/Ollama). |
| [prompts/draft_teams_message.txt](../prompts/draft_teams_message.txt) · [draft_email.txt](../prompts/draft_email.txt) · [draft_reminder.txt](../prompts/draft_reminder.txt) · [draft_calendar_reminder.txt](../prompts/draft_calendar_reminder.txt) · [draft_followup_suggestion.txt](../prompts/draft_followup_suggestion.txt) | **NEW.** One prompt per draft type — JSON-only, citation-enforced, token-preserving (tuned for gpt-5-nano). |

### Execution (Graph write side)
| File | What it does |
|---|---|
| [services/graph/token_manager.py](../services/graph/token_manager.py) | **EXTENDED.** `graph_post()` — the write half (same auth + 429 retry as `graph_get`). |
| [services/graph/senders/teams_sender.py](../services/graph/senders/teams_sender.py) | **NEW.** `resolve_one_on_one_chat` + `send_message` (`POST /chats...`). |
| [services/graph/senders/email_sender.py](../services/graph/senders/email_sender.py) | **NEW.** `send_mail` (`POST /users/{upn}/sendMail`). |
| [services/graph/senders/calendar_writer.py](../services/graph/senders/calendar_writer.py) | **NEW.** `create_event` (`POST /users/{upn}/events`). |
| [services/graph/senders/errors.py](../services/graph/senders/errors.py) | **NEW.** `GraphSendError` + `classify_graph_error` (403→`permission_denied`, 404→`not_found`, 429→`rate_limited`, …). |
| [services/commitment_service.py](../services/commitment_service.py) | **REUSED.** `reminder_draft` executes here (no Graph). |
| [services/vault_service.py](../services/vault_service.py) | **REUSED (unchanged).** `decrypt()` — called only at execution, behind the approved-status gate; writes the `UNMASK` audit. |

### Reads / data
| File | What it does |
|---|---|
| [database/crud.py](../database/crud.py) | **EXTENDED.** `list_all_versions_by_key` (version-history) + `list_audit_for_action` (timeline). |
| [database/models.py](../database/models.py) | **REUSED.** `AgentAction` (drafts), `AuditEvent`, `KnowledgeItem`, `MeetingTranscript`, `VaultToken` — all pre-existing columns (no migration). |
| [services/knowledge_retrieval_service.py](../services/knowledge_retrieval_service.py) | **REUSED.** Plan 2 `retrieve()` — confidence floor + conflict gate that draft generation depends on. |

### Provider migration (dormant — no transcript Graph API access)
| File | What it does |
|---|---|
| [services/graph/connectors/graph_transcript_provider.py](../services/graph/connectors/graph_transcript_provider.py) | **NEW (dormant).** Live Graph transcript provider. Only used if `TRANSCRIPT_PROVIDER=graph` (keep `mock`). |
| [services/graph/connectors/transcript_connector.py](../services/graph/connectors/transcript_connector.py) | **EXTENDED.** One `if provider_name == "graph"` branch (cutover seam). |

### Scenario / demo / tests
| File | What it does |
|---|---|
| [scripts/seed_followup_demo.py](../scripts/seed_followup_demo.py) | **NEW.** Ingests the `API_INTEGRATION` scenario (encrypt/vault), generates drafts, prints DB state, and **launches the popup**. Flags: `--force` (re-extract), `--reset` (dismiss + regenerate fresh pending), `--no-popup`. |
| [services/graph/connectors/mock_transcript_fixtures.py](../services/graph/connectors/mock_transcript_fixtures.py) | **EXTENDED.** Mock transcripts incl. `CLIENT_DELAY` and `API_INTEGRATION` (the care.taker@ / caretaker.user@ scenario). |
| [tests/test_graph_transcript_provider.py](../tests/test_graph_transcript_provider.py) | **NEW.** Parity test: Graph provider produces identical Transcripts to the mock. |

---

## 3. Shared upstream pipeline  (Plan 1 + Plan 2 — both features build on this)

| File | What it does |
|---|---|
| [services/transcript_ingestion_service.py](../services/transcript_ingestion_service.py) | Ingest orchestrator: archive → threat scan → **mask + vault (ENCRYPT)** → persist transcript → LLM extract → `KnowledgeItem`s. |
| [services/graph/transcript_payload_parser.py](../services/graph/transcript_payload_parser.py) · [transcript_models.py](../services/graph/transcript_models.py) | Parse raw Graph/VTT payloads → provider-agnostic `Transcript` model. |
| [services/knowledge_persistence_service.py](../services/knowledge_persistence_service.py) · [knowledge_evolution_service.py](../services/knowledge_evolution_service.py) · [knowledge_intent_service.py](../services/knowledge_intent_service.py) | Plan 1/2: write knowledge, version/supersede, classify queries. |
| [services/threat_engine.py](../services/threat_engine.py) · [preprocessing_service.py](../services/preprocessing_service.py) · [llm_guard.py](../services/llm_guard.py) · [llm_audit_service.py](../services/llm_audit_service.py) | Security/masking/guard/audit infrastructure (reused, unchanged). |

---

## 4. Shared desktop-UI infrastructure  (used by BOTH popups)

| File | What it does |
|---|---|
| [ui/theme.py](../ui/theme.py) | `current_theme()` + `build_stylesheet()` — Fluent/Win11 dark styling used by both popups. |
| [ui/workers.py](../ui/workers.py) | `QThreadPool` + Signal-carrier pattern for background work. |
| [ui/document_controller.py](../ui/document_controller.py) · [document_cache.py](../ui/document_cache.py) · [document_viewer_popup.py](../ui/document_viewer_popup.py) · [viewers/](../ui/viewers/) · [renderers/](../ui/renderers/) | Document fetch/parse/preview stack — used by Meeting Prep for invite attachments. |

---

## Security invariants (both features)

- Everything is **masked** except at execution. Drafts, the Center, and all `/drafts` reads render vault **tokens only**.
- **Decryption happens only** in `api/agent.py::_execute_action`, behind the corrected `status == "approved"` gate, scoped to the specific tokens, with `agent_action_id` flowing into a 1:1 `UNMASK` audit.
- `vault_service` / `llm_guard` / `threat_engine` / `middleware/auth` internals are unchanged.

## Plan documents
- Plan 3 (+ refinement): `~/.claude/plans/the-overall-architecture-is-swirling-wilkes.md`
- Plan 1 / Plan 2: `~/.claude/plans/we-do-like-plan-squishy-finch.md`, `~/.claude/plans/plan-2-knowledge-retrieval-versioning.md`
