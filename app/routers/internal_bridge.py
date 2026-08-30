"""
Bridge-facing, NOT a user endpoint -- deliberately kept in its own file,
separate from app/routers/broker_credentials.py, because the auth story
here is fundamentally different (a per-credential bridge token via a
header, no JWT/get_current_user at all). Keeping it separate makes that
boundary visually obvious and avoids ever accidentally copy-pasting user
auth onto this, or this onto a real user endpoint.

Lets a bridge worker fetch its own account's decrypted login/password/
server once, at its own process startup (see bridge/app/config.py's
fetch_credential()), instead of reading them from a local plaintext
config.json. Part of eliminating that second, unsynced copy of the
secret -- see ADMIN_FRONTEND_PLAN.md / the MT5-credential-flow plan for
the full reasoning.
"""
import logging

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.core.audit import client_ip, commit_audit_or_log, write_audit_log
from app.core.database import get_db
from app.core.security import hash_service_token
from app.models.broker_credential import BrokerCredential
from app.schemas.bridge_credentials import BridgeCredentialSecretOut

logger = logging.getLogger("app.internal_bridge")

router = APIRouter(prefix="/internal", tags=["internal"])


@router.get("/bridge-credentials", response_model=BridgeCredentialSecretOut)
def get_bridge_credentials(
    request: Request,
    x_bridge_token: str = Header(..., alias="X-Bridge-Token"),
    db: Session = Depends(get_db),
):
    """
    x_bridge_token is required with no default, so FastAPI itself
    returns 422 if the header is missing entirely -- deliberately not a
    hand-rolled 401 for that specific case, since "malformed request"
    and "wrong credential" are genuinely different failures worth
    distinguishing in logs/monitoring.

    401 covers both "no credential has this token" and "the matched
    credential is deactivated" -- deliberately the same response for
    both, so this endpoint never leaks which case occurred to whoever's
    holding a bad/revoked token. Audited either way (this is the single
    most security-sensitive endpoint in the system -- it hands back a
    DECRYPTED MT5 login/password/server), success or denial.
    """
    row = (
        db.query(BrokerCredential)
        .filter(BrokerCredential.bridge_fetch_token_hash == hash_service_token(x_bridge_token))
        .first()
    )
    if row is None or not row.is_active:
        # A one-way hash of the presented token is safe to persist (same
        # idiom as bridge_fetch_token_hash itself) and lets repeated
        # attempts against the same dead/guessed token be correlated
        # later, even though the row it might belong to can't be found.
        write_audit_log(
            db, "bridge_credentials_fetch_denied",
            "credential" if row is not None else "unknown",
            actor_id=row.credential_id if row is not None else None,
            actor_label=row.provisioning_account_label if row is not None else None,
            resource_type="broker_credential" if row is not None else None,
            resource_id=row.credential_id if row is not None else None,
            details={
                "reason": "inactive" if row is not None else "not_found",
                "token_hash": hash_service_token(x_bridge_token),
            },
            ip_address=client_ip(request),
        )
        commit_audit_or_log(db, logger)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or inactive bridge token")

    # actor and resource are deliberately the SAME row here -- the
    # credential's own bridge worker is fetching that credential's own
    # secrets, not acting on behalf of anything else.
    write_audit_log(
        db, "bridge_credentials_fetched", "credential",
        actor_id=row.credential_id, actor_label=row.provisioning_account_label,
        resource_type="broker_credential", resource_id=row.credential_id,
        ip_address=client_ip(request),
    )
    db.commit()

    return BridgeCredentialSecretOut(login=row.account_login, password=row.account_password, server=row.server)
