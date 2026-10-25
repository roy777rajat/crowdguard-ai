"""Alarm rules, incident lifecycle, deduplication and notification interface."""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional, Protocol, Tuple

from backend.repositories.store import Repos

log = logging.getLogger("crowdguard.incidents")

TRANSITIONS: Dict[str, set] = {
    "open": {"acknowledged", "assigned", "resolved"},
    "acknowledged": {"assigned", "resolved"},
    "assigned": {"resolved"},
    "resolved": set(),
}
SEVERITY_RANK = {"info": 0, "warning": 1, "critical": 2}
DEFAULT_RULES = {"occupancy_warning": True, "occupancy_critical": True, "predicted_critical": True,
                 "exit_bottleneck": True, "stale_data": True}


class InvalidTransition(Exception):
    pass


class Notifier(Protocol):
    def notify(self, incident: Dict[str, Any]) -> None: ...


class NullNotifier:
    """Local MVP: records intent in the log only. No email/SMS/webhook is ever sent."""

    def notify(self, incident: Dict[str, Any]) -> None:
        log.info("notification suppressed (local MVP): incident=%s severity=%s", incident["id"], incident["severity"])


def candidate_alarms(pandal: Dict[str, Any], state: Dict[str, Any], rules: Dict[str, bool],
                     lead_min: float) -> List[Dict[str, Any]]:
    """Pure rule evaluation. Stale data suppresses occupancy rules: a stale number is not evidence."""
    out: List[Dict[str, Any]] = []
    pid, name = pandal["id"], pandal["name"]

    def add(rule: str, severity: str, title: str, detail: Dict[str, Any]) -> None:
        if rules.get(rule, True):
            out.append({"rule": rule, "severity": severity, "title": title, "detail": detail,
                        "dedupe_key": f"{pid}:{rule}"})

    if state.get("stale"):
        add("stale_data", "warning", f"{name}: monitoring data is stale",
            {"age_s": state.get("age_s"), "simulated": True})
        return out
    if not state.get("has_data"):
        return out
    base = {"occupancy": state["occupancy"], "capacity": state["capacity"],
            "occupancy_pct": state["occupancy_pct"], "simulated": True}
    risk = state["risk"]
    if risk == "critical":
        add("occupancy_critical", "critical", f"{name}: occupancy above critical threshold", base)
    elif risk == "warning":
        add("occupancy_warning", "warning", f"{name}: occupancy above warning threshold", base)
    ttc = state.get("minutes_to_critical")
    if risk != "critical" and ttc is not None and 0 < ttc <= lead_min:
        add("predicted_critical", "warning",
            f"{name}: projected to reach critical threshold in {ttc} min",
            {**base, "minutes_to_critical": ttc, "model": "baseline-constant-rate"})
    bn = state.get("bottleneck") or {}
    if bn.get("flag"):
        pressure = bn.get("pressure")
        add("exit_bottleneck", "critical" if pressure and pressure >= 1.5 else "warning",
            f"{name}: exit bottleneck signal", {**bn, "simulated": True})
    return out


class IncidentService:
    def __init__(self, repos: Repos, notifier: Optional[Notifier] = None):
        self.repos = repos
        self.notifier = notifier or NullNotifier()

    def raise_alarm(self, pandal_id: int, alarm: Dict[str, Any], run_id: Optional[str]) -> Tuple[Dict[str, Any], bool]:
        """Create an incident or suppress a duplicate. Returns (incident, created)."""
        now = time.time()
        existing = self.repos.incidents.open_with_key(alarm["dedupe_key"])
        if existing:
            fields: Dict[str, Any] = {"repeat_count": existing["repeat_count"] + 1, "updated_at": now,
                                      "detail": alarm["detail"]}
            if SEVERITY_RANK[alarm["severity"]] > SEVERITY_RANK[existing["severity"]]:
                fields["severity"] = alarm["severity"]
                self.repos.events.create(incident_id=existing["id"], ts=now, actor="system", action="escalated",
                                         from_status=existing["status"], to_status=existing["status"],
                                         note=f"severity {existing['severity']} -> {alarm['severity']}")
            return self.repos.incidents.update(existing["id"], **fields), False  # type: ignore[return-value]
        inc = self.repos.incidents.create(pandal_id=pandal_id, rule=alarm["rule"], severity=alarm["severity"],
                                          status="open", title=alarm["title"], detail=alarm["detail"],
                                          dedupe_key=alarm["dedupe_key"], repeat_count=1, run_id=run_id,
                                          created_at=now, updated_at=now)
        self.repos.events.create(incident_id=inc["id"], ts=now, actor="system", action="created",
                                 from_status=None, to_status="open", note=alarm["title"])
        self.notifier.notify(inc)
        return inc, True

    def transition(self, incident_id: int, to: str, actor: str, note: Optional[str] = None,
                   assignee: Optional[str] = None) -> Dict[str, Any]:
        inc = self.repos.incidents.get(incident_id)
        if inc is None:
            raise KeyError(incident_id)
        if to not in TRANSITIONS[inc["status"]]:
            raise InvalidTransition(f"cannot move from {inc['status']} to {to}")
        if to == "assigned" and not assignee:
            raise InvalidTransition("assignee is required when assigning")
        now = time.time()
        fields: Dict[str, Any] = {"status": to, "updated_at": now}
        if to == "assigned":
            fields["assignee"] = assignee
        if to == "resolved":
            fields["resolved_at"] = now
        updated = self.repos.incidents.update(incident_id, **fields)
        self.repos.events.create(incident_id=incident_id, ts=now, actor=actor, action=to,
                                 from_status=inc["status"], to_status=to,
                                 note=note or (f"assigned to {assignee}" if assignee else None))
        self.repos.audit.log(actor, f"incident.{to}", "incident", incident_id, {"note": note})
        return updated  # type: ignore[return-value]

    def add_note(self, incident_id: int, actor: str, note: str) -> Dict[str, Any]:
        inc = self.repos.incidents.get(incident_id)
        if inc is None:
            raise KeyError(incident_id)
        ev = self.repos.events.create(incident_id=incident_id, ts=time.time(), actor=actor, action="note",
                                      from_status=inc["status"], to_status=inc["status"], note=note)
        self.repos.audit.log(actor, "incident.note", "incident", incident_id)
        return ev
