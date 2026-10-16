# SPARQL queries (PLAN Appendix A)

Every query the pipeline sends to `https://data.legilux.public.lu/sparqlendpoint` lives here, one file
per query. The texts are the live-tested ones of Appendix A (2026-10-16), with the deviations listed at
the bottom.

## Conventions (Appendix A.0)

- `POST`, `application/x-www-form-urlencoded`, field `query`; `Accept: text/csv` for SELECT,
  `application/n-triples` for CONSTRUCT (`*.construct.rq`).
- Sequential requests, ≥ 1.2 s apart, User-Agent from `LGX_USER_AGENT`.
- Flat, unsorted, `DISTINCT` (the store is a union default graph). Never `ORDER BY` with
  `LIMIT`/`OFFSET` past 10,000 rows (Virtuoso SR353). Join on the client.
- Parse every date/datetime on the client (some `dct:modified` literals are malformed); never parse dates
  from URIs.
- `prefixes.rq` is prepended to every query by the renderer; the files contain only the body. `#` lines
  are SPARQL comments and are sent as-is.

## Placeholder syntax

Placeholders are `{{NAME}}` (upper-case). They are filled **only** by
`lgx_ingest.sparql_templates.render_query(name, **params)`, which validates the value type — raw SPARQL
fragments are never interpolated.

| Placeholder | Python type | Rendered as | Used by |
|---|---|---|---|
| `{{WORKS}}`, `{{GRAPHS}}`, `{{IRIS}}` | sequence of 1–500 IRIs | `<iri>` per line (inside `VALUES`) | `q_manif`, `q16_construct_graphs.construct` |
| `{{SINCE}}` | tz-aware `datetime` | `2026-09-01T00:00:00Z` (UTC, inside `"…"^^xsd:dateTime`) | `q11_graph_watermark` |
| `{{FROM}}`, `{{TO}}`, `{{SINCE_DATE}}` | `date` | `2026-10-09` (inside `"…"^^xsd:date`) | `q12_boundary_sweep`, `q13_*` |
| `{{Y0}}`, `{{Y1}}` | `int` | `1990` | `q07_titles_by_year` |

```python
from lgx_ingest.sparql_templates import render_query

text = render_query(
    "q_manif", WORKS=["http://data.legilux.public.lu/eli/etat/leg/loi/2018/08/01/a686/consolide/20251226"]
)
```

## Files → snapshot tables

`lgx discover` writes each SELECT result to `data/meta/<T>/<table>.parquet` (contract:
`docs/dev/INTERFACES.md` §3).

| File | Table | Cadence |
|---|---|---|
| `q01_acts.rq` | `acts` | W |
| `q02_consolidations.rq` | `consolidations` | N |
| `q03_conso_formats.rq` | `conso_formats` | N |
| `q04_conso_base_acts.rq` | `conso_base_acts` | N |
| `q05_rel_modifies_repeals.rq` | `rel_modifies_repeals` | W |
| `q06_rel_other.rq` | `rel_other` | W |
| `q07_titles.rq` (fallback `q07_titles_by_year.rq`) | `titles` | W |
| `q08_memorial_refs.rq` | `memorial_refs` | W |
| `q10_vocabularies.construct.rq` | `vocab` | W |
| `q_manif.rq` | `manifestations` | N (changed works) / M (all) |
| `q11_graph_watermark.rq`, `q12_boundary_sweep.rq`, `q13_*.rq`, `q16_*.rq` | sync state / reports | N / D |
| `checks/q09, q14, q15, q17, q18` | gates and reports | N / W |
| `checks/probe_*.rq` | endpoint regression probes (A.5) | M1, then quarterly |

## Deviations from Appendix A (to be re-measured in M1)

- `DISTINCT` added to Q07-fallback, Q08 and Q09, which were tested without it; A.0 requires `DISTINCT`
  on every pull because of the union default graph. Row counts are recorded again in M1.
- Q13 is also provided in its non-aggregated form (`q13_new_relations_list.rq`), which Appendix A
  describes but does not print.
- Golden-number reproductions (A.4: b1, tt1, m1, natur, …) are test assets built from the frozen
  2026-10-16 snapshot, not production queries; they are not duplicated here.
