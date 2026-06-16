from typing import Dict

from services.llm_service import LLMService
from utils.logger import get_logger

logger = get_logger("services.intent")


class IntentService:

    @staticmethod
    def decide(brain_data: Dict) -> Dict:
        """
        Converts brain context into an action plan.
        Tries LLM first; falls back to deterministic rules if Ollama is unavailable.
        """
        tasks = brain_data.get("tasks", [])
        memories = brain_data.get("memories", [])
        context = brain_data.get("context", {})
        insights = brain_data.get("insights", {})

        focus_minutes = context.get("focus_minutes", 0)
        work_state = insights.get("work_state", "unknown")
        top_task = tasks[0]["description"] if tasks else None

        # ── LLM path ────────────────────────────────────────────────────────
        llm_context = {
            "focus_minutes": focus_minutes,
            "work_state": work_state,
            "distraction_level": insights.get("distraction_level", "low"),
            "top_application": insights.get("top_application"),
            "pending_tasks": [
                {"description": t["description"], "priority": t.get("priority", "medium")}
                for t in tasks[:5]
            ],
            "pending_commitments": [
                {"text": m["text"], "type": m.get("type")}
                for m in memories[:5]
                if m.get("type") in ("panic_note", "follow_up", "work_note")
            ],
        }

        llm_call = LLMService.reason_intent(llm_context)

        if llm_call.succeeded and llm_call.data and "action_type" in llm_call.data:
            logger.info(
                f"LLM intent: {llm_call.data.get('action_type')} "
                f"urgency={llm_call.data.get('urgency')} [{llm_call.status}]"
            )
            actions = [
                {
                    "type":    llm_call.data["action_type"],
                    "message": llm_call.data.get("message", ""),
                    "urgency": llm_call.data.get("urgency", "medium"),
                    "source":  "llm",
                }
            ]
            return {
                "actions": actions,
                "summary": {
                    "top_task": top_task,
                    "focus_minutes": focus_minutes,
                    "work_state": work_state,
                },
            }

        # ── Deterministic fallback ───────────────────────────────────────────
        logger.debug(f"LLM fallback [{llm_call.status}] — using rule-based intent")
        actions = []

        if tasks and work_state == "low_focus":
            actions.append({
                "type": "suggest_next_task",
                "message": f"Focus on: {top_task}",
                "urgency": "medium",
                "source": "rules",
            })

        panic_memories = [m for m in memories if m.get("type") == "panic_note"]
        if panic_memories:
            actions.append({
                "type": "commitment_reminder",
                "message": f"You have {len(panic_memories)} unresolved brain dump(s)",
                "urgency": "high",
                "source": "rules",
            })
        elif memories:
            actions.append({
                "type": "commitment_reminder",
                "message": f"You have {len(memories)} pending memories",
                "urgency": "low",
                "source": "rules",
            })

        if insights.get("distraction_level") == "high":
            actions.append({
                "type": "focus_recommendation",
                "message": "High distraction detected — close unused apps",
                "urgency": "medium",
                "source": "rules",
            })

        return {
            "actions": actions,
            "summary": {
                "top_task": top_task,
                "focus_minutes": focus_minutes,
                "work_state": work_state,
            },
        }
