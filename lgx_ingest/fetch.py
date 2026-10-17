"""S3 fetch: polite, resumable, conditional GETs into the content-addressed raw cache.

Contract: docs/dev/INTERFACES.md §5 (PLAN §3.3, §6.1 S3).

* Only ``https://legilux.public.lu/filestore/eli/etat/leg/…`` is ever requested: identifiers
  ``http://data.legilux.public.lu/filestore/…`` are rewritten, :func:`lgx_core.eli.assert_fetchable` runs
  before every request, then the robots policy (``lgx_ingest.robots.load_policy``) and the ELI denylist.
* One connection, ≥ ``max(settings.rate_limit_s, scope.fetch.min_spacing_s)`` between requests
  (retries included), hourly/daily ceilings, circuit breaker (consecutive errors, 403 bursts).
* ``If-Modified-Since`` / ``If-None-Match`` from the last :class:`RawFile` of the URL (304 → reuse);
  **never HEAD** (the filestore answers 403).
* Retries with backoff on 429/5xx and transport errors; ``Retry-After`` honoured.
* Files above ``scope.fetch.max_file_bytes`` are refused unless allow-listed (the size is checked from
  ``Content-Length``/``X-Content-Size`` and again while streaming).
* Bytes go to ``raw/<sha[:2]>/<sha256><ext>``; every success appends a ``RawFile`` to ``raw/index.jsonl``
  (last line per URL wins) and every attempt a ``FetchLogEntry`` to ``logs/fetch_log.jsonl``.
* Resumable: URLs completed for this ``T`` are listed in ``logs/fetch_progress_<T>.txt`` and skipped.
* Full runs (not ``--sample``) request only inside ``scope.fetch.nightly_window`` (01:00–05:00 Europe/Luxembourg,
  PLAN §3.3): outside it the run stops, resumable; ``--opt ignore_window=1`` (``lgx ingest --ignore-window``) is
  the operator opt-in that keeps every rate limit.
"""

from __future__ import annotations

import collections
import datetime as dt
import email.utils
import os
import tempfile
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

import httpx

from lgx_core.config import DataLayout, FetchPolicy
from lgx_core.eli import FetchNotAllowedError, assert_fetchable, filestore_fetch_url
from lgx_core.io import append_jsonl, read_jsonl
from lgx_core.models import (
    ActFlag,
    DateWindow,
    FetchLogEntry,
    FileRef,
    ManifestEntry,
    RawFile,
    SourceFormat,
    TextBasis,
    UserFormat,
    VersionKind,
    VersionRole,
    sha256_hex,
)
from lgx_ingest.context import StageContext, StageResult, UrlPolicy

EXTENSIONS = {SourceFormat.XML: ".xml", SourceFormat.HTML: ".html", SourceFormat.PDF: ".pdf"}
MAX_RETRY_AFTER_S = 300.0


class Status(StrEnum):
    FETCHED = "fetched"
    NOT_MODIFIED = "not_modified"
    SKIPPED_DONE = "skipped_done"
    REFUSED_URL = "refused_url"
    REFUSED_ROBOTS = "refused_robots"
    REFUSED_DENYLIST = "refused_denylist"
    TOO_LARGE = "too_large"
    ERROR = "error"


class FetchStopped(RuntimeError):
    """The run must stop now (circuit breaker, hourly/daily ceiling); it can be resumed later."""


@dataclass(frozen=True)
class Target:
    url: str
    entry: ManifestEntry


@dataclass
class Outcome:
    url: str
    status: Status
    http_status: int | None = None
    raw: RawFile | None = None
    error: str | None = None


# --------------------------------------------------------------------------------------- selection
def fetch_url(entry: ManifestEntry) -> str:
    """The fetchable main-host URL of an entry's file (rewrites ``data.legilux`` identifiers)."""
    if entry.file is None:
        raise ValueError(f"{entry.eli_version or entry.eli_work}: entry without file")
    url = entry.file.url or entry.file.file_uri
    return filestore_fetch_url(url) if "/filestore/" in url else url


