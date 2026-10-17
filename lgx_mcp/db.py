"""Read-only database access for the MCP server (PLAN §7.5, §8.8).

* One async pool (psycopg 3); every connection is forced read-only and gets a ``statement_timeout`` on top of
  the role defaults of ``lgx_ro`` (``default_transaction_read_only = on``, 5 s).
* SQL is parameterised only; no tool ever accepts SQL.
* The pool opens without waiting, so the process starts (and ``/livez`` answers) while the database is still
  down; tool calls then fail with ``BACKEND_UNAVAILABLE`` and ``/readyz`` reports 503.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool, PoolTimeout

log = logging.getLogger("lgx_mcp.db")


class BackendUnavailableError(RuntimeError):
    """The database cannot be reached (or a query timed out)."""


class Database:
    def __init__(
        self,
        conninfo: str,
        *,
        max_size: int = 8,
        statement_timeout_ms: int = 5000,
        timeout_s: float = 3.0,
    ) -> None:
        self._conninfo = conninfo
        self._timeout_s = timeout_s
        self._pool = AsyncConnectionPool(
            conninfo,
            min_size=1,
            max_size=max(1, max_size),
            open=False,
            timeout=timeout_s,
            reconnect_timeout=0,  # keep retrying in the background; never give up the pool
            kwargs={
                "autocommit": True,
                "row_factory": dict_row,
                "options": f"-c default_transaction_read_only=on -c statement_timeout={int(statement_timeout_ms)}",
                "prepare_threshold": None,
            },
            name="lgx-mcp",
        )
        self._opened = False

    async def open(self) -> None:
        if not self._opened:
            await self._pool.open(wait=False)
            self._opened = True

    async def close(self) -> None:
        if self._opened:
            await self._pool.close()
            self._opened = False

    async def fetch(
        self, sql: str, params: Sequence[Any] | dict[str, Any] | None = None, *, timeout_s: float | None = None
    ) -> list[dict[str, Any]]:
        await self.open()
        try:
            async with self._pool.connection(timeout=timeout_s or self._timeout_s) as conn, conn.cursor() as cur:
                await cur.execute(sql, params)  # type: ignore[arg-type]
                if cur.description is None:
                    return []
                return list(await cur.fetchall())
        except PoolTimeout as exc:
            raise BackendUnavailableError("database unavailable (no connection within the timeout)") from exc
        except (psycopg.OperationalError, psycopg.errors.QueryCanceled, psycopg.errors.AdminShutdown) as exc:
            log.warning("database error: %s", type(exc).__name__)
            raise BackendUnavailableError(f"database unavailable: {type(exc).__name__}") from exc

    async def fetchone(
        self, sql: str, params: Sequence[Any] | dict[str, Any] | None = None, *, timeout_s: float | None = None
    ) -> dict[str, Any] | None:
        rows = await self.fetch(sql, params, timeout_s=timeout_s)
        return rows[0] if rows else None
