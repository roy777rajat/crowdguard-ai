"""Monitoring service: runs the simulation, drives the ingestion workflow and builds pandal state.

All measurements here are SIMULATED (source='simulated'). Authoritative occupancy/forecast values
come from backend.engine only; the model provider can explain them but never changes them.
"""
from __future__ import annotations

import threading
import time
from typing import Any, Dict, List, Optional

from backend.engine import forecast as eng
from backend.observability.llm import LLMProvider, budget_exceeded, traced_call
from backend.observability.prompts import EXPLAIN_INSTRUCTIONS, EXPLAIN_VERSION
from backend.repositories.store import Repos
from backend.services.incidents import DEFAULT_RULES, IncidentService, candidate_alarms
from backend.simulator.scenarios import SCENARIOS, Simulator
from backend.workflows.runner import RunContext, StageError, start_run

PROMPT_VERSION = EXPLAIN_VERSION


class MonitorService:
    def __init__(self, repos: Repos, incidents: IncidentService, provider: LLMProvider):
        self.repos = repos
        self.incidents = incidents
        self.provider = provider
        self.sims: Dict[int, Simulator] = {}
        self.scenario = "normal_flow"
        self.scenarios: Dict[int, str] = {}   # scenario chosen for each pandal
        self.speed = 1.0
        self.active: set = set()               # pandals whose simulation is running (each runs on its own)
        self.paused: set = set()               # pandals that were started and are currently paused
        self.minutes: Dict[int, int] = {}
        self._controlled = False               # becomes True once anyone starts/pauses/resets a simulation
        self.minute = 0
        self.lock = threading.RLock()

    @property
    def running(self) -> bool:
        return bool(self.active)

    @running.setter
    def running(self, value: bool) -> None:
        self._controlled = True
        if value:
            self.active = set(self.paused) | self.active or {p["id"] for p in self.repos.pandals.list({"active": 1})}
            self.paused.clear()
        else:
            self.paused |= self.active
            self.active = set()

    # ---- settings -------------------------------------------------------------------------
    def max_age(self) -> float:
        return float(self.repos.get_setting("max_data_age_s", 180))

    def rules(self) -> Dict[str, bool]:
        return {**DEFAULT_RULES, **(self.repos.get_setting("rules_enabled", {}) or {})}

    def lead_min(self) -> float:
        return float(self.repos.get_setting("predicted_critical_lead_min", 5))

    # ---- control --------------------------------------------------------------------------
    def status(self, pandal_id: Optional[int] = None) -> Dict[str, Any]:
        """With pandal_id the answer is about that pandal only; without it, about the whole simulator."""
        if pandal_id is not None:
            return {"running": pandal_id in self.active, "scenario": self.scenarios.get(pandal_id, self.scenario),
                    "speed": self.speed, "minute": self.minutes.get(pandal_id, 0), "scenarios": list(SCENARIOS),
                    "source": "simulated", "pandal_id": pandal_id, "started": pandal_id in self.minutes}
        return {"running": self.running, "scenario": self.scenario, "speed": self.speed,
                "minute": self.minute, "scenarios": list(SCENARIOS), "source": "simulated"}

    def _targets(self, pandal_id: Optional[int]) -> List[int]:
        if pandal_id is not None:
            return [pandal_id]
        return [p["id"] for p in self.repos.pandals.list({"active": 1})]

    def _clear_data(self, pandal_id: Optional[int] = None) -> None:
        if pandal_id is None:
            self.sims.clear()
            self.minutes.clear()
            self.minute = 0
            self.repos.db.execute("DELETE FROM measurements")
            self.repos.db.execute("DELETE FROM forecasts")
            self.repos.db.execute("UPDATE cameras SET status='registered', last_seen=NULL, last_error=NULL")
            return
        self.sims.pop(pandal_id, None)
        self.minutes.pop(pandal_id, None)
        self.repos.db.execute("DELETE FROM measurements WHERE pandal_id=?", [pandal_id])
        self.repos.db.execute("DELETE FROM forecasts WHERE pandal_id=?", [pandal_id])
        self.repos.db.execute("UPDATE cameras SET status='registered', last_seen=NULL, last_error=NULL "
                              "WHERE pandal_id=?", [pandal_id])

    def start(self, scenario: str, speed: float, pandal_id: Optional[int] = None) -> Dict[str, Any]:
        if scenario not in SCENARIOS:
            raise ValueError("unknown scenario")
        with self.lock:
            self._controlled = True
            self._clear_data(pandal_id)
            self.scenario, self.speed = scenario, speed
            for pid in self._targets(pandal_id):
                self.scenarios[pid] = scenario
                self.minutes[pid] = 0
                self.active.add(pid)
                self.paused.discard(pid)
        return self.status(pandal_id)

    def pause(self, pandal_id: Optional[int] = None) -> Dict[str, Any]:
        with self.lock:
            self._controlled = True
            ids = set(self._targets(pandal_id)) & self.active
            self.active -= ids
            self.paused |= ids
        return self.status(pandal_id)

    def resume(self, pandal_id: Optional[int] = None) -> Dict[str, Any]:
        with self.lock:
            ids = set(self._targets(pandal_id)) & (self.paused | set(self.minutes))
            if pandal_id is None and not ids:
                ids = set(self._targets(None))
            self.active |= ids
            self.paused -= ids
        return self.status(pandal_id)

    def reset(self, pandal_id: Optional[int] = None) -> Dict[str, Any]:
        with self.lock:
            self._controlled = True
            ids = set(self._targets(pandal_id))
            self.active -= ids
            self.paused -= ids
            self._clear_data(pandal_id)
        return self.status(pandal_id)

    # ---- state ----------------------------------------------------------------------------
    def _exit_capacity(self, p: Dict[str, Any]) -> float:
        zones = self.repos.zones.list({"pandal_id": p["id"], "active": 1})
        total = sum(z["exit_capacity_per_min"] for z in zones)
        return float(total) if total > 0 else p["capacity"] * 0.06

    def compute_state(self, p: Dict[str, Any]) -> Dict[str, Any]:
        rows = self.repos.measurements.recent(p["id"], 3)
        cap = p["capacity"]
        warn, crit = cap * p["warn_pct"], cap * p["critical_pct"]
        base: Dict[str, Any] = {"pandal_id": p["id"], "capacity": cap, "warn_threshold": round(warn, 1),
                                "critical_threshold": round(crit, 1), "source": "simulated"}
        if not rows:
            return {**base, "has_data": False, "stale": False, "status": "no_data", "age_s": None,
                    "quality": eng.data_quality(None, self.max_age(), 0)}
        latest = rows[0]
        sim = self.sims.get(p["id"])
        if sim is not None and latest["sim_minute"] is not None:
            age_s = float((sim.minute - latest["sim_minute"]) * 60)
        else:
            age_s = max(0.0, time.time() - latest["wall_ts"])
        max_age = self.max_age()
        stale = eng.is_stale(0.0, age_s, max_age)
        entry = eng.rolling_rate([(60.0, r["entry_rate"]) for r in rows])
        exit_ = eng.rolling_rate([(60.0, r["exit_rate"]) for r in rows])
        occ = latest["occupancy"]
        bottleneck = None
        if latest.get("exit_capacity"):
            bottleneck = eng.exit_bottleneck(latest["exit_demand"] or 0.0, latest["exit_capacity"],
                                             latest["exit_rate"])
        risk = eng.risk_level(occ, cap, p["warn_pct"], p["critical_pct"])
        return {**base, "has_data": True, "status": "stale" if stale else "live", "stale": stale,
                "age_s": age_s, "occupancy": round(occ, 2), "occupancy_pct": round(100 * occ / cap, 1),
                "entry_rate": round(entry, 2), "exit_rate": round(exit_, 2),
                "forecast": eng.forecast_all(occ, entry, exit_), "risk": risk,
                "minutes_to_warning": eng.time_to_threshold(occ, entry, exit_, warn),
                "minutes_to_critical": eng.time_to_threshold(occ, entry, exit_, crit),
                "quality": eng.data_quality(age_s, max_age, len(rows)), "bottleneck": bottleneck,
                "last_measurement_wall_ts": latest["wall_ts"], "sim_minute": latest["sim_minute"]}

    # ---- tick / workflow ------------------------------------------------------------------
    def tick(self) -> List[str]:
        with self.lock:
            self.minute += 1
            runs = []
            # A manual tick before anything was started (tests, scripts) advances every pandal, as before.
            manual = not self._controlled
            for p in self.repos.pandals.list({"active": 1}):
                if manual or p["id"] in self.active:
                    self.minutes[p["id"]] = self.minutes.get(p["id"], 0) + 1
                    runs.append(self._tick_pandal(p))
            return runs

    def _tick_pandal(self, p: Dict[str, Any]) -> str:
        sim = self.sims.get(p["id"])
        if sim is None:
            sim = Simulator(self.scenarios.get(p["id"], self.scenario), seed=1000 + p["id"], capacity=p["capacity"],
                            exit_capacity=self._exit_capacity(p))
            self.sims[p["id"]] = sim
        obs = sim.step()
        cams = self.repos.cameras.list({"pandal_id": p["id"], "active": 1})
        cam = cams[0] if cams else None
        ctx = start_run(self.repos, p["id"], cam["id"] if cam else None, source_ts=time.time())
        try:
            raw = ctx.execute("receive", lambda: self._receive(obs))
            ctx.execute("validate", lambda: self._validate(raw))
            m = ctx.execute("persist", lambda: self._persist(p, cam, raw, ctx), retries=1)
            ctx.outputs["measurement_id"] = m["id"]
            state = ctx.execute("forecast", lambda: self._forecast(p, ctx))
            created = ctx.execute("evaluate", lambda: self._evaluate(p, state, ctx))
            ctx.execute("explain", lambda: self._explain(p, state, created, ctx), critical=False)
        except Exception:  # noqa: BLE001 - already recorded on the run
            if ctx.error_code == "NO_DATA":
                self._handle_missing(p, cam, ctx)
        finally:
            ctx.finish()
        return ctx.run_id

    @staticmethod
    def _receive(obs: Dict[str, Any]) -> Dict[str, Any]:
        if not obs["emit"]:
            raise StageError("NO_DATA", "No measurement received from sensor in this interval")
        return obs

    @staticmethod
    def _validate(raw: Dict[str, Any]) -> None:
        try:
            eng.forecast_occupancy(raw["occupancy"], raw["entry_rate"], raw["exit_rate"], 1)
        except ValueError as exc:
            raise StageError("INVALID_INPUT", str(exc))

    def _persist(self, p: Dict[str, Any], cam: Optional[Dict[str, Any]], raw: Dict[str, Any],
                 ctx: RunContext) -> Dict[str, Any]:
        now = time.time()
        m = self.repos.measurements.create(
            pandal_id=p["id"], camera_id=cam["id"] if cam else None, sim_minute=raw["minute"], wall_ts=now,
            occupancy=raw["occupancy"], entry_rate=raw["entry_rate"], exit_rate=raw["exit_rate"],
            exit_demand=raw["exit_demand"], exit_capacity=raw["exit_capacity"], source="simulated",
            run_id=ctx.run_id)
        if cam:
            self.repos.cameras.update(cam["id"], status="receiving_simulated", last_seen=now, last_error=None)
        return m

    def _forecast(self, p: Dict[str, Any], ctx: RunContext) -> Dict[str, Any]:
        state = self.compute_state(p)
        now = time.time()
        for h, v in state["forecast"].items():
            self.repos.forecasts.create(run_id=ctx.run_id, pandal_id=p["id"], wall_ts=now, horizon_min=h, value=v)
        ctx.outputs["forecast_horizons"] = list(state["forecast"].keys())
        return state

    def _evaluate(self, p: Dict[str, Any], state: Dict[str, Any], ctx: RunContext) -> List[Dict[str, Any]]:
        created: List[Dict[str, Any]] = []
        for alarm in candidate_alarms(p, state, self.rules(), self.lead_min()):
            inc, is_new = self.incidents.raise_alarm(p["id"], alarm, ctx.run_id)
            if inc["id"] not in ctx.incident_ids:
                ctx.incident_ids.append(inc["id"])
            if is_new:
                created.append(inc)
        return created

    def _explain(self, p: Dict[str, Any], state: Dict[str, Any], created: List[Dict[str, Any]],
                 ctx: RunContext) -> Optional[int]:
        if not created or not state.get("has_data"):
            return None
        res = self.explain_state(p, state, created[0]["rule"], ctx.run_id, ctx.trace_id)
        call = res["call"]
        ctx.outputs["llm_call_id"] = call["id"]
        if call["status"] != "success":
            raise StageError("LLM_ERROR", f"model call failed: {call['error_category']}")
        self.repos.events.create(incident_id=created[0]["id"], ts=time.time(),
                                 actor=f"{call['provider']}:{call['model']}", action="ai_explanation",
                                 from_status="open", to_status="open", note=res["text"])
        return call["id"]

    def explain_state(self, p: Dict[str, Any], state: Dict[str, Any], rule: str,
                      run_id: Optional[str] = None, trace_id: Optional[str] = None) -> Dict[str, Any]:
        if self.provider.name != "local-template" and budget_exceeded(self.repos):
            raise StageError("BUDGET_EXCEEDED", "Model budget limit reached; no further model calls are made")
        q = state["quality"]
        facts = {"pandal": p["name"], "occupancy": state["occupancy"], "capacity": state["capacity"],
                 "occupancy_pct": state["occupancy_pct"], "risk": state["risk"],
                 "forecast_5": state["forecast"][5], "forecast_10": state["forecast"][10],
                 "confidence": q["confidence"], "confidence_label": q["label"], "rule": rule,
                 "horizons": [5, 10]}
        prompt = EXPLAIN_INSTRUCTIONS
        return traced_call(self.repos, self.provider, "explain_incident", PROMPT_VERSION, prompt, facts,
                           run_id=run_id, trace_id=trace_id)

    def _handle_missing(self, p: Dict[str, Any], cam: Optional[Dict[str, Any]], ctx: RunContext) -> None:
        try:
            state = self.compute_state(p)
            if cam:
                self.repos.cameras.update(cam["id"], status="stale" if state.get("stale") else "degraded",
                                          last_error="No measurement received")
            for alarm in candidate_alarms(p, state, self.rules(), self.lead_min()):
                inc, _ = self.incidents.raise_alarm(p["id"], alarm, ctx.run_id)
                if inc["id"] not in ctx.incident_ids:
                    ctx.incident_ids.append(inc["id"])
        except Exception:  # noqa: BLE001
            pass
