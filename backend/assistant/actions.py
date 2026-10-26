"""State-changing capabilities, kept apart from the read-only tools.

The model can only PROPOSE an action. Proposing stores a pending action bound to the signed-in user and
returns an id; nothing changes. The user must confirm it in the UI (POST /api/assistant/actions/{id}/confirm),
and the server then re-checks the permission and pandal scope with the confirming user's session and runs the
same service call the normal route uses. Destructive operations, role changes and settings changes are not
available here at all.
"""
from __future__ import annotations

import json
import secrets
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Literal, Optional, Type

from fastapi import HTTPException
from pydantic import Field

from backend.api import admin_routes as admin_api
from backend.api import routes as api
from backend.assistant.tools import Args, ToolEnv, ToolError, _pandal
from backend.schemas.models import (
    CameraIn,
    CameraUpdate,
    LocationPandalIn,
    PandalUpdate,
    SettingsIn,
    ZoneIn,
    ZoneUpdate,
)
from backend.security.auth import can_access_pandal, has_permission
from backend.services.incidents import InvalidTransition

PENDING_TTL_S = 600
MAX_PENDING_PER_USER = 10


class TransitionArgs(Args):
    incident_id: int = Field(ge=1)
    to: Literal["acknowledged", "assigned", "resolved"]
    note: Optional[str] = Field(default=None, max_length=1000)
    assignee: Optional[str] = Field(default=None, max_length=100)


class NoteArgs(Args):
    incident_id: int = Field(ge=1)
    note: str = Field(min_length=1, max_length=1000)


class SimControlArgs(Args):
    pandal_id: int = Field(ge=1, description="Pandal whose simulation to control")


class SimStartArgs(Args):
    pandal_id: int = Field(ge=1)
    scenario: Literal["normal_flow", "arrival_surge", "reduced_exit", "stale_sensor"] = "normal_flow"
    speed: float = Field(default=1.0, gt=0, le=20)


@dataclass
class Action:
    name: str
    description: str
    args: Type[Args]
    permission: str
    source: str
    check: Callable[[ToolEnv, Any], str]      # validates scope/state, returns a human summary
    run: Callable[[ToolEnv, Any], Any]
    destructive: bool = False                 # permanent deletes / clears / resets: wording is stronger

    def spec(self) -> Dict[str, Any]:
        schema = self.args.model_json_schema()
        schema.pop("title", None)
        for prop in schema.get("properties", {}).values():
            prop.pop("title", None)
        schema["additionalProperties"] = False
        return {"type": "function", "name": self.name,
                "description": self.description + " This only PROPOSES the change; the user must confirm it.",
                "parameters": schema}


def _inc(env: ToolEnv, incident_id: int) -> Dict[str, Any]:
    inc = env.ctx.repos.incidents.get(incident_id)
    if inc is None:
        raise ToolError("not_found", f"No incident with id {incident_id}.")
    if not can_access_pandal(env.user, inc["pandal_id"]):
        raise ToolError("forbidden", "You are not authorized for this pandal.")
    return inc


def _check_transition(env: ToolEnv, a: TransitionArgs) -> str:
    inc = _inc(env, a.incident_id)
    if a.to == "assigned" and not a.assignee:
        raise ToolError("invalid_arguments", "An assignee is required to assign an incident.")
    return f"Move incident #{a.incident_id} ({inc['status']}) to '{a.to}'" + (f" with note: {a.note}" if a.note else "")


def _run_transition(env: ToolEnv, a: TransitionArgs) -> Any:
    try:
        return env.ctx.incidents.transition(a.incident_id, a.to, env.user["username"], a.note, a.assignee)
    except InvalidTransition as exc:
        raise ToolError("conflict", str(exc)) from None


def _check_note(env: ToolEnv, a: NoteArgs) -> str:
    _inc(env, a.incident_id)
    return f"Add a note to incident #{a.incident_id}: {a.note}"


def _run_note(env: ToolEnv, a: NoteArgs) -> Any:
    return env.ctx.incidents.add_note(a.incident_id, env.user["username"], a.note)


