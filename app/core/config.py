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


settings = Settings()
