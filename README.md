# CrowdGuard AI

**Crowd-safety decision support for Durga Puja pandals in Kolkata.** CrowdGuard AI watches camera feeds from each
pandal, counts the people in every zone with Meta's SAM 3.1 vision model, compares the count with safe limits,
raises warnings, and explains what is happening in plain language with a Meta Muse Spark language model.

> **Important.** This is an experimental *decision-support* tool for authorised staff. It never issues evacuation or
> crowd-control instructions, and the forecasts are estimates, not safety guarantees. Sign-in is an evaluation
> mechanism, **not production security**. Demo data is clearly labelled as simulated.

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
- **Roles:** admin, operator, pandal manager (one pandal only), read-only auditor.

## 3. Models used

| Purpose | Model | How it is called | Notes |
|---|---|---|---|
| People detection and counting | **Meta SAM 3.1** (`sam-3.1`) | Meta Model API, image segmentation with the text prompt `"person"` | About $0.0025 per image. Off unless `CROWDGUARD_DETECTOR=sam31`. |
| Plain-language explanations | **Meta Muse Spark** (default `muse-spark-1.3`, change with `META_MODEL`) | Meta Model API, Responses API | Describes recorded numbers only; never computes them. Off unless `CROWDGUARD_PROVIDER=meta`. |
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
   integrations/   Meta Responses API adapter (Muse Spark)
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

Python 3.10+ · FastAPI · Pydantic v2 · SQLite · httpx · pytest · ruff · mypy · React 18 · TypeScript · Vite · Vitest.

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

## 10. Cost and privacy

- SAM 3.1 costs about **$0.0025 per image**. Cost per hour ≈ active cameras × 3600 ÷ interval × $0.0025
  (3 cameras every 10 s ≈ $2.70/hour). A spend cap (default $1) stops counting when reached; admins change it on the wall.
  The figure is an estimate from the app's own log, not your invoice.
- Frames that show people are sent to Meta when SAM is enabled. Obtain venue and legal approval first. Frames are not stored
  by this app; only counts, latency and status are.
- The built-in test animation is never sent to a paid model.

## 11. Limits

- Counting is per sampled frame: no tracking IDs and no entry/exit flow.
- Capture runs in the browser; closing the Camera wall stops counting.
- Sign-in is not production-grade (no MFA, email verification, rate limiting; token kept in browser storage).
- Live monitoring forecasts use the labelled simulator, not the SAM counts.
- AWS deployment (Cognito, API Gateway, Lambda, DynamoDB, S3, CloudWatch) is a plan only.

## 12. Roadmap

AI chat page (Muse Spark choosing backend tools to answer questions), usage and cost view for both models, server-side
capture worker, cloud database, real authentication, notifications.

## 13. Repository layout

```
backend/        api, schemas, repositories, services, engine, simulator, workflows, vision, observability, integrations, security
frontend/       React app (src/api, components, pages, hooks, types, styles)
tests/backend/  pytest suites          frontend/src/__tests__/  Vitest suites
docs/           architecture, camera wall, Meta integration, AWS plan
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
