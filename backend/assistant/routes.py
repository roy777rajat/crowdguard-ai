"""Assistant HTTP API: /api/assistant/*."""
from __future__ import annotations

import logging
import re
import time
import uuid
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.concurrency import run_in_threadpool

from backend.api.deps import get_ctx, require
from backend.assistant.actions import PendingStore, actions_for, execute
from backend.assistant.orchestrator import ConversationStore, RateLimiter, run_chat
from backend.assistant.schemas import ChatIn, ChatOut
from backend.assistant.tools import ToolEnv, ToolError, tools_for
from backend.assistant.transcribe import (
    ALLOWED_TYPES,
    LANGUAGES,
    MAX_AUDIO_BYTES,
    MODEL,
    TranscriptionError,
    build_keywords,
    estimate_cost_usd,
    validate_wav,
)
from backend.services.context import AppContext

router = APIRouter(prefix="/api/assistant")
log = logging.getLogger("crowdguard.assistant")
User = Dict[str, Any]

SUGGESTIONS = [
    ("list_incidents", "Show me unresolved incidents and their pandal names."),
    ("get_camera_health", "Which cameras are currently offline?"),
    ("get_overview", "Which pandals have the highest risk right now?"),
    ("get_forecast_and_risk", "What is the forecast and risk for the first pandal in the list?"),
    ("get_simulation_status", "Is the simulation running, and with which scenario?"),
    ("get_vision_status", "Is the people counter simulated or the real Meta SAM 3.1 model?"),
    ("get_model_usage_summary", "How much have model calls cost so far, and how many failed?"),
    ("list_workflow_runs", "Show the most recent workflow runs and whether any failed."),
    ("get_system_metrics", "Is the system healthy? Show API errors and camera failures."),
]


class AssistantState:
    def __init__(self) -> None:
        self.pending = PendingStore()
        self.conversations = ConversationStore()
        self.chat_limit = RateLimiter(20)
        self.voice_limit = RateLimiter(10)


def _state(request: Request) -> AssistantState:
    st: AssistantState = request.app.state.assistant
    return st


def _env(request: Request, ctx: AppContext, user: User) -> ToolEnv:
    return ToolEnv(ctx=ctx, user=user, app_state=request.app.state)


@router.get("/capabilities")
def capabilities(request: Request, user: User = Depends(require("read")),
                 ctx: AppContext = Depends(get_ctx)) -> Dict[str, Any]:
    tr = getattr(request.app.state, "transcriber", None)
    available_tools = {t.name for t in tools_for(user)}
    return {
        "chat": {"available": ctx.provider.name == "meta" and hasattr(ctx.provider, "respond_with_tools"),
                 "provider": ctx.provider.name if ctx.provider.name == "meta" else None,
                 "model": ctx.provider.model if ctx.provider.name == "meta" else None,
                 "reason": None if ctx.provider.name == "meta" else
                 "No Meta Muse Spark credential is configured on the server, so the assistant cannot answer."},
        "voice": {"available": tr is not None, "model": MODEL if tr is not None else None,
                  "languages": ["auto", *LANGUAGES], "max_seconds": 60,
                  "reason": None if tr is not None else
                  "Voice transcription is off (set CROWDGUARD_VOICE=meta and a Meta key on the server)."},
        "tools": [{"name": t.name, "description": t.description, "permission": t.permission,
                   "source": t.source} for t in tools_for(user)],
        "actions": [{"name": a.name, "permission": a.permission, "requires_confirmation": True}
                    for a in actions_for(user)],
        "suggestions": [q for tool, q in SUGGESTIONS if tool in available_tools],
    }


YES = {"yes", "y", "yeah", "yep", "confirm", "confirmed", "yes confirm", "yes please", "yes do it", "do it"}
NO = {"no", "n", "nope", "no thanks", "dont", "do not"}
CANCEL = {"cancel", "cancel it", "cancel all", "stop", "abort", "never mind", "nevermind"}


def _reply_word(message: str) -> Optional[str]:
    t = re.sub(r"[^a-z ]", "", message.lower().replace("'", "")).strip()
    t = re.sub(r"\s+", " ", t)
    return "yes" if t in YES else "no" if t in NO else "cancel" if t in CANCEL else None


def _answer_reply(word: str, st: AssistantState, env: ToolEnv, ctx: AppContext, user: User, conv_id: str,
                  corr: str, message: str) -> ChatOut:
    """The user's own Yes/No/Cancel decides a pending action. The model is not involved and cannot influence it."""
    pend = st.pending.latest_for(user["id"])
    assert pend is not None
    if word == "cancel":
        n = len(st.pending.pending_for(user["id"]))
        for p in st.pending.pending_for(user["id"]):
            st.pending.drop(p.id)
        text = f"Cancelled. {n} pending change(s) discarded; nothing was changed."
        status = "cancelled"
    elif word == "no":
        st.pending.drop(pend.id)
        text = f"Okay, not doing that: {pend.summary}. Nothing was changed."
        status = "declined"
    else:
        st.pending.drop(pend.id)
        try:
            res = execute(pend, env)
            text = f"Done: {res['summary']}."
            status = "confirmed"
        except ToolError as exc:
            text = f"That could not be done: {exc.message} Nothing was changed."
            status = "failed"
        left = len(st.pending.pending_for(user["id"]))
        if left:
            text += f" {left} other change(s) are still waiting for your reply."
    ctx.repos.audit.log(user["username"], "assistant.reply", "assistant", None,
                        {"correlation_id": corr, "decision": status, "action": pend.action})
    st.conversations.append(conv_id, user["id"], message, text)
    return ChatOut(status="ok", answer=text, conversation_id=conv_id, correlation_id=corr, turns=0)


