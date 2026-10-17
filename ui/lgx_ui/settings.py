"""Environment-driven settings of the tester UI (PLAN §9.3, Appendix C service ``lgx-streamlit``).

Secrets are read from ``*_FILE`` paths (Docker secrets / compose configs). The plain variables
(``LGX_MCP_TOKEN``, ``OPENAI_API_KEY``) exist only for host-side development. Nothing here is ever
rendered in the page: the MCP token and the LLM key stay server-side.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_MCP_URL = "http://localhost:8000/mcp"
DEFAULT_LLM_BASE_URL = "http://llm:11434/v1"
DEFAULT_LLM_MODEL = "gemma4:26b-a4b"
NO_KEY = "sk-no-key"  # Ollama / vLLM ignore the key; the OpenAI client insists on one

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
class LlmPreset:
    """An OpenAI-compatible Chat Completions endpoint the sidebar offers."""

    key: str
    label: str
    base_url: str
    model: str
    note: str = ""
    cloud: bool = False


@dataclass(frozen=True)
class UiSettings:
    mcp_url: str = DEFAULT_MCP_URL
    mcp_token: str | None = None
    mcp_timeout_s: float = 20.0
    llm_base_url: str = DEFAULT_LLM_BASE_URL
    llm_api_key: str = NO_KEY
    llm_model: str = DEFAULT_LLM_MODEL
    llm_timeout_s: float = 120.0
    llm_temperature: float = 0.2
    llm_trust_env: bool = False
    presets: tuple[LlmPreset, ...] = field(default_factory=tuple)
    allow_cloud: bool = False
    max_tool_iterations: int = 5
    turn_budget_s: float = 600.0
    tool_result_tokens: int = 4000
    eval_dir: Path | None = None
    hostname: str = "localhost"
    ui_lang: str = "fr"

    def preset(self, key: str) -> LlmPreset | None:
        return next((p for p in self.presets if p.key == key), None)

    def known_llm_urls(self) -> set[str]:
        """Base URLs that may receive the configured LLM key (never a free-typed URL)."""
        urls = {self.llm_base_url.rstrip("/")}
        urls.update(p.base_url.rstrip("/") for p in self.presets if p.base_url)
        return urls


def _read_secret(env: Mapping[str, str], name: str) -> str | None:
    """``NAME_FILE`` (a 0600 file / Docker secret) wins over ``NAME``; blank values count as unset."""
    path = env.get(f"{name}_FILE", "").strip()
    if path:
        try:
            value = Path(path).read_text(encoding="utf-8").strip()
        except OSError:
            value = ""
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


def build_presets(env: Mapping[str, str], *, allow_cloud: bool, base_url: str, model: str) -> tuple[LlmPreset, ...]:
    """Presets from ``LGX_LLM_PRESETS``; the first listed one carries ``OPENAI_BASE_URL`` / ``LGX_LLM_MODEL``.

    ``cloud`` is dropped unless ``LGX_ALLOW_CLOUD_LLM=1`` (only the opt-in ``cloudllm`` dev profile sets it).
    """
    names = [n.strip() for n in env.get("LGX_LLM_PRESETS", "ollama,vllm,mistral-onprem").split(",") if n.strip()]
    if not allow_cloud:
        names = [n for n in names if n != "cloud"]
    catalogue = {
        "ollama": LlmPreset(
            "ollama",
            "Ollama /v1 (cpu, gpu24)",
            env.get("LGX_OLLAMA_BASE_URL", DEFAULT_LLM_BASE_URL),
            env.get("LGX_OLLAMA_MODEL", DEFAULT_LLM_MODEL),
            "Ollama OpenAI-compatible endpoint; tool calling depends on the model.",
        ),
        "vllm": LlmPreset(
            "vllm",
            "vLLM (gpu48)",
            env.get("LGX_VLLM_BASE_URL", "http://llm:8000/v1"),
            env.get("LGX_VLLM_MODEL", "legal-llm"),
            "vLLM with --enable-auto-tool-choice.",
        ),
        "mistral-onprem": LlmPreset(
            "mistral-onprem",
            "Mistral on-prem (OpenAI-compatible)",
            env.get("LGX_MISTRAL_ONPREM_URL", ""),
            env.get("LGX_MISTRAL_ONPREM_MODEL", ""),
            "Operator-supplied endpoint under the user's licence; tool calling [U].",
        ),
        "cloud": LlmPreset(
            "cloud",
            "Cloud (opt-in, connected dev host only)",
            base_url,
            model,
            "Questions and retrieved public texts leave the host.",
            cloud=True,
        ),
    }
    presets: list[LlmPreset] = []
    for i, name in enumerate(names):
        preset = catalogue.get(name)
        if preset is None:
            continue
        if i == 0:  # the configured endpoint
            preset = LlmPreset(
                preset.key, preset.label, base_url or preset.base_url, model or preset.model, preset.note, preset.cloud
            )
        presets.append(preset)
    presets.append(LlmPreset("custom", "Autre endpoint (saisie libre)", "", model, "No API key is sent to it."))
    return tuple(presets)


def load_settings(env: Mapping[str, str] | None = None) -> UiSettings:
    env = os.environ if env is None else env
    allow_cloud = _truthy(env.get("LGX_ALLOW_CLOUD_LLM"))
    base_url = env.get("OPENAI_BASE_URL", "").strip() or DEFAULT_LLM_BASE_URL
    model = env.get("LGX_LLM_MODEL", "").strip() or DEFAULT_LLM_MODEL
    eval_dir = env.get("LGX_EVAL_DIR", "").strip()
    trust_env_raw = env.get("LGX_LLM_TRUST_ENV")
    return UiSettings(
        mcp_url=env.get("LGX_MCP_URL", "").strip() or DEFAULT_MCP_URL,
        mcp_token=_read_secret(env, "LGX_MCP_TOKEN"),
        mcp_timeout_s=_float(env, "LGX_MCP_TIMEOUT_S", 20.0),
        llm_base_url=base_url,
        llm_api_key=_read_secret(env, "OPENAI_API_KEY") or NO_KEY,
        llm_model=model,
        llm_timeout_s=_float(env, "LGX_LLM_TIMEOUT_S", 120.0),
        llm_temperature=_float(env, "LGX_LLM_TEMPERATURE", 0.2),
        # Internal endpoints never go through a proxy; only the opt-in cloud profile may use one.
        llm_trust_env=_truthy(trust_env_raw) if trust_env_raw is not None else allow_cloud,
        presets=build_presets(env, allow_cloud=allow_cloud, base_url=base_url, model=model),
        allow_cloud=allow_cloud,
        max_tool_iterations=max(1, _int(env, "LGX_MAX_TOOL_ITERATIONS", 5)),
        turn_budget_s=_float(env, "LGX_TURN_BUDGET_S", 600.0),
        tool_result_tokens=max(500, _int(env, "LGX_TOOL_RESULT_TOKENS", 4000)),
        eval_dir=Path(eval_dir) if eval_dir else None,
        hostname=env.get("LGX_HOSTNAME", "localhost"),
        ui_lang=(env.get("LGX_UI_LANG", "fr") or "fr").lower()[:2],
    )