def select_targets(entries: Iterable[ManifestEntry], *, sample: bool, include_m2: bool = False) -> list[Target]:
    """Entries with a file; in sample mode only ``in_sample`` ones of stage m1 (m2 too with ``include_m2``).

    Duplicate URLs are fetched once.
    """
    seen: set[str] = set()
    out: list[Target] = []
    for e in entries:
        if e.file is None:
            continue
        if sample and (not e.in_sample or (e.sample_stage == "m2" and not include_m2)):
            continue
        url = fetch_url(e)
        if url not in seen:
            seen.add(url)
            out.append(Target(url, e))
    return out


def load_plan(path: Path) -> list[ManifestEntry]:
    """Entries to fetch from ``manifest.jsonl`` (INTERFACES §2), ``meta/<T>/serving_plan.parquet`` (one row
    per act, ``lgx_ingest.select.SERVING_PLAN_SCHEMA``) or a parquet export of manifest rows."""
    if path.suffix != ".parquet":
        return list(read_jsonl(path, ManifestEntry))
    import json

    import pyarrow.parquet as pq

    rows = pq.read_table(path).to_pylist()
    if rows and "file_url" in rows[0]:
        return [e for row in rows for e in entries_from_serving_plan(row)]
    out = []
    for row in rows:
        for key in ("file", "text_valid"):
            if isinstance(row.get(key), str):
                row[key] = json.loads(row[key])
        out.append(ManifestEntry.model_validate(row))
    return out


_USER_FORMATS = {"xml": UserFormat.XML, "html": UserFormat.HTML, "pdf": UserFormat.PDF}


def entries_from_serving_plan(row: dict[str, Any]) -> list[ManifestEntry]:
    """Manifest entries (served version, and the stored future version) of one serving-plan row."""
    out: list[ManifestEntry] = []
    common = {
        "eli_work": row["eli_work"],
        "doc_type": row.get("doc_type") or "?",
        "text_basis": TextBasis(row["text_basis"]),
        "flags": [ActFlag(f) for f in row.get("flags") or []],
        "amending_acts": list(row.get("amending_acts") or []),
        "in_sample": bool(row.get("in_sample")),
        "sample_stage": row.get("sample_stage"),
    }
    fmt = row.get("format")
    if row.get("file_url") and row.get("served_version") and fmt in _USER_FORMATS:
        uf = _USER_FORMATS[fmt]
        out.append(
            ManifestEntry(
                **common,
                eli_version=row["served_version"],
                kind=VersionKind(row["served_kind"]) if row.get("served_kind") else None,
                role=VersionRole(row.get("served_role") or "current"),
                file=FileRef(
                    url=row["file_url"],
                    file_uri=row.get("file_uri") or row["file_url"],
                    user_format=uf,
                    source_format=SourceFormat(fmt),
                    licence=row.get("licence") or "",
                ),
                text_valid=DateWindow(lower=row.get("text_valid_from"), upper=row.get("text_valid_to")),
                reason=row.get("reason") or "serving_plan",
            )
        )
    if row.get("future_file_url") and row.get("future_version"):
        ffmt = row["future_file_url"].rsplit(".", 1)[-1]
        if ffmt in _USER_FORMATS:
            out.append(
                ManifestEntry(
                    **common,
                    eli_version=row["future_version"],
                    kind=VersionKind(row["served_kind"]) if row.get("served_kind") else None,
                    role=VersionRole.FUTURE,
                    file=FileRef(
                        url=row["future_file_url"],
                        file_uri=row["future_file_url"],
                        user_format=_USER_FORMATS[ffmt],
                        source_format=SourceFormat(ffmt),
                        licence=row.get("licence") or "",
                    ),
                    text_valid=DateWindow(lower=row.get("future_valid_from")),
                    reason="2e:future",
                )
            )
    return out


# ------------------------------------------------------------------------------------- raw index
class RawIndex:
    """``raw/index.jsonl``: append-only; the last line per URL wins."""

    def __init__(self, layout: DataLayout) -> None:
        self.layout = layout
        self.by_url: dict[str, RawFile] = {}
        if layout.raw_index_path.exists():
            for raw in read_jsonl(layout.raw_index_path, RawFile):
                self.by_url[raw.url] = raw

    def get(self, url: str) -> RawFile | None:
        raw = self.by_url.get(url)
        if raw is not None and not (self.layout.raw_dir / raw.path).exists():
            return None  # cache file lost: refetch unconditionally
        return raw

    def add(self, raw: RawFile) -> None:
        append_jsonl(self.layout.raw_index_path, raw)
        self.by_url[raw.url] = raw

    def path_of(self, raw: RawFile) -> Path:
        return self.layout.raw_dir / raw.path


