# LegiLux-Full-Local-MCP-Clone

An airgap-deployable, Docker-packaged local clone of the **current and relevant** part of
[Legilux](https://legilux.public.lu), the Luxembourg legal portal. A local LLM can query it
through an **MCP server** to answer legal questions in detail, with article-level citations.

- **Plan:** [`docs/PLAN.md`](docs/PLAN.md), with appendices in [`docs/plan/`](docs/plan/)
- **Short summary (German):** [`docs/PLAN_KURZFASSUNG.md`](docs/PLAN_KURZFASSUNG.md)
- **Research notes and tested SPARQL queries:** [`docs/research/`](docs/research/)
- **Developer quick start:** [`docs/dev/DEVELOPMENT.md`](docs/dev/DEVELOPMENT.md) ·
  stage contracts: [`docs/dev/INTERFACES.md`](docs/dev/INTERFACES.md)

## Quick start (dev)

Linux host with Docker and [uv](https://docs.astral.sh/uv/); everything listens on `127.0.0.1` only.

```sh
uv sync --all-extras          # Python 3.13 deps from uv.lock (+ dev tools)
make ingest-sample            # sample DB + bundle data/bundles/data-2026-10-16-sample.dump (offline, from data/ caches;
                              #   fresh checkout: make ingest-sample INGEST_FLAGS=  → polite sample fetch, never the full crawl)
make dev-up                   # db + sample restore + MCP + Streamlit + Ollama (cpu), waits until healthy
make ollama-pull              # once: qwen3:4b-instruct-2507-q4_K_M (2.5 GB) into the Ollama volume; MODEL=<tag> for another
open http://127.0.0.1:8501    # test UI (Chat, Recherche directe, Évaluation, Corpus); MCP: http://127.0.0.1:8000/mcp
make e2e                      # optional: 5 scripted FR/DE questions through the UI code path (JSON in data/reports/e2e/)
make dev-down                 # stop (volumes kept); make dev-logs SERVICE=mcp to follow one service
```

Another OpenAI-compatible endpoint (vLLM, a Mistral on-prem server, …): pick « Autre endpoint (saisie libre) » in
the sidebar and type its base URL and model (no API key is sent to a typed URL), or start the stack with it as a
preset that also gets the key:
`LGX_MISTRAL_ONPREM_URL=https://mistral.example.internal/v1 LGX_MISTRAL_ONPREM_MODEL=<model> LGX_DEV_LLM_API_KEY=<key> make dev-up`
(preset « Mistral on-prem »), or make it the default with `LGX_LLM_BASE_URL=https://…/v1 MODEL=<model> make dev-up`.
Embeddings stay off (`provider: none`, lexical search). On 4 vCPUs an answer takes 4–10 minutes; results, timings and
known problems: [`docs/dev/e2e-2026-10-17.md`](docs/dev/e2e-2026-10-17.md). Airgap egress check: `make egress-check`.
Host runs without Docker for the UI/MCP: `make mcp` (:8001) and `make ui` (:8511, `HOST_LLM_BASE_URL=…`).

Checks: `make lint` and `make test` (the db tests start their own throwaway PostgreSQL container).

Data source: Legilux, © Ministère d'État – Service central de législation, licensed under
CC BY 4.0. This is an unofficial copy: only the published Journal officiel is authoritative
(loi du 23 décembre 2016, art. 5).
