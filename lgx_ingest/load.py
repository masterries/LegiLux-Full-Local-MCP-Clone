"""S8 load: fresh database per release, COPY, post-load, invariants (PLAN §6.1, §7, Appendix B).

Owner: loader stage. Contract: docs/dev/INTERFACES.md §6.

"Rebuild, don't migrate": every release is loaded into a **fresh** database (``legilux_build_<T>`` or
``legilux_build_<T>_sample``), dropped and recreated when it already exists, so a re-run of the same
release yields the same content (surrogate ids are assigned deterministically in ELI order).

Steps (:func:`run_load`):

1. :func:`read_inputs` — selection outputs (``manifest/<T>/``), the raw index, ``parsed/`` (parser
   version + parse issues), ``units/<T>/`` and ``chunks/<T>/``, the SPARQL vocabulary, the fetch log, the
   robots state and the denylist. A version gets text iff its manifest entry has a file that was fetched
   (raw index) and parsed (units file whose ``source_sha256`` is that file) and chunked.
2. :func:`build_plan` — pure: every table as COPY rows with pre-assigned ids (ELI → id maps in memory,
   hex → bytea, ``DateWindow`` → ``daterange '[)'``), relations resolved to acts and units, the release row.
   Acts whose selected text is not in this build (a sample release; a fetch/parse gap) are relabelled
   ``metadata_only`` + ``metadata_reason = not_loaded`` so the database stays self-consistent; the planned
   label is kept in ``lgx_meta.release.counts``.
3. :func:`apply_schema` (``db/schema/*.sql``, psql meta-commands rendered here, no psql needed) → COPY in FK
   order inside one transaction → ``90_postload.sql`` → ``lgx_meta.invariant_violations`` must be empty and
   no denylisted ELI may be present. Any constraint violation aborts with :class:`LoadError` naming the
   table, the constraint and the offending record.

Embeddings stay off (provider ``none``): ``lgx.embedding_model`` keeps ZERO rows (its CHECK has no
``none`` provider) and every ``chunk.embedding`` is NULL; the provider and ``vector_status`` are recorded in
``lgx_meta.release.config``.
"""

from __future__ import annotations

import collections
import datetime as dt
import hashlib
import json
import re
import subprocess
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import psycopg
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from psycopg.types.json import Jsonb
from psycopg.types.range import Range

from lgx_core.config import REPO_ROOT, ProjectConfig
from lgx_core.eli import ELI_BASE, expand
from lgx_core.io import atomic_write_text, read_jsonl
from lgx_core.models import (
    Act,
    ActVersion,
    Chunk,
    DataQualityIssue,
    DateWindow,
    FetchLogEntry,
    Lang,
    ManifestEntry,
    MetadataReason,
    RawFile,
    Relation,
    RelationSource,
    SourceFormat,
    TextBasis,
    Unit,
    UnitType,
    VocabConcept,
)
from lgx_core.normalise import alias_norm, num_aliases
from lgx_ingest.context import StageContext, StageResult

SCHEMA_VERSION = 1
LOADER_VERSION = "load-1"
SCHEMA_DIR = REPO_ROOT / "db" / "schema"
CC_BY_LEGALCODE = REPO_ROOT / "LICENSES" / "CC-BY-4.0.txt"
VECTOR_STATUS_NONE = "disabled:provider_none"
DB_NAME_RE = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")
POSTLOAD_PART = "90_postload.sql"
ROLES_PART = "95_roles.sql"


class LoadError(RuntimeError):
    """A constraint, consistency or input error that aborts the load (the message says what and where)."""


# ------------------------------------------------------------------------------------------- naming
def release_id_for(sync_date: dt.date, sample: bool) -> str:
    """``data-20261016`` (full) / ``data-20261016-sample``; the database and the bundle use the same T."""
    return f"data-{sync_date:%Y%m%d}" + ("-sample" if sample else "")


def default_db_name(sync_date: dt.date, sample: bool) -> str:
    """``legilux_build_20261016`` (full) / ``legilux_build_20261016_sample``."""
    return f"legilux_build_{sync_date:%Y%m%d}" + ("_sample" if sample else "")


def check_db_name(name: str) -> str:
    if not DB_NAME_RE.match(name):
        raise LoadError(f"invalid database name {name!r} (expected [a-z_][a-z0-9_]*)")
    return name


def resolve_dsn(ctx: StageContext, dsn: str | None = None) -> str:
    """``--dsn`` → ``--opt dsn=`` → ``LGX_PG_DSN`` (the build/owner role; never the read-only one)."""
    found = dsn or ctx.opt("dsn") or ctx.settings.pg_dsn
    if not found:
        raise LoadError("no PostgreSQL DSN: pass --dsn, --opt dsn=… or set LGX_PG_DSN (see .env.example)")
    return found


def target_dsn(dsn: str, dbname: str) -> str:
    return make_conninfo(dsn, dbname=dbname)


def maintenance_dsn(dsn: str, target: str) -> str:
    """The DSN's own database (or ``postgres`` when the DSN names the target itself)."""
    own = conninfo_to_dict(dsn).get("dbname")
    return make_conninfo(dsn, dbname=str(own) if own and own != target else "postgres")


def connect(dsn: str, *, autocommit: bool = False) -> psycopg.Connection[Any]:
    return psycopg.connect(dsn, autocommit=autocommit, connect_timeout=10)


# ----------------------------------------------------------------------------------- schema applier
_META = re.compile(r"^\s*\\(\w+)\s*(.*?)\s*$")
_VAR = re.compile(r"(?<![:\w]):(?:'(\w+)'|(\w+))")


def render_psql(text: str, variables: Mapping[str, str]) -> str:
    """Render the psql subset used by ``db/schema/*.sql`` (``\\set``, ``\\if :{?v}``/``\\else``/``\\endif``,
    ``:var`` / ``:'var'`` substitution) into plain SQL; any other meta-command is an error."""
    env = dict(variables)
    out: list[str] = []
    stack: list[tuple[bool, bool]] = []  # (branch active, some branch taken)

    def active() -> bool:
        return all(a for a, _ in stack)

    def cond(expr: str) -> bool:
        m = re.fullmatch(r":\{\?(\w+)\}", expr)
        if m:
            return m.group(1) in env
        if expr.lower() in ("true", "on", "1"):
            return True
        if expr.lower() in ("false", "off", "0"):
            return False
        raise LoadError(f"unsupported psql \\if expression {expr!r}")

    for line in text.splitlines():
        meta = _META.match(line)
        if meta:
            cmd, arg = meta.group(1), meta.group(2)
            if cmd == "if":
                c = active() and cond(arg)
                stack.append((c, c))
            elif cmd == "elif":
                if not stack:
                    raise LoadError("\\elif without \\if")
                _, taken = stack[-1]
                c = not taken and cond(arg)
                stack[-1] = (c, taken or c)
            elif cmd == "else":
                if not stack:
                    raise LoadError("\\else without \\if")
                _, taken = stack[-1]
                stack[-1] = (not taken, True)
            elif cmd == "endif":
                if not stack:
                    raise LoadError("\\endif without \\if")
                stack.pop()
            elif cmd == "set":
                if active():
                    name, _, value = arg.partition(" ")
                    if name != "ON_ERROR_STOP":  # we always stop on the first error
                        env[name] = value.strip()
            else:
                raise LoadError(f"unsupported psql meta-command \\{cmd}")
            continue
        if not active():
            continue

        def sub(m: re.Match[str]) -> str:
            quoted, name = m.group(1), m.group(2)
            key = quoted or name
            if key not in env:
                return m.group(0)
            return "'" + env[key].replace("'", "''") + "'" if quoted else env[key]

        out.append(_VAR.sub(sub, line))
    if stack:
        raise LoadError("unterminated \\if")
    return "\n".join(out) + "\n"


