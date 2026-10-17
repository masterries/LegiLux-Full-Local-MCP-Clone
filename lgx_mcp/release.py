"""The shipped release as the server sees it: ``lgx_meta.release``, legal texts, warning catalogue, aliases.

Loaded from the database (never from ``config/``) so the texts always match the data, cached for a minute,
and reloaded when the ``release_id`` changes (rename-swap of a new data bundle, PLAN §10.7).
"""

from __future__ import annotations

import datetime as dt
import time
from dataclasses import dataclass, field
from typing import Any

from lgx_core.models import Severity, WarningCode, WarningDef
from lgx_core.warnings import Catalogue
from lgx_mcp.db import Database

CACHE_TTL_S = 60.0


@dataclass(frozen=True)
class ReleaseInfo:
    release_id: str
    sync_date: dt.date  # T
    horizon: dt.date  # H = T + 365
    schema_version: int
    rules_version: str
    parser_version: str
    pipeline_git_sha: str
    built_at: dt.datetime
    config: dict[str, Any]
    counts: dict[str, Any]
    gates: dict[str, Any]
    legal_texts: dict[str, dict[str, str]]  # code -> lang -> body
    catalogue: Catalogue
    aliases: dict[str, list[int]]  # alias_norm -> act ids
    alias_kinds: dict[str, str] = field(default_factory=dict)
    embedding_models: list[dict[str, Any]] = field(default_factory=list)
    search_api: int = 1  # 1: lgx.search_candidates of M1 (9 arguments); 2: + expansion, act boosts, options (M2)

    def legal(self, code: str, lang: str = "fr") -> str:
        texts = self.legal_texts.get(code, {})
        return texts.get(lang) or texts.get("fr") or ""

    @property
    def vectors_present(self) -> bool:
        return bool(self.embedding_models)

    @property
    def embedding_model(self) -> dict[str, Any] | None:
        """The release's ``lgx.embedding_model`` row (model id, dim, startup probe), or ``None`` (lexical)."""
        return self.embedding_models[0] if self.embedding_models else None


async def load_release(db: Database) -> ReleaseInfo:
    row = await db.fetchone(
        "SELECT release_id, sync_date, horizon, schema_version, rules_version, parser_version, pipeline_git_sha,"
        " built_at, config, counts, gates FROM lgx_meta.release"
    )
    if row is None:
        raise LookupError("lgx_meta.release is empty: not a released database")
    legal: dict[str, dict[str, str]] = {}
    for r in await db.fetch("SELECT code, lang, body FROM lgx.legal_text"):
        legal.setdefault(r["code"], {})[r["lang"]] = r["body"]
    defs: dict[WarningCode, WarningDef] = {}
    for r in await db.fetch("SELECT code, severity, text_fr, text_de, text_en, variants FROM lgx.warning_def"):
        try:
            code = WarningCode(r["code"])
        except ValueError:
            continue
        defs[code] = WarningDef(
            code=code,
            severity=Severity(r["severity"]),
            text_fr=r["text_fr"],
            text_de=r["text_de"],
            text_en=r["text_en"],
            variants=r["variants"] or {},
        )
    aliases: dict[str, list[int]] = {}
    kinds: dict[str, str] = {}
    for r in await db.fetch("SELECT alias_norm, act_id, kind FROM lgx.act_alias ORDER BY alias_norm, act_id"):
        aliases.setdefault(r["alias_norm"], []).append(int(r["act_id"]))
        kinds[r["alias_norm"]] = r["kind"]
    # to_jsonb: whatever columns the release's schema generation has (releases are rebuilt, never migrated)
    models = [
        dict(r["m"])
        for r in await db.fetch("SELECT to_jsonb(m) AS m FROM lgx.embedding_model m ORDER BY embedding_set_id")
    ]
    api = await db.fetchone(
        "SELECT max(p.pronargs) AS n FROM pg_proc p WHERE p.proname = 'search_candidates'"
        " AND p.pronamespace = 'lgx'::regnamespace"
    )
    return ReleaseInfo(
        release_id=row["release_id"],
        sync_date=row["sync_date"],
        horizon=row["horizon"],
        schema_version=int(row["schema_version"]),
        rules_version=row["rules_version"],
        parser_version=row["parser_version"],
        pipeline_git_sha=row["pipeline_git_sha"],
        built_at=row["built_at"],
        config=row["config"] or {},
        counts=row["counts"] or {},
        gates=row["gates"] or {},
        legal_texts=legal,
        catalogue=Catalogue(defs),
        aliases=aliases,
        alias_kinds=kinds,
        embedding_models=models,
        search_api=2 if api and (api["n"] or 0) >= 12 else 1,
    )


class ReleaseCache:
    def __init__(self, db: Database, ttl_s: float = CACHE_TTL_S) -> None:
        self._db = db
        self._ttl = ttl_s
        self._value: ReleaseInfo | None = None
        self._loaded_at = 0.0

    async def get(self) -> ReleaseInfo:
        now = time.monotonic()
        if self._value is None or now - self._loaded_at > self._ttl:
            if self._value is not None:
                row = await self._db.fetchone("SELECT release_id FROM lgx_meta.release")
                if row is not None and row["release_id"] == self._value.release_id:
                    self._loaded_at = now
                    return self._value
            self._value = await load_release(self._db)
            self._loaded_at = now
        return self._value

    @property
    def cached(self) -> ReleaseInfo | None:
        return self._value

    def invalidate(self) -> None:
        self._value = None
