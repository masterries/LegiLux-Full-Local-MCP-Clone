"""Per-call context: clock, ``as_of`` range check, envelope (Appendix D.0/D.1) and warning accumulation."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Any
from zoneinfo import ZoneInfo

from lgx_core.models import Lang, Severity, ToolErrorCode, WarningCode
from lgx_core.warnings import Catalogue
from lgx_mcp.release import ReleaseInfo
from lgx_mcp.settings import McpSettings


class ToolFailure(Exception):
    """A tool error with an Appendix D code (``isError: true``)."""

    def __init__(
        self,
        code: ToolErrorCode,
        message: str,
        *,
        hint: str | None = None,
        candidates: list[dict[str, Any]] | None = None,
        warnings: list[dict[str, Any]] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.hint = hint
        self.candidates = candidates or []
        self.warnings = warnings or []

    def as_error(self) -> dict[str, Any]:
        return {"code": self.code.value, "message": self.message, "hint": self.hint, "candidates": self.candidates}


def fr_date(d: dt.date | None) -> str:
    return d.strftime("%d/%m/%Y") if d else "?"


def iso(d: dt.date | None) -> str | None:
    return d.isoformat() if d else None


class Clock:
    def __init__(self, tz: str = "Europe/Luxembourg", override: str | None = None) -> None:
        self.tz = ZoneInfo(tz)
        self._override = dt.datetime.fromisoformat(override) if override else None
        if self._override is not None and self._override.tzinfo is None:
            self._override = self._override.replace(tzinfo=self.tz)

    def now(self) -> dt.datetime:
        return self._override if self._override is not None else dt.datetime.now(self.tz)

    def today(self) -> dt.date:
        return self.now().astimezone(self.tz).date()


class WarningBag:
    """Rendered warnings with the citation keys they apply to, deduplicated on (code, message)."""

    def __init__(self, catalogue: Catalogue, lang: Lang = Lang.FR) -> None:
        self.catalogue = catalogue
        self.lang = lang
        self._items: dict[tuple[str, str], dict[str, Any]] = {}
        self._by_ref: dict[str, list[str]] = {}

    def add(
        self,
        code: WarningCode,
        *,
        ref: str | None = None,
        variant: str | None = None,
        data: dict[str, Any] | None = None,
        severity: Severity | None = None,
        **params: Any,
    ) -> None:
        w = self.catalogue.warning(code, self.lang, variant=variant, data=data, severity=severity, **params)
        item_key = (w.code.value, w.message)
        item = self._items.get(item_key)
        if item is None:
            item = {
                "code": w.code.value,
                "severity": w.severity.value,
                "message": w.message,
                "refs": [],
                "data": w.data,
            }
            self._items[item_key] = item
        if ref and ref not in item["refs"]:
            item["refs"].append(ref)
        if ref is not None:
            codes = self._by_ref.setdefault(ref, [])
            if w.code.value not in codes:
                codes.append(w.code.value)

    def codes_for(self, ref: str) -> list[str]:
        return list(self._by_ref.get(ref, []))

    def as_list(self) -> list[dict[str, Any]]:
        order = {Severity.HIGH.value: 0, Severity.MEDIUM.value: 1, Severity.INFO.value: 2}
        items = list(self._items.values())
        # envelope warnings (no refs) first, then by severity; stable otherwise
        return sorted(items, key=lambda w: (1 if w["refs"] else 0, order.get(w["severity"], 3)))

    def __len__(self) -> int:
        return len(self._items)


@dataclass
class CallContext:
    release: ReleaseInfo
    settings: McpSettings
    as_of: dt.date
    today: dt.date
    now: dt.datetime
    clock_ok: bool
    age_days: int
    staleness_days: int
    staleness_level: int
    bag: WarningBag
    as_of_given: bool = True
    notes: list[str] = field(default_factory=list)

    @property
    def T(self) -> dt.date:
        return self.release.sync_date

    @property
    def H(self) -> dt.date:
        return self.release.horizon

    def snapshot(self) -> dict[str, Any]:
        return {
            "release_id": self.release.release_id,
            "sync_date": iso(self.T),
            "horizon": iso(self.H),
            "age_days": self.age_days,
            "staleness_level": self.staleness_level,
            "clock_ok": self.clock_ok,
        }

    def envelope(self) -> dict[str, Any]:
        lang = self.bag.lang.value
        return {
            "snapshot": self.snapshot(),
            "as_of": iso(self.as_of),
            "warnings": self.bag.as_list(),
            "attribution": self.release.legal("attribution", lang),
            "disclaimer": self.release.legal("disclaimer", lang),
        }


def parse_as_of(value: Any) -> dt.date | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise ToolFailure(ToolErrorCode.BAD_INPUT, "as_of doit être une date YYYY-MM-DD")
    try:
        return dt.date.fromisoformat(value)
    except ValueError as exc:
        raise ToolFailure(ToolErrorCode.BAD_INPUT, f"as_of invalide : {value!r} (format YYYY-MM-DD)") from exc


def make_context(release: ReleaseInfo, settings: McpSettings, clock: Clock, as_of_arg: Any) -> CallContext:
    """Clock check, ``as_of`` default and range (Appendix D.0), envelope warnings."""
    now = clock.now()
    today = clock.today()
    T, H = release.sync_date, release.horizon
    clock_ok = T <= today <= H
    given = parse_as_of(as_of_arg)
    # default « after the clock check »: a suspect clock never sets the date
    as_of = given if given is not None else (today if clock_ok else T)
    if as_of > H:
        raise ToolFailure(
            ToolErrorCode.AS_OF_OUT_OF_RANGE,
            f"as_of {as_of.isoformat()} dépasse l'horizon {H.isoformat()} (T + 365 jours) : aucun statut n'est projeté"
            " au-delà.",
            hint=f"Utilisez une date as_of ≤ {H.isoformat()} ; les textes publiés après le {T.isoformat()}"
            " sont inconnus de cette copie.",
        )
    age_days = max(0, (today - T).days)
    staleness_days = max(age_days, (as_of - T).days)  # W_DATA_STALE: as_of − T (or the data age) > 30 / 90 days

    def level_of(days: int) -> int:
        if days > settings.staleness_critical_days:
            return 2
        return 1 if days > settings.staleness_warn_days else 0

    level = level_of(staleness_days)
    bag = WarningBag(release.catalogue)
    ctx = CallContext(
        release=release,
        settings=settings,
        as_of=as_of,
        today=today,
        now=now,
        clock_ok=clock_ok,
        age_days=age_days,
        staleness_days=staleness_days,
        staleness_level=level_of(age_days),  # snapshot.staleness_level: age of the data (now − T)
        bag=bag,
        as_of_given=given is not None,
    )
    bag.add(WarningCode.W_NOT_OFFICIAL)
    bag.add(WarningCode.W_SNAPSHOT, T=fr_date(T), as_of=fr_date(as_of))
    if level:
        n = settings.staleness_critical_days if level == 2 else settings.staleness_warn_days
        bag.add(WarningCode.W_DATA_STALE, n=n, T=fr_date(T), severity=Severity.HIGH if level == 2 else None)
    if not clock_ok:  # status tools refuse without as_of (server.run_tool); with as_of only that date is asserted
        bag.add(
            WarningCode.W_CLOCK_SUSPECT,
            variant="explicit_as_of" if given is not None else None,
            now=now.strftime("%d/%m/%Y %H:%M"),
            T=fr_date(T),
        )
    return ctx
