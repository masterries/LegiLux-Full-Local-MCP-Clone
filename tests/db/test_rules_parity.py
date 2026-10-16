"""Parity of the Python rule twins (lgx_core.rules) with the SQL functions (db/schema/30_functions.sql).

The selection of the committed sample fixture is loaded into a fresh database (acts, versions with their
text windows, SPARQL relations); ``lgx.serving_at`` must equal ``lgx_core.rules.serving.serving_at`` for
every act on T, T+1, T+30, T+90, T+365 and on d-1 / d / d+1 of every stored change point (PLAN §7.3,
§11.5). ``lgx.act_status`` is compared on a grid of dates and raw flags.
"""

from __future__ import annotations

import datetime as dt
import itertools
from collections import defaultdict
from collections.abc import Iterator
from pathlib import Path

import psycopg
import pytest
from psycopg.types.range import Range

from lgx_core.config import ProjectConfig
from lgx_core.models import ManifestEntry
from lgx_core.rules.select import Selection, select
from lgx_core.rules.serving import serving_at, views_from_manifest
from lgx_core.rules.status import act_status
from lgx_ingest.sparql import load_snapshot

from .conftest import PgServer

pytestmark = pytest.mark.db

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "meta" / "snapshot-20261016-sample"
T = dt.date(2026, 10, 16)
CC_BY = "http://creativecommons.org/licenses/by/4.0/"


@pytest.fixture(scope="module")
def sel() -> Selection:
    return select(load_snapshot(FIXTURE), T, ProjectConfig.load())


@pytest.fixture(scope="module")
def db(pg_server: PgServer, sel: Selection) -> Iterator[tuple[psycopg.Connection, dict[str, int]]]:
    pg_server.apply_schema("lgx_test_parity", "--recreate")
    with pg_server.connect("lgx_test_parity") as conn:
        ids: dict[str, int] = {}
        for a in sel.acts:
            row = conn.execute(
                "INSERT INTO lgx.act (eli_work, eli_jo, doc_type, dei_min, dnl, rdf_status, text_basis, "
                "metadata_reason, flags) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING act_id",
                (
                    a.eli_work,
                    a.eli_jo,
                    a.doc_type,
                    a.dei_min,
                    a.dnl,
                    a.rdf_status,
                    a.text_basis.value,
                    a.metadata_reason.value if a.metadata_reason else None,
                    [f.value for f in a.flags],
                ),
            ).fetchone()
            assert row is not None
            ids[a.eli_work] = row[0]
            if a.eli_jo:
                ids[a.eli_jo] = row[0]
        seen: set[str] = set()
        for e in sel.manifest:
            if e.eli_version is None or e.kind is None or e.eli_version in seen:
                continue
            seen.add(e.eli_version)
            window = Range(e.text_valid.lower, e.text_valid.upper, "[)") if e.text_valid else None
            conn.execute(
                "INSERT INTO lgx.act_version (act_id, eli_version, kind, role, date_applicability, "
                "date_end_applicability, has_files, has_text, text_valid, source_url, licence, sha256) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    ids[e.eli_work],
                    e.eli_version,
                    e.kind.value,
                    e.role.value,
                    e.date_applicability,
                    e.date_end_applicability,
                    window is not None,
                    window is not None,
                    window,
                    "https://legilux.public.lu/filestore/eli/etat/leg/x" if window else None,
                    CC_BY if window else None,
                    b"\x01" * 32 if window else None,
                ),
            )
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO lgx.relation (src_eli, src_act_id, rel_type, dst_eli, dst_act_id, src_dei, source) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                [
                    (
                        r.src_eli,
                        ids.get(r.src_eli),
                        r.rel_type.value,
                        r.dst_eli,
                        ids.get(r.dst_eli),
                        r.src_dei,
                        "sparql",
                    )
                    for r in sel.relations
                ],
            )
        yield conn, ids


def _dates(entries: list[ManifestEntry]) -> set[dt.date]:
    days = {T + dt.timedelta(days=n) for n in (0, 1, 30, 90, 365)}
    for e in entries:
        for b in (e.text_valid.lower, e.text_valid.upper) if e.text_valid else ():
            if b is not None:
                days |= {b - dt.timedelta(days=1), b, b + dt.timedelta(days=1)}
        if e.date_applicability:
            days |= {e.date_applicability - dt.timedelta(days=1), e.date_applicability}
    return days


def test_serving_at_parity(db: tuple[psycopg.Connection, dict[str, int]], sel: Selection) -> None:
    conn, ids = db
    entries: dict[str, list[ManifestEntry]] = defaultdict(list)
    for e in sel.manifest:
        entries[e.eli_work].append(e)
    rels = defaultdict(list)
    for r in sel.relations:
        rels[r.dst_eli].append(r)
    by_id = {}
    for row in conn.execute("SELECT version_id, eli_version FROM lgx.act_version"):
        by_id[row[0]] = row[1]
    compared = 0
    for act in sel.acts:
        views = views_from_manifest(entries[act.eli_work])
        for day in sorted(_dates(entries[act.eli_work])):
            py = serving_at(act, views, rels[act.eli_jo or ""], day)
            row = conn.execute(
                "SELECT status, window_version_id, text_version_id, text_basis::text, next_version_date, "
                "next_version_has_text FROM lgx.serving_at(%s, %s)",
                (ids[act.eli_work], day),
            ).fetchone()
            assert row is not None
            sql = (row[0], by_id.get(row[1]), by_id.get(row[2]), row[3], row[4], row[5])
            mine = (
                py.status.value,
                py.window_version,
                py.text_version,
                py.text_basis.value,
                py.next_version_date,
                py.next_version_has_text,
            )
            assert sql == mine, (act.eli_work, day)
            compared += 1
    assert compared > 500


def test_amending_acts_since_parity(db: tuple[psycopg.Connection, dict[str, int]], sel: Selection) -> None:
    from lgx_core.rules.serving import amending_acts_since

    conn, ids = db
    rels = defaultdict(list)
    for r in sel.relations:
        rels[r.dst_eli].append(r)
    for act in sel.acts:
        if not act.eli_jo:
            continue
        since = act.dei_min or dt.date(1800, 1, 1)
        py = [(src, rel.value, d) for src, rel, d in amending_acts_since(act, rels[act.eli_jo], since, T)]
        sql = conn.execute(
            "SELECT src_eli, rel_type, src_dei FROM lgx.amending_acts_since(%s, %s, %s)", (ids[act.eli_work], since, T)
        ).fetchall()
        assert sorted(py) == sorted(sql), act.eli_work


def test_act_status_parity_grid(db: tuple[psycopg.Connection, dict[str, int]]) -> None:
    conn, _ = db
    days = [None, T - dt.timedelta(days=1), T, T + dt.timedelta(days=1)]
    flags = [None, "in-force", "no-longer-in-force", "no-longer-in-force-implicit", "not-yet-in-force"]
    for dei, dnl, rdf in itertools.product(days, days, flags):
        sql = conn.execute("SELECT lgx.act_status(%s, %s, %s, %s)", (dei, dnl, rdf, T)).fetchone()
        assert sql is not None and sql[0] == act_status(dei, dnl, rdf, T).value, (dei, dnl, rdf)
