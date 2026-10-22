# Lane Groq setup on Windows — no OpenAI API credits required

This updates your existing Lane project without replacing `lane_dataset.db`, `.venv` or `.env`.

1. Stop running Uvicorn with Ctrl+C.
2. From a PowerShell window, download `Lane_Groq_Free_Tier_Patch.zip` into Downloads.
3. Run:

```powershell
Set-Location 'C:\Users\91921\Downloads\yc\lane\demo'
Expand-Archive -LiteralPath "$env:USERPROFILE\Downloads\Lane_Groq_Free_Tier_Patch.zip" -DestinationPath '.' -Force
Test-Path '.\scripts\compare_extractors.py'
Test-Path '.\app\extract.py'
if (!(Test-Path '.\.env')) { New-Item -ItemType File -Path '.\.env' | Out-Null }
notepad .env
```

Add `GROQ_API_KEY=your_actual_key` (obtain from https://console.groq.com/keys) and
`GROQ_EXTRACTION_MODEL=openai/gpt-oss-120b` in `.env`.
Save the file. Never share or commit your key.

4. Run the benchmark (this makes real Groq API calls within your own Groq plan):

```powershell
$python = 'C:\Users\91921\Downloads\yc\lane\.venv\Scripts\python.exe'
$env:DATABASE_URL = 'sqlite:///./lane_dataset.db'
$env:DEMO_MODE = 'true'
& $python -m scripts.compare_extractors datasets/extraction_challenge.jsonl --backends rules,groq
```

5. Run the dashboard and open `http://127.0.0.1:8001/comparison`:

```powershell
& $python -m uvicorn app.main:app --host 127.0.0.1 --port 8001
```

6. Optional: test the full synthetic fixture importer through Groq:

```powershell
& $python -m scripts.import_dataset datasets/booking_cases.jsonl --backend groq --prefix groq_fixture_1
```

For the *Load example* button to use Groq, add `DEMO_EXTRACTION_BACKEND=groq` to
`.env` and restart Uvicorn. Keep it `rules` if you want that button offline.

**Limitations:** The test fixtures and calendar records are synthetic. A 10/10
scripted fixture result is not measured real-world accuracy. The Groq comparison
runner never counts an API authentication, billing or rate-limit failure as an
LLM accuracy score. No live Groq API calls were performed while building this patch.
