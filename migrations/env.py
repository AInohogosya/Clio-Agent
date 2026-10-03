from __future__ import annotations

import asyncio
import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from ethos.config import load_config

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

dsn_env = os.environ.get("ETHOS_DSN")
if dsn_env:
    if dsn_env.startswith("postgres://"):
        dsn_env = "postgresql://" + dsn_env[len("postgres://"):]
    sqlalchemy_url = dsn_env.replace("postgresql://", "postgresql+asyncpg://")
    config.set_main_option("sqlalchemy.url", sqlalchemy_url)
else:
    try:
        cfg = load_config()
        url = cfg.database.dsn.replace("postgresql://", "postgresql+asyncpg://")
        config.set_main_option("sqlalchemy.url", url)
    except Exception:
        pass

target_metadata = None


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
