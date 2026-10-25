"""Per-camera workflow: one run per analysed frame.

Stages (each recorded in `stages`, whole run in `runs`):
  capture   check the frame and that the camera is allowed to be counted (active, not deactivated)
  detect    people detector (SAM 3.1 when enabled; the synthetic demo uses its own count and costs nothing)
  persist   store the count (also stored when the detector fails, so spend and errors stay visible)
  evaluate  compare the count with the zone's warning/critical levels and raise or update an incident

A detector failure fails the run with a DETECTOR_* code and no count is shown. Counts are never invented.
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from backend.repositories.store import Repos
from backend.services.incidents import IncidentService
from backend.services.structure import level_from, limits
from backend.vision.detector import DetectionResult, DetectorError, PersonDetector
from backend.workflows.runner import StageError, start_run

RULES = {"zone_warning": True, "zone_critical": True}


class CameraWorkflow:
    def __init__(self, repos: Repos, incidents: IncidentService):
        self.repos = repos
        self.incidents = incidents

    def process(self, cam: Dict[str, Any], jpeg: bytes, detector: PersonDetector,
                demo_truth: Optional[int] = None) -> Dict[str, Any]:
        pandal = self.repos.pandals.get(cam["pandal_id"]) or {}
        zone = self.repos.zones.get(cam["zone_id"]) if cam.get("zone_id") else None
        ctx = start_run(self.repos, cam["pandal_id"], cam["id"], source_ts=time.time())
        res: Optional[DetectionResult] = None
        failure: Optional[str] = None
        attempted = False
        stored = False
        incidents: List[Dict[str, Any]] = []
        started = time.time()

        def store(status: str) -> None:
            nonlocal stored
            now = time.time()
            count = res.count if res else None
            self.repos.vision.create(
                camera_id=cam["id"], pandal_id=cam["pandal_id"], zone_id=cam.get("zone_id"), ts=now, count=count,
                detector=detector.name, is_real_model=int(detector.is_real_model),
                latency_ms=res.latency_ms if res else round((now - started) * 1000, 1),
                status=status, error_category=failure, run_id=ctx.run_id)
            ok = status == "success" and count is not None
            self.repos.cameras.update(cam["id"], status="active" if ok else ("error" if failure else "registered"),
                                      last_seen=now, last_error=failure)
            stored = True
        try:
            ctx.execute("capture", lambda: self._capture(cam, jpeg))
            attempted = True

            def detect() -> DetectionResult:
                try:
                    return detector.detect(jpeg, demo_truth)
                except DetectorError as exc:
                    nonlocal failure
                    failure = exc.category
                    raise StageError(f"DETECTOR_{exc.category.upper()}", f"detector failed: {exc.category}") from None

            res = ctx.execute("detect", detect)
            ctx.execute("persist", lambda: store("success"))
            level = "unknown"
            if res is not None and res.count is not None and zone is not None and cam.get("mode", "active") == "active":
                warn, crit, _ = limits(pandal, zone)
                level = level_from(res.count, warn, crit)
                incidents = ctx.execute("evaluate", lambda: self._evaluate(pandal, zone, cam, res, warn, crit, ctx.run_id)) or []
                for inc in incidents:
                    if inc["id"] not in ctx.incident_ids:
                        ctx.incident_ids.append(inc["id"])
            ctx.outputs.update({"count": res.count if res else None, "level": level,
                                "detector": detector.name, "is_real_model": detector.is_real_model})
        except Exception:  # noqa: BLE001 - already recorded on the run and its stage
            pass
        finally:
            if attempted and not stored:
                store("error")  # a failed call may still be billed, so it is always logged
            ctx.finish()
        return {"run_id": ctx.run_id, "run_status": ctx.status, "res": res, "failure": failure,
                "incident_ids": list(ctx.incident_ids)}

    @staticmethod
    def _capture(cam: Dict[str, Any], jpeg: bytes) -> None:
        if not jpeg:
            raise StageError("EMPTY_FRAME", "no image data")
        if cam.get("mode", "active") != "active":
            raise StageError("CAMERA_PASSIVE", "camera is passive; frames are not counted")

    def _evaluate(self, pandal: Dict[str, Any], zone: Dict[str, Any], cam: Dict[str, Any], res: DetectionResult,
                  warn: float, crit: float, run_id: str) -> List[Dict[str, Any]]:
        if res.count is None:
            return []
        rule = "zone_critical" if res.count >= crit else "zone_warning" if res.count >= warn else None
        if rule is None:
            return []
        label = f"{pandal.get('name', 'Pandal')} / {zone['name']}"
        alarm = {"rule": rule, "severity": "critical" if rule == "zone_critical" else "warning",
                 "title": f"{label}: {res.count} people in view, above {'critical' if rule == 'zone_critical' else 'warning'} level "
                          f"{(crit if rule == 'zone_critical' else warn):.0f}",
                 "detail": {"count": res.count, "warn_level": round(warn, 1), "critical_level": round(crit, 1),
                            "zone_id": zone["id"], "camera_id": cam["id"], "detector": res.detector,
                            "simulated": not res.is_real_model},
                 "dedupe_key": f"{pandal['id']}:zone{zone['id']}:{rule}"}
        inc, _ = self.incidents.raise_alarm(pandal["id"], alarm, run_id)
        return [inc]
