Lane accountability dependency repair (Windows)

Extract this ZIP into C:\Users\91921\Downloads\yc\lane\demo
It contains app/timezone_guard.py only. Your database and .env are untouched.

PowerShell:
  Set-Location "C:\Users\91921\Downloads\yc\lane\demo"
  Expand-Archive -LiteralPath "$env:USERPROFILE\Downloads\Lane_Accountability_Complete_Dependency_Fix.zip" -DestinationPath "." -Force
  $python = "C:\Users\91921\Downloads\yc\lane\.venv\Scripts\python.exe"
  & $python -c "from app.time_utils import unique_local_wall, AmbiguousTime; import app.timezone_guard; import app.event_engine; import app.accountability; print('Imports OK')"
  & $python -m pytest -q
