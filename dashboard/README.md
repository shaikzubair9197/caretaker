# Caretaker Developer Observability Dashboard

Internal admin + observability console for the Caretaker FastAPI system.
Zero build step — pure vanilla JS ES modules, served directly by FastAPI or any static file server.

---

## Folder Structure

```
dashboard/
├── index.html              # App shell: sidebar, content area, toast container
├── styles.css              # Dark theme (GitHub-style), all component styles
├── app.js                  # Hash-based router + config wiring (type="module")
├── api.js                  # HTTP client — auth header, error handling
├── components/
│   ├── toast.js            # Toast notifications (success/error/warn/info)
│   └── panel.js            # collapsible(), codeBlock(), badge(), fmtTime() utilities
└── pages/
    ├── dashboard.js        # System health cards + recent activity
    ├── panic_dump.js       # Panic dump playground with timeline
    ├── brain.js            # Brain inspector — context, memories, app usage
    ├── agent.js            # Agent actions — approve, dismiss, lifecycle
    ├── tasks.js            # Task explorer — search, filter, create
    ├── commitments.js      # Commitment explorer — mark done/dismissed
    ├── llm_audit.js        # LLM audit log — color-coded, expandable rows
    ├── telemetry.js        # Window sessions + application usage bar chart
    ├── txn_debug.js        # Transaction debug — source item tree view
    └── dev_testing.js      # 8 automated test scenarios
```

---

## Setup

### Option A — Served by FastAPI (recommended)

The dashboard is automatically mounted at `/dashboard/` when FastAPI starts,
as long as the `dashboard/` directory exists next to `app.py`.

```bash
cd caretaker
conda activate caretaker
uvicorn app:app --reload
```

Then open: **http://localhost:8000/dashboard/**

### Option B — Standalone static server

```bash
cd caretaker/dashboard
python -m http.server 3000
```

Then open: **http://localhost:3000/**

> The API base URL and key are configurable in the sidebar. Defaults to
> `http://127.0.0.1:8000` with the key pre-filled.

---

## Backend Endpoints Required

All endpoints are protected by `X-API-Key` header.

| Method | Path                          | Used by               |
|--------|-------------------------------|-----------------------|
| GET    | /                             | Dashboard (health)    |
| GET    | /health/detailed              | Dashboard cards       |
| POST   | /panic_dump/                  | Panic Dump page       |
| GET    | /tasks/                       | Tasks, Dashboard      |
| POST   | /tasks/                       | Tasks (create)        |
| GET    | /memories/                    | Brain Inspector       |
| GET    | /commitments/                 | Commitments page      |
| PATCH  | /commitments/{id}/status      | Commitments page      |
| GET    | /llm/audit                    | LLM Audit, Dashboard  |
| GET    | /source-items/                | Transaction Debug     |
| GET    | /source-items/{id}/children   | Transaction Debug     |
| GET    | /brain/                       | Brain Inspector       |
| GET    | /agent/tick                   | Agent (trigger)       |
| POST   | /agent/idle                   | Agent, Dev Testing    |
| GET    | /agent/actions                | Agent Inspector       |
| GET    | /agent/actions/pending        | Dashboard             |
| POST   | /agent/actions/{id}/approve   | Agent Inspector       |
| POST   | /agent/actions/{id}/dismiss   | Agent Inspector       |
| GET    | /telemetry/                   | Telemetry page        |
| GET    | /telemetry/summary            | Telemetry page        |
| POST   | /telemetry/                   | Dev Testing           |

---

## Pages Overview

### 1. Dashboard
- 8 system health cards: API, DB, Ollama, tasks, memories, commitments, pending actions, LLM calls/24h
- Last panic dump / last telemetry timestamps
- Recent tasks table and recent LLM calls table
- Auto-refreshes every 30 seconds

### 2. Panic Dump Playground
- Textarea for arbitrary input text
- Raw request payload display
- Response summary: item count, LLM status (success/unavailable/empty_result/schema_invalid), sensitivity label, RTT
- 7-step execution timeline: input → preprocessing → LLM → tasks → commitments → memory → DB commit
- Extracted items table with action, person, type, priority, due hint
- Live transaction debug: fetches latest source item and renders children tree

### 3. Brain Inspector
- Summary cards: top task, focus state, memory source
- Context snapshot: focus minutes, pending tasks, top app, distraction level
- Application usage bar chart
- Memory cards: id, type, importance, masked text, embedding status
- Filter toggle: all / recency / semantic

### 4. Agent Actions Inspector
- Lifecycle legend: pending → approved → executed / dismissed
- One-click Trigger Tick + Trigger Idle buttons
- Actions table with approve/dismiss buttons per row
- Expandable row showing payload and execution result
- Last tick result panel with intent summary

### 5. Task Explorer
- Full task table with search, status filter, priority filter, sort by ID
- Inline task creation form
- Shows source_id for traceability

### 6. Commitment Explorer
- Status summary cards (pending/done/drafted/dismissed counts)
- Table with mark-done, reopen, dismiss buttons
- Filter by status

### 7. LLM Audit Viewer
- Color-coded rows: green=success, yellow=fallback/invalid, red=error
- Stats cards: success/fallback/error counts, average duration
- Expandable rows: prompt SHA256, entity types, injection warnings, fallback reason
- Filter by status and call type
- Auto-refreshes every 15 seconds

### 8. Telemetry Viewer
- Summary cards: focus minutes, open/closed sessions, unique apps
- Horizontal bar chart of application usage
- Context summary with full app list
- Window sessions table with duration and open/closed state

### 9. Transaction Debug Panel
- Transaction model diagram at top explaining savepoint architecture
- Source items list (newest first)
- Click any source item to expand its transaction tree:
  - SourceItem created
  - Tasks inserted (with SAVEPOINT badge)
  - Commitments staged
  - Memory upserted (with SAVEPOINT badge)
  - DB COMMIT
- Shows masked_text stored in memory

### 10. Developer Testing Mode
- 8 automated test scenarios, each logs step-by-step output:
  1. **Duplicate Task Test** — verifies savepoint dedup, tasks_skipped
  2. **Duplicate Memory Test** — verifies memory upsert idempotency
  3. **PII Masking Test** — verifies memory contains `<EMAIL_ADDRESS>` not raw values
  4. **Injection Sanitization Test** — verifies injected text doesn't reach task descriptions
  5. **Approval Workflow Test** — full pending → approved → executed, 409 on re-approve
  6. **Idle Event Test** — verifies AgentAction created with correct shape
  7. **Stale Session Cleanup Test** — verifies telemetry POST triggers cleanup
  8. **Full Health Check** — verifies all three subsystems respond

---

## API Configuration

The sidebar "API Config" section lets you change the base URL and API key at runtime.
Values are persisted in `localStorage` so they survive page reloads.

Default values:
- URL: `http://127.0.0.1:8000`
- Key: pre-filled with the dev key from `.env`

---

## Testing Workflow

Recommended sequence for a full Phase 1 validation:

```
1. Open Dashboard → verify all 3 status dots are green (API, DB, Ollama)
2. Panic Dump → paste a text with email address → verify:
   - sensitivity_label = CONFIDENTIAL or INTERNAL
   - memory text shows <EMAIL_ADDRESS>, not raw email
   - timeline shows all 7 steps green
3. Brain Inspector → verify memory_source in top cards
4. Transaction Debug → click latest source item → verify all 5 transaction steps
5. LLM Audit → verify latest entry has status=success or fallback with reason
6. Agent Actions → Trigger Tick → approve the resulting action → verify status=executed
7. Dev Testing → Run All Tests → all 8 should pass (or show expected failures)
```
