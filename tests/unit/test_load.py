"""Loader transforms (lgx_ingest.load) without a database: psql rendering, ELI resolution, the load plan."""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pytest
from psycopg.types.range import Range

from lgx_core.config import ProjectConfig
from lgx_core.models import DateWindow, Lang
from lgx_ingest.context import StageContext
from lgx_ingest.load import (
    SCHEMA_DIR,
    Denylist,
    EliIndex,
    LoadError,
    anchor_key,
    build_plan,
    clean_title,
    content_digest,
    date_range,
    default_db_name,
    fill_t,
    hex_bytes,
    legal_text_rows,
    maintenance_dsn,
    read_inputs,
    release_id_for,
    render_psql,
    target_dsn,
)
from unit.load_fixture import A1, A2, A2_JO, A3, A4, DENIED, V1, V1_FUT, E, T, build_tiny_release

BUILT = dt.datetime(2026, 10, 16, 20, 0, tzinfo=dt.UTC)


# ------------------------------------------------------------------------------------------ helpers
def test_names_and_dsn() -> None:
    assert release_id_for(T, True) == "data-20261016-sample" and release_id_for(T, False) == "data-20261016"
    assert default_db_name(T, True) == "legilux_build_20261016_sample"
    dsn = "postgresql://postgres:pw@127.0.0.1:55432/postgres"
    assert "dbname=legilux" in target_dsn(dsn, "legilux")
    assert "dbname=postgres" in maintenance_dsn("postgresql://u@h/legilux_x", "legilux_x")
    assert "dbname=admin" in maintenance_dsn("postgresql://u@h/admin", "legilux_x")


def test_render_psql_subset() -> None:
    sql = (
        "\\set ON_ERROR_STOP on\n\\if :{?embed_dim}\n\\else\n  \\set embed_dim 8\n\\endif\n"
        "SELECT 'a'::text, halfvec(:embed_dim), :'name';\n"
    )
    out = render_psql(sql, {"name": "o'k"})
    assert "\\" not in out
    assert "halfvec(8)" in out and "'a'::text" in out and "'o''k'" in out
    assert "halfvec(1024)" in render_psql(sql, {"embed_dim": "1024", "name": "x"})
    with pytest.raises(LoadError, match="unsupported psql meta-command"):
        render_psql("\\copy x from y\n", {})
    with pytest.raises(LoadError, match="unterminated"):
        render_psql("\\if true\nSELECT 1;\n", {})


def test_render_real_schema_parts() -> None:
    tables = render_psql((SCHEMA_DIR / "20_tables.sql").read_text(encoding="utf-8"), {"embed_dim": "16"})
    assert "halfvec(16)" in tables and ":embed_dim" not in tables
    for part in sorted(SCHEMA_DIR.glob("[0-9][0-9]_*.sql")):
        rendered = render_psql(part.read_text(encoding="utf-8"), {"embed_dim": "1024"})
        assert not any(line.lstrip().startswith("\\") for line in rendered.splitlines()), part.name


def test_denylist_prefix_semantics() -> None:
    d = Denylist((DENIED,))
    assert d.matches(DENIED) and d.matches(DENIED + "/jo") and d.matches(DENIED + "/jo#art_1")
    assert not d.matches(DENIED + "0") and not d.matches(None) and not d.matches(E + "loi/2007/01/15/n20")


def test_eli_index_resolution() -> None:
    idx = EliIndex(
        {E + "loi/2006/07/31/n2": 1, E + "loi/2018/08/01/a686": 2},
        {E + "loi/2018/08/01/a686/jo": 2},
        {E + "code/travail/20260726": 1, E + "loi/2018/08/01/a686/consolide/20251226": 2},
    )
    assert idx.act_of(E + "loi/2018/08/01/a686/jo") == 2
    assert idx.act_of(E + "loi/2018/08/01/a686/art_49") == 2
    assert idx.act_of(E + "loi/2018/08/01/a686/consolide/20251226") == 2
    assert idx.act_of(E + "code/travail/art_l_121-1") == 1  # code family -> base act
    assert idx.act_of(E + "code/travail") == 1
    assert idx.act_of("http://data.legilux.public.lu/eli/reg_ue/2016/679/jo") is None
    assert idx.act_of(E + "loi/2018/08/01/a689/jo") is None
    assert idx.act_of(None) is None and idx.act_of("http://example.org/x") is None


def test_small_transforms() -> None:
    assert anchor_key("art_l234-51") == anchor_key("art_l_234-51") == anchor_key("#Art_L.234-51")
    assert fill_t("arrêtée au {T}", T, Lang.FR) == "arrêtée au 16/10/2026"
    assert fill_t("Stand {T}", T, Lang.DE) == "Stand 16.10.2026" and fill_t("{T}", T, Lang.EN) == "2026-10-16"
    assert date_range(None) is None
    r = date_range(DateWindow(lower=T))
    assert isinstance(r, Range) and r.lower == T and r.upper is None and r.bounds == "[)"
    assert hex_bytes("00ff") == b"\x00\xff" and hex_bytes(None) is None
    assert clean_title("soutirés,\\net les fruits.") == "soutirés, et les fruits." and clean_title(None) is None


