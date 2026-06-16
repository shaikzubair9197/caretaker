"""
Microsoft Graph Permission Audit — READ-ONLY
=============================================
Credentials are loaded exclusively from the .env file via python-dotenv.
NO secret values are ever printed, logged, or exposed.
"""

import base64
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import httpx

# ── Load credentials from .env ONLY inside Python (never via shell source) ────
_ENV_PATH = Path(__file__).parent.parent / ".env"

def _load_env(path: Path) -> None:
    """Parse .env manually — avoids shell exposure and doesn't need python-dotenv."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = val

_load_env(_ENV_PATH)

# ── Pull credentials (never printed) ──────────────────────────────────────────
ACCESS_TOKEN  = os.getenv("access_token", "")
TENANT_ID     = os.getenv("tenant_id",    "")
CLIENT_ID     = os.getenv("client_id",    "")
CLIENT_SECRET = os.getenv("client_secret", "")

GRAPH_BASE = "https://graph.microsoft.com/v1.0"

# Service account UPN — confirmed by user; not a secret
SERVICE_UPN = "care.taker@amperatech.ai"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _b64pad(s: str) -> str:
    return s + "=" * (-len(s) % 4)


def decode_jwt_payload(token: str) -> Optional[dict]:
    """Decode JWT payload locally — no network, no external service."""
    try:
        parts = token.strip().split(".")
        if len(parts) < 2:
            return None
        decoded = base64.urlsafe_b64decode(_b64pad(parts[1]))
        return json.loads(decoded)
    except Exception as e:
        return {"_error": str(e)}


def _mask(value: str, keep: int = 6) -> str:
    if not value:
        return "<not set>"
    return value[:keep] + "…[REDACTED]"


def _ts(epoch: Optional[int]) -> str:
    if not epoch:
        return "—"
    dt = datetime.fromtimestamp(epoch, tz=timezone.utc)
    expired = dt < datetime.now(tz=timezone.utc)
    suffix = " ⚠ EXPIRED" if expired else " ✓ valid"
    return dt.strftime("%Y-%m-%d %H:%M UTC") + suffix


def graph_get(path: str, token: str, params: Optional[dict] = None) -> tuple[int, dict]:
    """Single read-only GET against Microsoft Graph."""
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
    }
    try:
        r = httpx.get(
            GRAPH_BASE + path,
            headers=headers,
            params=params or {},
            timeout=15.0,
            follow_redirects=True,
        )
        try:
            body = r.json()
        except Exception:
            body = {"_raw": r.text[:200]}
        return r.status_code, body
    except httpx.ConnectError as e:
        return -1, {"error": f"Network: {e}"}
    except Exception as e:
        return -2, {"error": str(e)}


def err_note(body: dict) -> str:
    err = body.get("error", {})
    if isinstance(err, dict):
        code = err.get("code", "")
        msg  = err.get("message", "")[:100]
        return f"{code}: {msg}" if code else msg
    return str(err)[:100]


def get_app_token() -> Optional[str]:
    """Obtain fresh app-only token via client_credentials. Never prints secret."""
    if not (TENANT_ID and CLIENT_ID and CLIENT_SECRET):
        return None
    url = f"https://login.microsoftonline.com/{TENANT_ID}/oauth2/v2.0/token"
    try:
        r = httpx.post(url, data={
            "grant_type":    "client_credentials",
            "client_id":     CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "scope":         "https://graph.microsoft.com/.default",
        }, timeout=15.0)
        if r.status_code == 200:
            return r.json().get("access_token")
        # Print only the error code, not the secret
        err = r.json().get("error_description", r.text[:80])
        print(f"    ✗ Token request failed ({r.status_code}): {err}")
    except Exception as e:
        print(f"    ✗ Token request exception: {e}")
    return None


# ── Main audit ────────────────────────────────────────────────────────────────

def run_audit():
    SEP  = "=" * 72
    SEP2 = "-" * 72

    print(SEP)
    print("  MICROSOFT GRAPH PERMISSION AUDIT — READ-ONLY")
    print(f"  Service account : {SERVICE_UPN}")
    print(f"  Generated       : {datetime.now(tz=timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print(SEP)

    # ── Credential presence check ──────────────────────────────────────────────
    print("\n[CREDENTIAL PRESENCE]")
    print(f"  access_token  : {'set (' + _mask(ACCESS_TOKEN) + ')' if ACCESS_TOKEN else '⚠ NOT SET or incomplete'}")
    print(f"  tenant_id     : {'set (' + _mask(TENANT_ID)    + ')' if TENANT_ID    else '⚠ NOT SET'}")
    print(f"  client_id     : {'set (' + _mask(CLIENT_ID)    + ')' if CLIENT_ID    else '⚠ NOT SET'}")
    print(f"  client_secret : {'set'                                if CLIENT_SECRET else '⚠ NOT SET'}")

    # ── STEP 1 — Decode access token ──────────────────────────────────────────
    print(f"\n{SEP}")
    print("  STEP 1 — JWT TOKEN ANALYSIS (local decode, no network)")
    print(SEP)

    delegated_token = ACCESS_TOKEN if len(ACCESS_TOKEN) > 50 else None
    token_permissions: set[str] = set()

    if delegated_token:
        payload = decode_jwt_payload(delegated_token)
        if payload and "_error" not in payload:
            aud   = payload.get("aud", "—")
            appid = payload.get("appid", payload.get("azp", "—"))
            tid   = payload.get("tid", "—")
            roles = payload.get("roles", [])
            scp   = payload.get("scp", "")
            exp   = payload.get("exp")
            nbf   = payload.get("nbf")
            idtyp = payload.get("idtyp", "—")
            iss   = payload.get("iss", "—")

            is_app  = idtyp == "app" or (not scp and bool(roles))

            print(f"\n  Audience (aud)   : {aud}")
            print(f"  App ID           : {appid}")
            print(f"  Tenant ID (tid)  : {tid}")
            print(f"  Token type       : {'Application (app-only)' if is_app else 'Delegated (user context)'}")
            print(f"  Issued by        : {str(iss)[:70]}")
            print(f"  Valid from (nbf) : {_ts(nbf)}")
            print(f"  Expires   (exp)  : {_ts(exp)}")

            print(f"\n  Application roles (app-only permissions in JWT):")
            if roles:
                for r in sorted(roles):
                    print(f"    ✓  {r}")
                token_permissions |= set(roles)
            else:
                print("    — none —")

            print(f"\n  Delegated scopes (scp field):")
            if scp:
                for s in sorted(scp.split()):
                    print(f"    ✓  {s}")
                token_permissions |= set(scp.split())
            else:
                print("    — none (token is application-only) —")
        else:
            err = payload.get("_error") if payload else "unknown"
            print(f"  ⚠  JWT decode failed: {err}")
    else:
        print("  ⚠  access_token is absent or too short to be a valid JWT.")
        print(f"     Proceeding with client_credentials flow only.")

    # ── Acquire fresh app-only token ──────────────────────────────────────────
    print(f"\n  Acquiring fresh app-only token via client_credentials flow...")
    app_token = get_app_token()
    active_token: str = ""

    if app_token:
        app_payload = decode_jwt_payload(app_token)
        app_roles   = app_payload.get("roles", []) if app_payload else []
        print(f"  ✓  App token acquired successfully.")
        print(f"     Application roles in fresh token:")
        if app_roles:
            for r in sorted(app_roles):
                print(f"       ✓  {r}")
            token_permissions |= set(app_roles)
        else:
            print("       — no roles present —")
        active_token = app_token
        token_type   = "Application (client_credentials, admin-consented)"
    else:
        if delegated_token:
            active_token = delegated_token
            token_type   = "Delegated (access_token from env)"
            print("  ⚠  App token failed — falling back to delegated access_token from env")
        else:
            print("\n  ✗  No usable token. Cannot continue.")
            sys.exit(1)

    # ── STEP 2 + 3 — API probes ───────────────────────────────────────────────
    print(f"\n{SEP}")
    print("  STEP 2 + 3 — READ-ONLY GRAPH API CAPABILITY PROBES")
    print(SEP)
    print(f"  Token type: {token_type}")
    print(f"  UPN in use: {SERVICE_UPN}")

    U = SERVICE_UPN  # shorthand; not a secret

    # For app-only tokens, /me is invalid — use /users/{upn} paths
    probes = [
        # (label, path, params, required_permission, category)

        # MAIL
        ("Mail.Read (delegated /me)",
            "/me/messages",                              {"$top":"1"},
            "Mail.Read", "MAIL"),
        ("Mail.ReadBasic.All or Mail.Read.All (app)",
            f"/users/{U}/messages",                      {"$top":"1","$select":"subject,receivedDateTime,from"},
            "Mail.Read.All / Mail.ReadBasic.All", "MAIL"),

        # CALENDAR
        ("Calendars.Read (delegated)",
            "/me/events",                                {"$top":"1"},
            "Calendars.Read", "CALENDAR"),
        ("Calendars.Read.All (app)",
            f"/users/{U}/events",                        {"$top":"1","$select":"subject,start,end,organizer"},
            "Calendars.Read.All", "CALENDAR"),

        # TEAMS CHATS
        ("Chat.Read (delegated /me/chats)",
            "/me/chats",                                 {"$top":"1"},
            "Chat.Read", "TEAMS"),
        ("Chat.Read.All (app /chats)",
            "/chats",                                    {"$top":"1"},
            "Chat.Read.All", "TEAMS"),

        # TEAMS CHANNELS
        ("Team.ReadBasic.All (/teams)",
            "/teams",                                    {"$top":"1"},
            "Team.ReadBasic.All", "TEAMS"),

        # ONLINE MEETINGS
        ("OnlineMeetings.Read (delegated)",
            "/me/onlineMeetings",                        {"$top":"1"},
            "OnlineMeetings.Read", "MEETINGS"),
        ("OnlineMeetings.Read.All (app)",
            f"/users/{U}/onlineMeetings",                {"$top":"1"},
            "OnlineMeetings.Read.All", "MEETINGS"),

        # PRESENCE
        ("Presence.Read.All",
            "/communications/presences",                 {"ids": f"{U}"},
            "Presence.Read.All", "PRESENCE"),

        # ONEDRIVE
        ("Files.Read (delegated /me/drive)",
            "/me/drive/root/children",                   {"$top":"1"},
            "Files.Read", "FILES"),
        ("Files.Read.All (app /users/{upn}/drive)",
            f"/users/{U}/drive/root/children",           {"$top":"1"},
            "Files.Read.All", "FILES"),

        # USERS DIRECTORY
        ("User.Read.All",
            "/users",                                    {"$top":"5","$select":"displayName,mail,id,userPrincipalName"},
            "User.Read.All", "USERS"),

        # SITES
        ("Sites.Read.All",
            "/sites",                                    {"$top":"1"},
            "Sites.Read.All", "SITES"),

        # REPORTS
        ("Reports.Read.All",
            "/reports/getEmailActivityUserDetail(period='D7')",
            {},
            "Reports.Read.All", "REPORTS"),

        # AUDIT LOGS
        ("AuditLog.Read.All",
            "/auditLogs/signIns",                        {"$top":"1"},
            "AuditLog.Read.All", "AUDIT"),
    ]

    results = []
    print(f"\n  {'Capability':<42} {'Status':>6}  {'Access':>8}  Notes")
    print(f"  {'-'*42} {'-'*6}  {'-'*8}  {'-'*35}")

    for cap, path, params, perm, cat in probes:
        status, body = graph_get(path, active_token, params)
        if status == 200:
            count = len(body.get("value", [])) if "value" in body else 1
            accessible = "YES"
            note = f"{count} record(s) returned"
        elif status == 401:
            accessible = "NO"
            note = "Unauthorized — token invalid/expired"
        elif status == 403:
            accessible = "NO"
            note = "Forbidden — " + err_note(body)[:55]
        elif status == 404:
            accessible = "NO"
            note = "Not Found (no /me user context for app token)"
        elif status == 400:
            accessible = "NO"
            note = "Bad Request — " + err_note(body)[:55]
        elif status < 0:
            accessible = "ERR"
            note = body.get("error","Network error")[:55]
        else:
            accessible = "NO"
            note = f"HTTP {status} — " + err_note(body)[:50]

        results.append({
            "capability": cap,
            "endpoint":   path,
            "status":     status,
            "accessible": accessible,
            "permission": perm,
            "category":   cat,
            "note":       note,
        })

        icon = "✓" if accessible == "YES" else ("⚠" if accessible == "ERR" else "✗")
        print(f"  {cap:<42} {str(status):>6}  {icon+' '+accessible:>8}  {note[:55]}")
        time.sleep(0.25)

    # ── Teams channels (drill-down if /teams returned data) ───────────────────
    teams_result = next((r for r in results if r["category"] == "TEAMS" and "teams" in r["endpoint"] and r["accessible"] == "YES"), None)
    if teams_result:
        ts_status, ts_body = graph_get("/teams", active_token, {"$top": "1"})
        if ts_status == 200 and ts_body.get("value"):
            team_id = ts_body["value"][0].get("id", "")
            if team_id:
                ch_status, ch_body = graph_get(f"/teams/{team_id}/channels", active_token, {"$top": "1"})
                accessible = "YES" if ch_status == 200 else "NO"
                note = f"HTTP {ch_status} — {err_note(ch_body)[:50]}" if ch_status != 200 else f"{len(ch_body.get('value',[]))} channel(s)"
                results.append({
                    "capability": "ChannelMessage.Read.All (channels drill-down)",
                    "endpoint": f"/teams/{team_id[:8]}…/channels",
                    "status": ch_status,
                    "accessible": accessible,
                    "permission": "ChannelMessage.Read.All",
                    "category": "TEAMS",
                    "note": note,
                })
                icon = "✓" if accessible == "YES" else "✗"
                print(f"  {'ChannelMessage.Read.All (drill-down)':<42} {str(ch_status):>6}  {icon+' '+accessible:>8}  {note[:55]}")
                time.sleep(0.25)

    # ── Transcript probe (drill-down from meetings) ────────────────────────────
    mtg_result = next((r for r in results if r["category"] == "MEETINGS" and r["accessible"] == "YES"), None)
    if mtg_result:
        mtg_path = mtg_result["endpoint"]
        mt_status, mt_body = graph_get(mtg_path, active_token, {"$top": "1"})
        if mt_status == 200 and mt_body.get("value"):
            mid = mt_body["value"][0].get("id", "")
            upn_path = mtg_path.split("/onlineMeetings")[0]  # /users/{upn} or /me
            tr_status, tr_body = graph_get(f"{upn_path}/onlineMeetings/{mid}/transcripts", active_token, {"$top": "1"})
            accessible = "YES" if tr_status == 200 else "NO"
            note = f"{len(tr_body.get('value',[]))} transcript(s)" if tr_status == 200 else f"HTTP {tr_status} — {err_note(tr_body)[:50]}"
            results.append({
                "capability": "OnlineMeetingTranscript.Read.All",
                "endpoint":   f"{upn_path}/onlineMeetings/…/transcripts",
                "status":     tr_status,
                "accessible": accessible,
                "permission": "OnlineMeetingTranscript.Read.All",
                "category":   "TRANSCRIPTS",
                "note":       note,
            })
            icon = "✓" if accessible == "YES" else "✗"
            print(f"  {'OnlineMeetingTranscript.Read.All':<42} {str(tr_status):>6}  {icon+' '+accessible:>8}  {note[:55]}")
        else:
            results.append({
                "capability": "OnlineMeetingTranscript.Read.All",
                "endpoint":   "—",
                "status":     0,
                "accessible": "UNKNOWN",
                "permission": "OnlineMeetingTranscript.Read.All",
                "category":   "TRANSCRIPTS",
                "note":       "No meetings discoverable to probe transcript endpoint",
            })
            print(f"  {'OnlineMeetingTranscript.Read.All':<42} {'N/A':>6}  {'? UNKNOWN':>8}  No meetings to probe")
    else:
        results.append({
            "capability": "OnlineMeetingTranscript.Read.All",
            "endpoint": "—", "status": 0, "accessible": "UNKNOWN",
            "permission": "OnlineMeetingTranscript.Read.All",
            "category": "TRANSCRIPTS",
            "note": "Meetings endpoint inaccessible — transcript probe skipped",
        })
        print(f"  {'OnlineMeetingTranscript.Read.All':<42} {'N/A':>6}  {'? UNKNOWN':>8}  Meetings inaccessible — skipped")

    # ── Full capability table ──────────────────────────────────────────────────
    print(f"\n\n{'─'*72}")
    print("  FULL CAPABILITY TABLE")
    print(f"{'─'*72}")
    print(f"  {'Capability':<42} {'Endpoint':<38} {'HTTP':>5}  {'Accessible':>10}  Required Permission")
    print(f"  {'-'*42} {'-'*38} {'-'*5}  {'-'*10}  {'-'*30}")
    for r in results:
        print(f"  {r['capability']:<42} {r['endpoint']:<38} {str(r['status']):>5}  {r['accessible']:>10}  {r['permission']}")

    # ── STEP 4 — Effective Permissions ────────────────────────────────────────
    print(f"\n{SEP}")
    print("  STEP 4 — EFFECTIVE PERMISSIONS CROSS-REFERENCE")
    print(SEP)

    perm_map: dict[str, dict] = {}
    for r in results:
        for p in r["permission"].split(" / "):
            p = p.strip()
            if p not in perm_map:
                perm_map[p] = {"in_token": False, "api_yes": False, "api_no": False}
            if r["accessible"] == "YES":
                perm_map[p]["api_yes"] = True
            elif r["accessible"] == "NO":
                perm_map[p]["api_no"] = True

    for p in perm_map:
        for tp in token_permissions:
            if p.lower() == tp.lower() or p.lower().replace(".all","") in tp.lower():
                perm_map[p]["in_token"] = True

    verified   = []
    token_only = []
    denied_    = []

    print(f"\n  {'Permission':<42} {'In Token':>10}  {'API Result':>12}  Verdict")
    print(f"  {'-'*42} {'-'*10}  {'-'*12}  {'-'*15}")

    for perm in sorted(perm_map.keys()):
        s = perm_map[perm]
        if s["api_yes"]:
            verdict = "VERIFIED"
            verified.append(perm)
        elif s["in_token"] and not s["api_no"]:
            verdict = "TOKEN ONLY"
            token_only.append(perm)
        else:
            verdict = "DENIED"
            denied_.append(perm)

        tok  = "✓" if s["in_token"] else "—"
        api  = "✓ confirmed" if s["api_yes"] else ("✗ denied" if s["api_no"] else "— skipped")
        print(f"  {perm:<42} {tok:>10}  {api:>12}  {verdict}")

    # ── STEP 5 — Ingestion Readiness ─────────────────────────────────────────
    print(f"\n{SEP}")
    print("  STEP 5 — CARETAKER INGESTION READINESS MATRIX")
    print(SEP)

    def cat_ok(cat: str) -> str:
        return "YES" if any(r["accessible"] == "YES" and r["category"] == cat for r in results) else "NO"

    mail_ok    = cat_ok("MAIL")
    cal_ok     = cat_ok("CALENDAR")
    teams_ok   = cat_ok("TEAMS")
    mtg_ok     = cat_ok("MEETINGS")
    xscript_ok = cat_ok("TRANSCRIPTS")
    files_ok   = cat_ok("FILES")

    matrix = [
        ("Outlook Emails",      mail_ok,    "Proceed — mail pipeline ready"   if mail_ok    == "YES" else "Block — grant Mail.Read.All"),
        ("Teams Chats",         teams_ok,   "Proceed — chat pipeline ready"   if teams_ok   == "YES" else "Block — grant Chat.Read.All"),
        ("Calendar Events",     cal_ok,     "Proceed — calendar ready"        if cal_ok     == "YES" else "Block — grant Calendars.Read.All"),
        ("Meeting Transcripts", xscript_ok, "Proceed — transcripts ready"     if xscript_ok == "YES" else "Block — grant OnlineMeetingTranscript.Read.All"),
        ("OneDrive Files",      files_ok,   "Proceed — files pipeline ready"  if files_ok   == "YES" else "Block — grant Files.Read.All"),
    ]

    print(f"\n  {'Data Source':<24} {'Can Access':>11}  Recommendation")
    print(f"  {'-'*24} {'-'*11}  {'-'*48}")
    for src, can, rec in matrix:
        icon = "✓" if can == "YES" else "✗"
        print(f"  {src:<24} {icon + ' ' + can:>11}  {rec}")

    can_access    = [src for src, can, _ in matrix if can == "YES"]
    cannot_access = [src for src, can, _ in matrix if can != "YES"]

    # ── STEP 6 — Security Classification ─────────────────────────────────────
    print(f"\n{SEP}")
    print("  STEP 6 — SECURITY RISK CLASSIFICATION")
    print(SEP)

    has_mail    = mail_ok    == "YES"
    has_chat    = teams_ok   == "YES"
    has_files   = files_ok   == "YES"
    has_users   = any(r["accessible"] == "YES" and r["category"] == "USERS" for r in results)
    has_cal     = cal_ok     == "YES"
    tenant_wide = has_users or any(
        r["accessible"] == "YES" and ".All" in r["permission"]
        for r in results
    )

    broad_access = sum([has_mail, has_chat, has_files, has_users, has_cal])

    if broad_access == 0:
        risk = "LOW RISK"
        risk_exp = "No permissions confirmed through API testing."
    elif broad_access >= 4 or (tenant_wide and (has_mail or has_chat)):
        risk = "HIGH RISK"
        risk_exp = "Confirmed access to tenant-wide communications and PII."
    elif broad_access >= 2 and tenant_wide:
        risk = "HIGH RISK"
        risk_exp = "Multiple data categories with tenant-wide scope."
    elif broad_access >= 2:
        risk = "MODERATE RISK"
        risk_exp = "Access to multiple data categories but limited scope."
    else:
        risk = "MODERATE RISK"
        risk_exp = "Limited confirmed access; single data category."

    stars = {"LOW RISK": "★☆☆☆", "MODERATE RISK": "★★☆☆", "HIGH RISK": "★★★☆", "CRITICAL RISK": "★★★★"}

    print(f"\n  Classification : {stars.get(risk,'?')}  {risk}")
    print(f"  Justification  : {risk_exp}")
    print(f"\n  Employee data accessible:")
    if has_mail:    print("    ● Email content — sender, recipients, subject, full body")
    if has_chat:    print("    ● Teams chat messages — private and group conversations")
    if has_cal:     print("    ● Calendar events — meeting attendees, times, descriptions")
    if has_users:   print("    ● User directory — names, email addresses, UPNs (all users in tenant)")
    if has_files:   print("    ● OneDrive file listings and content")
    if xscript_ok == "YES": print("    ● Meeting transcripts — verbatim conversation records")
    if not any([has_mail, has_chat, has_cal, has_users, has_files]):
        print("    — No confirmed access through API testing —")
    print(f"\n  Tenant-wide visibility   : {'YES' if tenant_wide else 'NO'}")
    print(f"  Admin approval required  : {'YES — mandatory before enabling production ingestion' if tenant_wide or has_mail or has_chat else 'RECOMMENDED'}")

    # ── Executive Summary ─────────────────────────────────────────────────────
    print(f"\n{SEP}")
    print("  EXECUTIVE SUMMARY")
    print(SEP)

    missing_for_transcripts = []
    if xscript_ok != "YES":
        for p in ["OnlineMeetingTranscript.Read.All", "OnlineMeetings.Read.All"]:
            if p not in verified:
                missing_for_transcripts.append(p)

    print(f"\n  ┌─ 1. WHAT THE APPLICATION CAN ACCESS ─────────────────────────────┐")
    if can_access:
        for src in can_access:
            print(f"  │   ✓  {src:<58}│")
    else:
        print(f"  │   — Nothing confirmed via live API testing —                     │")
    print(f"  └──────────────────────────────────────────────────────────────────┘")

    print(f"\n  ┌─ 2. WHAT THE APPLICATION CANNOT ACCESS ──────────────────────────┐")
    if cannot_access:
        for src in cannot_access:
            print(f"  │   ✗  {src:<58}│")
    else:
        print(f"  │   — All tested sources are accessible —                          │")
    print(f"  └──────────────────────────────────────────────────────────────────┘")

    print(f"\n  ┌─ 3. CARETAKER INGESTION SAFETY VERDICT ──────────────────────────┐")
    if not can_access:
        print(f"  │  ⚠  No access confirmed. Grant permissions before ingestion.     │")
    elif risk in ("HIGH RISK", "CRITICAL RISK"):
        print(f"  │  ⚠  CONDITIONAL PROCEED. Required safeguards:                    │")
        print(f"  │     a) Data privacy review and written approval                  │")
        print(f"  │     b) Presidio PII masking confirmed BEFORE any LLM call        │")
        print(f"  │     c) Only masked_text may reach Ollama — raw text stays local  │")
        print(f"  │     d) Audit log enabled for all ingestion events                │")
        print(f"  │     e) Scope ingestion to service account's own mailbox/calendar │")
    else:
        print(f"  │  ✓  Proceed for confirmed sources with standard safeguards.      │")
    print(f"  └──────────────────────────────────────────────────────────────────┘")

    print(f"\n  ┌─ 4. PERMISSIONS NEEDED FOR MEETING TRANSCRIPT INGESTION ─────────┐")
    if not missing_for_transcripts:
        print(f"  │  ✓  Transcript permissions appear to be in place.               │")
    else:
        print(f"  │  The following permissions are MISSING or UNCONFIRMED:           │")
        for p in missing_for_transcripts:
            print(f"  │     ✗  {p:<57}│")
        print(f"  │                                                                  │")
        print(f"  │  To enable:                                                      │")
        print(f"  │   1. Azure Portal → App Registrations → this app                │")
        print(f"  │   2. API Permissions → Add a permission → Microsoft Graph        │")
        print(f"  │   3. Application permissions → add:                              │")
        print(f"  │        OnlineMeetingTranscript.Read.All                          │")
        print(f"  │        OnlineMeetings.Read.All                                   │")
        print(f"  │   4. Grant admin consent for tenant                              │")
        print(f"  │   5. Re-run this script to verify                                │")
    print(f"  └──────────────────────────────────────────────────────────────────┘")

    print(f"\n  Token permissions seen (JWT + client_credentials):")
    if token_permissions:
        for p in sorted(token_permissions):
            mark = "VERIFIED" if p in verified else ("TOKEN ONLY" if p in token_only else "DENIED")
            print(f"    • {p:<50} [{mark}]")
    else:
        print("    — none found —")

    print(f"\n  Permissions VERIFIED via live API call:")
    if verified:
        for p in sorted(verified):
            print(f"    ✓  {p}")
    else:
        print("    — none confirmed —")

    print(f"\n  Permissions DENIED by API:")
    if denied_:
        for p in sorted(denied_):
            print(f"    ✗  {p}")
    else:
        print("    — none denied (all either verified or untested) —")

    print(f"\n{SEP}")
    print("  Audit complete. No secrets, tokens, or credentials were exposed.")
    print(f"  Risk level: {risk}")
    print(SEP)


if __name__ == "__main__":
    run_audit()
