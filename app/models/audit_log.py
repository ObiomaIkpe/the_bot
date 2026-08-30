import uuid

from sqlalchemy import CheckConstraint, Column, DateTime, Index, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID

from app.core.database import Base

# Audit-trail logging, security/identity gap fix. The `events` table
# (app/models/event.py) already gives the trading pipeline excellent,
# disciplined audit coverage -- but its shape is trading-specific
# (`model` CHECK-constrained to fvg/ob/fvg_ob, `is_shadow` meaningless
# outside a trade). This table exists for everything auth- and
# credential-lifecycle-adjacent instead: register/login/password-change,
# broker credential create/update/remove/token-rotation, and the
# machine-token-authenticated provisioning/decommission job transitions
# -- none of which have anywhere close to a "model"/"is_shadow" concept,
# and all of which had ZERO durable audit trail before this table
# existed (see HANDOFF.md's logging/audit review for the full gap
# analysis this was built from).
#
# Deliberately a SEPARATE table from `events`, not an extension of it --
# forcing these two genuinely different concerns (what did the trading
# pipeline detect/do vs. who did what to an account/credential/job) into
# one table would mean either a `model` column that's meaningless most
# of the time, or weakening that column's CHECK constraint for
# everyone. Two disciplined, narrowly-scoped tables beat one
# overloaded one.
VALID_ACTOR_TYPES = (
    # A human, authenticated via JWT (app.core.deps.get_current_user).
    "user",
    # A VPS poller, authenticated via a machine token
    # (app/routers/internal_provisioning.py's get_current_machine).
    # Claims/completes/fails provisioning and decommission jobs -- can
    # act on ANY user's pending job, not just one, so this actor kind
    # is deliberately distinct from "user".
    "machine",
    # A bridge worker, authenticated via a per-credential bridge token
    # (app/routers/internal_bridge.py) -- acting on behalf of exactly
    # one BrokerCredential row, fetching its own decrypted secrets.
    "credential",
    # The actor could not be verified at all -- e.g. a failed login (the
    # attempted email doesn't prove anyone), or a bridge-credentials
    # fetch whose token matched no row. actor_id stays null in this
    # case; actor_label carries whatever unverified identity was
    # presented, if anything, for forensic value only.
    "unknown",
)

# One event type per meaningfully distinct transition, grouped by the
# router that emits it. Deliberately NOT a DB-level CHECK constraint --
# same reasoning as Event.event_type's own comment: new event types are
# likely as this audit trail's coverage grows, and VALID_EVENT_TYPES over
# in event.py is never enforced at runtime either, only documented. This
# tuple is the source of truth at the app layer.
VALID_AUDIT_EVENT_TYPES = (
    # -- app/routers/auth.py --
    "user_registered",
    "login_succeeded",
    # Covers BOTH unknown-email and bad-password -- matches the existing
    # merged-401 response's own "never leak which case occurred" posture.
    # See details={"reason": ...} at the call site for which one.
    "login_failed",
    # Distinct from login_failed: credentials were correct, the account
    # is deliberately disabled. Worth knowing apart from a bad password.
    "login_rejected_inactive",
    "password_changed",
    # -- app/routers/broker_credentials.py --
    "broker_credential_created",
    "broker_credential_updated",
    # Two distinct types, not one -- mirrors the state machine's own
    # real distinction between an immediate removal (nothing was ever
    # provisioned) and a queued teardown (a machine must tear down real
    # VPS resources). See remove_broker_credential()'s own docstring.
    "broker_credential_removed",
    "broker_credential_decommission_requested",
    # One type covers both first-issue and rotation -- see
    # details={"rotated": ...} at the call site.
    "bridge_token_issued",
    # -- app/routers/internal_bridge.py (the most security-sensitive
    # endpoint in the system: hands a bridge worker a live account's
    # DECRYPTED login/password/server) --
    "bridge_credentials_fetched",
    # Covers both failure sub-cases (token matched nothing / matched an
    # inactive credential) -- see details={"reason": ...}.
    "bridge_credentials_fetch_denied",
    # -- app/routers/internal_provisioning.py -- claim/complete/fail
    # only; report_provisioning_step is deliberately NOT audited here,
    # it's operational telemetry already captured on the row itself via
    # provisioning_step, not a security-relevant transition.
    "provisioning_job_claimed",
    "provisioning_job_completed",
    "provisioning_job_failed",
    # -- app/routers/internal_decommission.py -- same shape/reasoning as
    # provisioning above.
    "decommission_job_claimed",
    "decommission_job_completed",
    "decommission_job_failed",
)


class AuditLog(Base):
    """
    See module docstring for why this is a separate table from `events`.

    Write path is app/core/audit.py's write_audit_log() -- exactly one
    function builds these rows, mirroring shadow_runner/persistence.py's
    write_event() discipline. Nothing else should ever instantiate
    AuditLog(...) directly.
    """

    __tablename__ = "audit_log"

    audit_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    timestamp = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    actor_type = Column(String, nullable=False)

    # NOT a real foreign key -- can point at users.user_id,
    # provisioning_machines.machine_id, or broker_credentials.credential_id
    # depending on actor_type, and a single FK column can't span three
    # tables. Null when the actor couldn't be verified at all (see
    # actor_type == "unknown").
    actor_id = Column(UUID(as_uuid=True), nullable=True)

    # Denormalized, human-readable actor identity -- a user's email, a
    # machine's `label`, or a credential's `provisioning_account_label`.
    # Also the ONLY field populated when actor_id is null (e.g. the
    # attempted email on a failed login) -- kept for forensic/readability
    # value even though it isn't a stable, unique identifier.
    actor_label = Column(String, nullable=True)

    event_type = Column(String, nullable=False, index=True)

    # Same "not a real FK, spans multiple tables" reasoning as actor_id.
    # Values used today: ("user", <user_id>) and
    # ("broker_credential", <credential_id>).
    resource_type = Column(String, nullable=True)
    resource_id = Column(UUID(as_uuid=True), nullable=True)

    # Catch-all, same convention as Event.details -- deliberately generic
    # so new fields never require a schema change. NEVER put a raw
    # secret in here (bridge token, machine token, password) -- only
    # hashes (e.g. hash_service_token's output) or otherwise
    # non-reversible references, same discipline as every other secret
    # in this codebase.
    details = Column(JSONB, nullable=False, default=dict, server_default="{}")

    # Best-effort, via request.client.host. NOT reliable in production
    # as committed: this repo has no committed Caddyfile, so whether the
    # reverse proxy forwards a real client IP (X-Forwarded-For) vs. this
    # column ending up with a Docker-network-internal address is not
    # something solved here -- flagged as a follow-up once Caddy's real
    # config is available to audit.
    ip_address = Column(String, nullable=True)

    __table_args__ = (
        CheckConstraint(
            "actor_type IN ('user', 'machine', 'credential', 'unknown')",
            name="ck_audit_log_actor_type_valid",
        ),
        # Composite indexes for the two ways this table actually gets
        # queried later (no read endpoint exists yet, but a direct-SQL
        # incident review needs these): "everything this actor did" and
        # "everything that happened to this resource." A plain
        # single-column timestamp index covers "what happened recently"
        # -- unlike `events`, there's no single user_id to scope that
        # query by first.
        Index("ix_audit_log_actor", "actor_type", "actor_id"),
        Index("ix_audit_log_resource", "resource_type", "resource_id"),
        Index("ix_audit_log_timestamp", "timestamp"),
    )
