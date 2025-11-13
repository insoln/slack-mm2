from logging.config import fileConfig

from sqlalchemy import engine_from_config
from sqlalchemy import pool

from alembic import context

import os


def _sync_database_url() -> str:
    """Return the DATABASE_URL suited for Alembic (sync driver).

    Allows the runtime app to use async URLs while Alembic sticks to psycopg2.
    """
    env_url = os.environ.get("DATABASE_URL")
    if env_url:
        # Convert common async driver specifiers to psycopg2 for migrations.
        if env_url.startswith("postgresql+asyncpg"):
            return env_url.replace("postgresql+asyncpg", "postgresql+psycopg2", 1)
        return env_url
    cfg_url = config.get_main_option("sqlalchemy.url")
    if not cfg_url:
        raise RuntimeError("sqlalchemy.url not configured in alembic.ini")
    return cfg_url


# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
# This line sets up loggers basically.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Не используем models, только миграции через SQL-файлы
# target_metadata = None

# other values from the config, defined by the needs of env.py,
# can be acquired:
# my_important_option = config.get_main_option("my_important_option")
# ... etc.


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.

    """
    url = _sync_database_url()
    context.configure(
        url=url,
        # target_metadata=target_metadata, # Removed as per edit hint
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode.

    In this scenario we need to create an Engine
    and associate a connection with the context.

    """
    config.set_main_option("sqlalchemy.url", _sync_database_url())
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            transaction_per_migration=True,  # allow autocommit_block inside individual revisions
        )

        # Wrap all migrations in a transaction; revisions needing CONCURRENTLY will
        # use op.get_context().autocommit_block() to temporarily break out.
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
