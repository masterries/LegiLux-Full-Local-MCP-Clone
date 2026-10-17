"""Runtime settings of ``lgx-mcp`` from ``LGX_*`` environment variables (Appendix C, service ``mcp``).

Secrets come from files (``*_FILE``, Docker secrets); the plain variables (``LGX_MCP_TOKEN``,
``LGX_DB_PASSWORD``) exist for development only. Nothing here reads ``config/`` except the optional
``LGX_CONFIG_DIR`` (out-of-scope aliases, :mod:`lgx_mcp.resolve`).
"""

from __future__ import annotations

import ipaddress
import json
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from psycopg.conninfo import conninfo_to_dict, make_conninfo

from lgx_core.config import DEFAULT_CONFIG_DIR
from lgx_core.models import EmbeddingProvider
from lgx_core.urls import is_internal_url

RerankMode = Literal["auto", "on", "off"]
RetrievalMode = Literal["auto", "lexical", "hybrid"]

DEFAULT_ALLOWED_HOSTS = ("127.0.0.1:*", "localhost:*", "[::1]:*", "mcp:*")
SUPPORTED_SCHEMA_VERSIONS = (1,)


class SettingsError(ValueError):
    """Invalid or unsafe configuration; the server refuses to start."""


def _read_secret(path: str | None) -> str | None:
    if not path:
        return None
    try:
        return Path(path).read_text(encoding="utf-8").strip() or None
    except OSError as exc:
        raise SettingsError(f"cannot read secret file {path}: {exc}") from exc


def _bool(v: str | None, default: bool) -> bool:
    if v is None or v == "":
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


LEXICAL_OPTS = ("df_cap", "pool_df_max", "pool_cap", "header_weight", "phrase_weight", "length_b", "act_boost",
                "expansion_weight", "lex_limit")  # fmt: skip


def _opts(v: str | None) -> dict[str, float]:
    """``LGX_LEXICAL_OPTS='{"length_b": 0.4}'`` — numeric knobs of the bm25 ranker, unknown keys refused."""
    if not v or not v.strip():
        return {}
    try:
        data = json.loads(v)
    except json.JSONDecodeError as exc:
        raise SettingsError(f"LGX_LEXICAL_OPTS is not JSON: {exc}") from exc
    if not isinstance(data, dict) or any(k not in LEXICAL_OPTS for k in data):
        raise SettingsError(f"LGX_LEXICAL_OPTS keys must be among {', '.join(LEXICAL_OPTS)}")
    try:
        return {str(k): float(x) for k, x in data.items()}
    except (TypeError, ValueError) as exc:
        raise SettingsError("LGX_LEXICAL_OPTS values must be numbers") from exc


def _list(v: str | None, default: tuple[str, ...]) -> tuple[str, ...]:
    if v is None or not v.strip():
        return default
    return tuple(x.strip() for x in v.split(",") if x.strip())


