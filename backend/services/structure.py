"""Location -> Pandal -> Zone -> Camera structure and the per-pandal camera wall view.

Rules (set by the product owner):
  * Locations are fixed: Kolkata South, North, East, West.
  * A pandal is named after its (unique) association and always has exactly 3 zones with user-entered names.
  * Every zone has exactly one camera, which is "active" (frames are counted) or "passive" (shown, not counted).

Per-zone limits, all user-editable:
  max_people     people that may be in that camera's view
  warn level   = max_people * pandal.warn_pct      -> "% of threshold" = count / warn level
  critical level = max_people * pandal.critical_pct -> "% criticality"  = count / critical level
Counts are people visible in a camera view, produced by the detector. They are not total pandal occupancy.
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional, Sequence, Tuple

from backend.repositories.store import Repos

LOCATIONS = ["Kolkata South", "Kolkata North", "Kolkata East", "Kolkata West"]
ZONES_PER_PANDAL = 3
DEFAULT_MAX_PEOPLE = 40
FRESH_S = 60.0   # a count older than this is shown as stale, never as current
WINDOW_S = 1800  # default history window for the graphs


def ensure_locations(repos: Repos) -> List[Dict[str, Any]]:
    have = {r["name"]: r for r in repos.locations.list()}
    for name in LOCATIONS:
        if name not in have:
            have[name] = repos.locations.create(name=name, active=1)
    return [have[n] for n in LOCATIONS] + [r for n, r in have.items() if n not in LOCATIONS]


def create_pandal(repos: Repos, location_id: int, name: str, zone_names: Sequence[str],
                  max_people: int = DEFAULT_MAX_PEOPLE, warn_pct: float = 0.7,
                  critical_pct: float = 0.9) -> Dict[str, Any]:
    """Create a pandal with its 3 zones and one active camera per zone."""
    if len(zone_names) != ZONES_PER_PANDAL:
        raise ValueError(f"a pandal needs exactly {ZONES_PER_PANDAL} zones")
    if len({z.strip().lower() for z in zone_names}) != len(zone_names):
        raise ValueError("zone names must be different")
    loc = repos.locations.get(location_id)
    if loc is None:
        raise KeyError("location")
    now = time.time()
    p = repos.pandals.create(name=name, location=loc["name"], capacity=max_people * ZONES_PER_PANDAL,
                             warn_pct=warn_pct, critical_pct=critical_pct, active=1, meta={},
                             location_id=location_id, created_at=now, updated_at=now)
    zones, cameras = [], []
    for zn in zone_names:
        z = repos.zones.create(pandal_id=p["id"], name=zn.strip(), exit_capacity_per_min=max_people * 0.075,
                               active=1, max_people=max_people)
        c = repos.cameras.create(pandal_id=p["id"], zone_id=z["id"], name=f"{zn.strip()} camera",
                                 source_type="simulated", status="registered", active=1, mode="active")
        zones.append(z)
        cameras.append(c)
    return {"pandal": p, "zones": zones, "cameras": cameras}


def limits(pandal: Dict[str, Any], zone: Dict[str, Any]) -> Tuple[float, float, int]:
    mp = int(zone.get("max_people") or DEFAULT_MAX_PEOPLE)
    return mp * float(pandal["warn_pct"]), mp * float(pandal["critical_pct"]), mp


def level_from(count: Optional[int], warn: float, crit: float) -> str:
    if count is None:
        return "unknown"
    if count >= crit:
        return "crowded"
    if count >= warn:
        return "busy"
    return "quiet"


def _pct(value: Optional[float], base: float) -> Optional[float]:
    return None if value is None or base <= 0 else round(100.0 * value / base, 1)


def reset_ts(repos: Repos, pandal_id: int) -> float:
    v = repos.get_setting(f"vision_reset:{pandal_id}", 0)
    return float(v) if isinstance(v, (int, float)) else 0.0


def wall_view(repos: Repos, pandal: Dict[str, Any], window_s: float = WINDOW_S,
              now: Optional[float] = None) -> Dict[str, Any]:
    now = time.time() if now is None else now
    since = max(reset_ts(repos, pandal["id"]), now - window_s)
    zones = repos.zones.list({"pandal_id": pandal["id"], "active": 1})
    cams = {c["zone_id"]: c for c in repos.cameras.list({"pandal_id": pandal["id"], "active": 1})}
    rows = repos.vision.recent_for_cameras([c["id"] for c in cams.values()], since)
    by_cam: Dict[int, List[Dict[str, Any]]] = {}
    for r in sorted(rows, key=lambda r: r["id"]):
        by_cam.setdefault(r["camera_id"], []).append(r)

    out_zones: List[Dict[str, Any]] = []
    sum_count = sum_warn = sum_crit = 0.0
    reporting = active = 0
    series_all: Dict[int, List[Tuple[float, int]]] = {}
    for z in zones:
        cam = cams.get(z["id"])
        warn, crit, mp = limits(pandal, z)
        ok = [r for r in by_cam.get(cam["id"], []) if r["status"] == "success" and r["count"] is not None] if cam else []
        latest = ok[-1] if ok else None
        fresh = latest is not None and now - latest["ts"] <= FRESH_S
        count = latest["count"] if fresh and latest else None
        is_active = bool(cam and cam.get("mode", "active") == "active")
        if is_active:
            active += 1
            sum_warn += warn
            sum_crit += crit
            if count is not None:
                reporting += 1
                sum_count += count
        if cam:
            series_all[cam["id"]] = [(r["ts"], r["count"]) for r in ok if is_active]
        out_zones.append({
            "zone": z, "camera": cam,
            "limits": {"max_people": mp, "warn": round(warn, 1), "critical": round(crit, 1)},
            "count": count, "stale": bool(latest) and not fresh,
            "age_s": None if latest is None else round(now - latest["ts"], 1),
            "pct_of_threshold": _pct(count, warn), "pct_criticality": _pct(count, crit),
            "level": level_from(count, warn, crit) if is_active else "passive",
            "series": [{"ts": t, "count": c} for t, c in series_all.get(cam["id"], [])] if cam else [],
            "last_error": None if not cam else cam.get("last_error"),
        })

    # Sum over cameras as a step function: at each sample time, add each camera's latest known count.
    last: Dict[int, int] = {}
    sum_series: List[Dict[str, Any]] = []
    for ts, cid, cnt in sorted((t, cid, c) for cid, pts in series_all.items() for t, c in pts):
        last[cid] = cnt
        sum_series.append({"ts": ts, "count": sum(last.values())})

    totals = {"count": int(sum_count) if reporting else None, "warn": round(sum_warn, 1), "critical": round(sum_crit, 1),
              "pct_of_threshold": _pct(sum_count, sum_warn) if reporting else None,
              "pct_criticality": _pct(sum_count, sum_crit) if reporting else None,
              "level": level_from(int(sum_count), sum_warn, sum_crit) if reporting else "unknown",
              "cameras_active": active, "cameras_reporting": reporting}
    return {"pandal": pandal, "zones": out_zones, "totals": totals, "sum_series": sum_series,
            "window_s": window_s, "server_time": now, "reset_ts": reset_ts(repos, pandal["id"]) or None,
            "basis": "People visible in each camera view, counted by the detector. Not total pandal occupancy."}
