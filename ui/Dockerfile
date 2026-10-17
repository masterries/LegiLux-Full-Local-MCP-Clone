# lgx-streamlit — tester UI of the LegiLux local clone (PLAN §9.3, §10.1; service lgx-streamlit, profile testui).
#
# Build from the repository root (the context needs pyproject.toml, uv.lock, lgx_core/ and ui/):
#   docker build -f ui/Dockerfile -t lgx.local/lgx-streamlit:dev .
# Behind a TLS-intercepting proxy, pass its CA bundle as a build secret (never baked into a layer):
#   docker build -f ui/Dockerfile --secret id=ca_bundle,src=/path/to/ca-bundle.crt -t lgx.local/lgx-streamlit:dev .
#
# Bases are digest-pinned (deploy/images.lock) and pulled through mirror.gcr.io. Third-party dependencies come
# from uv.lock (--frozen, hashes) with the `ui` extra only; the project itself is not built (no build backend is
# downloaded): lgx_core + lgx_ui are copied onto PYTHONPATH. No database driver is installed: the UI has no DB
# credentials and talks only to the MCP server and the configured LLM endpoint. No pip, no network at runtime.
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
    uv sync --frozen --no-dev --extra ui --no-install-project \
        --no-install-package psycopg --no-install-package psycopg-binary --no-install-package psycopg-pool
COPY lgx_core /opt/lgx/lib/lgx_core
COPY ui/lgx_ui /opt/lgx/lib/lgx_ui
RUN find /opt/lgx/lib -name '__pycache__' -prune -exec rm -rf {} + \
    && /opt/venv/bin/python -m compileall -q /opt/lgx/lib \
    && PYTHONPATH=/opt/lgx/lib /opt/venv/bin/python -c "import lgx_ui.app_support, streamlit, openai, mcp; print('ok')"

FROM ${PYTHON_IMAGE} AS runtime
LABEL org.opencontainers.image.title="lgx-streamlit" \
      org.opencontainers.image.description="Tester UI (Streamlit) of the LegiLux local clone; MCP client, read-only" \
      org.opencontainers.image.source="https://github.com/masterries/LegiLux-Full-Local-MCP-Clone"
RUN groupadd --system --gid 10001 lgx \
    && useradd --system --uid 10001 --gid 10001 --home-dir /tmp --no-create-home --shell /usr/sbin/nologin lgx
COPY --from=build /opt/venv /opt/venv
COPY --from=build /opt/lgx/lib /opt/lgx/lib
COPY ui/app.py /app/app.py
COPY ui/pages /app/pages
COPY ui/.streamlit /app/.streamlit
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONPATH=/opt/lgx/lib \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOME=/tmp \
    HF_HUB_OFFLINE=1 \
    DO_NOT_TRACK=1 \
    STREAMLIT_BROWSER_GATHER_USAGE_STATS=false \
    STREAMLIT_BROWSER_SERVER_ADDRESS=localhost \
    STREAMLIT_LOGGER_HIDE_WELCOME_MESSAGE=true \
    STREAMLIT_SERVER_HEADLESS=true \
    STREAMLIT_SERVER_FILE_WATCHER_TYPE=none \
    STREAMLIT_SERVER_ADDRESS=0.0.0.0 \
    STREAMLIT_SERVER_PORT=8501 \
    STREAMLIT_CLIENT_TOOLBAR_MODE=minimal \
    STREAMLIT_CLIENT_SHOW_ERROR_DETAILS=none
WORKDIR /app
USER 10001:10001
EXPOSE 8501
HEALTHCHECK --interval=30s --timeout=5s --retries=10 \
    CMD ["python", "-c", "import urllib.request as u; u.urlopen('http://127.0.0.1:8501/_stcore/health', timeout=3)"]
# Binds 0.0.0.0 inside the container; compose publishes it on 127.0.0.1 only (dev) or keeps it internal (airgap).
CMD ["python", "-m", "lgx_ui.launch"]
