"""Lightweight, durable local workflow engine. State is persisted to SQLite after every stage.

Deliberately simple: no Step Functions until the need and deployment plan are reviewed.
"""
from __future__ import annotations

import re
import time
import uuid
from typing import Any, Callable, Dict, List, Optional

from backend.repositories.store import Repos


class StageError(Exception):
    def __init__(self, code: str, message: str, retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.retryable = retryable


_PATH_RE = re.compile(r"([A-Za-z]:\\[^\s'\"]+|/(?:[\w.\-]+/)+[\w.\-]+)")
_SECRET_RE = re.compile(r"(?i)(password|secret|token|api[_-]?key)\s*[=:]\s*\S+")


def sanitize(message: str, limit: int = 200) -> str:
    msg = _SECRET_RE.sub(r"\1=[redacted]", str(message))
    msg = _PATH_RE.sub("[path]", msg)
    return msg[:limit]


class RunContext:
    def __init__(self, repos: Repos, run_id: str, trace_id: str):
        self.repos = repos
        self.run_id = run_id
        self.trace_id = trace_id
        self.started = time.time()
        self.status = "running"
        self.retry_count = 0
        self.error_code: Optional[str] = None
        self.error_message: Optional[str] = None
        self.outputs: Dict[str, Any] = {}
        self.incident_ids: List[int] = []

    def execute(self, name: str, fn: Callable[[], Any], retries: int = 0, critical: bool = True) -> Any:
        """Run a stage, recording each attempt. Critical failures re-raise; others mark the run partial."""
        attempt = 0
        while True:
            attempt += 1
            started = time.time()
            stage = self.repos.stages.create(run_id=self.run_id, name=name, attempt=attempt,
                                             status="running", started=started)
            try:
                result = fn()
            except Exception as exc:  # noqa: BLE001 - every failure must be recorded
                code = exc.code if isinstance(exc, StageError) else "UNEXPECTED_ERROR"
                retryable = isinstance(exc, StageError) and exc.retryable
                ended = time.time()
                self.repos.stages.update(stage["id"], status="failed", ended=ended,
                                         duration_ms=round((ended - started) * 1000, 2),
                                         error_code=code, error_message=sanitize(str(exc)))
                if retryable and attempt <= retries:
                    self.retry_count += 1
                    self.repos.runs.update(self.run_id, retry_count=self.retry_count)
                    continue
                if critical:
                    self.status = "failed"
                    self.error_code = code
                    self.error_message = sanitize(str(exc))
                    raise
                if self.status == "running":
                    self.status = "partial"
                    self.error_code = code
                    self.error_message = sanitize(str(exc))
                return None
            ended = time.time()
            self.repos.stages.update(stage["id"], status="succeeded", ended=ended,
                                     duration_ms=round((ended - started) * 1000, 2))
            return result

    def finish(self) -> None:
        if self.status == "running":
            self.status = "succeeded"
        ended = time.time()
        self.repos.runs.update(self.run_id, status=self.status, ended=ended,
                               duration_ms=round((ended - self.started) * 1000, 2),
                               retry_count=self.retry_count, error_code=self.error_code,
                               error_message=self.error_message, outputs=self.outputs,
                               incident_ids=self.incident_ids)


def start_run(repos: Repos, pandal_id: Optional[int], camera_id: Optional[int],
              source_ts: Optional[float] = None, trace_id: Optional[str] = None) -> RunContext:
    run_id = "run_" + uuid.uuid4().hex[:12]
    trace = trace_id or uuid.uuid4().hex
    repos.runs.create(run_id=run_id, trace_id=trace, pandal_id=pandal_id, camera_id=camera_id,
                      source_ts=source_ts, status="running", started=time.time(), retry_count=0,
                      outputs={}, incident_ids=[])
    return RunContext(repos, run_id, trace)


def recover_interrupted(repos: Repos) -> int:
    """Mark runs left 'running' by a previous process as failed so history stays truthful."""
    rows = repos.runs.list({"status": "running"})
    for r in rows:
        repos.runs.update(r["run_id"], status="failed", ended=time.time(), error_code="INTERRUPTED",
                          error_message="Process stopped before the run finished")
    return len(rows)
