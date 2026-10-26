"""Read-only tool registry for the assistant, plus the (separate) confirmation-gated action registry.

Every tool is a thin wrapper over the same repositories/services the HTTP routes use, and applies the same
checks the route applies: the permission string passed to `require(...)` and the manager pandal scope.
Identity always comes from the server-side session, never from model output.
Nothing here writes data. State-changing capabilities live in ACTIONS and only run after the signed-in
user confirms them (see actions.py).
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Literal, Optional, Type

from pydantic import BaseModel, ConfigDict, Field

from backend.observability import llm as llm_obs
from backend.observability.telemetry import system_metrics
from backend.security.auth import can_access_pandal, has_permission, public_user
from backend.services.context import AppContext
from backend.services.incidents import DEFAULT_RULES
from backend.services.structure import WINDOW_S, ensure_locations, wall_view

User = Dict[str, Any]
MAX_TOOL_OUTPUT_CHARS = 12_000
LIST_CAP = 50
OFFLINE_STATUSES = ("error", "stale", "inactive")


class ToolError(Exception):
    """A safe, user-presentable failure (bad id, forbidden scope, nothing to report)."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class ToolEnv:
    ctx: AppContext
    user: User
    app_state: Any = None   # FastAPI app.state, for the vision detector info (read only)


class Args(BaseModel):
    model_config = ConfigDict(extra="forbid")


class NoArgs(Args):
    pass


class PandalArgs(Args):
    pandal_id: int = Field(ge=1, description="Numeric id of the pandal")


class PandalOptArgs(Args):
    pandal_id: Optional[int] = Field(default=None, ge=1, description="Limit to one pandal; omit for all you may see")


class LimitArgs(Args):
    limit: int = Field(default=20, ge=1, le=LIST_CAP)


class ListPandalsArgs(Args):
    location_id: Optional[int] = Field(default=None, ge=1)
    include_inactive: bool = False


class SeriesArgs(Args):
    pandal_id: int = Field(ge=1)
    limit: int = Field(default=30, ge=1, le=120, description="Most recent N measurement points")


class VisionCountsArgs(Args):
    pandal_id: Optional[int] = Field(default=None, ge=1)
    camera_id: Optional[int] = Field(default=None, ge=1)
    limit: int = Field(default=20, ge=1, le=LIST_CAP)


class IncidentListArgs(Args):
    status: Optional[Literal["open", "acknowledged", "assigned", "resolved", "unresolved"]] = Field(
        default=None, description="'unresolved' means every incident that is not resolved")
    pandal_id: Optional[int] = Field(default=None, ge=1)
    severity: Optional[Literal["info", "warning", "critical"]] = None
    date: Optional[str] = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$", description="UTC day, YYYY-MM-DD")
    limit: int = Field(default=20, ge=1, le=LIST_CAP)


class IncidentArgs(Args):
    incident_id: int = Field(ge=1)


class RunListArgs(Args):
    status: Optional[str] = Field(default=None, max_length=30)
    pandal_id: Optional[int] = Field(default=None, ge=1)
    limit: int = Field(default=10, ge=1, le=LIST_CAP)


class RunArgs(Args):
    run_id: str = Field(min_length=1, max_length=64)


class CameraListArgs(Args):
    pandal_id: Optional[int] = Field(default=None, ge=1)
    status: Optional[str] = Field(default=None, max_length=30)


class SimStatusArgs(Args):
    pandal_id: Optional[int] = Field(default=None, ge=1)


@dataclass
class Tool:
    name: str
    description: str
    args: Type[Args]
    permission: str
    source: str                      # route / service this wraps (documented in docs/assistant-tools.md)
    handler: Callable[[ToolEnv, Any], Any]
    scoped: bool                     # True when manager pandal scope is applied
    limitations: str = ""

    def spec(self) -> Dict[str, Any]:
        schema = self.args.model_json_schema()
        schema.pop("title", None)
        for prop in schema.get("properties", {}).values():
            prop.pop("title", None)
        schema["additionalProperties"] = False
        return {"type": "function", "name": self.name, "description": self.description, "parameters": schema}