@dataclass(frozen=True)
class McpSettings:
    """Everything the server reads from its environment."""

    pg_conninfo: str
    host: str = "0.0.0.0"
    port: int = 8000
    path: str = "/mcp"
    auth: bool = True
    token: str | None = field(default=None, repr=False)
    allowed_hosts: tuple[str, ...] = DEFAULT_ALLOWED_HOSTS
    allowed_origins: tuple[str, ...] = ()
    rerank_url: str | None = None
    rerank: RerankMode = "auto"
    rerank_k: int = 40
    rerank_batch: int = 32
    rerank_timeout_s: float = 10.0
    rerank_body_chars: int = 1800
    lexical_ranker: str = "bm25"  # bm25 (IDF-weighted, release search API ≥ 2) | rank_cd (M1 ranking)
    query_expansion: bool = True  # curated French legal synonyms / DE-EN glossary (lgx_mcp/retrieval/expansion)
    lexical_opts: dict[str, float] = field(default_factory=dict)  # ranker knobs (40_search.sql p_opts; eval, tuning)
    retrieval_mode_default: RetrievalMode = "auto"
    embed_provider: EmbeddingProvider = EmbeddingProvider.NONE
    embed_url: str | None = None
    embed_model: str | None = None
    embed_timeout_s: float = 5.0
    tz: str = "Europe/Luxembourg"
    staleness_warn_days: int = 30
    staleness_critical_days: int = 90
    max_result_tokens: int = 4000
    audit_query_text: bool = False
    audit_file: str | None = None
    audit_hmac_key: bytes = field(default=b"", repr=False)
    statement_timeout_ms: int = 5000
    pool_max: int = 8
    db_timeout_s: float = 3.0
    rate_per_client_min: int = 30
    rate_global_min: int = 300
    trusted_proxies: tuple[str, ...] = ()  # peers (IP / CIDR) whose user headers key the rate limit and audit
    config_dir: Path = DEFAULT_CONFIG_DIR
    log_level: str = "INFO"
    allow_external_urls: bool = False
    clock_override: str | None = None  # tests only: ISO datetime used as "now" (W_CLOCK_SUSPECT tests)

    @classmethod
    def from_env(
        cls,
        env: Mapping[str, str] | None = None,
        *,
        transport: Literal["http", "stdio"] | None = "http",
        **overrides: object,
    ) -> McpSettings:
        """Read the environment; ``transport=None`` skips validation (tests build partial settings)."""
        env = os.environ if env is None else env
        g = env.get
        dsn = g("LGX_MCP_PG_DSN")
        if dsn:
            info = conninfo_to_dict(dsn)
        else:
            info = {
                "host": g("LGX_DB_HOST", "db"),
                "port": g("LGX_DB_PORT", "5432"),
                "dbname": g("LGX_DB_NAME", "legilux"),
                "user": g("LGX_DB_USER", "lgx_ro"),
            }
            password = _read_secret(g("LGX_DB_PASSWORD_FILE")) or g("LGX_DB_PASSWORD")
            if password:
                info["password"] = password
        info.setdefault("application_name", "lgx-mcp")
        info.setdefault("connect_timeout", "3")
        token = _read_secret(g("LGX_MCP_TOKEN_FILE")) or g("LGX_MCP_TOKEN") or None
        hmac_key = _read_secret(g("LGX_AUDIT_HMAC_KEY_FILE"))
        rerank = (g("LGX_RERANK") or "auto").strip().lower()
        if rerank not in ("auto", "on", "off"):
            raise SettingsError(f"LGX_RERANK must be auto|on|off, got {rerank!r}")
        ranker = (g("LGX_LEXICAL_RANKER") or "bm25").strip().lower()
        if ranker not in ("bm25", "rank_cd"):
            raise SettingsError(f"LGX_LEXICAL_RANKER must be bm25|rank_cd, got {ranker!r}")
        mode = (g("LGX_RETRIEVAL_MODE_DEFAULT") or "auto").strip().lower()
        if mode not in ("auto", "lexical", "hybrid"):
            raise SettingsError(f"LGX_RETRIEVAL_MODE_DEFAULT must be auto|lexical|hybrid, got {mode!r}")
        values: dict[str, object] = {
            "pg_conninfo": make_conninfo(**info),
            "host": g("LGX_MCP_HOST", "0.0.0.0"),
            "port": int(g("LGX_MCP_PORT", "8000")),
            "path": g("LGX_MCP_PATH", "/mcp"),
            "auth": _bool(g("LGX_MCP_AUTH"), True),
            "token": token,
            "allowed_hosts": _list(g("LGX_ALLOWED_HOSTS"), DEFAULT_ALLOWED_HOSTS),
            "allowed_origins": _list(g("LGX_ALLOWED_ORIGINS"), ()),
            "rerank_url": (g("LGX_RERANK_URL") or "").strip() or None,
            "rerank": rerank,
            "rerank_k": int(g("LGX_RERANK_K", "40")),
            "rerank_batch": int(g("LGX_RERANK_BATCH", "32")),
            "rerank_timeout_s": float(g("LGX_RERANK_TIMEOUT_S", "10")),
            "rerank_body_chars": int(g("LGX_RERANK_BODY_CHARS", "1800")),
            "lexical_ranker": ranker,
            "query_expansion": _bool(g("LGX_QUERY_EXPANSION"), True),
            "lexical_opts": _opts(g("LGX_LEXICAL_OPTS")),
            "retrieval_mode_default": mode,
            "embed_provider": EmbeddingProvider(g("LGX_EMBED_PROVIDER") or "none"),
            "embed_url": (g("LGX_EMBED_URL") or "").strip() or None,
            "embed_model": (g("LGX_EMBED_MODEL") or "").strip() or None,
            "embed_timeout_s": float(g("LGX_EMBED_TIMEOUT_S", "5")),
            "tz": g("LGX_TZ", "Europe/Luxembourg"),
            "staleness_warn_days": int(g("LGX_STALENESS_WARN_DAYS", "30")),
            "staleness_critical_days": int(g("LGX_STALENESS_CRITICAL_DAYS", "90")),
            "max_result_tokens": int(g("LGX_MAX_RESULT_TOKENS", "4000")),
            "audit_query_text": _bool(g("LGX_AUDIT_QUERY_TEXT"), False),
            "audit_file": (g("LGX_AUDIT_FILE") or "").strip() or None,
            "audit_hmac_key": (hmac_key or token or "lgx-audit").encode("utf-8"),
            "statement_timeout_ms": int(g("LGX_STATEMENT_TIMEOUT_MS", "5000")),
            "pool_max": int(g("LGX_DB_POOL_MAX", "8")),
            "db_timeout_s": float(g("LGX_DB_TIMEOUT_S", "3")),
            "rate_per_client_min": int(g("LGX_RATE_PER_CLIENT_MIN", "30")),
            "rate_global_min": int(g("LGX_RATE_GLOBAL_MIN", "300")),
            "trusted_proxies": _list(g("LGX_TRUSTED_PROXIES"), ()),
            "config_dir": Path(g("LGX_CONFIG_DIR") or DEFAULT_CONFIG_DIR),
            "log_level": (g("LGX_LOG_LEVEL") or "INFO").upper(),
            "allow_external_urls": _bool(g("LGX_ALLOW_EXTERNAL_URLS"), False),
            "clock_override": g("LGX_CLOCK_OVERRIDE") or None,
        }
        values.update(overrides)
        settings = cls(**values)  # type: ignore[arg-type]
        if transport is not None:
            settings.validate(transport=transport)
        return settings

    def validate(self, *, transport: Literal["http", "stdio"] = "http") -> None:
        if self.rerank_batch < 1 or self.rerank_batch > 32:
            raise SettingsError("LGX_RERANK_BATCH must be 1..32 (TEI rejects larger /rerank batches by default)")
        if self.rerank_k < 1 or self.rerank_k > 100:
            raise SettingsError("LGX_RERANK_K must be 1..100")
        if self.rerank_body_chars < 100 or self.rerank_body_chars > 8000:
            raise SettingsError("LGX_RERANK_BODY_CHARS must be 100..8000")
        for name, url in (("LGX_RERANK_URL", self.rerank_url), ("LGX_EMBED_URL", self.embed_url)):
            if url and not self.allow_external_urls and not is_internal_url(url):
                raise SettingsError(
                    f"{name}={url} is not an internal service URL (set LGX_ALLOW_EXTERNAL_URLS=1 in dev)"
                )
        if transport == "http" and self.auth and not self.token:
            raise SettingsError(
                "bearer token missing: set LGX_MCP_TOKEN_FILE (Docker secret) or LGX_MCP_TOKEN,"
                " or LGX_MCP_AUTH=off (dev)"
            )
        if self.token is not None and len(self.token) < 16:
            raise SettingsError("the MCP bearer token must have at least 16 characters")
        for net in self.trusted_proxies:
            try:
                ipaddress.ip_network(net, strict=False)
            except ValueError as exc:
                raise SettingsError(f"LGX_TRUSTED_PROXIES: {net!r} is not an IP address or CIDR") from exc

    def describe(self) -> dict[str, object]:
        """Non-secret view for logs and ``about_corpus``."""
        info = conninfo_to_dict(self.pg_conninfo)
        return {
            "db": {k: info.get(k) for k in ("host", "port", "dbname", "user")},
            "listen": f"{self.host}:{self.port}{self.path}",
            "auth": self.auth,
            "allowed_hosts": list(self.allowed_hosts),
            "allowed_origins": list(self.allowed_origins),
            "rerank": self.rerank,
            "rerank_url": self.rerank_url,
            "rerank_k": self.rerank_k,
            "rerank_body_chars": self.rerank_body_chars,
            "lexical_ranker": self.lexical_ranker,
            "query_expansion": self.query_expansion,
            "retrieval_mode_default": self.retrieval_mode_default,
            "embed_provider": self.embed_provider.value,
        }
