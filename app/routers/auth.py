import logging

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import get_current_user
from app.core.provisioning import provision_new_user_defaults
from app.core.security import create_access_token, hash_password, verify_password
from app.models.user import User
from app.schemas.auth import PasswordChange, Token, UserOut, UserRegister

logger = logging.getLogger("app.auth")

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def register(payload: UserRegister, db: Session = Depends(get_db)):
    existing = db.query(User).filter(User.email == payload.email).first()
    if existing:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already registered")

    user = User(email=payload.email, password_hash=hash_password(payload.password))
    db.add(user)
    db.commit()
    db.refresh(user)

    # All models are available to every user automatically, scoped per
    # user -- never customer-created (see app/core/provisioning.py).
    # Deliberately a separate step/commit from the user creation above:
    # if this ever fails after its own internal retries, the user still
    # exists and can self-heal via the same idempotent function later
    # (e.g. the backfill script) rather than needing this whole request
    # to be one all-or-nothing transaction.
    provision_new_user_defaults(db, user.user_id)

    logger.info("User registered: user_id=%s", user.user_id)
    return user


@router.post("/login", response_model=Token)
def login(form_data: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    # OAuth2PasswordRequestForm uses 'username' as the field name; we treat
    # it as the email since users log in with email, not a separate handle.
    user = db.query(User).filter(User.email == form_data.username).first()
    if not user or not verify_password(form_data.password, user.password_hash):
        logger.info("Failed login attempt for email=%s", form_data.username)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not user.is_active:
        logger.info("Login rejected for inactive account: user_id=%s", user.user_id)
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Account is inactive")

    token = create_access_token(subject=str(user.user_id))
    logger.info("User logged in: user_id=%s", user.user_id)
    return Token(access_token=token)


@router.get("/me", response_model=UserOut)
def read_current_user(current_user: User = Depends(get_current_user)):
    return current_user


@router.post("/change-password", response_model=UserOut)
def change_password(
    payload: PasswordChange,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not verify_password(payload.current_password, current_user.password_hash):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Current password is incorrect")

    current_user.password_hash = hash_password(payload.new_password)
    db.commit()
    logger.info("Password changed: user_id=%s", current_user.user_id)
    return current_user
