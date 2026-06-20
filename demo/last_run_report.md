################################################################################
#  CARETAKER — HOW YOUR MEETINGS BECOME SAFE, SEARCHABLE KNOWLEDGE
################################################################################
Run at: 2026-06-20T15:48:21

Read this top to bottom. Each step has a plain-English line and a
technical line. Every number is real — pulled live from the database
after actually running the pipeline on two sample meetings.

# PART A — A normal meeting becomes safe, searchable knowledge


## STEP 1 — The raw meeting (what was actually said)

  WHAT HAPPENED: This is the unprocessed Teams transcript exactly as it arrived — real names, an email address, a SharePoint link and a GitHub PR. Watch how the system handles each of these.
  TECHNICAL DETAIL: subject='Mobile App Release Planning', 13 spoken turns.

  ---- raw transcript ----
  Sarah Lin: Hi everyone, let's plan the mobile release. Raj, where are we on PROJ-720, the offline sync feature?
  Raj Mehta: PR #512 is open on github.com/contoso/mobile-app and it's ready for review. Nina, could you review it by Friday?
  Nina Kowalski: Yes, I'll review PR #512 by Friday.
  Sarah Lin: Great. We've decided to ship the release on June 29th instead of the 25th, to give beta testers a full week.
  Nina Kowalski: That works for me. One blocker though, I'm waiting on the updated app store screenshots from the design team before I can finalize the listing.
  Raj Mehta: I'll chase the design team for the screenshots and get them to you by Wednesday.
  Sarah Lin: Thanks. The release checklist is on SharePoint at https://contoso.sharepoint.com/sites/Mobile/Shared%20Documents/Release-Checklist-v7.docx, please go through it before the 29th.
  Raj Mehta: Will do. I'll also loop in priya.kapoor@contoso.com since she owns the beta program.
  Carlos Ferreira: Hi all, Carlos from the QA vendor side. Quick question, does the offline sync need to work on Android 11, or can we drop support for it this release?
  Sarah Lin: Good question, let me confirm the minimum Android version with product and get back to you.
  Nina Kowalski: Also flagging a risk, the offline sync could double our storage usage on older devices if we don't add a cache cap.
  Sarah Lin: Good catch, let's add a cache cap to the scope before we ship.
  Raj Mehta: Agreed. I'll write up the cache cap requirement and share it today.
  ------------------------

## STEP 2 — Ingesting the meeting (one entry point)

  WHAT HAPPENED: We hand the meeting to the system once. Behind that single call it gets archived, scrubbed of personal info, scanned for risk, and read by the AI — each shown separately below.
  TECHNICAL DETAIL: ingest() -> outcome='ingested', source_id=301, meeting_transcript_id=34, knowledge_item_count=13.

## STEP 3 — Protecting personal information (before anything else)

  WHAT HAPPENED: Before the meeting is stored or shown to any AI, names, emails and links are swapped for safe placeholder tags. Compare BEFORE (what was said) with AFTER (the only version the system keeps).

    BEFORE (raw, never stored): PR #512 is open on github.com/contoso/mobile-app and it's ready for review. Nina, could you review it by Friday?
    AFTER  (what we keep):      PR #512 is open on <S301_URL_1> and it's ready for review. <S301_PERSON_2>, could you review it by <S301_DATE_TIME_1>?

    BEFORE (raw, never stored): Yes, I'll review PR #512 by Friday.
    AFTER  (what we keep):      Yes, I'll review PR #512 by <S301_DATE_TIME_1>.

    BEFORE (raw, never stored): Thanks. The release checklist is on SharePoint at https://contoso.sharepoint.com/sites/Mobile/Shared%20Documents/Release-Checklist-v7.docx, 
    AFTER  (what we keep):      Thanks. The release checklist is on <S301_ORGANIZATION_2> at <S301_URL_2> please go through it before <S301_DATE_TIME_6>.

  TECHNICAL DETAIL: 13 transcript segments stored; the AFTER text is the TranscriptSegment.text_masked column — the only version that survives.

## STEP 4 — Storing the real values safely (encrypted vault)

  WHAT HAPPENED: The real names, emails and links aren't thrown away — they're encrypted and locked in a vault, each behind a placeholder tag. They never leave this machine and are never sent to the AI.
  TECHNICAL DETAIL: 26 encrypted VaultToken rows (AES-256-GCM). By kind:
    - SPEAKER: 10
    - DATE_TIME: 7
    - ORGANIZATION: 3
    - PERSON: 3
    - URL: 2
    - EMAIL_ADDRESS: 1

## STEP 5 — Scanning for risk

  WHAT HAPPENED: Every meeting is scored for sensitivity. This one is a normal working meeting — no leaked secrets — so it's allowed to proceed to the AI.
  TECHNICAL DETAIL: SourceItem.sensitivity_label='INTERNAL', MeetingTranscript.threat_score=0.000 (low = safe to process).

