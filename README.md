# Lane — Phase 0 runnable prototype

**Based on Part 1 / Phase 0 (22 Oct–2 Nov 2026) of `YC (3).pdf`.** It demonstrates a caller correcting a booking from 9 to 10, a calendar still showing 9, and an independent re-check after an operator fixes the calendar.

> **Scope and truthfulness:** This is a working local Phase 0 starter, not a validated production integration. It supports real Retell webhooks and read-only HighLevel GET calls when credentials are supplied. Without those credentials, use explicitly labelled synthetic demo data. No customer-facing deployment, security audit, real paid pilot, external API success or autonomous calendar write is claimed.

## Fastest way: run synthetic demo in Docker

Prerequisites: Docker Desktop (or Docker Engine with Compose).

```bash
cp .env.example .env
# Edit .env: set APP_TOKEN and DASHBOARD_PASSWORD to different strong values.
docker compose up --build -d
```

Open **http://localhost:8000**. Basic-Auth username is `lane` by default, password is your `DASHBOARD_PASSWORD`. Click **Load example**. Open the case: it says **caller requested 10** while the synthetic calendar shows **9**. Click **Simulate operator fix (demo)** and then **Re-check calendar**: status becomes **verified**. The synthetic fix writes **only** a local demo record; Lane never writes to HighLevel.

Stop: `docker compose down`. Erase demo database: `docker compose down -v` (destructive).

## Live pilot: configure one real Retell + HighLevel sub-account

1. Set `DEMO_MODE=false`, `RETELL_API_KEY=...`, `HIGHLEVEL_TOKEN=...` in `.env`, restart services. Keep `APP_TOKEN` and dashboard credentials private. Use a dedicated HighLevel sub-account *Private Integration* token with only `calendars/events.readonly` and `contacts.readonly` scopes; **do not grant write scopes**. HighLevel v3 documented endpoints are used by default. If the API version accepted by your sub-account differs, set `HIGHLEVEL_VERSION` to the supported documented value.
2. Register the client and its mapping (agent ID, location ID, calendar ID, timezone):

```bash
# In bash, export your local API secret from .env manually. Do not paste it into public repos.
export LANE_TOKEN='the APP_TOKEN you set in .env'
curl -sS -X POST http://localhost:8000/api/clients \
  -H "Authorization: Bearer $LANE_TOKEN" -H 'Content-Type: application/json' \
  --data-binary @examples/new-client.json
# Read returned id (example 1), then change approved_agent_version and approver.
curl -sS -X POST http://localhost:8000/api/clients/1/manifests \
  -H "Authorization: Bearer $LANE_TOKEN" -H 'Content-Type: application/json' \
  --data-binary @examples/manifest.json
```

3. Expose the webhook through an HTTPS tunnel or HTTPS deployment. Set the Retell webhook endpoint to `https://YOUR_PUBLIC_HOST/webhooks/retell`, enable **call_ended** events; it checks the raw-body `x-retell-signature` with the official Retell SDK. It will reject unsigned webhooks. Make a real call on the **matching Retell agent version** *after* the manifest was approved.
4. Retell's webhook is ingested immediately; a separate worker extracts the final accepted booking request using `OPENAI_API_KEY` and `EXTRACTION_MODEL` (optional for demo, **required for real transcript extraction**). A missing/invalid extraction or insufficient identity evidence becomes **unknown**, never verified.
5. HighLevel calendar reads run at **2, 15, and 60 minutes after call end**, using a persistent Postgres job table. A tool success response is *not* proof. Visit the findings dashboard. Correct a wrong booking manually in HighLevel, then click **Re-check calendar**.

### Test HighLevel reads directly

These are the **documented read-only endpoints**. Run them with the sub-account's private integration token and the version header that each endpoint supports:

```bash
curl -i 'https://services.leadconnectorhq.com/calendars/events/appointments/APPOINTMENT_ID' \
  -H "Authorization: Bearer $HIGHLEVEL_TOKEN" -H 'Version: v3' -H 'Accept: application/json'
curl -i 'https://services.leadconnectorhq.com/contacts/CONTACT_ID/appointments' \
  -H "Authorization: Bearer $HIGHLEVEL_TOKEN" -H 'Version: v3' -H 'Accept: application/json'
```

The app also checks `GET /contacts/CONTACT_ID` to confirm that the referenced contact belongs to the configured sub-account. Missing contact IDs, ambiguous times, or partial calendar records cannot pass. The Phase 0 code uses the single HighLevel token in `.env`; a separate encrypted token per client plus OAuth refresh belong to the pilot-hardening step.

## File structure

