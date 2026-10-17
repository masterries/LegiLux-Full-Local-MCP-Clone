"""Embeddings (PLAN §6.5): limiter, Mistral client (retries, Retry-After, checks), lgx embed, load selection,
the MCP startup probe and the lexical fallback. Offline: fake clock, fake HTTP endpoints, never a real API call."""

from __future__ import annotations

import asyncio
import datetime as dt
import heapq
import http.server
import itertools
import json
import math
import threading
from collections.abc import Awaitable, Callable, Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest

from lgx_core.config import EmbeddingConfig
from lgx_core.embed import (
    PROBE_TEXT,
    EmbeddingError,
    MistralEmbedder,
    SlotLimiter,
    ThrottledError,
    cosine,
    estimate_duration_s,
    plan_batches,
    retry_after_s,
)
from lgx_core.io import write_jsonl
from lgx_core.models import Chunk, EmbeddingProvider, ManifestEntry, TextBasis
from lgx_ingest.chunk import content_sha256
from lgx_ingest.context import StageContext
from lgx_ingest.embed import (
    EmbeddingCache,
    cache_path,
    embed_text,
    halfvec_literal,
    pack_f16,
    release_vectors,
    run_embed,
    unpack_f16,
)

DIM = 8


# ------------------------------------------------------------------------------------------- fake clock
class FakeClock:
    """Virtual time: ``sleep`` registers a timer; :meth:`run` advances to the next timer whenever every task
    is blocked. Nothing waits in real time."""

    def __init__(self) -> None:
        self.now = 0.0
        self._timers: list[tuple[float, int, asyncio.Future[None]]] = []
        self._seq = itertools.count()

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        fut: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        heapq.heappush(self._timers, (self.now + max(0.0, seconds), next(self._seq), fut))
        await fut

    async def run[T](self, coro: Awaitable[T]) -> T:
        task = asyncio.ensure_future(coro)
        idle_rounds = 0
        while not task.done():
            for _ in range(200):  # let every runnable task progress
                await asyncio.sleep(0)
                if task.done():
                    break
            while self._timers and self._timers[0][2].done():  # cancelled sleeps
                heapq.heappop(self._timers)
            if task.done():
                break
            if not self._timers:
                idle_rounds += 1
                assert idle_rounds < 50, "deadlock: every task waits and no timer is pending"
                continue
            idle_rounds = 0
            when, _, fut = heapq.heappop(self._timers)
            self.now = max(self.now, when)
            fut.set_result(None)
        return task.result()


def occupied_max(intervals: list[tuple[float, float]], cooldown: float) -> int:
    """Max number of slots occupied at once: a slot is occupied from a request's start to end + cooldown."""
    events = sorted(
        [(s, 1) for s, _ in intervals] + [(e + cooldown, -1) for _, e in intervals], key=lambda x: (x[0], x[1])
    )
    cur = best = 0
    for _, d in events:
        cur += d
        best = max(best, cur)
    return best


# ------------------------------------------------------------------------------------------- limiter
async def test_limiter_never_exceeds_max_concurrency_and_respects_the_cooldown() -> None:
    clock = FakeClock()
    lim = SlotLimiter(3, 20.0, clock=clock, sleep=clock.sleep)
    intervals: list[tuple[float, float]] = []
    in_flight = 0
    peak = 0

    async def request(latency: float) -> None:
        nonlocal in_flight, peak
        async with lim.slot():
            in_flight += 1
            peak = max(peak, in_flight)
            start = clock.now
            await clock.sleep(latency)
            intervals.append((start, clock.now))
            in_flight -= 1

    async def main() -> None:
        await asyncio.gather(*(request(2.0 + (i % 3)) for i in range(10)))

    await clock.run(main())
    assert len(intervals) == 10
    assert peak == 3 and lim.max_in_flight == 3
    assert occupied_max(intervals, 20.0) <= 3  # in flight + cooling down never more than the slots
    # each slot: the next request on it starts >= cooldown after the previous one ended
    starts = sorted(s for s, _ in intervals)
    assert starts[:3] == [0.0, 0.0, 0.0] and min(starts[3:]) >= 22.0 - 1e-9
    assert lim.requests == 10 and lim.waited_s > 0
    assert estimate_duration_s(10, 3, 20.0, 2.0) == 4 * 22.0 - 20.0


