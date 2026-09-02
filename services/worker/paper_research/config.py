from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_SECRETS_FILE = Path("/home/czh/.config/paper-research/secrets.env")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(DEFAULT_SECRETS_FILE) if DEFAULT_SECRETS_FILE.exists() else None,
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=True,
    )

    DEEPSEEK_API_KEY: SecretStr | None = None
    MINERU_API_TOKEN: SecretStr | None = None
    OPENALEX_API_KEY: SecretStr | None = None
    SERPER_API_KEY: SecretStr | None = None
    TAVILY_API_KEY: SecretStr | None = None

    SUPABASE_URL: str | None = None
    SUPABASE_SERVICE_ROLE_KEY: SecretStr | None = None
    TURNSTILE_SECRET_KEY: SecretStr | None = None
    TURNSTILE_TEST_MODE: bool = False
    CROSSREF_MAILTO: str = "research@example.invalid"

    WORKER_ID: str = "paper-worker-1"
    POLL_INTERVAL_SECONDS: float = Field(default=10, ge=1)
    JOB_LEASE_SECONDS: int = Field(default=300, ge=60)
    MAX_MONTHLY_CNY: float = Field(default=100, gt=0)
    BUDGET_GUARD_CNY: float = Field(default=95, gt=0)
    MAX_PROVIDER_CONCURRENCY: int = Field(default=4, ge=1, le=16)
    SEARCH_PROFILE: Literal["academic_only", "academic_web"] = "academic_web"
    IDEA_PIPELINE_V3: bool = False
    IDEA_PIPELINE_V4: bool = False
    V4_MAX_MINUTES: int = Field(default=90, ge=10, le=180)
    V4_FULL_TEXT_TARGET: int = Field(default=20, ge=6, le=30)
    V4_MAX_RETRIEVAL_BATCHES: int = Field(default=3, ge=1, le=5)
    V4_IDEA_RETRY_ENABLED: bool = True
    V4_MAX_IDEA_REVIEW_ATTEMPTS: int = Field(default=8, ge=1, le=8)
    IDEA_EVOLUTION_LOOP_ENABLED: bool = True
    JOB_AUTO_RECOVERY_ENABLED: bool = True
    JOB_RETRY_MAX_DELAY_SECONDS: int = Field(default=21600, ge=30, le=86400)
    PDF_EVIDENCE_PREVIEW_ENABLED: bool = True
    REPORT_SECTIONS_ENABLED: bool = True

    E2B_API_KEY: SecretStr | None = None
    V4_PILOT_SPEC_REQUIRED: bool = True
    E2B_PILOT_ENABLED: bool = False
    # Creation is staged independently from the master runtime kill switch.
    # Existing experiments remain inspectable when either creation path is off.
    E2B_MANUAL_EXPERIMENT_ENABLED: bool = False
    E2B_AUTO_EXPERIMENT_ENABLED: bool = False
    E2B_TEMPLATE_ID: str = "base"
    E2B_CPU_COUNT: int = Field(default=4, ge=1, le=4)
    E2B_MEMORY_MIB: int = Field(default=8192, ge=256, le=8192)
    E2B_DISK_MIB: int = Field(default=10240, ge=1024, le=10240)
    E2B_RUN_TIMEOUT_SECONDS: int = Field(default=3600, ge=60, le=3600)
    E2B_IDLE_PAUSE_SECONDS: int = Field(default=600, ge=60, le=3600)
    E2B_DESTROY_AFTER_SECONDS: int = Field(default=604800, ge=3600)
    E2B_GLOBAL_CONCURRENCY: int = Field(default=1, ge=1, le=1)
    E2B_MAX_SPEND_USD: float = Field(default=90, gt=0, le=90)
    E2B_ESTIMATED_COST_PER_SECOND_USD: float = Field(default=0.000092, gt=0)
    EXPERIMENT_MAX_REPAIRS: int = Field(default=2, ge=0, le=2)
    EXPERIMENT_MAX_USER_VALIDATIONS: int = Field(default=3, ge=0, le=3)
    EXPERIMENT_LLM_MAX_CNY_PER_RUN: float = Field(default=5, gt=0, le=5)
    # Experiment calls are additionally bounded at the Claude Code process.
    # These limits make the database's per-invocation reservation a provable
    # upper bound even when a model consumes its full context on every turn.
    EXPERIMENT_LLM_CONTEXT_TOKENS: int = Field(default=200_000, ge=128_000, le=200_000)
    EXPERIMENT_LLM_MAX_OUTPUT_TOKENS: int = Field(default=16_384, ge=1_024, le=16_384)
    EXPERIMENT_FLASH_MAX_TURNS: int = Field(default=4, ge=2, le=4)
    EXPERIMENT_PRO_MAX_TURNS: int = Field(default=2, ge=2, le=2)
    # Subject-code inference is intentionally much smaller than repository
    # generation and always uses the Flash/Sonnet Claude Code route.
    EXPERIMENT_SANDBOX_INFERENCE_CONTEXT_TOKENS: int = Field(
        default=32_768, ge=8_192, le=32_768
    )
    EXPERIMENT_SANDBOX_INFERENCE_MAX_OUTPUT_TOKENS: int = Field(
        default=4_096, ge=256, le=4_096
    )
    EXPERIMENT_SANDBOX_INFERENCE_MAX_TURNS: int = Field(default=2, ge=2, le=2)
    EXPERIMENT_SANDBOX_INFERENCE_TIMEOUT_SECONDS: int = Field(
        default=300, ge=30, le=600
    )
    EXPERIMENT_ASSISTANT_MAX_CNY: float = Field(default=20, gt=0, le=100)
    CLAUDE_VISION_MODEL: str = "deepseek-v4-flash-vision-exp"
    EXPERIMENT_LLM_MAX_CNY: float = Field(default=40, gt=0, le=200)
    EXPERIMENT_LLM_GLOBAL_MAX_CNY: float = Field(default=200, gt=0, le=10000)
    EXPERIMENT_REPOSITORY_MAX_FILES: int = Field(default=500, ge=48, le=1000)
    EXPERIMENT_REPOSITORY_MAX_FILE_BYTES: int = Field(
        default=2 * 1024 * 1024, ge=64 * 1024, le=16 * 1024 * 1024
    )
    EXPERIMENT_REPOSITORY_MAX_TOTAL_BYTES: int = Field(
        default=64 * 1024 * 1024, ge=1024 * 1024, le=512 * 1024 * 1024
    )
    EXPERIMENT_ARCHIVE_MAX_BYTES: int = Field(
        default=64 * 1024 * 1024, ge=1024 * 1024, le=512 * 1024 * 1024
    )
    EXPERIMENT_ARTIFACT_MAX_BYTES: int = Field(
        default=16 * 1024 * 1024, ge=1024 * 1024, le=128 * 1024 * 1024
    )
    EXPERIMENT_PUBLIC_ARTIFACT_MAX_BYTES: int = Field(
        default=8 * 1024 * 1024, ge=256 * 1024, le=32 * 1024 * 1024
    )
    EXPERIMENT_WORKER_ID: str = "paper-experiment-worker-1"
    EXPERIMENT_POLL_INTERVAL_SECONDS: float = Field(default=5, ge=1, le=60)
    EXPERIMENT_LEASE_SECONDS: int = Field(default=300, ge=60, le=1800)

    CLAUDE_BIN: str = "claude"
    CLAUDE_TIMEOUT_SECONDS: int = Field(default=900, ge=30)
    CLAUDE_ANALYSIS_MAX_TURNS: int = Field(default=8, ge=4, le=16)
    CLAUDE_WEB_MAX_TURNS: int = Field(default=12, ge=8, le=20)
    CLAUDE_MODEL: str = "deepseek-v4-flash"
    CLAUDE_PRO_MODEL: str = "deepseek-v4-pro"
    CLAUDE_EFFORT: str = "high"

    MINERU_BASE_URL: str = "https://mineru.net"
    MINERU_MODEL: str = "vlm"
    MINERU_POLL_SECONDS: float = Field(default=5, ge=1)
    MINERU_TIMEOUT_SECONDS: int = Field(default=900, ge=60)
    EXTERNAL_PDF_TIMEOUT_SECONDS: int = Field(default=180, ge=60, le=600)

    ARTIFACT_ROOT: Path = Path(".artifacts")
    MOCK_MODE: bool = False

    @model_validator(mode="after")
    def validate_budget(self) -> Settings:
        if self.BUDGET_GUARD_CNY >= self.MAX_MONTHLY_CNY:
            raise ValueError("BUDGET_GUARD_CNY must be lower than MAX_MONTHLY_CNY")
        if self.IDEA_PIPELINE_V3 and self.IDEA_PIPELINE_V4:
            raise ValueError("IDEA_PIPELINE_V3 and IDEA_PIPELINE_V4 cannot both be enabled")
        return self

    def require_worker_secrets(self) -> None:
        required = {
            "DEEPSEEK_API_KEY": self.DEEPSEEK_API_KEY,
            "MINERU_API_TOKEN": self.MINERU_API_TOKEN,
            "SUPABASE_URL": self.SUPABASE_URL,
            "SUPABASE_SERVICE_ROLE_KEY": self.SUPABASE_SERVICE_ROLE_KEY,
        }
        missing = [name for name, value in required.items() if not value]
        if missing:
            raise RuntimeError(f"Missing required worker settings: {', '.join(missing)}")

    def require_experiment_secrets(self) -> None:
        required = {
            "DEEPSEEK_API_KEY": self.DEEPSEEK_API_KEY,
            "SUPABASE_URL": self.SUPABASE_URL,
            "SUPABASE_SERVICE_ROLE_KEY": self.SUPABASE_SERVICE_ROLE_KEY,
            "E2B_API_KEY": self.E2B_API_KEY,
        }
        missing = [name for name, value in required.items() if not value]
        if missing:
            raise RuntimeError(
                f"Missing required experiment-worker settings: {', '.join(missing)}"
            )

    @staticmethod
    def reveal(value: SecretStr | None) -> str | None:
        return value.get_secret_value() if value else None
