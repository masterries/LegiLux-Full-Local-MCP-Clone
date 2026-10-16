"""Offline tests of the S3 fetcher (HTTP mocked with respx; no network)."""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import httpx
import pytest
import respx

from lgx_core.config import FetchPolicy
from lgx_core.io import read_jsonl, write_jsonl
from lgx_core.models import FetchLogEntry, ManifestEntry, RawFile
from lgx_ingest.context import StageContext
from lgx_ingest.fetch import (
    CircuitBreaker,
    Fetcher,
    FetchStopped,
    RateLimiter,
    Status,
    Target,
    fetch_url,
    load_plan,
    run_fetch,
    select_targets,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
BOOTSTRAP = FIXTURES / "manifest" / "bootstrap_sample.jsonl"
T = dt.date(2026, 10, 16)
LM = "Thu, 26 Feb 2026 15:45:05 GMT"


class Policy:
    def __init__(self, *, allow: bool = True, denied: tuple[str, ...] = ()) -> None:
        self.allow = allow
        self.denied = denied

    def allows(self, url: str) -> bool:
        return self.allow

    def is_denied_eli(self, eli: str) -> bool:
        return any(eli.startswith(d) for d in self.denied)


class Sleeper:
    def __init__(self) -> None:
        self.calls: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)


@pytest.fixture
def entries() -> list[ManifestEntry]:
    return list(read_jsonl(BOOTSTRAP, ManifestEntry))


@pytest.fixture
def ctx(tmp_path: Path) -> StageContext:
    return StageContext.create(sync_date=T, data_dir=tmp_path / "data")


def make_fetcher(ctx: StageContext, policy: Policy | None = None, cfg: FetchPolicy | None = None) -> Fetcher:
    return Fetcher(
        layout=ctx.layout,
        policy=policy or Policy(),
        cfg=cfg or ctx.config.scope.fetch,
        user_agent=ctx.settings.user_agent,
        spacing_s=1.2,
        client=httpx.Client(),
        sleep=Sleeper(),
    )


def target(entries: list[ManifestEntry], needle: str) -> Target:
    e = next(x for x in entries if needle in (x.eli_version or ""))
    return Target(fetch_url(e), e)


# ------------------------------------------------------------------------------------- selection
def test_select_targets_sample_filters_and_dedups(entries: list[ManifestEntry]) -> None:
    targets = select_targets(entries, sample=True)
    assert len(targets) == len({t.url for t in targets}) == 12
    m2 = entries[0].model_copy(update={"sample_stage": "m2"})
    assert select_targets([m2], sample=True) == []
    assert len(select_targets([m2], sample=True, include_m2=True)) == 1
    not_sampled = entries[0].model_copy(update={"in_sample": False})
    assert select_targets([not_sampled], sample=True) == []
    assert len(select_targets([not_sampled, not_sampled], sample=False)) == 1


def test_fetch_url_rewrites_data_host_identifiers(entries: list[ManifestEntry]) -> None:
    e = entries[0]
    file = e.file.model_copy(update={"url": e.file.file_uri})  # type: ignore[union-attr]
    url = fetch_url(e.model_copy(update={"file": file}))
    assert url.startswith("https://legilux.public.lu/filestore/eli/etat/leg/")


def test_load_plan_jsonl_and_parquet(tmp_path: Path, entries: list[ManifestEntry]) -> None:
    import pyarrow as pa
    import pyarrow.parquet as pq

    assert load_plan(BOOTSTRAP) == entries
    rows = [e.model_dump(mode="json") for e in entries]
    for r in rows:
        r["file"] = json.dumps(r["file"])
        r["text_valid"] = json.dumps(r["text_valid"])
    path = tmp_path / "serving_plan.parquet"
    pq.write_table(pa.Table.from_pylist(rows), path)
    assert [e.eli_version for e in load_plan(path)] == [e.eli_version for e in entries]


# --------------------------------------------------------------------------------------- policy
def test_refuses_urls_outside_the_filestore_branch(ctx: StageContext, entries: list[ManifestEntry]) -> None:
    t = target(entries, "a88")
    with respx.mock(assert_all_called=False) as mock, make_fetcher(ctx) as f:
        route = mock.get(url__regex=r".*").respond(200)
        for url in (
            t.url.replace("https://legilux.public.lu", "https://data.legilux.public.lu"),
            t.url.replace("/etat/leg/", "/etat/adm/"),
            t.url.replace("-fr-xml.xml", "-fr-docx.docx"),
            "https://legilux.public.lu/eli/etat/leg/loi/2026/02/25/a88/jo",
        ):
            assert f.fetch(Target(url, t.entry)).status is Status.REFUSED_URL
        assert not route.called


def test_refuses_robots_and_denylist(ctx: StageContext, entries: list[ManifestEntry]) -> None:
    t = target(entries, "a88")
    with respx.mock(assert_all_called=False) as mock:
        route = mock.get(t.url).respond(200, content=b"<x/>")
        with make_fetcher(ctx, Policy(allow=False)) as f:
            assert f.fetch(t).status is Status.REFUSED_ROBOTS
        with make_fetcher(ctx, Policy(denied=(t.entry.eli_work,))) as f:
            assert f.fetch(t).status is Status.REFUSED_DENYLIST
        assert not route.called


