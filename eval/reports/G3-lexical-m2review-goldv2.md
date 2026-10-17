# G3 — lexical/bm25

- release `data-20261016-sample`, run 20261017T122005Z, 199 items (draft_unvalidated), backend in-process

Mandatory-warning coverage **0.981** (616/628), items ok 188/199, forbidden seen 0, status statements 0.992, skipped 0; gates {'mandatory_warnings==100%': False, 'status_statements>=98%': True}

| code | expected | present |
|---|---|---|
| W_ARTICLE_REPEALED | 6 | 5 |
| W_BASE_LEGALE_ABROGEE | 11 | 11 |
| W_CLOCK_SUSPECT | 2 | 2 |
| W_CONSO_DISCONTINUED | 6 | 6 |
| W_CONSO_LAGGING | 3 | 3 |
| W_DATA_QUALITY | 28 | 18 |
| W_DATA_STALE | 2 | 2 |
| W_DERIVED_ELI | 21 | 21 |
| W_FUTURE_VERSION | 18 | 18 |
| W_NOT_CONSOLIDATED | 20 | 20 |
| W_NOT_OFFICIAL | 199 | 199 |
| W_NOT_YET_IN_FORCE | 6 | 6 |
| W_OUT_OF_SCOPE | 3 | 3 |
| W_PARTIAL_REPEAL_UNKNOWN | 54 | 54 |
| W_PDF_EXTRACTED | 16 | 15 |
| W_REPEALED | 17 | 17 |
| W_SNAPSHOT | 199 | 199 |
| W_STUB | 9 | 9 |
| W_SUNSET | 5 | 5 |
| W_SUPERSEDED_BY_CODE | 3 | 3 |

Failing items: 11

- G3-021 (act:consolidated): missing ['W_DATA_QUALITY'], forbidden [], status in-force, error None
- G3-022 (article:consolidated): missing ['W_DATA_QUALITY'], forbidden [], status in-force, error None
- G3-024 (act:consolidated): missing ['W_DATA_QUALITY'], forbidden [], status in-force, error None
- G3-025 (article:consolidated): missing ['W_DATA_QUALITY'], forbidden [], status in-force, error None
- G3-030 (act:consolidated): missing ['W_DATA_QUALITY'], forbidden [], status in-force, error None
- G3-031 (article:consolidated): missing ['W_DATA_QUALITY'], forbidden [], status in-force, error None
- G3-048 (act:consolidated): missing ['W_DATA_QUALITY'], forbidden [], status in-force, error None
- G3-049 (article:consolidated): missing ['W_PDF_EXTRACTED', 'W_DATA_QUALITY'], forbidden [], status None, error NOT_FOUND
- G3-054 (act:stub_fallback): missing ['W_DATA_QUALITY'], forbidden [], status in-force, error None
- G3-055 (article:stub_fallback): missing ['W_DATA_QUALITY'], forbidden [], status in-force, error None
- G3-156 (article:repealed_stub): missing ['W_ARTICLE_REPEALED'], forbidden [], status None, error None
