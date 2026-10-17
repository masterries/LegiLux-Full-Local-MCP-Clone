# Development quick start

Build-zone development on one Linux host with Docker. Development and CI use the **stratified sample**
(`config/sample.yaml`, ~50 works); the full crawl (`lgx ingest --full`, ≈ 25k GETs) is an operator command
and is never run in development. Embeddings are off (`config/embedding.yaml: provider: none`).

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
uv run lgx --t 2026-10-16 --opt keep_prev=0 ingest --sample --offline --package --container lgx-dev-db --restore-to legilux
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

`make ingest-sample` (offline sample ingest + package into `lgx-dev-db`, which `make dev-db` creates if needed),
`make dev-up` / `dev-down` / `dev-logs` / `dev-ps` (`deploy/compose.dev.yml`, profile `cpu`, waits until healthy),
`make ollama-pull MODEL=<tag>`, `make e2e` (scripted questions through the UI container), `make egress-check`
(airgap egress check), and host runs `make mcp` (:8001) / `make ui` (:8511). Behind a TLS-intercepting proxy pass
`LGX_DEV_CA_BUNDLE=/path/ca-bundle.crt` to `dev-up` (image builds) and `ollama-pull`. `db-load` restores the bundle only while the stack's
`legilux` database is missing: after a new `make ingest-sample` run `make dev-down`, `docker volume rm legilux-dev-pgdata`
and `make dev-up` (the model volume stays). Results of the first CPU run
and the egress check: `docs/dev/e2e-2026-10-17.md`.

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

## Layout

```text
lgx_core/     shared legal logic: models, config, ELI helpers, rules, normalise, warnings, embed, citeparse, verify
lgx_ingest/   build pipeline + `lgx` CLI (robots, sparql, select, fetch, parse/, structure, chunk, load, gates)
lgx_mcp/      runtime MCP server (M1)
ui/lgx_ui/    Streamlit tester UI helpers (M1b)
config/       scope, sample, embedding, warnings, legal_texts, aliases, denylist, recueil_members
sparql/       Appendix A queries ({{PLACEHOLDER}} syntax, sparql/README.md)
db/           lgx-db Dockerfile, schema/*.sql, tsearch/french_unaccent.stop
deploy/       images.lock (compose files follow in M1/M5)
scripts/      db_apply.sh, make_french_unaccent_stop.py
tests/        unit/, db/, live/, contract/, fixtures/
data/         build-side caches and outputs (gitignored, never shipped)
```
