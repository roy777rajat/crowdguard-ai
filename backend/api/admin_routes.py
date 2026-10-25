"""Admin clean-up and registry deletion endpoints (admin only).

Every destructive call is audited. Deletions are permanent; the database file is the only copy.
The number of paid SAM calls already made is remembered (`vision_prior_paid_calls`) so that clearing history
never resets the spend estimate or the budget cap.
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional, Sequence

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from backend.api.deps import get_ctx, require
from backend.services.context import AppContext

router = APIRouter(prefix="/api")
User = Dict[str, Any]


class DeleteSelection(BaseModel):
    ids: List[str] = Field(default_factory=list, max_length=5000)
    all: bool = False
    pandal_id: Optional[int] = None


class ActiveIn(BaseModel):
    active: bool


class ConfirmIn(BaseModel):
    confirm: str


def _marks(n: int) -> str:
    return ",".join("?" for _ in range(n))


def _in(ctx: AppContext, sql: str, ids: Sequence[Any]) -> int:
    """Run `sql` with a `{IN}` placeholder expanded to the ids (chunked for SQLite's parameter limit)."""
    total = 0
    for i in range(0, len(ids), 500):
        chunk = list(ids[i:i + 500])
        total += ctx.repos.db.execute(sql.replace("{IN}", _marks(len(chunk))), chunk).rowcount or 0
    return total


def _remember_paid_calls(ctx: AppContext, where_sql: str, params: Sequence[Any]) -> None:
    row = ctx.repos.db.one(f"SELECT COUNT(*) AS n FROM vision_counts WHERE is_real_model=1 AND {where_sql}", params)
    n = int((row or {}).get("n") or 0)
    if n:
        prior = int(ctx.repos.get_setting("vision_prior_paid_calls", 0) or 0)
        ctx.repos.set_setting("vision_prior_paid_calls", prior + n)


def _delete_runs(ctx: AppContext, run_ids: List[str]) -> int:
    if not run_ids:
        return 0
    _in(ctx, "DELETE FROM stages WHERE run_id IN ({IN})", run_ids)
    _in(ctx, "DELETE FROM forecasts WHERE run_id IN ({IN})", run_ids)
    _in(ctx, "DELETE FROM measurements WHERE run_id IN ({IN})", run_ids)
    return _in(ctx, "DELETE FROM runs WHERE run_id IN ({IN})", run_ids)


def _delete_incidents(ctx: AppContext, ids: List[int]) -> int:
    if not ids:
        return 0
    _in(ctx, "DELETE FROM incident_events WHERE incident_id IN ({IN})", ids)
    return _in(ctx, "DELETE FROM incidents WHERE id IN ({IN})", ids)


def _delete_pandal_data(ctx: AppContext, pandal_ids: List[int]) -> Dict[str, int]:
    db = ctx.repos.db
    if not pandal_ids:
        return {}
    marks = _marks(len(pandal_ids))
    runs = [r["run_id"] for r in db.query(f"SELECT run_id FROM runs WHERE pandal_id IN ({marks})", pandal_ids)]
    incs = [r["id"] for r in db.query(f"SELECT id FROM incidents WHERE pandal_id IN ({marks})", pandal_ids)]
    out = {"runs": _delete_runs(ctx, runs), "incidents": _delete_incidents(ctx, incs)}
    _remember_paid_calls(ctx, f"pandal_id IN ({marks})", pandal_ids)
    for table in ("vision_counts", "measurements", "forecasts"):
        db.execute(f"DELETE FROM {table} WHERE pandal_id IN ({marks})", pandal_ids)
    for pid in pandal_ids:
        db.execute("DELETE FROM settings WHERE key=?", [f"vision_reset:{pid}"])
    out["cameras"] = db.execute(f"DELETE FROM cameras WHERE pandal_id IN ({marks})", pandal_ids).rowcount or 0
    out["zones"] = db.execute(f"DELETE FROM zones WHERE pandal_id IN ({marks})", pandal_ids).rowcount or 0
    out["pandals"] = db.execute(f"DELETE FROM pandals WHERE id IN ({marks})", pandal_ids).rowcount or 0
    gone = set(pandal_ids)
    for u in ctx.repos.users.list({"role": "manager"}):
        ids = u.get("pandal_ids") or []
        if gone & set(ids):
            ctx.repos.users.update(u["id"], pandal_ids=[i for i in ids if i not in gone])
    return out


# ---------------------------------------------------------------- registry
@router.delete("/pandals/{pandal_id}/permanent")
def delete_pandal(pandal_id: int, user: User = Depends(require("registry:write")),
                  ctx: AppContext = Depends(get_ctx)) -> Dict[str, Any]:
    if ctx.repos.pandals.get(pandal_id) is None:
        raise HTTPException(404, "Pandal not found")
    out = _delete_pandal_data(ctx, [pandal_id])
    ctx.repos.audit.log(user["username"], "pandal.delete", "pandal", pandal_id, out)
    return {"ok": True, "deleted": out}


@router.post("/registry/clear")
def clear_registry(body: ConfirmIn, user: User = Depends(require("registry:write")),
                   ctx: AppContext = Depends(get_ctx)) -> Dict[str, Any]:
    """Delete every pandal, zone and camera with their counts, runs and incidents. Locations stay."""
    if body.confirm != "DELETE":
        raise HTTPException(422, 'Type DELETE to confirm')
    ids = [p["id"] for p in ctx.repos.pandals.list()]
    out = _delete_pandal_data(ctx, ids)
    ctx.repos.audit.log(user["username"], "registry.clear", "registry", None, out)
    return {"ok": True, "deleted": out}


@router.delete("/cameras/{camera_id}/permanent")
def delete_camera(camera_id: int, user: User = Depends(require("registry:write")),
                  ctx: AppContext = Depends(get_ctx)) -> Dict[str, Any]:
    cam = ctx.repos.cameras.get(camera_id)
    if cam is None:
        raise HTTPException(404, "Camera not found")
    ctx.repos.db.execute("DELETE FROM vision_counts WHERE camera_id=?", [camera_id])
    ctx.repos.db.execute("DELETE FROM cameras WHERE id=?", [camera_id])
    ctx.repos.audit.log(user["username"], "camera.delete", "camera", camera_id, {"pandal_id": cam["pandal_id"]})
    return {"ok": True, "deleted": camera_id}


@router.post("/cameras/{camera_id}/active")
def set_camera_active(camera_id: int, body: ActiveIn, user: User = Depends(require("registry:write")),
                      ctx: AppContext = Depends(get_ctx)) -> Dict[str, Any]:
    cam = ctx.repos.cameras.get(camera_id)
    if cam is None:
        raise HTTPException(404, "Camera not found")
    if body.active and cam.get("zone_id") is not None:
        other = [c for c in ctx.repos.cameras.list({"zone_id": cam["zone_id"], "active": 1}) if c["id"] != camera_id]
        if other:
            raise HTTPException(409, "Another camera in this zone is already active. Deactivate it first.")
    updated = ctx.repos.cameras.update(camera_id, active=1 if body.active else 0,
                                       status="registered" if body.active else "inactive")
    ctx.repos.audit.log(user["username"], "camera.activate" if body.active else "camera.deactivate", "camera", camera_id)
    return updated  # type: ignore[return-value]


# ---------------------------------------------------------------- history clean-up
@router.post("/runs/delete")
def delete_runs(body: DeleteSelection, user: User = Depends(require("settings:write")),
                ctx: AppContext = Depends(get_ctx)) -> Dict[str, Any]:
    if body.all:
        rows = ctx.repos.db.query("SELECT run_id FROM runs" + (" WHERE pandal_id=?" if body.pandal_id else ""),
                                  [body.pandal_id] if body.pandal_id else [])
        ids = [r["run_id"] for r in rows]
    else:
        ids = list(dict.fromkeys(body.ids))
    n = _delete_runs(ctx, ids)
    if body.all and not body.pandal_id:
        _remember_paid_calls(ctx, "1=1", [])
        ctx.repos.db.execute("DELETE FROM vision_counts")
        ctx.repos.db.execute("UPDATE cameras SET status='registered', last_seen=NULL, last_error=NULL WHERE active=1")
    ctx.repos.audit.log(user["username"], "runs.delete", "run", None, {"count": n, "all": body.all})
    return {"ok": True, "deleted": n}


@router.post("/incidents/delete")
def delete_incidents(body: DeleteSelection, user: User = Depends(require("settings:write")),
                     ctx: AppContext = Depends(get_ctx)) -> Dict[str, Any]:
    if body.all:
        rows = ctx.repos.db.query("SELECT id FROM incidents" + (" WHERE pandal_id=?" if body.pandal_id else ""),
                                  [body.pandal_id] if body.pandal_id else [])
        ids = [int(r["id"]) for r in rows]
    else:
        try:
            ids = [int(i) for i in dict.fromkeys(body.ids)]
        except ValueError:
            raise HTTPException(422, "incident ids must be numbers")
    n = _delete_incidents(ctx, ids)
    ctx.repos.audit.log(user["username"], "incidents.delete", "incident", None, {"count": n, "all": body.all})
    return {"ok": True, "deleted": n}


@router.post("/llm/clear")
def clear_llm_calls(body: ConfirmIn, user: User = Depends(require("settings:write")),
                    ctx: AppContext = Depends(get_ctx)) -> Dict[str, Any]:
    """Delete the model-call history (Muse-Spark). SAM image counts are cleared with workflow runs."""
    if body.confirm != "DELETE":
        raise HTTPException(422, "Type DELETE to confirm")
    row = ctx.repos.db.one("SELECT COUNT(*) AS n, COALESCE(SUM(est_cost_usd),0) AS cost FROM llm_calls") or {}
    ctx.repos.db.execute("DELETE FROM llm_calls")
    ctx.repos.audit.log(user["username"], "llm.clear", "llm_call", None,
                        {"count": row.get("n"), "est_cost_usd_cleared": row.get("cost"), "at": time.time()})
    return {"ok": True, "deleted": row.get("n", 0),
            "note": "History cleared. Your provider's own billing is not affected."}


@router.post("/system/clear")
def clear_system_health(user: User = Depends(require("settings:write")),
                        ctx: AppContext = Depends(get_ctx)) -> Dict[str, Any]:
    """Reset system-health figures: the API request/latency buffer and camera error flags."""
    ctx.request_metrics.clear()
    ctx.repos.db.execute("UPDATE cameras SET last_error=NULL, last_seen=NULL, "
                         "status=CASE WHEN active=1 THEN 'registered' ELSE 'inactive' END")
    ctx.repos.audit.log(user["username"], "system.clear", "system", None)
    return {"ok": True,
            "note": "API metrics and camera status cleared. Workflow figures follow the workflow history (Runs)."}