async def test_limiter_timeout_raises_throttled() -> None:
    clock = FakeClock()
    lim = SlotLimiter(1, 20.0, clock=clock, sleep=clock.sleep)

    async def main() -> None:
        async with lim.slot():
            pass
        assert lim.occupied() == 1 and math.isclose(lim.next_free_in(), 20.0)
        with pytest.raises(ThrottledError):
            await lim.acquire(timeout=5.0)
        assert clock.now == pytest.approx(0.0)  # no slot can be free within 5 s: fails at once (M2 review)
        slot = await lim.acquire(timeout=30.0)  # free again after the cooldown
        assert clock.now == pytest.approx(20.0)
        lim.release(slot)

    await clock.run(main())


async def test_limiter_waits_when_a_slot_can_free_in_time_and_fails_fast_otherwise() -> None:
    clock = FakeClock()
    lim = SlotLimiter(5, 20.0, clock=clock, sleep=clock.sleep)

    async def main() -> None:
        for _ in range(5):  # five requests: every slot cools down for 20 s
            async with lim.slot():
                pass
        assert math.isclose(lim.next_free_in(), 20.0)
        with pytest.raises(ThrottledError):
            async with lim.slot(5):
                pass
        assert clock.now == pytest.approx(0.0)  # the query path's 5 s timeout: no 5 s sleep for nothing
        clock.now = 17.0
        async with lim.slot(5):  # the first slot frees at 20 s, within the 5 s: wait for it
            assert clock.now == pytest.approx(20.0)

    await clock.run(main())


async def test_limiter_in_flight_slot_without_cooldown_is_worth_waiting_for() -> None:
    lim = SlotLimiter(1, 0.0)
    held = await lim.acquire()

    async def later() -> None:
        await asyncio.sleep(0.01)
        lim.release(held)

    task = asyncio.ensure_future(later())
    slot = await lim.acquire(timeout=1.0)  # released (no cooldown) before the timeout
    lim.release(slot)
    await task


async def test_limiter_without_cooldown_reuses_slots_at_once() -> None:
    lim = SlotLimiter(2, 0.0)
    out = []

    async def req(i: int) -> None:
        async with lim.slot():
            out.append(i)
            await asyncio.sleep(0)

    await asyncio.gather(*(req(i) for i in range(6)))
    assert sorted(out) == list(range(6)) and lim.max_in_flight == 2


# ------------------------------------------------------------------------------------------- client
def cfg(**kw: Any) -> EmbeddingConfig:
    base: dict[str, Any] = {
        "provider": "mistral",
        "base_url": "https://mistral.example.test",
        "dim": DIM,
        "max_concurrency": 2,
        "cooldown_s": 0.0,
        "retries": 3,
        "backoff_s": [2.0, 4.0, 8.0],
    }
    base.update(kw)
    return EmbeddingConfig.model_validate(base)


def vec(text: str, dim: int = DIM) -> list[float]:
    """Deterministic fake embedding: unit vector from the text's characters."""
    raw = [float((sum(ord(c) for c in text) * (i + 3)) % 97 + 1) for i in range(dim)]
    norm = math.sqrt(sum(x * x for x in raw))
    return [x / norm for x in raw]


class FakeEndpoint:
    """A fake Mistral ``/v1/embeddings``: scripted failures first, then vectors; records every request."""

    def __init__(self, *, model: str = "mistral-embed", dim: int = DIM, script: list[Any] | None = None) -> None:
        self.model = model
        self.dim = dim
        self.script = list(script or [])
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.script:
            step = self.script.pop(0)
            if isinstance(step, Exception):
                raise step
            return step
        body = json.loads(request.content)
        data = [{"object": "embedding", "index": i, "embedding": vec(t, self.dim)} for i, t in enumerate(body["input"])]
        data.reverse()  # the client must sort by index
        n = sum(len(t) for t in body["input"])
        return httpx.Response(200, json={"object": "list", "model": self.model, "data": data,
                                         "usage": {"prompt_tokens": n, "total_tokens": n}})  # fmt: skip

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self)


async def test_request_shape_reported_model_and_order() -> None:
    fake = FakeEndpoint(model="mistral-embed-2312")
    async with MistralEmbedder(cfg(), api_key="secret-key", transport=fake.transport) as emb:
        res = await emb.embed(["a", "bb"])
    req = fake.requests[0]
    assert str(req.url) == "https://mistral.example.test/v1/embeddings" and req.method == "POST"
    assert req.headers["authorization"] == "Bearer secret-key"
    assert json.loads(req.content) == {"model": "mistral-embed", "input": ["a", "bb"]}
    assert res.model == "mistral-embed-2312"  # the id the API reports, recorded as is
    assert res.vectors == [vec("a"), vec("bb")] and res.tokens == 3


