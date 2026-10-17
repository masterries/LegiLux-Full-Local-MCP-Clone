# Appendix E — Evidence index

This appendix belongs to [PLAN.md](../PLAN.md). It maps each key fact to its source and its verification status. Every source was produced or checked on **2026-10-16**.

**Source abbreviations.** Paths are relative to the research archive of the planning run. Recommendation: commit that archive as `docs/research/` next to the plan, so every row stays checkable.

| Abbrev. | File |
|---|---|
| **GR** | `research-results/gaps-and-reconciliation.md` (authoritative where tracks conflict) |
| **SJ** | `research-results/sparql-jolux-model.md` |
| **CF** | `research-results/content-formats.md` |
| **SS** | `research-results/scope-sizing.md` |
| **DB** | `research-results/db-retrieval-design.md` |
| **AG** | `research-results/airgap-llm-mcp-stack.md` |
| **PA** | `research-results/prior-art.md` |
| **Jn** | Judge re-checks, `plan/verify/judge-checks.txt` |
| **Pn** | Plan-writer re-checks, `plan/verify/plan-checks.txt`; raw outputs in `plan/verify/w/` (P9–P11: revision re-checks) |
| **RV** | Plan review of 2026-10-16 (live checks quoted in the review findings; not re-run by the plan writer unless a P-check says so) |
| `out/…`, `q/…`, `bin/…` | Under `research/gaps-and-reconciliation/` unless another track is named |

**Status markers.**
- **V** — verified live, with evidence.
- **E** — estimate or extrapolation.
- **U** — unverified. U-rows are listed in E.11, each with its build-time check and fallback.

## E.1 Endpoint and access

| # | Fact | Value | Source | Status |
|---|---|---|---|---|
| E1 | Store size and layout | 13,307,204 triples; 395,094 named graphs (one per resource, `<work>/graph` with `dct:modified`); union default graph | SJ verified facts | V |
| E2 | Sorted paging cap | `ORDER BY … LIMIT/OFFSET` beyond 10,000 → HTTP 500 `SR353`; `LIMIT 10 OFFSET 9990` → 200 | GR §1.1; `out/cap_*` | V |
| E3 | Unsorted pulls are uncapped | Act table: 151,253 rows / 46,136,891 B / 5.18 s; `LIMIT 200000` → 151,250 distinct rows, 2.17 s | GR §1.1 | V |
| E4 | `ORDER BY` without a LIMIT works | C07 returned 36,875 sorted rows | J10 | V |
| E5 | Gateway timeout; DESCRIBE broken; CONSTRUCT and POST work | ≈ 45 s (Whitelabel 500 at 45.5 s); `SP031` | SJ verified facts | V |
| E6 | Malformed `dct:modified` literals | 12 of 439 watermark rows (`…472Z+02:00`); `YEAR()`/`MAX()` misbehave | SJ verified facts | V |
| E7 | `REPLACE` with a regex returns its input unchanged | Hence "/graph" is stripped client-side | J10 (scope-sizing report) | V |
| E8 | robots.txt, main host | Disallows `/publications-regroupees`, `/api/rss-adm.xml`, `/eli/etat/adm/`, `/reg_ue/`, `/dir_ue/`, `/search`, `/*.svg`, `/*.docx` + 9 URIs incl. `/eli/etat/leg/loi/2007/01/15/n2/jo` (naturalisation) | GR §1.2; `out/robots_main.txt` | V |
| E9 | robots.txt, data host | Disallows `/api/rss-adm.xml`, `/eli/*`, `/filestore/*` | GR §1.2; `out/robots_data.txt` | V |
| E10 | Filestore on the main host | Not disallowed; SPA `manifestation_prefix`; HEAD 403, Range 206, IMS 304, no ETag, XML not gzipped | CF §1.2; DB verified facts | V |
| E11 | `legilux.public.lu/eli/…` pages | SPA shell only | CF verified facts | V |
| E12 | Docker Hub anonymous pulls | 429; `mirror.gcr.io` served pgvector with the same digest | DB verified facts | V |

## E.2 Selection and status