def progress_path(layout: DataLayout) -> Path:
    return layout.root / "logs" / f"fetch_progress_{layout.t}.txt"


def read_progress(layout: DataLayout) -> set[str]:
    p = progress_path(layout)
    return set(p.read_text(encoding="utf-8").split()) if p.exists() else set()


def mark_done(layout: DataLayout, url: str) -> None:
    p = progress_path(layout)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as fh:
        fh.write(url + "\n")


# ------------------------------------------------------------------------------------ nightly window
@dataclass(frozen=True)
class NightlyWindow:
    """``"01:00-05:00 Europe/Luxembourg"`` (may wrap midnight, e.g. ``"22:00-05:00 …"``)."""

    start: dt.time
    end: dt.time
    tz: Any

    @classmethod
    def parse(cls, spec: str) -> NightlyWindow:
        from zoneinfo import ZoneInfo

        hours, _, zone = spec.strip().partition(" ")
        a, _, b = hours.partition("-")
        try:
            tz = ZoneInfo(zone.strip() or "Europe/Luxembourg")
            return cls(dt.time.fromisoformat(a), dt.time.fromisoformat(b), tz)
        except ValueError as exc:
            raise ValueError(f"nightly_window {spec!r}: expected 'HH:MM-HH:MM <IANA zone>'") from exc

    @property
    def hours(self) -> float:
        a = self.start.hour * 60 + self.start.minute
        b = self.end.hour * 60 + self.end.minute
        return ((b - a) % 1440 or 1440) / 60

    def is_open(self, now: dt.datetime) -> bool:
        t = now.astimezone(self.tz).time()
        if self.start <= self.end:
            return self.start <= t < self.end
        return t >= self.start or t < self.end


def fetch_window(ctx: StageContext) -> NightlyWindow | None:
    """The window this run must respect: none for the sample, ``--ignore-window`` or an empty config value."""
    spec = ctx.config.scope.fetch.nightly_window
    if ctx.sample or not spec or ctx.opt("ignore_window") in ("1", "true", "yes"):
        return None
    return NightlyWindow.parse(spec)


# ----------------------------------------------------------------------------------------- limiter
@dataclass
class RateLimiter:
    """Minimum spacing between requests plus hourly/daily ceilings (sliding windows, this process)."""

    spacing_s: float
    per_hour: int
    per_day: int
    clock: Callable[[], float] = time.monotonic
    sleep: Callable[[float], None] = time.sleep
    _last: float | None = None
    _stamps: collections.deque[float] = field(default_factory=collections.deque)

    def acquire(self) -> None:
        now = self.clock()
        while self._stamps and now - self._stamps[0] >= 86400:
            self._stamps.popleft()
        if len(self._stamps) >= self.per_day:
            raise FetchStopped(f"daily ceiling of {self.per_day} requests reached")
        if sum(1 for s in self._stamps if now - s < 3600) >= self.per_hour:
            raise FetchStopped(f"hourly ceiling of {self.per_hour} requests reached")
        if self._last is not None:
            wait = self._last + self.spacing_s - now
            if wait > 0:
                self.sleep(wait)
        self._last = self.clock()
        self._stamps.append(self._last)


@dataclass
class CircuitBreaker:
    max_consecutive: int
    max_403_per_minute: int
    clock: Callable[[], float] = time.monotonic
    consecutive: int = 0
    _403: collections.deque[float] = field(default_factory=collections.deque)

    def record(self, ok: bool, http_status: int | None) -> None:
        self.consecutive = 0 if ok else self.consecutive + 1
        if http_status == 403:
            now = self.clock()
            self._403.append(now)
            while self._403 and now - self._403[0] > 60:
                self._403.popleft()
            if len(self._403) > self.max_403_per_minute:
                raise FetchStopped(f"circuit breaker: more than {self.max_403_per_minute} × 403 within a minute")
        if self.consecutive >= self.max_consecutive:
            raise FetchStopped(f"circuit breaker: {self.consecutive} consecutive errors")


