# Starts the CrowdGuard API on http://localhost:8000 (demo auth, simulated data).
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
if (-not (Test-Path .\.venv)) {
    python -m venv .venv
    .\.venv\Scripts\Activate.ps1
    pip install -r requirements.txt
} else {
    .\.venv\Scripts\Activate.ps1
}
uvicorn backend.api.app:create_app --factory --port 8000
