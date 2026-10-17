# ADR 0001 — Lexical ranker (OD7): native FTS with an IDF-weighted SQL ranker, reranker optional

- Status: **accepted for M2** (draft evidence: G2 v1 is `draft_unvalidated`; revisit with the lawyer-validated
  G2 in M4 and at full scale)
- Date: 2026-10-17
- Deciders: M2 serving stage (Task B); OD7 owner: project lead
- Evidence: `eval/reports/m2-baseline.md` (numbers, commands), `eval/reports/G2-*.json`

## Context

PLAN §15 OD7 asks which lexical engine ships: **native PostgreSQL FTS + reranker** (recommended), pg_textsearch
(BM25 extension) « if the M2 bake-off gains ≥ 2–3 points », or ParadeDB (AGPL). Embeddings stay off (user
decision), so lexical retrieval is the whole first stage.

The M1 ranking — `ts_rank_cd` over an OR query of the question's lexemes, then a Python re-ordering by the number
of distinct query terms matched — has no IDF. Frequent words dominate: in the 2026-10-16 sample « code » occurs in
8,506 of 10,220 chunks (every code header), « travail » in 2,080, the interrogative « quel » in 121, while
« préavis » occurs in 72. The e2e run of 2026-10-17 showed the consequence: « délai de préavis … licenciement »
did not return Code du travail art. L. 124-3 even at `top_k` 10; « Constitution liberté d'expression » returned
eight press-law articles (the Constitution says « liberté de manifester ses opinions »).

PLAN §8.3 makes the cross-encoder reranker « mandatory in lexical mode » for this reason. Measured on the 4-vCPU
dev host, `bge-reranker-v2-m3` (fp32) on TEI cpu-1.9.4 costs ≈ 19 s for 16 candidates of compact header + 600
characters and ≈ 60 s with 1,800 characters (idle host; over G2 on a busy host p50 17.7 s, p95 34.4 s), and it can
only re-order the candidates the first stage returns.

## Options

1. **Native FTS as in M1** (`ts_rank_cd` + coverage order), quality from the reranker only.
2. **Native FTS with an IDF-weighted ranker written in SQL** (this ADR): BM25-like score from the existing
   `tsvector` columns and GIN index — per-term idf (document frequency counted on the GIN index, capped),
   saturated term frequency (`ts_rank` of the single term), a header-match weight (chunk headers carry the
   breadcrumb, article number and index terms), a proximity bonus for adjacent query terms, a soft length
   normalisation and a boost for the acts the query names; plus server-side query preparation (citation spans and
   interrogatives out, a small curated FR synonym list and DE/EN → FR glossary in, at a lower weight).
3. **pg_textsearch** (Timescale BM25 index): a compiled extension in the `lgx-db` image.
4. **ParadeDB** `pg_search`: AGPL, separate image (`bm25-agpl` profile in Appendix C).

## Evidence (G2 v1, 205 questions, sample release, `eval/reports/m2-baseline.md`)

