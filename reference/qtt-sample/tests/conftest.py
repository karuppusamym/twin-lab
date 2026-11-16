import sys
from pathlib import Path

import pytest

SAMPLE = Path(__file__).resolve().parents[1]
# This sample's `app` package, not another one on the path (the repo also has
# reference/case-workbench/backend/app).
sys.path.insert(0, str(SAMPLE))
for name in [m for m in sys.modules if m == "app" or m.startswith("app.")]:
    del sys.modules[name]

from fastapi.testclient import TestClient  # noqa: E402

from app.main import create_app  # noqa: E402
from app.seed import TEST_PASSWORD  # noqa: E402


@pytest.fixture
def qtt(tmp_path):
    return create_app(tmp_path / "qtt.db")


def signed_in(app, username: str) -> TestClient:
    client = TestClient(app)
    response = client.post("/api/login", json={"username": username, "password": TEST_PASSWORD})
    assert response.status_code == 200, response.text
    client.headers["Authorization"] = f"Bearer {response.json()['token']}"
    return client


@pytest.fixture
def analyst1(qtt):
    return signed_in(qtt, "analyst1")


@pytest.fixture
def analyst2(qtt):
    return signed_in(qtt, "analyst2")


@pytest.fixture
def reviewer(qtt):
    return signed_in(qtt, "reviewer1")
