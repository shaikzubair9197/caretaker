#!/usr/bin/env python
"""
Caretaker Phase 2A — Security Boundary Validation Dashboard
===========================================================

Visualises the evidence produced by run_security_validation.py.

Run:
    cd caretaker && conda activate caretaker
    pip install streamlit          # one-time
    streamlit run phase2_security_validation/dashboard.py
"""

import json
from pathlib import Path

import streamlit as st

HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results"
REPORT = HERE / "security_report.json"

st.set_page_config(page_title="Caretaker Security Validation", layout="wide")


def load(name, base=RESULTS):
    p = base / name
    return json.loads(p.read_text()) if p.exists() else None


report = load("security_report.json", base=HERE)
if report is None:
    st.error("No security_report.json found. Run:  "
             "python phase2_security_validation/run_security_validation.py")
    st.stop()

# ── FINAL GO / NO-GO BANNER ───────────────────────────────────────────────────

go = report["go_no_go"] == "GO"
banner_color = "#1a7f37" if go else "#c0392b"
st.markdown(
    f"<div style='background:{banner_color};padding:22px;border-radius:10px;"
    f"text-align:center;color:white;font-size:30px;font-weight:800'>"
    f"{'✅  GO' if go else '⛔  NO-GO'} — {report['verdict']}</div>",
    unsafe_allow_html=True,
)
st.caption(f"overall_status = {report['overall_status']}  •  generated {report['generated_at']}")

# ── Execution summary ─────────────────────────────────────────────────────────

c = st.columns(4)
c[0].metric("Tests passed", report["tests_passed"])
c[1].metric("Tests failed", report["tests_failed"])
c[2].metric("Critical findings", len(report["critical_findings"]))
c[3].metric("Total findings", len(report["all_findings"]))

st.markdown("### Test execution summary")
cols = st.columns(4)
for i, (test, status) in enumerate(report["test_summary"].items()):
    color = "#1a7f37" if status == "PASS" else "#c0392b"
    with cols[i % 4]:
        st.markdown(
            f"<div style='border:2px solid {color};border-radius:8px;padding:10px;margin-bottom:8px'>"
            f"<div style='font-size:12px;color:#aaa'>{test}</div>"
            f"<div style='font-size:20px;font-weight:700;color:{color}'>{status}</div></div>",
            unsafe_allow_html=True,
        )

st.markdown("---")

# ── Detail tabs ───────────────────────────────────────────────────────────────

tabs = st.tabs([
    "LLM Leakage", "Threat Distribution", "Classification",
    "Vault Recovery", "Audit Matrix", "Findings",
])

# --- LLM leakage -------------------------------------------------------------
with tabs[0]:
    leak = load("llm_leak_validation.json")
    boundary = load("boundary_proof.json")
    if leak:
        ok = leak["passed"]
        st.markdown(f"**LLM data-leak status:** "
                    f"{'🟢 NO LEAKS' if ok else '🔴 LEAK DETECTED'}  "
                    f"({leak['items_checked']} items)")
        if boundary:
            st.markdown(f"**Boundary proof:** "
                        f"{'🟢 ' + str(boundary['llm_prompts_scanned']) + ' prompts scanned, 0 violations' if boundary['passed'] else '🔴 violations found'}")
        for r in leak["records"]:
            with st.expander(f"[{r['verdict']}] {r['source']} — {r['name']}"):
                st.markdown("**Raw input (contains secrets):**")
                st.code(r["raw_input"], language="text")
                st.markdown("**Exact payload sent to Ollama (masked):**")
                st.code(r["llm_prompt"][:1500], language="json")
                st.write({"secret_leaked_to_llm": r["secret_leaked_to_llm"],
                          "email_leaked_to_llm": r["email_leaked_to_llm"],
                          "placeholders_present": r["indexed_placeholders_present"]})

