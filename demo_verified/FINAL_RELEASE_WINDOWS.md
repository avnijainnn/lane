# Lane — Consolidated patch / final local check

**Scope:** Local read-only/synthetic pilot; NOT production multi-tenant or HIPAA-ready.

## Use Windows PowerShell

First, stop Uvicorn (`Ctrl+C`). Keep your original database and `.env` safe.

```powershell
Set-Location "C:\Users\91921\Downloads\yc\lane\demo_verified"
$python = "C:\Users\91921\Downloads\yc\lane\.venv\Scripts\python.exe"
Copy-Item .\lane_dataset.db .\lane_dataset.pre_final_patch.db -ErrorAction SilentlyContinue
Expand-Archive -LiteralPath "$env:USERPROFILE\Downloads\Lane_Final_Accountability_Gemini_Patch.zip" -DestinationPath "." -Force
# Keep .env private. If key is not set already, add GEMINI_API_KEY and GEMINI_MODEL in notepad .env.
& $python -m pytest -q
& $python -m scripts.evaluate_accountability
& $python -m scripts.verify_release
# Preview any existing findings missing a case, without changing the DB:
& $python -m scripts.backfill_cases
# Only if you want to create the missing case tickets now:
& $python -m scripts.backfill_cases --apply
```

**API configuration in `.env`:**

```dotenv
GEMINI_API_KEY=your_real_secret_key
GEMINI_MODEL=gemini-3.5-flash-lite
# Optional for future live worker extraction; not needed for local demo:
EXTRACTION_BACKEND=gemini_events
```

If your API key does not grant this model, choose the exact available model ID displayed in Google AI Studio. Paid Google AI Pro does not eliminate API quotas.

**Do not paste the key into ChatGPT or share screenshots of `.env`.**

**Optional small, real Gemini benchmark (sends synthetic/public text externally):**

```powershell
# Preserve your previous comparison before updating the dashboard report
if (Test-Path 'datasets/reports/extraction_challenge.comparison.json') {
  Copy-Item 'datasets/reports/extraction_challenge.comparison.json' 'datasets/reports/extraction_challenge.comparison.before_gemini.json' -Force
}
& $python -m scripts.compare_extractors datasets/extraction_challenge.jsonl --backends rules,gemini,gemini_events --pace-seconds 12 --output datasets/reports/extraction_challenge.comparison.json
```

**Public SGD stress test (unlabelled; do not treat as accuracy):**

```powershell
& $python -m scripts.public_benchmark stress datasets/public/sgd_candidates.jsonl --backend gemini_events --n 20 --allow-cloud --pace-seconds 12 --output datasets/reports/sgd_gemini_events.json
```

If a report exists, rerun with `--resume`; never overwrite your old Groq checkpoint. No live provider calls were run by the local regression tests.

**Start the web app:**

```powershell
$env:DATABASE_URL = 'sqlite:///./lane_dataset.db'
$env:DEMO_MODE = 'true'
& $python -m uvicorn app.main:app --host 127.0.0.1 --port 8001
```

Open `http://127.0.0.1:8001/` (redirects to `/accountability`).

**Optional second terminal for worker:**

```powershell
Set-Location "C:\Users\91921\Downloads\yc\lane\demo_verified"
$env:DATABASE_URL = 'sqlite:///./lane_dataset.db'
$env:DEMO_MODE = 'true'
& "C:\Users\91921\Downloads\yc\lane\.venv\Scripts\python.exe" -m app.worker
```

The UI will display worker heartbeat after one iteration. Uvicorn on its own does NOT run scheduled checks.

**Caveats:** Existing legacy manifests are still visible. Newly registered live calls bind only manifests with valid approval events; historic legacy reports will show reduced approval coverage. Approval identities are typed by the operator, not cryptographically verified. The CSRF safeguard for live browser forms enforces explicit allowed origin; demo mode retains easy local testing. Single-workspace HTTP Basic, bearer tokens and limited redaction are not adequate for customer PII or multi-tenant SaaS. Real HighLevel OAuth/scopes, live Retell, true external signer, tenant authorization, purpose-specific retention, and DPA/compliance remain separate production work. Heuristic simulated Trials are not proof of deployed behavior. Gemini free usage and model availability vary by project.
