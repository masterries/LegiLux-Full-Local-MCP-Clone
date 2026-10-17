"""S7 embed (PLAN §6.5): searchable chunks of T → Mistral embeddings, cached by content hash. OFF by default.

* **Provider ``none``** (the default): nothing is computed and nothing is sent. ``lgx embed --dry-run`` still
  prints the plan (chunks, requests, estimated tokens and duration under the throttle) without any network.
* **Provider ``mistral``**: ``POST {base_url}/v1/embeddings`` (:class:`lgx_core.embed.MistralEmbedder`) through
  the shared limiter (``max_concurrency`` requests in flight, ``cooldown_s`` per slot after each request), with
  retries on 429 / 5xx honouring ``Retry-After``.

**Cache** (``data/emb/<model>-<dim>.sqlite``, build side only, never shipped): one row per
``content_sha256 = sha256(header + body)`` with the float16 vector (what ``halfvec`` stores anyway) and the model
id the API reported. Every finished request is committed, so an interrupted run resumes where it stopped and a
re-run only embeds new or changed chunks. The cache also keeps the startup probe (:data:`PROBE_TEXT` and its
full-precision vector) and refuses to mix two reported model ids.

**Load** (:func:`release_vectors`, used by ``lgx load``): when the provider is ``mistral`` and the cache covers
*every* searchable chunk of the release, the release gets ``lgx.embedding_model`` (reported model id, dim, probe)
and ``chunk.embedding``; otherwise it is loaded lexical-only with a message (never half-embedded).
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import math
import re
import sqlite3
import struct
import time
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from lgx_core.config import EmbeddingConfig
from lgx_core.embed import (
    PROBE_TEXT,
    EmbeddingError,
    MistralEmbedder,
    estimate_duration_s,
    plan_batches,
)
from lgx_core.io import atomic_write_text, read_jsonl
from lgx_core.models import Chunk, ManifestEntry
from lgx_core.textsize import estimate_tokens
from lgx_ingest.context import StageContext, StageResult

DEFAULT_LATENCY_S = 2.0  # [E] assumed seconds per embeddings request (64 texts) for the duration estimate
FULL_CORPUS_CHUNKS = 400_000  # [E] PLAN §0 "≈ 400k ± 30 %" searchable chunks of the full release
FATAL_REASONS = {"unauthorized", "dim_mismatch", "bad_response", "model_changed"}


def embed_text(chunk: Chunk) -> str:
    """What is sent for a chunk: header + body (the content hash covers exactly these two)."""
    return f"{chunk.header}\n{chunk.body}"


def _slug(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("_") or "model"


def cache_path(emb_dir: Path, cfg: EmbeddingConfig) -> Path:
    return emb_dir / f"{_slug(cfg.model)}-{cfg.dim}.sqlite"


def pack_f16(vector: Iterable[float]) -> bytes:
    values = list(vector)
    return struct.pack(f"<{len(values)}e", *values)


def unpack_f16(blob: bytes) -> list[float]:
    return list(struct.unpack(f"<{len(blob) // 2}e", blob))


def halfvec_literal(blob: bytes) -> str:
    return "[" + ",".join(repr(x) for x in unpack_f16(blob)) + "]"


# ------------------------------------------------------------------------------------------------ cache
class EmbeddingCache:
    """SQLite file: ``vec(sha256, model, vec)`` + ``meta(key, value)`` (model, dim, probe)."""

    def __init__(self, path: Path, dim: int, *, readonly: bool = False) -> None:
        self.path = path
        self.dim = dim
        if readonly:
            self._db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            self._db = sqlite3.connect(path)
            self._db.executescript(
                "PRAGMA journal_mode=WAL;"
                "CREATE TABLE IF NOT EXISTS vec (sha256 TEXT PRIMARY KEY, model TEXT NOT NULL, vec BLOB NOT NULL,"
                " created_at TEXT NOT NULL);"
                "CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);"
            )

    @classmethod
    def open_existing(cls, path: Path, dim: int) -> EmbeddingCache | None:
        return cls(path, dim, readonly=True) if path.exists() else None

    def close(self) -> None:
        self._db.close()

    def __enter__(self) -> EmbeddingCache:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def meta(self, key: str) -> Any:
        row = self._db.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def set_meta(self, key: str, value: Any) -> None:
        self._db.execute("INSERT OR REPLACE INTO meta VALUES (?, ?)", (key, json.dumps(value)))
        self._db.commit()

    def known(self, shas: Iterable[str]) -> set[str]:
        wanted = list(shas)
        found: set[str] = set()
        for i in range(0, len(wanted), 900):  # SQLite's host-parameter limit
            part = wanted[i : i + 900]
            q = f"SELECT sha256 FROM vec WHERE sha256 IN ({','.join('?' * len(part))})"
            found.update(r[0] for r in self._db.execute(q, part))
        return found

    def count(self) -> int:
        return int(self._db.execute("SELECT count(*) FROM vec").fetchone()[0])

    def models(self) -> list[str]:
        return [r[0] for r in self._db.execute("SELECT DISTINCT model FROM vec ORDER BY 1")]

    def put_many(self, shas: list[str], vectors: list[list[float]], model: str) -> None:
        now = dt.datetime.now(dt.UTC).isoformat(timespec="seconds")
        rows = []
        for sha, vec in zip(shas, vectors, strict=True):
            if len(vec) != self.dim:
                raise EmbeddingError(f"vector length {len(vec)} != dim {self.dim}", reason="dim_mismatch")
            rows.append((sha, model, pack_f16(vec), now))
        self._db.executemany("INSERT OR REPLACE INTO vec VALUES (?, ?, ?, ?)", rows)
        self._db.commit()

    def blobs(self, shas: Iterable[str]) -> Iterator[tuple[str, bytes]]:
        wanted = list(shas)
        for i in range(0, len(wanted), 900):
            part = wanted[i : i + 900]
            q = f"SELECT sha256, vec FROM vec WHERE sha256 IN ({','.join('?' * len(part))})"
            yield from self._db.execute(q, part)


# ------------------------------------------------------------------------------------------------ inputs
def searchable_texts(ctx: StageContext) -> tuple[dict[str, str], int]:
    """``content_sha256 → text`` of the searchable chunks of every manifest version that has chunks for T, and
    the number of searchable chunks (identical texts are embedded once)."""
    layout = ctx.layout
    texts: dict[str, str] = {}
    n = 0
    if not layout.manifest_path.exists():
        return texts, 0
    seen: set[str] = set()
    for entry in read_jsonl(layout.manifest_path, ManifestEntry):
        if entry.eli_version is None or entry.eli_version in seen:
            continue
        seen.add(entry.eli_version)
        path = layout.chunks_path(entry.eli_version)
        if not path.exists():
            continue
        for c in read_jsonl(path, Chunk):
            if c.searchable:
                n += 1
                texts.setdefault(c.content_sha256, embed_text(c))
    return texts, n


@dataclass
class EmbedPlan:
    chunks_searchable: int
    unique_texts: int
    cached: int
    todo: list[str]
    batches: list[list[int]]
    est_tokens: int

    def as_dict(self, cfg: EmbeddingConfig, latency_s: float) -> dict[str, Any]:
        requests = len(self.batches)
        duration = estimate_duration_s(requests, cfg.max_concurrency, cfg.cooldown_s, latency_s)
        # texts per request as planned here (the token budget usually binds before batch_size does)
        per_request = len(self.todo) / requests if requests else float(cfg.batch_size)
        full_requests = math.ceil(FULL_CORPUS_CHUNKS / max(per_request, 1.0))
        full = estimate_duration_s(full_requests, cfg.max_concurrency, cfg.cooldown_s, latency_s)
        throughput = cfg.max_concurrency * per_request / max(latency_s + cfg.cooldown_s, 1e-9)
        return {
            "provider": cfg.provider.value,
            "endpoint": cfg.endpoint,
            "model": cfg.model,
            "dim": cfg.dim,
            "chunks_searchable": self.chunks_searchable,
            "unique_texts": self.unique_texts,
            "cached": self.cached,
            "to_embed": len(self.todo),
            "requests": requests,
            "est_tokens": self.est_tokens,
            "throttle": {
                "max_concurrency": cfg.max_concurrency,
                "cooldown_s": cfg.cooldown_s,
                "batch_size": cfg.batch_size,
                "max_tokens_per_request": cfg.max_tokens_per_request,
                "assumed_latency_s": latency_s,
                "texts_per_request": round(per_request, 1),
                "throughput_texts_per_s": round(throughput, 2),
                "formula": "max_concurrency x texts_per_request / (latency + cooldown_s)",
            },
            "est_duration_s": round(duration, 1),
            "est_duration_h": round(duration / 3600, 2),
            "full_corpus_estimate": {
                "chunks": FULL_CORPUS_CHUNKS,
                "requests": full_requests,
                "est_duration_h": round(full / 3600, 1),
                "basis": "[E] ≈ 400k searchable chunks (PLAN §0) at this plan's texts per request",
            },
        }


def make_plan(texts: Mapping[str, str], chunks_searchable: int, cfg: EmbeddingConfig, cache: EmbeddingCache | None):
    shas = sorted(texts)
    known = cache.known(shas) if cache is not None else set()
    todo = [s for s in shas if s not in known]
    batches = plan_batches([texts[s] for s in todo], cfg.batch_size, cfg.max_tokens_per_request)
    tokens = sum(estimate_tokens(texts[s]) for s in todo)
    return EmbedPlan(chunks_searchable, len(shas), len(known), todo, batches, tokens)


# ------------------------------------------------------------------------------------------------ run
@dataclass
class RunOutcome:
    embedded: int = 0
    requests: int = 0
    failed_batches: int = 0
    errors: list[str] = field(default_factory=list)
    fatal: str | None = None
    model: str | None = None
    tokens: int = 0
    retries: int = 0
    limiter: dict[str, Any] = field(default_factory=dict)


async def embed_batches(
    embedder: MistralEmbedder,
    cache: EmbeddingCache,
    texts: Mapping[str, str],
    plan: EmbedPlan,
    *,
    progress: Any = None,
) -> RunOutcome:
    """Run every batch through ``max_concurrency`` workers; each finished request is committed to the cache."""
    out = RunOutcome(model=cache.meta("model_reported"))
    queue: asyncio.Queue[list[str]] = asyncio.Queue()
    for batch in plan.batches:
        queue.put_nowait([plan.todo[i] for i in batch])
    stop = asyncio.Event()

    async def worker() -> None:
        while not stop.is_set():
            try:
                shas = queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            try:
                res = await embedder.embed([texts[s] for s in shas])
                if out.model is None:
                    out.model = res.model
                    cache.set_meta("model_reported", res.model)
                elif res.model != out.model:
                    raise EmbeddingError(
                        f"the API now reports model {res.model!r}, the cache holds {out.model!r}: pin the model id"
                        f" or start a new cache ({cache.path})",
                        reason="model_changed",
                    )
                cache.put_many(shas, res.vectors, res.model)
                out.embedded += len(shas)
                out.requests += 1
                if progress is not None and out.requests % 10 == 0:
                    progress(f"embedded {out.embedded} / {len(plan.todo)} texts ({out.requests} requests)")
            except EmbeddingError as exc:
                out.failed_batches += 1
                if len(out.errors) < 20:
                    out.errors.append(f"{exc.reason}: {exc}")
                if exc.reason in FATAL_REASONS:
                    out.fatal = f"{exc.reason}: {exc}"
                    stop.set()

    await asyncio.gather(*(worker() for _ in range(embedder.limiter.max_concurrency)))
    out.tokens = embedder.tokens
    out.retries = embedder.retries
    out.limiter = embedder.limiter.stats()
    return out


async def ensure_probe(embedder: MistralEmbedder, cache: EmbeddingCache, model: str | None) -> dict[str, Any]:
    """Embed :data:`PROBE_TEXT` once per cache (and again if the reported model changed)."""
    probe = cache.meta("probe")
    if probe and probe.get("text") == PROBE_TEXT and (model is None or probe.get("model") == model):
        return probe
    res = await embedder.embed([PROBE_TEXT])
    probe = {
        "text": PROBE_TEXT,
        "vector": res.vectors[0],
        "model": res.model,
        "embedded_at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
    }
    cache.set_meta("probe", probe)
    if cache.meta("model_reported") is None:
        cache.set_meta("model_reported", res.model)
    return probe


def run_embed(
    ctx: StageContext,
    *,
    latency_s: float = DEFAULT_LATENCY_S,
    env: Mapping[str, str] | None = None,
    transport: Any = None,
) -> StageResult:
    """S7. ``ctx.dry_run``: the plan only (never a request). Provider ``none``: nothing (exit 0)."""
    cfg = ctx.config.embedding.with_env(env)
    result = StageResult(stage="embed")
    path = cache_path(ctx.layout.emb_dir, cfg)
    texts, n = searchable_texts(ctx)
    if ctx.dry_run or not cfg.enabled:
        cache = EmbeddingCache.open_existing(path, cfg.dim)
        try:
            plan = make_plan(texts, n, cfg, cache)
        finally:
            if cache is not None:
                cache.close()
        details = plan.as_dict(cfg, latency_s)
        result.details = {"dry_run": ctx.dry_run, "cache": str(path), "plan": details}
        result.counts = {k: details[k] for k in ("chunks_searchable", "unique_texts", "cached", "to_embed", "requests")}
        if not cfg.enabled:
            result.messages.append(
                "provider=none: no vectors are computed and nothing is sent (PLAN §6.5); "
                "the plan shows what provider mistral would do"
            )
        else:
            result.messages.append("dry run: no request made")
        h = details["est_duration_h"]
        result.messages.append(
            f"{details['to_embed']} texts to embed in {details['requests']} requests ≈ {h} h at "
            f"{details['throttle']['throughput_texts_per_s']} texts/s ({cfg.max_concurrency} in flight, "
            f"{cfg.cooldown_s:g} s cooldown, {latency_s:g} s assumed latency)"
        )
        if not texts:
            result.messages.append("no chunks for T: run `lgx ingest` (or `lgx chunk`) first")
        return result

    try:
        api_key = cfg.api_key(env)
    except ValueError as exc:
        return StageResult(stage="embed", ok=False, messages=[str(exc)])
    if not api_key:
        return StageResult(
            stage="embed",
            ok=False,
            messages=["provider mistral needs an API key: LGX_EMBED_API_KEY_FILE (0600 file) or LGX_EMBED_API_KEY"],
        )
    started = time.monotonic()
    with EmbeddingCache(path, cfg.dim) as cache:
        plan = make_plan(texts, n, cfg, cache)
        ctx.console.print(
            f"embed: {len(plan.todo)} of {plan.unique_texts} texts to embed in {len(plan.batches)} requests"
            f" ({plan.cached} cached) → {cfg.endpoint}"
        )

        async def go() -> tuple[RunOutcome, dict[str, Any] | None]:
            async with MistralEmbedder(cfg, api_key=api_key, transport=transport, trust_env=True) as emb:
                outcome = await embed_batches(emb, cache, texts, plan, progress=lambda m: ctx.console.print(m))
                probe = None
                if outcome.fatal is None:
                    try:
                        probe = await ensure_probe(emb, cache, outcome.model)
                    except EmbeddingError as exc:
                        outcome.errors.append(f"probe: {exc.reason}: {exc}")
                        outcome.fatal = outcome.fatal or (
                            f"{exc.reason}: {exc}" if exc.reason in FATAL_REASONS else None
                        )
                outcome.limiter = emb.limiter.stats()
                return outcome, probe

        outcome, probe = asyncio.run(go())
        missing = len(texts) - len(cache.known(texts))
        result.counts = {
            "chunks_searchable": n,
            "unique_texts": len(texts),
            "cached_before": plan.cached,
            "embedded": outcome.embedded,
            "requests": outcome.requests,
            "failed_batches": outcome.failed_batches,
            "missing": missing,
        }
        result.details = {
            "cache": str(path),
            "endpoint": cfg.endpoint,
            "model_requested": cfg.model,
            "model_reported": outcome.model or (probe or {}).get("model"),
            "dim": cfg.dim,
            "tokens": outcome.tokens,
            "retries": outcome.retries,
            "limiter": outcome.limiter,
            "probe": bool(probe),
            "seconds": round(time.monotonic() - started, 1),
            "errors": outcome.errors,
        }
    result.ok = outcome.fatal is None and missing == 0 and probe is not None
    if outcome.fatal:
        result.messages.append(f"stopped: {outcome.fatal}")
    if missing:
        result.messages.append(f"{missing} texts still without a vector: re-run `lgx embed` (resumes from the cache)")
    if result.ok:
        result.messages.append("all searchable chunks have vectors: the next `lgx load` attaches them")
    report = ctx.layout.reports_dir / "embed.json"
    atomic_write_text(report, json.dumps(result.model_dump(mode="json"), indent=2, ensure_ascii=False) + "\n")
    result.outputs.append(str(report))
    return result


# ------------------------------------------------------------------------------------------------ load
@dataclass(frozen=True)
class ReleaseVectors:
    """What ``lgx load`` attaches: every searchable chunk's vector + the model row (with the startup probe)."""

    cache: Path
    cfg: EmbeddingConfig
    model_id: str
    probe_text: str
    probe_vector: list[float]
    shas: frozenset[str]

    @property
    def dim(self) -> int:
        return self.cfg.dim