# --- Threat distribution -----------------------------------------------------
with tabs[1]:
    threat = load("threat_validation.json")
    if threat:
        dist = threat["distribution"]
        st.markdown("**Threat distribution**")
        st.bar_chart(dist)
        cc = st.columns(3)
        cc[0].metric("CLEAN", dist["CLEAN"])
        cc[1].metric("SUSPICIOUS", dist["SUSPICIOUS"])
        cc[2].metric("QUARANTINE", dist["QUARANTINE"])
        st.markdown("**Per-attack results**")
        for r in threat["records"]:
            color = {"CLEAN": "🟢", "SUSPICIOUS": "🟡", "QUARANTINE": "🔴"}[r["actual_threat"]]
            ok = "✅" if r["meets_or_exceeds_expected"] else "❌"
            st.markdown(f"{ok} {color} **{r['name']}** — expected `{r['expected_threat']}`, "
                        f"got `{r['actual_threat']}` (score {r['threat_score']})  "
                        f"{', '.join(r['flags']) if r['flags'] else ''}")

# --- Classification ----------------------------------------------------------
with tabs[2]:
    inj = load("injection_validation.json")
    leak = load("llm_leak_validation.json")
    st.markdown("**Sensitivity classification (injection + leak fixtures)**")
    rows = []
    for src in (inj, ):
        if src:
            for r in src["records"]:
                rows.append({"name": r["name"], "classification": r["classification"],
                             "threat": r["threat_label"]})
    CLASS_COLOR = {"PUBLIC": "⚪", "INTERNAL": "🔵", "CONFIDENTIAL": "🔵", "RESTRICTED": "🟣"}
    for r in rows:
        st.markdown(f"{CLASS_COLOR.get(r['classification'],'•')} **{r['name']}** → "
                    f"`{r['classification']}` (threat `{r['threat']}`)")

# --- Vault recovery ----------------------------------------------------------
with tabs[3]:
    vault = load("vault_roundtrip.json")
    if vault:
        st.metric("Vault recovery", f"{vault['recovery_percentage']}%")
        cc = st.columns(3)
        cc[0].metric("Entities", vault["entity_count"])
        cc[1].metric("Encrypted", vault["encrypted_count"])
        cc[2].metric("Recovered", vault["recovered_count"])
        st.markdown("**Per-source recovery**")
        for r in vault["records"]:
            st.markdown(f"- **{r['source']}** {r['name']}: "
                        f"{r['recovered_count']}/{r['entity_count']} "
                        f"({r['recovery_pct']}%)")

# --- Audit matrix ------------------------------------------------------------
with tabs[4]:
    audit = load("audit_validation.json")
    if audit:
        st.markdown(f"**Audit completeness:** "
                    f"{'🟢 critical events present' if audit['passed'] else '🔴 critical event missing'}")
        st.markdown(f"Emitted event types: `{audit.get('emitted_audit_event_types')}`")
        for ev, m in audit.get("matrix", {}).items():
            icon = "✅" if m["present"] else ("🟥" if m["severity"] == "CRITICAL" else "🟨")
            st.markdown(f"{icon} **{ev}** — {m['state']}  "
                        f"<span style='color:#888'>({m['severity']}: {m['note']})</span>",
                        unsafe_allow_html=True)

# --- Findings ----------------------------------------------------------------
with tabs[5]:
    if report["critical_findings"]:
        st.error("Critical findings:")
        for f in report["critical_findings"]:
            st.markdown(f"- **{f['severity']}** — {f['finding']}")
    else:
        st.success("No critical findings.")
    st.markdown("**All findings**")
    for f in report["all_findings"]:
        sev_color = {"CRITICAL": "🟥", "HIGH": "🟧", "MEDIUM": "🟨", "LOW": "🟦"}.get(f["severity"], "•")
        st.markdown(f"{sev_color} **{f['severity']}** — {f['finding']}")
        if f.get("recommendation"):
            st.caption("→ " + f["recommendation"])
    st.markdown("**Recommendations**")
    for r in report["recommendations"]:
        st.markdown(f"- {r}")
