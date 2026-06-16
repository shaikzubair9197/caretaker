from pydantic import BaseModel
from typing import List, Optional


class PanicDumpRequest(BaseModel):
    text: str


class CommitmentItem(BaseModel):
    raw_text: str
    action: str
    person: Optional[str]
    commitment_type: str
    priority: str = "medium"
    due_hint: Optional[str] = None


class PanicDumpResponse(BaseModel):
    item_count: int
    items: List[CommitmentItem]
    sensitivity_label: str = "PUBLIC"
    overload_detected: bool = False
    llm_used: bool = False
    # Granular LLM observability fields (Layer 5/7)
    llm_status: str = "NOT_CALLED"
    # SUCCESS | CONNECTION_REFUSED | DNS_FAILURE | TIMEOUT | MODEL_NOT_FOUND |
    # HTTP_ERROR | JSON_PARSE_ERROR | SCHEMA_VALIDATION_FAILURE |
    # EMPTY_RESULT | ITEMS_SCHEMA_INVALID | UNKNOWN_ERROR
    llm_reason: Optional[str] = None   # human-readable explanation
    llm_fix:    Optional[str] = None   # suggested remediation step
