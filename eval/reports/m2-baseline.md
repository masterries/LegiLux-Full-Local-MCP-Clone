# M2 baseline — lexical vs lexical + reranker (sample release)

- Date: 2026-10-17. Gold sets: G0, G1, G2 v1, G3 — all **`draft_unvalidated`** (lawyer review pending).
- Release `data-20261016-sample` (sync date 2026-10-16; 10,220 searchable chunks), embeddings **off**
  (`LGX_EMBED_PROVIDER=none`, lexical mode only). Decision drawn from these numbers: `docs/adr/0001-lexical-ranker.md`.
- Machine-readable reports next to this file: `G{0,1,2,3}-*.json` (per-item results, config, latency) and `.md`.

## Setup

| | |
|---|---|
| Host | 4 vCPU Intel Xeon @ 2.8 GHz, 15 GB RAM, no GPU. Another stage's ingest jobs ran during the G2 / G3 runs (load average 3–9), so their latencies are « busy dev host » numbers; the micro-benchmark below ran on the idle host |
| Database | `legilux_eval_m2` in the dev PostgreSQL 18 container: the sample bundle (`data/bundles/data-2026-10-16-sample.dump`) restored, then the M2 functions of `db/schema/40_search.sql` (`lgx.query_terms`, 12-argument `lgx.search_candidates`) created — function bodies checked identical to the file (md5 of `prosrc`). The `*-legilux-m1db` reports ran on the dev runtime database `legilux` (same data, M1 search API 1) |
| Server | in process (`lgx_mcp.server.run_tool`: validation, envelope, retrieval, warnings), clock pinned to 2026-10-16T12:00+02:00 |
| Lexical | search API 2, ranker `bm25` (IDF-weighted, `db/schema/40_search.sql`), query preparation + curated expansion on, shipped knob defaults (`LGX_LEXICAL_OPTS` empty). `rank_cd` = the M1 ordering (`ts_rank_cd` + coverage) on the same database |
| Reranker | TEI `cpu-1.9.4` (`sha256:2538ea1c…c78d`), `BAAI/bge-reranker-v2-m3` @ `953dc6f6…` (fp32 safetensors, candle backend), `--max-client-batch-size 64 --max-batch-tokens 4096 --auto-truncate true`, 4 threads; client: top **K = 16** articles, compact header (≤ 400 chars) + **600** body characters, batch 16, `LGX_RERANK=on` (a reranker failure is an error, so no item silently fell back) |

Reproduce (eval DB built as in `eval/README.md`; TEI: `docker compose -f deploy/compose.dev.yml --profile rerank up -d rerank`):

```sh
EV=postgresql://lgx_ro:lgx-ro-dev-only@127.0.0.1:55432/legilux_eval_m2
python -m eval.harness run --dsn $EV --set G0 --tag G0-lexical          # and G1, G3
python -m eval.harness run --dsn $EV --set G2 --ranker rank_cd --tag G2-lexical-rank_cd
python -m eval.harness run --dsn $EV --set G2 --no-expansion --tag G2-lexical-bm25-noexp
python -m eval.harness run --dsn $EV --set G2 --tag G2-lexical-bm25
python -m eval.harness run --dsn $EV --set G2 --mode rerank --rerank-k 16 --rerank-body-chars 600 --tag G2-rerank-bm25
python -m eval.harness compare eval/reports/G2-lexical-rank_cd.json eval/reports/G2-lexical-bm25-noexp.json \
    eval/reports/G2-lexical-bm25.json eval/reports/G2-rerank-bm25.json
```

## Summary

| set | metric (gate) | M1 serving (`legilux`, search API 1) | M2 lexical | M2 lexical + reranker |
|---|---|---|---|---|
| G0 smoke (20) | checks passed (20/20) | 20/20 | 20/20 | 20/20 |
| G1 citations (566) | exact accuracy (≥ 0.95) | 0.997 (564/566) | 0.997 (564/566) | 0.997 (564/566) |
| G1 | ambiguity recall (≥ 0.90) / not-found accuracy | 1.000 / 1.000 | 1.000 / 1.000 | 1.000 / 1.000 |
| G2 retrieval (205) | Recall@10 | 0.434 | **0.849** | **0.880** |
| G2 | MRR@10 | 0.302 | **0.657** | **0.823** |
| G2 | nDCG@10 | 0.333 | 0.698 | 0.836 |
| G3 warnings (184) | mandatory-warning coverage (100 %) | 1.000 (563/563) | 1.000 (563/563) | 1.000 (563/563) |
| G3 | forbidden codes (0) / status statements (≥ 98 %) | 0 / 1.000 | 0 / 1.000 | 0 / 1.000 |
| G2 | search latency p50 / p95 | 0.23 / 0.54 s | 0.22 / 0.41 s | 17.9 / 34.6 s |

