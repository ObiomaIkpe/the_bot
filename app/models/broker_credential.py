import uuid

from sqlalchemy import Boolean, CheckConstraint, Column, DateTime, ForeignKey, Index, String, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.core.database import Base
from app.core.security import decrypt_secret, encrypt_secret

# Self-service MT5 provisioning, Phase 0 (schema + internal API only --
# nothing sets a row to "pending" automatically yet; see
# app/routers/internal_provisioning.py and
# app/scripts/register_provisioning_machine.py). "claimed" and "in
# progress" are deliberately one status, not two -- provisioning_claimed_at
# already gives staleness detection for a crashed poller without a
# second status value.
VALID_PROVISIONING_STATUSES = ("not_requested", "pending", "in_progress", "active", "failed")

# Self-service MT5 provisioning, Phase 2. Only meaningful while
# provisioning_status == "in_progress" -- everything else leaves this
# null. Maps 1:1 to the real steps in
# bridge/scripts/provisioning_poller/provisioner.py's provision_account(),
# in order; kept as a fixed vocabulary (not freeform text) so the
# frontend can map each one to real, translatable copy rather than
# displaying whatever string the poller happened to send.
VALID_PROVISIONING_STEPS = (
    "cleaning_up",
    "copying_terminal",
    "launching_and_logging_in",
    "verifying_login",
    "configuring_worker",
    "installing_service",
    "opening_firewall",
    "waiting_for_health",
)


