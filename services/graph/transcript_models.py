"""
Transcript domain model — provider-agnostic internal representation.

Both MockTranscriptProvider and the future GraphTranscriptProvider must
produce these objects (via the shared transcript_payload_parser). Nothing
downstream of a provider — TranscriptIngestionService, masking, the LLM
extraction stage, KnowledgeItem persistence — is allowed to see a raw
provider-specific payload shape. Only these dataclasses.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class Participant:
    """A meeting participant, identified by Azure AD object id / email / display name."""
    aad_object_id: Optional[str] = None
    email:         Optional[str] = None
    display_name:  Optional[str] = None
    role:          str           = "attendee"   # organizer | attendee | external

    def identity_key(self) -> str:
        """Stable key for matching the same participant across utterances/participant list."""
        return (self.aad_object_id or self.email or self.display_name or "").lower()


@dataclass
class Utterance:
    """A single speaker turn within the transcript."""
    speaker:        Optional[Participant]
    text:           str
    start_ms:       int
    end_ms:         int
    sequence_index: int


@dataclass
class MeetingMetadata:
    """Meeting-level metadata for a transcript."""
    meeting_id:       str
    subject:          Optional[str] = None
    organizer:        Optional[Participant] = None
    start_time:       Optional[datetime] = None
    end_time:         Optional[datetime] = None
    duration_seconds: Optional[int] = None
    join_url:         Optional[str] = None
    external_id:      Optional[str] = None   # the transcript resource's own Graph id (dedup key)
    raw_source:       dict = field(default_factory=dict)
    # original provider payload(s), retained only for archival (SourceItem.raw_text) —
    # never read by masking, threat-scoring, or extraction logic.


@dataclass
class Transcript:
    """A complete, parsed meeting transcript — the unit TranscriptIngestionService consumes."""
    metadata:     MeetingMetadata
    participants: list[Participant] = field(default_factory=list)
    utterances:   list[Utterance]   = field(default_factory=list)
