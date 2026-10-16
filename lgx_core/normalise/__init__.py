"""Text and number normalisation shared by parsers, resolver and loader.

Contract (PLAN §6.3 "Normalisation", INTERFACES §5):

* ``num_norm('Art. L. 121-1.') == 'l121-1'``, ``num_norm('Art. 1er.') == '1'``, ``bis/ter/…`` kept;
  lists and ranges keep their connector: ``'Art. 9 et 10.' -> '9_et_10'``, ``'Art. 17 à 20.' -> '17_a_20'``;
* :func:`num_aliases` gives the extra ``lgx.article_alias`` keys of a unit: every member of a list or
  range (``'9'``, ``'10'``; ``'17'`` … ``'20'``) plus the key forms of the AKN id and of the article ELI
  (``art_9_et_10`` → ``'9_et_10'``, ELI ``art_9_10`` → ``'9_10'``, ``art_17--20`` → ``'17--20'``);
* ``normalise_whitespace`` collapses runs of whitespace (incl. NBSP) to one space and strips;
* ``alias_norm`` = lower-case + unaccent + collapsed spaces (``lgx.act_alias.alias_norm``).

Everything here is pure and deterministic: the MCP resolver normalises user input with the same
functions, so build-side keys and query-side keys always agree.
"""

from __future__ import annotations

import re
import unicodedata

__all__ = [
    "LATIN_SUFFIXES",
    "alias_norm",
    "is_repeal_stub_text",
    "key_from_eid",
    "normalise_whitespace",
    "num_aliases",
    "num_norm",
    "unaccent",
]

# Latin ordinal suffixes used in Luxembourg article numbering (« Art. 7bis. », « Art. 120quinquies. »).
LATIN_SUFFIXES = (
    "bis",
    "ter",
    "quater",
    "quinquies",
    "sexies",
    "septies",
    "octies",
    "nonies",
    "novies",
    "decies",
    "undecies",
    "duodecies",
    "terdecies",
    "quaterdecies",
    "quindecies",
    "sexdecies",
    "septdecies",
    "octodecies",
    "novodecies",
    "vicies",
)
_SUFFIX_ALT = "|".join(sorted(LATIN_SUFFIXES, key=len, reverse=True))

# Zero-width characters that appear in Legilux HTML anchors (``<a name="art_1">​</a>``) and as
# editor artefacts; they are removed, not turned into spaces.
_ZERO_WIDTH = dict.fromkeys(map(ord, "​‌‍⁠﻿­"), None)
_WS = re.compile(r"\s+")  # str regex: \s covers NBSP (U+00A0), U+202F, U+2009 … in Python 3
_LIGATURES = str.maketrans({"œ": "oe", "Œ": "OE", "æ": "ae", "Æ": "AE", "ß": "ss"})
_RANGE_CAP = 400  # never expand a range into more alias keys than this


def normalise_whitespace(text: str) -> str:
    """Collapse every run of whitespace (NBSP, thin spaces, newlines …) to one space; strip.

    Zero-width characters and soft hyphens are removed first.
    """
    return _WS.sub(" ", text.translate(_ZERO_WIDTH)).strip()


def unaccent(text: str) -> str:
    """Strip diacritics (NFKD + drop combining marks) and expand œ/æ/ß, like PostgreSQL ``unaccent``."""
    decomposed = unicodedata.normalize("NFKD", text.translate(_LIGATURES))
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def alias_norm(text: str) -> str:
    """Key of ``lgx.act_alias.alias_norm``: lower-case, unaccented, typographic quotes unified, spaces collapsed."""
    t = unaccent(text).lower()
    t = t.replace("’", "'").replace("‘", "'").replace("`", "'")
    return normalise_whitespace(t)


# ----------------------------------------------------------------------------------------- num_norm
_ART_WORD = re.compile(r"\b(?:articles?|arts?)\b\.?", re.IGNORECASE)
_QUOTES = re.compile(r"[«»\"“”„'’‘()\[\]:;]")
# 'l. 121-1' / 'l 121-1' -> 'l121-1'; the connectors 'et' / 'a' (« 17 à 20 ») never join without a dot
_LETTER_PREFIX = re.compile(r"\b(?:([a-z]{1,2})\.\s*|(?!(?:et|a|au)\b)([a-z]{1,2})\s+)(?=\d)")
_FIRST = re.compile(r"(\d+)\s*(?:er|ere|re)(?=(?:" + _SUFFIX_ALT + r")?\b)")  # 1er / 1ère -> 1
_SUFFIX_GAP = re.compile(r"(\d)\s+(" + _SUFFIX_ALT + r")\b")  # '5 bis' -> '5bis'
_TRAILING_DOT = re.compile(r"\.(?=\s|$)")
_CONNECTORS = {"et": "et", "a": "a", "au": "a"}


def num_norm(num_raw: str) -> str:
    """Normalise an article number label to its lookup key (see module docstring).

    Returns ``''`` when nothing but the « Art. » prefix is present.
    """
    t = unaccent(normalise_whitespace(num_raw)).lower()
    t = _ART_WORD.sub(" ", t)
    t = re.sub(r"\s*,\s*", " et ", t)  # « Art. 5, 6 et 7. » is a list
    t = _QUOTES.sub(" ", t)
    t = _LETTER_PREFIX.sub(lambda m: m.group(1) or m.group(2), t)
    t = _TRAILING_DOT.sub(" ", t)
    t = normalise_whitespace(t)
    t = _FIRST.sub(r"\1", t)
    t = _SUFFIX_GAP.sub(r"\1\2", t)
    t = t.strip(" .-")
    return "_".join(t.split())


