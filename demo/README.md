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


## No HighLevel account? Run the genuine transcript extraction path

**What changed:** The previous importer handed the verifier a pre-labelled final
booking. The updated importer feeds **only the transcript and observed tool
identifiers** into `process_call()`. The reference commitment is held aside until
after prediction, then scored. Matching to the calendar still uses a *simulated*
HighLevel appointment record, which is clearly labelled synthetic.

There are two separate evaluation tracks:

1. **End-to-end simulated booking tests:** ten paired synthetic conversations and
   simulated calendar records. Extraction is performed on the transcripts instead
   of injecting the expected answer. Any extractor error can now cause a verdict
   mismatch. The `/evaluation` page reports extraction correctness and booking
   verdict correctness separately.
2. **Transcript extraction benchmark:** an independent JSONL evaluation runner
   for annotated conversations, where only transcripts, timezone and call start
   go to the extractor. The labels remain hidden until scoring. It works offline
   (`rules`) or with structured-output LLM extraction (`openai`). The bundled
   eight-example `extraction_challenge.jsonl` stresses relative dates, corrected
   dates, conditional requests, spoken times and unaccepted bookings.

### Windows PowerShell without Docker

From the **new** extracted `lane-phase0` folder:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
$env:DATABASE_URL = 'sqlite:///./lane_dataset.db'
$env:DEMO_MODE = 'true'
$env:DEMO_EXTRACTION_BACKEND = 'rules'
$env:DASHBOARD_USER = 'lane'
$env:DASHBOARD_PASSWORD = 'choose-a-local-password'

# Import 10 scripted calls; extract from transcripts; compare with mock CRM.
.\.venv\Scripts\python.exe -m scripts.import_dataset datasets/booking_cases.jsonl --backend rules --prefix dataset_extracted_v2

# An independent harder benchmark (transcript extraction only).
.\.venv\Scripts\python.exe -m scripts.evaluate_extraction datasets/extraction_challenge.jsonl --backend rules

# Avoid port 8000 if another Uvicorn process is already running.
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8001
```

Open **http://127.0.0.1:8001** and sign in with the configured dashboard
credentials. Click **Dataset extraction evaluation** to see your scenario-level
scores. Click **Load example** to generate a new demo call whose **9→10 AM**
correction is extracted from the transcript by the offline rules backend. The
synthetic calendar still holds 9 AM; the mismatch stays `pending` until the
final verification window (or you fix the example and press Re-check).

If you already used the old importer, keep your existing SQLite database:
new calls use a distinct ID prefix. Original findings remain in your queue.
Change `--prefix` for additional experimental runs; repeated prefixes skip
previously imported calls to preserve the evidence.

### Optional: structured LLM extraction

With an API key, choose the LLM extractor instead:

```powershell
$env:OPENAI_API_KEY = 'YOUR_KEY'
$env:EXTRACTION_MODEL = 'gpt-4o-mini'
.\.venv\Scripts\python.exe -m scripts.import_dataset datasets/booking_cases.jsonl --backend openai --prefix dataset_llm_v1
.\.venv\Scripts\python.exe -m scripts.evaluate_extraction datasets/extraction_challenge.jsonl --backend openai
```

These make API calls that may incur cost. **Never paste real customer transcripts
into a cloud model without permission, de-identification and an appropriate
privacy agreement.** Missing API credentials cause extraction to abstain,
not to invent results.

### Evaluate a public dataset without fabricating calendar labels

Download metadata from
[Arcada Labs Appointment Bench](https://huggingface.co/datasets/arcada-labs/appointment-bench):

```powershell
.\.venv\Scripts\python.exe -m scripts.prepare_appointment_bench
```

That public benchmark includes dialogue turns/tool-call annotations, **not**
independently observed HighLevel appointment records. Its turn metadata cannot
simply be assumed to be complete booked call transcripts. Reconstruct full
conversations **only where supported by the data**, annotate the final accepted
request manually, and export evaluation JSONL in this format:

```json
{"id":"conversation_1","transcript":"Caller: Book November 3 at 10 AM.\nAgent: Confirmed.","timezone":"America/Chicago","call_started_at":"2026-10-22T14:00:00Z","commitment":{"confirmed":true,"final_start":"2026-11-03T10:00:00-06:00"}}
```

Use `python -m scripts.evaluate_extraction YOUR_ANNOTATED_FILE.jsonl --backend rules`.
For a robust measurement, annotate and review an independent sample; don't
report this project's curated fixture results as measured external accuracy.

### Synthetic fixture scenarios

The `datasets/booking_cases.jsonl` set includes correct booking, 9→10
correction, booking absent from record, wrong contact/calendar, incomplete
record, delayed write, availability-only question, missing contact, and
agent-version drift. Booking verification reads a fresh mock CRM record and
follows the same deterministic matching path as the live HighLevel adapter.

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
- The demo fixture is synthetic but now **parses the final accepted booking time from its transcript** using the selected extraction backend; it no longer supplies the target time as an answer to the extractor.
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

## Phase 0: rules versus LLM benchmark (October 2026)

**What this adds:** `scripts/compare_extractors.py` runs the *same*
independently labelled transcript challenge through the rules baseline and an
OpenAI structured-output extractor. The gold labels are used only after each
prediction. It produces JSON and Markdown reports and a local, Basic-Auth
protected dashboard at `http://127.0.0.1:8001/comparison`.

