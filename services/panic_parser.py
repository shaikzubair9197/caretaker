import re
from typing import List, Dict

_COMMITMENT_VERBS = re.compile(
    r"\b(send|email|reply|forward|follow[\s-]?up|ping|message|remind|tell|update|share|deliver|submit|schedule)\b",
    re.IGNORECASE,
)

_PERSON_PATTERN = re.compile(
    r"\b(?:to|with|for)\s+([A-Za-z]+(?:\s[A-Za-z]+)?)\b",
    re.IGNORECASE,
)

_REMINDER_WORDS = re.compile(
    r"\b(remind|don'?t forget|remember to|make sure)\b",
    re.IGNORECASE,
)

_EMAIL_WORDS = re.compile(
    r"\b(email|send|reply|forward)\b",
    re.IGNORECASE,
)


def _split_into_items(text: str) -> List[str]:
    items = re.split(r"[.\n;!?]+|(?<!\w),(?!\w)", text)
    return [i.strip() for i in items if len(i.strip()) > 5]


def _classify(item: str) -> str:
    if _EMAIL_WORDS.search(item):
        return "email"
    if _REMINDER_WORDS.search(item):
        return "reminder"
    if _COMMITMENT_VERBS.search(item):
        return "follow_up"
    return "task"


def _extract_person(item: str) -> str | None:
    match = _PERSON_PATTERN.search(item)
    return match.group(1).title() if match else None


def parse(text: str) -> List[Dict]:
    """
    Split raw panic dump text into structured commitment items.
    Returns a list of dicts ready for persistence.
    """
    items = _split_into_items(text)

    results = []
    for item in items:
        results.append({
            "raw_text": item,
            "action": item,
            "person": _extract_person(item),
            "commitment_type": _classify(item),
        })

    return results
