"""Sizing report from the sample: articles, text bytes, chunks per stratum (PLAN §4.1, §6.2).

Reads the build files of T (``manifest/<T>/``, ``raw/index.jsonl``, ``units/<T>/``, ``chunks/<T>/``), so it
runs without a database; with ``--opt dsn=…`` (or ``LGX_PG_DSN``) it adds the size of the release
database per table. Output: ``reports/<T>/sizing.json``.

Strata are the ``config/sample.yaml`` groups (core, code_recueil, consolidation, jo, parser_fixture,
selection_fixture). The extrapolation to the full text basis multiplies the per-version means of the
sample by the selection's stored-text counts per version kind — an estimate [E] to re-base PLAN §4, not a
measurement; codes are not extrapolated (they are all in the sample).
"""

from __future__ import annotations

import collections
import json
import statistics
from typing import Any

import psycopg

from lgx_core.io import atomic_write_text, read_jsonl
from lgx_core.models import Chunk, ManifestEntry, Unit, UnitType, VersionKind
from lgx_ingest.context import StageContext, StageResult
from lgx_ingest.load import LoadError, connect, default_db_name, resolve_dsn, target_dsn

METRICS = ("versions", "units", "articles", "repealed", "text_bytes", "raw_bytes", "chunks", "tokens", "split_articles")
CODE_KINDS = frozenset({VersionKind.CODE, VersionKind.CODE_RECUEIL, VersionKind.RECUEIL})


def version_metrics(units: list[Unit], chunks: list[Chunk], raw_bytes: int) -> dict[str, int]:
    return {
        "versions": 1,
        "units": len(units),
        "articles": sum(u.unit_type is UnitType.ARTICLE for u in units),
        "repealed": sum(u.is_repealed for u in units),
        "text_bytes": sum(len((u.text_plain or "").encode("utf-8")) for u in units),
        "raw_bytes": raw_bytes,
        "chunks": len(chunks),
        "tokens": sum(c.n_tokens or 0 for c in chunks),
        "split_articles": len({c.unit_ordinal for c in chunks if c.part_count > 1}),
    }


def _add(total: dict[str, int], m: dict[str, int]) -> None:
    for k in METRICS:
        total[k] = total.get(k, 0) + m.get(k, 0)


def sizing(ctx: StageContext) -> dict[str, Any]:
    from lgx_ingest.fetch import RawIndex, fetch_url

    layout = ctx.layout
    if not layout.manifest_path.exists():
        raise LoadError(f"missing {layout.manifest_path} (run `lgx select`)")
    by_work = ctx.config.sample.by_work()
    index = RawIndex(layout)
    per_version: list[dict[str, Any]] = []
    stored_by_kind: collections.Counter[str] = collections.Counter()
    for e in read_jsonl(layout.manifest_path, ManifestEntry):
        if e.text_valid is not None and e.kind is not None:
            stored_by_kind[e.kind.value] += 1
        if e.file is None or e.eli_version is None or not layout.units_path(e.eli_version).exists():
            continue
        units = list(read_jsonl(layout.units_path(e.eli_version), Unit))
        cpath = layout.chunks_path(e.eli_version)
        chunks = list(read_jsonl(cpath, Chunk)) if cpath.exists() else []
        raw = index.get(fetch_url(e))
        sample = by_work.get(e.eli_work)
        per_version.append(
            {
                "eli_version": e.eli_version,
                "group": sample.group if sample else "outside_sample",
                "doc_type": e.doc_type,
                "kind": e.kind.value if e.kind else None,
                "format": e.file.source_format.value,
                "role": e.role.value,
                **version_metrics(units, chunks, raw.bytes if raw else 0),
            }
        )
    strata: dict[str, dict[str, dict[str, int]]] = {"group": {}, "doc_type": {}, "kind": {}, "format": {}}
    totals: dict[str, int] = {}
    for v in per_version:
        _add(totals, v)
        for dim in strata:
            _add(strata[dim].setdefault(str(v[dim]), {}), v)
    estimate = _extrapolate(per_version, stored_by_kind)
    return {
        "sync_date": ctx.sync_date.isoformat(),
        "sample": ctx.sample,
        "totals": totals,
        "strata": {dim: dict(sorted(rows.items())) for dim, rows in strata.items()},
        "versions": sorted(per_version, key=lambda v: (v["group"], v["eli_version"])),
        "extrapolation": estimate,
    }


