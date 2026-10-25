"""Meta adapter tests. No network: every request is answered by an in-process httpx mock transport."""
import json

import httpx
import pytest

from backend.config import build_provider, load_dotenv
from backend.db import Database
from backend.integrations.meta_adapter import MetaModelProvider
from backend.observability import llm
from backend.observability.llm import LocalTemplateProvider, ProviderError
from backend.observability.prompts import EXPLAIN_INSTRUCTIONS
from backend.repositories.store import Repos

SECRET = "test-secret-credential-123"
FACTS = {"pandal": "A", "occupancy": 100, "capacity": 1000, "occupancy_pct": 10.0, "risk": "normal",
         "forecast_5": 130.0, "forecast_10": 160.0, "confidence": 0.9, "confidence_label": "high",
         "rule": "manual_request", "horizons": [5, 10]}

OK_BODY = {
    "id": "resp_1", "object": "response", "status": "completed", "model": "muse-spark-1.3",
    "output": [
        {"id": "r1", "type": "reasoning", "content": []},
        {"id": "m1", "type": "message", "role": "assistant", "status": "completed",
         "content": [{"type": "output_text", "text": "Occupancy is 100 of 1000, simulated.", "annotations": []}]},
    ],
    "usage": {"input_tokens": 42, "output_tokens": 97, "total_tokens": 139},
}


def make(handler, **kw):
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return MetaModelProvider(SECRET, client=client, **kw)


def ok_handler(request):
    return httpx.Response(200, json=OK_BODY)


def test_request_matches_documented_shape():
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["auth"] = request.headers["authorization"]
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json=OK_BODY)

    res = make(handler).generate(EXPLAIN_INSTRUCTIONS, FACTS)
    assert seen["url"] == "https://api.meta.ai/v1/responses"
    assert seen["auth"] == f"Bearer {SECRET}"
    body = seen["body"]
    assert body["model"] == "muse-spark-1.3" and body["store"] is False
    assert body["instructions"] == EXPLAIN_INSTRUCTIONS
    assert body["reasoning"] == {"effort": "minimal"} and body["max_output_tokens"] == 1500
    assert json.loads(body["input"].split("\n", 1)[1]) == FACTS
    assert res.text == "Occupancy is 100 of 1000, simulated."
    assert (res.input_tokens, res.output_tokens) == (42, 97)


def test_output_text_helper_is_preferred():
    body = {**OK_BODY, "output_text": "Direct text 100."}
    res = make(lambda r: httpx.Response(200, json=body)).generate("p", FACTS)
    assert res.text == "Direct text 100."


def test_missing_usage_stays_none_not_zero():
    body = {k: v for k, v in OK_BODY.items() if k != "usage"}
    res = make(lambda r: httpx.Response(200, json=body)).generate("p", FACTS)
    assert res.input_tokens is None and res.output_tokens is None
    bad = {**OK_BODY, "usage": {"input_tokens": "42", "output_tokens": True}}
    res2 = make(lambda r: httpx.Response(200, json=bad)).generate("p", FACTS)
    assert res2.input_tokens is None and res2.output_tokens is None


@pytest.mark.parametrize("status,category", [
    (400, "invalid_request"), (401, "authentication"), (402, "billing"), (403, "forbidden"),
    (404, "not_found"), (413, "payload_too_large"), (429, "throttled"), (500, "server_error"),
    (503, "overloaded"), (504, "gateway_timeout"), (502, "server_error"), (418, "http_error"),
])
def test_http_errors_are_categorised(status, category):
    body = {"error": {"message": f"details with {SECRET}", "type": "x", "param": None, "code": "y"}}
    with pytest.raises(ProviderError) as exc:
        make(lambda r: httpx.Response(status, json=body, headers={"Retry-After": "7"})).generate("p", FACTS)
    assert exc.value.category == category
    assert SECRET not in str(exc.value)  # server-echoed text is never copied into our errors
    assert exc.value.retry_after == 7.0


def test_timeout_network_and_bad_body():
    def slow(request):
        raise httpx.ReadTimeout("slow", request=request)

    def down(request):
        raise httpx.ConnectError("down", request=request)

    with pytest.raises(ProviderError) as e1:
        make(slow).generate("p", FACTS)
    with pytest.raises(ProviderError) as e2:
        make(down).generate("p", FACTS)
    with pytest.raises(ProviderError) as e3:
        make(lambda r: httpx.Response(200, content=b"not json")).generate("p", FACTS)
    assert (e1.value.category, e2.value.category, e3.value.category) == ("timeout", "network", "bad_response")


