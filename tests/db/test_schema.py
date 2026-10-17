"""db/schema/*.sql applies from scratch and behaves as PLAN §7 / Appendix B specify.

Also asserts Python ↔ SQL vocabulary parity, so lgx_core.models and the DDL cannot drift apart.
"""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Iterator

import psycopg
import pytest
from psycopg.types.json import Jsonb
from psycopg.types.range import Range

from lgx_core.config import DEFAULT_CONFIG_DIR, LegalTextsConfig, WarningsConfig, load_yaml
from lgx_core.models import (
    ActFlag,
    AliasKind,
    ChangeType,
    EmbeddingProvider,
    MetadataReason,
    RecueilMethod,
    RelationSource,
    RelType,
    Severity,
    SourceFormat,
    TextBasis,
    UnitType,
    VersionKind,
    VersionRole,
    WarningCode,
)

from .conftest import PgServer

pytestmark = pytest.mark.db

T = dt.date(2026, 10, 16)
E = "http://data.legilux.public.lu/eli/etat/leg/"
CC_BY = "http://creativecommons.org/licenses/by/4.0/"


@pytest.fixture(scope="module")
def db(pg_server: PgServer) -> Iterator[psycopg.Connection]:
    """Two fresh databases: proves the schema applies from scratch twice in one cluster."""
    pg_server.apply_schema("lgx_test_a", "--recreate")
    pg_server.apply_schema("lgx_test_b", "--recreate")
    with pg_server.connect("lgx_test_b") as conn:
        yield conn


def scalar(conn: psycopg.Connection, sql: str, params: tuple[object, ...] | None = None) -> object:
    row = conn.execute(sql, params).fetchone()
    assert row is not None
    return row[0]


# ----------------------------------------------------------------------------------- structure
def test_key_objects_exist(db: psycopg.Connection) -> None:
    schemas = {r[0] for r in db.execute("SELECT nspname FROM pg_namespace")}
    assert {"lgx", "lgx_meta", "build"} <= schemas
    tables = {
        f"{r[0]}.{r[1]}"
        for r in db.execute(
            "SELECT schemaname, tablename FROM pg_tables WHERE schemaname IN ('lgx','lgx_meta','build')"
        )
    }
    expected = {
        "lgx_meta.release",
        "lgx.embedding_model",
        "lgx.vocab",
        "lgx.legal_text",
        "lgx.warning_def",
        "lgx.act",
        "lgx.act_alias",
        "lgx.act_version",
        "lgx.unit",
        "lgx.article_alias",
        "lgx.relation",
        "lgx.recueil_member",
        "lgx.chunk",
        "lgx.change_log",
        "lgx.data_quality_issue",
        "build.fetch_log",
        "build.sync_state",
    }
    assert expected <= tables
    functions = {r[0] for r in db.execute("SELECT proname FROM pg_proc WHERE pronamespace = 'lgx'::regnamespace")}
    assert {
        "act_status",
        "version_as_of",
        "text_version_as_of",
        "serving_at",
        "amending_acts_since",
        "acts_as_of",
        "or_query",
        "search_candidates",
        "f_unaccent",
    } <= functions
    cfgs = {r[0] for r in db.execute("SELECT cfgname FROM pg_ts_config WHERE cfgnamespace = 'lgx'::regnamespace")}
    assert cfgs == {"fr_legal", "simple_ua"}
    assert scalar(db, "SELECT to_regclass('lgx_meta.invariant_violations') IS NOT NULL")
    assert scalar(db, "SELECT extversion FROM pg_extension WHERE extname = 'vector'") == "0.8.7"
    assert str(scalar(db, "SHOW server_version")).startswith("18.")


def test_embedding_column_nullable_and_no_hnsw_by_default(db: psycopg.Connection) -> None:
    typ, notnull = db.execute(
        "SELECT format_type(atttypid, atttypmod), attnotnull FROM pg_attribute "
        "WHERE attrelid = 'lgx.chunk'::regclass AND attname = 'embedding'"
    ).fetchone() or (None, None)
    assert typ == "halfvec(1024)" and notnull is False
    assert scalar(db, "SELECT count(*) FROM pg_indexes WHERE indexname LIKE 'chunk_hnsw%'") == 0
    assert scalar(db, "SELECT count(*) FROM lgx.embedding_model") == 0


