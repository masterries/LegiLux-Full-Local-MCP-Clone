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
DEV_PROFILES ?= cpu
DEV_PROFILE_FLAGS = $(foreach p,$(DEV_PROFILES),--profile $(p))
DEV_WAIT_S ?= 600
LGX_T ?= 2026-10-16
MODEL ?= qwen3:4b-instruct-2507-q4_K_M
LGX_DEV_DB ?= lgx-dev-db
LGX_DEV_DB_VOLUME ?= lgx-dev-pgdata
LGX_PG_DSN ?= postgresql://postgres:$(POSTGRES_PASSWORD)@127.0.0.1:$(LGX_DB_PORT)/postgres
LGX_RO_PASSWORD ?= lgx-ro-dev-only
LGX_MCP_PG_DSN ?= postgresql://lgx_ro:$(LGX_RO_PASSWORD)@127.0.0.1:$(LGX_DB_PORT)/legilux
LGX_DEV_MCP_TOKEN ?= lgx-dev-mcp-token
INGEST_FLAGS ?= --offline
HOST_MCP_PORT ?= 8001
HOST_UI_PORT ?= 8511
HOST_MCP_URL ?= http://127.0.0.1:$(HOST_MCP_PORT)/mcp
HOST_LLM_BASE_URL ?= http://127.0.0.1:11434/v1
E2E_STAMP := $(shell date -u +%Y%m%dT%H%M%SZ)
E2E_OUT ?= data/reports/e2e/e2e-$(E2E_STAMP).json

.PHONY: help sync lint fmt test test-db test-live config-check db-image db-up db-down db-schema db-psql sample \
	dev-db ingest-sample dev-up dev-down dev-logs dev-ps ollama-pull mcp ui e2e egress-check

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
	-docker rm -f $(LGX_DB_CONTAINER)

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

ingest-sample: dev-db ## offline sample ingest + gates + package (bundle in data/bundles/) + restore into legilux
	LGX_PG_DSN=$(LGX_PG_DSN) $(UV) run lgx --t $(LGX_T) --opt keep_prev=0 ingest --sample $(INGEST_FLAGS) \
	  --package --container $(LGX_DEV_DB) --restore-to legilux
	@printf '%s\n' "ALTER ROLE lgx_ro PASSWORD :'pw';" | docker exec -i $(LGX_DEV_DB) psql -U postgres -q \
	  -v ON_ERROR_STOP=1 -v pw='$(LGX_RO_PASSWORD)' && echo "lgx_ro password set (dev)"

dev-up: ## build + start db, sample restore, mcp, UI, Ollama (DEV_PROFILES=cpu); waits until healthy
	@test -f data/bundles/data-$(LGX_T)-sample.dump || { echo "no sample bundle: run 'make ingest-sample' first"; exit 1; }
	LGX_LLM_MODEL=$(MODEL) LGX_DEV_BUNDLE=data-$(LGX_T)-sample.dump \
	  $(if $(LGX_DEV_CA_BUNDLE),LGX_DEV_CA_BUNDLE=$(LGX_DEV_CA_BUNDLE)) \
	  $(COMPOSE_DEV) $(DEV_PROFILE_FLAGS) up -d --build --wait --wait-timeout $(DEV_WAIT_S)
	@$(COMPOSE_DEV) $(DEV_PROFILE_FLAGS) ps --format 'table {{.Service}}\t{{.Status}}\t{{.Ports}}'
	@echo "UI  http://127.0.0.1:$${LGX_DEV_UI_PORT:-8501}   MCP http://127.0.0.1:$${LGX_DEV_MCP_PORT:-8000}/mcp (/readyz)"

dev-down: ## stop the dev stack (volumes are kept; DEV_DOWN_FLAGS=-v also drops the db + model volumes)
	$(COMPOSE_DEV) --profile cpu --profile ollama-pull --profile cloudllm down $(DEV_DOWN_FLAGS)

dev-logs: ## follow the dev stack logs (SERVICE=mcp|lgx-streamlit|ollama|db|db-load to narrow)
	$(COMPOSE_DEV) $(DEV_PROFILE_FLAGS) logs -f --tail=200 $(SERVICE)

dev-ps: ## dev stack status
	$(COMPOSE_DEV) $(DEV_PROFILE_FLAGS) ps

ollama-pull: ## pull MODEL=<ollama tag> (default qwen3:4b-instruct-2507-q4_K_M) into the dev Ollama volume
	LGX_OLLAMA_PULL='$(MODEL)' $(if $(LGX_DEV_CA_BUNDLE),LGX_DEV_CA_BUNDLE=$(LGX_DEV_CA_BUNDLE)) \
	  $(COMPOSE_DEV) --profile ollama-pull run --rm ollama-pull

mcp: dev-db ## run lgx-mcp on the host against lgx-dev-db: http://127.0.0.1:8001/mcp (HOST_MCP_PORT)
	LGX_MCP_PG_DSN=$(LGX_MCP_PG_DSN) LGX_MCP_TOKEN=$(LGX_DEV_MCP_TOKEN) LGX_EMBED_PROVIDER=none \
	  $(UV) run python -m lgx_mcp --host 127.0.0.1 --port $(HOST_MCP_PORT)

ui: ## run the Streamlit UI on the host: http://127.0.0.1:8511 (needs 'make mcp' + HOST_LLM_BASE_URL)
	cd ui && LGX_MCP_URL=$(HOST_MCP_URL) LGX_MCP_TOKEN=$(LGX_DEV_MCP_TOKEN) OPENAI_BASE_URL=$(HOST_LLM_BASE_URL) \
	  LGX_LLM_MODEL=$(MODEL) $(UV) run python -m lgx_ui.launch --server.address 127.0.0.1 --server.port $(HOST_UI_PORT)

e2e: ## scripted FR/DE chat questions through the running dev stack (UI container -> Ollama + MCP), JSON report
	@mkdir -p $(dir $(E2E_OUT))
	$(COMPOSE_DEV) exec -T lgx-streamlit python - $(E2E_ARGS) < scripts/e2e_chat.py > $(E2E_OUT)
	@echo "e2e report: $(E2E_OUT)"

egress-check: ## airgap egress check (internal network, DNS sentinel, DOCKER-USER counters, browser requests)
	scripts/egress_check.sh $(EGRESS_ARGS)
