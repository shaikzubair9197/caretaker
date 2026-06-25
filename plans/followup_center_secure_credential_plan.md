# Follow-up Center — Secure Credential Lifecycle, Action Items, Reminders & Workspace UI

> **Scope decisions (confirmed with user):**
> - **Secure store (single source of truth):** one new **credential-type-agnostic** store (`SecureCredential` + `CredentialVersion`) that every source (Teams, Email, manual, future) syncs into; reuses VaultService AES primitives for the ciphertext. Drafts/reveal/send resolve only against this store.
> - **Resolution:** **intelligent + structured** — infer a `SecureReference` from the masked transcript and resolve by metadata + confidence via `resolve_secure_reference()`; ask the user only when genuinely ambiguous.
> - **Action Items vs Commitments:** classified by **assignee relative to the meeting**, not by raw `knowledge_type`.
> - **Reveal posture:** values stay **masked by default**; a per-draft **click-to-reveal** does an authenticated, audited server-side unmask of the latest active value; final send also resolves to latest.
> - **Reminders:** **auto-scheduled** with a default offset, user-adjustable, notify at time, **no per-task approval popup**.
> - **UI:** replace the accordion with a **3-pane workspace `| Drafts | Commitments | Action Items |`** (current transcript only) + a typography/readability pass.
> - **Phasing:** independently-shippable phases; ship in order, stop anywhere.
>
> **Naming is deliberately generic** (SecureCredential / SecureAsset / SecureReference / `resolve_secure_reference()`) so the architecture supports API keys, passwords, certificates, SSH keys, JWTs, OAuth tokens, client secrets, connection strings, database credentials, license keys, and future secure assets **without renaming services later**.

---

## Context

The desired Follow-up Center is an assistant that continuously learns confidential values from Teams/email, turns meeting talk into **Action Items + Commitments + reminders**, and uses the **latest** credential value when drafting/sending — while the **LLM only ever sees masked placeholders**.

Prior audits in this repo established the current state with code references:

- **Masking before the LLM works** — transcript ingest masks its own utterances ([transcript_ingestion_service.py:206-321](../../ai_agentic_project/caretaker/services/transcript_ingestion_service.py)); `/graph/sync` masks Teams/email ([api/graph_sync.py:124-173](../../ai_agentic_project/caretaker/api/graph_sync.py)); a structural secret recognizer exists (`API_KEY`/`CONNECTION_STRING`/`AWS_KEY`, [preprocessing_service.py:42-65](../../ai_agentic_project/caretaker/services/preprocessing_service.py)).
- **Key fact for intelligent resolution:** masking redacts the **value** (`sk-…`) but leaves the **descriptor** ("OpenAI API key") in the masked text — so a credential can be identified from masked text alone, without the LLM ever seeing plaintext.
- **Gaps:** no continuous Teams/email credential sync; no versioned store/rotation (`vault_tokens` is insert-if-absent, no owner/version/active columns, [vault_service.py:208-222](../../ai_agentic_project/caretaker/services/vault_service.py); `credential_reference.extra_data["vault_token"]` is read but never written, [knowledge_retrieval_service.py:206](../../ai_agentic_project/caretaker/services/knowledge_retrieval_service.py)); the popup misclassifies by delivery channel and never receives the semantic category ([ui/followup_center_popup.py:427-450](../../ai_agentic_project/caretaker/ui/followup_center_popup.py), DTO [api/drafts.py:85-115](../../ai_agentic_project/caretaker/api/drafts.py)); no latest-credential resolution ([api/agent.py:279-302](../../ai_agentic_project/caretaker/api/agent.py)); no reminder scheduling/notification.

**Outcome:** implement the workflow **additively** — never rewriting vault/masking internals or weakening "LLM sees masked only."

**Mandatory security principle (must not change):** Teams/Email sync continuously discovers credentials → credentials are **encrypted + versioned** → the **LLM only receives masked placeholders + structured SecureReferences** → draft generation always uses placeholders → placeholders are resolved to the **latest active** value only at click-to-reveal / send, server-side, **audited**, ephemeral → the authenticated user sees the real value; the **LLM never sees plaintext**. No plaintext is ever persisted into drafts, payloads, responses (except the explicit reveal), or logs. New endpoints inherit the global `X-API-Key` gate ([middleware/auth.py]). `vault_service.py` / `preprocessing_service.py` / `threat_engine.py` are **extended, not rewritten**.

