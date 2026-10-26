import os

import pytest
from fastapi.testclient import TestClient

from backend.api.app import create_app
from backend.services.seed import DEMO_PASSWORDS

# Tests must never reach a real model API, whatever the developer's .env says.
os.environ["CROWDGUARD_PROVIDER"] = "local"
os.environ["CROWDGUARD_DETECTOR"] = "simulated"
os.environ["CROWDGUARD_VOICE"] = "off"


@pytest.fixture()
def client(tmp_path):
    app = create_app(db_path=str(tmp_path / "test.db"), run_loop=False, seed=True)
    return TestClient(app)


def auth_headers(client, username):
    r = client.post("/api/auth/login", json={"username": username, "password": DEMO_PASSWORDS[username]})
    assert r.status_code == 200, r.text
    return {"Authorization": "Bearer " + r.json()["token"]}


@pytest.fixture()
def admin(client):
    return auth_headers(client, "admin")
