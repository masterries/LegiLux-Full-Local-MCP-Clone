# lgx-mcp — the read-only MCP server (PLAN §8, Appendix D)

Five read-only tools over the release database, on the official SDK `mcp==2.3.0`:
`search_legislation`, `get_article`, `get_act`, `resolve_citation`, `about_corpus` (contracts: Appendix D.2,
published verbatim by `lgx_mcp/schemas.py`). Every result has `structuredContent` (valid against the tool's
`outputSchema`) and one compact French `TextContent` (legal text wrapped in `<<<TEXTE … >>>`, ≤
`LGX_MAX_RESULT_TOKENS` by `ceil(chars / 3.5)`). Errors: `isError: true`, `structuredContent.error = {code,
message, hint, candidates}` with `NOT_FOUND | AMBIGUOUS | OUT_OF_SCOPE | BAD_INPUT | AS_OF_OUT_OF_RANGE |
BACKEND_UNAVAILABLE`.

## Run

```sh
# host (dev DB from the loader stage, docs/dev/INTERFACES.md §6.1)
export LGX_MCP_PG_DSN=postgresql://lgx_ro:lgx-ro-dev-only@127.0.0.1:55432/legilux
export LGX_MCP_TOKEN=lgx-dev-mcp-token            # dev only; airgap: LGX_MCP_TOKEN_FILE=/run/secrets/mcp_token
uv run python -m lgx_mcp --port 8000               # Streamable HTTP at http://127.0.0.1:8000/mcp
uv run python -m lgx_mcp --stdio                   # same server over stdio (no token)
uv run python -m lgx_mcp --check                   # prints /readyz once (exit 0 = ready)

# container (non-root uid 10001, read-only root, no egress needed)
docker build -f docker/lgx-mcp.Dockerfile --secret id=ca_bundle,src=/root/.ccr/ca-bundle.crt -t lgx.local/lgx-mcp:dev .
docker run -d --name lgx-mcp-dev --network lgx-dev-net -p 127.0.0.1:8765:8000 --read-only --tmpfs /tmp \
  --cap-drop ALL --security-opt no-new-privileges \
  -e LGX_DB_HOST=db -e LGX_DB_NAME=legilux -e LGX_DB_USER=lgx_ro \
  -e LGX_DB_PASSWORD_FILE=/run/secrets/pg_ro_pw -e LGX_MCP_TOKEN_FILE=/run/secrets/mcp_token \
  -e LGX_ALLOWED_HOSTS="mcp:8000,localhost:8765,127.0.0.1:8765" \
  -v $PWD/secrets/dev/mcp_token:/run/secrets/mcp_token:ro -v $PWD/secrets/dev/pg_ro_pw:/run/secrets/pg_ro_pw:ro \
  lgx.local/lgx-mcp:dev
```

Clients: `mcp.Client(streamable_http_client(url, http_client=httpx2.AsyncClient(headers={"Authorization":
"Bearer <token>"})))` (2026-07-28 era; `mode="legacy"` for the handshake) or any 1.x `ClientSession` over
`streamablehttp_client(url, headers=…)` (handshake era 2024-11-05 … 2025-11-25, e.g. Open WebUI).

## Endpoints

| Path | Auth | Answer |
|---|---|---|
| `POST /mcp` | `Authorization: Bearer`, Host + Origin allowlists | MCP, stateless, JSON responses, both protocol eras |
| `GET /livez`, `GET /healthz` | none, any Host | 200 while the process runs |
| `GET /readyz` | none, any Host | 200 iff the DB answers and `schema_version` ∈ {1}; else 503. Body: `status, db_ok, schema_ok, release_id, vector_status, reranker_ok, rerank_status, rerank_reason, clock_ok, canned_query_ok, sync_date, error` — degraded optional parts never make it 503 |

Resources: `legilux://legal/{attribution|disclaimer|cc-by-4.0|notice}` (text), templates
`legilux://eli/{+path}` (act card = `get_act`, JSON) and `legilux://eli/{+path}/art/{n}` (article = `get_article`,
JSON; `n` = `num_norm`, e.g. `l121-1`). Prompt: `legal_answer(lang = fr | de | en)` (Appendix D.6).

## Settings (`LGX_*`, `lgx_mcp/settings.py`)