def _check_pandal(verb: str) -> Callable[[ToolEnv, Any], str]:
    def check(env: ToolEnv, a: Any) -> str:
        p = _pandal(env, a.pandal_id)
        extra = f" with scenario '{a.scenario}' at speed {a.speed}" if hasattr(a, "scenario") else ""
        return f"{verb} the SIMULATION for {p['name']}{extra} (simulated data only)"
    return check


def _audited(env: ToolEnv, action: str, detail: Dict[str, Any]) -> None:
    env.ctx.repos.audit.log(env.user["username"], f"simulation.{action}", "simulation", None, detail)


def _run_pause(env: ToolEnv, a: SimControlArgs) -> Any:
    _audited(env, "pause", {"pandal_id": a.pandal_id, "via": "assistant"})
    return env.ctx.monitor.pause(a.pandal_id)


def _run_resume(env: ToolEnv, a: SimControlArgs) -> Any:
    _audited(env, "resume", {"pandal_id": a.pandal_id, "via": "assistant"})
    return env.ctx.monitor.resume(a.pandal_id)


def _run_start(env: ToolEnv, a: SimStartArgs) -> Any:
    _audited(env, "start", {"scenario": a.scenario, "speed": a.speed, "pandal_id": a.pandal_id, "via": "assistant"})
    return env.ctx.monitor.start(a.scenario, a.speed, a.pandal_id)


# ---------------------------------------------------------------- registry, history and settings writes
# These call the very same route functions the HTTP API uses (same validation, same audit rows). The route
# functions' own scope checks run too; the permission is checked here first because FastAPI's `require` is bypassed.
def _call(env: ToolEnv, fn: Callable[..., Any], *a: Any, **kw: Any) -> Any:
    try:
        return fn(*a, user=env.user, ctx=env.ctx, **kw)
    except HTTPException as exc:
        code = {403: "forbidden", 404: "not_found", 409: "conflict"}.get(exc.status_code, "invalid_arguments")
        raise ToolError(code, str(exc.detail)) from None
    except ValueError as exc:
        raise ToolError("invalid_arguments", (str(exc).splitlines() or ["invalid"])[0][:200]) from None


def _pname(env: ToolEnv, pid: int) -> str:
    p = env.ctx.repos.pandals.get(pid)
    if p is None:
        raise ToolError("not_found", f"No pandal with id {pid}.")
    return str(p["name"])


def _cam(env: ToolEnv, cid: int) -> Dict[str, Any]:
    c = env.ctx.repos.cameras.get(cid)
    if c is None:
        raise ToolError("not_found", f"No camera with id {cid}.")
    return c


class CreatePandalArgs(Args):
    location_id: int = Field(ge=1)
    name: str = Field(min_length=1, max_length=120)
    zone_names: List[str] = Field(min_length=3, max_length=3, description="Exactly three zone names")
    max_people: int = Field(default=40, ge=1, le=5000, description="Per-zone people limit")
    warn_pct: float = Field(default=0.7, gt=0, lt=1.5)
    critical_pct: float = Field(default=0.9, gt=0, le=1.5)


class UpdatePandalArgs(Args):
    pandal_id: int = Field(ge=1)
    name: Optional[str] = Field(default=None, min_length=1, max_length=120)
    capacity: Optional[int] = Field(default=None, gt=0, le=1_000_000)
    warn_pct: Optional[float] = Field(default=None, gt=0, lt=1.5)
    critical_pct: Optional[float] = Field(default=None, gt=0, le=1.5)


class CreateZoneArgs(Args):
    pandal_id: int = Field(ge=1)
    name: str = Field(min_length=1, max_length=120)
    exit_capacity_per_min: float = Field(default=60, gt=0, le=10_000)


class UpdateZoneArgs(Args):
    zone_id: int = Field(ge=1)
    name: Optional[str] = Field(default=None, min_length=1, max_length=80)
    max_people: Optional[int] = Field(default=None, ge=1, le=5000)


class CreateCameraArgs(Args):
    pandal_id: int = Field(ge=1)
    zone_id: Optional[int] = Field(default=None, ge=1)
    name: str = Field(min_length=1, max_length=120)
    source_type: Literal["simulated", "local_video", "live_stream"] = "simulated"
    source_ref: Optional[str] = Field(default=None, max_length=300, description="Video file name or stream URL, no credentials")


