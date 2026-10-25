from tests.backend.conftest import auth_headers

PANDAL = {"name": "Test Pandal", "location": "Somewhere", "capacity": 500, "warn_pct": 0.6, "critical_pct": 0.8}


def start(client, h, scenario, ticks):
    r = client.post("/api/simulation/start", json={"scenario": scenario, "speed": 1}, headers=h)
    assert r.status_code == 200
    for _ in range(ticks):
        assert client.post("/api/simulation/tick", headers=h).status_code == 200


def test_health_is_public_and_honest(client):
    j = client.get("/api/health").json()
    assert j["status"] == "ok" and j["data_source"] == "simulated" and "evaluation" in j["auth"]


def test_protected_routes_need_auth(client):
    for path in ("/api/pandals", "/api/dashboard", "/api/incidents", "/api/runs", "/api/audit"):
        assert client.get(path).status_code == 401
    assert client.get("/api/pandals", headers={"Authorization": "Bearer nope"}).status_code == 401


def test_login_failure_and_register(client):
    assert client.post("/api/auth/login", json={"username": "admin", "password": "wrong"}).status_code == 401
    r = client.post("/api/auth/register", json={"username": "newbie", "password": "longenough1"})
    assert r.status_code == 201 and r.json()["user"]["role"] == "auditor"
    assert client.post("/api/auth/register", json={"username": "newbie", "password": "longenough1"}).status_code == 409
    assert client.post("/api/auth/register", json={"username": "x", "password": "short"}).status_code == 422


def test_pandal_crud_validation_and_audit(client, admin):
    r = client.post("/api/pandals", json=PANDAL, headers=admin)
    assert r.status_code == 201
    pid = r.json()["id"]
    assert client.post("/api/pandals", json=PANDAL, headers=admin).status_code == 409
    assert client.post("/api/pandals", json={**PANDAL, "name": "B", "capacity": -5}, headers=admin).status_code == 422
    assert client.post("/api/pandals", json={**PANDAL, "name": "C", "warn_pct": 0.9, "critical_pct": 0.5},
                       headers=admin).status_code == 422
    assert client.put(f"/api/pandals/{pid}", json={"capacity": 900}, headers=admin).json()["capacity"] == 900
    assert client.put(f"/api/pandals/{pid}", json={"warn_pct": 0.95}, headers=admin).status_code == 422
    assert client.delete(f"/api/pandals/{pid}", headers=admin).status_code == 200
    names = [p["name"] for p in client.get("/api/pandals", headers=admin).json()]
    assert "Test Pandal" not in names
    assert "Test Pandal" in [p["name"] for p in client.get("/api/pandals?include_inactive=true", headers=admin).json()]
    actions = [a["action"] for a in client.get("/api/audit", headers=admin).json()]
    assert {"pandal.create", "pandal.update", "pandal.deactivate", "login"} <= set(actions)
    assert client.get("/api/pandals/9999", headers=admin).status_code == 404


def test_camera_crud_and_credential_rejection(client, admin):
    pid = client.get("/api/pandals", headers=admin).json()[0]["id"]
    body = {"pandal_id": pid, "name": "Cam X", "source_type": "simulated"}
    r = client.post("/api/cameras", json=body, headers=admin)
    assert r.status_code == 201 and r.json()["status"] == "registered"
    cid = r.json()["id"]
    bad = {"pandal_id": pid, "name": "V", "source_type": "local_video", "source_ref": "rtsp://user:pw@10.0.0.1/s"}
    assert client.post("/api/cameras", json=bad, headers=admin).status_code == 422
    assert client.post("/api/cameras", json={**bad, "source_ref": None}, headers=admin).status_code == 422
    ok = {**bad, "source_ref": "videos/demo.mp4"}
    assert client.post("/api/cameras", json=ok, headers=admin).status_code == 201
    assert client.post("/api/cameras", json={**body, "zone_id": 9999}, headers=admin).status_code == 422
    assert client.put(f"/api/cameras/{cid}", json={"name": "Renamed"}, headers=admin).json()["name"] == "Renamed"
    assert client.delete(f"/api/cameras/{cid}", headers=admin).status_code == 200
    cams = client.get(f"/api/cameras?pandal_id={pid}", headers=admin).json()
    assert [c for c in cams if c["id"] == cid][0]["active"] == 0
    assert all("connection_note" in c for c in cams)