The reranker changes only the order of search hits, so G0, G1 and G3 are identical in both modes (G3's 48 search
items ran reranked: `LGX_RERANK=on`, no error). The M1 column equals the `rank_cd` run on the M2 database to the
fourth decimal, with identical per-item ranks (same data, same ordering): the `rank_cd` switch reproduces M1.

G1's two misses: G1-288 « Loi du 5 mars 1922 portant approbation de la Convention d'Union économique, art.
unique » resolves to the act, not to its `article_unique` (the backward « …, art. unique » form is not parsed as an
article); G1-564 « règlement général sur la protection des données » is `not_found` instead of `out_of_scope` (no
out-of-scope alias). Both are resolver / alias gaps (`lgx_core/citeparse`, `config/aliases.yaml`), noted in
`docs/dev/INTERFACES.md` § 10.

## G2 retrieval (205 questions: 185 FR, 10 DE, 10 EN; top 20 scored)

`G2-legilux-m1db` (M1 runtime database) is identical to `lexical/rank_cd` and left out of the tables.

**all** (n = 205)

| run | R@1 | R@5 | R@10 | R@20 | MRR@10 | nDCG@10 | act R@10 | p50 tool ms |
|---|---|---|---|---|---|---|---|---|
| lexical/rank_cd (= M1) | 0.229 | 0.388 | 0.434 | 0.471 | 0.302 | 0.333 | 0.722 | 205 |
| lexical/bm25, no expansion | 0.527 | 0.763 | 0.820 | 0.846 | 0.639 | 0.678 | 0.907 | 182 |
| lexical/bm25 (shipped) | 0.541 | 0.778 | 0.849 | 0.885 | 0.657 | 0.698 | 0.961 | 217 |
| rerank/bm25 k=16, 600 chars | **0.766** | **0.856** | **0.880** | 0.885 | **0.823** | **0.836** | 0.971 | 17,889 |
| rerank/rank_cd k=16 (not run) | | | ≤ 0.461 | ≤ 0.471 | | | | |

**fr** (n = 185)

| run | R@1 | R@5 | R@10 | R@20 | MRR@10 | nDCG@10 | act R@10 |
|---|---|---|---|---|---|---|---|
| lexical/rank_cd (= M1) | 0.254 | 0.430 | 0.481 | 0.516 | 0.334 | 0.369 | 0.751 |
| lexical/bm25, no expansion | 0.573 | 0.830 | 0.886 | 0.916 | 0.695 | 0.736 | 0.973 |
| lexical/bm25 (shipped) | 0.578 | 0.824 | 0.886 | 0.922 | 0.698 | 0.739 | 0.973 |
| rerank/bm25 k=16, 600 chars | 0.795 | 0.895 | 0.916 | 0.922 | 0.857 | 0.870 | 0.984 |

**de** (n = 10, sent untranslated)

| run | R@1 | R@5 | R@10 | R@20 | MRR@10 | nDCG@10 | act R@10 |
|---|---|---|---|---|---|---|---|
| lexical/rank_cd (= M1) | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.300 |
| lexical/bm25, no expansion | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 |
| lexical/bm25 (shipped) | 0.100 | 0.400 | 0.500 | 0.600 | 0.220 | 0.287 | 0.800 |
| rerank/bm25 k=16, 600 chars | 0.500 | 0.500 | 0.600 | 0.600 | 0.514 | 0.533 | 0.800 |

**en** (n = 10, sent untranslated)

| run | R@1 | R@5 | R@10 | R@20 | MRR@10 | nDCG@10 | act R@10 |
|---|---|---|---|---|---|---|---|
| lexical/rank_cd (= M1) | 0.000 | 0.000 | 0.000 | 0.100 | 0.000 | 0.000 | 0.600 |
| lexical/bm25, no expansion | 0.200 | 0.300 | 0.400 | 0.400 | 0.260 | 0.292 | 0.600 |
| lexical/bm25 (shipped) | 0.300 | 0.300 | 0.500 | 0.500 | 0.327 | 0.364 | 0.900 |
| rerank/bm25 k=16, 600 chars | 0.500 | 0.500 | 0.500 | 0.500 | 0.500 | 0.500 | 0.900 |

- **IDF weighting** is the main gain (M1 → no expansion: +39 points R@10, +34 MRR); the curated expansion adds
  +3 R@10 / +2 MRR overall and carries the DE/EN items (R@10 DE 0 → 0.5, EN 0.4 → 0.5).
