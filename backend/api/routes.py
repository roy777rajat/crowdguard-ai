"""HTTP API routes."""
from __future__ import annotations

import sqlite3
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException

from backend.api.deps import bearer_token, current_user, get_ctx, load_pandal, require
from backend.observability import llm as llm_obs
from backend.observability.telemetry import system_metrics
from backend.schemas.models import (
    CameraIn,
    CameraUpdate,
    LocationPandalIn,
    LoginIn,
    NoteIn,
    PandalIn,
    PandalUpdate,
    RegisterIn,
    SettingsIn,
    SimIn,
    TransitionIn,
    ZoneIn,
    ZoneUpdate,
)
from backend.security.auth import can_access_pandal, public_user
from backend.services.context import AppContext
from backend.services.incidents import DEFAULT_RULES, InvalidTransition
from backend.services.structure import create_pandal as create_structured_pandal
from backend.services.structure import ensure_locations
from backend.workflows.runner import StageError

router = APIRouter(prefix="/api")

User = Dict[str, Any]


def _day(date: Optional[str]) -> Optional[tuple]:
    if not date:
        return None
    try:
        d = datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        raise HTTPException(422, "date must be YYYY-MM-DD (UTC)")
    start = d.timestamp()
    return start, start + 86400


def _visible_ids(ctx: AppContext, user: User) -> Optional[set]:
    """None means unrestricted."""
    if user["role"] == "manager":
        return set(user.get("pandal_ids") or [])
    return None


def _cameras_for(ctx: AppContext, pandal_id: int) -> List[Dict[str, Any]]:
    cams = ctx.repos.cameras.list({"pandal_id": pandal_id})
    for c in cams:
        c["connection_note"] = ("Simulated source: no physical camera is connected."
                                if c["source_type"] == "simulated" else
                                "Registered only; connection is not verified until frames are processed.")
    return cams


# ---------------------------------------------------------------- health & auth
@router.get("/health")
def health(ctx: AppContext = Depends(get_ctx)) -> Dict[str, Any]:
    try:
        ctx.repos.db.one("SELECT 1 AS ok")
        db = "ok"
    except sqlite3.Error:
        db = "error"
    return {"status": "ok" if db == "ok" else "degraded", "database": db, "time": time.time(),
            "auth": "evaluation sign-in (not production security)", "data_source": "simulated"}


@router.post("/auth/login")
def login(body: LoginIn, ctx: AppContext = Depends(get_ctx)) -> Dict[str, Any]:
    res = ctx.auth.login(body.username, body.password)
    if res is None:
        ctx.repos.audit.log(body.username, "login.failed", "session")
        raise HTTPException(401, "Invalid username or password")
    ctx.repos.audit.log(body.username, "login", "session")
    return {**res, "demo_auth": True}


@router.post("/auth/register", status_code=201)
def register(body: RegisterIn, ctx: AppContext = Depends(get_ctx)) -> Dict[str, Any]:
    if ctx.repos.users.list({"username": body.username}):
        raise HTTPException(409, "Username already taken")
    u = ctx.auth.create_user(body.username, body.password, "auditor", body.display_name or body.username)
    ctx.repos.audit.log(body.username, "register", "user", u["id"], {"role": "auditor", "verified": False})
    return {"user": public_user(u), "demo_auth": True,
            "note": "Evaluation registration: read-only role, no email verification."}


@router.post("/auth/logout")
def logout(token: str = Depends(bearer_token), user: User = Depends(current_user),
           ctx: AppContext = Depends(get_ctx)) -> Dict[str, Any]:
    ctx.auth.logout(token)
    ctx.repos.audit.log(user["username"], "logout", "session")
    return {"ok": True}


@router.get("/auth/me")
def me(user: User = Depends(current_user)) -> Dict[str, Any]:
    return {"user": public_user(user), "demo_auth": True}


# ---------------------------------------------------------------- pandals
@router.get("/locations")
def list_locations(user: User = Depends(require("read")), ctx: AppContext = Depends(get_ctx)) -> List[Dict[str, Any]]:
    visible = _visible_ids(ctx, user)
    pandals = [p for p in ctx.repos.pandals.list({"active": 1}) if visible is None or p["id"] in visible]
    return [{**loc, "pandal_count": len([p for p in pandals if p.get("location_id") == loc["id"]])}
            for loc in ensure_locations(ctx.repos) if loc["active"]]


