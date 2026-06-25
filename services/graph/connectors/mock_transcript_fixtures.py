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

# Demo scenario for the Follow-up Center: keep this one deliberately simple so
# the extractor produces exactly three clean draftable commitments and nothing
# secret-related. The goal is to exercise email/task/Teams draft generation with
# obvious, non-ambiguous wording.
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

# Full production-readiness stress scenario (Plans 1/2/3): a ~15-min Follow-up
# Center + Payments API integration review between organizer care.taker@ and
# participant caretaker.user@. Deliberately hard: router/middleware/auth bugs,
# an API-package email request, a Teams follow-up request, a self-reminder, an
# Ollama→Azure OpenAI provider SUPERSESSION, duplicate-knowledge restatements
# (403 handling, FC grouping), 4 HEDGED clarification items (provider migration,
# deploy date, popup-vs-panel, security review), and non-actionable banter. No
# secret VALUE is ever pasted (keys/secret are vault-by-reference) so the
# transcript is NOT quarantined and drafts are produced.
PROD_READINESS = {
    "transcript_metadata": {
        "id": "transcript-prod-readiness-007",
        "meetingId": "meeting-prod-readiness-007",
        "createdDateTime": "2026-06-20T15:20:00Z",
        "meetingOrganizer": {"user": {"id": "aad-7001", "displayName": "Care Taker"}},
    },
    "meeting_metadata": {
        "id": "meeting-prod-readiness-007",
        "subject": "Follow-up Center & Payments API Integration — Production Readiness Review",
        "startDateTime": "2026-06-20T15:00:00Z",
        "endDateTime": "2026-06-20T15:18:00Z",
        "joinWebUrl": "https://teams.microsoft.com/l/meetup-join/prod-readiness-007",
        "participants": {
            "organizer": {
                "identity": {"user": {"id": "aad-7001", "displayName": "Care Taker"}},
                "upn": "care.taker@amperatech.ai",
            },
            "attendees": [
                {
                    "identity": {"user": {"id": "aad-7002", "displayName": "Caretaker User"}},
                    "upn": "caretaker.user@amperatech.ai",
                    "role": "attendee",
                },
            ],
        },
    },
    "vtt_content": """WEBVTT

00:00:01.000 --> 00:00:12.000
<v Care Taker>Thanks for jumping on. This is the production-readiness review for the Follow-up Center — Plan 3. Last week we got draft generation working end to end on the mock transcripts; today I want to close out the API integration bugs and agree what actually blocks the production deployment.

00:00:12.500 --> 00:00:24.000
<v Caretaker User>Sounds good. Before anything else — the router's still broken. The endpoint at /drafts/generate is returning a 404 in staging. I'm fairly sure the drafts router never got wired into the app at all.

00:00:24.500 --> 00:00:34.000
<v Care Taker>Right, the include_router call. Can you check app.py? The router's defined in the drafts module but the include_router line is either missing or it's got the wrong prefix — that'd explain the 404.

00:00:34.500 --> 00:00:45.000
<v Caretaker User>Yeah. I'll fix that today. I'll add the include_router for the drafts router with the X-API-Key dependency, same wiring as the other routers, and confirm the prefix is /drafts and not /draft.

00:00:45.500 --> 00:00:55.000
<v Care Taker>Good. And while you're in there — the Swagger docs aren't showing the new draft endpoints at all. Once the router registers they should appear under /docs automatically, but verify it actually shows up.

00:00:55.500 --> 00:01:08.000
<v Caretaker User>Will do, I'll verify the Swagger docs. There's a related one though — middleware ordering. The X-API-Key auth middleware is running after the CORS middleware, and on a couple of routes the auth check looks like it's being skipped. Can you take the middleware ordering? It's subtle.

00:01:08.500 --> 00:01:22.000
<v Care Taker>That one's mine then. I'll verify the middleware ordering by end of day. The rule is auth has to run before routing, and X-API-Key gets enforced on every route. If CORS short-circuits an OPTIONS preflight before auth, fine — but nothing else bypasses auth, ever.

00:01:22.500 --> 00:01:29.000
<v Caretaker User>Agreed. Write that down as a hard rule — auth middleware before routing, always. That's a standing convention now.

00:01:29.500 --> 00:01:46.000
<v Caretaker User>OK, the bigger blocker for me — I can't integrate against the Payments API until I have the full package. I need the API keys, the API documentation, the environment variables for staging and prod, an authentication guide, the base URLs, and a couple of sample payloads. Can you send all of that over to my Outlook?

00:01:46.500 --> 00:02:01.000
<v Care Taker>Yep. The docs are on SharePoint at https://contoso.sharepoint.com/sites/Payments/api-guide.docx. Staging base URL is api-staging.payments.internal, prod is api.payments.amperatech.ai. Auth is OAuth2 client-credentials.

00:02:01.500 --> 00:02:04.000
<v Caretaker User>And the client secret?

00:02:04.500 --> 00:02:16.000
<v Care Taker>The OAuth client secret is in the vault — I'll reference it by name, I'm not pasting it here. Same with the API key, it was rotated this morning and it's vaulted. Never paste a secret value into a transcript or a log line.

00:02:16.500 --> 00:02:24.000
<v Caretaker User>That's the right call — if a raw key lands in the transcript the threat engine quarantines the whole thing and we get no drafts. Reference by name only.

00:02:24.500 --> 00:02:33.000
<v Care Taker>Exactly. So I'll send you the full API package by EOD today —

00:02:33.500 --> 00:02:42.000
<v Care Taker>— actually, no. Realistically before Friday. I need to sanitize the sample payloads first. I'll send that before Friday.

00:02:42.500 --> 00:02:50.000
<v Caretaker User>Before Friday works, but I'm blocked without at least the payloads. Can the sample payloads come sooner?

00:02:50.500 --> 00:02:59.000
<v Care Taker>Yes — the sample payloads I'll send before tomorrow morning. The rest of the package before Friday. And what's the callback URL for the OAuth flow, you'll need that.

00:02:59.500 --> 00:03:07.000
<v Caretaker User>Right, the redirect URI. What is it?

00:03:07.500 --> 00:03:15.000
<v Care Taker>The registered callback URL is https://app.amperatech.ai/oauth/callback. It's already in the Azure app registration.

00:03:15.500 --> 00:03:23.000
<v Care Taker>Caretaker User, remind me to send you the API package before Friday — I don't want it slipping.

00:03:23.500 --> 00:03:27.000
<v Caretaker User>Done, I'll set that reminder on my side too.

00:03:27.500 --> 00:03:38.000
<v Care Taker>And let's make this a decision: we standardize on OAuth2 client-credentials for all service-to-service calls going forward. No more API-key headers for internal services.

00:03:38.500 --> 00:03:46.000
<v Caretaker User>Agreed — OAuth2 client-credentials for everything service-to-service. API-key headers only for the external dashboard.

00:03:46.500 --> 00:03:58.000
<v Care Taker>OK, draft generation. Decision: the default draft-generation provider is Ollama llama3.2. Locking that in for the current build.

00:03:58.500 --> 00:04:09.000
<v Caretaker User>Mmm. The thing is Ollama's latency on that box is killing us, and the JSON adherence is worse — we're getting more parse errors and the confidence scores come back noisier.

00:04:09.500 --> 00:04:22.000
<v Care Taker>That data is convincing. Updated decision: the default draft-generation provider is Azure OpenAI gpt-5-nano. That replaces Ollama as the default draft-generation provider from here on.

00:04:22.500 --> 00:04:31.000
<v Caretaker User>Agreed — the default draft-generation provider is Azure OpenAI gpt-5-nano now, replacing Ollama. I'll make the call-with-fallback order reflect that.

00:04:31.500 --> 00:04:41.000
<v Care Taker>And on output quality — every draft has to carry citations and a confidence score, no exceptions. That's the anti-hallucination backstop.

00:04:41.500 --> 00:04:50.000
<v Caretaker User>Right, and anything below the confidence floor becomes a clarification item, not a send. The floor's 0.5, correct?

00:04:50.500 --> 00:04:59.000
<v Care Taker>0.5. Below that, retrieval returns insufficient confidence and we create a clarification_needed action instead of ever calling the LLM.

00:04:59.500 --> 00:05:13.000
<v Caretaker User>OK, Graph. We're getting 403s on the sendMail and on the Teams chat creation in staging. The app registration is probably missing Mail.Send or the chat message permission. Right now a 403 just bubbles up as a generic failure.

00:05:13.500 --> 00:05:28.000
<v Care Taker>Yeah, Graph permission failures need handling. Can you improve the Graph failure handling? Map 403 to permission_denied, 404 to not_found, 429 to rate_limited, and surface them in the Failed group of the Follow-up Center. Before deployment.

00:05:28.500 --> 00:05:41.000
<v Caretaker User>I'll take that. So to be explicit — when Graph throws a 403 we never silently drop the draft. We mark the action failed with a specific reason and show it in the Failed group.

00:05:41.500 --> 00:05:45.000
<v Care Taker>Exactly that. Never silently dropped.

00:05:45.500 --> 00:05:57.000
<v Care Taker>Next — the duplicate reminder problem. On the Apex meeting we generated two reminder drafts for the same commitment. Something in draft generation isn't deduping properly.

00:05:57.500 --> 00:06:11.000
<v Caretaker User>I saw that. I think the dedup guard keys on the knowledge item id, but the same commitment got extracted as two knowledge items with different keys, so the guard never matched. Can you investigate the duplicate reminder generation?

00:06:11.500 --> 00:06:22.000
<v Caretaker User>Actually — give it to me, I know the extraction path. I'll investigate it. I'll check whether it's the extractor producing duplicates or the dedup guard missing them. Before deployment.

00:06:22.500 --> 00:06:35.000
<v Care Taker>Good. This ties into knowledge versioning, too. When we superseded that provider decision just now — Ollama to Azure — the old decision becomes stale. We cannot have a draft citing a superseded knowledge item and getting sent anyway.

00:06:35.500 --> 00:06:48.000
<v Caretaker User>Right, the stale-citation guard. At approve and at execute, we re-check that every cited knowledge item is still active. If it's been superseded, we surface stale or conflict instead of sending.

00:06:48.500 --> 00:06:59.000
<v Care Taker>Yes. That one's mine — I wrote the retrieval gate, so I'll add the stale-knowledge validation at approve and execute. Before deployment.

00:06:59.500 --> 00:07:14.000
<v Caretaker User>Good. And on duplicate detection generally — if two knowledge items refer to the same thing in different words, the classifier should collapse them to one knowledge key. Like the way we just said "handle the 403" and "don't silently drop the permission failure" — that's the same knowledge, not two.

00:07:14.500 --> 00:07:23.000
<v Care Taker>Right, the adjudicator should mark those as duplicate, not conflict. Conflict is only when the values actually disagree.

00:07:23.500 --> 00:07:34.000
<v Caretaker User>Let's lock the Follow-up Center popup grouping while we're here. Right now it groups drafts by meeting, then by status, then by draft type. I want that locked in.

00:07:34.500 --> 00:07:49.000
<v Care Taker>That grouping is correct — meeting first, then the status group in order: Pending, Clarification, Failed, Executed, Dismissed, then the individual draft cards. Can you update the Follow-up Center popup so the grouping is exactly that? Some of the status groups render out of order right now.

00:07:49.500 --> 00:08:05.000
<v Caretaker User>I'll update the Follow-up Center popup. Quick question, and I keep going back and forth on this — should the Follow-up Center really be a PySide6 desktop popup, or should it just be a panel in the dashboard? I'm genuinely torn. The popup is more in-your-face, which is good for action items, but the panel's easier to maintain.

00:08:05.500 --> 00:08:17.000
<v Care Taker>Honestly I'm not sure either. Good arguments both ways. Let's not decide the popup-versus-panel question today — let's revisit the architecture next week when we've slept on it.

00:08:17.500 --> 00:08:31.000
<v Care Taker>The approval workflow itself is settled, though. Drafts stay masked until execution, nothing decrypts until the action reaches approved status, and decryption only ever happens inside the execute path. That's a hard security invariant.

00:08:31.500 --> 00:08:42.000
<v Caretaker User>Agreed. And Approve All only ever sweeps pending draft actions — it never touches clarification_needed items. Those always need an explicit human decision.

00:08:42.500 --> 00:08:51.000
<v Care Taker>Correct. Write that down — Approve All never auto-approves clarification items.

00:08:51.500 --> 00:09:02.000
<v Care Taker>And don't touch the Meeting Prep popup architecture in any of this. That popup is deterministic, plaintext, no LLM — it fires before a meeting. The Follow-up Center is the opposite: masked, async, post-meeting. Keep those two architectures completely separate.

00:09:02.500 --> 00:09:08.000
<v Caretaker User>Understood — separate pipelines, the prep popup never sees a vault token and never calls the LLM.

00:09:08.500 --> 00:09:18.000
<v Caretaker User>You know, it's kind of wild how far this came. The original follow-up thing was that ugly Tkinter popup we threw together in like a day.

00:09:18.500 --> 00:09:27.000
<v Care Taker>Ha, yeah. The Copilot-style right-docked panel looks so much better. I always thought the Tkinter one looked like it was from 2005.

00:09:27.500 --> 00:09:31.000
<v Caretaker User>Totally. Anyway — back on track.

00:09:31.500 --> 00:09:45.000
<v Care Taker>Production readiness. Logging and the audit trail — every draft generation, every gate decision, every approve, dismiss, execute, and failure has to write an audit event. That's wired already, but I want it confirmed running in prod.

00:09:45.500 --> 00:09:58.000
<v Caretaker User>The audit trail's solid. DRAFT_GATE_DECISION fires on every candidate, even the proceeds. DRAFT_GENERATED, DRAFT_FAILED, ACTION_APPROVED, ACTION_DISMISSED, ACTION_EXECUTED, ACTION_FAILED — all there. Plus the UNMASK on decrypt, one-to-one with the agent action id.

00:09:58.500 --> 00:10:10.000
<v Care Taker>Good. For monitoring we'll wire the logs into the existing monitoring stack. I'll review the logs after the first deployment to make sure nothing's leaking plaintext into them.

00:10:10.500 --> 00:10:18.000
<v Caretaker User>Important, because if a recipient email shows up unmasked in a log line, that's a security incident, full stop.

00:10:18.500 --> 00:10:25.000
<v Care Taker>Agreed. And I'll update the API documentation once the endpoints are stable, so the package I send you stays accurate.

00:10:25.500 --> 00:10:33.000
<v Care Taker>So — production deployment. When are we actually shipping this?

00:10:33.500 --> 00:10:44.000
<v Caretaker User>That's the open question. I was thinking next sprint, but it really depends on whether the security review is mandatory before prod.

00:10:44.500 --> 00:10:59.000
<v Care Taker>Right, and I'm honestly not certain a formal security review is required here, since it's all additive and masked. Can we confirm whether the security review is mandatory before prod? I don't want to commit to a date until we actually know.

00:10:59.500 --> 00:11:10.000
<v Caretaker User>Yeah, let's not pin the deployment date until that's confirmed. Maybe next sprint, maybe sooner — too many unknowns right now to commit.

00:11:10.500 --> 00:11:23.000
<v Care Taker>Agreed, leave the deployment date open. But once we do deploy — can you verify the deployment? Smoke-test the generate endpoint, the counts endpoint, and confirm the popup actually launches from the daemon.

00:11:23.500 --> 00:11:33.000
<v Caretaker User>I'll verify the deployment when it goes out. Though that one's dependent — I can't verify the deploy until the router fix lands and you've sent the package.

00:11:33.500 --> 00:11:46.000
<v Care Taker>Fair, it's a dependent task. One more architectural item — the transcript provider migration. We've got the Graph transcript provider written but dormant. Do we flip the provider to graph for this deployment?

00:11:46.500 --> 00:11:59.000
<v Caretaker User>I really don't think we should flip to the Graph transcript provider yet. We still don't have reliable transcript API access on the tenant, and the mock is what all our validation runs against.

00:11:59.500 --> 00:12:12.000
<v Care Taker>Yeah, I'm not sure either. Let's not decide the provider migration now — keep it on mock, and we'll revisit graph once we've got tenant transcript access sorted out.

00:12:12.500 --> 00:12:17.000
<v Caretaker User>Agreed — mock for now, that decision's deferred.

00:12:17.500 --> 00:12:31.000
<v Care Taker>Just for the record on the retrieval scoring — the confidence number is a weighted blend: forty percent extraction confidence, forty percent retrieval match, twenty percent recency, with a thirty-day half-life on the recency term. So nobody's confused where those numbers come from.

00:12:31.500 --> 00:12:44.000
<v Caretaker User>Yeah, and the vector search is just in-memory cosine similarity over the MiniLM embeddings — no vector database. People keep asking if we need Pinecone. We don't, not at this scale.

00:12:44.500 --> 00:12:48.000
<v Care Taker>Right, not worth the operational overhead for now.

00:12:48.500 --> 00:13:02.000
<v Caretaker User>OK, I think that's most of it. Can you send me a Teams follow-up after this meeting summarizing the action items, the deadlines, the implementation status, and the next steps? I want it in writing so I can track my side of it.

00:13:02.500 --> 00:13:12.000
<v Care Taker>Sure, I'll send you a Teams summary right after — action items, deadlines, status, and next steps. You'll have it in the chat.

00:13:12.500 --> 00:13:20.000
<v Caretaker User>Perfect. And I'll message the team channel once the integration's merged, so QA can pick it up for the regression cycle.

00:13:20.500 --> 00:13:30.000
<v Care Taker>Good — and note that cycle: QA's regression run starts Monday, so the integration has to be merged before Monday regardless of the prod date.

00:13:30.500 --> 00:13:44.000
<v Care Taker>So, recap the deadlines: router fix today, sample payloads before tomorrow morning, full API package before Friday, middleware verification by EOD, merge before Monday for QA, and everything else targeted next sprint pending the security-review answer.

00:13:44.500 --> 00:13:54.000
<v Caretaker User>Got it. And the duplicate-reminder investigation, the Graph failure handling, and the stale-knowledge validation — all of those I'll have before the deployment, whenever it lands.

00:13:54.500 --> 00:13:58.000
<v Care Taker>Perfect. Before deployment for all three. Thanks, both.

00:13:58.500 --> 00:14:03.000
<v Caretaker User>Thanks. I'll go fix that router right now.
""",
}


