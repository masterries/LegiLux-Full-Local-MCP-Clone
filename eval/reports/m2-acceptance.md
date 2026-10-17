# M2 acceptance — « Full text basis + lexical quality » (PLAN §13)

Checked 2026-10-17 on the M2 integration release `data-20261016-sample` (T = 2026-10-16, built with
`lgx ingest --sample --offline --include-m2`, restored as the runtime database `legilux` on `lgx-dev-db` and in the dev
stack `legilux-dev-*` with the `rerank` profile). Embeddings off (`provider: none`), sample only, no full crawl. Every
gold set is `draft_unvalidated` (lawyer review pending, PLAN §13 « + ≈ 4 ld »). Numbers: `m2-baseline.md`.

**Re-checked after the M2 review (same day, release rebuilt).** The review showed that the first verdicts of A1/A2
rested on an oracle that could not see merged articles: the spot-check ended the reference article at the last token
the parser had matched, and the ±1 % gate counted « Art. » candidates from the parser's own lines. With the corrected
oracle (reference article ends at the next `pdftotext` heading; heading **recall** — every reference heading must
start a unit on its page), the integration parser (`pdf-1`) **fails** on the CSS: recall 0.903 (204 headings inside
another article: 173 « 1  Art. 7. » headings after an alinea number, plus quoted amendments), similarity min 0.726.
The verdicts below are those of the rebuilt release (`pdf-2`).

## Acceptance criteria

| # | Criterion (PLAN §13 M2) | Verdict | Evidence |
|---|---|---|---|
| A1 | Oracle thresholds met on the sample | **Met for the oracles that apply to the sample; one oracle not run** | **HTML twin** (§11.4.1: article-count equality ≥ 98 %, text similarity ≥ 0.98): all 5 XML/HTML twin pairs the sample has (a88 JO, AIFM consolidation, Constitution, Code du travail, Code civil) give identical article anchors, numbers and repeal/modifying flags (100 %) and similarity ≥ 0.98 (AIFM) / ≥ 0.99 (codes) / 1.0 (a88, Constitution) — `tests/unit/test_parse_html.py`. The « 200 acts » of §11.4.1 need the full crawl. **PDF oracle** (`lgx content pdf-check` on the parser output, 50 articles, seed 0, + heading recall over every page): 5/5 PDFs `ok` — heading recall CSS **0.9995** (2,112 of 2,114 reference headings start a unit), route 0.9983, communal 0.9959, éducation 0.9929 (81 further headings inside quoted amendments, by design), secteur financier 1.000; spot-check similarity mean 0.997 / 0.999 / 0.995 / 1.000 / 0.999, precision ≥ 0.9988, numbering 1.0 (`data/reports/2026-10-16/pdf_check-*.json`). **Gates:** 28 pass / 6 skip (no previous snapshot/run/release) / 1 warn (`pdf_member_numbering`: 13 of 668 member parts repeat an article number, ≤ 5 % per code), parse failures 0, `pdf_article_counts`, `recueil_dedup`, `recueil_routing_attached` (345 routed members, each with its articles in the code text after the dedup) pass. **Not run:** the `SFHAJJI/lex-articles` parquet oracle (§11.4.2) — an external 61 MB dataset, not fetched under this stage's sample-only / disk rules; open for M4 |
| A2 | CSS spot-check ≥ 98 % | **Met** (mean; 47 of 50 articles individually) | `lgx content pdf-check` on `code/securite_sociale/20260101` (parser output), 50 articles (seed 0) against pdftotext: similarity mean **0.997**, median 1.0, min 0.957, **94 % of the sampled articles ≥ 0.98** (the 3 below have precision 1.0: the reference keeps footnote markers « 2) 3) » the unit drops), precision 0.9997, numbering 1.0; heading recall **0.9995** (the integration parser: 0.903). Served through MCP: « art. 24 / 6 / 7 / 426 du Code de la sécurité sociale » → the code's own articles (p. 81, 72, 72, 234, `W_PDF_EXTRACTED` only — no `W_DATA_QUALITY` any more); « art. 74bis du Code de la sécurité sociale » → `NOT_FOUND` naming the member law that has one |
| A3 | **100 % mandatory warnings on G3** | **Met** | G3 v3 (199 items, regenerated on the review release): lexical **618/618 = 1.000**, forbidden 0, status statements 1.000 (`G3-lexical-m2review.json`). G3 v2 (integration release) on the review data: 0.981 — 11 items whose expectations the review changed on purpose (W_DATA_QUALITY rows now part/page-level; CSS « art. 139 » / « art. L. 327-2 » no longer answered with member articles), `m2-baseline.md`. Integration release: v2 628/628 lexical, reranked search subset 32/32. W_TEMPORARY_ACT is not triggerable in the sample (OD10) |
| A4 | Lexical + rerank baseline recorded (it calibrates the gates) | **Met** | `m2-baseline.md` « M2 review re-baseline »: G2 lexical (218 = 205 + 13 PDF-code questions) R@10 **0.821**, MRR@10 **0.643**, non-leaky (187) 0.797 / 0.616, PDF-code questions (15) 0.600 / 0.369; on the 205 integration ids 0.839 / 0.664 (integration 0.844 / 0.662); lexical + rerank on the fixed 40-item subset R@10 **0.825**, MRR@10 **0.749** (integration 0.825 / 0.745). G0 20/20, G1 0.997 (622/624, incl. 60/60 independent PDF items). Reranked runs only on the fixed subset (CPU budget), one at a time |
| A5 | Restore time measured | **Met** | `lgx package --restore-to legilux` (pg_restore `-j 4` inside `lgx-dev-db`, then GRANTs, ANALYZE, read-only, row counts, smoke, rename-swap): pg_restore **13.8 s** (dump 15.0 MB → 321 MB database); the dev stack's `db-load` service (restore + `95_roles.sql` + ANALYZE + rename) **26 s** start to exit (`docker inspect legilux-dev-db-load-1`). Whole offline ingest + package + restore: 1 min 52 s with warm caches |
| A6 | Ranker decision recorded (OD7) | **Met** | `docs/adr/0001-lexical-ranker.md`: native FTS + IDF-weighted SQL ranker (search API 2), reranker recommended, optional on CPU. The M2 data confirm it: with twice the chunks the IDF ranker held (R@10 −0.5, MRR +0.5 points) while the M1 `ts_rank_cd` ordering lost 8 points R@10 |

