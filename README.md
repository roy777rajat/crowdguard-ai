<h1 align="center">CrowdGuard AI</h1>

<p align="center"><b>Crowd-safety decision support for Durga Puja pandals</b><br>
Camera counting · forecasting · incidents · an AI assistant you can talk to</p>

<p align="center">
  <a href="https://skillicons.dev"><img alt="Tech stack: Python, FastAPI, React, TypeScript, Vite, SQLite, Docker, pytest" src="https://skillicons.dev/icons?i=python,fastapi,react,ts,vite,sqlite,docker,pytest&perline=8" /></a>
</p>

<p align="center">
  <img alt="Python 3.10+" src="https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white" />
  <img alt="FastAPI" src="https://img.shields.io/badge/FastAPI-009688?logo=fastapi&logoColor=white" />
  <img alt="Pydantic v2" src="https://img.shields.io/badge/Pydantic-v2-E92063?logo=pydantic&logoColor=white" />
  <img alt="React 18" src="https://img.shields.io/badge/React-18-61DAFB?logo=react&logoColor=black" />
  <img alt="TypeScript" src="https://img.shields.io/badge/TypeScript-3178C6?logo=typescript&logoColor=white" />
  <img alt="Vite" src="https://img.shields.io/badge/Vite-646CFF?logo=vite&logoColor=white" />
  <img alt="SQLite" src="https://img.shields.io/badge/SQLite-003B57?logo=sqlite&logoColor=white" />
  <img alt="Docker" src="https://img.shields.io/badge/Docker-2496ED?logo=docker&logoColor=white" />
  <br>
  <img alt="Meta SAM 3.1" src="https://img.shields.io/badge/Meta-SAM%203.1%20(people%20counting)-0866FF?logo=meta&logoColor=white" />
  <img alt="Meta Muse Spark" src="https://img.shields.io/badge/Meta-Muse%20Spark%20(explanations%20%26%20assistant)-0866FF?logo=meta&logoColor=white" />
  <img alt="Meta Muse Voice Transcribe" src="https://img.shields.io/badge/Meta-Muse%20Voice%20Transcribe%20(voice%20input)-0866FF?logo=meta&logoColor=white" />
</p>

<p align="center">
  <sub>Backend: Python · FastAPI · Pydantic · SQLite · httpx &nbsp;|&nbsp; Frontend: React · TypeScript · Vite · Vitest &nbsp;|&nbsp;
  Models: SAM 3.1 · Muse Spark · Muse Voice Transcribe &nbsp;|&nbsp; Quality: pytest · ruff · mypy &nbsp;|&nbsp; Packaging: Docker · Caddy</sub>
</p>

---

**Crowd-safety decision support for Durga Puja pandals in Kolkata.** CrowdGuard AI watches camera feeds from each
pandal, counts the people in every zone with Meta's SAM 3.1 vision model, compares the count with safe limits,
raises warnings, and explains what is happening in plain language with a Meta Muse Spark language model.

> **Important.** This is an experimental *decision-support* tool for authorised staff. It never issues evacuation or
> crowd-control instructions, and the forecasts are estimates, not safety guarantees. Sign-in is an evaluation
> mechanism, **not production security**. Demo data is clearly labelled as simulated.

---

## ⚡ One command: install, run and control

> **Install, test, start, watch, chat with the AI, and even uninstall - all from one terminal window.**
> Works on Windows, macOS and Linux. No manual setup, no config files to edit, no AWS or Meta account required to try it.

**Windows (PowerShell)**

```powershell
irm https://raw.githubusercontent.com/roy777rajat/crowdguard-ai/main/install.ps1 | iex
```

**macOS / Linux**

```bash
curl -fsSL https://raw.githubusercontent.com/roy777rajat/crowdguard-ai/main/install.sh | bash
```

**The same command works for everyone, every time.** Nothing installed yet? It installs. Already installed? It finds
your install and offers the **Control Room** instead, with a live health check:

```text
  An install was found: C:\CrowdGuard
  Backend :8000 RUNNING pid 4120     Web screens :5173 RUNNING pid 5388
  ALL SYSTEMS UP  backend, database and web screens are healthy

    1  Open the Control Room (start/stop, AI chat, uninstall ...)
    2  Install again / repair / update this install
    3  Install a new copy in a different folder
    0  Exit
```

