# Lane — DST/timezone patch (Windows)

## What the patch does

- Uses `ZoneInfo` + packaged `tzdata` to validate the business-local time.
- Keeps a true UTC timestamp's instant intact when source evidence supports it.
- Corrects `2026-11-03T09:00:00-05:00` to
  `2026-11-03T09:00:00-06:00` **only** when transcript evidence supports
  November 3 at 9 AM in America/Chicago.
- Rejects repeated or nonexistent DST wall times as **abstained**.
- Applies to `groq_guarded` only; raw `groq` remains an untouched baseline.
- Makes only one Groq API call per case for paired reports.
- No migration, no database deletion, no HighLevel required for the benchmark.

## Installation

1. Stop Uvicorn with Ctrl+C.
2. Download `Lane_DST_Timezone_Guard_Patch.zip` to `Downloads`.
3. In a fresh PowerShell window, paste the commands **one line at a time**
   or as a multiline block that preserves newlines:

```powershell
Set-Location 'C:\Users\91921\Downloads\yc\lane\demo'
Expand-Archive -LiteralPath "$env:USERPROFILE\Downloads\Lane_DST_Timezone_Guard_Patch.zip" -DestinationPath '.' -Force
$python = 'C:\Users\91921\Downloads\yc\lane\.venv\Scripts\python.exe'
& $python -m pip install -r requirements.txt
$env:DEMO_MODE = 'true'
$env:DATABASE_URL = 'sqlite:///./lane_dataset.db'
& $python -m scripts.compare_extractors datasets/extraction_challenge.jsonl --backends rules,groq,groq_guarded
$env:DEMO_EXTRACTION_BACKEND = 'groq_guarded'
& $python -m uvicorn app.main:app --host 127.0.0.1 --port 8001
```

The comparison command automatically reads `GROQ_API_KEY` from `.env` in
this project, assuming it is already configured. Don't share your key.
Refresh `http://127.0.0.1:8001/comparison` after the comparison completes.

## Verify just the deterministic guard (no Groq key, no bill)

```powershell
& $python -m pytest tests/test_timezone_guard.py -q
```

Install `requirements-dev.txt` if `pytest` is missing:

```powershell
& $python -m pip install -r requirements-dev.txt
```

## Important

- The live LLM score may vary between runs; we cannot claim the new guarded
  system is 8/8 until you run it.
- If a local time is ambiguous, Lane abstains instead of silently taking the
  first/second occurrence. Customer confirmation is needed for production.
- The guarded route only affects future calls. Existing recorded extraction
  results in SQLite are not retroactively rewritten.
- The synthetic benchmark is not proof of a real HighLevel calendar write.