async def test_retries_honour_retry_after_and_backoff() -> None:
    delays: list[float] = []

    async def sleep(d: float) -> None:
        delays.append(d)

    fake = FakeEndpoint(
        script=[
            httpx.Response(429, headers={"Retry-After": "7"}, json={"message": "rate limited"}),
            httpx.Response(503, text="busy"),
            httpx.ConnectError("refused"),
        ]
    )
    async with MistralEmbedder(cfg(), api_key="k", transport=fake.transport, sleep=sleep) as emb:
        res = await emb.embed(["x"])
    assert len(fake.requests) == 4 and res.attempts == 4 and emb.retries == 3
    assert delays == [7.0, 4.0, 8.0]  # max(backoff 2, Retry-After 7), then the backoff steps


async def test_non_retryable_and_exhausted_errors() -> None:
    async def nosleep(d: float) -> None:
        return None

    fake = FakeEndpoint(script=[httpx.Response(401, json={"detail": "no"})])
    async with MistralEmbedder(cfg(), api_key="bad", transport=fake.transport, sleep=nosleep) as emb:
        with pytest.raises(EmbeddingError) as e401:
            await emb.embed(["x"])
    assert e401.value.reason == "unauthorized" and len(fake.requests) == 1
    fake = FakeEndpoint(script=[httpx.Response(400, text="input too long")])
    async with MistralEmbedder(cfg(), api_key="k", transport=fake.transport, sleep=nosleep) as emb:
        with pytest.raises(EmbeddingError) as e400:
            await emb.embed(["x"])
    assert e400.value.reason == "endpoint_error" and e400.value.status == 400 and len(fake.requests) == 1
    fake = FakeEndpoint(script=[httpx.Response(429)] * 5)
    async with MistralEmbedder(cfg(retries=2), api_key="k", transport=fake.transport, sleep=nosleep) as emb:
        with pytest.raises(EmbeddingError) as e429:
            await emb.embed(["x"])
    assert e429.value.reason == "rate_limited" and len(fake.requests) == 3


async def test_response_checks() -> None:
    fake = FakeEndpoint(dim=DIM + 1)
    async with MistralEmbedder(cfg(), api_key="k", transport=fake.transport) as emb:
        with pytest.raises(EmbeddingError) as dim:
            await emb.embed(["x"])
    assert dim.value.reason == "dim_mismatch"
    fake = FakeEndpoint(script=[httpx.Response(200, json={"model": "m", "data": []})])
    async with MistralEmbedder(cfg(), api_key="k", transport=fake.transport) as emb:
        with pytest.raises(EmbeddingError) as bad:
            await emb.embed(["x"])
    assert bad.value.reason == "bad_response"


async def test_every_attempt_takes_a_limiter_slot() -> None:
    clock = FakeClock()
    fake = FakeEndpoint(script=[httpx.Response(503)])
    starts: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        starts.append(clock.now)
        return fake(request)

    emb = MistralEmbedder(
        cfg(max_concurrency=1, cooldown_s=20.0, backoff_s=[1.0]),
        api_key="k",
        transport=httpx.MockTransport(handler),
        clock=clock,
        sleep=clock.sleep,
    )

    async def main() -> None:
        await emb.embed(["x"])
        await emb.aclose()

    await clock.run(main())
    assert starts == [0.0, 20.0]  # the retry waited for the slot's cooldown (20 s > the 1 s backoff)


