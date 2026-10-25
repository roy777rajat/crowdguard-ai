# Camera wall, locations and per-camera workflow

## Hierarchy

Location (Kolkata South / North / East / West) → Pandal (unique association name) → exactly 3 Zones (names are
user-entered) → one camera per zone, **active** (counted) or **passive** (shown, never counted).
Create pandals on the *Pandals* page; zone names and each zone's `max people` can be edited later.
The Dashboard and Camera wall both start with *Location*, then *Pandal*.

## Thresholds

Per zone: `max_people` (default 40). Warning limit = max × pandal warn % (70% → 28). Critical limit = max × critical % (90% → 36).

- **% of threshold** = count ÷ warning limit × 100
- **% criticality** = count ÷ critical limit × 100
- Pandal total = sum of the zones' latest counts, against the sum of the zone limits.
- Counts older than 60 s are treated as stale (not shown as current). A missing count is "No count", never 0.

## Camera wall

Three tiles (one per zone). Each has a video dropdown (**Demo animation** or any `.mp4` in the `gallery/` folder;
override the folder with `CROWDGUARD_GALLERY`) and an Active/Passive switch. Controls: **Start all**, **Pause**,
**Stop all** (rewinds videos), **Reset** (clears this pandal's counts, keeps spend accounting), **Snapshot every N seconds**.
The wall always opens *Stopped*, so nothing is billed until someone presses Start.
Below: four grids (one per camera, then the pandal sum) with Total count, % of threshold, % criticality.

## Workflow per camera

Every snapshot runs the durable workflow `capture → detect → persist → evaluate` for that camera: the frame goes to the
detector (Meta SAM 3.1 when `CROWDGUARD_DETECTOR=sam31`), the count is stored, and `zone_warning` / `zone_critical`
incidents are raised (deduplicated per zone and rule). Failed detections are recorded as failed runs, not as zeros.

## Real vs demo

| Source | Count comes from |
|---|---|
| Demo animation | The animation's own count. **Never sent to Meta, never billed.** |
| Gallery video | Meta SAM 3.1 when enabled, otherwise "No count" |
| Live stream (http/https) | Same, if the browser may read its frames (CORS) |

Test clips: `python scripts/make_demo_videos.py` makes cartoon clips (SAM may detect cartoons poorly). Use real, licensed footage for evaluation.

## SAM 3.1

Source: https://dev.meta.ai/docs/sam/segmenting (read 2026-10-25). `POST https://api.meta.ai/v1/responses`, model `sam-3.1`,
prompt `"person"`, one base64 JPEG, boxes below `SAM_THRESHOLD` (0.5) dropped. Price $0.0025/image.
Frames showing people **leave this computer**: get privacy sign-off first. Spend cap default $1.00 (admins change it on the wall);
failed calls count as spend. Cost ≈ active cameras × 3600 ÷ interval × $0.0025 per hour (3 cameras at 10 s ≈ $2.70/h).

## Limits

- Counting is per sampled frame, no tracking IDs, no entry/exit flow.
- Capture is browser-driven: counting runs only while the Camera wall is open and started. A server-side worker is the cloud step.
- Counts are not fed into the forecast engine yet.

## Clearing old workflow data

Stop the backend, then `python scripts/reset_workflow_data.py` (dry run) and `python scripts/reset_workflow_data.py --yes`.
It backs up the database to `data/backups/`, deletes runs, stages, forecasts, measurements, incidents and vision counts, and keeps
pandals, zones, cameras, users, the audit log and the model-call log.
