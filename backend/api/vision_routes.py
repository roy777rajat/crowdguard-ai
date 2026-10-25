"""Camera wall API: serves gallery videos, runs the per-camera workflow on frames, and builds the wall view.

The browser grabs a frame from the playing video and posts it here. Counting is done by the configured
detector (simulated by default). Nothing is sent outside this computer unless a cloud detector is enabled.
"""
from __future__ import annotations

import os
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse

from backend.api.deps import get_ctx, load_pandal, require
from backend.services.camera_workflow import CameraWorkflow
from backend.services.context import AppContext
from backend.services.structure import WINDOW_S, level_from, limits, wall_view
from backend.vision.detector import MAX_FRAME_BYTES, PersonDetector, SimulatedDetector

router = APIRouter(prefix="/api/vision")

User = Dict[str, Any]
MIN_INTERVAL_S = 1.0       # server-side guard so a page left open cannot flood a paid endpoint
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _.-]{0,100}\.mp4$")
DEFAULT_BUDGET_USD = 1.0   # cap on estimated SAM spend until the operator sets another value
DEFAULT_INTERVAL_S = 10    # suggested seconds between frames per camera when a paid model is on
DEMO_INTERVAL_S = 3
_last_call: Dict[int, float] = {}


def _spend(ctx: AppContext, det: PersonDetector) -> float:
    """Estimated spend: every call to a paid model counts, successful or not (a failed call may still bill)."""
    row = ctx.repos.db.one("SELECT COUNT(*) AS n FROM vision_counts WHERE is_real_model=1")
    prior = ctx.repos.get_setting("vision_prior_paid_calls", 0)  # calls made before a workflow-data wipe
    n = (row["n"] if row else 0) + (int(prior) if isinstance(prior, (int, float)) else 0)
    return round(n * det.price_per_call_usd, 4)


def _budget(ctx: AppContext) -> float:
    v = ctx.repos.get_setting("vision_budget_usd")
    return float(v) if isinstance(v, (int, float)) else DEFAULT_BUDGET_USD


def gallery_dir() -> Path:
    return Path(os.environ.get("CROWDGUARD_GALLERY") or Path(__file__).resolve().parents[2] / "gallery")


def _detector(request: Request) -> PersonDetector:
    return request.app.state.detector  # type: ignore[no-any-return]


@router.get("/status")
def vision_status(request: Request, user: User = Depends(require("read")),
                  ctx: AppContext = Depends(get_ctx)) -> Dict[str, Any]:
    det = _detector(request)
    info = request.app.state.detector_info
    return {"detector": det.name, "is_real_model": det.is_real_model, "note": info.get("note", ""),
            "frames_leave_this_computer": det.is_real_model,
            "min_interval_s": MIN_INTERVAL_S,
            "suggested_interval_s": DEFAULT_INTERVAL_S if det.is_real_model else DEMO_INTERVAL_S,
            "price_per_image_usd": det.price_per_call_usd, "spent_usd": _spend(ctx, det),
            "budget_usd": _budget(ctx) if det.is_real_model else None,
            "notice": ("Camera frames are sent to Meta SAM 3.1 (billed per image)." if det.is_real_model else
                       "Simulated detector: no frames are sent anywhere and no real people are detected.")}


@router.get("/gallery")
def list_gallery(user: User = Depends(require("read"))) -> List[Dict[str, Any]]:
    d = gallery_dir()
    if not d.is_dir():
        return []
    return [{"name": p.name, "bytes": p.stat().st_size} for p in sorted(d.glob("*.mp4")) if _NAME_RE.match(p.name)]


@router.get("/gallery/{name}")
def get_gallery_video(name: str, user: User = Depends(require("read"))) -> FileResponse:
    if not _NAME_RE.match(name):
        raise HTTPException(422, "Only plain .mp4 file names in the gallery folder are allowed")
    path = (gallery_dir() / name).resolve()
    if path.parent != gallery_dir().resolve() or not path.is_file():
        raise HTTPException(404, "Video not found in the gallery folder")
    return FileResponse(path, media_type="video/mp4")


