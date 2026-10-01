"""`docs/data-model.md` -> Migration approach.

Reads the database URL from `Settings().database_url` (env `DATABASE_URL` /
`.env`), not from `alembic.ini`, so the same configuration source as the
application is used (WP2 checklist).
"""

from logging.config import fileConfig

from alembic import context
from mana_leak_core.db.models import SQLModel
from mana_leak_core.settings import get_settings
from sqlalchemy import engine_from_config, pool

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Autogenerate support: SQLModel tables defined in mana_leak_core.db.models.
target_metadata = SQLModel.metadata


def get_url() -> str:
    return get_settings().database_url.get_secret_value()


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode: emit SQL without a live connection."""
    context.configure(
        url=get_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode: open a connection and apply them."""
    configuration = config.get_section(config.config_ini_section, {})
    configuration["sqlalchemy.url"] = get_url()
    connectable = engine_from_config(configuration, prefix="sqlalchemy.", poolclass=pool.NullPool)

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
