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

ALL_FIXTURES = [STANDUP, INCIDENT_POSTMORTEM, SPRINT_PLANNING, CLIENT_ESCALATION, CLIENT_DELAY, API_INTEGRATION, PROD_READINESS]
