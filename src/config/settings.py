"""Settings from the environment / .env, and the YAML config files next to this module."""

from functools import cache
from pathlib import Path

import yaml
from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

CONFIG_DIR = Path(__file__).parent


class Settings(BaseSettings):
    """API keys stay SecretStr so they never end up in a log line."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    gemini_api_key: SecretStr | None = None
    groq_api_key: SecretStr | None = None
    github_token: SecretStr | None = None

    model_override: str | None = None  # evals: run every role on one model

    dry_run: bool = True
    public_demo: bool = False
    daily_budget_usd: float = 2.0

    max_repo_files: int = 5000
    tool_output_chars: int = 6000
    example_pr_url: str | None = None

    @property
    def opens_prs(self) -> bool:
        """Real PRs only when dry run is off and this is not the public demo."""
        return not self.dry_run and not self.public_demo


@cache
def get_settings() -> Settings:
    """One Settings instance per process."""
    return Settings()


@cache
def load_yaml(name: str) -> dict:
    """Read models.yaml or prompts.yaml from the config folder."""
    return yaml.safe_load((CONFIG_DIR / name).read_text(encoding="utf-8"))


def models_for(role: str) -> list[str]:
    """LiteLLM models for an agent role, preferred first."""
    if override := get_settings().model_override:
        return [override]
    return load_yaml("models.yaml")[role]
