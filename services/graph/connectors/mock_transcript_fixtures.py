"""
Realistic, Graph-shape-faithful fixtures for MockTranscriptProvider.

Each fixture bundles the three raw payloads transcript_payload_parser expects:
  - transcript_metadata: the transcript resource (id, meetingId, createdDateTime, ...)
  - meeting_metadata: the onlineMeeting resource (subject, start/end, participants, ...)
  - vtt_content: WebVTT transcript content with <v Speaker> voice tags

These are intentionally separate from phase2_validation/fixtures/transcripts.json,
which stores a flattened, simplified shape — these fixtures exist specifically to
exercise transcript_payload_parser the same way a live Graph response would, so
that swapping in GraphTranscriptProvider later requires no parser changes.

Scenario 4 (incident postmortem) deliberately contains a plaintext database
connection string, to exercise ThreatEngine.score_transcript()'s quarantine path.
"""

STANDUP = {
    "transcript_metadata": {
        "id": "transcript-eng-standup-001",
        "meetingId": "meeting-eng-standup-001",
        "createdDateTime": "2026-06-10T09:35:00Z",
        "meetingOrganizer": {"user": {"id": "aad-1001", "displayName": "Alice Chen"}},
    },
    "meeting_metadata": {
        "id": "meeting-eng-standup-001",
        "subject": "Engineering Daily Standup",
        "startDateTime": "2026-06-10T09:30:00Z",
        "endDateTime": "2026-06-10T09:45:00Z",
        "joinWebUrl": "https://teams.microsoft.com/l/meetup-join/eng-standup-001",
        "participants": {
            "organizer": {
                "identity": {"user": {"id": "aad-1001", "displayName": "Alice Chen"}},
                "upn": "alice.chen@contoso.com",
            },
            "attendees": [
                {
                    "identity": {"user": {"id": "aad-1002", "displayName": "Bob Singh"}},
                    "upn": "bob.singh@contoso.com",
                    "role": "attendee",
                },
                {
                    "identity": {"user": {"id": "aad-1003", "displayName": "Priya Patel"}},
                    "upn": "priya.patel@contoso.com",
                    "role": "attendee",
                },
            ],
        },
    },
    "vtt_content": """WEBVTT

00:00:01.000 --> 00:00:06.000
<v Alice Chen>Morning everyone, let's go round the table. Bob, how's the payments API PR looking?

00:00:06.500 --> 00:00:14.000
<v Bob Singh>I pushed the fix for PROJ-482, PR #342 on github.com/contoso/payments-api is ready for review. Alice, can you review it by tomorrow EOD?

00:00:14.500 --> 00:00:17.000
<v Alice Chen>Sure, I'll review PR #342 today.

00:00:17.500 --> 00:00:26.000
<v Priya Patel>I'm blocked on INFRA-77, the staging cluster doesn't have the new secret mounted yet. Can someone from infra confirm when that'll be done?

00:00:26.500 --> 00:00:33.000
<v Bob Singh>I'll follow up with infra and get back to you by Friday.

00:00:33.500 --> 00:00:41.000
<v Alice Chen>Great. Also, the design doc for the rate limiter is in SharePoint at https://contoso.sharepoint.com/sites/Eng/Shared%20Documents/RFC-042-rate-limiter.docx, please review before Thursday's design review.

00:00:41.500 --> 00:00:46.000
<v Priya Patel>Got it, I'll read it tonight and leave comments.
""",
}

