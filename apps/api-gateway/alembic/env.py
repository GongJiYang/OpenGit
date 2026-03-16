from logging.config import fileConfig
import sys
from os.path import abspath, dirname

from sqlalchemy import engine_from_config
from sqlalchemy import pool

from alembic import context

# add your model's MetaData object here
# for 'autogenerate' support
from sqlmodel import SQLModel

# Add the project root to sys.path
sys.path.insert(0, dirname(dirname(abspath(__file__))))

# Import all models to register them with SQLModel.metadata
# We use noqa: E402 because these must happen after sys.path.insert
# We use noqa: F401 because these are imported strictly for their side-effects (metadata registration)
try:
    import src.persistence  # noqa: E401, E402, F401
    import src.agent_auth.models  # noqa: E401, E402, F401
    import src.agent_auth.models.platform  # noqa: E401, E402, F401
    import src.agent_auth.models.runner  # noqa: E401, E402, F401
except ImportError:
    # If standard imports fail, the sys.path hack should cover it
    pass

target_metadata = SQLModel.metadata

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
# This line sets up loggers basically.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection, target_metadata=target_metadata
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
