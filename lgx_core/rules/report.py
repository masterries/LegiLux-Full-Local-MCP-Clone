"""``selection_report.json`` (INTERFACES §4): counts, golden numbers, anomalies, sample check."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any

from lgx_core.eli import relative
from lgx_core.models import Act, DataQualityIssue, ManifestEntry, TextBasis, VersionRole
from lgx_core.rules import golden

if TYPE_CHECKING:  # pragma: no cover
    from lgx_core.rules.select import Outcome, _Selector


def _sample_actual(out: Outcome | None) -> dict[str, Any]:
    if out is None:
        return {"text_basis": None, "format": None, "flags": [], "metadata_reason": None, "role": None}
    cur = out.current
    text = out.text_basis is not TextBasis.METADATA_ONLY
    fmt = cur.pick.source_format.value if text and cur and cur.pick.source_format else ("none" if not text else None)
    return {
        "text_basis": out.text_basis.value,
        "format": fmt,
        "flags": sorted(f.value for f in out.flags),
        "metadata_reason": out.metadata_reason.value if out.metadata_reason else None,
        "role": cur.role.value if text and cur else None,
        "version": relative(cur.eli) if text and cur else None,
        "future": sorted(relative(s.eli) for s in out.served if s.role is VersionRole.FUTURE),
    }


def sample_check(sel: _Selector, outcomes: Sequence[Outcome]) -> dict[str, Any]:
    by_work = {o.eli_work: o for o in outcomes}
    sample = sel.config.sample
    mismatches = []
    matched = 0
    present = 0
    for w in sample.works:
        out = by_work.get(w.eli_work)
        if out is None and sel.restricted:
            continue  # restricted selection (works=…) without this sample work
        present += 1
        act = _sample_actual(out)
        exp = w.expect
        problems = []
        if act["text_basis"] != exp.text_basis.value:
            problems.append("text_basis")
        if act["format"] != exp.format:
            problems.append("format")
        if not {f.value for f in exp.flags} <= set(act["flags"]):
            problems.append("flags")
        if exp.metadata_reason and act["metadata_reason"] != exp.metadata_reason.value:
            problems.append("metadata_reason")
        if exp.role and act["role"] != exp.role:
            problems.append("role")
        if w.version and exp.text_basis is not TextBasis.METADATA_ONLY and act.get("version") != w.version:
            problems.append("version")
        if w.extra_versions and not set(w.extra_versions) <= set(act.get("future", [])):
            problems.append("extra_versions")
        if problems:
            expected = exp.model_dump(mode="json") | {"version": w.version, "extra_versions": w.extra_versions}
            mismatches.append({"work": w.work, "fields": problems, "expected": expected, "actual": act})
        else:
            matched += 1
    return {
        "snapshot_date": sample.snapshot_date.isoformat(),
        "works": present,
        "matched": matched,
        "mismatches": mismatches,
    }


def build_report(
    sel: _Selector,
    outcomes: Sequence[Outcome],
    acts: Sequence[Act],
    manifest: Sequence[ManifestEntry],
    issues: Sequence[DataQualityIssue],
    plan: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    from lgx_core.rules.select import RULES_VERSION

    by_flag: Counter[str] = Counter(f.value for a in acts for f in a.flags)
    by_type_basis: Counter[str] = Counter(f"{a.doc_type}:{a.text_basis.value}" for a in acts)
    stored = [e for e in manifest if e.text_valid is not None]
    info = sel.ix.snapshot.info
    report: dict[str, Any] = {
        "rules_version": RULES_VERSION,
        "sync_date": sel.T.isoformat(),
        "horizon": sel.H.isoformat(),
        "snapshot_date": info.get("sync_date"),
        "config_digest": sel.config.digest(),
        "snapshot_digest": info.get("snapshot_digest"),
        "licence_policy": sel.scope.licence_policy,
        "licence_sources": dict(Counter(o.licence_source or "none" for o in outcomes)),
        "manifestations_scope": info.get("manifestations_scope"),
        "act_formats_coverage": info.get("act_formats_coverage"),
        "counts": {
            "acts": len(acts),
            "excluded": dict(sorted(sel.excluded.items())),
            "manifest_entries": len(manifest),
            "stored_texts": len(stored),
            "stored_texts_with_file": sum(1 for e in stored if e.file is not None),
            "by_text_basis": dict(sorted(Counter(a.text_basis.value for a in acts).items())),
            "by_flag": dict(sorted(by_flag.items())),
            "by_role": dict(sorted(Counter(e.role.value for e in manifest).items())),
            "by_reason": dict(sorted(Counter(e.reason for e in manifest).items())),
            "by_act_reason": dict(sorted(Counter(o.reason for o in outcomes).items())),
            "by_format": dict(sorted(Counter(e.file.source_format.value for e in stored if e.file).items())),
            "by_doc_type_text_basis": dict(sorted(by_type_basis.items())),
            "data_quality_issues": dict(sorted(Counter(i.code for i in issues).items())),
        },
        "golden": {
            "research": {} if sel.restricted else golden.research_numbers(sel.ix.snapshot, sel.T),
            "selection": golden.selection_numbers(plan),
        },
        "anomalies": [
            {"code": code, "count": len(items), "examples": sorted(items)[:5]}
            for code, items in sorted(sel.anomalies.items())
        ],
        "sample": sample_check(sel, outcomes),
    }
    return report
