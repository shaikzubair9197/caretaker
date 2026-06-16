from fastapi import APIRouter
from sqlalchemy.exc import IntegrityError as SQLIntegrityError

from database.connection import SessionLocal
from database.models import Task, SourceItem

from schemas.panic_schema import (
    PanicDumpRequest,
    PanicDumpResponse,
    CommitmentItem,
)

from services.commitment_service import CommitmentService
from services.llm_service import LLMService, LLMStatus
from services.memory_service import MemoryService
from services.panic_parser import parse as regex_parse
from services.preprocessing_service import PreprocessingService
from utils.logger import get_logger

logger = get_logger("api.panic_dump")

router = APIRouter(prefix="/panic_dump", tags=["Panic Dump"])

# Business-level statuses that only exist at the panic_dump layer
# (the LLM call itself succeeded but the content was insufficient).
_BIZ_EMPTY_RESULT       = "EMPTY_RESULT"
_BIZ_ITEMS_SCHEMA_INVALID = "ITEMS_SCHEMA_INVALID"


def _items_from_llm(llm_items: list, masked_text: str) -> list[dict]:
    """Normalise LLM output. Uses masked_text as fallback so PII never leaks."""
    result = []
    for item in llm_items:
        if not isinstance(item, dict) or not item.get("action"):
            continue
        result.append({
            "raw_text":        item.get("action", masked_text),
            "action":          item["action"],
            "person":          item.get("person"),
            "commitment_type": item.get("commitment_type", "task"),
            "priority":        item.get("priority", "medium"),
            "due_hint":        item.get("due_hint"),
        })
    return result


def _items_from_regex(parsed: list) -> list[dict]:
    for item in parsed:
        item.setdefault("priority", "medium")
        item.setdefault("due_hint", None)
    return parsed


@router.post("/", response_model=PanicDumpResponse)
def panic_dump(request: PanicDumpRequest):
    db = SessionLocal()
    try:
        # ── Step 1: Archive raw text ─────────────────────────────────────────
        source = SourceItem(source_type="panic_dump", raw_text=request.text)
        db.add(source)
        db.flush()

        # ── Step 2: Preprocess ───────────────────────────────────────────────
        preprocessed = PreprocessingService.preprocess(request.text)
        source.noise_removed     = preprocessed.cleaned_text
        source.masked_text       = preprocessed.masked_text
        source.sensitivity_label = preprocessed.sensitivity_label
        source.metadata_ = {
            "is_autoreply":    preprocessed.is_autoreply,
            "redaction_count": len(preprocessed.redactions),
            "redactions":      preprocessed.redactions,
        }

        # ── Step 3: LLM extraction ───────────────────────────────────────────
        # Always send masked_text — raw text never leaves SourceItem.
        llm_call = LLMService.extract_panic_items(
            preprocessed.masked_text,
            entity_types=[r["type"] for r in preprocessed.redactions],
        )

        llm_used         = False
        overload_detected = False
        parsed_items: list[dict] = []

        # Map LLM result to response-level status
        if llm_call.succeeded:
            if not llm_call.data.get("items"):
                # LLM responded but returned an empty items list
                llm_status = _BIZ_EMPTY_RESULT
                logger.warning("LLM returned empty items list — regex fallback")
            else:
                valid_items = _items_from_llm(llm_call.data["items"], preprocessed.masked_text)
                if valid_items:
                    parsed_items      = valid_items
                    overload_detected = llm_call.data.get("overload_detected", False)
                    llm_status        = LLMStatus.SUCCESS
                    llm_used          = True
                    logger.info(f"LLM extracted {len(parsed_items)} items")
                else:
                    llm_status = _BIZ_ITEMS_SCHEMA_INVALID
                    logger.warning("LLM items had no 'action' field — regex fallback")
        else:
            # Propagate the specific status code from the LLM layer directly —
            # this is what replaces the old opaque "unavailable".
            llm_status = llm_call.status
            logger.warning(
                f"LLM failed — status={llm_status} "
                f"exc={llm_call.exception_type} reason={llm_call.fallback_reason}"
            )

        if not llm_used:
            # Regex fallback always operates on masked_text — never raw request.text.
            parsed_items = _items_from_regex(regex_parse(preprocessed.masked_text))
            logger.info(
                f"Regex fallback [llm_status={llm_status}]: "
                f"extracted {len(parsed_items)} items"
            )

        # ── Step 4: Persist Tasks + Commitments ─────────────────────────────
        tasks_created = 0
        tasks_skipped = 0

        for item in parsed_items:
            try:
                with db.begin_nested():
                    db.add(Task(
                        description=item["action"],
                        priority=item.get("priority", "medium"),
                        status="pending",
                        source_id=source.id,
                    ))
                    db.flush()
                tasks_created += 1
            except SQLIntegrityError:
                tasks_skipped += 1
                logger.info(f"Duplicate task skipped: '{item['action'][:60]}'")

            CommitmentService.create(
                db=db,
                raw_text=item["raw_text"],
                action=item["action"],
                person=item.get("person"),
                commitment_type=item.get("commitment_type", "task"),
                source_id=source.id,
            )

        if tasks_skipped:
            logger.info(f"Task dedup: {tasks_created} created, {tasks_skipped} skipped")

        # ── Step 5: Memory ───────────────────────────────────────────────────
        MemoryService.create(
            db=db,
            text=preprocessed.masked_text,
            memory_type="panic_note",
            importance=2,
            source_id=source.id,
        )

        # ── Step 6: Atomic commit ────────────────────────────────────────────
        db.commit()

        logger.info(
            f"Panic dump complete: {len(parsed_items)} items, "
            f"label={preprocessed.sensitivity_label}, "
            f"llm_used={llm_used} [{llm_status}]"
        )

        return PanicDumpResponse(
            item_count        = len(parsed_items),
            items             = [CommitmentItem(**item) for item in parsed_items],
            sensitivity_label = preprocessed.sensitivity_label,
            overload_detected = overload_detected,
            llm_used          = llm_used,
            llm_status        = llm_status,
            llm_reason        = llm_call.reason if not llm_call.succeeded else None,
            llm_fix           = llm_call.fix    if not llm_call.succeeded else None,
        )

    except Exception:
        db.rollback()
        raise

    finally:
        db.close()
