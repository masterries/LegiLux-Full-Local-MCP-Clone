"""Golden numbers of PLAN §11.5, asserted exactly on the frozen 2026-10-16 snapshot (offline)."""

from __future__ import annotations

import datetime as dt

import pytest

from lgx_core.config import ProjectConfig
from lgx_core.rules import golden
from lgx_core.rules.select import Selection, select
from lgx_ingest.select import GOLDEN_SNAPSHOT
from lgx_ingest.sparql import load_snapshot

pytestmark = pytest.mark.skipif(not (GOLDEN_SNAPSHOT / "acts.parquet").exists(), reason="golden snapshot absent")


@pytest.fixture(scope="module")
def sel() -> Selection:
    return select(load_snapshot(GOLDEN_SNAPSHOT), golden.GOLDEN_DATE, ProjectConfig.load())


def test_research_definitions_reproduce_exactly(sel: Selection) -> None:
    research = sel.report["golden"]["research"]
    assert [row for row in golden.compare(research, golden.EXPECTED) if not row[3]] == []


def test_selection_numbers(sel: Selection) -> None:
    numbers = sel.report["golden"]["selection"]
    assert [row for row in golden.compare(numbers, golden.EXPECTED_SELECTION) if not row[3]] == []
    assert set(golden.SELECTION_NOTES) <= set(golden.EXPECTED_SELECTION)


def test_frozen_volumes(sel: Selection) -> None:
    """Regression values of the frozen snapshot (M1): any change must be explained in the report."""
    counts = sel.report["counts"]
    assert counts["acts"] == 58390
    assert counts["excluded"] == {"branch": 70332, "doc_type:RC": 21656, "naturalisation": 918}
    assert counts["by_text_basis"] == {
        "consolidated": 1118,
        "metadata_only": 32832,
        "original_never_amended": 22127,
        "original_possibly_outdated": 2303,
        "stub_fallback": 10,
    }
    assert sel.report["sample"]["mismatches"] == []
    assert {a["code"] for a in sel.report["anomalies"]} == {"consolidation_file_is_memorial_issue"}


def test_reconciliation_accounts_for_every_research_act(sel: Selection) -> None:
    rec = golden.reconcile(load_snapshot(GOLDEN_SNAPSHOT), sel.plan, dt.date(2026, 10, 16))
    loi_never = sum(n for k, n in rec.items() if k.startswith("loi.never_modified"))
    assert loi_never == golden.EXPECTED["loi_never_modified"]
    assert rec["loi.never_modified -> excluded (naturalisation/denylist)"] == 918
    assert rec["rgd.never_modified -> metadata_only [3:temporary]"] == 638