@router.post("/locations/{location_id}/pandals", status_code=201)
def create_location_pandal(location_id: int, body: LocationPandalIn, user: User = Depends(require("registry:write")),
                           ctx: AppContext = Depends(get_ctx)) -> Dict[str, Any]:
    if ctx.repos.locations.get(location_id) is None:
        raise HTTPException(404, "Location not found")
    if ctx.repos.pandals.list({"name": body.name.strip()}):
        raise HTTPException(409, "A pandal (association) with this name already exists")
    try:
        res = create_structured_pandal(ctx.repos, location_id, body.name.strip(), body.zone_names, body.max_people,
                                       body.warn_pct, body.critical_pct)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    ctx.repos.audit.log(user["username"], "pandal.create", "pandal", res["pandal"]["id"],
                        {"name": body.name, "location_id": location_id, "zones": body.zone_names})
    return res


@router.put("/zones/{zone_id}")
def update_zone(zone_id: int, body: ZoneUpdate, user: User = Depends(require("registry:write")),
                ctx: AppContext = Depends(get_ctx)) -> Dict[str, Any]:
    z = ctx.repos.zones.get(zone_id)
    if z is None:
        raise HTTPException(404, "Zone not found")
    load_pandal(ctx, user, z["pandal_id"])
    changes = body.model_dump(exclude_none=True)
    if "name" in changes:
        changes["name"] = changes["name"].strip()
        others = [o for o in ctx.repos.zones.list({"pandal_id": z["pandal_id"]}) if o["id"] != zone_id]
        if any(o["name"].lower() == changes["name"].lower() for o in others):
            raise HTTPException(409, "Another zone in this pandal already has that name")
    updated = ctx.repos.zones.update(zone_id, **changes)
    ctx.repos.audit.log(user["username"], "zone.update", "zone", zone_id, {"fields": sorted(changes)})
    return updated  # type: ignore[return-value]


@router.get("/pandals")
def list_pandals(include_inactive: bool = False, location_id: Optional[int] = None,
                 user: User = Depends(require("read")),
                 ctx: AppContext = Depends(get_ctx)) -> List[Dict[str, Any]]:
    visible = _visible_ids(ctx, user)
    out = []
    for p in ctx.repos.pandals.list({"active": None if include_inactive else 1, "location_id": location_id}):
        if visible is not None and p["id"] not in visible:
            continue
        out.append({**p, "zones": ctx.repos.zones.list({"pandal_id": p["id"]}), "cameras": _cameras_for(ctx, p["id"])})
    return out


@router.post("/pandals", status_code=201)
def create_pandal(body: PandalIn, user: User = Depends(require("registry:write")),
                  ctx: AppContext = Depends(get_ctx)) -> Dict[str, Any]:
    if ctx.repos.pandals.list({"name": body.name}):
        raise HTTPException(409, "A pandal with this name already exists")
    now = time.time()
    p = ctx.repos.pandals.create(**body.model_dump(), active=1, created_at=now, updated_at=now)
    ctx.repos.audit.log(user["username"], "pandal.create", "pandal", p["id"], {"name": p["name"]})
    return p


@router.get("/pandals/{pandal_id}")
def get_pandal(pandal_id: int, user: User = Depends(require("read")),
               ctx: AppContext = Depends(get_ctx)) -> Dict[str, Any]:
    p = load_pandal(ctx, user, pandal_id)
    return {"pandal": p, "zones": ctx.repos.zones.list({"pandal_id": pandal_id}),
            "cameras": _cameras_for(ctx, pandal_id), "state": ctx.monitor.compute_state(p),
            "incidents": ctx.repos.incidents.list({"pandal_id": pandal_id}, desc=True, limit=50),
            "runs": ctx.repos.runs.list({"pandal_id": pandal_id}, order="started", desc=True, limit=10)}


@router.put("/pandals/{pandal_id}")
def update_pandal(pandal_id: int, body: PandalUpdate, user: User = Depends(require("registry:write")),
                  ctx: AppContext = Depends(get_ctx)) -> Dict[str, Any]:
    p = load_pandal(ctx, user, pandal_id)
    changes = body.model_dump(exclude_none=True)
    merged = {**p, **changes}
    try:
        PandalIn(name=merged["name"], location=merged["location"], capacity=merged["capacity"],
                 warn_pct=merged["warn_pct"], critical_pct=merged["critical_pct"], meta=merged.get("meta") or {})
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    if "name" in changes and changes["name"] != p["name"] and ctx.repos.pandals.list({"name": changes["name"]}):
        raise HTTPException(409, "A pandal with this name already exists")
    updated = ctx.repos.pandals.update(pandal_id, **changes, updated_at=time.time())
    ctx.repos.audit.log(user["username"], "pandal.update", "pandal", pandal_id,
                        {"changed": {k: [p[k], changes[k]] for k in changes if k != "meta"}})
    return updated  # type: ignore[return-value]