**From the folder with `app/`, `scripts/`, and `datasets/`**, open a *new*
PowerShell terminal. Reuse your existing virtualenv (adapt `$python` if yours
is elsewhere):

```powershell
$python = 'C:\Users\91921\Downloads\yc\lane\.venv\Scripts\python.exe'
$env:DEMO_MODE = 'true'

# Free baseline, no external services, produces only rules scores
& $python -m scripts.compare_extractors datasets/extraction_challenge.jsonl --backends rules
```

To run **both** extractors, put your API key in the `.env` file **inside this
project** using `OPENAI_API_KEY=...` (never commit or share `.env`). The runner
loads `.env` automatically; model defaults to `gpt-4o-mini`, configurable via
`EXTRACTION_MODEL` in `.env`. This calls the paid OpenAI API:

```powershell
& $python -m scripts.compare_extractors datasets/extraction_challenge.jsonl --backends rules,openai
```

Generated files:

- `datasets/reports/extraction_challenge.comparison.json`
- `datasets/reports/extraction_challenge.comparison.md`
- `http://127.0.0.1:8001/comparison` (restart Uvicorn to load new route)

The report includes total exact accuracy *with abstentions counted incorrect*,
coverage, false bookings, missed bookings and wrong times. It explicitly avoids
saving raw transcripts, quoted evidence or API keys. **No OpenAI scores are
claimed until you run the live API call successfully.** If credentials are
missing, the script exits with an error rather than calling a non-model
fallback and counting it as an LLM run.

The eight bundled challenge rows are **synthetic**. On 22 Oct 2026, the
existing unmodified `rules` baseline scored **2/8 exact, 3/8 covered,
1 false booking**, not real-world accuracy. The booking-landing demonstration
uses synthetic calendar records. More robust evidence requires a separate,
independently annotated set of complete conversations. The
`prepare_appointment_bench.py` downloader fetches publicly available
**turn metadata**; it does not invent full agent call transcripts, independent
booking confirmations or calendar ground truth. Once real, consented and
appropriately de-identified dialogue annotations are ready, run the comparator
on their JSONL using the same schema and `--output <another-file>.json`.

**To avoid losing data:** apply only the changed-files ZIP over the existing
project; do not delete `.venv`, `.env`, or `lane_dataset.db`. This benchmark is
separate from the SQLite findings queue and does not rewrite old calls.


## Groq (no OpenAI API credits needed) — rules vs LLM

Groq supports the OpenAI-compatible chat-completions endpoint and strict JSON schema on
`openai/gpt-oss-120b`. This is a separate account/key and subject to GroqCloud's free
tier limits. It does **not** use your OpenAI billing credits. Never commit API keys.

For your Windows installation at `C:\Users\91921\Downloads\yc\lane\demo`:

```powershell
Set-Location 'C:\Users\91921\Downloads\yc\lane\demo'
# Open .env and add GROQ_API_KEY=gsk_... (keep private)
notepad .env
$python = 'C:\Users\91921\Downloads\yc\lane\.venv\Scripts\python.exe'
$env:DEMO_MODE = 'true'
$env:DATABASE_URL = 'sqlite:///./lane_dataset.db'
& $python -m scripts.compare_extractors datasets/extraction_challenge.jsonl --backends rules,groq
& $python -m uvicorn app.main:app --host 127.0.0.1 --port 8001
```

Open http://127.0.0.1:8001/comparison. To run dataset ingestion through Groq:

```powershell
& $python -m scripts.import_dataset datasets/booking_cases.jsonl --backend groq --prefix groq_run_1
```

If you want **Load example** to use Groq too, set `DEMO_EXTRACTION_BACKEND=groq`
in `.env`, then restart Uvicorn. For the existing offline demo, leave it `rules`.
If you choose Groq for live extraction set `EXTRACTION_BACKEND=groq`, but get
permission and apply data privacy controls before sending customer transcripts
to a hosted model.

The comparison runner will not write a Groq accuracy score on API failures.
It validates transcript extraction only; the demo mock calendar is not real HighLevel.


## DST-safe extraction guard (October 2026)