DATABASE_MIGRATION = {
    "transcript_metadata": {
        "id": "MSoxNjg3NTQzMjEwMDAwKjE5OmFiYzEyM2RlZjQ1NjdnaGk4OTBqa2wxMjNtbm9AYW1wZXJhdGVjaC5haQ==",
        "meetingId": "MSoxNjg3NTQzMjEwMDAwKjE5OmFiYzEyM2RlZjQ1NjdnaGk4OTBqa2wxMjNtbm9AYW1wZXJhdGVjaC5haQ==",
        "createdDateTime": "2025-01-21T10:03:47.812Z",
        "transcriptContentUrl": "https://graph.microsoft.com/v1.0/me/onlineMeetings/MSoxNjg3NTQzMjEwMDAwKjE5OmFiYzEyM2RlZjQ1NjdnaGk4OTBqa2wxMjNtbm9AYW1wZXJhdGVjaC5haQ==/transcripts/MSoxNjg3NTQzMjEwMDAwKjE5OmFiYzEyM2RlZjQ1NjdnaGk4OTBqa2wxMjNtbm9AYW1wZXJhdGVjaC5haQ==/content",
    },
    "meeting_metadata": {
        "id": "MSoxNjg3NTQzMjEwMDAwKjE5OmFiYzEyM2RlZjQ1NjdnaGk4OTBqa2wxMjNtbm9AYW1wZXJhdGVjaC5haQ==",
        "subject": "Zero-Downtime PostgreSQL Production Migration",
        "startDateTime": "2025-01-21T10:00:00.000Z",
        "endDateTime": "2025-01-21T10:14:32.000Z",
        "joinWebUrl": "https://teams.microsoft.com/l/meetup-join/19%3Aabc123def4567ghi890jkl123mno%40thread.tacv2/1737453600000?context=%7B%22Tid%22%3A%22f8a3b2c1-4d5e-6f7a-8b9c-0d1e2f3a4b5c%22%2C%22Oid%22%3A%22a1b2c3d4-e5f6-7890-abcd-ef1234567890%22%7D",
        "organizer": {
            "displayName": "Care Taker",
            "upn": "care.taker@amperatech.ai",
            "id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
            "tenantId": "f8a3b2c1-4d5e-6f7a-8b9c-0d1e2f3a4b5c",
        },
        "attendees": [
            {
                "displayName": "Caretaker User",
                "upn": "caretaker.user@amperatech.ai",
                "id": "b2c3d4e5-f6a7-8901-bcde-f12345678901",
                "tenantId": "f8a3b2c1-4d5e-6f7a-8b9c-0d1e2f3a4b5c",
                "role": "attendee",
            }
        ],
    },
    "vtt_content": """WEBVTT

00:00:01.000 --> 00:00:09.000
<v Care Taker>Alright, let's get started. So the main thing I want to cover today is the PostgreSQL 13 to 16 migration. We're targeting zero downtime, and I need us to walk through the plan end to end so we're aligned before the migration window opens Thursday night.

00:00:09.500 --> 00:00:18.000
<v Caretaker User>Yeah — I looked over the approach. Quick callout: I had pg_dump penciled in as a fallback, but given the dataset and the uptime constraints, that won't fly.

00:00:18.500 --> 00:00:27.000
<v Care Taker>Same here. Let's drop pg_dump and do logical replication. It buys us a parallel run and a much cleaner cutover path.

00:00:27.500 --> 00:00:38.000
<v Caretaker User>Agreed. With replication up we can let the replica catch up, watch lag, and then flip traffic when it's steady — avoids a long maintenance window.

00:00:38.500 --> 00:00:50.000
<v Care Taker>Also, keep the old cluster around for a bit after the flip — I'd like it available for a couple of days so rollback isn't a scramble if something odd shows up.

00:00:50.500 --> 00:01:02.000
<v Caretaker User>Yep. I'll bake that into the rollback flow. I can have a rollback script tested and ready by tomorrow morning: repoint connections, drain PgBouncer, and swap the K8s secret back to the old host.

00:01:02.500 --> 00:01:12.000
<v Care Taker>Good — and please actually run the rollback end-to-end in staging. Don't want to find out it fails during the window.

00:01:12.500 --> 00:01:22.000
<v Caretaker User>I'll run it in staging today and report any issues by EOD.

00:01:22.500 --> 00:01:35.000
<v Care Taker>On schema migrations — which tool are we standardizing on? Last time I heard there was some back-and-forth.

00:01:35.500 --> 00:01:50.000
<v Caretaker User>We decided on Flyway. It plugs into our CI and we already have versioned scripts partially written. Liquibase would have been more work. Also starting a schema freeze today so nothing drifts during the window.

00:01:50.500 --> 00:01:58.000
<v Care Taker>Okay — I'll broadcast the freeze to engineering so no schema changes land until we greenlight post-cutover.

00:01:58.500 --> 00:02:10.000
<v Caretaker User>One other check: ORM compatibility. A couple services use SQLAlchemy 1.4 and connection URL handling changed between the drivers. I want to avoid driver-level errors after cutover.

00:02:10.500 --> 00:02:20.000
<v Care Taker>Make that a priority. I need confirmation before Wednesday so we can patch if necessary.

00:02:20.500 --> 00:02:32.000
<v Caretaker User>I'll run compatibility checks against the primary and the replica endpoints and document results. Expect that by Wednesday morning.

00:02:32.500 --> 00:02:45.000
<v Care Taker>PgBouncer — we're on transaction mode today. Anything to watch for when we point to the new cluster?

00:02:45.500 --> 00:03:05.000
<v Caretaker User>A few things: PG16 tweaks auth, so verify pg_hba.conf and PgBouncer's auth_type. Also re-evaluate pool sizes against the new cluster's max_connections. I'll audit PgBouncer config and confirm by Wednesday.

00:03:05.500 --> 00:03:15.000
<v Care Taker>Pool sizing is critical — misconfigured pooling could saturate the new DB quickly. Call out any issues you find.

00:03:15.500 --> 00:03:28.000
<v Caretaker User>Will do. Related: credentials live in Vault at db/prod/postgres. I won't change secret values, but I'll update the K8s manifests so the Vault agent sidecar injects the new hostname correctly.

00:03:28.500 --> 00:03:40.000
<v Care Taker>Right — you can't meaningfully validate prod until those manifests and Vault references are updated and confirmed. Don't try to validate production without that.

00:03:40.500 --> 00:03:52.000
<v Caretaker User>Understood. I'll update manifests and verify Vault injection by Wednesday so the cutover on Thursday isn't blocked.

00:03:52.500 --> 00:04:05.000
<v Care Taker>We're doing blue/green for deployment, yes?

00:04:05.500 --> 00:04:22.000
<v Caretaker User>Yes. Blue is current PG13 cluster, green will be PG16. We'll spin up green via Helm — need to verify chart values (resource requests, persistence changes) before we bring it up in prod.

00:04:22.500 --> 00:04:35.000
<v Care Taker>Validate the Helm deploy in staging first. Helm templating errors during the window would be painful.

00:04:35.500 --> 00:04:50.000
<v Caretaker User>Already on it. Helm deployment will be verified in staging by tomorrow. K8s manifests for green will be updated and reviewed before Thursday too.

00:04:50.500 --> 00:05:05.000
<v Care Taker>How are we monitoring replication lag? I want visibility before we flip traffic.

00:05:05.500 --> 00:05:22.000
<v Caretaker User>Prometheus is polling pg_stat_replication. We're targeting lag < 2 seconds as our readiness threshold. I'll validate the alert fires correctly in staging today.

00:05:22.500 --> 00:05:32.000
<v Care Taker>And Grafana — do we have a single migration dashboard?

00:05:32.500 --> 00:05:48.000
<v Caretaker User>There's a PostgreSQL dashboard but it lacks replication lag and connection pool panels. I'll add replication lag, pool utilization, and query latency so we have one screen during the cutover. That'll be done before Thursday.

00:05:48.500 --> 00:06:02.000
<v Care Taker>I want replication lag, pool utilization, and query latency visible together during the window. Make sure it's ready.

00:06:02.500 --> 00:06:12.000
<v Caretaker User>I'll have that ready by Wednesday evening.

00:06:12.500 --> 00:06:28.000
<v Care Taker>Okay, walk me through the cutover once more.

00:06:28.500 --> 00:06:55.000
<v Caretaker User>Plan is: establish logical replication, wait for the green cluster to catch up, then route read-only traffic to the new cluster first and validate. If reads look fine we flip the primary connection string for writes. Validating reads before writes gives us a safe intermediate state — if reads fail, we stop and investigate.

00:06:55.500 --> 00:07:08.000
<v Care Taker>Good. Read-only validation first — include that as a hard item in the checklist.

00:07:08.500 --> 00:07:22.000
<v Caretaker User>I'm building the full migration checklist covering pre-cutover checks, cutover steps, post-cutover verifications, and rollback triggers. I'll have a draft for you to review by tomorrow morning.

00:07:22.500 --> 00:07:35.000
<v Care Taker>I'll review it tomorrow and then loop in stakeholders once it's approved.

00:07:35.500 --> 00:07:48.000
<v Caretaker User>One open question: the analytics cluster. The reporting team has a separate read replica feeding dashboards. Not sure if it needs migrating or if it's out of scope.

00:07:48.500 --> 00:08:05.000
<v Care Taker>Yeah, I don't have that answer offhand. The data team partly manages it and I'm not sure what version or downstream dependencies they have. We'll need to ask them — don't want to decide this without talking to the data team.

00:08:05.500 --> 00:08:15.000
<v Caretaker User>Okay — I'll leave analytics out of the main plan for now. It shouldn't block our migration.

00:08:15.500 --> 00:08:28.000
<v Care Taker>Good. What about checksum validation to ensure data consistency?

00:08:28.500 --> 00:08:48.000
<v Caretaker User>I'll run checksums after replication is established and before cutover. Compare row counts and checksums for critical tables — orders, users, events — between old and new clusters. Any discrepancy and we don't proceed. I can write the validation script today and have it ready in staging by tomorrow.

00:08:48.500 --> 00:08:58.000
<v Care Taker>Perfect. I don't want to flip traffic if data integrity is questionable.

00:08:58.500 --> 00:09:12.000
<v Caretaker User>Also, Prometheus alerts — some rules reference the old cluster's labels. If we don't update them we'll lose alerting on the new cluster.

00:09:12.500 --> 00:09:22.000
<v Care Taker>Fix those before Thursday. Missing alerts during the window is unacceptable.

00:09:22.500 --> 00:09:38.000
<v Caretaker User>I'll update and validate alert rules before Thursday, and add an alert for replica delay > 2 seconds to watch during replication.

00:09:38.500 --> 00:09:50.000
<v Care Taker>Two seconds is our threshold. If lag consistently exceeds that before cutover we hold. Put that as a gate in the checklist.

00:09:50.500 --> 00:10:05.000
<v Caretaker User>I'll include the two-second replication threshold as a hard gate. If lag is above that at cutover time, we wait or abort.

00:10:05.500 --> 00:10:20.000
<v Care Taker>Post-migration, I want QA to run a smoke suite against prod. Can you get QA involved once the migration looks stable?

00:10:20.500 --> 00:10:30.000
<v Caretaker User>Yes — once we confirm post-cutover health I'll ping the QA channel so they can run the tests.

00:10:30.500 --> 00:10:45.000
<v Care Taker>Make sure they start before Friday so they have time to test while the old cluster is still available as a comparison.

00:10:45.500 --> 00:10:58.000
<v Caretaker User>Understood. I'll notify QA before Friday.

00:10:58.500 --> 00:11:15.000
<v Care Taker>Anything else — env vars, hardcoded hosts?

00:11:15.500 --> 00:11:30.000
<v Caretaker User>All services should get DB host from env vars injected from K8s secrets, but I'll sweep configs for any hardcoded hosts and include that check in the manifest updates.

00:11:30.500 --> 00:11:42.000
<v Care Taker>Better to find any hardcoded strings now than after the flip.

00:11:42.500 --> 00:11:58.000
<v Care Taker>Can you put together a Teams message summarizing this call? Include owners and deadlines, decisions, blockers, open items, dependencies, and next steps — post it to the engineering channel so everyone sees it.

00:11:58.500 --> 00:12:10.000
<v Caretaker User>Will do. I'll post a full summary with action owners, deadlines, decisions, blockers, and next steps.

00:12:10.500 --> 00:12:22.000
<v Care Taker>And when the migration finishes and is stable, send an update to engineering with date, status, and any notable items.

00:12:22.500 --> 00:12:35.000
<v Caretaker User>Yes — after we confirm stability I'll post a completion notification to the engineering channel.

00:12:35.500 --> 00:12:48.000
<v Care Taker>I'll get the migration window formally approved — I'll submit the change request to change management today so Thursday is booked.

00:12:48.500 --> 00:13:02.000
<v Caretaker User>If anything blocks me — ORM problems, staging failures — I'll message you directly today or tomorrow rather than wait for the next meeting.

00:13:02.500 --> 00:13:15.000
<v Care Taker>Thanks. Alright, I think we're set. Let's make sure Thursday goes smoothly. Talk soon.

00:13:15.500 --> 00:13:20.000
<v Caretaker User>Sounds good. I'll start the rollback script and staging validation now.

""",
}
GRAPH_API_OAUTH = {
    "transcript_metadata": {
        "id": "MSoxNzA5NjM4NDAwMDAwKjE5OmNkZTIzNGVmZzU2NzhoaWo5MDFrbG0yMzRub3BAYW1wZXJhdGVjaC5haQ==",
        "meetingId": "MSoxNzA5NjM4NDAwMDAwKjE5OmNkZTIzNGVmZzU2NzhoaWo5MDFrbG0yMzRub3BAYW1wZXJhdGVjaC5haQ==",
        "createdDateTime": "2025-02-04T14:03:11.204Z",
        "transcriptContentUrl": "https://graph.microsoft.com/v1.0/me/onlineMeetings/MSoxNzA5NjM4NDAwMDAwKjE5OmNkZTIzNGVmZzU2NzhoaWo5MDFrbG0yMzRub3BAYW1wZXJhdGVjaC5haQ==/transcripts/MSoxNzA5NjM4NDAwMDAwKjE5OmNkZTIzNGVmZzU2NzhoaWo5MDFrbG0yMzRub3BAYW1wZXJhdGVjaC5haQ==/content",
    },
    "meeting_metadata": {
        "id": "MSoxNzA5NjM4NDAwMDAwKjE5OmNkZTIzNGVmZzU2NzhoaWo5MDFrbG0yMzRub3BAYW1wZXJhdGVjaC5haQ==",
        "subject": "Graph API Integration & OAuth Token Management Review",
        "startDateTime": "2025-02-04T14:00:00.000Z",
        "endDateTime": "2025-02-04T14:16:48.000Z",
        "joinWebUrl": "https://teams.microsoft.com/l/meetup-join/19%3Acde234efg5678hij901klm234nop%40thread.tacv2/1738677600000?context=%7B%22Tid%22%3A%22f8a3b2c1-4d5e-6f7a-8b9c-0d1e2f3a4b5c%22%2C%22Oid%22%3A%22a1b2c3d4-e5f6-7890-abcd-ef1234567890%22%7D",
        "organizer": {
            "displayName": "Care Taker",
            "upn": "care.taker@amperatech.ai",
            "id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
            "tenantId": "f8a3b2c1-4d5e-6f7a-8b9c-0d1e2f3a4b5c",
        },
        "attendees": [
            {
                "displayName": "Caretaker User",
                "upn": "caretaker.user@amperatech.ai",
                "id": "b2c3d4e5-f6a7-8901-bcde-f12345678901",
                "tenantId": "f8a3b2c1-4d5e-6f7a-8b9c-0d1e2f3a4b5c",
                "role": "attendee",
            }
        ],
    },
    "vtt_content": """WEBVTT

00:00:01.000 --> 00:00:12.000
<v Care Taker>Okay, good. Let's jump in — I know you've been heads-down on the Graph API integration this week. Before I forget, did the client secret issue from last Thursday get resolved?

00:00:12.500 --> 00:00:28.000
<v Caretaker User>Partially. The immediate thing that was blocking us — the OAuth client credentials flow wasn't completing — I tracked that down to an expired client secret in Azure Key Vault. The secret itself had a one-year expiry and nobody rotated it. I've since pulled the new one from Key Vault and updated the app registration, but we need a longer-term answer for rotation.

00:00:28.500 --> 00:00:40.000
<v Care Taker>Right, and that's actually one of the things I wanted to talk through today. But first — can you pull up the current client secret reference from Key Vault and just confirm what expiry we're looking at now? I don't want to get caught off-guard again.

00:00:40.500 --> 00:00:55.000
<v Caretaker User>Yeah, I can check that. The secret is stored under the path graph-api/prod/client-secret in Key Vault — I won't paste the value here obviously, but I can retrieve the metadata and confirm the expiry. Give me until this afternoon and I'll send you a message with the expiry date and a note on whether we need to plan rotation soon.

00:00:55.500 --> 00:01:07.000
<v Care Taker>Good. And while you're at it, can you also verify the OAuth refresh token for the delegated permissions flow? There's a separate token we use for the mail send scope, and I want to make sure that one's not about to expire too.

00:01:07.500 --> 00:01:22.000
<v Caretaker User>I'll check both. The delegated token — that one's stored differently, it's not in Key Vault, it's in our Redis cache with a TTL. I'll check what TTL we set and whether there's a background refresh job keeping it alive. I suspect there might not be, which would be a problem.

00:01:22.500 --> 00:01:32.000
<v Care Taker>Yeah that would be a problem. Look into that today if you can. If there's no refresh job, that needs to be built before we go live. That's not optional.

00:01:32.500 --> 00:01:45.000
<v Caretaker User>Understood. I'll look into it today. If the refresh job is missing I'll flag it as a blocker and we'll need to decide whether to delay the go-live.

00:01:45.500 --> 00:02:00.000
<v Care Taker>Agreed. Let's not go live with a token that's going to silently expire in production. Okay, stepping back — where are we overall with the Graph API integration? Walk me through what's working and what's not.

00:02:00.500 --> 00:02:22.000
<v Caretaker User>So the core stuff is in decent shape. The application permissions flow — client credentials — is working again now that the secret is rotated. We can call the Users endpoint, pull profile data, and the mail send via the sendMail action is working in staging. The Draft API is where things get interesting. We're using the createDraft endpoint to generate email drafts on behalf of users, and that's been mostly working but I hit an issue yesterday.

00:02:22.500 --> 00:02:35.000
<v Care Taker>What kind of issue?

00:02:35.500 --> 00:02:58.000
<v Caretaker User>So the createDraft endpoint requires the Mail.ReadWrite delegated permission. In staging our test account has that scope, no problem. But in the production app registration I noticed the permission is listed but it hasn't been admin-consented yet. So in production, any call to createDraft is going to fail with a 403 until someone with Global Admin or an appropriate admin role consents to that permission for the tenant.

00:02:58.500 --> 00:03:10.000
<v Care Taker>Okay. That's a blocker. Who do we need for that — is that something I can do or does Security need to be involved?

00:03:10.500 --> 00:03:25.000
<v Caretaker User>It depends on how the tenant is configured. In some tenants individual users can consent to delegated permissions, but in most enterprise setups admin consent is required for Mail scopes. We'll need to check with Security whether they require a formal request or whether you can go in and consent directly as an admin.

00:03:25.500 --> 00:03:38.000
<v Care Taker>I'll ask Security today. Can you send me the exact permission name and the app registration ID so I can give them everything they need in one message? I don't want to go back and forth.

00:03:38.500 --> 00:03:50.000
<v Caretaker User>Sure. It's the Mail.ReadWrite delegated permission, application display name is AmperaTech-GraphIntegration-Prod. I'll send you the app registration object ID as well. I can have that to you in the next ten minutes.

00:03:50.500 --> 00:04:02.000
<v Care Taker>Great, do that. And while we're waiting on Security to respond — can you test the Draft API in staging end to end and document what the response payload looks like? I want to see an actual example draft response before we decide how to parse it in the frontend.

00:04:02.500 --> 00:04:18.000
<v Caretaker User>Yeah I can do that. I'll run through the full flow — auth, createDraft, then pull it back with getDraft — and I'll capture the response. I'll have a documented example ready by tomorrow morning, including what the message body field looks like and whether the isDraft flag is set correctly.

00:04:18.500 --> 00:04:28.000
<v Care Taker>Perfect. That'll be useful for the frontend team too. Make sure it's clear enough that they can read it without having to ask you follow-up questions.

00:04:28.500 --> 00:04:42.000
<v Caretaker User>Will do. Actually — one thing I want to flag before I forget. The Teams channel message API is separate from the mail Draft API. We've been talking about them almost interchangeably but they're quite different underneath. The channel message endpoint uses a different permission scope and the payload structure is different.

00:04:42.500 --> 00:04:55.000
<v Care Taker>Right, good point. We need both, right? We want to be able to draft emails and also post to Teams channels. So we need to make sure both flows are covered.

00:04:55.500 --> 00:05:10.000
<v Caretaker User>Correct. For Teams channel messages it's the ChannelMessage.Send application permission. That one I believe is already consented in the production app registration, but I should verify. Let me add that to my list — I'll confirm the permission status for ChannelMessage.Send in the prod app registration before Wednesday.

00:05:10.500 --> 00:05:22.000
<v Care Taker>Good. Add it to the list. Okay, back to secrets for a second — you mentioned we're using Key Vault for the client secret. What about the webhook signing secret for the Teams notification subscription? Is that in Key Vault too?

00:05:22.500 --> 00:05:40.000
<v Caretaker User>That's a good question and honestly I'm not a hundred percent sure. I think it might be stored as a Kubernetes secret directly rather than going through Key Vault. I'd need to check the deployment manifests. If it's not in Key Vault that's a gap — everything should be going through Key Vault or at minimum through the Vault agent for secrets management.

00:05:40.500 --> 00:05:52.000
<v Care Taker>Yeah, we can't have secrets scattered across different stores. Please check that today and if the webhook signing secret is not in Key Vault, get it moved over. That's the policy.

00:05:52.500 --> 00:06:05.000
<v Caretaker User>Understood. I'll check the Kubernetes manifests for the notification subscription service and if the signing secret is hardcoded or in a plain Kubernetes secret I'll migrate it to Key Vault and update the manifests. I can have that done today.

00:06:05.500 --> 00:06:18.000
<v Care Taker>Good. And please also verify the webhook signature validation logic itself — make sure we're actually validating incoming notifications against that signing secret and not just accepting everything. I saw a PR a few weeks ago where the validation was commented out.

00:06:18.500 --> 00:06:32.000
<v Caretaker User>Yeah... that was a temporary thing during development. That should have been re-enabled before we merged it to main. Let me check the current state of the notification handler. If validation is still disabled that's a security issue and I'll re-enable it immediately.

00:06:32.500 --> 00:06:42.000
<v Care Taker>Please do. That one is not optional — we cannot accept unvalidated webhook payloads in production. Check it today and let me know either way.

00:06:42.500 --> 00:06:55.000
<v Caretaker User>I will. I'll check it as soon as we're off this call. If it's still disabled I'll fix it and push the patch today. I'll message you once it's confirmed enabled and tested.

00:06:55.500 --> 00:07:10.000
<v Care Taker>Good. One more thing on the auth side — JWT. We're issuing our own JWTs for internal service-to-service calls alongside the Graph tokens, right? What's the signing key situation there?

00:07:10.500 --> 00:07:30.000
<v Caretaker User>Yes. The JWT signing key is referenced from Key Vault — secret name is internal-jwt-signing-key-prod. I rotated it last month as part of the quarterly rotation schedule. The services that consume it fetch it at startup via the Key Vault SDK, so a rotation doesn't require a redeploy. But there's one service — the notification dispatcher — that I think is caching the key in memory and not refreshing it. If we rotate mid-deployment that service would start failing signature verification.

00:07:30.500 --> 00:07:45.000
<v Care Taker>That sounds like a bug. Can you look at the notification dispatcher and figure out whether it's refreshing the signing key on a schedule or just loading it once at startup?

00:07:45.500 --> 00:08:00.000
<v Caretaker User>I'll look at it. If it's a one-time load I'll add a refresh interval — probably pulling from Key Vault every hour or so. That's a pretty low-effort fix. I can have a patch ready by Wednesday.

00:08:00.500 --> 00:08:12.000
<v Care Taker>Wednesday is fine. Don't rush and break something else. Just make sure it's tested properly before you deploy it.

00:08:12.500 --> 00:08:25.000
<v Caretaker User>Yeah, I'll test it in staging first — make sure rotation doesn't cause a blip for the dispatcher. One thing I'm not sure about is whether we should also rotate the JWT signing key now, ahead of the fix, or wait until the fix is in place first.

00:08:25.500 --> 00:08:38.000
<v Care Taker>Wait until the fix is in. Don't rotate a key while a service is known to not handle rotation correctly. That's just asking for a production incident. Fix the dispatcher first, validate it, then we can rotate on schedule.

00:08:38.500 --> 00:08:48.000
<v Caretaker User>Makes sense. I'll hold off on the JWT key rotation until the dispatcher patch is in and confirmed stable. Probably early next week then.

00:08:48.500 --> 00:09:00.000
<v Care Taker>Sounds right. Okay, let's talk about monitoring. We've got Prometheus scraping the Graph API call latency, right? How's that looking?

00:09:00.500 --> 00:09:22.000
<v Caretaker User>It's set up but the dashboard needs work. Right now we have request count and latency histograms, but we're missing error rate breakdowns by status code. I'd really like to see 401s and 403s called out separately — those are usually auth failures and they get lost in the general error rate right now. I'll update the Grafana dashboard to add those panels. I can do that before Friday.

00:09:22.500 --> 00:09:35.000
<v Care Taker>Please do. I want to be able to tell at a glance whether we're getting auth errors without having to dig through logs. Also — do we have alerting set up for repeated 401s? That would be a strong signal that a token is expired.

00:09:35.500 --> 00:09:52.000
<v Caretaker User>Not yet. I'll add a Prometheus alert rule — something like if the 401 error rate exceeds a threshold over a five-minute window, fire an alert. I'll make it route to the engineering Slack channel. That'll give us early warning if a secret expires in production without anyone noticing.

00:09:52.500 --> 00:10:05.000
<v Care Taker>Good idea. Hook it into Slack and also make sure it shows up in the on-call rotation. I don't want a secret expiry to be a silent failure. Can you have the alert set up before Friday as well?

00:10:05.500 --> 00:10:15.000
<v Caretaker User>Yeah I'll do the dashboard and alert together. Both before Friday.

00:10:15.500 --> 00:10:30.000
<v Care Taker>Good. Okay — is there anything that's actually blocking you right now today? Things you can't make progress on without something from someone else?

00:10:30.500 --> 00:10:52.000
<v Caretaker User>The main blocker is the Mail.ReadWrite admin consent. Until that's approved by Security I can't do end-to-end testing of the Draft API in production — only in staging. Everything else I can move forward on independently. The JWT dispatcher fix, the Grafana updates, the Key Vault secret check — those are all things I can do today and tomorrow without waiting on anyone.

00:10:52.500 --> 00:11:05.000
<v Care Taker>Okay, I'll message Security right after this call. I'll send them the app registration details you're about to forward me and request expedited review. Hopefully we get a response today or tomorrow.

00:11:05.500 --> 00:11:18.000
<v Caretaker User>That would be great. Once consent is granted I can do the production end-to-end test the same day. I'd rather not wait until next week on that.

00:11:18.500 --> 00:11:32.000
<v Care Taker>Agreed. I'll push for a response by tomorrow. If they come back with questions let me know and I'll handle the back and forth with them — I don't want it to become a time sink for you.

00:11:32.500 --> 00:11:44.000
<v Caretaker User>Appreciated. Oh — one thing I almost forgot. The feature flag for the Draft API feature is currently off in production. We have it behind a flag in our Redis-backed feature flag system. Once the admin consent is in place and we've done the production validation, we'll need to flip that flag on. Someone needs to own that step.

00:11:44.500 --> 00:11:58.000
<v Care Taker>That should be you — once you've done the production validation and everything checks out, go ahead and enable the feature flag. But don't touch it until production is validated end to end. That's the dependency — production validation first, then flag enable.

00:11:58.500 --> 00:12:10.000
<v Caretaker User>Understood. I'll own the flag flip, but only after full production validation is done. I'll document the validation steps and check each one off before enabling it.

00:12:10.500 --> 00:12:25.000
<v Care Taker>Good. And once the flag is on and the feature is live, can you post an update in the engineering Teams channel? I want the broader team to know it's available.

00:12:25.500 --> 00:12:38.000
<v Caretaker User>Yes, I'll post to the engineering channel once the feature is live. I'll include what's available, any known limitations, and who to contact with issues.

00:12:38.500 --> 00:12:52.000
<v Care Taker>Perfect. And one more thing — can you put together a brief Teams summary of everything we just talked about? Action items, owners, what's blocked, what the dependencies are. Post it to our project channel so we both have a record of it.

00:12:52.500 --> 00:13:08.000
<v Caretaker User>Sure. I'll write up a summary — action items with owners and deadlines, the blocker around admin consent, the dependency chain for the feature flag, and next steps. I'll post it to the project channel this afternoon.

00:13:08.500 --> 00:13:22.000
<v Care Taker>Good. Also — can you draft the email to the stakeholders about the Draft API feature going live? I'll need to send them a heads-up once we're confirmed live. Just a short one — what the feature does, when it went live, who to contact. You can send me the draft and I'll review and send it.

00:13:22.500 --> 00:13:38.000
<v Caretaker User>I'll put together an email draft. Short, factual — feature description, go-live date, contact info. I'll have the draft ready by tomorrow morning so you can review it before we actually flip the flag.

00:13:38.500 --> 00:13:50.000
<v Care Taker>That works. Alright, I think that's everything on my list. Anything else from your side before we wrap up?

00:13:50.500 --> 00:14:05.000
<v Caretaker User>Just one open question — and I genuinely don't have an answer for this yet. The feature flag is per-tenant in our system. Do we want to roll this out to all tenants at once when we enable it, or do we want to start with a subset? I don't know if there's a product decision on that yet.

00:14:05.500 --> 00:14:18.000
<v Care Taker>Good question. I don't know either off the top of my head. I'll check with Product and get back to you. Don't flip the flag until we've got an answer on that — it might affect how you enable it.

00:14:18.500 --> 00:14:32.000
<v Caretaker User>Got it. I'll wait to hear from you on the rollout scope before touching the flag. Everything else I can move forward on.

00:14:32.500 --> 00:14:48.000
<v Care Taker>Sounds good. Alright, let's wrap up. Talk later today once you've checked the webhook validation and the Key Vault secret. Message me directly if anything looks wrong.

00:14:48.500 --> 00:14:56.000
<v Caretaker User>Will do. I'll be in touch this afternoon.

""",
}
API_KEY_REQUEST = {
    "transcript_metadata": {
        "id": "MSoxNzA5ODEwMDAwMDAwKjE5OmFiMTIzY2Q0NTY3ZWZnODkwaGlqMTIza2xtQGFtcGVyYXRlY2guYWk=",
        "meetingId": "MSoxNzA5ODEwMDAwMDAwKjE5OmFiMTIzY2Q0NTY3ZWZnODkwaGlqMTIza2xtQGFtcGVyYXRlY2guYWk=",
        "createdDateTime": "2025-02-05T15:04:22.109Z",
        "transcriptContentUrl": "https://graph.microsoft.com/v1.0/me/onlineMeetings/MSoxNzA5ODEwMDAwMDAwKjE5OmFiMTIzY2Q0NTY3ZWZnODkwaGlqMTIza2xtQGFtcGVyYXRlY2guYWk=/transcripts/MSoxNzA5ODEwMDAwMDAwKjE5OmFiMTIzY2Q0NTY3ZWZnODkwaGlqMTIza2xtQGFtcGVyYXRlY2guYWk=/content",
    },
    "meeting_metadata": {
        "id": "MSoxNzA5ODEwMDAwMDAwKjE5OmFiMTIzY2Q0NTY3ZWZnODkwaGlqMTIza2xtQGFtcGVyYXRlY2guYWk=",
        "subject": "Production OpenAI API Key Request",
        "startDateTime": "2025-02-05T15:00:00.000Z",
        "endDateTime": "2025-02-05T15:05:10.000Z",
        "joinWebUrl": "https://teams.microsoft.com/l/meetup-join/19%3Aab123cd4567efg890hij123klm%40thread.tacv2/1738764000000?context=%7B%22Tid%22%3A%22f8a3b2c1-4d5e-6f7a-8b9c-0d1e2f3a4b5c%22%2C%22Oid%22%3A%22a1b2c3d4-e5f6-7890-abcd-ef1234567890%22%7D",
        "participants": {
            "organizer": {
                "identity": {"user": {"id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890", "displayName": "Care Taker"}},
                "upn": "care.taker@amperatech.ai",
            },
            "attendees": [
                {
                    "identity": {"user": {"id": "b2c3d4e5-f6a7-8901-bcde-f12345678901", "displayName": "Caretaker User"}},
                    "upn": "caretaker.user@amperatech.ai",
                    "role": "attendee",
                },
            ],
        },
    },
    "vtt_content": """WEBVTT
00:00:01.000 --> 00:00:09.000
<v Caretaker User>I'm blocked on the chatbot deployment and I need the production OpenAI API key via outlook. Can you send it to me?

00:00:09.500 --> 00:00:17.000
<v Care Taker>Sure, I'll email you the production OpenAI API key today.

00:00:17.500 --> 00:00:23.000
<v Caretaker User>Perfect, thank you.
""",
}

ALL_FIXTURES = [
    STANDUP,
    INCIDENT_POSTMORTEM,
    SPRINT_PLANNING,
    CLIENT_ESCALATION,
    CLIENT_DELAY,
    API_INTEGRATION,
    PROD_READINESS,
    DATABASE_MIGRATION,
    GRAPH_API_OAUTH,
    API_KEY_REQUEST,
]
