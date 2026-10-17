"""``lgx ingest`` (the whole build for T) and ``lgx package`` — registered on the main app by ``cli.py``.

``lgx ingest --sample`` runs, for T:

    robots → discover → select → fetch → parse → chunk → load → gates → report [→ package]

* **robots** — reuses ``data/robots/<T>/`` when present; fetched again with ``--refresh`` or before any
  download (robots.txt is checked on every run that fetches, PLAN §3.2).
* **discover** — reuses the SPARQL snapshot ``data/meta/<T>/`` unless ``--refresh`` (full metadata pulls).
* **select** — always (pure, offline, ≈ 15 s on the full metadata).
* **fetch** — cache first: files already in ``raw/`` are reused without a request; only missing ones are
  fetched (politely, conditional GETs, robots + denylist). ``--revalidate`` sends conditional GETs for the
  cached ones too. ``--offline`` never makes a request (missing files are reported, then gated).
* **parse / chunk** — cached by file hash + parser version.
* **load** — fresh database ``legilux_build_<T>[_sample]`` (``--db-name``); default: when a DSN is
  configured (``--dsn`` / ``LGX_PG_DSN``); ``--no-load`` skips it and the database gates.
* **gates / report** — always; the command exits 1 when a stage or a gate fails.
* **package** (``--package``) — ``pg_dump`` into ``data/bundles/`` (+ ``--restore-to NAME``).

``--full`` is the operator crawl (≈ 25k GETs over nights): it needs ``--confirm-full-crawl`` and is never
used in development (PLAN §6.2).
"""

from __future__ import annotations

import dataclasses
import json
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Any

import typer

from lgx_ingest import cli as _cli
from lgx_ingest.context import StageContext, StageResult
from lgx_ingest.load import LoadError, default_db_name, resolve_dsn


def _with_options(ctx: StageContext, **options: str) -> StageContext:
    return dataclasses.replace(ctx, options={**ctx.options, **options})


def _step_summary(r: StageResult) -> dict[str, Any]:
    return {"stage": r.stage, "ok": r.ok, "counts": r.counts, "messages": r.messages[:10]}


def _snapshot_dates(ctx: StageContext) -> list[str]:
    base = ctx.layout.root / "meta"
    return sorted(d.name for d in base.iterdir() if (d / "_snapshot.json").exists()) if base.exists() else []


def fetch_from_cache(ctx: StageContext, *, offline: bool, revalidate: bool, include_m2: bool) -> StageResult | None:
    """Fetch step: reuse cached files; ``None`` means "files are missing and must be fetched"."""
    from lgx_ingest.fetch import RawIndex, load_plan, mark_done, read_progress, select_targets

    entries = load_plan(ctx.layout.manifest_path)
    targets = select_targets(entries, sample=ctx.sample, include_m2=include_m2)
    index = RawIndex(ctx.layout)
    cached = [t for t in targets if index.get(t.url) is not None]
    missing = [t for t in targets if index.get(t.url) is None]
    result = StageResult(
        stage="fetch", counts={"targets": len(targets), "cached": len(cached), "missing": len(missing)}
    )
    result.details["missing"] = [t.entry.eli_version or t.url for t in missing]
    if offline:
        result.messages.append("offline: no request made")
        if missing:
            result.messages.append(f"{len(missing)} file(s) not in the cache (reported by the gates)")
        return result
    if not missing and not revalidate:
        result.messages.append("all files cached: no request made (use --revalidate for conditional GETs)")
        return result
    if not revalidate:  # only the missing files are requested
        done = read_progress(ctx.layout)
        for t in cached:
            if t.url not in done:
                mark_done(ctx.layout, t.url)
    return None


