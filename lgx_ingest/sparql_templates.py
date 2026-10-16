"""Load ``sparql/*.rq`` and fill ``{{NAME}}`` placeholders with validated values (sparql/README.md).

Only typed values are accepted, never raw SPARQL fragments: IRIs, IRI lists (≤ 500 per batch, sent by
POST), ISO dates/datetimes and integers. ``sparql/prefixes.rq`` is prepended to every query.
"""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Sequence
from pathlib import Path

from lgx_core.config import REPO_ROOT

SPARQL_DIR = REPO_ROOT / "sparql"
MAX_VALUES = 500
PLACEHOLDER = re.compile(r"{{([A-Z][A-Z0-9_]*)}}")
_IRI = re.compile(r"^https?://[^\s<>\"{}|\\^`]+$")

PlaceholderValue = str | int | dt.date | dt.datetime | Sequence[str]


class TemplateError(ValueError):
    pass


def query_path(name: str) -> Path:
    """``q01_acts`` → ``sparql/q01_acts.rq``; ``checks/q14_stubs`` works too."""
    path = (SPARQL_DIR / f"{name}.rq").resolve()
    if SPARQL_DIR.resolve() not in path.parents or not path.is_file():
        raise TemplateError(f"unknown query {name!r}")
    return path


def placeholders(text: str) -> set[str]:
    return set(PLACEHOLDER.findall(text))


def _iri(value: str) -> str:
    if not _IRI.match(value):
        raise TemplateError(f"not an IRI: {value!r}")
    return f"<{value}>"


def _literal(name: str, value: PlaceholderValue) -> str:
    """Render one placeholder value. Names decide the expected type (see sparql/README.md)."""
    if name in {"WORKS", "GRAPHS", "IRIS"}:
        if isinstance(value, str) or not isinstance(value, Sequence):
            raise TemplateError(f"{name} expects a list of IRIs")
        if not 0 < len(value) <= MAX_VALUES:
            raise TemplateError(f"{name}: 1..{MAX_VALUES} IRIs per request, got {len(value)}")
        return "\n    ".join(_iri(v) for v in value)
    if name in {"SINCE"}:
        if not isinstance(value, dt.datetime):
            raise TemplateError(f"{name} expects a datetime")
        if value.tzinfo is None:
            raise TemplateError(f"{name} must be timezone-aware")
        return value.astimezone(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    if name in {"FROM", "TO", "SINCE_DATE"}:
        if isinstance(value, dt.datetime) or not isinstance(value, dt.date):
            raise TemplateError(f"{name} expects a date")
        return value.isoformat()
    if name in {"Y0", "Y1"}:
        if isinstance(value, bool) or not isinstance(value, int):
            raise TemplateError(f"{name} expects an int")
        return str(value)
    raise TemplateError(f"undocumented placeholder {{{{{name}}}}}")


def render_query(name: str, **params: PlaceholderValue) -> str:
    """Return the full query text (prefixes + body) with every placeholder filled."""
    body = query_path(name).read_text(encoding="utf-8")
    needed = placeholders(body)
    missing, extra = needed - params.keys(), params.keys() - needed
    if missing or extra:
        raise TemplateError(f"{name}: missing {sorted(missing)}, unexpected {sorted(extra)}")
    filled = PLACEHOLDER.sub(lambda m: _literal(m.group(1), params[m.group(1)]), body)
    prefixes = (SPARQL_DIR / "prefixes.rq").read_text(encoding="utf-8")
    return prefixes.rstrip() + "\n\n" + filled.strip() + "\n"


def list_queries() -> list[str]:
    return sorted(
        str(p.relative_to(SPARQL_DIR).with_suffix("")) for p in SPARQL_DIR.rglob("*.rq") if p.name != "prefixes.rq"
    )
