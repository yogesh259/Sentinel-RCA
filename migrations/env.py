from alembic import context
from sqlalchemy import create_engine

from src.db.engine import CONNECT_ARGS
from src.db.migrate import database_url
from src.db.models import Base


def include_name(name, type_, parent_names) -> bool:
    # Checkpoint tables belong to LangGraph; keep autogenerate away from them.
    return type_ != "table" or name in Base.metadata.tables


engine = create_engine(database_url(), connect_args=CONNECT_ARGS)

with engine.connect() as connection:
    context.configure(
        connection=connection,
        target_metadata=Base.metadata,
        include_name=include_name,
        transaction_per_migration=True,
    )
    with context.begin_transaction():
        context.run_migrations()
    connection.commit()
