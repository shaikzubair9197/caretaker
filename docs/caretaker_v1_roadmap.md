# Caretaker V1 Roadmap

## Product Thesis

Caretaker V1 should help users enter meetings prepared because the relevant context is already precomputed, stored, and ready to retrieve.

The system should prioritize:

- reliability over cleverness
- precision over coverage
- explanations over opaque ranking
- no result over a wrong result

Meeting-time work must be limited to retrieval, ranking, and presentation. Any heavy parsing, classification, or LLM usage belongs to background ingest pipelines.

## What V1 Is

Caretaker V1 is a precomputed meeting-prep system.

It should answer:

- What meeting is next?
- Who is attending?
- What already-known material is likely relevant?
- Why was each item chosen?

It should not try to summarize the world at the last second.

## What V1 Is Not

Caretaker V1 should not:

- perform document parsing on the join path
- make LLM calls when the user is about to join a meeting
- broad-index the user’s local machine
- guess when evidence is weak
- show a card without a clear provenance reason

## Current Codebase Fit

The current repo already has pieces we can reuse:

- `caretaker/api/graph_sync.py` already performs ingest-time normalization, masking, threat scoring, classification, vaulting, and audit writing.
- `caretaker/services/graph/normalizer.py` already normalizes Outlook email, Teams, calendar, transcripts, and todo payloads.
- `caretaker/database/models.py` already contains `CalendarEvent`, `Email`, `TeamsMessage`, `MeetingTranscript`, `Commitment`, `Memory`, and `SourceItem`.
- `caretaker/services/commitment_service.py` already supports a commitment store.
- `caretaker/services/context_service.py` and `caretaker/services/brain_service.py` already model active-window and task context.

The main correction is architectural:

- `BrainService` currently mixes semantic retrieval into a general context builder.
- That is fine for exploratory UI, but it should not be the meeting-prep decision engine.
- V1 should introduce a dedicated meeting-prep service with deterministic rules and precomputed inputs.

## Foundational Constraints

These are prerequisites for a trustworthy V1 launch:

- raw archives at rest must not leak sensitive content in plaintext
- vault keys must persist across restarts
- meeting preparation must work after sleep and wake
- time zone changes must not break scheduling
- cancelled or declined meetings must not produce prep alerts
- focus blocks, presentation mode, and Do Not Disturb must suppress interruptions when appropriate

If any of these fail, the product loses trust quickly.

## Suggested Architecture

### 1. Ingest-Time Intelligence

Background workers continuously process incoming Graph and local data.

Responsibilities:

- normalize source payloads
- clean HTML and other transport noise
- detect threat signals
- mask and vault sensitive tokens
- extract relationship edges
- write deterministic metadata for later retrieval

This stage may use heuristics, classifiers, and LLMs only if they are not on the meeting-time path.

### 2. Precomputed Knowledge Store

The system should persist structured, queryable facts rather than only raw text.

Recommended stored facts:

- meeting identity and recurrence links
- attendees and organizer
- join URL and meeting provider
- attached documents and links
- related emails
- open commitments involving attendees
- prior occurrence context for recurring meetings
- user opt-in local document references

This store should be queryable without parsing source bodies again.

### 3. Meeting-Prep Service

This should be a dedicated service, separate from generic context and agent orchestration.

Responsibilities:

- load the next meeting(s)
- filter out meetings that should not interrupt the user
- fetch candidate evidence from precomputed relationships
- rank candidates using deterministic rules
- attach a short explanation to every surfaced item
- return an empty result when confidence is too low

### 4. Desktop Trigger Layer

The desktop app should do only lightweight scheduling and presentation work.

Responsibilities:

- wake/sleep aware scheduling
- countdown to upcoming meetings
- display top meeting-prep card
- suppress alerts during presentation mode or Do Not Disturb
- re-evaluate state after wake, time zone changes, or calendar resync

## Retrieval Priority

When preparing for a meeting, evidence should be ranked in this order:

1. invite attachments and links
2. related emails
3. open commitments involving attendees
4. previous occurrences of recurring meetings
5. opt-in local folders

