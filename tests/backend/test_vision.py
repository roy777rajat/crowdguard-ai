import json

import httpx
import pytest
from fastapi.testclient import TestClient

from backend.api import vision_routes
from backend.api.app import create_app
from backend.vision.detector import (
    DetectionResult,
    DetectorError,
    Sam31Detector,
    SimulatedDetector,
    build_detector,
    parse_boxes,
)
from tests.backend.conftest import auth_headers

JPEG = b"\xff\xd8\xff\xe0fakejpeg"
KEY = "meta_test_secret_value"

# Shaped like the documented grammar: <Nf> ordinal <|box;...|> <|mask;...|>, records comma separated.
SAM_TEXT = ("<0f>0<|box;x1=10;y1=20;x2=110;y2=220;w=640;h=360;c=0.93|><|mask;x=0;y=0;data=122,376,~AAAA|>,"
            "<0f>1<|box;x1=300;y1=40;x2=360;y2=200;w=640;h=360;c=0.31|><|mask;x=0;y=0;data=1,2,~BB|>,"
            "<0f>2<|box;x1=400;y1=50;x2=470;y2=230;w=640;h=360;c=0.77|><|mask;x=0;y=0;data=3,4,~CC|>")


def sse(text, completed=True):
    ev = [{"type": "response.created"}]
    mid = len(text) // 2
    ev += [{"type": "response.output_text.delta", "delta": text[:mid]},
           {"type": "response.output_text.delta", "delta": text[mid:]}]
    if completed:
        ev.append({"type": "response.completed"})
    body = "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in ev)
    return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=body.encode())


def mk(handler):
    return Sam31Detector(KEY, client=httpx.Client(transport=httpx.MockTransport(handler)))


def test_simulated_detector_is_honest():
    d = SimulatedDetector()
    assert d.detect(JPEG).count is None
    r = d.detect(JPEG, demo_truth=7)
    assert r.count == 7 and not r.is_real_model and "not a model result" in r.note


def test_parse_boxes_filters_by_confidence_and_reads_frame_size():
    boxes, w, h = parse_boxes(SAM_TEXT, 0.5)
    assert [b["box"] for b in boxes] == [[10, 20, 110, 220], [400, 50, 470, 230]]
    assert (w, h) == (640, 360) and parse_boxes("", 0.5) == ([], None, None)


def test_sam_request_follows_the_documented_shape_and_counts_confident_people():
    seen = {}

    def handler(req):
        seen["auth"] = req.headers["authorization"]
        seen["accept"] = req.headers["accept"]
        seen["url"] = str(req.url)
        seen["body"] = json.loads(req.read())
        return sse(SAM_TEXT)

    res = mk(handler).detect(JPEG)
    assert res.count == 2 and res.is_real_model and (res.frame_w, res.frame_h) == (640, 360)
    assert seen["auth"] == f"Bearer {KEY}" and seen["accept"] == "text/event-stream"
    assert seen["url"] == "https://api.meta.ai/v1/responses"
    body = seen["body"]
    assert body["model"] == "sam-3.1" and body["stream"] is True
    assert body["metadata"] == {"include_confidence": "true"}
    parts = body["input"][0]["content"]
    assert parts[0] == {"type": "input_text", "text": "person"}
    assert parts[1]["type"] == "input_image" and parts[1]["image_url"].startswith("data:image/jpeg;base64,")


def test_empty_reply_means_zero_people_not_an_error():
    assert mk(lambda r: sse("")).detect(JPEG).count == 0


def test_stream_without_completion_is_an_error_not_a_count():
    with pytest.raises(DetectorError) as exc:
        mk(lambda r: sse(SAM_TEXT, completed=False)).detect(JPEG)
    assert exc.value.category == "incomplete"


@pytest.mark.parametrize("status,category", [(401, "auth"), (404, "model_not_found"), (429, "throttled"),
                                             (400, "bad_request"), (500, "server_error")])
def test_http_errors_are_categorised_without_leaking_the_key(status, category):
    with pytest.raises(DetectorError) as exc:
        mk(lambda r: httpx.Response(status, text="secret " + KEY)).detect(JPEG)
    assert exc.value.category == category and KEY not in str(exc.value)


