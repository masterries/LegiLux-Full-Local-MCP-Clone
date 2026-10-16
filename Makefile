# Developer entry points (docs/dev/DEVELOPMENT.md). Variables can be overridden: make db-up LGX_DB_PORT=55433
SHELL := /bin/bash
UV ?= uv
LGX_DB_IMAGE ?= lgx.local/lgx-db:dev
LGX_DB_CONTAINER ?= lgx-db-dev
LGX_DB_PORT ?= 55432
LGX_DB_NAME ?= legilux_dev
POSTGRES_PASSWORD ?= lgx-dev-only
EMBED_DIM ?= 1024

.PHONY: help sync lint fmt test test-db test-live config-check db-image db-up db-down db-schema db-psql sample mcp ui

help:
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-13s %s\n", $$1, $$2}'

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

mcp: ## run the MCP server (M1 MCP stage)
	@echo "lgx-mcp is implemented in the M1 MCP stage (PLAN §8)"; exit 3

ui: ## run the Streamlit tester UI (M1b)
	@echo "lgx-streamlit is implemented in M1b (PLAN §9.3)"; exit 3
