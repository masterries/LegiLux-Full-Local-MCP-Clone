"""Status from dates (PLAN §2.2 step 1); Python twin of ``lgx.act_status`` (owner: agent A, M1)."""

from __future__ import annotations

import datetime as dt

from lgx_core.models import ActStatus


def act_status(dei: dt.date | None, dnl: dt.date | None, rdf_status: str | None, as_of: dt.date) -> ActStatus:
    """Status of an act on ``as_of``; ``rdf_status`` is the raw code (e.g. ``in-force``)."""
    raise NotImplementedError("lgx_core.rules.status.act_status: M1 (agent A)")
