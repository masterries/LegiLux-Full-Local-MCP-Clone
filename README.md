# LegiLux-Full-Local-MCP-Clone

An airgap-deployable, Docker-packaged local clone of the **current and relevant** part of
[Legilux](https://legilux.public.lu), the Luxembourg legal portal. An LLM — by default the user's Mistral endpoint
(the Mistral API, on-prem in the airgap) — queries it through an **MCP server** to answer legal questions in detail,
with article-level citations. No LLM runtime is bundled; embeddings (Mistral embeddings API, throttled) are optional
and off by default.

- **Plan:** [`docs/PLAN.md`](docs/PLAN.md), with appendices in [`docs/plan/`](docs/plan/)
- **Short summary (German):** [`docs/PLAN_KURZFASSUNG.md`](docs/PLAN_KURZFASSUNG.md)
- **Step-by-step guide (German, for the operator):** [`docs/ANLEITUNG.md`](docs/ANLEITUNG.md) — build host, Mistral
  SaaS / on-prem, sample and full build, embeddings, airgap package (`make airgap-export`), installation, updates, FAQ
- **Research notes and tested SPARQL queries:** [`docs/research/`](docs/research/)
- **Developer quick start:** [`docs/dev/DEVELOPMENT.md`](docs/dev/DEVELOPMENT.md) ·
  stage contracts: [`docs/dev/INTERFACES.md`](docs/dev/INTERFACES.md)

**Status (2026-10-17): M0, M1, M1b and M2 done** on the dev sample (embeddings off, never a full crawl). M2 added the
PDF-only codes (route, sécurité sociale, communal, éducation nationale; page citations, member acts), code-recueil
dedup and routing (`superseded_by_code` members are served from the code text), an IDF-weighted lexical ranker and the
optional TEI reranker. Sample release: 19,682 searchable chunks; G2 retrieval R@10 0.821 / MRR 0.643 lexical (218
questions), mandatory warnings 100 % (G3) — [`eval/reports/m2-baseline.md`](eval/reports/m2-baseline.md),
[`eval/reports/m2-acceptance.md`](eval/reports/m2-acceptance.md). Full crawl: operator step `lgx ingest --full`
(plan with `--dry-run`, [DEVELOPMENT.md](docs/dev/DEVELOPMENT.md#full-ingest-operator-step-never-in-development)).

## Quick start (dev)

Linux host with Docker and [uv](https://docs.astral.sh/uv/); everything listens on `127.0.0.1` only.

```sh
uv sync --all-extras          # Python 3.13 deps from uv.lock (+ dev tools)
make ingest-sample            # sample DB + bundle data/bundles/data-2026-10-16-sample.dump (offline, from data/ caches;
                              #   fresh checkout: docs/ANLEITUNG.md §3.1 → polite sample fetch, never the full crawl)
make dev-up                   # db + sample restore + MCP + Streamlit, waits until healthy
                              #   DEV_PROFILES=rerank: + the optional TEI reranker (OFF by default; `make fetch-models` once, 2.2 GB)
open http://127.0.0.1:8501    # test UI (Chat, Recherche directe, Évaluation, Corpus); MCP: http://127.0.0.1:8000/mcp
make e2e                      # optional: 5 scripted FR/DE questions through the UI code path (JSON in data/reports/e2e/);
                              #   E2E_STUB=1: against the keyless OpenAI-compatible stub scripts/openai_stub.py
make embed-plan               # optional: what `lgx embed` would send (chunks, requests, duration under the throttle)
make eval                     # optional: gold sets G0–G3, lexical (EVAL_MODE=rerank with a --subset, see eval/README.md)
make dev-down                 # stop (volumes kept); make dev-logs SERVICE=mcp to follow one service
make airgap-export            # airgap package -> data/airgap/<dir>; on the airgap host: sudo ./scripts/airgap_import.sh install .
```

The chat LLM is one external OpenAI-compatible endpoint: by default Mistral (`https://api.mistral.ai/v1`, model
`mistral-small-latest`; the key in the file `secrets/llm_api_key`, owned by uid 10001 with mode 0400 so that the
non-root UI container can read it — `sudo install -o 10001 -g 10001 -m 0400 /dev/stdin secrets/llm_api_key`, see
[`docs/ANLEITUNG.md`](docs/ANLEITUNG.md) §2.2 — picked up by `make dev-up`). Another endpoint
(the on-prem Mistral domain, any OpenAI-compatible server): `LGX_LLM_BASE_URL=https://…/v1 LGX_LLM_MODEL=<model> make
dev-up`, or type base URL and model into the sidebar (no API key is sent to a typed URL). Embeddings stay off
(`provider: none`, lexical search); `config/embedding.yaml` + `lgx embed` once a Mistral key and URL exist. Results
and known problems of the first end-to-end run: [`docs/dev/e2e-2026-10-17.md`](docs/dev/e2e-2026-10-17.md). Airgap
egress check: `make egress-check`. The TEI reranker (bge-reranker-v2-m3, the only non-Mistral model) is optional and
OFF by default everywhere (`LGX_RERANK=off`, compose profile `rerank` not enabled). Host runs without Docker for the
UI/MCP: `make mcp` (:8001) and `make ui` (:8511).

Checks: `make lint` and `make test` (the db tests start their own throwaway PostgreSQL container).

Data source: Legilux, © Ministère d'État – Service central de législation, licensed under
CC BY 4.0. This is an unofficial copy: only the published Journal officiel is authoritative
(loi du 23 décembre 2016, art. 5).
