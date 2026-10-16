from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from lgx_core import __version__
from lgx_ingest.cli import app

runner = CliRunner()


def test_help_lists_pipeline_commands() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for cmd in (
        "robots",
        "discover",
        "select",
        "fetch",
        "parse",
        "chunk",
        "embed",
        "load",
        "gates",
        "ingest",
    ):
        assert cmd in result.output


def test_version() -> None:
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0 and result.output.strip() == __version__


def test_config_check() -> None:
    result = runner.invoke(app, ["config-check"])
    assert result.exit_code == 0, result.output
    summary = json.loads(result.output)
    assert summary["embedding_provider"] == "none" and summary["warning_codes"] == 22
    assert summary["history_mode"] == "none"


def test_paths_with_fixed_t(tmp_path: Path) -> None:
    result = runner.invoke(app, ["--t", "2026-10-16", "--data-dir", str(tmp_path), "paths"])
    assert result.exit_code == 0, result.output
    paths = json.loads(result.output)
    assert paths["manifest"] == str(tmp_path / "manifest" / "2026-10-16" / "manifest.jsonl")


def test_embed_is_off_by_default(tmp_path: Path) -> None:
    result = runner.invoke(app, ["--data-dir", str(tmp_path), "embed"])
    assert result.exit_code == 0
    assert "provider=none" in result.output


def test_bad_options() -> None:
    assert runner.invoke(app, ["--t", "16/10/2026", "paths"]).exit_code != 0
    assert runner.invoke(app, ["--opt", "novalue", "paths"]).exit_code != 0