def test_authorization_boundaries(client, admin):
    op, mgr, aud = (auth_headers(client, u) for u in ("operator", "manager", "auditor"))
    assert client.post("/api/pandals", json=PANDAL, headers=op).status_code == 403
    assert client.post("/api/pandals", json=PANDAL, headers=mgr).status_code == 403
    assert client.post("/api/simulation/start", json={}, headers=mgr).status_code == 403
    assert client.post("/api/simulation/start", json={}, headers=aud).status_code == 403
    assert client.get("/api/audit", headers=op).status_code == 403
    assert client.get("/api/llm/summary", headers=mgr).status_code == 403
    assert client.put("/api/settings", json={"max_data_age_s": 60}, headers=op).status_code == 403
    # manager only sees authorized pandal(s)
    all_ids = [p["id"] for p in client.get("/api/pandals", headers=admin).json()]
    mine = [p["id"] for p in client.get("/api/pandals", headers=mgr).json()]
    assert mine == all_ids[:1] and len(all_ids) > 1
    assert client.get(f"/api/pandals/{all_ids[0]}", headers=mgr).status_code == 200
    assert client.get(f"/api/pandals/{all_ids[1]}", headers=mgr).status_code == 403
    assert client.get(f"/api/pandals/{all_ids[1]}/series", headers=mgr).status_code == 403
    assert client.post(f"/api/pandals/{all_ids[1]}/explain", headers=mgr).status_code == 403
    dash = client.get("/api/dashboard", headers=mgr).json()
    assert [c["pandal"]["id"] for c in dash["pandals"]] == mine and dash["llm"] is None


def test_end_to_end_surge_creates_runs_forecasts_and_incidents(client, admin):
    op = auth_headers(client, "operator")
    start(client, op, "arrival_surge", 16)
    dash = client.get("/api/dashboard", headers=admin).json()
    assert dash["simulation"]["scenario"] == "arrival_surge" and dash["data_notice"]
    card = dash["pandals"][0]
    st = card["state"]
    assert st["source"] == "simulated" and set(st["forecast"]) == {"1", "3", "5", "10"} or set(st["forecast"]) == {1, 3, 5, 10}
    assert st["risk"] in ("warning", "critical") and card["active_incidents"] >= 1
    runs = client.get("/api/runs", headers=admin).json()
    n_pandals = len(client.get("/api/pandals", headers=admin).json())
    assert len(runs) == 16 * n_pandals and all(r["trace_id"] for r in runs)
    run_with_inc = next(r for r in runs if r["incident_ids"])
    detail = client.get(f"/api/runs/{run_with_inc['run_id']}", headers=admin).json()
    names = [s["name"] for s in detail["stages"]]
    assert names[:5] == ["receive", "validate", "persist", "forecast", "evaluate"]
    assert detail["incidents"] and detail["forecasts"] and detail["measurements"]
    # AI explanation linked to the run, grounded, and never overrides engine values
    with_llm = [r for r in runs if client.get(f"/api/runs/{r['run_id']}", headers=admin).json()["llm_calls"]]
    assert with_llm
    summ = client.get("/api/llm/summary", headers=admin).json()
    assert summ["requests"] >= 1 and summ["input_tokens"] is None
    ex = client.post(f"/api/pandals/{card['pandal']['id']}/explain", headers=admin).json()
    assert ex["call"]["evaluation"]["grounded"] and "human review" in ex["disclaimer"]
    series = client.get(f"/api/pandals/{card['pandal']['id']}/series", headers=admin).json()
    assert len(series["points"]) == 16 and series["critical_threshold"] > series["warn_threshold"]