def test_repr_hides_key_and_blank_key_is_rejected():
    assert KEY not in repr(mk(lambda r: sse("")))
    with pytest.raises(ValueError):
        Sam31Detector("  ")


def test_build_detector_is_opt_in_and_reuses_the_meta_key():
    assert build_detector({})[0].name == "simulated"
    det, info = build_detector({"CROWDGUARD_DETECTOR": "sam31"})
    assert det.name == "simulated" and "no Meta credential" in info["note"]
    det, _ = build_detector({"CROWDGUARD_DETECTOR": "sam31", "META_MODEL_API_KEY": KEY})
    assert det.name == "meta-sam-3.1"


@pytest.fixture(autouse=True)
def _reset_rate_limit():
    vision_routes._last_call.clear()


def cam_id(client, h):
    return client.get("/api/cameras", headers=h).json()[0]["id"]


class FakePaid:
    name, is_real_model, price_per_call_usd = "fake-paid", True, 0.4

    def detect(self, jpeg, demo_truth=None):
        return DetectionResult(count=2, boxes=[{"id": 1, "box": [1, 2, 3, 4], "score": 0.9},
                                               {"id": 2, "box": [5, 6, 7, 8], "score": 0.8}],
                               frame_w=640, frame_h=360, detector=self.name, is_real_model=True)


def test_paid_detector_returns_boxes_and_stops_at_the_budget(tmp_path):
    client = TestClient(create_app(db_path=str(tmp_path / "p.db"), run_loop=False, seed=True, detector=FakePaid()))
    h = auth_headers(client, "admin")
    cid = cam_id(client, h)
    st = client.get("/api/vision/status", headers=h).json()
    assert st["is_real_model"] and st["budget_usd"] == 1.0 and st["spent_usd"] == 0 and st["suggested_interval_s"] >= 10
    j = client.post(f"/api/vision/cameras/{cid}/analyze", content=JPEG, headers=h).json()
    assert j["count"] == 2 and len(j["boxes"]) == 2 and (j["frame_w"], j["frame_h"]) == (640, 360)
    vision_routes._last_call.clear()
    assert client.post(f"/api/vision/cameras/{cid}/analyze", content=JPEG, headers=h).status_code == 200
    vision_routes._last_call.clear()
    r = client.post(f"/api/vision/cameras/{cid}/analyze", content=JPEG, headers=h)  # 0.4 + 0.4 + 0.4 > 1.0
    assert r.status_code == 409 and "VISION_BUDGET_EXCEEDED" in r.json()["detail"]
    assert client.put("/api/settings", json={"vision_budget_usd": 5}, headers=h).status_code == 200
    vision_routes._last_call.clear()
    assert client.post(f"/api/vision/cameras/{cid}/analyze", content=JPEG, headers=h).status_code == 200


def test_demo_animation_is_never_sent_to_the_paid_model_and_costs_nothing(tmp_path):
    class Boom(FakePaid):
        def detect(self, jpeg, demo_truth=None):
            raise AssertionError("the paid detector must not be called for the demo animation")

    client = TestClient(create_app(db_path=str(tmp_path / "d.db"), run_loop=False, seed=True, detector=Boom()))
    h = auth_headers(client, "admin")
    cid = cam_id(client, h)
    j = client.post(f"/api/vision/cameras/{cid}/analyze?demo_truth=9", content=JPEG, headers=h).json()
    assert j["count"] == 9 and j["is_real_model"] is False and j["detector"] == "simulated"
    assert client.get("/api/vision/status", headers=h).json()["spent_usd"] == 0


def test_analyze_demo_feed_saves_count_and_overview_reports_it(client, admin):
    cid = cam_id(client, admin)
    r = client.post(f"/api/vision/cameras/{cid}/analyze?demo_truth=12", content=JPEG, headers=admin)
    assert r.status_code == 200
    j = r.json()
    assert j["count"] == 12 and j["level"] == "quiet" and j["is_real_model"] is False
    assert j["run_id"] and j["run_status"] == "succeeded"
    pid = client.get("/api/cameras", headers=admin).json()[0]["pandal_id"]
    wall = client.get(f"/api/vision/pandals/{pid}/wall", headers=admin).json()
    z = next(z for z in wall["zones"] if z["camera"]["id"] == cid)
    assert z["count"] == 12 and not z["stale"] and z["level"] == "quiet"
    assert z["limits"] == {"max_people": 40, "warn": 28.0, "critical": 36.0}
    assert z["pct_of_threshold"] == pytest.approx(42.9) and z["pct_criticality"] == pytest.approx(33.3)
    assert wall["totals"]["count"] == 12 and wall["sum_series"][-1]["count"] == 12
    st = client.get("/api/vision/status", headers=admin).json()
    assert st["frames_leave_this_computer"] is False and st["budget_usd"] is None