@router.post("/chat", response_model=ChatOut)
def chat(body: ChatIn, request: Request, response: Response, user: User = Depends(require("read")),
         ctx: AppContext = Depends(get_ctx)) -> ChatOut:
    st = _state(request)
    corr = uuid.uuid4().hex
    response.headers["X-Correlation-Id"] = corr
    if not st.chat_limit.allow(user["id"]):
        raise HTTPException(429, "Too many questions. Please wait a minute.")
    message = body.message.strip()
    if not message:
        raise HTTPException(422, "Message must not be empty")
    conv_id, history, found = st.conversations.get(body.conversation_id, user["id"])
    if not found:
        raise HTTPException(404, "Conversation not found")
    env = _env(request, ctx, user)
    word = _reply_word(message)
    if word is not None and st.pending.latest_for(user["id"]) is not None:
        return _answer_reply(word, st, env, ctx, user, conv_id, corr, message)
    out = run_chat(env, ctx.provider, message, history, st.pending, conv_id, corr)
    if out.status == "ok" and out.answer:
        st.conversations.append(conv_id, user["id"], message, out.answer)
    ctx.repos.audit.log(user["username"], "assistant.chat", "assistant", None,
                        {"correlation_id": corr, "status": out.status, "input_mode": body.input_mode,
                         "tools": [t.name for t in out.tools], "turns": out.turns})
    return out


@router.delete("/conversations/{conversation_id}")
def delete_conversation(conversation_id: str, request: Request, user: User = Depends(require("read"))) -> Dict[str, Any]:
    if not _state(request).conversations.delete(conversation_id, user["id"]):
        raise HTTPException(404, "Conversation not found")
    return {"ok": True}


@router.post("/actions/{action_id}/confirm")
def confirm_action(action_id: str, request: Request, user: User = Depends(require("read")),
                   ctx: AppContext = Depends(get_ctx)) -> Dict[str, Any]:
    pend = _state(request).pending.take(action_id, user["id"])
    if pend is None:
        raise HTTPException(404, "That action was not found, has expired, or was already handled")
    try:
        res = execute(pend, _env(request, ctx, user))
    except ToolError as exc:
        status = {"forbidden": 403, "not_found": 404, "conflict": 409}.get(exc.code, 422)
        raise HTTPException(status, exc.message) from None
    return {"ok": True, "summary": res["summary"]}


@router.post("/actions/cancel-all")
def cancel_all(request: Request, user: User = Depends(require("read"))) -> Dict[str, Any]:
    st = _state(request)
    items = st.pending.pending_for(user["id"])
    for p in items:
        st.pending.drop(p.id)
    return {"ok": True, "cancelled": len(items)}


@router.post("/actions/{action_id}/cancel")
def cancel_action(action_id: str, request: Request, user: User = Depends(require("read"))) -> Dict[str, Any]:
    if _state(request).pending.take(action_id, user["id"]) is None:
        raise HTTPException(404, "That action was not found, has expired, or was already handled")
    return {"ok": True}


@router.post("/transcribe")
async def transcribe(request: Request, language: str = "auto", user: User = Depends(require("read")),
                     ctx: AppContext = Depends(get_ctx)) -> Dict[str, Any]:
    """Body: one WAV clip (mono, 16-bit PCM, 16 or 24 kHz). Returns the transcript for the user to review."""
    st = _state(request)
    corr = uuid.uuid4().hex
    tr = getattr(request.app.state, "transcriber", None)
    if tr is None:
        raise HTTPException(503, "Voice transcription is not available: no Meta credential is configured on the server.")
    if language != "auto" and language not in LANGUAGES:
        raise HTTPException(422, "Unsupported language. Choose auto or one of: " + ", ".join(LANGUAGES))
    ctype = (request.headers.get("content-type") or "").split(";")[0].strip().lower()
    if ctype not in ALLOWED_TYPES:
        raise HTTPException(415, "Send the recording as audio/wav")
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > MAX_AUDIO_BYTES:
        raise HTTPException(413, "The recording is too large. Keep it under one minute.")
    if not st.voice_limit.allow(user["id"]):
        raise HTTPException(429, "Too many recordings. Please wait a minute.")
    data = await request.body()
    t0 = time.time()
    try:
        info = validate_wav(data)
        pandal_names: List[str] = [p["name"] for p in ctx.repos.pandals.list({"active": 1})
                                   if user["role"] != "manager" or p["id"] in (user.get("pandal_ids") or [])]
        result = await run_in_threadpool(tr.transcribe, data, language, build_keywords(pandal_names))
    except TranscriptionError as exc:
        _log_voice(ctx, user, corr, "error", t0, None, exc.code)
        raise HTTPException(exc.status, exc.message) from None
    finally:
        del data   # raw audio is never kept
    _log_voice(ctx, user, corr, "ok", t0, info.seconds, None)
    if not result.text:
        raise HTTPException(422, "No speech was recognised. Try again, closer to the microphone.")
    return {"transcript": result.text, "model": MODEL, "correlation_id": corr, "language": language,
            "audio_seconds": round(info.seconds, 2), "audio_duration_ms_reported": result.duration_ms,
            "est_cost_usd": estimate_cost_usd(info.seconds)}


def _log_voice(ctx: AppContext, user: User, corr: str, status: str, t0: float, seconds: Optional[float],
               code: Optional[str]) -> None:
    dur = round((time.time() - t0) * 1000, 1)
    log.info("transcribe corr=%s user=%s status=%s duration_ms=%s audio_s=%s code=%s", corr, user["id"], status,
             dur, seconds, code)
    ctx.repos.audit.log(user["username"], "assistant.transcribe", "assistant", None,
                        {"correlation_id": corr, "status": status, "duration_ms": dur, "audio_seconds": seconds,
                         "error": code, "model": MODEL})