def test_stale_sensor_flow(client, admin):
    op = auth_headers(client, "operator")
    start(client, op, "stale_sensor", 10)
    runs = client.get("/api/runs?status=failed", headers=admin).json()
    assert runs and all(r["error_code"] == "NO_DATA" for r in runs)
    inc = client.get("/api/incidents", headers=admin).json()
    assert any(i["rule"] == "stale_data" for i in inc)
    assert not any(i["rule"].startswith("occupancy") for i in inc)
    card = client.get("/api/dashboard", headers=admin).json()["pandals"][0]
    assert card["state"]["stale"] is True and card["state"]["quality"]["label"] in ("low", "none")
    cams = client.get("/api/cameras", headers=admin).json()
    assert any(c["status"] == "stale" for c in cams)
    stage = client.get(f"/api/runs/{runs[0]['run_id']}", headers=admin).json()["stages"]
    assert stage[0]["status"] == "failed" and stage[0]["error_code"] == "NO_DATA"


def test_pause_resume_reset(client):
    op = auth_headers(client, "operator")
    start(client, op, "normal_flow", 2)
    assert client.post("/api/simulation/pause", headers=op).json()["running"] is False
    assert client.post("/api/simulation/resume", headers=op).json()["running"] is True
    r = client.post("/api/simulation/reset", headers=op).json()
    assert r["running"] is False and r["minute"] == 0
    d = client.get("/api/dashboard", headers=op).json()["pandals"][0]["state"]
    assert d["has_data"] is False and d["status"] == "no_data"
    assert client.post("/api/simulation/start", json={"scenario": "bogus"}, headers=op).status_code == 422


def test_incident_lifecycle_via_api(client, admin):
    op, aud = auth_headers(client, "operator"), auth_headers(client, "auditor")
    start(client, op, "arrival_surge", 14)
    inc = client.get("/api/incidents?status=open", headers=op).json()[0]
    iid = inc["id"]
    assert client.post(f"/api/incidents/{iid}/transition", json={"to": "acknowledged"}, headers=aud).status_code == 403
    assert client.post(f"/api/incidents/{iid}/transition", json={"to": "acknowledged"}, headers=op).status_code == 200
    assert client.post(f"/api/incidents/{iid}/transition", json={"to": "assigned"}, headers=op).status_code == 409
    assert client.post(f"/api/incidents/{iid}/transition", json={"to": "assigned", "assignee": "Ravi"},
                       headers=op).json()["assignee"] == "Ravi"
    assert client.post(f"/api/incidents/{iid}/notes", json={"note": "Marshals sent"}, headers=op).status_code == 201
    assert client.post(f"/api/incidents/{iid}/transition", json={"to": "resolved", "note": "ok"}, headers=op).status_code == 200
    assert client.post(f"/api/incidents/{iid}/transition", json={"to": "acknowledged"}, headers=op).status_code == 409
    assert client.post(f"/api/incidents/{iid}/transition", json={"to": "bogus"}, headers=op).status_code == 422
    detail = client.get(f"/api/incidents/{iid}", headers=op).json()
    assert [e["action"] for e in detail["events"]][-1] == "resolved" and "note" in [e["action"] for e in detail["events"]]
    assert client.get("/api/incidents/99999", headers=op).status_code == 404


def test_history_survives_restart(tmp_path):
    from fastapi.testclient import TestClient

    from backend.api.app import create_app
    path = str(tmp_path / "persist.db")
    c1 = TestClient(create_app(db_path=path, run_loop=False))
    op = auth_headers(c1, "operator")
    start(c1, op, "normal_flow", 3)
    n = len(c1.get("/api/runs", headers=op).json())
    c2 = TestClient(create_app(db_path=path, run_loop=False))
    op2 = auth_headers(c2, "operator")
    assert len(c2.get("/api/runs", headers=op2).json()) == n == 3 * len(c2.get("/api/pandals", headers=op2).json())
    assert c2.get("/api/simulation", headers=op2).json()["running"] is False


def test_settings_metrics_and_validation(client, admin):
    s = client.get("/api/settings", headers=admin).json()
    assert s["model_integration"]["is_real_model"] is False and s["model_integration"]["credentials_required"] is False
    assert "key" not in str(s).lower().replace("keyword", "")
    assert client.put("/api/settings", json={"max_data_age_s": -1}, headers=admin).status_code == 422
    assert client.put("/api/settings", json={"max_data_age_s": 60, "llm_budget_usd": 5}, headers=admin).json()["max_data_age_s"] == 60
    m = client.get("/api/system/metrics", headers=admin).json()
    assert m["api"]["requests"] > 0 and m["storage"]["sqlite"] == "ok"
