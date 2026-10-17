"""`lgx ingest` orchestration without a database: offline, cache-first, --no-load, the --full guard."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from typer.testing import CliRunner

from lgx_ingest.cli import app
from lgx_ingest.cli_ingest import run_ingest
from lgx_ingest.context import StageContext
from unit.load_fixture import E2E_WORKS, T, build_fixture_sample


@pytest.fixture
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError("network request during an offline ingest")

    monkeypatch.setattr(httpx.Client, "send", refuse)


def test_offline_ingest_without_database(tmp_path: Path, no_network: None) -> None:
    works = {k: v for k, v in E2E_WORKS.items() if k.endswith(("n25", "1978/07/14/n1"))}
    cfg, data = build_fixture_sample(tmp_path, works)
    ctx = StageContext.create(sync_date=T, config_dir=cfg, data_dir=data, sample=True)
    result = run_ingest(ctx, offline=True, load=False, echo=lambda _m: None)
    steps = {s["stage"]: s for s in result.details["steps"]}
    assert result.ok, (result.messages, steps)
    assert list(steps) == ["robots", "discover", "select", "fetch", "parse", "chunk", "load", "gates", "report"]
    assert steps["fetch"]["counts"] == {"targets": 2, "cached": 2, "missing": 0}
    assert "skipped" in steps["load"]["messages"][0]
    gates = json.loads((data / "reports" / T.isoformat() / "gates.json").read_text(encoding="utf-8"))
    names = {g["gate"]: g["status"] for g in gates["gates"]}
    assert names["database"] == "skip" and names["parse_failures"] == "pass" and names["robots"] == "warn"
    sizing = json.loads((data / "reports" / T.isoformat() / "sizing.json").read_text(encoding="utf-8"))
    assert sizing["totals"]["versions"] == 2 and sizing["totals"]["chunks"] > 0 and "database" not in sizing


def test_offline_ingest_reports_missing_files(tmp_path: Path, no_network: None) -> None:
    cfg, data = build_fixture_sample(tmp_path, {k: v for k, v in E2E_WORKS.items() if k.endswith("n25")})
    # the sample also lists a686, whose file is not in the cache
    import yaml

    sample = yaml.safe_load((cfg / "sample.yaml").read_text(encoding="utf-8"))
    full = yaml.safe_load((Path(__file__).resolve().parents[2] / "config" / "sample.yaml").read_text(encoding="utf-8"))
    sample["works"] += [w for w in full["works"] if w["work"].endswith("2018/08/01/a686")]
    (cfg / "sample.yaml").write_text(yaml.safe_dump(sample, allow_unicode=True), encoding="utf-8")
    ctx = StageContext.create(sync_date=T, config_dir=cfg, data_dir=data, sample=True)
    result = run_ingest(ctx, offline=True, load=False, echo=lambda _m: None)
    fetch = next(s for s in result.details["steps"] if s["stage"] == "fetch")
    assert fetch["counts"] == {"targets": 2, "cached": 1, "missing": 1}
    assert any("not in the cache" in m for m in fetch["messages"])


def test_offline_without_snapshot_fails_with_hint(tmp_path: Path, no_network: None) -> None:
    cfg, data = build_fixture_sample(tmp_path, {})
    ctx = StageContext.create(sync_date=T.replace(day=17), config_dir=cfg, data_dir=data, sample=True)
    result = run_ingest(ctx, offline=True, load=False, echo=lambda _m: None)
    assert not result.ok and "available: 2026-10-16" in result.messages[0]


def test_full_crawl_needs_confirmation(tmp_path: Path) -> None:
    res = CliRunner().invoke(app, ["--t", "2026-10-16", "--data-dir", str(tmp_path), "ingest", "--full"])
    assert res.exit_code == 2


def test_dry_run_prints_the_plan_without_any_request(tmp_path: Path, no_network: None) -> None:
    """`lgx ingest [--full] --dry-run`: robots + snapshot reused, select, fetch plan, nothing fetched or parsed."""
    works = {k: v for k, v in E2E_WORKS.items() if k.endswith(("n25", "1978/07/14/n1"))}
    cfg, data = build_fixture_sample(tmp_path, works)
    for sample in (True, False):
        ctx = StageContext.create(sync_date=T, config_dir=cfg, data_dir=data, sample=sample, dry_run=True)
        result = run_ingest(ctx, echo=lambda _m: None)
        steps = {s["stage"]: s for s in result.details["steps"]}
        assert result.ok, (result.messages, steps)
        assert list(steps) == ["robots", "discover", "select", "fetch"]
        plan = result.details["plan"]
        assert plan["counts"]["targets"] >= 2 and plan["duration_estimate"]["spacing_s"] >= 1.0
        assert plan["requests"]["files"] == plan["counts"]["missing"]
        assert any("environnement_annexes" in n for n in plan["never_fetched"])
        report = data / "reports" / T.isoformat() / f"fetch_plan_{'sample' if sample else 'full'}.json"
        assert json.loads(report.read_text(encoding="utf-8"))["stage"] == "fetch"
        if not sample:
            assert plan["duration_estimate"]["nightly_window"] and plan["full_file_plan"] is not None
    assert not (data / "parsed").exists() or not any((data / "parsed").iterdir())


def test_full_dry_run_needs_no_confirmation(tmp_path: Path) -> None:
    res = CliRunner().invoke(app, ["--t", "2026-10-16", "--data-dir", str(tmp_path), "ingest", "--full", "--dry-run"])
    assert res.exit_code != 2  # no snapshot in tmp: fails later (exit 1), but not at the --confirm-full-crawl guard
