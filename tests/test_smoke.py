from fastapi.testclient import TestClient
from backend.main import app

client = TestClient(app)


def test_health_ok():
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json().get("status") == "ok"


def test_docs_and_openapi_present():
    r_docs = client.get("/docs")
    assert r_docs.status_code == 200
    r_openapi = client.get("/openapi.json")
    assert r_openapi.status_code == 200
    assert r_openapi.json().get("openapi")
