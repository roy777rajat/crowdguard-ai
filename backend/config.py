"""Environment configuration and model-provider selection.

The Meta provider is opt-in: it is used only when CROWDGUARD_PROVIDER=meta AND a credential is set.
Otherwise the app uses the local template provider, which needs no credentials and sends nothing anywhere.
The credential itself is never logged or returned by any API.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Tuple

from backend.assistant.transcribe import MetaTranscriber
from backend.integrations.meta_adapter import DEFAULT_MODEL, MetaModelProvider
from backend.observability.llm import LLMProvider, LocalTemplateProvider

ENV_PATH = Path(__file__).resolve().parent.parent / ".env"


def load_dotenv(path: Path = ENV_PATH) -> bool:
    """Read KEY=VALUE lines into os.environ without overriding variables that are already set."""
    if not path.is_file():
        return False
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key:
            os.environ.setdefault(key, value)
    return True


def build_provider(env: Optional[Mapping[str, str]] = None) -> Tuple[LLMProvider, Dict[str, Any]]:
    env = os.environ if env is None else env
    requested = (env.get("CROWDGUARD_PROVIDER") or "local").strip().lower()
    credential = (env.get("META_MODEL_API_KEY") or env.get("MODEL_API_KEY") or "").strip()
    info: Dict[str, Any] = {"requested": requested, "credential_set": bool(credential), "note": ""}

    if requested == "meta":
        if not credential:
            info["note"] = "Meta was requested but no credential is set; using the local template provider."
        else:
            try:
                return MetaModelProvider(credential, model=(env.get("META_MODEL") or DEFAULT_MODEL).strip()), info
            except ValueError as exc:
                info["note"] = f"Meta provider not enabled: {exc}. Using the local template provider."
    elif requested != "local":
        info["note"] = f"Unknown provider '{requested}'; using the local template provider."
    return LocalTemplateProvider(), info


def build_transcriber(env: Optional[Mapping[str, str]] = None) -> Optional[MetaTranscriber]:
    """Voice transcription is opt-in because audio leaves this computer: CROWDGUARD_VOICE=meta AND a key.

    The key is META_VOICE_API_KEY if set, otherwise the same Meta key used for Muse Spark.
    """
    env = os.environ if env is None else env
    if (env.get("CROWDGUARD_VOICE") or "off").strip().lower() != "meta":
        return None
    key = (env.get("META_VOICE_API_KEY") or env.get("META_MODEL_API_KEY") or env.get("MODEL_API_KEY") or "").strip()
    mode = (env.get("META_VOICE_MODE") or "ENDPOINTING").strip().upper()
    if mode not in ("ENDPOINTING", "PUSH_TO_TALK"):
        mode = "ENDPOINTING"
    return MetaTranscriber(key, mode=mode) if key else None
