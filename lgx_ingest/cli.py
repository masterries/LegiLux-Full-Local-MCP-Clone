"""``lgx`` — build-zone operator CLI (PLAN §6.1).

Every pipeline command is declared here once and dispatches to a fixed function in its stage module
(``docs/dev/INTERFACES.md`` §1). Stage modules are implemented independently; a stage that is not
implemented yet exits with code 3 and says which milestone delivers it.
"""

from __future__ import annotations

import datetime as dt
import importlib
import json
import sys
from pathlib import Path
from typing import Annotated, Any

import typer
from pydantic import ValidationError
from rich.console import Console

from lgx_core import __version__
from lgx_core.config import DEFAULT_CONFIG_DIR, ProjectConfig, Settings
from lgx_ingest.context import StageContext, StageResult

EXIT_NOT_IMPLEMENTED = 3

app = typer.Typer(
    name="lgx",
    help="LegiLux local clone — build-zone pipeline (robots → discover → select → fetch → parse → chunk → load).",
    no_args_is_help=True,
    add_completion=False,
)
err = Console(stderr=True)


class _Globals:
    sync_date: dt.date | None = None
    config_dir: Path | None = None
    data_dir: Path | None = None
    options: dict[str, str]

    def __init__(self) -> None:
        self.options = {}


def _parse_date(value: str | None) -> dt.date | None:
    if value is None or value == "auto":
        return None
    try:
        return dt.date.fromisoformat(value)
    except ValueError as exc:
        raise typer.BadParameter(f"expected YYYY-MM-DD or 'auto', got {value!r}") from exc


def _parse_opts(values: list[str] | None) -> dict[str, str]:
    out: dict[str, str] = {}
    for item in values or []:
        key, sep, val = item.partition("=")
        if not sep or not key:
            raise typer.BadParameter(f"--opt expects KEY=VALUE, got {item!r}")
        out[key.strip()] = val.strip()
    return out


@app.callback()
def main(
    ctx: typer.Context,
    t: Annotated[
        str | None, typer.Option("--t", "--sync-date", help="Sync date T (YYYY-MM-DD) or 'auto' (today, LU)")
    ] = None,
    config_dir: Annotated[Path | None, typer.Option(help="Config directory (default: ./config)")] = None,
    data_dir: Annotated[Path | None, typer.Option(help="Data directory (default: ./data or LGX_DATA_DIR)")] = None,
    opt: Annotated[list[str] | None, typer.Option("--opt", "-o", help="Stage option KEY=VALUE (repeatable)")] = None,
) -> None:
    g = _Globals()
    g.sync_date = _parse_date(t)
    g.config_dir = config_dir
    g.data_dir = data_dir
    g.options = _parse_opts(opt)
    ctx.obj = g


def _context(ctx: typer.Context, *, sample: bool = True, dry_run: bool = False) -> StageContext:
    g: _Globals = ctx.obj
    try:
        return StageContext.create(
            sync_date=g.sync_date,
            config_dir=g.config_dir,
            data_dir=g.data_dir,
            sample=sample,
            dry_run=dry_run,
            options=g.options,
        )
    except (ValidationError, FileNotFoundError) as exc:
        err.print(f"[red]configuration error:[/red] {exc}")
        raise typer.Exit(2) from exc


def _run(target: str, stage_ctx: StageContext, **kwargs: Any) -> StageResult:
    """Import ``module:function`` lazily and run it; NotImplementedError → exit 3 with a hint."""
    module_name, func_name = target.split(":")
    func = getattr(importlib.import_module(module_name), func_name)
    try:
        result: StageResult = func(stage_ctx, **kwargs)
    except NotImplementedError as exc:
        err.print(f"[yellow]not implemented yet:[/yellow] {exc}")
        raise typer.Exit(EXIT_NOT_IMPLEMENTED) from exc
    typer.echo(result.model_dump_json(indent=2))
    if not result.ok:
        raise typer.Exit(1)
    return result


Sample = Annotated[bool, typer.Option("--sample/--full", help="Dev sample (config/sample.yaml) or full corpus")]


# ------------------------------------------------------------------------------------------- info
@app.command()
def version() -> None:
    """Print the pipeline version."""
    typer.echo(__version__)


@app.command("config-check")
def config_check(ctx: typer.Context) -> None:
    """Load and validate every file in config/ and print a summary."""
    g: _Globals = ctx.obj
    config_dir = g.config_dir or Settings.from_env().config_dir or DEFAULT_CONFIG_DIR
    try:
        cfg = ProjectConfig.load(config_dir)
    except (ValidationError, FileNotFoundError, ValueError) as exc:
        err.print(f"[red]invalid configuration in {config_dir}:[/red]\n{exc}")
        raise typer.Exit(1) from exc
    summary = {
        "config_dir": str(cfg.config_dir),
        "digest": cfg.digest(),
        "sync_date": str(cfg.scope.sync_date),
        "resolved_T": cfg.scope.resolve_sync_date().isoformat(),
        "text_types": len(cfg.scope.text_types),
        "history_mode": cfg.scope.history.mode,
        "licence_policy": cfg.scope.licence_policy,
        "sample_works": len(cfg.sample.works),
        "sample_m1": sum(1 for w in cfg.sample.works if w.stage == "m1"),
        "embedding_provider": cfg.embedding.provider.value,
        "warning_codes": len(cfg.warnings.warnings),
        "aliases": len(cfg.aliases.aliases),
        "denylist_entries": len(cfg.denylist.entries),
    }
    typer.echo(json.dumps(summary, indent=2, ensure_ascii=False))


