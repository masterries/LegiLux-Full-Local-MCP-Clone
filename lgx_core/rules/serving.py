"""Serving decision for one act on one date; Python twin of ``lgx.serving_at`` (agent A, M1)."""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from typing import Any

from lgx_core.models import Act, ActVersion, Relation


def serving_at(act: Act, versions: Sequence[ActVersion], relations: Sequence[Relation], as_of: dt.date) -> Any:
    """Status, window version, served text version, label recomputed for ``as_of``, flags, next version."""
    raise NotImplementedError("lgx_core.rules.serving.serving_at: M1 (agent A)")