| # | Fact | Value | Source | Status |
|---|---|---|---|---|
| E13 | Consolidations | 4,719 versions over 1,416 works | SS, SJ | V |
| E14 | Window rule | 1,213 works with exactly one window-current version | GR §1.3 (`bin/recon.py`, `out/c1–c3`) | V |
| E15 | `applicable` flag | 1,181; 2 wrong (`agd/1927/05/04/n1/…/19300129`, `loi/1992/06/17/n1/…/20090616`); 34 window-current stubs lack it | GR §1.3 | V |
| E16 | "No end date" rule | 1,214; includes 18 future versions, misses 17 current | GR §1.3 | V |
| E17 | Usable current consolidations | 1,129 (1,097 XML + 32 PDF-only); 1,118 in `/etat/leg/` | GR §1.3 | V |
| E18 | Repealed base, never closed | 35 (e.g. `loi/1999/05/31/n8/consolide/20180918`, base repealed 2026-08-01) | GR §1.3 | V |
| E19 | Stubs | 49 current (15 flagged + 34 status-less); 10 with an older fallback, 39 without; 34 older than 6 months; e.g. `loi/1993/04/05/n1` falls back to 2003-10-01 | GR §1.3 | V |
| E20 | Discontinued consolidations | 40 (C04's 1,179 = 1,129 + 10 + 40); e.g. `loi/1922/03/05/n1/consolide/19650531` ended 2004 | GR §1.3 | V |
| E21 | Future versions | 34 versions over 18 works, 26 with files | GR §2.1; SS verified facts | V |
| E22 | Silent status flips | Acts with dnl 2026-09-01..10-16: 266 graph-modified before dnl vs 3 after; 341 future-dnl acts still in force; consolidation `rgd/2011/02/27/n10/consolide/20261011` pre-published 2026-10-08 | GR verified facts (`q/t08`, `q/f1`) | V |
| E23 | Date vs flag conflicts | 1 in force with past dnl; 4 not-yet with past dei; 3 no-longer without past dnl; 50 implicit without dnl | GR §2.1 | V |
| E24 | Act status counts | in force 119,980; no-longer 31,162; not-yet 58; implicit 50 (151,250 acts) | SJ verified facts | V |
| E25 | Mémorial A / B | leg 80,918 (64,536 in force; 16,320 no-longer with date); adm 70,332 | SS verified facts | V |
| E26 | In-force LOI / RGD | LOI 8,060 = 8,033 leg + 27 adm; RGD 9,747 = 9,734 + 13 | GR §1.4 (`out/k2`) | V |
| E27 | LOI breakdown (leg, in force) | never modified 6,803; up to date 467; never consolidated 721; only ended 16; stub 18; lags 8 | GR §1.4 (`bin/acts.py`) | V |
| E28 | RGD breakdown | 8,445 / 486 / 764 / 17 / 19 / 3 | GR §1.4 | V |
| E29 | Lagging consolidations | 13 of 1,129 (12 > 1 year, 1 by 14 days); codes and Constitution 0 | GR §2.2 (`bin/lag.py`) | V |
| E30 | Temporary traffic RGDs; RGDs with repealed legal bases | 663 (no dnl); 1,586 of 9,361 RGDs with `basedOn` | GR §2.6 (`out/tt1`, `out/b1`) | V |
| E31 | Repeal metadata | 16,368 repealed leg acts; 16,320 with date; 12,065 with a repealer link | SS verified facts | V |
| E32 | Pure amending acts | 5,773 in force; 2,888 absorbed or targeting repealed acts; 2,767 target unconsolidated acts; 118 newer than the consolidation | SS verified facts | V (heuristic classification) |
| E33 | URI date ≠ `dateApplicability` | `recueil/sites_monuments/20142209` | GR verified facts | V |
| E34 | Multi-valued `dateEntryInForce` | 151,252 values over 151,249 acts; cause not established | SJ §2 (predicates on `jolux:Act`) | V (count) / U (cause) |
| E35 | `jolux:Article` graph is stale | 58,673 rows / 15,770 distinct; 2010-12-21 … 2021-12-26 | GR §1.4; SJ | V |

## E.3 Codes, structure and formats

| # | Fact | Value | Source | Status |
|---|---|---|---|---|
| E36 | Code types | 7 CODE (civil, commerce, procédure pénale, pénal, travail, consommation, procédure civile), 9 CODE_RECUEIL (8 current; `cooperation_judiciaire` none); 15 current code versions; CSS = CODE_RECUEIL with base `loi/1925/12/17/n1` | GR §1.5 | V |
| E37 | No RDF links from recueils to members | Only isMemberOf, isRealizedBy, … | GR §2.3 (`out/r1`, `out/r2`) | V |
| E38 | XML code-recueil composition | `code/fonction_publique/20261011` 6,550,354 B; 404 of 478 `wId`s equal the member's current version; generated `art_N…` ids; malformed `…/n2//consolide/…` | GR §2.3 | V (1 MB sample) |
| E39 | Code de la route PDF | 35,507,629 B, 969 pages, text layer (120 fonts with ToUnicode, 10,203 Tj/TJ in 94 streams); members `loi/1955/02/14/n3` (42×) and `agd/1955/11/23/n1` (162×) have no consolidation | GR §2.3 | V |
| E40 | CSS PDF | 5,059,985 B, 838 pages, ≈ 3.0M chars, 1,583 `Art.` line starts | SS verified facts | V |
| E41 | PDF-only current consolidations | 32, total 374.7 MB (`environnement_annexes` 239.9 MB) | SS verified facts | V |
| E42 | AKN dialect | CSD13 namespace + `scl:`; in-XML article ELIs (`uriThis`); `textualMod`, notes `A\|pubdate\|actdate\|type\|memNo`; `embeddedStructure` (59 id-less of 288); `style="del"`; stale `<meta>` (`docDate 2999-01-01`) | CF verified facts; PA §4 | V |
| E43 | JO XML has only document-level ELIs | `loi/2016/12/23/n25/jo`: 9 articles without `uriThis` | GR §2.4 | V |
| E44 | HTML is XSLT output with the same anchors | `div id="art_N" class="richtext_article"`; JSON-LD `legislationLegalForce` disagrees with SPARQL | CF verified facts | V |
| E45 | Parser prototype counts | a88 11; AIFM consolidation 229 (229 ELI); travail 1,196 (1,179 ELI); civil 2,301; Constitution 132; Code pénal 699 | CF §2.6; SS verified facts | V |
| E46 | Article numbering irregularities | `art_9_10` vs `art_9_et_10`; `art_17--20`; `art_l_121-1` | DB verified facts | V |
| E47 | Article wording dates | `…/code/travail/art_l_121-1/20201101` → "rédaction depuis" (inference) | CF verified facts | V (data) / inference |
| E48 | Base64 images | Up to 1.9 MB per file (`rgd/2018/10/02/a936/jo`) | CF verified facts | V |
| E49 | Languages | FRA 263,155; ENG 103; DEU 14; LTZ 2 (only the 2023 Constitution + treaties) | DB/CF; GR §2.5 (`out/g1`) | V |
| E50 | Repealed article served as live by prior art | legalize-lu shows a686 Art. 56 as live text; the source marks it `style="del"` | PA verified facts | V |
| E51 | Official short-citation article | a686 Art. 77 « La référence à la présente loi peut se faire sous une forme abrégée … » | DB verified facts | V |
| E52 | Dated-citation ambiguity | Only 2,078 of 8,060 in-force LOIs have a unique date; up to 35 share one; 20 on 2018-08-01 | DB verified facts | V |

## E.4 Licence and legal texts

| # | Fact | Value | Source | Status |
|---|---|---|---|---|
| E53 | FAQ grants CC BY 4.0 | « Les textes et les métadonnées sont mis à disposition selon la licence … by/4.0 » | GR verified facts (`out/faq_12-introduction.html`) | V |
| E54 | Site terms are restrictive "sauf indication contraire" | `14-utilisation.html` quote | GR verified facts | V |
| E55 | Licences per manifestation | CC BY 350,336; licenceSCL 149,106; all current consolidations CC BY | CF/SS verified facts; GR §1.6 | V |
| E56 | licenceSCL has no defined terms | Only prefLabel "Licence de réutilisation SCL", created 2016-01-01, CURRENT | GR (`out/l2`) | V |
| E57 | Licence crosstab (in-force LOI/RGD/AGD/RMIN, 28,703 acts) | CC-BY XML 8,115; CC-BY HTML only 15,502; text only licenceSCL 619; PDF-only licenceSCL 4,442; CC-BY PDF only 25 | GR §1.6 (`out/l4`) | V |
| E58 | DOCX-only CC BY cases | `rgd/2015/06/25/n12/jo`, `rgd/2003/01/24/n2/jo` (DOCX is robots-disallowed) | J10; `plan/verify/q_manif.csv` | V |
| E59 | Copyright exception | Art. 10, 8° of the amended loi du 18 avril 2001 (not Art. 8); scope over consolidations, markup and database right not established | GR §1.6 (`out/copyright_law`) | V (text) / U (scope) |
| E60 | JO authenticity | `loi/2016/12/23/n25/jo`, art. 5: « Le Journal officiel publié fait seul foi et produit des effets juridiques. » | GR §2.8 | V |
| E61 | Naturalisation titles | 511 in-force LOIs with "naturalisation" + "conférant\|accordant"; 460 more with "naturalisation" only (e.g. "… qui accorde la naturalisation au sieur Theisen …") | J9 (`out/natur`, `out/natur2`) | V |
| E62 | `legalValue` | officiel: 3,190 consolidation / 113,209 Mémorial A manifestations; definitif 8,660; semantics unknown | J9 (`out/legalvalue`) | V (counts) / U (meaning) |
| E63 | data.public.lu dataset | `62c83bfd9794ec8e47b5bc68`, cc-by, no bulk dump | PA verified facts | V |

## E.5 Feeds, freshness, sync

| # | Fact | Value | Source | Status |
|---|---|---|---|---|
| E64 | Atom ELI feed | 1,439 entries (2026-08-17 → 10-15), adm 836 / leg 603 / **consolidations 0**; mirrors graph edits | GR §2.7 | V |
| E65 | RSS | `rss-leg.xml` 50 items | GR §2.7; PA | V |
| E66 | Consolidation timeliness | Creation lag median 0 days, p90 48; 67 works with a new version in 90 days | GR §2.2 | V |
| E67 | Publication rate | ≈ 55 Mémorial A acts and ≈ 55 consolidation versions per month | SS verified facts | V |
| E68 | `LegalResourceImpact` | 384 impacts (379 modifie, 5 rectifie), 2023-07-27 → 2026-08-03 | GR §2.7 (`q/i3`) | V |
| E69 | PROV log | Stops at 2022-02-07 | SJ verified facts | V |

## E.6 Sizing

| # | Fact | Value | Source | Status |
|---|---|---|---|---|
| E70 | Tier 1 corpus | ≈ 24.2k docs; ≈ 280 MB clean text (205–375); ≈ 70M tokens; ≈ 400k chunks; DB 2.3–3 GB with halfvec; dump 1.5–2 GB; crawl ≈ 7 h at a bare 1 req/s. Under this plan's limits (≥ 1.2 s spacing, ≤ 2,000 requests/h, 4 h nightly window) the ≈ 25k GETs take ≈ 12.5 h of fetching ≈ 4 nights (PLAN §3.3) | SS §4, §6; PLAN §3.3 | E |
| E71 | Article length | Mean 623, median 331 chars over 4,363 sampled articles | SS report §3 | V (sample) |
| E72 | Size probes | Consolidation HTML mean 157 KB / median 76 KB; Code civil XML 5,413,811 B; Code du travail XML 4,128,058 B | SS/CF verified facts | V |
| E73 | `halfvec(1024)` size | 2,052 B per value | DB verified facts | V |
| E74 | Lexical-only DB / dump; DB with vectors | ≈ 0.9–1.3 GB / ≈ 0.2–0.5 GB; ≈ 2.5–3.1 GB. Recomputed in the revision: unit text and chunk body are two in-row copies, and TOAST compresses only values above ≈ 2 kB (median article 331 chars, E71) | Derived from E70–E73 (this plan §4.2) | E |

## E.7 Database and retrieval

| # | Fact | Value | Source | Status |
|---|---|---|---|---|
| E75 | pgvector image | `pgvector/pgvector:0.8.7-pg18-trixie@sha256:9d9c930220cb9bf2f956d10a8f909cf9973d9672ca0278aef2c1e6facccad2e0` (159 MB); PGDATA `/var/lib/postgresql/18/docker`, VOLUME `/var/lib/postgresql` | DB verified facts | V |
| E76 | pgvector HNSW limits | vector 2,000 / halfvec 4,000 / bit 64,000 dims; `binary_quantize` + `bit_hamming_ops` + re-rank pattern; iterative scans since 0.8.0 | J7; DB verified facts | V |
| E77 | Partial HNSW index used with post-filters | `EXPLAIN` shows `Index Scan using chunk_emb_hnsw_live … Filter: (doc_type = 'CODE')` | DB verified facts | V |
| E78 | Prototype ranking | Native `ts_rank_cd` + dense: target at rank 6; BM25 + dense: rank 1; cross-encoder rerank of the native list: rank 1 (363 articles, 3 questions; not statistically meaningful) | DB verified facts | V (anecdotal) |
| E79 | CPU costs | Query embedding 53 ms (e5-large); bge-reranker-v2-m3-int8 on 40 candidates 10.6–16.9 s (4 vCPU) | DB verified facts | V |
| E80 | ParadeDB | 0.26.2-pg18 (354 MB), pg_search AGPL-3.0, French stemmer; ships pgvector 0.8.6 | DB verified facts | V |
| E81 | pg_textsearch | PostgreSQL License, BM25 over PG configs; latest tag v1.5.1 | DB; GR verified facts | V (tag) / U (build) |
| E82 | Alternative engines | Qdrant v1.19.2 (full text is filtering only); OpenSearch 3.9.0 (≈ 1.1 GB); Elasticsearch 9.5.5 (AGPL/SSPL/ELv2); sqlite-vec pre-v1; DuckDB VSS experimental | DB verified facts | V |
| E83 | Accented stopwords leak after unaccent | Prototype output; fixed with `french_unaccent.stop` | DB verified facts; P4 | V |

## E.8 Embeddings

| # | Fact | Value | Source | Status |
|---|---|---|---|---|
| E84 | Mistral Embed | "Premier" (API-only), v23.12; model id `mistral-embed-2312`; 8k context; $0.1/M tokens; `/v1/embeddings`; released 2023-12-11; 1024-d; unit-norm vectors; per-request batch and token limits not stated | J6 (docs.mistral.ai) | V (limits U) |
| E85 | No open-weights Mistral embedder on HF | `huggingface.co/api/models?author=mistralai&search=embed` returns nothing | User verification 2026-10-16 (task brief) | V |
| E86 | Option B (embeddings) | Withdrawn 2026-10-17 (user decision: no bundled LLM runtime, one embedding provider); the dated evidence stays in `docs/research/` | — | — |
| E87 | Option C (embeddings) | Withdrawn 2026-10-17 (user decision: no bundled LLM runtime, one embedding provider); the dated evidence stays in `docs/research/` | — | — |
| E88 | TEI | 1.9.4 (`cpu-1.9.4`, `86-1.9.4`, `89-1.9.4`, `1.9.4`, `hopper-1.9.4`); GPU images are per architecture (README: Ampere 8.6 → `86-1.9`, Ada → `89-1.9`, Hopper → `hopper-1.9`, Blackwell → `100-1.9`/`120-1.9` experimental); local `--model-id`; `--port`; `/rerank`; `--max-client-batch-size` default **32**; `--auto-truncate` default true; "OpenAI-compatible endpoints" (exact path U) | DB verified facts; J8; `out/tei_readme.md` l.190–223, 345–352; RV (manifest HEAD 200 for 86/89/hopper/100-1.9.4; v1.9.4 `router/src/main.rs` default 32) | V / U (path) |
| E89 | Reranker | `BAAI/bge-reranker-v2-m3` @ `953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e`, Apache-2.0, `model.safetensors` | J2 | V |

## E.9 MCP, UIs, LLM serving, images

| # | Fact | Value | Source | Status |
|---|---|---|---|---|
| E90 | MCP SDK | `mcp` 2.3.0 (2026-10-02, MIT); `MCPServer`; serves handshake versions 2024-11-05 … 2025-11-25 and modern 2026-07-28; `run(transport="streamable-http", …, stateless_http=True, json_response=True)` | AG verified facts; J3 | V |
| E91 | mcp 2.x client | `mcp.client.Client`; `streamable_http_client(url, *, http_client: httpx2.AsyncClient \| None …)`; the 1.x name `streamablehttp_client` is absent; requires httpx2, opentelemetry-api (API only) | J4 (wheel read without execution) | V |
| E92 | Open WebUI 0.12.3 | Pins `mcp==1.27.2`; native MCP over Streamable HTTP only, admin-only; `TOOL_SERVER_CONNECTIONS` (type mcp, `info.id`, `config.enable`); `OFFLINE_MODE` disables the version check but does not block egress; `ENABLE_PERSISTENT_CONFIG` default True; branding clause above 50 users per 30 days | AG verified facts; GR | V |
| E93 | Streamlit | 1.65.0 (2026-10-02), 1.66.0 (2026-10-14); Apache-2.0; Python ≥ 3.11; config keys `browser.gatherUsageStats`, `server.headless`, `server.fileWatcherType`, `client.toolbarMode`, `client.showErrorDetails`, `server.enableXsrfProtection`, `server.enableStaticServing`, `theme.font`, `global.developmentMode`; metrics only when gatherUsageStats is true; the first-run e-mail prompt is suppressed by headless. **With `server.headless=true`, `bootstrap._print_url` calls `net_util.get_external_ip()` (GET `http://checkip.amazonaws.com`, then https) unless `logger.hideWelcomeMessage=true` or `browser.serverAddress` / a non-wildcard `server.address` is set** | J3, J5; P10 (wheel read); RV (sentinel test: 1 lookup without, 0 with `hideWelcomeMessage`) | V |
| E94 | Streamlit `StaticConnection` | Fetches `data.streamlit.io/static.json` in static-app mode; trigger not established | J5 | U → HAR gate |
| E95 | openai (Python) | 3.32.0 latest (2026-10-16), Apache-2.0; 3.25–3.31 released 10-06…10-14; **3.24.0 released 2026-10-02T15:55Z** (newest ≥ 14 days old → pin), 3.23.0 2026-10-01 | J3; P10 (PyPI JSON) | V |
| E96 | Local LLM server (former `cpu`/`gpu24` runtime) | Withdrawn 2026-10-17 (user decision: no bundled LLM runtime, one embedding provider); the dated evidence stays in `docs/research/` | AG verified facts | — |
| E97 | Local LLM server (former `gpu48` runtime) | Withdrawn 2026-10-17 (user decision: no bundled LLM runtime, one embedding provider); the dated evidence stays in `docs/research/` | AG verified facts | — |
| E98 | Local LLM models | Withdrawn 2026-10-17 (user decision: no bundled LLM runtime, one embedding provider); the dated evidence stays in `docs/research/`; the chat model is now the user's Mistral deployment (§9.1) | AG verified facts; J2 | — |
| E99 | Image digests (AG) | open-webui `a537bbe1…`, TEI cpu `2538ea1c…`, paradedb `c552094b…`, registry `ddf75434…` (the LLM-runtime and TEI GPU digests were dropped with those images, 2026-10-17) | `research/airgap-llm-mcp-stack/digests.txt` | V |
| E100 | Image digests (re-verified) | caddy 2.11.7-alpine `d8542f48…`, python 3.13.16-slim-trixie `70729b46…`, coredns 1.14.7 `7efd3c63…`, prometheus v3.15.0-distroless `b2a413d5…`; the `postgres:18.6` digest `74935e72…` belongs to tag `18.6`, not `18.6-trixie` (not used) | J1 | V |
| E101 | Image sizes (amd64 compressed) | Open WebUI 1.62 GB; TEI cpu 0.24 GB; pgvector 0.17 GB; ParadeDB 0.37 GB | AG verified facts | V |
| E102 | Supply-chain tools | syft v1.54.1, grype v0.120.1, cosign v3.1.3, MCP Inspector 2.11.0, LibreChat v0.8.8 (MIT), FastMCP 4.1.0 (Apache-2.0, `JWTVerifier`) | AG verified facts | V |
| E103 | Parser oracle | `SFHAJJI/lex-articles` parquet 61,644,220 B, CC BY 4.0, 1,334 works / 96,793 anchors, 2026-08-15 | PA verified facts | V |
| E104 | psycopg 3.3.6 / lxml 6.1.3 / prometheus-client 0.26.0 licences | LGPL-3.0-only / BSD-3-Clause / Apache-2.0 AND BSD-2-Clause | P7 (PyPI JSON) | V |

## E.10 Plan-writer re-checks (2026-10-16)

| # | Check | Result | File |
|---|---|---|---|
| P1 | Q12 union boundary sweep (2026-10-09..12-15) | 261 rows (leg: da 8, dea 9, dei 17, dnl 6), 22,452 B, 0.76 s | `plan/verify/w/c10x.rq/.csv` |
| P2 | Q06 basedOn/cites/rectifies/transposes | 169,243 rows (86,315 / 77,981 / 1,593 / 3,354), 32,998,420 B, 3.64 s | `plan/verify/w/relother.*` |
| P3 | Q07 French titles of `/eli/etat/leg/` acts | 80,906 rows = distinct acts, 18,611,429 B, 2.94 s | `plan/verify/w/titles.*` |
| P4 | Appendix B DDL on `pgvector:0.8.7-pg18-trixie` (PG 18.6) | Applies cleanly; fixtures; `serving_at` time switch; EXCLUDE rejects overlap; invariants 0; lexical/exact/dense candidates; conditional HNSW; zstd `pg_dump` → `pg_restore` round trip OK | `plan/verify/w/fixture.sql`, `vec.sql` |
| P5 | Appendix C compose documents | `docker compose config -q` passes (v5.6.0) for runtime, runtime + dev / audit / enclave overlays, and build; per-profile service sets checked | `plan/verify/w/ctest/` |
| P6 | Appendix D input schemas | Valid draft 2020-12; no top-level combinators; enum rejection works | `plan/verify/plan-checks.txt` |
| P7 | PyPI licence check | psycopg 3.3.6 LGPL-3.0-only; lxml 6.1.3 BSD-3-Clause; prometheus-client 0.26.0 Apache-2.0 AND BSD-2-Clause | `plan/verify/plan-checks.txt` |
| P8 | Offline recount on the frozen snapshot | leg acts 80,918 (not-yet-in-force 14; implicit 48); RC 21,656; LOI with "naturalisation" in the title 951 (918 strict rule) → metadata scope ≈ 58,311 before review | `plan/verify/plan-checks.txt` |
| P9 | Q15 with the Constitution type added (live, one request) | 53 rows in 0.63 s: RECUEIL 37, CODE_RECUEIL 8, CODE 7, Constitution 1 (`constitution/1868/10/17/n1/consolide/20260829`); the C13 text without it gave 52 | `plan/verify/w/q15_const.rq/.csv` |
| P10 | Revision re-checks of Appendix C and its inputs | The compose documents of that revision passed `docker compose config -q` (v5.6.0) per profile combination (re-validated 2026-10-17 after the simplification: four documents, no LLM profiles); a missing `LGX_TESTUI_BCRYPT` fails interpolation; the Caddyfile passes `caddy validate` (2.11.7) with a bcrypt hash and fails with an empty one; ports of a container only on an `internal` network are not published (Docker 29.8.2); Streamlit 1.65.0 wheel `_print_url` read; PyPI dates of openai/streamlit | `plan/revise/ctest/`; this table |
| P11 | Appendix B re-applied after the revision (pgvector 0.8.7-pg18) | DDL applies; P4 fixtures unchanged; `search_candidates` filter tests: default window, `p_include_future`, `p_act_ids`, `doc_types` on the exact branch, act filter on the cited branch, ended windows never returned; `text_md = text_plain` rejected by CHECK | `plan/verify/w/filters_p11.sql/.out`, `fixture_p11.out` |

## E.11 Unverified items: build-time check and fallback

| # | Item | Verify in | Fallback |
|---|---|---|---|
| U1 | Mistral API per-request batch and token limits; API host `https://api.mistral.ai` | M3 (sample run) | Configurable batch/rate; backoff on 429; resume from cache |
| U2 | Mistral on-prem: same API as SaaS (user statement 2026-10-17), the embedding model id it reports, quantisation effect on the probe cosine | When the user provides the endpoint (OD2) | Startup probe → lexical with the reason; `probe_min_cosine` recalibrated (documented) |
| U3 | Mistral on-prem chat endpoint tool calling | M1b/M5 with the user's endpoint | Streamlit retrieve-first mode |
| U4 | Withdrawn 2026-10-17 (no bundled LLM runtime) | — | — |
| U5 | Withdrawn 2026-10-17 (no bundled LLM runtime) | — | — |
| U6 | Tool calling of the user's Mistral chat model through the OpenAI-compatible route | M1b/M5 with the user's endpoint | Retrieve-first mode |
| U7 | mcp 2.x bearer-auth hook; MCP tool annotation names in 2026-07-28 | M0 | ASGI middleware or FastMCP 4.1.0 `JWTVerifier`; annotations are advisory |
| U8 | Exact call signatures of the mcp 2.3.0 `Client` (`list_tools`, `call_tool`) | M0 | Use `ClientSession` from the same SDK |
| U9 | Open WebUI `ENABLE_PERSISTENT_CONFIG=false` behaviour; header-template field names; admin provisioning API; data path | M5 | Provisioning job; manual admin checklist |
| U10 | Withdrawn 2026-10-17 (no bundled LLM runtime) | — | — |
| U11 | pg_textsearch v1.5.1 build in `lgx-db` | M2 bake-off | Native FTS + reranker |
| U12 | `docker save`/`load` keeping repository digests (classic vs containerd store) | M5 | Image-ID verification + local tags; `registry` profile |
| U13 | OpenSSL Ed25519 `-rawin` on target OSes; cosign v3 offline flags | M5 | OpenSSL ≥ 3.0 prerequisite; sha256 + gpg detached signature |
| U14 | CoreDNS template syntax; Postgres minimal `cap_add` (the Caddyfile, incl. `basic_auth` and the global options, passed `caddy validate` in P10) | M5 | Adjust config; documented exceptions in SECURITY.md |
| U15 | Withdrawn 2026-10-17 (one embedding provider, the Mistral API) | — | — |
| U16 | Streamlit `StaticConnection` fetch and bundled fonts; `/_stcore/health` endpoint | M1b HAR test | Config change; patched static asset; TCP health check |
| U17 | HTML instruction-pattern heuristic for modifying articles | M2 | Treat all articles of HTML amending acts as autonomous, with `rank_prior` 0.8 |
| U18 | Q-MANIF at 500 IRIs per POST (tested with 5) | M1 | Batches of 100 |
| U19 | Cause of multi-valued `dateEntryInForce` | M2 | `dei_min` + flag (no behaviour depends on the cause) |
| U20 | Scope of Art. 10, 8° and the database right; licenceSCL terms; `legalValue` semantics | SCL letter, counsel (M0 → M5) | CC BY only; internal use; `legalValue` not displayed |
| U21 | Largest XML file ≈ 31 MB (reported by prior art) | M2 full dry-run statistics | 64 MB cap + PDF allowlist |
| U22 | `age` version for backup encryption; offline grype DB import; host `.deb/.rpm` set | M5 | `openssl enc -aes-256-gcm`; build-side scan only; operator-installed packages |
