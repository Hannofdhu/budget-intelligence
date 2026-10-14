from functools import lru_cache
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="BUDGET_", env_file=".env", extra="ignore")

    environment: Literal["local", "test", "staging", "production"] = "local"
    log_level: str = "INFO"
    auth_mode: Literal["dev_headers", "oidc"] = "dev_headers"
    local_trial_enabled: bool = False
    local_trial_department_id: str = ""
    ai_agent_enabled: bool = False
    ai_agent_proxy_secret: str = Field(default="", repr=False)
    ai_agent_model_config: str = "~/.openclaw/openclaw.json"
    ai_agent_runtime_root: str = "output/ai-agent"
    ai_agent_image: str = "budget-pi-agent:1.2.0"
    ai_agent_browser_origin: str = "http://127.0.0.1:5188"
    database_url: str = "postgresql+asyncpg://budget:budget@127.0.0.1:54329/budget"
    api_host: str = "127.0.0.1"
    api_port: int = Field(default=8000, ge=1, le=65535)
    worker_id: str = "local-worker-1"
    worker_poll_seconds: float = Field(default=1.0, gt=0, le=60)
    guancli_binary: str = "guancli"
    guancli_profile: str = "default"
    guancli_timeout_seconds: float = Field(default=300, gt=0, le=1800)
    guancli_retry_attempt_timeout_seconds: float = Field(default=60, gt=0, le=1800)
    guancli_retry_count: int = Field(default=2, ge=0, le=4)
    guancli_retry_backoff_seconds: float = Field(default=0.5, ge=0, le=10)
    guancli_max_concurrent_queries: int = Field(default=2, ge=1, le=8)
    guancli_proxy_url: str | None = Field(default=None, repr=False)
    forecast_refresh_timeout_seconds: float = Field(default=180, gt=0, le=1800)
    source_preview_max_rows: int = Field(default=500_000, ge=1_000, le=1_000_000)
    source_preview_chunk_max_rows: int = Field(default=60_000, ge=1_000, le=60_000)
    source_preview_target_rows: int = Field(default=10_000, ge=1_000, le=60_000)
    source_chain_max_lag_minutes: int = Field(default=180, ge=0, le=1440)
    audit_archive_max_bytes: int = Field(default=52_428_800, ge=1_048_576, le=524_288_000)
    roster_dataset_id: str = "na942601c1371459eaf089f4"
    roster_dataset_name: str = "智慧学习部学员花名册"
    summary_dataset_id: str = "l932b728584e3496685341a5"
    summary_dataset_name: str = "智慧学习部指标汇总宽表"
    roster_etl_id: str = "g9bc9e7a162b04ca0bb207ac"
    source_field_mapping_version: str = "zhihui-budget-source-v1"

    @model_validator(mode="after")
    def reject_development_auth_in_production(self) -> "Settings":
        if self.environment == "production" and self.auth_mode == "dev_headers":
            raise ValueError("production cannot use dev_headers authentication")
        return self

    @model_validator(mode="after")
    def validate_source_proxy(self) -> "Settings":
        if self.guancli_proxy_url is not None:
            proxy = urlsplit(self.guancli_proxy_url)
            if (
                proxy.scheme not in {"http", "https", "socks5", "socks5h"}
                or not proxy.hostname
                or proxy.query
                or proxy.fragment
                or proxy.path not in {"", "/"}
            ):
                raise ValueError("guancli_proxy_url must be an HTTP(S) or SOCKS proxy URL")
            # Accessing port also validates malformed/out-of-range values, without echoing the URL.
            try:
                _ = proxy.port
            except ValueError:
                raise ValueError("guancli_proxy_url contains an invalid port") from None
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
