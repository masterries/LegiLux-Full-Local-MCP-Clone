"""S4 parse + S5 structure: raw files → ParseResult (units, relations, issues). Contract: INTERFACES.md §5.

Dispatch on ``entry.file.source_format``: XML → :mod:`.akn`, HTML → :mod:`.html`, PDF → :mod:`.pdf`
(born-digital codes and consolidations; ``scope.pdf_codes`` = ``articles`` | ``pages``). Results are
cached in ``parsed/<sha256>.<parser_version>.json``; units are written to ``units/<T>/<slug>.jsonl`` and AKN
relations to ``units/<T>/<slug>.relations.jsonl``. A cached result is re-stamped with the provenance of
the *current* manifest entry (label, flags, doc type …), because one file can serve several T.

Structure pass (PLAN §2.6, after every file of the run is parsed): XML code-recueils and recueils are
deduplicated against the member consolidations stored in this build (:func:`.recueil.deduplicate`);
PDF code-recueils get their member act ELIs from ``config/recueil_members.yaml``
(:func:`.recueil.apply_members`), then lose the copies of member articles whose member consolidation is stored
and served in this build (:func:`.recueil.dedup_pdf_copies`; a member served from its JO original keeps the copy).
Report per recueil version: ``units/<T>/<slug>.structure.json`` (stats, issues, every dedup outcome). The parse
cache always holds the un-deduplicated parser output.
"""

from __future__ import annotations

import collections
import datetime as dt
import json
import os
from pathlib import Path
from typing import Any

from lgx_core.eli import expand
from lgx_core.io import atomic_write_text, write_jsonl
from lgx_core.models import (
    ActFlag,
    DataQualityIssue,
    ManifestEntry,
    ParseResult,
    RawFile,
    SourceFormat,
    TextBasis,
    VersionRole,
    sha256_hex,
)
from lgx_ingest.context import StageContext, StageResult
from lgx_ingest.parse import akn, html, pdf, recueil
from lgx_ingest.parse.common import ParseError

__all__ = [
    "PARSER_VERSIONS",
    "ParseError",
    "parse_bytes",
    "parse_file",
    "parser_version",
    "restamp",
    "run_parse",
    "structure_recueil",
]

PARSER_VERSIONS = {
    SourceFormat.XML: akn.PARSER_VERSION,
    SourceFormat.HTML: html.PARSER_VERSION,
    SourceFormat.PDF: pdf.PARSER_VERSION,
}
CORE_DOC_TYPES = frozenset({"CODE", "CODE_RECUEIL", "Constitution"})
RECUEIL_DOC_TYPES = frozenset({"CODE_RECUEIL", "RECUEIL"})


def parser_version(fmt: SourceFormat, *, pdf_mode: str = "articles") -> str:
    """Parser version (= parse-cache key) of a format; the PDF page mode has its own key."""
    if fmt is SourceFormat.PDF and pdf_mode == "pages":
        return f"{pdf.PARSER_VERSION}+pages"
    return PARSER_VERSIONS[fmt]


def parse_bytes(
    data: bytes, entry: ManifestEntry, raw: RawFile, *, pdf_mode: str = "articles", workers: int = 1
) -> ParseResult:
    """Parse already-read bytes (pure)."""
    if entry.file is None:
        raise ParseError(f"{entry.eli_version}: manifest entry without file")
    fmt = entry.file.source_format
    if fmt is SourceFormat.XML:
        return akn.parse_akn(data, entry, raw)
    if fmt is SourceFormat.HTML:
        return html.parse_html(data, entry, raw)
    return pdf.parse_pdf(data, entry, raw, mode=pdf_mode, workers=workers)


