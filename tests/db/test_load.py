"""lgx load / gates / ingest / package against a real PostgreSQL (marked db).

* the synthetic tiny release (``tests/unit/load_fixture.py``): load, gates, idempotency per release name,
  runtime functions on the loaded rows, a constraint violation reported with table/constraint/record;
* the real offline pipeline on committed fixtures: fixture SPARQL snapshot → select → (cache) → parse →
  chunk → load → gates → report → package (pg_dump) → restore + rename-swap, with no network access.
"""

from __future__ import annotations

import datetime as dt
import json
from collections.abc import Iterator
from pathlib import Path

import psycopg
import pytest
from unit.load_fixture import A1, A2_JO, A3, V1, V1_FUT, T, build_fixture_sample, build_tiny_release

from lgx_core.models import sha256_hex
from lgx_ingest.cli_ingest import run_ingest
from lgx_ingest.context import StageContext
from lgx_ingest.gates import run_gates
from lgx_ingest.load import run_load

from .conftest import PgServer

pytestmark = pytest.mark.db


def _track(server: PgServer, *names: str) -> None:
    server.databases.extend(names)


def _one(conn: psycopg.Connection, sql: str, params: object = None) -> object:
    row = conn.execute(sql, params).fetchone()  # type: ignore[arg-type]
    assert row is not None
    return row[0]


# ------------------------------------------------------------------------------- synthetic release
@pytest.fixture(scope="module")
def tiny_db(pg_server: PgServer, tmp_path_factory: pytest.TempPathFactory) -> Iterator[tuple[StageContext, str]]:
    root = tmp_path_factory.mktemp("tiny")
    build_tiny_release(root)
    ctx = StageContext.create(sync_date=T, data_dir=root, sample=False)
    _track(pg_server, "lgx_test_load_tiny")
    result = run_load(ctx, dsn=pg_server.dsn("postgres"), db_name="lgx_test_load_tiny")
    assert result.ok, result.messages
    yield ctx, "lgx_test_load_tiny"


def test_tiny_release_counts_and_relabel(pg_server: PgServer, tiny_db: tuple[StageContext, str]) -> None:
    _, db = tiny_db
    with pg_server.connect(db) as conn:
        assert _one(conn, "SELECT count(*) FROM lgx.act") == 4
        assert _one(conn, "SELECT count(*) FROM lgx.act_version WHERE has_text") == 3
        assert conn.execute(
            "SELECT text_basis::text, metadata_reason FROM lgx.act WHERE eli_work = %s", (A3,)
        ).fetchone() == (
            "metadata_only",
            "not_loaded",
        )
        assert _one(conn, "SELECT count(*) FROM lgx.embedding_model") == 0
        assert _one(conn, "SELECT count(*) FROM lgx.chunk WHERE embedding IS NOT NULL") == 0
        assert conn.execute("SELECT check_name, n FROM lgx_meta.invariant_violations WHERE n <> 0").fetchall() == []
        assert _one(conn, "SELECT count(*) FROM lgx.relation WHERE src_eli LIKE '%loi/2007/01/15/n2%'") == 0
        gates = _one(conn, "SELECT gates->'loader'->>'ok' FROM lgx_meta.release")
        assert gates == "true"
        # identity sequences continue after the pre-assigned ids (COPY wrote explicit ids)
        top = _one(conn, "SELECT max(relation_id) FROM lgx.relation")
        with conn.transaction(force_rollback=True):
            new_id = _one(
                conn,
                "INSERT INTO lgx.relation (src_eli, rel_type, dst_eli, source) "
                "VALUES ('urn:a', 'cites', 'urn:b', 'sparql') RETURNING relation_id",
            )
        assert isinstance(top, int) and isinstance(new_id, int) and new_id > top


