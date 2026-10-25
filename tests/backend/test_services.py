import pytest

from backend.db import Database
from backend.observability import llm
from backend.repositories.store import Repos
from backend.services.incidents import DEFAULT_RULES, IncidentService, InvalidTransition, candidate_alarms
from backend.workflows.runner import StageError, recover_interrupted, sanitize, start_run


@pytest.fixture()
def repos():
    return Repos(Database(":memory:"))


# ---------------------------------------------------------------- workflow engine
def test_stages_recorded_and_persisted(repos):
    ctx = start_run(repos, 1, None)
    assert ctx.execute("a", lambda: 41 + 1) == 42
    ctx.finish()
    run = repos.runs.get(ctx.run_id)
    stages = repos.stages.list({"run_id": ctx.run_id})
    assert run["status"] == "succeeded" and run["trace_id"] and run["duration_ms"] is not None
    assert [s["status"] for s in stages] == ["succeeded"] and stages[0]["duration_ms"] is not None


def test_failed_stage_is_sanitized_and_fails_run(repos):
    ctx = start_run(repos, 1, None)

    def boom():
        raise StageError("DB_DOWN", r"cannot open E:\secret\path\x.db password=hunter2")

    with pytest.raises(StageError):
        ctx.execute("persist", boom)
    ctx.finish()
    run = repos.runs.get(ctx.run_id)
    assert run["status"] == "failed" and run["error_code"] == "DB_DOWN"
    assert "hunter2" not in run["error_message"] and "secret" not in run["error_message"]


def test_retry_history_and_success(repos):
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise StageError("TEMP", "temporary", retryable=True)
        return "ok"

    ctx = start_run(repos, 1, None)
    assert ctx.execute("fetch", flaky, retries=2) == "ok"
    ctx.finish()
    stages = repos.stages.list({"run_id": ctx.run_id})
    assert [(s["attempt"], s["status"]) for s in stages] == [(1, "failed"), (2, "failed"), (3, "succeeded")]
    assert repos.runs.get(ctx.run_id)["retry_count"] == 2 and repos.runs.get(ctx.run_id)["status"] == "succeeded"


def test_noncritical_failure_marks_partial(repos):
    ctx = start_run(repos, 1, None)
    assert ctx.execute("explain", lambda: (_ for _ in ()).throw(StageError("LLM_ERROR", "x")), critical=False) is None
    ctx.execute("later", lambda: 1)
    ctx.finish()
    assert repos.runs.get(ctx.run_id)["status"] == "partial"


def test_interrupted_runs_recovered(repos):
    ctx = start_run(repos, 1, None)
    assert recover_interrupted(repos) == 1
    assert repos.runs.get(ctx.run_id)["error_code"] == "INTERRUPTED"


def test_sanitize_truncates():
    assert len(sanitize("x" * 1000)) == 200


# ---------------------------------------------------------------- incidents
def _alarm(sev="warning"):
    return {"rule": "occupancy_warning", "severity": sev, "title": "t", "detail": {}, "dedupe_key": "1:occupancy_warning"}


def test_dedup_suppresses_repeats_and_escalates(repos):
    svc = IncidentService(repos)
    inc, new = svc.raise_alarm(1, _alarm(), None)
    inc2, new2 = svc.raise_alarm(1, _alarm(), None)
    assert new and not new2 and inc2["id"] == inc["id"] and inc2["repeat_count"] == 2
    inc3, _ = svc.raise_alarm(1, _alarm("critical"), None)
    assert inc3["severity"] == "critical"
    assert len(repos.incidents.list()) == 1


def test_transitions(repos):
    svc = IncidentService(repos)
    inc, _ = svc.raise_alarm(1, _alarm(), None)
    svc.transition(inc["id"], "acknowledged", "op")
    with pytest.raises(InvalidTransition):
        svc.transition(inc["id"], "assigned", "op")  # assignee required
    svc.transition(inc["id"], "assigned", "op", assignee="ravi")
    done = svc.transition(inc["id"], "resolved", "op", note="cleared")
    assert done["status"] == "resolved" and done["resolved_at"]
    with pytest.raises(InvalidTransition):
        svc.transition(inc["id"], "acknowledged", "op")
    assert [e["to_status"] for e in repos.events.list({"incident_id": inc["id"]})] == [
        "open", "acknowledged", "assigned", "resolved"]
    # a new alarm after resolution opens a fresh incident
    again, new = svc.raise_alarm(1, _alarm(), None)
    assert new and again["id"] != inc["id"]