def test_retry_after_forms_and_helpers() -> None:
    now = dt.datetime(2026, 10, 17, 12, 0, 0, tzinfo=dt.UTC)
    assert retry_after_s("12") == 12.0 and retry_after_s(None) is None and retry_after_s("soon") is None
    assert retry_after_s("Sat, 17 Oct 2026 12:00:30 GMT", now) == 30.0
    assert cosine([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0) and cosine([1.0, 0.0], [0.0, 1.0]) == 0.0
    texts = ["a" * 35] * 5 + ["b" * 350]  # 10 tokens each, then 100
    assert plan_batches(texts, batch_size=2, max_tokens=1000) == [[0, 1], [2, 3], [4, 5]]
    assert plan_batches(texts, batch_size=64, max_tokens=30) == [[0, 1, 2], [3, 4], [5]]
    blob = pack_f16([0.5, -0.25, 1.0])
    assert unpack_f16(blob) == [0.5, -0.25, 1.0] and halfvec_literal(blob) == "[0.5,-0.25,1.0]"


def test_against_a_real_local_http_server() -> None:
    """The client over real sockets against a fake Mistral endpoint (stdlib HTTP server on 127.0.0.1)."""
    seen: list[dict[str, Any]] = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            seen.append({"path": self.path, "auth": self.headers.get("Authorization"), "body": body})
            out = {
                "model": "mistral-embed",
                "data": [{"index": i, "embedding": vec(t)} for i, t in enumerate(body["input"])],
            }
            raw = json.dumps(out).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, *args: Any) -> None:
            return None

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        c = cfg(base_url=f"http://127.0.0.1:{server.server_address[1]}")

        async def go() -> Any:
            async with MistralEmbedder(c, api_key="local-key") as emb:
                return await emb.embed(["contrat de travail", "préavis"])

        res = asyncio.run(go())
    finally:
        server.shutdown()
    assert seen == [{"path": "/v1/embeddings", "auth": "Bearer local-key",
                     "body": {"model": "mistral-embed", "input": ["contrat de travail", "préavis"]}}]  # fmt: skip
    assert res.vectors[1] == vec("préavis")


# ------------------------------------------------------------------------------------------- lgx embed
T = dt.date(2026, 10, 16)
WORK = "http://data.legilux.public.lu/eli/etat/leg/loi/2020/01/01/a1"
VERSION = WORK + "/jo"


def chunk(i: int, body: str, *, searchable: bool = True) -> Chunk:
    header = f"Loi test > Art. {i}."
    return Chunk(
        eli_work=WORK,
        eli_version=VERSION,
        unit_ordinal=i,
        header=header,
        body=body,
        doc_type="LOI",
        text_basis=TextBasis.ORIGINAL_NEVER_AMENDED,
        valid_from=dt.date(2020, 1, 1),
        searchable=searchable,
        content_sha256=content_sha256(header, body),
    )


@pytest.fixture
def stage(tmp_path: Path) -> Iterator[Callable[..., StageContext]]:
    def make(
        *, dry_run: bool = False, bodies: tuple[str, ...] = ("le salarié", "le préavis", "abrogé")
    ) -> StageContext:
        ctx = StageContext.create(sync_date=T, data_dir=tmp_path, dry_run=dry_run)
        entry = ManifestEntry.model_validate(
            {"eli_work": WORK, "eli_version": VERSION, "doc_type": "LOI", "text_basis": "original_never_amended",
             "role": "current", "file": None, "reason": "test"}
        )  # fmt: skip
        write_jsonl(ctx.layout.manifest_path, [entry])
        chunks = [chunk(i, b, searchable=i < 3) for i, b in enumerate(bodies, 1)]
        write_jsonl(ctx.layout.chunks_path(VERSION), chunks)
        return ctx

    yield make


def _env(**kw: str) -> dict[str, str]:
    return {"LGX_EMBED_PROVIDER": "mistral", "LGX_EMBED_BASE_URL": "https://mistral.example.test",
            "LGX_EMBED_API_KEY": "k", "LGX_EMBED_DIM": str(DIM), "LGX_EMBED_COOLDOWN_S": "0", **kw}  # fmt: skip


def test_dry_run_prints_the_plan_without_network(stage: Callable[..., StageContext]) -> None:
    def boom(request: httpx.Request) -> httpx.Response:
        raise AssertionError("no request in a dry run")

    ctx = stage(dry_run=True)
    res = run_embed(ctx, transport=httpx.MockTransport(boom), env={})
    plan = res.details["plan"]
    assert res.ok and plan["provider"] == "none" and plan["chunks_searchable"] == 2
    assert (plan["to_embed"], plan["requests"], plan["cached"]) == (2, 1, 0)
    assert plan["throttle"]["max_concurrency"] == 5 and plan["throttle"]["cooldown_s"] == 20.0
    assert plan["est_duration_s"] == 2.0 and "provider=none" in res.messages[0]
    assert not cache_path(ctx.layout.emb_dir, ctx.config.embedding).exists()  # a dry run creates nothing
    # provider none without --dry-run: nothing is computed either (and still no request)
    assert run_embed(stage(), transport=httpx.MockTransport(boom), env={}).ok