def key_from_eid(eid: str) -> str:
    """Lookup key of an AKN id or an ELI article tail (``art_l_121-1`` → ``l121-1``, ``art_9_10`` → ``9_10``).

    Ids of annex articles (``annexe_1_art_2``) keep only the article part (``2``).
    """
    tail = eid.rstrip("/").rsplit("/", 1)[-1]
    tail = unaccent(tail).lower()
    if tail.startswith("annex") and "_art_" in tail:
        tail = tail.rsplit("_art_", 1)[1]
    elif tail.startswith("art_"):
        tail = tail[4:]
    tail = tail.replace("_art_", "_")  # 'art_181_à_art_192-2'
    tail = re.sub(r"^([a-z]{1,2})_(?=\d)", r"\1", tail)
    tail = re.sub(r"_(?!(?:et|a|au)_)([a-z]{1,2})_(?=\d)", r"_\1", tail)
    tail = _FIRST.sub(r"\1", tail.replace("_", " ")).replace(" ", "_")
    return tail.strip("_")


def _split_members(key: str) -> tuple[str, list[str]] | None:
    """('et', [members]) for lists, ('a', [start, end]) for ranges, None for a single number."""
    parts = key.split("_")
    if len(parts) < 3:
        return None
    if "a" in parts and parts.count("a") == 1:
        i = parts.index("a")
        start, end = "_".join(parts[:i]), "_".join(parts[i + 1 :])
        if start and end:
            return "a", [start, end]
    if "et" in parts:
        members = [m for m in "_".join(parts).replace("_et_", ",").split(",") if m]
        return "et", [m.strip("_") for m in members if m.strip("_")]
    return None


_NUM_TAIL = re.compile(r"^(?P<prefix>.*?)(?P<n>\d+)(?P<suffix>(?:" + _SUFFIX_ALT + r")?)$")


def _expand_range(start: str, end: str) -> list[str]:
    """Members of 'start à end' when both share a prefix and end in integers (17 à 20; l542-4 à l542-6)."""
    ms, me = _NUM_TAIL.match(start), _NUM_TAIL.match(end)
    if not ms or not me:
        return [start, end]
    a, b = int(ms["n"]), int(me["n"])
    if ms["prefix"] == me["prefix"] and a <= b and b - a < _RANGE_CAP:
        out = [f"{ms['prefix']}{k}" for k in range(a, b + 1)]
        if ms["suffix"]:
            out[0] = start
        if me["suffix"]:
            out.append(end)
        return out
    # different prefixes ('181' à '192-2'): integers of the start's level up to the end's base number
    mb = re.match(r"^(\d+)", end)
    if not ms["prefix"] and mb and a <= int(mb.group(1)) and int(mb.group(1)) - a < _RANGE_CAP:
        out = [str(k) for k in range(a, int(mb.group(1)) + 1)]
        return [*out, end] if end not in out else out
    return [start, end]


def num_aliases(num_raw: str | None, *, akn_id: str | None = None, eli_unit: str | None = None) -> list[str]:
    """Extra ``article_alias`` keys of a unit (deduplicated, in a stable order, without ``num_norm`` itself)."""
    primary = num_norm(num_raw) if num_raw else ""
    keys: list[str] = []
    for source in (primary, key_from_eid(akn_id) if akn_id else "", key_from_eid(eli_unit) if eli_unit else ""):
        if not source:
            continue
        keys.append(source)
        split = _split_members(source)
        if split is None:
            continue
        kind, members = split
        if kind == "a" and len(members) == 2:
            keys.extend(_expand_range(*members))
            keys.append(f"{members[0]}--{members[1]}")
        else:
            keys.extend(members)
            keys.append("_".join(members))
    seen: set[str] = {primary}
    out: list[str] = []
    for k in keys:
        if k and k not in seen:
            seen.add(k)
            out.append(k)
    return out


# ------------------------------------------------------------------------------------ repeal stubs
_PAREN_GROUP = re.compile(r"\((?:[^()]|\([^()]*\))*\)")
_STUB_WORDS = frozenset(
    {
        "article",
        "articles",
        "abroge",
        "abroges",
        "abrogee",
        "abrogees",
        "implicitement",
        "supprime",
        "supprimes",
        "supprimee",
        "supprimees",
        "et",
    }
)


def is_repeal_stub_text(text: str) -> bool:
    """True when a whole article body is only a repeal note.

    Matches « Abrogé ( L. 23 avril 1934 ) », « (...) (abrogé par la loi du 14 mars 2017 ) »,
    « ( L. du 20 juillet 2018 ) Article abrogé. », « Abrogés implicitement (Const. art. 18) » — but not
    a modifying article such as « L'article 104 de la loi … est abrogé. ».
    """
    t = unaccent(normalise_whitespace(text)).lower()
    if not t or len(t) > 400 or not re.search(r"abrog|supprim", t):
        return False
    residual = _PAREN_GROUP.sub(" ", t)
    residual = re.sub(r"[\s.,;:…«»\"'’\-–]+", " ", residual).strip()
    return all(w in _STUB_WORDS for w in residual.split())
