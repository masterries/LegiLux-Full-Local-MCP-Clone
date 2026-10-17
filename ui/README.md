# lgx-streamlit — tester UI (PLAN §9.3)

Streamlit app that drives any OpenAI-compatible Chat Completions model through the MCP tools of `lgx-mcp`,
plus a no-LLM Direct-search tab, the G0 smoke set and a corpus card. It is read-only, has no database
credentials and no server-side chat history, and talks only to the MCP server (`LGX_MCP_URL`, bearer
token) and the configured LLM endpoint. Embeddings are off: the hybrid toggle stays greyed out while
`vector_status != ok`.

| Path | Role |
|---|---|
| `app.py` | entry: page config, Art. 5 banner (red cloud banner with `LGX_ALLOW_CLOUD_LLM=1`), sidebar, `st.navigation` |
| `pages/1_Chat.py` | tool loop; answer + sources (ELI, version, `text_basis` badge, warnings), V1–V3, tool trace, LLM/MCP latency |
| `pages/2_Direct_search.py` | one form per MCP tool, generated from its `inputSchema` (tools/list is dynamic); rendered + raw JSON |
| `pages/3_Eval.py` | G0 (deterministic tool calls); results as JSON download |
| `pages/4_Corpus.py` | `about_corpus` + `/readyz` |
| `lgx_ui/mcp_client.py` | official `mcp==2.3.0` client, Streamable HTTP, bearer, `trust_env=False`; `/readyz` probe |
| `lgx_ui/llm.py` | official `openai==3.24.0` client (`OPENAI_BASE_URL`); detects endpoints that reject `tools` |
| `lgx_ui/tools_bridge.py` | MCP → OpenAI tools, sidebar hard overrides (schema-scoped), validation, loop, retrieve-first, probe |
| `lgx_ui/render.py` | citation cards, warnings, envelope, latency/vector lines, Streamlit views |
| `lgx_ui/verify_light.py` | turn-local V1–V3 (until `lgx_core.verify`, M4) |
| `lgx_ui/evalg0.py`, `lgx_ui/gold/g0_dev_sample.json` | G0 runner; dev gold set for the 2026-10-16 sample (used when `LGX_EVAL_DIR` has none) |
| `lgx_ui/launch.py` | `python -m lgx_ui.launch`: offline env defaults + disables `net_util.get_external_ip` |
| `.streamlit/config.toml` | offline config (baked into the image; `STREAMLIT_*` variables override per host) |
| `Dockerfile` | image `lgx.local/lgx-streamlit` (build from the repo root) |

## Run

```sh
# Host (dev): needs a running lgx-mcp; run from ui/ so .streamlit/config.toml applies
cd ui && LGX_MCP_URL=http://127.0.0.1:8000/mcp LGX_MCP_TOKEN=<token> \
  OPENAI_BASE_URL=http://127.0.0.1:11434/v1 LGX_LLM_MODEL=ministral-3:8b \
  uv run python -m lgx_ui.launch --server.address 127.0.0.1          # http://127.0.0.1:8501

# Dev stack (db + sample restore + mcp + UI, ports on 127.0.0.1 only), from the repo root
docker compose -f deploy/compose.dev.yml up -d --build
docker compose -f deploy/compose.dev.yml --profile cpu up -d          # + Ollama (see deploy/ollama/README.md)

# Image only
docker build -f ui/Dockerfile -t lgx.local/lgx-streamlit:dev .       # --secret id=ca_bundle,src=… behind a TLS proxy
```

Environment (Appendix C names): `LGX_MCP_URL`, `LGX_MCP_TOKEN_FILE` (or `LGX_MCP_TOKEN` in dev),
`OPENAI_BASE_URL`, `OPENAI_API_KEY_FILE` (or `OPENAI_API_KEY`), `LGX_LLM_MODEL`, `LGX_LLM_PRESETS`
(`ollama,vllm,mistral-onprem`; `cloud` only with `LGX_ALLOW_CLOUD_LLM=1`), `LGX_MAX_TOOL_ITERATIONS` (5),
`LGX_MCP_TIMEOUT_S` (20), `LGX_LLM_TIMEOUT_S` (120), `LGX_TOOL_RESULT_TOKENS` (4000), `LGX_TURN_BUDGET_S`
(600), `LGX_EVAL_DIR`, `LGX_HOSTNAME`. The configured API key is only sent to the configured/preset base URLs,
never to a URL typed into the sidebar.

## Tests

```sh
uv run pytest -q tests/unit/test_ui_*.py    # bridge, MCP client (in-process + ASGI HTTP), render, AppTest, sentinel, compose
```

`openai` and `mcp` use `httpx2`, which `respx` cannot intercept: the fake OpenAI server is an
`httpx2.MockTransport`; `respx` covers the plain-httpx `/readyz` probe.
