import pytest
from fastapi.testclient import TestClient

from backend.api import vision_routes
from backend.api.app import create_app
from backend.db import Database
from backend.vision.detector import DetectionResult, DetectorError
from tests.backend.conftest import auth_headers

JPEG = b"\xff\xd8\xff\xe0fakejpeg"
NEW = {"name": "Test Association A", "zone_names": ["Gate", "Hall", "Exit"], "max_people": 50}


@pytest.fixture(autouse=True)
def _reset_rate_limit():
    vision_routes._last_call.clear()


def locations(client, h):
    return {loc["name"]: loc for loc in client.get("/api/locations", headers=h).json()}


def test_four_locations_and_seeded_structure(client, admin):
    locs = locations(client, admin)
    assert list(locs) == ["Kolkata South", "Kolkata North", "Kolkata East", "Kolkata West"]
    assert all(loc["pandal_count"] >= 1 for loc in locs.values())
    for p in client.get("/api/pandals", headers=admin).json():
        assert p["location_id"] and len(p["zones"]) == 3 and len(p["cameras"]) == 3
        assert {c["zone_id"] for c in p["cameras"]} == {z["id"] for z in p["zones"]}
        assert all(c["mode"] == "active" for c in p["cameras"])


def test_pandals_filter_by_location(client, admin):
    south = locations(client, admin)["Kolkata South"]
    got = client.get(f"/api/pandals?location_id={south['id']}", headers=admin).json()
    assert len(got) == south["pandal_count"] and all(p["location_id"] == south["id"] for p in got)


def test_create_pandal_needs_unique_name_and_exactly_three_zones(client, admin):
    loc = locations(client, admin)["Kolkata East"]["id"]
    r = client.post(f"/api/locations/{loc}/pandals", json=NEW, headers=admin)
    assert r.status_code == 201
    j = r.json()
    assert [z["name"] for z in j["zones"]] == ["Gate", "Hall", "Exit"] and len(j["cameras"]) == 3
    assert all(z["max_people"] == 50 for z in j["zones"])
    assert client.post(f"/api/locations/{loc}/pandals", json=NEW, headers=admin).status_code == 409
    bad = {**NEW, "name": "B"}
    assert client.post(f"/api/locations/{loc}/pandals", json={**bad, "zone_names": ["a", "b"]}, headers=admin).status_code == 422
    assert client.post(f"/api/locations/{loc}/pandals", json={**bad, "zone_names": ["a", "A", "c"]}, headers=admin).status_code == 422
    assert client.post(f"/api/locations/{loc}/pandals", json={**bad, "zone_names": ["a", " ", "c"]}, headers=admin).status_code == 422
    assert client.post("/api/locations/999/pandals", json=bad, headers=admin).status_code == 404
    op = auth_headers(client, "operator")
    assert client.post(f"/api/locations/{loc}/pandals", json={**bad, "name": "C"}, headers=op).status_code == 403


def test_zone_rename_and_limit_edit(client, admin):
    p = client.get("/api/pandals", headers=admin).json()[0]
    z1, z2 = p["zones"][0], p["zones"][1]
    r = client.put(f"/api/zones/{z1['id']}", json={"name": "Renamed gate", "max_people": 60}, headers=admin)
    assert r.status_code == 200 and r.json()["max_people"] == 60
    assert client.put(f"/api/zones/{z2['id']}", json={"name": "renamed GATE"}, headers=admin).status_code == 409
    assert client.put(f"/api/zones/{z1['id']}", json={"max_people": 0}, headers=admin).status_code == 422
    assert client.put("/api/zones/9999", json={"max_people": 5}, headers=admin).status_code == 404


def test_one_camera_per_zone_and_passive_mode(client, admin):
    p = client.get("/api/pandals", headers=admin).json()[0]
    cam, zone = p["cameras"][0], p["zones"][0]
    again = {"pandal_id": p["id"], "zone_id": zone["id"], "name": "Second", "source_type": "simulated"}
    assert client.post("/api/cameras", json=again, headers=admin).status_code == 409
    assert client.put(f"/api/cameras/{cam['id']}", json={"mode": "passive"}, headers=admin).json()["mode"] == "passive"
    assert client.put(f"/api/cameras/{cam['id']}", json={"mode": "bogus"}, headers=admin).status_code == 422
    r = client.post(f"/api/vision/cameras/{cam['id']}/analyze?demo_truth=3", content=JPEG, headers=admin)
    assert r.status_code == 409
    wall = client.get(f"/api/vision/pandals/{p['id']}/wall", headers=admin).json()
    zw = next(z for z in wall["zones"] if z["zone"]["id"] == zone["id"])
    assert zw["level"] == "passive" and wall["totals"]["cameras_active"] == 2


class Scripted:
    """Paid-looking detector returning a chosen count, or failing."""
    name, is_real_model, price_per_call_usd = "scripted", True, 0.001

    def __init__(self):
        self.count = 0
        self.fail = None

    def detect(self, jpeg, demo_truth=None):
        if self.fail:
            raise DetectorError(self.fail)
        return DetectionResult(count=self.count, boxes=[], detector=self.name, is_real_model=True, latency_ms=5.0)


@pytest.fixture()
def paid(tmp_path):
    det = Scripted()
    client = TestClient(create_app(db_path=str(tmp_path / "w.db"), run_loop=False, seed=True, detector=det))
    return client, auth_headers(client, "admin"), det


def pandal_cams(client, h):
    p = client.get("/api/pandals", headers=h).json()[0]
    return p, [c for z in p["zones"] for c in p["cameras"] if c["zone_id"] == z["id"]]