def test_unreadable_video_without_model_gives_no_count_not_zero(client, admin):
    cid = cam_id(client, admin)
    j = client.post(f"/api/vision/cameras/{cid}/analyze", content=JPEG, headers=admin).json()
    assert j["count"] is None and j["level"] == "unknown"


def test_rate_limit_size_and_demo_truth_rules(client, admin):
    cid = cam_id(client, admin)
    assert client.post(f"/api/vision/cameras/{cid}/analyze?demo_truth=1", content=JPEG, headers=admin).status_code == 200
    assert client.post(f"/api/vision/cameras/{cid}/analyze?demo_truth=1", content=JPEG, headers=admin).status_code == 429
    vision_routes._last_call.clear()
    assert client.post(f"/api/vision/cameras/{cid}/analyze", content=b"", headers=admin).status_code == 422
    assert client.post(f"/api/vision/cameras/{cid}/analyze", content=b"x" * 2_000_001, headers=admin).status_code == 413
    vision_routes._last_call.clear()
    client.put(f"/api/cameras/{cid}", json={"source_type": "local_video", "source_ref": "a.mp4"}, headers=admin)
    assert client.post(f"/api/vision/cameras/{cid}/analyze?demo_truth=5", content=JPEG, headers=admin).status_code == 422


def test_permissions_and_scoping(client):
    assert client.post("/api/vision/cameras/1/analyze", content=JPEG).status_code == 401
    aud = auth_headers(client, "auditor")
    assert client.post("/api/vision/cameras/1/analyze", content=JPEG, headers=aud).status_code == 403
    adm = auth_headers(client, "admin")
    pandals = [p["id"] for p in client.get("/api/pandals", headers=adm).json()]
    assert client.get(f"/api/vision/pandals/{pandals[0]}/wall", headers=aud).status_code == 200
    assert client.post(f"/api/vision/pandals/{pandals[0]}/reset", headers=aud).status_code == 403
    mgr = auth_headers(client, "manager")  # scoped to the first pandal only
    assert client.get(f"/api/vision/pandals/{pandals[0]}/wall", headers=mgr).status_code == 200
    assert client.get(f"/api/vision/pandals/{pandals[1]}/wall", headers=mgr).status_code == 403


def test_gallery_serving_is_confined_to_the_gallery_folder(client, admin, tmp_path, monkeypatch):
    monkeypatch.setenv("CROWDGUARD_GALLERY", str(tmp_path))
    (tmp_path / "gate.mp4").write_bytes(b"\x00\x00\x00 ftypmp42")
    (tmp_path / "notes.txt").write_text("x")
    assert [m["name"] for m in client.get("/api/vision/gallery", headers=admin).json()] == ["gate.mp4"]
    assert client.get("/api/vision/gallery/gate.mp4", headers=admin).status_code == 200
    assert client.get("/api/vision/gallery/notes.txt", headers=admin).status_code == 422
    assert client.get("/api/vision/gallery/..%2Fsecret.mp4", headers=admin).status_code in (404, 422)
    assert client.get("/api/vision/gallery/missing.mp4", headers=admin).status_code == 404
    assert client.get("/api/vision/gallery/gate.mp4").status_code == 401


def test_live_stream_camera_needs_http_url_without_credentials(client, admin):
    body = {"pandal_id": 1, "name": "Live", "source_type": "live_stream"}
    assert client.post("/api/cameras", json=body, headers=admin).status_code == 422
    assert client.post("/api/cameras", json={**body, "source_ref": "rtsp://x"}, headers=admin).status_code == 422
    assert client.post("/api/cameras", json={**body, "source_ref": "https://u:p@x.invalid/a"}, headers=admin).status_code == 422
    assert client.post("/api/cameras", json={**body, "source_ref": "https://cam.invalid/a.mp4"}, headers=admin).status_code == 201
