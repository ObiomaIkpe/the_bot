import secrets
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import get_current_user
from app.core.security import hash_service_token
from app.models.broker_credential import BrokerCredential
from app.models.user import User
from app.schemas.broker_credentials import (
    BridgeTokenIssueOut,
    BrokerCredentialCreate,
    BrokerCredentialOut,
    BrokerCredentialUpdate,
)

router = APIRouter(prefix="/broker-credentials", tags=["broker-credentials"])


@router.get("", response_model=list[BrokerCredentialOut])
def list_broker_credentials(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    rows = db.query(BrokerCredential).filter(BrokerCredential.user_id == current_user.user_id).all()
    return [BrokerCredentialOut.from_model(r) for r in rows]


@router.post("", response_model=BrokerCredentialOut, status_code=status.HTTP_201_CREATED)
def create_broker_credential(
    payload: BrokerCredentialCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    cred = BrokerCredential(
        user_id=current_user.user_id,
        broker_name=payload.broker_name,
        server=payload.server,
        account_type=payload.account_type,
    )
    cred.account_login = payload.account_login
    cred.account_password = payload.account_password
    db.add(cred)
    db.commit()
    db.refresh(cred)
    return BrokerCredentialOut.from_model(cred)


@router.patch("/{credential_id}", response_model=BrokerCredentialOut)
def update_broker_credential(
    credential_id: uuid.UUID,
    payload: BrokerCredentialUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    row = (
        db.query(BrokerCredential)
        .filter(BrokerCredential.credential_id == credential_id, BrokerCredential.user_id == current_user.user_id)
        .first()
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Broker credential not found")

    changes = payload.model_dump(exclude_unset=True)
    if not changes:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No fields provided to update")

    for field, value in changes.items():
        setattr(row, field, value)
    db.commit()
    db.refresh(row)
    return BrokerCredentialOut.from_model(row)


@router.post("/{credential_id}/bridge-token", response_model=BridgeTokenIssueOut)
def issue_bridge_token(
    credential_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Mints a new token letting this credential's bridge worker fetch its
    decrypted login/password/server (see app/routers/internal_bridge.py)
    instead of reading them from a local plaintext config.json. Shown
    ONLY here, ONLY once -- never persisted in plaintext, never
    retrievable again (same convention as any API-key-issuance endpoint).

    Calling this again for the same credential ROTATES it: the previous
    token's hash is overwritten, so it stops working immediately. No
    separate revoke endpoint needed.
    """
    row = (
        db.query(BrokerCredential)
        .filter(BrokerCredential.credential_id == credential_id, BrokerCredential.user_id == current_user.user_id)
        .first()
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Broker credential not found")

    token = secrets.token_urlsafe(32)
    row.bridge_fetch_token_hash = hash_service_token(token)
    db.commit()

    return BridgeTokenIssueOut(bridge_token=token)