INCIDENT_POSTMORTEM = {
    "transcript_metadata": {
        "id": "transcript-incident-pm-002",
        "meetingId": "meeting-incident-pm-002",
        "createdDateTime": "2026-06-12T16:05:00Z",
        "meetingOrganizer": {"user": {"id": "aad-2001", "displayName": "Daniel Kim"}},
    },
    "meeting_metadata": {
        "id": "meeting-incident-pm-002",
        "subject": "Incident Postmortem — Payments Outage",
        "startDateTime": "2026-06-12T16:00:00Z",
        "endDateTime": "2026-06-12T16:30:00Z",
        "joinWebUrl": "https://teams.microsoft.com/l/meetup-join/incident-pm-002",
        "participants": {
            "organizer": {
                "identity": {"user": {"id": "aad-2001", "displayName": "Daniel Kim"}},
                "upn": "daniel.kim@contoso.com",
            },
            "attendees": [
                {
                    "identity": {"user": {"id": "aad-2002", "displayName": "Elena Rossi"}},
                    "upn": "elena.rossi@contoso.com",
                    "role": "attendee",
                },
                {
                    "identity": {"user": {"id": "aad-2003", "displayName": "Frank Liu"}},
                    "upn": "frank.liu@contoso.com",
                    "role": "attendee",
                },
            ],
        },
    },
    "vtt_content": """WEBVTT

00:00:01.000 --> 00:00:08.000
<v Daniel Kim>Thanks for joining. Elena, can you walk us through the root cause of the payments outage?

00:00:08.500 --> 00:00:20.000
<v Elena Rossi>Sure. The deploy script had the old DB connection string hardcoded — postgres://admin:Sup3rSecret91@db-prod.internal:5432/payments — and it pointed at the wrong replica during failover.

00:00:20.500 --> 00:00:27.000
<v Frank Liu>That's a serious finding, we need to rotate that credential immediately, not just fix the config.

00:00:27.500 --> 00:00:34.000
<v Daniel Kim>Agreed, that's a blocker. Frank, can you own rotating the credential by end of day?

00:00:34.500 --> 00:00:37.000
<v Frank Liu>Yes, I'll have it rotated by EOD today.

00:00:37.500 --> 00:00:46.000
<v Elena Rossi>I've filed INFRA-88 to move the connection string into the vault instead of the deploy script. Azure DevOps work item is at dev.azure.com/contoso/Payments/_workitems/edit/9911.

00:00:46.500 --> 00:00:52.000
<v Daniel Kim>Good. What's our action item to prevent this from recurring?

00:00:52.500 --> 00:00:58.000
<v Elena Rossi>We should add a pre-deploy lint step that fails the build on any hardcoded connection string.
""",
}

SPRINT_PLANNING = {
    "transcript_metadata": {
        "id": "transcript-sprint-planning-003",
        "meetingId": "meeting-sprint-planning-003",
        "createdDateTime": "2026-06-15T10:05:00Z",
        "meetingOrganizer": {"user": {"id": "aad-3001", "displayName": "Grace Tan"}},
    },
    "meeting_metadata": {
        "id": "meeting-sprint-planning-003",
        "subject": "Sprint 42 Planning",
        "startDateTime": "2026-06-15T10:00:00Z",
        "endDateTime": "2026-06-15T11:00:00Z",
        "joinWebUrl": "https://teams.microsoft.com/l/meetup-join/sprint-planning-003",
        "participants": {
            "organizer": {
                "identity": {"user": {"id": "aad-3001", "displayName": "Grace Tan"}},
                "upn": "grace.tan@contoso.com",
            },
            "attendees": [
                {
                    "identity": {"user": {"id": "aad-3002", "displayName": "Henry Osei"}},
                    "upn": "henry.osei@contoso.com",
                    "role": "attendee",
                },
                {
                    "identity": {"user": {"id": "aad-3003", "displayName": "Isabel Cruz"}},
                    "upn": "isabel.cruz@contoso.com",
                    "role": "attendee",
                },
            ],
        },
    },
    "vtt_content": """WEBVTT

00:00:01.000 --> 00:00:09.000
<v Grace Tan>Let's plan Sprint 42. Henry, what's the status of PROJ-510, the OneDrive export feature?

00:00:09.500 --> 00:00:18.000
<v Henry Osei>It's mostly done, PR #355 is open on github.com/contoso/export-service. I think it needs one more reviewer.

00:00:18.500 --> 00:00:22.000
<v Isabel Cruz>I can review PR #355 this afternoon.

00:00:22.500 --> 00:00:31.000
<v Grace Tan>Thanks Isabel. Henry, can you commit to merging it by Wednesday so QA has time before the release?

00:00:31.500 --> 00:00:34.000
<v Henry Osei>Yes, I'll merge it by Wednesday.

00:00:34.500 --> 00:00:42.000
<v Isabel Cruz>Quick question — does the export feature also need to handle SharePoint-hosted files, or just OneDrive personal?

00:00:42.500 --> 00:00:46.000
<v Grace Tan>Good question, let me check with the client and get back to you.

00:00:46.500 --> 00:00:54.000
<v Henry Osei>Also flagging INFRA-90 as a risk — the export service might hit rate limits on Graph's OneDrive API under load.
""",
}

