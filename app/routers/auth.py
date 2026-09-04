import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session

from app.core.audit import client_ip, commit_audit_or_log, write_audit_log
from app.core.database import get_db
from app.core.deps import get_current_user
from app.core.provisioning import provision_new_user_defaults
from app.core.security import create_access_token, hash_password, verify_password
from app.models.user import User
from app.schemas.auth import PasswordChange, Token, UserOut, UserRegister

logger = logging.getLogger("app.auth")

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def register(payload: UserRegister, request: Request, db: Session = Depends(get_db)):
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

    # Its own commit, same reasoning as above -- provision_new_user_defaults
    # already committed, so there's nothing left to keep this atomic with.
    #
    # 2026-09-04 write-path audit fix: the user (and their model
    # configs) are already real and durably committed by this point --
    # a failure journaling JUST the user_registered audit row must not
    # 500 a registration that actually succeeded (the account works
    # fine; the user could otherwise get a false "registration failed"
    # and be confused when a retry then 409s as "already registered").
    # Lower stakes than this pass's other fixes (a missing audit-log
    # row for a one-time, self-evident action, not a live trading
    # state), so no alert here -- just don't lie about success.
    try:
        write_audit_log(
            db, "user_registered", "user",
            actor_id=user.user_id, actor_label=user.email,
            resource_type="user", resource_id=user.user_id,
            ip_address=client_ip(request),
        )
        db.commit()
    except Exception:
        db.rollback()
        logger.exception(
            "Journaling user_registered failed for user_id=%s (the account itself already "
            "committed regardless)", user.user_id,
        )

    logger.info("User registered: user_id=%s", user.user_id)
    return user


@router.post("/login", response_model=Token)
def login(request: Request, form_data: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    # OAuth2PasswordRequestForm uses 'username' as the field name; we treat
    # it as the email since users log in with email, not a separate handle.
    user = db.query(User).filter(User.email == form_data.username).first()
    if not user or not verify_password(form_data.password, user.password_hash):
        logger.info("Failed login attempt for email=%s", form_data.username)
        # actor is deliberately "unknown" -- an unverified email/password
        # pair proves nothing about identity -- but resource_type/id is
        # still set when a real user row matched by email, so a bad
        # password against a real account is still traceable to that
        # account after the fact, distinct from a wholly unknown email.
        write_audit_log(
            db, "login_failed", "unknown",
            actor_label=form_data.username,
            resource_type="user" if user else None,
            resource_id=user.user_id if user else None,
            details={"reason": "bad_password" if user else "unknown_email"},
            ip_address=client_ip(request),
        )
        commit_audit_or_log(db, logger)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not user.is_active:
        logger.info("Login rejected for inactive account: user_id=%s", user.user_id)
        write_audit_log(
            db, "login_rejected_inactive", "user",
            actor_id=user.user_id, actor_label=user.email,
            resource_type="user", resource_id=user.user_id,
            ip_address=client_ip(request),
        )
        commit_audit_or_log(db, logger)
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Account is inactive")

    token = create_access_token(subject=str(user.user_id))
    # No other DB mutation happens on a successful login -- this is the
    # only write in this branch, but still follows the fail-loud
    # (bare commit) discipline, not commit_audit_or_log: a successful
    # login is a real action worth being loud about if it can't be
    # journaled, not a deny path where the trail is disposable.
    write_audit_log(
        db, "login_succeeded", "user",
        actor_id=user.user_id, actor_label=user.email,
        resource_type="user", resource_id=user.user_id,
        ip_address=client_ip(request),
    )
    db.commit()
    logger.info("User logged in: user_id=%s", user.user_id)
    return Token(access_token=token)


@router.get("/me", response_model=UserOut)
def read_current_user(current_user: User = Depends(get_current_user)):
    return current_user


@router.post("/change-password", response_model=UserOut)
def change_password(
    payload: PasswordChange,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not verify_password(payload.current_password, current_user.password_hash):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Current password is incorrect")

    current_user.password_hash = hash_password(payload.new_password)
    write_audit_log(
        db, "password_changed", "user",
        actor_id=current_user.user_id, actor_label=current_user.email,
        resource_type="user", resource_id=current_user.user_id,
        ip_address=client_ip(request),
    )
    db.commit()
    logger.info("Password changed: user_id=%s", current_user.user_id)
    return current_user