| First stage | R@1 | R@5 | R@10 | R@20 | MRR@10 | nDCG@10 |
|---|---|---|---|---|---|---|
| M1: `ts_rank_cd` + coverage (option 1) | 0.229 | 0.388 | 0.434 | 0.471 | 0.302 | 0.333 |
| option 2, no expansion | 0.527 | 0.763 | 0.820 | 0.846 | 0.639 | 0.678 |
| option 2 (shipped defaults) | 0.541 | 0.778 | 0.849 | 0.885 | 0.657 | 0.698 |
| option 2 + reranker (TEI, top 16, header + 600 characters) | **0.766** | **0.856** | **0.880** | 0.885 | **0.823** | **0.836** |
| option 1 + reranker | not run — bounded: Recall@10 ≤ 0.461 (option 1's Recall@16) | | | | | |

- The reranker only re-orders option 2's top 16, so Recall@20 stays 0.885; it lifts Recall@1 by 22 points and
  MRR@10 by 17 (63 questions better, 8 worse, 134 unchanged). Option 1 + reranker was not run (≈ 1 h of CPU on the
  shared host): re-ordering option 1's top 16 cannot bring Recall@10 above its Recall@16 of 0.461, far below option
  2 alone (0.849).
- The two known failures are fixed by option 2 without the reranker: L. 124-3 ranks 2nd for the e2e question
  (1st after reranking), Constitution art. 23 ranks 1st for « Constitution liberté d'expression ». The untranslated
  German and English notice-period questions rank L. 124-3 3rd and 6th (1st after reranking).
- Per language (option 2): FR R@10 0.886 / MRR 0.698; untranslated DE 0.50 / 0.22, EN 0.50 / 0.33 (10 items each;
  M1: 0.0 for both); with the reranker FR 0.916 / 0.857, DE 0.60 / 0.51, EN 0.50 / 0.50. The tool description
  now asks the LLM to search with French legal terms and gives DE/EN → FR examples; the glossary is the safety net.
- Knob defaults (header weight 0.5, expansion weight 0.3; phrase 0.5, length 0.25, act boost 1.0 unchanged) were
  chosen from a one-at-a-time sweep on G2 and kept only where both halves of G2 (odd / even items) improved; the
  gain from tuning is ≈ +4 MRR points of the +35.
- Latency: SQL p50 ≈ 0.2 s per search on the sample (the M1 query was the same order); the df lookup is capped at
  5,000 index rows per term so its cost stays bounded at full scale (≈ 400k chunks [E]).
- Resolver and warnings are unaffected: G1 exact accuracy 0.9965 (564/566), G3 mandatory-warning coverage 1.0
  (563/563 pairs, 0 forbidden, status 100 %), G0 20/20, in both modes.

## Decision

- **OD7 = native FTS, with the IDF-weighted SQL ranker (option 2) as the lexical first stage.** It lives in
  `db/schema/40_search.sql` (search API 2: new optional parameters of `lgx.search_candidates`, new
  `lgx.query_terms`, no table, index or extension change); the MCP server detects the API per release and keeps
  serving older releases with the M1 ordering. `LGX_LEXICAL_RANKER=rank_cd` restores the M1 ordering for
  comparisons.
- **The reranker stays recommended but is no longer mandatory on CPU** (`rerank` compose profile,
  `LGX_RERANK=auto`): PLAN §8.3's « mandatory in lexical mode » assumed the IDF-less first stage, which was unusable
  alone (Recall@10 0.43); option 2 alone reaches 0.85. The reranker still adds +22 points of Recall@1 and +17 of MRR,
  so it should be on wherever its latency is acceptable — GPU profiles (top 40) and the 16–32-core `cpu` profile
  (top 16). On a 4-vCPU host its 18–35 s per search is a deployment choice, reported in every result
  (`retrieval.reranked`, `rerank_status`, `rerank_reason`). Follow-up (M3/M5): measure an int8 ONNX export of the
  same model on TEI's ORT backend (PLAN E79: 40 candidates in 10.6–16.9 s on 4 vCPU) — a new pinned model artifact,
  to be reviewed like any dependency.
- **pg_textsearch: not built in M2 (time-boxed decision).** It would add a compiled extension and its build chain to
  the `lgx-db` image (a new pinned dependency to review, sign and ship in the platform bundle), its PG 18
  compatibility is unverified (PLAN Appendix E U11), and the dev host has < 5 GB of free disk. The bar set by OD7
  (« ≥ 2–3 points over native FTS + reranker ») is now measured against option 2; revisit only if the
  lawyer-validated G2 or full-scale latency (M4) shows option 2 falling short — then compare pg_textsearch BM25
  with option 2 on the same harness (`python -m eval.harness run --set G2`).
- ParadeDB stays excluded (AGPL sign-off not given).

## Consequences

- The PLAN §8.3 text « Lexical mode re-orders by coverage … The reranker is mandatory in lexical mode » and the
  Appendix D tool description are superseded for M2 releases: the description adds DE/EN → FR examples; the
  inputSchema stays verbatim.
- Gate G2 (PLAN §11.3, from M4): the frozen M2 baseline is the « option 2 (shipped defaults) » row (lexical) and the
  « option 2 + reranker » row; regressions are measured with the same harness and gold set.
- Risks: G2 v1, the synonym list and the ranker have the same author; DE/EN items are few; idf and pool sizes
  behave differently on the full corpus. Mitigations: lawyer-written G2 items (M4), the `rank_cd` switch, the
  knobs in `LGX_LEXICAL_OPTS` (eval only), and a full-scale latency check before the first full release.
