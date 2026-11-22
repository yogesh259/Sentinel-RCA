"""Bring the database schema up to date. Run once before starting the API:

    python -m src.db.migrate        (or: make migrate)
"""

import logging
from pathlib import Path

from alembic import command
from alembic.config import Config
from langgraph.checkpoint.postgres import PostgresSaver
from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger("sentinel-rca.migrate")

ALEMBIC_INI = Path(__file__).resolve().parents[2] / "alembic.ini"


class MigrationSettings(BaseSettings):
    """Migrations only need the database URL, never the API's other secrets."""

    database_url: SecretStr

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


def database_url() -> str:
    return MigrationSettings().database_url.get_secret_value()


def upgrade_app_tables() -> None:
    """incidents, issues, execution_traces: apply pending revisions in migrations/versions/."""
    command.upgrade(Config(str(ALEMBIC_INI)), "head")


def upgrade_checkpoint_tables() -> None:
    """LangGraph's checkpoint tables: the library creates and versions these itself."""
    with PostgresSaver.from_conn_string(database_url()) as saver:
        saver.setup()


def main() -> None:
    upgrade_app_tables()
    upgrade_checkpoint_tables()
    logger.info("Database schema is up to date.")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    main()