class UpdateCameraArgs(Args):
    camera_id: int = Field(ge=1)
    zone_id: Optional[int] = Field(default=None, ge=1)
    name: Optional[str] = Field(default=None, min_length=1, max_length=120)
    source_type: Optional[Literal["simulated", "local_video", "live_stream"]] = None
    source_ref: Optional[str] = Field(default=None, max_length=300)
    mode: Optional[Literal["active", "passive"]] = None


class PandalIdArgs(Args):
    pandal_id: int = Field(ge=1)


class CameraIdArgs(Args):
    camera_id: int = Field(ge=1)


class CameraActiveArgs(Args):
    camera_id: int = Field(ge=1)
    active: bool


class ClearArgs(Args):
    pass


class DeleteRunsArgs(Args):
    run_ids: List[str] = Field(default_factory=list, max_length=100)
    all: bool = Field(default=False, description="Delete every workflow run (optionally only for pandal_id)")
    pandal_id: Optional[int] = Field(default=None, ge=1)


class DeleteIncidentsArgs(Args):
    incident_ids: List[int] = Field(default_factory=list, max_length=100)
    all: bool = Field(default=False, description="Delete every incident (optionally only for pandal_id)")
    pandal_id: Optional[int] = Field(default=None, ge=1)


class SettingsArgs(Args):
    max_data_age_s: Optional[float] = Field(default=None, gt=0, le=3600)
    sim_speed: Optional[float] = Field(default=None, gt=0, le=20)
    llm_budget_usd: Optional[float] = Field(default=None, ge=0)
    predicted_critical_lead_min: Optional[float] = Field(default=None, gt=0, le=60)


def _chk_create_pandal(env: ToolEnv, a: CreatePandalArgs) -> str:
    if env.ctx.repos.locations.get(a.location_id) is None:
        raise ToolError("not_found", f"No location with id {a.location_id}.")
    if env.ctx.repos.pandals.list({"name": a.name.strip()}):
        raise ToolError("conflict", "A pandal with this name already exists.")
    return (f"Create pandal '{a.name.strip()}' in location #{a.location_id} with zones {', '.join(a.zone_names)} "
            f"(limit {a.max_people} people per zone, warn {a.warn_pct:.0%}, critical {a.critical_pct:.0%})")


def _run_create_pandal(env: ToolEnv, a: CreatePandalArgs) -> Any:
    body = LocationPandalIn(**a.model_dump())
    return _call(env, api.create_location_pandal, a.location_id, body)


def _chk_update_pandal(env: ToolEnv, a: UpdatePandalArgs) -> str:
    _pandal(env, a.pandal_id)
    ch = a.model_dump(exclude_none=True, exclude={"pandal_id"})
    if not ch:
        raise ToolError("invalid_arguments", "Nothing to change.")
    return f"Update pandal '{_pname(env, a.pandal_id)}': " + ", ".join(f"{k} = {v}" for k, v in ch.items())


def _run_update_pandal(env: ToolEnv, a: UpdatePandalArgs) -> Any:
    return _call(env, api.update_pandal, a.pandal_id, PandalUpdate(**a.model_dump(exclude={"pandal_id"})))


def _chk_deactivate_pandal(env: ToolEnv, a: PandalIdArgs) -> str:
    _pandal(env, a.pandal_id)
    return f"Deactivate pandal '{_pname(env, a.pandal_id)}' (it is hidden, not deleted, and can be reactivated)"


def _chk_create_zone(env: ToolEnv, a: CreateZoneArgs) -> str:
    _pandal(env, a.pandal_id)
    return f"Add zone '{a.name}' (exit capacity {a.exit_capacity_per_min}/min) to pandal '{_pname(env, a.pandal_id)}'"


