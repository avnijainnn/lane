# Lane SGD Dashboard Recording Fix — Windows

Fixes the presentation problems demonstrated in the Oct 23 screen recording, not the underlying Gemini extraction algorithm.
- Puts the 100-case SGD Gemini/Rules benchmark first.
- Moves the old 8-example synthetic check into a collapsible historical section.
- Clarifies that abstentions are unresolved predictions, not a proof of non-booking or accuracy.
- Labels common SGD phrasing (24-hour times, ordinal dates, half-past expressions) as *possible parser gaps*, never as ground truth.
- Shows that the old report has no stored abstention reason.
- Fixes the Findings navigation links touching each other.
- Does not touch the database, API keys, checkpoint JSON or candidate datasets.

In PowerShell (stop uvicorn first):

    cd "C:\Users\91921\Downloads\yc\lane\demo_verified"
    $python = "C:\Users\91921\Downloads\yc\lane\.venv\Scripts\python.exe"
    Expand-Archive -LiteralPath "$env:USERPROFILE\Downloads\Lane_Screen_Recording_UI_Fix.zip" -DestinationPath "." -Force
    & $python -m scripts.verify_release
    & $python -m pytest tests/test_sgd_dashboard.py -q
    & $python -m uvicorn app.main:app --host 127.0.0.1 --port 8001

Then open http://127.0.0.1:8001/comparison#sgd100

If other project files were manually changed, verify_release may report MISMATCH for those unchanged files. Do not overwrite your .env, database or results to fix such a mismatch.

Operational status in the recording: "worker offline or stale" means the web server is running but no recent background worker heartbeat. 18 due checks and HighLevel token configured: no are separate from the SGD benchmark. Running the worker requires a second terminal and correct permissions; this patch does not provision CRM credentials. Two Bright Smile Dental demo rows are separate fixture clients with different location IDs, not accidental duplicated production records.

IMPORTANT: The saved Gemini checkpoint has only outcome booleans; individual abstention reasons cannot be recovered without generating and saving a new prediction or richer raw events. This patch makes the limitation transparent. It does not claim to improve extraction accuracy.
