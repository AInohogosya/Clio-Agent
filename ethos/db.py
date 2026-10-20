from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from typing import Any

import asyncpg

logger = logging.getLogger(__name__)


class DatabaseUnavailable(ConnectionError):
    pass


class Database:
    def __init__(self, pool: asyncpg.Pool, dsn: str):
        self.pool = pool
        self.dsn = dsn

    @classmethod
    async def connect(
        cls,
        dsn: str,
        min_pool: int = 2,
        max_pool: int = 10,
        command_timeout_s: float = 30.0,
        connect_timeout_s: float = 3.0,
    ) -> Database:
        pool = await asyncpg.create_pool(
            dsn,
            min_size=min_pool,
            max_size=max_pool,
            command_timeout=command_timeout_s,
            timeout=connect_timeout_s,
        )
        if pool is None:
            raise DatabaseUnavailable("asyncpg pool creation failed")
        return cls(pool, dsn)

    @classmethod
    async def try_connect(cls, dsn: str, timeout_s: float = 3.0) -> Database | None:
        try:
            return await asyncio.wait_for(
                cls.connect(dsn, min_pool=1, max_pool=4, connect_timeout_s=timeout_s),
                timeout=timeout_s + 2,
            )
        except Exception:
            return None

    async def close(self) -> None:
        await self.pool.close()

    async def execute(self, stmt: str, *args: Any) -> str:
        async with self.pool.acquire() as conn:
            return await conn.execute(stmt, *args)

    async def fetch(self, stmt: str, *args: Any) -> list[asyncpg.Record]:
        async with self.pool.acquire() as conn:
            return await conn.fetch(stmt, *args)

    async def fetchrow(self, stmt: str, *args: Any) -> asyncpg.Record | None:
        async with self.pool.acquire() as conn:
            return await conn.fetchrow(stmt, *args)

    async def fetchval(self, stmt: str, *args: Any) -> Any:
        async with self.pool.acquire() as conn:
            return await conn.fetchval(stmt, *args)

    def acquire(self) -> asyncpg.pool.PoolAcquireContext:
        return self.pool.acquire()

    async def new_listener_connection(self) -> asyncpg.Connection:
        return await asyncpg.connect(self.dsn, timeout=5.0)

    async def add_listener(
        self,
        conn: asyncpg.Connection,
        channel: str,
        callback: Callable[[str], Any],
    ) -> None:
        await conn.add_listener(channel, callback)

    # ------------------------------------------------- the environment store
    #
    # `env_entities` is one table with one meaning — "a fact the running system
    # holds about itself, keyed" — and every writer used to hand-write the same
    # INSERT, with copies that had drifted: one updated `health` on conflict, one
    # had no conflict clause at all, and the row id was spelled three ways. The
    # id is left to the column's own server default; the three methods below are
    # the whole write, read and forget surface.

    async def upsert_env(
        self,
        kind: str,
        key: str,
        attrs: dict[str, Any],
        *,
        owner: str | None,
        health: str = "ok",
        update_health: bool = False,
        on_conflict: bool = True,
    ) -> None:
        """One row of the environment store: `kind`+`key` naming what, `attrs` the fact.

        `update_health` is for the one writer whose health is part of the fact
        (the integrity report) rather than bookkeeping; `on_conflict=False` for
        the one writer that wants a duplicate key to be an error rather than a
        quiet overwrite (snapshot names).
        """
        health_sql = ", health = $5" if update_health else ""
        conflict_sql = (
            f"ON CONFLICT (key) DO UPDATE SET attrs = $3::jsonb, last_verified_at = now(){health_sql}"
            if on_conflict else ""
        )
        await self.execute(
            "INSERT INTO env_entities (kind, key, attrs, owner, last_verified_at, health) "
            "VALUES ($1, $2, $3::jsonb, $4, now(), $5) " + conflict_sql,
            kind, key, json.dumps(attrs), owner, health,
        )

    async def delete_env(self, kind: str, key: str) -> None:
        await self.execute(
            "DELETE FROM env_entities WHERE kind = $1 AND key = $2", kind, key,
        )


def decode_json_value(value: Any, default: Any = None) -> Any:
    """A jsonb column as Python, whether the driver handed back text or a mapping."""
    if isinstance(value, str):
        value = json.loads(value)
    return value if value is not None else default


def decode_env_attrs(row: Any) -> dict[str, Any]:
    """The `attrs` column as a dict, whether the driver handed back jsonb or text."""
    return dict(decode_json_value(row["attrs"], {}) or {})
