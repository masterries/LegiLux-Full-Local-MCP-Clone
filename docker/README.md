# Own images (PLAN §5.3, §10.1)

Planned Dockerfiles, all `FROM python:3.13.16-slim-trixie@sha256:70729b46…` (see `deploy/images.lock`),
dependencies installed from `uv.lock` with hashes, non-root uid 10001:

- `lgx-builder.Dockerfile` — build zone: `lgx` CLI + lxml + poppler-utils (GPL, build-only). M1.
- `lgx-mcp.Dockerfile` — runtime MCP server, `mcp==2.3.0`, no ML libraries. M1.
- `lgx-ctl.Dockerfile` — one-shot jobs (restore, smoke, provision, audit-egress, eval). M4/M5.
- `lgx-streamlit` lives in `ui/Dockerfile`. M1b.

The database image is `db/Dockerfile` (lgx-db = pgvector 0.8.7 on PostgreSQL 18 + unaccented stopwords).
