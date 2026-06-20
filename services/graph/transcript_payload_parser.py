"""
transcript_payload_parser — converts raw Microsoft Graph transcript resources
into the provider-agnostic Transcript domain model.

This module is written against the real shape of three Graph resources:

  1. Transcript metadata  — GET /users/{id}/onlineMeetings/{meetingId}/transcripts
     {"id": ..., "meetingId": ..., "createdDateTime": ..., "meetingOrganizer": {...}}

  2. Transcript content    — GET .../transcripts/{id}/content (format=text/vtt)
     Standard WebVTT, with speaker turns marked using the "<v Display Name>" voice tag:
         00:00:01.000 --> 00:00:05.000
         <v Alice Chen>Let's start the standup.

  3. Meeting/participant metadata — GET /users/{id}/onlineMeetings/{meetingId}
     {"subject": ..., "startDateTime": ..., "endDateTime": ..., "joinWebUrl": ...,
      "participants": {"organizer": {...}, "attendees": [...]}}

Both MockTranscriptProvider and the future GraphTranscriptProvider call
parse_transcript_payload() with payloads in this shape — the function cannot
tell whether its input came from a fixture or a live Graph response. That is
what lets the provider swap later touch only the fetch half.
"""

import re
from datetime import datetime, timezone
from typing import Optional

from utils.logger import get_logger
from services.graph.transcript_models import (
    MeetingMetadata,
    Participant,
    Transcript,
    Utterance,
)

logger = get_logger("services.graph.transcript_payload_parser")

_VTT_TIME_RE = re.compile(
    r"(\d{2}):(\d{2}):(\d{2})[.,](\d{3})\s*-->\s*(\d{2}):(\d{2}):(\d{2})[.,](\d{3})"
)
_VOICE_TAG_RE = re.compile(r"^<v\s+([^>]+)>(.*)$", re.DOTALL)


def _parse_dt(value: Optional[str]) -> Optional[datetime]:
    """Parse a Graph ISO 8601 timestamp (always UTC, may carry a trailing Z) to naive UTC."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    except Exception:
        return None


def _vtt_timestamp_to_ms(h: str, m: str, s: str, ms: str) -> int:
    return ((int(h) * 3600 + int(m) * 60 + int(s)) * 1000) + int(ms)


def _participant_from_identity(identity_block: dict, role: str) -> Participant:
    """Build a Participant from a Graph identity/communications-style block."""
    user = (identity_block.get("identity") or {}).get("user") or {}
    return Participant(
        aad_object_id=user.get("id"),
        email=identity_block.get("upn") or identity_block.get("email"),
        display_name=user.get("displayName") or identity_block.get("displayName"),
        role=role,
    )


def _parse_participants(meeting_metadata: dict) -> tuple[list[Participant], Optional[Participant]]:
    """Parse the onlineMeeting-style participants block into (all_participants, organizer)."""
    participants_block = meeting_metadata.get("participants") or {}
    organizer_block = participants_block.get("organizer")
    organizer = _participant_from_identity(organizer_block, "organizer") if organizer_block else None

    attendees = [
        _participant_from_identity(a, a.get("role", "attendee"))
        for a in participants_block.get("attendees", [])
    ]

    all_participants = ([organizer] if organizer else []) + attendees
    return all_participants, organizer


def _parse_vtt(vtt_content: str, participants_by_name: dict[str, Participant]) -> list[Utterance]:
    """Parse WebVTT cue blocks (with <v Speaker> voice tags) into ordered Utterances."""
    utterances: list[Utterance] = []
    blocks = re.split(r"\r?\n\r?\n+", vtt_content.strip())
    sequence_index = 0

    for block in blocks:
        lines = [ln for ln in block.splitlines() if ln.strip()]
        if not lines or lines[0].strip().upper() == "WEBVTT":
            continue

        time_line = None
        text_lines: list[str] = []
        for line in lines:
            if _VTT_TIME_RE.search(line):
                time_line = line
            elif time_line is not None:
                text_lines.append(line)
            # lines before the time line (a numeric cue index) are ignored

        if time_line is None or not text_lines:
            continue

        m = _VTT_TIME_RE.search(time_line)
        start_ms = _vtt_timestamp_to_ms(*m.group(1, 2, 3, 4))
        end_ms = _vtt_timestamp_to_ms(*m.group(5, 6, 7, 8))

        raw_text = " ".join(text_lines).strip()
        speaker: Optional[Participant] = None
        voice_match = _VOICE_TAG_RE.match(raw_text)
        if voice_match:
            speaker_name = voice_match.group(1).strip()
            raw_text = voice_match.group(2).strip()
            speaker = participants_by_name.get(speaker_name.lower())
            if speaker is None:
                # Speaker mentioned in the transcript but absent from the participant
                # roster (e.g. a guest) — keep their name so masking/attribution still works.
                speaker = Participant(display_name=speaker_name, role="external")

        utterances.append(
            Utterance(
                speaker=speaker,
                text=raw_text,
                start_ms=start_ms,
                end_ms=end_ms,
                sequence_index=sequence_index,
            )
        )
        sequence_index += 1

    return utterances


def parse_transcript_payload(
    transcript_metadata: dict,
    vtt_content: str,
    meeting_metadata: dict,
) -> Transcript:
    """
    Parse the three raw Graph-shaped payloads into a Transcript domain model.

    Args:
        transcript_metadata: the transcript resource (id, meetingId, createdDateTime, ...)
        vtt_content: the WebVTT transcript content text
        meeting_metadata: the onlineMeeting resource (subject, start/end, participants, ...)
    """
    participants, organizer = _parse_participants(meeting_metadata)
    participants_by_name = {p.display_name.lower(): p for p in participants if p.display_name}

    utterances = _parse_vtt(vtt_content, participants_by_name)

    start_time = _parse_dt(meeting_metadata.get("startDateTime"))
    end_time = _parse_dt(meeting_metadata.get("endDateTime"))
    duration_seconds = None
    if start_time and end_time:
        duration_seconds = int((end_time - start_time).total_seconds())

    metadata = MeetingMetadata(
        meeting_id=transcript_metadata.get("meetingId") or meeting_metadata.get("id", ""),
        subject=meeting_metadata.get("subject"),
        organizer=organizer,
        start_time=start_time,
        end_time=end_time,
        duration_seconds=duration_seconds,
        join_url=meeting_metadata.get("joinWebUrl"),
        external_id=transcript_metadata.get("id"),
        raw_source={
            "transcript_metadata": transcript_metadata,
            "vtt_content": vtt_content,
            "meeting_metadata": meeting_metadata,
        },
    )

    return Transcript(metadata=metadata, participants=participants, utterances=utterances)