def test_embed_is_resumable_and_only_embeds_changed_chunks(stage: Callable[..., StageContext]) -> None:
    fake = FakeEndpoint()
    ctx = stage()
    res = run_embed(ctx, transport=fake.transport, env=_env())
    assert res.ok, res.messages
    assert res.counts["embedded"] == 2 and res.counts["missing"] == 0 and res.details["probe"]
    bodies = [json.loads(r.content)["input"] for r in fake.requests]
    assert bodies[-1] == [PROBE_TEXT] and len(bodies) == 2  # one batch + the probe
    path = cache_path(ctx.layout.emb_dir, ctx.config.embedding.with_env(_env()))
    with EmbeddingCache(path, DIM, readonly=True) as cache:
        assert cache.count() == 2 and cache.meta("model_reported") == "mistral-embed"
        assert cache.meta("probe")["vector"] == vec(PROBE_TEXT)
    # a re-run sends nothing; a changed chunk sends only that chunk
    again = run_embed(ctx, transport=fake.transport, env=_env())
    assert again.ok and again.counts["embedded"] == 0 and len(fake.requests) == 2
    changed = stage(bodies=("le salarié", "le préavis modifié", "abrogé"))
    third = run_embed(changed, transport=fake.transport, env=_env())
    assert third.ok and third.counts["embedded"] == 1
    assert json.loads(fake.requests[-1].content)["input"] == [embed_text(chunk(2, "le préavis modifié"))]


def test_embed_stops_when_the_api_reports_another_model(stage: Callable[..., StageContext]) -> None:
    ctx = stage()
    assert run_embed(ctx, transport=FakeEndpoint().transport, env=_env()).ok
    swapped = run_embed(stage(bodies=("x", "y", "z")), transport=FakeEndpoint(model="other").transport, env=_env())
    assert not swapped.ok and any("model_changed" in m for m in swapped.messages)


def test_embed_needs_a_key(stage: Callable[..., StageContext]) -> None:
    env = _env()
    env.pop("LGX_EMBED_API_KEY")
    res = run_embed(stage(), transport=FakeEndpoint().transport, env=env)
    assert not res.ok and "API key" in res.messages[0]


def test_release_vectors_are_all_or_nothing(stage: Callable[..., StageContext]) -> None:
    ctx = stage()
    c = ctx.config.embedding.with_env(_env())
    shas = [chunk(1, "le salarié").content_sha256, chunk(2, "le préavis").content_sha256]
    none, why = release_vectors(c, ctx.layout.emb_dir, shas)
    assert none is None and "no embedding cache" in why
    assert release_vectors(ctx.config.embedding, ctx.layout.emb_dir, shas) == (None, "provider none")
    assert run_embed(ctx, transport=FakeEndpoint().transport, env=_env()).ok
    rv, why = release_vectors(c, ctx.layout.emb_dir, shas)
    assert why == "ok" and rv is not None and rv.model_id == "mistral-embed" and rv.dim == DIM
    assert rv.probe_text == PROBE_TEXT and len(rv.probe_vector) == DIM
    partial, why = release_vectors(c, ctx.layout.emb_dir, [*shas, "ab" * 32])
    assert partial is None and "1 of 3 searchable chunks have no vector" in why


# ------------------------------------------------------------------------------------------- MCP probe
def release(model: dict[str, Any] | None, rid: str = "data-20261016") -> Any:
    from lgx_core.warnings import Catalogue
    from lgx_mcp.release import ReleaseInfo

    return ReleaseInfo(
        release_id=rid, sync_date=T, horizon=T + dt.timedelta(days=365), schema_version=1, rules_version="r",
        parser_version="p", pipeline_git_sha="x", built_at=dt.datetime(2026, 10, 16, tzinfo=dt.UTC), config={},
        counts={}, gates={}, legal_texts={}, catalogue=Catalogue({}), aliases={},
        embedding_models=[model] if model else [],
    )  # fmt: skip


def model_row(**kw: Any) -> dict[str, Any]:
    row = {"embedding_set_id": 1, "provider": "mistral", "model_id": "mistral-embed",
           "model_requested": "mistral-embed", "dim": DIM, "probe_text": PROBE_TEXT,
           "probe_vector": vec(PROBE_TEXT), "probe_min_cosine": 0.99}  # fmt: skip
    row.update(kw)
    return row


def qv(fake: Any, *, key: str | None = "k", **kw: Any) -> Any:
    from lgx_mcp.vectors import QueryVectors

    transport = fake.transport if isinstance(fake, FakeEndpoint) else fake
    return QueryVectors(cfg(dim=1024, **kw), key, transport=transport)