def test_legal_texts_fill_t_and_vector_variant() -> None:
    cfg = ProjectConfig.load()
    rows = {(c, lang): body for c, lang, body in legal_text_rows(cfg, T, with_vectors=False)}
    assert {"attribution", "disclaimer", "cc_by_4_0", "notice"} <= {c for c, _ in rows}
    assert ("attribution_vectors", "fr") not in rows
    assert "{T}" not in rows[("attribution", "fr")] and "16/10/2026" in rows[("attribution", "fr")]
    assert "vectorisation" not in rows[("attribution", "fr")]
    with_vec = {(c, lang): body for c, lang, body in legal_text_rows(cfg, T, with_vectors=True)}
    assert "vectorisation" in with_vec[("attribution", "fr")]


# ------------------------------------------------------------------------------------- inputs + plan
@pytest.fixture
def tiny(tmp_path: Path) -> tuple[StageContext, dict[str, object]]:
    data = build_tiny_release(tmp_path)
    ctx = StageContext.create(sync_date=T, data_dir=tmp_path, sample=False)
    return ctx, data


def _plan(ctx: StageContext):  # type: ignore[no-untyped-def]
    return build_plan(read_inputs(ctx), ctx.config, release_id="data-20261016", built_at=BUILT, pipeline_git_sha="t")


def _rows(plan, table: str) -> list[dict[str, object]]:  # type: ignore[no-untyped-def]
    tb = plan.table(table)
    return [dict(zip(tb.columns, r, strict=True)) for r in tb.rows]


def test_read_inputs_finds_texts_and_gaps(tiny) -> None:  # type: ignore[no-untyped-def]
    ctx, _ = tiny
    inputs = read_inputs(ctx)
    assert set(inputs.texts) == {V1, V1_FUT, A2_JO}
    assert [m["reason"] for m in inputs.missing] == ["not_fetched"]
    assert inputs.denylist.matches(DENIED + "/jo")
    assert inputs.texts[A2_JO].parser_version == "html-1"


def test_plan_ids_relabel_and_versions(tiny) -> None:  # type: ignore[no-untyped-def]
    ctx, _ = tiny
    plan = _plan(ctx)
    acts = {r["eli_work"]: r for r in _rows(plan, "lgx.act")}
    assert [r["act_id"] for r in sorted(acts.values(), key=lambda r: r["eli_work"])] == [1, 2, 3, 4]
    assert acts[A3]["text_basis"] == "metadata_only" and acts[A3]["metadata_reason"] == "not_loaded"
    assert acts[A3]["basis_version_id"] is None and plan.relabelled == {A3: "consolidated"}
    assert acts[A4]["metadata_reason"] == "repealed"
    assert acts[A1]["title"] == "Loi du 1er janvier 2020 relative aux essais, et aux contrôles."
    versions = {r["eli_version"]: r for r in _rows(plan, "lgx.act_version")}
    assert acts[A1]["basis_version_id"] == versions[V1]["version_id"]
    assert versions[V1]["has_text"] and versions[V1]["text_valid"] == Range(
        dt.date(2025, 1, 1), dt.date(2027, 1, 1), "[)"
    )
    assert versions[V1]["sha256"] is not None and versions[V1]["parser_version"] == "akn-1"
    assert versions[V1]["source_url"].startswith("https://legilux.public.lu/filestore/eli/etat/leg/")
    assert not versions[A3 + "/consolide/20240101"]["has_text"]
    assert plan.counts["not_loaded_by_planned_text_basis"] == {"consolidated": 1}
    assert plan.counts["by_text_basis"] == {"consolidated": 1, "metadata_only": 2, "original_never_amended": 1}


def test_plan_units_aliases_chunks(tiny) -> None:  # type: ignore[no-untyped-def]
    ctx, _ = tiny
    plan = _plan(ctx)
    units = _rows(plan, "lgx.unit")
    by_key = {(u["version_id"], u["ordinal"]): u for u in units}
    versions = {r["eli_version"]: r["version_id"] for r in _rows(plan, "lgx.act_version")}
    title, art1 = by_key[(versions[V1], 1)], by_key[(versions[V1], 2)]
    assert art1["parent_id"] == title["unit_id"] and title["parent_id"] is None
    assert by_key[(versions[V1], 3)]["is_repealed"]
    aliases = {(a["num_norm"], a["unit_id"]) for a in _rows(plan, "lgx.article_alias")}
    art9 = by_key[(versions[V1], 4)]["unit_id"]
    assert {("9", art9), ("10", art9), ("9_10", art9)} <= aliases
    chunks = _rows(plan, "lgx.chunk")
    assert len(chunks) == 4  # V1: art 1 + art 9/10; future: art 1; RGD: art 1 (no chunk for the repealed art 2)
    assert {c["unit_id"] for c in chunks} <= {u["unit_id"] for u in units}
    assert all(c["valid_from"] for c in chunks)


