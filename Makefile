# Developer entry points (docs/dev/DEVELOPMENT.md). Variables can be overridden: make db-up LGX_DB_PORT=55433
SHELL := /bin/bash
UV ?= uv
LGX_DB_IMAGE ?= lgx.local/lgx-db:dev
LGX_DB_CONTAINER ?= lgx-db-dev
LGX_DB_PORT ?= 55432
LGX_DB_NAME ?= legilux_dev
POSTGRES_PASSWORD ?= lgx-dev-only
EMBED_DIM ?= 1024
# Dev stack (deploy/compose.dev.yml) and host runs. Dev credentials only; the airgap uses Docker secrets.
COMPOSE_DEV ?= docker compose -f deploy/compose.dev.yml
DEV_PROFILES ?=
DEV_PROFILE_FLAGS = $(foreach p,$(DEV_PROFILES),--profile $(p))
# With the `rerank` profile the MCP server uses the TEI reranker (auto = degrade with a reason when it is down)
DEV_RERANK = $(if $(filter rerank,$(DEV_PROFILES)),auto,off)
ALL_DEV_PROFILES = --profile rerank
DEV_WAIT_S ?= 600
LGX_T ?= 2026-10-16
# The chat LLM is one external OpenAI-compatible endpoint (default: the user's Mistral endpoint; no bundled runtime)
LGX_LLM_BASE_URL ?= https://api.mistral.ai/v1
LGX_LLM_MODEL ?= mistral-small-latest
LLM_KEY_FILE ?= secrets/llm_api_key
LGX_DEV_DB ?= lgx-dev-db
LGX_DEV_DB_VOLUME ?= lgx-dev-pgdata
LGX_PG_DSN ?= postgresql://postgres:$(POSTGRES_PASSWORD)@127.0.0.1:$(LGX_DB_PORT)/postgres
LGX_RO_PASSWORD ?= lgx-ro-dev-only
LGX_MCP_PG_DSN ?= postgresql://lgx_ro:$(LGX_RO_PASSWORD)@127.0.0.1:$(LGX_DB_PORT)/legilux
LGX_DEV_MCP_TOKEN ?= lgx-dev-mcp-token
INGEST_FLAGS ?= --offline --include-m2
HOST_MCP_PORT ?= 8001
HOST_UI_PORT ?= 8511
HOST_MCP_URL ?= http://127.0.0.1:$(HOST_MCP_PORT)/mcp
E2E_STAMP := $(shell date -u +%Y%m%dT%H%M%SZ)
E2E_OUT ?= data/reports/e2e/e2e-$(E2E_STAMP).json
# `make e2e E2E_STUB=1`: the scripted chat against scripts/openai_stub.py inside the UI container (no LLM key needed)
E2E_STUB ?=
STUB_PORT ?= 8089
# Evaluation (eval/README.md): lexical runs of every set; EVAL_MODE=rerank needs the rerank service (:8090)
EVAL_DSN ?= $(LGX_MCP_PG_DSN)
EVAL_SETS ?= G0 G1 G2 G3
EVAL_MODE ?= lexical
EVAL_ARGS ?=
# not committed: `make eval` never overwrites the baselines in eval/reports (write those with --out-dir eval/reports)
EVAL_OUT ?= data/reports/eval
# Airgap bundle (docs/ANLEITUNG.md §6-§7): export on the build host, import (root) on the airgap host or as a test
AIRGAP_DATA ?=
AIRGAP_EXPORT_ARGS ?=
AIRGAP_BUNDLE ?=
AIRGAP_IMPORT_ARGS ?=

.PHONY: help sync lint fmt test test-db test-live config-check db-image db-up db-down db-schema db-psql sample \
	dev-db ingest-sample dev-up dev-down dev-logs dev-ps fetch-models rerank-up rerank-down mcp ui e2e \
	egress-check eval embed-plan airgap-export airgap-import

help:
	@grep -E '^[a-z0-9-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-13s %s\n", $$1, $$2}'

