# Lane Groq stress test recovery, October 22 2026

This patch only replaces `app/extract.py`, `scripts/public_benchmark.py`, and adds `tests/test_public_stress_resume.py`. It does not touch SQLite, .env, source data or existing annotations. Source files were tested with Lane's public-dataset benchmark release.

## Install
```powershell
Set-Location "C:\Users\91921\Downloads\yc\lane\demo_verified"
$python = "C:\Users\91921\Downloads\yc\lane\.venv\Scripts\python.exe"
Copy-Item app\extract.py app\extract.py.pre_stress_fix.bak -Force
Copy-Item scripts\public_benchmark.py scripts\public_benchmark.py.pre_stress_fix.bak -Force
Expand-Archive -LiteralPath "$env:USERPROFILE\Downloads\Lane_Groq_Stress_Recovery_Patch.zip" -DestinationPath "." -Force
& $python -m pytest tests/test_public_stress_resume.py -q
& $python -m pytest -q
```

## Option A: Recover from conversation 43 with fewer API calls (incomplete sample)
Your prior 42 responses were never saved. Do not claim 100 complete when starting at 43.
```powershell
& $python -m scripts.public_benchmark stress `
 datasets/public/sgd_candidates.jsonl `
 --backend groq_events --n 100 --start-at 43 `
 --allow-cloud --continue-on-bad-request --pace-seconds 8 `
 --output datasets/reports/sgd_groq_events_recovery.json
```
If case 43 is rejected with 400, it is recorded as a provider error, not an abstention or correct prediction. The run continues unless 3 consecutive 400 errors or another error type occurs. After completion, the report explicitly indicates that 1..42 remain unprocessed. To fill them in later (this costs new Groq calls):
```powershell
& $python -m scripts.public_benchmark stress `
 datasets/public/sgd_candidates.jsonl `
 --backend groq_events --n 100 --start-at 1 `
 --resume --allow-cloud --continue-on-bad-request --pace-seconds 8 `
 --output datasets/reports/sgd_groq_events_recovery.json
```
To retry the saved failed case after investigating the API 400:
```powershell
& $python -m scripts.public_benchmark stress `
 datasets/public/sgd_candidates.jsonl `
 --backend groq_events --n 100 --start-at 43 `
 --resume --retry-provider-errors --allow-cloud --continue-on-bad-request --pace-seconds 8 `
 --output datasets/reports/sgd_groq_events_recovery.json
```

## Option B: Complete new test of 100 from scratch (reissues 42 calls)
```powershell
& $python -m scripts.public_benchmark stress `
 datasets/public/sgd_candidates.jsonl `
 --backend groq_events --n 100 `
 --allow-cloud --continue-on-bad-request --pace-seconds 8 `
 --output datasets/reports/sgd_groq_events_checkpointed.json
```
After an interruption, repeat with `--resume` and the same output file.

## Privacy and method
- The extractor sends transcript text to Groq. Check applicable licenses, privacy terms and customer consents before real customer data.
- Error reports include allowlisted provider error *class*, HTTP status, `code`, `type`, `param` if available, not raw Groq messages or transcripts.
- No change to the LLM prompt/schema/decision algorithm.
- The artificial timezone + call start is not historical truth. This is unlabelled extraction stress, NOT a real accuracy measurement or HighLevel booking verification.
- `provider_error_n` is excluded from `evaluated_n` (not counted as model abstention).
- `--start-at 43` does not recover the 42 unsaved predictions; they remain missing until explicitly rerun.
