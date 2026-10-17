"""Answer verifier, minimal v1 (PLAN §8.7): V1 existence, V2 support, V3 verbatim.

``verify(answer, turn)`` checks an LLM answer against what the tools returned in this turn:

* **V1 existence** — every cited ELI (or ``[Cn]`` key) resolves to a unit/act; the lookup goes through a
  :class:`Resolver` (Streamlit passes an MCP-backed one over ``get_article`` / ``resolve_citation``, evaluation a
  database-backed one). Without a resolver, V1 falls back to V2's evidence and says so.
* **V2 support** — every cited ELI / key was returned by a tool in this turn.
* **V3 verbatim** — text in « … » or "…" is a substring of a returned unit text or snippet, after whitespace,
  quote and apostrophe normalisation.

V4–V8 (status, basis, temporal, coverage, language) arrive in M4. Pure Python, no I/O of its own.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

from lgx_core.normalise import normalise_whitespace

ELI_RE = re.compile(r"http://data\.legilux\.public\.lu/eli/[A-Za-z0-9_\-./]+[A-Za-z0-9_\-]")
KEY_RE = re.compile(r"\[(C\d+)\]")
QUOTE_RE = re.compile(r"«\s*([^«»]{12,}?)\s*»|“([^“”]{12,}?)”|\"([^\"]{12,}?)\"")
_QUOTES = str.maketrans({"’": "'", "‘": "'", "`": "'", "“": '"', "”": '"', "«": '"', "»": '"', " ": " "})


class Resolver(Protocol):
    """V1 lookups. ``exists`` answers for an act, version or article ELI."""

    def exists(self, eli: str) -> bool: ...


@dataclass
class TurnEvidence:
    """What the tools returned in this turn (``structuredContent`` of each call)."""

    elis: set[str] = field(default_factory=set)
    keys: dict[str, set[str]] = field(default_factory=dict)  # 'C1' -> ELIs of that citation
    texts: list[str] = field(default_factory=list)  # verbatim article texts and snippets

    @classmethod
    def from_results(cls, results: Iterable[Mapping[str, Any]]) -> TurnEvidence:
        ev = cls()
        for r in results:
            if not isinstance(r, Mapping):
                continue
            cits: list[Mapping[str, Any]] = []
            if isinstance(r.get("citation"), Mapping):
                cits.append(r["citation"])
                if isinstance(r.get("text"), str):
                    ev.texts.append(r["text"])
            for h in r.get("hits") or []:
                if isinstance(h, Mapping) and isinstance(h.get("citation"), Mapping):
                    cits.append(h["citation"])
                    if isinstance(h.get("snippet"), str):
                        ev.texts.append(h["snippet"])
            for c in r.get("candidates") or []:
                if isinstance(c, Mapping):
                    ev.elis.update(e for e in (c.get("act_eli"), c.get("unit_eli")) if isinstance(e, str))
            for k in ("act_eli", "act_jo_eli"):
                if isinstance(r.get(k), str):
                    ev.elis.add(r[k])
            for c in cits:
                elis = {
                    e
                    for e in (c.get("act_eli"), c.get("act_jo_eli"), c.get("article_eli"), c.get("version_eli"))
                    if isinstance(e, str)
                }
                ev.elis |= elis
                if isinstance(c.get("key"), str):
                    ev.keys.setdefault(c["key"], set()).update(elis)
        return ev


@dataclass
class CheckResult:
    check: str
    ok: bool
    details: list[str] = field(default_factory=list)


@dataclass
class VerifyReport:
    checks: list[CheckResult]

    @property
    def ok(self) -> bool:
        return all(c.ok for c in self.checks)

    def as_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "checks": [{"check": c.check, "ok": c.ok, "details": c.details} for c in self.checks]}


def _norm(text: str) -> str:
    return normalise_whitespace(text.translate(_QUOTES)).lower()


def cited_elis(answer: str) -> list[str]:
    return list(dict.fromkeys(m.group(0).rstrip(".") for m in ELI_RE.finditer(answer)))


def cited_keys(answer: str) -> list[str]:
    return list(dict.fromkeys(KEY_RE.findall(answer)))


def quotes(answer: str) -> list[str]:
    return [next(g for g in m.groups() if g) for m in QUOTE_RE.finditer(answer)]


def _covered(eli: str, known: set[str]) -> bool:
    """An article ELI is supported by itself; a work ELI also by any returned ELI below it."""
    return eli in known or any(k.startswith(eli.rstrip("/") + "/") for k in known)


def verify(answer: str, results: Iterable[Mapping[str, Any]], resolver: Resolver | None = None) -> VerifyReport:
    ev = TurnEvidence.from_results(results)
    elis = cited_elis(answer)
    keys = cited_keys(answer)
    # V2 support
    v2 = CheckResult("V2_support", True)
    for e in elis:
        if not _covered(e, ev.elis):
            v2.ok = False
            v2.details.append(f"ELI not returned by a tool in this turn: {e}")
    for k in keys:
        if k not in ev.keys:
            v2.ok = False
            v2.details.append(f"citation key not returned by a tool in this turn: [{k}]")
    # V1 existence
    v1 = CheckResult("V1_existence", True)
    if resolver is None:
        v1.details.append("no resolver: existence inferred from this turn's tool results")
        v1.ok = v2.ok
        v1.details += [d for d in v2.details if d.startswith("ELI")]
    else:
        for e in elis:
            if not resolver.exists(e):
                v1.ok = False
                v1.details.append(f"ELI does not resolve: {e}")
    # V3 verbatim
    v3 = CheckResult("V3_verbatim", True)
    corpus = [_norm(t) for t in ev.texts]
    for q in quotes(answer):
        nq = _norm(q).strip(" .,;:…")
        if nq and not any(nq in t for t in corpus):
            v3.ok = False
            v3.details.append(f"quotation not found verbatim in the returned texts: « {q[:120]} »")
    return VerifyReport([v1, v2, v3])


__all__ = ["CheckResult", "Resolver", "TurnEvidence", "VerifyReport", "cited_elis", "cited_keys", "quotes", "verify"]