- **The reranker** re-orders the top 16 only, so R@20 stays 0.885; it moves targets up: +22.5 points R@1, +16.6
  MRR. Per item: 63 better, 8 worse, 134 unchanged (MRR@10). The 8 worse: G2-011 (1 → 10), G2-078 (1 → 8), G2-161
  (1 → 6), G2-020, G2-039, G2-108 (1 → 2), G2-131 (4 → 6), G2-191 (5 → 7).
- **rank_cd + reranker** was not run (≈ 1 h of CPU on the shared host): re-ordering option 1's top 16 cannot bring
  R@10 above its R@16 = 0.461 (computed from `G2-lexical-rank_cd.json`), i.e. far below bm25 alone.

## Knob sweep (lexical, G2, one knob at a time)

Run before the defaults were fixed, from the then-defaults header 1.0, phrase 0.5, length 0.25, act boost 1.0,
expansion 0.5 (and before the DE/EN glossary swap). « odd » / « even » are the two halves of G2 by item order; a
change was kept only if both halves improved.

| change | R@10 | MRR@10 | nDCG@10 | odd R@10 / MRR | even R@10 / MRR |
|---|---|---|---|---|---|
| (then-defaults) | 0.844 | 0.609 | 0.663 | 0.806 / 0.601 | 0.882 / 0.617 |
| length_b 0 | 0.824 | 0.576 | 0.632 | 0.782 / 0.526 | 0.868 / 0.626 |
| length_b 0.5 | 0.780 | 0.564 | 0.614 | 0.782 / 0.577 | 0.779 / 0.551 |
| **header_weight 0.5** | 0.844 | 0.642 | 0.688 | 0.806 / 0.626 | 0.882 / 0.658 |
| header_weight 2 | 0.785 | 0.556 | 0.607 | 0.801 / 0.543 | 0.770 / 0.569 |
| phrase_weight 0 | 0.824 | 0.585 | 0.640 | 0.811 / 0.573 | 0.838 / 0.598 |
| phrase_weight 1 | 0.842 | 0.605 | 0.659 | 0.806 / 0.589 | 0.877 / 0.621 |
| act_boost 0.5 | 0.834 | 0.604 | 0.657 | 0.796 / 0.597 | 0.873 / 0.612 |
| act_boost 2 | 0.849 | 0.610 | 0.665 | 0.806 / 0.601 | 0.892 / 0.619 |
| **expansion_weight 0.3** | 0.844 | 0.616 | 0.668 | 0.806 / 0.610 | 0.882 / 0.623 |
| expansion_weight 0.8 | 0.817 | 0.594 | 0.645 | 0.791 / 0.578 | 0.843 / 0.611 |
| header 0.25 | 0.832 | 0.647 | — | — | — |
| **header 0.5 + expansion 0.3** (shipped) | 0.849 | 0.653 | — | 0.806 / 0.636 | 0.892 / 0.671 |
| header 0.5 + act boost 2 | 0.844 | 0.640 | — | — | — |

act boost 2 helped one half only and was not kept. The DE/EN glossary swap (French glossary terms become the
lexical query, the original words the low-weight expansion, for questions guessed as DE/EN) then moved the EN MRR from
0.25 to 0.33 and the overall MRR from 0.653 to 0.657 (the shipped row of the G2 table). Tuning is worth ≈ +4 MRR
points of the +35 over M1; the rest is the IDF weighting itself.

## Reranker cost (CPU)

| measurement | result |
|---|---|
| Container start → ready (model load + warm-up, `--max-batch-tokens 4096`) | 5 min 53 s (load 4 s, warm-up 5 min 48 s); healthcheck `start_period` 300 s + 30 × 10 s retries covers it |
| TEI notes | ORT backend skipped (no ONNX file in the pinned revision) → candle fp32; « Backend does not support a batch size > 4 » → `max_batch_requests = 4`; inputs truncated at 4,096 tokens (`--auto-truncate`) |
| 16 candidates, compact header + 600 body chars (avg 909 chars), idle host | 20.3 s, 19.3 s |
| 1 candidate, 1,800 body chars (2,105 chars) | 3.7 s, 3.9 s |
| 16 candidates, 1,800 body chars (avg 2,109 chars) | 60.3 s, 59.9 s |
| G2 run (205 searches, k = 16, 600 chars, shared host, load 4–9) | rerank p50 17.7 s, p95 34.4 s, max 43.9 s; SQL p50 0.17 s |
| `tests/contract/test_mcp_m2.py` (real container) | 40 short pairs in 2 batches (32 + 8) 10.3 s; 16-candidate search, 300 chars 13.7 s; cited article stays first 6.6 s; timeout 0.001 s → lexical fallback with reason |