def test_read_only_role(db: psycopg.Connection) -> None:
    assert scalar(db, "SELECT rolconnlimit FROM pg_roles WHERE rolname = 'lgx_ro'") == 40
    assert scalar(db, "SELECT has_table_privilege('lgx_ro', 'lgx.act', 'SELECT')") is True
    assert scalar(db, "SELECT has_table_privilege('lgx_ro', 'lgx.act', 'INSERT')") is False
    assert scalar(db, "SELECT has_schema_privilege('lgx_ro', 'build', 'USAGE')") is False
    settings = scalar(db, "SELECT array_to_string(rolconfig, ',') FROM pg_roles WHERE rolname = 'lgx_ro'")
    assert "default_transaction_read_only=on" in str(settings)


def test_unaccented_stopwords_do_not_leak(db: psycopg.Connection) -> None:
    tsv = str(
        scalar(
            db,
            "SELECT to_tsvector('lgx.fr_legal', 'Les données à caractère personnel; règlement (UE) 2016/679; "
            "1er août 2018; résiliation')",
        )
    )
    lexemes = set(re.findall(r"'([^']+)'", tsv))
    assert "a" not in lexemes  # the unaccented "à" is a stopword
    assert {"2016/679", "1er", "resili", "donne"} <= lexemes


# --------------------------------------------------------------------------- vocabulary parity
def _enum(db: psycopg.Connection, typ: str) -> set[str]:
    return set(scalar(db, f"SELECT enum_range(NULL::{typ})::text[]"))  # type: ignore[arg-type]


def _check_values(db: psycopg.Connection, table: str, column: str) -> set[str]:
    defs = [
        r[0]
        for r in db.execute(
            "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conrelid = %s::regclass AND contype = 'c'",
            (table,),
        )
    ]
    for d in defs:
        if re.search(rf"\(\(?{column}\b", d) and ("ANY" in d or "<@" in d or f"{column} = '" in d):  # IN (one)
            return set(re.findall(r"'([^']+)'::text", d))
    raise AssertionError(f"no CHECK list on {table}.{column}: {defs}")


@pytest.mark.parametrize(
    ("typ", "enum"),
    [("lgx.text_basis", TextBasis), ("lgx.version_kind", VersionKind), ("lgx.version_role", VersionRole)],
)
def test_enum_parity(db: psycopg.Connection, typ: str, enum: type) -> None:
    assert _enum(db, typ) == {e.value for e in enum}  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    ("table", "column", "values"),
    [
        ("lgx.act", "flags", {e.value for e in ActFlag}),
        ("lgx.act", "metadata_reason", {e.value for e in MetadataReason}),
        ("lgx.act_version", "source_format", {e.value for e in SourceFormat}),
        ("lgx.unit", "unit_type", {e.value for e in UnitType}),
        ("lgx.relation", "rel_type", {e.value for e in RelType}),
        ("lgx.relation", "source", {e.value for e in RelationSource}),
        ("lgx.act_alias", "kind", {e.value for e in AliasKind}),
        ("lgx.change_log", "change_type", {e.value for e in ChangeType}),
        ("lgx.recueil_member", "method", {e.value for e in RecueilMethod}),
        ("lgx.warning_def", "severity", {e.value for e in Severity}),
        ("lgx.embedding_model", "provider", {e.value for e in EmbeddingProvider} - {"none"}),
    ],
)
def test_check_constraint_parity(db: psycopg.Connection, table: str, column: str, values: set[str]) -> None:
    assert _check_values(db, table, column) == values


