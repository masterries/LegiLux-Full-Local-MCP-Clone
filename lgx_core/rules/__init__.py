"""Selection and status rules: the single source of legal logic (owner: agent A, M1).

Pure functions only (no I/O, no network). The SQL twins are ``lgx.act_status`` / ``lgx.serving_at``
(``db/schema/30_functions.sql``); ``tests/db/test_rules_parity.py`` compares both. Contract:
docs/dev/INTERFACES.md §4.

* :mod:`.status` — status from dates (PLAN §2.2 step 1).
* :mod:`.select` — the selection at T (PLAN §2.2 steps 0–5) → :class:`~.select.Selection`.
* :mod:`.serving` — the runtime decision for any ``as_of`` (PLAN §7.3).
* :mod:`.licence` — licence-aware format choice (PLAN §3.4).
* :mod:`.snapshot` — snapshot table schemas, literal parsing, :class:`~.snapshot.MetaSnapshot`.
* :mod:`.golden` / :mod:`.report` — golden numbers (PLAN §11.5) and ``selection_report.json``.
"""

from lgx_core.rules.select import RULES_VERSION, Selection, select
from lgx_core.rules.serving import ServingDecision, serving_at
from lgx_core.rules.snapshot import MetaSnapshot
from lgx_core.rules.status import act_status

__all__ = ["RULES_VERSION", "MetaSnapshot", "Selection", "ServingDecision", "act_status", "select", "serving_at"]
