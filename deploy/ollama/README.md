# Ollama (dev host and cpu profile)

- Image: `ollama/ollama:0.40.5@sha256:5d3a2f25…` (`deploy/images.lock`), pulled through `mirror.gcr.io`
  (index digest re-verified 2026-10-16 with `docker buildx imagetools inspect`).
- Runtime service: `ollama` in `deploy/compose.dev.yml` (profile `cpu`; alias `llm` on the internal `backend`
  network, `OLLAMA_NO_CLOUD=1`, `OLLAMA_CONTEXT_LENGTH=16384`, `OLLAMA_KEEP_ALIVE=-1`). It never pulls.
- Models on the connected dev host (one-shot, internet via the `dev-egress` network):

  ```sh
  make ollama-pull                                   # = the next line; MODEL=<tag> for another model
  docker compose -f deploy/compose.dev.yml --profile ollama-pull run --rm ollama-pull   # qwen3:4b-instruct-2507-q4_K_M
  LGX_OLLAMA_PULL=ministral-3:8b docker compose -f deploy/compose.dev.yml --profile ollama-pull run --rm ollama-pull
  LGX_OLLAMA_ALIAS=legal-llm docker compose -f deploy/compose.dev.yml --profile ollama-pull run --rm ollama-pull
  docker compose -f deploy/compose.dev.yml --profile cpu up -d
  ```

  The UI's default model is `LGX_LLM_MODEL` (dev default `qwen3:4b-instruct-2507-q4_K_M`, 2.5 GB, Apache-2.0,
  non-thinking; tool calling verified on 4 vCPUs in `docs/dev/e2e-2026-10-17.md`; `ministral-3:8b` 6.0 GB and
  `ministral-3:3b` 3.0 GB are untested alternatives; the cpu default of the airgap plan `gemma4:26b-a4b` is 18.7 GB).
  Tool-calling support of any other model on Ollama `/v1` is [U]: use the sidebar's "Tester l'appel d'outils" probe;
  without tool calls the UI falls back to retrieve-first.
- Behind a TLS-intercepting proxy set `LGX_DEV_CA_BUNDLE=/path/ca-bundle.crt` (mounted into the pull job as
  `SSL_CERT_FILE`; the same variable feeds the image builds of `make dev-up` as the `ca_bundle` build secret).
- `Modelfile.legilux-cpu`: optional alias with `num_ctx 16384` / `temperature 0.2`, also the template for the
  documented GGUF + Modelfile fallback of the airgap install (PLAN §9.2).