def test_warning_and_legal_texts_load(db: psycopg.Connection) -> None:
    warnings = WarningsConfig.model_validate(load_yaml(DEFAULT_CONFIG_DIR / "warnings.yaml"))
    texts = LegalTextsConfig.model_validate(load_yaml(DEFAULT_CONFIG_DIR / "legal_texts.yaml"))
    with db.transaction(force_rollback=True):
        for d in warnings.defs().values():
            db.execute(
                "INSERT INTO lgx.warning_def (code, severity, text_fr, text_de, text_en, variants) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                (
                    d.code.value,
                    d.severity.value,
                    d.text_fr,
                    d.text_de,
                    d.text_en,
                    Jsonb({k: {lang.value: t for lang, t in v.items()} for k, v in d.variants.items()}),
                ),
            )
        for code, by_lang in texts.texts.items():
            for lang, body in by_lang.items():
                db.execute(
                    "INSERT INTO lgx.legal_text VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
                    (code, lang.value, body),
                )
        assert scalar(db, "SELECT count(*) FROM lgx.warning_def") == len(WarningCode)
        assert scalar(db, "SELECT variants->'old_fallback'->>'en' FROM lgx.warning_def WHERE code = 'W_STUB'")
        assert scalar(db, "SELECT n FROM lgx_meta.invariant_violations WHERE check_name = 'missing_legal_texts'") == 0


# -------------------------------------------------------------------------- behaviour on fixtures
@pytest.mark.parametrize(
    ("dei", "dnl", "rdf", "as_of", "expected"),
    [
        (dt.date(2000, 1, 1), None, "in-force", T, "in-force"),
        (dt.date(2000, 1, 1), None, "no-longer-in-force-implicit", T, "no-longer-in-force"),
        (dt.date(2000, 1, 1), T, "in-force", T, "no-longer-in-force"),
        (dt.date(2000, 1, 1), T + dt.timedelta(days=1), "in-force", T, "in-force"),
        (dt.date(2000, 1, 1), None, "no-longer-in-force", T, "no-longer-in-force"),
        (T + dt.timedelta(days=4), None, "not-yet-in-force", T, "not-yet-in-force"),
        (T + dt.timedelta(days=4), None, "in-force", T + dt.timedelta(days=4), "in-force"),
    ],
)
def test_act_status(db: psycopg.Connection, dei, dnl, rdf, as_of, expected) -> None:  # type: ignore[no-untyped-def]
    assert scalar(db, "SELECT lgx.act_status(%s, %s, %s, %s)", (dei, dnl, rdf, as_of)) == expected


def _act(db: psycopg.Connection, work: str, doc_type: str, text_basis: str, **kw: object) -> int:
    cols = ["eli_work", "eli_jo", "doc_type", "text_basis", *kw]
    vals = [E + work, E + work + "/jo", doc_type, text_basis, *kw.values()]
    sql = f"INSERT INTO lgx.act ({', '.join(cols)}) VALUES ({', '.join(['%s'] * len(cols))}) RETURNING act_id"
    return int(scalar(db, sql, tuple(vals)))  # type: ignore[arg-type]


def _version(
    db: psycopg.Connection,
    act_id: int,
    eli: str,
    kind: str,
    role: str,
    valid: Range | None,
    da: dt.date | None = None,
    dea: dt.date | None = None,
    fmt: str = "xml",
) -> int:
    return int(
        scalar(  # type: ignore[arg-type]
            db,
            "INSERT INTO lgx.act_version (act_id, eli_version, kind, role, date_applicability, "
            "date_end_applicability, has_files, has_text, text_valid, source_format, source_url, licence, sha256) "
            "VALUES (%s, %s, %s, %s, %s, %s, true, %s, %s, %s, %s, %s, %s) RETURNING version_id",
            (
                act_id,
                E + eli,
                kind,
                role,
                da,
                dea,
                valid is not None,
                valid,
                fmt,
                "https://legilux.public.lu/filestore/eli/etat/leg/x",
                CC_BY,
                b"\x01" * 32,
            ),
        )
    )