sync: ## install all extras + dev tools from uv.lock
	$(UV) sync --locked --all-extras

lint: ## ruff lint + format check
	$(UV) run ruff check .
	$(UV) run ruff format --check .

fmt: ## apply ruff formatting and safe fixes
	$(UV) run ruff format .
	$(UV) run ruff check --fix .

test: ## offline tests (+ the db test when Docker is available)
	$(UV) run pytest -q

test-db: ## only the schema/DB tests
	$(UV) run pytest -q -m db

test-live: ## tests that hit Legilux (polite, few requests)
	$(UV) run pytest -q -m live --run-live

config-check: ## validate config/*.yaml
	$(UV) run lgx config-check

db-image: ## build the lgx-db image (PG18 + pgvector 0.8.7 + unaccented stopwords)
	docker build -t $(LGX_DB_IMAGE) db

db-up: db-image ## start a throwaway dev PostgreSQL on 127.0.0.1:$(LGX_DB_PORT)
	docker run -d --rm --name $(LGX_DB_CONTAINER) -e POSTGRES_PASSWORD=$(POSTGRES_PASSWORD) \
	  -p 127.0.0.1:$(LGX_DB_PORT):5432 $(LGX_DB_IMAGE)
	@for i in $$(seq 1 60); do docker exec $(LGX_DB_CONTAINER) pg_isready -h 127.0.0.1 -U postgres -q && break; sleep 1; done
	@docker exec $(LGX_DB_CONTAINER) pg_isready -h 127.0.0.1 -U postgres  # TCP: skips the init-time socket-only server

db-down: ## stop and remove the dev PostgreSQL
	-docker rm -f -v $(LGX_DB_CONTAINER)  # -v: drop the anonymous /var/lib/postgresql volume (≈ 200 MB per run)

db-schema: ## (re)create $(LGX_DB_NAME) and apply db/schema/*.sql
	scripts/db_apply.sh --container $(LGX_DB_CONTAINER) --dbname $(LGX_DB_NAME) --embed-dim $(EMBED_DIM) --recreate

db-psql: ## psql shell into the dev database
	docker exec -it $(LGX_DB_CONTAINER) psql -U postgres -d $(LGX_DB_NAME)

sample: ## lgx ingest --sample (full metadata pulls + sample files; never the full crawl)
	$(UV) run lgx ingest --sample

dev-db: ## ensure the persistent host-side build DB lgx-dev-db runs (127.0.0.1:55432)
	@if docker ps --format '{{.Names}}' | grep -qx '$(LGX_DEV_DB)'; then echo "$(LGX_DEV_DB): running"; \
	elif docker ps -a --format '{{.Names}}' | grep -qx '$(LGX_DEV_DB)'; then docker start $(LGX_DEV_DB); \
	else docker build -q -t $(LGX_DB_IMAGE) db && docker volume create $(LGX_DEV_DB_VOLUME) >/dev/null && \
	  docker run -d --name $(LGX_DEV_DB) --restart unless-stopped -e POSTGRES_PASSWORD=$(POSTGRES_PASSWORD) \
	    -v $(LGX_DEV_DB_VOLUME):/var/lib/postgresql -p 127.0.0.1:$(LGX_DB_PORT):5432 $(LGX_DB_IMAGE); fi
	@for i in $$(seq 1 60); do docker exec $(LGX_DEV_DB) pg_isready -h 127.0.0.1 -U postgres -q && break; sleep 1; done
	@docker exec $(LGX_DEV_DB) pg_isready -h 127.0.0.1 -U postgres

