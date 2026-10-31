from alembic import context
from sqlalchemy import create_engine, pool

from app.config import Settings
from app.customers import models as customer_models  # noqa: F401 -- register M4 metadata
from app.database import Base
from app.identity import models  # noqa: F401 -- register metadata for Alembic
from app.incidents import models as incident_models  # noqa: F401 -- register M7 metadata
from app.integrations import models as integration_models  # noqa: F401 -- register M6 metadata
from app.semantic import models as semantic_models  # noqa: F401 -- register M8 metadata
from app.sla import models as sla_models  # noqa: F401 -- register M5 metadata
from app.tickets import models as ticket_models  # noqa: F401 -- register M3 metadata

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=Settings().database_url.get_secret_value(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(
        Settings().database_url.get_secret_value(),
        poolclass=pool.NullPool,
        hide_parameters=True,
        connect_args={"connect_timeout": 5},
    )
    try:
        with engine.connect() as connection:
            context.configure(connection=connection, target_metadata=target_metadata)
            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