def schema_parts(schema_dir: Path = SCHEMA_DIR) -> list[Path]:
    parts = sorted(schema_dir.glob("[0-9][0-9]_*.sql"))
    if not parts:
        raise LoadError(f"no schema files in {schema_dir}")
    return parts


def apply_schema(
    conn: psycopg.Connection[Any],
    *,
    embed_dim: int,
    schema_dir: Path = SCHEMA_DIR,
    only: Sequence[str] | None = None,
) -> list[str]:
    """Apply ``db/schema/*.sql`` in file-name order (or only the named parts) on an autocommit connection."""
    applied: list[str] = []
    for part in schema_parts(schema_dir):
        if only is not None and part.name not in only:
            continue
        sql = render_psql(part.read_text(encoding="utf-8"), {"embed_dim": str(embed_dim)})
        try:
            conn.execute(sql.encode("utf-8"))
        except psycopg.Error as exc:
            raise LoadError(f"schema part {part.name} failed: {_diag(exc)}") from exc
        applied.append(part.name)
    return applied


def create_database(dsn: str, db_name: str, *, recreate: bool) -> None:
    """Fresh database for the release (``DROP … WITH (FORCE)`` + ``CREATE`` when ``recreate``)."""
    check_db_name(db_name)
    with connect(maintenance_dsn(dsn, db_name), autocommit=True) as conn:
        exists = conn.execute("SELECT 1 FROM pg_database WHERE datname = %s", (db_name,)).fetchone()
        if exists and not recreate:
            raise LoadError(f"database {db_name} exists: releases are rebuilt, never migrated (use --recreate)")
        if exists:
            conn.execute(f'DROP DATABASE "{db_name}" WITH (FORCE)')
        conn.execute(f'CREATE DATABASE "{db_name}"')


# ------------------------------------------------------------------------------------------ denylist
@dataclass(frozen=True)
class Denylist:
    """ELI work prefixes that must never appear in a release (robots-derived + manual + GDPR)."""

    prefixes: tuple[str, ...] = ()

    def matches(self, eli: str | None) -> bool:
        if not eli:
            return False
        return any(eli == p or eli.startswith(p + "/") for p in self.prefixes)

    def __len__(self) -> int:
        return len(self.prefixes)


def load_denylist(ctx: StageContext) -> Denylist:
    prefixes: set[str] = set()
    path = ctx.layout.robots_dir / "denylist.json"
    if path.exists():
        for e in json.loads(path.read_text(encoding="utf-8")).get("entries", []):
            prefixes.add(str(e["eli"]).rstrip("/"))
    for entry in ctx.config.denylist.entries:
        prefixes.add((entry.eli if entry.eli.startswith("http") else ELI_BASE + entry.eli.strip("/")).rstrip("/"))
    return Denylist(tuple(sorted(prefixes)))


def denylisted_in_db(conn: psycopg.Connection[Any], denylist: Denylist) -> dict[str, int]:
    """Rows per table that carry a denylisted ELI (must all be 0; checked after every load and by the gates)."""
    if not denylist.prefixes:
        return {}

    def m(col: str) -> str:
        return f"EXISTS (SELECT 1 FROM unnest(%(p)s::text[]) p WHERE {col} = p OR starts_with({col}, p || '/'))"

    checks = {
        "act": f"SELECT count(*) FROM lgx.act WHERE {m('eli_work')} OR {m('eli_jo')}",
        "act_version": f"SELECT count(*) FROM lgx.act_version WHERE {m('eli_version')}",
        "relation": f"SELECT count(*) FROM lgx.relation WHERE {m('src_eli')} OR {m('dst_eli')}",
        "unit": f"SELECT count(*) FROM lgx.unit WHERE {m('eli_unit')} OR {m('eli_unit_version')}",
        "data_quality_issue": f"SELECT count(*) FROM lgx.data_quality_issue WHERE {m('entity_eli')}",
    }
    out: dict[str, int] = {}
    for name, sql in checks.items():
        row = conn.execute(sql, {"p": list(denylist.prefixes)}).fetchone()  # type: ignore[arg-type]
        out[name] = int(row[0]) if row else 0
    return out


# --------------------------------------------------------------------------------------------- inputs
@dataclass
class TextVersion:
    """A version whose text is in this build: manifest entry + raw file + parse/chunk outputs."""

    entry: ManifestEntry
    raw: RawFile
    units: list[Unit]
    relations: list[Relation]
    chunks: list[Chunk]
    parser_version: str
    issues: list[DataQualityIssue] = field(default_factory=list)
    stats: dict[str, int] = field(default_factory=dict)  # parser stats (PDF: articles, candidates, pages …)
    dedup: dict[str, Any] | None = None  # recueil deduplication report summary (XML recueils)

    @property
    def eli_version(self) -> str:
        assert self.entry.eli_version is not None
        return self.entry.eli_version


@dataclass
class ReleaseInputs:
    sync_date: dt.date
    sample: bool
    acts: list[Act]
    versions: list[ActVersion]
    manifest: list[ManifestEntry]
    relations: list[Relation]
    issues: list[DataQualityIssue]
    report: dict[str, Any]
    snapshot: dict[str, Any]
    vocab: list[VocabConcept]
    texts: dict[str, TextVersion]
    missing: list[dict[str, str]]
    fetch_log: list[FetchLogEntry] = field(default_factory=list)
    robots: dict[str, Any] = field(default_factory=dict)
    denylist: Denylist = field(default_factory=Denylist)


def _require(path: Path, hint: str) -> Path:
    if not path.exists():
        raise LoadError(f"missing input {path} ({hint})")
    return path


_PDF_STAT_KEYS = (
    "pages",
    "pages_without_text",
    "articles",
    "article_candidates",
    "page_units",
    "members",
    "anonymous_members",
    "member_parts_resolved",
    "member_parts_unresolved",
    "jurisprudence_paragraphs",
    "tables",
)


def _read_json(path: Path) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return data


def read_vocab(path: Path) -> list[VocabConcept]:
    if not path.exists():
        return []
    import pyarrow.parquet as pq

    cols = set(VocabConcept.model_fields)
    rows = pq.read_table(path).to_pylist()
    return [VocabConcept(**{k: v for k, v in r.items() if k in cols}) for r in rows]


