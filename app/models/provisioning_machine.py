"""
Phase 0 of self-service MT5 provisioning (see the plan this was built
from -- broker_credentials rows gaining a provisioning_status). A
"machine" here is a Windows VPS capable of running MT5 terminal copies +
bridge workers -- exactly one exists today (see bridge/README.md's
architecture note), but this is modeled as a real table rather than a
hardcoded single value so a second machine, whenever it exists, is a new
row, not a code change.

Deliberately holds NO relationship to who it serves -- a machine claims
whatever pending broker_credentials job it has capacity for (see
app/routers/internal_provisioning.py), it doesn't belong to a user.
"""
import uuid

from sqlalchemy import Boolean, Column, DateTime, Integer, String, func
from sqlalchemy.dialects.postgresql import UUID

from app.core.database import Base


class ProvisioningMachine(Base):
    __tablename__ = "provisioning_machines"

    machine_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # Human-readable, e.g. "vps-1" -- log/debug-friendly, not used for
    # anything programmatic.
    label = Column(String, nullable=False, unique=True)

    # Explicit, simple capacity guard: this machine's poller only claims
    # a new job while its count of (in_progress + active) credentials is
    # below this. See internal_provisioning.py's claim endpoint.
    max_accounts = Column(Integer, nullable=False)

    is_active = Column(Boolean, nullable=False, server_default="true")

    # SHA-256 hash (app.core.security.hash_service_token) of this
    # machine's poller token -- same one-way, unsalted, indexable-by-hash
    # idiom as broker_credentials.bridge_fetch_token_hash, and for the
    # same reason (a high-entropy generated secret, not a human-chosen
    # password -- see that module's docstring). Nullable because a
    # machine can be registered before its token is minted.
    #
    # Deliberately NOT mintable via any HTTP endpoint reachable by a JWT
    # -- User has no role/admin concept at all today, so there is no
    # correct way to gate "who may create a machine that can see
    # multiple users' plaintext MT5 passwords in flight." Minted only via
    # app/scripts/register_provisioning_machine.py, run by hand.
    machine_token_hash = Column(String, nullable=True, unique=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    def __repr__(self) -> str:
        return f"<ProvisioningMachine label={self.label} max_accounts={self.max_accounts}>"
