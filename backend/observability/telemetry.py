"""Workflow/system observability and exporter interfaces.

TelemetryExporter is the seam for future OpenTelemetry traces and CloudWatch metrics.
The local default only keeps data in memory/SQLite. Nothing is sent externally.
"""
from __future__ import annotations

import threading
import time
from collections import deque
from typing import Any, Deque, Dict, Protocol, Tuple

from backend.observability.llm import percentile
from backend.repositories.store import Repos


class TelemetryExporter(Protocol):
    def emit_metric(self, name: str, value: float, unit: str, dimensions: Dict[str, str]) -> None: ...
    def emit_span(self, name: str, trace_id: str, start: float, end: float, attrs: Dict[str, Any]) -> None: ...


class NullExporter:
    def emit_metric(self, name: str, value: float, unit: str, dimensions: Dict[str, str]) -> None:
        return None

    def emit_span(self, name: str, trace_id: str, start: float, end: float, attrs: Dict[str, Any]) -> None:
        return None


class RequestMetrics:
    """In-memory ring buffer of recent API requests: (ts, path, status, latency_ms)."""

    def __init__(self, size: int = 2000):
        self._buf: Deque[Tuple[float, str, int, float]] = deque(maxlen=size)
        self._lock = threading.Lock()

    def record(self, path: str, status: int, latency_ms: float) -> None:
        with self._lock:
            self._buf.append((time.time(), path, status, latency_ms))

    def clear(self) -> None:
        with self._lock:
            self._buf.clear()

    def summary(self) -> Dict[str, Any]:
        with self._lock:
            rows = list(self._buf)
        lat = [r[3] for r in rows]
        failures = [r for r in rows if r[2] >= 500]
        return {"requests": len(rows), "failures_5xx": len(failures),
                "client_errors_4xx": len([r for r in rows if 400 <= r[2] < 500]),
                "latency_p50_ms": percentile(lat, 50), "latency_p95_ms": percentile(lat, 95)}


def system_metrics(repos: Repos, requests: RequestMetrics) -> Dict[str, Any]:
    runs = repos.runs.list(order="started", desc=True, limit=500)
    done = [r for r in runs if r["status"] != "running"]
    durations = [r["duration_ms"] for r in done if r["duration_ms"] is not None]
    failed = [r for r in done if r["status"] == "failed"]
    stale_runs = [r for r in runs if r.get("error_code") in ("NO_DATA",)]
    cams = repos.cameras.list({"active": 1})
    llm = repos.llm_calls.list()
    ts = [r["started"] for r in runs]
    span_s = (max(ts) - min(ts)) if len(ts) > 1 else 0
    return {
        "api": requests.summary(),
        "ingestion": {"runs_sampled": len(runs), "failed": len(failed),
                      "throughput_per_min": round(len(runs) / (span_s / 60), 2) if span_s > 0 else None,
                      "stale_event_rate": round(len(stale_runs) / len(runs), 4) if runs else None,
                      "retries": sum(r["retry_count"] or 0 for r in runs)},
        "workflow": {"duration_p50_ms": percentile(durations, 50), "duration_p95_ms": percentile(durations, 95)},
        "cameras": {"active": len(cams),
                    "processing_failures": len([c for c in cams if c["status"] in ("stale", "error")])},
        "model_api": {"errors": len([c for c in llm if c["status"] != "success"]),
                      "throttled": len([c for c in llm if c["error_category"] == "throttled"])},
        "storage": {"sqlite": "ok"},
    }
