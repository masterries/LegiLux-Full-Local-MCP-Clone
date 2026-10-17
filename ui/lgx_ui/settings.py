"""Environment-driven settings of the tester UI (PLAN §9.3, Appendix C service ``lgx-streamlit``).

Secrets are read from ``*_FILE`` paths (Docker secrets / compose configs). The plain variables
(``LGX_MCP_TOKEN``, ``OPENAI_API_KEY``) exist only for host-side development. Nothing here is ever
rendered in the page: the MCP token and the LLM key stay server-side.

The chat LLM is one EXTERNAL OpenAI-compatible endpoint (no LLM runtime is bundled): ``OPENAI_BASE_URL`` +
``OPENAI_API_KEY_FILE`` + ``LGX_LLM_MODEL``. By default that is the user's Mistral endpoint — the same API as
Mistral SaaS (``https://api.mistral.ai/v1``) under the on-prem domain in the airgap; only the URL differs.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from lgx_core.urls import is_internal_url

DEFAULT_MCP_URL = "http://localhost:8000/mcp"
DEFAULT_LLM_BASE_URL = "https://api.mistral.ai/v1"  # Mistral SaaS; the airgap sets its on-prem domain
DEFAULT_LLM_MODEL = "mistral-small-latest"  # [U] whatever model the user's endpoint serves (LGX_LLM_MODEL)
NO_KEY = "sk-no-key"  # sent when no key is configured (or to a free-typed URL); the OpenAI client insists on one

DOC_TYPES_FALLBACK: tuple[str, ...] = (
    "Constitution",
    "CODE",
    "CODE_RECUEIL",
    "RECUEIL",
    "LOI",
    "RGD",
    "AGD",
    "RMIN",
    "AMIN",
    "RGC",
    "RILR",
    "RCSF",
    "RBCL",
    "ST",
    "ACC",
    "AGC",
    "DGC",
    "RI",
    "AGDR",
    "CONV",
    "PROT",
)


@dataclass(frozen=True)
class UiSettings:
    mcp_url: str = DEFAULT_MCP_URL
    mcp_token: str | None = None
    mcp_timeout_s: float = 30.0  # > the server's default rerank budget (LGX_RERANK_TIMEOUT_S 15) + SQL + margin
    llm_base_url: str = DEFAULT_LLM_BASE_URL
    llm_api_key: str = NO_KEY
    llm_model: str = DEFAULT_LLM_MODEL
    llm_timeout_s: float = 120.0
    llm_temperature: float = 0.2
    llm_trust_env: bool = False
    max_tool_iterations: int = 5
    turn_budget_s: float = 600.0
    tool_result_tokens: int = 4000
    eval_dir: Path | None = None
    hostname: str = "localhost"
    ui_lang: str = "fr"

    def key_allowed(self, base_url: str) -> bool:
        """Only the configured endpoint receives the configured LLM key (never a URL typed into the sidebar)."""
        return base_url.strip().rstrip("/") == self.llm_base_url.strip().rstrip("/")

    @property
    def llm_external(self) -> bool:
        """The chat endpoint is outside the stack: questions and retrieved public texts are sent to it."""
        return not is_internal_url(self.llm_base_url)


class UiSettingsError(RuntimeError):
    """A configured secret file cannot be read: the UI refuses to start (as lgx-mcp does)."""


def _read_secret(env: Mapping[str, str], name: str) -> str | None:
    """``NAME_FILE`` (a Docker secret, readable by uid 10001) wins over ``NAME``; blank values count as unset.

    An unreadable ``NAME_FILE`` is an error, never a silent fallback to "no token" / "no key" (every MCP call
    would then fail with 401 and the LLM would get ``sk-no-key``)."""
    path = env.get(f"{name}_FILE", "").strip()
    if path:
        try:
            value = Path(path).read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise UiSettingsError(
                f"cannot read secret file {name}_FILE={path}: {exc} (Docker secrets of lgx-streamlit must be"
                " readable by uid 10001: chown 10001:10001, chmod 0400)"
            ) from exc
        if value:
            return value
    value = env.get(name, "").strip()
    return value or None


def _float(env: Mapping[str, str], name: str, default: float) -> float:
    try:
        return float(env.get(name, "") or default)
    except ValueError:
        return default


def _int(env: Mapping[str, str], name: str, default: int) -> int:
    try:
        return int(env.get(name, "") or default)
    except ValueError:
        return default


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def load_settings(env: Mapping[str, str] | None = None) -> UiSettings:
    env = os.environ if env is None else env
    base_url = env.get("OPENAI_BASE_URL", "").strip() or DEFAULT_LLM_BASE_URL
    model = env.get("LGX_LLM_MODEL", "").strip() or DEFAULT_LLM_MODEL
    eval_dir = env.get("LGX_EVAL_DIR", "").strip()
    trust_env_raw = env.get("LGX_LLM_TRUST_ENV")
    return UiSettings(
        mcp_url=env.get("LGX_MCP_URL", "").strip() or DEFAULT_MCP_URL,
        mcp_token=_read_secret(env, "LGX_MCP_TOKEN"),
        mcp_timeout_s=_float(env, "LGX_MCP_TIMEOUT_S", 30.0),
        llm_base_url=base_url,
        llm_api_key=_read_secret(env, "OPENAI_API_KEY") or NO_KEY,
        llm_model=model,
        llm_timeout_s=_float(env, "LGX_LLM_TIMEOUT_S", 120.0),
        llm_temperature=_float(env, "LGX_LLM_TEMPERATURE", 0.2),
        # Never through a proxy unless asked (LGX_LLM_TRUST_ENV=1, e.g. a dev host that reaches Mistral SaaS
        # only through its HTTPS proxy); the airgap's on-prem endpoint is reached directly.
        llm_trust_env=_truthy(trust_env_raw),
        max_tool_iterations=max(1, _int(env, "LGX_MAX_TOOL_ITERATIONS", 5)),
        turn_budget_s=_float(env, "LGX_TURN_BUDGET_S", 600.0),
        tool_result_tokens=max(500, _int(env, "LGX_TOOL_RESULT_TOKENS", 4000)),
        eval_dir=Path(eval_dir) if eval_dir else None,
        hostname=env.get("LGX_HOSTNAME", "localhost"),
        ui_lang=(env.get("LGX_UI_LANG", "fr") or "fr").lower()[:2],
    )
