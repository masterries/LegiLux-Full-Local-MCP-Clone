"""Serving decision for one act on one date; Python twin of ``lgx.serving_at`` (agent A, M1).

The runtime recomputes everything from dates (PLAN §7.3): status, the window version (may be a stub),
the stored text that serves on ``as_of`` (at most one, EXCLUDE constraint), the label for ``as_of`` and
the next announced version. Branch order mirrors ``db/schema/30_functions.sql`` exactly; the parity is
tested in ``tests/db/test_rules_parity.py``.

Inputs are duck-typed (:class:`ActLike`, :class:`VersionLike`, :class:`RelationLike`) so the same
function runs on loaded ``lgx_core.models`` records and on the selection's manifest windows.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Protocol

from lgx_core.models import (
    ActFlag,
    ActStatus,
    DateWindow,
    ManifestEntry,
    RelationSource,
    RelType,
    TextBasis,
    VersionKind,
)
from lgx_core.rules.status import act_status

AMENDING = (RelType.MODIFIES, RelType.REPEALS)


class ActLike(Protocol):
    @property
    def eli_work(self) -> str: ...
    @property
    def eli_jo(self) -> str | None: ...
    @property
    def dei_min(self) -> dt.date | None: ...
    @property
    def dnl(self) -> dt.date | None: ...
    @property
    def rdf_status(self) -> str | None: ...
    @property
    def text_basis(self) -> TextBasis: ...
    @property
    def flags(self) -> Sequence[ActFlag]: ...


class VersionLike(Protocol):
    @property
    def eli_version(self) -> str: ...
    @property
    def kind(self) -> VersionKind: ...
    @property
    def date_applicability(self) -> dt.date | None: ...
    @property
    def date_end_applicability(self) -> dt.date | None: ...
    @property
    def has_text(self) -> bool: ...
    @property
    def text_valid(self) -> DateWindow | None: ...


class RelationLike(Protocol):
    @property
    def rel_type(self) -> RelType: ...
    @property
    def dst_eli(self) -> str: ...
    @property
    def src_eli(self) -> str: ...
    @property
    def src_dei(self) -> dt.date | None: ...
    @property
    def source(self) -> RelationSource: ...


@dataclass(frozen=True)
class VersionView:
    """Minimal version record (e.g. built from the manifest before any file is fetched)."""

    eli_version: str
    kind: VersionKind
    date_applicability: dt.date | None
    date_end_applicability: dt.date | None
    has_text: bool
    text_valid: DateWindow | None


@dataclass(frozen=True)
class ServingDecision:
    """One row of ``lgx.serving_at(act, as_of)`` with ELIs instead of ids."""

    eli_work: str
    as_of: dt.date
    status: ActStatus
    window_version: str | None
    text_version: str | None
    text_basis: TextBasis
    flags: tuple[ActFlag, ...]
    next_version_date: dt.date | None
    next_version_has_text: bool | None


def version_as_of(versions: Iterable[VersionLike], as_of: dt.date) -> VersionLike | None:
    """The consolidation version whose window contains ``as_of`` (latest dA wins; may be a stub)."""
    window = [
        v
        for v in versions
        if v.kind is not VersionKind.ORIGINAL
        and v.date_applicability is not None
        and v.date_applicability <= as_of
        and (v.date_end_applicability is None or v.date_end_applicability > as_of)
    ]
    return max(window, key=lambda v: v.date_applicability or dt.date.min, default=None)


def text_version_as_of(versions: Iterable[VersionLike], as_of: dt.date) -> VersionLike | None:
    """The stored text whose ``text_valid`` contains ``as_of`` (at most one by construction)."""
    hits = [v for v in versions if v.has_text and v.text_valid is not None and v.text_valid.contains(as_of)]
    if len(hits) > 1:
        raise ValueError(f"overlapping text windows on {as_of}: {[v.eli_version for v in hits]}")
    return hits[0] if hits else None


def _targets(act: ActLike) -> set[str]:
    return {e for e in (act.eli_jo, act.eli_work) if e}


def amended_by_as_of(act: ActLike, relations: Iterable[RelationLike], as_of: dt.date) -> bool:
    targets = _targets(act)
    return any(
        r.dst_eli in targets
        and r.rel_type in AMENDING
        and r.source is RelationSource.SPARQL
        and r.src_dei is not None
        and r.src_dei <= as_of
        for r in relations
    )


def serving_at(
    act: ActLike, versions: Sequence[VersionLike], relations: Sequence[RelationLike], as_of: dt.date
) -> ServingDecision:
    """Status, window version, served text version, label recomputed for ``as_of``, flags, next version."""
    status = act_status(act.dei_min, act.dnl, act.rdf_status, as_of)
    w = version_as_of(versions, as_of)
    t = text_version_as_of(versions, as_of)
    if t is None:
        basis = TextBasis.METADATA_ONLY
    elif t.kind is not VersionKind.ORIGINAL and w is not None and w.eli_version == t.eli_version:
        basis = TextBasis.CONSOLIDATED
    elif t.kind is not VersionKind.ORIGINAL:
        basis = TextBasis.STUB_FALLBACK  # window version is a stub / unknown
    elif act.text_basis is TextBasis.ORIGINAL_NEVER_AMENDED and not amended_by_as_of(act, relations, as_of):
        basis = TextBasis.ORIGINAL_NEVER_AMENDED
    else:
        basis = TextBasis.ORIGINAL_POSSIBLY_OUTDATED
    upcoming = sorted(
        (
            v
            for v in versions
            if v.kind is not VersionKind.ORIGINAL and v.date_applicability is not None and v.date_applicability > as_of
        ),
        key=lambda v: v.date_applicability or dt.date.max,
    )
    nxt = upcoming[0] if upcoming else None
    return ServingDecision(
        eli_work=act.eli_work,
        as_of=as_of,
        status=status,
        window_version=w.eli_version if w else None,
        text_version=t.eli_version if t else None,
        text_basis=basis,
        flags=tuple(act.flags),
        next_version_date=nxt.date_applicability if nxt else None,
        next_version_has_text=nxt.has_text if nxt else None,
    )


def amending_acts_since(
    act: ActLike, relations: Iterable[RelationLike], since: dt.date | None, as_of: dt.date
) -> list[tuple[str, RelType, dt.date]]:
    """Twin of ``lgx.amending_acts_since``: amending/repealing acts in force in ``(since, as_of]``."""
    targets = _targets(act)
    rows = {
        (r.src_eli, r.rel_type, r.src_dei)
        for r in relations
        if r.dst_eli in targets
        and r.rel_type in AMENDING
        and r.source is RelationSource.SPARQL
        and r.src_dei is not None
        and (since is None or r.src_dei > since)
        and r.src_dei <= as_of
    }
    return sorted(rows, key=lambda x: (x[2], x[0], x[1].value))  # type: ignore[return-value]


def views_from_manifest(entries: Iterable[ManifestEntry]) -> list[VersionView]:
    """Version views of one act from its manifest entries: stored texts get ``has_text`` + window."""
    out = []
    for e in entries:
        if e.eli_version is None or e.kind is None:
            continue
        out.append(
            VersionView(
                eli_version=e.eli_version,
                kind=e.kind,
                date_applicability=e.date_applicability if e.kind is not VersionKind.ORIGINAL else None,
                date_end_applicability=e.date_end_applicability if e.kind is not VersionKind.ORIGINAL else None,
                has_text=e.text_valid is not None,
                text_valid=e.text_valid,
            )
        )
    return out
