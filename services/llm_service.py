import json


class LLMService:

    @staticmethod
    def reason_brain(payload: dict):

        """
        For now: deterministic mock reasoning layer.
        Later you can plug OpenAI here.
        """

        tasks = payload.get("tasks", [])
        context = payload.get("context", {})
        insights = payload.get("insights", {})

        # -------------------------
        # Simple reasoning logic
        # -------------------------

        high_priority_tasks = [
            t for t in tasks
            if t["priority"] == "high"
        ]

        recommendations = []

        if insights["work_state"] == "low_focus":
            recommendations.append(
                "Low focus detected. Start with smallest task to build momentum."
            )

        if len(high_priority_tasks) > 0:
            recommendations.append(
                "You have high priority tasks pending. Focus on them first."
            )

        if insights["distraction_level"] == "high":
            recommendations.append(
                "High distraction detected. Reduce application switching."
            )

        top_task = tasks[0]["description"] if tasks else None

        return {
            "summary": {
                "top_task": top_task,
                "task_count": len(tasks),
                "focus_state": insights["work_state"]
            },
            "recommendations": recommendations
        }