## Deliverables (PLAN §13 M2 row)

| Deliverable | Status | Evidence / gap |
|---|---|---|
| HTML parser + HTML-twin oracle | Done | `lgx_ingest/parse/html.py` (M1); twin tests above |
| PDF codes (pages, then member-context segmentation) | Done | `lgx_ingest/parse/pdf.py`, `pdf_layout.py` (Task A, `pdf-2` after the review): 5 sample PDFs, 12,586 units after the dedup, every unit with `page_from/page_to` + `W_PDF_EXTRACTED`; served by MCP with page, member act and citation « Art. 12 de l'arrêté grand-ducal du 23 novembre 1955 … (Code de la route, version consolidée applicable au 12/06/2026) » (`tests/contract/test_mcp_m2.py`) |
| `recueil_members.yaml` v1 (lawyer) | **Partly** | v1 regenerated after the review (756 members, 351 routed — excerpt parts no longer routed —, 30 unresolved, 41 pending), every entry `reviewed: false`: the lawyer review (≈ 2 ld) is open |
| All labels, flags and amending lists | Done for the sample | labels consolidated 27, stub_fallback 2, original_never_amended 11, original_possibly_outdated 7, metadata_only 58,343; flags incl. `pdf_extracted`, `superseded_by_code` (355 acts); `acte_temporaire` texts stay metadata-only (OD10) |
| All relations | Done | 174,339 relations (SPARQL + AKN), recueil dedup re-points relations (57 dropped with their copies) |
| Reranker (batches ≤ 32; 40-candidate integration test against the real TEI image) | Done | TEI `cpu-1.9.4` pinned by digest, model sha256-pinned (`deploy/models.lock`, `make fetch-models`); `test_real_tei_reranks_40_candidates_in_batches` (32 + 8) against the real container; `make rerank-up`, `make dev-up DEV_PROFILES=rerank` |
| Search-filter contract tests | Done | `tests/contract/test_mcp_contract.py` (doc_types on the exact branch, unknown act_eli, in_force_only=false) + M2: act_eli of a superseded member searches the code text (`test_search_filtered_to_a_superseded_member_runs_in_the_code_text`) |
| pg_textsearch bake-off | **Not done (deviation, recorded)** | ADR 0001: not built in M2 (compiled extension + build chain in the airgap image); re-opened if the lawyer-validated G2 or the M4 full-scale latency shows the SQL ranker short |
| G1 and G3 v1, G2 v1 (150) | Done (draft) | G1 v2 564 + 60 PDF-code items from `pdftotext` (independent of the parser), G3 v3 199 (generated per release), G2 v1 218 (198 FR + 20 DE/EN, > 150; 15 on PDF codes; 31 French items marked `leaky`); all `draft_unvalidated` — the ≥ 30 % lawyer validation of G2 (PLAN §11.1) is open |
| `lgx ingest --full` documented and dry-run on the sample | Done | `docs/dev/DEVELOPMENT.md` « Full ingest »; `lgx --t 2026-10-16 ingest --full --dry-run`: 0 requests, 25,569 versions with a file (25,520 to fetch + 52 Q-MANIF SPARQL requests), 7.1 h at 1 req/s, 12.8 h under the 2,000/h ceiling = 4 nightly windows, raw ≈ 2.9 GB [E] (`data/reports/2026-10-16/fetch_plan_full.json`) |

