"""Agent-A CLI commands, registered on the main ``lgx`` app by one import line in ``lgx_ingest/cli.py``.

* ``lgx meta pull [-q q01 …] [--manif sample|all|none]`` — S1 discover (full SPARQL metadata pulls).
* ``lgx meta select [--date YYYY-MM-DD] [--snapshot DIR]`` — S2 selection at a date (default T).
* ``lgx meta golden [--snapshot DIR]`` — golden numbers (PLAN §11.5): expected vs actual.
* ``lgx robots`` / ``lgx robots check`` — S0 (fetch + parse both robots.txt, regenerate the denylist);
  ``lgx robots ack SHA`` is the same as ``lgx robots-ack SHA``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from lgx_ingest import cli as _cli

meta_app = typer.Typer(name="meta", help="SPARQL metadata snapshot and selection (agent A).", no_args_is_help=True)
robots_app = typer.Typer(name="robots", help="S0: robots.txt of both hosts → policy + ELI denylist.")


def _set_option(ctx: typer.Context, key: str, value: str | None) -> None:
    if value is not None:
        ctx.obj.options[key] = value


@meta_app.command("pull")
def meta_pull(
    ctx: typer.Context,
    query: Annotated[list[str] | None, typer.Option("--query", "-q", help="Only these query ids / tables")] = None,
    manif: Annotated[str | None, typer.Option(help="Q-MANIF scope: sample | all | none")] = None,
    sample: _cli.Sample = True,
    dry_run: Annotated[bool, typer.Option(help="List the tables that would be pulled")] = False,
) -> None:
    """S1: flat unsorted SPARQL pulls into data/meta/<T>/ (+ Q17 completeness, Q-MANIF)."""
    _set_option(ctx, "manif", manif)
    _cli._run("lgx_ingest.sparql:run_discover", _cli._context(ctx, sample=sample, dry_run=dry_run), queries=query)


@meta_app.command("select")
def meta_select(
    ctx: typer.Context,
    date: Annotated[str | None, typer.Option("--date", help="Selection date YYYY-MM-DD (default: T)")] = None,
    snapshot: Annotated[Path | None, typer.Option(help="Snapshot directory (default data/meta/<T>)")] = None,
    sample: _cli.Sample = True,
) -> None:
    """S2: selection at a date → manifest, metadata rows, selection report, serving plan."""
    if date is not None:
        ctx.obj.sync_date = _cli._parse_date(date)
    _set_option(ctx, "snapshot", str(snapshot) if snapshot else None)
    _cli._run("lgx_ingest.select:run_select", _cli._context(ctx, sample=sample))


@meta_app.command("golden")
def meta_golden(
    ctx: typer.Context,
    snapshot: Annotated[Path | None, typer.Option(help="Snapshot directory (default: the frozen golden one)")] = None,
) -> None:
    """Golden numbers of PLAN §11.5 on a snapshot: expected vs actual (exit 1 on any difference)."""
    from lgx_ingest.select import GOLDEN_SNAPSHOT, golden_check

    stage_ctx = _cli._context(ctx)
    text, ok = golden_check(snapshot or GOLDEN_SNAPSHOT, stage_ctx.config, ctx.obj.sync_date)
    typer.echo(text)
    if not ok:
        raise typer.Exit(1)


@robots_app.callback(invoke_without_command=True)
def robots_main(ctx: typer.Context) -> None:
    """S0: fetch + parse robots.txt of both hosts, regenerate the denylist (pauses on change)."""
    if ctx.invoked_subcommand is None:
        _cli._run("lgx_ingest.robots:run_robots", _cli._context(ctx))


@robots_app.command("check")
def robots_check(ctx: typer.Context) -> None:
    """Same as `lgx robots`: fetch, compare with the acknowledged sha256, hard-stop if /filestore is disallowed."""
    _cli._run("lgx_ingest.robots:run_robots", _cli._context(ctx))


@robots_app.command("ack")
def robots_ack(ctx: typer.Context, sha256: Annotated[str, typer.Argument(help="sha256 of the new robots.txt")]) -> None:
    """Acknowledge a changed robots.txt so fetching may resume."""
    _cli._run("lgx_ingest.robots:acknowledge", _cli._context(ctx), sha256=sha256)


def register(app: typer.Typer) -> None:
    """Replace the flat ``robots`` command by the ``robots`` group and add the ``meta`` group."""
    app.registered_commands = [
        c for c in app.registered_commands if (c.name or getattr(c.callback, "__name__", "")) != "robots"
    ]
    app.add_typer(robots_app, name="robots")
    app.add_typer(meta_app, name="meta")


register(_cli.app)
