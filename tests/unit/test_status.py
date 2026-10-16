"""Status from dates (PLAN §2.2 step 1) — the Python twin of ``lgx.act_status``."""

from __future__ import annotations

import datetime as dt

import pytest

from lgx_core.models import ActStatus
from lgx_core.rules.status import act_status, date_status, is_live, rdf_as_status, status_flag_conflict

T = dt.date(2026, 10, 16)
D = dt.timedelta(days=1)


@pytest.mark.parametrize(
    ("dei", "dnl", "rdf", "as_of", "expected"),
    [
        (dt.date(2000, 1, 1), None, "in-force", T, ActStatus.IN_FORCE),
        # -implicit is never in force (50 acts without a dnl)
        (dt.date(2000, 1, 1), None, "no-longer-in-force-implicit", T, ActStatus.NO_LONGER_IN_FORCE),
        (None, dt.date(2030, 1, 1), "no-longer-in-force-implicit", T, ActStatus.NO_LONGER_IN_FORCE),
        # dnl is exclusive: no longer in force ON the dnl
        (dt.date(2000, 1, 1), T, "in-force", T, ActStatus.NO_LONGER_IN_FORCE),
        (dt.date(2000, 1, 1), T + D, "in-force", T, ActStatus.IN_FORCE),
        # the flag flips silently after the date (266 of 269 acts): dates win
        (dt.date(2000, 1, 1), T - D, "in-force", T, ActStatus.NO_LONGER_IN_FORCE),
        # flag-only repeal without a date (≤ 3 acts)
        (dt.date(2000, 1, 1), None, "no-longer-in-force", T, ActStatus.NO_LONGER_IN_FORCE),
        # flag says no longer but the dnl is still ahead (loi/2010/05/27/n2): dates win
        (dt.date(2010, 7, 3), T + 4 * D, "no-longer-in-force", T, ActStatus.IN_FORCE),
        (dt.date(2010, 7, 3), T + 4 * D, "no-longer-in-force", T + 4 * D, ActStatus.NO_LONGER_IN_FORCE),
        # not yet in force: dei is inclusive
        (T + 4 * D, None, "not-yet-in-force", T, ActStatus.NOT_YET_IN_FORCE),
        (T + 4 * D, None, "not-yet-in-force", T + 4 * D, ActStatus.IN_FORCE),
        (T - D, None, "not-yet-in-force", T, ActStatus.IN_FORCE),  # stale flag (4 acts)
        (None, None, None, T, ActStatus.IN_FORCE),  # recueil works carry no act dates
        # repeal wins over a later entry into force
        (T + 10 * D, T + 5 * D, "in-force", T + 6 * D, ActStatus.NO_LONGER_IN_FORCE),
    ],
)
def test_act_status(
    dei: dt.date | None, dnl: dt.date | None, rdf: str | None, as_of: dt.date, expected: ActStatus
) -> None:
    assert act_status(dei, dnl, rdf, as_of) is expected
    assert is_live(dei, dnl, rdf, as_of) is (expected is ActStatus.IN_FORCE)


def test_date_status_ignores_flags() -> None:
    assert date_status(None, None, T) is ActStatus.IN_FORCE
    assert date_status(T + D, None, T) is ActStatus.NOT_YET_IN_FORCE
    assert date_status(None, T, T) is ActStatus.NO_LONGER_IN_FORCE


@pytest.mark.parametrize(
    ("dei", "dnl", "rdf", "conflict"),
    [
        (dt.date(2000, 1, 1), None, "in-force", False),
        (dt.date(2000, 1, 1), T - D, "in-force", True),  # in force with past dnl (1 act)
        (T - D, None, "not-yet-in-force", True),  # not-yet with past dei (4 acts)
        (dt.date(2000, 1, 1), None, "no-longer-in-force", True),  # no-longer without past dnl (3 acts)
        (dt.date(2000, 1, 1), T + D, "no-longer-in-force", True),
        (dt.date(2000, 1, 1), None, "no-longer-in-force-implicit", True),  # implicit (50 acts)
        (dt.date(2000, 1, 1), T - D, "no-longer-in-force", False),
        (T + D, None, "not-yet-in-force", False),
        (None, None, None, False),
        (None, None, "something-else", False),
    ],
)
def test_status_flag_conflict(dei: dt.date | None, dnl: dt.date | None, rdf: str | None, conflict: bool) -> None:
    assert status_flag_conflict(dei, dnl, rdf, T) is conflict


def test_rdf_as_status() -> None:
    assert rdf_as_status("no-longer-in-force-implicit") is ActStatus.NO_LONGER_IN_FORCE
    assert rdf_as_status("in-force") is ActStatus.IN_FORCE
    assert rdf_as_status(None) is None and rdf_as_status("weird") is None
