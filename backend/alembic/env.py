from logging.config import fileConfig

from backend.config import load_settings
from backend.db import normalize_url
from backend.models import Base
from sqlalchemy import engine_from_config, pool

from alembic import context

config = context.config

if config.config_file_name is not None and config.attributes.get("configure_logger", True):
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _database_url() -> str:
    """CLI -x url=... > alembic.ini sqlalchemy.url > app settings."""
    x_url = context.get_x_argument(as_dictionary=True).get("url")
    if x_url:
        return normalize_url(x_url)
    ini_url = config.get_main_option("sqlalchemy.url")
    if ini_url:
        return normalize_url(ini_url)
    return normalize_url(load_settings().database_url)


def run_migrations_offline() -> None:
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    section = config.get_section(config.config_ini_section, {})
    section["sqlalchemy.url"] = _database_url()
    connectable = engine_from_config(
        section,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
