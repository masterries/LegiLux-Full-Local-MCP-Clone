"""S9 anomaly gates (PLAN §6.8) — the M1 subset, plus the invariant checks of the loaded release.

A gate either passes, fails (the build fails; the previous release stays current), warns (reported,
never blocking) or is skipped (its input does not exist yet, e.g. no previous snapshot to compare with).
``run_gates`` writes ``reports/<T>/gates.json`` and, when the release database is reachable, stores the
result in ``lgx_meta.release.gates``.

File gates (no database): robots, SPARQL completeness, Q01/Q02 rows and usable consolidations vs the
previous snapshot/selection, text-basis buckets, stubs, flag-vs-window disagreements, fetch errors, parse
failures, sample expectations, CODE/Constitution texts present.

Database gates (``--opt dsn=…``/``LGX_PG_DSN`` + ``--opt db_name=…``, default ``legilux_build_<T>[_sample]``):
``lgx_meta.invariant_violations`` empty, denylist absence, release row, counts vs selection report and the
loader inputs, no chunk without a unit / of a non-text version / outside its version window, text_basis
consistency (labels vs stored texts vs ``lgx.serving_at(T)``), core texts and article counts, licence,
embedding off (0 vectors, 0 embedding_model rows), canned lexical query.
"""

from __future__ import annotations

import collections
import datetime as dt
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

import psycopg
from psycopg.types.json import Jsonb

from lgx_core.config import DataLayout, ProjectConfig
from lgx_core.io import atomic_write_text, read_jsonl
from lgx_core.models import CC_BY_4_0, DataQualityIssue, FetchLogEntry, ManifestEntry, SourceFormat, TextBasis
from lgx_ingest.context import StageContext, StageResult
from lgx_ingest.load import (
    LoadError,
    connect,
    default_db_name,
    denylisted_in_db,
    invariant_violations,
    load_denylist,
    resolve_dsn,
    target_dsn,
)

Status = Literal["pass", "fail", "warn", "skip"]
CORE_TYPES = frozenset({"CODE", "CODE_RECUEIL", "Constitution"})
CANNED_QUERY = "travail"


@dataclass
class Gate:
    gate: str
    status: Status
    value: Any = None
    threshold: Any = None
    detail: str = ""
    examples: list[str] = field(default_factory=list)


@dataclass
class GateReport:
    gates: list[Gate] = field(default_factory=list)

    def add(
        self,
        gate: str,
        status: Status,
        value: Any = None,
        threshold: Any = None,
        detail: str = "",
        examples: list[str] | None = None,
    ) -> Gate:
        g = Gate(gate, status, value, threshold, detail, (examples or [])[:10])
        self.gates.append(g)
        return g

    def check(self, gate: str, ok: bool, value: Any = None, threshold: Any = None, detail: str = "", **kw: Any) -> Gate:
        return self.add(gate, "pass" if ok else "fail", value, threshold, detail, kw.get("examples"))

    @property
    def ok(self) -> bool:
        return not any(g.status == "fail" for g in self.gates)

    def summary(self) -> dict[str, int]:
        return dict(collections.Counter(g.status for g in self.gates))

    def as_json(self) -> dict[str, Any]:
        return {
            "status": "pass" if self.ok else "fail",
            "summary": self.summary(),
            "gates": [asdict(g) for g in self.gates],
        }


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return data


def previous_date(root: Path, kind: str, sync_date: dt.date, name: str) -> dt.date | None:
    """Latest T' < T for which ``<root>/<kind>/<T'>/<name>`` exists."""
    base = root / kind
    if not base.exists():
        return None
    found: list[dt.date] = []
    for d in base.iterdir():
        try:
            day = dt.date.fromisoformat(d.name)
        except ValueError:
            continue
        if day < sync_date and (d / name).exists():
            found.append(day)
    return max(found) if found else None


def _change(prev: float, cur: float) -> float:
    return abs(cur - prev) / prev if prev else (0.0 if cur == prev else 1.0)


