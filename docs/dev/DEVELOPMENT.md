# Development quick start

Build-zone development on one Linux host with Docker. Development and CI use the **stratified sample**
(`config/sample.yaml`, ~50 works, since M2 including the PDF code-recueils marked `stage: m2`); the full crawl
(`lgx ingest --full`, ≈ 25.6k GETs, [§ Full ingest](#full-ingest-operator-step-never-in-development)) is an operator
command and is never run in development. Embeddings are off (`config/embedding.yaml: provider: none`); no LLM runtime
is bundled: the chat LLM is one external OpenAI-compatible endpoint, by default the user's Mistral endpoint
([§ Dev stack](#dev-stack-one-command-each), [§ Embeddings](#embeddings-off-by-default)).

## Prerequisites

- Python 3.12+ (3.13 recommended, see `.python-version`) and [uv](https://docs.astral.sh/uv/).
- Docker (for the database). `psql` on the host is optional (the scripts can run psql inside the container).

## Setup

```sh
uv sync --all-extras            # or: make sync  (uses uv.lock; all extras + dev tools)
cp .env.example .env            # optional; Settings reads LGX_* from the environment
uv run lgx config-check         # validates config/*.yaml, prints the config digest and resolved T
```

`uv.lock` resolves only releases published ≥ 14 days before M0 (`[tool.uv] exclude-newer`), including
`mcp==2.3.0`, `streamlit==1.65.0` and `openai==3.24.0`. Bump deliberately.

## Database

```sh
make db-up        # builds lgx.local/lgx-db:dev (db/Dockerfile) and starts it on 127.0.0.1:55432
make db-schema    # (re)creates legilux_dev and applies db/schema/*.sql via scripts/db_apply.sh
make db-psql      # psql shell
make db-down      # removes the container (it is --rm, all data is disposable)
```

**Persistent dev database** (created by the loader stage; the MCP and UI stages use it — details and counts in
`docs/dev/INTERFACES.md` §6.1):

```sh
docker build -t lgx.local/lgx-db:dev db
docker volume create lgx-dev-pgdata
docker run -d --name lgx-dev-db --restart unless-stopped -e POSTGRES_PASSWORD=lgx-dev-only \
  -v lgx-dev-pgdata:/var/lib/postgresql -p 127.0.0.1:55432:5432 lgx.local/lgx-db:dev
export LGX_PG_DSN=postgresql://postgres:lgx-dev-only@127.0.0.1:55432/postgres     # build/owner role
uv run lgx --t 2026-10-16 --opt keep_prev=0 ingest --sample --offline --include-m2 --package --container lgx-dev-db --restore-to legilux
docker exec lgx-dev-db psql -U postgres -c "ALTER ROLE lgx_ro PASSWORD 'lgx-ro-dev-only'"   # once per cluster
psql postgresql://lgx_ro:lgx-ro-dev-only@127.0.0.1:55432/legilux                     # what the MCP server uses
```

`legilux` is the runtime database (restored from `data/bundles/data-2026-10-16-sample.dump`, read-only by
default, `lgx_ro` = SELECT only, 5 s statement timeout); `legilux_build_20261016_sample` is the build database.
`make db-up` / `db-down` manage a different, throwaway container (`lgx-db-dev`) on the same port: stop
`lgx-dev-db` first (`docker stop lgx-dev-db`) if you need it. Always pass `--t 2026-10-16` for the dev
sample (T=auto follows the Luxembourg date).

`scripts/db_apply.sh --dbname NAME --recreate [--embed-dim 1024] [--container NAME] [--postload-only]`
applies the parts `00_extensions … 95_roles` in order. Without `--container` it uses the local `psql`
and `PGHOST/PGPORT/PGUSER/PGPASSWORD`. Each release is a fresh database ("rebuild, don't migrate").

## Dev stack (one command each)

`make ingest-sample` (offline sample ingest incl. the m2 PDF codes + package into `lgx-dev-db`, which `make dev-db`
creates if needed; ≈ 2 min with warm caches, the first PDF parse of the 5 sample PDFs ≈ 7 min on 4 CPUs),
`make dev-up` / `dev-down` / `dev-logs` / `dev-ps` (`deploy/compose.dev.yml`: db, db-load, mcp, lgx-streamlit; waits
until healthy; the TEI reranker is OFF by default everywhere — `make dev-up DEV_PROFILES=rerank` adds it and `LGX_RERANK=auto`), `make fetch-models` (pinned reranker into `data/models/hf`, once, connected host;
`FETCH_MODELS_ARGS=--verify-only` checks `deploy/models.lock` offline), `make rerank-up` / `rerank-down` (start or stop
TEI on a running stack and switch the dev `mcp` to `LGX_RERANK=auto` / `off`; TEI is ready ≈ 1.5 min after start on an
idle 4-vCPU host, ≈ 6 min on a busy one), `make eval` (below),
**Timeouts with the reranker on (coupled):** the mcp's `LGX_RERANK_TIMEOUT_S` is the *total* re-ranking budget of one
search (all batches; past it the search returns the lexical order with `retrieval.rerank_reason`), and it must stay below
the UI's per-tool timeout `LGX_MCP_TIMEOUT_S` minus the SQL time and a margin — otherwise a slow re-ranking times out in
the UI instead of falling back (M2 review: 20 s UI vs 60 s server). Dev stack: budget 45 s, UI 60 s; airgap compose:
budget 20 s, UI 45 s; server and UI code defaults 15 s / 30 s. CPU-safe reranker defaults everywhere: `LGX_RERANK_K` 16,
`LGX_RERANK_BODY_CHARS` 600 (16 × 600 characters ≈ 17–25 s on this 4-vCPU host); the startup probe times a search-sized
payload and reports a reranker slower than the budget as unavailable. `tests/unit/test_ui_compose.py` checks the
coupling in both compose files.
`make e2e` (scripted questions through the UI container; `E2E_STUB=1` runs them against `scripts/openai_stub.py`, a
deterministic keyless OpenAI-compatible stub started inside the UI container), `make embed-plan` (`lgx embed --dry-run`),
`make egress-check` (airgap egress check of `data/bundles/data-$LGX_T-sample.dump`; sudo drops an exported `LGX_T`, so
pass `LGX_T=…` on the make line; the chat goes to the stub on the internal network), and host runs `make mcp`
(:8001) / `make ui` (:8511). Behind a TLS-intercepting proxy pass `LGX_DEV_CA_BUNDLE=/path/ca-bundle.crt` to `dev-up`
(image builds). `db-load` restores the bundle only while the stack's `legilux` database is missing: after a new
`make ingest-sample` run `make dev-down`, `docker volume rm legilux-dev-pgdata` and `make dev-up`. Results of the first
end-to-end run and the egress check: `docs/dev/e2e-2026-10-17.md`. Airgap package: `make airgap-export`
(`scripts/airgap_export.sh`) and, as root on the airgap host, `scripts/airgap_import.sh install|update-data|rollback|keys|
firewall|check` (`make airgap-import AIRGAP_BUNDLE=…` for a test install); operator guide: `docs/ANLEITUNG.md`.

**Chat LLM.** No LLM runtime is bundled (user decision 2026-10-17). The UI talks to one external OpenAI-compatible
endpoint: `OPENAI_BASE_URL` (`LGX_LLM_BASE_URL`, default `https://api.mistral.ai/v1`), the key file `LGX_LLM_KEY_FILE`
(`make dev-up` uses `secrets/llm_api_key` when it exists — owned by uid 10001, mode 0400, or the non-root UI stops with `UiSettingsError`; default `/dev/null` = no key) and `LGX_LLM_MODEL` (default
`mistral-small-latest`, [U]: whatever the endpoint serves). In the airgap the same variables name the user's on-prem
Mistral domain (same API as Mistral SaaS). The sidebar's Base URL / model fields accept any OpenAI-compatible URL; the
key is only ever sent to the configured one. Open WebUI (optional, M5) points to the same URL.

## Evaluation (PLAN §11, `eval/README.md`)

```sh
make eval                                   # G0 G1 G2 G3, lexical, in process against legilux -> data/reports/eval/
make eval EVAL_SETS=G2 EVAL_MODE=rerank EVAL_ARGS="--subset eval/gold/subsets/G2-rerank-40.json --tag G2-rerank-subset40"
uv run python -m eval.harness compare --subset eval/gold/subsets/G2-rerank-40.json eval/reports/G2-*.json
```

Reports go to `data/reports/eval/` by default (not committed; a run stamp in the name); the committed baselines in
`eval/reports/` are written only with an explicit `--out-dir eval/reports --tag …`. Reranked runs cost 10–45 s per search on 4 vCPU: run them on the fixed stratified subset (`python -m eval.harness subset`)
and never two at a time. Results and the M2 acceptance check: `eval/reports/m2-baseline.md`, `eval/reports/m2-acceptance.md`.

## Embeddings (off by default)

`config/embedding.yaml` holds `provider: none` (user decision): nothing is computed, nothing is sent, `vector_status =
disabled:provider_none`, retrieval runs lexical (+ reranker). The one provider is `mistral` — the Mistral embeddings API
(`POST {base_url}/v1/embeddings`, model `mistral-embed`, 1024 dims, Bearer key from `LGX_EMBED_API_KEY_FILE`), the
**same** API for documents on the build side (`base_url: https://api.mistral.ai`) and for queries in the airgap (the
on-prem domain in `LGX_EMBED_BASE_URL`). Every key of the file can be overridden by `LGX_EMBED_*`.

```sh
uv run lgx --t 2026-10-16 embed --dry-run         # = make embed-plan: the plan, never a request (any provider)
LGX_EMBED_PROVIDER=mistral LGX_EMBED_API_KEY_FILE=secrets/mistral_api_key uv run lgx --t 2026-10-16 embed
uv run lgx --t 2026-10-16 load                     # attaches the vectors (all searchable chunks or none) + HNSW
```

- **Throttle** (one limiter, `lgx_core/embed/limiter.py`, shared by `lgx embed` and the MCP server's query embedding):
  at most `max_concurrency` (5) requests in flight, and each request slot waits `cooldown_s` (20 s) after its request
  completed before it is reused; `batch_size` (64) texts per request, split earlier at `max_tokens_per_request`
  (16,000 estimated tokens); `timeout_s` (60; lgx-mcp 5); `retries` (5) on 429 / 5xx / network errors with
  `backoff_s` and `Retry-After` (the longer wins; every retry takes a slot too).
- **Throughput** ≈ `max_concurrency × texts_per_request / (latency + cooldown_s)` texts/s. With the defaults and an
  assumed 2 s per request [E]: ≤ 5 × 64 / 22 ≈ **14.5 texts/s** at full batches. On the dev sample the token budget
  binds first (≈ 374 estimated tokens per chunk → 41.8 texts per request), so `lgx embed --dry-run` (2026-10-17, T =
  2026-10-16) reports 19,682 searchable chunks → 19,634 distinct texts, 459 requests,
  **≈ 9.7 texts/s, ≈ 0.56 h** (sample after the M2 review fixes; `make embed-plan`). **Full corpus** (≈ 400k chunks [E], PLAN §0): ≈ 9,350 requests ≈
  **11.4 h** at the default throttle (7.6 h if every request carried 64 texts); the cooldown dominates — with
  `LGX_EMBED_COOLDOWN_S=0` the same run would take ≈ 1.1 h at 2 s latency. Resumable: re-run the same command.
- **Cache** `data/emb/<model>-<dim>.sqlite` (build side only, never shipped): `content_sha256 → float16 vector` plus
  the model id the API reported; every finished request is committed, so an interrupted run resumes and a re-run only
  embeds new or changed chunks. A second reported model id stops the run (`model_changed`).
- **Load**: with `provider: mistral` and a cache that covers *every* searchable chunk, `lgx load` writes
  `lgx.embedding_model` (reported model id, dim, the startup probe text + vector) and `chunk.embedding`; the post-load
  builds the HNSW index; gate `embedding_vectors`. Otherwise the release is lexical-only with a message.
- **Runtime** (`lgx_mcp/vectors.py`): one startup probe — the endpoint's reported model id and dim must equal the
  release's `lgx.embedding_model` row and the probe vector's cosine must be ≥ `probe_min_cosine` (0.99). Hybrid (dense +
  lexical, RRF k = 60) only when the release has vectors **and** the probe passed; a failed probe, a missing key, an
  unreachable endpoint or a throttled query gives lexical with the reason (`retrieval.reason`, `/readyz`
  `vector_reason`), never an error. Tests use fake endpoints only (`tests/unit/test_embed.py`,
  `tests/contract/test_mcp_vectors.py`); no real embedding is ever computed in development.

## Tests and lint

```sh
make lint         # ruff check + ruff format --check
make fmt          # apply formatting
make test         # uv run pytest -q   (offline; includes the db tests when Docker is available)
make test-db      # only tests marked db
make test-live    # tests marked live: a few polite requests to Legilux (opt-in)
```

- `@pytest.mark.live` tests are skipped unless `--run-live` or `LGX_RUN_LIVE=1`.
- `@pytest.mark.db` tests build `lgx.local/lgx-db:test`, start a throwaway container on a random local
  port, apply the schema to two fresh databases and check objects, vocabulary parity with
  `lgx_core.models`, status/serving functions, the EXCLUDE constraint, lexical search and the conditional
  HNSW index. Set `LGX_TEST_PG_DSN=postgresql://…` to use an existing server instead (CI does this with a
  pgvector service container) and `LGX_TEST_PG_CONTAINER=<its container>` so the package → pg_dump/pg_restore →
  rename-swap path runs too; they are skipped when neither is available.
- CI (`.github/workflows/ci.yml`) also restores the offline fixture release as `legilux`
  (`scripts/ci_fixture_release.py`), runs the `any_release` MCP contract tests against it with
  `LGX_TEST_REQUIRE_DB=1`, and builds both runtime images. The sample-content contract tests need the dev DB.

## Pipeline (sample)

```sh
uv run lgx --help
uv run lgx paths                      # where each stage reads/writes under data/
uv run lgx --t 2026-10-16 ingest --sample --offline   # robots → discover → select → fetch → parse → chunk
                                                     #   → load → gates → report (+ --package)
uv run lgx --t 2026-10-16 load | gates | report | package --container lgx-dev-db [--restore-to legilux]
```

`ingest` reuses the robots state and the SPARQL snapshot of T (`--refresh` pulls again), fetches only files
missing from `data/raw/` (`--offline`: never a request), and loads when `LGX_PG_DSN`/`--dsn` is set. Reports go
to `data/reports/<T>/` (`load.json`, `gates.json`, `sizing.json`). Stages not implemented yet exit with code 3. Contracts between stages: `docs/dev/INTERFACES.md`.
Politeness is built in: ≤ 1 request/s (`LGX_RATE_LIMIT_S`, default 1.2 s), User-Agent
`LegiLux-Local-Clone/0.1 (+https://github.com/masterries/LegiLux-Full-Local-MCP-Clone)`, robots.txt checked
every run, only `https://legilux.public.lu/filestore/eli/etat/leg/…` is ever fetched.

## Full ingest (operator step, never in development)

`lgx ingest --full` builds the release from the whole metadata scope (PLAN §6.2): every act with a text basis gets its
file, not only the sample works. It is run by the operator on the build host; development and CI never run it.

**1. Plan first (no request at all):**

```sh
uv run lgx --t 2026-10-16 ingest --full --dry-run     # robots + snapshot reused, select, plan → reports/<T>/fetch_plan_full.json
```

Dry run of 2026-10-17 on the T = 2026-10-16 snapshot (26 s, 0 requests):

| | |
|---|---|
| Versions that get a file | 25,569 (17,396 HTML JO originals, 7,034 XML originals, 1,065 XML consolidations, 21 XML recueils, 9 XML codes, 33 PDF: 5 code-recueils, 16 recueils, 12 consolidations); 49 already cached (the sample) |
| Requests | 25,520 file GETs + 52 Q-MANIF SPARQL requests (the snapshot's file list covers the sample works only; a real `--full` run pulls Q-MANIF for every served version first) + 2 robots.txt; ≈ 12 flat SPARQL pulls (≈ 140 MB) only with `--refresh` |
| Duration | 7.1 h at exactly 1 req/s; 8.5 h at the default 1.2 s spacing; **12.8 h of fetching under the 2,000 requests/hour ceiling = 4 nightly windows** (01:00–05:00 Europe/Luxembourg, ≤ 8,000 GETs each); ≤ 30,000 per day |
| Disk | raw files ≈ 2.9 GB [E] (sample means per format and kind; `code/environnement_annexes` 239.9 MB excluded), parsed/units/chunks caches ≈ 1–1.5× that, build database ≈ 0.9–1.3 GB and dump ≈ 0.2–0.5 GB (PLAN §4.2): **keep ≥ 8 GB free** in `data/` and the PostgreSQL volume |
| Parse | the 33 PDFs dominate (the 5 sample PDFs: 7 min on 4 CPUs); XML/HTML parse ≈ minutes |

**2. Run (on the build host, inside the window):**

```sh
export LGX_PG_DSN=postgresql://postgres:…@127.0.0.1:55432/postgres
uv run lgx --t <T> ingest --full --confirm-full-crawl --package --container lgx-dev-db --restore-to legilux
```

- **Window.** File GETs run only inside `scope.fetch.nightly_window` (01:00–05:00 Europe/Luxembourg); outside it the
  fetch stops before the next request with « outside the nightly window … » (exit 1, resumable).
  `--ignore-window` is the operator opt-in for one ≈ 13 h session; every rate limit stays (≥ 1.2 s spacing, ≤ 2,000/h,
  ≤ 30,000/day, backoff, circuit breaker).
- **Resumable.** Completed URLs are listed in `data/logs/fetch_progress_<T>.txt` and skipped; files already in
  `data/raw/` are reused (`--revalidate` sends conditional GETs for them). Re-run the same command after the window
  closed, an hourly/daily ceiling, the circuit breaker or an interruption; nothing is fetched twice for T. The parse and
  chunk caches are keyed by file hash + parser version, the load rebuilds the database from the caches ("rebuild,
  don't migrate"). The gates decide whether the release is packaged.
- **What it fetches:** robots.txt of both hosts (every run that fetches), the SPARQL endpoint (Q-MANIF; the flat pulls only
  with `--refresh`), and files under `https://legilux.public.lu/filestore/eli/etat/leg/…` — nothing else
  (`lgx_core.eli.assert_fetchable` before every request), one connection, User-Agent `LegiLux-Local-Clone/…`.
- **What it never fetches:** URLs outside that filestore branch; robots-disallowed paths and denylisted ELIs (GDPR list);
  Mémorial B, RC, naturalisation acts, EU texts, drafts and closed versions' text (not selected); DOCX / PDF-A duplicates
  (one file per version); `code/environnement_annexes` (239.9 MB, metadata-only unless `include_env_annexes_text` +
  the allowlist); files above 64 MB that are not allow-listed.

## Layout

```text
lgx_core/     shared legal logic: models, config, ELI helpers, rules, normalise, warnings, embed, citeparse, verify
lgx_ingest/   build pipeline + `lgx` CLI (robots, sparql, select, fetch, parse/, structure, chunk, load, gates)
lgx_mcp/      runtime MCP server (M1; query embeddings + startup probe: vectors.py)
ui/lgx_ui/    Streamlit tester UI helpers (M1b)
config/       scope, sample, embedding, warnings, legal_texts, aliases, denylist, recueil_members
sparql/       Appendix A queries ({{PLACEHOLDER}} syntax, sparql/README.md)
db/           lgx-db Dockerfile, schema/*.sql, tsearch/french_unaccent.stop
deploy/       compose.yml (airgap), compose.dev.yml (dev), images.lock, models.lock, .env.example
scripts/      db_apply.sh, fetch_models.sh, e2e_chat.py, openai_stub.py, egress_check.sh, …
tests/        unit/, db/, live/, contract/, fixtures/
data/         build-side caches and outputs (gitignored, never shipped)
```