Benchmark: `Reranker.rerank` on 16 long chunks of the sample (`length(body) > 1900`), two calls each, after the
G2 run, host otherwise idle. PLAN E79 (int8 model, 40 candidates in 10.6–16.9 s on 4 vCPU) is not reproduced by
the pinned fp32 safetensors: 16 × 1,800 characters take ≈ 60 s, which is why the dev profile uses 600 body
characters and a 60 s timeout (`deploy/compose.dev.yml`). An int8 ONNX export (TEI ORT backend) is the follow-up
recorded in the ADR.

## Known queries

| question | M1 (`rank_cd`) | bm25, no expansion | bm25 (shipped) | bm25 + rerank |
|---|---|---|---|---|
| G2-001 « Quel est le délai de préavis en cas de licenciement selon le Code du travail ? » → C. trav. L. 124-3 | 8 | 3 | 2 | **1** |
| G2-106 « Constitution liberté d'expression » → Constitution art. 23 | — | — | **1** | **1** |
| G2-186 « Welche Kündigungsfrist gilt, wenn der Arbeitgeber einem Arbeitnehmer kündigt? » → L. 124-3 | — | — | 3 | **1** |
| G2-196 « What notice period must an employer give when dismissing an employee? » → L. 124-3 | — | — | 6 | **1** |

(rank of the target in the top 20; — = not in the top 20). The first three are contract tests
(`tests/contract/test_mcp_m2.py`). The 2026-10-17 e2e transcript's LLM query (« délai de préavis … licenciement »
with the act named) did not return L. 124-3 at `top_k` 10 under M1.

## Where the lexical first stage still misses (bm25, shipped)

32 of 205 items have Recall@10 < 1; for 23 no target is in the top 20.

- **Nothing in the top 20 for 14 FR questions, mostly a vocabulary mismatch between question and text.** The
  article uses other words, often older ones: « se marier » vs « contracter mariage » (C. civ. 144), « force de
  loi » vs « tiennent lieu de loi » (1134), « locataire » vs « preneur » (1728), « se défendre » vs « légitime défense » (C. pén. 416), « met le feu » vs
  « mis le feu » (510), « tribunaux » vs « juridictions » (Constitution 108), « chef de l'État » for the
  Grand-Duc (56). These words were **not** added to the synonym list afterwards: that would fit the list to G2.
  A lawyer-written G2 (M4) decides whether the list grows; the reranker cannot help when the target is outside
  the top K.
- **Untranslated DE/EN questions (9 of 20 with nothing in the top 20).** The worst case by design: the tool
  description tells the LLM to search with French legal terms; the glossary is only a safety net.
- **Second target ranked lower (2 items: G2-049, G2-067)** and targets at ranks 11–17 (7 items: G2-043, -052,
  -119, -136, -153, -177, -193).

## Warnings per code (G3, both modes)

| code | expected = present | | code | expected = present |
|---|---|---|---|---|
| W_NOT_OFFICIAL | 184 | | W_FUTURE_VERSION | 15 |
| W_SNAPSHOT | 184 | | W_DATA_QUALITY | 14 |
| W_PARTIAL_REPEAL_UNKNOWN | 56 | | W_BASE_LEGALE_ABROGEE | 12 |
| W_DERIVED_ELI | 21 | | W_ARTICLE_REPEALED, W_CONSO_DISCONTINUED, W_NOT_YET_IN_FORCE, W_STUB | 6 each |
| W_NOT_CONSOLIDATED | 21 | | W_SUNSET | 5 |
| W_REPEALED | 17 | | W_CONSO_LAGGING, W_OUT_OF_SCOPE | 3 each |
| | | | W_CLOCK_SUSPECT, W_DATA_STALE | 2 each |

## Caveats

- Small sample (10,220 chunks): idf values, the pool sizes and SQL latency change at full scale (≈ 400k chunks);
  the df lookup is capped at 5,000 index rows per term to bound its cost. Full-scale latency is measured in M4.
- Same author for G2 v1, the synonym list and the ranker (see `eval/README.md`); DE/EN items are few (10 each), so
  their numbers move by 0.1 per item.
- W_PDF_EXTRACTED, W_SUPERSEDED_BY_CODE and W_TEMPORARY_ACT cannot be triggered by the sample release yet (no PDF
  text, recueil routing or temporary-act flags loaded); G3 is regenerated (`gen-g3`) once the M2 ingest is
  rebuilt.
