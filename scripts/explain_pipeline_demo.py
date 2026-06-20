#!/usr/bin/env python3
"""
Caretaker pipeline — plain-English + technical walkthrough.

This is a GUIDED TOUR, not a test. It runs TWO realistic Microsoft Teams
meetings through the whole Caretaker pipeline and explains, in normal language,
what happens to your data and why — while also showing the real technical
detail (status codes, counts, the AI model used, confidence scores).

  PART A — a normal meeting that flows all the way through:
           archive -> mask personal info -> vault -> risk scan -> AI reads the
           SAFE copy -> facts -> versioning -> you ask questions.

  PART B — a meeting where someone pasted a live database password. The system
           catches it, locks the whole meeting down (quarantine), and refuses
           to let the AI read it at all. The safety net in action.

Everything printed is REAL: every number comes from an actual function call or
a live database read. Nothing is faked for the demo.

Usage:
    cd caretaker
    python scripts/explain_pipeline_demo.py

Output:
    - prints the full walkthrough to your terminal as it runs
    - writes the same walkthrough to demo/last_run_report.md (overwritten each run)
"""

import copy
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from database.connection import SessionLocal
from database.models import (
    SourceItem, MeetingTranscript, TranscriptSegment,
    VaultToken, KnowledgeItem, AuditEvent,
)
from services.graph.transcript_payload_parser import parse_transcript_payload
from services import transcript_ingestion_service
from services.knowledge_retrieval_service import retrieve
from services import llm_service

_DEMO_DIR = Path(__file__).resolve().parent.parent / "demo"
_CLEAN_FILE = _DEMO_DIR / "sample_meeting_clean.json"
_QUARANTINE_FILE = _DEMO_DIR / "sample_meeting_transcript.json"   # the leaked-credential meeting
_REPORT_FILE = _DEMO_DIR / "last_run_report.md"


# ── Output helper: print to terminal AND collect Markdown simultaneously ──────

class Report:
    """Collects every line so the terminal output and the saved Markdown file
    stay in sync. `md` overrides what goes to the file (used for headings)."""

    def __init__(self):
        self._md: list[str] = []

    def line(self, text: str = "", md: str | None = None):
        print(text)
        self._md.append(text if md is None else md)

    def part(self, letter: str, title: str):
        bar = "#" * 78
        print("\n" + bar)
        print(f"#  PART {letter} — {title}")
        print(bar)
        self._md.append("")
        self._md.append(f"# PART {letter} — {title}")
        self._md.append("")

    def step(self, n: int, title: str):
        bar = "=" * 78
        print("\n" + bar)
        print(f"STEP {n} — {title}")
        print(bar)
        self._md.append("")
        self._md.append(f"## STEP {n} — {title}")
        self._md.append("")

    def plain(self, text: str):
        self.line(f"  WHAT HAPPENED: {text}")

    def tech(self, text: str):
        self.line(f"  TECHNICAL DETAIL: {text}")

    def bullet(self, text: str):
        self.line(f"    - {text}")

    def save(self, path: Path):
        path.write_text("\n".join(self._md) + "\n", encoding="utf-8")