```text
app/main.py          API, signed Retell ingestion, simple authenticated UI, demo seed
app/db.py            Postgres tables + immutable observations
app/pipeline.py      call-start manifest binding, drift, extraction
app/extract.py       strict-schema LLM extraction; abstains on uncertainty
app/policy.py        unauthorized-promise flag-only rule
app/highlevel.py     HighLevel GET adapter (no POST/PUT/PATCH/DELETE)
app/verify.py        deterministic match + timed rechecks + observations
app/worker.py        background processor polling DB jobs
app/manifest.py      canonical SHA-256 hashes, time-bound versions
app/time_utils.py    date parsing including ambiguous DST avoidance
app/templates/       findings list and case detail
examples/            example client and manifest JSON
```

## Tests

```bash
# Locally, with Python 3.11+ (a virtualenv recommended):
pip install -r requirements-dev.txt
pytest -q
```

Tests include: 9→10 discrepancy, operator fix → verified, webhook signature rejection, read-only strict field matching, final mismatch → contradicted, DST ambiguity, deterministic manifest hashing.

## What Phase 0 implements

| Phase 0 item | Status |
|---|---|
| Signed Retell call_ended ingestion, call-ID dedupe | Implemented using Retell SDK verifier; test live signing with your account |
| Store transcript, tool trace, agent version/start time | Implemented; key fields extracted from webhook; no full audit storage guarantee |
| HighLevel appointment-by-ID and contact appointments | GET adapters implemented; needs live sub-account verification |
| Final caller-approved booking extraction | Strict LLM schema plus tool identifiers; missing evidence → unknown |
| Deterministic booking verifier / timed rechecks | Implemented; Postgres job persistence |
| Minimal findings page, detail, Re-check button | Implemented; local Basic Auth |
| Manifest v0 hashed/approved, call-start binding | Implemented; role visible; not client-signed |
| Agent version drift finding | Implemented |
| One transcript rule: unauthorized promise | Implemented (LLM labels; deterministic manifest rule; flag-for-review only) |
| Trials + Ed25519 + version chain | Deferred per source document |
| One live agency installation/paid pilot | **Not claimed** |

## Important limitations before using customer data

- **Do not expose this prototype directly on the public internet.** It lacks multi-user RBAC, session-based login, CSRF protection on browser POST forms, robust rate limits, migration tooling, full structured audit logs, encryption-at-rest choices, retention/deletion controls, and full PII/PCI redaction. Its regex-based redaction is demonstrative, not a security guarantee. Use synthetic/test sub-accounts until those are fixed. If tunnelling for a Retell webhook, restrict non-webhook routes upstream and use HTTPS.
- The authenticated dashboard and one shared HighLevel token are suitable only for controlled early testing, not a multi-tenant SaaS. A real agency needs per-client secrets and authorization on every query.
- The LLM parser does **not** decide whether a booking happened. A time guess or source ambiguity must abstain. The Phase 0 extractor supports one booked appointment per call; reschedules, cancellations, duplicates, disambiguating multiple customers, and recurring appointments are deferred.
- The demo's manually specified normalized final booking time is intentionally synthetic, to make the workflow reproducible without a real LLM/API key.
- Live integrations may have payload-schema differences by Retell agent type and HighLevel API version; verify an actual redacted payload before relying on it. This prototype doesn't attest that those account-specific APIs have been tested.
- HighLevel read failures, revoked tokens, ambiguous identity, and ambiguous DST cannot be treated as a successful booking.

## Next engineering slice (week 2)

Evaluate the included unauthorized-promise classifier on **50 genuinely hand-labelled agent sentences** (do not invent precision), improve the agent-drift queue indicators, and add realistic fixtures for delayed writes, duplicate Retell webhooks, a revoked HighLevel token, and the DST transition. **Do not** add full Trials, payments, or real-time sentence blocking before the central calendar check works on a real sub-account.

### Useful official references

- Retell webhook signature overview: https://www.retellai.com/blog/retell-ai-webhooks-feature
- Retell Python SDK: https://github.com/RetellAI/retell-python-sdk
- HighLevel appointment GET: https://marketplace.gohighlevel.com/docs/ghl/calendars/get-appointment/index.html
- HighLevel contact appointments GET: https://marketplace.gohighlevel.com/docs/ghl/contacts/get-appointments-for-contact/index.html
- HighLevel private integrations: https://help.gohighlevel.com/support/solutions/articles/155000003054-private-integrations-everything-you-need-to-know

### Measuring transcript-check precision (week 2)

Create `labels.jsonl` with 50 **real, human-labelled, consented or appropriately anonymized** examples (one per line):

```json
{"transcript":"Agent: I can give you a 20 percent discount.","has_unauthorized_promise":true,"never_promise":["discount"]}
```

The line above is **only a format example**, not a benchmark or a real customer incident. With your API key set, run:

```bash
PYTHONPATH=. python scripts/evaluate_policy.py labels.jsonl
```

It reports denominator, abstentions, TP/FP/FN/TN, measured precision and recall; there is no pre-baked accuracy claim.
