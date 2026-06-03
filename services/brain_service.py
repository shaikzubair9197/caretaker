from services.task_service import TaskService
from services.memory_service import MemoryService
from services.context_service import get_last_hour_context
from services.llm_service import LLMService


class BrainService:

    @staticmethod
    def build_context(db):

        # -------------------
        # RAW DATA
        # -------------------
        tasks = TaskService.get_all(db)
        memories = MemoryService.get_recent(db, 10)
        context = get_last_hour_context(db)

        # -------------------
        # INSIGHTS (RULES)
        # -------------------
        focus_minutes = context.get("focus_minutes", 0)
        applications = context.get("applications", [])

        top_app = applications[0]["window"] if applications else None

        work_state = (
            "deep_work" if focus_minutes > 60 else
            "focused" if focus_minutes > 25 else
            "low_focus"
        )

        distraction_level = (
            "low" if len(applications) <= 2 else
            "medium" if len(applications) <= 5 else
            "high"
        )

        insights = {
            "focus_minutes": focus_minutes,
            "top_application": top_app,
            "work_state": work_state,
            "distraction_level": distraction_level,
            "task_count": len(tasks),
            "memory_count": len(memories)
        }

        # -------------------
        # SERIALIZE
        # -------------------
        payload = {
            "tasks": [
                {
                    "id": t.id,
                    "description": t.description,
                    "priority": t.priority,
                    "status": t.status
                }
                for t in tasks
            ],
            "memories": [
                {
                    "id": m.id,
                    "text": m.text,
                    "type": m.type,
                    "importance": m.importance
                }
                for m in memories
            ],
            "context": context,
            "insights": insights
        }

        # -------------------
        # 🧠 LLM REASONING LAYER
        # -------------------
        llm_output = LLMService.reason_brain(payload)

        # -------------------
        # FINAL OUTPUT
        # -------------------
        return {
            **payload,
            "brain": llm_output
        }