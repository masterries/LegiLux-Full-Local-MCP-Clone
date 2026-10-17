"""Turn-local answer checks for the Chat verifier panel (PLAN §8.7, V1–V3 "always").

The full verifier (``lgx_core.verify``, M4) resolves citations through MCP; until it exists this module checks
what needs no lookup, using only the tool results of the current turn:

- **V2 support** — every ELI and every ``[Cn]`` key cited in the answer was returned by a tool in this turn;
- **V3 verbatim** — every quotation (« … » or "…", ≥ 15 characters) is a substring of text a tool returned,
  after whitespace and quote normalisation;
- **V1 existence** — ELIs returned by a tool come from the database, so V1 holds when V2 holds for every ELI;
  otherwise it stays *not checked* (``ok = None``) rather than guessing.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from lgx_ui.mcp_client import ToolOutcome

ELI_RE = re.compile(r"https?://data\.legilux\.public\.lu/eli/[^\s<>\"'»)\],;]+")
KEY_RE = re.compile(r"\[(C\d+)\]")
QUOTE_RE = re.compile(r"«\s*([^»]{15,}?)\s*»|“([^”]{15,}?)”|\"([^\"]{15,}?)\"")
MIN_QUOTE = 15


@dataclass
class Check:
    code: str
    ok: bool | None
    detail: str


@dataclass
class VerifierReport:
    checks: list[Check] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(c.ok is not False for c in self.checks)


def _strings(node: Any) -> Iterable[str]:
    if isinstance(node, str):
        yield node
    elif isinstance(node, Mapping):
        for v in node.values():
            yield from _strings(v)
    elif isinstance(node, list):
        for v in node:
            yield from _strings(v)


def normalise(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    for a, b in (("’", "'"), ("‘", "'"), ("“", '"'), ("”", '"'), ("«", '"'), ("»", '"'), (" ", " ")):
        text = text.replace(a, b)
    return re.sub(r"\s+", " ", text).strip().lower()


def _clean_eli(eli: str) -> str:
    return eli.rstrip(".").rstrip("/")


def verify_turn(answer: str, outcomes: Iterable[ToolOutcome]) -> VerifierReport:
    outcomes = [o for o in outcomes if not o.is_error]
    returned_strings: list[str] = []
    keys: set[str] = set()
    for o in outcomes:
        returned_strings.extend(_strings(o.structured))
        if o.text:
            returned_strings.append(o.text)
        for s in _walk_keys(o.structured):
            keys.add(s)
    returned_elis = {_clean_eli(m) for s in returned_strings for m in ELI_RE.findall(s)}

    cited_elis = sorted({_clean_eli(m) for m in ELI_RE.findall(answer)})
    unsupported_elis = [e for e in cited_elis if e not in returned_elis]
    cited_keys = sorted(set(KEY_RE.findall(answer)))
    unsupported_keys = [k for k in cited_keys if keys and k not in keys]

    report = VerifierReport()
    if not cited_elis and not cited_keys:
        report.checks.append(Check("V2", None, "aucune citation (ELI ou [Cn]) dans la réponse"))
    elif unsupported_elis or unsupported_keys:
        bad = unsupported_elis + [f"[{k}]" for k in unsupported_keys]
        report.checks.append(Check("V2", False, "cité sans avoir été renvoyé par un outil : " + ", ".join(bad)))
    else:
        report.checks.append(
            Check("V2", True, f"{len(cited_elis)} ELI et {len(cited_keys)} clé(s) renvoyés par les outils de ce tour")
        )

    if cited_elis and not unsupported_elis:
        report.checks.append(Check("V1", True, "tous les ELI cités proviennent de résultats d'outils (base locale)"))
    else:
        report.checks.append(Check("V1", None, "non vérifié sans recherche (lgx_core.verify, M4)"))

    haystack = normalise("\n".join(returned_strings))
    quotes = [next(g for g in m.groups() if g) for m in QUOTE_RE.finditer(answer)]
    quotes = [q for q in quotes if len(q.strip()) >= MIN_QUOTE]
    missing = [q for q in quotes if normalise(q) not in haystack]
    if not quotes:
        report.checks.append(Check("V3", None, "aucune citation entre guillemets"))
    elif missing:
        sample = "; ".join(f"« {q[:80]} »" for q in missing[:3])
        report.checks.append(Check("V3", False, f"{len(missing)}/{len(quotes)} citation(s) introuvable(s) : {sample}"))
    else:
        report.checks.append(Check("V3", True, f"{len(quotes)} citation(s) verbatim trouvée(s) dans les textes"))
    report.checks.sort(key=lambda c: c.code)
    return report


def _walk_keys(node: Any) -> Iterable[str]:
    if isinstance(node, Mapping):
        key = node.get("key")
        if isinstance(key, str) and re.fullmatch(r"C\d+", key):
            yield key
        for v in node.values():
            yield from _walk_keys(v)
    elif isinstance(node, list):
        for v in node:
            yield from _walk_keys(v)
