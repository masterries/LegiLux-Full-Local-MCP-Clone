"""Lexical leakage of G2 questions (M2 review, 2026-10-17): the longest run of normalised tokens a question shares
with its first target's text or heading. A question that reuses ≥ 5 tokens of the article's own wording is marked
``leaky`` (the lexical baseline scores it easily); reports give the metrics with and without the leaky items.

Tokens: lower case, unaccented, letters and digits, French stopwords kept (a verbatim run is what matters).
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

from lgx_core.normalise import unaccent

LEAK_MIN = 5  # shared run length (tokens) from which a question counts as leaky


def norm_tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", unaccent(text or "").lower())


def longest_shared_run(query: str, texts: Iterable[str]) -> int:
    """Length of the longest token run of ``query`` that occurs verbatim in one of ``texts``."""
    q = norm_tokens(query)
    best = 0
    for text in texts:
        t = norm_tokens(text)
        grams = {n: {tuple(t[i : i + n]) for i in range(len(t) - n + 1)} for n in range(1, min(len(q), 30) + 1)}
        for n in range(best + 1, len(q) + 1):
            if any(tuple(q[i : i + n]) in grams.get(n, set()) for i in range(len(q) - n + 1)):
                best = n
            else:
                break
    return best


def mark(items: Sequence[dict[str, Any]], texts_of: Mapping[str, Sequence[str]]) -> dict[int, int]:
    """Set ``leak`` (shared run) and ``leaky`` (≥ LEAK_MIN) on every French item; returns the histogram."""
    hist: dict[int, int] = {}
    for it in items:
        if it.get("lang") != "fr" or not it.get("targets"):
            continue
        run = longest_shared_run(it["query"], texts_of.get(str(it["targets"][0]["key"]), ()))
        it["leak"] = run
        if run >= LEAK_MIN:
            it["leaky"] = True
        else:
            it.pop("leaky", None)
        bucket = min(run, 6) if run >= 4 else 0
        hist[bucket] = hist.get(bucket, 0) + 1
    return dict(sorted(hist.items()))


_TEXTS = """
SELECT u.eli_unit, u.num_norm, u.heading, u.text_plain, a.eli_work, m.eli_work AS member
  FROM lgx.unit u JOIN lgx.act a ON a.basis_version_id = u.version_id
  LEFT JOIN lgx.act m ON m.act_id = u.member_act_id
 WHERE u.unit_type = 'article'
"""


def mark_file(dsn: str, path: Path) -> tuple[dict[int, int], int]:
    """Mark the G2 file at ``path`` against the article texts of the database (target keys of ``freeze-g2``)."""
    import psycopg
    from psycopg.rows import dict_row

    from eval.harness.gen_g2 import unit_key

    doc = json.loads(path.read_text(encoding="utf-8"))
    texts: dict[str, list[str]] = {}
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        for r in conn.execute(_TEXTS):
            key = unit_key(r["eli_unit"], r["eli_work"], r["num_norm"], r["member"])
            texts.setdefault(key, []).extend([r["text_plain"] or "", r["heading"] or ""])
    hist = mark(doc["items"], texts)
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return hist, sum(1 for it in doc["items"] if it.get("leaky"))
