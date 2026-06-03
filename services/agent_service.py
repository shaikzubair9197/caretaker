from services.context_service import (
    get_agent_context
)


def process_message(
    db,
    message: str
):

    context = get_agent_context(db)

    message = message.lower()

    if "task" in message:

        return {
            "answer": (
                f"You have "
                f"{context['pending_task_count']} "
                f"pending tasks."
            )
        }

    if "working" in message:

        return {
            "answer": (
                f"You spent most of your time in "
                f"{context['top_application']}."
            )
        }

    if "focus" in message:

        return {
            "answer": (
                f"Focus time today: "
                f"{context['focus_minutes']} minutes."
            )
        }

    return {
        "answer": (
            "I am still learning. "
            "Try asking about tasks, focus, or work."
        )
    }