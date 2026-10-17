# syntax=docker/dockerfile:1
# lgx-mcp — runtime MCP server of the LegiLux local clone (PLAN §8, §10.1; compose service `mcp`).
#
# Build from the repository root:
#   docker build -f docker/lgx-mcp.Dockerfile -t lgx.local/lgx-mcp:dev .
# Behind a TLS-intercepting proxy, pass its CA bundle as a build secret (never baked into a layer):
#   docker build -f docker/lgx-mcp.Dockerfile --secret id=ca_bundle,src=/path/to/ca-bundle.crt -t lgx.local/lgx-mcp:dev .
#
# Bases are digest-pinned (deploy/images.lock) and pulled through mirror.gcr.io. Third-party dependencies come
# from uv.lock (--frozen, hashes): the base dependencies + the `mcp` extra only (no lxml/pyarrow/pdfplumber, no ML
# library, no tokenizer). The project itself is not built: lgx_core + lgx_mcp are copied onto PYTHONPATH. Runs
# as uid 10001, works with a read-only root filesystem (bytecode precompiled, nothing written at runtime) and
# needs no network except the database and, optionally, the internal reranker.
ARG PYTHON_IMAGE=mirror.gcr.io/library/python:3.13.16-slim-trixie@sha256:70729b46c69b4f1e97c4822c1af3df53a1476cf5ddc6c087c0c10bc3a5678c2f
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.11.32@sha256:df4cae8f3a96d175e2e5f992e597550000edbe78fdc2594d5cd8de1a217f504c

FROM ${UV_IMAGE} AS uv

FROM ${PYTHON_IMAGE} AS build
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_NO_CACHE=1 \
    UV_PYTHON_DOWNLOADS=never \
    UV_PYTHON=/usr/local/bin/python3 \
    UV_PROJECT_ENVIRONMENT=/opt/venv
WORKDIR /src
COPY pyproject.toml uv.lock README.md ./
RUN --mount=type=secret,id=ca_bundle,required=false \
    if [ -s /run/secrets/ca_bundle ]; then export SSL_CERT_FILE=/run/secrets/ca_bundle; fi; \
    uv sync --frozen --no-dev --extra mcp --no-install-project \
        --no-install-package typer --no-install-package rich
COPY lgx_core /opt/lgx/lib/lgx_core
COPY lgx_mcp /opt/lgx/lib/lgx_mcp
COPY config/aliases.yaml /opt/lgx/lib/config/aliases.yaml
RUN find /opt/lgx/lib -name '__pycache__' -prune -exec rm -rf {} + \
    && /opt/venv/bin/python -m compileall -q /opt/lgx/lib \
    && PYTHONPATH=/opt/lgx/lib /opt/venv/bin/python -c "import lgx_mcp.server, mcp, psycopg_pool; print('ok')"

FROM ${PYTHON_IMAGE} AS runtime
LABEL org.opencontainers.image.title="lgx-mcp" \
      org.opencontainers.image.description="Read-only MCP server of the LegiLux local clone (Streamable HTTP + stdio)" \
      org.opencontainers.image.source="https://github.com/masterries/LegiLux-Full-Local-MCP-Clone"
RUN groupadd --system --gid 10001 lgx \
    && useradd --system --uid 10001 --gid 10001 --home-dir /tmp --no-create-home --shell /usr/sbin/nologin lgx
COPY --from=build /opt/venv /opt/venv
COPY --from=build /opt/lgx/lib /opt/lgx/lib
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONPATH=/opt/lgx/lib \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOME=/tmp \
    DO_NOT_TRACK=1 \
    HF_HUB_OFFLINE=1 \
    LGX_CONFIG_DIR=/opt/lgx/lib/config \
    LGX_MCP_HOST=0.0.0.0 \
    LGX_MCP_PORT=8000
USER 10001:10001
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=5s --start-period=10s --retries=20 \
    CMD ["python", "-c", "import urllib.request as u; u.urlopen('http://localhost:8000/readyz', timeout=3)"]
ENTRYPOINT ["python", "-m", "lgx_mcp"]