## Found and fixed during the integration (would have failed acceptance)

- `get_act` of every member act routed to a code crashed (`BACKEND_UNAVAILABLE`): the `{code}` placeholder of
  W_SUPERSEDED_BY_CODE collided with the `code` argument of the warning catalogue.
- Superseded member acts were **not** routed to the code text: `get_article(agd/1955/11/23/n1, 12)` answered
  `NOT_FOUND « texte disponible seulement en PDF »`; now the code's article (redirect) or, for a member with its own
  original, the original plus a pointer to the code article.
- « art. 24 du Code de la sécurité sociale » was `AMBIGUOUS` among 14 articles 24 of the laws printed in the CSS PDF; now
  the code's own article. Member articles were cited as « Art. 12, Code de la route » (20 different articles 12); now
  « Art. 12 de l'arrêté grand-ducal du 23 novembre 1955 … (Code de la route, …) » or « … d'un acte membre non identifié ».
- `lgx ingest --full` would have fetched only the 49 sample files (Q-MANIF covered the sample works only), had no
  `--dry-run` and ignored the nightly window; `make dev-down` left the rerank container running.

## Found by the M2 review and fixed (would have failed a correct acceptance)

- 173 CSS articles glued to their predecessor (« 1  Art. 7. » after an alinea number), quoted amending provisions
  parsed as articles of the member (the « Art. 8. » quoted inside art. 59 of the loi du 19 décembre 2008 cut art. 59
  short and made its own art. 8 `AMBIGUOUS`), EU « Complément »
  regulations attached to the preceding member law (« art. 8 de la loi du 19 mai 2009 » answered with art. 8 of
  Regulation (CE) 1082/2006), repealed articles whose heading was glued to the previous one.
- The PDF oracle itself (above) and G1, which was generated from the parser's own output: G1 now has 60 items from
  `pdftotext` alone.
- « art. N » of the CSS could resolve to a member's article; routed members whose code article was missing resolved
  to nothing useful; PDF copies of members with their own consolidation competed in search (the CSS reprints 204
  articles of members served from their own consolidation, mostly the Code du travail; 70 with other wording); the
  same `wId` printed twice in `code/fonction_publique` gave two hits.
- Search filtered to a member applied the filter after `LIMIT` (member articles outside the first 40 candidates were
  lost); the UI's 20 s tool timeout was below the reranker's; the reranker defaults (40 × 1,800 characters: ≈ 2.5 min
  estimated on this host from 16 × 1,800 ≈ 60 s) were not CPU-safe.

## Open (not M2-blocking, owners named in `docs/dev/INTERFACES.md` § 10–12)

Lawyer review of `recueil_members.yaml`, G1/G2/G3 (≈ 4 + 2 ld) and lawyer-written G2 questions (31 French items reuse
the target's wording); the lex-articles oracle; 13 PDF member parts that still repeat an article number (gate warn);
32 éducation and 8 communal reference headings without a unit (recall 0.993 / 0.996); the excerpt check against the
JO article count (an excerpt part is detected from its heading/preamble only); G3 is still generated from the
database (no independent W_DATA_QUALITY oracle); G1 resolver gaps (backward « …, art. unique », RGPD spelled out);
full-set reranked G2 incl. the PDF-code questions (≈ 1 h CPU) and the int8 ONNX reranker (ADR 0001).
