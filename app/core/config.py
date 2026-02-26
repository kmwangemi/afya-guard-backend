from typing import List

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── Application ──────────────────────────────────────────────────────────
    APP_NAME: str = "SHA Fraud Detection Platform"
    APP_VERSION: str = "1.0.0"
    DEBUG: bool = False
    ENVIRONMENT: str = "development"  # development | staging | production
    # ── Database (Neon PostgreSQL) ────────────────────────────────────────────
    DATABASE_URL: str = "postgresql://postgres:password@localhost:5432/sha_fraud"
    DB_POOL_SIZE: int = 10
    DB_MAX_OVERFLOW: int = 20
    DB_ECHO: bool = False
    # ── JWT Auth ──────────────────────────────────────────────────────────────
    SECRET_KEY: SecretStr
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7
    # ── CORS ──────────────────────────────────────────────────────────────────
    ALLOWED_ORIGINS: List[str] = ["http://localhost:3000", "http://localhost:8000"]
    # ── Fraud Scoring Thresholds ──────────────────────────────────────────────
    FRAUD_MEDIUM_THRESHOLD: float = 40.0
    FRAUD_HIGH_THRESHOLD: float = 70.0
    FRAUD_CRITICAL_THRESHOLD: float = 90.0
    # Score weights (must sum to 1.0)
    RULE_SCORE_WEIGHT: float = 0.4
    ML_SCORE_WEIGHT: float = 0.4
    DETECTOR_SCORE_WEIGHT: float = 0.2
    # ── ML Model ──────────────────────────────────────────────────────────────
    ML_MODEL_PATH: str = "ml_model.json"
    ML_MODEL_FALLBACK_ENABLED: bool = True
    # ── Alert Settings ────────────────────────────────────────────────────────
    ALERT_AUTO_ESCALATE_HOURS: int = 24
    ALERT_EXPIRE_HOURS: int = 72
    # ── Pagination ────────────────────────────────────────────────────────────
    DEFAULT_PAGE_SIZE: int = 20
    MAX_PAGE_SIZE: int = 100
    # ── Redis (for rate limiting & caching) ───────────────────────────────────
    REDIS_URL: str = "redis://localhost:6379/0"

    @field_validator("ALLOWED_ORIGINS", mode="before")
    @classmethod
    def parse_allowed_origins(cls, v: str | list) -> list:
        if isinstance(v, str):
            return [origin.strip() for origin in v.split(",")]
        return v


settings = Settings()
