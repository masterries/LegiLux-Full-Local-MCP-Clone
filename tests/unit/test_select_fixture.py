"""Selection on the committed sample fixture (tests/fixtures/meta/snapshot-20261016-sample): sample.yaml
expectations, SQL-twin parity at T, time travel around every stored change point, stage outputs."""

from __future__ import annotations

import datetime as dt
import itertools
import json
from collections import defaultdict
from pathlib import Path

import pyarrow.parquet as pq
import pytest
from typer.testing import CliRunner

from lgx_core.config import ProjectConfig
from lgx_core.io import read_jsonl
from lgx_core.models import Act, ActStatus, ActVersion, ManifestEntry, Relation, TextBasis
from lgx_core.rules.select import Selection, select
from lgx_core.rules.serving import serving_at, views_from_manifest
from lgx_ingest.context import StageContext
from lgx_ingest.select import run_select
from lgx_ingest.sparql import load_snapshot

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "meta" / "snapshot-20261016-sample"
T = dt.date(2026, 10, 16)
E = "http://data.legilux.public.lu/eli/etat/leg/"


@pytest.fixture(scope="module")
def sel() -> Selection:
    return select(load_snapshot(FIXTURE), T, ProjectConfig.load())


def test_sample_expectations_match_on_the_frozen_snapshot(sel: Selection) -> None:
    sample = sel.report["sample"]
    assert sample["works"] == 53  # M2: + code/communal, code/education_nationale (PDF, stage m2)
    assert sample["mismatches"] == []


def test_every_sample_entry_with_text_has_a_fetchable_file(sel: Selection) -> None:
    stored = [e for e in sel.manifest if e.in_sample and e.text_valid is not None]
    assert stored
    for e in stored:
        assert e.file is not None, e.eli_version
        assert e.file.url.startswith("https://legilux.public.lu/filestore/eli/etat/leg/")
        assert e.file.licence == "http://creativecommons.org/licenses/by/4.0/"
    assert {e.sample_stage for e in stored} == {"m1", "m2"}


def _by_work(sel: Selection) -> tuple[dict[str, list[ManifestEntry]], dict[str, list[Relation]]]:
    entries: dict[str, list[ManifestEntry]] = defaultdict(list)
    for e in sel.manifest:
        entries[e.eli_work].append(e)
    rels: dict[str, list[Relation]] = defaultdict(list)
    for r in sel.relations:
        rels[r.dst_eli].append(r)
    return entries, rels


def test_runtime_twin_reproduces_the_selection_at_t(sel: Selection) -> None:
    """PLAN §11.5 parity: serving_at(T) == the build label, except not-yet-in-force acts (text from dei)."""
    entries, rels = _by_work(sel)
    for act in sel.acts:
        decision = serving_at(act, views_from_manifest(entries[act.eli_work]), rels[act.eli_jo or ""], T)
        if decision.status is ActStatus.NOT_YET_IN_FORCE and act.text_basis is not TextBasis.METADATA_ONLY:
            assert decision.text_basis is TextBasis.METADATA_ONLY
            continue
        assert decision.text_basis is act.text_basis, act.eli_work
        if act.text_basis is not TextBasis.METADATA_ONLY:
            assert decision.text_version == act.basis_version_eli


def test_time_travel_switches_exactly_on_change_points(sel: Selection) -> None:
    """For every stored window boundary inside H: d-1 serves the old text, d the new one (PLAN §11.5)."""
    entries, rels = _by_work(sel)
    horizon = T + dt.timedelta(days=365)
    checked = 0
    for act in sel.acts:
        views = views_from_manifest(entries[act.eli_work])
        for v in views:
            if v.text_valid is None:
                continue
            for day in (v.text_valid.lower, v.text_valid.upper):
                if day is None or not (T < day <= horizon):
                    continue
                before = serving_at(act, views, rels[act.eli_jo or ""], day - dt.timedelta(days=1))
                on = serving_at(act, views, rels[act.eli_jo or ""], day)
                after = serving_at(act, views, rels[act.eli_jo or ""], day + dt.timedelta(days=1))
                if day == v.text_valid.lower:
                    assert before.text_version != v.eli_version and on.text_version == v.eli_version
                else:
                    assert before.text_version == v.eli_version and on.text_version != v.eli_version
                assert after.text_version == on.text_version
                checked += 1
    assert checked >= 5  # 2014/07/04/n2 (T+4), 1988/07/15/n3, a510 (dei T+4), 2010/05/27/n2 (dnl T+4) …


