# Lane: corrected-booking safeguard for Groq

This is an incremental patch for the Lane Phase 0 project with `scripts/compare_extractors.py` already installed. **No database migration is needed** and your `lane_dataset.db` is not included.

## What changed

- `app/extract.py`: adds `groq_guarded`, a strict correction-consistency check applied only after a completed Groq response. It can recover a clearly confirmed caller correction when the LLM says no booking or abstains. Requires matching explicit date AND AM/PM time repeated by the agent; refuses if the caller later retracts or any input is ambiguous. An API error can never be converted into a positive booking.
- `scripts/compare_extractors.py`: benchmark `rules`, `groq`, `groq_guarded`. **Raw Groq and guarded Groq share the same live API response per case**, avoiding sampling differences and duplicate Groq calls.
- `scripts/import_dataset.py` and `scripts/evaluate_extraction.py`: accept the `groq_guarded` backend.
- `tests/test_correction_guard.py`: regression tests for correction rescue, wrong confirmation, rejection, availability-only, DST ambiguity, API failures, and paired evaluation.

## Install (PowerShell)

Stop Uvicorn with `Ctrl+C`, then:

```powershell
cd "C:\Users\91921\Downloads\yc\lane\demo"
Expand-Archive -LiteralPath "$env:USERPROFILE\Downloads\Lane_Correction_Guard_Changed_Files.zip" -DestinationPath "." -Force
$python = "C:\Users\91921\Downloads\yc\lane\.venv\Scripts\python.exe"
& $python -m pytest -q
```

If `pytest` is missing, install it in the same environment for local tests:

```powershell
& $python -m pip install pytest
```

The package does not contain `.env`, SQLite files or other application data. **Never replace your existing database** with a file from a ZIP.

## Compare original Groq and guarded Groq

```powershell
$env:DEMO_MODE = "true"
$env:DATABASE_URL = "sqlite:///./lane_dataset.db"
& $python -m scripts.compare_extractors datasets/extraction_challenge.jsonl --backends rules,groq,groq_guarded
& $python -m uvicorn app.main:app --host 127.0.0.1 --port 8001
```

Open <http://127.0.0.1:8001/comparison>. **This is a new model-backed test; there is no guaranteed score** until your account runs it.

Set `DEMO_EXTRACTION_BACKEND=groq_guarded` if you want this hybrid to be used when you click **Load example**. The default live extraction backend is still controlled separately by `EXTRACTION_BACKEND`.

## Scope and caveats

- Works without HighLevel, but it **does not verify actual calendar writes**. This is a transcript-only extraction improvement.
- Requires caller corrections and corresponding agent confirmations with explicit English month/day and AM/PM times in separate speaker-labelled lines. It deliberately leaves spoken times, relative dates, missing agent times, and uncertain consent to Groq (or abstains).
- Existing dataset gold is never passed to the extractor. The added tests mock Groq. No live Groq calls were made while building this patch.
- Report `groq_guarded` separately from `groq`; it is a different hybrid extraction system, not a better raw model.
