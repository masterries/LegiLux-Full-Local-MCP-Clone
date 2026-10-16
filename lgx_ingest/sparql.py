"""S1 discover: flat, unsorted SPARQL pulls into data/meta/<T>/*.parquet (Appendix A).

Owner: agent A (M1). Contract: docs/dev/INTERFACES.md §3. Query texts: sparql/*.rq, rendered with
``lgx_ingest.sparql_templates.render_query``.

* One connection, sequential requests ≥ ``max(LGX_RATE_LIMIT_S, scope.fetch.min_spacing_s)`` apart
  (shared with the robots fetch), project User-Agent, POST form-encoded, read timeout < the ≈ 45 s
  gateway timeout, retries with backoff on 429/5xx (``Retry-After`` honoured).
* Whole tables are pulled unsorted in one request. Q17 checks completeness of the act and consolidation
  pulls; when a pull fails or is incomplete the same SELECT is re-run in partitions
  (``sparql/partitions/``: year windows, predicates or types) and merged.
* Values are typed on the client (``lgx_core.rules.snapshot``); malformed literals become null with the
  raw string kept in ``<col>_raw``. Rows are kept as returned; aggregation is selection's job.
"""

from __future__ import annotations

import csv
import datetime as dt
import hashlib
import io
import json
import re
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import pyarrow as pa
import pyarrow.parquet as pq

from lgx_core.eli import ELI_BASE as ELI_ROOT
from lgx_core.io import atomic_write_text
from lgx_core.rules.snapshot import TABLES, ColKind, MetaSnapshot, TableSpec, convert_row, iri_code
from lgx_ingest.context import StageContext, StageResult
from lgx_ingest.sparql_templates import MAX_VALUES, PlaceholderValue, render_query

ACCEPT_CSV = "text/csv"
ACCEPT_NT = "application/n-triples"
RESOURCE_TYPE = "http://data.legilux.public.lu/resource/authority/resource-type/"
JOLUX = "http://data.legilux.public.lu/resource/ontology/jolux#"
DEFAULT_TIMEOUT_S = 40.0  # below the ≈ 45 s gateway timeout (Appendix A.0)
SNAPSHOT_FORMAT = 1

# Pull order: Q17 (completeness) runs with Q01/Q02; Q-MANIF last (its IRIs come from a selection pass).
DEFAULT_TABLES = (
    "acts",
    "consolidations",
    "conso_formats",
    "conso_base_acts",
    "rel_modifies_repeals",
    "rel_other",
    "titles",
    "memorial_refs",
    "vocab",
    "act_formats",
    "manifestations",
)


# ----------------------------------------------------------------------------------------- HTTP
class SparqlError(RuntimeError):
    """A SPARQL request failed for good (after retries, or with a non-retriable error)."""

    def __init__(self, message: str, *, status: int | None = None, body: str = "") -> None:
        super().__init__(message)
        self.status = status
        self.body = body

    @property
    def sorted_cap(self) -> bool:
        """Virtuoso SR353: sorted TOP over 10,000 rows (Appendix A.5)."""
        return "SR353" in self.body


