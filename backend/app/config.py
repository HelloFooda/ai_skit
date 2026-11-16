from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """从 .env 读配置，统一 LLM_ 前缀。"""

    model_config = SettingsConfigDict(env_file=".env", env_prefix="LLM_", extra="ignore")

    base_url: str = "https://api.openai.com/v1"
    api_key: str = ""
    model: str = "gpt-4o-mini"
    mock: bool = False


settings = Settings()
