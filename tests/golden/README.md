# Frozen golden snapshot (PLAN §11.5)

`snapshot-20261016/` is the frozen SPARQL metadata of 2026-10-16 in the `data/meta/<T>/` format (≈ 4.7 MB,
zstd parquet, rows sorted). It was converted with the production parser (`lgx_ingest.sparql.import_csv_snapshot`)
from the research pulls of that day:

| Table | Source |
|---|---|
| `acts`, `consolidations`, `conso_formats`, `conso_base_acts`, `rel_modifies_repeals`, `memorial_refs` | gaps-and-reconciliation `a1`, `c1`, `c2`, `c3`, `m2`, `histid` |
| `rel_other`, `titles` | plan checks P2 (`relother.csv`), P3 (`titles.csv`) |
| `vocab` | review run of Q10 (N-Triples) |
| `act_formats` | GR `l4` (= Q09) ∪ the live Q19 pull of 2026-10-16T22:44Z |
| `manifestations` | plan Q-MANIF (5 works) ∪ the live Q-MANIF of the 51 sample works (2026-10-16T22:44Z) |

The live pull of 2026-10-16 22:44 UTC returned byte-identical result sizes for every research table, so the
two additions describe the same state. `tests/unit/test_select_golden.py` asserts the golden numbers
exactly; `uv run python scripts/check_golden.py` (or `uv run lgx meta golden`) prints expected vs actual and
the reconciliation of the research definitions with the selection rules.
