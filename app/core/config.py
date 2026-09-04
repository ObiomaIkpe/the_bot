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

    # `db`'s own credential, not something app code ever reads directly
    # (database_url already embeds it) -- declared here only because
    # this whole class rejects unknown env vars (pydantic-settings'
    # BaseSettings default), and .env is loaded wholesale into every
    # service via `env_file: ./.env` in docker-compose.yml, POSTGRES_PASSWORD
    # included. Surfaced 2026-09-02: `docker compose run --rm api alembic
    # ...` was the first thing to actually construct Settings() against
    # this exact .env/model_config combination and hit it.
    postgres_password: str | None = None

    # shadow_runner's own config (shadow_runner/config.py reads these
    # directly via os.environ, never through this Settings object) --
    # declared here for the exact same reason as postgres_password above:
    # once these move from docker-compose.yml's shadow_runner-only
    # `environment:` block into the shared .env (2026-09-04, so a future
    # BRIDGE_URL change is a server-side .env edit instead of a code
    # change + deploy), `env_file: ./.env` loads them into `api` too, and
    # this class rejects unknown env vars unless declared.
    bridge_url: str | None = None
    shadow_runner_user_id: str | None = None

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