If something is down you are told exactly what and what to do - start it, repair it, or contact your system administrator
with the log folder - instead of a wall of errors:

```text
  FAILED - the system is not fully up:
    x The backend is not running (port 8000)
    x The web screens are not running (port 5173)
  What to do:
    - Choose 2 (start everything), or 4 / 6 to start one part
    - Still failing? Contact your system administrator and share the logs in C:\CrowdGuard\logs
```

### 🧭 Part 1 - Guided install (9 steps, one at a time)

| # | Step | What it proves |
|---|---|---|
| 1 | Choose install folder | Default `C:\CrowdGuard` or `~/CrowdGuard`; an existing install is updated and its data kept |
| 2 | Check your computer | OS, Python 3.10+, Node.js 20+, internet (GitHub, PyPI, npm), free disk, ports 8000 and 5173. Offers to install Node.js if missing |
| 3 | Get the code | `git clone` / `git pull` (ZIP download if Git is missing) |
| 4 | Python environment | Private `.venv`, packages installed, import check |
| 5 | Web screens | `npm ci`, then a full type-check and build |
| 6 | Settings | Optional Meta key (hidden input), voice opt-in, private `.env` |
| 7 | Self-test | The project's own backend and web tests |
| 8 | Start the app | Backend on **8000**, web screens on **5173**, in the background |
| 9 | Verify | Health, database file, **every table with its row count**, demo sign-in, API counts, web-to-API link |

Every step is plain, deterministic code with coloured `[ OK ] / [WARN] / [FAIL]` lines - **no AI in the normal path**.
At the end you get the URLs, process IDs, database path, log folder and demo logins, and your browser opens.

### 🎛️ Part 2 - The Control Room (run `.\crowdguard.cmd` or `./crowdguard`)

```text
========================================================================
  CrowdGuard AI - control menu
  Backend :8000 RUNNING pid 262     Web screens :5173 RUNNING pid 272
  Install folder: C:\CrowdGuard
  ALL SYSTEMS UP  backend, database and web screens are healthy
========================================================================
    1  Show status and verify everything      8  Restart everything
    2  Start everything                       9  Open the app in your browser
    3  Stop everything                       10  Show recent log lines
    4  Start backend only                    11  Chat with the AI assistant (text or voice)
    5  Stop backend only                     12  Turn AI features on/off (Meta key)
    6  Start web screens only                13  Install / repair / update (re-run the installer)
    7  Stop web screens only                 14  Uninstall and delete everything
                                              0  Exit
```

| Capability | What you get |
|---|---|
| **Separate start/stop** | Stop or start the **backend** and the **web screens** independently, or both together; PIDs and ports are tracked and shown |
| **Live health banner** | `ALL SYSTEMS UP` or `FAILED` with plain-language reasons and the next action |
| **Status that proves it** | Option 1 re-runs the real checks: health, database integrity, every table and row count, sign-in, API and proxy |
| **AI chat in the terminal** | The same assistant as the web page: Muse Spark with read-only tools over your data, role-aware, with Yes/No/Cancel before any change |
| **Voice, hands-free** | `/mic` listens, detects when you stop (4 seconds of silence) and sends automatically; `/voice file.wav` for recordings |
| **Language choice** | Auto-detect by default; `/lang Bengali`, `Hindi`, `English`, `Tamil`, `Telugu`, `Kannada` to pin one |
| **Agent thinking view** | Streams, in dim text, what the assistant really did: tools checked, data sources, timings, reasoning turns and tokens (`/think` toggles it) |
| **AI on/off in one place** | Add, change or remove your Meta key and voice setting; the key is validated first and never printed or logged; the backend restarts for you |
| **Repair / update** | Re-run the installer on the same folder without losing data |
| **Safe uninstall** | See exactly what will be deleted, then confirm twice |

<details>
<summary><b>🤖 Example: AI chat with the thinking view</b> (click to expand)</summary>

```text
  AI assistant ready (muse-spark-1.3), signed in as operator.
         Commands: /mic  /voice file.wav  /lang  /think  /new  /help  /quit

  You> which cameras are offline?
    agent steps:
      - checked get_camera_health: ok, 41 ms
      - data source: cameras table (via get_camera_health)
      - 2 reasoning turn(s), muse-spark-1.3, 1180 ms, input tokens 812, output tokens 96
  Assistant>
    ...answer streams here, then waits for your yes/no if it proposes any change...

  You> /mic
  Speak now. It sends by itself after 4 seconds of silence (max 60 s).
  listening... [########            ] pause 2.1s of 4s
  You said: ...your words, in the detected language...
```

