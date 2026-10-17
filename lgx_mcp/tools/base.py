"""Shared tool plumbing: argument validation, result shape."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

from lgx_core.models import ToolErrorCode
from lgx_mcp.context import ToolFailure
from lgx_mcp.schemas import TOOLS_BY_NAME

_VALIDATORS = {
    name: Draft202012Validator(t["inputSchema"], format_checker=FormatChecker()) for name, t in TOOLS_BY_NAME.items()
}


@dataclass
class ToolOutput:
    structured: dict[str, Any]
    text: str
    hits: int = 0
    meta: dict[str, Any] = field(default_factory=dict)


def validate_arguments(tool: str, arguments: dict[str, Any]) -> None:
    """jsonschema check against the published inputSchema (the model's mistake → BAD_INPUT, readable)."""
    errors = sorted(_VALIDATORS[tool].iter_errors(arguments), key=lambda e: list(e.path))
    if errors:
        e = errors[0]
        where = "/".join(str(p) for p in e.path) or "(arguments)"
        raise ToolFailure(
            ToolErrorCode.BAD_INPUT,
            f"argument invalide {where} : {e.message}",
            hint="Respectez le schéma d'entrée de l'outil (voir tools/list).",
        )