def _chk_update_zone(env: ToolEnv, a: UpdateZoneArgs) -> str:
    z = env.ctx.repos.zones.get(a.zone_id)
    if z is None:
        raise ToolError("not_found", f"No zone with id {a.zone_id}.")
    _pandal(env, z["pandal_id"])
    ch = a.model_dump(exclude_none=True, exclude={"zone_id"})
    if not ch:
        raise ToolError("invalid_arguments", "Nothing to change.")
    return f"Update zone '{z['name']}' of '{_pname(env, z['pandal_id'])}': " + ", ".join(f"{k} = {v}" for k, v in ch.items())


def _chk_create_camera(env: ToolEnv, a: CreateCameraArgs) -> str:
    _pandal(env, a.pandal_id)
    return (f"Register camera '{a.name}' ({a.source_type}) for pandal '{_pname(env, a.pandal_id)}'"
            + (f", zone #{a.zone_id}" if a.zone_id else ""))


def _run_create_camera(env: ToolEnv, a: CreateCameraArgs) -> Any:
    try:
        body = CameraIn(**a.model_dump())
    except ValueError as exc:
        raise ToolError("invalid_arguments", (str(exc).splitlines() or ["invalid"])[-1][:200]) from None
    return _call(env, api.create_camera, body)


def _chk_update_camera(env: ToolEnv, a: UpdateCameraArgs) -> str:
    c = _cam(env, a.camera_id)
    _pandal(env, c["pandal_id"])
    ch = a.model_dump(exclude_none=True, exclude={"camera_id"})
    if not ch:
        raise ToolError("invalid_arguments", "Nothing to change.")
    return f"Update camera '{c['name']}': " + ", ".join(f"{k} = {v}" for k, v in ch.items())


def _chk_camera(verb: str, destructive: bool = False) -> Callable[[ToolEnv, Any], str]:
    def check(env: ToolEnv, a: Any) -> str:
        c = _cam(env, a.camera_id)
        _pandal(env, c["pandal_id"])
        tail = " with its recorded counts. This cannot be undone" if destructive else ""
        if hasattr(a, "active"):
            verb2 = "Activate" if a.active else "Deactivate"
            return f"{verb2} camera '{c['name']}' of '{_pname(env, c['pandal_id'])}'"
        return f"{verb} camera '{c['name']}' of '{_pname(env, c['pandal_id'])}'{tail}"
    return check


def _chk_delete_pandal(env: ToolEnv, a: PandalIdArgs) -> str:
    return (f"PERMANENTLY DELETE pandal '{_pname(env, a.pandal_id)}' with all its zones, cameras, counts, "
            "workflow runs and incidents. This cannot be undone")


def _chk_clear_registry(env: ToolEnv, a: ClearArgs) -> str:
    n = len(env.ctx.repos.pandals.list())
    return (f"PERMANENTLY DELETE ALL {n} pandals with every zone, camera, count, workflow run and incident "
            "(locations stay). This cannot be undone")


def _chk_delete_runs(env: ToolEnv, a: DeleteRunsArgs) -> str:
    if not a.all and not a.run_ids:
        raise ToolError("invalid_arguments", "Give run_ids, or set all=true.")
    if a.pandal_id is not None:
        _pname(env, a.pandal_id)
    if a.all:
        scope = f"for pandal '{_pname(env, a.pandal_id)}'" if a.pandal_id else "for ALL pandals"
        return f"PERMANENTLY DELETE ALL workflow runs {scope}. This cannot be undone"
    return f"PERMANENTLY DELETE {len(a.run_ids)} workflow run(s). This cannot be undone"


def _chk_delete_incidents(env: ToolEnv, a: DeleteIncidentsArgs) -> str:
    if not a.all and not a.incident_ids:
        raise ToolError("invalid_arguments", "Give incident_ids, or set all=true.")
    if a.pandal_id is not None:
        _pname(env, a.pandal_id)
    if a.all:
        scope = f"for pandal '{_pname(env, a.pandal_id)}'" if a.pandal_id else "for ALL pandals"
        return f"PERMANENTLY DELETE ALL incidents {scope} with their history. This cannot be undone"
    return f"PERMANENTLY DELETE {len(a.incident_ids)} incident(s) with their history. This cannot be undone"


def _chk_clear_llm(env: ToolEnv, a: ClearArgs) -> str:
    n = len(env.ctx.repos.llm_calls.list())
    return f"PERMANENTLY DELETE the model-call history ({n} records). Your provider's own billing is unaffected"


