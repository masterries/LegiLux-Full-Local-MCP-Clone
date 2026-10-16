"""S4 parse: raw files → ParseResult (units, relations, issues). Contract: docs/dev/INTERFACES.md §5.

Dispatch on ``entry.file.source_format``: XML → :mod:`.akn`, HTML → :mod:`.html`, PDF → :mod:`.pdf`
(M2; PDF entries are reported as ``skipped_pdf``). Results are cached in
``parsed/<sha256>.<parser_version>.json``; units are written to ``units/<T>/<slug>.jsonl`` and AKN
relations to ``units/<T>/<slug>.relations.jsonl``. A cached result is re-stamped with the provenance
of the *current* manifest entry (label, flags, doc type …), because one file can serve several T.
"""

from __future__ import annotations

import collections
import json
from pathlib import Path

from lgx_core.io import atomic_write_text, write_jsonl
from lgx_core.models import ManifestEntry, ParseResult, RawFile, SourceFormat, sha256_hex
from lgx_ingest.context import StageContext, StageResult
from lgx_ingest.parse import akn, html, pdf
from lgx_ingest.parse.common import ParseError

__all__ = ["PARSER_VERSIONS", "ParseError", "parse_bytes", "parse_file", "parser_version", "restamp", "run_parse"]

PARSER_VERSIONS = {
    SourceFormat.XML: akn.PARSER_VERSION,
    SourceFormat.HTML: html.PARSER_VERSION,
    SourceFormat.PDF: pdf.PARSER_VERSION,
}
CORE_DOC_TYPES = frozenset({"CODE", "CODE_RECUEIL", "Constitution"})


def parser_version(fmt: SourceFormat) -> str:
    return PARSER_VERSIONS[fmt]


def parse_bytes(data: bytes, entry: ManifestEntry, raw: RawFile) -> ParseResult:
    """Parse already-read bytes (pure)."""
    if entry.file is None:
        raise ParseError(f"{entry.eli_version}: manifest entry without file")
    fmt = entry.file.source_format
    if fmt is SourceFormat.XML:
        return akn.parse_akn(data, entry, raw)
    if fmt is SourceFormat.HTML:
        return html.parse_html(data, entry, raw)
    return pdf.parse_pdf(data, entry, raw)


def parse_file(path: Path, entry: ManifestEntry, raw: RawFile) -> ParseResult:
    """Parse one cached file for one manifest entry (pure apart from reading ``path``)."""
    data = path.read_bytes()
    if sha256_hex(data) != raw.sha256:
        raise ParseError(f"{path}: sha256 differs from raw index ({raw.sha256})")
    return parse_bytes(data, entry, raw)


def restamp(result: ParseResult, entry: ManifestEntry, raw: RawFile) -> ParseResult:
    """Copy of a (cached) result carrying the identity/provenance of ``entry`` and ``raw``."""
    if entry.file is None or entry.eli_version is None:
        raise ParseError(f"{entry.eli_work}: manifest entry without file/version")
    stamp = {
        "eli_work": entry.eli_work,
        "eli_version": entry.eli_version,
        "doc_type": entry.doc_type,
        "text_basis": entry.text_basis,
        "flags": list(entry.flags),
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


def run_parse(ctx: StageContext, *, cache_only: bool = False) -> StageResult:
    """Parse every fetched manifest entry; write data/parsed/ and data/units/<T>/.

    Options: ``--opt manifest=PATH``, ``--opt include_m2=1``, ``--opt reparse=1`` (ignore the cache).
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
    for t in targets:
        raw = index.get(t.url)
        if raw is None:
            counts["missing_raw"] += 1
            continue
        fmt = t.entry.file.source_format if t.entry.file else None
        if fmt is SourceFormat.PDF:
            counts["skipped_pdf"] += 1  # PDF-only codes: M2
            continue
        assert fmt is not None
        cache = ctx.layout.parsed_path(raw.sha256, parser_version(fmt))
        parsed = None if reparse else _load_cached(cache)
        if parsed is None and cache_only:
            counts["missing_parsed"] += 1
            continue
        if parsed is None:
            try:
                parsed = parse_file(index.path_of(raw), t.entry, raw)
            except (ParseError, NotImplementedError, OSError) as exc:
                counts["failed"] += 1
                failures.append(
                    {"eli_version": t.entry.eli_version or "", "doc_type": t.entry.doc_type, "error": str(exc)}
                )
                continue
            atomic_write_text(cache, parsed.model_dump_json())
            counts["parsed"] += 1
        else:
            counts["cached"] += 1
        for entry in by_url.get(t.url) or [t.entry]:
            res = restamp(parsed, entry, raw)
            assert entry.eli_version is not None
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
    report = ctx.layout.units_dir / "_parse_report.json"
    atomic_write_text(report, json.dumps(result.model_dump(mode="json"), indent=2, ensure_ascii=False) + "\n")
    return result
