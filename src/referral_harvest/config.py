"""Application settings. Every operational knob is an env var; see .env.example."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./referral_harvest.db"
    admin_token: str = "change-me-to-a-long-random-token"
    # bcrypt cost factor for API-key hashing. Default 12; tests set 4 for speed.
    # Production must keep the default.
    bcrypt_rounds: int = 12

    default_sms_sender: str = "console"
    twilio_account_sid: str | None = None
    twilio_auth_token: str | None = None
    twilio_from_number: str | None = None
    sms_cost_usd_per_message: float = 0.013

    servicetitan_client_id: str | None = None
    servicetitan_client_secret: str | None = None
    smartmoving_api_key: str | None = None

    scheduler_enabled: bool = True
    skip_migrations: bool = False
    app_base_url: str = "http://localhost:8000"


@lru_cache
def get_settings() -> Settings:
    return Settings()
