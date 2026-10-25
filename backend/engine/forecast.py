"""Deterministic crowd forecasting engine.

Baseline model (documented assumptions):

    forecast_occupancy(h) = max(0, current_occupancy + (entry_rate - exit_rate) * h)

* Rates are people per minute and are assumed constant over the horizon.
* The model ignores capacity limits of entrances/exits, queueing, and behaviour changes.
* It is a decision-support signal. It is NOT a safety guarantee.
* An LLM must never compute or overwrite any value produced here.
"""
from __future__ import annotations

import math
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

HORIZONS_MIN: Tuple[int, ...] = (1, 3, 5, 10)


def _check_number(name: str, value: float, allow_negative: bool = False) -> float:
    if value is None or isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a number")
    if math.isnan(value) or math.isinf(value):
        raise ValueError(f"{name} must be finite")
    if value < 0 and not allow_negative:
        raise ValueError(f"{name} must not be negative")
    return float(value)


def update_occupancy(current: float, entries: float, exits: float) -> float:
    """Apply entries and exits observed in an interval. Occupancy never goes below 0."""
    cur = _check_number("current", current)
    ent = _check_number("entries", entries)
    ext = _check_number("exits", exits)
    return max(0.0, cur + ent - ext)


def rate_per_minute(count: float, interval_s: float) -> float:
    """Convert a count observed over interval_s seconds into people per minute."""
    c = _check_number("count", count)
    i = _check_number("interval_s", interval_s)
    if i <= 0:
        raise ValueError("interval_s must be positive")
    return c * 60.0 / i


def rolling_rate(samples: Sequence[Tuple[float, float]]) -> float:
    """Average people/minute from (interval_seconds, count) samples. Empty input gives 0."""
    total_count = 0.0
    total_s = 0.0
    for interval_s, count in samples:
        total_count += _check_number("count", count)
        total_s += _check_number("interval_s", interval_s)
    if total_s <= 0:
        return 0.0
    return total_count * 60.0 / total_s


def forecast_occupancy(current: float, entry_rate: float, exit_rate: float,
                       horizon_min: float) -> float:
    cur = _check_number("current", current)
    ent = _check_number("entry_rate", entry_rate)
    ext = _check_number("exit_rate", exit_rate)
    h = _check_number("horizon_min", horizon_min)
    return max(0.0, cur + (ent - ext) * h)


def forecast_all(current: float, entry_rate: float, exit_rate: float,
                 horizons: Iterable[int] = HORIZONS_MIN) -> Dict[int, float]:
    return {h: round(forecast_occupancy(current, entry_rate, exit_rate, h), 2) for h in horizons}


def time_to_threshold(current: float, entry_rate: float, exit_rate: float,
                      threshold: float) -> Optional[float]:
    """Minutes until occupancy reaches threshold under constant rates.

    Returns 0.0 if already at/above the threshold, None if it is never reached.
    """
    cur = _check_number("current", current)
    thr = _check_number("threshold", threshold)
    net = _check_number("entry_rate", entry_rate) - _check_number("exit_rate", exit_rate)
    if cur >= thr:
        return 0.0
    if net <= 0:
        return None
    return round((thr - cur) / net, 2)


def risk_level(occupancy: float, capacity: float, warn_pct: float, critical_pct: float) -> str:
    cap = _check_number("capacity", capacity)
    if cap <= 0:
        raise ValueError("capacity must be positive")
    if not (0 < warn_pct < critical_pct <= 1.5):
        raise ValueError("thresholds must satisfy 0 < warn < critical")
    ratio = _check_number("occupancy", occupancy) / cap
    if ratio >= critical_pct:
        return "critical"
    if ratio >= warn_pct:
        return "warning"
    return "normal"


def is_stale(last_ts: Optional[float], now_ts: float, max_age_s: float) -> bool:
    """True if there is no measurement or it is older than max_age_s (epoch seconds)."""
    if last_ts is None:
        return True
    return (now_ts - last_ts) > max_age_s


def data_quality(age_s: Optional[float], max_age_s: float, sample_count: int,
                 min_samples: int = 3) -> Dict[str, object]:
    """Confidence is a data-quality signal, separate from risk level."""
    if age_s is None:
        return {"confidence": 0.0, "label": "none", "reasons": ["no measurements"]}
    reasons: List[str] = []
    freshness = max(0.0, 1.0 - age_s / (2.0 * max_age_s)) if max_age_s > 0 else 0.0
    if age_s > max_age_s:
        reasons.append("data is stale")
    coverage = min(1.0, sample_count / float(min_samples)) if min_samples > 0 else 1.0
    if sample_count < min_samples:
        reasons.append("few recent samples")
    conf = round(freshness * coverage, 2)
    label = "high" if conf >= 0.75 else "medium" if conf >= 0.4 else "low"
    return {"confidence": conf, "label": label, "reasons": reasons}


def exit_bottleneck(inflow_per_min: float, exit_capacity_per_min: float,
                    observed_exit_rate: float) -> Dict[str, object]:
    """Local bottleneck signal, independent of venue-wide occupancy.

    Flags when people arriving at the exit exceed what the exit can discharge, or the
    observed exit rate is far below the configured exit capacity while inflow is high.
    """
    inflow = _check_number("inflow", inflow_per_min)
    cap = _check_number("exit_capacity", exit_capacity_per_min)
    obs = _check_number("observed_exit_rate", observed_exit_rate)
    if cap <= 0:
        return {"flag": True, "pressure": None, "reason": "exit capacity not configured"}
    pressure = round(inflow / cap, 2)
    flag = pressure > 1.0 or (obs < 0.5 * cap and inflow > 0.8 * cap)
    return {"flag": flag, "pressure": pressure,
            "reason": "inflow exceeds exit capacity" if flag else "within exit capacity"}
