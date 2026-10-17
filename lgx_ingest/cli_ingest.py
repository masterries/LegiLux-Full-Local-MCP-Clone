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
used in development (PLAN §6.2). Full fetches run only inside the nightly window (``--ignore-window`` opts out,
keeping every rate limit). ``--dry-run`` (sample or full) makes **no request at all**: it reuses the robots state
and the SPARQL snapshot of T, re-runs select (local outputs only) and prints the fetch plan — counts per format /
role, cached vs missing, policy refusals, estimated bytes and duration — into
``reports/<T>/fetch_plan_<sample|full>.json``.
"""

from __future__ import annotations

import collections
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


# Never fetched, whatever the selection (PLAN §2.6, §3.2–3.4, §4.1 T3); shown by --dry-run.
NEVER_FETCHED = [
    "any URL outside https://legilux.public.lu/filestore/eli/etat/leg/… (lgx_core.eli.assert_fetchable)",
    "robots.txt-disallowed paths and denylisted ELIs (config/denylist*.yaml + the generated GDPR list)",
    "Mémorial B, RC, naturalisation acts, EU texts, drafts, closed versions' text (not selected)",
    "DOCX / PDF-A duplicates (one file per version, licence-aware picker)",
    "code/environnement_annexes (239.9 MB; metadata-only unless include_env_annexes_text + the allowlist)",
    "files above scope.fetch.max_file_bytes (64 MB) that are not allow-listed",
    "SPARQL / robots again unless --refresh (the snapshot of T is reused)",
]


def _hours(seconds: float) -> float:
    return round(seconds / 3600, 2)


def manifestations_scope(ctx: StageContext) -> str | None:
    """Q-MANIF scope of the snapshot of T: ``sample`` (files only for the sample works) or ``all``."""
    info = ctx.layout.snapshot_info
    return json.loads(info.read_text(encoding="utf-8")).get("manifestations_scope") if info.exists() else None


def _full_file_plan(ctx: StageContext) -> dict[str, Any]:
    """Full mode on a snapshot whose Q-MANIF covered the sample only: the versions that will get a file (exact,
    ``manifestation_candidates`` over the whole metadata) and their formats (``serving_plan``), no request."""
    import math

    import pyarrow.parquet as pq

    from lgx_ingest.sparql import TABLES, load_snapshot, manifestation_iris
    from lgx_ingest.sparql_templates import MAX_VALUES

    snapshot = load_snapshot(ctx.layout.meta_dir, [t for t in TABLES if t != "manifestations"])
    iris = manifestation_iris(snapshot, ctx)
    cols = ["text_basis", "format", "served_kind", "future_version"]
    rows = pq.read_table(ctx.layout.meta_table("serving_plan"), columns=cols).to_pylist()
    texts = [r for r in rows if r["text_basis"] != "metadata_only"]
    formats = collections.Counter(r["format"] or "?" for r in texts)
    kinds = collections.Counter(f"{r['format'] or '?'}/{r['served_kind'] or '?'}" for r in texts)
    return {
        "versions_with_file": len(iris),
        "acts_with_text_by_format": dict(sorted(formats.items())),
        "acts_with_text_by_format_kind": dict(sorted(kinds.items())),
        "future_versions_listed": sum(1 for r in rows if r["future_version"]),
        "q_manif_requests": math.ceil(len(iris) / MAX_VALUES),
        "iris": iris,
    }


def plan_fetch(ctx: StageContext, *, include_m2: bool) -> StageResult:
    """``ingest --dry-run`` fetch step: what a real run would request for T (no network, no state change)."""
    import math

    from lgx_ingest import robots
    from lgx_ingest.fetch import NightlyWindow, RawIndex, load_plan, read_progress, select_targets

    cfg = ctx.config.scope.fetch
    entries = load_plan(ctx.layout.manifest_path)
    targets = select_targets(entries, sample=ctx.sample, include_m2=include_m2)
    full_plan = None
    if not ctx.sample and manifestations_scope(ctx) != "all":
        # the manifest has files only where Q-MANIF ran (the sample works): count the full list from the metadata
        full_plan = _full_file_plan(ctx)
    index = RawIndex(ctx.layout)
    done = read_progress(ctx.layout)
    try:
        policy = robots.load_policy(ctx)
    except FileNotFoundError:
        policy = None
    by_format: collections.Counter[str] = collections.Counter()
    missing_by_format: collections.Counter[str] = collections.Counter()
    by_role: collections.Counter[str] = collections.Counter()
    refused: collections.Counter[str] = collections.Counter()
    cached_bytes: dict[str, list[int]] = collections.defaultdict(list)
    kind_bytes: dict[str, list[int]] = collections.defaultdict(list)
    missing: list[str] = []
    for t in targets:
        fmt = t.entry.file.source_format.value if t.entry.file else "?"
        by_format[fmt] += 1
        by_role[t.entry.role.value] += 1
        if policy is not None:
            if not policy.allows(t.url):
                refused["robots"] += 1
                continue
            if any(e and policy.is_denied_eli(e) for e in (t.entry.eli_version, t.entry.eli_work, t.url)):
                refused["denylist"] += 1
                continue
        raw = index.get(t.url)
        if raw is not None:
            cached_bytes[fmt].append(raw.bytes)
            kind_bytes[f"{fmt}/{t.entry.kind.value if t.entry.kind else '?'}"].append(raw.bytes)
        elif t.url not in done:
            missing.append(t.url)
            missing_by_format[fmt] += 1
    # [E] bytes: mean size per format of the files already cached (the sample), PDFs are the code-recueils
    means = {f: sum(v) / len(v) for f, v in cached_bytes.items() if v}
    kind_means = {k: sum(v) / len(v) for k, v in kind_bytes.items() if v}
    est_bytes = sum(n * means.get(f, 0) for f, n in missing_by_format.items())
    if full_plan is not None:  # every listed version except the ones already cached
        iris = set(full_plan["iris"])
        cached_versions = {r.eli_version for r in index.by_url.values() if r.eli_version in iris}
        missing = [i for i in full_plan["iris"] if i not in cached_versions]
        total = sum(full_plan["acts_with_text_by_format"].values()) or 1
        share = {f: c / total for f, c in full_plan["acts_with_text_by_format"].items()}
        missing_by_format = collections.Counter({f: round(len(missing) * x) for f, x in share.items()})
        # [E] per (format, kind) sample means (codes are large, JO originals small); per-format mean as fallback
        est_bytes = sum(
            n * kind_means.get(k, means.get(k.split("/")[0], 0))
            for k, n in full_plan["acts_with_text_by_format_kind"].items()
        )
    spacing = max(ctx.settings.rate_limit_s, cfg.min_spacing_s)
    n = len(missing)
    per_hour = min(cfg.max_requests_per_hour, int(3600 / spacing))
    window = NightlyWindow.parse(cfg.nightly_window) if cfg.nightly_window and not ctx.sample else None
    per_window = int(window.hours * per_hour) if window else None
    result = StageResult(stage="fetch", messages=["dry run: no request made"])
    result.counts = {
        "targets": len(targets),
        "cached": sum(len(v) for v in cached_bytes.values()),
        "missing": n,
        **{f"format.{k}": v for k, v in sorted(by_format.items())},
        **{f"missing.{k}": v for k, v in sorted(missing_by_format.items())},
        **{f"refused.{k}": v for k, v in sorted(refused.items())},
    }
    if full_plan is not None:
        result.counts.update(
            {"targets": full_plan["versions_with_file"], "cached": full_plan["versions_with_file"] - n, "missing": n}
        )
        result.counts.update({f"missing.{k}": v for k, v in sorted(missing_by_format.items())})
    result.details = {
        "roles": dict(sorted(by_role.items())),
        "robots_state": policy is not None,
        "manifestations_scope": manifestations_scope(ctx),
        "full_file_plan": {k: v for k, v in full_plan.items() if k != "iris"} if full_plan else None,
        "requests": {
            "files": n,
            "q_manif_sparql": full_plan["q_manif_requests"] if full_plan else 0,
            "robots_and_sparql": "≈ 2 robots.txt + ≈ 12 SPARQL only with --refresh or without a snapshot for T",
        },
        "duration_estimate": {
            "at_1_req_per_s_h": _hours(n * 1.0),
            "at_spacing_h": _hours(n * spacing),
            "spacing_s": spacing,
            "hourly_ceiling": cfg.max_requests_per_hour,
            "daily_ceiling": cfg.max_requests_per_day,
            "fetch_hours_under_ceilings": round(n / per_hour, 2) if per_hour else None,
            "nightly_window": cfg.nightly_window if window else None,
            "requests_per_window": per_window,
            "nights": math.ceil(n / per_window) if per_window else None,
        },
        "bytes_estimate": {
            "raw_missing_bytes_E": int(est_bytes),
            "mean_bytes_per_format_from_cache": {k: int(v) for k, v in sorted(means.items())},
            "mean_bytes_per_format_kind_from_cache": {k: int(v) for k, v in sorted(kind_means.items())},
            "note": "[E] sample means per (format, kind); add ≈ 1–1.5× for the parsed/units/chunks caches; the build"
            " database is ≈ 0.9–1.3 GB lexical-only, the dump ≈ 0.2–0.5 GB (PLAN §4.2)",
        },
        "never_fetched": NEVER_FETCHED,
        "missing_first": missing[:20],
    }
    out = ctx.layout.reports_dir / f"fetch_plan_{'sample' if ctx.sample else 'full'}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({**result.model_dump(mode="json"), "missing": missing}, indent=1), encoding="utf-8")
    result.outputs = [str(out)]
    return result


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
    ignore_window: bool = False,
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
    if ignore_window:
        ctx = _with_options(ctx, ignore_window="1")
    if ctx.dry_run:
        offline = True  # a dry run never makes a request
        if refresh:
            raise typer.BadParameter("--dry-run never pulls: drop --refresh")
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
    # S1b Q-MANIF over every served version: a snapshot pulled for the sample has files for the sample works only
    if not ctx.sample and not ctx.dry_run and manifestations_scope(ctx) != "all":
        if offline:
            record(
                StageResult(
                    stage="discover",
                    ok=False,
                    messages=["--full needs Q-MANIF for every served version (snapshot scope: sample); drop --offline"],
                )
            )
            return summary
        if not record(sparql.run_discover(ctx, ["manifestations"])).ok:
            return summary
    # S2 select (pure and local: a dry run writes the selection too, the plan below reads it)
    if not record(select.run_select(dataclasses.replace(ctx, dry_run=False))).ok:
        return summary
    # S3 fetch (cache first)
    if ctx.dry_run:  # the plan, then stop: nothing is fetched, parsed or loaded
        plan = record(plan_fetch(ctx, include_m2=include_m2))
        summary.details["plan"] = {"counts": plan.counts, **plan.details, "report": plan.outputs}
        summary.messages.append("dry run: nothing fetched, parsed or loaded")
        return summary
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
    ignore_window: Annotated[
        bool, typer.Option(help="--full: fetch outside the nightly window (operator opt-in; rate limits kept)")
    ] = False,
    dry_run: Annotated[
        bool, typer.Option(help="No request: reuse robots + snapshot, select, print the fetch plan and stop")
    ] = False,
) -> None:
    """robots → discover → select → fetch → parse → chunk → load → gates → report [→ package] for T."""
    if not sample and not confirm_full_crawl and not dry_run:
        _cli.err.print("[red]--full is the operator crawl (≈ 25k GETs): add --confirm-full-crawl (or --dry-run)[/red]")
        raise typer.Exit(2)
    stage_ctx = _cli._context(ctx, sample=sample, dry_run=dry_run)
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
        ignore_window=ignore_window,
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