| Variable | Default | Meaning |
|---|---|---|
| `LGX_MCP_PG_DSN` | — | full DSN (dev); else `LGX_DB_HOST` (db), `LGX_DB_PORT` (5432), `LGX_DB_NAME` (legilux), `LGX_DB_USER` (lgx_ro), `LGX_DB_PASSWORD_FILE` / `LGX_DB_PASSWORD` |
| `LGX_MCP_HOST` / `LGX_MCP_PORT` / `LGX_MCP_PATH` | 0.0.0.0 / 8000 / /mcp | listener |
| `LGX_MCP_TOKEN_FILE` / `LGX_MCP_TOKEN` / `LGX_MCP_AUTH` | — / — / on | bearer token (≥ 16 chars, constant-time compare); `off` for dev only |
| `LGX_ALLOWED_HOSTS` | `127.0.0.1:*,localhost:*,[::1]:*,mcp:*` | Host allowlist for `/mcp` (`host:port`, `host:*`, `*`) → 421 |
| `LGX_ALLOWED_ORIGINS` | none | Origin allowlist (absent Origin is fine) → 403 |
| `LGX_RERANK_URL` / `LGX_RERANK` / `LGX_RERANK_K` / `LGX_RERANK_BATCH` | unset / auto / 40 / 32 | TEI `/rerank`; `auto` degrades with a reason, `on` → `BACKEND_UNAVAILABLE`, batches ≤ 32 |
| `LGX_RETRIEVAL_MODE_DEFAULT` | auto | auto \| lexical \| hybrid |
| `LGX_EMBED_PROVIDER` | none | embeddings OFF: `vector_status = disabled:provider_none`, `mode_used = lexical` |
| `LGX_TZ` / `LGX_STALENESS_WARN_DAYS` / `LGX_STALENESS_CRITICAL_DAYS` | Europe/Luxembourg / 30 / 90 | default `as_of` and `W_DATA_STALE` |
| `LGX_MAX_RESULT_TOKENS` | 4000 | text budget (cpu: 2500; article pages shrink with it) |
| `LGX_AUDIT_FILE` / `LGX_AUDIT_QUERY_TEXT` / `LGX_AUDIT_HMAC_KEY_FILE` | stderr / false / token | JSONL audit; question text off by default |
| `LGX_STATEMENT_TIMEOUT_MS` / `LGX_DB_POOL_MAX` / `LGX_DB_TIMEOUT_S` | 5000 / 8 / 3 | read-only pool (every session `default_transaction_read_only=on`) |
| `LGX_RATE_PER_CLIENT_MIN` / `LGX_RATE_GLOBAL_MIN` | 30 / 300 | tool calls per minute (0 = off) |
| `LGX_TRUSTED_PROXIES` | none | IPs / CIDRs (Open WebUI) whose `x-openwebui-user-*` / `x-lgx-user` headers key the rate limit and audit; every other caller is keyed by its peer address |
| `LGX_CONFIG_DIR` | repo `config/` | only `aliases.yaml` (out-of-scope aliases such as RGPD) |

## Retrieval (PLAN §8.3)

1. `lgx_core.citeparse` finds citations in the query; resolved articles become `p_cited_units` (weight 10);
   identifier tokens (`2016/679`, `L. 121-1`, `124-10`, `12bis`) feed the exact branch; EU/case-law/communal/
   Mémorial B/bill vocabulary adds `W_OUT_OF_SCOPE` (the search still runs).
2. One `lgx.search_candidates(...)` call with every filter on every branch (`doc_types`, `act_eli` → act ids,
   `in_force_only`, `as_of`); dense only when `vector_status = ok`.
3. Lexical mode re-orders the fused candidates by cited unit → distinct query terms matched → RRF score
   (native FTS has no IDF). The optional TEI reranker decides after that.
4. One hit per article; citation objects and warnings from `lgx.serving_at` (status and label at `as_of`).

The resolver (`lgx_mcp/resolve.py`) never guesses: two candidates within 0.05 → `ambiguous` (`AMBIGUOUS` in
`get_article` / `get_act`, `status: ambiguous` in `resolve_citation`). The only act of a (type, date) is taken only
when no title is cited or the cited title fits it (score ≥ 0.4, or an inner alias); a truncated dated ELI
(`…/loi/2018/08/01`) lists that date's acts, a year or type prefix is `NOT_FOUND`, only real code / recueil families
(`…/code/travail`) name an act. An article ELI is a citation by itself (`get_article {citation: <article_eli>}`); as
`act_eli` with a different `article`, the requested article of that act is served.

Host clock outside [T, H]: status tools refuse calls without an explicit `as_of` (`AS_OF_OUT_OF_RANGE`, PLAN §2.8);
with `as_of` they answer for that date only (`W_CLOCK_SUSPECT` variant `explicit_as_of`). `about_corpus` always
answers.

## Tests

`tests/unit/test_mcp*.py`, `tests/unit/test_citeparse.py` (offline); `tests/contract/` (server in a thread
against the dev DB, official 2.3.0 client in both eras, G0 smoke set, health/auth/degraded states, stdio, and the
real `mcp==1.27.2` client via `uv run --isolated`). They skip when the dev DB is not reachable
(`LGX_TEST_REQUIRE_DB=1` turns that into a failure). Tests marked `any_release` need no sample content; CI runs them
against the offline fixture release (`scripts/ci_fixture_release.py`).