@router.delete("/pandals/{pandal_id}")
def deactivate_pandal(pandal_id: int, user: User = Depends(require("registry:write")),
                      ctx: AppContext = Depends(get_ctx)) -> Dict[str, Any]:
    load_pandal(ctx, user, pandal_id)
    ctx.repos.pandals.update(pandal_id, active=0, updated_at=time.time())
    ctx.repos.audit.log(user["username"], "pandal.deactivate", "pandal", pandal_id)
    return {"ok": True, "deactivated": pandal_id}


@router.post("/pandals/{pandal_id}/zones", status_code=201)
def create_zone(pandal_id: int, body: ZoneIn, user: User = Depends(require("registry:write")),
                ctx: AppContext = Depends(get_ctx)) -> Dict[str, Any]:
    load_pandal(ctx, user, pandal_id)
    z = ctx.repos.zones.create(pandal_id=pandal_id, name=body.name,
                               exit_capacity_per_min=body.exit_capacity_per_min, active=1)
    ctx.repos.audit.log(user["username"], "zone.create", "zone", z["id"], {"pandal_id": pandal_id})
    return z


@router.get("/pandals/{pandal_id}/series")
def series(pandal_id: int, limit: int = 60, user: User = Depends(require("read")),
           ctx: AppContext = Depends(get_ctx)) -> Dict[str, Any]:
    p = load_pandal(ctx, user, pandal_id)
    rows = ctx.repos.db.query("SELECT * FROM measurements WHERE pandal_id=? ORDER BY id DESC LIMIT ?",
                              [pandal_id, max(1, min(limit, 500))])
    rows.reverse()
    state = ctx.monitor.compute_state(p)
    return {"pandal_id": pandal_id, "source": "simulated", "points": [
        {"minute": r["sim_minute"], "occupancy": r["occupancy"], "entry_rate": r["entry_rate"],
         "exit_rate": r["exit_rate"], "wall_ts": r["wall_ts"]} for r in rows],
        "warn_threshold": state["warn_threshold"], "critical_threshold": state["critical_threshold"],
        "state": state}


@router.post("/pandals/{pandal_id}/explain")
def explain(pandal_id: int, user: User = Depends(require("read")),
            ctx: AppContext = Depends(get_ctx)) -> Dict[str, Any]:
    p = load_pandal(ctx, user, pandal_id)
    state = ctx.monitor.compute_state(p)
    if not state["has_data"]:
        raise HTTPException(409, "No measurements yet; start the simulation first")
    try:
        res = ctx.monitor.explain_state(p, state, "manual_request")
    except StageError as exc:
        raise HTTPException(409, str(exc))
    ctx.repos.audit.log(user["username"], "explain.request", "pandal", pandal_id,
                        {"llm_call_id": res["call"]["id"]})
    return {"text": res["text"], "call": res["call"], "state": state,
            "disclaimer": "Decision support for authorized human review. Not an instruction. "
                          "Authoritative values come from the forecasting engine."}


# ---------------------------------------------------------------- cameras
def _check_camera_refs(ctx: AppContext, user: User, pandal_id: int, zone_id: Optional[int]) -> None:
    load_pandal(ctx, user, pandal_id)
    if zone_id is not None:
        z = ctx.repos.zones.get(zone_id)
        if z is None or z["pandal_id"] != pandal_id:
            raise HTTPException(422, "zone_id does not belong to this pandal")


@router.get("/cameras")
def list_cameras(pandal_id: Optional[int] = None, user: User = Depends(require("read")),
                 ctx: AppContext = Depends(get_ctx)) -> List[Dict[str, Any]]:
    visible = _visible_ids(ctx, user)
    out: List[Dict[str, Any]] = []
    for p in ctx.repos.pandals.list({"id": pandal_id}):
        if visible is None or p["id"] in visible:
            out.extend(_cameras_for(ctx, p["id"]))
    return out