def _unit_chunk(
    db: psycopg.Connection,
    act_id: int,
    version_id: int,
    text: str,
    doc_type: str,
    basis: str,
    valid_from: dt.date,
    valid_to: dt.date | None,
) -> int:
    unit_id = scalar(
        db,
        "INSERT INTO lgx.unit (version_id, act_id, unit_type, anchor, num_raw, num_norm, ordinal, depth, text_plain) "
        "VALUES (%s, %s, 'article', 'art_1er', 'Art. 1er.', '1', 1, 1, %s) RETURNING unit_id",
        (version_id, act_id, text),
    )
    return int(
        scalar(  # type: ignore[arg-type]
            db,
            "INSERT INTO lgx.chunk (unit_id, act_id, version_id, header, body, doc_type, text_basis, valid_from, "
            "valid_to, content_sha256) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING chunk_id",
            (
                unit_id,
                act_id,
                version_id,
                "Art. 1er.",
                text,
                doc_type,
                basis,
                valid_from,
                valid_to,
                b"\x02" * 32,
            ),
        )
    )


@pytest.fixture(scope="module")
def loaded(db: psycopg.Connection) -> dict[str, int]:
    """A minimal release: consolidated work with a stored future version, and an amended JO original."""
    ids: dict[str, int] = {}
    db.execute(
        "INSERT INTO lgx_meta.release (release_id, sync_date, built_at, schema_version, rules_version, "
        "parser_version, pipeline_git_sha, inputs_digest, config, counts, gates) "
        "VALUES ('data-20261016', %s, now(), 1, 'r0', 'p0', 'test', 'sha256:test', '{}', '{}', '{}')",
        (T,),
    )
    for code in ("attribution", "disclaimer", "cc_by_4_0", "notice"):
        db.execute("INSERT INTO lgx.legal_text VALUES (%s, 'fr', %s)", (code, code))
    a1 = ids["aifm"] = _act(
        db,
        "loi/2013/07/12/n1",
        "LOI",
        "consolidated",
        dei_min=dt.date(2013, 7, 15),
        rdf_status="in-force",
        title="Loi AIFM",
        title_short="Loi AIFM",
    )
    cur = ids["aifm_cur"] = _version(
        db,
        a1,
        "loi/2013/07/12/n1/consolide/20260416",
        "consolidated",
        "current",
        Range(dt.date(2026, 4, 16), dt.date(2027, 4, 16), "[)"),
        dt.date(2026, 4, 16),
        dt.date(2027, 4, 16),
    )
    fut = ids["aifm_fut"] = _version(
        db,
        a1,
        "loi/2013/07/12/n1/consolide/20270416",
        "consolidated",
        "future",
        Range(dt.date(2027, 4, 16), None, "[)"),
        dt.date(2027, 4, 16),
    )
    db.execute("UPDATE lgx.act SET basis_version_id = %s WHERE act_id = %s", (cur, a1))
    ids["c_cur"] = _unit_chunk(
        db,
        a1,
        cur,
        "Les gestionnaires de fonds d'investissement alternatifs établis.",
        "LOI",
        "consolidated",
        dt.date(2026, 4, 16),
        dt.date(2027, 4, 16),
    )
    ids["c_fut"] = _unit_chunk(
        db,
        a1,
        fut,
        "Les gestionnaires de fonds alternatifs, version 2027.",
        "LOI",
        "consolidated",
        dt.date(2027, 4, 16),
        None,
    )
    a2 = ids["rgd"] = _act(
        db,
        "rgd/2015/06/25/n1",
        "RGD",
        "original_possibly_outdated",
        dei_min=dt.date(2015, 7, 1),
        dnl=dt.date(2026, 12, 1),
        rdf_status="in-force",
        flags=["base_legale_abrogee"],
    )
    jo = ids["rgd_jo"] = _version(
        db,
        a2,
        "rgd/2015/06/25/n1/jo",
        "original",
        "current",
        Range(dt.date(2015, 7, 1), dt.date(2026, 12, 1), "[)"),
        fmt="html",
    )
    db.execute("UPDATE lgx.act SET basis_version_id = %s WHERE act_id = %s", (jo, a2))
    ids["c_rgd"] = _unit_chunk(
        db,
        a2,
        jo,
        "Les données à caractère personnel du règlement (UE) 2016/679.",
        "RGD",
        "original_possibly_outdated",
        dt.date(2015, 7, 1),
        dt.date(2026, 12, 1),
    )
    a3 = ids["amending"] = _act(
        db,
        "rgd/2020/01/10/a5",
        "RGD",
        "original_never_amended",
        dei_min=dt.date(2020, 1, 20),
        rdf_status="in-force",
        flags=["is_amending_act"],
    )
    db.execute(
        "INSERT INTO lgx.relation (src_eli, src_act_id, rel_type, dst_eli, dst_act_id, src_dei, source) "
        "VALUES (%s, %s, 'modifies', %s, %s, %s, 'sparql')",
        (E + "rgd/2020/01/10/a5/jo", a3, E + "rgd/2015/06/25/n1/jo", a2, dt.date(2020, 1, 20)),
    )
    return ids