def test_stale_data_suppresses_occupancy_rules():
    p = {"id": 1, "name": "P"}
    state = {"stale": True, "has_data": True, "age_s": 400, "risk": "critical", "occupancy": 1, "capacity": 1,
             "occupancy_pct": 100}
    alarms = candidate_alarms(p, state, DEFAULT_RULES, 5)
    assert [a["rule"] for a in alarms] == ["stale_data"]


def test_rules_can_be_disabled():
    p = {"id": 1, "name": "P"}
    state = {"stale": False, "has_data": True, "risk": "warning", "occupancy": 750, "capacity": 1000,
             "occupancy_pct": 75, "minutes_to_critical": 3, "bottleneck": None}
    rules = {**DEFAULT_RULES, "occupancy_warning": False}
    assert [a["rule"] for a in candidate_alarms(p, state, rules, 5)] == ["predicted_critical"]


# ---------------------------------------------------------------- LLM observability
class Failing:
    name, model = "fake", "fake-1"

    def generate(self, prompt, facts):
        raise llm.ProviderError("throttled")


class WithTokens:
    name, model = "fake", "fake-2"

    def generate(self, prompt, facts):
        return llm.ProviderResult("occupancy 100", "fake-2", input_tokens=10, output_tokens=5)


FACTS = {"pandal": "A", "occupancy": 100, "capacity": 1000, "occupancy_pct": 10.0, "risk": "normal",
         "forecast_5": 130.0, "forecast_10": 160.0, "confidence": 0.9, "confidence_label": "high",
         "rule": "manual_request", "horizons": [5, 10]}


def test_missing_telemetry_is_null_not_fabricated(repos):
    res = llm.traced_call(repos, llm.LocalTemplateProvider(), "explain", "v1", "secret prompt text", FACTS)
    call = res["call"]
    assert call["status"] == "success" and call["input_tokens"] is None and call["output_tokens"] is None
    assert call["est_cost_usd"] is None and call["pricing_version"] == llm.PRICING_VERSION
    assert call["prompt_sha256"] and call["prompt_chars"] == len("secret prompt text")
    assert "secret prompt text" not in str(call)
    assert call["evaluation"]["grounded"] is True


def test_error_and_retry_recorded(repos):
    res = llm.traced_call(repos, Failing(), "explain", "v1", "p", FACTS, retries=2, sleep=lambda s: None)
    call = res["call"]
    assert call["status"] == "error" and call["error_category"] == "throttled" and call["retry_count"] == 2


def test_cost_only_with_pricing_and_tokens(repos, monkeypatch):
    monkeypatch.setitem(llm.PRICING, "fake/fake-2", (1.0, 2.0))
    call = llm.traced_call(repos, WithTokens(), "x", "v1", "p", FACTS)["call"]
    assert call["input_tokens"] == 10 and call["est_cost_usd"] == pytest.approx(0.00002)


def test_groundedness_detects_invented_numbers():
    assert llm.check_grounded("occupancy 100 of 1000", FACTS)["grounded"]
    bad = llm.check_grounded("occupancy will hit 4321", FACTS)
    assert not bad["grounded"] and "4321" in bad["ungrounded_numbers"]


def test_summary_percentiles_and_budget(repos):
    for _ in range(3):
        llm.traced_call(repos, llm.LocalTemplateProvider(), "explain", "v1", "p", FACTS)
    llm.traced_call(repos, Failing(), "explain", "v1", "p", FACTS)
    repos.set_setting("llm_budget_usd", 1.0)
    s = llm.summary(repos)
    assert s["requests"] == 4 and s["errors"] == 1 and s["error_rate"] == 0.25
    assert s["latency_p95_ms"] >= s["latency_p50_ms"] and s["input_tokens"] is None
    assert s["calls_missing_cost"] == 4 and s["budget"]["status"] == "unknown"
    assert llm.percentile([1, 2, 3, 4], 50) == 2 and llm.percentile([], 50) is None