ingest-sample: dev-db ## offline sample ingest (+ m2 PDF codes) + gates + package (data/bundles/) + restore into legilux
	LGX_PG_DSN=$(LGX_PG_DSN) $(UV) run lgx --t $(LGX_T) --opt keep_prev=0 ingest --sample $(INGEST_FLAGS) \
	  --package --container $(LGX_DEV_DB) --restore-to legilux
	@printf '%s\n' "ALTER ROLE lgx_ro PASSWORD :'pw';" | docker exec -i $(LGX_DEV_DB) psql -U postgres -q \
	  -v ON_ERROR_STOP=1 -v pw='$(LGX_RO_PASSWORD)' && echo "lgx_ro password set (dev)"

dev-up: ## build + start db, sample restore, mcp, UI (+ DEV_PROFILES=rerank: TEI); waits until healthy
	@test -f data/bundles/data-$(LGX_T)-sample.dump || { echo "no sample bundle: run 'make ingest-sample' first"; exit 1; }
	LGX_LLM_BASE_URL=$(LGX_LLM_BASE_URL) LGX_LLM_MODEL=$(LGX_LLM_MODEL) \
	  $(if $(wildcard $(LLM_KEY_FILE)),LGX_LLM_KEY_FILE=../$(LLM_KEY_FILE)) \
	  LGX_DEV_BUNDLE=data-$(LGX_T)-sample.dump LGX_RERANK=$${LGX_RERANK:-$(DEV_RERANK)} \
	  $(if $(LGX_DEV_CA_BUNDLE),LGX_DEV_CA_BUNDLE=$(LGX_DEV_CA_BUNDLE)) \
	  $(COMPOSE_DEV) $(DEV_PROFILE_FLAGS) up -d --build --wait --wait-timeout $(DEV_WAIT_S)
	@$(COMPOSE_DEV) $(DEV_PROFILE_FLAGS) ps --format 'table {{.Service}}\t{{.Status}}\t{{.Ports}}'
	@echo "UI  http://127.0.0.1:$${LGX_DEV_UI_PORT:-8501}   MCP http://127.0.0.1:$${LGX_DEV_MCP_PORT:-8000}/mcp (/readyz)"

dev-down: ## stop the dev stack incl. the rerank profile (volumes are kept; DEV_DOWN_FLAGS=-v drops the db volume)
	$(COMPOSE_DEV) $(ALL_DEV_PROFILES) down $(DEV_DOWN_FLAGS)

dev-logs: ## follow the dev stack logs (SERVICE=mcp|lgx-streamlit|rerank|db|db-load to narrow)
	$(COMPOSE_DEV) $(DEV_PROFILE_FLAGS) logs -f --tail=200 $(SERVICE)

dev-ps: ## dev stack status
	$(COMPOSE_DEV) $(DEV_PROFILE_FLAGS) ps

fetch-models: ## download the pinned reranker into data/models/hf (connected host; FETCH_MODELS_ARGS=--verify-only offline)
	scripts/fetch_models.sh $(FETCH_MODELS_ARGS)

rerank-up: ## start the TEI reranker (profile rerank; ready in 1.5-6 min on 4 vCPU) + switch the dev mcp to LGX_RERANK=auto
	scripts/fetch_models.sh --verify-only
	LGX_DEV_BUNDLE=data-$(LGX_T)-sample.dump LGX_RERANK=$${LGX_RERANK:-auto} \
	  $(COMPOSE_DEV) --profile rerank up -d --no-build --wait --wait-timeout $(DEV_WAIT_S) rerank mcp

rerank-down: ## stop the reranker and switch the dev mcp back to LGX_RERANK=off
	$(COMPOSE_DEV) --profile rerank rm -sf rerank
	LGX_DEV_BUNDLE=data-$(LGX_T)-sample.dump LGX_RERANK=off $(COMPOSE_DEV) up -d --no-build --no-deps --wait mcp

mcp: dev-db ## run lgx-mcp on the host against lgx-dev-db: http://127.0.0.1:8001/mcp (HOST_MCP_PORT)
	LGX_MCP_PG_DSN=$(LGX_MCP_PG_DSN) LGX_MCP_TOKEN=$(LGX_DEV_MCP_TOKEN) \
	  $(UV) run python -m lgx_mcp --host 127.0.0.1 --port $(HOST_MCP_PORT)