def run_ingest(
    ctx: StageContext,
    *,
    refresh: bool = False,
    offline: bool = False,
    revalidate: bool = False,
    load: bool | None = None,
    dsn: str | None = None,
    db_name: str | None = None,
    package: bool = False,
    container: str | None = None,
    restore_to: str | None = None,
    include_m2: bool = False,
    echo: Callable[[str], None] | None = None,
) -> StageResult:
    """Run the build for T (see the module docstring); stops at the first failing build stage."""
    from lgx_ingest import chunk, fetch, gates, parse, report, robots, select, sparql
    from lgx_ingest import load as load_mod
    from lgx_ingest import package as package_mod

    say = echo or (lambda m: ctx.console.print(m))
    if refresh and offline:
        raise typer.BadParameter("--refresh and --offline exclude each other")
    if include_m2:
        ctx = _with_options(ctx, include_m2="1")
    summary = StageResult(stage="ingest")
    steps: list[dict[str, Any]] = []
    summary.details = {"sync_date": ctx.sync_date.isoformat(), "sample": ctx.sample, "steps": steps}

    def record(r: StageResult) -> StageResult:
        steps.append(_step_summary(r))
        say(f"[bold]▶ {r.stage}[/bold] {'ok' if r.ok else '[red]FAILED[/red]'} {json.dumps(r.counts)[:160]}")
        for m in r.messages[:5]:
            say(f"    {m}")
        if not r.ok:
            summary.ok = False
            summary.messages.append(f"{r.stage} failed: {'; '.join(r.messages[:3])}")
        return r

    layout = ctx.layout
    robots_state = layout.robots_dir / "robots.json"
    # S0 robots
    if offline or (robots_state.exists() and not refresh):
        record(
            StageResult(
                stage="robots",
                messages=[f"reused {robots_state}" if robots_state.exists() else "offline: no robots state for T"],
            )
        )
    elif not record(robots.run_robots(ctx)).ok:
        return summary
    # S1 discover
    if layout.snapshot_info.exists() and not refresh:
        record(StageResult(stage="discover", messages=[f"reused snapshot {layout.meta_dir}"]))
    elif offline:
        have = _snapshot_dates(ctx)
        hint = f" (available: {', '.join(have)}; pass --t)" if have else ""
        record(StageResult(stage="discover", ok=False, messages=[f"offline and no snapshot for {ctx.sync_date}{hint}"]))
        return summary
    elif not record(sparql.run_discover(ctx)).ok:
        return summary
    # S2 select
    if not record(select.run_select(ctx)).ok:
        return summary
    # S3 fetch (cache first)
    cached = fetch_from_cache(ctx, offline=offline, revalidate=revalidate, include_m2=include_m2)
    if cached is not None:
        record(cached)
    else:
        if not refresh and not record(robots.run_robots(ctx)).ok:  # fresh robots before any download
            return summary
        if not record(fetch.run_fetch(ctx, include_m2=include_m2)).ok:
            return summary
    # S4-S6
    if not record(parse.run_parse(ctx)).ok:
        return summary
    if not record(chunk.run_chunk(ctx)).ok:
        return summary
    # S8 load
    target = db_name or ctx.opt("db_name") or default_db_name(ctx.sync_date, ctx.sample)
    resolved: str | None
    try:
        resolved = resolve_dsn(ctx, dsn)
    except LoadError as exc:
        resolved = None
        if load:
            record(StageResult(stage="load", ok=False, messages=[str(exc)]))
            return summary
    do_load = bool(resolved) if load is None else load
    if do_load:
        assert resolved is not None
        if not record(load_mod.run_load(ctx, dsn=resolved, db_name=target)).ok:
            return summary
    else:
        record(StageResult(stage="load", messages=["skipped (no DSN configured or --no-load)"]))
    # S9 gates + sizing report
    gate_ctx = ctx if do_load else _with_options(ctx, skip_db="1")
    gate_result = record(gates.run_gates(gate_ctx, dsn=resolved, db_name=target))
    record(report.run_report(_with_options(gate_ctx, db_name=target)))
    summary.details["gates"] = gate_result.details.get("summary")
    summary.details["database"] = target if do_load else None
    # S10 package
    if package:
        if not do_load:
            record(StageResult(stage="package", ok=False, messages=["--package needs a loaded database"]))
        elif gate_result.ok or ctx.opt("package_on_fail") == "1":
            pkg = record(
                package_mod.run_package(ctx, dsn=resolved, db_name=target, container=container, restore_to=restore_to)
            )
            summary.outputs += pkg.outputs
        else:
            record(StageResult(stage="package", ok=False, messages=["gates failed: not packaged"]))
    return summary


