#!/usr/bin/env python3
"""
Plan 1 + Plan 2 Full Flow Verification — transcript ingestion, versioning & retrieval.

Demonstrates the complete pipeline:
  1. Ingest transcript (Plan 1: archive, threat scan, masking, vault, knowledge persistence)
  2. Resolve versioning (Plan 2: knowledge_key, version, is_active, embeddings, supersession)
  3. Test retrieval (Plan 2: classification, structured+semantic search, ranking, audit)

Usage:
    cd caretaker
    python scripts/verify_plan1_and_plan2_pipeline.py

Output:
    plan1_plan2_verification_results.json (detailed report of full flow)
"""

import copy
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from database.connection import SessionLocal
from database.models import (
    SourceItem, ThreatAssessment, VaultToken, MeetingTranscript,
    TranscriptSegment, KnowledgeItem, LLMCallLog, AuditEvent
)
from services.graph.connectors.mock_transcript_fixtures import STANDUP
from services.graph.transcript_payload_parser import parse_transcript_payload
from services import transcript_ingestion_service
from services.knowledge_retrieval_service import retrieve
from utils.logger import get_logger

logger = get_logger("verify_plan1_and_plan2")

results = {
    "timestamp": datetime.now().isoformat(),
    "stages": {}
}


def print_section(title):
    print(f"\n{'='*80}")
    print(f"{title}")
    print(f"{'='*80}")


def stage_ingest_transcript():
    """Plan 1: Ingest a transcript through the full pipeline."""
    print_section("STAGE 1: TRANSCRIPT INGESTION (Plan 1)")
    db = SessionLocal()

    try:
        # Use a unique external_id per run so this verification actually
        # exercises fresh ingestion (and Plan 2 "new chain" resolution)
        # instead of hitting the dedup-by-external_id idempotency skip on
        # every rerun against a persistent Postgres DB.
        run_suffix = datetime.now().strftime("%Y%m%d%H%M%S%f")
        transcript_metadata = copy.deepcopy(STANDUP["transcript_metadata"])
        transcript_metadata["id"] = f"{transcript_metadata['id']}-{run_suffix}"

        # Parse mock transcript
        transcript = parse_transcript_payload(
            transcript_metadata=transcript_metadata,
            vtt_content=STANDUP["vtt_content"],
            meeting_metadata=STANDUP["meeting_metadata"],
        )
        print(f"Parsed transcript: {transcript.metadata.external_id}")
        print(f"  Subject: {transcript.metadata.subject}")
        print(f"  Utterances: {len(transcript.utterances)}")

        # Ingest (Plan 1 flow)
        result = transcript_ingestion_service.ingest(transcript, db, force_reextract=True)
        print(f"Ingestion result: {result}")

        results["stages"]["1_ingest"] = {
            "status": "success",
            "outcome": result["outcome"],
            "source_id": result["source_id"],
            "meeting_transcript_id": result["meeting_transcript_id"],
            "knowledge_item_count": result["knowledge_item_count"],
        }

        return result
    except Exception as e:
        logger.exception("Ingestion failed")
        results["stages"]["1_ingest"] = {"status": "error", "error": str(e)}
        return None
    finally:
        db.close()


def stage_plan1_artifacts(source_id):
    """Plan 1: Show what was created in the database."""
    print_section("STAGE 2: PLAN 1 ARTIFACTS")
    db = SessionLocal()

    try:
        # Raw archive
        source = db.query(SourceItem).filter(SourceItem.id == source_id).first()
        print(f"\n1. SourceItem (raw archive)")
        if source:
            print(f"   ID: {source.id}")
            print(f"   Type: {source.source_type}")
            print(f"   Sensitivity: {source.sensitivity_label}")
            print(f"   Created: {source.created_at}")

        # Threat assessment — note: transcript_ingestion_service stores its threat
        # score/category directly on SourceItem.sensitivity_label and
        # MeetingTranscript.threat_score instead of writing a ThreatAssessment row
        # (that table is only populated by the email/chat/calendar Graph sync path
        # in api/graph_sync.py). None here is the expected, pre-existing Plan 1
        # behavior for transcripts, not a missing record.
        threat = db.query(ThreatAssessment).filter(ThreatAssessment.source_id == source_id).first()
        print(f"\n2. ThreatAssessment (threat scan)")
        if threat:
            print(f"   Category: {threat.category}")
            print(f"   Score: {threat.threat_score}")
            print(f"   Credential Risk: {threat.credential_risk}")
        else:
            print(f"   N/A for transcripts (score stored on SourceItem/MeetingTranscript instead)")

        # Vault tokens
        vault_count = db.query(VaultToken).filter(VaultToken.source_id == source_id).count()
        print(f"\n3. VaultToken (encrypted secrets)")
        print(f"   Count: {vault_count}")

        # Meeting transcript
        mt = db.query(MeetingTranscript).filter(MeetingTranscript.source_id == source_id).first()
        print(f"\n4. MeetingTranscript (structure)")
        if mt:
            print(f"   Meeting ID: {mt.meeting_id}")
            print(f"   Subject: {mt.subject_masked[:60]}...")
            print(f"   Participants: {len(mt.participant_tokens)}")
            print(f"   Word count: {mt.word_count}")
            print(f"   Sensitivity: {mt.sensitivity_label}")

        # Transcript segments
        seg_count = db.query(TranscriptSegment).filter(
            TranscriptSegment.transcript_id == mt.id if mt else False
        ).count()
        print(f"\n5. TranscriptSegment (utterances)")
        print(f"   Count: {seg_count}")

        # Knowledge items (Plan 1)
        knowledge = db.query(KnowledgeItem).filter(KnowledgeItem.source_id == source_id).all()
        print(f"\n6. KnowledgeItem (extracted intelligence)")
        print(f"   Total: {len(knowledge)}")

        if knowledge:
            type_counts = {}
            for k in knowledge:
                type_counts[k.knowledge_type] = type_counts.get(k.knowledge_type, 0) + 1
            print(f"   By type:")
            for kt, count in sorted(type_counts.items()):
                print(f"     - {kt}: {count}")

        results["stages"]["2_plan1_artifacts"] = {
            "status": "success",
            "source_item": source.id if source else None,
            "threat_assessment": {"category": threat.category, "score": float(threat.threat_score)} if threat else None,
            "vault_tokens": vault_count,
            "meeting_transcript": {
                "id": mt.id,
                "segments": seg_count,
                "participants": len(mt.participant_tokens) if mt else 0,
            } if mt else None,
            "knowledge_items": len(knowledge),
            "knowledge_by_type": type_counts if knowledge else {},
        }

        return knowledge
    except Exception as e:
        logger.exception("Plan 1 artifacts query failed")
        results["stages"]["2_plan1_artifacts"] = {"status": "error", "error": str(e)}
        return []
    finally:
        db.close()


