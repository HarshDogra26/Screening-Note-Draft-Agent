"""Typed configuration. Every bound, timeout, path and credential enters here."""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv
from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_ROOT = Path(__file__).resolve().parent.parent
PROJECT_ROOT = BACKEND_ROOT.parent

load_dotenv(BACKEND_ROOT / ".env", override=False)

PART_A_DIR = PROJECT_ROOT / "data-part a"
PART_B_DIR = PROJECT_ROOT / "data-part b"


class ProviderMode(StrEnum):
    """FIXTURE replays recorded LLM calls; LIVE calls Azure.
    """

    FIXTURE = "fixture"
    LIVE = "live"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BACKEND_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    provider_mode: ProviderMode = ProviderMode.FIXTURE

    # --- data locations ----------------------------------------------------
    part_a_dir: Path = PART_A_DIR
    part_b_dir: Path = PART_B_DIR

    @property
    def plants_csv(self) -> Path:
        return self.part_a_dir / "plants.csv"

    @property
    def prices_csv(self) -> Path:
        return self.part_a_dir / "prices.csv"

    @property
    def documents_dir(self) -> Path:
        return self.part_a_dir / "documents"

    # --- Azure OpenAI ------------------------------------------------------
    azure_openai_endpoint: str = ""
    azure_openai_api_key: SecretStr = SecretStr("")
    azure_openai_chat_deployment: str = "gpt-4.1"
    azure_openai_chat_api_version: str = "2024-10-21"
    azure_openai_embedding_deployment: str = "text-embedding-3-large"
    azure_openai_embedding_api_version: str = "2024-10-21"

    # --- determinism -------------------------------------------------------
    llm_temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    llm_top_p: float = Field(default=1.0, ge=0.0, le=1.0)
    llm_seed: int = 7

    # --- MCP ---------------------------------------------------------------
    mcp_host: str = "127.0.0.1"
    mcp_port: int = 8766
    mcp_url: str = "http://127.0.0.1:8766/mcp"
    mcp_inprocess: bool = False

    # --- agent bounds ------------------------------------------------------
    max_tool_calls_per_section: int = Field(default=8, ge=1, le=40)
    section_wall_clock_s: float = Field(default=90.0, gt=0)
    max_sections: int = Field(default=6, ge=1, le=12)

    # --- timeouts & retries ------------------------------------------------
    llm_timeout_s: float = Field(default=120.0, gt=0)
    max_retries: int = Field(default=3, ge=1, le=8)
    retry_base_delay_s: float = Field(default=1.0, gt=0)

    # --- storage -----------------------------------------------------------
    chroma_dir: Path = Path("./var/chroma")
    index_dir: Path = Path("./var/index")
    cassette_dir: Path = Path("./var/cassettes")

    # --- ingestion ---------------------------------------------------------
    # A PDF page yielding fewer than this many characters is treated as having no
    # extractable text, so the agent reports unavailability instead of guessing.
    min_page_chars: int = Field(default=200, ge=0)
    max_replacement_char_ratio: float = Field(default=0.40, ge=0.0, le=1.0)

    # --- expected dataset shape (ingestion validation gate) ----------------
    expect_plant_rows: int = 52
    expect_price_rows: int = 190
    expect_document_count: int = 27

    log_level: str = "INFO"
    log_json: bool = False
    cors_origins: str = "http://localhost:5173"

    @field_validator("chroma_dir", "index_dir", "cassette_dir", mode="after")
    @classmethod
    def _absolutise(cls, v: Path) -> Path:
        return v if v.is_absolute() else (BACKEND_ROOT / v).resolve()

    @property
    def is_live(self) -> bool:
        return self.provider_mode is ProviderMode.LIVE

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    def missing_live_credentials(self) -> list[str]:
        """Which secrets are absent. Checked at startup so LIVE fails loudly."""
        missing: list[str] = []
        if not self.azure_openai_endpoint:
            missing.append("AZURE_OPENAI_ENDPOINT")
        if not self.azure_openai_api_key.get_secret_value():
            missing.append("AZURE_OPENAI_API_KEY")
        return missing


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
