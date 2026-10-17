# Ollama (dev host and cpu profile)

- Image: `ollama/ollama:0.40.5@sha256:5d3a2f25…` (`deploy/images.lock`), pulled through `mirror.gcr.io`
  (index digest re-verified 2026-10-16 with `docker buildx imagetools inspect`).
- Runtime service: `ollama` in `deploy/compose.dev.yml` (profile `cpu`; alias `llm` on the internal `backend`
  network, `OLLAMA_NO_CLOUD=1`, `OLLAMA_CONTEXT_LENGTH=16384`, `OLLAMA_KEEP_ALIVE=-1`). It never pulls.
- Models on the connected dev host (one-shot, internet via the `dev-egress` network):

  ```sh
  docker compose -f deploy/compose.dev.yml --profile ollama-pull run --rm ollama-pull            # ministral-3:8b
  LGX_OLLAMA_PULL=gemma4:26b-a4b docker compose -f deploy/compose.dev.yml --profile ollama-pull run --rm ollama-pull
  LGX_OLLAMA_ALIAS=legal-llm docker compose -f deploy/compose.dev.yml --profile ollama-pull run --rm ollama-pull
  docker compose -f deploy/compose.dev.yml --profile cpu up -d
  ```

  The UI's default model is `LGX_LLM_MODEL` (dev default `ministral-3:8b`, 6.0 GB; the cpu default
  `gemma4:26b-a4b` is 18.7 GB). Tool-calling support of a model on Ollama `/v1` is [U]: use the sidebar's
  "Tester l'appel d'outils" probe; without tool calls the UI falls back to retrieve-first.
- `Modelfile.legilux-cpu`: optional alias with `num_ctx 16384` / `temperature 0.2`, also the template for the
  documented GGUF + Modelfile fallback of the airgap install (PLAN §9.2).