def stage_plan2_versioning(knowledge_items):
    """Plan 2: Show versioning, keys, embeddings, supersession."""
    print_section("STAGE 3: PLAN 2 VERSIONING & EVOLUTION")
    db = SessionLocal()

    try:
        # Re-query to get Plan 2 fields
        knowledge = db.query(KnowledgeItem).filter(
            KnowledgeItem.id.in_([k.id for k in knowledge_items])
        ).all()

        print(f"Total knowledge items: {len(knowledge)}")

        active_count = len([k for k in knowledge if k.is_active])
        inactive_count = len([k for k in knowledge if not k.is_active])
        print(f"  Active: {active_count}")
        print(f"  Inactive (superseded): {inactive_count}")

        # Group by knowledge_key
        by_key = {}
        for k in knowledge:
            key = k.knowledge_key or f"<no-key-{k.id}>"
            if key not in by_key:
                by_key[key] = []
            by_key[key].append(k)

        print(f"\nKnowledge keys: {len(by_key)}")
        for key, chain in by_key.items():
            active = [k for k in chain if k.is_active]
            print(f"\n  Key: {key}")
            print(f"    Chain length: {len(chain)} rows")
            print(f"    Active: {len(active)} (should be ≤ 1)")
            for k in chain:
                status = "✓ active" if k.is_active else "✗ superseded"
                has_emb = "✓ embedding" if k.embedding else "✗ no embedding"
                print(f"      v{k.version} ({status}, {has_emb}, id={k.id})")
                if k.superseded_by_id:
                    print(f"        → superseded_by_id={k.superseded_by_id}")
                if k.valid_from and k.valid_to:
                    print(f"        → valid {k.valid_from.isoformat()[:19]} to {k.valid_to.isoformat()[:19]}")

        # Embedding stats
        with_emb = len([k for k in knowledge if k.embedding])
        print(f"\nEmbeddings: {with_emb}/{len(knowledge)} rows populated")

        results["stages"]["3_plan2_versioning"] = {
            "status": "success",
            "total_rows": len(knowledge),
            "active_rows": active_count,
            "superseded_rows": inactive_count,
            "knowledge_keys": len(by_key),
            "rows_with_embeddings": with_emb,
            "chains": {
                key: {
                    "length": len(chain),
                    "active": len([k for k in chain if k.is_active]),
                    "versions": sorted(set(k.version for k in chain)),
                }
                for key, chain in by_key.items()
            }
        }

        return knowledge
    except Exception as e:
        logger.exception("Plan 2 versioning query failed")
        results["stages"]["3_plan2_versioning"] = {"status": "error", "error": str(e)}
        return []
    finally:
        db.close()


