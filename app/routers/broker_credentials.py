import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import get_current_user
from app.models.broker_credential import BrokerCredential
from app.models.user import User
from app.schemas.broker_credentials import BrokerCredentialCreate, BrokerCredentialOut, BrokerCredentialUpdate

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