def _extrapolate(per_version: list[dict[str, Any]], stored_by_kind: collections.Counter[str]) -> dict[str, Any]:
    """Per-version means of non-code versions × stored-text counts of the selection, per kind [E]."""
    out: dict[str, Any] = {"note": "[E] sample means × selection stored-text counts; codes not extrapolated"}
    full: dict[str, float] = {}
    for kind in (VersionKind.CONSOLIDATED.value, VersionKind.ORIGINAL.value):
        rows = [v for v in per_version if v["kind"] == kind]
        n = stored_by_kind.get(kind, 0)
        if not rows:
            continue
        means = {k: statistics.fmean(v[k] for v in rows) for k in ("articles", "text_bytes", "chunks", "tokens")}
        medians = {k: statistics.median(v[k] for v in rows) for k in ("articles", "text_bytes", "chunks", "tokens")}
        out[kind] = {"sample_versions": len(rows), "stored_texts": n, "mean": means, "median": medians}
        for k, m in means.items():
            full[k] = full.get(k, 0.0) + m * n
    codes = [v for v in per_version if v["kind"] in {k.value for k in CODE_KINDS}]
    for v in codes:
        for k in ("articles", "text_bytes", "chunks", "tokens"):
            full[k] = full.get(k, 0.0) + v[k]
    out["codes_in_sample"] = len(codes)
    out["codes_selected"] = sum(stored_by_kind.get(k.value, 0) for k in CODE_KINDS)
    out["full_text_basis_estimate"] = {k: round(v) for k, v in full.items()}
    return out


def db_sizes(ctx: StageContext) -> dict[str, Any] | None:
    if ctx.opt("skip_db") in ("1", "true", "yes"):
        return None
    try:
        dsn = resolve_dsn(ctx)
    except LoadError:
        return None
    name = ctx.opt("db_name") or default_db_name(ctx.sync_date, ctx.sample)
    try:
        with connect(target_dsn(dsn, name), autocommit=True) as conn:
            total = conn.execute("SELECT pg_database_size(current_database())").fetchone()
            rows = conn.execute(
                "SELECT n.nspname || '.' || c.relname, pg_total_relation_size(c.oid), pg_relation_size(c.oid) "
                "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname IN ('lgx', 'lgx_meta', 'build') AND c.relkind = 'r' ORDER BY 2 DESC"
            ).fetchall()
    except psycopg.Error as exc:
        return {"db_name": name, "error": str(exc).strip()}
    return {
        "db_name": name,
        "database_bytes": int(total[0]) if total else None,
        "tables": {r[0]: {"total_bytes": int(r[1]), "heap_bytes": int(r[2])} for r in rows},
    }


def run_report(ctx: StageContext) -> StageResult:
    """Write ``reports/<T>/sizing.json`` (per-stratum sizes of the sample + an [E] extrapolation)."""
    result = StageResult(stage="report")
    try:
        data = sizing(ctx)
    except LoadError as exc:
        result.ok = False
        result.messages.append(str(exc))
        return result
    db = db_sizes(ctx)
    if db is not None:
        data["database"] = db
    path = ctx.layout.reports_dir / "sizing.json"
    atomic_write_text(path, json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    result.outputs = [str(path)]
    result.counts = dict(data["totals"])
    result.details = {
        "strata": data["strata"]["group"],
        "extrapolation": data["extrapolation"].get("full_text_basis_estimate"),
        "database_bytes": (db or {}).get("database_bytes"),
    }
    return result
