"""Provider-independent model-call interface with observability.

Rules: never fabricate measurements (unknown tokens/cost are None); never store raw prompts
(only a SHA-256 and length); LLM output never overwrites engine values.
"""
from __future__ import annotations

import hashlib
import math
import random
import re
import time
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Protocol

from backend.repositories.store import Repos

# provider/model -> (usd per 1M input tokens, usd per 1M output tokens), Standard mode.
# Source: https://dev.meta.ai/docs/pricing-rate-limits, read 2026-10-25 (the page shows no update date).
# Cached-input discounts are not applied, so costs may be slightly overestimated. Estimates only.
PRICING: Dict[str, tuple] = {
    "meta/muse-spark-1.3": (1.25, 4.25),
    "meta/muse-spark-1.2": (1.25, 4.25),
    "meta/muse-spark-1.1": (1.25, 4.25),
}
PRICING_VERSION = "meta-standard-2026-10-25"

# Meta's error-handling guide: retry 429, 500 and 503; do not blindly retry 504 or client errors.
RETRYABLE_CATEGORIES = frozenset({"throttled", "server_error", "overloaded"})
MAX_RETRY_WAIT_S = 10.0


@dataclass
class ProviderResult:
    text: str
    model: str
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None


class ProviderError(Exception):
    def __init__(self, category: str, message: str = "", retry_after: Optional[float] = None,
                 input_tokens: Optional[int] = None, output_tokens: Optional[int] = None):
        super().__init__(message or category)
        self.category = category
        self.retry_after = retry_after
        # Usage the provider reported even though the call failed (it may still be billed).
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


class LLMProvider(Protocol):
    name: str
    model: str

    def generate(self, prompt: str, facts: Dict[str, Any]) -> ProviderResult: ...


class LocalTemplateProvider:
    """Not a model. Renders a fixed template from recorded facts so the app works offline."""
    name = "local-template"
    model = "template-v1"

    def generate(self, prompt: str, facts: Dict[str, Any]) -> ProviderResult:
        f = facts
        text = (f"{f['pandal']}: estimated occupancy {f['occupancy']} of {f['capacity']} "
                f"({f['occupancy_pct']}%), risk {f['risk']}. Baseline forecast: {f['forecast_5']} in 5 min "
                f"and {f['forecast_10']} in 10 min. Data confidence {f['confidence']} ({f['confidence_label']}). "
                f"Triggered rule: {f['rule']}. Values are simulated. This is decision support for "
                f"authorized human review, not an instruction.")
        return ProviderResult(text=text, model=self.model)


_NUM = re.compile(r"\d+(?:\.\d+)?")


def _numbers(obj: Any, acc: Optional[set] = None) -> set:
    acc = set() if acc is None else acc
    if isinstance(obj, bool):
        return acc
    if isinstance(obj, (int, float)):
        for v in (obj, round(obj), round(obj, 1), round(obj, 2)):
            acc.add(float(v))
    elif isinstance(obj, dict):
        for v in obj.values():
            _numbers(v, acc)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            _numbers(v, acc)
    elif isinstance(obj, str):
        for m in _NUM.findall(obj):
            acc.add(float(m))
    return acc


def check_grounded(text: str, facts: Dict[str, Any]) -> Dict[str, Any]:
    """Every number in the generated text must appear in the recorded facts."""
    allowed = _numbers(facts)
    bad = sorted({m for m in _NUM.findall(text) if float(m) not in allowed})
    return {"grounded": not bad, "ungrounded_numbers": bad, "check": "numbers-in-facts-v1"}


def estimate_cost(provider: str, model: str, input_tokens: Optional[int],
                  output_tokens: Optional[int]) -> Optional[float]:
    price = PRICING.get(f"{provider}/{model}")
    if price is None or input_tokens is None or output_tokens is None:
        return None
    return round(input_tokens / 1e6 * price[0] + output_tokens / 1e6 * price[1], 6)


