from embeddings.embedder import encode
from embeddings.vector_search import search_similar
from services.task_service import TaskService
from services.memory_service import MemoryService
from services.context_service import get_agent_context


class BrainService:

    @staticmethod
    def build_context(db):

        tasks = TaskService.get_all(db)
        context = get_agent_context(db)

        task_list = [
            {
                "id": t.id,
                "description": t.description,
                "priority": t.priority,
                "status": t.status,
            }
            for t in tasks
        ]

        # Semantic retrieval: fetch memories relevant to the current active window.
        # Falls back to most-recent-10 when embeddings aren't stored yet or when
        # search_similar returns an empty list (no matches above min_score).
        top_app = context.get("top_application")
        memory_list = None
        memory_source = "recency"

        if top_app:
            query_vec = encode(top_app)
            if query_vec:
                results = search_similar(db, query_vec, limit=8)
                if results:
                    memory_list = results
                    memory_source = "semantic"
                # else: results=[], fall through to recency below

        if not memory_list:
            raw = MemoryService.get_recent(db, 10)
            memory_list = [
                {
                    "id": m.id,
                    "text": m.text,
                    "type": m.type,
                    "importance": m.importance,
                }
                for m in raw
            ]

        focus_minutes = context["focus_minutes"]

        insights = {
            "focus_minutes": focus_minutes,
            "task_count": len(task_list),
            "memory_count": len(memory_list),
            "memory_source": memory_source,
            "top_application": top_app,
            "work_state": "low_focus" if focus_minutes < 30 else "focused",
            "distraction_level": (
                "high" if len(context["applications"]) > 5 else "low"
            ),
        }

        brain_summary = {
            "top_task": task_list[0]["description"] if task_list else None,
            "task_count": len(task_list),
            "focus_state": "low_focus" if focus_minutes < 30 else "focused",
        }

        return {
            "tasks": task_list,
            "memories": memory_list,
            "context": context,
            "insights": insights,
            "brain": brain_summary,
        }
