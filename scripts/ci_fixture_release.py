"""CI only: build the offline fixture release and restore it as the runtime database for the MCP contract tests.

The fixture release is the real pipeline (select → parse → chunk → load → gates → package → restore → rename-swap)
over the committed CC BY fixtures of ``tests/unit/load_fixture.py`` (a handful of texts, no network). It carries the
2026-10-16 release id, so the ``any_release`` contract tests (auth 401/421/403, /readyz, protocol eras, degraded
states) run against a real ``legilux`` database on GitHub, where the gitignored sample bundle does not exist:

    uv run python scripts/ci_fixture_release.py --dsn postgresql://postgres:…@localhost:5432/postgres \\
        --container <postgres container id> --ro-password <lgx_ro password>
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

import psycopg
from psycopg import sql

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tests"))

from unit.load_fixture import T, build_fixture_sample  # noqa: E402

from lgx_ingest.cli_ingest import run_ingest  # noqa: E402
from lgx_ingest.context import StageContext  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dsn", required=True, help="superuser DSN of the server (database postgres)")
    ap.add_argument("--container", required=True, help="container running that server (pg_dump / pg_restore)")
    ap.add_argument("--restore-to", default="legilux")
    ap.add_argument("--ro-password", required=True, help="password to set for lgx_ro")
    args = ap.parse_args(argv)
    with tempfile.TemporaryDirectory(prefix="lgx-ci-fixture-") as tmp:
        cfg_dir, data = build_fixture_sample(Path(tmp))
        ctx = StageContext.create(
            sync_date=T, config_dir=cfg_dir, data_dir=data, sample=True, options={"keep_prev": "0"}
        )
        result = run_ingest(
            ctx,
            offline=True,
            load=True,
            dsn=args.dsn,
            db_name="legilux_build_ci_fixture",
            package=True,
            container=args.container,
            restore_to=args.restore_to,
            echo=print,
        )
    if not result.ok:
        print("fixture release failed:", result.messages, file=sys.stderr)
        return 1
    with psycopg.connect(args.dsn, autocommit=True) as conn:
        conn.execute(sql.SQL("ALTER ROLE lgx_ro PASSWORD {}").format(sql.Literal(args.ro_password)))
    print(f"fixture release restored as {args.restore_to}; lgx_ro password set")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