def parse_file(
    path: Path, entry: ManifestEntry, raw: RawFile, *, pdf_mode: str = "articles", workers: int = 1
) -> ParseResult:
    """Parse one cached file for one manifest entry (pure apart from reading ``path``)."""
    data = path.read_bytes()
    if sha256_hex(data) != raw.sha256:
        raise ParseError(f"{path}: sha256 differs from raw index ({raw.sha256})")
    return parse_bytes(data, entry, raw, pdf_mode=pdf_mode, workers=workers)


def restamp(result: ParseResult, entry: ManifestEntry, raw: RawFile) -> ParseResult:
    """Copy of a (cached) result carrying the identity/provenance of ``entry`` and ``raw``."""
    if entry.file is None or entry.eli_version is None:
        raise ParseError(f"{entry.eli_work}: manifest entry without file/version")
    flags = list(entry.flags)
    if entry.file.source_format is SourceFormat.PDF and ActFlag.PDF_EXTRACTED not in flags:
        flags.append(ActFlag.PDF_EXTRACTED)  # every PDF-derived unit carries W_PDF_EXTRACTED
    stamp = {
        "eli_work": entry.eli_work,
        "eli_version": entry.eli_version,
        "doc_type": entry.doc_type,
        "text_basis": entry.text_basis,
        "flags": flags,
        "licence": entry.file.licence,
        "source_url": raw.url,
    }
    units = [u.model_copy(update=stamp) for u in result.units]
    relations = [
        r.model_copy(update={"src_version_eli": entry.eli_version}) if r.src_version_eli else r
        for r in result.relations
    ]
    rel_src = {"src_eli": entry.eli_work}
    relations = [r.model_copy(update=rel_src) if r.source.value == "akn-ref" else r for r in relations]
    return result.model_copy(
        update={"eli_work": entry.eli_work, "eli_version": entry.eli_version, "units": units, "relations": relations}
    )


def _load_cached(path: Path) -> ParseResult | None:
    try:
        return ParseResult.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def is_recueil_entry(entry: ManifestEntry) -> bool:
    return pdf.is_recueil(entry) or entry.doc_type in RECUEIL_DOC_TYPES


def served_versions(entries: list[ManifestEntry]) -> dict[str, str]:
    """Work ELI → the version the selection serves at T (role current, else fallback)."""
    out: dict[str, str] = {}
    for e in entries:
        if not e.eli_version or e.role not in (VersionRole.CURRENT, VersionRole.FALLBACK) or e.text_valid is None:
            continue
        if e.role is VersionRole.CURRENT or e.eli_work not in out:
            out[e.eli_work] = e.eli_version
    return out


def structure_recueil(
    res: ParseResult,
    entry: ManifestEntry,
    *,
    canonical: dict[str, recueil.Canonical],
    served: dict[str, str],
    version_work: dict[str, str],
    members: list[recueil.MemberRef],
    first_seen: dt.date,
    dedup: bool = True,
    consolidated: frozenset[str] | set[str] = frozenset(),
) -> tuple[ParseResult, dict[str, Any] | None]:
    """S5 for one recueil version: member ELIs (PDF) and wId deduplication (XML).

    ``consolidated``: the served versions that are consolidations (a PDF copy is dropped only for those; default
    none, i.e. every copy kept)."""
    units, relations, issues = list(res.units), list(res.relations), list(res.issues)
    stats = dict(res.stats)
    added: dict[str, int] = {"units_before_structure": len(units)}
    new_issues: list[DataQualityIssue] = []
    dedup_report: dict[str, Any] | None = None
    if res.source_format is SourceFormat.PDF:
        units, mstats = recueil.apply_members(units, members)
        mstats.pop("routed_unattached_examples", None)
        added.update(mstats)
        if dedup:
            d = recueil.dedup_pdf_copies(
                units,
                relations,
                canonical=canonical,
                served=served,
                consolidated=consolidated,
                first_seen=first_seen,
            )
            units, relations, dedup_report = d.units, d.relations, d.report
            new_issues.extend(d.issues)
            added.update({f"dedup_{k}": v for k, v in d.report["counts"].items()})
            added["dedup_relations_dropped"] = d.report["relations_dropped"]
        # checked on the final units: a routed member whose copies were all dropped is as bad as an unattached one
        unattached = recueil.routed_unattached(units, members)
        added["routed_unattached"] = len(unattached)
        if unattached:
            new_issues.append(
                DataQualityIssue(
                    entity_eli=entry.eli_version or entry.eli_work,
                    code="recueil_route_unattached",
                    detail=f"{len(unattached)} routed member(s) without an article in the code text: "
                    + ", ".join(e.rsplit("/eli/", 1)[-1] for e in unattached[:20]),
                    first_seen=first_seen,
                )
            )
    elif dedup and any(u.from_recueil_wid for u in units):
        d = recueil.deduplicate(
            units, relations, canonical=canonical, served=served, version_work=version_work, first_seen=first_seen
        )
        units, relations, dedup_report = d.units, d.relations, d.report
        new_issues = d.issues
        added.update({f"dedup_{k}": v for k, v in d.report["counts"].items()})
        added["dedup_relations_dropped"] = d.report["relations_dropped"]
    added["units"] = len(units)
    stats.update(added)
    report = {
        "version": entry.eli_version,
        "structure": recueil.DEDUP_VERSION,
        "stats": added,
        "issues": [i.model_dump(mode="json") for i in new_issues],
        "dedup": dedup_report,
    }
    issues.extend(new_issues)
    out = res.model_copy(update={"units": units, "relations": relations, "issues": issues, "stats": stats})
    return out, report


