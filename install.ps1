# CrowdGuard AI - one-line installer for Windows (PowerShell).
#   irm https://raw.githubusercontent.com/roy777rajat/crowdguard-ai/main/install.ps1 | iex
# This small script only makes sure Python 3.10+ exists, then hands over to the guided installer.
$ErrorActionPreference = 'Stop'
$Raw = if ($env:CROWDGUARD_RAW) { $env:CROWDGUARD_RAW } else { 'https://raw.githubusercontent.com/roy777rajat/crowdguard-ai/main' }
function Say($c, $t) { Write-Host $t -ForegroundColor $c }
Say Cyan 'CrowdGuard AI - bootstrap'

function Find-Python {
  foreach ($cand in @(@('py','-3'), @('python'), @('python3'))) {
    $exe = $cand[0]
    if (-not (Get-Command $exe -ErrorAction SilentlyContinue)) { continue }
    try {
      $extra = @(); if ($cand.Count -gt 1) { $extra = $cand[1..($cand.Count-1)] }
      & $exe @extra -c "import sys;sys.exit(0 if sys.version_info>=(3,10) else 1)" 2>$null
      if ($LASTEXITCODE -eq 0) { return ,(@($exe) + $extra) }
    } catch {}
  }
  return $null
}

$py = Find-Python
if (-not $py) {
  Say Yellow '[WARN] Python 3.10+ not found.'
  if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
    Say Red '[FAIL] winget is not available. Install Python 3.10+ from https://www.python.org/downloads/ (tick "Add to PATH") and run this again.'
    return
  }
  $a = Read-Host 'Install Python now with winget? [Y/n]'
  if ($a -match '^[nN]') { Say Red 'Install Python 3.10+ and run this again.'; return }
  winget install -e --id Python.Python.3.12 --accept-package-agreements --accept-source-agreements
  $env:Path = [Environment]::GetEnvironmentVariable('Path','Machine') + ';' + [Environment]::GetEnvironmentVariable('Path','User')
  $py = Find-Python
  if (-not $py) { Say Red '[FAIL] Python was installed but is not visible yet. Close this window, open a new PowerShell and run the one-liner again.'; return }
}
Say Green ('[ OK ] Using Python: ' + ($py -join ' '))

$tmp = Join-Path ([IO.Path]::GetTempPath()) ('cg-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $tmp | Out-Null
$dst = Join-Path $tmp 'i.py'
Say Blue '[ .. ] Fetching the installer'
try {
  if (Test-Path "$Raw/installer/crowdguard_installer.py") { Copy-Item "$Raw/installer/crowdguard_installer.py" $dst }
  else { Invoke-WebRequest -UseBasicParsing "$Raw/installer/crowdguard_installer.py" -OutFile $dst }
} catch { Say Red "[FAIL] Could not download the installer from $Raw. Check internet / proxy. $_"; return }
$extra = @(); if ($py.Count -gt 1) { $extra = $py[1..($py.Count-1)] }
& $py[0] @extra $dst @args
