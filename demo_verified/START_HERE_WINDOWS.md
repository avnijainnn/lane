# Lane — clean verified recovery release

This is a consistent snapshot of Lane for Windows. It is designed for a **separate folder** named `demo_verified` next to your existing `demo`, not for patching older files one at a time.

**Do not delete your existing `demo` folder or its SQLite database.** Close the old Uvicorn server before copying database files.

PowerShell:

```powershell
$root = "C:\Users\91921\Downloads\yc\lane"
$dest = Join-Path $root "demo_verified"
$zip = Join-Path $env:USERPROFILE "Downloads\Lane_Verified_Clean_Recovery.zip"

if (Test-Path $dest) { throw "demo_verified already exists; choose a new folder or move it aside first" }
New-Item -ItemType Directory -Path $dest | Out-Null
Expand-Archive -LiteralPath $zip -DestinationPath $dest -Force

# Copy user data and secrets locally; these are NOT included in the ZIP
foreach ($name in @(".env", "lane_dataset.db", "lane_dataset.db-wal", "lane_dataset.db-shm")) {
    $source = Join-Path (Join-Path $root "demo") $name
    if (Test-Path $source) { Copy-Item $source (Join-Path $dest $name) }
}

Set-Location $dest
$python = Join-Path $root ".venv\Scripts\python.exe"
if (!(Test-Path $python)) { throw "Virtual environment missing: $python" }

$env:DATABASE_URL = "sqlite:///./lane_dataset.db"
$env:DEMO_MODE = "true"
$env:DEMO_EXTRACTION_BACKEND = "rules"

& $python -m scripts.verify_release
if ($LASTEXITCODE -ne 0) { throw "Release integrity check failed" }
& $python -m pytest -q
if ($LASTEXITCODE -ne 0) { throw "Tests failed; do not proceed" }
& $python -m scripts.evaluate_accountability
if ($LASTEXITCODE -ne 0) { throw "Evaluator failed" }
& $python -m uvicorn app.main:app --host 127.0.0.1 --port 8001
```

Expected results in this release: 96 automated tests, 72 synthetic accountability scenarios. If the copied `.env` sets dashboard credentials, use those at `http://127.0.0.1:8001/accountability`.

The tests use mocked LLM responses. They do not make actual Groq calls or validate production HighLevel calendar writes.

If port 8001 is occupied, stop your existing Lane/Uvicorn server before running this one.