**Single source of truth:** every credential — from Teams, Email, manual additions, and future integrations — is synchronized into **one centralized secure store** (Phase 1). Draft generation, reveal, and send-time resolution **never** read credentials from Teams/Email; they always resolve against the secure store.

### Canonical Secure Reference Resolution Pipeline

This is the one workflow used everywhere a confidential value is requested:

```
Meeting Transcript
  → Intent Detection            (is this a credential request?)
  → Descriptor Extraction       (from MASKED text: "production OpenAI credentials")
  → Structured SecureReference  ({credential_type, system_name, context{…}})
  → Context Enrichment          (environment/project/application/region/customer/owner/…)
  → Secure Resolver             (secure_store_service.resolve_secure_reference)
  → Confidence Scoring          ({matches, confidence})
  → Auto Resolve                (confidence ≥ threshold → latest active version)
      └─ else Clarification     (multiple/low confidence → ask user; zero → "not found")
```

The LLM participates only at the masked-text stages; resolution of the actual value is server-side, audited, ephemeral, and always to the **latest active** version.

---

## Phase 1 — Versioned Secure Credential Store (single source of truth)

**Goal:** durable, encrypted, versioned, credential-type-agnostic store with rotation history.

- **Models** (`database/models.py`, additive migration in `caretaker/migrations/`) — the single Secure Asset store:
  - `SecureCredential(id, credential_key UNIQUE, credential_type, system_name, context JSON, owner_token, active_version_id FK, created_at, last_seen_at)` — `credential_type` is an **open taxonomy** (api_key | password | certificate | ssh_key | jwt | oauth_token | client_secret | db_credential | connection_string | license_key | …). `context` is a **flexible dimension map** (environment, project, application, region, customer, business_unit, owner, … extensible) — replaces any single `environment` column so credentials are distinguished by whichever dimensions are present.
  - `CredentialVersion(id, credential_id FK, ciphertext LargeBinary, key_version, version, is_active, valid_from, valid_to, source_type, source_id FK, source_metadata JSON, created_at, last_seen_at)` + partial-unique index `(credential_id) WHERE is_active` (mirror [models.py:465-471](../../ai_agentic_project/caretaker/database/models.py)). `source_metadata` captures provenance — conversation_id, chat_id, team_id, channel_id, sender, source_url, message_id, created/last-seen time — so a user can trace where a credential originated.
- **Service** `services/secure_store_service.py`:
  - `derive_credential_key(credential_type, system_name, context, owner_token)` — deterministic identity, mirroring `derive_knowledge_key` ([knowledge_evolution_service.py:109-152](../../ai_agentic_project/caretaker/services/knowledge_evolution_service.py)); e.g. `f"{system_name}:{context.get('environment')}:{credential_type}"` from whichever dimensions are present.
  - `upsert_credential(value, metadata, db)` — rotation logic mirroring evolution's supersede/duplicate ([knowledge_evolution_service.py:175-216](../../ai_agentic_project/caretaker/services/knowledge_evolution_service.py)): same key+same value → no-op (bump `last_seen_at`); same key+new value → deactivate prior, insert active `version+1`, repoint `active_version_id`; new key → new credential + v1.
  - `resolve_secure_reference(secure_reference, db) -> {matches: [...], confidence: float}` — **generalized** structured resolver (no label match required). Accepts a `SecureReference` (`credential_type`, `system_name`, `context` dimensions, optional `owner`) and scores active candidates by matching dimensions; works uniformly across all credential types and future ones.
  - `get_active(credential_key)` and `decrypt_credential(credential_key, justification, actor, agent_action_id, db)`.