def test_plan_relations_resolved_and_filtered(tiny) -> None:  # type: ignore[no-untyped-def]
    ctx, _ = tiny
    plan = _plan(ctx)
    rels = _rows(plan, "lgx.relation")
    acts = {r["eli_work"]: r["act_id"] for r in _rows(plan, "lgx.act")}
    assert not any(DENIED in r["src_eli"] for r in rels)
    assert plan.counts["relations"]["dropped_denylist"] == 1
    sparql = [r for r in rels if r["source"] == "sparql"]
    mod = next(r for r in sparql if r["src_eli"] == A2_JO)
    assert (mod["src_act_id"], mod["dst_act_id"]) == (acts[A2], acts[A1])
    eu = next(r for r in sparql if "reg_ue" in r["dst_eli"])
    assert eu["dst_act_id"] is None and eu["src_act_id"] == acts[A3]
    tm = [r for r in rels if r["source"] == "akn-textualmod"]
    assert len(tm) == 1 and tm[0]["dst_unit_id"] is not None and tm[0]["dst_unit_hint"] == "art_1er"
    assert tm[0]["src_dei"] == dt.date(2021, 2, 10)  # filled from the modifying act
    refs = [r for r in rels if r["source"] == "akn-ref"]
    assert all(r["src_unit_id"] is not None for r in refs)
    art_ref = next(r for r in refs if r["dst_eli"].endswith("/art_1"))
    assert art_ref["dst_unit_id"] is not None and art_ref["dst_act_id"] == acts[A1]
    assert [r["relation_id"] for r in rels] == list(range(1, len(rels) + 1))


def test_plan_catalogues_and_release_row(tiny) -> None:  # type: ignore[no-untyped-def]
    ctx, _ = tiny
    plan = _plan(ctx)
    assert len(plan.table("lgx.warning_def")) == 22
    dq = _rows(plan, "lgx.data_quality_issue")
    assert [d["entity_eli"] for d in dq] == [A3]  # the denylisted issue is dropped
    release = _rows(plan, "lgx_meta.release")[0]
    assert release["release_id"] == "data-20261016" and release["sync_date"] == T
    config = release["config"].obj  # type: ignore[attr-defined]
    assert config["vector_status"] == "disabled:provider_none" and config["release_kind"] == "full"
    assert release["content_digest"] == plan.content_digest and plan.content_digest.startswith("sha256:")
    assert not plan.table("build.fetch_log").shipped


def test_plan_is_deterministic_and_digest_tracks_content(tiny, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    ctx, _ = tiny
    first, second = _plan(ctx), _plan(ctx)
    assert first.content_digest == second.content_digest
    assert first.table("lgx.relation").rows == second.table("lgx.relation").rows
    units_path = ctx.layout.units_path(A2_JO)
    lines = units_path.read_text(encoding="utf-8").splitlines()
    rec = json.loads(lines[-1])
    rec["text_plain"] = "Texte changé."
    rec["text_sha256"] = None
    units_path.write_text("\n".join([*lines[:-1], json.dumps(rec)]) + "\n", encoding="utf-8")
    assert _plan(ctx).content_digest != first.content_digest
    assert content_digest([]) == content_digest([])


def test_stale_or_incomplete_inputs_fail_loudly(tiny) -> None:  # type: ignore[no-untyped-def]
    ctx, _ = tiny
    units_path = ctx.layout.units_path(V1)
    lines = units_path.read_text(encoding="utf-8").splitlines()
    rec = json.loads(lines[0])
    rec["source_sha256"] = "0" * 64
    units_path.write_text("\n".join([json.dumps(rec), *lines[1:]]) + "\n", encoding="utf-8")
    with pytest.raises(LoadError, match="re-run `lgx parse`"):
        read_inputs(ctx)
    units_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    ctx.layout.chunks_path(V1).unlink()
    with pytest.raises(LoadError, match="run `lgx chunk`"):
        read_inputs(ctx)


def test_missing_parent_or_unit_is_an_error(tiny) -> None:  # type: ignore[no-untyped-def]
    ctx, _ = tiny
    inputs = read_inputs(ctx)
    text = inputs.texts[V1]
    text.units = [u for u in text.units if u.ordinal != 1]  # drop the title: articles lose their parent
    with pytest.raises(LoadError, match="parent ordinal 1 not found"):
        build_plan(inputs, ctx.config, release_id="r", built_at=BUILT, pipeline_git_sha="t")
    inputs = read_inputs(ctx)
    inputs.texts[V1].units = inputs.texts[V1].units[:2]
    with pytest.raises(LoadError, match="chunk without unit"):
        build_plan(inputs, ctx.config, release_id="r", built_at=BUILT, pipeline_git_sha="t")


def test_denylisted_act_in_selection_is_refused(tiny) -> None:  # type: ignore[no-untyped-def]
    ctx, _ = tiny
    inputs = read_inputs(ctx)
    inputs.acts[0] = inputs.acts[0].model_copy(update={"eli_work": DENIED, "eli_jo": DENIED + "/jo"})
    with pytest.raises(LoadError, match="denylisted act"):
        build_plan(inputs, ctx.config, release_id="r", built_at=BUILT, pipeline_git_sha="t")