The thinking view shows the assistant's **real, recorded steps** (tools, sources, timing). The model's private reasoning
text is not exposed by the app, so it is never invented or displayed.
</details>

<details>
<summary><b>🗑️ Example: uninstall, with a full account of what goes</b> (click to expand)</summary>

```text
  A. INSIDE THE INSTALL FOLDER - all of this will be permanently DELETED
     Folder: C:\CrowdGuard   (total 349.1 MB)
      238.0 MB  C:\CrowdGuard\.venv\      - private Python environment - every Python package the app uses
      109.1 MB  C:\CrowdGuard\frontend\   - web app source code + its node_modules packages
      140.0 KB  C:\CrowdGuard\data\       - the SQLite database with all stored data
      110 B     C:\CrowdGuard\.env        - your settings, including any Meta API key
      ...
     Python packages in the private environment (.venv) - deleted with it:
       - fastapi>=0.110   - uvicorn[standard]>=0.29   - pydantic>=2.6   - httpx>=0.27   ...
     Database C:\CrowdGuard\data\crowdguard.db (140.0 KB): 16 tables, 63 rows - all of it is deleted.

  B. OUTSIDE THE FOLDER (your laptop / SYSTEM)
     Will NOT be touched: Python itself, Node.js, Git, pip and npm caches, your GitHub repository
     Installed on your SYSTEM by this installer (NOT removed automatically): the command to remove it yourself

  ? Do you want to continue and delete everything listed in section A? Type yes or no
  FINAL CONFIRMATION: this permanently deletes C:\CrowdGuard and everything in it.
  ? Type DELETE (capital letters) to confirm
```

Nothing is deleted unless you answer **yes** *and* type **DELETE**. It refuses to run on your home folder, a drive root,
or any folder that is not a CrowdGuard install.
</details>

### 🧰 Command reference

Run inside the install folder (`.\crowdguard.cmd` on Windows, `./crowdguard` elsewhere). With no argument it opens the menu.

| Command | Does |
|---|---|
| *(none)* | Open the Control Room |
| `status` | Verify everything and print table counts |
| `start` / `stop` | Start or stop backend and web screens together |
| `start-backend` / `stop-backend` | Backend only |
| `start-frontend` / `stop-frontend` | Web screens only |
| `chat` | Go straight to the AI chat |
| `uninstall` | Detailed, double-confirmed uninstall |

### 🛟 When a step fails (optional AI helper)

The installer first tries built-in fixes. If you gave a Meta key, it can also ask Muse Spark to pick **one fix from a
fixed menu** (for example "clear the npm cache"). It shows you the suggestion and applies it **only after you answer
Yes**; it can never run commands of its own. Only the error text is sent, with keys, user name and home path removed. If
it has nothing to suggest, it now tells you why. Details: [docs/INSTALL.md](docs/INSTALL.md).

### ✅ Honest status

| Area | Status |
|---|---|
| Install, start, verify, Control Room, uninstall, AI chat (text and voice-file path) | Tested on Linux (Python 3.13) |
| Install, start, verify | Also run on Windows (Python 3.10.0, Node 24); Python 3.11+ is recommended |
| Real microphone input (`/mic`), Windows folder deletion, macOS | **Not yet tested** |
| Real Meta answers and transcripts | Need your own key; mixed-language speech depends on Meta's model |

Notes: installing Python, Node.js or Git automatically may need admin rights; ports 8000 and 5173 must be free. The
sample videos are not part of the repository: copy your own `.mp4` clips into `gallery/`. The installer never touches
the app's own code, and never stores your key anywhere except your private `.env`.

### Database tables and starter data

A new install creates a SQLite database (`data/crowdguard.db`) with **16 tables** and starter data: 4 locations,
6 pandals, 18 zones, 18 cameras and 4 demo users. All of it is simulated. To understand the data without running the
app, read [docs/database/TABLES.md](docs/database/TABLES.md) (what each table holds), [docs/database/schema.sql](docs/database/schema.sql)
(the exact layout) and the starter content in [docs/database/csv/](docs/database/csv/). Tables such as `measurements`,
`forecasts` and `incidents` start empty and fill while the simulator or a camera runs. Password hashes are not exported.
Regenerate these files from your own install with `python scripts/export_db_docs.py`.

