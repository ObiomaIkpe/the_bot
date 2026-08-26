"""
Four distinct concerns live here, deliberately kept separate:

1. User login passwords -- one-way hash (bcrypt). Never recoverable, only
   verifiable.
2. JWT access tokens -- short-lived, signed, used to authenticate API
   requests after login.
3. Broker credential encryption -- MUST be reversible (the bot needs the
   actual plaintext password to log into MT5), so this is symmetric
   *encryption* (Fernet), not hashing. Do not conflate this with #1.
4. High-entropy service tokens (e.g. a bridge's fetch token) -- one-way
   hash, but NOT bcrypt: bcrypt's slow, salted design defends a
   low-entropy, human-chosen secret against offline brute force. A
   256-bit value from `secrets.token_urlsafe` is already unguessable, so
   a fast, unsalted, indexable hash is the correct choice here -- it's
   also what lets the DB look a row up BY this hash directly, which
   bcrypt's per-call-random-salt design would make impossible. Do not
   conflate this with #1 either.
"""
import hashlib
from datetime import datetime, timedelta, timezone

from cryptography.fernet import Fernet
from jose import JWTError, jwt
from passlib.context import CryptContext

from app.core.config import settings

# ---------- User login passwords (one-way) ----------

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(plain_password: str) -> str:
    return pwd_context.hash(plain_password)


def verify_password(plain_password: str, password_hash: str) -> bool:
    return pwd_context.verify(plain_password, password_hash)


# ---------- JWT access tokens ----------

def create_access_token(subject: str) -> str:
    """subject is the user_id, stored as the JWT 'sub' claim."""
    expire = datetime.now(timezone.utc) + timedelta(
        minutes=settings.jwt_access_token_expire_minutes
    )
    payload = {"sub": subject, "exp": expire}
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> str | None:
    """Returns the user_id (sub claim) if the token is valid, else None."""
    try:
        payload = jwt.decode(
            token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm]
        )
        return payload.get("sub")
    except JWTError:
        return None


# ---------- Broker credential encryption (reversible, by design) ----------

_fernet = Fernet(settings.credentials_encryption_key.encode())


def encrypt_secret(plaintext: str) -> str:
    return _fernet.encrypt(plaintext.encode()).decode()


def decrypt_secret(ciphertext: str) -> str:
    return _fernet.decrypt(ciphertext.encode()).decode()


# ---------- High-entropy service tokens (one-way, unsalted -- see module docstring) ----------

def hash_service_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()
