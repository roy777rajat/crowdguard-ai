"""Meta Model API provider, using the Responses API.

Built only on interfaces documented at https://dev.meta.ai/docs (read on 2026-10-25):
  * POST https://api.meta.ai/v1/responses with a bearer token
  * request fields: model, instructions, input, max_output_tokens (min 16), store, reasoning.effort
  * response: status, output_text (optional) or output[] items of type "message" holding
    "output_text" parts, and usage.input_tokens / usage.output_tokens / usage.total_tokens
  * error body: {"error": {"message", "type", "param", "code"}}; retry 429, 500 and 503, honour
    Retry-After, do not blindly retry 504
Only standard-mode Muse Spark models are allowed. "Contributor" variants are rejected because their
data-use terms have not been reviewed.

The credential is never logged, never stored, and never included in an exception message.
Prompts are not stored either: observability keeps only a hash and a length.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import httpx

from backend.observability.llm import ProviderError, ProviderResult

DEFAULT_BASE_URL = "https://api.meta.ai/v1"
DEFAULT_MODEL = "muse-spark-1.3"
ALLOWED_MODELS = ("muse-spark-1.3", "muse-spark-1.2", "muse-spark-1.1")
REASONING_EFFORTS = ("minimal", "low", "medium", "high", "xhigh")

_STATUS_CATEGORY = {
    400: "invalid_request",
    401: "authentication",
    402: "billing",
    403: "forbidden",
    404: "not_found",
    413: "payload_too_large",
    429: "throttled",
    500: "server_error",
    503: "overloaded",
    504: "gateway_timeout",
}


def _as_int(value: Any) -> Optional[int]:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _retry_after(resp: httpx.Response) -> Optional[float]:
    raw = resp.headers.get("Retry-After")
    if raw is None:
        return None
    try:
        return max(0.0, float(raw))
    except ValueError:
        return None


def _error_detail(resp: httpx.Response) -> str:
    try:
        err = resp.json().get("error") or {}
        return f"{err.get('type')}/{err.get('code')}"
    except (ValueError, AttributeError):
        return ""


def _extract_text(data: Dict[str, Any]) -> str:
    direct = data.get("output_text")
    if isinstance(direct, str) and direct.strip():
        return direct.strip()
    chunks: list[str] = []
    refused = False
    for item in data.get("output") or []:
        if not isinstance(item, dict) or item.get("type") != "message":
            continue  # skip reasoning and other item types
        for part in item.get("content") or []:
            if not isinstance(part, dict):
                continue
            if part.get("type") == "output_text" and isinstance(part.get("text"), str):
                chunks.append(part["text"])
            elif part.get("type") == "refusal":
                refused = True
    text = "".join(chunks).strip()
    if text:
        return text
    raise ProviderError("refusal" if refused else "empty_response", "no usable text in response")


@dataclass
class ToolCall:
    call_id: str
    name: str
    arguments: str  # raw JSON text exactly as the model produced it; the caller validates it


@dataclass
class ToolTurn:
    """One model response in a tool-calling exchange."""
    output_items: List[Dict[str, Any]]
    text: Optional[str]
    calls: List[ToolCall] = field(default_factory=list)
    model: str = ""
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None


def parse_tool_turn(data: Dict[str, Any], fallback_model: str) -> ToolTurn:
    """Read a Responses API reply that may contain `function_call` items. Raises ProviderError if malformed."""
    raw_usage = data.get("usage")
    usage: Dict[str, Any] = raw_usage if isinstance(raw_usage, dict) else {}
    in_tok, out_tok = _as_int(usage.get("input_tokens")), _as_int(usage.get("output_tokens"))
    status = data.get("status")
    if status not in (None, "completed"):
        raise ProviderError("incomplete" if status == "incomplete" else "failed", f"status {status}",
                            input_tokens=in_tok, output_tokens=out_tok)
    items = data.get("output")
    if not isinstance(items, list):
        raise ProviderError("bad_response", "response has no output list", input_tokens=in_tok, output_tokens=out_tok)
    clean = [i for i in items if isinstance(i, dict)]
    calls: List[ToolCall] = []
    for item in clean:
        if item.get("type") == "function_call":
            cid, name, args = item.get("call_id"), item.get("name"), item.get("arguments")
            if not isinstance(cid, str) or not isinstance(name, str) or not isinstance(args, str):
                raise ProviderError("bad_response", "malformed function_call item",
                                    input_tokens=in_tok, output_tokens=out_tok)
            calls.append(ToolCall(cid, name, args))
    text: Optional[str] = None
    if not calls:
        try:
            text = _extract_text(data)
        except ProviderError as exc:
            exc.input_tokens, exc.output_tokens = in_tok, out_tok
            raise
    return ToolTurn(output_items=clean, text=text, calls=calls, model=str(data.get("model") or fallback_model),
                    input_tokens=in_tok, output_tokens=out_tok)


class MetaModelProvider:
    name = "meta"

    def __init__(self, api_key: str, model: str = DEFAULT_MODEL, timeout_s: float = 30.0,
                 max_output_tokens: int = 1500, reasoning_effort: str = "minimal",
                 client: Optional[httpx.Client] = None):
        if not api_key or not api_key.strip():
            raise ValueError("a Meta Model API credential is required")
        if model not in ALLOWED_MODELS:
            raise ValueError("model must be one of: " + ", ".join(ALLOWED_MODELS))
        if reasoning_effort not in REASONING_EFFORTS:
            raise ValueError("reasoning_effort must be one of: " + ", ".join(REASONING_EFFORTS))
        if max_output_tokens < 16:
            raise ValueError("max_output_tokens must be at least 16")
        self.model = model
        self._api_key = api_key.strip()
        self._max_output_tokens = max_output_tokens
        self._effort = reasoning_effort
        self._client = client or httpx.Client(timeout=timeout_s)

    def __repr__(self) -> str:  # never include the credential
        return f"MetaModelProvider(model={self.model!r})"

    def respond_with_tools(self, instructions: str, input_items: List[Dict[str, Any]],
                           tools: List[Dict[str, Any]]) -> ToolTurn:
        """One step of native function calling on the Responses API (flat function tools, tool_choice auto).

        Stateless on purpose (store=false, same as `generate`): the caller replays the previous output items
        and `function_call_output` items. Replaying reasoning items needs reasoning.encrypted_content.
        """
        body: Dict[str, Any] = {
            "model": self.model, "instructions": instructions, "input": input_items,
            "max_output_tokens": self._max_output_tokens, "store": False,
            "reasoning": {"effort": self._effort}, "include": ["reasoning.encrypted_content"],
        }
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"
        data = self._post(body)
        return parse_tool_turn(data, self.model)

    def _post(self, body: Dict[str, Any]) -> Dict[str, Any]:
        headers = {"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"}
        try:
            resp = self._client.post(f"{DEFAULT_BASE_URL}/responses", headers=headers, json=body)
        except httpx.TimeoutException:
            raise ProviderError("timeout", "request timed out") from None
        except httpx.HTTPError:
            raise ProviderError("network", "could not reach the Meta Model API") from None
        if resp.status_code != 200:
            category = _STATUS_CATEGORY.get(resp.status_code, "server_error" if resp.status_code >= 500
                                            else "http_error")
            raise ProviderError(category, f"HTTP {resp.status_code} {_error_detail(resp)}".strip(),
                                retry_after=_retry_after(resp))
        try:
            data = resp.json()
        except ValueError:
            raise ProviderError("bad_response", "response was not JSON") from None
        if not isinstance(data, dict):
            raise ProviderError("bad_response", "unexpected response shape")
        return data

    def generate(self, prompt: str, facts: Dict[str, Any]) -> ProviderResult:
        body = {
            "model": self.model,
            "instructions": prompt,
            "input": "Recorded values (JSON):\n" + json.dumps(facts, sort_keys=True, default=str),
            "max_output_tokens": self._max_output_tokens,
            "store": False,  # stateless: ask Meta not to keep the response
            "reasoning": {"effort": self._effort},
        }
        headers = {"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"}
        try:
            resp = self._client.post(f"{DEFAULT_BASE_URL}/responses", headers=headers, json=body)
        except httpx.TimeoutException:
            raise ProviderError("timeout", "request timed out") from None
        except httpx.HTTPError:
            raise ProviderError("network", "could not reach the Meta Model API") from None

        if resp.status_code != 200:
            category = _STATUS_CATEGORY.get(resp.status_code, "server_error" if resp.status_code >= 500
                                            else "http_error")
            raise ProviderError(category, f"HTTP {resp.status_code} {_error_detail(resp)}".strip(),
                                retry_after=_retry_after(resp))
        try:
            data = resp.json()
        except ValueError:
            raise ProviderError("bad_response", "response was not JSON") from None
        if not isinstance(data, dict):
            raise ProviderError("bad_response", "unexpected response shape")

        # Read usage first: Meta bills for a call even when its reply is unusable, and that must stay visible.
        raw_usage = data.get("usage")
        usage: Dict[str, Any] = raw_usage if isinstance(raw_usage, dict) else {}
        in_tok = _as_int(usage.get("input_tokens"))
        out_tok = _as_int(usage.get("output_tokens"))

        status = data.get("status")
        if status not in (None, "completed"):
            raise ProviderError("incomplete" if status == "incomplete" else "failed", f"status {status}",
                                input_tokens=in_tok, output_tokens=out_tok)
        try:
            text = _extract_text(data)
        except ProviderError as exc:
            exc.input_tokens, exc.output_tokens = in_tok, out_tok
            raise
        return ProviderResult(text=text, model=str(data.get("model") or self.model),
                              input_tokens=in_tok, output_tokens=out_tok)
