# Starts the UI on http://localhost:5173. The backend must already be running on port 8000.
$root = Split-Path -Parent $PSScriptRoot
Set-Location (Join-Path $root 'frontend')
if (-not (Test-Path .\node_modules)) { npm install }
npm run dev
