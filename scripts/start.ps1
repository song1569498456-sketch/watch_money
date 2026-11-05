$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")
if (-not (Test-Path ".venv\Scripts\python.exe")) {
    py -3 -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw "Python 3.11+ is required" }
}
& .venv\Scripts\python.exe -m pip install -e .
if ($LASTEXITCODE -ne 0) { throw "Python dependency installation failed" }
Push-Location frontend
try {
    npm ci
    if ($LASTEXITCODE -ne 0) { throw "npm ci failed" }
    npm run build
    if ($LASTEXITCODE -ne 0) { throw "Frontend build failed" }
} finally { Pop-Location }
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
& .venv\Scripts\python.exe -m uvicorn watch_money.app:app --host 127.0.0.1 --port 8000 --env-file .env