@router.post("/cameras", status_code=201)
def create_camera(body: CameraIn, user: User = Depends(require("registry:write")),
                  ctx: AppContext = Depends(get_ctx)) -> Dict[str, Any]:
    _check_camera_refs(ctx, user, body.pandal_id, body.zone_id)
    if body.zone_id is not None and ctx.repos.cameras.list({"zone_id": body.zone_id, "active": 1}):
        raise HTTPException(409, "This zone already has a camera. Change its source or set it to passive instead.")
    c = ctx.repos.cameras.create(**body.model_dump(), active=1, status="registered")
    ctx.repos.audit.log(user["username"], "camera.create", "camera", c["id"], {"pandal_id": body.pandal_id})
    return c


@router.put("/cameras/{camera_id}")
def update_camera(camera_id: int, body: CameraUpdate, user: User = Depends(require("registry:write")),
                  ctx: AppContext = Depends(get_ctx)) -> Dict[str, Any]:
    c = ctx.repos.cameras.get(camera_id)
    if c is None:
        raise HTTPException(404, "Camera not found")
    changes = body.model_dump(exclude_none=True)
    _check_camera_refs(ctx, user, c["pandal_id"], changes.get("zone_id"))
    if changes.get("source_type") == "local_video" and not (changes.get("source_ref") or c["source_ref"]):
        raise HTTPException(422, "local_video requires source_ref")
    updated = ctx.repos.cameras.update(camera_id, **changes)
    ctx.repos.audit.log(user["username"], "camera.update", "camera", camera_id, {"fields": sorted(changes)})
    return updated  # type: ignore[return-value]


@router.delete("/cameras/{camera_id}")
def deactivate_camera(camera_id: int, user: User = Depends(require("registry:write")),
                      ctx: AppContext = Depends(get_ctx)) -> Dict[str, Any]:
    if ctx.repos.cameras.get(camera_id) is None:
        raise HTTPException(404, "Camera not found")
    ctx.repos.cameras.update(camera_id, active=0, status="inactive")
    ctx.repos.audit.log(user["username"], "camera.deactivate", "camera", camera_id)
    return {"ok": True, "deactivated": camera_id}


# ---------------------------------------------------------------- dashboard & simulation
@router.get("/dashboard")
def dashboard(pandal_id: Optional[int] = None, status: Optional[str] = None, date: Optional[str] = None,
              user: User = Depends(require("read")), ctx: AppContext = Depends(get_ctx)) -> Dict[str, Any]:
    visible = _visible_ids(ctx, user)
    day = _day(date)
    cards: List[Dict[str, Any]] = []
    for p in ctx.repos.pandals.list({"active": 1, "id": pandal_id}):
        if visible is not None and p["id"] not in visible:
            continue
        state = ctx.monitor.compute_state(p)
        if status and status not in (state["status"], state.get("risk")):
            continue
        active = [i for i in ctx.repos.incidents.list({"pandal_id": p["id"]}) if i["status"] != "resolved"]
        cards.append({"pandal": p, "state": state, "cameras": _cameras_for(ctx, p["id"]),
                      "zones": ctx.repos.zones.list({"pandal_id": p["id"]}),
                      "active_incidents": len(active),
                      "critical_incidents": len([i for i in active if i["severity"] == "critical"])})
    ids = {c["pandal"]["id"] for c in cards}
    runs = [r for r in ctx.repos.runs.list(order="started", desc=True, limit=200)
            if r["pandal_id"] in ids and (day is None or day[0] <= r["started"] < day[1])][:10]
    ops = user["role"] in ("admin", "operator", "auditor")
    return {"simulation": ctx.monitor.status(), "pandals": cards, "recent_runs": runs,
            "llm": llm_obs.summary(ctx.repos) if ops else None, "server_time": time.time(),
            "data_notice": "All measurements are simulated."}


def _audit_sim(ctx: AppContext, user: User, action: str, detail: Optional[Dict[str, Any]] = None) -> None:
    ctx.repos.audit.log(user["username"], f"simulation.{action}", "simulation", None, detail)


@router.get("/simulation")
def sim_status(pandal_id: Optional[int] = None, user: User = Depends(require("read")),
               ctx: AppContext = Depends(get_ctx)) -> Dict[str, Any]:
    if pandal_id is not None:
        load_pandal(ctx, user, pandal_id)
    return ctx.monitor.status(pandal_id)


