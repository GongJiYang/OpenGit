from logging.config import fileConfig
import sys
import os
from os.path import abspath, dirname

from sqlalchemy import engine_from_config
from sqlalchemy import pool

from alembic import context

# add your model's MetaData object here
# for 'autogenerate' support
from sqlmodel import SQLModel

# Ensure project modules are importable for model registration
PROJECT_ROOT = dirname(dirname(abspath(__file__)))
PATHS_TO_ADD = [
    os.path.join(PROJECT_ROOT, "src"),
    os.path.join(PROJECT_ROOT, "..", "..", "packages", "protocol", "src"),
    os.path.join(PROJECT_ROOT, "..", "..", "services", "git-core", "src"),
    os.path.join(PROJECT_ROOT, "..", "..", "services", "semantic-store", "src"),
    os.path.join(PROJECT_ROOT, "..", "..", "services", "execution-vmm", "src"),
    os.path.join(PROJECT_ROOT, "..", ".."),
]
for path in PATHS_TO_ADD:
    normalized = os.path.abspath(path)
    if normalized not in sys.path:
        sys.path.insert(0, normalized)

# Import all models to register them with SQLModel.metadata
# We use noqa: E402 because these must happen after sys.path.insert
# We use noqa: F401 because these are imported strictly for their side-effects (metadata registration)
try:
    import persistence  # noqa: E401, E402, F401
    import agent_auth.models  # noqa: E401, E402, F401
    import agent_auth.models.platform  # noqa: E401, E402, F401
    import agent_auth.models.runner  # noqa: E401, E402, F401
except ImportError as exc:
    raise RuntimeError(f"Failed to import SQLModel models for Alembic metadata: {exc}") from exc

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
    url = os.getenv("DATABASE_URL") or config.get_main_option("sqlalchemy.url")
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
    database_url = os.getenv("DATABASE_URL")

    if database_url:
        # Build engine from explicit URL (e.g., Postgres/MySQL provided via Secret)
        connectable = engine_from_config(
            {"sqlalchemy.url": database_url},
            prefix="sqlalchemy.",
            poolclass=pool.NullPool,
        )
    else:
        # Fallback to alembic.ini settings (e.g., local sqlite)
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
