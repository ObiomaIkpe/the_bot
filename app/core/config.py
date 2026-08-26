"""
Central settings object. Everything secret or environment-specific comes
from env vars (see .env.example) -- never hardcoded, per the plan's
"never in code or logs" requirement for broker credentials and the JWT
secret.
"""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    database_url: str

    jwt_secret_key: str
    jwt_algorithm: str = "HS256"
    jwt_access_token_expire_minutes: int = 60

    credentials_encryption_key: str

    log_format: str = "text"  # "text" (human-readable) or "json" (structured)

    # Comma-separated list of origins the frontend is served from, e.g.
    # "http://localhost:5173,https://admin.example.com". Defaults to the
    # Vite dev server's default port so local frontend dev works with zero
    # config; override in .env for any real deployment.
    cors_allowed_origins: str = "http://localhost:5173"

    @property
    def cors_allowed_origins_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_allowed_origins.split(",") if origin.strip()]

    # Optional, unlike shadow_runner/config.py's BRIDGE_URL (which MUST be
    # explicit -- see that file's comment). Here, an eager required field
    # would crash the ENTIRE api service at import time on every
    # deployment that hasn't set it yet (confirmed: not in the local
    # .env), even though only the /trading routes actually need it.
    # Left None until configured; those routes fail clearly at request
    # time instead (see app/routers/trading.py's get_bridge_client()).
    bridge_url: str | None = None


settings = Settings()