def stage_retrieval(knowledge_items):
    """Plan 2: Test retrieval (classification, search, ranking, audit)."""
    print_section("STAGE 4: PLAN 2 RETRIEVAL & RANKING")
    db = SessionLocal()

    try:
        if not knowledge_items:
            print("No knowledge items to retrieve.")
            results["stages"]["4_retrieval"] = {"status": "skipped", "reason": "no_knowledge"}
            return

        # Test query 1: structured (by knowledge_type)
        print("\n1. Structured retrieval (filter by type)")
        query1 = "what are the action items"
        result1 = retrieve(query1, db, user_id=1)
        print(f"   Query: '{query1}'")
        print(f"   Status: {result1['status']}")
        print(f"   Results: {len(result1['results'])}")
        if result1['results']:
            for i, r in enumerate(result1['results'][:2]):
                print(f"     [{i+1}] type={r['knowledge_type']}, confidence={r['confidence']}, "
                      f"source={r['source_type']}")

        # Test query 2: semantic (free-text)
        print("\n2. Semantic retrieval (embedding similarity)")
        query2 = "what was decided in the meeting"
        result2 = retrieve(query2, db, user_id=1)
        print(f"   Query: '{query2}'")
        print(f"   Status: {result2['status']}")
        print(f"   Results: {len(result2['results'])}")
        if result2['results']:
            for i, r in enumerate(result2['results'][:2]):
                print(f"     [{i+1}] type={r['knowledge_type']}, confidence={r['confidence']}")

        # Check audit events for retrieval
        retrieval_audits = db.query(AuditEvent).filter(
            AuditEvent.event_type == "RETRIEVAL"
        ).order_by(AuditEvent.created_at.desc()).limit(5).all()

        print(f"\n3. Retrieval audit trail")
        print(f"   Audit events (event_type=RETRIEVAL): {len(retrieval_audits)}")
        for audit in retrieval_audits[:2]:
            event_data = audit.event_data or {}
            print(f"     - Query type: {event_data.get('query_type', 'unknown')}, "
                  f"Results: {event_data.get('result_count', 0)}, "
                  f"Conflict: {event_data.get('conflict', False)}")

        results["stages"]["4_retrieval"] = {
            "status": "success",
            "test_queries": [
                {
                    "query": query1,
                    "retrieval_status": result1["status"],
                    "result_count": len(result1["results"]),
                    "intent": result1["intent"],
                },
                {
                    "query": query2,
                    "retrieval_status": result2["status"],
                    "result_count": len(result2["results"]),
                    "intent": result2["intent"],
                }
            ],
            "audit_events_count": len(retrieval_audits),
        }
    except Exception as e:
        logger.exception("Retrieval test failed")
        results["stages"]["4_retrieval"] = {"status": "error", "error": str(e)}
    finally:
        db.close()


def stage_summary():
    """Summary stats."""
    print_section("SUMMARY & VERIFICATION")
    db = SessionLocal()

    try:
        knowledge_count = db.query(KnowledgeItem).count()
        active_count = db.query(KnowledgeItem).filter(KnowledgeItem.is_active.is_(True)).count()
        with_embeddings = db.query(KnowledgeItem).filter(KnowledgeItem.embedding.isnot(None)).count()
        with_key = db.query(KnowledgeItem).filter(KnowledgeItem.knowledge_key.isnot(None)).count()

        audit_count = db.query(AuditEvent).count()
        retrieval_audits = db.query(AuditEvent).filter(AuditEvent.event_type == "RETRIEVAL").count()

        print(f"\n✅ Plan 1 + Plan 2 Pipeline Status:")
        print(f"   Knowledge Items: {knowledge_count} total")
        print(f"     - Active (current): {active_count}")
        print(f"     - With embeddings: {with_embeddings}")
        print(f"     - With knowledge_key: {with_key}")
        print(f"   Audit Trail:")
        print(f"     - Total events: {audit_count}")
        print(f"     - Retrieval events: {retrieval_audits}")

        success = knowledge_count > 0 and active_count > 0 and with_embeddings > 0
        status = "✅ SUCCESS" if success else "⚠️ WARNING"

        print(f"\n{status}")
        if success:
            print(f"Full pipeline working! Transcripts → Knowledge → Retrieval")
        else:
            print(f"Check logs above for details.")

        results["summary"] = {
            "knowledge_items_total": knowledge_count,
            "knowledge_items_active": active_count,
            "knowledge_items_with_embeddings": with_embeddings,
            "knowledge_items_with_key": with_key,
            "audit_events_total": audit_count,
            "audit_retrieval_events": retrieval_audits,
            "status": "SUCCESS" if success else "WARNING",
        }
    except Exception as e:
        logger.exception("Summary failed")
        results["summary"] = {"status": "error", "error": str(e)}
    finally:
        db.close()


def main():
    print_section("PLAN 1 + PLAN 2 FULL FLOW VERIFICATION")
    print(f"Timestamp: {datetime.now().isoformat()}")
    print(f"Testing: Transcript Ingestion → Versioning → Retrieval")

    # Run stages
    ingest_result = stage_ingest_transcript()
    if not ingest_result:
        print("\n❌ Ingestion failed. Aborting.")
        return

    knowledge = stage_plan1_artifacts(ingest_result["source_id"])
    if not knowledge:
        print("\n⚠️ No knowledge items created. Continuing...")
    else:
        knowledge = stage_plan2_versioning(knowledge)
        stage_retrieval(knowledge)

    stage_summary()

    # Save results
    output_file = "plan1_plan2_verification_results.json"
    with open(output_file, "w") as f:
        json.dump(results, f, indent=2)

    print(f"\n📄 Full results saved to: {output_file}")
    print(f"View with: cat {output_file} | python -m json.tool")


if __name__ == "__main__":
    main()
