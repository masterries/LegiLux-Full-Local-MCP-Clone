"""Deterministic citation parser (PLAN §8.3; MCP stage, M1).

Resolution order used by the MCP resolver (``lgx_mcp.resolve``): ELI → Mémorial reference → ``act_alias`` →
(type, date) candidates ranked by title trigram similarity. Never guess: top-two within 0.05 → ``ambiguous``.
This package only *parses*; it never touches the database.
"""

from lgx_core.citeparse.model import ActMention, ArticleMention, Citation, OutOfScopeMention, QueryAnalysis
from lgx_core.citeparse.parser import (
    DEFAULT_ALIASES,
    MONTHS,
    TYPE_SURFACES,
    CitationParser,
    default_parser,
    exact_tokens,
    parse,
)

# Surface forms the parser recognises that the database stores under another alias_norm
# (lgx.act_alias holds the curated config/aliases.yaml keys). The resolver tries the equivalent key too.
ALIAS_EQUIVALENTS: dict[str, str] = {
    "code de procedure civile": "nouveau code de procedure civile",
    "n.c.p.c.": "ncpc",
    "code d'instruction criminelle": "code de procedure penale",
    "c.s.s.": "css",
    "const": "const.",
}

__all__ = [
    "ALIAS_EQUIVALENTS",
    "DEFAULT_ALIASES",
    "MONTHS",
    "TYPE_SURFACES",
    "ActMention",
    "ArticleMention",
    "Citation",
    "CitationParser",
    "OutOfScopeMention",
    "QueryAnalysis",
    "default_parser",
    "exact_tokens",
    "parse",
]