def _serving(db: psycopg.Connection, act_id: int, as_of: dt.date) -> dict[str, object]:
    cur = db.execute("SELECT * FROM lgx.serving_at(%s, %s)", (act_id, as_of))
    row = cur.fetchone()
    assert row is not None and cur.description is not None
    return dict(zip([c.name for c in cur.description], row, strict=True))


def test_serving_switches_on_dates(db: psycopg.Connection, loaded: dict[str, int]) -> None:
    s = _serving(db, loaded["aifm"], T)
    assert (s["status"], s["text_version_id"], s["text_basis"]) == (
        "in-force",
        loaded["aifm_cur"],
        "consolidated",
    )
    assert s["next_version_date"] == dt.date(2027, 4, 16) and s["next_version_has_text"] is True
    s = _serving(db, loaded["aifm"], dt.date(2027, 4, 16))
    assert (s["window_version_id"], s["text_version_id"], s["text_basis"]) == (
        loaded["aifm_fut"],
        loaded["aifm_fut"],
        "consolidated",
    )
    s = _serving(db, loaded["rgd"], T)
    assert (s["status"], s["text_basis"]) == ("in-force", "original_possibly_outdated")
    assert s["flags"] == ["base_legale_abrogee"]
    s = _serving(db, loaded["rgd"], dt.date(2026, 12, 1))
    assert (s["status"], s["text_version_id"], s["text_basis"]) == (
        "no-longer-in-force",
        None,
        "metadata_only",
    )
    rows = db.execute(
        "SELECT src_eli FROM lgx.amending_acts_since(%s, %s, %s)", (loaded["rgd"], dt.date(2015, 6, 25), T)
    ).fetchall()
    assert rows == [(E + "rgd/2020/01/10/a5/jo",)]


def test_search_candidates_lexical_and_exact(db: psycopg.Connection, loaded: dict[str, int]) -> None:
    def hits(query: str, as_of: dt.date, exact: str | None = None, types: list[str] | None = None) -> list:
        return db.execute(
            "SELECT chunk_id, exact_hit FROM lgx.search_candidates(%s, NULL, %s, '{}', "
            "CASE WHEN %s::text IS NULL THEN NULL ELSE phraseto_tsquery('lgx.simple_ua', %s) END, %s)",
            (query, as_of, exact, exact, types),
        ).fetchall()

    assert [h[0] for h in hits("gestionnaires de fonds alternatifs", T)] == [loaded["c_cur"]]
    assert [h[0] for h in hits("gestionnaires de fonds alternatifs", dt.date(2027, 5, 1))] == [loaded["c_fut"]]
    assert (loaded["c_rgd"], True) in hits("données personnelles", T, "2016/679", ["RGD"])
    # the exact branch honours doc_types (deviation from the Appendix B sketch)
    assert hits("zzz", T, "2016/679", ["LOI"]) == []


def test_exclude_one_text_per_act_and_date(db: psycopg.Connection, loaded: dict[str, int]) -> None:
    with pytest.raises(psycopg.errors.ExclusionViolation), db.transaction():
        _version(
            db,
            loaded["aifm"],
            "loi/2013/07/12/n1/consolide/x-overlap",
            "consolidated",
            "fallback",
            Range(dt.date(2026, 1, 1), dt.date(2026, 6, 1), "[)"),
        )