@router.post("/simulation/start")
def sim_start(body: SimIn, user: User = Depends(require("sim:control")),
              ctx: AppContext = Depends(get_ctx)) -> Dict[str, Any]:
    if body.pandal_id is not None:
        load_pandal(ctx, user, body.pandal_id)
    res = ctx.monitor.start(body.scenario, body.speed, body.pandal_id)
    _audit_sim(ctx, user, "start", {"scenario": body.scenario, "speed": body.speed, "pandal_id": body.pandal_id})
    return res


@router.post("/simulation/pause")
def sim_pause(pandal_id: Optional[int] = None, user: User = Depends(require("sim:control")),
              ctx: AppContext = Depends(get_ctx)) -> Dict[str, Any]:
    _audit_sim(ctx, user, "pause", {"pandal_id": pandal_id})
    return ctx.monitor.pause(pandal_id)


@router.post("/simulation/resume")
def sim_resume(pandal_id: Optional[int] = None, user: User = Depends(require("sim:control")),
               ctx: AppContext = Depends(get_ctx)) -> Dict[str, Any]:
    _audit_sim(ctx, user, "resume", {"pandal_id": pandal_id})
    return ctx.monitor.resume(pandal_id)


@router.post("/simulation/reset")
def sim_reset(pandal_id: Optional[int] = None, user: User = Depends(require("sim:control")),
              ctx: AppContext = Depends(get_ctx)) -> Dict[str, Any]:
    _audit_sim(ctx, user, "reset", {"pandal_id": pandal_id})
    return ctx.monitor.reset(pandal_id)


@router.post("/simulation/tick")
def sim_tick(user: User = Depends(require("sim:control")), ctx: AppContext = Depends(get_ctx)) -> Dict[str, Any]:
    run_ids = ctx.monitor.tick()
    return {"run_ids": run_ids, "status": ctx.monitor.status()}


# ---------------------------------------------------------------- incidents
@router.get("/incidents")
def list_incidents(status: Optional[str] = None, pandal_id: Optional[int] = None, severity: Optional[str] = None,
                   date: Optional[str] = None, user: User = Depends(require("read")),
                   ctx: AppContext = Depends(get_ctx)) -> List[Dict[str, Any]]:
    visible = _visible_ids(ctx, user)
    day = _day(date)
    rows = ctx.repos.incidents.list({"status": status, "pandal_id": pandal_id, "severity": severity},
                                    desc=True, limit=300)
    return [r for r in rows if (visible is None or r["pandal_id"] in visible)
            and (day is None or day[0] <= r["created_at"] < day[1])]


def _load_incident(ctx: AppContext, user: User, incident_id: int) -> Dict[str, Any]:
    inc = ctx.repos.incidents.get(incident_id)
    if inc is None:
        raise HTTPException(404, "Incident not found")
    if not can_access_pandal(user, inc["pandal_id"]):
        raise HTTPException(403, "You are not authorized for this pandal")
    return inc


@router.get("/incidents/{incident_id}")
def get_incident(incident_id: int, user: User = Depends(require("read")),
                 ctx: AppContext = Depends(get_ctx)) -> Dict[str, Any]:
    inc = _load_incident(ctx, user, incident_id)
    return {"incident": inc, "events": ctx.repos.events.list({"incident_id": incident_id}),
            "run": ctx.repos.runs.get(inc["run_id"]) if inc["run_id"] else None}


@router.post("/incidents/{incident_id}/transition")
def transition(incident_id: int, body: TransitionIn, user: User = Depends(require("incident:write")),
               ctx: AppContext = Depends(get_ctx)) -> Dict[str, Any]:
    _load_incident(ctx, user, incident_id)
    try:
        return ctx.incidents.transition(incident_id, body.to, user["username"], body.note, body.assignee)
    except InvalidTransition as exc:
        raise HTTPException(409, str(exc))


@router.post("/incidents/{incident_id}/notes", status_code=201)
def add_note(incident_id: int, body: NoteIn, user: User = Depends(require("incident:write")),
             ctx: AppContext = Depends(get_ctx)) -> Dict[str, Any]:
    _load_incident(ctx, user, incident_id)
    return ctx.incidents.add_note(incident_id, user["username"], body.note)


