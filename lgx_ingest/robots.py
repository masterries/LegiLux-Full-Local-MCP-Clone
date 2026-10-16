"""S0 robots: parse both robots.txt files every run, build the ELI denylist, pause on change.

Owner: agent A (M1). Contract: docs/dev/INTERFACES.md §3 (PLAN §3.2).
"""

from __future__ import annotations

from lgx_ingest.context import StageContext, StageResult, UrlPolicy


def run_robots(ctx: StageContext) -> StageResult:
    """Fetch + parse robots.txt of both hosts, write data/robots/<T>/, fail if changed and unacknowledged."""
    raise NotImplementedError("lgx robots: M1 (agent A)")


def acknowledge(ctx: StageContext, sha256: str) -> StageResult:
    """Operator acknowledgement of a changed robots.txt (``lgx robots-ack <sha256>``)."""
    raise NotImplementedError("lgx robots-ack: M1 (agent A)")


def load_policy(ctx: StageContext) -> UrlPolicy:
    """The policy for this run (from data/robots/<T>/ and the denylist config); used by fetch."""
    raise NotImplementedError("lgx_ingest.robots.load_policy: M1 (agent A)")