async def test_probe_ok_then_hybrid_query_vectors() -> None:
    fake = FakeEndpoint()
    v = qv(fake)
    rel = release(model_row())
    assert v.status(rel) == "disabled:not_probed"
    state = await v.probe(rel)
    assert state.status == "ok" and v.status(rel) == "ok" and state.cosine == pytest.approx(1.0)
    assert v.status(release(model_row(), rid="other-release")) == "disabled:not_probed"  # re-probe after a swap
    assert await v.embed_query("délai de préavis", rel) == vec("délai de préavis")
    assert len(fake.requests) == 2 and json.loads(fake.requests[0].content)["input"] == [PROBE_TEXT]
    again = await v.probe(rel)  # cached (re-probe after REPROBE_OK_S)
    assert again is state and len(fake.requests) == 2
    d = v.describe(rel)
    assert d["vector_status"] == "ok" and d["probe"]["cosine"] == pytest.approx(1.0) and d["dim"] == DIM
    await v.aclose()


@pytest.mark.parametrize(
    ("endpoint", "row", "status"),
    [
        (FakeEndpoint(model="mistral-embed-v2"), {}, "disabled:model_mismatch"),
        (FakeEndpoint(dim=DIM + 4), {}, "disabled:dim_mismatch"),
        (FakeEndpoint(), {"probe_vector": [1.0] + [0.0] * (DIM - 1)}, "disabled:probe_mismatch"),
        (FakeEndpoint(script=[httpx.ConnectError("no route")]), {}, "disabled:endpoint_unreachable"),
        (FakeEndpoint(script=[httpx.ReadTimeout("slow")]), {}, "disabled:endpoint_timeout"),
        (FakeEndpoint(script=[httpx.Response(401)]), {}, "disabled:unauthorized"),
        (FakeEndpoint(), {"probe_vector": []}, "disabled:no_probe"),
    ],
)
async def test_failed_probe_means_lexical_with_a_reason(
    endpoint: FakeEndpoint, row: dict[str, Any], status: str
) -> None:
    from lgx_mcp.retrieval.candidates import decide_mode

    v = qv(endpoint)
    rel = release(model_row(**row))
    state = await v.probe(rel)
    assert state.status == status and state.reason
    mode = decide_mode("auto", v.status(rel))
    assert mode.mode_used == "lexical" and mode.reason == f"vectors disabled: {status.removeprefix('disabled:')}"
    with pytest.raises(EmbeddingError):
        await v.embed_query("q", rel)
    await v.aclose()


async def test_states_without_a_probe_request() -> None:
    def boom(request: httpx.Request) -> httpx.Response:
        raise AssertionError("no request expected")

    t = httpx.MockTransport(boom)
    off = qv(t, provider="none")
    assert off.status(release(model_row())) == "disabled:provider_none" and off.describe(None)["probe"] is None
    assert (await qv(t).probe(release(None))).status == "disabled:no_vectors"
    assert (await qv(t, key=None).probe(release(model_row()))).status == "disabled:no_api_key"
    insecure = qv(t, base_url="http://mistral.example.com")
    assert (await insecure.probe(release(model_row()))).status == "disabled:insecure_url"


async def test_query_time_model_swap_and_throttling() -> None:
    fake = FakeEndpoint()
    v = qv(fake)
    rel = release(model_row())
    assert (await v.probe(rel)).status == "ok"
    fake.model = "swapped-model"
    with pytest.raises(EmbeddingError) as exc:
        await v.embed_query("q", rel)
    assert exc.value.reason == "model_mismatch" and v.status(rel) == "disabled:model_mismatch"
    # a request that finds no free slot within timeout_s runs lexical but keeps vectors enabled
    clock = FakeClock()
    from lgx_mcp.vectors import QueryVectors

    v2 = QueryVectors(cfg(dim=1024, max_concurrency=1, cooldown_s=20.0, timeout_s=5.0), "k",
                      transport=FakeEndpoint().transport, clock=clock, sleep=clock.sleep)  # fmt: skip

    async def main() -> None:
        assert (await v2.probe(rel)).status == "ok"  # occupies the only slot for 20 s
        with pytest.raises(EmbeddingError) as thr:
            await v2.embed_query("q", rel)
        assert thr.value.reason == "throttled" and v2.status(rel) == "ok"
        await clock.sleep(20.0)
        assert await v2.embed_query("q", rel) == vec("q")
        await v2.aclose()

    await clock.run(main())
    await v.aclose()


def test_provider_enum_is_none_or_mistral() -> None:
    assert [p.value for p in EmbeddingProvider] == ["none", "mistral"]
