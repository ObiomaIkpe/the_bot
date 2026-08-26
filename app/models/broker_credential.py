import uuid

from sqlalchemy import Boolean, Column, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.core.database import Base
from app.core.security import decrypt_secret, encrypt_secret


class BrokerCredential(Base):
    __tablename__ = "broker_credentials"

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