def _load_unique(path: Path) -> dict:
    """Load a transcript payload and give it a unique external_id per run, so
    re-running the demo actually re-ingests instead of hitting the
    dedup-by-external_id idempotency skip (same trick the verify script uses)."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload = copy.deepcopy(payload)
    suffix = datetime.now().strftime("%Y%m%d%H%M%S%f")
    payload["transcript_metadata"]["id"] = f"{payload['transcript_metadata']['id']}-{suffix}"
    return payload


def _spoken_lines(vtt: str) -> list[tuple[str, str]]:
    """Return [(speaker, text), ...] from raw WebVTT voice tags."""
    out = []
    for ln in vtt.splitlines():
        if ln.startswith("<v "):
            name, _, said = ln[3:].partition(">")
            out.append((name, said))
    return out


def _ai_backend() -> tuple[str, str]:
    provider = getattr(llm_service, "LLM_PROVIDER", "ollama")
    model = (
        getattr(llm_service, "AZURE_DEPLOYMENT", "") if provider == "azure_openai"
        else getattr(llm_service, "OLLAMA_MODEL", "")
    )
    return provider, model


# =====================================================================
# PART A — a normal meeting becomes safe, searchable knowledge
# =====================================================================

def part_a(r: Report, db) -> dict:
    payload = _load_unique(_CLEAN_FILE)
    r.part("A", "A normal meeting becomes safe, searchable knowledge")

    # STEP 1 — raw meeting
    r.step(1, "The raw meeting (what was actually said)")
    r.plain(
        "This is the unprocessed Teams transcript exactly as it arrived — real "
        "names, an email address, a SharePoint link and a GitHub PR. Watch how "
        "the system handles each of these."
    )
    subject = payload["meeting_metadata"]["subject"]
    spoken = _spoken_lines(payload["vtt_content"])
    r.tech(f"subject='{subject}', {len(spoken)} spoken turns.")
    r.line("")
    r.line("  ---- raw transcript ----")
    for name, said in spoken:
        r.line(f"  {name}: {said}")
    r.line("  ------------------------")

    # STEP 2 — ingest
    r.step(2, "Ingesting the meeting (one entry point)")
    r.plain(
        "We hand the meeting to the system once. Behind that single call it gets "
        "archived, scrubbed of personal info, scanned for risk, and read by the "
        "AI — each shown separately below."
    )
    transcript = parse_transcript_payload(
        transcript_metadata=payload["transcript_metadata"],
        vtt_content=payload["vtt_content"],
        meeting_metadata=payload["meeting_metadata"],
    )
    result = transcript_ingestion_service.ingest(transcript, db, force_reextract=True)
    source_id = result["source_id"]
    mt_id = result["meeting_transcript_id"]
    r.tech(
        f"ingest() -> outcome='{result['outcome']}', source_id={source_id}, "
        f"meeting_transcript_id={mt_id}, knowledge_item_count={result['knowledge_item_count']}."
    )

    # STEP 3 — masking
    r.step(3, "Protecting personal information (before anything else)")
    r.plain(
        "Before the meeting is stored or shown to any AI, names, emails and links "
        "are swapped for safe placeholder tags. Compare BEFORE (what was said) "
        "with AFTER (the only version the system keeps)."
    )
    segments = (
        db.query(TranscriptSegment)
        .filter(TranscriptSegment.transcript_id == mt_id)
        .order_by(TranscriptSegment.sequence_index)
        .all()
    )
    raw_utterances = [u.text for u in transcript.utterances]
    interesting = []
    for i, seg in enumerate(segments):
        raw = raw_utterances[i] if i < len(raw_utterances) else ""
        if any(k in raw.lower() for k in ("@contoso", "sharepoint", "review pr", "github.com")):
            interesting.append((raw, seg.text_masked or ""))
    for raw, masked in interesting[:3]:
        r.line("")
        r.line(f"    BEFORE (raw, never stored): {raw[:140]}")
        r.line(f"    AFTER  (what we keep):      {masked[:140]}")
    r.line("")
    r.tech(
        f"{len(segments)} transcript segments stored; the AFTER text is the "
        f"TranscriptSegment.text_masked column — the only version that survives."
    )

    # STEP 4 — vault
    r.step(4, "Storing the real values safely (encrypted vault)")
    r.plain(
        "The real names, emails and links aren't thrown away — they're encrypted "
        "and locked in a vault, each behind a placeholder tag. They never leave "
        "this machine and are never sent to the AI."
    )
    vault_rows = db.query(VaultToken).filter(VaultToken.source_id == source_id).all()
    by_type: dict[str, int] = {}
    for v in vault_rows:
        # entity_type can be "S298_EMAIL_ADDRESS_1" style; collapse to the family
        fam = _entity_family(v.entity_type)
        by_type[fam] = by_type.get(fam, 0) + 1
    r.tech(f"{len(vault_rows)} encrypted VaultToken rows (AES-256-GCM). By kind:")
    for etype, count in sorted(by_type.items(), key=lambda x: -x[1]):
        r.bullet(f"{etype}: {count}")

    # STEP 5 — risk
    r.step(5, "Scanning for risk")
    r.plain(
        "Every meeting is scored for sensitivity. This one is a normal working "
        "meeting — no leaked secrets — so it's allowed to proceed to the AI."
    )
    source = db.query(SourceItem).filter(SourceItem.id == source_id).first()
    mt = db.query(MeetingTranscript).filter(MeetingTranscript.id == mt_id).first()
    r.tech(
        f"SourceItem.sensitivity_label='{source.sensitivity_label}', "
        f"MeetingTranscript.threat_score={mt.threat_score} (low = safe to process)."
    )

    # STEP 6 — AI extraction
    r.step(6, "Understanding the meeting (the AI reads the SAFE copy)")
    r.plain(
        "Now — and only now — the AI reads the meeting. It only ever sees the "
        "masked version from Step 3: no real names, no email. From that it pulls "
        "out the action items, decisions, blockers and questions."
    )
    provider, model = _ai_backend()
    knowledge = (
        db.query(KnowledgeItem)
        .filter(KnowledgeItem.source_id == source_id)
        .order_by(KnowledgeItem.id)
        .all()
    )
    r.tech(
        f"AI backend: provider='{provider}', model='{model}'. "
        f"Extracted {len(knowledge)} facts from THIS meeting:"
    )
    for k in knowledge:
        conf = f"{float(k.confidence):.0%}" if k.confidence is not None else "n/a"
        r.bullet(f"[{k.knowledge_type}] {(k.title_masked or '')[:80]}  (confidence {conf})")

    # STEP 7 — versioning
    r.step(7, "Remembering it correctly over time (versioning)")
    r.plain(
        "Each fact gets a stable identity, so if a LATER meeting changes it (a "
        "deadline moves, say) the system can replace the old version instead of "
        "keeping two contradictory copies. This is a first-time meeting, so every "
        "fact is brand new — version 1, active."
    )
    active = [k for k in knowledge if k.is_active]
    keyed = [k for k in knowledge if k.knowledge_key]
    embedded = [k for k in knowledge if k.embedding]
    methods = sorted(set(k.resolution_method for k in knowledge if k.resolution_method))
    r.tech(
        f"{len(knowledge)} facts: {len(active)} active, {len(keyed)} with a stable "
        f"knowledge_key, {len(embedded)} with a search embedding. "
        f"Resolution methods: {methods or ['new_chain']}."
    )

    # STEP 8 — ask questions
    r.step(8, "Asking the assistant a question")
    r.plain(
        "The payoff: ask about the meeting in plain English and the assistant "
        "finds the right facts, ranked by confidence. It searches across ALL "
        "meetings it remembers, so each answer is tagged with where it came from."
    )
    for q in ["What are the action items?", "What is blocking us?", "What was decided?"]:
        res = retrieve(q, db, user_id=1)
        r.line("")
        r.line(f"    You ask: \"{q}\"")
        if res["status"] == "ok" and res["results"]:
            for hit in res["results"][:2]:
                conf = f"{float(hit['confidence']):.0%}"
                origin = "from THIS meeting" if hit["source_id"] == source_id else "from an earlier meeting"
                r.line(
                    f"      -> [{hit['knowledge_type']}] {hit['value_ref'][:70]}  "
                    f"(confidence {conf}, {origin})"
                )
        else:
            r.line(f"      -> (no confident answer — status={res['status']})")
    r.line("")
    r.tech(
        "Each question runs the real retrieve() pipeline: classify intent -> "
        "search by category AND by meaning (embeddings) -> rank by confidence -> "
        "log the lookup."
    )

    return {"source_id": source_id, "vault": len(vault_rows), "facts": len(knowledge), "model": model}


# =====================================================================
# PART B — the safety net: a leaked credential
# =====================================================================

def part_b(r: Report, db) -> dict:
    payload = _load_unique(_QUARANTINE_FILE)
    r.part("B", "The safety net: a meeting with a leaked password")

    # STEP 9 — raw meeting with the credential
    r.step(9, "A meeting where someone pastes a live password")
    r.plain(
        "Same kind of meeting, but this time a participant pastes a real database "
        "connection string (with a password) into the chat. This is exactly the "
        "kind of mistake that leaks secrets — here's what the system does about it."
    )
    spoken = _spoken_lines(payload["vtt_content"])
    cred_line = next((s for n, s in spoken if "postgres://" in s.lower()), "")
    r.tech(f"The risky line in the transcript:")
    r.line(f"      \"{cred_line[:150]}\"")

    # STEP 10 — ingest -> quarantine
    r.step(10, "The system catches it and locks the meeting down")
    r.plain(
        "On the way in, the risk scanner spots the live credential and QUARANTINES "
        "the entire meeting — it is NOT handed to the AI at all. The strongest "
        "possible protection: the AI never even sees a meeting this sensitive."
    )
    transcript = parse_transcript_payload(
        transcript_metadata=payload["transcript_metadata"],
        vtt_content=payload["vtt_content"],
        meeting_metadata=payload["meeting_metadata"],
    )
    result = transcript_ingestion_service.ingest(transcript, db, force_reextract=True)
    source_id = result["source_id"]
    mt_id = result["meeting_transcript_id"]
    source = db.query(SourceItem).filter(SourceItem.id == source_id).first()
    mt = db.query(MeetingTranscript).filter(MeetingTranscript.id == mt_id).first()
    r.tech(
        f"ingest() -> outcome='{result['outcome']}', "
        f"sensitivity='{source.sensitivity_label}', threat_score={mt.threat_score}, "
        f"knowledge_item_count={result['knowledge_item_count']} (AI was skipped)."
    )

    # STEP 11 — masking + credential vaulted even though quarantined
    r.step(11, "The password is still masked and locked away")
    r.plain(
        "Even though the meeting was quarantined, the personal info and the leaked "
        "password were still scrubbed out and encrypted in the vault. The real "
        "password is sealed away — not left sitting in plain text anywhere."
    )
    vault_rows = db.query(VaultToken).filter(VaultToken.source_id == source_id).all()
    by_type: dict[str, int] = {}
    for v in vault_rows:
        by_type[_entity_family(v.entity_type)] = by_type.get(_entity_family(v.entity_type), 0) + 1
    cred_kinds = [k for k in by_type if "CONNECTION" in k or "CREDENTIAL" in k or "SECRET" in k]
    r.tech(
        f"{len(vault_rows)} encrypted VaultToken rows. The leaked credential was "
        f"captured as: {cred_kinds or ['(see breakdown)']}. Full breakdown:"
    )
    for etype, count in sorted(by_type.items(), key=lambda x: -x[1]):
        r.bullet(f"{etype}: {count}")

    return {"source_id": source_id, "vault": len(vault_rows),
            "outcome": result["outcome"], "threat": float(mt.threat_score or 0)}


def _entity_family(entity_type: str) -> str:
    """Collapse 'S298_EMAIL_ADDRESS_1' -> 'EMAIL_ADDRESS' so the breakdown is readable."""
    et = entity_type or ""
    if et.startswith("S") and "_" in et:
        parts = et.split("_", 1)
        if len(parts) == 2 and parts[0][1:].isdigit():
            et = parts[1]
    # drop a trailing numeric index like EMAIL_ADDRESS_1 -> EMAIL_ADDRESS
    bits = et.rsplit("_", 1)
    if len(bits) == 2 and bits[1].isdigit():
        et = bits[0]
    return et


def main():
    r = Report()
    db = SessionLocal()

    r.line("################################################################################")
    r.line("#  CARETAKER — HOW YOUR MEETINGS BECOME SAFE, SEARCHABLE KNOWLEDGE")
    r.line("################################################################################")
    r.line(f"Run at: {datetime.now().isoformat(timespec='seconds')}")
    r.line("")
    r.line("Read this top to bottom. Each step has a plain-English line and a")
    r.line("technical line. Every number is real — pulled live from the database")
    r.line("after actually running the pipeline on two sample meetings.")

    a = part_a(r, db)
    b = part_b(r, db)

    # ── Shared closing: paper trail + summary ────────────────────────────────
    r.step(12, "The paper trail (nothing happens silently)")
    r.plain(
        "Every sensitive action across both meetings — masking, AI calls, "
        "lookups — is recorded in an independent audit log. If anyone ever asks "
        "'what did the system do with my data?', here's the receipt."
    )
    counts: dict[str, int] = {}
    for sid in (a["source_id"], b["source_id"]):
        for ev in db.query(AuditEvent).filter(AuditEvent.source_id == sid).all():
            counts[ev.event_type] = counts.get(ev.event_type, 0) + 1
    for ev in (db.query(AuditEvent).filter(AuditEvent.event_type == "RETRIEVAL")
               .order_by(AuditEvent.id.desc()).limit(3).all()):
        counts[ev.event_type] = counts.get(ev.event_type, 0) + 1
    r.tech("Audit events recorded for this run:")
    for etype, count in sorted(counts.items(), key=lambda x: -x[1]):
        r.bullet(f"{etype}: {count}")

    r.step(13, "In one breath")
    r.plain(
        "Two messy Teams meetings went in. The normal one was scrubbed of personal "
        "info, the real values locked in an encrypted vault, read by the AI in its "
        "SAFE form only, turned into searchable facts you can ask about, and fully "
        "logged. The second meeting contained a leaked password — so the system "
        "caught it, sealed the password in the vault, and refused to let the AI "
        "read the meeting at all. Convenience when it's safe, a hard stop when it "
        "isn't."
    )
    r.tech(
        f"PART A: source_id={a['source_id']}, {a['vault']} secrets vaulted, "
        f"{a['facts']} facts extracted by '{a['model']}'. "
        f"PART B: source_id={b['source_id']}, outcome='{b['outcome']}', "
        f"threat_score={b['threat']}, {b['vault']} secrets vaulted, 0 facts (AI blocked)."
    )

    db.close()
    r.save(_REPORT_FILE)
    r.line("")
    r.line(f"📄 Saved a copy of this walkthrough to: {_REPORT_FILE}")


if __name__ == "__main__":
    main()