def traced_call(repos: Repos, provider: LLMProvider, operation: str, prompt_version: str, prompt: str,
                facts: Dict[str, Any], run_id: Optional[str] = None, trace_id: Optional[str] = None,
                retries: int = 0, sleep: Callable[[float], None] = time.sleep) -> Dict[str, Any]:
    trace = trace_id or uuid.uuid4().hex
    started = time.time()
    retry_count = 0
    result: Optional[ProviderResult] = None
    err_in: Optional[int] = None
    err_out: Optional[int] = None
    status, category = "success", None
    while True:
        try:
            result = provider.generate(prompt, facts)
            break
        except ProviderError as exc:
            if exc.category in RETRYABLE_CATEGORIES and retry_count < retries:
                if exc.retry_after is not None:
                    delay = exc.retry_after
                else:
                    delay = 0.5 * (2 ** retry_count) + random.random() * 0.5
                if delay <= MAX_RETRY_WAIT_S:  # a long Retry-After means give up, not hang the workflow
                    retry_count += 1
                    sleep(delay)
                    continue
            status, category = "error", exc.category
            err_in, err_out = exc.input_tokens, exc.output_tokens
            break
        except Exception:  # noqa: BLE001
            status, category = "error", "unknown"
            break
    ended = time.time()
    in_tok = result.input_tokens if result else err_in
    out_tok = result.output_tokens if result else err_out
    model = result.model if result else provider.model
    evaluation = check_grounded(result.text, facts) if result else None
    row = repos.llm_calls.create(
        trace_id=trace, run_id=run_id, provider=provider.name, model=model, operation=operation,
        prompt_version=prompt_version, started=started, ended=ended,
        latency_ms=round((ended - started) * 1000, 2), input_tokens=in_tok, output_tokens=out_tok,
        status=status, error_category=category, retry_count=retry_count,
        est_cost_usd=estimate_cost(provider.name, model, in_tok, out_tok), pricing_version=PRICING_VERSION,
        evaluation=evaluation, prompt_sha256=hashlib.sha256(prompt.encode()).hexdigest(),
        prompt_chars=len(prompt))
    return {"call": row, "text": result.text if result else None}


def percentile(values: List[float], p: float) -> Optional[float]:
    if not values:
        return None
    s = sorted(values)
    return s[max(0, math.ceil(p / 100.0 * len(s)) - 1)]


def _summ(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    n = len(rows)
    ok = [r for r in rows if r["status"] == "success"]
    lat = [r["latency_ms"] for r in rows if r["latency_ms"] is not None]
    tin = [r["input_tokens"] for r in rows if r["input_tokens"] is not None]
    tout = [r["output_tokens"] for r in rows if r["output_tokens"] is not None]
    cost = [r["est_cost_usd"] for r in rows if r["est_cost_usd"] is not None]
    return {
        "requests": n, "success": len(ok), "errors": n - len(ok),
        "error_rate": round((n - len(ok)) / n, 4) if n else None,
        "latency_p50_ms": percentile(lat, 50), "latency_p95_ms": percentile(lat, 95),
        "input_tokens": sum(tin) if tin else None, "output_tokens": sum(tout) if tout else None,
        "calls_missing_tokens": n - min(len(tin), len(tout)),
        "est_cost_usd": round(sum(cost), 6) if cost else None, "calls_missing_cost": n - len(cost),
    }


def summary(repos: Repos) -> Dict[str, Any]:
    rows = repos.llm_calls.list(order="id")
    out = _summ(rows)
    for key in ("model", "operation"):
        groups: Dict[str, List[Dict[str, Any]]] = {}
        for r in rows:
            groups.setdefault(str(r[key]), []).append(r)
        out["by_" + key] = {k: _summ(v) for k, v in groups.items()}
    budget = repos.get_setting("llm_budget_usd")
    spent = out["est_cost_usd"]
    out["budget"] = {"limit_usd": budget, "spent_usd": spent,
                     "status": "unknown" if budget is None or spent is None
                     else ("exceeded" if spent > budget else "ok"),
                     "note": "Cost is an estimate and covers only calls with token counts and known pricing."}
    out["pricing_version"] = PRICING_VERSION
    return out


def budget_exceeded(repos: Repos) -> bool:
    """True when a budget is set and the recorded estimated spend has reached it."""
    limit = repos.get_setting("llm_budget_usd")
    if limit is None:
        return False
    row = repos.db.one("SELECT SUM(est_cost_usd) AS spent FROM llm_calls")
    spent = row["spent"] if row else None
    return spent is not None and spent >= limit
