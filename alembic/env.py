"""Alembic environment. Schema is managed by migrations only.

The database URL comes from the `sqlalchemy.url` alembic option when set
(tests use this to target a temp file), otherwise from the app settings.
"""

from sqlalchemy import engine_from_config, pool

import referral_harvest.models  # noqa: F401 -- register tables on Base.metadata
from alembic import context
from referral_harvest.config import get_settings
from referral_harvest.db import Base

config = context.config
target_metadata = Base.metadata


def get_url() -> str:
    return config.get_main_option("sqlalchemy.url") or get_settings().database_url


def run_migrations_online() -> None:
    configuration = dict(config.get_section(config.config_ini_section) or {})
    configuration["sqlalchemy.url"] = get_url()
    connectable = engine_from_config(configuration, prefix="sqlalchemy.", poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    raise RuntimeError("offline migrations are not supported; run online")
run_migrations_online()