# ---------------------------------------------------------------- helpers
def _visible(env: ToolEnv) -> Optional[set]:
    return set(env.user.get("pandal_ids") or []) if env.user["role"] == "manager" else None


def _pandal(env: ToolEnv, pandal_id: int) -> Dict[str, Any]:
    p = env.ctx.repos.pandals.get(pandal_id)
    if p is None:
        raise ToolError("not_found", f"No pandal with id {pandal_id}.")
    if not can_access_pandal(env.user, pandal_id):
        raise ToolError("forbidden", "You are not authorized for this pandal.")
    return p


def _pandals(env: ToolEnv, pandal_id: Optional[int] = None, include_inactive: bool = False) -> List[Dict[str, Any]]:
    if pandal_id is not None:
        return [_pandal(env, pandal_id)]
    vis = _visible(env)
    return [p for p in env.ctx.repos.pandals.list({"active": None if include_inactive else 1})
            if vis is None or p["id"] in vis]


def _names(env: ToolEnv) -> Dict[int, str]:
    return {p["id"]: p["name"] for p in env.ctx.repos.pandals.list()}


def _day(date: Optional[str]) -> Optional[tuple]:
    if not date:
        return None
    try:
        start = datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp()
    except ValueError:
        raise ToolError("bad_argument", "date must be YYYY-MM-DD (UTC)") from None
    return start, start + 86400


def _camera_view(c: Dict[str, Any], names: Dict[int, str]) -> Dict[str, Any]:
    """Camera fields the assistant may see. `health` is derived from stored status only (no live probe)."""
    status = c.get("status")
    if not c.get("active"):
        health = "offline"
    elif status in OFFLINE_STATUSES:
        health = "offline"
    elif c.get("last_seen") is None:
        health = "unknown"   # registered but never reported a frame: not proof of being up or down
    else:
        health = "online"
    return {"id": c["id"], "name": c.get("name"), "pandal_id": c["pandal_id"],
            "pandal_name": names.get(c["pandal_id"]), "zone_id": c.get("zone_id"),
            "source_type": c.get("source_type"), "mode": c.get("mode"), "active": bool(c.get("active")),
            "status": status, "last_seen": c.get("last_seen"), "last_error": c.get("last_error"),
            "health": health}


def _cameras(env: ToolEnv, pandal_id: Optional[int], status: Optional[str] = None) -> List[Dict[str, Any]]:
    names = _names(env)
    out = []
    for p in _pandals(env, pandal_id):
        for c in env.ctx.repos.cameras.list({"pandal_id": p["id"]}):
            if status and c.get("status") != status:
                continue
            out.append(_camera_view(c, names))
    return out


def _incident_view(i: Dict[str, Any], names: Dict[int, str]) -> Dict[str, Any]:
    return {**i, "pandal_name": names.get(i["pandal_id"])}


def _settings(env: ToolEnv) -> Dict[str, Any]:
    r, ctx = env.ctx.repos, env.ctx
    return {"max_data_age_s": r.get_setting("max_data_age_s", 180), "sim_speed": ctx.monitor.speed,
            "llm_budget_usd": r.get_setting("llm_budget_usd"),
            "predicted_critical_lead_min": r.get_setting("predicted_critical_lead_min", 5),
            "rules_enabled": {**DEFAULT_RULES, **(r.get_setting("rules_enabled", {}) or {})},
            "data_source": "simulated"}


def _cap(rows: List[Any], limit: int) -> Dict[str, Any]:
    return {"total_matching": len(rows), "returned": min(len(rows), limit), "items": rows[:limit]}


