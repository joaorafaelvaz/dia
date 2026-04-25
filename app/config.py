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

    # --- Error tracking (opcional) ---
    # Se SENTRY_DSN não estiver setado, error tracking vira no-op silencioso.
    # Setar em produção pra capturar exceções de dispatchers best-effort
    # (fetch climate, news, notifications) que hoje só vão pra log.
    sentry_dsn: str | None = None
    # Sample rate: 1.0 captura tudo (recomendado pra começar). Diminua só
    # se Sentry estourar quota.
    sentry_traces_sample_rate: float = 0.0  # transactions caras; default off

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
    # CEMADEN: deprecated — não integrado nesta release. Mantido só pra não
    # quebrar Settings de instâncias existentes. Ver docs/research/cemaden-2026-04.md.
    cemaden_base_url: str = "http://www2.cemaden.gov.br"

    # --- ANA Hidrowebservice ---
    # Substitui o INMET (apitempo saiu do ar; tempo.inmet.gov.br é gated por
    # reCAPTCHA v3). ANA requer OAUth (Identificador + Senha → Bearer JWT com
    # TTL ~1h). Escopo atual: chuva convencional — dado observado com lag
    # 2-6 meses, usado como ground-truth histórico em relatórios. Não cobre
    # real-time (esse papel fica com Open-Meteo). Telemétrica existe mas
    # endpoint ainda não foi destravado — ver TODO em ana.py.
    ana_base_url: str = "https://www.ana.gov.br/hidrowebservice"
    # Feature flag: cliente só é chamado quando true. Sem credenciais, deixa
    # false — task absorve AnaError e segue só com Open-Meteo.
    ana_enabled: bool = False
    ana_user: str = ""
    ana_pass: str = ""
    # Janela de lookback em meses. 6 cobre o trimestre anterior com dados
    # consistidos (operadora leva ~3 meses pra revisar). Limite duro da
    # API: 366 dias — o cliente faz o clamp.
    ana_lookback_months: int = 6
    # Quantas estações pluviométricas mais próximas tentar antes de desistir.
    # Sondagem em Ouro Preto (2026-04) mostrou que rank 1-6 eram todas
    # vazias mesmo pra 2023; primeiro hit foi rank 7 a 17 km. 15 cobre
    # até rank 19 (~20 km) dando folga pra casos onde rank 7-8 também
    # ficam temporariamente sem publicar. Cache negativo de 7 dias
    # (RAINFALL_EMPTY_CACHE_TTL_SECONDS) evita re-sondar vazias.
    ana_max_station_candidates: int = 15

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
    # Janela mínima entre notificações para a mesma combinação
    # (dam_id, alert_type). Evita inundar WhatsApp/email quando uma chuva
    # forte cria múltiplos alertas em sequência. Spec §15 = 6h.
    notification_rate_limit_hours: int = 6
    # Severidade mínima para canal. Spec: WhatsApp em qualquer alerta
    # significativo (≥3); email só para crítico (≥4) — caixa de entrada
    # de gestor não pode virar barulho.
    notification_min_severity_whatsapp: int = 3
    notification_min_severity_email: int = 4

    # --- News feature flags ---
    # Default-enabled: Google Notícias RSS cobre G1 / EM / Folha / UOL /
    # Agência Brasil automaticamente via agregação. É o único feed que se
    # provou estável em 2026.
    news_source_google_news_enabled: bool = True
    # Default-disabled em 2026-04:
    #   - g1, em: seletores CSS das páginas de busca mudaram, raw_cards=0
    #     em todos os testes. Roteamos via google_news no lugar.
    #   - agencia_brasil: HTTP 500 em todos os endpoints RSS testados
    #   - mpmg: /rss.xml responde 404 (endpoint histórico descontinuado)
    #   - anm: sem busca pública estável
    news_source_g1_enabled: bool = False
    news_source_em_enabled: bool = False
    news_source_agencia_brasil_enabled: bool = False
    news_source_mpmg_enabled: bool = False
    news_source_anm_enabled: bool = False

    # --- News scraper diagnostics ---
    # Quando ativo, o HTML scraper loga contagens de cards brutos + até 3 títulos
    # por query. Usar em troubleshooting ("por que candidates=0?"); desligar
    # em produção para reduzir volume de log.
    news_scraper_debug: bool = False

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
    # Sweep de notificações pendentes a cada 5 min. Curto o suficiente
    # pra alertas críticos chegarem rápido; longo o bastante pra rate-limit
    # de 6h por (dam, alert_type) evitar tempestade.
    schedule_notifications: str = "*/5 * * * *"
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
