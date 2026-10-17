"""Query preparation for the lexical branch (PLAN §8.3, R9): citation spans out, noise words out, curated expansions in.

``search_legislation`` hands the SQL ranker three things instead of the raw question:

* ``lexical`` — the question without the spans of citations the resolver turned into cited units or act boosts
  (« Code du travail », « art. L. 121-1 C. trav. ») and without interrogative / framing words (« quel »,
  « selon »; German and English function words) that the French stemmer keeps and that would carry a high IDF;
* ``expansion`` — French terms from ``query_expansion.yaml`` whose trigger occurs in the question: official
  wording for colloquial terms (« liberté d'expression » → « liberté de manifester ses opinions », Constitution
  art. 23) and a small DE/EN → FR glossary for questions the LLM did not reformulate. They are ranked with a lower
  weight (``expansion_weight``) and never replace a French question's own words; for a German or English question
  with glossary hits the roles swap (the French terms lead, the original words get the lower weight);
* the list of what was removed and added, returned in ``query_interpretation`` so the behaviour is visible.

Pure functions over a YAML file shipped in the image; nothing is fetched, nothing is learned at runtime.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path
from typing import Any

import yaml

DEFAULT_FILE = Path(__file__).with_name("query_expansion.yaml")
OVERRIDE_NAME = "query_expansion.yaml"
_APOSTROPHES = str.maketrans({"’": "'", "`": "'", "´": "'", "‘": "'", "ʼ": "'"})
_TOKEN = re.compile(r"\S+")
_EDGE_PUNCT = "\"'«»“”„()[]{}.,;:!?¿¡"


# function words that identify the question language (folded); French wins ties
_FR_WORDS = "le la les de du des un une et est en dans pour sur au aux par que qui quel quelle quels quelles ce cette"
_FR_WORDS += " il elle sont son sa ses mon ma mes peut doit ou"
_DE_WORDS = "der die das und ist nicht ein eine einen fur mit von zu im den dem wie was welche welcher nach bei oder"
_DE_WORDS += " kann muss gibt wird werden sind mein meine darf"
_EN_WORDS = "the and is of to in for what how which does do can an are with under my be when who must may there"
_LANG_WORDS: dict[str, frozenset[str]] = {
    "fr": frozenset(_FR_WORDS.split(" ")),
    "de": frozenset(_DE_WORDS.split(" ")),
    "en": frozenset(_EN_WORDS.split(" ")),
}


def guess_lang(text: str) -> str:
    """fr | de | en from function words (a heuristic for the glossary, never shown as a fact)."""
    tokens = [tok.strip(_EDGE_PUNCT) for tok in fold(text).split()]
    scores = {lang: sum(tok in words for tok in tokens) for lang, words in _LANG_WORDS.items()}
    best = max(scores, key=lambda k: (scores[k], k == "fr"))
    return best if scores[best] > scores["fr"] else "fr"


def fold(text: str) -> str:
    """Lower case, accents removed, apostrophes unified, whitespace collapsed (same idea as ``alias_norm``)."""
    t = unicodedata.normalize("NFKD", text.translate(_APOSTROPHES))
    t = "".join(ch for ch in t if not unicodedata.combining(ch))
    return " ".join(t.lower().replace("œ", "oe").replace("æ", "ae").replace("ß", "ss").split())


@dataclass(frozen=True)
class Expansion:
    lang: str
    triggers: tuple[str, ...]
    add: str
    patterns: tuple[re.Pattern[str], ...] = field(compare=False, repr=False, default=())


@dataclass(frozen=True)
class PreparedQuery:
    original: str
    lexical: str
    expansion: str
    added: list[dict[str, str]]
    removed: list[str]
    lang: str = "fr"

    def as_dict(self) -> dict[str, Any]:
        return {"lexical_query": self.lexical, "expansion": self.expansion or None, "expansions": self.added,
                "removed": self.removed, "language_guess": self.lang}  # fmt: skip


class QueryExpander:
    """Compiled ``query_expansion.yaml``."""

    def __init__(self, data: Mapping[str, Any]) -> None:
        noise = data.get("noise") or {}
        self.noise: dict[str, frozenset[str]] = {
            str(lang): frozenset(fold(str(w)) for w in words or ()) for lang, words in noise.items()
        }
        entries: list[Expansion] = []
        for e in data.get("expansions") or ():
            triggers = tuple(fold(str(t)) for t in e.get("when") or () if str(t).strip())
            add = " ".join(str(e.get("add") or "").split())
            if not triggers or not add:
                continue
            pats = tuple(re.compile(r"(?<![\w'-])" + re.escape(t) + r"(?![\w-])") for t in triggers)
            entries.append(Expansion(str(e.get("lang") or "fr"), triggers, add, pats))
        self.expansions: tuple[Expansion, ...] = tuple(entries)
        self.status = str(data.get("status") or "draft_unvalidated")

    @classmethod
    def from_file(cls, path: Path) -> QueryExpander:
        return cls(yaml.safe_load(path.read_text(encoding="utf-8")) or {})

    def noise_words(self, lang: str) -> frozenset[str]:
        """French noise always; German / English function words only for a question in that language (« an » is
        a French noun: « délai d'un an »)."""
        words = self.noise.get("fr", frozenset())
        return words | self.noise.get(lang, frozenset()) if lang != "fr" else words

    def strip_noise(self, text: str, lang: str = "fr") -> tuple[str, list[str]]:
        noise = self.noise_words(lang)
        kept: list[str] = []
        removed: list[str] = []
        for tok in _TOKEN.findall(text):
            core = fold(tok).strip(_EDGE_PUNCT)
            if core and core in noise:
                removed.append(tok.strip(_EDGE_PUNCT))
            else:
                kept.append(tok)
        return " ".join(kept), removed

    def expand(self, text: str, lang: str = "fr") -> list[Expansion]:
        """French entries always; the DE / EN glossary only for a question in that language (« court », « sentence »
        or « fine » are also French words)."""
        folded = fold(text)
        return [e for e in self.expansions if e.lang in ("fr", lang) and any(p.search(folded) for p in e.patterns)]

    def prepare(self, query: str, *, strip_spans: Sequence[tuple[int, int]] = (), expand: bool = True) -> PreparedQuery:
        """``strip_spans``: character spans of ``query`` (resolved citations) left out of the lexical text."""
        lang = guess_lang(query)
        text, removed = _cut(query, strip_spans)
        lexical, noise = self.strip_noise(text, lang)
        found = self.expand(query, lang) if expand else []
        words = set(fold(lexical).split())
        add_terms: list[str] = []
        added: list[dict[str, str]] = []
        for e in found:
            added.append({"lang": e.lang, "trigger": e.triggers[0], "add": e.add})
            for term in e.add.split():
                if fold(term) not in words and term not in add_terms:
                    add_terms.append(term)
        lexical = " ".join(lexical.split())
        expansion = " ".join(add_terms)
        if lang != "fr" and add_terms:
            # a German / English question: its own words are not French (« period », « employee » even collide with
            # French lexemes), so the glossary's French terms lead and the original words follow with the lower weight
            lexical, expansion = expansion, lexical
        return PreparedQuery(query, lexical, expansion, added, [*removed, *noise], lang)


def _cut(text: str, spans: Iterable[tuple[int, int]]) -> tuple[str, list[str]]:
    out: list[str] = []
    removed: list[str] = []
    pos = 0
    for a, b in sorted({(max(0, a), min(len(text), b)) for a, b in spans if b > a}):
        if a < pos:  # overlapping span: keep cutting from the current position
            a = pos
        if b <= a:
            continue
        out.append(text[pos:a])
        removed.append(text[a:b].strip())
        pos = b
    out.append(text[pos:])
    return " ".join("".join(out).split()), [r for r in removed if r]


@cache
def _load(path: str) -> QueryExpander:
    return QueryExpander.from_file(Path(path))


def load_expander(config_dir: Path | None = None) -> QueryExpander:
    """The operator override ``$LGX_CONFIG_DIR/query_expansion.yaml`` when present, else the shipped file."""
    if config_dir is not None and (config_dir / OVERRIDE_NAME).is_file():
        return _load(str(config_dir / OVERRIDE_NAME))
    return _load(str(DEFAULT_FILE))