# ---------------------------------------------------------------- read-only handlers
def t_current_user(env: ToolEnv, a: NoArgs) -> Any:
    u = public_user(env.user)
    perms = [p for p in ("read", "registry:write", "sim:control", "incident:write", "ops:read",
                         "settings:write", "audit:read") if has_permission(env.user, p)]
    return {"user": {k: u[k] for k in ("username", "role", "display_name")}, "permissions": perms,
            "pandal_scope": u["pandal_ids"] if u["role"] == "manager" else "all pandals"}


def t_locations(env: ToolEnv, a: NoArgs) -> Any:
    pandals = _pandals(env)
    return [{**loc, "pandal_count": len([p for p in pandals if p.get("location_id") == loc["id"]])}
            for loc in ensure_locations(env.ctx.repos) if loc["active"]]


def t_list_pandals(env: ToolEnv, a: ListPandalsArgs) -> Any:
    rows = [p for p in _pandals(env, None, a.include_inactive)
            if a.location_id is None or p.get("location_id") == a.location_id]
    return _cap([{"id": p["id"], "name": p["name"], "location_id": p.get("location_id"),
                  "capacity": p.get("capacity"), "warn_pct": p.get("warn_pct"),
                  "critical_pct": p.get("critical_pct"), "active": bool(p.get("active"))} for p in rows], LIST_CAP)


def t_get_pandal(env: ToolEnv, a: PandalArgs) -> Any:
    p = _pandal(env, a.pandal_id)
    r = env.ctx.repos
    active = [i for i in r.incidents.list({"pandal_id": p["id"]}) if i["status"] != "resolved"]
    return {"pandal": p, "zones": r.zones.list({"pandal_id": p["id"]}),
            "cameras": _cameras(env, p["id"]), "state": env.ctx.monitor.compute_state(p),
            "unresolved_incident_count": len(active)}


def t_zones(env: ToolEnv, a: PandalArgs) -> Any:
    p = _pandal(env, a.pandal_id)
    return env.ctx.repos.zones.list({"pandal_id": p["id"]})


def t_cameras(env: ToolEnv, a: CameraListArgs) -> Any:
    return _cap(_cameras(env, a.pandal_id, a.status), 200)


def t_camera_health(env: ToolEnv, a: SimStatusArgs) -> Any:
    cams = _cameras(env, a.pandal_id)
    groups: Dict[str, List[Dict[str, Any]]] = {"offline": [], "online": [], "unknown": []}
    for c in cams:
        groups[c["health"]].append(c)
    return {"counts": {k: len(v) for k, v in groups.items()}, "total": len(cams),
            "offline_cameras": groups["offline"], "unknown_cameras": groups["unknown"],
            "online_cameras": [{"id": c["id"], "name": c["name"], "pandal_name": c["pandal_name"]}
                               for c in groups["online"]],
            "rule": ("offline = camera deactivated, or stored status in error/stale/inactive. "
                     "unknown = active but has never reported a frame. online = active with a recorded frame "
                     "and no error status. Status comes from stored records, not a live network probe.")}


def t_camera_wall(env: ToolEnv, a: PandalArgs) -> Any:
    p = _pandal(env, a.pandal_id)
    w = wall_view(env.ctx.repos, p, window_s=WINDOW_S)
    for z in w.get("zones", []):
        z.pop("series", None)
    w.pop("series", None)
    w.pop("sum_series", None)
    return w


def t_vision_status(env: ToolEnv, a: NoArgs) -> Any:
    st = env.app_state
    det = getattr(st, "detector", None)
    if det is None:
        return {"detector": None, "note": "Vision detector information is not available."}
    info = getattr(st, "detector_info", {}) or {}
    return {"detector": det.name, "is_real_model": det.is_real_model, "note": info.get("note", ""),
            "frames_leave_this_computer": det.is_real_model,
            "price_per_image_usd": det.price_per_call_usd,
            "notice": ("Camera frames are sent to Meta SAM 3.1 (billed per image)." if det.is_real_model else
                       "Simulated detector: no frames are sent anywhere and no real people are detected.")}


