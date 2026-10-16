"""The pure selection function ``select(snapshot, T, config) -> Selection`` (PLAN §2.2; agent A, M1)."""

from __future__ import annotations

import datetime as dt
from typing import Any

from lgx_core.config import ProjectConfig


def select(snapshot: Any, sync_date: dt.date, config: ProjectConfig) -> Any:
    """Return acts, versions, relations, manifest entries and the selection report for ``sync_date``.

    ``snapshot`` is the in-memory form of the ``data/meta/<T>/*.parquet`` tables. See INTERFACES §4.
    """
    raise NotImplementedError("lgx_core.rules.select.select: M1 (agent A)")
