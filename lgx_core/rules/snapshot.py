"""The frozen SPARQL metadata snapshot: table schemas, literal parsing and the in-memory form.

``lgx discover`` (``lgx_ingest.sparql``) writes one parquet file per table under ``data/meta/<T>/``;
``lgx select`` reads them back into a :class:`MetaSnapshot` (plain rows) for the pure selection
function. Contract: ``docs/dev/INTERFACES.md`` §3.

Literal rules (Appendix A.0): datetimes are parsed on the client because some ``dct:modified`` literals
are malformed (``…472Z+02:00``); a value that does not parse becomes ``None`` and the raw string is
kept in ``<col>_raw``. Dates are never parsed from URIs.
"""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any
from zoneinfo import ZoneInfo

LUXEMBOURG = ZoneInfo("Europe/Luxembourg")


class ColKind(StrEnum):
    """How a SPARQL CSV column is stored."""

    IRI = "iri"  # kept as the full IRI string
    CODE = "code"  # vocabulary IRI reduced to its last path segment (…/resource-type/LOI → LOI)
    STR = "str"
    DATE = "date"  # date32 + <col>_raw when unparsable
    DATETIME = "datetime"  # UTC timestamp + <col>_raw when unparsable
    INT = "int"


@dataclass(frozen=True)
class TableSpec:
    """One snapshot table: the query that fills it and its typed columns (SPARQL variable names)."""

    name: str
    query: str
    columns: Mapping[str, ColKind]
    key: str  # distinct-subject column for completeness and diffs
    construct: bool = False

    @property
    def raw_columns(self) -> list[str]:
        return [f"{c}_raw" for c, k in self.columns.items() if k in (ColKind.DATE, ColKind.DATETIME)]


IRI, C, S, D, DT = ColKind.IRI, ColKind.CODE, ColKind.STR, ColKind.DATE, ColKind.DATETIME

TABLES: dict[str, TableSpec] = {
    spec.name: spec
    for spec in (
        TableSpec(
            "acts",
            "q01_acts",
            {"a": IRI, "cw": IRI, "type": C, "st": C, "dei": D, "dnl": D, "pub": D},
            key="a",
        ),
        TableSpec(
            "consolidations",
            "q02_consolidations",
            {"c": IRI, "cw": IRI, "d": D, "end": D, "st": C, "type": C, "created": DT, "pub": D},
            key="c",
        ),
        TableSpec("conso_formats", "q03_conso_formats", {"c": IRI, "uf": C, "lic": IRI, "lang": C}, key="c"),
        TableSpec(
            "conso_base_acts",
            "q04_conso_base_acts",
            {"cw": IRI, "act": IRI, "ast": C, "atype": C, "dnl": D},
            key="cw",
        ),
        TableSpec(
            "rel_modifies_repeals",
            "q05_rel_modifies_repeals",
            {"src": IRI, "rel": C, "tgt": IRI, "dei": D},
            key="src",
        ),
        TableSpec("rel_other", "q06_rel_other", {"src": IRI, "rel": C, "tgt": IRI}, key="src"),
        TableSpec("titles", "q07_titles", {"a": IRI, "title": S}, key="a"),
        TableSpec("memorial_refs", "q08_memorial_refs", {"a": IRI, "h": S, "mem": IRI}, key="a"),
        TableSpec(
            "vocab",
            "q10_vocabularies.construct",
            {"concept_uri": IRI, "scheme": IRI, "code": S, "label_fr": S, "label_de": S, "label_en": S},
            key="concept_uri",
            construct=True,
        ),
        TableSpec("act_formats", "q19_act_formats", {"a": IRI, "uf": C, "lic": IRI}, key="a"),
        TableSpec(
            "manifestations",
            "q_manif",
            {"w": IRI, "uf": C, "lic": IRI, "file": IRI, "mod": DT},
            key="w",
        ),
    )
}

# --------------------------------------------------------------------------------- literal parsing
_DATE = re.compile(r"^(-?\d{4,})-(\d{2})-(\d{2})(Z|[+-]\d{2}:\d{2})?$")
_DATETIME = re.compile(
    r"^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2}):(\d{2})(?:\.(\d+))?(Z|[+-]\d{2}:\d{2})?$",
)


