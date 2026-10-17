"""Freeze G2 (retrieval, PLAN §11.1): resolve every hand-written target against the database and record it.

A target is written either as an article ELI (``{"eli": …}``) or, for articles without one (code-recueil and
recueil members, a few code articles), as ``{"act": <work ELI suffix>, "num": <num_norm>, "member": <member work
ELI suffix>?}``. Freezing checks that exactly one article unit of the version serving at T matches, and stores
the match key the harness compares with the hits (:func:`hit_key`), the act ELI, the article label and the unit's
``text_sha256`` (drift: an item whose units changed is re-validated before it counts, PLAN §11.1).
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import psycopg
from psycopg.rows import dict_row

from lgx_core.eli import resource_uri

LEG = "http://data.legilux.public.lu/eli/etat/leg/"


class FreezeError(ValueError):
    """A G2 target that does not name exactly one served article."""


def unit_key(eli_unit: str | None, act_eli: str, num_norm: str | None, member_eli: str | None) -> str:
    """Article ELI when the unit has one, else ``legilux://eli/<act>/art/<num>|<member act ELI>``."""
    if eli_unit:
        return eli_unit
    return f"{resource_uri(act_eli)}/art/{num_norm or ''}|{member_eli or ''}"


def hit_key(citation: Mapping[str, Any]) -> str | None:
    """The same key computed from a ``search_legislation`` hit's citation object."""
    if citation.get("article_eli"):
        return str(citation["article_eli"])
    uri = citation.get("resource_uri")
    if not uri or "/art/" not in str(uri):
        return None
    return f"{uri}|{citation.get('member_act') or ''}"


_SQL = """
SELECT u.unit_id, u.eli_unit, u.num_raw, u.num_norm, encode(u.text_sha256, 'hex') AS sha, a.eli_work,
       m.eli_work AS member
  FROM lgx.act a JOIN lgx.unit u ON u.version_id = a.basis_version_id AND u.unit_type = 'article'
  LEFT JOIN lgx.act m ON m.act_id = u.member_act_id
 WHERE {where}
"""


def _resolve(conn: psycopg.Connection[dict[str, Any]], t: Mapping[str, Any]) -> dict[str, Any]:
    if t.get("eli"):
        rows = conn.execute(_SQL.format(where="u.eli_unit = %s"), [t["eli"]]).fetchall()
    else:
        where = "a.eli_work = %s AND u.num_norm = %s"
        params: list[Any] = [LEG + str(t["act"]), str(t["num"])]
        if t.get("member"):
            where += " AND m.eli_work = %s"
            params.append(LEG + str(t["member"]))
        rows = conn.execute(_SQL.format(where=where), params).fetchall()
    if len(rows) != 1:
        raise FreezeError(f"target {dict(t)} matches {len(rows)} served articles")
    r = rows[0]
    return {
        **{k: v for k, v in t.items() if k in ("eli", "act", "num", "member")},
        "grade": int(t.get("grade", 2)),
        "key": unit_key(r["eli_unit"], r["eli_work"], r["num_norm"], r["member"]),
        "act_eli": r["eli_work"],
        "article": r["num_raw"],
        "text_sha256": r["sha"],
    }


def freeze(dsn: str, path: Path) -> int:
    doc = json.loads(path.read_text(encoding="utf-8"))
    n = 0
    with psycopg.connect(dsn, row_factory=dict_row, autocommit=True) as conn:
        doc["release"] = conn.execute("SELECT release_id FROM lgx_meta.release").fetchone()["release_id"]  # type: ignore[index]
        for item in doc["items"]:
            item["targets"] = [_resolve(conn, t) for t in item["targets"]]
            n += len(item["targets"])
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return n
