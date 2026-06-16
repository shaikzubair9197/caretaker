# Phase 2A Ingestion Validation Harness

End-to-end validation of the Caretaker Microsoft Graph ingestion pipeline using
realistic synthetic payloads. Exercises **every layer with the real production
services** (no live Graph, no database connection required) and writes every
intermediate stage to JSON for human inspection.

## Folder structure

```
phase2_validation/
├── run_validation.py          # main harness — runs the full pipeline
├── dashboard.py               # Streamlit human-debug dashboard
├── README.md                  # this file
├── validation_report.json     # STEP 5 pass/fail (generated)
├── fixtures/                  # synthetic payloads (5 per source type)
│   ├── emails.json            # legit, phishing, BEC, credential-harvest, announcement
│   ├── chats.json             # normal, scheduling, API-key sharing, injection, HR
│   ├── transcripts.json       # decisions, action items, PII, credentials
│   ├── calendars.json         # design review, interview, exec, demo, 1:1
│   ├── todos.json             # engineering, follow-up, security, bugfix, docs
│   └── documents.json         # architecture, salary, runbook+secret, API, board deck
└── results/                   # generated per-run
    ├── <source>_<n>_<slug>.json   # one file per payload, all 9 stages
    ├── all_results.json           # combined array (dashboard input)
    └── summary.json               # aggregate counts
```

## What each item passes through

```
RAW PAYLOAD
  → NORMALIZE        (GraphNormalizer / inline for OneDrive)
  → MASK             (PreprocessingService.mask_pii_indexed → <PERSON_1>, <API_KEY_1>)
  → THREAT DETECT    (ThreatEngine → CLEAN | SUSPICIOUS | QUARANTINE + score)
  → CLASSIFY         (PreprocessingService.classify_sensitivity → PUBLIC|INTERNAL|CONFIDENTIAL|RESTRICTED)
  → VAULT            (vault_service AES-256-GCM encrypt; token → ciphertext)
  → DATABASE OBJECT  (simulated ORM dict mirroring the real model columns)
  → AUDIT EVENT      (INGEST event with outcome + signal metadata)
```

## Run it

```bash
cd caretaker
conda activate caretaker

# 1) Run the validation pipeline (writes results/ + validation_report.json)
python phase2_validation/run_validation.py

# 2) Launch the human debug dashboard
pip install streamlit          # one-time
streamlit run phase2_validation/dashboard.py
```

The harness exits `0` when all checks pass, `1` otherwise (CI-friendly).

## Validation checks (validation_report.json)

| Check | Verifies |
|---|---|
| `no_raw_emails_survive_masking` | No raw email address remains in any masked output |
| `no_api_keys_survive_masking` | No AWS/OpenAI key or DB connection string survives masking |
| `all_vault_tokens_decrypt` | Every AES-256-GCM token round-trips to its original |
| `threat_engine_catches_phishing` | Phishing fixtures flagged SUSPICIOUS/QUARANTINE |
| `threat_engine_catches_injection` | Prompt-injection fixtures detected |
| `sensitive_never_public` | Sensitive items never classified PUBLIC |
| `credentials_quarantined` | Credential-bearing items reach QUARANTINE |
| `database_objects_have_required_fields` | Each simulated ORM object has its required columns |

## Vault key

The harness uses an **ephemeral** AES-256 key per run (`secrets.token_hex(32)`),
unless `VAULT_MASTER_KEY` is already exported — in which case it reuses it.
Round-trip decryption is verified within the same run.

> Fixtures contain **synthetic data only**. The dashboard intentionally shows the
> original (decrypted) value next to each vault token so a human can confirm the
> right entity was protected. Do **not** point this harness at production data.

## Dashboard color coding

- **Green** CLEAN · **Yellow** SUSPICIOUS · **Red** QUARANTINE
- **Blue** CONFIDENTIAL · **Purple** RESTRICTED

Left panel: source + item selector. Center: raw → normalized → masked → threat →
classification → database object. Right: vault tokens, audit trail, threat
explanation.

## Findings surfaced by this harness

The validation run records recommendations in `validation_report.json`. Current
findings against the Phase 2A code:

1. **ThreatEngine under-flags injection/BEC** — their aggregate scores fall below
   the `FLAGGED` threshold. The harness applies a stricter rule (any fired signal
   flag → SUSPICIOUS); recommend porting it into `ThreatEngine`.
2. **Presidio generic `API_KEY` recognizer** matches any 16+ char alphanumeric at
   0.5 confidence — false-positives on long filenames, inflating the vault and
   over-classifying documents (fail-safe direction). Recommend a confidence/length
   heuristic in `PreprocessingService`.
3. **`api/graph_sync.py` classifies on body text only** — PII present only in
   metadata (calendar attendees, recipients) is missed. The harness classifies on
   the full vaulted-entity set; recommend the same in production.
4. **No dedicated OneDrive scorer/normalizer** exists yet — handled locally here.