---

## 1. Problem statement

Durga Puja draws very large crowds to neighbourhood pandals within a few days. Crowd crushes at gates and exits are
a known risk, yet most committees rely on volunteers watching and shouting. Three problems follow:

1. **No shared picture.** Nobody sees, in one place, how full each gate, hall and exit is right now.
2. **Late warnings.** By the time a volunteer notices a build-up, the safe window is gone.
3. **No record.** There is no log of what was seen, when, and what was done about it.

## 2. What CrowdGuard AI does

- Organises the city as **Location → Pandal → Zone → Camera** (Kolkata South, North, East, West; each pandal has
  exactly 3 zones, each zone has one active or passive camera).
- **Camera wall:** 3 live tiles per pandal. Pick a video per zone, press *Start all*, and a snapshot is sent to SAM 3.1
  at a chosen frequency. Boxes are drawn around detected people; counts, *% of threshold* and *% criticality* are shown per
  camera and summed for the pandal.
- **Per-camera workflow:** every snapshot runs `capture → detect → persist → evaluate`, stored as a durable run with
  stage timings. Crossing a warning or critical limit opens a de-duplicated **incident** that staff acknowledge,
  assign, note and resolve.
- **Dashboard:** choose a location and pandal; see a graph per camera plus the summed graph, against the limits.
- **Live monitoring:** per-pandal occupancy forecast (rule-based, deterministic) with freshness, confidence and an
  exit-bottleneck signal, plus an AI explanation written by Muse Spark.
- **Operations:** workflow runs, incidents, model-call log (latency, tokens, cost), system health, audit log, settings,
  spend budget, and admin clean-up tools.
