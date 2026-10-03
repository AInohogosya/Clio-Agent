from __future__ import annotations

import asyncio
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

    async def execute_returning(self, stmt: str, *args: Any) -> list[asyncpg.Record]:
        async with self.pool.acquire() as conn:
            return await conn.fetch(stmt, *args)

    def acquire(self) -> asyncpg.pool.PoolAcquireContext:
        return self.pool.acquire()

    def transaction(self) -> asyncpg.pool.PoolConnectionProxyMethods:
        return self.pool.acquire()

    async def notify(self, channel: str, payload: str) -> None:
        await self.execute("SELECT pg_notify($1, $2)", channel, payload)

    async def new_listener_connection(self) -> asyncpg.Connection:
        return await asyncpg.connect(self.dsn, timeout=5.0)

    async def add_listener(
        self,
        conn: asyncpg.Connection,
        channel: str,
        callback: Callable[[str], Any],
    ) -> None:
        await conn.add_listener(channel, callback)

    async def run_in_tx(self, fn: Callable[[asyncpg.Connection], Any]) -> Any:
        async def _tx(conn: asyncpg.Connection) -> Any:
            async with conn.transaction():
                return await fn(conn)

        async with self.pool.acquire() as conn:
            return await _tx(conn)
