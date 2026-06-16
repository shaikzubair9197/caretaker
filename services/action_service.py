from typing import List, Dict

from utils.logger import get_logger

logger = get_logger("services.action")

# Action types that require explicit user approval before execution.
# Everything else can be surfaced directly (it's just a UI suggestion).
_APPROVAL_REQUIRED = {
    "send_email",
    "create_calendar_event",
    "delete_task",
    "external_api_call",
}


class ActionService:

    @staticmethod
    def execute(actions: List[Dict]) -> List[Dict]:
        """
        Evaluate each action from IntentService.
        - Actions needing approval → status="pending_approval", not executed
        - UI suggestions (notifications, popups) → executed immediately (logged)
        Returns a list of result dicts describing what happened.
        """
        results = []

        for action in actions:
            action_type = action.get("type", "unknown")
            message = action.get("message", "")
            urgency = action.get("urgency", "medium")

            if action_type in _APPROVAL_REQUIRED:
                results.append({
                    "action_type": action_type,
                    "status": "pending_approval",
                    "message": message,
                    "urgency": urgency,
                    "note": "Requires user approval before execution",
                })
                logger.info(f"Action gated for approval: {action_type}")
                continue

            # UI suggestions — no side effects, just surface them
            ui_map = {
                "suggest_next_task": "popup",
                "commitment_reminder": "notification",
                "focus_recommendation": "notification",
                "panic_mode": "full_screen",
                "health_check": "notification",
            }

            ui_type = ui_map.get(action_type, "notification")
            results.append({
                "action_type": action_type,
                "status": "surfaced",
                "ui": ui_type,
                "message": message,
                "urgency": urgency,
            })
            logger.debug(f"Action surfaced: {action_type} via {ui_type}")

        return results
