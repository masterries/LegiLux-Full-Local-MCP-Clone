# scope-sizing

## Recommendations
- Define 'current' per consolidation as the version where dateApplicability <= today < dateEndApplicability (or no end date), not as 'open-ended'. Store that version plus at most the next future version (34 documents). Keep metadata only for the other 3,505 closed versions. This cuts tens of GB of version history down to about 0.4 GB for the big codes.
- Tier 1 corpus is about 24.2k documents: (A) 1,213 current consolidations plus 34 future versions, including the Constitution, 7 codes, 9 code-recueils and 38 recueils; (B) about 20.3k in-force Mémorial A acts of core normative types without a consolidation, using the JO text as current text; (C) about 2.6k amending acts that are not absorbed into a consolidation. Expect about 280 MB of clean text, about 70M tokens and about 400k article-level chunks.
- Use Akoma Ntoso consolidation XML (CSD13 with scl: extensions) as the primary source for consolidations, because it has <article> elements for article chunking. Use HTML (CC-BY) for JO acts. Use pdftotext for the 32 PDF-only current consolidations, including Code de la sécurité sociale, route and environnement. Never use the stale jolux:Article graph.
- Keep compact metadata for all 151,254 acts and all 4,719 consolidation versions, plus the modifies, repeals, cites, transposes and rectifies relations and the vocabularies (about 100-150 MB). With this the LLM can answer 'repealed by X on date Y' for 16,320 repealed acts without storing their text.
- Store embeddings as pgvector halfvec(1024) with HNSW. For about 400k chunks that is about 0.8 GB of vectors plus about 1 GB of index, and the whole Postgres database is about 2.3-3 GB (about 4-4.5 GB with float32). Ship a pg_dump of about 1.5-2 GB in the airgapped bundle.
- Crawl politely from https://legilux.public.lu/filestore/eli/etat/leg/... (allowed by its robots.txt) at 1 request per second or slower: about 7 hours initially. Never fetch /eli/etat/adm/, /reg_ue/, /dir_ue/ or *.docx, and do not fetch data.legilux.public.lu/eli or /filestore (disallowed there). Use SPARQL for all metadata, with year-chunked queries under 10k rows each.
- Incremental sync: poll the ELI Atom feed (https://legilux.public.lu/pillar4/eli-update-feed.atom, about 2 months deep) daily, and diff consolidations through SPARQL by publicationDate. That is under 50 requests per day, against roughly 55 new Mémorial A acts and 55 new consolidation versions per month.
- Licensing: ingest only manifestations whose jolux:license is CC BY 4.0, and store the licence and ELI with every chunk. Ship the full CC BY 4.0 text and an attribution and modification notice in the image. Print 'Source: Legilux / SCL, CC BY 4.0, ELI, version date' in every MCP answer, together with disclaimers that consolidations are informational and that SCL gives no legal advice.
- Exclude from text storage, for minimisation and GDPR reasons: Mémorial B, communal regulations, naturalisation laws, the person vocabulary, draft PDFs, PDF/PDF-A/DOCX duplicates, repealed acts' texts and absorbed amending acts.
- Tier 2 can be added later without schema change: metadata of pending drafts (697), treaty process metadata (1,184), the EU transposition table (3,354 links), administrative-court decisions (421, text from ja.public.lu), historic in-force types (about 7.8k with HTML) and OCR of about 1.5k PDF-only in-force core acts.

## Risks / open questions
- The text of the 'Licence de réutilisation SCL' (licenceSCL) could not be found. It covers 36,734 AKN XML, 80,755 PDF and 4,107 HTML Mémorial A files dated up to 2016, plus 27,352 Mémorial B files. Should we confirm with SCL (info@legilux.public.lu), or restrict ingestion to CC-BY manifestations, which cover 46,190 of the Mémorial A acts with HTML?
- There is a robots conflict: the official FAQ documents content negotiation at data.legilux.public.lu/eli/..., but data.legilux.public.lu/robots.txt disallows /eli/* and /filestore/*. The plan relies on legilux.public.lu/filestore, which is allowed and is the SPA's manifestation_prefix. Asking SCL for an explicit OK or a bulk export would remove the ambiguity.
- The size estimates for in-force JO acts without a consolidation (about 187 MB of text, range 130-260 MB) come from 90 random probes and a 0.28 text ratio. Treaty-approval laws and annexes create a heavy tail, so the real number could differ by about 40%.
- 55 current or future consolidation versions have SPARQL metadata but no files (for example loi/1967/12/04/n1/consolide/20260201, a 404 at the predictable path). For these the clone must fall back to the latest version that has files and flag the gap. It is unknown how quickly files appear after the metadata.
- Recueils and code-recueils (for example code/sante at 23 MB) duplicate member acts that also have individual consolidations. Deduplication rules are still to be designed; the expected saving is about 15-25% of the consolidation text.
- The jolux:modifies links and the title regex used to detect 'pure amending acts' are heuristics. Some amending acts also contain autonomous provisions, so dropping 2,859 'absorbed' amending acts to metadata only risks losing standalone articles. A safer variant keeps their text but marks it low-priority (+ about 20 MB).
- In-force status is unreliable for old acts (for example 1851 budget accounts marked in force). Tier 1 still includes about 20k acts dating from 1790, and retrieval should down-weight very old texts unless they are cited by current law.
- About 1.5k in-force core acts are PDF-only (mostly 1840-2000) inside JO issue PDFs. Whether those PDFs have a text layer or need OCR is unverified.
- The 4 chars/token ratio for French legal text and the HNSW index overhead are assumptions and should be measured on the chosen embedding model (for example bge-m3 or multilingual-e5).
- UNVERIFIED: Luxembourg copyright law may exclude official acts from copyright protection (loi du 18 avril 2001, art. 8). If so, the legal risk is lower, but CC-BY attribution should still be honoured.
- UNVERIFIED nuance: the EU Publications Office page says consolidated texts have no legal value, and the WebFetch summary also said that this excludes codes such as the Code civil and Code pénal. The exact wording should be checked before writing disclaimers.

## Verified facts
- The SPARQL store holds 13,307,204 triples; it contains 151,250 jolux:Act, 4,719 jolux:Consolidation and 58,673 jolux:Article resources.  
  _evidence:_ GET https://data.legilux.public.lu/sparqlendpoint with query SELECT (COUNT(*) AS ?triples) WHERE {?s ?p ?o} returned 13307204. SELECT ?c (COUNT(?s) AS ?n) WHERE {?s a ?c} GROUP BY ?c returned jolux#Act 151250, jolux#Consolidation 4719, jolux#Article 58673, jolux#Code 916, jolux#Memorial 72398.
- Act in-force status: 119,980 in force, 31,162 no longer in force, 50 no longer in force (implicit), 58 not yet in force. Mémorial A (eli/etat/leg) has 80,918 acts (64,536 in force); Mémorial B (eli/etat/adm) has 70,332 (55,444 in force).  
  _evidence:_ SELECT ?c ?status (COUNT(?s) AS ?n) WHERE { VALUES ?c {jolux:Act ...} ?s a ?c ; jolux:inForceStatus ?status } GROUP BY ?c ?status, plus query q8 with BIND(IF(CONTAINS(STR(?s),"/eli/etat/leg/"),"leg",...)). Output rows: 'leg in-force 64536', 'leg no-longer-in-force 16320', 'adm in-force 55444', 'adm no-longer-in-force 14842'.
- There are 1,416 consolidated works with 4,719 versions in total. 1,214 versions are open-ended. 1,213 works have a version applicable on 2026-10-16. 203 works have only closed versions. 3,505 versions have an end date. 18 works have 34 future versions. Applicability dates range from 1849-03-14 to 2030-09-15.  
  _evidence:_ q1: SELECT (COUNT(DISTINCT ?act) AS ?acts) (COUNT(?c) AS ?versions) (SUM(IF(BOUND(?end),0,1)) AS ?open_versions) (MIN(?d)..) (MAX(?d)..) WHERE { ?c a jolux:Consolidation ; jolux:isMemberOf ?act ; jolux:dateApplicability ?d . OPTIONAL{?c jolux:dateEndApplicability ?end} } returned '1416 4719 1214 1849-03-14 2030-09-15'. Python filtering of all 4,719 rows (out/consolidations.tsv) gave 'with current-as-of-today version 1213 without 203 {works_with_future_version: 18, future_versions: 34}'.
- Codes: 7 CODE works and 9 CODE_RECUEIL works, plus 38 RECUEIL works and the Constitution (38 versions, latest 2026-08-29). Code civil is Work loi/1804/03/21/n1, with versions at /eli/etat/leg/code/civil/YYYYMMDD.  
  _evidence:_ q12 output: 'loi/2006/07/31/n2 CODE 56 2026-07-26 code/travail/...', 'loi/1879/06/18/n1 CODE 36 2025-12-19 code/penal/...', 'loi/1804/03/21/n1 CODE 19 2025-12-26 code/civil/...'. q11 output: 'constitution/1868/10/17/n1 Constitution 38 2026-08-29', 'code/environnement CODE_RECUEIL 199 2026-10-03'.
- The current versions of Code de la sécurité sociale, Code de la route, Code de l'environnement (and its annexes), Code de l'éducation nationale and Code communal are PDF-only. 32 open-ended consolidation versions are PDF-only and 55 have no files at all.  
  _evidence:_ Query q19 (open-ended consolidations with manifestations), then Python: "code/securite_sociale/20260101 ['pdf']", "code/route/20260612 ['pdf']", "code/procedure_penale/20270301 ['']"; 'with html 1127 without html 87'; 'no file 55'. A GET on https://legilux.public.lu/filestore/eli/etat/leg/loi/1967/12/04/n1/consolide/20260201/fr/html/... returned 404.
- Manifestation files are served from https://legilux.public.lu/filestore/... (allowed by the legilux.public.lu robots.txt), whereas the data.legilux.public.lu robots.txt disallows /eli/* and /filestore/*. HEAD returns 403; a range GET returns 206 with the full size.  
  _evidence:_ https://data.legilux.public.lu/robots.txt: 'Disallow: /eli/* Disallow: /filestore/*'. https://legilux.public.lu/api/settings: '"manifestation_prefix": "https://legilux.public.lu"'. curl -r 0-0 on .../loi/2023/06/29/a334/jo/fr/html/eli-etat-leg-loi-2023-06-29-a334-jo-fr-html.html returned 'HTTP/1.1 206', 'Content-Range: bytes 0-0/391493', 'X-Robots-Tag: noindex, nofollow'. curl -I returned 'HTTP/1.1 403 Forbidden'.
- Consolidation 'xml' manifestations are Akoma Ntoso CSD13 with scl: extensions and <article> elements. Measured article counts: Code civil 2,301 articles and 774K chars of body text (5.41 MB XML); Code pénal 699 articles (1.64 MB); Code du travail 1,196 articles and 1.57M chars (4.13 MB); Constitution 132 articles (204 KB).  
  _evidence:_ Downloaded eli-etat-leg-code-civil-20251226-fr-xml.xml etc. from https://legilux.public.lu/filestore/eli/etat/leg/code/civil/20251226/fr/xml/. Root '{http://docs.oasis-open.org/legaldocml/ns/akn/3.0/CSD13}akomaNtoso', nsmap scl=http://www.scl.lu. bin/aknstats.py output: 'code-civil ... bytes= 5413811 bodytext= 774263 articles= 2301 mean_art= 326'.
- Size probes: random current consolidation HTML has mean 157 KB and median 76 KB (n=70). The 30 big codes and recueils total 79.6 MB of HTML (max code/sante 23.1 MB). The 32 PDF-only current consolidations total 374.7 MB (code/environnement_annexes 239.9 MB). JO HTML: LOI mean 54 KB, RGD 28 KB, AGD 16 KB.  
  _evidence:_ 268 range GETs (bin/probe.py, 1.1 s spacing), all 206 responses, results in out/probe_results.tsv. Summary: "('cons_html','rnd') n=70 mean=157KB median=76KB", "('cons_pdfonly','x') n=32 ... sum=374.7MB", "('jo_html','LOI') n=25 mean=54KB".
- The Code de la sécurité sociale PDF (2026-01-01) is 5,059,985 bytes and 838 pages, has a text layer, and pdftotext yields about 3.0M characters.  
  _evidence:_ Downloaded https://legilux.public.lu/filestore/eli/etat/leg/code/securite_sociale/20260101/fr/pdf/eli-etat-leg-code-securite_sociale-20260101-fr-pdf.pdf. pdfinfo: 'Pages: 838', 'Producer: Acrobat Distiller 22.0'. textstats: '5059985 3006714 0.594 2521'.
- Repeal metadata: 16,368 Mémorial A acts are no longer in force; 16,320 carry jolux:dateNoLongerInForce and 12,065 are the target of a jolux:repeals link from the repealing act.  
  _evidence:_ Year-chunked pulls of all 80,922 leg acts (out/leg_acts.tsv) and 47,052 modifies/repeals pairs (out/mod_rep.tsv). bin/tier.py output: 'repealed leg acts 16368 with date 16320 with repealer link 12065'.
- Of 5,773 in-force pure amending acts in Mémorial A, 2,888 are fully absorbed into a later consolidation or target a repealed act, and so can be kept as metadata only. 2,767 target unconsolidated acts and 118 are newer than the target's latest consolidation.  
  _evidence:_ Python classification over out/leg_acts.tsv, out/mod_rep.tsv and out/consolidations.tsv: "{'keep_target_unconsolidated': 2767, 'droppable_fully_absorbed_or_target_repealed': 2888, 'keep_newer_than_consolidation': 118} 5773".
- Licence statements: the Casemates FAQ states CC BY 4.0 for texts and metadata. The per-manifestation jolux:license is CC-BY on 129,358 leg, 13,674 consolidation and 207,304 adm manifestations, and 'Licence de réutilisation SCL' on 121,754 leg (AKN 36,734 dated 1617–2016, PDF 80,755, HTML 4,107) and 27,352 adm manifestations.  
  _evidence:_ https://legilux.public.lu/api/assets/faq/questions/12-introduction.html: 'Les textes et les métadonnées sont mis à disposition selon la licence d’utilisation http://creativecommons.org/licenses/by/4.0/ qui autorise leur réutilisation pour des utilisations commerciales ou non.' SPARQL: SELECT ?ns ?lic (COUNT(?m) AS ?n) WHERE { ?m a jolux:Manifestation . OPTIONAL{?m jolux:license ?lic} ...}. Label of http://data.legilux.public.lu/resource/authority/license/licenceSCL: skos:prefLabel 'Licence de réutilisation SCL'.
- The general site terms restrict reuse unless otherwise indicated; the editorial/use-conditions page is only a privacy and cookie policy.  
  _evidence:_ https://legilux.public.lu/api/assets/faq/questions/14-utilisation.html: 'Sauf indication contraire, l’usager est autorisé à consulter... les documents ne peuvent être utilisés qu'à titre personnel... ne peuvent être diffusés en dehors de ce site.' https://legilux.public.lu/api/assets/editable-items/editable-pages/use-conditions.html begins 'Protection des données et respect de la vie privée'.
- An ELI Pillar-4 act list and an update feed exist. The sitemaps list 151,254 act URIs (no consolidations). The Atom feed holds 1,439 entries covering 2026-08-17 to 2026-10-15.  
  _evidence:_ https://legilux.public.lu/pillar4/sitemap_index.xml points to http://data.legilux.public.lu/pillar4/sitemap1..4.xml (7.3 MB each; 'urls 151254', 'consolide 0'). https://legilux.public.lu/pillar4/eli-update-feed.atom: 'entries 1439 oldest 2026-08-17T00:09:41Z newest 2026-10-15T16:59:40Z', Counter({'adm': 836, 'leg': 603}).
- Publication rate over the last 12 months: about 55 Mémorial A acts per month, about 450 Mémorial B acts per month, and about 55 new consolidation versions per month.  
  _evidence:_ SELECT ?ym (SUM(IF(CONTAINS(STR(?s),"/eli/etat/leg/"),1,0)) AS ?memA) ... WHERE { ?s a jolux:Act ; jolux:publicationDate ?d . FILTER(?d >= "2025-10-01"^^xsd:date) ...} gave e.g. '2026-07 84 657', '2026-09 35 405'. The consolidation query gave '2026-02 115', '2026-09 41'.
- The legacy jolux:Article resources are article-version nodes for codes only and end at 2021-12-26, so they are stale.  
  _evidence:_ SELECT ?code (COUNT(DISTINCT ?a) ...) (MAX(?d) AS ?maxd) WHERE { ?a a jolux:Article ; jolux:dateApplicability ?d . BIND(STRBEFORE(STRAFTER(STR(?a),"/eli/etat/leg/"),"/art") AS ?code) } GROUP BY ?code returned 'code/civil 7091 2016-09-01 2021-12-26', 'code/travail 1126 2020-11-01 2020-11-01'.
- Drafts: 7,791 InitialDraft records, of which 697 are 'en-cours' (PL 258, PR 339, PPL 92, PPC 8). There are 27,710 DraftRelatedDocument records, which are PDFs.  
  _evidence:_ SELECT ?type ?st (COUNT(DISTINCT ?s) AS ?n) ... WHERE { ?s a jolux:InitialDraft ; jolux:statusDraft ?st ; jolux:eventType ?type ...} returned 'type-projet/PL draft-status/en-cours 258', 'type-projet/PR draft-status/en-cours 339'. Class counts: DraftRelatedDocument 27710.
- Judicial decisions in Legilux are 421 administrative-court decisions dated 2025-01-14 to 2026-10-08, with ECLI and owl:sameAs links to ja.public.lu PDFs. The 208 Cour constitutionnelle judgments (ACC) are published in Mémorial A.  
  _evidence:_ SELECT ?p (COUNT(*) ..) WHERE { ?s a jolux:JudicialDecision ; ?p ?o } returned 'jolux#ecli 421 ... ECLI:LU:CADM:2026:53153' and 'owl#sameAs ... https://ja.public.lu/50001-55000/53153C.pdf'. Date range query returned '2025-01-14 2026-10-08 421'.
- The Virtuoso endpoint refuses sorted pagination beyond 10,000 rows.  
  _evidence:_ Query with ORDER BY ?jo LIMIT 10000 OFFSET 10000 returned 'Virtuoso 22023 Error SR353: Sorted TOP clause specifies more then 20000 rows to sort. Only 10000 are allowed.'
- Mémorial C is absent from the SPARQL store: only the A and B memorial serials exist.  
  _evidence:_ SELECT ?ser (COUNT(DISTINCT ?m) AS ?issues) ... WHERE { ?m a jolux:Memorial ... } returned 'A 23770 1617-10-20 2026-10-15' and 'B 48628 1854-05-02 2026-10-15', with no other series.

## Report

# Track: Scope definition and corpus sizing for a "current and relevant" Legilux clone

Snapshot taken 2026-10-16, about 18:20 to 18:55 UTC. I queried the SPARQL endpoint (`https://data.legilux.public.lu/sparqlendpoint`) live, at 1 request per ~1.2 s with a descriptive User-Agent. I fetched files from `https://legilux.public.lu/filestore/...`, which is allowed by robots.txt: 268 one-byte range probes plus 23 full downloads, spaced 1.1 to 1.5 s apart.

All raw outputs and scripts are in `<research-scratch>/scope-sizing/`:
- `out/*.tsv`: query results and the full metadata pulls
- `bin/*.py`: the SPARQL helper and the analysis scripts
- `dl/`: downloaded samples

How claims are marked:
- **[V]**: verified live in this run.
- **[E]**: an estimate or extrapolation from verified samples.
- **[U]**: unverified, from memory or inference.

---
## 1. Key facts that drive the scope

### 1.1 The store is small and live [V]
- `SELECT (COUNT(*) AS ?triples) WHERE {?s ?p ?o}` returns **13,307,204 triples**.
- Acts are published up to 2026-10-15.
- Consolidations have `dateApplicability` values up to **2030-09-15**, so future versions exist.

### 1.2 What counts as an "act" [V]
There are **151,250 `jolux:Act`** resources.

| Namespace | Total | In force | No longer in force | No longer in force (implicit) | Not yet in force |
|---|---|---|---|---|---|
| Mémorial A (`/eli/etat/leg/`) | 80,918 | 64,536 | 16,320 | 48 | 14 |
| Mémorial B (`/eli/etat/adm/`) | 70,332 | 55,444 | 14,842 | 2 | 44 |

The ELI Pillar-4 sitemaps (`https://data.legilux.public.lu/pillar4/sitemap1..4.xml`) list **151,254 act URIs**. That is an exhaustive act list, and it contains no consolidations.

### 1.3 "In force" is a weak flag [V]
`jolux:inForceStatus=in-force` is set on things that are not useful as current law:
- 19th-century budget accounts.
- 5,314 old "arrêtés" (A) dating from 1796.
- 21,612 communal regulations.
- **968 naturalisation laws**, which concern named individuals.
- About 5.7k pure amending acts.

So "current and relevant" has to be defined by our own rules, not by this flag alone.

### 1.4 Consolidation is the backbone [V]
There are **4,719 `jolux:Consolidation` versions across 1,416 consolidated works** (query q1). The independent "Lex" reuse on data.public.lu reports 1,409 works and 4,705 versions, which roughly matches.

For each work, I took the version whose applicability window covers 2026-10-16 (`dateApplicability <= today < dateEndApplicability`, or no end date):
- **1,213 works have a version applicable today.**
- **203 works have only closed versions**, because the base act was repealed or expired.
- **18 works have 34 future versions.** For example, `code/procedure_penale/20270301` has no files yet, so the version currently applicable is the previous one.
- **3,505 of the 4,719 versions are closed (74%).**

Note: "open-ended" (no end date) is not the same as "current". 17 current versions have an end date because a future version is already scheduled.

Formats of the 1,214 open-ended versions [V]:
- **1,127** have HTML, AKN XML, DOCX and PDF.
- **32** are PDF-only. This includes 6 CODE_RECUEIL: **Code de la sécurité sociale, Code de la route, Code de l'environnement (and its annexes), Code de l'éducation nationale, Code communal**, plus 16 recueils.
- **55** are metadata-only, with no files yet. For example, `loi/1967/12/04/n1/consolide/20260201` returns 404 at the predictable filestore path.

### 1.5 Codes [V]

**CODE type (7 works).** The Work is the base act, and the version URIs are `/eli/etat/leg/code/<name>/<yyyymmdd>`. Version counts in parentheses:
- civil = `loi/1804/03/21/n1` (19)
- pénal = `loi/1879/06/18/n1` (36)
- procédure pénale = `loi/1808/11/17/n1` (37)
- procédure civile = `rgd/1998/08/03/n4` (17)
- travail = `loi/2006/07/31/n2` (56)
- commerce = `loi/1807/09/15/n2` (7)
- consommation = `loi/2011/04/08/n2` (18)

**CODE_RECUEIL type (9 works, 724 versions).** Version counts in parentheses:
- environnement (199)
- santé (123)
- éducation nationale (118)
- fonction publique (84)
- environnement_annexes (79)
- communal (63)
- route (46)
- sécurité sociale, Work `loi/1925/12/17/n1`
- coopération judiciaire

**Also:** **38 RECUEIL** works (754 versions), and the **Constitution** (`constitution/1868/10/17/n1`, 38 versions, current one dated 2026-08-29).

### 1.6 Amendment and repeal graph [V]
- `jolux:modifies` has 27,420 links, `jolux:repeals` 19,632, `jolux:cites` 77,981, `jolux:transposes` 3,354 and `jolux:rectifies` 1,593.
- 16,368 Mémorial A acts are no longer in force:
  - **16,320 have `dateNoLongerInForce`**.
  - **12,065 (74%) have an incoming `jolux:repeals` link** naming the repealing act.

### 1.7 Legacy article graph is stale [V]
The **58,673 `jolux:Article`** resources are article-version nodes for codes only, for example `code/civil/art_127/20150901`. They stop at **2021-12-26**, with code/civil having 7,091 article versions.

They must **not** be used. Article chunking has to come from the current consolidation XML.

### 1.8 File access [V]
- `data.legilux.public.lu/robots.txt` says `Disallow: /eli/*` and `Disallow: /filestore/*`.
- The SPA config (`https://legilux.public.lu/api/settings`) declares `"manifestation_prefix": "https://legilux.public.lu"`. Files are served at `https://legilux.public.lu/filestore/eli/etat/leg/...`, which the legilux.public.lu robots.txt does not disallow. `/eli/etat/adm/`, `/reg_ue/`, `/dir_ue/` and `*.docx` are disallowed there.
- HEAD returns 403. A `Range: bytes=0-0` GET returns 206 with `Content-Range` giving the full size.
- Responses carry `X-Robots-Tag: noindex, nofollow`, which governs indexing, not fetching.

### 1.9 Change rate [V]

| Month (2025-10 to 2026-10) | Range | Approx. average |
|---|---|---|
| New Mémorial A acts | 14 to 132 | ~55 |
| New Mémorial B acts | 244 to 657 | ~450 |
| New consolidation versions | 13 to 115 | ~55 |

The ELI Atom feed `https://legilux.public.lu/pillar4/eli-update-feed.atom` has 1,439 entries covering 2026-08-17 to 2026-10-15 (603 leg, 836 adm). It carries no consolidation URIs. **Incremental sync must poll at least every ~8 weeks; daily is recommended**, and consolidations must be diffed separately through SPARQL.

---
## 2. Segment inventory (counts verified live)

Below, each `#` row heading is the ELI path segment (`#leg` = `/eli/etat/leg/`, `#adm` = `/eli/etat/adm/`). Mixed-type rows give in-force counts per type, e.g. "in force 7,743 of 9,720".

| Segment | Class / type (`#leg` = `/eli/etat/leg/`, `#adm` = `/eli/etat/adm/`) | Count | Date range | Changes? | Value for legal Q&A | Decision |
|---|---|---|---|---|---|---|
| Constitution | `constitution/1868/10/17/n1` consolidations | 1 work, 38 versions | 1868 to 2026-08-29 | rare | critical | **T1**, current version only |
| Codes | CODE | 7 works, 190 versions | to 2026-09-27 | monthly | critical | **T1**, current version |
| Codes-recueils | CODE_RECUEIL | 9 works, 724 versions (6 current PDF-only) | to 2026-10-11 | monthly | critical (CSS, route, environnement) | **T1**, current version (PDF to text where needed) |
| Recueils | RECUEIL | 38 works, 754 versions | to 2026-09-16 | monthly | high | **T1**, current version, deduplicated against member acts |
| Consolidated LOI/RGD/other | Consolidation | about 1,160 works with a current version (LOI 494, RGD 541, AGD 16, RMIN 14, …) | 1849 to 2030 | weekly | critical | **T1**, current version plus next future version (34) |
| JO laws (LOI) | `#leg` LOI | 9,238 (8,033 in force) | 1790 to 2026 | ~10 per month | high | T1 if in force and not consolidated |
| JO RGD | `#leg` RGD | 15,366 (9,734 in force) | 1919 to 2026 | ~20 per month | high | same as LOI |
| AGD / RMIN / AMIN (Mém. A) | `#leg` | 6,185 / 6,061 / 2,972 | 1852 to 2026 | — | medium–high | same as LOI |
| Cour constitutionnelle | `#leg` ACC | 208 (160 with HTML) | 1998 to 2026 | ~1 per month | high | **T1** |
| Regulators | RILR 532, RCSF 112, RBCL 35 | — | 2007 to 2026 | — | medium | **T1** if in force |
| Old official coordinated texts | TC | 344 (52 in force) | 1930 to 2015 | frozen | low | T2 (only if no ELI consolidation) |
| Rectificatifs | RECT | 1,129 (810 in force), 1,593 `rectifies` links | — | — | medium (errata) | T2, small text plus link |
| Historic types | A 6,503 · ARGD 1,852 · DIV 3,060 · CMIN 1,078 · ORD/ORDR/AR/DEC/REG/REEX/RTDE/RTF/D … | about 11.7k in force | 1617 to 2023 | frozen | low | **T2** |
| Communal regulations | RC | 33,171 (21,656 in `#leg` 1811–2016, 11,515 in `#adm` 1854–2026) | — | daily | low for national Q&A, very large | **T3** (metadata optional) |
| Naturalisation laws | LOI title match | 968 in force | — | — | none, personal data | **Excluded** |
| Mémorial B | `#adm`: PA 42,725 · RC 11,515 · AMIN 9,720 · AGD 3,558 · CMIN 452 … | 70,332 acts, 48,628 issues | 1854 to 2026 | ~450 per month | low (nominations, exams, notices, personal data); `/eli/etat/adm/` disallowed in robots | **T3** text; metadata optional via SPARQL |
| Mémorial C (companies) | absent from SPARQL (only A and B serials) | — | PDF 1996 to 2016-06-01 (OP EU page) | abolished | out of scope (now RESA/LBR) | **Excluded** |
| Parliamentary drafts | InitialDraft | 7,791 (en-cours 697: PL 258, PR 339, PPL 92, PPC 8; publié 6,437; retiré 649) | 1985 to 2026 | weekly | medium ("is a bill pending?") | **T2** metadata (title, status, CHD URL) |
| Draft documents | DraftRelatedDocument | 27,710 (avis CE 4,684; avis organismes 12,478; exposé des motifs 826; commentaire des articles 773…), PDF | — | — | interpretive, heavy | **T3** (optional later for recent laws) |
| Treaties | TreatyProcess | 1,184 (approbationAct 204; firstEntryIntoForceDate 181); 704 PDFs | — | — | medium | **T2** metadata; texts largely in approval laws (T1) |
| EU transposition | EULegalResource 5,461; `transposes` 3,354 (1,955 national ↔ 2,256 EU acts) | — | — | — | medium | **T2** metadata only (`/reg_ue/`, `/dir_ue/` pages disallowed) |
| Administrative court decisions | JudicialDecision JURIS_ADMIN | 421, with ECLI, `owl:sameAs` to `https://ja.public.lu/...pdf` | 2025-01-14 to 2026-10-08 | weekly | high | **T2** (text from ja.public.lu, a separate source) |
| Legacy article graph | jolux:Article | 58,673 | to 2021-12-26 | frozen | stale | **Excluded** |
| Vocabularies | skos | legal-subject 3,170; legal-subject-theme 2,330; legal-institution 515; resource-type 74; … | — | rare | filters, labels | **T1** (tiny); exclude the `person` scheme |

---
## 3. Format and size measurements (sample-based)

**Range probes** (uncompressed byte size, n = 268):

| Group | n | Mean | Median | Max | Sum |
|---|---|---|---|---|---|
| Current consolidation HTML, random | 70 | 157 KB | 76 KB | 1.8 MB | — |
| Current consolidation HTML, 30 big codes and recueils | 30 | — | — | 23.1 MB (code/sante) | 79.6 MB |
| Current consolidation XML, random | 15 | 99 KB | 51 KB | — | — |
| Current consolidation XML, big | 10 | 4.3 MB | — | — | — |
| PDF-only current consolidations | 32 | — | — | 239.9 MB (code/environnement_annexes) | **374.7 MB** |
| JO act HTML, LOI | 25 | 54 KB | 32 KB | 269 KB | — |
| JO act HTML, RGD | 34 | 28 KB | — | — | — |
| JO act HTML, AGD / AMIN / RMIN | — | 15–17 KB | — | — | — |
| JO AKN | — | 7–17 KB | — | — | — |

Other PDF-only sizes: code/environnement 50.2 MB, code/route 35.5 MB, code/securite_sociale 5.06 MB.

**Full downloads.** Text was extracted with lxml or pdftotext (`bin/textstats.py`, `bin/aknstats.py`):
- Consolidation "xml" is **Akoma Ntoso CSD13 with `scl:` extensions** and contains `<article>` elements, so it is ideal for article chunking.
- Clean text is about **27–35% of raw HTML/XML bytes**; for random JO HTML the mean is 0.28.
- The PDF of Code de la sécurité sociale has a text layer (Acrobat Distiller, 838 pages): 3.0 M chars, 1,583 `Art.` line starts. Marginal notes such as "L. 27.7.92" need cleaning.

| Document | Raw | Body text | Articles | Mean / p95 / max chars per article |
|---|---|---|---|---|
| Code civil (2025-12-26) XML | 5.41 MB | 774 K chars | 2,301 | 326 / 833 / 4,057 |
| Code pénal (2025-12-19) XML | 1.64 MB | 383 K | 699 | 531 / 1,459 / 15,654 |
| Code du travail (2026-07-26) XML | 4.13 MB | 1.57 M | 1,196 | 1,273 / 4,030 / 15,585 |
| Constitution (2026-08-29) XML | 204 KB | 46 K | 132 | 337 / 970 / 1,974 |
| Code sécurité sociale (2026-01-01) PDF | 5.06 MB | 3.0 M | — | — |
| Random consolidation (loi/2020/04/03/a230) XML | 62 KB | 20 K | 11 | 1,798 |
| JO RGD 1982 AKN | 9.8 KB | 1.9 K | 2 | — |

Across all 4,363 sampled articles: mean 623 chars, median 331.

---
## 4. Recommended scope (tiers) with sizing

### Tier 1 (must have, about 24.2k documents)

**T1-A: current consolidations [V counts, E sizes]**
- 1,213 works have a version applicable on the run date, plus 34 future versions, for 1,247 documents.
  - Of these, 1,127 have XML/HTML, 32 are PDF-only and need pdftotext, and 55 have metadata but no files. For those 55, fall back to the latest version that has files and flag "newer consolidation dated X announced".
- Raw size:
  - HTML ≈ 79.6 MB (big, measured) + 1,097 × 157 KB ≈ **250 MB**.
  - XML is similar.
  - PDF-only: 375 MB, or 135 MB without the 240 MB environment annexes.
- **Clean text ≈ 75–115 MB** (midpoint about 90 MB): regular consolidations 37–52 MB, big codes and recueils about 24 MB, PDF-only codes 15–40 MB.
- Recueils such as code/sante (23 MB) bundle member acts that are also consolidated individually. Deduplicating by member ELI could save about 15–25% [E].

**T1-B: in-force Mémorial A acts without a consolidation (original JO text = current text) [V counts, E sizes]**
- Core types: LOI, RGD, AGD, RMIN, AMIN, RGC, RILR, RCSF, RBCL, ST, ACC, AGC, DGC, RI, AGDR, CONV, PROT.
- Exclusions: naturalisations, and amending acts already absorbed into a consolidation or whose target is repealed (2,859, kept as metadata only).
- Result: **20,326 acts with HTML** (est. raw 596 MB, text ≈ 167 MB), plus **2,587 amending acts that must be kept** (est. raw 73 MB, text ≈ 20 MB). Of the 2,887 such amending acts in total, 2,767 target an unconsolidated act and 118 are newer than the target's latest consolidation; 2,587 have HTML and 116 are PDF-only.
- 1,448 core acts and 116 amending acts are PDF-only (mostly 1840–2000), moved to T2.
- **T1-B text ≈ 187 MB (range 130–260 MB)** [E]. Treaty-approval laws, which can embed whole treaties, create a heavy tail.
- HTML is CC-BY for 46,190 of the Mémorial A acts with HTML; AKN is mostly "licenceSCL" (see §5), so **HTML should be the primary format for JO acts**.

**T1 metadata (all, small)**
- All 151,254 act records: ELI, type, title (mean 156 chars), dates, status, subjects.
- All 4,719 consolidation-version records (validity windows enable "which version applied on date D?" without storing old texts).
- Relations: modifies, repeals, cites, transposes, rectifies.
- Vocabularies.
- About 100–150 MB in Postgres including indexes [E].
- This lets the LLM say **"repealed by <ELI> on <date>"** for 16,320 repealed acts (date) and 12,065 (named repealing act) without storing their text.

**T1 totals [E]:**
- **≈ 24.2k documents, ≈ 280 MB clean text (range 205–375 MB), ≈ 70 M tokens** at ~4 chars per token for French [U ratio].
- About **400k chunks (±30%)**, chunked at article level, with articles over ~2,000–2,500 chars split and visas/preamble/annex as their own chunks.

### Tier 2 (nice to have)
- Historic in-force types (A, ARGD, DIV, CMIN, IMIN, ORD, ORDR, AR, DEC, REG, …): 7,817 with HTML (≈ 66 MB text) plus 3,891 PDF-only.
- PDF-only in-force core acts (about 1,564): JO issue PDFs; text layer unknown, may need OCR [U].
- RECT (810 in force) and the 52 in-force TC texts.
- Pending drafts metadata (697 en cours; title, status, `parliamentDraftUrl`, `hasResultingLegalResource`).
- Treaty metadata (1,184).
- EU transposition table (3,354 links).
- Administrative-court decisions: 421 metadata records with ECLI; text from ja.public.lu.
- Recent JO publications not yet consolidated: this set is already inside T1-B through the "keep newer than consolidation" rule.

### Tier 3 (excluded) and why
1. **Historical consolidation texts**: 3,505 closed versions.
   - Extrapolating latest-version size times version count for the top codes and recueils alone gives about **37 GB versus 0.4 GB for latest-only** [E]. Examples: code/environnement 199 × 50 MB; code/environnement_annexes 79 × 240 MB.
   - Keep only their metadata.
2. **Redundant manifestations**:
   - Mémorial A: 87,351 PDF, 17,323 PDF-A, 50,294 DOCX.
   - Mémorial B: 91,334 PDF-A.
   - DOCX is also robots-disallowed.
3. **Mémorial B** text: robots disallows `/eli/etat/adm/`; content is mostly nominations, exams and individual decisions (personal data).
4. **Mémorial C**: not in Legilux.
5. **Communal regulations** (33,171): local and very large.
6. **Naturalisation laws** (968) and the `person` vocabulary: GDPR minimisation.
7. **Repealed-act full texts** (16,368), and **absorbed amending acts** (2,859).
8. **Draft documents** (27,710 PDFs).
9. **EU texts** (get them from EUR-Lex if ever needed).
10. **The prov graph** (720k triples) and **the legacy jolux:Article graph**.
11. **RTC** temporary traffic rules (1,447, all expired).

---
## 5. Licensing and terms (airgapped redistribution)

**What the sources say [V]:**
- The Casemates FAQ (`https://legilux.public.lu/api/assets/faq/questions/12-introduction.html`) says: "Les textes et les métadonnées sont mis à disposition selon la licence d'utilisation http://creativecommons.org/licenses/by/4.0/ qui autorise leur réutilisation pour des utilisations commerciales ou non."
- The data.public.lu dataset is listed as "Creative Commons Attribution 4.0".
- The general site terms (`.../14-utilisation.html`) are restrictive: "Sauf indication contraire … les documents ne peuvent être utilisés qu'à titre personnel … ne peuvent être modifiés … ne peuvent être diffusés en dehors de ce site". The CC-BY statement is the "indication contraire".
- `https://legilux.public.lu/editorial/use-conditions` (content at `/api/assets/editable-items/editable-pages/use-conditions.html`) is only a privacy/cookie policy.

**Per-file licence triples (`jolux:license`) [V]:**

| Namespace | CC-BY 4.0 | Licence de réutilisation SCL (`resource/authority/license/licenceSCL`) | No licence |
|---|---|---|---|
| Mémorial A (`leg`) | 129,358 | 121,754 | 5,341 |
| Consolidations | 13,674 | — | 2 |
| Mémorial B (`adm`) | 207,304 | 27,352 | 21,851 |

The SCL-licensed Mémorial A files are AKN XML 36,734 (dated 1617–2016), PDF 80,755 and HTML 4,107 (dated 1963–2016).

I could not find the text of the SCL licence. Rights statement: "Copyright : Ministère d'État du Grand-Duché de Luxembourg - Service central de législation".

**Obligations for the airgapped deployment:**
- Attribute the "Ministère d'État – Service central de législation (Legilux)".
- Ship the full CC BY 4.0 legal text inside the image, since a URL cannot be resolved offline.
- State that the content was modified (HTML/PDF to text, chunks, embeddings).
- Do not imply endorsement.
- Do not add restrictions on downstream reuse of the data.
- Keep each chunk's ELI, version date and `jolux:license` value, and show "Source: Legilux/SCL, CC BY 4.0, ELI …, version du …" in citations.
- Ingest CC-BY manifestations only, or flag licenceSCL items until SCL (info@legilux.public.lu) confirms.
- Add disclaimers:
  - Consolidated texts are informational. Only the electronic JO (sealed PDF) is authentic since 2017-01-01 (OP EU page) [V via WebFetch].
  - SCL gives no legal advice (FAQ 14-conditions-generales) [V].

---
## 6. Storage and crawl estimate for T1 [E]

**Database:**

| Item | Estimate |
|---|---|
| Embeddings, 1024-d `halfvec` (≈ 2,056 B each) | 400k × 2.06 KB ≈ **0.82 GB** |
| HNSW index | ≈ 0.8–1.0 GB |
| Embeddings, `vector` float32 alternative | ≈ 1.64 GB plus ≈ 1.7 GB index |
| Chunk text (TOAST-compressed) | ≈ 120–150 MB |
| tsvector plus GIN (French FTS) | ≈ 350–450 MB |
| Metadata | ≈ 100–150 MB |
| **Total Postgres, halfvec** | **≈ 2.3–3 GB** |
| **Total Postgres, float32** | **≈ 4–4.5 GB** |
| Compressed dump for the airgapped image | ≈ 1.5–2 GB |
| Gzipped source archive (provenance and SHA-256) | ≈ 150–250 MB |
| PDF-only codes | +135 MB (or +375 MB with the environment annexes) |

**Initial crawl at 1 request per second:**

| Step | Requests | Time |
|---|---|---|
| SPARQL metadata (year-chunked, under 10k rows per query) | ~60–150 queries | ≈ 5 min |
| T1-A | ≈ 1.3–2.5k | 20–40 min |
| T1-B | ≈ 22.9k HTML | ≈ 6.4 h (≈ 12.7 h at 0.5 req/s) |
| **Total** | — | **≈ 7 h (≈ 13 h at 0.5 req/s)** |

Total transfer is ≈ 0.9 GB raw, or ≈ 150–250 MB with gzip, plus PDFs. **Daily delta is under 50 requests.**

---
## 7. SPARQL endpoint gotchas (Virtuoso) [V]
- Results are capped at **10,000 rows**.
- `ORDER BY` with `OFFSET+LIMIT > 10000` fails with **SR353**. Paginate by year ranges instead.
- `BIND(EXISTS{…})` inside an aggregate triggers compiler error **SQ156**.
- `BIND` inside `OPTIONAL` silently yields zero counts.
- `REPLACE` with a regex returned the input unchanged. Use `STRBEFORE`/`STRAFTER`.

---
## 8. Key queries (all ran and returned results)
- **Type counts:** `SELECT ?t ?label ?match (COUNT(?s) AS ?n) WHERE { ?s jolux:typeDocument ?t . OPTIONAL{?t skos:prefLabel ?label} OPTIONAL{?t skos:relatedMatch ?match} } GROUP BY ?t ?label ?match ORDER BY DESC(?n)`, giving 90 rows (PA 43,501; RC 33,171; RGD 16,591; LOI 10,799 …).
- **Current consolidations:** `SELECT ?c ?act ?d ?end ?status ?t WHERE { ?c a jolux:Consolidation ; jolux:isMemberOf ?act ; jolux:dateApplicability ?d . OPTIONAL{?c jolux:dateEndApplicability ?end} OPTIONAL{?c jolux:inForceStatus ?status} OPTIONAL{?c jolux:typeDocument ?t} }`, giving 4,719 rows, then filtered by today's date in Python.
- **Repeal metadata:** `SELECT ?src ?rel ?tgt WHERE { ?src a jolux:Act ; jolux:dateDocument ?d . VALUES ?rel {jolux:modifies jolux:repeals} ?src ?rel ?tgt . FILTER(YEAR(?d)>=Y0 && YEAR(?d)<=Y1) }`, run in 6 year chunks for 47,052 rows.
- **Mémorial A metadata:** `SELECT ?jo ?w ?t ?status ?d ?dnl ?title WHERE { ?jo a jolux:Act ; jolux:typeDocument ?t ; jolux:inForceStatus ?status ; jolux:isMemberOf ?w ; jolux:dateDocument ?d . OPTIONAL{?jo jolux:dateNoLongerInForce ?dnl} OPTIONAL{?jo jolux:isRealizedBy ?e . ?e jolux:title ?title} FILTER(CONTAINS(STR(?jo),"/eli/etat/leg/")) FILTER(YEAR(?d)>=Y0 && YEAR(?d)<=Y1) }`, run in 10 chunks for 80,922 rows (36 MB TSV).