@app.command()
def paths(ctx: typer.Context) -> None:
    """Show the data layout for T (where each stage reads and writes)."""
    typer.echo(json.dumps(_context(ctx).layout.as_dict(), indent=2))


# --------------------------------------------------------------------------------------- pipeline
@app.command()
def robots(ctx: typer.Context) -> None:
    """S0: fetch + parse robots.txt of both hosts, regenerate the denylist (pauses on change)."""
    _run("lgx_ingest.robots:run_robots", _context(ctx))


@app.command("robots-ack")
def robots_ack(ctx: typer.Context, sha256: Annotated[str, typer.Argument(help="sha256 of the new robots.txt")]) -> None:
    """Acknowledge a changed robots.txt so fetching may resume."""
    _run("lgx_ingest.robots:acknowledge", _context(ctx), sha256=sha256)


@app.command()
def discover(
    ctx: typer.Context,
    query: Annotated[list[str] | None, typer.Option("--query", "-q", help="Only these query ids (q01 …)")] = None,
) -> None:
    """S1: flat SPARQL metadata pulls into data/meta/<T>/ (always the full metadata, even in sample mode)."""
    _run("lgx_ingest.sparql:run_discover", _context(ctx), queries=query)


@app.command("select")
def select_cmd(ctx: typer.Context, sample: Sample = True) -> None:
    """S2: selection at T → manifest, metadata rows, selection report."""
    _run("lgx_ingest.select:run_select", _context(ctx, sample=sample))


@app.command()
def fetch(
    ctx: typer.Context,
    sample: Sample = True,
    limit: Annotated[int | None, typer.Option(help="Stop after N downloads")] = None,
    include_m2: Annotated[bool, typer.Option(help="Also fetch sample entries marked stage m2")] = False,
    dry_run: Annotated[bool, typer.Option(help="List what would be fetched")] = False,
) -> None:
    """S3: polite conditional GETs of selected files (sample by default)."""
    _run(
        "lgx_ingest.fetch:run_fetch",
        _context(ctx, sample=sample, dry_run=dry_run),
        limit=limit,
        include_m2=include_m2,
    )


@app.command()
def parse(ctx: typer.Context, sample: Sample = True) -> None:
    """S4: parse fetched files into units."""
    _run("lgx_ingest.parse:run_parse", _context(ctx, sample=sample))


@app.command()
def structure(ctx: typer.Context, sample: Sample = True) -> None:
    """S5: normalise + structure parsed units (may be folded into parse in M1)."""
    _run("lgx_ingest.structure:run_structure", _context(ctx, sample=sample))


@app.command()
def chunk(ctx: typer.Context, sample: Sample = True) -> None:
    """S6: units → retrieval chunks."""
    _run("lgx_ingest.chunk:run_chunk", _context(ctx, sample=sample))


@app.command()
def embed(ctx: typer.Context) -> None:
    """S7: embedding stage — OFF by default (provider none): reports the status and does nothing."""
    stage_ctx = _context(ctx)
    provider = stage_ctx.config.embedding.provider
    if not stage_ctx.config.embedding.enabled:
        typer.echo(
            StageResult(stage="embed", messages=["provider=none: no vectors are computed (PLAN §6.5)"]).model_dump_json(
                indent=2
            )
        )
        return
    err.print(f"[yellow]not implemented yet:[/yellow] embedding provider {provider.value!r} arrives in M3")
    raise typer.Exit(EXIT_NOT_IMPLEMENTED)


@app.command()
def load(
    ctx: typer.Context,
    dsn: Annotated[str | None, typer.Option(help="libpq DSN of the build server (default LGX_PG_DSN)")] = None,
    db_name: Annotated[str | None, typer.Option(help="Database name (default legilux_build_<T>)")] = None,
    recreate: Annotated[bool, typer.Option(help="Drop and recreate the database first")] = True,
) -> None:
    """S8: load into a fresh database, post-load, invariants."""
    _run("lgx_ingest.load:run_load", _context(ctx), dsn=dsn, db_name=db_name, recreate=recreate)


@app.command()
def gates(ctx: typer.Context) -> None:
    """S9: anomaly gates."""
    _run("lgx_ingest.gates:run_gates", _context(ctx))


@app.command()
def report(ctx: typer.Context, sample: Sample = True) -> None:
    """Sizing report (articles, text bytes, chunks per stratum)."""
    _run("lgx_ingest.report:run_report", _context(ctx, sample=sample))


@app.command()
def ingest(
    ctx: typer.Context,
    sample: Sample = True,
    with_load: Annotated[bool, typer.Option("--load/--no-load", help="Also load into PostgreSQL")] = False,
) -> None:
    """robots → discover → select → fetch → parse → chunk [→ load]. `--full` is the operator crawl."""
    stage_ctx = _context(ctx, sample=sample)
    steps = [
        "lgx_ingest.robots:run_robots",
        "lgx_ingest.sparql:run_discover",
        "lgx_ingest.select:run_select",
        "lgx_ingest.fetch:run_fetch",
        "lgx_ingest.parse:run_parse",
        "lgx_ingest.chunk:run_chunk",
    ]
    if with_load:
        steps.append("lgx_ingest.load:run_load")
    for target in steps:
        err.print(f"[bold]▶ {target}[/bold]")
        _run(target, stage_ctx)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(app())
