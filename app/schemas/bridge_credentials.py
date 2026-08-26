from pydantic import BaseModel


class BridgeCredentialSecretOut(BaseModel):
    """Returned only to a bridge worker presenting a valid X-Bridge-Token
    (see app/routers/internal_bridge.py) -- never to a regular user."""
    login: str
    password: str
    server: str