- **Reuse crypto:** add thin public wrappers to `VaultService` over existing privates — `encrypt_value()` over `_encrypt` ([vault_service.py:58-65](../../ai_agentic_project/caretaker/services/vault_service.py)) and a credential-decrypt over `_decrypt_bytes` + the approval/justification gate + `_write_audit` ([vault_service.py:83-93, 233-297](../../ai_agentic_project/caretaker/services/vault_service.py)). No edits to existing `store_tokens`/`decrypt`. New audit events: `CREDENTIAL_STORED`, `CREDENTIAL_ROTATED`, `CREDENTIAL_REVEAL`.
- **Done when:** `tests/test_secure_store_service.py` covers new/duplicate/rotation, asserts one active version per `credential_key`, asserts plaintext appears only in `ciphertext`.

## Phase 2 — Continuous Credential Sync from Teams/Email

**Goal:** periodically pull Teams/email and persist detected credentials into the single store.

- **Hook the existing mask step** in `api/graph_sync.py` (right after `mask_pii_indexed`, [graph_sync.py:124](../../ai_agentic_project/caretaker/api/graph_sync.py)): for each redaction whose type ∈ `_CONFIDENTIAL_ENTITY_TYPES` ([preprocessing_service.py:148-152](../../ai_agentic_project/caretaker/services/preprocessing_service.py)), call `secure_store_service.upsert_credential(...)` with `{credential_type, system_name, context, owner_token, source_type, source_id}` plus rich `source_metadata` (conversation_id / chat_id / team_id / channel_id / sender / source_url / message_id / created/last-seen time) drawn from the in-scope `SourceItem`/normalized Graph item. This is the **only** writer of credentials — Teams/Email feed the single store and are never read again at draft/reveal/send time.
- **Daemon loop** `daemon/meeting_scheduler.py`: add `_poll_credential_sync()` mirroring `_poll_followups`/`_sync_calendar` ([meeting_scheduler.py:124-156](../../ai_agentic_project/caretaker/daemon/meeting_scheduler.py)) — `POST /graph/sync?source=chat` then `source=email` on env interval `CREDENTIAL_SYNC_SECONDS` (default 900–1800s); call from `main()` ([meeting_scheduler.py:166-174](../../ai_agentic_project/caretaker/daemon/meeting_scheduler.py)).
- **Reuse:** `/graph/sync` already supports `source ∈ {email, chat, calendar, transcript}` ([graph_sync.py:390](../../ai_agentic_project/caretaker/api/graph_sync.py)); no connector changes.
- **Done when:** chat fixture sync creates `SecureCredential`/`CredentialVersion`; a changed value yields `version=2` active + `CREDENTIAL_ROTATED`; LLM-bound text stays masked.

## Phase 3 — Assignee-Based Classification (Action Items vs Commitments)

