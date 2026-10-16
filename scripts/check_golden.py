#!/usr/bin/env python3
"""Print the golden numbers of PLAN §11.5: expected vs actual, with the reconciliation of differences.

Usage::

    uv run python scripts/check_golden.py                               # frozen 2026-10-16 snapshot
    uv run python scripts/check_golden.py --snapshot data/meta/2026-10-16  # a live pull (drift allowed)
    uv run python scripts/check_golden.py --snapshot DIR --date 2026-11-01 --strict

Exit code 1 when an exact number differs (always on the frozen snapshot, only with --strict elsewhere).
Same logic as ``lgx meta golden`` (``lgx_ingest.select.golden_check``).
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

from lgx_core.config import DEFAULT_CONFIG_DIR, ProjectConfig
from lgx_ingest.select import GOLDEN_SNAPSHOT, golden_check


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--snapshot", type=Path, default=GOLDEN_SNAPSHOT, help="snapshot directory")
    parser.add_argument(
        "--date", type=dt.date.fromisoformat, default=None, help="selection date (default: snapshot date)"
    )
    parser.add_argument("--config-dir", type=Path, default=DEFAULT_CONFIG_DIR)
    parser.add_argument("--strict", action="store_true", help="fail on differences for any snapshot")
    args = parser.parse_args(argv)
    text, ok = golden_check(args.snapshot, ProjectConfig.load(args.config_dir), args.date)
    print(text)
    frozen = args.snapshot.resolve() == GOLDEN_SNAPSHOT.resolve()
    if ok:
        print("\nall exact golden numbers match")
        return 0
    print("\nDIFFERENCES found" + ("" if frozen or args.strict else " (live snapshot: drift is expected; see above)"))
    return 1 if frozen or args.strict else 0


if __name__ == "__main__":
    sys.exit(main())
