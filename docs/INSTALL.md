# Install CrowdGuard AI (guided installer)

One command. It asks a few questions, checks your computer, installs what is missing, starts the app, and
proves it works (backend, database tables and counts, sign-in, web screens).

**Windows (PowerShell)**
```powershell
irm https://raw.githubusercontent.com/roy777rajat/crowdguard-ai/main/install.ps1 | iex
```

**macOS / Linux**
```bash
curl -fsSL https://raw.githubusercontent.com/roy777rajat/crowdguard-ai/main/install.sh | bash
```

Needs internet. The small script above only makes sure Python 3.10+ exists (it asks before installing it), then
runs `installer/crowdguard_installer.py`, which does the rest in 9 colour-coded steps.

## What the 9 steps do
1. Choose install folder (default `C:\CrowdGuard` or `~/CrowdGuard`; an existing install is updated, data kept)
2. Check your computer: OS, Python 3.10+, Node.js 20+, internet (GitHub, PyPI, npm), disk, ports 8000 and 5173
3. Get the code (git clone/pull; ZIP download if git is missing)
4. Private Python environment (`.venv`) and packages, then an import check
5. Web packages and a compile check
6. Settings: optional AI provider (Meta) and key, voice opt-in, writes a private `.env`; prepares `gallery/`
7. Self-test: backend (pytest) and web (Vitest) tests
8. Start backend (port 8000) and web screens (port 5173) in the background
9. Verify: health, database file, every table with row counts, demo sign-in, API counts, web-to-API link

## AI helper (optional, failures only)
Everything above is fixed, predictable code. If you give a Meta key at the start and a step fails, the installer
first tries built-in fixes, then asks Muse Spark to pick **one fix from a fixed menu** (for example "clear the npm
cache" or "rebuild .venv"). It shows you the suggestion and applies it only if you answer Yes. It cannot run its
own commands. Only the error text is sent, with keys, user name and home path removed.
Without a key the installer still works and shows the plain error and the log path.

## After installing
Run inside the install folder: `./crowdguard status | stop | start | doctor` (`crowdguard.cmd` on Windows).
Logs: `logs/installer.log`, `logs/backend.log`, `logs/frontend.log`. Sample videos are not included: copy your own
`.mp4` clips into `gallery/`.

## Limits
Installing Python/Node/Git through a package manager may need admin rights or your password. On a locked-down
or offline computer the installer stops with a clear message instead of guessing. Ports 8000 and 5173 must be free.
Verified so far on Linux (Python 3.13) and on Windows (Python 3.10.0, Node 24: install, start and verification
passed; the self-test crashed inside pytest on that early Python build, so Python 3.11+ is recommended). macOS is not
tested yet.
