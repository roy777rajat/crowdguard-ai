# Prints row counts so you can confirm data is being saved. Run from any folder.
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
.\.venv\Scripts\Activate.ps1
python -c "import sqlite3; c=sqlite3.connect('data/crowdguard.db'); [print(t, c.execute(f'select count(*) from {t}').fetchone()[0]) for t in ['pandals','zones','cameras','users','sessions','audit','measurements','runs','stages','incidents','llm_calls']]"