# ---------------------------------------------------------------- workflow runs
@router.get("/runs")
def list_runs(status: Optional[str] = None, pandal_id: Optional[int] = None, date: Optional[str] = None,
              limit: int = 100, user: User = Depends(require("read")),
              ctx: AppContext = Depends(get_ctx)) -> List[Dict[str, Any]]:
    visible = _visible_ids(ctx, user)
    day = _day(date)
    rows = ctx.repos.runs.list({"status": status, "pandal_id": pandal_id}, order="started", desc=True,
                               limit=max(1, min(limit, 500)))
    return [r for r in rows if (visible is None or r["pandal_id"] in visible)
            and (day is None or day[0] <= r["started"] < day[1])]


@router.get("/runs/{run_id}")
def get_run(run_id: str, user: User = Depends(require("read")), ctx: AppContext = Depends(get_ctx)) -> Dict[str, Any]:
    run = ctx.repos.runs.get(run_id)
    if run is None:
        raise HTTPException(404, "Run not found")
    if run["pandal_id"] is not None and not can_access_pandal(user, run["pandal_id"]):
        raise HTTPException(403, "You are not authorized for this pandal")
    ids = run.get("incident_ids") or []
    return {"run": run, "stages": ctx.repos.stages.list({"run_id": run_id}, order="id"),
            "measurements": ctx.repos.measurements.list({"run_id": run_id}),
            "forecasts": ctx.repos.forecasts.list({"run_id": run_id}),
            "incidents": [i for i in (ctx.repos.incidents.get(x) for x in ids) if i],
            "llm_calls": ctx.repos.llm_calls.list({"run_id": run_id})}


# ---------------------------------------------------------------- observability, settings, audit
@router.get("/llm/summary")
def llm_summary(user: User = Depends(require("ops:read")), ctx: AppContext = Depends(get_ctx)) -> Dict[str, Any]:
    return llm_obs.summary(ctx.repos)


@router.get("/llm/calls")
def llm_calls(limit: int = 100, user: User = Depends(require("ops:read")),
              ctx: AppContext = Depends(get_ctx)) -> List[Dict[str, Any]]:
    return ctx.repos.llm_calls.list(order="id", desc=True, limit=max(1, min(limit, 500)))


@router.get("/system/metrics")
def sys_metrics(user: User = Depends(require("ops:read")), ctx: AppContext = Depends(get_ctx)) -> Dict[str, Any]:
    return system_metrics(ctx.repos, ctx.request_metrics)


def _settings_view(ctx: AppContext) -> Dict[str, Any]:
    r = ctx.repos
    return {"max_data_age_s": r.get_setting("max_data_age_s", 180), "sim_speed": ctx.monitor.speed,
            "llm_budget_usd": r.get_setting("llm_budget_usd"),
            "predicted_critical_lead_min": r.get_setting("predicted_critical_lead_min", 5),
            "rules_enabled": {**DEFAULT_RULES, **(r.get_setting("rules_enabled", {}) or {})},
            "data_source": "simulated",
            "model_integration": {"active_provider": ctx.provider.name, "model": ctx.provider.model,
                                  "is_real_model": ctx.provider.name != "local-template",
                                  "meta_adapter": "enabled" if ctx.provider.name == "meta" else "implemented, not enabled",
                                  "credential_set": bool(ctx.provider_info.get("credential_set")),
                                  "note": ctx.provider_info.get("note", ""),
                                  "credentials_required": ctx.provider.name == "meta"},
            "auth_mode": "evaluation sign-in (not production security)",
            "notifications": "disabled (local MVP, nothing is sent)"}


@router.get("/settings")
def get_settings(user: User = Depends(require("ops:read")), ctx: AppContext = Depends(get_ctx)) -> Dict[str, Any]:
    return _settings_view(ctx)


@router.put("/settings")
def put_settings(body: SettingsIn, user: User = Depends(require("settings:write")),
                 ctx: AppContext = Depends(get_ctx)) -> Dict[str, Any]:
    changes = body.model_dump(exclude_none=True)
    for k, v in changes.items():
        if k == "sim_speed":
            ctx.monitor.speed = float(v)
        else:
            ctx.repos.set_setting(k, v)
    ctx.repos.audit.log(user["username"], "settings.update", "settings", None, {"changed": changes})
    return _settings_view(ctx)


@router.get("/audit")
def audit_log(limit: int = 200, user: User = Depends(require("audit:read")),
              ctx: AppContext = Depends(get_ctx)) -> List[Dict[str, Any]]:
    return ctx.repos.audit.list(order="id", desc=True, limit=max(1, min(limit, 1000)))