For `groq_guarded` only, Lane now runs **two separate deterministic safeguards**
after exactly one Groq extraction: explicit correction recovery and business-zone
validation. The latter uses the IANA database through `zoneinfo`. We include
`tzdata` in `requirements.txt` for Windows, where IANA zone data may not be
installed. If the offset conflicts with the business timezone, the guard will
only reinterpret the local wall time when the caller's transcript includes a
matching explicit date/time; if the evidence is uncertain it abstains. Genuine
UTC representations of the same instant remain valid. On DST folds (repeated
hours) and gaps (nonexistent hours), the guard abstains pending clarification.

```powershell
Set-Location 'C:\Users\91921\Downloads\yc\lane\demo'
$python = 'C:\Users\91921\Downloads\yc\lane\.venv\Scripts\python.exe'
& $python -m pip install -r requirements.txt
& $python -m scripts.compare_extractors datasets/extraction_challenge.jsonl --backends rules,groq,groq_guarded
$env:DEMO_EXTRACTION_BACKEND = 'groq_guarded'
& $python -m uvicorn app.main:app --host 127.0.0.1 --port 8001
```

`http://127.0.0.1:8001/comparison` shows the new paired evaluation once
the CLI completes. **Do not assert a new Groq score without running live
calls yourself**; unit tests use mocked provider responses. This does not
confirm production HighLevel calendar writes or real-world accuracy.

Relevant upstream guidance: Python zoneinfo docs
https://docs.python.org/3/library/zoneinfo.html ; PEP 495
https://peps.python.org/pep-0495/ ; dateutil gap/fold checks
https://dateutil.readthedocs.io/en/stable/tz.html .

## Research-driven typed event extraction (`groq_events`) — experimental

The earlier direct-output `groq` / `groq_guarded` extractors are unchanged. New
`groq_events` has Groq return a sequence of **quoted, turn-linked, structured
conversation events** (caller requests/revisions/rejections, agent offers,
caller acceptance and agent confirmation). A deterministic Python reducer checks
who said what, which candidate time was accepted, chronology, local date/time,
zoneinfo DST validity and whether the agent confirmed that same slot. Unsupported
or ambiguous cases return review/abstain, not a fabricated verified booking.

See `RESEARCH_AND_ARCHITECTURE.md` for source evidence, covered failure modes,
known limits and next validation gates. `RESEARCH_PATCH_WINDOWS.md` gives commands
for the existing Windows `lane\demo` installation. Test against the original
8-case set **and** `datasets/extraction_extended_synthetic.jsonl` (22 additional
synthetic cases). All labels remain hidden from the model. Run with `--pace-seconds 5`
on a free-tier Groq key to reduce bursts, though it does not guarantee quota.

A flagged `unaccepted_booking_claim` is a **human-review only** indicator; this
patch never creates, edits or cancels an appointment. The fresh CRM verification
engine remains separate. **No live Groq or HighLevel verification score was
measured when preparing this patch.**

## NEW: Dataset-first agency accountability layer

See [ACCOUNTABILITY_SETUP_WINDOWS.md](ACCOUNTABILITY_SETUP_WINDOWS.md) for an exact Windows setup and a guided demo of the five product-spec features:

1. Manifest drafts + named client/agency approval of a hash/version/agent binding.
2. Three-way reconciliation of policy permission, customer agreement and observed calendar state.
3. Cross-client resolution queue with assignment, contact tracking, simulated operator repairs and independent re-check.
4. Client evidence report with denominators, historical approval versions and case outcomes.
5. Scenarios compiled from the approved manifest and evaluated on explicitly synthetic agent responses.

The linked fixture contains three fictional clients, six representative calls and a calendar observation timeline. The separate `scripts.evaluate_accountability` command runs 72 more paired synthetic scenarios in a **temporary SQLite database** and records expected vs actual results in `datasets/reports/accountability_regression.json`. These checks do not require API keys or a running server. They do not substantiate real-world accuracy or live HighLevel write verification.

**Run offline on Windows in the project root:**

```powershell
$env:DATABASE_URL = 'sqlite:///./lane_dataset.db'
$env:DEMO_MODE = 'true'
$env:DEMO_EXTRACTION_BACKEND = 'rules'
& 'C:\Users\91921\Downloads\yc\lane\.venv\Scripts\python.exe' -m scripts.seed_agency_world
& 'C:\Users\91921\Downloads\yc\lane\.venv\Scripts\python.exe' -m scripts.evaluate_accountability
& 'C:\Users\91921\Downloads\yc\lane\.venv\Scripts\python.exe' -m uvicorn app.main:app --host 127.0.0.1 --port 8001
```

Open `/accountability` and use existing dashboard Basic authentication. **Do not deploy the new UI publicly** before replacing single-workspace HTTP Basic with multi-tenant permission checking and CSRF protection.
