"""Speech-to-text through Meta Muse Voice Transcribe (REST, push-to-talk).

Documented at https://dev.meta.ai/docs/speech-to-text (read 2026-10-26):
  POST https://api.meta.ai/v1/asr/transcribe, Authorization: Bearer <key>, multipart/form-data with
  `request` (JSON: model, audioEncoding "WAV", mode "PUSH_TO_TALK", optional languageBias[], keywords[]) and
  `audio` (RIFF/WAVE, mono, 16-bit PCM, 16 or 24 kHz). Default JSON reply: transcript, audioDurationMs, sessionId.
Errors: 400 bad/too-long audio, 406 bad Accept, 413 body too large, 429 rate limit, 500 server.
The realtime WebSocket variant exists but is not used: the browser records a clip, converts it to WAV, and
sends it here, which is simpler and easier to verify. Pricing: $0.18 per hour of audio.

The credential is never logged. Raw audio is never stored or logged; it lives in memory for one request.
"""
from __future__ import annotations

import io
import json
import uuid
import wave
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import httpx

TRANSCRIBE_URL = "https://api.meta.ai/v1/asr/transcribe"
MODEL = "muse-voice-transcribe-1.0"
PRICE_PER_HOUR_USD = 0.18
MODES = ("PUSH_TO_TALK", "ENDPOINTING")
DEFAULT_MODE = "ENDPOINTING"   # returns one turn per pause; we join them so long questions are not cut short
MAX_AUDIO_BYTES = 2_500_000
MAX_AUDIO_SECONDS = 60.0
MIN_AUDIO_SECONDS = 0.3
ALLOWED_RATES = (16000, 24000)
ALLOWED_TYPES = ("audio/wav", "audio/x-wav", "audio/wave", "audio/vnd.wave")
# Languages the assistant UI offers. The API supports more (25); this list is what has been wired and tested.
LANGUAGES = ("English", "Bengali", "Hindi", "Kannada", "Tamil", "Telugu")
BASE_KEYWORDS = ("CrowdGuard", "pandal", "Durga Puja", "incident", "camera", "zone", "occupancy")
MAX_KEYWORDS = 30


class TranscriptionError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


@dataclass
class WavInfo:
    sample_rate: int
    seconds: float


def validate_wav(data: bytes) -> WavInfo:
    """Check size, container and format before anything is sent anywhere."""
    if not data:
        raise TranscriptionError(422, "empty_audio", "No audio was received.")
    if len(data) > MAX_AUDIO_BYTES:
        raise TranscriptionError(413, "audio_too_large", "The recording is too large. Keep it under one minute.")
    if data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise TranscriptionError(415, "unsupported_format", "Audio must be a WAV file (mono, 16-bit, 16 or 24 kHz).")
    try:
        with wave.open(io.BytesIO(data), "rb") as w:
            channels, width, rate, frames = w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()
            comp = w.getcomptype()
    except (wave.Error, EOFError):
        raise TranscriptionError(415, "unsupported_format", "The WAV file could not be read.") from None
    if comp != "NONE" or width != 2 or channels != 1 or rate not in ALLOWED_RATES:
        raise TranscriptionError(415, "unsupported_format",
                                 "Audio must be mono, 16-bit PCM WAV at 16 kHz or 24 kHz.")
    seconds = frames / float(rate)
    if seconds > MAX_AUDIO_SECONDS:
        raise TranscriptionError(413, "audio_too_long", "The recording is too long. Keep it under one minute.")
    if seconds < MIN_AUDIO_SECONDS:
        raise TranscriptionError(422, "audio_too_short", "The recording was too short to transcribe.")
    return WavInfo(rate, seconds)


def build_keywords(extra: Optional[List[str]] = None) -> List[str]:
    out: List[str] = []
    for k in [*BASE_KEYWORDS, *(extra or [])]:
        k = (k or "").strip()
        if 1 <= len(k) <= 60 and k not in out:
            out.append(k)
    return out[:MAX_KEYWORDS]


@dataclass
class Transcript:
    text: str
    duration_ms: Optional[int]
    session_id: Optional[str]


class MetaTranscriber:
    name = "meta"
    model = MODEL

    def __init__(self, api_key: str, timeout_s: float = 30.0, client: Optional[httpx.Client] = None,
                 mode: str = DEFAULT_MODE):
        if not api_key or not api_key.strip():
            raise ValueError("a Meta Model API credential is required")
        if mode not in MODES:
            raise ValueError("mode must be one of: " + ", ".join(MODES))
        self.mode = mode
        self._api_key = api_key.strip()
        self._client = client or httpx.Client(timeout=timeout_s)

    def __repr__(self) -> str:  # never include the credential
        return f"MetaTranscriber(model={self.model!r})"

    def transcribe(self, wav: bytes, language: Optional[str], keywords: List[str]) -> Transcript:
        req: Dict[str, Any] = {"model": MODEL, "audioEncoding": "WAV", "mode": self.mode}
        if language and language != "auto":
            req["languageBias"] = [language]   # omit entirely to let the model detect the language
        if keywords:
            req["keywords"] = keywords
        files: Dict[str, Any] = {"request": (None, json.dumps(req), "application/json"),
                 "audio": ("question.wav", wav, "audio/wav")}
        try:
            resp = self._client.post(TRANSCRIBE_URL, params={"sessionId": uuid.uuid4().hex},
                                     headers={"Authorization": f"Bearer {self._api_key}",
                                              "Accept": "application/json"}, files=files)
        except httpx.TimeoutException:
            raise TranscriptionError(504, "timeout", "Transcription took too long. Please try again.") from None
        except httpx.HTTPError:
            raise TranscriptionError(502, "network", "The transcription service could not be reached.") from None
        if resp.status_code != 200:
            raise _map_http_error(resp.status_code)
        try:
            data = resp.json()
        except ValueError:
            raise TranscriptionError(502, "bad_response", "The transcription reply could not be understood.") from None
        text = data.get("transcript") if isinstance(data, dict) else None
        if not isinstance(text, str):
            raise TranscriptionError(502, "bad_response", "The transcription reply could not be understood.")
        # `transcript` is documented as the full text in every mode; in turn-based modes also join the turns and
        # keep whichever is longer, so a long question with pauses is never reduced to its last phrase.
        turns = data.get("turns") if isinstance(data, dict) else None
        if isinstance(turns, list):
            joined = " ".join(t["transcript"].strip() for t in turns
                              if isinstance(t, dict) and isinstance(t.get("transcript"), str) and t["transcript"].strip())
            if len(joined) > len(text):
                text = joined
        dur = data.get("audioDurationMs")
        sid = data.get("sessionId")
        return Transcript(text=text.strip(), duration_ms=dur if isinstance(dur, int) and not isinstance(dur, bool) else None,
                          session_id=sid if isinstance(sid, str) else None)


def _map_http_error(status: int) -> TranscriptionError:
    if status == 429:
        return TranscriptionError(429, "throttled", "The transcription service is busy. Please try again shortly.")
    if status == 413:
        return TranscriptionError(413, "audio_too_large", "The recording is too large.")
    if status == 400:
        return TranscriptionError(422, "rejected_audio", "The transcription service could not use this audio.")
    if status in (401, 403):
        return TranscriptionError(502, "authentication", "The transcription credential was rejected. Ask an administrator.")
    return TranscriptionError(502, "server_error", "The transcription service failed. Please try again.")


def estimate_cost_usd(seconds: float) -> float:
    """Computed from the measured clip length and the published per-hour price (an estimate)."""
    return round(seconds / 3600.0 * PRICE_PER_HOUR_USD, 6)
