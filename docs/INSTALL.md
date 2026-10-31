# Install and control CrowdGuard AI (guided installer + Control Room)

One command. It installs everything, proves it works, and later becomes your control panel.

**Windows (PowerShell)**
```powershell
irm https://raw.githubusercontent.com/roy777rajat/crowdguard-ai/main/install.ps1 | iex
```

**macOS / Linux**
```bash
curl -fsSL https://raw.githubusercontent.com/roy777rajat/crowdguard-ai/main/install.sh | bash
```

Needs internet. The small script only makes sure Python 3.10+ exists (it asks before installing it), then runs
`installer/crowdguard_installer.py`.

**Run the same command again later:** if CrowdGuard is already installed it offers the Control Room (health check,
start/stop, AI chat), repair/update, or a new copy in another folder.

## The 9 install steps
1. Choose install folder (default `C:\CrowdGuard` or `~/CrowdGuard`; an existing install is updated, data kept)
2. Check your computer: OS, Python 3.10+, Node.js 20+, internet (GitHub, PyPI, npm), disk, ports 8000 and 5173
3. Get the code (git clone/pull; ZIP download if git is missing)
4. Private Python environment (`.venv`) and packages, then an import check
5. Web packages and a compile check
6. Settings: optional AI provider (Meta) and key, voice opt-in, writes a private `.env`; prepares `gallery/`
7. Self-test: backend (pytest) and web (Vitest) tests
8. Start backend (port 8000) and web screens (port 5173) in the background
9. Verify: health, database file, every table with row counts, demo sign-in, API counts, web-to-API link

## The Control Room
Open it with `.\crowdguard.cmd` (Windows) or `./crowdguard` inside the install folder, or by re-running the install command.

| # | Option |
|---|---|
| 1 | Show status and verify everything |
| 2 / 3 | Start / stop everything |
| 4 / 5 | Start / stop the backend only |
| 6 / 7 | Start / stop the web screens only |
| 8 | Restart everything |
| 9 | Open the app in your browser |
| 10 | Show recent log lines |
| 11 | Chat with the AI assistant (text or voice) |
| 12 | Turn AI features on/off (Meta key, voice) |
| 13 | Install / repair / update |
| 14 | Uninstall and delete everything |

The header always shows `ALL SYSTEMS UP`, or `FAILED` with reasons: choose 2 to start, 13 to repair, or contact your
system administrator with the logs folder.

Direct commands: `status`, `start`, `stop`, `start-backend`, `stop-backend`, `start-frontend`, `stop-frontend`, `chat`,
`uninstall`.

## AI chat in the terminal
Uses the running backend's own assistant, so answers, tools and permissions match the web page. Needs the Meta key turned
on (option 12). Sign in as a demo user (default `operator`), then type questions.
- `/mic` records from your microphone and sends by itself after 4 seconds of silence (waits up to 20 s for you to start).
  The first time it asks to install the small `sounddevice` package into the app's `.venv`.
- `/voice file.wav` sends a recording (mono, 16-bit WAV, 16 or 24 kHz, under 1 minute).
- `/lang` shows or sets the voice language: `auto` (default, the model detects it) or English, Bengali, Hindi, Kannada,
  Tamil, Telugu. If a transcript is wrong, pin the language.
- `/think` toggles the thinking view: dim, streamed lines showing what the assistant really did (tools checked, data
  sources, timings, turns, tokens). The model's private reasoning text is not exposed by the app and is not shown.
- `/new`, `/help`, `/quit`. Any change the assistant proposes waits for your own yes or no.
Voice audio goes to Meta only when you use voice, and is not stored.

## Uninstall (double-confirmed)
Option 14 or `uninstall`. It lists, point by point: every file and folder that will be deleted with path and size, the
Python packages in `.venv`, the database tables and row counts, and the processes it will stop first. It also lists what
it will **not** touch (Python, Node.js, Git, pip/npm caches, your GitHub repo) and any system install it recorded, with
the command to remove it yourself. You must answer `yes`, then type `DELETE`. It refuses to run on your home folder, a
drive root, or a folder that is not a CrowdGuard install. There is no undo.

## AI helper for failed install steps (optional)
Everything above is fixed, predictable code. If you give a Meta key at the start and a step fails, the installer first
tries built-in fixes, then asks Muse Spark to pick **one fix from a fixed menu** (for example "clear the npm cache" or
"rebuild .venv"). It shows you the suggestion and applies it only if you answer Yes. It cannot run its own commands. Only
the error text is sent, with keys, user name and home path removed. If it has nothing to suggest it says why. Without a
key the installer still works and shows the plain error and the log path.

## Logs
`logs/installer.log`, `logs/backend.log`, `logs/frontend.log` in the install folder.

## Limits
Installing Python/Node/Git through a package manager may need admin rights or your password. On a locked-down or
offline computer the installer stops with a clear message instead of guessing. Ports 8000 and 5173 must be free. Sample
videos are not included: copy your own `.mp4` clips into `gallery/`.

Tested: Linux (Python 3.13) end to end, including the Control Room, AI chat (text and voice-file path against a stand-in
server) and uninstall. Windows (Python 3.10.0, Node 24): install, start and verification passed; the self-test crashed
inside pytest on that early Python build, so Python 3.11+ is recommended. Not yet tested: real microphone input,
Windows folder deletion, macOS, and real Meta answers (these need your own key).
