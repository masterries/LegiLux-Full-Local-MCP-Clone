# M2 acceptance — « Full text basis + lexical quality » (PLAN §13)

Checked 2026-10-17 on the M2 integration release `data-20261016-sample` (T = 2026-10-16, built with
`lgx ingest --sample --offline --include-m2`, restored as the runtime database `legilux` on `lgx-dev-db` and in the dev
stack `legilux-dev-*` with the `rerank` profile). Embeddings off (`provider: none`), sample only, no full crawl. Every
gold set is `draft_unvalidated` (lawyer review pending, PLAN §13 « + ≈ 4 ld »). Numbers: `m2-baseline.md`.

## Acceptance criteria

| # | Criterion (PLAN §13 M2) | Verdict | Evidence |
|---|---|---|---|
| A1 | Oracle thresholds met on the sample | **Met for the oracles that apply to the sample; one oracle not run** | **HTML twin** (§11.4.1: article-count equality ≥ 98 %, text similarity ≥ 0.98): all 5 XML/HTML twin pairs the sample has (a88 JO, AIFM consolidation, Constitution, Code du travail, Code civil) give identical article anchors, numbers and repeal/modifying flags (100 %) and similarity ≥ 0.98 (AIFM) / ≥ 0.99 (codes) / 1.0 (a88, Constitution) — `tests/unit/test_parse_html.py`, 13 passed incl. the large-fixture code twins. The « 200 acts » of §11.4.1 need the full crawl. **PDF oracle** (pdftotext spot-check, Task A): 5/5 PDFs `ok`, articles within ±1 % of the « Art. » candidates (route 1,216/1,217, CSS 1,908/1,911, communal 1,977/1,984, éducation 4,549/4,589, secteur financier 148/148). **Gates:** 27 pass / 6 skip (skips: no previous snapshot/run/release), parse failures 0, `pdf_article_counts` and `recueil_dedup` pass. **Not run:** the `SFHAJJI/lex-articles` parquet oracle (§11.4.2) — an external 61 MB dataset, not fetched under this stage's sample-only / disk rules; open for M4 |
| A2 | CSS spot-check ≥ 98 % | **Met** | `lgx content pdf-check` on `code/securite_sociale/20260101`, 50 articles (seed 0) against pdftotext: similarity mean **0.9995**, median 1.0, min 0.9922, **100 % of the articles ≥ 0.98**, precision 0.9999, numbering 1.0 (`data/reports/2026-10-16/pdf_check-eli-etat-leg-code-securite_sociale-20260101.json`). Served through MCP: « art. 24 du Code de la sécurité sociale » → the code's art. 24, p. 81–82, `W_PDF_EXTRACTED` (« p. 81–82 ») |
| A3 | **100 % mandatory warnings on G3** | **Met** | G3 v2 (199 items, regenerated on this release; covers W_PDF_EXTRACTED 15 pairs, W_SUPERSEDED_BY_CODE 3): lexical **628/628 = 1.000**, forbidden 0, status statements 1.000 (`G3-lexical-m2data.json`); reranked, the 10 search items whose hits can change under re-ranking: 32/32, forbidden 0 (`G3-rerank-search-subset-m2data.json`). The first v2 run was 0.987 (3 items): one generator rule and two serving gaps, fixed (`m2-baseline.md`, « What changed »). G3 v1 on the pre-PDF data: 563/563 (Task B). W_TEMPORARY_ACT is not triggerable in the sample (OD10) |
| A4 | Lexical + rerank baseline recorded (it calibrates the gates) | **Met** | `m2-baseline.md` « Final M2 baseline »: G2 lexical (205) R@10 **0.844**, MRR@10 **0.662**; lexical + rerank on the fixed 40-item subset R@10 **0.825**, MRR@10 **0.745** (before/after the PDF data: 0.825 / 0.749 → 0.825 / 0.745); full-set reranked reference 0.880 / 0.823 (Task B, pre-PDF data). G0 20/20 and G1 v2 0.996 in both modes. Reranked runs only on the fixed subsets (CPU budget), never two at a time |
| A5 | Restore time measured | **Met** | `lgx package --restore-to legilux` (pg_restore `-j 4` inside `lgx-dev-db`, then GRANTs, ANALYZE, read-only, row counts, smoke, rename-swap): pg_restore **13.8 s** (dump 15.0 MB → 321 MB database); the dev stack's `db-load` service (restore + `95_roles.sql` + ANALYZE + rename) **26 s** start to exit (`docker inspect legilux-dev-db-load-1`). Whole offline ingest + package + restore: 1 min 52 s with warm caches |
| A6 | Ranker decision recorded (OD7) | **Met** | `docs/adr/0001-lexical-ranker.md`: native FTS + IDF-weighted SQL ranker (search API 2), reranker recommended, optional on CPU. The M2 data confirm it: with twice the chunks the IDF ranker held (R@10 −0.5, MRR +0.5 points) while the M1 `ts_rank_cd` ordering lost 8 points R@10 |

