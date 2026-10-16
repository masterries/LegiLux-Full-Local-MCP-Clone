"""Status from dates (PLAN §2.2 step 1); Python twin of ``lgx.act_status`` (owner: agent A, M1).

Dates win over flags (§2.1): the raw ``jolux:inForceStatus`` code is consulted only for the
``-implicit`` status (50 acts without a ``dateNoLongerInForce``) and for an undated repeal (≤ 3 acts).
"""

from __future__ import annotations

import datetime as dt

from lgx_core.models import ActStatus, RdfActStatus

IMPLICIT = RdfActStatus.NO_LONGER_IN_FORCE_IMPLICIT.value
NO_LONGER = RdfActStatus.NO_LONGER_IN_FORCE.value


def act_status(dei: dt.date | None, dnl: dt.date | None, rdf_status: str | None, as_of: dt.date) -> ActStatus:
    """Status of an act on ``as_of``; ``rdf_status`` is the raw code (e.g. ``in-force``).

    Mirrors ``lgx.act_status`` in ``db/schema/30_functions.sql`` branch by branch.
    """
    if rdf_status == IMPLICIT:
        return ActStatus.NO_LONGER_IN_FORCE
    if dnl is not None and dnl <= as_of:
        return ActStatus.NO_LONGER_IN_FORCE
    if dnl is None and rdf_status == NO_LONGER:
        return ActStatus.NO_LONGER_IN_FORCE
    if dei is not None and dei > as_of:
        return ActStatus.NOT_YET_IN_FORCE
    return ActStatus.IN_FORCE


def is_live(dei: dt.date | None, dnl: dt.date | None, rdf_status: str | None, as_of: dt.date) -> bool:
    """``live(a, d)`` of PLAN §2.2: in force on ``as_of`` (not repealed and already in force)."""
    return act_status(dei, dnl, rdf_status, as_of) is ActStatus.IN_FORCE


def date_status(dei: dt.date | None, dnl: dt.date | None, as_of: dt.date) -> ActStatus:
    """Status from the two dates alone, ignoring the raw flag (used to detect flag/date conflicts)."""
    if dnl is not None and dnl <= as_of:
        return ActStatus.NO_LONGER_IN_FORCE
    if dei is not None and dei > as_of:
        return ActStatus.NOT_YET_IN_FORCE
    return ActStatus.IN_FORCE


def rdf_as_status(rdf_status: str | None) -> ActStatus | None:
    """Map a raw act status code to the computed vocabulary (``-implicit`` → no longer in force)."""
    if rdf_status is None:
        return None
    if rdf_status.startswith(NO_LONGER):
        return ActStatus.NO_LONGER_IN_FORCE
    try:
        return ActStatus(rdf_status)
    except ValueError:
        return None


def status_flag_conflict(dei: dt.date | None, dnl: dt.date | None, rdf_status: str | None, as_of: dt.date) -> bool:
    """True when the raw flag disagrees with the dates on ``as_of`` (PLAN §2.5: 58 acts at T0).

    Covers an in-force flag with a past dnl, a not-yet flag with a past dei, a no-longer flag without a
    past dnl and every ``-implicit`` act (it has no dnl by construction). Dates win; the flag is shown as
    ``W_DATA_QUALITY``.
    """
    flagged = rdf_as_status(rdf_status)
    return flagged is not None and flagged is not date_status(dei, dnl, as_of)