class BrokerCredential(Base):
    __tablename__ = "broker_credentials"

    __table_args__ = (
        # Partial unique index: only rows where is_active is true
        # participate, so a user can have any number of *inactive*
        # credentials but never more than one active one at a time --
        # this is the DB-level backstop for what app/routers/
        # broker_credentials.py's create/update logic already enforces
        # by deactivating others first (radio-button semantics). See
        # migration 0010 for why this needs a data-cleanup step first.
        Index(
            "uq_broker_credentials_one_active_per_user",
            "user_id",
            unique=True,
            postgresql_where=text("is_active"),
        ),
        CheckConstraint(
            "provisioning_status IN ('not_requested', 'pending', 'in_progress', 'active', 'failed')",
            name="ck_broker_credentials_provisioning_status_valid",
        ),
        CheckConstraint(
            "provisioning_step IS NULL OR provisioning_step IN ("
            "'cleaning_up', 'copying_terminal', 'launching_and_logging_in', 'verifying_login', "
            "'configuring_worker', 'installing_service', 'opening_firewall', 'waiting_for_health'"
            ")",
            name="ck_broker_credentials_provisioning_step_valid",
        ),
    )

    credential_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.user_id"), nullable=False, index=True)

    broker_name = Column(String, nullable=False)  # e.g. 'forex.com'

    # Stored encrypted (Fernet, via app.core.security). Never touch these
    # columns directly -- use the .account_login / .account_password
    # properties below, which handle encrypt/decrypt transparently so the
    # plaintext never gets logged or serialized by accident.
    _account_login_enc = Column("account_login_enc", String, nullable=False)
    _account_password_enc = Column("account_password_enc", String, nullable=False)

    server = Column(String, nullable=False)  # e.g. 'FOREXcom-Demo' -- not secret
    account_type = Column(String, nullable=False)  # 'demo' or 'live'

    # A user can have any number of rows here (demo + live, an old
    # broker kept around after switching, a closed/replaced account
    # left inactive rather than deleted, etc) -- but at most one can be
    # active at once, enforced both here (create/update deactivate any
    # other active one first) and at the DB level (see __table_args__
    # above). Exclusive because trading.py's endpoints each answer a
    # singular question -- "what's THE account balance," "what are MY
    # open positions" -- for exactly one real MT5 account; two active
    # credentials would make that ambiguous (whose balance?). Contrast
    # with ModelConfig.status (model_config.py), which is deliberately
    # NOT exclusive: models are independent strategies meant to run in
    # parallel on that one active account, not alternatives to pick
    # between.
    is_active = Column(Boolean, nullable=False, default=True)

    # Which bridge worker (its own process/port/config.json -- see
    # bridge/app/config.py) actually serves this account. Null until
    # someone (currently: manual ops, since MT5 requires a real terminal
    # install on the Windows VPS) has provisioned a worker for it and
    # set this. See migration 0008 for the full reasoning.
    bridge_url = Column(String, nullable=True)

    # SHA-256 hash of a per-credential bridge token -- lets that specific
    # bridge worker fetch this row's decrypted login/password/server once
    # at its own startup (GET /internal/bridge-credentials), instead of
    # reading them from a local plaintext config.json. Only the hash is
    # ever stored; the plaintext token is shown once, at mint time
    # (POST /broker-credentials/{id}/bridge-token), never again. See
    # migration 0009 and app/core/security.py's service-token section for
    # why this is a plain SHA-256 hash, not bcrypt like user passwords.
    bridge_fetch_token_hash = Column(String, nullable=True, unique=True)

    # Self-service provisioning state -- see VALID_PROVISIONING_STATUSES
    # above. "not_requested" (the default) is today's only reachable
    # state via the real create endpoint; the rest are driven entirely
    # by app/routers/internal_provisioning.py's claim/complete/fail
    # endpoints, called by a machine's poller (not built yet -- Phase 1).
    provisioning_status = Column(String, nullable=False, server_default="not_requested")
    # Human-readable failure detail, set by the /fail endpoint. Cleared
    # (set back to null) whenever a job is retried or completes.
    provisioning_error = Column(String, nullable=True)
    # Which machine claimed this job -- null until claimed. Not a
    # relationship() on purpose; nothing needs to traverse from a
    # credential to the full ProvisioningMachine row, just compare IDs
    # (see the ownership check in internal_provisioning.py's
    # complete/fail endpoints).
    provisioning_machine_id = Column(UUID(as_uuid=True), ForeignKey("provisioning_machines.machine_id"), nullable=True)
    # Set at claim time -- lets a stuck job (claimed but never completed
    # or failed, e.g. a poller that crashed mid-job) be recognized by age
    # without a separate status value. No automatic requeue-after-timeout
    # sweep exists yet (deliberately deferred -- see the Phase 0 plan).
    provisioning_claimed_at = Column(DateTime(timezone=True), nullable=True)
    # Deterministic, human-readable folder/service-name component (e.g.
    # "C:\MT5-<this>\", an NSSM service name) -- derived from
    # credential_id at claim time (str(credential_id)[:8]), not chosen by
    # the user. Stable across a retry so a failed job's leftover files
    # are recognizable and safely replaceable, not orphaned under a new
    # name each attempt.
    provisioning_account_label = Column(String, nullable=True)
    # Which real step the poller is on right now -- see
    # VALID_PROVISIONING_STEPS above. Null except while
    # provisioning_status == "in_progress"; not cleared on
    # complete/fail, so a failed job's last-known step stays visible
    # for debugging ("it died during verifying_login").
    provisioning_step = Column(String, nullable=True)

    user = relationship("User", back_populates="broker_credentials")

    @property
    def account_login(self) -> str:
        return decrypt_secret(self._account_login_enc)

    @account_login.setter
    def account_login(self, value: str) -> None:
        self._account_login_enc = encrypt_secret(value)

    @property
    def account_password(self) -> str:
        return decrypt_secret(self._account_password_enc)

    @account_password.setter
    def account_password(self, value: str) -> None:
        self._account_password_enc = encrypt_secret(value)

    def __repr__(self) -> str:
        # Deliberately do NOT include decrypted values -- guards against
        # credentials leaking into logs via an accidental print()/repr().
        return (
            f"<BrokerCredential user_id={self.user_id} broker={self.broker_name} "
            f"account_type={self.account_type}>"
        )
