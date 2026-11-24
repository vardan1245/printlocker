from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="PL_", extra="ignore")

    database_url: str = "sqlite:///./printlocker.db"
    jwt_secret: str = "change-me"
    access_token_minutes: int = 30
    refresh_token_days: int = 14

    # Fernet key (base64, 32 bytes). Generate: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    file_key: str = ""
    storage_dir: str = "./data/pdfs"

    max_pdf_mb: int = 50
    max_sheets_per_job: int = 2000
    compartment_capacity_sheets: int = 500
    pickup_hours: int = 24
    expiry_check_seconds: int = 60

    admin_email: str = "admin@example.com"
    # No default: without a password no admin account is created (never a guessable default).
    admin_password: str = ""

    cors_origins: str = "*"


def fernet_key(secret: str) -> str:
    """Use the value as a Fernet key if it is one, else derive one from it (SHA-256).

    Lets hosting providers' generated secrets be used directly.
    """
    import base64
    import hashlib

    from cryptography.fernet import Fernet

    if not secret:
        raise RuntimeError("PL_FILE_KEY is not set")
    try:
        Fernet(secret.encode())
        return secret
    except ValueError:
        return base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest()).decode()


@lru_cache
def get_settings() -> Settings:
    return Settings()