ui: ## run the Streamlit UI on the host: http://127.0.0.1:8511 (needs 'make mcp'; LGX_LLM_BASE_URL, LLM_KEY_FILE)
	cd ui && LGX_MCP_URL=$(HOST_MCP_URL) LGX_MCP_TOKEN=$(LGX_DEV_MCP_TOKEN) OPENAI_BASE_URL=$(LGX_LLM_BASE_URL) \
	  $(if $(wildcard $(LLM_KEY_FILE)),OPENAI_API_KEY_FILE=../$(LLM_KEY_FILE)) LGX_LLM_MODEL=$(LGX_LLM_MODEL) \
	  $(UV) run python -m lgx_ui.launch --server.address 127.0.0.1 --server.port $(HOST_UI_PORT)

e2e: ## scripted FR/DE chat through the running dev stack (UI container -> LLM endpoint + MCP; E2E_STUB=1: stub LLM)
	@mkdir -p $(dir $(E2E_OUT))
	$(if $(E2E_STUB),$(COMPOSE_DEV) exec -d lgx-streamlit python -c "$$(cat scripts/openai_stub.py)" --port $(STUB_PORT); sleep 2)
	$(COMPOSE_DEV) exec -T $(if $(E2E_STUB),-e OPENAI_BASE_URL=http://127.0.0.1:$(STUB_PORT)/v1 -e LGX_LLM_MODEL=lgx-stub) \
	  lgx-streamlit python - $(E2E_ARGS) < scripts/e2e_chat.py > $(E2E_OUT)
	@echo "e2e report: $(E2E_OUT)"

egress-check: ## airgap egress check of the sample bundle for LGX_T (internal network, DNS sentinel, DOCKER-USER counters, browser requests)
	@test -f data/bundles/data-$(LGX_T)-sample.dump || { echo "no sample bundle data-$(LGX_T)-sample.dump: pass LGX_T=<T> (sudo drops it) or run 'make ingest-sample'"; exit 1; }
	LGX_DEV_BUNDLE=data-$(LGX_T)-sample.dump scripts/egress_check.sh $(EGRESS_ARGS)

embed-plan: ## embedding plan for T (chunks, requests, tokens, duration under the throttle); never a request
	$(UV) run lgx --t $(LGX_T) embed --dry-run

eval: ## eval harness: EVAL_SETS (G0 G1 G2 G3) in EVAL_MODE (lexical | rerank) against EVAL_DSN -> $(EVAL_OUT)
	@for s in $(EVAL_SETS); do \
	  echo "== $$s ($(EVAL_MODE))"; \
	  $(UV) run python -m eval.harness run --set $$s --mode $(EVAL_MODE) --dsn '$(EVAL_DSN)' --out-dir $(EVAL_OUT) \
	    --tag $$s-$(EVAL_MODE)-$$(date -u +%Y%m%dT%H%M%SZ) $(EVAL_ARGS) || exit 1; \
	done

airgap-export: ## airgap bundle -> data/airgap/: images + data bundle + compose + import script (AIRGAP_DATA, AIRGAP_EXPORT_ARGS)
	scripts/airgap_export.sh $(if $(AIRGAP_DATA),--data $(AIRGAP_DATA)) \
	  $(if $(LGX_DEV_CA_BUNDLE),--ca-bundle $(LGX_DEV_CA_BUNDLE)) $(AIRGAP_EXPORT_ARGS)

airgap-import: ## install a bundle as root: AIRGAP_BUNDLE=<dir> [AIRGAP_IMPORT_ARGS="--home DIR --project NAME ..."]
	@test -n "$(AIRGAP_BUNDLE)" || { echo "AIRGAP_BUNDLE=<directory written by make airgap-export> is required"; exit 2; }
	scripts/airgap_import.sh install $(AIRGAP_BUNDLE) $(AIRGAP_IMPORT_ARGS)