def release_vectors(cfg: EmbeddingConfig, emb_dir: Path, searchable_shas: Iterable[str]) -> tuple[Any, str]:
    """``(ReleaseVectors | None, reason)``: vectors only when the cache covers every searchable chunk."""
    if not cfg.enabled:
        return None, "provider none"
    shas = frozenset(searchable_shas)
    path = cache_path(emb_dir, cfg)
    cache = EmbeddingCache.open_existing(path, cfg.dim)
    if cache is None:
        return None, f"no embedding cache {path}: run `lgx embed`"
    with cache:
        missing = len(shas) - len(cache.known(shas))
        probe = cache.meta("probe")
        models = cache.models()
    if missing:
        return None, f"{missing} of {len(shas)} searchable chunks have no vector in {path.name}: run `lgx embed`"
    if not probe:
        return None, f"{path.name} has no startup probe: run `lgx embed`"
    if len(models) > 1:
        return None, f"{path.name} mixes model ids {models}: start a new cache"
    model_id = models[0] if models else str(probe["model"])
    if probe.get("model") != model_id:
        return None, f"probe model {probe.get('model')!r} != chunk model {model_id!r}: run `lgx embed`"
    return ReleaseVectors(path, cfg, model_id, str(probe["text"]), list(probe["vector"]), shas), "ok"