# ---------------------------------------------------------------------------------- downloading
def test_get_stores_content_addressed_and_logs(ctx: StageContext, entries: list[ManifestEntry]) -> None:
    t = target(entries, "a88")
    body = b"<akomaNtoso/>"
    with respx.mock() as mock, make_fetcher(ctx) as f:
        route = mock.get(t.url).respond(
            200, content=body, headers={"Last-Modified": LM, "Content-Type": "application/xml"}
        )
        out = f.fetch(t)
    assert out.status is Status.FETCHED and out.raw is not None
    raw = out.raw
    assert raw.bytes == len(body) and raw.path == f"{raw.sha256[:2]}/{raw.sha256}.xml"
    assert (ctx.layout.raw_dir / raw.path).read_bytes() == body
    assert raw.last_modified == dt.datetime(2026, 2, 26, 15, 45, 5, tzinfo=dt.UTC)
    assert raw.eli_version == t.entry.eli_version and raw.content_type == "application/xml"
    assert [r.url for r in read_jsonl(ctx.layout.raw_index_path, RawFile)] == [t.url]
    log = list(read_jsonl(ctx.layout.fetch_log_path, FetchLogEntry))
    assert len(log) == 1 and log[0].http_status == 200 and log[0].sha256 == raw.sha256
    request = route.calls.last.request
    assert request.method == "GET" and request.headers["User-Agent"].startswith("LegiLux-Local-Clone/")
    assert "If-Modified-Since" not in request.headers


def test_conditional_get_304_reuses_cache(ctx: StageContext, entries: list[ManifestEntry]) -> None:
    t = target(entries, "a88")
    with respx.mock() as mock:
        mock.get(t.url).mock(
            side_effect=[httpx.Response(200, content=b"<a/>", headers={"Last-Modified": LM}), httpx.Response(304)]
        )
        with make_fetcher(ctx) as f:
            first = f.fetch(t)
        with make_fetcher(ctx) as f:  # a new run reads the index from disk
            second = f.fetch(t)
        sent = mock.calls.last.request
    assert second.status is Status.NOT_MODIFIED and second.raw == first.raw
    assert sent.headers["If-Modified-Since"] == LM
    assert all(c.request.method == "GET" for c in mock.calls)  # never HEAD
    log = list(read_jsonl(ctx.layout.fetch_log_path, FetchLogEntry))
    assert [e.http_status for e in log] == [200, 304]


def test_etag_is_sent_back_when_present(ctx: StageContext, entries: list[ManifestEntry]) -> None:
    t = target(entries, "a88")
    with respx.mock() as mock, make_fetcher(ctx) as f:
        mock.get(t.url).mock(
            side_effect=[httpx.Response(200, content=b"<a/>", headers={"ETag": '"v1"'}), httpx.Response(304)]
        )
        f.fetch(t)
        f.fetch(t)
        assert mock.calls.last.request.headers["If-None-Match"] == '"v1"'


def test_lost_cache_file_forces_full_get(ctx: StageContext, entries: list[ManifestEntry]) -> None:
    t = target(entries, "a88")
    with respx.mock() as mock, make_fetcher(ctx) as f:
        mock.get(t.url).mock(side_effect=[httpx.Response(200, content=b"<a/>", headers={"Last-Modified": LM})] * 2)
        raw = f.fetch(t).raw
        assert raw is not None
        (ctx.layout.raw_dir / raw.path).unlink()
        assert f.fetch(t).status is Status.FETCHED
        assert "If-Modified-Since" not in mock.calls.last.request.headers


def test_retries_with_backoff_and_retry_after(ctx: StageContext, entries: list[ManifestEntry]) -> None:
    t = target(entries, "a88")
    with respx.mock() as mock, make_fetcher(ctx) as f:
        f.limiter.sleep = lambda s: None  # only record the retry waits
        mock.get(t.url).mock(
            side_effect=[
                httpx.Response(503),
                httpx.Response(429, headers={"Retry-After": "7"}),
                httpx.ConnectError("reset"),
                httpx.Response(200, content=b"<a/>"),
            ]
        )
        out = f.fetch(t)
        assert out.status is Status.FETCHED and f.requests == 4
        assert f.sleep.calls == [1, 7, 4]  # type: ignore[attr-defined]  # backoff[0], Retry-After, backoff[2]


def test_gives_up_after_max_retries_and_no_retry_on_404(ctx: StageContext, entries: list[ManifestEntry]) -> None:
    t = target(entries, "a88")
    cfg = ctx.config.scope.fetch.model_copy(update={"max_retries": 2})
    with respx.mock() as mock, make_fetcher(ctx, cfg=cfg) as f:
        mock.get(t.url).respond(500)
        out = f.fetch(t)
        assert out.status is Status.ERROR and out.http_status == 500 and f.requests == 3
    with respx.mock() as mock, make_fetcher(ctx, cfg=cfg) as f:
        mock.get(t.url).respond(404)
        assert f.fetch(t).status is Status.ERROR and f.requests == 1