def t_vision_counts(env: ToolEnv, a: VisionCountsArgs) -> Any:
    ids = {p["id"] for p in _pandals(env, a.pandal_id)}
    where, params = [], []
    if a.camera_id is not None:
        cam = env.ctx.repos.cameras.get(a.camera_id)
        if cam is None:
            raise ToolError("not_found", f"No camera with id {a.camera_id}.")
        if cam["pandal_id"] not in ids:
            raise ToolError("forbidden", "You are not authorized for this camera's pandal.")
        where.append("camera_id=?")
        params.append(a.camera_id)
    marks = ",".join("?" for _ in ids) or "NULL"
    where.append(f"pandal_id IN ({marks})")
    params.extend(sorted(ids))
    rows = env.ctx.repos.db.query(f"SELECT * FROM vision_counts WHERE {' AND '.join(where)} ORDER BY id DESC LIMIT ?",
                                  params + [a.limit])
    keep = ("id", "camera_id", "pandal_id", "zone_id", "ts", "count", "status", "error_category",
            "detector", "is_real_model")
    return {"items": [{k: r.get(k) for k in keep if k in r} for r in rows],
            "note": "count is null when the detector call failed; null means unknown, not zero."}


def t_series(env: ToolEnv, a: SeriesArgs) -> Any:
    p = _pandal(env, a.pandal_id)
    rows = env.ctx.repos.db.query("SELECT * FROM measurements WHERE pandal_id=? ORDER BY id DESC LIMIT ?",
                                  [p["id"], a.limit])
    rows.reverse()
    return {"pandal_id": p["id"], "pandal_name": p["name"], "source": "simulated", "points": [
        {"minute": r["sim_minute"], "occupancy": r["occupancy"], "entry_rate": r["entry_rate"],
         "exit_rate": r["exit_rate"], "wall_ts": r["wall_ts"]} for r in rows]}


def t_forecast(env: ToolEnv, a: PandalArgs) -> Any:
    p = _pandal(env, a.pandal_id)
    return {"pandal_name": p["name"], "state": env.ctx.monitor.compute_state(p),
            "note": "Authoritative values come from the deterministic forecast engine; report them as given."}


def t_overview(env: ToolEnv, a: PandalOptArgs) -> Any:
    names = _names(env)
    out = []
    for p in _pandals(env, a.pandal_id):
        s = env.ctx.monitor.compute_state(p)
        active = [i for i in env.ctx.repos.incidents.list({"pandal_id": p["id"]}) if i["status"] != "resolved"]
        out.append({"pandal_id": p["id"], "pandal_name": names.get(p["id"]), "status": s.get("status"),
                    "risk": s.get("risk"), "occupancy": s.get("occupancy"), "capacity": s.get("capacity"),
                    "has_data": s.get("has_data"), "stale": s.get("stale"), "age_s": s.get("age_s"),
                    "unresolved_incidents": len(active),
                    "critical_unresolved": len([i for i in active if i["severity"] == "critical"])})
    return {"simulation": env.ctx.monitor.status(), "pandals": out, "data_source": "simulated"}


def t_incidents(env: ToolEnv, a: IncidentListArgs) -> Any:
    vis, day = _visible(env), _day(a.date)
    if a.pandal_id is not None:
        _pandal(env, a.pandal_id)
    flt = {"pandal_id": a.pandal_id, "severity": a.severity,
           "status": None if a.status in (None, "unresolved") else a.status}
    rows = env.ctx.repos.incidents.list(flt, desc=True, limit=300)
    names = _names(env)
    rows = [r for r in rows if (vis is None or r["pandal_id"] in vis)
            and (day is None or day[0] <= r["created_at"] < day[1])
            and (a.status != "unresolved" or r["status"] != "resolved")]
    return _cap([_incident_view(r, names) for r in rows], a.limit)