# ------------------------------------------------------------------------------------- file gates
def file_gates(ctx: StageContext, report: GateReport) -> dict[str, Any]:
    """Gates on the build files of T (no database). Returns the selection report for the DB gates."""
    layout, cfg = ctx.layout, ctx.config
    robots = _read_json(layout.robots_dir / "robots.json")
    if robots is None:
        report.add("robots", "warn", detail=f"no robots state for {ctx.sync_date} (offline build without `lgx robots`)")
    else:
        report.check(
            "robots",
            not robots.get("paused") and not robots.get("hard_stop"),
            {"paused": robots.get("paused"), "hard_stop": robots.get("hard_stop")},
            "acknowledged, /filestore allowed",
        )

    snap = _read_json(layout.snapshot_info)
    if snap is None:
        report.add("sparql_completeness", "fail", detail=f"no snapshot {layout.snapshot_info}")
    else:
        checks = (snap.get("completeness") or {}).get("checks", {})
        bad = [k for k, v in checks.items() if not v.get("ok")]
        if not checks:
            report.add("sparql_completeness", "warn", None, "Q17 record", "snapshot has no Q17 completeness record")
        else:
            report.check(
                "sparql_completeness",
                not bad,
                {k: (v.get("distinct_pulled"), v.get("distinct_endpoint")) for k, v in checks.items()},
                "distinct pulled == COUNT(DISTINCT) at the endpoint (Q17)",
                examples=bad,
            )
        prev_t = previous_date(layout.root, "meta", ctx.sync_date, "_snapshot.json")
        prev = _read_json(DataLayout(layout.root, prev_t).snapshot_info) if prev_t else None
        for table, gate in (("acts", "q01_rows"), ("consolidations", "q02_rows")):
            cur_rows = snap.get("tables", {}).get(table, {}).get("rows")
            prev_rows = (prev or {}).get("tables", {}).get(table, {}).get("rows")
            if not prev_rows or cur_rows is None:
                report.add(gate, "skip", cur_rows, "drop <= 2 % vs previous snapshot", "no previous snapshot")
            else:
                drop = (prev_rows - cur_rows) / prev_rows
                report.check(gate, drop <= 0.02, cur_rows, "drop <= 2 %", f"previous {prev_t}: {prev_rows}")

    sel = _read_json(layout.selection_report_path)
    if sel is None:
        report.add("selection_report", "fail", detail=f"missing {layout.selection_report_path} (run `lgx select`)")
        return {}
    counts = sel.get("counts", {})
    by_basis: dict[str, int] = counts.get("by_text_basis", {})
    prev_t = previous_date(layout.root, "manifest", ctx.sync_date, "selection_report.json")
    prev_sel = _read_json(DataLayout(layout.root, prev_t).selection_report_path) if prev_t else None
    if prev_sel is None:
        report.add("usable_consolidations", "skip", by_basis.get("consolidated"), "change <= 2 %", "no previous run")
        report.add("text_basis_buckets", "skip", by_basis, "change <= 10 % per bucket", "no previous run")
        report.add("stubs", "skip", counts.get("by_flag", {}).get("stub_no_text"), "+20 per run", "no previous run")
    else:
        prev_basis = prev_sel.get("counts", {}).get("by_text_basis", {})
        cur_c, prev_c = by_basis.get("consolidated", 0), prev_basis.get("consolidated", 0)
        report.check("usable_consolidations", _change(prev_c, cur_c) <= 0.02, cur_c, "change <= 2 %", f"prev {prev_c}")
        moved = {k: (prev_basis.get(k, 0), v) for k, v in by_basis.items() if _change(prev_basis.get(k, 0), v) > 0.10}
        report.check("text_basis_buckets", not moved, by_basis, "change <= 10 % per bucket", json.dumps(moved))
        stubs = counts.get("by_flag", {}).get("stub_no_text", 0) + by_basis.get("stub_fallback", 0)
        prev_stubs = prev_sel.get("counts", {}).get("by_flag", {}).get("stub_no_text", 0) + prev_basis.get(
            "stub_fallback", 0
        )
        report.check("stubs", stubs - prev_stubs <= 20, stubs, "+20 per run", f"prev {prev_stubs}")
    dq_path = layout.manifest_dir / "data_quality.jsonl"
    conflicts = [
        i.entity_eli
        for i in (read_jsonl(dq_path, DataQualityIssue) if dq_path.exists() else [])
        if i.code == "consolidation_flag_window_conflict" and (i.detail or "").startswith("flag applicable")
    ]
    report.check("flag_window_disagreements", len(conflicts) <= 5, len(conflicts), "<= 5 (2 at T0)", examples=conflicts)

    attempts = [
        f
        for f in (read_jsonl(layout.fetch_log_path, FetchLogEntry) if layout.fetch_log_path.exists() else [])
        if f.fetched_at.date() >= ctx.sync_date - dt.timedelta(days=1)
    ]
    # final outcome per URL: a transient error that a retry recovered is reported, not counted (§6.8 intent)
    last: dict[str, FetchLogEntry] = {}
    for f in sorted(attempts, key=lambda f: f.fetched_at):
        last[f.url] = f
    errors = [u for u, f in last.items() if f.http_status not in (200, 304)]
    transient = sum(1 for f in attempts if f.http_status not in (200, 304)) - len(errors)
    if not attempts:
        report.add("fetch_errors", "skip", 0, "<= 1 % of fetched URLs", "no fetch attempts for T (cache reused)")
    else:
        report.check(
            "fetch_errors",
            len(errors) / len(last) <= 0.01,
            len(errors),
            "<= 1 % of fetched URLs (final outcome)",
            f"{len(last)} URLs, {len(attempts)} attempts, {transient} transient error(s) recovered by a retry",
            examples=errors,
        )

    parse = _read_json(layout.units_dir / "_parse_report.json")
    if parse is None:
        report.add("parse_failures", "fail", detail="no parse report (run `lgx parse`)")
    else:
        failures = parse.get("details", {}).get("failures", [])
        c = parse.get("counts", {})
        attempted = c.get("parsed", 0) + c.get("cached", 0) + c.get("failed", 0)
        core = [f["eli_version"] for f in failures if f.get("doc_type") in CORE_TYPES]
        rate = len(failures) / attempted if attempted else 0.0
        report.check(
            "parse_failures",
            rate <= 0.01 and not core,
            len(failures),
            "<= 1 %, none on CODE/Constitution/PDF code-recueil",
            examples=[f["eli_version"] for f in failures],
        )

    sample = sel.get("sample", {})
    mismatches = sample.get("mismatches", [])
    frozen = cfg.sample.snapshot_date == ctx.sync_date
    if ctx.sample:
        report.add(
            "sample_expectations",
            "pass" if not mismatches else ("fail" if frozen else "warn"),
            f"{sample.get('matched')}/{sample.get('works')}",
            "all match on the frozen snapshot",
            "" if frozen else f"T {ctx.sync_date} differs from the frozen snapshot {cfg.sample.snapshot_date}",
            [m.get("work", "") for m in mismatches],
        )
    licence = counts.get("by_format", {})
    report.add("selection_formats", "pass", licence, None, "formats of the files to fetch")
    return sel


