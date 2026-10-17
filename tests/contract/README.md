# MCP contract tests (Appendix D.3, PLAN §13 M1)

The real server runs in a thread (uvicorn) against the persistent dev database (`LGX_TEST_MCP_PG_DSN` /
`LGX_MCP_PG_DSN`, default `postgresql://lgx_ro:lgx-ro-dev-only@127.0.0.1:55432/legilux`, docs/dev/INTERFACES.md
§6.1); every test skips when that database is not reachable or does not hold the 2026-10-16 sample release. The
server clock is pinned to T (2026-10-16) so `as_of` defaults are deterministic.

- `test_mcp_contract.py` — every tool through the official `mcp==2.3.0` `Client` over Streamable HTTP in both
  eras (`mode="auto"` → 2026-07-28, `mode="legacy"` → 2025-11-25 handshake), results validated against the output
  schemas; M1 acceptance (`get_article("art. L. 121-1 C. trav.")`); the G0 smoke set (`eval/gold/G0/smoke.json`, 20 items, shared with the Streamlit Eval tab since M2);
  one test per search filter; retrieval modes without vectors; error codes; resources and prompts; audit.
- `test_mcp_ops.py` — `/livez` `/healthz` `/readyz` without token and with any Host; 401/421/403; raw JSON-RPC
  initialise + call with the old protocol versions 2024-11-05 … 2025-11-25 (no 1.x dependency needed); DB down
  (readyz 503, `BACKEND_UNAVAILABLE`), reranker down (`auto` degrades, `on` fails), reranker batches ≤ 32, suspect
  clock, rate limit, stdio entry point, `--check`.
- `test_mcp_m2.py` — M2 serving quality: the notice-period question finds Code du travail art. L. 124-3, « Constitution
  liberté d'expression » ranks Constitution art. 23 first, a German question reaches the notice articles, the
  `rank_cd` knob reproduces the M1 ordering, an EU regulation citation is `OUT_OF_SCOPE` (skip on a release built
  before search API 2: run with `LGX_TEST_MCP_PG_DSN=postgresql://lgx_ro:lgx-ro-dev-only@127.0.0.1:55432/legilux_eval_m2`
  until the dev runtime is rebuilt); and the **real TEI reranker** (`LGX_TEST_RERANK_URL`, default
  `http://127.0.0.1:8090`, skip when absent): 40 candidates in batches 32 + 8, a reranked search with per-batch
  latency, a real-container timeout falling back to lexical with the reason.
- `test_mcp_legacy.py` (`-m legacy_client`) — the real `mcp==1.27.2` `ClientSession` (Open WebUI 0.12.3's SDK)
  in an isolated env, never in the project lock:
  `uv run --isolated --no-project --with mcp==1.27.2 python tests/contract/legacy_client_check.py URL TOKEN`.
  The test runs it with `UV_OFFLINE=1` (uv cache only) and skips when the package is not cached;
  `LGX_RUN_LEGACY_CLIENT=1` lets uv download it once.

```sh
uv run pytest -q tests/contract                 # all of the above
uv run pytest -q tests/contract -m legacy_client
```
