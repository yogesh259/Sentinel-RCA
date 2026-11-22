"""Runtime settings read from the environment / .env, plus the per-role model table."""

from functools import cache
from pathlib import Path

import yaml
from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

CONFIG_DIR = Path(__file__).parent


class Settings(BaseSettings):
    """Everything the app reads from the environment. Keys stay SecretStr so they never get logged."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    gemini_api_key: SecretStr | None = None
    groq_api_key: SecretStr | None = None
    github_token: SecretStr | None = None

    model_override: str | None = None  # evals: run every role on one model

    dry_run: bool = True
    public_demo: bool = False
    daily_budget_usd: float = 2.0

    cache_dir: Path = Path(".cache/repos")
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


def model_for(role: str) -> str:
    """The LiteLLM model name configured for an agent role (or the override, if set)."""
    return get_settings().model_override or load_yaml("models.yaml")[role]
