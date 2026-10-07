import os
from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import URL
from app.scraping.registry import BANKS

def _env_file() -> tuple[str, ...] | None:
    env = os.getenv("ENV", "dev")
    path = f"environments/{env}.env"
    paths = tuple(p for p in (path, "environments/private.env") if os.path.exists(p))
    return paths or None

class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=_env_file(),
        env_file_encoding="utf-8",
        extra="ignore",
        hide_input_in_errors=True,
    )

    ENV: str = "dev"
    APP_VERSION: str = os.getenv("APP_VERSION", "0.0.0")

    DB_HOST: str
    DB_PORT: int
    DB_NAME: str
    DB_USER: str
    DB_PASSWORD: str
    DB_SSLMODE: str = "disable"

    GEMINI_API_KEY: str | None = None
    CORS_ORIGINS: str = "http://localhost:3000"
    ADMIN_API_KEY: str | None = None
    API_ACCESS_KEY: str | None = None
    REQUIRE_API_ACCESS_KEY: bool = False
    ENABLE_AI_ENRICHMENT: bool = False
    SNAPSHOT_DIR: str = "data/snapshots"
    SCRAPING_BANKS: str = ",".join(BANKS)
    SCRAPING_INTERVAL_SECONDS: int = Field(default=86400, gt=0)
    SCRAPING_TIMEOUT_SECONDS: int = Field(default=1800, gt=0)
    SCRAPING_MAX_DOCUMENTS: int = Field(default=1500, gt=0)
    SCRAPING_MAX_DETAILS_PER_BANK: int = Field(default=600, gt=0)
    AI_MODEL: str = "gemini-2.5-flash"
    SCRAPING_RETIRE_MISSING: bool = False
    PUBLIC_TIMEZONE: str = "America/Asuncion"

    @field_validator("API_ACCESS_KEY")
    @classmethod
    def validate_api_access_key(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        if not all(33 <= ord(character) <= 126 for character in value):
            raise ValueError("API_ACCESS_KEY debe usar caracteres ASCII sin espacios.")
        return value

    @model_validator(mode="after")
    def require_api_access_key(self) -> "Settings":
        if self.REQUIRE_API_ACCESS_KEY and not self.API_ACCESS_KEY:
            raise ValueError("REQUIRE_API_ACCESS_KEY requiere configurar API_ACCESS_KEY.")
        return self

    @field_validator("SCRAPING_BANKS")
    @classmethod
    def validate_banks(cls, value: str) -> str:
        banks = list(dict.fromkeys(b.strip().lower() for b in value.split(",") if b.strip()))
        if not banks or set(banks) - set(BANKS):
            raise ValueError("SCRAPING_BANKS debe contener bancos reconocidos separados por coma.")
        return ",".join(banks)

    @property
    def DATABASE_URL(self) -> str:
        return URL.create("postgresql+psycopg", username=self.DB_USER, password=self.DB_PASSWORD, host=self.DB_HOST, port=self.DB_PORT, database=self.DB_NAME, query={"sslmode": self.DB_SSLMODE}).render_as_string(hide_password=False)

settings = Settings()
