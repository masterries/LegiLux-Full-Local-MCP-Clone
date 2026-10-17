"""Loader on a PDF-only code-recueil (lgx_ingest.load, PLAN §2.6 / §11.4): the real offline pipeline
(select → parse → chunk → plan) on the committed Code de la route excerpt — page ranges, member ELIs,
``recueil_member`` heading rows, the PDF counts of the release and the superseded_by_code routing."""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any

import httpx
import pytest
import yaml

from lgx_core.config import REPO_ROOT
from lgx_core.models import RawFile, sha256_hex
from lgx_ingest.chunk import run_chunk
from lgx_ingest.context import StageContext
from lgx_ingest.load import build_plan, read_inputs
from lgx_ingest.parse import pdf as P
from lgx_ingest.parse import run_parse
from lgx_ingest.select import run_select
from unit.load_fixture import T, build_fixture_sample
from unit.test_pdf_parse import CATALOGUE, PDF_DIR

E = "http://data.legilux.public.lu/eli/etat/leg/"
ROUTE, ROUTE_V = E + "code/route", E + "code/route/20260612"
AGD, LOI = E + "agd/1955/11/23/n1", E + "loi/1955/02/14/n3"
BUILT = dt.datetime(2026, 10, 16, 20, 0, tzinfo=dt.UTC)


@pytest.fixture(scope="module")
def route_plan(tmp_path_factory: pytest.TempPathFactory) -> Any:
    mp = pytest.MonkeyPatch()

    def refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError("network request during an offline test")

    mp.setattr(httpx.Client, "send", refuse)
    try:
        yield _build(tmp_path_factory.mktemp("route"))
    finally:
        mp.undo()


def _build(tmp: Path) -> Any:
    cfg, data = build_fixture_sample(tmp, {"etat/leg/constitution/1868/10/17/n1": "constitution"})
    sample = yaml.safe_load((cfg / "sample.yaml").read_text(encoding="utf-8"))
    full = yaml.safe_load((REPO_ROOT / "config" / "sample.yaml").read_text(encoding="utf-8"))
    sample["works"] += [w for w in full["works"] if w["work"] == "etat/leg/code/route"]
    (cfg / "sample.yaml").write_text(yaml.safe_dump(sample, allow_unicode=True, sort_keys=False), encoding="utf-8")
    meta = CATALOGUE["excerpts"]["route"]  # the 8-page excerpt stands in for the full 35.5 MB PDF
    payload = (PDF_DIR.parent / meta["path"]).read_bytes()
    sha = sha256_hex(payload)
    target = data / "raw" / sha[:2] / f"{sha}.pdf"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(payload)
    raw = RawFile(
        url=meta["url"],
        sha256=sha,
        bytes=len(payload),
        path=f"{sha[:2]}/{sha}.pdf",
        fetched_at=dt.datetime(2026, 10, 16, 18, 0, tzinfo=dt.UTC),
        eli_version=ROUTE_V,
        source_format="pdf",
    )
    with (data / "raw" / "index.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(raw.model_dump_json() + "\n")
    ctx = StageContext.create(
        sync_date=T, config_dir=cfg, data_dir=data, sample=True, options={"include_m2": "1", "pdf_workers": "1"}
    )
    for stage in (run_select, run_parse, run_chunk):
        res = stage(ctx)
        assert res.ok, (stage.__name__, res.messages)
    plan = build_plan(read_inputs(ctx), ctx.config, release_id="data-20261016", built_at=BUILT, pipeline_git_sha="t")
    return ctx, plan


def _rows(plan: Any, table: str) -> list[dict[str, Any]]:
    tb = plan.table(table)
    return [dict(zip(tb.columns, r, strict=True)) for r in tb.rows]


def test_pdf_version_is_loaded_with_its_parser_and_counts(route_plan: Any) -> None:
    _ctx, plan = route_plan
    versions = {r["eli_version"]: r for r in _rows(plan, "lgx.act_version")}
    v = versions[ROUTE_V]
    assert v["has_text"] and v["parser_version"] == P.PARSER_VERSION and v["source_url"].endswith("-fr-pdf.pdf")
    pdf = plan.counts["pdf"][ROUTE_V]
    assert pdf["articles"] == pdf["article_candidates"] and pdf["pages"] == 8
    assert pdf["members"] >= 2 and pdf["member_parts_resolved"] >= 2
    # PDF code-recueils: member-copy dedup (M2 review); none of the excerpt's members has a stored consolidation
    dedup = plan.counts["recueil_dedup"].get(ROUTE_V)
    assert dedup is None or dedup["dropped"] == 0


def test_pdf_units_carry_pages_flag_and_member_acts(route_plan: Any) -> None:
    _ctx, plan = route_plan
    acts = {r["eli_work"]: r["act_id"] for r in _rows(plan, "lgx.act")}
    vid = next(r["version_id"] for r in _rows(plan, "lgx.act_version") if r["eli_version"] == ROUTE_V)
    units = [u for u in _rows(plan, "lgx.unit") if u["version_id"] == vid]
    assert units and all(u["page_from"] is not None and u["page_from"] <= u["page_to"] for u in units)
    by_anchor = {u["anchor"]: u for u in units}
    art1 = by_anchor["loi_1955-02-14.art_1"]
    assert art1["member_act_id"] == acts[LOI] and art1["unit_type"] == "article"
    assert by_anchor["agd_1955-11-23"]["member_act_id"] == acts[AGD]
    chunks = [c for c in _rows(plan, "lgx.chunk") if c["unit_id"] in {u["unit_id"] for u in units}]
    assert chunks and len(chunks) <= sum(u["unit_type"] == "article" for u in units)


def test_recueil_member_rows_come_from_the_headings(route_plan: Any) -> None:
    _ctx, plan = route_plan
    acts = {r["act_id"]: r["eli_work"] for r in _rows(plan, "lgx.act")}
    vid = next(r["version_id"] for r in _rows(plan, "lgx.act_version") if r["eli_version"] == ROUTE_V)
    rows = {acts[r["member_act_id"]]: r for r in _rows(plan, "lgx.recueil_member") if r["recueil_version_id"] == vid}
    assert rows[LOI]["method"] == "heading" and rows[LOI]["part_heading"].startswith("Loi du 14 février 1955")
    assert rows[AGD]["method"] == "heading" and rows[AGD]["reviewed_by"] is None


def test_outdated_members_are_superseded_by_the_code(route_plan: Any) -> None:
    ctx, plan = route_plan
    acts = {r["eli_work"]: r for r in _rows(plan, "lgx.act")}
    for member in (AGD, LOI):
        assert acts[member]["superseded_by_act_id"] == acts[ROUTE]["act_id"], member
        assert "superseded_by_code" in acts[member]["flags"], member
    assert "superseded_by_code" not in acts[ROUTE]["flags"]
    structure = json.loads(
        (ctx.layout.units_dir / "eli-etat-leg-code-route-20260612.structure.json").read_text(encoding="utf-8")
    )
    assert structure["stats"]["member_parts_resolved"] >= 2 and structure["stats"]["units_with_member"] > 0