def test_incomplete_refusal_and_empty_outputs_are_errors():
    cases = [
        ({**OK_BODY, "status": "incomplete"}, "incomplete"),
        ({**OK_BODY, "status": "failed"}, "failed"),
        ({**OK_BODY, "output": [{"type": "message", "content": [{"type": "refusal", "refusal": "no"}]}]}, "refusal"),
        ({**OK_BODY, "output": [{"type": "reasoning"}]}, "empty_response"),
    ]
    for body, category in cases:
        with pytest.raises(ProviderError) as exc:
            make(lambda r, b=body: httpx.Response(200, json=b)).generate("p", FACTS)
        assert exc.value.category == category


def test_constructor_guards_and_no_secret_in_repr():
    p = MetaModelProvider(SECRET)
    assert SECRET not in repr(p) and SECRET not in str(vars(p).get("model"))
    with pytest.raises(ValueError):
        MetaModelProvider("  ")
    with pytest.raises(ValueError):
        MetaModelProvider(SECRET, model="muse-spark-1.3-contributor")  # data-use terms not reviewed
    with pytest.raises(ValueError):
        MetaModelProvider(SECRET, model="not-a-model")
    with pytest.raises(ValueError):
        MetaModelProvider(SECRET, reasoning_effort="none")  # documented as unsupported
    with pytest.raises(ValueError):
        MetaModelProvider(SECRET, max_output_tokens=5)


# ---------------------------------------------------------------- observability integration
@pytest.fixture()
def repos():
    return Repos(Database(":memory:"))


def test_failed_but_billed_call_keeps_its_token_usage_and_cost(repos):
    cut_off = {**OK_BODY, "status": "incomplete", "output": [{"type": "reasoning"}],
               "usage": {"input_tokens": 500, "output_tokens": 600, "total_tokens": 1100}}
    call = llm.traced_call(repos, make(lambda r: httpx.Response(200, json=cut_off)), "x", "v", "p", FACTS)["call"]
    assert call["status"] == "error" and call["error_category"] == "incomplete"
    assert (call["input_tokens"], call["output_tokens"]) == (500, 600)
    assert call["est_cost_usd"] == pytest.approx(500 / 1e6 * 1.25 + 600 / 1e6 * 4.25, abs=1e-6)
    empty = {**OK_BODY, "output": [{"type": "reasoning"}], "usage": {"input_tokens": 7, "output_tokens": 9}}
    call2 = llm.traced_call(repos, make(lambda r: httpx.Response(200, json=empty)), "x", "v", "p", FACTS)["call"]
    assert call2["error_category"] == "empty_response" and call2["output_tokens"] == 9


def test_traced_call_records_tokens_and_estimated_cost(repos):
    res = llm.traced_call(repos, make(ok_handler), "explain_incident", "v", EXPLAIN_INSTRUCTIONS, FACTS)
    call = res["call"]
    assert call["provider"] == "meta" and call["status"] == "success"
    assert (call["input_tokens"], call["output_tokens"]) == (42, 97)
    assert call["est_cost_usd"] == pytest.approx(42 / 1e6 * 1.25 + 97 / 1e6 * 4.25, abs=1e-6)
    assert call["pricing_version"] == llm.PRICING_VERSION
    assert call["evaluation"]["grounded"] is True
    assert SECRET not in json.dumps(call, default=str)


def test_ungrounded_model_output_is_flagged(repos):
    body = {**OK_BODY, "output_text": "Occupancy will reach 4321 soon."}
    call = llm.traced_call(repos, make(lambda r: httpx.Response(200, json=body)), "x", "v", "p", FACTS)["call"]
    assert call["evaluation"]["grounded"] is False and "4321" in call["evaluation"]["ungrounded_numbers"]


def test_retry_honours_retry_after_then_succeeds(repos):
    calls = {"n": 0}
    sleeps = []

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(503, json={"error": {"type": "server_error", "code": "service_overloaded"}},
                                  headers={"Retry-After": "2"})
        return httpx.Response(200, json=OK_BODY)

    call = llm.traced_call(repos, make(handler), "x", "v", "p", FACTS, retries=2, sleep=sleeps.append)["call"]
    assert call["status"] == "success" and call["retry_count"] == 1 and sleeps == [2.0]