def _members_for(ctx: StageContext, entry: ManifestEntry) -> list[recueil.MemberRef]:
    keys = {entry.eli_work, entry.eli_version}
    out = []
    for m in ctx.config.recueil_members.members:
        rec = expand(m.recueil)
        if rec in keys or (m.recueil_version and expand(m.recueil_version) in keys):
            out.append(recueil.MemberRef(expand(m.member), m.member_key, m.part_heading, route=m.route))
    return out


def run_parse(ctx: StageContext, *, cache_only: bool = False) -> StageResult:
    """Parse every fetched manifest entry; write data/parsed/ and data/units/<T>/.

    Options: ``--opt manifest=PATH``, ``--opt include_m2=1``, ``--opt reparse=1`` (ignore the cache),
    ``--opt pdf_workers=N`` (parallel PDF page extraction, default min(4, CPUs)).
    """
    from lgx_ingest.fetch import RawIndex, _entries, fetch_url, select_targets

    result = StageResult(stage="structure" if cache_only else "parse")
    try:
        entries, manifest = _entries(ctx)
    except FileNotFoundError as exc:
        result.ok = False
        result.messages.append(str(exc))
        return result
    include_m2 = ctx.opt("include_m2") in ("1", "true", "yes")
    reparse = ctx.opt("reparse") in ("1", "true", "yes") and not cache_only
    pdf_mode = ctx.config.scope.pdf_codes
    workers = int(ctx.opt("pdf_workers") or min(4, os.cpu_count() or 1))
    targets = select_targets(entries, sample=ctx.sample, include_m2=include_m2)
    # every entry sharing a file gets its own units (e.g. a code version served for one T only)
    by_url: dict[str, list[ManifestEntry]] = collections.defaultdict(list)
    for e in entries:
        if e.file is not None and e.eli_version:
            by_url[fetch_url(e)].append(e)
    index = RawIndex(ctx.layout)
    counts: collections.Counter[str] = collections.Counter()
    failures: list[dict[str, str]] = []
    stats: dict[str, dict[str, int]] = {}
    done: list[tuple[ManifestEntry, ParseResult, RawFile]] = []
    for t in targets:
        raw = index.get(t.url)
        if raw is None:
            counts["missing_raw"] += 1
            continue
        fmt = t.entry.file.source_format if t.entry.file else None
        assert fmt is not None
        cache = ctx.layout.parsed_path(raw.sha256, parser_version(fmt, pdf_mode=pdf_mode))
        parsed = None if reparse else _load_cached(cache)
        if parsed is None and cache_only:
            counts["missing_parsed"] += 1
            continue
        if parsed is None:
            try:
                parsed = parse_file(index.path_of(raw), t.entry, raw, pdf_mode=pdf_mode, workers=workers)
            except (ParseError, NotImplementedError, OSError) as exc:
                counts["failed"] += 1
                failures.append(
                    {"eli_version": t.entry.eli_version or "", "doc_type": t.entry.doc_type, "error": str(exc)}
                )
                continue
            atomic_write_text(cache, parsed.model_dump_json())
            counts["parsed"] += 1
            counts[f"parsed_{fmt.value}"] += 1
        else:
            counts["cached"] += 1
        for entry in by_url.get(t.url) or [t.entry]:
            done.append((entry, restamp(parsed, entry, raw), raw))
    # ---- S5 structure: recueil deduplication (XML) and member ELIs (PDF), after every file is parsed
    version_work = {e.eli_version: e.eli_work for e in entries if e.eli_version}
    served = served_versions(entries)
    consolidated = {e.eli_version for e in entries if e.eli_version and e.text_basis is TextBasis.CONSOLIDATED}
    canonical = {
        e.eli_version: recueil.Canonical.of(e.eli_version, e.eli_work, res.units)
        for e, res, _ in done
        if e.eli_version and not is_recueil_entry(e)
    }
    dedup_reports: dict[str, dict[str, Any]] = {}
    for entry, res, raw in done:
        assert entry.eli_version is not None
        if is_recueil_entry(entry):
            res, report = structure_recueil(
                res,
                entry,
                first_seen=raw.fetched_at.date(),
                canonical=canonical,
                served=served,
                version_work=version_work,
                members=_members_for(ctx, entry),
                dedup=ctx.config.scope.recueil_dedup,
                consolidated=consolidated,
            )
            path = ctx.layout.units_path(entry.eli_version).with_suffix(".structure.json")
            atomic_write_text(path, json.dumps(report, indent=1, ensure_ascii=False) + "\n")
            if report["dedup"] is not None:
                dedup_reports[entry.eli_version] = report["dedup"]
                counts["dedup_dropped"] += report["dedup"]["dropped"]
                counts["dedup_kept"] += report["dedup"]["kept"]
        write_jsonl(ctx.layout.units_path(entry.eli_version), res.units)
        write_jsonl(ctx.layout.unit_relations_path(entry.eli_version), res.relations)
        counts["versions"] += 1
        counts["units"] += len(res.units)
        counts["relations"] += len(res.relations)
        counts["issues"] += len(res.issues)
        stats[entry.eli_version] = res.stats
    result.counts.update(counts)
    result.details["manifest"] = str(manifest)
    result.details["failures"] = failures
    result.details["stats"] = stats
    result.details["dedup"] = {k: {"dropped": v["dropped"], "kept": v["kept"]} for k, v in dedup_reports.items()}
    result.outputs = [str(ctx.layout.units_dir), str(ctx.layout.parsed_dir)]
    attempted = counts["parsed"] + counts["cached"] + counts["failed"]
    core_failed = [f for f in failures if f["doc_type"] in CORE_DOC_TYPES]
    if core_failed or (attempted and counts["failed"] / attempted > 0.01):
        result.ok = False
        result.messages.append(
            f"parse failures: {counts['failed']}/{attempted} (core: {len(core_failed)}) — PLAN §6.8 gate"
        )
    if counts["missing_raw"]:
        result.messages.append(f"{counts['missing_raw']} target(s) not fetched yet (run `lgx fetch`)")
    report_path = ctx.layout.units_dir / "_parse_report.json"
    atomic_write_text(report_path, json.dumps(result.model_dump(mode="json"), indent=2, ensure_ascii=False) + "\n")
    return result
