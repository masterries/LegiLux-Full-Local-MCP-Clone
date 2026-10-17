"""Process-wide state of the server: settings, database pool, release cache, reranker, query vectors, health.

Created once per process (``create_runtime``) and shared by the MCP handlers and the health routes.
Optional components (reranker, vectors) are probed in the background and never block startup or ``/readyz``.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from lgx_mcp.context import Clock
from lgx_mcp.db import BackendUnavailableError, Database
from lgx_mcp.facts import Facts
from lgx_mcp.release import ReleaseCache, ReleaseInfo
from lgx_mcp.resolve import OutOfScopeAlias, Resolver, load_out_of_scope_aliases
from lgx_mcp.retrieval.rerank import Reranker
from lgx_mcp.settings import SUPPORTED_SCHEMA_VERSIONS, McpSettings
from lgx_mcp.vectors import QueryVectors

log = logging.getLogger("lgx_mcp.runtime")
RATE_KEYS_MAX = 1024

CANNED_QUERY = "contrat de travail"
HEALTH_TTL_S = 30.0


@dataclass
class Health:
    db_ok: bool = False
    schema_ok: bool = False
    release_id: str | None = None
    canned_query_ok: bool | None = None
    canned_checked_at: float = 0.0
    error: str | None = None


@dataclass
class Runtime:
    settings: McpSettings
    db: Database
    releases: ReleaseCache
    reranker: Reranker
    vectors: QueryVectors
    clock: Clock
    oos_aliases: dict[str, OutOfScopeAlias]
    health: Health = field(default_factory=Health)
    _tasks: list[asyncio.Task[Any]] = field(default_factory=list)
    _rate: dict[str, list[float]] = field(default_factory=dict)
    _started: bool = False

    # ------------------------------------------------------------------------------------- lifecycle
    async def start(self) -> None:
        if self._started:
            return
        self._started = True
        await self.db.open()
        self._tasks.append(asyncio.create_task(self._background_checks(), name="lgx-mcp-health"))
        log.info("lgx-mcp started: %s", self.settings.describe())

    async def stop(self) -> None:
        for t in self._tasks:
            t.cancel()
        for t in self._tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await t
        self._tasks.clear()
        await self.vectors.aclose()
        await self.db.close()
        self._started = False

    async def _background_checks(self) -> None:
        while True:
            try:
                await self.check_db()
                await self.reranker.probe()
                if self.vectors.enabled and self.health.schema_ok:  # startup check 2: the embedding probe
                    await self.vectors.probe(await self.releases.get())
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # health probing must never crash the server
                log.debug("background check failed: %s", exc)
            await asyncio.sleep(30 if self.health.schema_ok else 5)

    # -------------------------------------------------------------------------------------- per call
    async def release(self) -> ReleaseInfo:
        rel = await self.releases.get()
        if rel.schema_version not in SUPPORTED_SCHEMA_VERSIONS:
            raise BackendUnavailableError(f"unsupported schema_version {rel.schema_version}")
        return rel

    def facts(self) -> Facts:
        return Facts(self.db)

    def resolver(self, release: ReleaseInfo, facts: Facts) -> Resolver:
        return Resolver(facts, release.aliases, self.oos_aliases)

    def vector_status(self, release: ReleaseInfo | None) -> str:
        """``ok`` only for a release with vectors whose startup probe passed; else ``disabled:<reason>``."""
        return self.vectors.status(release)

    def rate_limited(self, client: str) -> float | None:
        """Seconds to wait when ``client`` (or the whole server) exceeded its per-minute budget."""
        now = time.monotonic()
        if len(self._rate) > RATE_KEYS_MAX:  # bounded memory: drop clients without a call in the last minute
            self._rate = {k: w for k, w in self._rate.items() if w and now - w[-1] < 60.0}
        checks = (
            (client, self.settings.rate_per_client_min),
            ("*", self.settings.rate_global_min),
        )
        for key, limit in checks:
            if limit <= 0:
                continue
            window = [t for t in self._rate.get(key, []) if now - t < 60.0]
            self._rate[key] = window
            if len(window) >= limit:
                return max(0.1, 60.0 - (now - window[0]))
        for key, limit in checks:
            if limit > 0:
                self._rate.setdefault(key, []).append(now)
        return None

    # ---------------------------------------------------------------------------------------- health
    async def check_db(self) -> Health:
        h = self.health
        try:
            row = await self.db.fetchone("SELECT release_id, schema_version FROM lgx_meta.release", timeout_s=1.5)
            h.db_ok = True
            h.schema_ok = bool(row) and int(row["schema_version"]) in SUPPORTED_SCHEMA_VERSIONS
            h.release_id = row["release_id"] if row else None
            h.error = None if h.schema_ok else "unsupported or missing release row"
        except BackendUnavailableError as exc:
            h.db_ok = h.schema_ok = False
            h.error = str(exc)
        except Exception as exc:
            h.db_ok = h.schema_ok = False
            h.error = f"{type(exc).__name__}: {exc}"
        return h

    async def check_canned(self) -> bool:
        h = self.health
        if h.canned_query_ok is not None and time.monotonic() - h.canned_checked_at < HEALTH_TTL_S:
            return h.canned_query_ok
        try:
            row = await self.db.fetchone(
                "SELECT count(*) AS n FROM lgx.search_candidates(%s, NULL::halfvec, CURRENT_DATE, '{}'::bigint[],"
                " NULL, NULL, 5)",
                [CANNED_QUERY],
                timeout_s=1.5,
            )
            h.canned_query_ok = bool(row and row["n"] > 0)
        except Exception:
            h.canned_query_ok = False
        h.canned_checked_at = time.monotonic()
        return h.canned_query_ok

    async def readyz(self) -> tuple[int, dict[str, Any]]:
        """200 iff the database is reachable and the schema is supported; degraded states in the body."""
        h = await self.check_db()
        canned = await self.check_canned() if h.schema_ok else False
        rel = self.releases.cached
        if h.schema_ok and rel is None:
            with contextlib.suppress(Exception):
                rel = await self.releases.get()
        clock_ok = None
        if rel is not None:
            today = self.clock.today()
            clock_ok = rel.sync_date <= today <= rel.horizon
        rr = self.reranker.state
        body = {
            "status": "ready" if h.schema_ok else "not_ready",
            "db_ok": h.db_ok,
            "schema_ok": h.schema_ok,
            "release_id": h.release_id,
            "vector_status": self.vector_status(rel),
            "vector_reason": self.vectors.reason(rel),
            "reranker_ok": rr.status == "ok" if self.reranker.enabled else None,
            "rerank_status": rr.status,
            "rerank_reason": rr.reason,
            "clock_ok": clock_ok,
            "canned_query_ok": canned,
            "sync_date": rel.sync_date.isoformat() if rel else None,
            "error": h.error,
        }
        return (200 if h.schema_ok else 503), body


def create_runtime(settings: McpSettings) -> Runtime:
    db = Database(
        settings.pg_conninfo,
        max_size=settings.pool_max,
        statement_timeout_ms=settings.statement_timeout_ms,
        timeout_s=settings.db_timeout_s,
    )
    return Runtime(
        settings=settings,
        db=db,
        releases=ReleaseCache(db),
        reranker=Reranker(
            settings.rerank_url,
            settings.rerank,
            batch=settings.rerank_batch,
            timeout_s=settings.rerank_timeout_s,
            body_chars=settings.rerank_body_chars,
            k=settings.rerank_k,
        ),
        vectors=QueryVectors(settings.embedding, settings.embed_api_key),
        clock=Clock(settings.tz, settings.clock_override),
        oos_aliases=load_out_of_scope_aliases(settings.config_dir),
    )
