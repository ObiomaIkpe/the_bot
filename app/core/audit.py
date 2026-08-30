"""
Single write path for app/models/audit_log.py's AuditLog table --
mirrors shadow_runner/persistence.py's write_event() discipline exactly:
one function builds the row, nothing else should ever instantiate
AuditLog(...) directly.

Two commit disciplines, used at different call sites (see each router
for which applies where):

1. State-mutating success paths (register, login success, password
   change, credential CRUD, provisioning/decommission transitions,
   bridge-fetch success): write_audit_log()'s row is add()ed into the
   SAME transaction as the real mutation's own db.commit(). If that
   commit fails, the whole request 500s and nothing persists -- action
   and audit trail are atomic, so there is never an "action happened
   but the trail didn't" split. This is the correct default: a bug here
   is loud (caught by app/main.py's own
   @app.exception_handler(Exception)), never silent. No special helper
   needed for this path -- just call write_audit_log() before the
   existing db.commit().

2. Deny/failure paths where the audit write is the ONLY thing happening
   (a failed login, a denied bridge-credentials fetch): use
   commit_audit_or_log() below instead of a bare db.commit(). A DB
   hiccup while journaling a failed login must never turn a correct 401
   into an unrelated 500 for a user who just mistyped their password --
   the deny decision itself must always win. This is a narrow,
   deliberate exception to the fail-loud default above, not the general
   rule.
"""
import logging
import uuid

from fastapi import Request
from sqlalchemy.orm import Session

from app.models.audit_log import AuditLog


def write_audit_log(
    db: Session,
    event_type: str,
    actor_type: str,
    *,
    actor_id: uuid.UUID | None = None,
    actor_label: str | None = None,
    resource_type: str | None = None,
    resource_id: uuid.UUID | None = None,
    details: dict | None = None,
    ip_address: str | None = None,
) -> AuditLog:
    """Builds and db.add()s an AuditLog row. Does NOT commit -- caller
    controls the transaction, exactly like write_event() and
    mint_bridge_token() already do. Never pass a raw secret (bridge
    token, machine token, password) in `details` -- only hashes
    (app.core.security.hash_service_token's output) or other
    non-reversible references, same discipline as every other secret in
    this codebase."""
    row = AuditLog(
        audit_id=uuid.uuid4(),
        actor_type=actor_type,
        actor_id=actor_id,
        actor_label=actor_label,
        event_type=event_type,
        resource_type=resource_type,
        resource_id=resource_id,
        details=details or {},
        ip_address=ip_address,
    )
    db.add(row)
    return row


def client_ip(request: Request) -> str | None:
    """Best-effort caller IP. NOT reliable in production as committed:
    this repo has no committed Caddyfile, so whether the reverse proxy
    forwards a real client IP (X-Forwarded-For) vs. this ending up with
    a Docker-network-internal address is not solved here -- centralized
    in one place so that caveat lives in one comment, not one per call
    site."""
    return request.client.host if request.client else None


def commit_audit_or_log(db: Session, logger: logging.Logger) -> None:
    """Used ONLY on deny/failure paths where the audit write is the only
    thing happening in the request (see module docstring, discipline
    #2). Swallows a commit failure -- logs it loudly via
    logger.exception() and rolls back -- so a DB hiccup while journaling
    a denial can never turn the correct deny response into an unrelated
    500. The caller's own HTTPException/401 must still raise regardless
    of whether this succeeds."""
    try:
        db.commit()
    except Exception:
        logger.exception("Failed to persist audit log row for a deny/failure path")
        db.rollback()