@router.get("/pandals/{pandal_id}/wall")
def pandal_wall(pandal_id: int, window_s: float = WINDOW_S, user: User = Depends(require("read")),
                ctx: AppContext = Depends(get_ctx)) -> Dict[str, Any]:
    p = load_pandal(ctx, user, pandal_id)
    return wall_view(ctx.repos, p, window_s=max(60.0, min(window_s, 86400.0)))


@router.post("/pandals/{pandal_id}/reset")
def pandal_reset(pandal_id: int, user: User = Depends(require("sim:control")),
                 ctx: AppContext = Depends(get_ctx)) -> Dict[str, Any]:
    """Start the wall's counts and graphs from zero. Non-destructive: older rows stay in the database."""
    load_pandal(ctx, user, pandal_id)
    now = time.time()
    ctx.repos.set_setting(f"vision_reset:{pandal_id}", now)
    ctx.repos.audit.log(user["username"], "vision.reset", "pandal", pandal_id)
    return {"ok": True, "reset_ts": now}


@router.post("/cameras/{camera_id}/analyze")
async def analyze(camera_id: int, request: Request, demo_truth: Optional[int] = None,
                  user: User = Depends(require("sim:control")),
                  ctx: AppContext = Depends(get_ctx)) -> Dict[str, Any]:
    cam = ctx.repos.cameras.get(camera_id)
    if cam is None:
        raise HTTPException(404, "Camera not found")
    pandal = load_pandal(ctx, user, cam["pandal_id"])
    if cam.get("mode", "active") != "active" or not cam["active"]:
        raise HTTPException(409, "This camera is passive (or deactivated): frames are not counted")
    if demo_truth is not None and (cam["source_type"] != "simulated" or not 0 <= demo_truth <= 10_000):
        raise HTTPException(422, "demo_truth is only accepted for the synthetic demo feed")
    jpeg = await request.body()
    if not jpeg or len(jpeg) > MAX_FRAME_BYTES:
        raise HTTPException(413 if jpeg else 422, f"Send one JPEG frame of at most {MAX_FRAME_BYTES} bytes")
    now = time.time()
    if now - _last_call.get(camera_id, 0) < MIN_INTERVAL_S:
        raise HTTPException(429, "Frames are accepted at most once per second per camera")
    _last_call[camera_id] = now

    # The synthetic demo animation is never sent to a paid model: its own count is used, at no cost.
    det: PersonDetector = SimulatedDetector() if demo_truth is not None else _detector(request)
    if det.is_real_model and _spend(ctx, det) + det.price_per_call_usd > _budget(ctx):
        raise HTTPException(409, "VISION_BUDGET_EXCEEDED: the estimated image-analysis budget is used up. "
                                 "Raise it on this page to continue.")
    out = await run_in_threadpool(CameraWorkflow(ctx.repos, ctx.incidents).process, cam, jpeg, det, demo_truth)
    res = out["res"]
    count = res.count if res else None
    zone = ctx.repos.zones.get(cam["zone_id"]) if cam.get("zone_id") else None
    if zone is not None and count is not None:
        warn, crit, _ = limits(pandal, zone)
        level = level_from(count, warn, crit)
    else:
        level = "unknown"
    failed = out["failure"] is not None
    return {"camera_id": camera_id, "ts": now, "run_id": out["run_id"], "run_status": out["run_status"],
            "status": "error" if failed else "success", "error_category": out["failure"], "count": count,
            "level": level, "boxes": res.boxes if res else [],
            "frame_w": res.frame_w if res else None, "frame_h": res.frame_h if res else None,
            "detector": det.name, "is_real_model": det.is_real_model,
            "incident_ids": out["incident_ids"],
            "note": res.note if res else f"The detector call failed ({out['failure']}). No count is shown."}
