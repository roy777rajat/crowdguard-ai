"""Person detection for camera frames.

Two detectors:
  * SimulatedDetector (default): never looks at pixels and sends nothing anywhere. It only echoes the
    ground-truth count that the built-in synthetic demo feed reports about itself, and says so.
  * Sam31Detector (opt-in): sends one JPEG frame to Meta's SAM 3.1 (model "sam-3.1") with the concept prompt
    "person" and counts the returned boxes. It uses the same Meta Model API credential as the explanations.

API facts come from https://dev.meta.ai/docs/sam/segmenting and /docs/pricing-rate-limits (read 2026-10-25):
POST https://api.meta.ai/v1/responses, bearer token, input = one user message with an input_text concept prompt
and an input_image (base64 data URL allowed), metadata.include_confidence = "true", streaming optional. The
reply text is a token grammar such as  <0f>0<|box;x1=..;y1=..;x2=..;y2=..;w=..;h=..;c=..|><|mask;...|>
(absolute pixel coordinates, x2/y2 inclusive, w/h = source image size). An empty reply means "no match".
Image segmentation costs $0.0025 per image. Counts come only from a detector, never from a language model.
"""
from __future__ import annotations

import base64
import json
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Protocol, Tuple

import httpx

MAX_FRAME_BYTES = 2_000_000
SAM_URL = "https://api.meta.ai/v1/responses"
SAM_MODEL = "sam-3.1"
SAM_PRICE_PER_IMAGE_USD = 0.0025
PRICING_NOTE = "meta-sam-3.1 image price $2.50 per 1,000 images (read 2026-10-25)"

_BOX_RE = re.compile(r"<\|box;([^|]*)\|>")


class DetectorError(Exception):
    def __init__(self, category: str, message: str = ""):
        super().__init__(message or category)
        self.category = category


@dataclass
class DetectionResult:
    count: Optional[int]  # None = "not available", never a made-up zero
    boxes: List[Dict[str, Any]] = field(default_factory=list)  # pixels of the frame that was sent
    frame_w: Optional[int] = None
    frame_h: Optional[int] = None
    detector: str = ""
    is_real_model: bool = False
    latency_ms: float = 0.0
    note: str = ""


class PersonDetector(Protocol):
    name: str
    is_real_model: bool
    price_per_call_usd: float

    def detect(self, jpeg: bytes, demo_truth: Optional[int] = None) -> DetectionResult: ...


class SimulatedDetector:
    name = "simulated"
    is_real_model = False
    price_per_call_usd = 0.0

    def detect(self, jpeg: bytes, demo_truth: Optional[int] = None) -> DetectionResult:
        if demo_truth is not None:
            return DetectionResult(count=max(0, int(demo_truth)), detector=self.name,
                                   note="Synthetic demo feed: this number is the animation's own count, "
                                        "not a model result.")
        return DetectionResult(count=None, detector=self.name,
                               note="No person-detection model is enabled, so this video cannot be counted.")


def parse_boxes(text: str, threshold: float) -> Tuple[List[Dict[str, Any]], Optional[int], Optional[int]]:
    """Extract person boxes from SAM's token-grammar text. Boxes below the threshold are dropped."""
    boxes: List[Dict[str, Any]] = []
    fw: Optional[int] = None
    fh: Optional[int] = None
    for m in _BOX_RE.finditer(text):
        kv: Dict[str, float] = {}
        for part in m.group(1).split(";"):
            if "=" in part:
                k, v = part.split("=", 1)
                try:
                    kv[k.strip()] = float(v)
                except ValueError:
                    pass
        if not all(k in kv for k in ("x1", "y1", "x2", "y2")):
            continue
        if "w" in kv and "h" in kv:
            fw, fh = int(kv["w"]), int(kv["h"])
        score = kv.get("c")
        if score is not None and score < threshold:
            continue
        boxes.append({"id": len(boxes) + 1, "box": [kv["x1"], kv["y1"], kv["x2"], kv["y2"]], "score": score})
    return boxes, fw, fh


