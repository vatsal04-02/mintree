from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="MINTREE_", extra="ignore")

    database_url: str = "postgresql+psycopg://mintree:mintree@localhost:5433/mintree"
    environment: str = "development"  # development | test | production

    # Session / cookie security
    session_cookie_name: str = "mintree_session"
    session_ttl_hours: int = 12
    cookie_secure: bool = False  # must be True behind HTTPS in production
    cookie_samesite: str = "lax"

    # Login protection
    max_failed_logins: int = 5
    lockout_minutes: int = 15

    # Document storage (local filesystem now; S3-compatible backend is a drop-in
    # implementation of app.documents.storage.Storage)
    storage_root: str = "storage"
    max_upload_mb: int = 25

    # First-run bootstrap admin (used only by `python -m app.seed`)
    bootstrap_admin_email: str = "admin@example.com"  # override via MINTREE_BOOTSTRAP_ADMIN_EMAIL
    bootstrap_admin_password: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