def _chk_clear_system(env: ToolEnv, a: ClearArgs) -> str:
    return "Reset system-health figures (API request buffer and camera error flags)"


def _chk_settings(env: ToolEnv, a: SettingsArgs) -> str:
    ch = a.model_dump(exclude_none=True)
    if not ch:
        raise ToolError("invalid_arguments", "Nothing to change.")
    return "Change settings: " + ", ".join(f"{k} = {v}" for k, v in ch.items())


def _chk_sim_reset(env: ToolEnv, a: PandalIdArgs) -> str:
    return f"RESET the simulation of pandal '{_pname(env, _pandal(env, a.pandal_id)['id'])}' (its simulated data is cleared)"


def _run_sim_reset(env: ToolEnv, a: PandalIdArgs) -> Any:
    _audited(env, "reset", {"pandal_id": a.pandal_id, "via": "assistant"})
    return env.ctx.monitor.reset(a.pandal_id)


def _run_delete_runs(env: ToolEnv, a: DeleteRunsArgs) -> Any:
    from backend.api.admin_routes import DeleteSelection
    return _call(env, admin_api.delete_runs, DeleteSelection(ids=a.run_ids, all=a.all, pandal_id=a.pandal_id))


def _run_delete_incidents(env: ToolEnv, a: DeleteIncidentsArgs) -> Any:
    from backend.api.admin_routes import DeleteSelection
    return _call(env, admin_api.delete_incidents,
                 DeleteSelection(ids=[str(i) for i in a.incident_ids], all=a.all, pandal_id=a.pandal_id))


def _run_settings(env: ToolEnv, a: SettingsArgs) -> Any:
    return _call(env, api.put_settings, SettingsIn(**a.model_dump()))


def _act(name: str, desc: str, args: Type[Args], perm: str, source: str, check: Callable[[ToolEnv, Any], str],
         run: Callable[[ToolEnv, Any], Any], destructive: bool = False) -> Action:
    return Action(name, desc, args, perm, source, check, run, destructive)


