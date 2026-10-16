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

`scripts/db_apply.sh --dbname NAME --recreate [--embed-dim 1024] [--container NAME] [--postload-only]`
applies the parts `00_extensions … 95_roles` in order. Without `--container` it uses the local `psql`
and `PGHOST/PGPORT/PGUSER/PGPASSWORD`. Each release is a fresh database ("rebuild, don't migrate").

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
  pgvector service container); they are skipped when neither is available.

## Pipeline (sample)

```sh
uv run lgx --help
uv run lgx paths                      # where each stage reads/writes under data/
uv run lgx ingest --sample            # robots → discover → select → fetch → parse → chunk
```

Stages not implemented yet exit with code 3. Contracts between stages: `docs/dev/INTERFACES.md`.
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