# ------------------------------------------------------------------------------------------- commands
_cli.app.registered_commands = [
    c for c in _cli.app.registered_commands if (c.name or getattr(c.callback, "__name__", "")) != "ingest"
]


@_cli.app.command("ingest")
def ingest(
    ctx: typer.Context,
    sample: _cli.Sample = True,
    refresh: Annotated[bool, typer.Option(help="Re-fetch robots.txt and re-pull the SPARQL snapshot for T")] = False,
    offline: Annotated[bool, typer.Option(help="Never make a network request (reuse every cache)")] = False,
    revalidate: Annotated[bool, typer.Option(help="Conditional GETs for cached files too")] = False,
    load: Annotated[
        bool | None, typer.Option("--load/--no-load", help="Load into PostgreSQL (default: when a DSN is set)")
    ] = None,
    dsn: Annotated[str | None, typer.Option(help="libpq DSN of the build server (default LGX_PG_DSN)")] = None,
    db_name: Annotated[str | None, typer.Option(help="Database name (default legilux_build_<T>[_sample])")] = None,
    package: Annotated[bool, typer.Option(help="pg_dump the release into data/bundles/ after the gates")] = False,
    container: Annotated[str | None, typer.Option(help="Run pg_dump/pg_restore inside this container")] = None,
    restore_to: Annotated[str | None, typer.Option(help="With --package: restore + swap into this database")] = None,
    include_m2: Annotated[bool, typer.Option(help="Also fetch/parse sample entries of stage m2")] = False,
    confirm_full_crawl: Annotated[bool, typer.Option(help="Required with --full (≈ 25k GETs, operator only)")] = False,
) -> None:
    """robots → discover → select → fetch → parse → chunk → load → gates → report [→ package] for T."""
    if not sample and not confirm_full_crawl:
        _cli.err.print("[red]--full is the operator crawl (≈ 25k GETs): add --confirm-full-crawl[/red]")
        raise typer.Exit(2)
    stage_ctx = _cli._context(ctx, sample=sample)
    result = run_ingest(
        stage_ctx,
        refresh=refresh,
        offline=offline,
        revalidate=revalidate,
        load=load,
        dsn=dsn,
        db_name=db_name,
        package=package,
        container=container,
        restore_to=restore_to,
        include_m2=include_m2,
        echo=_cli.err.print,
    )
    typer.echo(result.model_dump_json(indent=2))
    if not result.ok:
        raise typer.Exit(1)


@_cli.app.command("package")
def package_cmd(
    ctx: typer.Context,
    sample: _cli.Sample = True,
    dsn: Annotated[str | None, typer.Option(help="libpq DSN of the build server (default LGX_PG_DSN)")] = None,
    db_name: Annotated[str | None, typer.Option(help="Release database (default legilux_build_<T>[_sample])")] = None,
    out_dir: Annotated[Path | None, typer.Option(help="Output directory (default data/bundles)")] = None,
    container: Annotated[str | None, typer.Option(help="Run pg_dump/pg_restore inside this container")] = None,
    restore_to: Annotated[str | None, typer.Option(help="Restore + verify + swap into this database")] = None,
) -> None:
    """S10 (minimal): pg_dump -Fc of the release database (+ restore check / rename-swap)."""
    _cli._run(
        "lgx_ingest.package:run_package",
        _cli._context(ctx, sample=sample),
        dsn=dsn,
        db_name=db_name,
        out_dir=out_dir,
        container=container,
        restore_to=restore_to,
    )