_WRITE_ACTIONS = [
    _act("propose_create_pandal", "Propose creating a pandal (association) with exactly three zones in a location.",
         CreatePandalArgs, "registry:write", "POST /api/locations/{id}/pandals", _chk_create_pandal, _run_create_pandal),
    _act("propose_update_pandal", "Propose changing a pandal's name, capacity or warn/critical percentages.",
         UpdatePandalArgs, "registry:write", "PUT /api/pandals/{id}", _chk_update_pandal, _run_update_pandal),
    _act("propose_deactivate_pandal", "Propose deactivating (hiding) a pandal. Reversible.", PandalIdArgs,
         "registry:write", "DELETE /api/pandals/{id}", _chk_deactivate_pandal,
         lambda env, a: _call(env, api.deactivate_pandal, a.pandal_id)),
    _act("propose_create_zone", "Propose adding a zone to a pandal.", CreateZoneArgs, "registry:write",
         "POST /api/pandals/{id}/zones", _chk_create_zone,
         lambda env, a: _call(env, api.create_zone, a.pandal_id, ZoneIn(name=a.name, exit_capacity_per_min=a.exit_capacity_per_min))),
    _act("propose_update_zone", "Propose renaming a zone or changing its people limit.", UpdateZoneArgs,
         "registry:write", "PUT /api/zones/{id}", _chk_update_zone,
         lambda env, a: _call(env, api.update_zone, a.zone_id, ZoneUpdate(**a.model_dump(exclude={"zone_id"})))),
    _act("propose_create_camera", "Propose registering a camera for a pandal/zone.", CreateCameraArgs,
         "registry:write", "POST /api/cameras", _chk_create_camera, _run_create_camera),
    _act("propose_update_camera", "Propose changing a camera's name, zone, source or active/passive mode.",
         UpdateCameraArgs, "registry:write", "PUT /api/cameras/{id}", _chk_update_camera,
         lambda env, a: _call(env, api.update_camera, a.camera_id, CameraUpdate(**a.model_dump(exclude={"camera_id"})))),
    _act("propose_deactivate_camera", "Propose deactivating a camera. Reversible.", CameraIdArgs, "registry:write",
         "DELETE /api/cameras/{id}", _chk_camera("Deactivate"),
         lambda env, a: _call(env, api.deactivate_camera, a.camera_id)),
    _act("propose_set_camera_active", "Propose activating or deactivating a camera.", CameraActiveArgs,
         "registry:write", "POST /api/cameras/{id}/active", _chk_camera("Set"),
         lambda env, a: _call(env, admin_api.set_camera_active, a.camera_id, admin_api.ActiveIn(active=a.active))),
    _act("propose_delete_pandal_permanently", "Propose PERMANENTLY deleting a pandal and everything under it.",
         PandalIdArgs, "registry:write", "DELETE /api/pandals/{id}/permanent", _chk_delete_pandal,
         lambda env, a: _call(env, admin_api.delete_pandal, a.pandal_id), True),
    _act("propose_delete_camera_permanently", "Propose PERMANENTLY deleting a camera and its counts.", CameraIdArgs,
         "registry:write", "DELETE /api/cameras/{id}/permanent", _chk_camera("PERMANENTLY DELETE", True),
         lambda env, a: _call(env, admin_api.delete_camera, a.camera_id), True),
    _act("propose_clear_registry", "Propose PERMANENTLY deleting ALL pandals, zones and cameras.", ClearArgs,
         "registry:write", "POST /api/registry/clear", _chk_clear_registry,
         lambda env, a: _call(env, admin_api.clear_registry, admin_api.ConfirmIn(confirm="DELETE")), True),
    _act("propose_delete_workflow_runs", "Propose PERMANENTLY deleting some or all workflow runs.", DeleteRunsArgs,
         "settings:write", "POST /api/runs/delete", _chk_delete_runs, _run_delete_runs, True),
    _act("propose_delete_incidents", "Propose PERMANENTLY deleting some or all incidents.", DeleteIncidentsArgs,
         "settings:write", "POST /api/incidents/delete", _chk_delete_incidents, _run_delete_incidents, True),
    _act("propose_clear_model_call_history", "Propose PERMANENTLY deleting the model-call history.", ClearArgs,
         "settings:write", "POST /api/llm/clear", _chk_clear_llm,
         lambda env, a: _call(env, admin_api.clear_llm_calls, admin_api.ConfirmIn(confirm="DELETE")), True),
    _act("propose_clear_system_health", "Propose resetting system-health figures and camera error flags.", ClearArgs,
         "settings:write", "POST /api/system/clear", _chk_clear_system,
         lambda env, a: _call(env, admin_api.clear_system_health)),
    _act("propose_update_settings", "Propose changing operating settings (data age limit, speed, budget, lead time).",
         SettingsArgs, "settings:write", "PUT /api/settings", _chk_settings, _run_settings),
    _act("propose_simulation_reset", "Propose resetting the simulation of one pandal (clears its simulated data).",
         PandalIdArgs, "sim:control", "POST /api/simulation/reset", _chk_sim_reset, _run_sim_reset, True),
]

ACTIONS: Dict[str, Action] = {a.name: a for a in [
    Action("propose_incident_transition", "Propose acknowledging, assigning or resolving an incident.",
           TransitionArgs, "incident:write", "POST /api/incidents/{id}/transition", _check_transition, _run_transition),
    Action("propose_incident_note", "Propose adding a note to an incident.", NoteArgs, "incident:write",
           "POST /api/incidents/{id}/notes", _check_note, _run_note),
    Action("propose_simulation_pause", "Propose pausing the simulation of one pandal.", SimControlArgs, "sim:control",
           "POST /api/simulation/pause", _check_pandal("Pause"), _run_pause),
    Action("propose_simulation_resume", "Propose resuming the simulation of one pandal.", SimControlArgs,
           "sim:control", "POST /api/simulation/resume", _check_pandal("Resume"), _run_resume),
    Action("propose_simulation_start", "Propose starting the simulation of one pandal with a scenario.",
           SimStartArgs, "sim:control", "POST /api/simulation/start", _check_pandal("Start"), _run_start),
    *_WRITE_ACTIONS,
]}