def test_tiny_release_runtime_functions(pg_server: PgServer, tiny_db: tuple[StageContext, str]) -> None:
    _, db = tiny_db
    with pg_server.connect(db) as conn:
        a1 = _one(conn, "SELECT act_id FROM lgx.act WHERE eli_work = %s", (A1,))
        served = {
            day: conn.execute(
                "SELECT v.eli_version, s.text_basis::text FROM lgx.serving_at(%s, %s) s "
                "JOIN lgx.act_version v ON v.version_id = s.text_version_id",
                (a1, day),
            ).fetchone()
            for day in (T, dt.date(2027, 1, 2))
        }
        assert served[T] == (V1, "consolidated") and served[dt.date(2027, 1, 2)] == (V1_FUT, "consolidated")
        amending = conn.execute(
            "SELECT src_eli FROM lgx.amending_acts_since(%s, '2020-01-01', %s) ORDER BY 1", (a1, T)
        ).fetchall()
        assert (A2_JO,) in amending
        art = conn.execute(
            "SELECT r.dst_unit_id = u.unit_id FROM lgx.relation r JOIN lgx.unit u ON u.unit_id = r.dst_unit_id "
            "WHERE r.source = 'akn-textualmod' AND u.anchor = 'art_1er'"
        ).fetchall()
        assert art == [(True,)]
        hits = conn.execute(
            "SELECT count(*) FROM lgx.search_candidates('contrôle des véhicules', NULL, %s, '{}', NULL, NULL)", (T,)
        ).fetchone()
        assert hits is not None and hits[0] >= 1
        exact = conn.execute(
            "SELECT count(*) FILTER (WHERE exact_hit) FROM lgx.search_candidates('données', NULL, %s, '{}', "
            "phraseto_tsquery('lgx.simple_ua', '2016/679'), ARRAY['LOI'])",
            (T,),
        ).fetchone()
        assert exact == (1,)
        # in_force_only / act filter (optional trailing parameters)
        future = conn.execute(
            "SELECT count(*) FROM lgx.search_candidates('essais contrôle', NULL, %s, '{}', NULL, NULL, 40, %s, %s)",
            (T, [a1], False),
        ).fetchone()
        current = conn.execute(
            "SELECT count(*) FROM lgx.search_candidates('essais contrôle', NULL, %s, '{}', NULL, NULL, 40, %s)",
            (T, [a1]),
        ).fetchone()
        assert future is not None and current is not None and future[0] > current[0]


def test_tiny_release_gates_pass_and_reload_is_idempotent(
    pg_server: PgServer, tiny_db: tuple[StageContext, str]
) -> None:
    ctx, db = tiny_db
    gates = run_gates(ctx, dsn=pg_server.dsn("postgres"), db_name=db)
    failing = [g for g in gates.details["gates"] if g["status"] == "fail"]
    assert gates.ok, failing
    with pg_server.connect(db) as conn:
        before = conn.execute("SELECT content_digest FROM lgx_meta.release").fetchone()
        ids = conn.execute("SELECT eli_work, act_id FROM lgx.act ORDER BY 1").fetchall()
        assert _one(conn, "SELECT gates->>'status' FROM lgx_meta.release") == "pass"
    again = run_load(ctx, dsn=pg_server.dsn("postgres"), db_name=db)
    assert again.ok, again.messages
    with pg_server.connect(db) as conn:
        assert conn.execute("SELECT content_digest FROM lgx_meta.release").fetchone() == before
        assert conn.execute("SELECT eli_work, act_id FROM lgx.act ORDER BY 1").fetchall() == ids


def test_constraint_violation_names_table_constraint_and_record(pg_server: PgServer, tmp_path: Path) -> None:
    build_tiny_release(tmp_path)
    manifest = tmp_path / "manifest" / T.isoformat() / "manifest.jsonl"
    lines = []
    for line in manifest.read_text(encoding="utf-8").splitlines():
        rec = json.loads(line)
        if rec["eli_version"] == V1_FUT:
            rec["text_valid"] = {"lower": "2026-01-01", "upper": None}  # overlaps the current window
        lines.append(json.dumps(rec))
    manifest.write_text("\n".join(lines) + "\n", encoding="utf-8")
    ctx = StageContext.create(sync_date=T, data_dir=tmp_path, sample=False)
    _track(pg_server, "lgx_test_load_bad")
    result = run_load(ctx, dsn=pg_server.dsn("postgres"), db_name="lgx_test_load_bad")
    assert not result.ok
    message = result.messages[0]
    assert "lgx.act_version" in message and "one_text_per_act_and_date" in message and V1_FUT in message


