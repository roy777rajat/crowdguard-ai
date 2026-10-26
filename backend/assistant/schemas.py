"""Request/response models for the assistant API."""
from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

MAX_MESSAGE_CHARS = 1500


class ChatIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    message: str = Field(min_length=1, max_length=MAX_MESSAGE_CHARS)
    conversation_id: Optional[str] = Field(default=None, max_length=64)
    # How the text was produced. Informational only: typed and spoken questions take the same path.
    input_mode: Literal["text", "voice"] = "text"


class ToolTrace(BaseModel):
    name: str
    ok: bool
    duration_ms: Optional[float] = None
    error_code: Optional[str] = None


class SourceRef(BaseModel):
    tool: str
    source: str
    as_of: Optional[float] = None


class PendingOut(BaseModel):
    action_id: str
    summary: str
    destructive: bool = False


class ChatOut(BaseModel):
    status: Literal["ok", "unavailable", "error", "limit_reached"]
    answer: Optional[str] = None
    message: Optional[str] = None          # client-safe explanation when status != ok
    conversation_id: Optional[str] = None
    correlation_id: str
    provider: Optional[str] = None
    model: Optional[str] = None
    turns: int = 0
    tools: List[ToolTrace] = Field(default_factory=list)
    sources: List[SourceRef] = Field(default_factory=list)
    pending_actions: List[PendingOut] = Field(default_factory=list)
    duration_ms: Optional[float] = None
    usage: Dict[str, Any] = Field(default_factory=dict)
    disclaimer: str = ("Decision support for authorized human review, not an instruction. "
                       "Authoritative values come from the forecasting engine; data is simulated.")