def read_inputs(ctx: StageContext) -> ReleaseInputs:
    """Read every input of the loader for T (raises :class:`LoadError` on a missing/stale input)."""
    from lgx_ingest.fetch import RawIndex, fetch_url
    from lgx_ingest.parse import parser_version

    layout = ctx.layout
    hint = "run `lgx select` (or `lgx ingest --sample`)"
    acts = list(read_jsonl(_require(layout.acts_path, hint), Act))
    versions = list(read_jsonl(_require(layout.versions_path, hint), ActVersion))
    manifest = list(read_jsonl(_require(layout.manifest_path, hint), ManifestEntry))
    relations = list(read_jsonl(_require(layout.relations_path, hint), Relation))
    report = _read_json(_require(layout.selection_report_path, hint))
    dq_path = layout.manifest_dir / "data_quality.jsonl"
    issues = list(read_jsonl(dq_path, DataQualityIssue)) if dq_path.exists() else []
    snapshot = _read_json(layout.snapshot_info) if layout.snapshot_info.exists() else {}
    vocab = read_vocab(layout.meta_dir / "vocab.parquet")
    robots_path = layout.robots_dir / "robots.json"
    robots = _read_json(robots_path) if robots_path.exists() else {}
    fetch_log = list(read_jsonl(layout.fetch_log_path, FetchLogEntry)) if layout.fetch_log_path.exists() else []

    index = RawIndex(layout)
    texts: dict[str, TextVersion] = {}
    missing: list[dict[str, str]] = []
    for e in manifest:
        if e.file is None or e.eli_version is None:
            continue
        item = {"eli_version": e.eli_version, "eli_work": e.eli_work, "format": e.file.source_format.value}
        raw = index.get(fetch_url(e))
        if raw is None:
            reason = "not_fetched_m2" if e.sample_stage == "m2" else "not_fetched"
            missing.append(item | {"reason": reason, "sample_stage": e.sample_stage or ""})
            continue
        units_path = layout.units_path(e.eli_version)
        if not units_path.exists():
            reason = "not_parsed_m2" if e.sample_stage == "m2" else "not_parsed"
            missing.append(item | {"reason": reason, "sample_stage": e.sample_stage or ""})
            continue
        units = list(read_jsonl(units_path, Unit))
        if not units:
            missing.append(item | {"reason": "no_units", "sample_stage": e.sample_stage or ""})
            continue
        for u in units:
            if u.eli_version != e.eli_version or u.eli_work != e.eli_work:
                raise LoadError(f"{units_path}: unit {u.ordinal} belongs to {u.eli_version}, not {e.eli_version}")
            if u.source_sha256 != raw.sha256:
                raise LoadError(
                    f"{units_path}: units were parsed from {u.source_sha256[:12]}…, but the raw index serves "
                    f"{raw.sha256[:12]}… for {raw.url}: re-run `lgx parse` and `lgx chunk`"
                )
        chunks_path = layout.chunks_path(e.eli_version)
        if not chunks_path.exists():
            raise LoadError(f"{e.eli_version} has units but no chunks ({chunks_path}): run `lgx chunk`")
        chunks = list(read_jsonl(chunks_path, Chunk))
        for c in chunks:
            if c.eli_version != e.eli_version or c.eli_work != e.eli_work:
                raise LoadError(f"{chunks_path}: chunk of {c.eli_version} found in the file of {e.eli_version}")
        rel_path = layout.unit_relations_path(e.eli_version)
        unit_rels = list(read_jsonl(rel_path, Relation)) if rel_path.exists() else []
        fmt = e.file.source_format
        pv = parser_version(fmt, pdf_mode=ctx.config.scope.pdf_codes)
        parse_issues: list[DataQualityIssue] = []
        parse_stats: dict[str, int] = {}
        parsed = layout.parsed_path(raw.sha256, pv)
        if parsed.exists():
            data = json.loads(parsed.read_text(encoding="utf-8"))
            pv = str(data.get("parser_version") or pv)
            parse_issues = [DataQualityIssue.model_validate(i) for i in data.get("issues", [])]
            parse_stats = {k: v for k, v in (data.get("stats") or {}).items() if isinstance(v, int)}
        structure_path = units_path.with_suffix(".structure.json")
        structure = _read_json(structure_path) if structure_path.exists() else None
        dedup = None
        if structure is not None:  # recueil structure pass (PLAN §2.6): extra issues, dedup outcome
            parse_issues += [DataQualityIssue.model_validate(i) for i in structure.get("issues", [])]
            parse_stats.update({k: v for k, v in (structure.get("stats") or {}).items() if isinstance(v, int)})
            if structure.get("dedup"):
                dedup = {k: v for k, v in structure["dedup"].items() if k != "articles"}
        texts[e.eli_version] = TextVersion(e, raw, units, unit_rels, chunks, pv, parse_issues, parse_stats, dedup)
    return ReleaseInputs(
        sync_date=ctx.sync_date,
        sample=ctx.sample,
        acts=acts,
        versions=versions,
        manifest=manifest,
        relations=relations,
        issues=issues,
        report=report,
        snapshot=snapshot,
        vocab=vocab,
        texts=texts,
        missing=missing,
        fetch_log=fetch_log,
        robots=robots,
        denylist=load_denylist(ctx),
    )


# ------------------------------------------------------------------------------------------ transforms
def hex_bytes(value: str | None) -> bytes | None:
    """Lower-case hex sha256 (models) → ``bytea``."""
    return bytes.fromhex(value) if value else None


def date_range(window: DateWindow | None) -> Range[dt.date] | None:
    """``DateWindow`` (half-open) → ``daterange '[)'``; ``None`` bounds stay open."""
    if window is None:
        return None
    return Range(window.lower, window.upper, "[)")


def fill_t(text: str, sync_date: dt.date, lang: Lang) -> str:
    """Fill ``{T}`` in legal texts (fr 16/10/2026, de 16.10.2026, en 2026-10-16)."""
    shown = {Lang.FR: f"{sync_date:%d/%m/%Y}", Lang.DE: f"{sync_date:%d.%m.%Y}", Lang.EN: sync_date.isoformat()}
    return text.replace("{T}", shown[lang])


def clean_title(value: str | None) -> str | None:
    """Display/search form of a SPARQL title: the literal two-character ``\\n`` artefact (5,301 titles of the
    2026-10-16 snapshot, e.g. « soutirés,\\net les fruits ») becomes a space; whitespace is collapsed."""
    if not value:
        return value
    return " ".join(value.replace("\\n", " ").split())


def anchor_key(value: str) -> str:
    """Loose key for textualMod destination hints vs unit anchors ('art_l234-51' ~ 'art_l_234-51')."""
    return re.sub(r"[\s_.]", "", value.lower().lstrip("#"))


class EliIndex:
    """ELI → act id resolver for relation endpoints, aliases and member acts.

    Exact matches on work, ``/jo`` and version ELIs; otherwise the ELI is walked up segment by segment
    (article ELIs ``…/art_5``, file paths, code families such as ``…/code/travail`` of
    ``…/code/travail/20260726``). Unknown ELIs (EU law, adm acts, out-of-scope works) resolve to ``None``.
    """

    def __init__(self, act_ids: Mapping[str, int], jo_ids: Mapping[str, int], version_acts: Mapping[str, int]):
        self.exact: dict[str, int] = {**version_acts, **jo_ids, **act_ids}
        family: dict[str, set[int]] = collections.defaultdict(set)
        for eli_version, act_id in version_acts.items():
            if "/consolide/" not in eli_version and not eli_version.endswith("/jo"):
                family[eli_version.rsplit("/", 1)[0]].add(act_id)
        self.family = {k: next(iter(v)) for k, v in family.items() if len(v) == 1 and k not in self.exact}

    def act_of(self, eli: str | None) -> int | None:
        if not eli or not eli.startswith(ELI_BASE):
            return None
        probe = eli.rstrip("/").split("#", 1)[0]
        floor = len(ELI_BASE + "etat/leg/")
        while len(probe) > floor:
            found = self.exact.get(probe)
            if found is None:
                found = self.family.get(probe)
            if found is not None:
                return found
            probe = probe.rsplit("/", 1)[0]
        return None


@dataclass
class Table:
    """COPY payload of one table: column names, rows, and a key per row for error messages."""

    name: str
    columns: tuple[str, ...]
    rows: list[tuple[Any, ...]] = field(default_factory=list)
    keys: list[str] = field(default_factory=list)
    shipped: bool = True

    def add(self, key: str, *values: Any) -> None:
        if len(values) != len(self.columns):
            raise AssertionError(f"{self.name}: {len(values)} values for {len(self.columns)} columns")
        self.rows.append(values)
        self.keys.append(key)

    def __len__(self) -> int:
        return len(self.rows)


