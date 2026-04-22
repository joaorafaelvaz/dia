"""Application settings loaded from .env via pydantic-settings."""
from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- App ---
    app_name: str = "dia"
    app_env: Literal["development", "production"] = "production"
    app_debug: bool = False
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    app_secret_key: str = "change-me"
    app_base_url: str = "http://localhost:8080"

    # --- Auth ---
    basic_auth_user: str = "admin"
    basic_auth_pass: str = "change-me"

    # --- Database ---
    database_url: str = "postgresql+asyncpg://dia:dia_pass@postgres:5432/dia_db"

    # --- Redis / broker ---
    redis_url: str = "redis://redis:6379/0"
    celery_broker_url: str = "redis://redis:6379/1"
    celery_result_backend: str = "redis://redis:6379/2"

    # --- Anthropic ---
    anthropic_api_key: str = ""
    claude_model_reports: str = "claude-opus-4-7"
    claude_model_classify: str = "claude-haiku-4-5-20251001"
    claude_max_tokens_report: int = 8192
    claude_max_tokens_classify: int = 512

    # --- External climate APIs ---
    open_meteo_forecast_url: str = "https://api.open-meteo.com/v1/forecast"
    open_meteo_archive_url: str = "https://archive-api.open-meteo.com/v1/archive"
    inmet_base_url: str = "https://apitempo.inmet.gov.br"
    cemaden_base_url: str = "http://www.cemaden.gov.br"

    # --- Notifications ---
    notifications_enabled: bool = False
    n8n_webhook_url: str = ""
    n8n_webhook_token: str = ""
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_pass: str = ""
    smtp_from: str = "dia@fractaleng.com.br"
    alert_email_to: str = ""

    # --- News feature flags ---
    # Default-enabled: fontes com URL estável e comprovadamente funcionando em 2026.
    news_source_g1_enabled: bool = True
    news_source_em_enabled: bool = True
    news_source_agencia_brasil_enabled: bool = True
    # Default-disabled: URLs instáveis / placeholders. Ative manualmente via .env
    # quando descobrir o endpoint correto.
    news_source_mpmg_enabled: bool = False
    news_source_anm_enabled: bool = False

    # --- Alert thresholds ---
    alert_rain_mm_24h_moderate: float = 50.0
    alert_rain_mm_24h_high: float = 100.0
    alert_rain_mm_24h_very_high: float = 150.0
    alert_rain_mm_24h_critical: float = 250.0
    alert_forecast_days: int = 7

    # --- Schedules (cron) ---
    schedule_climate_fetch: str = "0 */3 * * *"
    schedule_news_scrape: str = "0 6,12,18 * * *"
    schedule_analysis: str = "30 */6 * * *"
    schedule_alert_check: str = "0 * * * *"
    schedule_alert_expiration: str = "15 * * * *"
    schedule_report_briefing: str = "0 7 * * 1"
    schedule_report_client: str = "0 8 1 * *"

    # --- Logging ---
    log_level: str = "INFO"
    log_format: Literal["json", "console"] = "json"

    # Derived / helpers
    @property
    def sync_database_url(self) -> str:
        """Sync URL for Alembic (psycopg2)."""
        return self.database_url.replace("postgresql+asyncpg://", "postgresql+psycopg2://")


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
