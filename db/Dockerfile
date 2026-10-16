# lgx-db: PostgreSQL 18.6 + pgvector 0.8.7 + unaccented French stopwords (PLAN §5.3, §7.2, D6).
# Upstream pin (deploy/images.lock): pgvector/pgvector:0.8.7-pg18-trixie
#   @sha256:9d9c930220cb9bf2f956d10a8f909cf9973d9672ca0278aef2c1e6facccad2e0
# Pulled through mirror.gcr.io (anonymous Docker Hub pulls are rate-limited, PLAN §10.1).
# The schema is NOT applied at init: each release is built into a fresh database by
# scripts/db_apply.sh / `lgx load` ("rebuild, don't migrate"). The SQL is copied for lgx-ctl/restore use.
ARG PGVECTOR_IMAGE=mirror.gcr.io/pgvector/pgvector:0.8.7-pg18-trixie@sha256:9d9c930220cb9bf2f956d10a8f909cf9973d9672ca0278aef2c1e6facccad2e0
FROM ${PGVECTOR_IMAGE}

LABEL org.opencontainers.image.title="lgx-db" \
      org.opencontainers.image.description="PostgreSQL 18 + pgvector 0.8.7 + french_unaccent stopwords for the LegiLux local clone" \
      org.opencontainers.image.source="https://github.com/masterries/LegiLux-Full-Local-MCP-Clone"

COPY --chmod=0644 tsearch/french_unaccent.stop /usr/share/postgresql/18/tsearch_data/french_unaccent.stop
COPY --chmod=0644 schema/ /opt/lgx/schema/
