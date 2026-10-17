"""The five read-only tools (Appendix D.2), dispatched by name."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from lgx_mcp.context import CallContext
from lgx_mcp.runtime import Runtime
from lgx_mcp.tools.about import about_corpus
from lgx_mcp.tools.act import get_act
from lgx_mcp.tools.article import get_article
from lgx_mcp.tools.base import ToolOutput, validate_arguments
from lgx_mcp.tools.cite import resolve_citation
from lgx_mcp.tools.search import search_legislation

ToolFn = Callable[[Runtime, CallContext, dict[str, Any]], Awaitable[ToolOutput]]

HANDLERS: dict[str, ToolFn] = {
    "search_legislation": search_legislation,
    "get_article": get_article,
    "get_act": get_act,
    "resolve_citation": resolve_citation,
    "about_corpus": about_corpus,
}

__all__ = ["HANDLERS", "ToolFn", "ToolOutput", "validate_arguments"]
