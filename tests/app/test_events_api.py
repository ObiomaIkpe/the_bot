import datetime

from app.models.event import Event


def _register_and_login(client, email):
    client.post("/auth/register", json={"email": email, "password": "a-real-password"})
    resp = client.post("/auth/login", data={"username": email, "password": "a-real-password"})
    return resp.json()["access_token"]


def _auth_header(token):
    return {"Authorization": f"Bearer {token}"}


def test_list_events_returns_only_current_users_events(client, db_session):
    token_a = _register_and_login(client, "a@example.com")
    token_b = _register_and_login(client, "b@example.com")

    user_a_id = client.get("/auth/me", headers=_auth_header(token_a)).json()["user_id"]
    user_b_id = client.get("/auth/me", headers=_auth_header(token_b)).json()["user_id"]

    db_session.add(Event(user_id=user_a_id, model="fvg", event_type="raid_detected", details={}))
    db_session.add(Event(user_id=user_b_id, model="fvg", event_type="raid_detected", details={}))
    db_session.commit()

    resp = client.get("/events", headers=_auth_header(token_a))
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["event_type"] == "raid_detected"


def test_list_events_requires_auth(client):
    resp = client.get("/events")
    assert resp.status_code == 401


def test_list_events_filters_by_model_and_since(client, db_session):
    token = _register_and_login(client, "c@example.com")
    user_id = client.get("/auth/me", headers=_auth_header(token)).json()["user_id"]

    old = Event(
        user_id=user_id, model="fvg", event_type="raid_detected", details={},
        timestamp=datetime.datetime.utcnow() - datetime.timedelta(days=5),
    )
    recent_other_model = Event(user_id=user_id, model="ob", event_type="raid_detected", details={})
    recent_fvg = Event(user_id=user_id, model="fvg", event_type="mss_confirmed", details={})
    db_session.add_all([old, recent_other_model, recent_fvg])
    db_session.commit()

    resp = client.get(
        "/events",
        params={"model": "fvg", "since": (datetime.datetime.utcnow() - datetime.timedelta(days=1)).isoformat()},
        headers=_auth_header(token),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["event_type"] == "mss_confirmed"
