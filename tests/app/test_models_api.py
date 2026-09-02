"""
Tests for the dynamic model registry: GET /models (any user) and
POST /admin/models (admin-gated) -- see app/models/model.py and
migration 0018 for why this replaced the old hardcoded
ALL_MODEL_NAMES tuple + CHECK constraints. The important case here is
backfill: an EXISTING user must get a new ModelConfig row for a model
registered after they signed up, immediately, with no separate script
run.
"""
from app.models.model_config import ModelConfig
from app.models.user import User


def _register_and_login(client, email):
    client.post("/auth/register", json={"email": email, "password": "a-real-password"})
    resp = client.post("/auth/login", data={"username": email, "password": "a-real-password"})
    return resp.json()["access_token"]


def _auth_header(token):
    return {"Authorization": f"Bearer {token}"}


def _promote(db_session, email):
    user = db_session.query(User).filter(User.email == email).first()
    user.is_admin = True
    db_session.commit()
    return user


def test_get_models_returns_the_three_seeded_models(client):
    token = _register_and_login(client, "models_seed@example.com")
    resp = client.get("/models", headers=_auth_header(token))
    assert resp.status_code == 200
    names = {m["model_name"] for m in resp.json()}
    assert names == {"fvg", "ob", "fvg_ob"}
    # display_name is real, not just an echo of model_name.
    fvg = next(m for m in resp.json() if m["model_name"] == "fvg")
    assert fvg["display_name"] == "FVG"


def test_get_models_requires_auth(client):
    resp = client.get("/models")
    assert resp.status_code == 401


def test_non_admin_cannot_create_model(client, db_session):
    # Every user defaults to is_admin=True as of migration 0019 -- this
    # test needs an explicit demote to still exercise the rejection path.
    token = _register_and_login(client, "models_nonadmin@example.com")
    user = db_session.query(User).filter(User.email == "models_nonadmin@example.com").first()
    user.is_admin = False
    db_session.commit()
    resp = client.post(
        "/admin/models", json={"model_name": "drt", "display_name": "Displacement"},
        headers=_auth_header(token),
    )
    assert resp.status_code == 403


def test_admin_creates_model_and_it_appears_in_get_models(client, db_session):
    token = _register_and_login(client, "models_admin1@example.com")
    _promote(db_session, "models_admin1@example.com")

    resp = client.post(
        "/admin/models", json={"model_name": "drt", "display_name": "Displacement"},
        headers=_auth_header(token),
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["model_name"] == "drt"
    assert body["display_name"] == "Displacement"

    list_resp = client.get("/models", headers=_auth_header(token))
    names = {m["model_name"] for m in list_resp.json()}
    assert "drt" in names


def test_creating_duplicate_model_name_returns_409(client, db_session):
    token = _register_and_login(client, "models_admin2@example.com")
    _promote(db_session, "models_admin2@example.com")

    resp = client.post(
        "/admin/models", json={"model_name": "fvg", "display_name": "Fair Value Gap (again)"},
        headers=_auth_header(token),
    )
    assert resp.status_code == 409


def test_invalid_model_name_returns_422(client, db_session):
    token = _register_and_login(client, "models_admin3@example.com")
    _promote(db_session, "models_admin3@example.com")

    for bad_name in ["Has Spaces", "UPPERCASE", "1starts_with_digit", "has-dash"]:
        resp = client.post(
            "/admin/models", json={"model_name": bad_name, "display_name": "whatever"},
            headers=_auth_header(token),
        )
        assert resp.status_code == 422, f"expected 422 for {bad_name!r}, got {resp.status_code}"


def test_creating_a_model_backfills_existing_users_immediately(client, db_session):
    """The whole point of doing this via the admin UI instead of a
    script: an existing user (registered BEFORE this new model existed)
    must get a real ModelConfig row for it, with a real allocated
    magic_number, the instant it's created -- no backfill script run."""
    existing_token = _register_and_login(client, "models_existing_user@example.com")
    existing_user = db_session.query(User).filter(User.email == "models_existing_user@example.com").first()

    admin_token = _register_and_login(client, "models_admin4@example.com")
    _promote(db_session, "models_admin4@example.com")

    # Confirm the existing user does NOT have this model yet.
    before = (
        db_session.query(ModelConfig)
        .filter(ModelConfig.user_id == existing_user.user_id, ModelConfig.model_name == "drt")
        .first()
    )
    assert before is None

    resp = client.post(
        "/admin/models", json={"model_name": "drt", "display_name": "Displacement"},
        headers=_auth_header(admin_token),
    )
    assert resp.status_code == 201
    assert resp.json()["backfilled_users"] >= 1  # at least models_existing_user + models_admin4 themselves

    db_session.expire_all()
    after = (
        db_session.query(ModelConfig)
        .filter(ModelConfig.user_id == existing_user.user_id, ModelConfig.model_name == "drt")
        .first()
    )
    assert after is not None
    assert after.status == "disabled"
    assert after.magic_number is not None

    # And it shows up for that existing user via the normal API too.
    mc_resp = client.get("/model-configs", headers=_auth_header(existing_token))
    names = {mc["model_name"] for mc in mc_resp.json()}
    assert "drt" in names
