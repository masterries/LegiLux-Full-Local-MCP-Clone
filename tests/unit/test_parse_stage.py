"""Offline end-to-end of the content stages on committed fixtures: raw cache → parse → units → chunks."""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

from typer.testing import CliRunner

from lgx_core.io import append_jsonl, read_jsonl, write_jsonl
from lgx_core.models import Chunk, Relation, SourceFormat, Unit, UserFormat
from lgx_ingest.chunk import run_chunk
from lgx_ingest.cli import app
from lgx_ingest.context import StageContext
from lgx_ingest.fetch import _atomic_write_bytes
from lgx_ingest.parse import run_parse
from lgx_ingest.structure import run_structure

from .test_parse_fixtures import FIXTURES, fixture_input

T = dt.date(2026, 10, 16)
NAMES = ("a686_conso", "a88_jo", "l1978_jo_html")


def staged(tmp_path: Path) -> StageContext:
    ctx = StageContext.create(sync_date=T, data_dir=tmp_path / "data")
    entries = []
    for name in NAMES:
        data, entry, raw = fixture_input(name)
        ext = ".html" if raw.source_format and raw.source_format.value == "html" else ".xml"
        path = ctx.layout.raw_path(raw.sha256, ext)
        _atomic_write_bytes(path, data)
        append_jsonl(
            ctx.layout.raw_index_path, raw.model_copy(update={"path": str(path.relative_to(ctx.layout.raw_dir))})
        )
        entries.append(entry)
    write_jsonl(ctx.layout.manifest_path, entries)
    return ctx


def test_parse_then_chunk_writes_units_relations_and_chunks(tmp_path: Path) -> None:
    ctx = staged(tmp_path)
    res = run_parse(ctx)
    assert res.ok, res.messages
    assert res.counts["parsed"] == 3 and res.counts["versions"] == 3 and not res.details["failures"]
    _d, entry, raw = fixture_input("a686_conso")
    assert entry.eli_version
    units = list(read_jsonl(ctx.layout.units_path(entry.eli_version), Unit))
    assert sum(1 for u in units if u.unit_type.value == "article") == 77
    rels = list(read_jsonl(ctx.layout.unit_relations_path(entry.eli_version), Relation))
    assert {r.source.value for r in rels} == {"akn-ref", "akn-textualmod"}
    assert ctx.layout.parsed_path(raw.sha256, "akn-1").exists()

    again = run_parse(ctx)  # cache hit, provenance re-stamped
    assert again.counts["cached"] == 3 and "parsed" not in again.counts
    assert run_structure(ctx).counts["versions"] == 3

    chunked = run_chunk(ctx)
    assert chunked.ok and chunked.counts["versions"] == 3
    chunks = list(read_jsonl(ctx.layout.chunks_path(entry.eli_version), Chunk))
    stub = next(u for u in units if u.anchor == "art_56")
    assert stub.ordinal not in {c.unit_ordinal for c in chunks}
    assert all(c.valid_from == entry.text_valid.lower for c in chunks)  # type: ignore[union-attr]
    assert chunks[0].header.startswith("etat/leg/loi/2018/08/01/a686 — version consolidée applicable au 26/12/2025")


def test_parse_reports_missing_raw_and_broken_pdf(tmp_path: Path) -> None:
    ctx = StageContext.create(sync_date=T, data_dir=tmp_path / "data")
    _d, entry, _raw = fixture_input("a88_jo")
    url = entry.file.url.replace("-fr-xml.xml", "-fr-pdf.pdf").replace("/fr/xml/", "/fr/pdf/")  # type: ignore[union-attr]
    pdf_file = entry.file.model_copy(  # type: ignore[union-attr]
        update={"source_format": SourceFormat.PDF, "user_format": UserFormat.PDF, "url": url, "file_uri": url}
    )
    pdf = entry.model_copy(update={"file": pdf_file})
    write_jsonl(ctx.layout.manifest_path, [entry, pdf])
    res = run_parse(ctx)
    assert res.ok and res.counts["missing_raw"] == 2
    _atomic_write_bytes(ctx.layout.raw_path("0" * 64, ".pdf"), b"%PDF-1.4")
    append_jsonl(
        ctx.layout.raw_index_path,
        _raw.model_copy(update={"url": url, "sha256": "0" * 64, "path": f"00/{'0' * 64}.pdf"}),
    )
    broken = run_parse(ctx)  # M2: PDFs are parsed; a broken file is a gated parse failure, never skipped
    assert not broken.ok and broken.counts["failed"] == 1 and "skipped_pdf" not in broken.counts
    assert broken.details["failures"][0]["eli_version"] == entry.eli_version


def test_cli_content_parse_summary() -> None:
    path = FIXTURES / "xml" / "eli-etat-leg-loi-2026-02-25-a88-jo-fr-xml.xml"
    out = CliRunner().invoke(app, ["content", "parse", str(path), "--chunks", "--anchor", "art_2"])
    assert out.exit_code == 0, out.output
    summary = json.loads(out.output)
    assert summary["eli_version"] == "http://data.legilux.public.lu/eli/etat/leg/loi/2026/02/25/a88/jo"
    assert summary["stats"]["articles"] == 11 and summary["unit_types"]["annex"] == 2
    assert summary["chunks"]["count"] == 13 and summary["units"]["art_2"]["heading"] == "TVP UE"


def test_cli_content_fetch_dry_run(tmp_path: Path) -> None:
    plan = FIXTURES.parent / "manifest" / "bootstrap_sample.jsonl"
    out = CliRunner().invoke(
        app, ["--t", "2026-10-16", "--data-dir", str(tmp_path), "content", "fetch", "--plan", str(plan), "--dry-run"]
    )
    assert out.exit_code == 0, out.output
    result = json.loads(out.output)
    assert result["counts"]["targets"] == 12 and result["counts"]["new"] == 12
