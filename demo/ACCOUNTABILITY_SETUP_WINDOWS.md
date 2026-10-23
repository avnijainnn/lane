# Lane — linked accountability dataset (Windows PowerShell)

This is an additive source-code patch for the existing **`C:\Users\91921\Downloads\yc\lane\demo`** folder. It does **not** require HighLevel, Retell, Groq, Docker, or OpenAI credentials to run the synthetic demo and deterministic evaluator.

## 1) Install the patch without deleting your DB

Stop Uvicorn with `Ctrl+C`. Download `Lane_Accountability_Dataset_Patch.zip` into Downloads and run:

```powershell
Set-Location 'C:\Users\91921\Downloads\yc\lane\demo'
$python = 'C:\Users\91921\Downloads\yc\lane\.venv\Scripts\python.exe'

# Back up your existing SQLite evidence first.
if (Test-Path '.\lane_dataset.db') {
  Copy-Item '.\lane_dataset.db' '.\lane_dataset.pre_accountability.backup.db' -Force
}
Expand-Archive -LiteralPath "$env:USERPROFILE\Downloads\Lane_Accountability_Dataset_Patch.zip" -DestinationPath '.' -Force

$env:DATABASE_URL = 'sqlite:///./lane_dataset.db'
$env:DEMO_MODE = 'true'
$env:DEMO_EXTRACTION_BACKEND = 'rules'

& $python -m pip install -r requirements.txt
& $python -m scripts.seed_agency_world
& $python -m scripts.evaluate_accountability
& $python -m uvicorn app.main:app --host 127.0.0.1 --port 8001
```

If port 8001 is already used, change the `--port` to 8002. Keep `DATABASE_URL` the same for seeding and serving. Do not delete `lane_dataset.db`; `Base.metadata.create_all()` adds new tables and does not drop your existing ones.

The evaluator **creates its own temporary SQLite DB** and writes `datasets/reports/accountability_regression.json` without modifying `lane_dataset.db`.

## 2) Open the five feature flows

- **Clients / manifest approval:** http://127.0.0.1:8001/accountability
- **Resolution queue:** http://127.0.0.1:8001/accountability/queue
- **Existing findings:** http://127.0.0.1:8001/
- **Client evidence report:** Open any client, then select *Client evidence report*.
- **Trials:** Open any client, then click *Manifest-derived trials* on an approved version.

Sign in using your current `DASHBOARD_USER` and `DASHBOARD_PASSWORD` from `.env` or your PowerShell environment.

## 3) Suggested end-to-end demo

1. Open **Clients**. See three fictional businesses, approved manifests, and a pending **Bright Smile v2** draft (not active).
2. Open Bright Smile's client page. Approve v2 as a **client**. Old calls remain linked to v1 and the hash that was approved at their start.
3. Open **Resolution queue**. Find the wrong-time Bright Smile booking. Open the case: authorization allowed; caller consent accepted; calendar execution contradicted. Assign the case to an operator.
4. Click **Simulate fixing local calendar only**, then choose **Fresh calendar recheck**. Only that fresh read closes the case as `verified_closed`. The original contradiction and action history remain available.
5. Find **Cedar Repairs**. Its booking is present in a simulated calendar but **not authorized** by its approved `allowed_actions`. See both the booking finding and a separate policy finding. Review the policy issue rather than conflating calendar success with permission.
6. Download Bright Smile's **Client evidence report (JSON)** and inspect denominators plus the named approval role and policy hash.
7. Open **Manifest-driven Trials**. Each approved rule generated a scenario. See synthetic passing, failing, repeated and `not_run` outcomes. You can run the same fixtures again.

## 4) Validate all five workflows with datasets

```powershell
$python = 'C:\Users\91921\Downloads\yc\lane\.venv\Scripts\python.exe'
& $python -m scripts.evaluate_accountability
& $python -m pip install -r requirements-dev.txt
& $python -m pytest -q
```

`accountability_regression_72.jsonl` contains 72 templated synthetic conversation/record pairs covering correct bookings, wrong time, wrong identity, wrong calendar, missing record, delayed write, availability-only, agent-version drift, and unauthorized actions. **These are not real calls or independent benchmark accuracy measurements.** Ground-truth expectations are used only *after* the extractor and calendar verifier return their outputs.

## 5) What's real in this build, and what isn't

Works locally: draft and approve manifest with hash chain linking, timestamp-bound call evaluation, three-way reconciliation, assigned cases with append-only-by-application event records, simulated repairs followed by calendar re-read, client report and manifest-derived synthetic trial scenarios. New tables are added to SQLite; existing findings remain.

Still needs a pilot / production engineering: genuine customer approval-link identity verification, agency-level tenant authorization, CSRF protections for browser forms, tamper-evident external audit log/Ed25519 signatures, live Retell trials, real HighLevel credentials/reads, sustained retries and coverage monitoring, real independent customer incidents, and payment validation. HTML login here is single-workspace HTTP Basic, **not** a production authentication system. The trial judge uses simple deliberately conservative synthetic checks; it is not a replacement for voice simulation or real calls. No automated HighLevel writes are provided.