def test_invariants_clean_and_conditional_hnsw(
    pg_server: PgServer, db: psycopg.Connection, loaded: dict[str, int]
) -> None:
    assert db.execute("SELECT check_name, n FROM lgx_meta.invariant_violations WHERE n <> 0").fetchall() == []
    pg_server.apply_schema("lgx_test_b", "--postload-only")
    assert scalar(db, "SELECT count(*) FROM pg_indexes WHERE indexname LIKE 'chunk_hnsw%'") == 0
    # optional embedding stage: model row (with its startup probe) + vectors -> post-load creates HNSW (idempotently)
    db.execute(
        "INSERT INTO lgx.embedding_model (embedding_set_id, provider, model_id, model_requested, dim, probe_text,"
        " probe_vector, chunk_count) VALUES (1, 'mistral', 'mistral-embed', 'mistral-embed', 1024, 'probe',"
        " (SELECT array_agg(random()::real) FROM generate_series(1, 1024)), 3)"
    )
    with pytest.raises(psycopg.errors.CheckViolation), db.transaction():  # probe length = dim
        db.execute(
            "INSERT INTO lgx.embedding_model (embedding_set_id, provider, model_id, model_requested, dim, probe_text,"
            " probe_vector, chunk_count) VALUES (2, 'mistral', 'm', 'm', 1024, 'p', '{1,2}', 0)"
        )
    db.execute(
        "UPDATE lgx.chunk SET embedding_set_id = 1, "
        "embedding = (SELECT array_agg(random())::real[] FROM generate_series(1, 1024))::halfvec(1024)"
    )
    pg_server.apply_schema("lgx_test_b", "--postload-only")
    pg_server.apply_schema("lgx_test_b", "--postload-only")
    assert scalar(db, "SELECT count(*) FROM pg_indexes WHERE indexname = 'chunk_hnsw'") == 1
    assert db.execute("SELECT check_name, n FROM lgx_meta.invariant_violations WHERE n <> 0").fetchall() == []


def _axis(i: int, dim: int = 1024) -> str:
    return "[" + ",".join("1" if k == i else "0" for k in range(dim)) + "]"


def test_hybrid_rrf_with_fake_vectors(db: psycopg.Connection, loaded: dict[str, int]) -> None:
    """Dense + lexical in one statement (PLAN §8.3): RRF k = 60, the dense branch obeys the validity window."""
    for key, axis in (("c_cur", 0), ("c_fut", 1), ("c_rgd", 2)):
        db.execute("UPDATE lgx.chunk SET embedding = %s::halfvec, embedding_set_id = 1 WHERE chunk_id = %s",
                   (_axis(axis), loaded[key]))  # fmt: skip

    def run(query: str, qvec: str | None, as_of: dt.date = T) -> list[tuple]:
        return db.execute(
            "SELECT chunk_id, score, lex_rank, dense_rank FROM lgx.search_candidates(%s, %s::halfvec, %s, '{}',"
            " NULL, NULL, 10)",
            (query, qvec, as_of),
        ).fetchall()

    lexical = run("gestionnaires de fonds alternatifs", None)
    assert [r[0] for r in lexical] == [loaded["c_cur"]] and lexical[0][3] is None
    hybrid = {r[0]: r for r in run("gestionnaires de fonds alternatifs", _axis(2))}
    # the RGD chunk comes in through the dense branch only (rank 1); the AIFM chunk keeps lex rank 1 + dense rank 2
    assert hybrid[loaded["c_rgd"]][2:] == (None, 1)
    assert hybrid[loaded["c_cur"]][2:] == (1, 2)
    assert loaded["c_fut"] not in hybrid  # a future window is outside the dense branch too
    assert hybrid[loaded["c_cur"]][1] > hybrid[loaded["c_rgd"]][1]  # 1/61 + 1/62 > 1/61
    assert hybrid[loaded["c_rgd"]][1] == pytest.approx(1 / 61, rel=1e-3)
    dense_only = run("zzzz", _axis(0))
    assert dense_only[0][0] == loaded["c_cur"] and dense_only[0][3] == 1
