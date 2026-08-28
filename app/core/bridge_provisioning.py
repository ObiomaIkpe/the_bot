"""
Shared logic between the human-facing bridge-token mint endpoint
(app/routers/broker_credentials.py's issue_bridge_token()) and the
internal, machine-facing claim endpoint
(app/routers/internal_provisioning.py) -- both need to mint a fresh
bridge token for a credential, and both must do it identically. Pulled
out here rather than one calling the other, since the two endpoints have
completely different auth stories (JWT + ownership check vs. a machine
token) and shouldn't be coupled beyond this one shared piece.
"""
import secrets

from app.core.security import hash_service_token
from app.models.broker_credential import BrokerCredential


def mint_bridge_token(credential: BrokerCredential) -> str:
    """
    Generates a fresh bridge token, stores only its hash on the
    credential, and returns the plaintext -- shown/returned exactly once
    by the caller, never persisted anywhere in recoverable form. Calling
    this again for the same credential rotates it: the previous token's
    hash is overwritten, so it stops working immediately (no separate
    revoke needed). Does NOT commit -- the caller controls the
    transaction, since both call sites do other work in the same commit
    (create-endpoint does none extra today; the claim endpoint also sets
    provisioning_status/provisioning_machine_id/etc in the same commit).
    """
    token = secrets.token_urlsafe(32)
    credential.bridge_fetch_token_hash = hash_service_token(token)
    return token