def parse_xsd_date(value: str | None) -> dt.date | None:
    """``xsd:date`` literal → date; a timezone suffix is ignored, anything else malformed → None."""
    if not value:
        return None
    m = _DATE.match(value.strip())
    if not m:
        return None
    try:
        return dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def _tz(suffix: str | None) -> dt.tzinfo:
    if suffix is None:
        return LUXEMBOURG  # naive publisher timestamps are Luxembourg local time
    if suffix == "Z":
        return dt.UTC
    sign = 1 if suffix[0] == "+" else -1
    hours, minutes = int(suffix[1:3]), int(suffix[4:6])
    return dt.timezone(sign * dt.timedelta(hours=hours, minutes=minutes))


def parse_xsd_datetime(value: str | None) -> dt.datetime | None:
    """``xsd:dateTime`` literal → aware UTC datetime; malformed (``…472Z+02:00``) → None.

    A bare date is accepted as midnight Luxembourg time; a naive datetime is taken as Luxembourg time.
    """
    if not value:
        return None
    text = value.strip()
    m = _DATETIME.match(text)
    if not m:
        day = parse_xsd_date(text)
        if day is None:
            return None
        return dt.datetime(day.year, day.month, day.day, tzinfo=LUXEMBOURG).astimezone(dt.UTC)
    year, month, mday, hour, minute, second = (int(m.group(i)) for i in range(1, 7))
    micro = int((m.group(7) or "0")[:6].ljust(6, "0"))
    try:
        local = dt.datetime(year, month, mday, hour, minute, second, micro, tzinfo=_tz(m.group(8)))
    except ValueError:
        return None
    return local.astimezone(dt.UTC)


def iri_code(value: str | None) -> str | None:
    """Last path (or fragment) segment of a vocabulary IRI: ``…/resource-type/LOI`` → ``LOI``."""
    if not value:
        return None
    tail = value.rstrip("/").rsplit("/", 1)[-1]
    return tail.rsplit("#", 1)[-1] or None


def convert_value(kind: ColKind, raw: str | None) -> tuple[Any, str | None]:
    """Convert one CSV cell; returns ``(value, raw_if_unparsable)``. Empty cells are unbound (None)."""
    if raw is None or raw == "":
        return None, None
    if kind is ColKind.CODE:
        return iri_code(raw), None
    if kind is ColKind.DATE:
        day = parse_xsd_date(raw)
        return day, (None if day is not None else raw)
    if kind is ColKind.DATETIME:
        stamp = parse_xsd_datetime(raw)
        return stamp, (None if stamp is not None else raw)
    if kind is ColKind.INT:
        try:
            return int(raw), None
        except ValueError:
            return None, raw
    return raw, None


def convert_row(spec: TableSpec, cells: Mapping[str, str | None]) -> dict[str, Any]:
    """CSV row (variable → raw string) → typed row incl. ``<col>_raw`` for date/datetime columns."""
    out: dict[str, Any] = {}
    for col, kind in spec.columns.items():
        value, raw = convert_value(kind, cells.get(col))
        out[col] = value
        if kind in (ColKind.DATE, ColKind.DATETIME):
            out[f"{col}_raw"] = raw
    return out


# --------------------------------------------------------------------------------- in-memory form
@dataclass(frozen=True)
class MetaSnapshot:
    """All snapshot tables as typed rows plus ``_snapshot.json`` (``info``).

    ``info["act_formats_coverage"]`` describes which acts the ``act_formats`` table covers: ``None``
    means every ``/eli/etat/leg/`` act of the pulled types; the frozen research snapshot carries
    ``{"doc_types": [LOI, RGD, AGD, RMIN], "rdf_status": "in-force"}`` (it was built from the Q09
    cross-tab). Acts outside the coverage have an unknown licence.
    """

    tables: Mapping[str, Sequence[Mapping[str, Any]]]
    info: Mapping[str, Any] = field(default_factory=dict)

    def rows(self, name: str) -> Sequence[Mapping[str, Any]]:
        if name not in TABLES:
            raise KeyError(f"unknown snapshot table {name!r}")
        return self.tables.get(name, ())

    def has(self, name: str) -> bool:
        return name in self.tables

    @property
    def snapshot_date(self) -> dt.date | None:
        value = self.info.get("sync_date")
        return dt.date.fromisoformat(value) if isinstance(value, str) else None
