from fastapi.testclient import TestClient

import app.routers.auth as auth_module
from app.core.database import get_db
from app.main import app


def test_unhandled_exception_returns_generic_500(db_session, monkeypatch):
    """Uses its own client with raise_server_exceptions=False -- by default
    TestClient re-raises the underlying exception even when a catch-all
    handler is registered (so ordinary tests still surface real bugs);
    here we specifically want to see the handled response instead."""

    def _boom(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(auth_module, "hash_password", _boom)

    def _get_db_override():
        yield db_session

    app.dependency_overrides[get_db] = _get_db_override
    try:
        with TestClient(app, raise_server_exceptions=False) as c:
            resp = c.post(
                "/auth/register",
                json={"email": "crash@example.com", "password": "a-real-password"},
            )
    finally:
        app.dependency_overrides.pop(get_db, None)

    assert resp.status_code == 500
    assert resp.json() == {"detail": "Internal server error"}
