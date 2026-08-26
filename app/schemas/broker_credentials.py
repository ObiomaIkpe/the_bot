import uuid

from pydantic import BaseModel, Field


class BrokerCredentialCreate(BaseModel):
    """Submitted by the user themselves -- their own MT5 login details.
    bridge_url is deliberately absent here: it's not something a user
    knows or provides, it's set later (see BrokerCredentialUpdate) once
    a bridge worker actually exists for this account."""
    broker_name: str
    account_login: str
    account_password: str = Field(min_length=1)
    server: str
    account_type: str = Field(pattern="^(demo|live)$")


class BrokerCredentialUpdate(BaseModel):
    """bridge_url: set once a bridge worker has been provisioned for
    this account (currently a manual step -- see migration 0008).
    is_active: which credential trading endpoints should use, if a user
    has more than one."""
    bridge_url: str | None = None
    is_active: bool | None = None


class BrokerCredentialOut(BaseModel):
    """Deliberately excludes account_password entirely -- it must never
    leave the server once written, encrypted or not. account_login (the
    MT5 account number, not a secret on the level of the password) is
    included since it's useful for a user to confirm which account
    they've connected."""
    credential_id: uuid.UUID
    broker_name: str
    account_login: str
    server: str
    account_type: str
    is_active: bool
    bridge_configured: bool

    class Config:
        from_attributes = True

    @classmethod
    def from_model(cls, cred) -> "BrokerCredentialOut":
        return cls(
            credential_id=cred.credential_id,
            broker_name=cred.broker_name,
            account_login=cred.account_login,
            server=cred.server,
            account_type=cred.account_type,
            is_active=cred.is_active,
            bridge_configured=bool(cred.bridge_url),
        )