# ----------------------------------------------------------------------------------------- fetcher
def _http_date(value: dt.datetime) -> str:
    return email.utils.format_datetime(value.astimezone(dt.UTC), usegmt=True)


def _parse_http_date(value: str | None) -> dt.datetime | None:
    if not value:
        return None
    try:
        parsed = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt.UTC)


def _retry_after(value: str | None, now: dt.datetime) -> float | None:
    if not value:
        return None
    if value.strip().isdigit():
        return float(value.strip())
    when = _parse_http_date(value)
    return max(0.0, (when - now).total_seconds()) if when else None


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        umask = os.umask(0)
        os.umask(umask)
        Path(tmp).chmod(0o666 & ~umask)  # mkstemp creates 0600
        Path(tmp).replace(path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


class _TooLarge(Exception):
    pass


class Fetcher:
    """Downloads targets one by one; the caller owns the loop (so a stop can be resumed)."""

    def __init__(
        self,
        *,
        layout: DataLayout,
        policy: UrlPolicy,
        cfg: FetchPolicy,
        user_agent: str,
        spacing_s: float,
        timeout_s: float = 60.0,
        client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        now: Callable[[], dt.datetime] = lambda: dt.datetime.now(dt.UTC),
    ) -> None:
        self.layout = layout
        self.policy = policy
        self.cfg = cfg
        self.index = RawIndex(layout)
        self.sleep = sleep
        self.now = now
        self.limiter = RateLimiter(
            max(spacing_s, cfg.min_spacing_s), cfg.max_requests_per_hour, cfg.max_requests_per_day, clock, sleep
        )
        self.breaker = CircuitBreaker(cfg.circuit_breaker_consecutive_errors, cfg.circuit_breaker_403_per_minute, clock)
        self._own_client = client is None
        self.client = client or httpx.Client(
            timeout=httpx.Timeout(timeout_s, connect=min(10.0, timeout_s)), follow_redirects=False, http2=False
        )
        self.client.headers["User-Agent"] = user_agent
        self.requests = 0

    def close(self) -> None:
        if self._own_client:
            self.client.close()

    def __enter__(self) -> Fetcher:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ---------------------------------------------------------------------------------- policy
    def check(self, target: Target) -> Outcome | None:
        """Refusal outcome if the URL may not be fetched, else None (no network)."""
        try:
            assert_fetchable(target.url)
        except FetchNotAllowedError as exc:
            return Outcome(target.url, Status.REFUSED_URL, error=str(exc))
        if not self.policy.allows(target.url):
            return Outcome(target.url, Status.REFUSED_ROBOTS, error="robots.txt policy (or robots paused)")
        for eli in (target.entry.eli_version, target.entry.eli_work, target.url):
            if eli and self.policy.is_denied_eli(eli):
                return Outcome(target.url, Status.REFUSED_DENYLIST, error=f"denylisted: {eli}")
        return None

    def _cap(self, target: Target) -> int | None:
        tail = target.url.split("/filestore/eli/", 1)[-1]
        if any(tail.startswith(p.removeprefix("eli/").strip("/")) for p in self.cfg.large_file_allowlist):
            return None
        return self.cfg.max_file_bytes

    # ----------------------------------------------------------------------------------- fetch
    def fetch(self, target: Target, *, conditional: bool = True) -> Outcome:
        refused = self.check(target)
        if refused is not None:
            return refused
        previous = self.index.get(target.url) if conditional else None
        headers = {"Accept-Encoding": "gzip"}
        if previous is not None:
            if previous.last_modified is not None:
                headers["If-Modified-Since"] = _http_date(previous.last_modified)
            if previous.etag:
                headers["If-None-Match"] = previous.etag
        attempt = 0
        while True:
            outcome, retry_in = self._attempt(target, headers, previous)
            ok = outcome.status in (Status.FETCHED, Status.NOT_MODIFIED)
            final = ok or retry_in is None or attempt >= self.cfg.max_retries
            if final:
                self.breaker.record(ok or outcome.status is Status.TOO_LARGE, outcome.http_status)
                return outcome
            backoff = self.cfg.backoff_s[min(attempt, len(self.cfg.backoff_s) - 1)] if self.cfg.backoff_s else 1.0
            self.sleep(min(MAX_RETRY_AFTER_S, max(backoff, retry_in)))
            attempt += 1

    def _attempt(
        self, target: Target, headers: dict[str, str], previous: RawFile | None
    ) -> tuple[Outcome, float | None]:
        """One GET. Returns (outcome, seconds to wait before a retry, or None if not retryable)."""
        self.limiter.acquire()
        self.requests += 1
        started = time.monotonic()
        fetched_at = self.now()
        log = FetchLogEntry(url=target.url, fetched_at=fetched_at)
        try:
            with self.client.stream("GET", target.url, headers=headers) as resp:
                log.http_status = resp.status_code
                log.last_modified = _parse_http_date(resp.headers.get("last-modified"))
                if resp.status_code == 304 and previous is not None:
                    log.sha256, log.bytes = previous.sha256, previous.bytes
                    return Outcome(target.url, Status.NOT_MODIFIED, 304, raw=previous), None
                if resp.status_code != 200:
                    log.error = f"HTTP {resp.status_code}"
                    retry = resp.status_code in self.cfg.retry_on
                    wait = _retry_after(resp.headers.get("retry-after"), fetched_at) if retry else None
                    return Outcome(target.url, Status.ERROR, resp.status_code, error=log.error), (
                        (wait or 0.0) if retry else None
                    )
                cap = self._cap(target)
                declared = resp.headers.get("x-content-size") or (
                    resp.headers.get("content-length") if "content-encoding" not in resp.headers else None
                )
                if cap is not None and declared and declared.isdigit() and int(declared) > cap:
                    raise _TooLarge(f"{declared} bytes > cap {cap}")
                body = bytearray()
                for chunk in resp.iter_bytes():
                    body.extend(chunk)
                    if cap is not None and len(body) > cap:
                        raise _TooLarge(f"more than {cap} bytes")
                data = bytes(body)
                raw = self._store(target, data, resp, fetched_at)
                log.sha256, log.bytes = raw.sha256, raw.bytes
                return Outcome(target.url, Status.FETCHED, 200, raw=raw), None
        except _TooLarge as exc:
            log.error = f"too large: {exc}"
            return Outcome(target.url, Status.TOO_LARGE, log.http_status, error=log.error), None
        except httpx.HTTPError as exc:
            log.error = f"{type(exc).__name__}: {exc}"
            return Outcome(target.url, Status.ERROR, None, error=log.error), 0.0
        finally:
            log.elapsed_ms = int((time.monotonic() - started) * 1000)
            append_jsonl(self.layout.fetch_log_path, log)

    def _store(self, target: Target, data: bytes, resp: httpx.Response, fetched_at: dt.datetime) -> RawFile:
        sha = sha256_hex(data)
        fmt = target.entry.file.source_format if target.entry.file else None
        ext = EXTENSIONS.get(fmt, "") if fmt else ""
        path = self.layout.raw_path(sha, ext)
        if not path.exists():
            _atomic_write_bytes(path, data)
        raw = RawFile(
            url=target.url,
            sha256=sha,
            bytes=len(data),
            path=str(path.relative_to(self.layout.raw_dir)),
            content_type=resp.headers.get("content-type"),
            http_status=200,
            last_modified=_parse_http_date(resp.headers.get("last-modified")),
            etag=resp.headers.get("etag"),
            fetched_at=fetched_at,
            eli_version=target.entry.eli_version,
            source_format=fmt,
        )
        self.index.add(raw)
        return raw


# -------------------------------------------------------------------------------------------- stage
def _policy(ctx: StageContext) -> UrlPolicy:
    from lgx_ingest import robots

    return robots.load_policy(ctx)


def _entries(ctx: StageContext) -> tuple[list[ManifestEntry], Path]:
    path = Path(ctx.opt("manifest") or ctx.opt("plan") or ctx.layout.manifest_path)
    if not path.exists():
        raise FileNotFoundError(f"manifest not found: {path} (run `lgx select`, or pass --opt manifest=PATH)")
    return load_plan(path), path


def run_fetch(
    ctx: StageContext,
    *,
    limit: int | None = None,
    include_m2: bool = False,
    policy: UrlPolicy | None = None,
    client: httpx.Client | None = None,
    sleep: Callable[[float], None] = time.sleep,
    now: Callable[[], dt.datetime] | None = None,
) -> StageResult:
    """Fetch every manifest entry with a ``file`` (sample entries only when ``ctx.sample``).

    Options: ``--opt manifest=PATH`` (or ``plan=PATH``, .jsonl or .parquet), ``--opt refetch=1`` (ignore
    the progress file of this T; requests stay conditional).
    """
    result = StageResult(stage="fetch")
    try:
        entries, manifest = _entries(ctx)
    except FileNotFoundError as exc:
        result.ok = False
        result.messages.append(str(exc))
        return result
    targets = select_targets(entries, sample=ctx.sample, include_m2=include_m2)
    done = set() if ctx.opt("refetch") in ("1", "true", "yes") else read_progress(ctx.layout)
    result.details["manifest"] = str(manifest)
    result.counts["targets"] = len(targets)
    if ctx.dry_run:
        index = RawIndex(ctx.layout)
        plan = []
        for t in targets[:limit] if limit else targets:
            action = "skip_done" if t.url in done else ("conditional" if index.get(t.url) else "new")
            plan.append({"url": t.url, "eli_version": t.entry.eli_version, "action": action})
        result.details["plan"] = plan
        result.counts.update(collections.Counter(p["action"] for p in plan))
        result.messages.append("dry run: nothing was fetched")
        return result
    try:
        policy = policy or _policy(ctx)
    except (FileNotFoundError, NotImplementedError) as exc:
        result.ok = False
        result.messages.append(f"no robots policy, fetching refused: {exc}")
        return result
    counts: collections.Counter[str] = collections.Counter()
    failures: list[dict[str, str | int | None]] = []
    spacing = max(ctx.settings.rate_limit_s, ctx.config.scope.fetch.min_spacing_s)
    window = fetch_window(ctx)
    clock = now or (lambda: dt.datetime.now(dt.UTC))
    with Fetcher(
        layout=ctx.layout,
        policy=policy,
        cfg=ctx.config.scope.fetch,
        user_agent=ctx.settings.user_agent,
        spacing_s=spacing,
        timeout_s=ctx.settings.http_timeout_s,
        client=client,
        sleep=sleep,
    ) as fetcher:
        downloads = 0
        try:
            for t in targets:
                if t.url in done:
                    counts[Status.SKIPPED_DONE] += 1
                    continue
                if limit is not None and downloads >= limit:
                    result.messages.append(f"stopped after --limit {limit} downloads")
                    break
                if window is not None and not window.is_open(clock()):
                    raise FetchStopped(
                        f"outside the nightly window {ctx.config.scope.fetch.nightly_window}; run again inside it"
                        " (completed URLs are skipped) or pass --ignore-window"
                    )
                outcome = fetcher.fetch(t)
                counts[outcome.status] += 1
                if outcome.status in (Status.FETCHED, Status.NOT_MODIFIED):
                    downloads += 1
                    mark_done(ctx.layout, t.url)
                    done.add(t.url)
                else:
                    failures.append(
                        {
                            "url": t.url,
                            "status": outcome.status.value,
                            "http": outcome.http_status,
                            "error": outcome.error,
                        }
                    )
        except FetchStopped as exc:
            result.ok = False
            result.messages.append(f"fetch stopped (resumable): {exc}")
        result.counts["requests"] = fetcher.requests
    result.counts.update({str(k): v for k, v in counts.items()})
    result.details["failures"] = failures
    errors = counts[Status.ERROR] + counts[Status.TOO_LARGE]
    attempted = sum(counts[s] for s in (Status.FETCHED, Status.NOT_MODIFIED, Status.ERROR, Status.TOO_LARGE))
    if attempted and errors / attempted > 0.01:
        result.messages.append(f"fetch error rate {errors}/{attempted} exceeds 1 % (PLAN §6.8 gate)")
    refused = counts[Status.REFUSED_ROBOTS] + counts[Status.REFUSED_DENYLIST] + counts[Status.REFUSED_URL]
    if refused:
        result.messages.append(f"{refused} URL(s) refused by policy")
    result.outputs = [str(ctx.layout.raw_index_path), str(ctx.layout.fetch_log_path), str(progress_path(ctx.layout))]
    return result