def t_incident(env: ToolEnv, a: IncidentArgs) -> Any:
    inc = env.ctx.repos.incidents.get(a.incident_id)
    if inc is None:
        raise ToolError("not_found", f"No incident with id {a.incident_id}.")
    if not can_access_pandal(env.user, inc["pandal_id"]):
        raise ToolError("forbidden", "You are not authorized for this pandal.")
    return {"incident": _incident_view(inc, _names(env)),
            "events": env.ctx.repos.events.list({"incident_id": a.incident_id}),
            "run_id": inc.get("run_id")}


def t_runs(env: ToolEnv, a: RunListArgs) -> Any:
    vis = _visible(env)
    if a.pandal_id is not None:
        _pandal(env, a.pandal_id)
    rows = env.ctx.repos.runs.list({"status": a.status, "pandal_id": a.pandal_id}, order="started", desc=True,
                                   limit=200)
    names = _names(env)
    rows = [{**r, "pandal_name": names.get(r["pandal_id"])} for r in rows
            if vis is None or r["pandal_id"] is None or r["pandal_id"] in vis]
    return _cap(rows, a.limit)


def t_run(env: ToolEnv, a: RunArgs) -> Any:
    r = env.ctx.repos
    run = r.runs.get(a.run_id)
    if run is None:
        raise ToolError("not_found", "No workflow run with that id.")
    if run["pandal_id"] is not None and not can_access_pandal(env.user, run["pandal_id"]):
        raise ToolError("forbidden", "You are not authorized for this pandal.")
    return {"run": run, "stages": r.stages.list({"run_id": a.run_id}, order="id"),
            "forecasts": r.forecasts.list({"run_id": a.run_id}), "incident_ids": run.get("incident_ids") or []}


def t_simulation(env: ToolEnv, a: SimStatusArgs) -> Any:
    if a.pandal_id is not None:
        _pandal(env, a.pandal_id)
    return env.ctx.monitor.status(a.pandal_id)


def t_provider_status(env: ToolEnv, a: NoArgs) -> Any:
    c = env.ctx
    return {"active_provider": c.provider.name, "model": c.provider.model,
            "is_real_model": c.provider.name != "local-template",
            "credential_set": bool(c.provider_info.get("credential_set")), "note": c.provider_info.get("note", "")}


def t_llm_calls(env: ToolEnv, a: LimitArgs) -> Any:
    keep = ("id", "trace_id", "run_id", "provider", "model", "operation", "started", "latency_ms", "input_tokens",
            "output_tokens", "status", "error_category", "retry_count", "est_cost_usd")
    rows = env.ctx.repos.llm_calls.list(order="id", desc=True, limit=a.limit)
    return {"items": [{k: r.get(k) for k in keep} for r in rows],
            "note": "est_cost_usd is an estimate; null means tokens or pricing were unavailable."}


def t_llm_summary(env: ToolEnv, a: NoArgs) -> Any:
    return llm_obs.summary(env.ctx.repos)


def t_health(env: ToolEnv, a: NoArgs) -> Any:
    try:
        env.ctx.repos.db.one("SELECT 1 AS ok")
        db = "ok"
    except Exception:  # noqa: BLE001 - report any storage failure as a state, not a crash
        db = "error"
    return {"status": "ok" if db == "ok" else "degraded", "database": db, "time": time.time(),
            "data_source": "simulated"}


def t_system_metrics(env: ToolEnv, a: NoArgs) -> Any:
    return system_metrics(env.ctx.repos, env.ctx.request_metrics)


def t_settings(env: ToolEnv, a: NoArgs) -> Any:
    return _settings(env)


def t_audit(env: ToolEnv, a: LimitArgs) -> Any:
    rows = env.ctx.repos.audit.list(order="id", desc=True, limit=a.limit)
    return {"items": rows}


def _t(name: str, desc: str, args: Type[Args], perm: str, source: str, fn: Callable[[ToolEnv, Any], Any],
       scoped: bool, lim: str = "") -> Tool:
    return Tool(name, desc, args, perm, source, fn, scoped, lim)