# --------------------------------------------------------------------------------------- DB gates
def _scalar(conn: psycopg.Connection[Any], sql: str, params: Any = None) -> Any:
    row = conn.execute(sql, params).fetchone()
    return row[0] if row else None


def _rows(conn: psycopg.Connection[Any], sql: str, params: Any = None) -> list[tuple[Any, ...]]:
    return conn.execute(sql, params).fetchall()


def expected_core_works(cfg: ProjectConfig, sample: bool) -> list[str]:
    """Sample works whose text must be present: CODE/Constitution/code-recueil groups of stage m1."""
    if not sample:
        return []
    return [w.eli_work for w in cfg.sample.works if w.group in ("core", "code_recueil") and w.stage == "m1"]


def db_gates(ctx: StageContext, conn: psycopg.Connection[Any], report: GateReport, sel: dict[str, Any]) -> None:
    t = ctx.sync_date
    violations = invariant_violations(conn)
    report.check("invariants", not violations, violations, "every lgx_meta.invariant_violations row n = 0")
    denied = {k: v for k, v in denylisted_in_db(conn, load_denylist(ctx)).items() if v}
    report.check("denylist_absent", not denied, denied, "no denylisted ELI in any table")

    rel = _rows(conn, "SELECT release_id, sync_date, counts, config FROM lgx_meta.release")
    report.check("release_row", len(rel) == 1 and rel[0][1] == t, [(r[0], str(r[1])) for r in rel], f"one row, T={t}")
    counts: dict[str, Any] = rel[0][2] if rel else {}
    release_config: dict[str, Any] = rel[0][3] if rel else {}

    # counts vs selection report and loader inputs
    sel_counts = sel.get("counts", {})
    acts = int(_scalar(conn, "SELECT count(*) FROM lgx.act"))
    report.check("acts_vs_selection", acts == sel_counts.get("acts"), acts, sel_counts.get("acts"))
    versions = int(_scalar(conn, "SELECT count(*) FROM lgx.act_version"))
    versions_jsonl = (
        sum(1 for _ in ctx.layout.versions_path.open(encoding="utf-8")) if ctx.layout.versions_path.exists() else None
    )
    report.check("versions_vs_selection", versions == versions_jsonl, versions, versions_jsonl)
    loaded_basis = dict(_rows(conn, "SELECT text_basis::text, count(*) FROM lgx.act GROUP BY 1"))
    not_loaded: dict[str, int] = counts.get("not_loaded_by_planned_text_basis", {})
    planned: dict[str, int] = sel_counts.get("by_text_basis", {})
    partition = {
        b.value: (loaded_basis.get(b.value, 0) + not_loaded.get(b.value, 0), planned.get(b.value, 0))
        for b in TextBasis
        if b is not TextBasis.METADATA_ONLY
    }
    meta_expected = planned.get("metadata_only", 0) + sum(not_loaded.values())
    partition["metadata_only"] = (loaded_basis.get("metadata_only", 0), meta_expected)
    bad = {k: v for k, v in partition.items() if v[0] != v[1]}
    report.check(
        "text_basis_vs_selection",
        not bad,
        partition,
        "loaded + not_loaded == selection label, per bucket",
        json.dumps(bad) if bad else "",
    )
    reason_mismatch = int(
        _scalar(
            conn,
            "SELECT count(*) FROM lgx.act WHERE (text_basis = 'metadata_only') <> (metadata_reason IS NOT NULL)",
        )
    )
    report.check("metadata_reason_consistent", reason_mismatch == 0, reason_mismatch, 0)
    for table, key in (("unit", "lgx.unit"), ("chunk", "lgx.chunk"), ("relation", "lgx.relation")):
        n = int(_scalar(conn, f"SELECT count(*) FROM {key}"))
        exp = counts.get("tables", {}).get(key)
        report.check(f"{table}_count_vs_plan", n == exp, n, exp)

    # chunks
    chunk_problems = dict(
        _rows(
            conn,
            """
        SELECT 'chunk_unit_mismatch', count(*) FROM lgx.chunk c JOIN lgx.unit u USING (unit_id)
         WHERE c.version_id <> u.version_id OR c.act_id <> u.act_id
        UNION ALL SELECT 'chunk_of_version_without_text', count(*) FROM lgx.chunk c
          JOIN lgx.act_version v ON v.version_id = c.version_id WHERE NOT v.has_text
        UNION ALL SELECT 'chunk_outside_version_window', count(*) FROM lgx.chunk c
          JOIN lgx.act_version v ON v.version_id = c.version_id
         WHERE NOT (daterange(c.valid_from, c.valid_to, '[)') <@ v.text_valid)
        UNION ALL SELECT 'chunk_of_repealed_or_modifying_unit', count(*) FROM lgx.chunk c
          JOIN lgx.unit u USING (unit_id) WHERE u.is_repealed OR u.is_modifying
        UNION ALL SELECT 'text_version_without_units', count(*) FROM lgx.act_version v
         WHERE v.has_text AND NOT EXISTS (SELECT 1 FROM lgx.unit u WHERE u.version_id = v.version_id)
        UNION ALL SELECT 'units_of_version_without_text', count(*) FROM lgx.unit u
          JOIN lgx.act_version v USING (version_id) WHERE NOT v.has_text
        """,
        )
    )
    bad_chunks = {k: v for k, v in chunk_problems.items() if v}
    report.check("chunk_unit_consistency", not bad_chunks, chunk_problems, "all 0")

    # text_basis consistency: labels vs stored texts vs the runtime recomputation at T
    label_problems = dict(
        _rows(
            conn,
            """
        SELECT 'text_label_without_basis_text', count(*) FROM lgx.act a
          LEFT JOIN lgx.act_version v ON v.version_id = a.basis_version_id
         WHERE a.text_basis <> 'metadata_only' AND (v.version_id IS NULL OR NOT v.has_text OR v.act_id <> a.act_id)
        UNION ALL SELECT 'metadata_only_with_basis', count(*) FROM lgx.act
         WHERE text_basis = 'metadata_only' AND basis_version_id IS NOT NULL
        UNION ALL SELECT 'chunk_label_differs_from_act', count(*) FROM lgx.chunk c JOIN lgx.act a USING (act_id)
         WHERE c.version_id = a.basis_version_id AND c.text_basis <> a.text_basis
        """,
        )
    )
    runtime = _rows(
        conn,
        """
        SELECT a.eli_work, a.text_basis::text, s.text_basis::text, lower(v.text_valid)
          FROM lgx.act a JOIN lgx.act_version v ON v.version_id = a.basis_version_id,
               LATERAL lgx.serving_at(a.act_id, %s) s
         WHERE s.text_basis IS DISTINCT FROM a.text_basis
        """,
        (t,),
    )
    # documented exception (INTERFACES §4): text windows that start after T (not yet in force / future)
    unexpected = [r for r in runtime if not (r[2] == "metadata_only" and r[3] is not None and r[3] > t)]
    label_problems["serving_at_T_differs"] = len(unexpected)
    bad_labels = {k: v for k, v in label_problems.items() if v}
    report.check(
        "text_basis_consistent",
        not bad_labels,
        label_problems,
        "all 0 (windows starting after T excepted)",
        examples=[f"{r[0]}: {r[1]} vs serving_at {r[2]}" for r in unexpected],
    )

    # core texts: CODE / Constitution / code-recueils present (sample: the m1 core works)
    expected = expected_core_works(ctx.config, ctx.sample)
    core_rows = {
        r[0]: (r[1], int(r[2]))
        for r in _rows(
            conn,
            """
        SELECT a.eli_work, a.text_basis::text,
               (SELECT count(*) FROM lgx.unit u WHERE u.version_id = a.basis_version_id AND u.unit_type = 'article')
          FROM lgx.act a WHERE a.eli_work = ANY(%s) OR (a.doc_type = ANY(%s) AND a.basis_version_id IS NOT NULL)
        """,
            (expected, sorted(CORE_TYPES)),
        )
    }
    missing = [w for w in expected if w not in core_rows or core_rows[w][0] == "metadata_only" or core_rows[w][1] == 0]
    report.check(
        "core_texts_present",
        not missing,
        {w.rsplit("/eli/", 1)[-1]: v for w, v in core_rows.items()},
        "every CODE/Constitution of the sample (m1) has text and articles",
        examples=missing,
    )
    if ctx.sample and ctx.layout.manifest_path.exists():
        wanted = [
            e.eli_version
            for e in read_jsonl(ctx.layout.manifest_path, ManifestEntry)
            if e.in_sample
            and e.sample_stage == "m1"
            and e.file is not None
            and e.eli_version
            and e.file.source_format is not SourceFormat.PDF
        ]
        have = {
            r[0]
            for r in _rows(
                conn, "SELECT eli_version FROM lgx.act_version WHERE has_text AND eli_version = ANY(%s)", (wanted,)
            )
        }
        absent = [v for v in wanted if v not in have]
        report.check(
            "sample_texts_loaded",
            not absent,
            f"{len(have)}/{len(wanted)}",
            "every m1 sample file (XML/HTML) has text in the release",
            examples=absent,
        )
    prev_counts = _previous_article_counts(ctx)
    if prev_counts is None:
        report.add("core_article_counts", "skip", None, "change <= 2 % without a new version", "no previous release")
    else:
        drift = {
            w: (prev_counts[w], n)
            for w, (_b, n) in core_rows.items()
            if w in prev_counts and _change(prev_counts[w], n) > 0.02
        }
        report.check("core_article_counts", not drift, len(core_rows), "change <= 2 %", json.dumps(drift))

    unlicensed = int(
        _scalar(
            conn, "SELECT count(*) FROM lgx.act_version WHERE has_text AND licence IS DISTINCT FROM %s", (CC_BY_4_0,)
        )
    )
    if ctx.config.scope.licence_policy == "cc_by_only":
        report.check("licence_cc_by_only", unlicensed == 0, unlicensed, 0)
    vectors = int(_scalar(conn, "SELECT count(*) FROM lgx.chunk WHERE embedding IS NOT NULL"))
    models = int(_scalar(conn, "SELECT count(*) FROM lgx.embedding_model"))
    if not ctx.config.embedding.enabled:
        report.check(
            "embedding_off",
            vectors == 0 and models == 0 and release_config.get("vector_status") == "disabled:provider_none",
            {"vectors": vectors, "embedding_model": models, "vector_status": release_config.get("vector_status")},
            "provider none: no vectors, no model row",
        )
    query = ctx.opt("canned_query") or CANNED_QUERY
    sql = "SELECT count(*) FROM lgx.search_candidates(%s, NULL, %s, '{}', NULL, NULL, 10, NULL, false)"
    hits = int(_scalar(conn, sql, (query, t)))
    if hits == 0:  # small or unusual releases: query with the words of the first searchable chunk instead
        body = _scalar(conn, "SELECT left(body, 80) FROM lgx.chunk WHERE searchable ORDER BY chunk_id LIMIT 1")
        if body:
            query = str(body)
            hits = int(_scalar(conn, sql, (query, t)))
    searchable = int(_scalar(conn, "SELECT count(*) FROM lgx.chunk WHERE searchable"))
    report.check(
        "canned_lexical_query", hits > 0 and searchable > 0, hits, "> 0 hits", f"{query!r}; {searchable} searchable"
    )


