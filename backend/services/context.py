"""Application context shared by routes."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict

from backend.observability.llm import LLMProvider
from backend.observability.telemetry import RequestMetrics, TelemetryExporter
from backend.repositories.store import Repos
from backend.security.auth import DemoAuth
from backend.services.incidents import IncidentService
from backend.services.monitor import MonitorService


@dataclass
class AppContext:
    repos: Repos
    auth: DemoAuth
    incidents: IncidentService
    monitor: MonitorService
    provider: LLMProvider
    request_metrics: RequestMetrics
    exporter: TelemetryExporter
    provider_info: Dict[str, Any] = field(default_factory=dict)
