"""Pydantic request schemas (validation at the API boundary)."""
from __future__ import annotations

import re
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

_CRED_RE = re.compile(r"://[^/\s]*:[^/\s]*@|(?i:password|token|secret|key)=")


class PandalIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    location: Optional[str] = Field(default=None, max_length=200)
    capacity: int = Field(gt=0, le=1_000_000)
    warn_pct: float = Field(default=0.7, gt=0, lt=1.5)
    critical_pct: float = Field(default=0.9, gt=0, le=1.5)
    meta: Dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _order(self) -> "PandalIn":
        if self.warn_pct >= self.critical_pct:
            raise ValueError("warn_pct must be lower than critical_pct")
        return self


class PandalUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=120)
    location: Optional[str] = Field(default=None, max_length=200)
    capacity: Optional[int] = Field(default=None, gt=0, le=1_000_000)
    warn_pct: Optional[float] = Field(default=None, gt=0, lt=1.5)
    critical_pct: Optional[float] = Field(default=None, gt=0, le=1.5)
    meta: Optional[Dict[str, Any]] = None


class LocationPandalIn(BaseModel):
    """A pandal inside a location: unique association name, exactly 3 user-named zones."""
    name: str = Field(min_length=1, max_length=120)
    zone_names: List[str] = Field(min_length=3, max_length=3)
    max_people: int = Field(default=40, ge=1, le=5000)
    warn_pct: float = Field(default=0.7, gt=0, lt=1.5)
    critical_pct: float = Field(default=0.9, gt=0, le=1.5)

    @field_validator("zone_names")
    @classmethod
    def _zone_names(cls, v: List[str]) -> List[str]:
        names = [z.strip() for z in v]
        if any(not n or len(n) > 80 for n in names):
            raise ValueError("each zone name must be 1-80 characters")
        if len({n.lower() for n in names}) != len(names):
            raise ValueError("zone names must be different")
        return names

    @model_validator(mode="after")
    def _order(self) -> "LocationPandalIn":
        if self.warn_pct >= self.critical_pct:
            raise ValueError("warn_pct must be lower than critical_pct")
        return self


class ZoneUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=80)
    max_people: Optional[int] = Field(default=None, ge=1, le=5000)


class ZoneIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    exit_capacity_per_min: float = Field(default=60, gt=0, le=10_000)


class CameraIn(BaseModel):
    pandal_id: int
    zone_id: Optional[int] = None
    name: str = Field(min_length=1, max_length=120)
    source_type: Literal["simulated", "local_video", "live_stream"] = "simulated"
    source_ref: Optional[str] = Field(default=None, max_length=300)

    @field_validator("source_ref")
    @classmethod
    def _no_credentials(cls, v: Optional[str]) -> Optional[str]:
        if v and _CRED_RE.search(v):
            raise ValueError("source_ref must not contain credentials")
        return v

    @model_validator(mode="after")
    def _video_needs_ref(self) -> "CameraIn":
        if self.source_type in ("local_video", "live_stream") and not self.source_ref:
            raise ValueError(f"{self.source_type} requires source_ref")
        if self.source_type == "live_stream" and not (self.source_ref or "").lower().startswith(("http://", "https://")):
            raise ValueError("live_stream source_ref must be an http(s) URL")
        return self


class CameraUpdate(BaseModel):
    zone_id: Optional[int] = None
    name: Optional[str] = Field(default=None, min_length=1, max_length=120)
    source_type: Optional[Literal["simulated", "local_video", "live_stream"]] = None
    source_ref: Optional[str] = Field(default=None, max_length=300)
    mode: Optional[Literal["active", "passive"]] = None

    @field_validator("source_ref")
    @classmethod
    def _no_credentials(cls, v: Optional[str]) -> Optional[str]:
        if v and _CRED_RE.search(v):
            raise ValueError("source_ref must not contain credentials")
        return v


class LoginIn(BaseModel):
    username: str = Field(min_length=1, max_length=60)
    password: str = Field(min_length=1, max_length=200)


class RegisterIn(BaseModel):
    username: str = Field(min_length=3, max_length=60, pattern=r"^[A-Za-z0-9_.-]+$")
    password: str = Field(min_length=8, max_length=200)
    display_name: Optional[str] = Field(default=None, max_length=100)


class SimIn(BaseModel):
    scenario: Literal["normal_flow", "arrival_surge", "reduced_exit", "stale_sensor"] = "normal_flow"
    speed: float = Field(default=1.0, gt=0, le=20)
    pandal_id: Optional[int] = None  # None = every pandal (kept for scripts); the UI always sends one


class TransitionIn(BaseModel):
    to: Literal["acknowledged", "assigned", "resolved"]
    note: Optional[str] = Field(default=None, max_length=1000)
    assignee: Optional[str] = Field(default=None, max_length=100)


class NoteIn(BaseModel):
    note: str = Field(min_length=1, max_length=1000)


class SettingsIn(BaseModel):
    max_data_age_s: Optional[float] = Field(default=None, gt=0, le=3600)
    sim_speed: Optional[float] = Field(default=None, gt=0, le=20)
    llm_budget_usd: Optional[float] = Field(default=None, ge=0)
    predicted_critical_lead_min: Optional[float] = Field(default=None, gt=0, le=60)
    rules_enabled: Optional[Dict[str, bool]] = None
    vision_budget_usd: Optional[float] = Field(default=None, ge=0, le=1000)
