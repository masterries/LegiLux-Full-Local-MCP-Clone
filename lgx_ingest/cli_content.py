"""``lgx content …`` — fetch/parse helpers for the content stages (agent B), registered by ``cli.py``.

* ``lgx content fetch --plan PLAN [--sample/--full] [--limit N] [--include-m2] [--dry-run]``: the S3
  fetcher on an explicit manifest (``manifest.jsonl`` or a parquet export of it).
* ``lgx content parse FILE [--eli-version ELI] [--eli-work ELI] [--anchor A] [--chunks]``: parse one
  local AKN/HTML file and print a units summary (no network, nothing written).
"""

from __future__ import annotations

import collections
import datetime as dt
import json
import re
from pathlib import Path
from typing import Annotated, Any

import typer

from lgx_core.eli import ELI_BASE
from lgx_core.models import (
    CC_BY_4_0,
    DateWindow,
    FileRef,
    ManifestEntry,
    RawFile,
    SourceFormat,
    TextBasis,
    UnitType,
    UserFormat,
    VersionKind,
    VersionRole,
    sha256_hex,
)
from lgx_ingest.cli import Sample, _context, _run, app, err

content = typer.Typer(help="Content stages (fetch, parse) on explicit inputs.", no_args_is_help=True)
app.add_typer(content, name="content")


@content.command("fetch")
def content_fetch(
    ctx: typer.Context,
    plan: Annotated[Path, typer.Option("--plan", help="manifest.jsonl (or .parquet) to fetch from")],
    sample: Sample = True,
    limit: Annotated[int | None, typer.Option(help="Stop after N downloads")] = None,
    include_m2: Annotated[bool, typer.Option(help="Also fetch sample entries marked stage m2")] = False,
    dry_run: Annotated[bool, typer.Option(help="List what would be fetched")] = False,
) -> None:
    """Polite conditional GETs of the files listed in PLAN (sample entries only unless --full)."""
    if not plan.exists():
        err.print(f"[red]plan not found:[/red] {plan}")
        raise typer.Exit(2)
    stage_ctx = _context(ctx, sample=sample, dry_run=dry_run)
    stage_ctx = type(stage_ctx)(**{**stage_ctx.__dict__, "options": {**stage_ctx.options, "manifest": str(plan)}})
    _run("lgx_ingest.fetch:run_fetch", stage_ctx, limit=limit, include_m2=include_m2)


def guess_identity(path: Path) -> tuple[str, str]:
    """(eli_work, eli_version) from a filestore file name (``eli-etat-leg-…-fr-xml.xml``)."""
    stem = re.sub(r"-(?:fr|de|en|lb)-(?:xml|html|pdf)$", "", path.stem)
    if not stem.startswith("eli-"):
        raise typer.BadParameter(f"cannot infer the ELI from {path.name}: pass --eli-version")
    version = ELI_BASE + stem[4:].replace("-", "/")
    return work_of(version), version


def work_of(version: str) -> str:
    if version.endswith("/jo"):
        return version[: -len("/jo")]
    if "/consolide/" in version:
        return version.split("/consolide/", 1)[0]
    return version.rsplit("/", 1)[0]


def synthetic_entry(path: Path, data: bytes, eli_work: str, eli_version: str) -> tuple[ManifestEntry, RawFile]:
    fmt = SourceFormat.HTML if path.suffix.lower() in (".html", ".htm") else SourceFormat.XML
    url = f"file://{path.resolve()}"
    kind = VersionKind.ORIGINAL if eli_version.endswith("/jo") else VersionKind.CONSOLIDATED
    entry = ManifestEntry(
        eli_work=eli_work,
        eli_version=eli_version,
        kind=kind,
        role=VersionRole.CURRENT,
        doc_type="?",
        text_basis=TextBasis.ORIGINAL_NEVER_AMENDED if kind is VersionKind.ORIGINAL else TextBasis.CONSOLIDATED,
        file=FileRef(
            url=url,
            file_uri=url,
            user_format=UserFormat.HTML if fmt is SourceFormat.HTML else UserFormat.XML,
            source_format=fmt,
            licence=CC_BY_4_0,
        ),
        text_valid=DateWindow(lower=dt.date(1, 1, 1)),
        reason="cli:content-parse",
    )
    raw = RawFile(
        url=url,
        sha256=sha256_hex(data),
        bytes=len(data),
        path=str(path),
        fetched_at=dt.datetime.now(dt.UTC),
        eli_version=eli_version,
        source_format=fmt,
    )
    return entry, raw


@content.command("parse")
def content_parse(
    file: Annotated[Path, typer.Argument(help="AKN XML or Legilux HTML file", exists=True, dir_okay=False)],
    eli_version: Annotated[str | None, typer.Option(help="Version ELI (default: from the file name)")] = None,
    eli_work: Annotated[str | None, typer.Option(help="Work ELI (default: derived from the version)")] = None,
    anchor: Annotated[list[str] | None, typer.Option(help="Print these units in full (repeatable)")] = None,
    show: Annotated[int, typer.Option(help="How many article units to preview")] = 5,
    chunks: Annotated[bool, typer.Option(help="Also chunk and report chunk counts")] = False,
) -> None:
    """Parse one local file and print a units summary (stats, issues, relations, preview)."""
    from lgx_ingest.chunk import chunk_units, skip_reason
    from lgx_ingest.parse import ParseError, parse_bytes

    data = file.read_bytes()
    if eli_version:
        version = eli_version if eli_version.startswith("http") else ELI_BASE + eli_version.strip("/")
        work = eli_work or work_of(version)
    else:
        work, version = guess_identity(file)
        work = eli_work or work
    entry, raw = synthetic_entry(file, data, work, version)
    try:
        result = parse_bytes(data, entry, raw)
    except (ParseError, NotImplementedError) as exc:
        err.print(f"[red]parse failed:[/red] {exc}")
        raise typer.Exit(1) from exc
    arts = [u for u in result.units if u.unit_type is UnitType.ARTICLE]
    summary: dict[str, Any] = {
        "file": str(file),
        "sha256": raw.sha256,
        "eli_version": version,
        "parser": f"{result.parser}/{result.parser_version}",
        "stats": result.stats,
        "unit_types": dict(collections.Counter(u.unit_type.value for u in result.units)),
        "issues": dict(collections.Counter(i.code for i in result.issues)),
        "relations": dict(collections.Counter(r.source.value for r in result.relations)),
        "preview": [
            {
                "ordinal": u.ordinal,
                "anchor": u.anchor,
                "num": u.num_raw,
                "num_norm": u.num_norm,
                "eli_unit": u.eli_unit,
                "derived": u.eli_unit_derived,
                "repealed": u.is_repealed,
                "breadcrumb": u.breadcrumb,
                "text": (u.text_plain or "")[:200],
            }
            for u in arts[:show]
        ],
    }
    if chunks:
        cs = chunk_units(result.units, title_short=work.rsplit("/eli/", 1)[-1], version_label="cli")
        summary["chunks"] = {
            "count": len(cs),
            "split_units": len({c.unit_ordinal for c in cs if c.part_count > 1}),
            "max_tokens": max((c.n_tokens or 0 for c in cs), default=0),
            "skipped": dict(collections.Counter(r for u in result.units if (r := skip_reason(u)))),
        }
    for a in anchor or []:
        unit = next((u for u in result.units if u.anchor == a), None)
        summary.setdefault("units", {})[a] = unit.model_dump(mode="json") if unit else None
    typer.echo(json.dumps(summary, indent=2, ensure_ascii=False))
