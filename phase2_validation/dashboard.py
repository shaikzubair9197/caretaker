#!/usr/bin/env python
"""
Phase 2A Ingestion — Human Debug Dashboard
==========================================

Visual inspection of every pipeline stage produced by run_validation.py.

Run:
    cd caretaker
    conda activate caretaker
    pip install streamlit          # one-time
    streamlit run phase2_validation/dashboard.py

Reads:
    phase2_validation/results/all_results.json
    phase2_validation/results/summary.json
    phase2_validation/validation_report.json

Color coding:
    CLEAN → green   SUSPICIOUS → yellow   QUARANTINE → red
    CONFIDENTIAL → blue   RESTRICTED → purple
"""

import json
from pathlib import Path

import streamlit as st

HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results" / "all_results.json"
SUMMARY = HERE / "results" / "summary.json"
REPORT = HERE / "validation_report.json"

st.set_page_config(page_title="Caretaker Phase 2A Validation", layout="wide")

# ── Color helpers ─────────────────────────────────────────────────────────────

THREAT_COLORS = {"CLEAN": "#1a7f37", "SUSPICIOUS": "#b58900", "QUARANTINE": "#c0392b"}
CLASS_COLORS = {
    "PUBLIC": "#6e7781", "INTERNAL": "#57606a",
    "CONFIDENTIAL": "#1f6feb", "RESTRICTED": "#8250df",
}


def badge(text, color):
    return (
        f"<span style='background:{color};color:white;padding:3px 10px;"
        f"border-radius:12px;font-size:13px;font-weight:600'>{text}</span>"
    )


def section(title):
    st.markdown(
        f"<div style='font-size:13px;font-weight:700;color:#888;"
        f"text-transform:uppercase;letter-spacing:1px;margin:6px 0 2px'>{title}</div>",
        unsafe_allow_html=True,
    )


# ── Load data ─────────────────────────────────────────────────────────────────

if not RESULTS.exists():
    st.error("No results found. Run first:  python phase2_validation/run_validation.py")
    st.stop()

results = json.loads(RESULTS.read_text())
summary = json.loads(SUMMARY.read_text()) if SUMMARY.exists() else {}
report = json.loads(REPORT.read_text()) if REPORT.exists() else {}

# ── Header / summary strip ────────────────────────────────────────────────────

st.title("🛡️ Caretaker Phase 2A — Ingestion Validation")

if summary:
    c = st.columns(7)
    c[0].metric("Processed", summary.get("total_processed", 0))
    c[1].metric("Clean", summary.get("clean", 0))
    c[2].metric("Suspicious", summary.get("suspicious", 0))
    c[3].metric("Quarantine", summary.get("quarantine", 0))
    c[4].metric("Confidential", summary.get("confidential", 0))
    c[5].metric("Restricted", summary.get("restricted", 0))
    c[6].metric("Vault items", summary.get("vault_items_created", 0))

if report:
    if report.get("all_passed"):
        st.success(f"✅ All validation checks passed "
                   f"({report['checks_passed']}/{report['checks_total']})")
    else:
        st.error(f"❌ {report['checks_total'] - report['checks_passed']} validation "
                 f"check(s) failed ({report['checks_passed']}/{report['checks_total']})")
    with st.expander("Validation checks & recommendations"):
        for chk in report.get("checks", []):
            mark = "✅" if chk["passed"] else "❌"
            st.markdown(f"{mark} **{chk['check']}** — {chk['details']}")
            for f in chk.get("failures", []):
                st.markdown(f"&nbsp;&nbsp;&nbsp;&nbsp;⚠️ {f}")
        if report.get("recommendations"):
            st.markdown("---")
            st.markdown("**Recommendations**")
            for r in report["recommendations"]:
                st.markdown(f"- {r}")

st.markdown("---")

# ── LEFT PANEL — selectors ────────────────────────────────────────────────────