## Deliverables (PLAN §13 M2 row)

| Deliverable | Status | Evidence / gap |
|---|---|---|
| HTML parser + HTML-twin oracle | Done | `lgx_ingest/parse/html.py` (M1); twin tests above |
| PDF codes (pages, then member-context segmentation) | Done | `lgx_ingest/parse/pdf.py`, `pdf_layout.py` (Task A): 5 sample PDFs, 12,866 units, every unit with `page_from/page_to` + `W_PDF_EXTRACTED`; served by MCP with page, member act and citation « Art. 12 de l'arrêté grand-ducal du 23 novembre 1955 … (Code de la route, version consolidée applicable au 12/06/2026) » (`tests/contract/test_mcp_m2.py`) |
| `recueil_members.yaml` v1 (lawyer) | **Partly** | v1 generated (758 members, 362 routed, 25 unresolved, 41 pending), every entry `reviewed: false`: the lawyer review (≈ 2 ld) is open |
| All labels, flags and amending lists | Done for the sample | labels consolidated 27, stub_fallback 2, original_never_amended 11, original_possibly_outdated 7, metadata_only 58,343; flags incl. `pdf_extracted`, `superseded_by_code` (355 acts); `acte_temporaire` texts stay metadata-only (OD10) |
| All relations | Done | 174,339 relations (SPARQL + AKN), recueil dedup re-points relations (57 dropped with their copies) |
| Reranker (batches ≤ 32; 40-candidate integration test against the real TEI image) | Done | TEI `cpu-1.9.4` pinned by digest, model sha256-pinned (`deploy/models.lock`, `make fetch-models`); `test_real_tei_reranks_40_candidates_in_batches` (32 + 8) against the real container; `make rerank-up`, `make dev-up DEV_PROFILES=rerank` |
| Search-filter contract tests | Done | `tests/contract/test_mcp_contract.py` (doc_types on the exact branch, unknown act_eli, in_force_only=false) + M2: act_eli of a superseded member searches the code text (`test_search_filtered_to_a_superseded_member_runs_in_the_code_text`) |
| pg_textsearch bake-off | **Not done (deviation, recorded)** | ADR 0001: not built in M2 (compiled extension + build chain in the airgap image); re-opened if the lawyer-validated G2 or the M4 full-scale latency shows the SQL ranker short |
| G1 and G3 v1, G2 v1 (150) | Done (draft) | G1 v2 564, G3 v2 199 (generated per release), G2 v1 205 (185 FR + 20 DE/EN, > 150); all `draft_unvalidated` — the ≥ 30 % lawyer validation of G2 (PLAN §11.1) is open |
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

## Open (not M2-blocking, owners named in `docs/dev/INTERFACES.md` § 10–11)

Lawyer review of `recueil_members.yaml`, G1/G2/G3 (≈ 4 + 2 ld); the lex-articles oracle; 58 anonymous PDF member parts;
the CSS « Titre IX » heading that swallowed a provenance note; the `pdf_member_without_articles` data-quality row on
`code/route` that names members which do have articles (it puts W_DATA_QUALITY on every PDF code article); G1 resolver
gaps (backward « …, art. unique », RGPD spelled out); full-set reranked G2 on the M2 data (≈ 1 h CPU) and the int8 ONNX
reranker (ADR 0001).