def test_workflow_run_has_stages_and_levels_raise_deduplicated_incidents(paid):
    client, h, det = paid
    p, cams = pandal_cams(client, h)
    det.count = 36  # critical level is 40 * 0.9 = 36
    r = client.post(f"/api/vision/cameras/{cams[0]['id']}/analyze", content=JPEG, headers=h).json()
    assert r["level"] == "crowded" and r["run_status"] == "succeeded" and len(r["incident_ids"]) == 1
    run = client.get(f"/api/runs/{r['run_id']}", headers=h).json()
    assert [s["name"] for s in run["stages"]] == ["capture", "detect", "persist", "evaluate"]
    assert run["run"]["camera_id"] == cams[0]["id"] and run["run"]["outputs"]["count"] == 36
    vision_routes._last_call.clear()
    r2 = client.post(f"/api/vision/cameras/{cams[0]['id']}/analyze", content=JPEG, headers=h).json()
    assert r2["incident_ids"] == r["incident_ids"]  # same open incident, not a new one
    inc = client.get("/api/incidents", headers=h).json()
    assert len(inc) == 1 and inc[0]["rule"] == "zone_critical" and inc[0]["repeat_count"] == 2
    det.count = 29  # warning level is 28
    vision_routes._last_call.clear()
    r3 = client.post(f"/api/vision/cameras/{cams[1]['id']}/analyze", content=JPEG, headers=h).json()
    assert r3["level"] == "busy"
    det.count = 5
    vision_routes._last_call.clear()
    r4 = client.post(f"/api/vision/cameras/{cams[2]['id']}/analyze", content=JPEG, headers=h).json()
    assert r4["level"] == "quiet" and r4["incident_ids"] == []
    wall = client.get(f"/api/vision/pandals/{p['id']}/wall", headers=h).json()
    t = wall["totals"]
    assert t["count"] == 36 + 29 + 5 and t["cameras_reporting"] == 3 and t["warn"] == 84 and t["critical"] == 108
    assert t["pct_of_threshold"] == pytest.approx(round(100 * 70 / 84, 1))
    assert t["pct_criticality"] == pytest.approx(round(100 * 70 / 108, 1))
    assert wall["sum_series"][-1]["count"] == 70


def test_detector_failure_fails_the_run_shows_no_count_but_is_logged_for_spend(paid):
    client, h, det = paid
    p, cams = pandal_cams(client, h)
    det.fail = "throttled"
    r = client.post(f"/api/vision/cameras/{cams[0]['id']}/analyze", content=JPEG, headers=h).json()
    assert r["status"] == "error" and r["error_category"] == "throttled" and r["count"] is None
    run = client.get(f"/api/runs/{r['run_id']}", headers=h).json()
    assert run["run"]["status"] == "failed" and run["run"]["error_code"] == "DETECTOR_THROTTLED"
    st = client.get("/api/vision/status", headers=h).json()
    assert st["spent_usd"] == pytest.approx(0.001)  # the failed call is still counted as possible spend
    wall = client.get(f"/api/vision/pandals/{p['id']}/wall", headers=h).json()
    assert wall["totals"]["count"] is None and wall["zones"][0]["count"] is None


def test_reset_hides_older_counts_without_deleting_them(paid):
    client, h, det = paid
    p, cams = pandal_cams(client, h)
    det.count = 7
    client.post(f"/api/vision/cameras/{cams[0]['id']}/analyze", content=JPEG, headers=h)
    assert client.get(f"/api/vision/pandals/{p['id']}/wall", headers=h).json()["totals"]["count"] == 7
    assert client.post(f"/api/vision/pandals/{p['id']}/reset", headers=h).status_code == 200
    wall = client.get(f"/api/vision/pandals/{p['id']}/wall", headers=h).json()
    assert wall["totals"]["count"] is None and wall["sum_series"] == [] and wall["reset_ts"]
    rows = client.app.state.ctx.repos.db.one("SELECT COUNT(*) AS n FROM vision_counts")
    assert rows["n"] == 1


def test_stale_counts_are_not_shown_as_current(paid):
    client, h, det = paid
    p, cams = pandal_cams(client, h)
    det.count = 9
    client.post(f"/api/vision/cameras/{cams[0]['id']}/analyze", content=JPEG, headers=h)
    repos = client.app.state.ctx.repos
    repos.db.execute("UPDATE vision_counts SET ts = ts - 300")
    z = client.get(f"/api/vision/pandals/{p['id']}/wall", headers=h).json()["zones"][0]
    assert z["count"] is None and z["stale"] is True and z["level"] == "unknown"


def test_existing_database_is_upgraded_in_place(tmp_path):
    import sqlite3
    path = str(tmp_path / "old.db")
    con = sqlite3.connect(path)
    con.executescript("CREATE TABLE pandals (id INTEGER PRIMARY KEY, name TEXT, capacity INTEGER);"
                      "CREATE TABLE zones (id INTEGER PRIMARY KEY, pandal_id INTEGER, name TEXT, exit_capacity_per_min REAL, active INTEGER);"
                      "CREATE TABLE cameras (id INTEGER PRIMARY KEY, pandal_id INTEGER, name TEXT);"
                      "CREATE TABLE vision_counts (id INTEGER PRIMARY KEY, camera_id INTEGER, pandal_id INTEGER, ts REAL);"
                      "CREATE TABLE runs (run_id TEXT PRIMARY KEY, pandal_id INTEGER, started REAL);"
                      "CREATE TABLE incidents (id INTEGER PRIMARY KEY, pandal_id INTEGER, status TEXT);")
    con.commit()
    con.close()
    db = Database(path)
    for table, col in (("pandals", "location_id"), ("zones", "max_people"), ("cameras", "mode"),
                       ("vision_counts", "run_id"), ("vision_counts", "zone_id")):
        assert col in {r["name"] for r in db.query(f"PRAGMA table_info({table})")}
    Database(path)  # a second start must not fail
