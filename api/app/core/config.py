import os
from pydantic_settings import BaseSettings, SettingsConfigDict

def _env_file() -> str | None:
    env = os.getenv("ENV", "dev")
    path = f"environments/{env}.env"
    if os.path.exists(path):
        return path
    return None

class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=_env_file(),
        env_file_encoding="utf-8",
        extra="ignore"
    )
    
    ENV: str = "dev"
    APP_VERSION: str = os.getenv("APP_VERSION", "0.0.0")
    
    DB_HOST: str
    DB_PORT: int
    DB_NAME: str
    DB_USER: str
    DB_PASSWORD: str
    DB_SSLMODE: str = "disable"
    
    @property
    def DATABASE_URL(self) -> str:
        return (
            f"postgresql+psycopg://{self.DB_USER}:{self.DB_PASSWORD}"
            f"@{self.DB_HOST}:{self.DB_PORT}/{self.DB_NAME}"
            f"?sslmode={self.DB_SSLMODE}"
        )
        
settings = Settings()    