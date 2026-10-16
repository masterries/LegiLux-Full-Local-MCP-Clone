-- =====================================================================================================
-- lgx-db schema, part 00: extensions and schemas (PLAN Appendix B.1).
-- Apply all parts in file-name order into a FRESH database ("rebuild, don't migrate"):
--   scripts/db_apply.sh --dbname legilux_build_<T> --recreate [--embed-dim 1024]
-- Target: PostgreSQL 18 + pgvector 0.8.7 (image lgx-db, db/Dockerfile).
-- =====================================================================================================
\set ON_ERROR_STOP on

CREATE EXTENSION IF NOT EXISTS vector;      -- pgvector 0.8.7 (halfvec, HNSW, iterative scans)
CREATE EXTENSION IF NOT EXISTS unaccent;
CREATE EXTENSION IF NOT EXISTS pg_trgm;     -- fuzzy title / alias matching for the citation resolver
CREATE EXTENSION IF NOT EXISTS btree_gist;  -- EXCLUDE (act_id WITH =, text_valid WITH &&)

CREATE SCHEMA lgx;        -- legal data, shipped
CREATE SCHEMA lgx_meta;   -- release metadata + invariant views, shipped
CREATE SCHEMA build;      -- build-side state, NEVER shipped (pg_dump --exclude-schema=build)