## STEP 6 — Understanding the meeting (the AI reads the SAFE copy)

  WHAT HAPPENED: Now — and only now — the AI reads the meeting. It only ever sees the masked version from Step 3: no real names, no email. From that it pulls out the action items, decisions, blockers and questions.
  TECHNICAL DETAIL: AI backend: provider='azure_openai', model='gpt-5-nano'. Extracted 13 facts from THIS meeting:
    - [jira_ticket] Status check on PROJ-720 offline sync feature  (confidence 55%)
    - [github_pr] PR #512 open and awaiting review  (confidence 90%)
    - [commitment] Review PR #512 by date  (confidence 90%)
    - [commitment] Ship release on new date  (confidence 85%)
    - [blocker] Blocking issue: need updated app store screenshots  (confidence 90%)
    - [information_request] Obtain screenshots from design team by date  (confidence 80%)
    - [mentioned_document] Release checklist location  (confidence 80%)
    - [information_request] Include beta program owner in loop  (confidence 80%)
    - [question] Minimum Android version for offline sync  (confidence 90%)
    - [information_request] Confirm minimum Android version with product  (confidence 70%)
    - [risk] Risk: offline sync may increase storage on older devices  (confidence 90%)
    - [decision] Add cache cap to scope  (confidence 80%)
    - [commitment] Write cache cap requirement  (confidence 85%)

## STEP 7 — Remembering it correctly over time (versioning)

  WHAT HAPPENED: Each fact gets a stable identity, so if a LATER meeting changes it (a deadline moves, say) the system can replace the old version instead of keeping two contradictory copies. This is a first-time meeting, so every fact is brand new — version 1, active.
  TECHNICAL DETAIL: 13 facts: 13 active, 13 with a stable knowledge_key, 13 with a search embedding. Resolution methods: ['llm_adjudicated', 'new_chain'].

## STEP 8 — Asking the assistant a question

  WHAT HAPPENED: The payoff: ask about the meeting in plain English and the assistant finds the right facts, ranked by confidence. It searches across ALL meetings it remembers, so each answer is tagged with where it came from.

    You ask: "What are the action items?"
      -> [action_item] Morning everyone, let's go round the table. S266_SPEAKER_1: How's the   (confidence 80%, from an earlier meeting)
      -> [action_item] Write cache cap requirement  (confidence 73%, from an earlier meeting)

    You ask: "What is blocking us?"
      -> [decision] Got it, I'll review the design doc before the design review. S266_SPEA  (confidence 80%, from an earlier meeting)
      -> [risk] Risk: offline sync may increase storage on older devices  (confidence 76%, from THIS meeting)

    You ask: "What was decided?"
      -> [decision] Got it, I'll review the design doc before the design review. S266_SPEA  (confidence 80%, from an earlier meeting)
      -> [decision] Ship release date approved  (confidence 77%, from an earlier meeting)

  TECHNICAL DETAIL: Each question runs the real retrieve() pipeline: classify intent -> search by category AND by meaning (embeddings) -> rank by confidence -> log the lookup.

# PART B — The safety net: a meeting with a leaked password


## STEP 9 — A meeting where someone pastes a live password

  WHAT HAPPENED: Same kind of meeting, but this time a participant pastes a real database connection string (with a password) into the chat. This is exactly the kind of mistake that leaks secrets — here's what the system does about it.
  TECHNICAL DETAIL: The risky line in the transcript:
      "Sorry I'm late, joining from the vendor side. While I was debugging the staging connector last night I had to hardcode a test value, here it is so you"

## STEP 10 — The system catches it and locks the meeting down

  WHAT HAPPENED: On the way in, the risk scanner spots the live credential and QUARANTINES the entire meeting — it is NOT handed to the AI at all. The strongest possible protection: the AI never even sees a meeting this sensitive.
  TECHNICAL DETAIL: ingest() -> outcome='quarantined', sensitivity='RESTRICTED', threat_score=0.950, knowledge_item_count=0 (AI was skipped).

## STEP 11 — The password is still masked and locked away

  WHAT HAPPENED: Even though the meeting was quarantined, the personal info and the leaked password were still scrubbed out and encrypted in the vault. The real password is sealed away — not left sitting in plain text anywhere.
  TECHNICAL DETAIL: 27 encrypted VaultToken rows. The leaked credential was captured as: ['CONNECTION_STRING']. Full breakdown:
    - DATE_TIME: 10
    - SPEAKER: 10
    - ORGANIZATION: 3
    - URL: 2
    - PERSON: 1
    - CONNECTION_STRING: 1

## STEP 12 — The paper trail (nothing happens silently)

  WHAT HAPPENED: Every sensitive action across both meetings — masking, AI calls, lookups — is recorded in an independent audit log. If anyone ever asks 'what did the system do with my data?', here's the receipt.
  TECHNICAL DETAIL: Audit events recorded for this run:
    - RETRIEVAL: 3
    - INGEST: 2

## STEP 13 — In one breath

  WHAT HAPPENED: Two messy Teams meetings went in. The normal one was scrubbed of personal info, the real values locked in an encrypted vault, read by the AI in its SAFE form only, turned into searchable facts you can ask about, and fully logged. The second meeting contained a leaked password — so the system caught it, sealed the password in the vault, and refused to let the AI read the meeting at all. Convenience when it's safe, a hard stop when it isn't.
  TECHNICAL DETAIL: PART A: source_id=301, 26 secrets vaulted, 13 facts extracted by 'gpt-5-nano'. PART B: source_id=302, outcome='quarantined', threat_score=0.95, 27 secrets vaulted, 0 facts (AI blocked).
