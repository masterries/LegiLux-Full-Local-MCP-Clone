# SPARQL snapshot fixtures (agent A)

`snapshot-20261016-sample/` — commit-sized subset (≈ 100 KB) of the frozen golden snapshot
`tests/golden/snapshot-20261016/`, in the exact `data/meta/<T>/` format (zstd parquet + `_snapshot.json`).
It holds every row about the 53 `config/sample.yaml` works plus 12 selection edge cases (listed in
`_snapshot.json` → `works`): flag/date conflicts (`loi/2010/05/27/n2`, `rgd/1978/02/28/n2`,
`acc/2026/02/27/a120`, an `-implicit` act), a code-recueil without window version
(`code/cooperation_judiciaire`), a future-only work (`loi/2026/04/15/a211`), `code/environnement_annexes`,
the URI-date mismatch `recueil/sites_monuments`, two `dateEntryInForce` values (`rgd/2026/06/12/a299`), a
stored next version (`loi/2002/11/12/n1`), a stub without any files (`agd/1995/05/18/n1`) and a pre-published
version (`loi/2017/04/24/a479`). The basedOn targets of these acts are included (for `base_legale_abrogee`).
No naturalisation laws are included (minimisation); those rules are tested on synthetic rows.

Rebuild (after a golden-snapshot change):

```python
from pathlib import Path
from lgx_core.config import ProjectConfig
from lgx_ingest.sparql import subset_snapshot

works = [w.work for w in ProjectConfig.load().sample.works] + EDGE_CASES  # see _snapshot.json "works"
subset_snapshot(Path("tests/golden/snapshot-20261016"), Path("tests/fixtures/meta/snapshot-20261016-sample"), works)
```

The selection of each listed work on the subset is identical to its selection on the full snapshot
(checked when the fixture was built).
