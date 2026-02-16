import os
from pydantic_settings import BaseSettings

def _env_file() -> str | None:
    env = os.getenv("ENV", "dev")
    path = f"environments/{env}.env"
    if os.path.exists(path):
        return path
    return None

class Settings(BaseSettings):
    env: str = "dev"
    app_version: str = os.getenv("APP_VERSION", "0.0.0")
    
    class Config:
        env_file = _env_file()
        env_file_encoding = "utf-8"
        
settings = Settings()    