- **AI Assistant:** a chat page where staff ask questions in text or by voice ("Show me unresolved incidents and
  their pandal names", "Which cameras are currently offline?"). Muse Spark calls 24 read-only tools over the app's own
  data; answers cite their sources and respect your role. It can also create, update and delete records (pandals,
  zones, cameras, incidents, settings), but only as proposals: it always asks **Yes / No / Cancel**, and only your own
  reply makes anything happen (see [docs/assistant-tools.md](docs/assistant-tools.md)).
- **Roles:** admin, operator, pandal manager (one pandal only), read-only auditor.

## 3. Models used

| Purpose | Model | How it is called | Notes |
|---|---|---|---|
| People detection and counting | **Meta SAM 3.1** (`sam-3.1`) | Meta Model API, image segmentation with the text prompt `"person"` | About $0.0025 per image. Off unless `CROWDGUARD_DETECTOR=sam31`. |
| Plain-language explanations | **Meta Muse Spark** (default `muse-spark-1.3`, change with `META_MODEL`) | Meta Model API, Responses API | Describes recorded numbers only; never computes them. Off unless `CROWDGUARD_PROVIDER=meta`. |
| Assistant chat (tool calling) | **Meta Muse Spark** (same model setting) | Responses API with native function calling | Reads app data through server-side tools; can only *propose* changes, which you confirm with Yes / No / Cancel. Needs `CROWDGUARD_PROVIDER=meta`. |
| Voice questions | **Meta Muse Voice Transcribe** (`muse-voice-transcribe-1.0`) | REST `POST /v1/asr/transcribe`, recorded WAV clip | $0.18 per hour of audio. Off unless `CROWDGUARD_VOICE=meta`. Audio is not stored. |
| Forecasting | None (deterministic Python) | `backend/engine/forecast.py` | The language model can never change a count or forecast. |

Both models use the **same Meta API key**. Without a key, the app still runs using a local template provider and a
simulated detector, so you can try it with no account and no cost.

## 4. Architecture

```
 Browser (React + TypeScript + Vite)
   Camera wall: plays gallery video, grabs a frame every N seconds
        |  JPEG frame                                  Dashboard / Live / Incidents / Runs / Settings
        v                                                     |
 FastAPI backend  ------------------------------------------+
   api/        routes, vision_routes, admin_routes, RBAC (deps.py)
   services/   camera_workflow (capture>detect>persist>evaluate), monitor, incidents, structure, seed
   vision/     PersonDetector -> Sam31Detector (Meta) | SimulatedDetector
   engine/     deterministic forecast, risk, data quality, exit bottleneck
   observability/  model-call log, prompts, telemetry
   integrations/   Meta Responses API adapter (Muse Spark, incl. tool calling)
   assistant/  tools (read-only), actions (Yes/No/Cancel writes), orchestrator, Muse Voice Transcribe client
   repositories/ store.py  --->  SQLite (data/crowdguard.db)   [swappable for a cloud database]
        |                                |
        v                                v
 Meta Model API: sam-3.1            Meta Model API: Muse Spark
 (people count + boxes)             (explanations)
```

Key design rules: local-first; the model never computes counts or forecasts; unknown values are stored as `NULL` and
shown as "No count", never as a fake 0; every protected call re-checks the caller's role on the server; only
`db.py` and `repositories/store.py` contain SQL, so the database can be replaced without touching services.
More detail is in `docs/architecture.md`, `docs/camera-wall.md`, `docs/meta-integration.md` and the AWS plan in
`docs/aws-deployment-plan.md` (a plan only; nothing is deployed).

**Capture is browser-driven today:** frames are taken by the Camera wall page, so counting runs only while that page is
open and started. A server-side capture worker is the planned step for always-on cloud use.

## 5. Tech stack

| Layer | Technology |
|---|---|
| Backend | Python 3.10+, FastAPI, Pydantic v2, httpx |
| Database | SQLite behind a repository layer (swappable) |
| Frontend | React 18, TypeScript, Vite, React Router |
| AI models | Meta SAM 3.1, Meta Muse Spark (Responses API + function calling), Meta Muse Voice Transcribe |
| Quality | pytest, Vitest + Testing Library, ruff, mypy, TypeScript strict |
| Packaging | Docker, Docker Compose, Caddy (see `compose.prod.yaml`) |

## 6. Get the code

```bash
git clone https://github.com/roy777rajat/crowdguard-ai.git
cd crowdguard-ai
```

Requirements: **Python 3.10+** and **Node.js 20+** (with npm). Examples below use Windows PowerShell; on macOS/Linux use
`source .venv/bin/activate` instead of `.\.venv\Scripts\Activate.ps1`.

## 7. Set up, step by step

### Step 1: backend

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1          # if blocked: Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
pip install -r requirements.txt
```

### Step 2: configuration (optional but needed for the real models)

```powershell
copy .env.example .env
```

Open `.env` and set, only if you want the real models:

```
CROWDGUARD_PROVIDER=meta
CROWDGUARD_DETECTOR=sam31
META_MODEL_API_KEY=<your own Meta Model API key>
```

`.env` is git-ignored. **Never commit a key or paste it into chat.** Skip this step to run fully offline.

### Step 3: add videos

Copy one or more `.mp4` files into the `gallery/` folder (see `gallery/README.md`). They appear in each camera's
dropdown within about 10 seconds, with no restart. Use footage you are allowed to use, and get approval before
analysing real people.

### Step 4: start the backend

```powershell
uvicorn backend.api.app:create_app --factory --port 8000
```

The database `data/crowdguard.db` and sample pandals are created on first start.

### Step 5: start the frontend (second terminal)

```powershell
cd frontend
npm install
npm run dev
```

Open **http://localhost:5173**. API docs are at http://localhost:8000/docs.

### Step 6: sign in

Use the *Quick sign-in* buttons on the login page, or these local evaluation accounts:

| Username | Password | Role |
|---|---|---|
| admin | demo-admin | Administrator |
| operator | demo-operator | Control-room operator |
| manager | demo-manager | Pandal manager (first pandal only) |
| auditor | demo-auditor | Read-only auditor |

These are defaults for local use only. Change them (and replace the auth) before any shared deployment.

### Step 7: first run

1. **Pandals:** add a pandal in a location, with a unique association name and 3 zone names (or use the sample ones).
2. **Camera wall:** pick the location and pandal, choose a video for each zone, set *Snapshot every* (10 s is a good start),
   press **Start all**.
3. Watch counts, boxes and the four grids fill in. Open **Dashboard** for graphs and **Workflow runs** for each snapshot.
4. Set a **Budget** (top right of the wall) before long runs.

## 8. Checks and tests

```powershell
python -m pytest
ruff check backend tests
mypy backend
cd frontend
npm run typecheck
npm test
```

## 9. Configuration reference

| Variable | Default | Meaning |
|---|---|---|
| `CROWDGUARD_DB` | `data/crowdguard.db` | SQLite file |
| `CROWDGUARD_CORS` | localhost:5173 | Allowed browser origins |
| `CROWDGUARD_PROVIDER` | `local` | `meta` enables Muse Spark explanations |
| `META_MODEL` | `muse-spark-1.3` | Language model name |
| `CROWDGUARD_DETECTOR` | `simulated` | `sam31` enables SAM 3.1 counting |
| `SAM_THRESHOLD` | `0.5` | Minimum confidence for a person box |
| `META_MODEL_API_KEY` | none | Meta Model API key (never commit) |
| `CROWDGUARD_GALLERY` | `gallery` | Folder holding the `.mp4` files |
| `CROWDGUARD_VOICE` | `off` | `meta` enables voice input in the AI Assistant |
| `META_VOICE_API_KEY` | none | Optional separate voice key; defaults to `META_MODEL_API_KEY` |
| `META_VOICE_MODE` | `ENDPOINTING` | Or `PUSH_TO_TALK`; how Meta segments the recording |

## 10. Cost and privacy

- SAM 3.1 costs about **$0.0025 per image**. Cost per hour ≈ active cameras × 3600 ÷ interval × $0.0025
  (3 cameras every 10 s ≈ $2.70/hour). A spend cap (default $1) stops counting when reached; admins change it on the wall.
  The figure is an estimate from the app's own log, not your invoice.
- Frames that show people are sent to Meta when SAM is enabled. Obtain venue and legal approval first. Frames are not stored
  by this app; only counts, latency and status are.
- The built-in test animation is never sent to a paid model.

### AI Assistant: how to try it

1. In `.env` set `CROWDGUARD_PROVIDER=meta`, `META_MODEL_API_KEY=...` and (for the microphone) `CROWDGUARD_VOICE=meta`.
2. Start the backend and frontend, sign in, open **AI Assistant** in the sidebar.
3. Type "Show me unresolved incidents and their pandal names." (run a simulation first so there is data), or press
   **Speak**, say "Which cameras are currently offline?", check the transcript, edit it if needed, and press **Send**.
4. Without a key the page says the assistant is unavailable and no model is called.

Everything the assistant can read or do is listed in [docs/assistant-tools.md](docs/assistant-tools.md). Any change it
proposes (including permanent deletes) is shown in plain words and waits for your **Yes**, **No** or **Cancel**; your
role and pandal scope are checked again when you say Yes. It cannot manage users or roles. Voice audio is sent to Meta
only when you use the microphone and is not stored.

## 11. Limits

- Counting is per sampled frame: no tracking IDs and no entry/exit flow.
- Capture runs in the browser; closing the Camera wall stops counting.
- Sign-in is not production-grade (no MFA, email verification, rate limiting; token kept in browser storage).
- Live monitoring forecasts use the labelled simulator, not the SAM counts.
- AWS deployment (Cognito, API Gateway, Lambda, DynamoDB, S3, CloudWatch) is a plan only.

## 12. Roadmap

Usage and cost view for SAM 3.1, Muse Spark and Voice Transcribe, server-side capture worker, cloud database, real
authentication, notifications, streaming assistant replies.

## 13. Repository layout

```
backend/        assistant (tools, orchestration, voice), api, schemas, repositories, services, engine, simulator, workflows, vision, observability, integrations, security
frontend/       React app (src/api, components, pages, hooks, types, styles)
tests/backend/  pytest suites          frontend/src/__tests__/  Vitest suites
docs/           architecture, camera wall, Meta integration, AI assistant tools, AWS plan
scripts/        helper scripts (PowerShell runners, clip generator, data reset)
gallery/        your .mp4 files (not committed)
data/           local SQLite database (not committed)
```

## 14. Troubleshooting

- **`ModuleNotFoundError: backend`:** run commands from the project root with the venv active.
- **UI shows a connection problem:** the backend is not running on port 8000.
- **"No count" on a video:** SAM 3.1 is not enabled (check `.env` and restart), the video has not started, or the budget is used up.
- **Port in use:** start uvicorn with `--port 8001` and update the proxy in `frontend/vite.config.ts`.

## 15. Contributing and licence

Issues and pull requests are welcome. No licence file has been added yet; add one before sharing the code publicly.
