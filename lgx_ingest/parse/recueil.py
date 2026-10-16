"""XML code-recueils (``code/fonction_publique``, ``code/sante``, ``recueil/*``; PLAN §2.6).

M1 (here): helpers the AKN parser uses so recueil articles are parsed safely — generated ids
(``art_N10064``) are never anchors, ``wId`` article ELIs are normalised (``…/n2//consolide/…``) and give
the member act. M2: deduplication against the members' own consolidations (404 of 478 ``wId``s in
``code/fonction_publique/20261011``), keeping only ``/jo``-sourced articles from the recueil.
"""

from __future__ import annotations

import re

from lgx_core.eli import ELI_BASE

_GENERATED_ID = re.compile(r"^[a-z]+_N[0-9A-F]{4,}$")
_DATA_HOST = "http://data.legilux.public.lu"


def is_generated_id(akn_id: str) -> bool:
    """True for editor-generated, unstable ids such as ``art_N10064`` / ``part_N10026``."""
    return bool(_GENERATED_ID.match(akn_id))


def normalise_wid(wid: str) -> str:
    """Absolute, slash-normalised ``wId`` (``/eli/etat/leg/…/n2//consolide/…`` → ``…/n2/consolide/…``)."""
    w = wid.strip()
    if w.startswith("/eli/"):
        w = _DATA_HOST + w
    head, sep, tail = w.partition("://")
    if sep:
        tail = re.sub(r"/{2,}", "/", tail)
        w = f"{head}{sep}{tail}"
    return w.rstrip("/")


_ARTICLE_SEGMENT = re.compile(r"^(?P<work>.+?)/(?:art_|art\d|article_)")


def member_work_of_wid(wid: str) -> str | None:
    """Work ELI of the member act a recueil article comes from (the part before ``/art_…``,
    ``/art8``, ``/article_unique``)."""
    m = _ARTICLE_SEGMENT.match(wid)
    return m["work"] if m and wid.startswith(ELI_BASE) else None


def deduplicate(*_args: object, **_kwargs: object) -> None:
    """Recueil deduplication by ``wId`` (PLAN §2.6 rule 2)."""
    # TODO(M2): drop recueil copies whose wId is the member's current consolidation, keep /art_X/jo
    # articles, log malformed wIds; needs the selection's member versions (config/recueil_members.yaml).
    raise NotImplementedError("lgx_ingest.parse.recueil.deduplicate: M2")
