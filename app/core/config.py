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

    # Monitoring/alerting (logging/audit review part 3). All optional and
    # dormant by default -- app.core.telegram/app.core.healthchecks no-op
    # (with one warning log, not per-call) if left unset, so this is safe
    # to deploy before real credentials exist. See PENDING_ITEMS.md.
    telegram_bot_token: str | None = None
    telegram_chat_id: str | None = None
    # Deliberately one generic name, not telegram_bot_token-style: each
    # service gets its OWN healthchecks.io check (one for api, one for
    # shadow_runner), set via docker-compose.yml's per-service
    # `environment:` block, not this shared default.
    healthchecks_ping_url: str | None = None

    # Comma-separated list of origins the frontend is served from, e.g.
    # "http://localhost:5173,https://admin.example.com". Defaults to the
    # Vite dev server's default port so local frontend dev works with zero
    # config; override in .env for any real deployment.
    cors_allowed_origins: str = "http://localhost:5173"

    @property
    def cors_allowed_origins_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_allowed_origins.split(",") if origin.strip()]


settings = Settings()