def test_text_windows_never_overlap(sel: Selection) -> None:
    entries, _ = _by_work(sel)
    for work, es in entries.items():
        windows = sorted(
            ((e.text_valid.lower or dt.date.min, e.text_valid.upper or dt.date.max) for e in es if e.text_valid),
        )
        for (_, hi), (lo, _) in itertools.pairwise(windows):
            assert hi <= lo, work


def test_secteur_financier_stub_fallback(sel: Selection) -> None:
    plan = next(r for r in sel.plan if r["eli_work"] == E + "loi/1993/04/05/n1")
    assert plan["text_basis"] == "stub_fallback" and plan["served_version"].endswith("/consolide/20031001")
    assert plan["window_version"].endswith("/consolide/20260711") and plan["window_is_stub"]
    assert plan["n_amending"] > 0  # mandatory amending list after the 2003 fallback


def test_run_select_writes_the_contract_outputs(tmp_path: Path) -> None:
    ctx = StageContext.create(sync_date=T, data_dir=tmp_path, options={"snapshot": str(FIXTURE)})
    result = run_select(ctx)
    assert result.ok and result.counts["sample_matched"] == 53
    assert any("no robots denylist" in m for m in result.messages)
    layout = ctx.layout
    acts = list(read_jsonl(layout.acts_path, Act))
    versions = list(read_jsonl(layout.versions_path, ActVersion))
    manifest = list(read_jsonl(layout.manifest_path, ManifestEntry))
    assert len(acts) == result.counts["acts"] and len(manifest) == result.counts["manifest_entries"]
    assert {v.eli_work for v in versions} <= {a.eli_work for a in acts}
    report = json.loads(layout.selection_report_path.read_text())
    assert set(report) >= {"sync_date", "config_digest", "snapshot_digest", "counts", "golden", "anomalies", "sample"}
    assert set(report["counts"]) >= {"by_text_basis", "by_flag", "by_role", "by_reason"}
    plan = pq.read_table(layout.meta_dir / "serving_plan.parquet").to_pylist()
    assert len(plan) == len(acts)
    row = next(r for r in plan if r["eli_work"] == E + "loi/2014/07/04/n2")
    assert row["future_version"].endswith("/consolide/20261020") and row["text_valid_to"] == dt.date(2026, 10, 20)


def test_cli_meta_select_with_date_and_snapshot(tmp_path: Path) -> None:
    from lgx_ingest.cli import app

    runner = CliRunner()
    res = runner.invoke(
        app, ["--data-dir", str(tmp_path), "meta", "select", "--date", "2026-11-01", "--snapshot", str(FIXTURE)]
    )
    assert res.exit_code == 0, res.output
    out = json.loads(res.stdout[res.stdout.index("{") :])
    assert out["stage"] == "select" and out["counts"]["acts"] > 51
    assert (tmp_path / "manifest" / "2026-11-01" / "manifest.jsonl").exists()
    # on 2026-11-01 the stored next version of rgd/1988/07/15/n3 is the current one
    later = select(load_snapshot(FIXTURE), dt.date(2026, 11, 1), ProjectConfig.load())
    row = next(r for r in later.plan if r["eli_work"] == E + "rgd/1988/07/15/n3")
    assert row["served_version"].endswith("/consolide/20261101")