def _delta_text(resp: httpx.Response) -> str:
    """Join the text deltas of a server-sent-event stream (or read a plain JSON reply)."""
    ctype = resp.headers.get("content-type", "")
    if "text/event-stream" not in ctype:
        try:
            data = resp.json()
        except ValueError:
            raise DetectorError("bad_response", "not JSON or event stream") from None
        if isinstance(data, dict) and isinstance(data.get("output_text"), str):
            return str(data["output_text"])
        raise DetectorError("bad_response", "no output_text in reply")
    parts: List[str] = []
    completed = False
    for line in resp.iter_lines():
        if not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if not payload or payload == "[DONE]":
            continue
        try:
            ev = json.loads(payload)
        except ValueError:
            continue
        kind = ev.get("type") if isinstance(ev, dict) else None
        if kind == "response.output_text.delta" and isinstance(ev.get("delta"), str):
            parts.append(ev["delta"])
        elif kind == "response.completed":
            completed = True
        elif kind in ("response.failed", "error"):
            raise DetectorError("failed", "the model reported a failure")
    if not completed:
        raise DetectorError("incomplete", "stream ended before response.completed")
    return "".join(parts)


class Sam31Detector:
    """Counts people with Meta SAM 3.1. Opt-in; frames showing people leave this computer."""

    name = "meta-sam-3.1"
    is_real_model = True
    price_per_call_usd = SAM_PRICE_PER_IMAGE_USD

    def __init__(self, api_key: str, threshold: float = 0.5, timeout_s: float = 60.0,
                 client: Optional[httpx.Client] = None, url: str = SAM_URL):
        if not api_key or not api_key.strip():
            raise ValueError("a Meta Model API credential is required")
        if not 0 < threshold <= 1:
            raise ValueError("threshold must be between 0 and 1")
        self._key, self._threshold, self._url = api_key.strip(), threshold, url
        self._client = client or httpx.Client(timeout=timeout_s)

    def __repr__(self) -> str:  # never show the key
        return f"Sam31Detector(threshold={self._threshold})"

    def detect(self, jpeg: bytes, demo_truth: Optional[int] = None) -> DetectionResult:
        started = time.perf_counter()
        data_url = "data:image/jpeg;base64," + base64.b64encode(jpeg).decode("ascii")
        body = {"model": SAM_MODEL, "stream": True, "metadata": {"include_confidence": "true"},
                "input": [{"type": "message", "role": "user", "content": [
                    {"type": "input_text", "text": "person"},
                    {"type": "input_image", "image_url": data_url}]}]}
        headers = {"Authorization": f"Bearer {self._key}", "Content-Type": "application/json",
                   "Accept": "text/event-stream"}
        try:
            with self._client.stream("POST", self._url, json=body, headers=headers) as resp:
                if resp.status_code in (401, 403):
                    raise DetectorError("auth", f"HTTP {resp.status_code}")
                if resp.status_code == 404:
                    raise DetectorError("model_not_found", "HTTP 404 (check model access)")
                if resp.status_code == 429:
                    raise DetectorError("throttled", "HTTP 429")
                if resp.status_code == 400:
                    raise DetectorError("bad_request", "HTTP 400")
                if resp.status_code >= 400:
                    raise DetectorError("server_error", f"HTTP {resp.status_code}")
                text = _delta_text(resp)
        except httpx.TimeoutException:
            raise DetectorError("timeout") from None
        except httpx.HTTPError:
            raise DetectorError("network") from None
        boxes, fw, fh = parse_boxes(text, self._threshold)
        ms = (time.perf_counter() - started) * 1000
        return DetectionResult(count=len(boxes), boxes=boxes, frame_w=fw, frame_h=fh, detector=self.name,
                               is_real_model=True, latency_ms=ms,
                               note=f"Counted by Meta SAM 3.1 (people with confidence ≥ {self._threshold}).")


def build_detector(env: Optional[Mapping[str, str]] = None) -> Tuple[PersonDetector, Dict[str, Any]]:
    env = os.environ if env is None else env
    requested = (env.get("CROWDGUARD_DETECTOR") or "simulated").strip().lower()
    key = (env.get("META_MODEL_API_KEY") or env.get("MODEL_API_KEY") or "").strip()
    info: Dict[str, Any] = {"requested": requested, "credential_set": bool(key), "note": ""}
    if requested == "sam31":
        if not key:
            info["note"] = "SAM 3.1 was requested but no Meta credential is set; using the simulated detector."
        else:
            try:
                return Sam31Detector(key, float(env.get("SAM_THRESHOLD") or 0.5)), info
            except ValueError as exc:
                info["note"] = f"SAM 3.1 detector not enabled: {exc}. Using the simulated detector."
    elif requested != "simulated":
        info["note"] = f"Unknown detector '{requested}'; using the simulated detector."
    return SimulatedDetector(), info
