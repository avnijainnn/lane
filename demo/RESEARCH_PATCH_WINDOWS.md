# Lane full-problem research patch — Windows setup

**Important:** Use the patch ZIP, not the full project ZIP. Files are flat relative to your existing `demo` project folder. It never ships a `.env`, `.venv`, or SQLite database.

## 1. Stop Uvicorn and apply patch

```powershell
Set-Location "C:\Users\91921\Downloads\yc\lane\demo"

# Keep a recovery copy of the local database and existing source.
if (Test-Path ".\lane_dataset.db") {
  Copy-Item ".\lane_dataset.db" ".\lane_dataset.backup.db" -Force
}

Expand-Archive -LiteralPath "$env:USERPROFILE\Downloads\Lane_Full_Problem_Research_Patch.zip" -DestinationPath "." -Force

Test-Path ".\app\event_engine.py"
Test-Path ".\scripts\compare_extractors.py"
Test-Path ".\datasets\extraction_extended_synthetic.jsonl"
```

All three checks should print `True`. Your existing `.env`, `.venv` and `lane_dataset.db` are not included or modified by this patch.

## 2. Use existing virtual environment

```powershell
$python = "C:\Users\91921\Downloads\yc\lane\.venv\Scripts\python.exe"
$env:DATABASE_URL = "sqlite:///./lane_dataset.db"
$env:DEMO_MODE = "true"
& $python -m pip install -r requirements.txt
& $python -m pip install -r requirements-dev.txt
& $python -m pytest -q
```


## 3. Compare old and new algorithms on the original eight synthetic cases

Make sure `.env` contains your **Groq** API key as before. Do not share it.

```powershell
& $python -m scripts.compare_extractors datasets/extraction_challenge.jsonl --backends rules,groq,groq_guarded,groq_events --pace-seconds 5
```

This uses 2 cloud model calls per case (raw+guarded share one response; events uses another), unless an error stops the run. `--pace-seconds 5` reduces API burstiness; it is not a guarantee that a free-tier quota will suffice. Provider 429/auth/billing failures abort without publishing a misleading score.

## 4. Evaluate on an additional 22 synthetic conversations

```powershell
& $python -m scripts.compare_extractors datasets/extraction_extended_synthetic.jsonl --backends rules,groq_events --pace-seconds 5 --output datasets/reports/extraction_extended_synthetic.comparison.json
```

**No live Groq accuracy is included in this release.** The old baseline on the new hand-written set is 13/22. The old eight examples were used to develop the code and do not constitute an untouched benchmark.

## 5. Run the dashboard and demo with the new backend

```powershell
$env:DEMO_EXTRACTION_BACKEND = "groq_events"
$env:EXTRACTION_BACKEND = "groq_events"
& $python -m uvicorn app.main:app --host 127.0.0.1 --port 8001
```

Open `/comparison` for the **original-eight** report and `/` for findings. The extended dataset writes a **separate** report file and does not automatically update the `/comparison` page. Existing imported findings do not get reprocessed, by design: they remain stamped with the extractor/version used originally. To test a fresh call, create a new demo call; the existing seed is idempotent, so do not reset history or silently edit old findings.

## 6. What this does NOT deliver

There is no HighLevel integration install, no verified real customer appointment, no reliable ASR/audio assessment, no comprehensive multi-booking reschedule/cancel engine, and no full independent real-world test set. Those remain follow-on work. See `RESEARCH_AND_ARCHITECTURE.md`.