TOOLS: List[Tool] = [
    _t("get_current_user", "Who is signed in: username, role, permissions and pandal scope.", NoArgs, "read",
       "GET /api/auth/me (+ PERMISSIONS table)", t_current_user, False),
    _t("list_locations", "List locations (areas) with the number of pandals you can see in each.", NoArgs, "read",
       "GET /api/locations", t_locations, True),
    _t("list_pandals", "List pandals (id, name, location, capacity, thresholds). Use to resolve a pandal name to an id.",
       ListPandalsArgs, "read", "GET /api/pandals", t_list_pandals, True),
    _t("get_pandal", "One pandal with its zones, cameras, current computed state and unresolved incident count.",
       PandalArgs, "read", "GET /api/pandals/{id}", t_get_pandal, True),
    _t("list_zones", "Zones of a pandal with their limits.", PandalArgs, "read", "GET /api/pandals/{id} (zones)",
       t_zones, True),
    _t("list_cameras", "Cameras with stored status, last_seen, last_error and a derived health (online/offline/unknown).",
       CameraListArgs, "read", "GET /api/cameras", t_cameras, True,
       "Status is stored data, not a live probe."),
    _t("get_camera_health", "Group cameras into offline / unknown / online with the rule used. Use for 'which cameras are offline'.",
       SimStatusArgs, "read", "GET /api/cameras (derived)", t_camera_health, True,
       "Derived from stored status; no live network probe."),
    _t("get_camera_wall", "Camera-wall view of a pandal: per-zone latest person count, level, staleness and totals.",
       PandalArgs, "read", "GET /api/vision/pandals/{id}/wall", t_camera_wall, True,
       "Time-series arrays are removed to keep output small."),
    _t("get_vision_status", "Which people-counting detector is active (simulated vs Meta SAM 3.1) and whether frames leave the computer.",
       NoArgs, "read", "GET /api/vision/status", t_vision_status, False,
       "Spend figures are not included; use the model-usage tools."),
    _t("get_vision_counts", "Recent per-frame people counts from the vision detector (count null = failed/unknown).",
       VisionCountsArgs, "read", "vision_counts table (via wall/analyze data)", t_vision_counts, True),
    _t("get_measurements", "Recent occupancy / entry / exit measurement points for a pandal (simulated).",
       SeriesArgs, "read", "GET /api/pandals/{id}/series", t_series, True),
    _t("get_forecast_and_risk", "Authoritative current state, forecast and risk of a pandal from the deterministic engine.",
       PandalArgs, "read", "MonitorService.compute_state (GET /api/pandals/{id})", t_forecast, True),
    _t("get_overview", "Dashboard-style overview of every pandal you may see: status, risk, occupancy, unresolved incidents.",
       PandalOptArgs, "read", "GET /api/dashboard", t_overview, True),
    _t("list_incidents", "List incidents with pandal names. Filter by status (use 'unresolved' for not-resolved), pandal, severity, UTC day.",
       IncidentListArgs, "read", "GET /api/incidents", t_incidents, True),
    _t("get_incident", "One incident with its event history.", IncidentArgs, "read", "GET /api/incidents/{id}",
       t_incident, True),
    _t("list_workflow_runs", "Recent workflow runs with status and duration.", RunListArgs, "read", "GET /api/runs",
       t_runs, True),
    _t("get_workflow_run", "One workflow run with its stages and forecasts.", RunArgs, "read", "GET /api/runs/{id}",
       t_run, True),
    _t("get_simulation_status", "Whether the simulation is running, its scenario, speed and minute (all data is simulated).",
       SimStatusArgs, "read", "GET /api/simulation", t_simulation, True),
    _t("get_model_provider_status", "Which model provider/model the app uses and whether a credential is configured (never the key).",
       NoArgs, "ops:read", "GET /api/settings (model_integration)", t_provider_status, False),
    _t("get_model_call_history", "Recent model calls: operation, latency, tokens, status, estimated cost.", LimitArgs,
       "ops:read", "GET /api/llm/calls", t_llm_calls, False, "Prompts are never stored, so none are shown."),
    _t("get_model_usage_summary", "Aggregate model usage, error rate, latency percentiles and estimated cost vs budget.",
       NoArgs, "ops:read", "GET /api/llm/summary", t_llm_summary, False,
       "Cost is an estimate; SAM image spend is tracked separately."),
    _t("get_system_health", "Database/API health flag.", NoArgs, "read", "GET /api/health", t_health, False),
    _t("get_system_metrics", "API latency/error counts, workflow throughput, camera failures, model API errors.", NoArgs,
       "ops:read", "GET /api/system/metrics", t_system_metrics, False),
    _t("get_settings", "Read-only operating settings (data age limit, rules, budget). Cannot change them.", NoArgs,
       "ops:read", "GET /api/settings", t_settings, False, "Read only."),
    _t("get_audit_log", "Recent audit log entries (who did what).", LimitArgs, "audit:read", "GET /api/audit",
       t_audit, False),
]
REGISTRY: Dict[str, Tool] = {t.name: t for t in TOOLS}
# Tools whose numbers come from the simulator / simulated detector in this build.
SIMULATED_TOOLS = frozenset({"get_pandal", "get_measurements", "get_forecast_and_risk", "get_overview",
                             "get_simulation_status", "get_camera_wall", "get_vision_counts"})


