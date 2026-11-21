from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Config(BaseSettings):
    env: Literal["local", "staging", "prod"] = "local"

    database_url: SecretStr
    redis_url: SecretStr
    webhook_secret: SecretStr = SecretStr("")
    mcp_auth_token: SecretStr = SecretStr("")

    groq_api_key: SecretStr = SecretStr("")
    gemini_api_key: SecretStr = SecretStr("")
    primary_llm_model: str = "groq/llama-3.3-70b-versatile"
    fallback_llm_model: str = "gemini/gemini-2.5-flash"

    llm_daily_limit: int = 30
    rate_limit_per_hour: int = 5

    github_token: SecretStr = SecretStr("")
    github_repo: str = ""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


config = Config()