CLIENT_ESCALATION = {
    "transcript_metadata": {
        "id": "transcript-client-escalation-004",
        "meetingId": "meeting-client-escalation-004",
        "createdDateTime": "2026-06-17T14:05:00Z",
        "meetingOrganizer": {"user": {"id": "aad-4001", "displayName": "Jack Romero"}},
    },
    "meeting_metadata": {
        "id": "meeting-client-escalation-004",
        "subject": "Client Escalation — Acme Corp SLA Review",
        "startDateTime": "2026-06-17T14:00:00Z",
        "endDateTime": "2026-06-17T14:30:00Z",
        "joinWebUrl": "https://teams.microsoft.com/l/meetup-join/client-escalation-004",
        "participants": {
            "organizer": {
                "identity": {"user": {"id": "aad-4001", "displayName": "Jack Romero"}},
                "upn": "jack.romero@contoso.com",
            },
            "attendees": [
                {
                    "identity": {"user": {"id": "aad-4002", "displayName": "Karen Yu"}},
                    "upn": "karen.yu@contoso.com",
                    "role": "attendee",
                },
                # Liam Walsh is an external client participant deliberately NOT
                # listed in the attendee roster, to exercise the parser's
                # fallback path for speakers absent from the participant list.
            ],
        },
    },
    "vtt_content": """WEBVTT

00:00:01.000 --> 00:00:10.000
<v Jack Romero>Thanks for joining, Liam. We wanted to address the SLA breach from last week directly.

00:00:10.500 --> 00:00:20.000
<v Liam Walsh>Appreciate the call. Our main concern is the uptime dip on the 9th — can you confirm what caused it and when the fix lands?

00:00:20.500 --> 00:00:28.000
<v Karen Yu>That was tied to PROJ-499, a connection pool exhaustion bug. The fix is in PR #361, currently in review.

00:00:28.500 --> 00:00:33.000
<v Jack Romero>Karen, can you commit to getting PR #361 merged and deployed by Friday?

00:00:33.500 --> 00:00:36.000
<v Karen Yu>Yes, I'll have it deployed by Friday.

00:00:36.500 --> 00:00:44.000
<v Liam Walsh>That works. Can you also send over the updated SLA document? I believe it's the one shared on SharePoint last month.

00:00:44.500 --> 00:00:48.000
<v Jack Romero>I'll send the updated SLA doc over by end of day.
""",
}

# Demo scenario for the Follow-up Center: the AI must draft a follow-up TO
# caretaker.user@amperatech.ai (a commitment to email the client about a delay →
# email_draft, and an action to Teams-message the client tech lead → teams draft).
# No raw secret is pasted (the API key is "stored in the vault, reference by
# name") so the transcript is not quarantined and drafts are produced; the
# encrypt→decrypt demo runs on the vaulted recipient identity at send time.
CLIENT_DELAY = {
    "transcript_metadata": {
        "id": "transcript-client-delay-005",
        "meetingId": "meeting-client-delay-005",
        "createdDateTime": "2026-06-19T16:35:00Z",
        "meetingOrganizer": {"user": {"id": "aad-5001", "displayName": "Sarah Manager"}},
    },
    "meeting_metadata": {
        "id": "meeting-client-delay-005",
        "subject": "Apex Integration — Client Delay Comms",
        "startDateTime": "2026-06-19T16:30:00Z",
        "endDateTime": "2026-06-19T16:50:00Z",
        "joinWebUrl": "https://teams.microsoft.com/l/meetup-join/client-delay-005",
        "participants": {
            "organizer": {
                "identity": {"user": {"id": "aad-5001", "displayName": "Sarah Manager"}},
                "upn": "sarah.manager@amperatech.ai",
            },
            "attendees": [
                {
                    "identity": {"user": {"id": "aad-5002", "displayName": "Caretaker User"}},
                    "upn": "caretaker.user@amperatech.ai",
                    "role": "attendee",
                },
            ],
        },
    },
    "vtt_content": """WEBVTT

00:00:01.000 --> 00:00:08.000
<v Sarah Manager>Quick sync on the Apex client integration. We're going to miss Friday's delivery date, so we need to proactively inform the client about the delay.

00:00:08.500 --> 00:00:16.000
<v Caretaker User>Agreed. I'll email the client today to inform them about the delay and share the revised timeline.

00:00:16.500 --> 00:00:24.000
<v Sarah Manager>Thanks. Caretaker User, can you also message the client's technical lead on Teams to confirm they received the rotated staging API key?

00:00:24.500 --> 00:00:30.000
<v Caretaker User>Sure, I'll ping the tech lead on Teams once the client email goes out.

00:00:30.500 --> 00:00:38.000
<v Sarah Manager>Perfect. The new staging API key has already been rotated and stored securely in the vault — reference it by name, do not paste the value anywhere.
""",
}

