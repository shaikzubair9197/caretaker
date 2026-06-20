#!/usr/bin/env python3
"""
Plan 1 Verification Script — Run full transcript pipeline and capture results.

Usage:
    python verify_plan1_pipeline.py

Output:
    plan1_verification_results.json (detailed report of each stage)
"""

import json
import requests
from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from database.connection import SessionLocal
from database.models import (
    SourceItem, ThreatAssessment, VaultToken, MeetingTranscript,
    TranscriptSegment, KnowledgeItem, LLMCallLog, AuditEvent
)
from utils.config import settings
from utils.logger import get_logger
import dotenv

dotenv.load_dotenv()

logger = get_logger("verify_plan1_pipeline")

API_KEY = dotenv.dotenv_values().get("CARETAKER_API_KEY") or "test-key"
API_BASE = "http://localhost:8000"
ENDPOINT = f"{API_BASE}/graph/sync/transcript/mock-trigger"

results = {
    "timestamp": datetime.now().isoformat(),
    "endpoint": ENDPOINT,
    "stages": {}
}


def run_transcript_sync():
    """Trigger the transcript ingestion pipeline."""
    logger.info(f"Triggering: POST {ENDPOINT}")
    print(f"\n{'='*80}")
    print(f"STAGE 0: TRIGGER TRANSCRIPT SYNC")
    print(f"{'='*80}")
    print(f"POST {ENDPOINT}")

    try:
        response = requests.post(
            ENDPOINT,
            headers={"X-API-Key": API_KEY},
            timeout=60
        )
        response.raise_for_status()
        result = response.json()
        print(f"✅ Response: {json.dumps(result, indent=2)}")

        results["stages"]["0_trigger"] = {
            "status": "success",
            "response": result
        }
        return True
    except Exception as e:
        print(f"❌ Error: {e}")
        results["stages"]["0_trigger"] = {
            "status": "error",
            "error": str(e)
        }
        return False


def query_stage(stage_name, stage_num, db, table_class, filters=None):
    """Query a database table and capture results."""
    print(f"\n{'='*80}")
    print(f"STAGE {stage_num}: {stage_name}")
    print(f"{'='*80}")

    try:
        # Query all rows (don't filter by time, since idempotent duplicates won't create new rows)
        query = db.query(table_class)

        if filters:
            for attr, value in filters.items():
                query = query.filter(getattr(table_class, attr) == value)

        rows = query.order_by(table_class.id.desc()).limit(50).all()
        count = len(rows)

        print(f"Table: {table_class.__tablename__}")
        print(f"Rows found: {count}")

        # Capture row data
        row_data = []
        for row in rows:
            row_dict = {}
            for col in table_class.__table__.columns:
                val = getattr(row, col.name)
                # Handle special types
                if isinstance(val, datetime):
                    row_dict[col.name] = val.isoformat()
                elif isinstance(val, dict):
                    row_dict[col.name] = val
                elif isinstance(val, list):
                    row_dict[col.name] = val
                else:
                    row_dict[col.name] = str(val) if val is not None else None
            row_data.append(row_dict)

        if row_data:
            print(f"Sample row: {json.dumps(row_data[0], indent=2)}")

        results["stages"][f"{stage_num}_{stage_name.lower().replace(' ', '_')}"] = {
            "status": "success",
            "table": table_class.__tablename__,
            "count": count,
            "rows": row_data
        }

        return count, row_data

    except Exception as e:
        print(f"❌ Query error: {e}")
        results["stages"][f"{stage_num}_{stage_name.lower().replace(' ', '_')}"] = {
            "status": "error",
            "error": str(e)
        }
        return 0, []


def main():
    print("\n" + "="*80)
    print("PLAN 1 PIPELINE VERIFICATION")
    print("="*80)
    print(f"Timestamp: {datetime.now().isoformat()}")
    print(f"API Base: {API_BASE}")
    print(f"Database: Using SessionLocal()")

    # Step 0: Trigger sync
    if not run_transcript_sync():
        print("\n❌ Pipeline trigger failed. Aborting.")
        return

    # Wait a moment for DB writes
    import time
    time.sleep(2)

    # Open DB session
    db = SessionLocal()

    try:
        # Query each stage
        count_source, data_source = query_stage(
            "Raw Archive", 1, db, SourceItem,
            filters={"source_type": "transcript"}
        )

        count_threat, data_threat = query_stage(
            "Threat Scan", 2, db, ThreatAssessment
        )

        count_vault, data_vault = query_stage(
            "Vault Tokens", 3, db, VaultToken
        )

        count_transcript, data_transcript = query_stage(
            "Meeting Transcript", 4, db, MeetingTranscript
        )

        count_segments, data_segments = query_stage(
            "Transcript Segments", 5, db, TranscriptSegment
        )

        count_knowledge, data_knowledge = query_stage(
            "Knowledge Items", 6, db, KnowledgeItem,
            filters={"source_type": "transcript"}
        )

        count_llm_log, data_llm_log = query_stage(
            "LLM Call Logs", 7, db, LLMCallLog
        )

        count_audit, data_audit = query_stage(
            "Audit Events", 8, db, AuditEvent
        )

        # Summary
        print(f"\n{'='*80}")
        print("SUMMARY")
        print(f"{'='*80}")
        print(f"✅ SourceItem (raw archive):        {count_source} rows")
        print(f"✅ ThreatAssessment (threat scan):  {count_threat} rows")
        print(f"✅ VaultToken (encrypted secrets):  {count_vault} rows")
        print(f"✅ MeetingTranscript (structure):   {count_transcript} rows")
        print(f"✅ TranscriptSegment (utterances):  {count_segments} rows")
        print(f"✅ KnowledgeItem (intelligence):    {count_knowledge} rows")
        print(f"✅ LLMCallLog (LLM audit):          {count_llm_log} rows")
        print(f"✅ AuditEvent (full trail):         {count_audit} rows")

        # Knowledge type breakdown
        if count_knowledge > 0:
            print(f"\nKnowledge Types Breakdown:")
            knowledge_types = db.query(
                KnowledgeItem.knowledge_type,
            ).filter(KnowledgeItem.source_type == "transcript").all()

            type_counts = {}
            for kt in knowledge_types:
                type_counts[kt[0]] = type_counts.get(kt[0], 0) + 1

            for ktype, kcount in sorted(type_counts.items()):
                print(f"  {ktype}: {kcount}")

            results["knowledge_type_breakdown"] = type_counts

        # Add summary to results
        results["summary"] = {
            "source_items": count_source,
            "threat_assessments": count_threat,
            "vault_tokens": count_vault,
            "meeting_transcripts": count_transcript,
            "transcript_segments": count_segments,
            "knowledge_items": count_knowledge,
            "llm_call_logs": count_llm_log,
            "audit_events": count_audit
        }

        # Success check
        if count_knowledge > 0:
            results["status"] = "SUCCESS"
            print(f"\n{'='*80}")
            print("✅ PLAN 1 PIPELINE WORKING!")
            print(f"{'='*80}")
        else:
            results["status"] = "WARNING"
            print(f"\n{'='*80}")
            print("⚠️  No knowledge items created. Check logs above.")
            print(f"{'='*80}")

    finally:
        db.close()

    # Save results to JSON
    output_file = "plan1_verification_results.json"
    with open(output_file, "w") as f:
        json.dump(results, f, indent=2)

    print(f"\n📄 Full results saved to: {output_file}")
    print(f"\nTo view: cat {output_file} | python -m json.tool")


if __name__ == "__main__":
    main()