def attach_vectors(conn: Any, vectors: ReleaseVectors, *, embedding_set_id: int = 1) -> int:
    """Insert the model row and set ``chunk.embedding`` for every searchable chunk (inside one transaction)."""
    with conn.transaction(), conn.cursor() as cur:
        cur.execute(
            "INSERT INTO lgx.embedding_model (embedding_set_id, provider, model_id, model_requested, dim, normalized,"
            " probe_text, probe_vector, probe_min_cosine, chunk_count) VALUES (%s, 'mistral', %s, %s, %s, true,"
            " %s, %s, %s, %s)",
            (
                embedding_set_id,
                vectors.model_id,
                vectors.cfg.model,
                vectors.dim,
                vectors.probe_text,
                vectors.probe_vector,
                vectors.cfg.probe_min_cosine,
                len(vectors.shas),
            ),
        )
        cur.execute(f"CREATE TEMP TABLE emb_in (sha bytea PRIMARY KEY, v halfvec({int(vectors.dim)})) ON COMMIT DROP")
        with (
            EmbeddingCache(vectors.cache, vectors.dim, readonly=True) as cache,
            cur.copy("COPY emb_in (sha, v) FROM STDIN") as cp,
        ):
            for sha, blob in cache.blobs(sorted(vectors.shas)):
                cp.write_row((bytes.fromhex(sha), halfvec_literal(blob)))
        cur.execute(
            "UPDATE lgx.chunk c SET embedding = e.v, embedding_set_id = %s FROM emb_in e"
            " WHERE c.content_sha256 = e.sha AND c.searchable",
            (embedding_set_id,),
        )
        n = cur.rowcount
        cur.execute(
            "UPDATE lgx.embedding_model SET chunk_count = %s WHERE embedding_set_id = %s", (n, embedding_set_id)
        )
    return int(n)


__all__ = [
    "DEFAULT_LATENCY_S",
    "EmbedPlan",
    "EmbeddingCache",
    "ReleaseVectors",
    "attach_vectors",
    "cache_path",
    "embed_batches",
    "embed_text",
    "ensure_probe",
    "make_plan",
    "release_vectors",
    "run_embed",
    "searchable_texts",
]