@dataclass
class LoadPlan:
    release_id: str
    tables: list[Table]
    counts: dict[str, Any]
    messages: list[str]
    relabelled: dict[str, str]
    content_digest: str
    identity: dict[str, str]  # table -> identity column (sequences are advanced after COPY)

    def table(self, name: str) -> Table:
        for t in self.tables:
            if t.name == name:
                return t
        raise KeyError(name)


ACT_COLS = (
    "act_id",
    "eli_work",
    "eli_jo",
    "doc_type",
    "date_document",
    "publication_date",
    "dei_min",
    "dei_count",
    "dnl",
    "rdf_status",
    "memorial_ref",
    "historical_legal_id",
    "title",
    "title_short",
    "citation_title",
    "subjects",
    "institutions",
    "text_basis",
    "metadata_reason",
    "flags",
    "basis_version_id",
    "superseded_by_act_id",
    "rank_prior",
    "graph_modified",
)
VERSION_COLS = (
    "version_id",
    "act_id",
    "eli_version",
    "kind",
    "role",
    "date_applicability",
    "date_end_applicability",
    "rdf_status",
    "is_stub",
    "has_files",
    "has_text",
    "text_valid",
    "label",
    "source_format",
    "source_url",
    "licence",
    "rights",
    "legal_value",
    "http_last_modified",
    "manif_modified",
    "graph_modified",
    "sha256",
    "bytes",
    "fetched_at",
    "parser_version",
    "created",
)
UNIT_COLS = (
    "unit_id",
    "version_id",
    "act_id",
    "member_act_id",
    "parent_id",
    "unit_type",
    "akn_id",
    "anchor",
    "eli_unit",
    "eli_unit_version",
    "eli_unit_derived",
    "unit_version_date",
    "num_raw",
    "num_norm",
    "heading",
    "provenance",
    "breadcrumb",
    "ordinal",
    "depth",
    "text_plain",
    "text_md",
    "text_sha256",
    "is_repealed",
    "repeal_note",
    "is_modifying",
    "contains_quoted_amendment",
    "amended_by",
    "index_terms",
    "page_from",
    "page_to",
    "from_recueil_wid",
)
RELATION_COLS = (
    "relation_id",
    "src_eli",
    "src_act_id",
    "src_unit_id",
    "rel_type",
    "dst_eli",
    "dst_act_id",
    "dst_unit_id",
    "dst_unit_hint",
    "src_dei",
    "source",
)
CHUNK_COLS = (
    "chunk_id",
    "unit_id",
    "act_id",
    "version_id",
    "part_no",
    "part_count",
    "header",
    "body",
    "n_tokens",
    "doc_type",
    "text_basis",
    "valid_from",
    "valid_to",
    "searchable",
    "rank_prior",
    "content_sha256",
)
IDENTITY = {
    "lgx.act": "act_id",
    "lgx.act_version": "version_id",
    "lgx.unit": "unit_id",
    "lgx.relation": "relation_id",
    "lgx.chunk": "chunk_id",
}


def _canon(value: Any) -> Any:
    """JSON-stable form of a COPY value (for the content digest)."""
    if isinstance(value, bytes):
        return value.hex()
    if isinstance(value, dt.date | dt.datetime):
        return value.isoformat()
    if isinstance(value, Range):
        return [_canon(value.lower), _canon(value.upper), value.bounds]
    if isinstance(value, Jsonb):
        return value.obj
    if isinstance(value, list | tuple):
        return [_canon(v) for v in value]
    return value


def content_digest(tables: Iterable[Table]) -> str:
    h = hashlib.sha256()
    for t in tables:
        if not t.shipped or t.name == "lgx_meta.release":
            continue
        h.update(f"#{t.name}\n".encode())
        for row in t.rows:
            h.update(json.dumps([_canon(v) for v in row], ensure_ascii=False, separators=(",", ":")).encode())
            h.update(b"\n")
    return "sha256:" + h.hexdigest()