# Full end-to-end production test scenario (Plan 3): a ~12-min API-integration
# meeting. Organizer care.taker@amperatech.ai, implementer/requester
# caretaker.user@amperatech.ai (the owner of the follow-up items, so drafts are
# addressed to them), plus Maya Reddy who picks up a separate task. Contains:
# API references (key by name only — not pasted, so no quarantine), docs/base-URL
# links, auth flow, env vars, decisions, multiple deadlines, a self-reminder, a
# task to a different owner, and one deliberately HEDGED item meant to land in
# "Clarification Needed" (low extraction confidence → below the retrieval floor).
API_INTEGRATION = {
    "transcript_metadata": {
        "id": "transcript-api-integration-006",
        "meetingId": "meeting-api-integration-006",
        "createdDateTime": "2026-06-19T18:05:00Z",
        "meetingOrganizer": {"user": {"id": "aad-6001", "displayName": "Care Taker"}},
    },
    "meeting_metadata": {
        "id": "meeting-api-integration-006",
        "subject": "Payments API Integration — Kickoff",
        "startDateTime": "2026-06-19T18:00:00Z",
        "endDateTime": "2026-06-19T18:14:00Z",
        "joinWebUrl": "https://teams.microsoft.com/l/meetup-join/api-integration-006",
        "participants": {
            "organizer": {
                "identity": {"user": {"id": "aad-6001", "displayName": "Care Taker"}},
                "upn": "care.taker@amperatech.ai",
            },
            "attendees": [
                {
                    "identity": {"user": {"id": "aad-6002", "displayName": "Caretaker User"}},
                    "upn": "caretaker.user@amperatech.ai",
                    "role": "attendee",
                },
                {
                    "identity": {"user": {"id": "aad-6003", "displayName": "Maya Reddy"}},
                    "upn": "maya.reddy@amperatech.ai",
                    "role": "attendee",
                },
            ],
        },
    },
    "vtt_content": """WEBVTT

00:00:01.000 --> 00:00:09.000
<v Care Taker>Thanks everyone. Goal today is to kick off the Payments API integration. Caretaker User, you'll own the client-side integration work this sprint.

00:00:09.500 --> 00:00:19.000
<v Caretaker User>Sounds good. To get started I'll need the full API package — the API keys, the API documentation, the authentication flow, the base URLs, and the environment variables for staging and prod.

00:00:19.500 --> 00:00:27.000
<v Care Taker>Understood. The docs are at https://contoso.sharepoint.com/sites/Payments/api-guide.docx and the staging base URL is api-staging.payments.internal. Auth is OAuth2 client-credentials.

00:00:27.500 --> 00:00:35.000
<v Care Taker>The staging API key was rotated this morning and is in the vault — I'll send you the full package after this meeting, I won't paste it here.

00:00:35.500 --> 00:00:42.000
<v Caretaker User>Perfect. I'll complete the client integration and have it ready for review by Thursday.

00:00:42.500 --> 00:00:50.000
<v Care Taker>Great. Decision: we're standardizing on OAuth2 client-credentials for all service-to-service calls, not API-key headers, going forward.

00:00:50.500 --> 00:00:58.000
<v Care Taker>Maya, can you set up the staging environment variables and the secrets mounts so Caretaker User isn't blocked? Let's target Wednesday for that.

00:00:58.500 --> 00:01:03.000
<v Maya Reddy>Yes, I'll have the staging env vars and secret mounts ready by Wednesday.

00:01:03.500 --> 00:01:11.000
<v Caretaker User>One thing — I think we might also need to involve the security team for a review before we go to prod, but I'm not totally sure that's required. Can we confirm that later?

00:01:11.500 --> 00:01:18.000
<v Care Taker>Good question, let's confirm whether a security review is mandatory before prod — I'm not certain either. Caretaker User, remind me to send you the API package before Thursday.

00:01:18.500 --> 00:01:24.000
<v Caretaker User>Will do. I'll also message the team channel once the integration is merged so QA can pick it up.
""",
}

ALL_FIXTURES = [STANDUP, INCIDENT_POSTMORTEM, SPRINT_PLANNING, CLIENT_ESCALATION, CLIENT_DELAY, API_INTEGRATION]
