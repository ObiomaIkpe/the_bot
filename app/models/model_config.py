"""
Phase 4: one row per (user, model) -- replaces user_settings'
live_model/shadow_models columns, which assumed exactly one live model
per user. Confirmed design (this phase's chat history): every model
runs independently, detects its own setups, and manages its own trades;
there is no single "the live model" anymore, just a set of models each
individually in one of three states.

Deliberately NOT exclusive like BrokerCredential.is_active (see that
column's own comment) -- multiple models can be "active" for the same
user at once, on purpose. A broker credential answers "which single
real account is this," which only makes sense as one at a time; a
model answers "which of my strategies may trade on that account right
now," and MT5 itself already supports several strategies running
concurrently on one account (that's what magic_number distinguishes
them by -- see trading.py's module docstring). Different question,
different shape of constraint.
"""
import uuid

from sqlalchemy import Boolean, CheckConstraint, Column, Float, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.core.database import Base

# 'disabled': no streaming pipeline exists yet for this model, or it's
#   deliberately turned off. Nothing runs.
# 'shadow': a real streaming pipeline exists and runs continuously,
#   journaling every signal -- same as Phase 3's FVG shadow mode. No
#   real orders.
# 'active': a real streaming pipeline exists and runs, AND its
#   trade_candidate_ready events trigger real orders via the bridge.
#   The only state that touches real money (even demo).
#
# New models always start 'disabled' until their streaming pipeline is
# built (mirrors how FVG itself only started running in Phase 3, long
# after Phase 1 built it). A model moves 'shadow' -> 'active' only via
# an explicit, deliberate status change -- never automatically, and
# never as a side effect of a migration. Same philosophy as the
# bridge's orders_enabled kill switch: default OFF, explicit opt-in.
VALID_MODEL_STATUSES = ("disabled", "shadow", "active")

# Every user gets one ModelConfig row per models.model_name,
# automatically (see app/core/provisioning.py) -- never customer-
# created, but always present, scoped per user. Adding a new model is
# now just an admin-UI action (POST /admin/models -- see
# app/models/model.py) that inserts a `models` row and immediately
# backfills a ModelConfig row for every existing user; new
# registrations pick it up automatically the same way they always have.
# This used to be a hardcoded ALL_MODEL_NAMES tuple here plus two
# separate hardcoded CHECK constraints on trades.model/events.model,
# requiring a migration + 4 manual steps per model -- see migration
# 0018 for the cutover to a real `models` table and FK constraints
# instead, if that history matters for something.
#
# bridge/scripts/provision_account.ps1 fetches these rows' magic_number
# values via GET /model-configs when setting up a new account's bridge
# worker, rather than an operator re-deriving or guessing one -- see
# that script's own header comment for why, and its "Known limitation"
# note on why only one of an account's several magic numbers ends up in
# the bridge worker's config.json.


class ModelConfig(Base):
    __tablename__ = "model_configs"

    config_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.user_id"), nullable=False, index=True)

    model_name = Column(String, ForeignKey("models.model_name"), nullable=False)
    status = Column(String, nullable=False, server_default="disabled")
    risk_pct = Column(Float, nullable=False)

    # MT5's magic field is a plain integer at the protocol level (not an
    # application choice -- see this phase's chat history for why text
    # isn't possible there). Globally unique across the whole system,
    # not just per user, so /positions filtering is always unambiguous
    # even once a second real account (the friend's) exists.
    magic_number = Column(Integer, nullable=False, unique=True)

    # Nullable = no cap (current confirmed design: "infinite, per
    # model" -- see this phase's chat history). Present as a real column
    # rather than hardcoded so a future model CAN be given a real cap
    # without another migration.
    max_concurrent_positions = Column(Integer, nullable=True)

    # Per-model pause, distinct from UserSettings.is_paused (the
    # account-wide emergency stop -- see that column's own docstring in
    # user_settings.py). This one stops just THIS model without
    # affecting any others the user runs. Both are checked fresh on
    # every trade candidate; either one being true blocks a real order.
    is_paused = Column(Boolean, nullable=False, server_default="false")

    user = relationship("User", back_populates="model_configs")

    __table_args__ = (
        CheckConstraint(
            "status IN ('disabled', 'shadow', 'active')", name="ck_model_configs_status_valid"
        ),
        UniqueConstraint("user_id", "model_name", name="uq_model_configs_user_model"),
    )