def tools_for(user: User) -> List[Tool]:
    return [t for t in TOOLS if has_permission(user, t.permission)]


def specs_for(user: User, extra: Optional[List[Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
    return [t.spec() for t in tools_for(user)] + list(extra or [])


def run_tool(name: str, raw_args: str, env: ToolEnv) -> Dict[str, Any]:
    """Execute one tool call. Never raises: returns an `ok` envelope the model can read as data."""
    started = time.time()
    tool = REGISTRY.get(name)
    if tool is None:
        return {"ok": False, "error": {"code": "unknown_tool", "message": f"There is no tool named '{name}'."}}
    if not has_permission(env.user, tool.permission):
        return {"ok": False, "error": {"code": "forbidden",
                                       "message": f"Your role is not allowed to use '{name}'."}}
    try:
        parsed = json.loads(raw_args) if raw_args.strip() else {}
        if not isinstance(parsed, dict):
            raise ValueError("arguments must be a JSON object")
        args = tool.args.model_validate(parsed)
    except (ValueError, TypeError) as exc:   # pydantic.ValidationError is a ValueError
        detail = str(exc).splitlines()[0][:200] if str(exc) else "invalid arguments"
        return {"ok": False, "error": {"code": "invalid_arguments", "message": detail}}
    try:
        data = tool.handler(env, args)
    except ToolError as exc:
        return {"ok": False, "error": {"code": exc.code, "message": exc.message}}
    except Exception:  # noqa: BLE001 - never leak internals to the model or user
        return {"ok": False, "error": {"code": "tool_failed", "message": "The data could not be read."}}
    env_out: Dict[str, Any] = {"ok": True, "tool": name, "source": tool.source, "as_of": time.time(),
                               "result": data}
    if name in SIMULATED_TOOLS:
        env_out["data_source"] = "simulated"
    if tool.limitations:
        env_out["limitations"] = tool.limitations
    text = json.dumps(env_out, default=str)
    if len(text) > MAX_TOOL_OUTPUT_CHARS:
        env_out = {"ok": True, "tool": name, "source": tool.source, "truncated": True,
                   "message": "Result was too large and was cut. Narrow the request (pandal_id, status, limit).",
                   "preview": text[:MAX_TOOL_OUTPUT_CHARS // 2]}
    env_out["duration_ms"] = round((time.time() - started) * 1000, 1)
    return env_out