class Throttle:
    """Minimum spacing between requests on one connection (≤ 1 request/s across both hosts)."""

    def __init__(
        self,
        spacing_s: float,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.spacing_s = spacing_s
        self._clock = clock
        self._sleep = sleep
        self._last: float | None = None
        self.requests = 0

    def wait(self) -> None:
        if self._last is not None:
            delay = self._last + self.spacing_s - self._clock()
            if delay > 0:
                self._sleep(delay)
        self._last = self._clock()
        self.requests += 1


@dataclass
class Response:
    text: str
    status: int
    bytes: int
    seconds: float
    fetched_at: dt.datetime
    attempts: int = 1


@dataclass
class RetryPolicy:
    max_retries: int = 5
    backoff_s: Sequence[float] = (1, 2, 4, 8, 16, 32, 60)
    retry_on: Sequence[int] = (429, 500, 502, 503, 504)

    def delay(self, attempt: int, retry_after: str | None) -> float:
        if retry_after:
            try:
                return min(max(float(retry_after), 0.0), 300.0)
            except ValueError:
                pass
        return float(self.backoff_s[min(attempt, len(self.backoff_s) - 1)])


class PoliteHttp:
    """One httpx connection shared by robots and SPARQL: throttle, User-Agent, retries, no HEAD."""

    def __init__(
        self,
        *,
        user_agent: str,
        spacing_s: float,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        retry: RetryPolicy | None = None,
        client: httpx.Client | None = None,
        throttle: Throttle | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.retry = retry or RetryPolicy()
        self.throttle = throttle or Throttle(spacing_s, sleep=sleep)
        self._sleep = sleep
        timeout = httpx.Timeout(timeout_s, connect=min(10.0, timeout_s))
        self._client = client or httpx.Client(timeout=timeout, follow_redirects=False)
        self._client.headers["User-Agent"] = user_agent
        self._own = client is None

    def close(self) -> None:
        if self._own:
            self._client.close()

    def __enter__(self) -> PoliteHttp:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def request(self, method: str, url: str, **kwargs: Any) -> Response:
        if method.upper() == "HEAD":
            raise ValueError("HEAD is never used against Legilux (it returns 403)")
        attempt = 0
        while True:
            self.throttle.wait()
            started = time.monotonic()
            fetched_at = dt.datetime.now(dt.UTC)
            try:
                resp = self._client.request(method, url, **kwargs)
            except httpx.TransportError as exc:  # timeouts, resets
                if attempt >= self.retry.max_retries:
                    raise SparqlError(f"{method} {url}: {type(exc).__name__}: {exc}") from exc
                self._sleep(self.retry.delay(attempt, None))
                attempt += 1
                continue
            seconds = time.monotonic() - started
            body = resp.text
            if resp.status_code == 200:
                return Response(body, 200, len(resp.content), seconds, fetched_at, attempt + 1)
            error = SparqlError(f"{method} {url}: HTTP {resp.status_code}", status=resp.status_code, body=body[:2000])
            if error.sorted_cap or resp.status_code not in self.retry.retry_on or attempt >= self.retry.max_retries:
                raise error
            self._sleep(self.retry.delay(attempt, resp.headers.get("Retry-After")))
            attempt += 1

    def get(self, url: str, **kwargs: Any) -> Response:
        return self.request("GET", url, **kwargs)


class SparqlClient:
    """POST form-encoded queries to the endpoint (Accept: CSV for SELECT, N-Triples for CONSTRUCT)."""

    def __init__(self, http: PoliteHttp, endpoint: str) -> None:
        self.http = http
        self.endpoint = endpoint

    def query(self, text: str, accept: str = ACCEPT_CSV) -> Response:
        return self.http.request("POST", self.endpoint, data={"query": text}, headers={"Accept": accept})


def http_from_context(ctx: StageContext, *, timeout_s: float | None = None) -> PoliteHttp:
    fetch = ctx.config.scope.fetch
    timeout = timeout_s or float(ctx.opt("sparql_timeout_s") or min(DEFAULT_TIMEOUT_S, ctx.settings.http_timeout_s))
    return PoliteHttp(
        user_agent=ctx.settings.user_agent,
        spacing_s=max(ctx.settings.rate_limit_s, fetch.min_spacing_s),
        timeout_s=timeout,
        retry=RetryPolicy(max_retries=fetch.max_retries, backoff_s=fetch.backoff_s, retry_on=fetch.retry_on),
    )


# -------------------------------------------------------------------------------- result parsing
def parse_csv(text: str) -> list[dict[str, str]]:
    """Virtuoso CSV → list of {variable: raw string}; an empty cell is an unbound variable."""
    reader = csv.reader(io.StringIO(text))
    try:
        header = next(reader)
    except StopIteration:
        return []
    return [dict(zip(header, row, strict=False)) for row in reader if row]


def typed_rows(spec: TableSpec, raw_rows: Iterable[Mapping[str, str | None]]) -> list[dict[str, Any]]:
    return [convert_row(spec, r) for r in raw_rows]


_NT_LINE = re.compile(r'^<([^>]*)>\s+<([^>]*)>\s+(<[^>]*>|_:\S+|"(?:[^"\\]|\\.)*"(?:@[\w-]+|\^\^<[^>]*>)?)\s*\.\s*$')
_NT_ESC = re.compile(r"\\(u[0-9A-Fa-f]{4}|U[0-9A-Fa-f]{8}|[tbnrf\"'\\])")
_ESC = {"t": "\t", "b": "\b", "n": "\n", "r": "\r", "f": "\f", '"': '"', "'": "'", "\\": "\\"}


def _unescape(s: str) -> str:
    def sub(m: re.Match[str]) -> str:
        code = m.group(1)
        return chr(int(code[1:], 16)) if code[0] in "uU" else _ESC[code]

    return _NT_ESC.sub(sub, s)


def parse_ntriples(text: str) -> list[tuple[str, str, str, str | None]]:
    """N-Triples → (subject, predicate, object, lang); IRIs without <>, literals unescaped."""
    out: list[tuple[str, str, str, str | None]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = _NT_LINE.match(line)
        if not m:
            continue
        s, p, o = m.group(1), m.group(2), m.group(3)
        lang = None
        if o.startswith("<"):
            obj = o[1:-1]
        elif o.startswith('"'):
            end = o.rfind('"')
            obj = _unescape(o[1:end])
            suffix = o[end + 1 :]
            if suffix.startswith("@"):
                lang = suffix[1:].lower()
        else:
            obj = o
        out.append((s, p, obj, lang))
    return out


SKOS = "http://www.w3.org/2004/02/skos/core#"


def vocab_rows(triples: Iterable[tuple[str, str, str, str | None]]) -> list[dict[str, Any]]:
    """Q10 CONSTRUCT triples → ``vocab`` rows (= ``VocabConcept``)."""
    concepts: dict[str, dict[str, Any]] = {}
    for s, p, o, lang in triples:
        c = concepts.setdefault(s, {"concept_uri": s, "scheme": None, "code": None, "labels": {}})
        if p == SKOS + "inScheme" and c["scheme"] is None:
            c["scheme"] = o
        elif p == SKOS + "notation" and c["code"] is None:
            c["code"] = o
        elif p == SKOS + "prefLabel":
            c["labels"].setdefault(lang or "", o)
    rows = []
    for c in concepts.values():
        if c["scheme"] is None:
            continue
        labels = c["labels"]
        rows.append(
            {
                "concept_uri": c["concept_uri"],
                "scheme": c["scheme"],
                "code": c["code"] or iri_code(c["concept_uri"]),
                "label_fr": labels.get("fr") or labels.get(""),
                "label_de": labels.get("de"),
                "label_en": labels.get("en"),
            }
        )
    return rows


# --------------------------------------------------------------------------------- parquet I/O
def arrow_schema(spec: TableSpec) -> pa.Schema:
    fields: list[pa.Field] = []
    for col, kind in spec.columns.items():
        if kind is ColKind.DATE:
            fields += [pa.field(col, pa.date32()), pa.field(f"{col}_raw", pa.string())]
        elif kind is ColKind.DATETIME:
            fields += [pa.field(col, pa.timestamp("us", tz="UTC")), pa.field(f"{col}_raw", pa.string())]
        elif kind is ColKind.INT:
            fields.append(pa.field(col, pa.int64()))
        else:
            fields.append(pa.field(col, pa.string()))
    return pa.schema(fields)


def write_table(path: Path, spec: TableSpec, rows: Sequence[Mapping[str, Any]]) -> str:
    """Write rows as zstd parquet atomically; returns the file sha256.

    Rows (duplicates included) are sorted by all columns, key first: the endpoint returns unsorted
    results, so sorting makes the file — and the snapshot digest — independent of server order (and
    halves the size).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    table = pa.Table.from_pylist(list(rows), schema=arrow_schema(spec))
    order = [spec.key, *(c for c in spec.columns if c != spec.key)]
    if table.num_rows:
        table = table.sort_by([(c, "ascending") for c in order])
    tmp = path.with_name(f".{path.name}.tmp")
    pq.write_table(table, tmp, compression="zstd", compression_level=15)
    tmp.replace(path)
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_table(path: Path) -> list[dict[str, Any]]:
    return pq.read_table(path).to_pylist()


def load_snapshot(meta_dir: Path, tables: Iterable[str] | None = None) -> MetaSnapshot:
    """Read ``<meta_dir>/*.parquet`` + ``_snapshot.json`` into a :class:`MetaSnapshot`."""
    if not meta_dir.is_dir():
        raise FileNotFoundError(f"no snapshot at {meta_dir} (run `lgx discover` / `lgx meta pull` first)")
    info_path = meta_dir / "_snapshot.json"
    info = json.loads(info_path.read_text(encoding="utf-8")) if info_path.exists() else {}
    names = list(tables) if tables is not None else list(TABLES)
    data = {n: read_table(meta_dir / f"{n}.parquet") for n in names if (meta_dir / f"{n}.parquet").exists()}
    return MetaSnapshot(tables=data, info=info)


# ---------------------------------------------------------------------------------- partitions
Params = dict[str, PlaceholderValue]


@dataclass(frozen=True)
class Partition:
    query: str
    params: Params = field(default_factory=dict)


def year_windows(last_year: int) -> list[tuple[int, int]]:
    """Windows that keep every partition well under 10k rows (≈ 2–8k acts per year since 2000)."""
    windows = [(1000, 1899), (1900, 1944), (1945, 1969), (1970, 1984), (1985, 1994)]
    windows += [(y, min(y + 1, 1999)) for y in range(1995, 2000, 2)]
    windows += [(y, y) for y in range(2000, last_year + 2)]
    return windows


def partitions_for(table: str, ctx_params: Params, last_year: int) -> list[Partition]:
    """Fallback partitions of one table (Appendix A.0); merged and de-duplicated by the caller."""
    years = year_windows(last_year)
    if table in ("acts", "titles", "memorial_refs"):
        q = {"acts": "q01_acts", "titles": "q07_titles", "memorial_refs": "q08_memorial_refs"}[table]
        return [Partition(f"partitions/{q}_year", {"Y0": y0, "Y1": y1}) for y0, y1 in years] + [
            Partition(f"partitions/{q}_undated")
        ]
    if table in ("consolidations", "conso_formats"):
        q = {"consolidations": "q02_consolidations", "conso_formats": "q03_conso_formats"}[table]
        return [Partition(f"partitions/{q}_year", {"Y0": y0, "Y1": y1}) for y0, y1 in years]
    if table == "rel_modifies_repeals":
        return [
            Partition("partitions/q05_rel_modifies_repeals_rel", {"IRIS": [JOLUX + r]}) for r in ("modifies", "repeals")
        ]
    if table == "rel_other":
        return [
            Partition("partitions/q06_rel_other_rel", {"IRIS": [JOLUX + r]})
            for r in ("basedOn", "cites", "rectifies", "transposes")
        ]
    if table == "act_formats":
        types = ctx_params.get("IRIS")
        assert isinstance(types, Sequence) and not isinstance(types, str)
        return [Partition("q19_act_formats", {"IRIS": [t]}) for t in types]
    return []


# --------------------------------------------------------------------------------------- pulls
@dataclass
class PullResult:
    table: str
    rows: list[dict[str, Any]]
    query: str
    query_sha256: str
    bytes: int = 0
    seconds: float = 0.0
    requests: int = 0
    fetched_at: str = ""
    partitioned: bool = False
    errors: list[str] = field(default_factory=list)

    def info(self, file_sha256: str) -> dict[str, Any]:
        spec = TABLES[self.table]
        return {
            "file": f"{self.table}.parquet",
            "file_sha256": file_sha256,
            "query": self.query,
            "query_sha256": self.query_sha256,
            "rows": len(self.rows),
            "distinct_keys": len({r[spec.key] for r in self.rows if r.get(spec.key)}),
            "bytes": self.bytes,
            "seconds": round(self.seconds, 3),
            "requests": self.requests,
            "fetched_at": self.fetched_at,
            "partitioned": self.partitioned,
            "errors": self.errors,
        }


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _dedupe(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[Any, ...]] = set()
    out = []
    for r in rows:
        key = tuple(r.values())
        if key not in seen:
            seen.add(key)
            out.append(r)
    return out


class Puller:
    """Runs the table pulls of one discover run; keeps per-request accounting."""

    def __init__(self, client: SparqlClient, *, last_year: int, log: Callable[[str], None] = lambda _m: None) -> None:
        self.client = client
        self.last_year = last_year
        self.log = log

    def _run(self, spec: TableSpec, query: str, params: Params) -> tuple[list[dict[str, Any]], Response, str]:
        text = render_query(query, **params)
        resp = self.client.query(text, ACCEPT_NT if spec.construct else ACCEPT_CSV)
        rows = vocab_rows(parse_ntriples(resp.text)) if spec.construct else typed_rows(spec, parse_csv(resp.text))
        return rows, resp, text

    def pull(self, table: str, params: Params | None = None, *, force_partitions: bool = False) -> PullResult:
        spec = TABLES[table]
        params = params or {}
        text = render_query(spec.query, **params)
        result = PullResult(table, [], spec.query, _sha(text))
        if not force_partitions:
            try:
                rows, resp, _ = self._run(spec, spec.query, params)
                result.rows, result.bytes, result.seconds = rows, resp.bytes, resp.seconds
                result.requests, result.fetched_at = resp.attempts, resp.fetched_at.isoformat()
                self.log(f"{table}: {len(rows)} rows, {resp.bytes} B, {resp.seconds:.2f} s")
                return result
            except SparqlError as exc:
                result.errors.append(str(exc))
                self.log(f"{table}: single pull failed ({exc}); switching to partitions")
        return self._partitioned(spec, params, result)

    def _partitioned(self, spec: TableSpec, params: Params, result: PullResult) -> PullResult:
        parts = partitions_for(spec.name, params, self.last_year)
        if not parts:
            raise SparqlError(f"{spec.name}: pull failed and no partitioned fallback exists: {result.errors}")
        rows: list[dict[str, Any]] = []
        hashes = []
        for part in parts:
            got, resp, text = self._run(spec, part.query, part.params)
            rows.extend(got)
            hashes.append(_sha(text))
            result.bytes += resp.bytes
            result.seconds += resp.seconds
            result.requests += resp.attempts
            result.fetched_at = resp.fetched_at.isoformat()
        result.rows = _dedupe(rows)
        result.partitioned = True
        result.query = f"partitions/{spec.name} ({len(parts)} requests)"
        result.query_sha256 = _sha("\n".join(hashes))
        self.log(f"{spec.name}: {len(result.rows)} rows from {len(parts)} partitions")
        return result

    def completeness(self) -> dict[str, dict[str, int]]:
        """Q17: rows and distinct subjects per class (Act, Consolidation)."""
        resp = self.client.query(render_query("checks/q17_completeness"))
        out: dict[str, dict[str, int]] = {}
        for r in parse_csv(resp.text):
            cls = iri_code(r.get("c"))
            if cls:
                out[cls] = {"rows": int(r.get("rows") or 0), "distinct": int(r.get("distinct") or 0)}
        return out


COMPLETENESS_KEYS = {"acts": "Act", "consolidations": "Consolidation"}


def check_complete(result: PullResult, expected: Mapping[str, Mapping[str, int]]) -> dict[str, Any] | None:
    cls = COMPLETENESS_KEYS.get(result.table)
    if cls is None or cls not in expected:
        return None
    spec = TABLES[result.table]
    pulled = len({r[spec.key] for r in result.rows if r.get(spec.key)})
    want = expected[cls]["distinct"]
    return {"class": cls, "distinct_endpoint": want, "distinct_pulled": pulled, "ok": pulled == want}


def manifestation_iris(snapshot: MetaSnapshot, ctx: StageContext) -> list[str]:
    """Version/act IRIs that need Q-MANIF: every version that will get a file (sample works only in
    sample mode, unless ``--opt manif=all``)."""
    from lgx_core.rules.select import manifestation_candidates

    scope = ctx.opt("manif", "sample" if ctx.sample else "all")
    if scope == "none":
        return []
    only = None if scope == "all" else set(ctx.config.sample.by_work())
    return manifestation_candidates(snapshot, ctx.sync_date, ctx.config, works=only)


def text_type_iris(ctx: StageContext) -> list[str]:
    return [RESOURCE_TYPE + t for t in ctx.config.scope.text_types]


def run_discover(
    ctx: StageContext, queries: Sequence[str] | None = None, *, http: PoliteHttp | None = None
) -> StageResult:
    """Run the full-table pulls (all, or the named query ids / table names) and write the snapshot.

    ``http`` injects the connection (tests); by default one is built from the context's politeness
    settings.
    """
    wanted = _resolve_tables(queries)
    meta = ctx.layout.meta_dir
    info_path = ctx.layout.snapshot_info
    info: dict[str, Any] = json.loads(info_path.read_text(encoding="utf-8")) if info_path.exists() else {}
    info.update(
        {
            "format": SNAPSHOT_FORMAT,
            "sync_date": ctx.sync_date.isoformat(),
            "endpoint": ctx.settings.sparql_url,
            "user_agent": ctx.settings.user_agent,
            "act_formats_coverage": None,
        }
    )
    info.setdefault("tables", {})
    started = dt.datetime.now(dt.UTC)
    result = StageResult(stage="discover")
    if ctx.dry_run:
        result.details["would_pull"] = wanted
        return result
    with http or http_from_context(ctx) as http:
        puller = Puller(SparqlClient(http, ctx.settings.sparql_url), last_year=ctx.sync_date.year, log=ctx.console.log)
        expected: dict[str, dict[str, int]] = {}
        if any(t in COMPLETENESS_KEYS for t in wanted):
            expected = puller.completeness()
            info["completeness"] = {"endpoint": expected, "checks": {}}
        for table in wanted:
            if table == "manifestations":
                continue
            params: Params = {"IRIS": text_type_iris(ctx)} if table == "act_formats" else {}
            pulled = puller.pull(table, params)
            check = check_complete(pulled, expected)
            if check and not check["ok"]:
                ctx.console.log(f"{table}: incomplete pull {check}; retrying in partitions")
                pulled = puller.pull(table, params, force_partitions=True)
                check = check_complete(pulled, expected)
            if check is not None:
                info["completeness"]["checks"][table] = check
                if not check["ok"]:
                    result.ok = False
                    result.messages.append(f"{table}: completeness check failed {check}")
            sha = write_table(meta / f"{table}.parquet", TABLES[table], pulled.rows)
            info["tables"][table] = pulled.info(sha)
            result.counts[table] = len(pulled.rows)
            result.outputs.append(str(meta / f"{table}.parquet"))
        if "manifestations" in wanted:
            snapshot = load_snapshot(meta, [t for t in TABLES if t != "manifestations"])
            iris = manifestation_iris(snapshot, ctx)
            pulled = pull_manifestations(puller, iris, int(ctx.opt("manif_batch") or MAX_VALUES))
            sha = write_table(meta / "manifestations.parquet", TABLES["manifestations"], pulled.rows)
            info["tables"]["manifestations"] = pulled.info(sha) | {"iris": len(iris)}
            info["manifestations_scope"] = ctx.opt("manif", "sample" if ctx.sample else "all")
            result.counts["manifestations"] = len(pulled.rows)
            result.outputs.append(str(meta / "manifestations.parquet"))
        result.counts["requests"] = http.throttle.requests
    info["started_at"], info["finished_at"] = started.isoformat(), dt.datetime.now(dt.UTC).isoformat()
    info["snapshot_digest"] = snapshot_digest(info)
    atomic_write_text(info_path, json.dumps(info, indent=2, ensure_ascii=False) + "\n")
    result.outputs.append(str(info_path))
    result.details["snapshot_digest"] = info["snapshot_digest"]
    return result


def pull_manifestations(puller: Puller, iris: Sequence[str], batch: int = MAX_VALUES) -> PullResult:
    """Q-MANIF in VALUES batches of ≤ 500 IRIs, sent by POST."""
    spec = TABLES["manifestations"]
    result = PullResult("manifestations", [], spec.query, "")
    hashes = []
    for i in range(0, len(iris), max(1, min(batch, MAX_VALUES))):
        chunk = list(iris[i : i + batch])
        rows, resp, text = puller._run(spec, spec.query, {"WORKS": chunk})
        result.rows.extend(rows)
        hashes.append(_sha(text))
        result.bytes += resp.bytes
        result.seconds += resp.seconds
        result.requests += resp.attempts
        result.fetched_at = resp.fetched_at.isoformat()
    result.rows = _dedupe(result.rows)
    result.query_sha256 = _sha("\n".join(hashes))
    return result


def snapshot_digest(info: Mapping[str, Any]) -> str:
    tables = info.get("tables", {})
    blob = json.dumps({k: v.get("file_sha256") for k, v in sorted(tables.items())}, sort_keys=True)
    return "sha256:" + _sha(blob)


_QUERY_TO_TABLE = {spec.query.split(".")[0].split("_")[0]: name for name, spec in TABLES.items()}


def _resolve_tables(queries: Sequence[str] | None) -> list[str]:
    """``None`` → all tables; otherwise query ids (``q01``, ``q_manif``) or table names."""
    if not queries:
        return list(DEFAULT_TABLES)
    out = []
    for q in queries:
        key = q.strip().lower()
        if key in TABLES:
            out.append(key)
        elif key in ("q_manif", "qmanif", "manif"):
            out.append("manifestations")
        elif key.split("_")[0] in _QUERY_TO_TABLE:
            out.append(_QUERY_TO_TABLE[key.split("_")[0]])
        else:
            raise ValueError(f"unknown query or table {q!r}; known: {sorted(TABLES)}")
    return [t for t in DEFAULT_TABLES if t in out]


# ------------------------------------------------------------------------ CSV snapshot import
def import_csv_snapshot(
    sources: Mapping[str, Path],
    out_dir: Path,
    *,
    sync_date: dt.date,
    note: str = "",
    act_formats_coverage: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Convert SPARQL CSV / N-Triples result files (e.g. the frozen research pulls) into a snapshot.

    Uses the same parser as a live pull, so the frozen golden snapshot and ``data/meta/<T>`` are
    byte-compatible. ``sources`` maps table names to result files.
    """
    info: dict[str, Any] = {
        "format": SNAPSHOT_FORMAT,
        "sync_date": sync_date.isoformat(),
        "source": "import",
        "note": note,
        "act_formats_coverage": dict(act_formats_coverage) if act_formats_coverage else None,
        "tables": {},
    }
    for table, src in sources.items():
        spec = TABLES[table]
        text = src.read_text(encoding="utf-8")
        rows = vocab_rows(parse_ntriples(text)) if spec.construct else typed_rows(spec, parse_csv(text))
        sha = write_table(out_dir / f"{table}.parquet", spec, rows)
        info["tables"][table] = {
            "file": f"{table}.parquet",
            "file_sha256": sha,
            "source_file": src.name,
            "rows": len(rows),
            "distinct_keys": len({r[spec.key] for r in rows if r.get(spec.key)}),
            "bytes": len(text.encode("utf-8")),
        }
    info["snapshot_digest"] = snapshot_digest(info)
    atomic_write_text(out_dir / "_snapshot.json", json.dumps(info, indent=2, ensure_ascii=False) + "\n")
    return info


# ------------------------------------------------------------------------------ fixture subsets
def subset_snapshot(src: Path, out: Path, works: Iterable[str], *, note: str = "") -> dict[str, Any]:
    """Commit-sized subset of a snapshot: every row about ``works`` (their acts, versions, formats,
    inbound/outbound edges, titles, Mémorial refs, manifestations) plus the acts that are ``basedOn``
    targets of them (needed for ``base_legale_abrogee``). Used for ``tests/fixtures/meta/``."""
    snap = load_snapshot(src)
    work_set = {w if w.startswith("http") else ELI_ROOT + w for w in works}
    jo = {w + "/jo" for w in work_set}
    versions = {r["c"] for r in snap.rows("consolidations") if r["cw"] in work_set}
    targets = {r["tgt"] for r in snap.rows("rel_other") if r["src"] in jo and r["rel"] == "basedOn"}
    act_set = jo | targets
    keep: dict[str, list[dict[str, Any]]] = {
        "acts": [r for r in snap.rows("acts") if r["a"] in act_set],  # type: ignore[misc]
        "consolidations": [r for r in snap.rows("consolidations") if r["c"] in versions],  # type: ignore[misc]
        "conso_formats": [r for r in snap.rows("conso_formats") if r["c"] in versions],  # type: ignore[misc]
        "conso_base_acts": [r for r in snap.rows("conso_base_acts") if r["cw"] in work_set],  # type: ignore[misc]
        "rel_modifies_repeals": [
            r
            for r in snap.rows("rel_modifies_repeals")
            if r["tgt"] in jo or r["src"] in jo  # type: ignore[misc]
        ],
        "rel_other": [r for r in snap.rows("rel_other") if r["src"] in jo],  # type: ignore[misc]
        "titles": [r for r in snap.rows("titles") if r["a"] in act_set],  # type: ignore[misc]
        "memorial_refs": [r for r in snap.rows("memorial_refs") if r["a"] in act_set],  # type: ignore[misc]
        "vocab": [r for r in snap.rows("vocab") if str(r["scheme"]).endswith("/resource-type")],  # type: ignore[misc]
        "act_formats": [r for r in snap.rows("act_formats") if r["a"] in act_set],  # type: ignore[misc]
        "manifestations": [
            r
            for r in snap.rows("manifestations")
            if r["w"] in versions or r["w"] in jo  # type: ignore[misc]
        ],
    }
    info: dict[str, Any] = {
        "format": SNAPSHOT_FORMAT,
        "sync_date": snap.info.get("sync_date"),
        "source": f"subset of {src.name}",
        "note": note,
        "act_formats_coverage": snap.info.get("act_formats_coverage"),
        "works": sorted(work_set),
        "tables": {},
    }
    for table, rows in keep.items():
        sha = write_table(out / f"{table}.parquet", TABLES[table], rows)
        info["tables"][table] = {"file": f"{table}.parquet", "file_sha256": sha, "rows": len(rows)}
    info["snapshot_digest"] = snapshot_digest(info)
    atomic_write_text(out / "_snapshot.json", json.dumps(info, indent=2, ensure_ascii=False) + "\n")
    return info