with st.sidebar:
    st.header("Browse")
    sources = sorted({r["source_type"] for r in results})
    source_sel = st.selectbox("Source type", ["(all)"] + sources)

    pool = results if source_sel == "(all)" else [r for r in results if r["source_type"] == source_sel]

    def label_for(r):
        return f"[{r['threat_label']}] {r['fixture_name']}"

    idx = st.radio(
        "Item",
        options=list(range(len(pool))),
        format_func=lambda i: label_for(pool[i]),
    )
    item = pool[idx]

    st.markdown("---")
    st.markdown("**Legend**")
    for k, v in THREAT_COLORS.items():
        st.markdown(badge(k, v), unsafe_allow_html=True)
    for k, v in CLASS_COLORS.items():
        st.markdown(badge(k, v), unsafe_allow_html=True)

# ── Title row with badges ─────────────────────────────────────────────────────

st.subheader(item["fixture_name"])
b = st.columns([1, 1, 1, 3])
b[0].markdown(badge(item["threat_label"], THREAT_COLORS.get(item["threat_label"], "#555")),
              unsafe_allow_html=True)
b[1].markdown(badge(item["classification"], CLASS_COLORS.get(item["classification"], "#555")),
              unsafe_allow_html=True)
b[2].markdown(badge(f"score {item['threat_score']}", "#444"), unsafe_allow_html=True)
b[3].markdown(f"`{item['source_type']}`  •  engine_category=`{item['engine_category']}`")

# ── 3-column layout ───────────────────────────────────────────────────────────

center, right = st.columns([2, 1])

with center:
    section("1 · Raw payload")
    st.json(item["raw_payload"], expanded=False)

    section("2 · Normalized payload")
    st.json(item["normalized"], expanded=False)

    section("3 · Masked output (sent to LLM)")
    st.code(item["masked_output"] or "(empty)", language="text")

    section("4 · Threat analysis")
    td = item["threat_detail"]
    tcols = st.columns(4)
    tcols[0].metric("Aggregate", item["threat_score"])
    tcols[1].metric("Phishing", td["phishing_score"])
    tcols[2].metric("Credential", td["credential_risk"])
    tcols[3].metric("Injection", td["injection_score"])
    tcols2 = st.columns(4)
    tcols2[0].metric("BEC", td["bec_score"])
    tcols2[1].metric("Urgency", td["urgency_score"])
    tcols2[2].metric("Social-eng", td["social_eng_score"])
    tcols2[3].metric("Link risk", td["link_risk_score"])
    if item["threat_flags"]:
        st.markdown("**Flags:** " + " ".join(
            badge(f, "#c0392b") for f in item["threat_flags"]))
    else:
        st.markdown("**Flags:** _none_")
    if td.get("recommended_action"):
        st.info(f"Recommended action: {td['recommended_action']}")
    if td.get("notes"):
        st.caption(td["notes"])

    section("5 · Classification")
    st.markdown(
        badge(item["classification"], CLASS_COLORS.get(item["classification"], "#555")),
        unsafe_allow_html=True,
    )

    section("6 · Database representation (simulated ORM)")
    st.json(item["database_object"], expanded=True)

with right:
    section("Vault tokens extracted")
    vt = item["vault_tokens"]
    if not vt:
        st.caption("No sensitive entities detected.")
    for tok, info in vt.items():
        ok = "✅" if info["roundtrip_ok"] else "❌"
        st.markdown(
            f"**`<{tok}>`** {ok}  "
            f"<span style='color:#888;font-size:12px'>{info['entity_type']}</span>",
            unsafe_allow_html=True,
        )
        st.caption(f"plain: {info['original_value']}")
        st.caption(f"cipher: {info['encrypted_hex'][:48]}… ({info['blob_bytes']} B, "
                   f"key v{info['key_version']})")
        st.markdown("<hr style='margin:4px 0'>", unsafe_allow_html=True)

    section("Audit trail")
    st.json(item["audit_event"], expanded=True)

    section("Threat explanation")
    label = item["threat_label"]
    if label == "QUARANTINE":
        st.error("Item QUARANTINED — blocked from LLM processing. "
                 "Credential or high-confidence threat signal detected.")
    elif label == "SUSPICIOUS":
        st.warning("Item flagged SUSPICIOUS — processed with elevated audit. "
                   "One or more threat signals fired below the quarantine threshold.")
    else:
        st.success("Item CLEAN — no threat signals fired. Normal ingestion path.")
