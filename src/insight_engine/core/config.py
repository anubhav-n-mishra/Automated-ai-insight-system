"""Application settings.

Every knob is an environment variable prefixed ``INSIGHT_ENGINE_`` so a
container can be configured without touching the image, and nothing reads
``os.environ`` directly outside this module.

Settings are resolved once per process via :func:`get_settings`; tests override
them through the FastAPI dependency, never by mutating globals.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from insight_engine.core.errors import ConfigurationError

Environment = Literal["development", "staging", "production"]
LogFormat = Literal["text", "json"]


def _default_data_dir() -> Path:
    """Writable state directory, honouring the XDG spec where it applies."""
    xdg = os.environ.get("XDG_STATE_HOME")
    if xdg:
        return Path(xdg) / "insight-engine"
    return Path.cwd() / "var"


class Settings(BaseSettings):
    """Runtime configuration, assembled from the environment and ``.env``."""

    model_config = SettingsConfigDict(
        env_prefix="INSIGHT_ENGINE_",
        env_file=".env",
        env_file_encoding="utf-8",
        env_nested_delimiter="__",
        extra="ignore",
        frozen=True,
    )

    # ---------------------------------------------------------------- runtime
    environment: Environment = "development"
    debug: bool = False
    log_level: str = "INFO"
    log_format: LogFormat = "json"

    # ------------------------------------------------------------------- HTTP
    host: str = "127.0.0.1"
    port: int = Field(default=8000, ge=1, le=65535)

    public_base_url: str | None = Field(
        default=None,
        description=(
            "Externally reachable origin, e.g. https://insights.example.com. "
            "QR codes and dashboard links are built from this; without it they "
            "only resolve on the machine that generated them."
        ),
    )

    cors_allow_origins: list[str] = Field(default_factory=list)

    api_keys: list[SecretStr] = Field(
        default_factory=list,
        description="Shared secrets accepted in the X-API-Key header. Empty disables auth.",
    )

    # ------------------------------------------------------------------ limits
    max_upload_bytes: int = Field(default=64 * 1024 * 1024, ge=1024)
    max_config_bytes: int = Field(default=512 * 1024, ge=256)
    max_rows: int = Field(default=5_000_000, ge=1)
    max_dimension_cardinality: int = Field(
        default=10_000,
        ge=1,
        description="Reject a group-by that would explode into more segments than this.",
    )
    request_timeout_seconds: float = Field(default=300.0, gt=0)

    rate_limit_requests: int = Field(default=60, ge=0, description="0 disables rate limiting.")
    rate_limit_window_seconds: int = Field(default=60, ge=1)

    # ----------------------------------------------------------------- workers
    worker_threads: int = Field(default=4, ge=1, le=64)
    job_retention_seconds: int = Field(default=24 * 3600, ge=60)

    # ----------------------------------------------------------------- storage
    data_dir: Path = Field(default_factory=_default_data_dir)
    session_ttl_hours: int = Field(default=72, ge=1)
    artifact_ttl_hours: int = Field(default=168, ge=1)
    cleanup_interval_seconds: int = Field(default=900, ge=30)

    # --------------------------------------------------------------- narrative
    llm_provider: Literal["auto", "gemini", "openai", "none"] = "auto"
    llm_model: str | None = Field(
        default=None,
        description="Provider-specific model id. Defaults per provider when unset.",
    )
    llm_timeout_seconds: float = Field(default=45.0, gt=0)
    llm_max_retries: int = Field(default=2, ge=0, le=5)
    llm_cache_size: int = Field(default=256, ge=0)

    gemini_api_key: SecretStr | None = None
    openai_api_key: SecretStr | None = None
    openai_base_url: str | None = None

    # ------------------------------------------------------------------ voice
    murf_api_key: SecretStr | None = None
    murf_voice_id: str = "en-US-natalie"
    voice_timeout_seconds: float = Field(default=60.0, gt=0)

    # ------------------------------------------------ relational source policy
    allow_remote_sql: bool = Field(
        default=False,
        description=(
            "Allow callers to supply their own database connection strings over "
            "HTTP. Off by default: an open engine is a server-side request "
            "forgery primitive against everything the container can reach."
        ),
    )
    allowed_sql_drivers: list[str] = Field(
        default_factory=lambda: ["postgresql", "mysql", "sqlite", "mssql"]
    )
    allowed_sql_hosts: list[str] = Field(
        default_factory=list,
        description="Hostname allowlist for remote sources. Empty allows any host.",
    )
    sql_statement_timeout_seconds: int = Field(default=60, ge=1)

    # ---------------------------------------------------------------- telemetry
    metrics_enabled: bool = True
    tracing_enabled: bool = False
    otlp_endpoint: str | None = None
    service_name: str = "insight-engine"

    # ---------------------------------------------------------------- derived
    @property
    def reports_dir(self) -> Path:
        return self.data_dir / "reports"

    @property
    def audio_dir(self) -> Path:
        return self.data_dir / "audio"

    @property
    def sessions_dir(self) -> Path:
        return self.data_dir / "sessions"

    @property
    def uploads_dir(self) -> Path:
        return self.data_dir / "uploads"

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    def base_url(self) -> str:
        """Origin used to build shareable links."""
        if self.public_base_url:
            return self.public_base_url.rstrip("/")
        host = "localhost" if self.host in {"0.0.0.0", "::"} else self.host  # noqa: S104
        return f"http://{host}:{self.port}"

    def ensure_directories(self) -> None:
        """Create the writable tree. Idempotent; called at startup and by the CLI."""
        for path in (
            self.data_dir,
            self.reports_dir,
            self.audio_dir,
            self.sessions_dir,
            self.uploads_dir,
        ):
            path.mkdir(parents=True, exist_ok=True)

    # --------------------------------------------------------------- validation
    @field_validator("log_level")
    @classmethod
    def _upper_log_level(cls, value: str) -> str:
        level = value.upper()
        if level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ValueError(f"unknown log level: {value}")
        return level

    @field_validator("public_base_url")
    @classmethod
    def _validate_base_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not value.startswith(("http://", "https://")):
            raise ValueError("public_base_url must start with http:// or https://")
        return value.rstrip("/")

    @field_validator("api_keys", mode="before")
    @classmethod
    def _split_api_keys(cls, value: object) -> object:
        # Accept a comma-separated string so a single env var can hold several keys.
        if isinstance(value, str):
            return [part.strip() for part in value.split(",") if part.strip()]
        return value

    @field_validator(
        "cors_allow_origins", "allowed_sql_hosts", "allowed_sql_drivers", mode="before"
    )
    @classmethod
    def _split_csv(cls, value: object) -> object:
        if isinstance(value, str):
            return [part.strip() for part in value.split(",") if part.strip()]
        return value

    @model_validator(mode="after")
    def _production_guardrails(self) -> Settings:
        """Refuse to boot a production deployment in an obviously unsafe shape.

        These are the settings that are convenient in development and dangerous
        in production, so the check lives here rather than in a runbook nobody
        reads.
        """
        if self.environment != "production":
            return self

        problems: list[str] = []
        if self.debug:
            problems.append("debug must be off in production")
        if not self.api_keys:
            problems.append(
                "INSIGHT_ENGINE_API_KEYS must be set in production; an unauthenticated "
                "report endpoint is an open compute and LLM-spend proxy"
            )
        if not self.public_base_url:
            problems.append(
                "INSIGHT_ENGINE_PUBLIC_BASE_URL must be set in production; QR codes and "
                "dashboard links would otherwise point at localhost"
            )
        if "*" in self.cors_allow_origins:
            problems.append("cors_allow_origins must not be '*' in production")
        if self.allow_remote_sql and not self.allowed_sql_hosts:
            problems.append(
                "allow_remote_sql requires a non-empty allowed_sql_hosts allowlist in production"
            )
        if problems:
            raise ConfigurationError(
                "Unsafe production configuration: " + "; ".join(problems),
                context={"problems": problems},
            )
        return self


SettingsDep = Annotated[Settings, "settings"]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Process-wide settings singleton."""
    return Settings()


def reset_settings_cache() -> None:
    """Drop the cached settings. Used by tests and the CLI after env changes."""
    get_settings.cache_clear()