def git_sha(repo: Path = REPO_ROOT) -> str:
    """HEAD of the pipeline checkout (``+dirty`` when tracked files changed); ``unknown`` without git."""
    try:
        head = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True, timeout=10, check=True
        ).stdout.strip()
        dirty = subprocess.run(
            ["git", "-C", str(repo), "status", "--porcelain", "--untracked-files=no"],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    return head + ("+dirty" if dirty else "")


def inputs_digest(inputs: ReleaseInputs, config_digest: str) -> str:
    """sha256 over the SPARQL snapshot digest, the robots files, the config and every loaded file."""
    payload = {
        "snapshot_digest": inputs.snapshot.get("snapshot_digest") or inputs.report.get("snapshot_digest"),
        "config_digest": config_digest,
        "robots": {h: v.get("sha256") for h, v in sorted(inputs.robots.get("hosts", {}).items())},
        "files": sorted(t.raw.sha256 for t in inputs.texts.values()),
        "parsers": sorted({t.parser_version for t in inputs.texts.values()}),
        "rules_version": inputs.report.get("rules_version"),
    }
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return "sha256:" + hashlib.sha256(blob).hexdigest()


def legal_text_rows(config: ProjectConfig, sync_date: dt.date, *, with_vectors: bool) -> list[tuple[str, str, str]]:
    """``lgx.legal_text`` rows: ``{T}`` filled; ``attribution_vectors`` replaces ``attribution`` only when the
    release has vectors; the CC BY 4.0 legal code file is appended to the licence notice in every language."""
    rows: dict[tuple[str, str], str] = {}
    texts = config.legal_texts.texts
    for code, by_lang in texts.items():
        if code == "attribution_vectors":
            continue
        source = texts.get("attribution_vectors", by_lang) if code == "attribution" and with_vectors else by_lang
        for lang, body in source.items():
            rows[(code, lang.value)] = fill_t(body, sync_date, lang)
    if CC_BY_LEGALCODE.exists():
        legalcode = CC_BY_LEGALCODE.read_text(encoding="utf-8").strip()
        for (code, lang), body in list(rows.items()):
            if code == "cc_by_4_0":
                rows[(code, lang)] = f"{body}\n\n{legalcode}\n"
    return [(code, lang, body) for (code, lang), body in sorted(rows.items())]


def _dedupe_issues(issues: Iterable[DataQualityIssue], denylist: Denylist) -> list[DataQualityIssue]:
    grouped: dict[tuple[str, str], list[DataQualityIssue]] = {}
    for i in issues:
        if denylist.matches(i.entity_eli.split("#", 1)[0]):
            continue
        grouped.setdefault((i.entity_eli, i.code), []).append(i)
    out: list[DataQualityIssue] = []
    for (eli, code), items in sorted(grouped.items()):
        details = list(dict.fromkeys(i.detail for i in items if i.detail))
        detail = "; ".join(details[:3]) + (f" (+{len(details) - 3} more)" if len(details) > 3 else "")
        first_seen = min(i.first_seen for i in items)
        out.append(DataQualityIssue(entity_eli=eli, code=code, detail=detail or None, first_seen=first_seen))
    return out


def build_plan(
    inputs: ReleaseInputs,
    config: ProjectConfig,
    *,
    release_id: str,
    built_at: dt.datetime,
    pipeline_git_sha: str,
) -> LoadPlan:
    """Pure transformation of the inputs into COPY rows for every table (ids assigned in ELI order)."""
    t = inputs.sync_date
    deny = inputs.denylist
    messages: list[str] = []
    counts: dict[str, Any] = {}

    # ---- acts / versions: ids, relabelling
    acts = sorted(inputs.acts, key=lambda a: a.eli_work)
    act_ids: dict[str, int] = {}
    jo_ids: dict[str, int] = {}
    for i, a in enumerate(acts, 1):
        if deny.matches(a.eli_work) or deny.matches(a.eli_jo):
            raise LoadError(f"denylisted act {a.eli_work} in acts.jsonl: the selection must drop it (re-run select)")
        if a.eli_work in act_ids:
            raise LoadError(f"duplicate act {a.eli_work} in acts.jsonl")
        act_ids[a.eli_work] = i
        if a.eli_jo:
            jo_ids[a.eli_jo] = i
    versions = sorted(inputs.versions, key=lambda v: v.eli_version)
    version_ids: dict[str, int] = {}
    version_acts: dict[str, int] = {}
    for i, v in enumerate(versions, 1):
        if v.eli_work not in act_ids:
            raise LoadError(f"version {v.eli_version} belongs to {v.eli_work}, which is not in acts.jsonl")
        if v.eli_version in version_ids:
            raise LoadError(f"duplicate version {v.eli_version} in versions.jsonl")
        version_ids[v.eli_version] = i
        version_acts[v.eli_version] = act_ids[v.eli_work]
    for eli_version, text in inputs.texts.items():
        if eli_version not in version_ids:
            raise LoadError(f"text version {eli_version} has no row in versions.jsonl")
        if text.entry.eli_work != versions[version_ids[eli_version] - 1].eli_work:
            raise LoadError(f"text version {eli_version}: manifest and versions.jsonl disagree on the work")
    index = EliIndex(act_ids, jo_ids, version_acts)

    relabelled: dict[str, str] = {}
    act_table = Table("lgx.act", ACT_COLS)
    by_basis: collections.Counter[str] = collections.Counter()
    by_flag: collections.Counter[str] = collections.Counter()
    titles_cleaned = 0
    for a in acts:
        text_basis, reason, basis_id = a.text_basis, a.metadata_reason, None
        if a.text_basis is not TextBasis.METADATA_ONLY:
            if a.basis_version_eli and a.basis_version_eli in inputs.texts:
                basis_id = version_ids[a.basis_version_eli]
            else:
                relabelled[a.eli_work] = a.text_basis.value
                text_basis, reason = TextBasis.METADATA_ONLY, MetadataReason.NOT_LOADED
        superseded = index.act_of(a.superseded_by_eli) if a.superseded_by_eli else None
        if a.superseded_by_eli and superseded is None:
            messages.append(f"{a.eli_work}: superseded_by {a.superseded_by_eli} is not in scope")
        titles_cleaned += clean_title(a.title) != a.title
        by_basis[text_basis.value] += 1
        for f in a.flags:
            by_flag[f.value] += 1
        act_table.add(
            a.eli_work,
            act_ids[a.eli_work],
            a.eli_work,
            a.eli_jo,
            a.doc_type,
            a.date_document,
            a.publication_date,
            a.dei_min,
            a.dei_count,
            a.dnl,
            a.rdf_status,
            a.memorial_ref,
            a.historical_legal_id,
            clean_title(a.title),
            clean_title(a.title_short),
            clean_title(a.citation_title),
            list(a.subjects),
            list(a.institutions),
            text_basis.value,
            reason.value if reason else None,
            [f.value for f in a.flags],
            basis_id,
            superseded,
            a.rank_prior,
            a.graph_modified,
        )

    version_table = Table("lgx.act_version", VERSION_COLS)
    by_role: collections.Counter[str] = collections.Counter()
    for v in versions:
        if deny.matches(v.eli_version):
            raise LoadError(f"denylisted version {v.eli_version} in versions.jsonl")
        text = inputs.texts.get(v.eli_version)
        by_role[v.role.value + (":text" if text else "")] += 1
        if text is not None:
            entry, raw = text.entry, text.raw
            assert entry.file is not None
            if entry.text_valid is None:
                raise LoadError(f"{v.eli_version}: manifest entry with a file but without text_valid")
            values: tuple[Any, ...] = (
                True,
                date_range(entry.text_valid),
                v.label,
                entry.file.source_format.value,
                raw.url,
                entry.file.licence,
                v.rights,
                v.legal_value,
                raw.last_modified,
                entry.file.manif_modified or v.manif_modified,
                v.graph_modified,
                hex_bytes(raw.sha256),
                raw.bytes,
                raw.fetched_at,
                text.parser_version,
            )
        else:
            values = (
                False,
                None,
                v.label,
                v.source_format.value if v.source_format else None,
                v.source_url,
                v.licence,
                v.rights,
                v.legal_value,
                v.http_last_modified,
                v.manif_modified,
                v.graph_modified,
                None,
                None,
                None,
                None,
            )
        version_table.add(
            v.eli_version,
            version_ids[v.eli_version],
            act_ids[v.eli_work],
            v.eli_version,
            v.kind.value,
            v.role.value,
            v.date_applicability,
            v.date_end_applicability,
            v.rdf_status,
            v.is_stub,
            v.has_files,
            *values,
            v.created,
        )

    # ---- units, article aliases, chunks (only versions with text)
    unit_table = Table("lgx.unit", UNIT_COLS)
    alias_table = Table("lgx.article_alias", ("act_id", "num_norm", "unit_id"))
    chunk_table = Table("lgx.chunk", CHUNK_COLS)
    unit_ids: dict[tuple[str, int], int] = {}
    anchors: dict[int, dict[str, int]] = {}  # version_id -> anchor key -> unit_id (first in document order)
    unit_by_eli: dict[str, list[tuple[int, int]]] = collections.defaultdict(list)  # eli_unit -> (version, unit)
    members: dict[int, collections.Counter[int]] = collections.defaultdict(collections.Counter)
    member_heads: dict[tuple[int, int], str | None] = {}  # PDF member parts: (recueil version, member) -> heading
    units_by_type: collections.Counter[str] = collections.Counter()
    unresolved_members: collections.Counter[str] = collections.Counter()
    next_unit = 1
    for eli_version in sorted(inputs.texts):
        text = inputs.texts[eli_version]
        vid, aid = version_ids[eli_version], version_acts[eli_version]
        ords: dict[int, int] = {}
        keys = anchors.setdefault(vid, {})
        for u in sorted(text.units, key=lambda u: u.ordinal):
            if u.ordinal in ords:
                raise LoadError(f"{eli_version}: duplicate unit ordinal {u.ordinal}")
            uid = next_unit
            next_unit += 1
            ords[u.ordinal] = uid
            unit_ids[(eli_version, u.ordinal)] = uid
            parent = None
            if u.parent_ordinal is not None:
                parent = ords.get(u.parent_ordinal)
                if parent is None:
                    raise LoadError(f"{eli_version}#{u.anchor}: parent ordinal {u.parent_ordinal} not found")
            member = index.act_of(u.member_act_eli) if u.member_act_eli else None
            if u.member_act_eli and member is None:
                unresolved_members[u.member_act_eli] += 1
            if member is not None and member != aid:
                members[vid][member] += 1
                if u.source_format is SourceFormat.PDF and u.unit_type is UnitType.PART and u.num_raw is None:
                    member_heads.setdefault((vid, member), u.heading)
            for k in (u.anchor, u.akn_id):
                if k:
                    keys.setdefault(anchor_key(k), uid)
            if u.eli_unit:
                unit_by_eli[u.eli_unit].append((vid, uid))
            units_by_type[u.unit_type.value] += 1
            unit_table.add(
                f"{eli_version}#{u.anchor} (ordinal {u.ordinal})",
                uid,
                vid,
                aid,
                member,
                parent,
                u.unit_type.value,
                u.akn_id,
                u.anchor,
                u.eli_unit,
                u.eli_unit_version,
                u.eli_unit_derived,
                u.unit_version_date,
                u.num_raw,
                u.num_norm,
                u.heading,
                u.provenance,
                u.breadcrumb,
                u.ordinal,
                u.depth,
                u.text_plain,
                u.text_md,
                hex_bytes(u.text_sha256),
                u.is_repealed,
                u.repeal_note,
                u.is_modifying,
                u.contains_quoted_amendment,
                list(u.amended_by),
                list(u.index_terms),
                u.page_from,
                u.page_to,
                u.from_recueil_wid,
            )
            if u.unit_type is UnitType.ARTICLE:
                for key in num_aliases(u.num_raw, akn_id=u.akn_id, eli_unit=u.eli_unit):
                    alias_table.add(f"{eli_version}#{u.anchor}:{key}", aid, key, uid)
    if unresolved_members:
        messages.append(f"{len(unresolved_members)} member act ELI(s) not in scope (member_act_id NULL)")
    seen_alias: set[tuple[int, str, int]] = set()
    dedup_rows, dedup_keys = [], []
    for key, row in zip(alias_table.keys, alias_table.rows, strict=True):
        if (row[0], row[1], row[2]) not in seen_alias:
            seen_alias.add((row[0], row[1], row[2]))
            dedup_rows.append(row)
            dedup_keys.append(key)
    alias_table.rows, alias_table.keys = dedup_rows, dedup_keys

    next_chunk = 1
    searchable = 0
    for eli_version in sorted(inputs.texts):
        text = inputs.texts[eli_version]
        vid, aid = version_ids[eli_version], version_acts[eli_version]
        for c in sorted(text.chunks, key=lambda c: (c.unit_ordinal, c.part_no)):
            uid = unit_ids.get((eli_version, c.unit_ordinal))
            if uid is None:
                raise LoadError(f"chunk without unit: {eli_version} unit ordinal {c.unit_ordinal}")
            searchable += c.searchable
            chunk_table.add(
                f"{eli_version}:{c.unit_ordinal}:{c.part_no}",
                next_chunk,
                uid,
                aid,
                vid,
                c.part_no,
                c.part_count,
                c.header,
                c.body,
                c.n_tokens,
                c.doc_type,
                c.text_basis.value,
                c.valid_from,
                c.valid_to,
                c.searchable,
                c.rank_prior,
                hex_bytes(c.content_sha256),
            )
            next_chunk += 1

    # ---- relations: SPARQL (manifest) + AKN (per version), resolved, deduplicated, denylist-filtered
    rel_rows: dict[tuple[Any, ...], tuple[Any, ...]] = {}
    rel_counts: collections.Counter[str] = collections.Counter()
    dei = {a.eli_work: a.dei_min for a in acts}
    work_of_id = {i: a.eli_work for i, a in enumerate(acts, 1)}

    def add_relation(r: Relation, src_unit: int | None, dst_unit: int | None) -> None:
        if deny.matches(r.src_eli) or deny.matches(r.dst_eli):
            rel_counts["dropped_denylist"] += 1
            return
        src_act, dst_act = index.act_of(r.src_eli), index.act_of(r.dst_eli)
        src_dei = r.src_dei
        if src_dei is None and r.source is not RelationSource.SPARQL and src_act is not None:
            src_dei = dei.get(work_of_id[src_act])
        key = (r.src_eli, r.rel_type.value, r.dst_eli, r.source.value, src_unit, dst_unit, r.dst_unit_hint)
        if key in rel_rows:
            rel_counts["duplicates"] += 1
            return
        rel_rows[key] = (
            r.src_eli,
            src_act,
            src_unit,
            r.rel_type.value,
            r.dst_eli,
            dst_act,
            dst_unit,
            r.dst_unit_hint,
            src_dei,
            r.source.value,
        )
        rel_counts[r.source.value] += 1
        rel_counts["dst_in_scope"] += dst_act is not None

    def serving_unit(candidates: list[tuple[int, int]]) -> int:
        for vid, uid in candidates:
            window = versions[vid - 1]
            text = inputs.texts.get(window.eli_version)
            if text and text.entry.text_valid and text.entry.text_valid.contains(t):
                return uid
        return candidates[0][1]

    for r in inputs.relations:
        add_relation(r, None, None)
    for eli_version in sorted(inputs.texts):
        vid = version_ids[eli_version]
        for r in inputs.texts[eli_version].relations:
            src_unit = (
                unit_ids.get((r.src_version_eli, r.src_unit_ordinal))
                if r.src_version_eli and r.src_unit_ordinal is not None
                else None
            )
            if r.src_version_eli and r.src_unit_ordinal is not None and src_unit is None:
                rel_counts["src_unit_unresolved"] += 1
            dst_unit = None
            if r.dst_unit_hint:
                dst_unit = anchors.get(vid, {}).get(anchor_key(r.dst_unit_hint))
                rel_counts["hint_resolved" if dst_unit else "hint_unresolved"] += 1
            elif r.dst_eli in unit_by_eli:
                dst_unit = serving_unit(unit_by_eli[r.dst_eli])
                rel_counts["dst_article_resolved"] += 1
            add_relation(r, src_unit, dst_unit)
    rel_table = Table("lgx.relation", RELATION_COLS)
    for n, key in enumerate(sorted(rel_rows, key=lambda k: tuple("" if x is None else str(x) for x in k)), 1):
        rel_table.add(f"{key[0]} -{key[1]}-> {key[2]} ({key[3]})", n, *rel_rows[key])

    # ---- act aliases (config + titles), recueil members, data quality, vocab, catalogue texts
    alias_rows: dict[tuple[str, int], tuple[str, int, str, bool]] = {}
    for entry in config.aliases.aliases:
        if entry.target is None:
            continue
        target = expand(entry.target)
        aid = act_ids.get(target) or jo_ids.get(target)
        if aid is None:
            messages.append(f"alias {entry.alias!r}: target {entry.target} is not in the metadata scope")
            continue
        norm = alias_norm(entry.alias)
        alias_rows.setdefault((norm, aid), (norm, aid, entry.kind.value, entry.reviewed))
    for a in acts:
        for kind, title in (("short_title", a.title_short), ("citation_title", a.citation_title)):
            if title:
                key = (alias_norm(clean_title(title) or title), act_ids[a.eli_work])
                alias_rows.setdefault(key, (key[0], key[1], kind, False))
    act_alias_table = Table("lgx.act_alias", ("alias_norm", "act_id", "kind", "reviewed"))
    for key in sorted(alias_rows):
        act_alias_table.add(f"{key[0]} -> {work_of_id[key[1]]}", *alias_rows[key])

    member_rows: dict[tuple[int, int], tuple[Any, ...]] = {}
    for vid, counter in sorted(members.items()):
        for member in sorted(counter):
            if (vid, member) in member_heads:  # PDF code-recueil: member heading (config/recueil_members.yaml)
                member_rows[(vid, member)] = (vid, member, member_heads[(vid, member)], None, "heading", None)
            else:
                member_rows[(vid, member)] = (vid, member, None, None, "wid", None)
    for m in config.recueil_members.members:
        rec = expand(m.recueil)
        rec_vid = version_ids.get(rec)
        if rec_vid is None:
            rec_act = act_ids.get(rec)
            basis = next((a.basis_version_eli for a in acts if a.eli_work == rec), None) if rec_act else None
            rec_vid = version_ids.get(basis) if basis else None
        mem = index.act_of(expand(m.member))
        if rec_vid is None or mem is None:
            messages.append(f"recueil_members: {m.recueil} / {m.member} not resolvable in this release")
            continue
        member_rows[(rec_vid, mem)] = (rec_vid, mem, m.part_heading, None, m.method, m.reviewed_by)
    member_table = Table(
        "lgx.recueil_member",
        ("recueil_version_id", "member_act_id", "part_heading", "member_version_eli", "method", "reviewed_by"),
    )
    for key in sorted(member_rows):
        member_table.add(f"version {key[0]} member {key[1]}", *member_rows[key])

    all_issues = list(inputs.issues)
    for text in inputs.texts.values():
        all_issues.extend(text.issues)
    dq_table = Table("lgx.data_quality_issue", ("entity_eli", "code", "detail", "first_seen"))
    for i in _dedupe_issues(all_issues, deny):
        dq_table.add(f"{i.entity_eli} {i.code}", i.entity_eli, i.code, i.detail, i.first_seen)

    vocab_table = Table("lgx.vocab", ("concept_uri", "scheme", "code", "label_fr", "label_de", "label_en"))
    for c in sorted({c.concept_uri: c for c in inputs.vocab}.values(), key=lambda c: c.concept_uri):
        vocab_table.add(c.concept_uri, c.concept_uri, c.scheme, c.code, c.label_fr, c.label_de, c.label_en)

    warning_table = Table("lgx.warning_def", ("code", "severity", "text_fr", "text_de", "text_en", "variants"))
    for code, d in sorted(config.warnings.defs().items()):
        variants = {k: {lang.value: txt for lang, txt in v.items()} for k, v in sorted(d.variants.items())}
        warning_table.add(code.value, code.value, d.severity.value, d.text_fr, d.text_de, d.text_en, Jsonb(variants))
    legal_table = Table("lgx.legal_text", ("code", "lang", "body"))
    for code, lang, body in legal_text_rows(config, t, with_vectors=config.embedding.enabled):
        legal_table.add(f"{code}/{lang}", code, lang, body)

    # ---- build-only tables (excluded from the dump)
    fetch_table = Table(
        "build.fetch_log",
        ("url", "fetched_at", "http_status", "bytes", "sha256", "last_modified", "elapsed_ms"),
        shipped=False,
    )
    seen_fetch: set[tuple[str, dt.datetime]] = set()
    for f in inputs.fetch_log:
        if (f.url, f.fetched_at) in seen_fetch:
            continue
        seen_fetch.add((f.url, f.fetched_at))
        fetch_table.add(
            f.url, f.url, f.fetched_at, f.http_status, f.bytes, hex_bytes(f.sha256), f.last_modified, f.elapsed_ms
        )
    sync_table = Table("build.sync_state", ("key", "value"), shipped=False)

    # ---- counts + release row
    selection_counts = inputs.report.get("counts", {})
    not_loaded = collections.Counter(relabelled.values())
    missing_by_reason = collections.Counter(m["reason"] for m in inputs.missing)
    tables = [
        vocab_table,
        legal_table,
        warning_table,
        act_table,
        version_table,
        unit_table,
        alias_table,
        act_alias_table,
        rel_table,
        member_table,
        chunk_table,
        dq_table,
    ]
    counts = {
        "tables": {tb.name: len(tb) for tb in tables},
        "by_text_basis": dict(sorted(by_basis.items())),
        "by_flag": dict(sorted(by_flag.items())),
        "by_role": dict(sorted(by_role.items())),
        "units_by_type": dict(sorted(units_by_type.items())),
        "chunks_searchable": searchable,
        "titles_cleaned": titles_cleaned,
        "texts_loaded": len(inputs.texts),
        "texts_missing": dict(sorted(missing_by_reason.items())),
        "not_loaded_by_planned_text_basis": dict(sorted(not_loaded.items())),
        "relations": dict(sorted(rel_counts.items())),
        "pdf": {
            v: {k: t.stats[k] for k in _PDF_STAT_KEYS if k in t.stats}
            for v, t in sorted(inputs.texts.items())
            if t.entry.file is not None and t.entry.file.source_format is SourceFormat.PDF
        },
        "recueil_dedup": {v: t.dedup for v, t in sorted(inputs.texts.items()) if t.dedup is not None},
        "selection": {
            "acts": selection_counts.get("acts"),
            "by_text_basis": selection_counts.get("by_text_basis", {}),
            "by_flag": selection_counts.get("by_flag", {}),
            "stored_texts": selection_counts.get("stored_texts"),
        },
        "embedding": {"vectors": 0, "embedding_model_rows": 0},
    }
    digest = content_digest(tables)
    config_digest = config.digest()
    release_config = {
        "release_kind": "sample" if inputs.sample else "full",
        "loader_version": LOADER_VERSION,
        "config_digest": config_digest,
        "snapshot_digest": inputs.snapshot.get("snapshot_digest") or inputs.report.get("snapshot_digest"),
        "selection_config_digest": inputs.report.get("config_digest"),
        "manifestations_scope": inputs.snapshot.get("manifestations_scope"),
        "denylist_count": len(deny),
        "scope": config.scope.model_dump(mode="json"),
        "embedding": config.embedding.model_dump(mode="json", exclude={"api_key_file"}),
        "vector_status": VECTOR_STATUS_NONE if not config.embedding.enabled else "disabled:no_vectors_loaded",
        "sample_works": [w.work for w in config.sample.works] if inputs.sample else [],
    }
    release_table = Table(
        "lgx_meta.release",
        (
            "release_id",
            "sync_date",
            "built_at",
            "schema_version",
            "rules_version",
            "parser_version",
            "pipeline_git_sha",
            "inputs_digest",
            "content_digest",
            "config",
            "counts",
            "gates",
        ),
    )
    release_table.add(
        release_id,
        release_id,
        t,
        built_at,
        SCHEMA_VERSION,
        str(inputs.report.get("rules_version") or "unknown"),
        ",".join(sorted({tv.parser_version for tv in inputs.texts.values()})) or "none",
        pipeline_git_sha,
        inputs_digest(inputs, config_digest),
        digest,
        Jsonb(release_config),
        Jsonb(counts),
        Jsonb({"status": "pending", "loader": {}}),
    )
    sync_table.add("robots", "robots", Jsonb({h: v.get("sha256") for h, v in inputs.robots.get("hosts", {}).items()}))
    sync_table.add("snapshot", "snapshot", Jsonb({"digest": release_config["snapshot_digest"], "sync_date": str(t)}))
    sync_table.add("release", "release", Jsonb({"release_id": release_id, "built_at": built_at.isoformat()}))
    tables += [release_table, fetch_table, sync_table]
    if relabelled:
        messages.append(
            f"{len(relabelled)} act(s) with a selected text not in this build relabelled metadata_only/not_loaded"
            + (" (sample release)" if inputs.sample else "")
        )
    return LoadPlan(release_id, tables, counts, messages, relabelled, digest, IDENTITY)


# --------------------------------------------------------------------------------------------- COPY
def _diag(exc: psycopg.Error) -> str:
    d = exc.diag
    parts = [d.message_primary or str(exc).strip()]
    if d.constraint_name:
        parts.append(f"constraint {d.constraint_name}")
    if d.message_detail:
        parts.append(d.message_detail)
    return " | ".join(parts)


def _offending(exc: psycopg.Error, table: Table) -> str:
    m = re.search(r"COPY \w+, line (\d+)", exc.diag.context or "")
    if m:
        idx = int(m.group(1)) - 1
        if 0 <= idx < len(table.keys):
            return f" (record: {table.keys[idx]})"
    return ""


def copy_plan(conn: psycopg.Connection[Any], plan: LoadPlan, *, progress: Callable[[str], None] | None = None) -> None:
    """COPY every table in FK order in ONE transaction; deferred FKs are checked at commit."""
    current: Table | None = None
    try:
        with conn.transaction(), conn.cursor() as cur:
            for table in plan.tables:
                current = table
                if not table.rows:
                    continue
                with cur.copy(f"COPY {table.name} ({', '.join(table.columns)}) FROM STDIN") as cp:
                    for row in table.rows:
                        cp.write_row(row)
                if progress:
                    progress(f"{table.name}: {len(table)} rows")
            current = None
            for tname, col in plan.identity.items():
                cur.execute(
                    f"SELECT setval(pg_get_serial_sequence('{tname}', '{col}'), "
                    f"coalesce((SELECT max({col}) FROM {tname}), 0) + 1, false)"
                )
    except psycopg.Error as exc:
        where = f" in {current.name}{_offending(exc, current)}" if current is not None else " at commit"
        raise LoadError(f"load failed{where}: {_diag(exc)}") from exc


def invariant_violations(conn: psycopg.Connection[Any]) -> dict[str, int]:
    rows = conn.execute("SELECT check_name, n FROM lgx_meta.invariant_violations WHERE n <> 0").fetchall()
    return {str(r[0]): int(r[1]) for r in rows}


def table_counts(conn: psycopg.Connection[Any], names: Iterable[str]) -> dict[str, int]:
    out: dict[str, int] = {}
    for name in names:
        row = conn.execute(f"SELECT count(*) FROM {name}").fetchone()
        out[name] = int(row[0]) if row else 0
    return out


# -------------------------------------------------------------------------------------------- stage
def write_stage_report(ctx: StageContext, name: str, result: StageResult) -> Path:
    path = ctx.layout.reports_dir / f"{name}.json"
    atomic_write_text(path, json.dumps(result.model_dump(mode="json"), indent=2, ensure_ascii=False) + "\n")
    return path


def _timed[R](timings: dict[str, float], name: str, fn: Callable[[], R]) -> R:
    start = time.monotonic()
    try:
        return fn()
    finally:
        timings[name] = round(time.monotonic() - start, 3)


def load_release(
    ctx: StageContext, *, dsn: str, db_name: str, recreate: bool = True, release_id: str | None = None
) -> StageResult:
    """Load T into ``db_name`` on the server of ``dsn``; raises :class:`LoadError` on any failure."""
    result = StageResult(stage="load")
    timings: dict[str, float] = {}
    rid = release_id or ctx.opt("release_id") or release_id_for(ctx.sync_date, ctx.sample)
    inputs = _timed(timings, "read_inputs", lambda: read_inputs(ctx))
    if ctx.config.embedding.enabled:
        result.messages.append(
            f"embedding provider {ctx.config.embedding.provider.value}: vectors arrive with M3; "
            "this release is loaded without vectors (lexical only)"
        )
    plan = _timed(
        timings,
        "build_plan",
        lambda: build_plan(
            inputs,
            ctx.config,
            release_id=rid,
            built_at=dt.datetime.now(dt.UTC).replace(microsecond=0),
            pipeline_git_sha=git_sha(),
        ),
    )
    result.messages += plan.messages
    if ctx.dry_run:
        result.counts = dict(plan.counts["tables"])
        result.details = {"release_id": rid, "db_name": db_name, "dry_run": True, "counts": plan.counts}
        return result
    _timed(timings, "create_database", lambda: create_database(dsn, db_name, recreate=recreate))
    dim = ctx.config.embedding.dim
    with connect(target_dsn(dsn, db_name), autocommit=True) as conn:
        result.details["schema_parts"] = _timed(timings, "schema", lambda: apply_schema(conn, embed_dim=dim))
    with connect(target_dsn(dsn, db_name)) as conn:
        _timed(timings, "copy", lambda: copy_plan(conn, plan, progress=lambda m: ctx.console.print(f"  COPY {m}")))
    with connect(target_dsn(dsn, db_name), autocommit=True) as conn:
        _timed(timings, "postload", lambda: apply_schema(conn, embed_dim=dim, only=[POSTLOAD_PART]))
        violations = invariant_violations(conn)
        denied = {k: v for k, v in denylisted_in_db(conn, inputs.denylist).items() if v}
        loaded = table_counts(conn, [tb.name for tb in plan.tables])
        size_row = conn.execute("SELECT pg_database_size(current_database())").fetchone()
        loader_gate = {
            "invariants": violations,
            "denylist": denied,
            "ok": not violations and not denied,
            "checked_at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        }
        conn.execute(
            "UPDATE lgx_meta.release SET gates = jsonb_set(gates, '{loader}', %s) WHERE release_id = %s",
            (Jsonb(loader_gate), rid),
        )
    mismatched = {tb.name: (len(tb), loaded.get(tb.name)) for tb in plan.tables if loaded.get(tb.name) != len(tb)}
    if mismatched:
        raise LoadError(f"row counts after COPY differ from the plan: {mismatched}")
    if violations:
        raise LoadError(f"invariant violations in {db_name}: {violations}")
    if denied:
        raise LoadError(f"denylisted ELIs present in {db_name}: {denied}")
    result.counts = {name.split(".", 1)[1]: n for name, n in loaded.items()}
    result.counts["relabelled_not_loaded"] = len(plan.relabelled)
    result.counts["texts_loaded"] = len(inputs.texts)
    result.counts["texts_missing"] = len(inputs.missing)
    result.outputs = [target_dsn(_redact(dsn), db_name)]
    result.details = {
        "release_id": rid,
        "db_name": db_name,
        "content_digest": plan.content_digest,
        "database_bytes": int(size_row[0]) if size_row else None,
        "timings_s": timings,
        "counts": plan.counts,
        "missing_texts": inputs.missing,
        "relabelled": dict(sorted(plan.relabelled.items())),
    }
    return result


def _redact(dsn: str) -> str:
    info = conninfo_to_dict(dsn)
    if "password" in info:
        info["password"] = "***"
    return make_conninfo(**{k: str(v) for k, v in info.items()})


def run_load(
    ctx: StageContext, *, dsn: str | None = None, db_name: str | None = None, recreate: bool = True
) -> StageResult:
    """S8: build the release database for T (fresh, idempotent per release name) and report counts."""
    try:
        target = check_db_name(db_name or ctx.opt("db_name") or default_db_name(ctx.sync_date, ctx.sample))
        result = load_release(ctx, dsn=resolve_dsn(ctx, dsn), db_name=target, recreate=recreate)
    except (LoadError, psycopg.OperationalError) as exc:
        result = StageResult(stage="load", ok=False, messages=[f"load failed: {exc}"])
    if not ctx.dry_run:
        result.outputs.append(str(write_stage_report(ctx, "load", result)))
    return result


__all__ = [
    "Denylist",
    "EliIndex",
    "LoadError",
    "LoadPlan",
    "ReleaseInputs",
    "Table",
    "TextVersion",
    "anchor_key",
    "apply_schema",
    "build_plan",
    "copy_plan",
    "create_database",
    "date_range",
    "default_db_name",
    "denylisted_in_db",
    "fill_t",
    "hex_bytes",
    "invariant_violations",
    "load_denylist",
    "read_inputs",
    "release_id_for",
    "render_psql",
    "resolve_dsn",
    "run_load",
]