# ------------------------------------------------------------------- end-to-end offline from fixtures
def test_offline_ingest_end_to_end_with_package_and_restore(
    pg_server: PgServer, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import httpx

    def no_network(*args: object, **kwargs: object) -> None:
        raise AssertionError("the offline ingest made a network request")

    monkeypatch.setattr(httpx.Client, "send", no_network)
    cfg_dir, data = build_fixture_sample(tmp_path)
    ctx = StageContext.create(sync_date=T, config_dir=cfg_dir, data_dir=data, sample=True)
    db, rt = "lgx_test_e2e", "lgx_test_e2e_rt"
    _track(pg_server, db, rt, f"{rt}_next", f"{rt}_prev")
    result = run_ingest(
        ctx,
        offline=True,
        load=True,
        dsn=pg_server.dsn("postgres"),
        db_name=db,
        package=pg_server.container is not None,
        container=pg_server.container,
        restore_to=rt if pg_server.container else None,
        echo=lambda _m: None,
    )
    steps = {s["stage"]: s for s in result.details["steps"]}
    assert result.ok, (result.messages, steps)
    assert steps["fetch"]["counts"] == {"targets": 5, "cached": 5, "missing": 0}
    assert steps["parse"]["counts"]["versions"] == 5
    gates = json.loads((data / "reports" / T.isoformat() / "gates.json").read_text(encoding="utf-8"))
    by_name = {g["gate"]: g for g in gates["gates"]}
    assert gates["status"] == "pass"
    for name in ("invariants", "denylist_absent", "core_texts_present", "sample_texts_loaded", "text_basis_consistent"):
        assert by_name[name]["status"] == "pass", by_name[name]
    with pg_server.connect(db) as conn:
        constitution = conn.execute(
            "SELECT a.text_basis::text, count(u.*) FROM lgx.act a JOIN lgx.unit u ON u.version_id = a.basis_version_id "
            "WHERE a.doc_type = 'Constitution' AND u.unit_type = 'article' GROUP BY 1"
        ).fetchone()
        assert constitution is not None and constitution[0] == "consolidated" and constitution[1] > 100
        art56 = conn.execute(
            "SELECT u.is_repealed, count(c.*) FROM lgx.unit u JOIN lgx.act a ON a.act_id = u.act_id "
            "LEFT JOIN lgx.chunk c ON c.unit_id = u.unit_id WHERE a.eli_work LIKE '%/loi/2018/08/01/a686' "
            "AND u.num_norm = '56' AND u.unit_type = 'article' GROUP BY 1"
        ).fetchone()
        assert art56 == (True, 0)
        top = conn.execute(
            "SELECT a.eli_work FROM lgx.search_candidates('protection des données à caractère personnel', NULL, %s, "
            "'{}', NULL, NULL, 5) s JOIN lgx.chunk c USING (chunk_id) JOIN lgx.act a ON a.act_id = c.act_id",
            (T,),
        ).fetchall()
        assert top and top[0][0].endswith("/loi/2018/08/01/a686")
        relabelled = _one(conn, "SELECT count(*) FROM lgx.act WHERE metadata_reason = 'not_loaded'")
        assert isinstance(relabelled, int) and relabelled > 0
    if pg_server.container is None:
        return
    package = steps["package"]
    assert package["ok"] and package["counts"]["dump_bytes"] > 0
    bundle = data / "bundles" / "data-2026-10-16-sample.dump"
    release = json.loads(bundle.with_suffix(".release.json").read_text(encoding="utf-8"))
    assert release["release_id"] == "data-20261016-sample" and release["requires"]["embedding"] is None
    assert release["dump"]["sha256"] == sha256_hex(bundle.read_bytes())
    with pg_server.connect(rt) as conn:
        assert _one(conn, "SHOW default_transaction_read_only") == "on"
        assert _one(conn, "SELECT count(*) FROM pg_namespace WHERE nspname = 'build'") == 0
        assert _one(conn, "SELECT has_table_privilege('lgx_ro', 'lgx.chunk', 'SELECT')") is True
        with pg_server.connect(db) as src:
            assert _one(conn, "SELECT count(*) FROM lgx.chunk") == _one(src, "SELECT count(*) FROM lgx.chunk")
