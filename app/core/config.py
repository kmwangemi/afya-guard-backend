import os
from pathlib import Path
from typing import Optional

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", 
        env_file_encoding="utf-8",
        extra="ignore"
    )

    base_dir: Path = Path(__file__).resolve().parent.parent.parent
    secret_key: SecretStr
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 30
    refresh_token_expire_days: int = 7
    database_url: str
    cloudinary_cloud_name: Optional[str] = None
    cloudinary_api_key: Optional[str] = None
    cloudinary_api_secret: Optional[str] = None

    # AI extraction keys — Optional so the app starts even without them
    xai_api_key: Optional[str] = None
    anthropic_api_key: Optional[str] = None
    use_ai_extraction: bool = True


settings = Settings()  # Loaded from .env file
