"""
Tests for get_active_subscribers() (shadow_runner/persistence.py) --
multi-user fan-out, piece 1. See MULTI_USER_FANOUT_PLAN.md for the full
design; this is the first piece being built, deliberately isolated from
anything that touches real order placement (see the plan's "Open
questions, resolved" section, item 1 -- the live-account transition
path -- for why this stays fully separate from OrderManager/runner.py
until proven).
"""
from app.models import BrokerCredential, ModelConfig, User
from shadow_runner.persistence import get_active_subscribers


def _make_user(db_session, email, is_active=True):
    user = User(email=email, password_hash="x", is_active=is_active)
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


def _make_model_config(db_session, user, model_name="fvg", status="active", magic_number=90001, risk_pct=0.01):
    mc = ModelConfig(
        user_id=user.user_id, model_name=model_name, status=status,
        risk_pct=risk_pct, magic_number=magic_number,
    )
    db_session.add(mc)
    db_session.commit()
    return mc


def _make_broker_credential(db_session, user, is_active=True, bridge_url="http://bridge:9001"):
    bc = BrokerCredential(
        user_id=user.user_id, broker_name="forex.com", server="FOREXcom-Demo",
        account_type="demo", is_active=is_active, bridge_url=bridge_url,
    )
    bc.account_login = "12345"
    bc.account_password = "secret"
    db_session.add(bc)
    db_session.commit()
    return bc


def test_no_subscribers_when_nobody_has_the_model_active(db_session):
    user = _make_user(db_session, "sub_a@example.com")
    _make_model_config(db_session, user, status="disabled", magic_number=90101)
    _make_broker_credential(db_session, user)

    result = get_active_subscribers(db_session, "fvg")
    assert result == []


def test_shadow_status_is_not_a_subscriber(db_session):
    """'shadow' runs detection but must never place real orders --
    matches ModelConfig.status's own documented meaning."""
    user = _make_user(db_session, "sub_b@example.com")
    _make_model_config(db_session, user, status="shadow", magic_number=90102)
    _make_broker_credential(db_session, user)

    result = get_active_subscribers(db_session, "fvg")
    assert result == []


def test_active_model_with_working_bridge_is_a_subscriber(db_session):
    user = _make_user(db_session, "sub_c@example.com")
    _make_model_config(db_session, user, status="active", magic_number=90103, risk_pct=0.02)
    _make_broker_credential(db_session, user, bridge_url="http://bridge-c:9001")

    result = get_active_subscribers(db_session, "fvg")
    assert result == [
        {
            "user_id": user.user_id,
            "bridge_url": "http://bridge-c:9001",
            "magic_number": 90103,
            "risk_pct": 0.02,
        }
    ]


def test_active_model_without_any_broker_credential_is_not_a_subscriber(db_session):
    user = _make_user(db_session, "sub_d@example.com")
    _make_model_config(db_session, user, status="active", magic_number=90104)
    # no BrokerCredential row at all

    result = get_active_subscribers(db_session, "fvg")
    assert result == []


def test_active_model_with_inactive_broker_credential_is_not_a_subscriber(db_session):
    user = _make_user(db_session, "sub_e@example.com")
    _make_model_config(db_session, user, status="active", magic_number=90105)
    _make_broker_credential(db_session, user, is_active=False)

    result = get_active_subscribers(db_session, "fvg")
    assert result == []


def test_active_model_with_no_bridge_url_yet_is_not_a_subscriber(db_session):
    """A broker credential can be active but not yet provisioned with a
    bridge worker -- bridge_url stays null until that happens."""
    user = _make_user(db_session, "sub_f@example.com")
    _make_model_config(db_session, user, status="active", magic_number=90106)
    _make_broker_credential(db_session, user, bridge_url=None)

    result = get_active_subscribers(db_session, "fvg")
    assert result == []


def test_inactive_user_is_not_a_subscriber_even_with_everything_else_active(db_session):
    user = _make_user(db_session, "sub_g@example.com", is_active=False)
    _make_model_config(db_session, user, status="active", magic_number=90107)
    _make_broker_credential(db_session, user)

    result = get_active_subscribers(db_session, "fvg")
    assert result == []


def test_only_returns_subscribers_for_the_requested_model(db_session):
    user = _make_user(db_session, "sub_h@example.com")
    _make_model_config(db_session, user, model_name="fvg", status="active", magic_number=90108)
    _make_broker_credential(db_session, user)

    result = get_active_subscribers(db_session, "some_other_model")
    assert result == []


def test_multiple_subscribers_all_returned(db_session):
    user1 = _make_user(db_session, "sub_i1@example.com")
    _make_model_config(db_session, user1, status="active", magic_number=90109)
    _make_broker_credential(db_session, user1, bridge_url="http://bridge-1:9001")

    user2 = _make_user(db_session, "sub_i2@example.com")
    _make_model_config(db_session, user2, status="active", magic_number=90110, risk_pct=0.015)
    _make_broker_credential(db_session, user2, bridge_url="http://bridge-2:9001")

    result = get_active_subscribers(db_session, "fvg")
    user_ids = {r["user_id"] for r in result}
    assert user_ids == {user1.user_id, user2.user_id}
    assert len(result) == 2