Semantic search should be a secondary signal, not the primary retrieval path.

## Ranking Rules

Use a conservative ranking strategy:

- favor direct relationships over content similarity
- prefer recent and user-authored items
- prefer exact attendee / meeting links over inferred links
- deduplicate aggressively
- cap output to a small number of cards

Suggested output limits:

- top 3 documents
- top 3 important things to know

If the system cannot justify an item, drop it.

## Explanation Rules

Every surfaced item must include a reason tag.

Examples:

- attached to the invite
- from an attendee in the thread
- you edited this yesterday
- open commitment involving this attendee
- discussed in the previous occurrence

The explanation should be machine-generated from stored relationships, not invented by an LLM at meeting time.

## Reliability Rules

The preparation system should follow fail-closed behavior:

- if the calendar sync is stale, show nothing or show a stale-state warning
- if meeting time is ambiguous, prefer not surfacing the card
- if the event was declined or cancelled, suppress the prep flow
- if the event is all-day or a focus block, route it through a separate policy
- if DND or presentation mode is active, suppress interruptions

Trust is more important than recall.

## Data Model Additions

Likely V1 additions:

- `meeting_prep_snapshots`
- `relationship_edges`
- `source_references`
- `meeting_state_flags`
- `user_prep_preferences`

Useful fields:

- meeting id
- recurrence master id
- attendee set hash
- join URL hash
- source item ids
- explanation code
- confidence score
- generated_at
- expires_at
- suppression reason

## Suggested V1 Delivery Plan

### M0 - Reliable Meeting Preparation Trigger

Goal: prove the desktop prep experience is accurate and predictable.

Deliverables:

- calendar sync
- detect upcoming meetings 15 minutes before start
- display meeting title and attendees
- extract and display join links
- surface invite attachments
- basic filtering rules
- no LLM involvement

Exit criterion:

- users consistently receive accurate preparation notifications
- false positives are rare enough that the feature feels trustworthy

### M1 - Context Expansion

Goal: make prep useful through explicit relationships.

Deliverables:

- related email discovery
- previous recurring meeting context
- provenance tracking
- confidence scoring
- deduplication
- ranking layer

Exit criterion:

- cards provide meaningful context without feeling noisy

### M2 - Commitment Awareness

Goal: surface obligations that matter before the meeting starts.

Deliverables:

- background commitment extraction
- attendee-to-commitment matching
- non-reversible attendee match keys
- open commitment surfacing

Exit criterion:

- the user is reminded of relevant promises often enough to trust the product

### M3 - Opt-In Local Knowledge

Goal: allow user-selected folders only.

Deliverables:

- explicit folder selection
- background indexing
- semantic retrieval as a secondary signal
- masking and encryption safeguards
- folder deny-lists

Exit criterion:

- personal documents can help, but the system never becomes broad local indexing

### M4 - Adoption and Trust Features

Goal: make the system easy to keep enabled.

Deliverables:

- mute meeting series
- not relevant feedback
- lead time customization
- presenting mode suppression
- confidence threshold tuning

Exit criterion:

- users keep the feature enabled because it stays useful and quiet

## Implementation Order

The safest order is:

1. harden ingest and storage guarantees
2. add precomputed meeting-prep snapshots
3. build deterministic retrieval and ranking
4. add desktop notification and suppression logic
5. layer in commitments and opt-in local knowledge
6. add feedback and tuning controls

## Metrics That Matter

Track the product with trust-first metrics:

- prep cards shown per week
- cards dismissed as not relevant
- cards opened before meeting start
- fraction of cards with at least one useful item
- false positive rate
- stale calendar / sync failure rate
- suppressions due to DND, focus, or presentation mode

The most important signal is not engagement volume.

It is whether the user stops scrambling before important meetings.

## Recommended CTO-Level Decision

For V1, keep the system narrow.

Do not optimize for broad recall or clever conversation.

Optimize for:

- a small number of high-confidence cards
- deterministic reasons
- precomputed evidence
- no expensive meeting-time work

That is the version users will trust.
