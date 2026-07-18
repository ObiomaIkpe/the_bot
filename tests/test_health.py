from fastapi.testclient import TestClient

from app.core.database import get_db
from app.main import app


def test_health(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_health_returns_503_when_db_unreachable():
    class BrokenSession:
        def execute(self, *args, **kwargs):
            raise RuntimeError("db down")

    def _broken_get_db():
        yield BrokenSession()

    app.dependency_overrides[get_db] = _broken_get_db
    try:
        with TestClient(app) as broken_client:
            resp = broken_client.get("/health")
    finally:
        app.dependency_overrides.pop(get_db, None)

    assert resp.status_code == 503
    assert resp.json() == {"status": "unhealthy"}
