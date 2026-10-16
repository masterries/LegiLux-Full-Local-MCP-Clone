# lgx-db

PostgreSQL 18.6 + pgvector 0.8.7 (`pgvector/pgvector:0.8.7-pg18-trixie@sha256:9d9c9302…`, pulled via
`mirror.gcr.io`) plus `tsearch/french_unaccent.stop` (stock `french.stop`, unaccented, deduplicated;
regenerate with `scripts/make_french_unaccent_stop.py`).

`schema/` is PLAN Appendix B split into parts applied in order by `scripts/db_apply.sh`:

| File | Content |
|---|---|
| `00_extensions.sql` | vector, unaccent, pg_trgm, btree_gist; schemas `lgx`, `lgx_meta`, `build` |
| `10_tsearch.sql` | `lgx.fr_legal` (unaccent + Snowball French + unaccented stopwords), `lgx.simple_ua`, `lgx.f_unaccent` |
| `20_tables.sql` | enums and tables (B.3–B.12); `chunk.embedding halfvec(:embed_dim)` is nullable |
| `30_functions.sql` | `act_status`, `version_as_of`, `text_version_as_of`, `serving_at`, `amending_acts_since`, `acts_as_of`, invariant view |
| `40_search.sql` | `or_query`, `search_candidates` (lexical + exact + cited [+ dense], RRF) |
| `90_postload.sql` | ANALYZE + HNSW index **only if vectors exist** (idempotent) |
| `95_roles.sql` | `lgx_ro` (read-only, 5 s statement timeout, 40 connections) and grants |

Deviations from the Appendix B sketch are listed at the top of each file and in `docs/dev/INTERFACES.md` §8.
The schema is not applied at container init: every release is built into a fresh database.