def _previous_article_counts(ctx: StageContext) -> dict[str, int] | None:
    prev_t = previous_date(ctx.layout.root, "reports", ctx.sync_date, "gates.json")
    if prev_t is None:
        return None
    data = _read_json(DataLayout(ctx.layout.root, prev_t).reports_dir / "gates.json") or {}
    for g in data.get("gates", []):
        if g.get("gate") == "core_texts_present" and isinstance(g.get("value"), dict):
            from lgx_core.eli import ELI_BASE

            return {ELI_BASE + k: int(v[1]) for k, v in g["value"].items()}
    return None


# -------------------------------------------------------------------------------------------- stage
def evaluate(ctx: StageContext, *, dsn: str | None = None, db_name: str | None = None) -> GateReport:
    report = GateReport()
    sel = file_gates(ctx, report)
    if ctx.opt("skip_db") in ("1", "true", "yes"):
        report.add("database", "skip", detail="--opt skip_db=1")
        return report
    try:
        resolved = resolve_dsn(ctx, dsn)
    except LoadError as exc:
        report.add("database", "fail", detail=str(exc))
        return report
    name = db_name or ctx.opt("db_name") or default_db_name(ctx.sync_date, ctx.sample)
    try:
        with connect(target_dsn(resolved, name), autocommit=True) as conn:
            db_gates(ctx, conn, report, sel)
            conn.execute(
                "UPDATE lgx_meta.release SET gates = %s || jsonb_build_object('loader', gates->'loader')",
                (Jsonb(report.as_json() | {"checked_at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds")}),),
            )
    except psycopg.Error as exc:
        report.add("database", "fail", detail=f"{name}: {exc}".strip())
    return report


def run_gates(ctx: StageContext, *, dsn: str | None = None, db_name: str | None = None) -> StageResult:
    """S9: anomaly gates for T; ``ok`` is False when any gate fails."""
    report = evaluate(ctx, dsn=dsn, db_name=db_name)
    result = StageResult(stage="gates", ok=report.ok)
    result.counts = report.summary()
    result.details = report.as_json()
    result.messages = [f"{g.gate}: {g.status.upper()} {g.detail}".strip() for g in report.gates if g.status == "fail"]
    path = ctx.layout.reports_dir / "gates.json"
    atomic_write_text(path, json.dumps(result.details, indent=2, ensure_ascii=False, default=str) + "\n")
    result.outputs = [str(path)]
    return result
