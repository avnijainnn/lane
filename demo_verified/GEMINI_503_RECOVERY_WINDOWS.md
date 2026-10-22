# Lane: HTTP 503 recovery for the Gemini benchmark

This additive patch changes only `scripts/public_benchmark.py`, `tests/test_public_stress_resume.py`, and `RELEASE_SHA256.json`. It does not include or overwrite `.env`, SQLite data, or `datasets/reports` checkpoints. It expects the *Final Integrated Accountability + Gemini Patch* and the *Gemini HTTP Diagnostics Fix* to have been installed.

## Apply safely in Windows PowerShell

```powershell
Set-Location "C:\Users\91921\Downloads\yc\lane\demo_verified"
$python = "C:\Users\91921\Downloads\yc\lane\.venv\Scripts\python.exe"
Copy-Item "datasets\reports\sgd_new_relevant_gemini.json" "datasets\reports\sgd_new_relevant_gemini.before_503_fix.json" -Force
Expand-Archive -LiteralPath "$env:USERPROFILE\Downloads\Lane_Gemini_503_Recovery.zip" -DestinationPath "." -Force
& $python -m scripts.verify_release
& $python -m pytest tests/test_public_stress_resume.py -q
```

## Resume, with bounded retries and skip transient errors

```powershell
& $python -m scripts.public_benchmark stress datasets/public/sgd_new_relevant_100.jsonl --backend gemini_events --n 100 --start-at 36 --resume --retry-provider-errors --allow-cloud --pace-seconds 30 --transient-retries 2 --transient-backoff-seconds 3 --skip-transient-errors --output datasets/reports/sgd_new_relevant_gemini.json
```

`--transient-retries 2` means at most **two extra API requests per case**, only after HTTP 408/500/502/503/504. Delays start at 3s and grow exponentially with jitter. After retries, a persistently unavailable case stays as a `provider_error` in the checkpoint; `--skip-transient-errors` allows the run to continue. Three consecutive provider-error cases stop the run. HTTP 429 (rate limits), HTTP 400 (invalid request), and authentication failures still stop the run; no model score is invented. If Google stays overloaded, no client-side patch can guarantee throughput: preserve the checkpoint, use offline rules, or try a different provider/model as a separate run.

## Read current counts without any network requests

```powershell
$report = Get-Content "datasets/reports/sgd_new_relevant_gemini.json" -Raw | ConvertFrom-Json
$report | Select-Object requested_n,evaluated_n,provider_error_n,unprocessed_n,status
$report.cases | Where-Object {$_.status -eq 'provider_error'} | Select-Object index,id,diagnostic
```

This dataset is unlabeled; `evaluated_n` counts completed model outputs, **not verified booking accuracy**.
