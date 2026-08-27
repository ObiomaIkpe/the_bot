def test_register_creates_user(client):
    resp = client.post(
        "/auth/register",
        json={"email": "a@example.com", "password": "a-real-password"},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["email"] == "a@example.com"
    assert body["is_active"] is True
    assert "user_id" in body


def test_register_duplicate_email_conflicts(client):
    payload = {"email": "dup@example.com", "password": "a-real-password"}
    client.post("/auth/register", json=payload)
    resp = client.post("/auth/register", json=payload)
    assert resp.status_code == 409


def test_login_success_returns_token(client):
    client.post(
        "/auth/register",
        json={"email": "login@example.com", "password": "a-real-password"},
    )
    resp = client.post(
        "/auth/login",
        data={"username": "login@example.com", "password": "a-real-password"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["token_type"] == "bearer"
    assert body["access_token"]


def test_login_wrong_password_rejected(client):
    client.post(
        "/auth/register",
        json={"email": "wrongpw@example.com", "password": "a-real-password"},
    )
    resp = client.post(
        "/auth/login",
        data={"username": "wrongpw@example.com", "password": "not-the-password"},
    )
    assert resp.status_code == 401


def test_login_unknown_user_rejected(client):
    resp = client.post(
        "/auth/login",
        data={"username": "nobody@example.com", "password": "whatever"},
    )
    assert resp.status_code == 401


def test_me_requires_token(client):
    resp = client.get("/auth/me")
    assert resp.status_code == 401


def test_me_returns_current_user_with_valid_token(client):
    client.post(
        "/auth/register",
        json={"email": "me@example.com", "password": "a-real-password"},
    )
    login_resp = client.post(
        "/auth/login",
        data={"username": "me@example.com", "password": "a-real-password"},
    )
    token = login_resp.json()["access_token"]

    resp = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert resp.json()["email"] == "me@example.com"


def test_change_password_requires_auth(client):
    resp = client.post(
        "/auth/change-password",
        json={"current_password": "a-real-password", "new_password": "a-new-password"},
    )
    assert resp.status_code == 401


def test_change_password_wrong_current_password_rejected(client):
    client.post(
        "/auth/register",
        json={"email": "pwchange_a@example.com", "password": "a-real-password"},
    )
    login_resp = client.post(
        "/auth/login",
        data={"username": "pwchange_a@example.com", "password": "a-real-password"},
    )
    token = login_resp.json()["access_token"]

    resp = client.post(
        "/auth/change-password",
        headers={"Authorization": f"Bearer {token}"},
        json={"current_password": "not-the-password", "new_password": "a-new-password"},
    )
    assert resp.status_code == 400


def test_change_password_too_short_rejected(client):
    client.post(
        "/auth/register",
        json={"email": "pwchange_b@example.com", "password": "a-real-password"},
    )
    login_resp = client.post(
        "/auth/login",
        data={"username": "pwchange_b@example.com", "password": "a-real-password"},
    )
    token = login_resp.json()["access_token"]

    resp = client.post(
        "/auth/change-password",
        headers={"Authorization": f"Bearer {token}"},
        json={"current_password": "a-real-password", "new_password": "short"},
    )
    assert resp.status_code == 422


def test_change_password_succeeds_and_old_password_stops_working(client):
    client.post(
        "/auth/register",
        json={"email": "pwchange_c@example.com", "password": "a-real-password"},
    )
    login_resp = client.post(
        "/auth/login",
        data={"username": "pwchange_c@example.com", "password": "a-real-password"},
    )
    token = login_resp.json()["access_token"]

    resp = client.post(
        "/auth/change-password",
        headers={"Authorization": f"Bearer {token}"},
        json={"current_password": "a-real-password", "new_password": "a-new-password"},
    )
    assert resp.status_code == 200
    assert resp.json()["email"] == "pwchange_c@example.com"

    old_login = client.post(
        "/auth/login",
        data={"username": "pwchange_c@example.com", "password": "a-real-password"},
    )
    assert old_login.status_code == 401

    new_login = client.post(
        "/auth/login",
        data={"username": "pwchange_c@example.com", "password": "a-new-password"},
    )
    assert new_login.status_code == 200