def test_size_cap_from_header_and_while_streaming(ctx: StageContext, entries: list[ManifestEntry]) -> None:
    t = target(entries, "a88")
    cfg = ctx.config.scope.fetch.model_copy(update={"max_file_bytes": 10})
    with respx.mock() as mock, make_fetcher(ctx, cfg=cfg) as f:
        mock.get(t.url).mock(
            side_effect=[
                httpx.Response(200, content=b"x" * 5, headers={"X-Content-Size": "999"}),
                httpx.Response(200, content=b"x" * 50),
            ]
        )
        assert f.fetch(t).status is Status.TOO_LARGE
        assert f.fetch(t).status is Status.TOO_LARGE
    assert not ctx.layout.raw_index_path.exists()
    allow = cfg.model_copy(update={"large_file_allowlist": ["etat/leg/loi/2026/02/25/a88"]})
    with respx.mock() as mock, make_fetcher(ctx, cfg=allow) as f:
        mock.get(t.url).respond(200, content=b"x" * 50)
        assert f.fetch(t).status is Status.FETCHED


# ------------------------------------------------------------------------------- limits/breaker
def test_rate_limiter_spacing_and_ceilings() -> None:
    now = [0.0]
    slept: list[float] = []

    def sleep(s: float) -> None:
        slept.append(s)
        now[0] += s

    limiter = RateLimiter(1.2, per_hour=3, per_day=10, clock=lambda: now[0], sleep=sleep)
    limiter.acquire()
    now[0] += 0.2
    limiter.acquire()
    assert slept == [pytest.approx(1.0)]
    limiter.acquire()
    with pytest.raises(FetchStopped, match="hourly"):
        limiter.acquire()


def test_circuit_breaker() -> None:
    b = CircuitBreaker(max_consecutive=3, max_403_per_minute=5, clock=lambda: 0.0)
    b.record(False, 500)
    b.record(True, 200)
    b.record(False, 404)
    b.record(False, 404)
    with pytest.raises(FetchStopped, match="consecutive"):
        b.record(False, 404)
    b2 = CircuitBreaker(max_consecutive=100, max_403_per_minute=2, clock=lambda: 0.0)
    b2.record(False, 403)
    b2.record(False, 403)
    with pytest.raises(FetchStopped, match="403"):
        b2.record(False, 403)


# ---------------------------------------------------------------------------------------- stage
def _stage(ctx: StageContext, tmp_path: Path, entries: list[ManifestEntry]) -> StageContext:
    write_jsonl(ctx.layout.manifest_path, entries)
    return ctx


def test_run_fetch_dry_run_lists_plan_without_requests(
    ctx: StageContext, tmp_path: Path, entries: list[ManifestEntry]
) -> None:
    ctx = _stage(ctx, tmp_path, entries)
    dry = StageContext(**{**ctx.__dict__, "dry_run": True})
    with respx.mock(assert_all_called=False) as mock:
        route = mock.get(url__regex=r".*").respond(200)
        res = run_fetch(dry)
        assert not route.called
    assert res.ok and res.counts["targets"] == 12 and res.counts["new"] == 12
    assert len(res.details["plan"]) == 12


def test_run_fetch_is_resumable_and_honours_limit(
    ctx: StageContext, tmp_path: Path, entries: list[ManifestEntry]
) -> None:
    ctx = _stage(ctx, tmp_path, entries)
    with respx.mock() as mock:
        mock.get(url__regex=r"https://legilux\.public\.lu/filestore/eli/etat/leg/.*").mock(
            side_effect=lambda req: httpx.Response(200, content=req.url.path.encode(), headers={"Last-Modified": LM})
        )
        first = run_fetch(ctx, limit=5, policy=Policy(), sleep=lambda s: None)
        assert first.ok and first.counts["fetched"] == 5 and first.counts["requests"] == 5
        second = run_fetch(ctx, policy=Policy(), sleep=lambda s: None)
        assert second.counts["skipped_done"] == 5 and second.counts["fetched"] == 7
        assert mock.calls.call_count == 12
    index = list(read_jsonl(ctx.layout.raw_index_path, RawFile))
    assert len({r.url for r in index}) == 12


def test_run_fetch_without_robots_policy_fails_closed(
    ctx: StageContext, tmp_path: Path, entries: list[ManifestEntry]
) -> None:
    ctx = _stage(ctx, tmp_path, entries)
    with respx.mock(assert_all_called=False) as mock:
        route = mock.get(url__regex=r".*").respond(200)
        res = run_fetch(ctx)  # no data/robots/<T>/ in tmp: the policy cannot be built
        assert not route.called
    assert not res.ok and "robots" in res.messages[0]


def test_run_fetch_missing_manifest(ctx: StageContext) -> None:
    res = run_fetch(ctx)
    assert not res.ok and "manifest not found" in res.messages[0]