def actions_for(user: Dict[str, Any]) -> List[Action]:
    return [a for a in ACTIONS.values() if has_permission(user, a.permission)]


@dataclass
class Pending:
    id: str
    user_id: int
    action: str
    args: Dict[str, Any]
    summary: str
    destructive: bool = False
    created: float = field(default_factory=time.time)


class PendingStore:
    """In-memory, per-process, per-user. Pending actions are lost on restart, which is the safe direction."""

    def __init__(self) -> None:
        self._items: Dict[str, Pending] = {}
        self._lock = threading.Lock()

    def _gc(self) -> None:
        now = time.time()
        for k in [k for k, v in self._items.items() if now - v.created > PENDING_TTL_S]:
            del self._items[k]

    def add(self, env: ToolEnv, action: Action, args: Any, summary: str) -> Pending:
        with self._lock:
            self._gc()
            mine = [p for p in self._items.values() if p.user_id == env.user["id"]]
            if len(mine) >= MAX_PENDING_PER_USER:
                raise ToolError("limit", "Too many unconfirmed actions. Confirm or cancel some first.")
            p = Pending(secrets.token_urlsafe(12), env.user["id"], action.name, args.model_dump(), summary,
                        action.destructive)
            self._items[p.id] = p
            return p

    def take(self, action_id: str, user_id: int) -> Optional[Pending]:
        """Remove and return the pending action only if it belongs to this user and has not expired."""
        with self._lock:
            self._gc()
            p = self._items.get(action_id)
            if p is None or p.user_id != user_id:
                return None
            del self._items[action_id]
            return p

    def latest_for(self, user_id: int) -> Optional[Pending]:
        with self._lock:
            self._gc()
            mine = [p for p in self._items.values() if p.user_id == user_id]
            return max(mine, key=lambda p: p.created) if mine else None

    def drop(self, action_id: str) -> None:
        with self._lock:
            self._items.pop(action_id, None)

    def pending_for(self, user_id: int) -> List[Pending]:
        with self._lock:
            self._gc()
            return [p for p in self._items.values() if p.user_id == user_id]


def propose(name: str, raw_args: str, env: ToolEnv, store: PendingStore) -> Dict[str, Any]:
    """Handle a model request for an action: validate, check authorization, store as pending. Never executes."""
    act = ACTIONS.get(name)
    if act is None or not has_permission(env.user, act.permission):
        return {"ok": False, "error": {"code": "unknown_tool" if act is None else "forbidden",
                                       "message": "That action is not available to you."}}
    try:
        parsed = json.loads(raw_args) if raw_args.strip() else {}
        if not isinstance(parsed, dict):
            raise ValueError("arguments must be a JSON object")
        args = act.args.model_validate(parsed)
        summary = act.check(env, args)
        pend = store.add(env, act, args, summary)
    except ToolError as exc:
        return {"ok": False, "error": {"code": exc.code, "message": exc.message}}
    except ValueError as exc:
        return {"ok": False, "error": {"code": "invalid_arguments", "message": (str(exc).splitlines() or [""])[0][:200]}}
    return {"ok": True, "status": "awaiting_user_confirmation", "action_id": pend.id, "summary": summary,
            "destructive": act.destructive,
            "message": "Nothing has changed yet. Tell the user exactly what will happen and ask them to reply "
                       "Yes, No or Cancel."}


def execute(pend: Pending, env: ToolEnv) -> Dict[str, Any]:
    """Run a confirmed action with the confirming user's session, re-checking permission and scope first."""
    act = ACTIONS[pend.action]
    if not has_permission(env.user, act.permission):
        raise ToolError("forbidden", "Your role is no longer allowed to do this.")
    args = act.args.model_validate(pend.args)
    act.check(env, args)   # re-validates pandal scope / incident existence at execution time
    result = act.run(env, args)
    env.ctx.repos.audit.log(env.user["username"], "assistant.action_confirmed", "assistant", None,
                            {"action": pend.action, "args": pend.args})
    return {"summary": pend.summary, "result": result}
