"""FastAPI application factory.

Run:  uvicorn backend.api.app:create_app --factory --reload --port 8000
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from backend.api.admin_routes import router as admin_router
from backend.api.routes import router
from backend.api.vision_routes import router as vision_router
from backend.config import build_provider, load_dotenv
from backend.db import Database
from backend.observability.llm import LLMProvider
from backend.observability.telemetry import NullExporter, RequestMetrics
from backend.repositories.store import Repos
from backend.security.auth import DemoAuth
from backend.services.context import AppContext
from backend.services.incidents import IncidentService
from backend.services.monitor import MonitorService
from backend.services.seed import seed_demo
from backend.vision.detector import PersonDetector, build_detector
from backend.workflows.runner import recover_interrupted

log = logging.getLogger("crowdguard")


def create_app(db_path: Optional[str] = None, run_loop: bool = True, seed: bool = True,
               provider: Optional[LLMProvider] = None,
               detector: Optional[PersonDetector] = None) -> FastAPI:
    if provider is None:
        load_dotenv()  # reads E:\dev\crowdguard-ai\.env if present; real environment variables win
        provider, provider_info = build_provider()
    else:
        provider_info = {"requested": "injected", "credential_set": False, "note": ""}
    if detector is None:
        load_dotenv()
        detector, detector_info = build_detector()
    else:
        detector_info = {"requested": "injected", "credential_set": False, "note": ""}
    path = db_path or os.environ.get("CROWDGUARD_DB", "data/crowdguard.db")
    db = Database(path)
    repos = Repos(db)
    auth = DemoAuth(repos)
    incidents = IncidentService(repos)
    monitor = MonitorService(repos, incidents, provider)
    ctx = AppContext(repos=repos, auth=auth, incidents=incidents, monitor=monitor, provider=provider,
                     request_metrics=RequestMetrics(), exporter=NullExporter(), provider_info=provider_info)
    if seed:
        seed_demo(repos, auth)
    recover_interrupted(repos)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        task = None
        if run_loop:
            async def loop() -> None:
                while True:
                    await asyncio.sleep(max(0.2, 2.0 / max(monitor.speed, 0.1)))
                    if monitor.running:
                        try:
                            await asyncio.get_running_loop().run_in_executor(None, monitor.tick)
                        except Exception:  # noqa: BLE001
                            log.exception("tick failed")
            task = asyncio.create_task(loop())
        yield
        if task:
            task.cancel()

    app = FastAPI(title="CrowdGuard AI", version="0.1.0", lifespan=lifespan,
                  description="Experimental crowd-safety decision support. Simulated data. Demo auth.")
    app.state.ctx = ctx
    app.state.detector = detector
    app.state.detector_info = detector_info
    origins = os.environ.get("CROWDGUARD_CORS", "http://localhost:5173,http://127.0.0.1:5173").split(",")
    app.add_middleware(CORSMiddleware, allow_origins=origins, allow_methods=["*"], allow_headers=["*"])

    @app.middleware("http")
    async def timing(request: Request, call_next):
        start = time.perf_counter()
        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
            return response
        finally:
            ctx.request_metrics.record(request.url.path, status, (time.perf_counter() - start) * 1000)

    app.include_router(router)
    app.include_router(vision_router)
    app.include_router(admin_router)
    return app