def test_very_long_retry_after_is_not_waited_for(repos):
    sleeps = []
    handler = lambda r: httpx.Response(503, headers={"Retry-After": "60"}, json={})  # noqa: E731
    call = llm.traced_call(repos, make(handler), "x", "v", "p", FACTS, retries=3, sleep=sleeps.append)["call"]
    assert call["status"] == "error" and call["error_category"] == "overloaded"
    assert call["retry_count"] == 0 and sleeps == []


def test_client_errors_and_gateway_timeouts_are_not_retried(repos):
    for status in (401, 402, 400, 504):
        sleeps = []
        handler = lambda r, s=status: httpx.Response(s, json={})  # noqa: E731
        call = llm.traced_call(repos, make(handler), "x", "v", "p", FACTS, retries=3, sleep=sleeps.append)["call"]
        assert call["retry_count"] == 0 and sleeps == [], status


def test_budget_blocks_further_real_calls(repos):
    assert llm.budget_exceeded(repos) is False
    repos.set_setting("llm_budget_usd", 0.0004)
    assert llm.budget_exceeded(repos) is False  # nothing spent yet
    llm.traced_call(repos, make(ok_handler), "x", "v", "p", FACTS)  # about $0.000465
    assert llm.budget_exceeded(repos) is True


# ---------------------------------------------------------------- configuration
def test_default_is_local_and_needs_no_credentials():
    provider, info = build_provider({})
    assert isinstance(provider, LocalTemplateProvider) and info["credential_set"] is False


def test_meta_requires_explicit_opt_in_even_with_a_credential():
    provider, info = build_provider({"META_MODEL_API_KEY": SECRET})
    assert isinstance(provider, LocalTemplateProvider) and info["credential_set"] is True


def test_meta_selected_only_with_flag_and_credential():
    provider, _ = build_provider({"CROWDGUARD_PROVIDER": "meta", "META_MODEL_API_KEY": SECRET})
    assert isinstance(provider, MetaModelProvider) and provider.model == "muse-spark-1.3"
    alt, _ = build_provider({"CROWDGUARD_PROVIDER": "meta", "MODEL_API_KEY": SECRET, "META_MODEL": "muse-spark-1.2"})
    assert isinstance(alt, MetaModelProvider) and alt.model == "muse-spark-1.2"


def test_misconfiguration_falls_back_to_local_with_a_note():
    p1, i1 = build_provider({"CROWDGUARD_PROVIDER": "meta"})
    p2, i2 = build_provider({"CROWDGUARD_PROVIDER": "meta", "META_MODEL_API_KEY": SECRET, "META_MODEL": "bogus"})
    p3, i3 = build_provider({"CROWDGUARD_PROVIDER": "banana"})
    for p, i in ((p1, i1), (p2, i2), (p3, i3)):
        assert isinstance(p, LocalTemplateProvider) and i["note"]
    assert SECRET not in json.dumps([i1, i2, i3])


def test_dotenv_loader_does_not_override_real_environment(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text('# comment\n\nTEST_CG_A=one\nTEST_CG_B="two"\nTEST_CG_C=from-file\nnot a pair\n', encoding="utf-8-sig")
    for k in ("TEST_CG_A", "TEST_CG_B"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("TEST_CG_C", "from-environment")
    assert load_dotenv(env) is True
    import os
    assert os.environ["TEST_CG_A"] == "one" and os.environ["TEST_CG_B"] == "two"
    assert os.environ["TEST_CG_C"] == "from-environment"
    monkeypatch.delenv("TEST_CG_A")
    monkeypatch.delenv("TEST_CG_B")
    assert load_dotenv(tmp_path / "missing.env") is False


def test_settings_endpoint_never_exposes_the_credential(tmp_path):
    from fastapi.testclient import TestClient

    from backend.api.app import create_app
    from backend.services.seed import DEMO_PASSWORDS
    app = create_app(db_path=str(tmp_path / "s.db"), run_loop=False, provider=make(ok_handler))
    c = TestClient(app)
    tok = c.post("/api/auth/login", json={"username": "admin", "password": DEMO_PASSWORDS["admin"]}).json()["token"]
    s = c.get("/api/settings", headers={"Authorization": f"Bearer {tok}"}).json()
    assert s["model_integration"]["active_provider"] == "meta" and s["model_integration"]["is_real_model"] is True
    assert SECRET not in json.dumps(s)