**Goal (Refinements #2, #3, #9):** the UI categorizes by **who the work involves**, computed server-side; raw `knowledge_type` is never exposed.

**Definitions:**
- **Action Item** = work assigned to the **caretaker self** (e.g. "Can you send me the OpenAI API key?", "Please update the dashboard").
- **Commitment** = follow-up work involving an **external recipient or external entity outside the current meeting** — not only an absent individual but also a team, department, vendor, customer, or distribution group (e.g. "Please send Suraj the API key"; "Ask Rahul for approval"; "Share credentials with the DevOps team"; "Coordinate with Finance").

**Data needed (all additive):**
- `self_token` on `MeetingTranscript` (nullable) — computed at ingestion by matching a participant's email to `settings.SENDER_IDENTITY` ([config.py:34](../../ai_agentic_project/caretaker/utils/config.py); reuse the `_identity_token_map` pass in [transcript_ingestion_service.py:106-124](../../ai_agentic_project/caretaker/services/transcript_ingestion_service.py)).
- `counterparty` — the other party a task is directed at, which may be a **person token or a named entity** (team/department/vendor/customer/distribution group). Captured via a small **extraction enhancement**: add an optional `counterparty` field (token or entity name + kind) to the prompt + `MeetingIntelligenceItem` ([meeting_intelligence_model.py:28-79](../../ai_agentic_project/caretaker/services/meeting_intelligence_model.py)) and store it in `extra_data` for `action_item`/`commitment` types (extend `_EXTRA_DATA_SCHEMA`, [knowledge_persistence_service.py:24-35](../../ai_agentic_project/caretaker/services/knowledge_persistence_service.py)). Update prompt examples in [prompts/meeting_intelligence_extract.txt](../../ai_agentic_project/caretaker/prompts/meeting_intelligence_extract.txt) (e.g. "send X to <PERSON_n>" or "share with the DevOps team" → set `counterparty`).

**Classification (compute once in `draft_generation_service` at generation; store `followup_category` on payload). Evaluated in this order so every example classifies correctly:**
1. **External counterparty** — `counterparty` present and is an external entity (a person token ∉ `transcript.participant_tokens`, or any named team/department/vendor/customer/group), or `owner_token` is a non-participant → **commitment**. (Takes precedence so "send Suraj the API key" / "ask Rahul for approval" / "share with the DevOps team" become commitments even though the caretaker performs them.)
2. **Assigned to current user** — `owner_token == self_token` with no external counterparty → **action_item**.
3. **Fallback** → **action_item**.
Serialize `followup_category` (+ keep drafts' channel/status) in `_draft_dto` ([api/drafts.py:85-115](../../ai_agentic_project/caretaker/api/drafts.py)). **Do not** expose `knowledge_type` to the UI.
> Reconciliation note: the user's stated priority lists "assigned to current user" first, but their Commitment examples ("send Suraj…") are also user-performed — so the **external-counterparty check must run first** to reproduce all the examples. Flag for correction if literal ordering is preferred.
- **Done when:** "Can you send me the OpenAI API key?" → action_item; "deploy by Friday" → action_item; "send Suraj the API key" (Suraj absent) → commitment; "share with the DevOps team" → commitment.

## Phase 4 — Intelligent Secure Reference Resolution + Click-to-Reveal

**Goal (Refinements #1, #5–#8, #10, #7-security):** auto-pick the exact credential from masked text via the canonical pipeline; ask only when ambiguous; resolve to latest active value at reveal/send; LLM never sees plaintext.

- **Pipeline (see "Canonical Secure Reference Resolution Pipeline" above):**
  - *Intent:* reuse `knowledge_intent_service`'s `is_secret_request` signal ([knowledge_retrieval_service.py:202-208](../../ai_agentic_project/caretaker/services/knowledge_retrieval_service.py)).
  - *Descriptor Extraction + Structured SecureReference + Context Enrichment:* from the item's **masked** `title`/`detail` (the descriptor "production OpenAI credentials", "database password for Payments" survives masking; the value does not), build a structured `SecureReference` `{credential_type, system_name, context{environment, project, application, region, customer, business_unit, owner, …}}`. Deterministic parser or masked-only LLM call.
  - *Secure Resolver + Confidence Scoring:* `secure_store_service.resolve_secure_reference(secure_reference)` → `{matches, confidence}`; structured-metadata matching means literal names are not required.
- **The LLM writes the natural message; placeholders replace only the value.** The model generates normal email/chat prose and emits an inline reference token only where a confidential value belongs — never the value, never the whole response. Example body: `"Please find the requested OpenAI API key below.\n{{SECURE_REF:1}}"`. A draft may contain **multiple independent** `{{SECURE_REF:n}}` tokens. Update the draft prompt templates (`prompts/draft_email.txt`, `draft_teams_message.txt`, …) to instruct this inline-reference convention.
- **Resolve + decide** (in `draft_generation_service._process_item`, [draft_generation_service.py:237-275](../../ai_agentic_project/caretaker/services/draft_generation_service.py)): store a `secure_refs` map on the payload — each `{{SECURE_REF:n}}` → its resolved `credential_key` (or, when unresolved, the pending `SecureReference`). This placeholder→asset mapping is **abstract and per-reference**, so any future secure-asset type plugs in without resolver changes. **Confidence-based** (threshold env `SECURE_RESOLUTION_THRESHOLD`, default 0.8):
  - **High confidence (≥ threshold, single best match) → auto-resolve, NO clarification.**
  - **Low confidence / multiple equally-valid matches → clarification only.** Reuse `_make_clarification` ([draft_generation_service.py:277-295](../../ai_agentic_project/caretaker/services/draft_generation_service.py)) to ask "Which …?", listing candidates by **masked label** (e.g. OpenAI / Azure / Anthropic). Ask **only** here.
  - **Zero matches → "no matching credential — sync or add one" notice** (not a pick-one prompt).
- **Latest version always wins:** resolution always retrieves the **latest active** `CredentialVersion` at reveal/send, regardless of when the draft was generated — rotated credentials are used automatically with no regeneration; never a historical version.
- **Never to the LLM:** the model receives only masked transcript text + structured `SecureReference`s; plaintext exists only during an authenticated reveal and the final send — never in prompts, payloads, or logs.
- **Resolution path (multi-ref):** extend `_rehydrate` ([api/agent.py:279-302](../../ai_agentic_project/caretaker/api/agent.py)) to find **every** `{{SECURE_REF:n}}` token, map each via the payload's `secure_refs` to its `credential_key`, and resolve each **independently** through `secure_store_service.decrypt_credential(credential_key, …)` (existing `<Sn_TOKEN>` behavior unchanged).
- **Click-to-reveal endpoint** `api/drafts.py`: `POST /drafts/{id}/reveal-credentials` → server resolves **all** references in that draft to their latest active values, returns them **for that reveal only**, writes one `CREDENTIAL_REVEAL` audit per reference; never persisted/logged. (Same resolver backs the confirm-before-send dialog.)
- **Done when:** "send me the production OpenAI API key" resolves by structured metadata (not exact label) at high confidence with no prompt; a draft with OpenAI + Azure + prod-DB references resolves all three independently; "send me the API key" with multiple equally-valid keys triggers the pick-one clarification; zero matches shows the "no matching credential" notice; a post-draft rotation is reflected on reveal/send without regenerating.

## Phase 5 — 3-Pane Workspace UI + Typography

**Goal (Refinements #5, #6):** replace the accordion with a fixed 3-column workspace and modernize readability.

- **Layout** `ui/followup_center_popup.py` `_meeting_section` ([followup_center_popup.py:408-452](../../ai_agentic_project/caretaker/ui/followup_center_popup.py)): rebuild as a `QHBoxLayout` of three always-visible columns — **Drafts** (sendable drafts pending review/approve+reveal+send), **Commitments** (`followup_category == "commitment"`), **Action Items** (`followup_category == "action_item"`) — scoped to the current transcript. Each column has its **own** `QScrollArea` (independent scrolling) and a clickable header. Remove all `_collapsible` usage.
- **Focus view (header click):** clicking a column header expands that column to **full width** (split-screen focus mode) and hides/minimizes the other two; a clear **Back/Restore** control returns to the 3-column view. Implement by toggling sibling visibility (or a `QStackedWidget`: page 0 = grid, page 1 = focused column), reusing the existing `_stack` pattern ([followup_center_popup.py:293-313](../../ai_agentic_project/caretaker/ui/followup_center_popup.py)).
- Clarifications (the "which credential?" ask) surface as actionable cards inside the relevant column (Drafts for credential asks). Per-card **Version/Audit** buttons and execution-status badges stay (reuse [followup_center_popup.py:718-742](../../ai_agentic_project/caretaker/ui/followup_center_popup.py)); the per-section Execution Queue/Knowledge Updates/Audit accordions are dropped from the main view.
- **Typography pass:** raise base font sizes and weights, increase padding/spacing, improve contrast and card styling via `ui/theme.py` and the popup palette constants ([followup_center_popup.py:93-102](../../ai_agentic_project/caretaker/ui/followup_center_popup.py)). Goal: a modern productivity surface, not a debug list.
- **Done when:** opening the Center shows three populated, independently-scrolling columns with no expansion needed; clicking a header opens that column full-width with a working Back/Restore; an action_item appears under Action Items, an external follow-up under Commitments, its sendable draft under Drafts; credential cards show `{{SECURE_REF:n}}` inline in natural prose with a working **Reveal**.

## Phase 6 — Reminder Auto-Scheduling + Notification

**Goal (Refinement #4):** deadlines auto-schedule an adjustable reminder; notify at time; no per-task approval popup.

- **Model** `database/models.py`: extend `Commitment` ([models.py:111-136](../../ai_agentic_project/caretaker/database/models.py)) with nullable `remind_at`, `remind_offset_minutes`, `reminded_at`. Set `remind_at = due_date - REMINDER_DEFAULT_OFFSET_MINUTES` (env, default 60) when a deadline-bearing `Commitment` is created ([api/agent.py:450-470](../../ai_agentic_project/caretaker/api/agent.py); due via `_resolve_due_hint`, [knowledge_persistence_service.py:68-92](../../ai_agentic_project/caretaker/services/knowledge_persistence_service.py)).
- **Adjust + due endpoints** (`api/reminders.py`): `PATCH /reminders/{commitment_id}` (reschedule) and `GET /reminders/due`.
- **Daemon** `daemon/meeting_scheduler.py`: add `_poll_reminders()` (pattern of `_poll_followups`) that fires due, un-fired reminders (`remind_at <= now AND reminded_at IS NULL`) as a desktop notification (reuse `subprocess.Popen` pattern, [meeting_scheduler.py:116-121](../../ai_agentic_project/caretaker/daemon/meeting_scheduler.py)) and stamps `reminded_at` — fire-once, no approval popup.
- **Reuse:** `CommitmentService.create/get_pending` ([commitment_service.py:9-43](../../ai_agentic_project/caretaker/services/commitment_service.py)).
- **Done when:** a near-due commitment auto-gets `remind_at`, fires once at/after that time, stamps `reminded_at`; `PATCH` reschedules.

---

## Refinements → phases (traceability)

| Refinement | Phase |
|---|---|
| Intelligent, structured, confidence-based credential resolution; multi-ref; placeholders replace only values | 4 |
| Generic SecureCredential/SecureReference/`resolve_secure_reference()` naming | 1, 4 (all) |
| Single source of truth (sync into one store; never read Teams/Email at draft time) | 1, 2 |
| Rich context + provenance metadata | 1, 2 |
| Action Items = work assigned to caretaker self | 3 |
| Commitments = follow-ups with external recipients/entities | 3 |
| Auto-scheduled, editable reminders, notify, no popup | 6 |
| 3-pane workspace + focus view + typography | 5 |
| Security: masked→resolve latest→reveal; LLM never sees plaintext | 1, 2, 4 (+ all) |

## Security review checklist (every phase)

- LLM prompts contain masked text + structured `SecureReference`s only — assert in `phase2_security_validation/` (CI gate).
- Plaintext lives only in memory during an audited decrypt; never in `SecureCredential`/`CredentialVersion` columns (except `ciphertext`), draft payloads, or responses other than the explicit reveal/send-preview; never logged.
- Every reveal/decrypt writes `CREDENTIAL_REVEAL`/`UNMASK` with justification + `agent_action_id`.
- New endpoints rely on the existing global `X-API-Key` middleware.
- `vault_service.py` / `preprocessing_service.py` / `threat_engine.py` gain new methods only — no behavioral edits.

## Verification (end-to-end)

1. **Unit/integration:** `python -m pytest tests/test_secure_store_service.py tests/test_meeting_prep.py -v`.
2. **Security gate:** `python -m pytest phase2_security_validation/tests/ -v` (add "no plaintext to LLM / no plaintext persisted" cases).
3. **Live demo:** `uvicorn app:app --reload`; `python scripts/seed_followup_demo.py --reset` → 3-pane Center. Verify classification (Action Items vs Commitments), Reveal on a credential draft, ambiguous-credential clarification, and that a re-sync rotation is sent without regeneration.
4. **Continuous sync + reminders:** run `python -m daemon.meeting_scheduler`; confirm periodic chat/email syncs create/rotate credentials and a near-due commitment fires one reminder.

## Notes

- Plan lives at this plan path; on approval the first execution step copies it into the repo under `caretaker/plans/` (per the request to save in a `plans/` folder), appending — never overwriting — per the repo's plan-saving rule.
- Each phase is independently shippable; recommended order is 1 → 2 → 3 → 4 → 5 → 6 (Phase 5 depends on `followup_category` from Phase 3 and the reveal endpoint from Phase 4).
