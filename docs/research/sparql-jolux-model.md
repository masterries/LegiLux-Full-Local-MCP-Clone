# sparql-jolux-model

## Recommendations
- Build the 'current corpus' manifest from two flat queries and join them on the client: C04 (latest consolidation with files and dateApplicability <= today; 1,375 rows, 1.6 s), combined with C05/C06 to drop repealed base acts and flag stubs (gives 1,179 current consolidations), plus C07 (36,872 in-force core-scope acts with no consolidation, using the original JO text). Do not rely only on the 'applicable' flag: 15 applicable consolidations are file-less stubs, and the flag lags in some cases.
- Exclude non-normative and municipal types by config by default: PA (30k), RC (33k), RTC, RECT, DIV, RV, TC, TREP, D, PL/PR/AVIS. Restrict to the /eli/etat/leg/ branch. Keep the type list from C07 (34 types) as the core scope and make it configurable.
- Use consolidated XML (Akoma Ntoso CSD13 + scl:jolux metadata) as the primary content. Chunk per <article id> and keep the embedded article ELIs (e.g. …/art_1er/20180820) as citation anchors. HTML is the fallback. PDF only for the 5,697 scanned-only acts, which need OCR and splitting of shared Mémorial issue PDFs (3,672 files).
- For in-force acts that were modified but never consolidated (721 LOI, 764 RGD), store the original text plus the list of modifying and repealing acts (inbound jolux:modifies/repeals). Have the MCP server always warn 'texte non consolidé' so the LLM does not present outdated wording as current law.
- Ingest the 38 RECUEIL and 8 CODE_RECUEIL compilations: they are the only consolidated source for some heavily amended texts. Expect overlap with per-act consolidations and deduplicate by ELI at retrieval time.
- Store full metadata by paged CONSTRUCT of per-work named graphs (C12/C15, ~44 triples per act, ~1.6M triples for the core scope). Export the vocabularies once (C03). Load into a local triple store (e.g. Oxigraph/Fuseki) or flatten into Postgres tables (act, consolidation, manifestation, relation, vocab).
- Incremental sync, nightly: (1) C09 graph dct:modified >= last watermark minus 2 days of overlap, parsing datetimes on the client because of malformed literals; (2) C10 status/boundary sweep for dateEntryInForce, dateNoLongerInForce and dateApplicability crossing [watermark, today], because status flips do not bump graph dct:modified; (3) C14 relations of new acts, to recheck the targets' consolidations. Weekly: re-pull the full act status table (151k rows, ~1.6 MB CSV, <2 s). Never watermark on dateApplicability, because historic consolidations are backfilled late.
- Endpoint hygiene: at most 1 request per second, descriptive User-Agent, each query under ~30 s (gateway kills at ~45 s). Avoid STRSTARTS/REPLACE scans over the whole store. Always use DISTINCT, because the union default graph duplicates triples. Use CONSTRUCT, not DESCRIBE (broken). Use POST for long queries. Page with ORDER BY + LIMIT/OFFSET (verified consistent) in 10k–50k chunks.
- Do not bulk-download from data.legilux.public.lu/filestore: robots.txt disallows /filestore/* and /eli/*. Contact the Service central de législation (SCL) for written permission, a crawl allowance or an official dump. Until then, fetch only what is explicitly permitted, or treat full-text acquisition as blocked.
- Derive an ELI-compatible view locally if needed, using the jolux->eli alignment (isRealizedBy->eli:is_realized_by, isEmbodiedBy->eli:is_embodied_by, isExemplifiedBy->eli:is_exemplified_by, inForceStatus via skos:exactMatch to eli:InForce-*). The endpoint has no eli: instance data.
- Keep citation metadata per chunk so the LLM can cite precisely: ELI of the act and of the article, expression title, consolidation 'Version consolidée applicable au <date>', Mémorial A number/year (jolux:isPartOf -> memorialNumber/memorialYear), publicationDate, dateEntryInForce, license, and the source file URL.

## Risks / open questions
- robots.txt on data.legilux.public.lu disallows /filestore/* and /eli/*. Bulk acquisition of full texts (HTML/XML/PDF) by a crawler is not permitted without SCL agreement. This is the main blocker for the project. (3 file GETs were made before this was discovered; no further file downloads followed.)
- Licensing is mixed: many original AKN XML files and PDFs carry 'Licence de réutilisation SCL' rather than CC-BY 4.0. The terms of licenceSCL were not retrieved (UNVERIFIED) and must be checked before redistribution inside an air-gapped bundle.
- 'in-force' for acts means 'not known to be repealed'. There is no partial-repeal status, amending laws stay in force forever, and many 19th-century acts are flagged in-force. The LLM must not treat the flag as proof that every provision still applies.
- Consolidation coverage is limited: only ~1,179 current consolidated texts. 721 modified in-force laws and 764 RGDs have no consolidation in RDF. Some recueils are stale (e.g. protection_donnees/20230905, directives_eu/20080711). It is UNVERIFIED whether the website shows more consolidations than the RDF.
- 113 stub consolidations without files exist; 10 current texts have a newer stub, so the downloadable consolidated text may be outdated. It is unknown when stubs get their files.
- 5,697 in-force core-scope acts exist only as scanned Mémorial issue PDFs. OCR quality and splitting multi-act issues are open problems. The text layer of these PDFs is UNVERIFIED.
- Status flips are silent: they do not bump the graph dct:modified. Some dct:modified literals are malformed (e.g. '…472Z+02:00'), so watermark filters return false positives and MAX()/YEAR() misbehave.
- There is no file-size property in RDF and full-text storage was not measured. The size estimate (roughly 1–3 GB for XML+HTML) is UNVERIFIED; only 3 samples were measured (84,689 B, 140,421 B, 4,128,058 B).
- The website (legilux.public.lu) is an Angular SPA that loads content through config.back_end.manifestation_prefix + filestore and a /zuul-proxy/casemates Elasticsearch API. Whether that API is allowed or stable for programmatic use is UNVERIFIED and belongs to another track.
- The endpoint has an approximately 45 s gateway timeout. Complex single-query manifests (joins + GROUP BY + EXISTS) time out and must be split. Server-side rate limits were not observed at 1 req/s but are not documented (UNVERIFIED).
- jolux:Article RDF is stale (last 2021-12-26). Article-level retrieval must rely on parsing the XML, and the article markup in HTML-only or older AKN files is UNVERIFIED.
- Scope choice is a product decision: excluding RC (règlements communaux, 33k in force), PA and CMIN (circulaires) saves space but may lose answers to municipal or administrative questions.

## Verified facts
- Endpoint is Virtuoso with union default graph and one named graph per resource (<work>/graph), 395,094 graphs, 13,307,204 triples total  
  _evidence:_ GET https://data.legilux.public.lu/sparqlendpoint query 'SELECT (COUNT(*) AS ?n) WHERE { ?s ?p ?o }' -> "value": "13307204"; 'SELECT (COUNT(DISTINCT ?g) AS ?graphs) WHERE { GRAPH ?g { ?s a ?c } }' -> 395094; error text 'Virtuoso 37000 Error SP031'; sd:feature UnionDefaultGraph
- Each named graph carries <g> dct:modified (395,092 graphs) usable as incremental watermark  
  _evidence:_ SELECT ?p (COUNT(*) AS ?n) WHERE { GRAPH ?g { ?g ?p ?o } } GROUP BY ?p -> dct:modified | 395092; e.g. <…/a686/consolide/20251226/graph> dct:modified "2026-04-23T18:38:48.622+02:00"
- No 10k row cap: 151,250 rows returned in one CSV response; OFFSET paging with ORDER BY is consistent  
  _evidence:_ SELECT ?d WHERE { ?s a jolux:Act ; jolux:publicationDate ?d } LIMIT 160000 (Accept text/csv) -> 151251 lines, 1,663,754 bytes, 1.54 s; LIMIT 10 == LIMIT 5 OFFSET 0 + LIMIT 5 OFFSET 5 -> 'PAGING CONSISTENT'
- Effective query timeout is about 45 s (Spring Boot gateway returns HTTP 500 Whitelabel page)  
  _evidence:_ q68/q94/q95 -> 'HTTP 500 size 247 time 45.46s/45.63s/45.66s', body '<h1>Whitelabel Error Page</h1>'; queries of 41.1-41.5 s succeeded
- DESCRIBE is broken; CONSTRUCT works in N-Triples, Turtle, RDF/XML, JSON-LD; SELECT works in JSON, CSV, TSV, XML; POST works  
  _evidence:_ DESCRIBE <…/a686/jo> -> 'HTTP 500 Virtuoso 37000 Error SP031: SPARQL: Internal error: corrupted metadata: PointerToCorrupted'; CONSTRUCT (q40) -> 200 application/n-triples 287 triples, text/turtle 200, application/rdf+xml 200, application/ld+json 200; POST --data-urlencode query@q66.rq -> 'POST HTTP 200'
- Acts have explicit jolux:inForceStatus on all 151,250 acts: in-force 119,980; no-longer-in-force 31,162; not-yet-in-force 58; no-longer-in-force-implicit 50  
  _evidence:_ SELECT ?cls ?status (COUNT(?s) AS ?n) WHERE { VALUES ?cls {jolux:Act …} ?s a ?cls . OPTIONAL { ?s jolux:inForceStatus ?status } } GROUP BY ?cls ?status -> jolux#Act | …/in-force | 119980 ; …/no-longer-in-force | 31162 ; …/not-yet-in-force | 58 ; …/no-longer-in-force-implicit | 50
- In-force status for acts is highly consistent with end dates: only 1 act has dateNoLongerInForce <= 2026-10-16 but is still in-force  
  _evidence:_ q19: SELECT ?status (COUNT(?s)) (SUM(IF(?d <= "2026-10-16"^^xsd:date,1,0))) … -> in-force | 3465 | 1 ; no-longer-in-force | 31162 | 31159; q21 -> eli/etat/adm/amin/2023/08/29/b3075/jo | 2026-08-27
- Status flips do not update the graph dct:modified watermark  
  _evidence:_ q87: acts with dateNoLongerInForce in [2026-09-01, 2026-10-16] -> 'graph-older | no-longer-in-force | 258' vs 'graph-modified-since-watermark | 11'
- Consolidation status vocabulary: applicable 1,181 / not-applicable 3,471 / not-yet-applicable 29 / none 38; exactly one applicable per complex work; applicable maps skos:exactMatch eli:InForce-inForce  
  _evidence:_ q09 Consolidation rows; q47 HAVING COUNT(DISTINCT ?c)>1 -> ROWS: 0; q60 -> application-status/applicable skos:exactMatch http://data.europa.eu/eli/ontology#InForce-inForce
- 113 stub consolidations exist without expressions/files, 15 of them flagged applicable (e.g. loi/2018/05/30/a446/consolide/20260606)  
  _evidence:_ q73 -> applicable | hasExpr 0 | 15 ; NONE | 0 | 38 ; not-applicable | 0 | 56 ; not-yet-applicable | 0 | 4; q72 shows …/a446/consolide/20260606 with only types, isMemberOf, inForceStatus applicable, dateApplicability 2026-06-06
- Robust rule (latest consolidation with files, dateApplicability <= today) yields 1,375 candidates, 1,179 after dropping repealed base acts, incl. 46 recueils/code-recueils without base act; 10 have a newer file-less stub  
  _evidence:_ C04 (q96) -> 'rows 5122 distinct conso 1375' in 1.56 s; client join with C05 (q97, 1370 rows) and C06 (q98, 113 stubs) -> 'keep 1179 drop(base act repealed) 196', 'kept but newer stub exists 10', 'kept w/o base act 46 {RECUEIL 38, CODE_RECUEIL 8}'
- In-force core-scope acts without any dated consolidation: 36,872 acts; 31,177 with html+xml+pdf, 5,697 PDF-only (3,672 distinct Mémorial issue PDFs, mostly 19th century)  
  _evidence:_ C07 (q93) CSV -> 'rows 36875 distinct acts 36872', 'html,xml,pdf: (True,True,True) 31177, (False,False,True) 5697'; 31,291,340 bytes in 5.7 s; pdf-only by century {18xx: 4083, 19xx: 1417, 20xx: 195}
- 721 in-force LOIs that have been modified have no consolidation at all (only 482 have an applicable one)  
  _evidence:_ q24: LOI | hasApplicable 1 | hasAny 1 | 482 ; LOI | 0 | 1 | 28 ; LOI | 0 | 0 | 721 (RGD: 496 / 29 / 764)
- File URLs follow http://data.legilux.public.lu/filestore/<eli path>/<fmt>/<eli-with-dashes>-<fmt>.<ext> via jolux:isExemplifiedBy; consolidated XML is Akoma Ntoso 3.0 CSD13 with scl: namespace and article-level ELIs  
  _evidence:_ q04/q28: …/consolide/20251226/fr/xml jolux:isExemplifiedBy http://data.legilux.public.lu/filestore/eli/etat/leg/loi/2018/08/01/a686/consolide/20251226/fr/xml/eli-etat-leg-loi-2018-08-01-a686-consolide-20251226-fr-xml.xml; GET -> 200 140,421 bytes, root '<akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0/CSD13" xmlns:scl="http://www.scl.lu">', 78 <article>, article URI …/a686/art_1er/20180820
- robots.txt of data.legilux.public.lu disallows /eli/* and /filestore/* (content files), while the SPARQL endpoint path is not disallowed  
  _evidence:_ GET https://data.legilux.public.lu/robots.txt -> 'User-agent: * / Disallow: /api/rss-adm.xml / Disallow: /eli/* / Disallow: /filestore/*'
- Filestore: HEAD returns 403, GET works with Last-Modified and byte ranges; Code du travail current XML is 4,128,058 bytes  
  _evidence:_ curl -I … -> 'HTTP/1.1 403 Forbidden'; GET gdpr jo xml -> 200 84,689 bytes, 'Last-Modified: Thu, 20 Nov 2025 05:46:48 GMT'; Range bytes=0-511 on …/code/travail/20260726/fr/xml/… -> 'HTTP/1.1 206', 'Content-Range: bytes 0-511/4128058'
- ELI ontology is not instantiated; only JOLux->ELI alignment axioms exist  
  _evidence:_ ASK { ?s <http://data.europa.eu/eli/ontology#is_realized_by> ?o } -> "boolean": false; instance classes in eli: namespace -> ROWS: 0; q58 -> jolux#isExemplifiedBy rdfs:subPropertyOf eli#is_exemplified_by, jolux#Expression rdfs:subClassOf eli#LegalExpression
- Codes are Consolidation+Code resources with dated versions; 16 jolux:Code applicable (7 CODE, 8 CODE_RECUEIL, 1 RECUEIL-typed) plus 36 more RECUEIL collections; Code du travail has 56 versions  
  _evidence:_ q11 -> code/civil/20251226 … code/travail/20260726 (16 rows); q51 -> 52 rows (CODE 7, CODE_RECUEIL 8, RECUEIL 37); q15 -> 57 rows under isMemberOf loi/2006/07/31/n2 (56 code/travail versions + the /jo act)
- Article-level RDF is stale legacy (15,770 distinct articles dated 2010-12-21..2021-12-26)  
  _evidence:_ q36: SELECT (MIN(?d)) (MAX(?d)) (COUNT(DISTINCT ?s)) WHERE { ?s a jolux:Article ; jolux:dateApplicability ?d } -> 2010-12-21 | 2021-12-26 | 15770
- PROV change log stops in Feb 2022 and is unusable for sync  
  _evidence:_ q42 year histogram of prov:generatedAtTime -> last row '2022 | 1397 | 2022-02-07T17:06:01Z'
- Incremental change summary since 2026-09-01 works in one query  
  _evidence:_ C11 (q81) -> entered-in-window 797; work-created 752; conso-became-applicable 39; ceased-in-window 269; file-modified 895; act-published 698 (0.41 s)
- Graph watermark query for the legislation branch returns 439 rows since 2026-09-01, 12 of them false positives from malformed literals like "2023-04-27T16:29:30.472Z+02:00"  
  _evidence:_ C09 (q100) -> 440 CSV lines, 0.85 s; client check 'malformed/quoted modified 12', well-typed min 2026-09-01T08:20:07.255+02:00
- YEAR() and MAX() fail or misbehave on dct:modified because of mixed or malformed datetime literals  
  _evidence:_ q43 -> 'Virtuoso 22003 Error SR586: Incomplete RDF box as argument 0 for year()'; q45 MAX(?t) for Manifestation with FILTER >= 2026-09-01 returned 2023-06-12T09:44:47.293+02:00
- Expressions are essentially French-only (263,155 FRA vs 103 ENG, 14 DEU)  
  _evidence:_ q52: SELECT ?lang (COUNT(DISTINCT ?e)) … -> language/FRA | 263155 ; ENG | 103 ; DEU | 14
- License is mixed between CC-BY 4.0 and 'Licence de réutilisation SCL'  
  _evidence:_ q79 -> user-format/html CC-BY 95035 / licenceSCL 4129 ; xml-akomantoso licenceSCL 36775 / CC-BY 27064 ; xml CC-BY 35250 ; q80 licenceSCL skos:prefLabel 'Licence de réutilisation SCL'
- Paged CONSTRUCT of per-act named graphs is fast: 500 laws -> 22,038 triples / 4.57 MB in 1.75 s  
  _evidence:_ C12 (q91) CONSTRUCT { ?s ?p ?o } WHERE { {SELECT ?act … ORDER BY ?act LIMIT 500 OFFSET 0} BIND(IRI(CONCAT(STR(?act),"/graph")) AS ?g) GRAPH ?g { ?s ?p ?o } } -> 'HTTP 200 size 4566000 time 1.751760s', 22038 lines
- Vocabulary export via CONSTRUCT: 15,253 triples (2.4 MB) for resource-type, application-status, legal-subject, legal-institution, user-format, license  
  _evidence:_ C03 (q90) -> 'HTTP 200 size 2422346 time 1.302364s', 15253 lines, 3787 prefLabels

## Report

# Track: SPARQL and the JOLux/ELI data model (live, 2026-10-16)

Every query below was run against `https://data.legilux.public.lu/sparqlendpoint` at no more than 1 request per second, with User-Agent `LegiLux-Local-Clone-Research/0.1`. That came to about 110 queries.
- Tested `.rq` files: `docs/research/sparql-catalog/` (C01–C16, plus `run_query.sh`).
- Raw query outputs: `<research-scratch>/sparql-jolux-model/out/`.

Anything not checked live is marked **UNVERIFIED**.

---
## 1. The endpoint and its limits (VERIFIED)

**Backend.** The server is OpenLink Virtuoso: error strings read `Virtuoso 37000 Error SP031`, and `virtrdf:` classes appear. It sits behind a Spring Boot / Apache front.

**Size and graph layout.**
- `SELECT (COUNT(*) AS ?n) WHERE {?s ?p ?o}` returns **13,307,204** triples.
- The default graph is the *union of all named graphs*. The service description advertises `sd:UnionDefaultGraph`.
- There is **one named graph per resource**, at `<work-IRI>/graph`. There are **395,094** named graphs.
- Each named graph describes itself with `<g> dct:modified "…"`. There are 395,092 such triples. This is the best watermark for incremental sync (section 6).

**Duplicate triples.** Because of the union default graph, the same triple can appear several times. Example: `jolux:Article` has 58,673 type rows but only **15,770 distinct** subjects. Always use `SELECT DISTINCT` and `COUNT(DISTINCT …)`.

**Row cap.** No 10k cap was hit.
- A `LIMIT 160000` query returned all **151,250** Act publication dates as CSV (1.66 MB, 1.5 s).
- `LIMIT 50000` on Expressions returned 50,000 rows.

**Paging.** `ORDER BY ?act LIMIT 5 OFFSET 0` plus `LIMIT 5 OFFSET 5` was identical to `LIMIT 10`.

**Timeout.** It is about 45 s.
- Two queries failed at 45.46 s and 45.66 s with `HTTP 500` and a Spring Boot "Whitelabel Error Page".
- Queries taking 41.1–41.5 s succeeded.
- Keep each query under about 30 s. Avoid `FILTER(STRSTARTS(...))` scans over the whole store: these took about 41 s.

**Result formats.**

| Query type | Formats that work |
|---|---|
| SELECT | JSON, CSV, TSV, SPARQL-XML |
| CONSTRUCT | N-Triples, Turtle, RDF/XML, JSON-LD |

**Broken or buggy features.**
- **DESCRIBE is broken.** `DESCRIBE <…/a686/jo>` returns `HTTP 500 Virtuoso 37000 Error SP031: Internal error: corrupted metadata: PointerToCorrupted`, in both Turtle and N-Triples. Use CONSTRUCT instead.
- **Mixed date literals break some functions.**
  - Some `dct:modified` values are not proper `xsd:dateTime`, for example `"2022-05-30T16:30:23.133754"` or the malformed `"2023-04-27T16:29:30.472Z+02:00"`.
  - Because of this, `YEAR()` fails with `SR586: Incomplete RDF box`, and `MAX(?t)` returns wrong results.
  - Range FILTERs mostly work, but let a few malformed values through: 12 of 439 rows in C09. Parse datetimes on the client side.

**POST** with `query=` form-encoding works (C14 was run this way).

---
## 2. The data model actually in use (VERIFIED)

FRBR chain:

```
ComplexWork  <eli>                         e.g. …/eli/etat/leg/loi/2018/08/01/a686           (jolux:ComplexWork)
  ^ jolux:isMemberOf
  ├─ Act (original, as published)    <eli>/jo                    (jolux:Act, jolux:Work, jolux:LegalResource)
  └─ Consolidation versions          <eli>/consolide/YYYYMMDD    (jolux:Consolidation)
       Codes:                         …/eli/etat/leg/code/<name>/YYYYMMDD  (also jolux:Code)
       Recueils (compilations):      …/eli/etat/leg/recueil/<name>/YYYYMMDD (jolux:Collection + jolux:Code)
Work --jolux:isRealizedBy--> Expression <…>/fr  (jolux:language FRA, jolux:title, jolux:titleShort, jolux:titleAlternative)
Expression --jolux:isEmbodiedBy--> Manifestation <…>/fr/{html|xml|pdf|pdfa|docx}
Manifestation --jolux:isExemplifiedBy--> file URL  http://data.legilux.public.lu/filestore/<path>/<eli-with-dashes>.<ext>
               jolux:userFormat (html|xml|xml-akomantoso|pdf|pdfa|docx), jolux:format (EU file-type), jolux:license,
               dct:modified, jolux:publishedIn (Mémorial PDF), jolux:numberOfPages, jolux:uploadDate, jolux:previousIsExemplifiedBy (old /file/ URL)
Act --jolux:isPartOf--> Memorial <…/eli/etat/leg/memorial/2018/a686> (jolux:memorialNumber 686, memorialYear 2018, title "Mémorial A n° 686 de 2018")
```

### Example: the GDPR implementing law (`…/eli/etat/leg/loi/2018/08/01/a686/jo`, 37 triples)

**Dates and status:**
- `jolux:typeDocument resource-type/LOI`
- `dateDocument 2018-08-01`, `publicationDate 2018-08-16`, `dateEntryInForce 2018-08-20`
- `inForceStatus application-status/in-force`

**Links:**
- `isMemberOf …/a686`, `isPartOf memorial/2018/a686`
- `cites` ×11, including `eli/reg_ue/2016/679/jo`
- `modifies` 2015/03/25/n3 and 2006/07/31/n2 (the Code du travail law)
- `repeals` 2002/08/02/n2
- `draft eli/etat/proj/pl/20170198`

**Subjects and responsibility:** `subjectLevel1/2` (legal-subject concepts), `responsibilityOf legal-institution/MC`.

**Inbound links:** `basedOn` (RGDs and RIs implementing it), `modifies` from loi/2025/12/19/a619, `foreseesModificationOf` from drafts, `hasResultingLegalResource` from `eli/dl/pl/2017/198`.

**Its consolidation** is `…/a686/consolide/20251226`:
- Classes: Consolidation, Work, LegalResource.
- `dateApplicability 2025-12-26`, `publicationDate 2026-01-08`, `dct:created 2026-01-08T09:59:17.373+01:00`.
- `inForceStatus application-status/applicable`.
- Expression title: `"Version consolidée applicable au 26/12/2025 : Loi du 1er août 2018 …"`.
- Manifestations docx/xml/html/pdf, with files such as `…/filestore/eli/etat/leg/loi/2018/08/01/a686/consolide/20251226/fr/xml/eli-etat-leg-loi-2018-08-01-a686-consolide-20251226-fr-xml.xml`.

### Example: Code du travail
- `…/eli/etat/leg/code/travail/20260726` has types Consolidation and Code, typeDocument CODE, `isMemberOf …/loi/2006/07/31/n2`, `dateApplicability 2026-07-26`, status `applicable`.
- There are **56 versions** in total (20110221 … 20260726). All but the newest are `not-applicable`.
- Its XML file is **4,128,058 bytes**, read from the `Content-Range` header of a ranged GET.

**Base acts of the codes:**

| Code | Base act |
|---|---|
| Code civil | loi/1804/03/21/n1 |
| Commerce | loi/1807/09/15/n2 |
| Pénal | loi/1879/06/18/n1 |
| Procédure pénale | loi/1808/11/17/n1 |
| Procédure civile | rgd/1998/08/03/n4 |
| Consommation | loi/2011/04/08/n2 |
| Sécurité sociale | loi/1925/12/17/n1 |

Code communal, éducation nationale, environnement, environnement_annexes, fonction publique, route and santé are `CODE_RECUEIL`, a member of `…/code/<name>`, with **no base Act**.

### Class counts (DISTINCT, C01)

| Class | Count |
|---|---|
| Manifestation | 560,369 |
| Work | 387,016 |
| Expression | 263,317 |
| LegalResource | 234,245 |
| ComplexWork | 151,580 |
| Act | 151,250 |
| Memorial | 72,398 |
| DraftRelatedDocument | 27,710 |
| Article | 15,770 |
| EULegalResource | 5,461 |
| Consolidation | 4,719 |
| TreatyDocument | 1,184 |
| Code | 916 |
| Collection | 754 |
| JudicialDecision | 421 |

### Predicates on `jolux:Act` (C17-style query, rows / distinct subjects)

| Predicate | Rows | Distinct subjects |
|---|---|---|
| subjectLevel2 | 257,320 | 140,528 |
| dateEntryInForce | 151,252 | 151,249 |
| isMemberOf | 151,250 | 151,250 |
| typeDocument | 151,250 | 151,250 |
| publicationDate | 151,250 | 151,250 |
| inForceStatus | 151,250 | 151,250 |
| isPartOf | 151,250 | 151,250 |
| dateDocument | 151,250 | 151,250 |
| isRealizedBy | 151,242 | 151,238 |
| historicalLegalId | 95,998 | 95,998 |
| basedOn | 86,315 | 57,909 |
| cites | 77,981 | 31,968 |
| dateNoLongerInForce | 34,636 | 34,636 |
| modifies | 27,420 | 17,504 |
| dct:created | 24,273 | 24,273 |
| repeals | 19,632 | 12,851 |
| transposes | 3,354 | 1,955 |
| rectifies | 1,593 | 1,577 |
| consolidates | 367 | 356 (336 of them are TC "texte coordonné") |

Less frequent predicates:
- `modifiedTempBy` 28
- `dateTempDerStartApplicability` 14
- `dateTempDerEndApplicability` 13

### Predicates on `jolux:Consolidation`

| Predicate | Rows |
|---|---|
| isMemberOf | 4,719 |
| dateApplicability | 4,719 |
| inForceStatus | 4,681 |
| isRealizedBy | 4,610 |
| typeDocument | 4,606 |
| publicationDate | 4,606 |
| dateEndApplicability | 3,505 |
| dct:created | 3,000 |
| responsibilityOf | 2,867 |

### Articles
`jolux:Article` resources such as `…/code/procedure_civile/art_127/20150901` carry only title, isMemberOf and dateApplicability. Their dates run **2010-12-21 … 2021-12-26** (C36), so they are a stale legacy. **There is no maintained article-level RDF.**

Article structure has to come from the XML. The consolidated XML is Akoma Ntoso 3.0 CSD13 with an `scl:` namespace. The GDPR consolidated XML has 78 `<article id="art_1er">` elements carrying article ELIs such as `…/a686/art_1er/20180820`.

### Languages
Expressions in French (FRA): 263,155. English: 103. German: 14. The corpus is effectively French-only.

### Manifestation formats

| Format | Count |
|---|---|
| pdf | 154,145 |
| pdfa | 108,657 |
| docx | 99,168 |
| html | 99,162 |
| xml-akomantoso (original JO texts) | 63,839 |
| xml (consolidations) | 35,323 |

### Licences (C79)
Licences are mixed:
- HTML: 95,035 CC-BY-4.0 and 4,129 `license/licenceSCL` ("Licence de réutilisation SCL").
- xml-akomantoso: 36,775 licenceSCL and 27,064 CC-BY.
- Consolidation XML: 35,250 CC-BY.

---
## 3. Controlled vocabularies (VERIFIED)

**Resource types** (`skos:inScheme <…/authority/resource-type>`, 74 concepts). Selected labels:
- LOI=loi, RGD=règlement grand-ducal, AGD=arrêté grand-ducal, RMIN=règlement ministériel, AMIN=arrêté ministériel, A=arrêté
- CODE=code, CODE_RECUEIL=code-recueil, RECUEIL=recueil, Constitution
- PA=publication administrative, RC=règlement communal, RTC=règlement temporaire de la circulation, RECT=rectificatif
- TC=texte coordonné, TREP=texte republié, CMIN=circulaire ministérielle, CONV=convention
- ACC=arrêt de la Cour Constitutionnelle, RCSF=règlement CSSF, RILR=règlements ILR, RBCL=règlement BCL
- DIR_UE / REG_UE / DEC_UE (EU acts)

The full table is in `out/q08.json`. C03 exports all needed schemes (resource-type, application-status, legal-subject, legal-institution, user-format, license) in one CONSTRUCT: **15,253 triples, 2.4 MB, 1.3 s**.

**Act counts by type** (C02): PA 43,495 · RC 33,171 · RGD 15,379 · AMIN 12,692 · AGD 9,743 · LOI 9,265 · A 7,261 · RMIN 6,103 · DIV 3,594 · ARGD 1,983 · … · Constitution 1.

**Application status** (`…/authority/application-status`) has two collections:

| Collection | Values |
|---|---|
| ACT | in-force, no-longer-in-force, no-longer-in-force-implicit, not-yet-in-force, indeterminate-status |
| CONSO | applicable, not-applicable, not-yet-applicable, partially-applicable, indeterminate-status |

**ELI alignment.** `skos:exactMatch` maps:
- `applicable` → `eli:InForce-inForce`
- `not-applicable` → `eli:InForce-notInForce`
- `partially-applicable` → `eli:InForce-partiallyInForce`

The ontology comment on `jolux:inForceStatus` says: *"Alignement sur eli:in_force - alignement à faire en sparql avec conversion des valeurs sur la base de l'alignement skos:exactMatch"*.

**The ELI view is not instantiated.**
- `ASK { ?s eli:is_realized_by ?o }` returns **false**.
- No instance has an `eli:` class.
- `eli:` appears only in `rdfs:subClassOf` / `subPropertyOf` / domain / range axioms of the JOLux ontology, e.g. `jolux:isExemplifiedBy rdfs:subPropertyOf eli:is_exemplified_by`.

An ELI view would have to be derived locally:
- isRealizedBy → eli:is_realized_by
- isEmbodiedBy → eli:is_embodied_by
- isExemplifiedBy → eli:is_exemplified_by
- inForceStatus → eli:in_force via exactMatch
- dateNoLongerInForce → eli:date_no_longer_in_force
- dateEntryInForce → eli:first_date_entry_in_force

---
## 4. How "in force" is represented and how reliable it is

### Acts: `jolux:inForceStatus` exists on all 151,250 Acts (C09/C16)

| Status | Acts |
|---|---|
| in-force | 119,980 |
| no-longer-in-force | 31,162 |
| not-yet-in-force | 58 |
| no-longer-in-force-implicit | 50 |

**Consistency checks:**
- Of the targets of `jolux:repeals`, 19,079 are no-longer-in-force and only 2 are still in-force. Partial repeals probably explain those 2.
- Acts with `dateNoLongerInForce <= 2026-10-16` but still `in-force`: **1** (`eli/etat/adm/amin/2023/08/29/b3075/jo`, end 2026-08-27).
- Another 3,464 in-force acts carry a *future* end date (expiring PA/AMIN/AGD acts etc.).

**Meaning.** "in-force" means "not known to be repealed". There is no partial status, and amending laws stay "in force". For example, 292 LOIs from the 1850s are still "in-force".

**Status flips do not bump the graph watermark.** Of 269 acts that ceased between 2026-09-01 and 2026-10-16, **258 had no graph `dct:modified` update** (C87). They must be caught with a separate date-window sweep (C10).

### In-force acts by type and branch (C16; the `/eli/etat/adm/` branch is Mémorial B)

| Type | Branch | In force | With HTML | With consolidation |
|---|---|---|---|---|
| PA | adm | 30,584 | | |
| RC | leg | 21,612 | | |
| RC | adm | 11,501 | | |
| RGD | leg | 9,734 | 9,649 | 532 |
| LOI | leg | 8,033 | 7,131 | 499 |
| AMIN | adm | 7,743 | | |
| A | leg | 5,314 | 3,681 | 1 |
| AGD | leg | 4,327 | 4,056 | 14 |
| RMIN | leg | 3,240 | 3,036 | 13 |
| DIV | leg | 3,034 | | |
| AMIN | leg | 2,102 | | |
| ARGD | leg | 1,623 | | |
| CMIN | leg | 1,073 | | |

The full table is in `out/q55.json`.

### Consolidations

**By flag:**

| Status | Consolidations |
|---|---|
| applicable | 1,181 |
| not-applicable | 3,471 |
| not-yet-applicable | 29 |
| no status | 38 |

**Checks on the flag:**
- Exactly 0 complex works have more than one `applicable` consolidation (C47).
- Exactly 0 works have a newer consolidation dated ≤ today than the `applicable` one (C48).

**Caveat: 113 "stub" consolidations exist with no `isRealizedBy` (no files).**
- 15 of them are flagged `applicable`, 38 have no status, 56 are not-applicable and 4 are not-yet-applicable.
- Example: `loi/2018/05/30/a446/consolide/20260606` is `applicable` with only `dateApplicability`. The newest version *with files* is `…/consolide/20260403`, which is flagged `not-applicable` with `dateEndApplicability 2026-06-06`.

**Recommended robust rule (C04 + C05 + C06).** Take the latest consolidation **with an expression** whose `dateApplicability ≤ today`, drop it if the base act is no-longer-in-force, and flag it if a newer stub exists. Results:
- 1,375 latest-with-files candidates
- minus 196 whose base act was repealed
- = **1,179 current consolidated texts**: 1,133 tied to a base act plus 46 recueils/code-recueils with no base act.
- 10 of these have a newer file-less stub (text possibly outdated).
- 51 have an end date already passed.

**Coverage gap.** Of 1,231 in-force LOIs that are targets of `jolux:modifies`, only 482 have an applicable consolidation and **721 have none at all**. Example: `loi/1947/07/23/n2` (Benelux customs convention), modified 180 times.

Content of the code-recueils/recueils, such as Code de la route for loi/1955/02/14/n3 (UNVERIFIED mapping), is only available inside the compilation documents. RDF has no hasPart links from recueils to their member acts.

---
## 5. Working ingestion queries (all run live)

| ID | Purpose | Result |
|---|---|---|
| C04 (q96) | Current consolidation per work, with every file URL and dct:modified | 5,122 rows / **1,375** consolidations, CSV 3.0 MB, **1.56 s**. Format sets: {docx, html, pdf, xml} 1,236; {pdf} only 126; akn variant 13 |
| C05 (q97) | Base-act status of every consolidated work | 1,370 rows, 2.6 s |
| C06 (q98) | Stub consolidations | 113 rows, 0.55 s |
| C07 (q93) | In-force core-scope acts (`/eli/etat/leg/` only; 34 types listed below) with no usable consolidation → original JO text, one row per act with html/xml/pdf | **36,875 rows / 36,872 acts**, CSV 31.3 MB, **5.7 s**. 31,177 have html + xml + pdf; **5,697 are PDF-only** (scanned Mémorial issue PDFs: 3,672 distinct files, 4,083 of them 19th-century) |
| C08 (q63) | Flag-based variant (applicable + not-yet-applicable) | 4,668 rows / 1,191 consolidations, 1.9 s |
| C13 (q51) | 52 applicable codes + recueils with titles | 0.7 s |
| C12 (q91) | Paged CONSTRUCT of per-act named graphs (complete metadata incl. expressions and manifestations) | Page of 500 LOIs → 22,038 triples, 4.57 MB N-Triples, 1.75 s |
| C15 (q83) | CONSTRUCT of chosen named graphs | 2 graphs → 149 triples |
| C03 (q90) | Vocabulary export | 15,253 triples |

The 34 core types in C07: Constitution, LOI, RGD, AGD, RMIN, AMIN, A, ARGD, AGC, RGC, ORD, ORDR, DEC, REG, RBCL, RCSF, RILR, CONV, PROT, ACCO, ST, RI, IMIN, CMIN, DGC, AGDR, DMIN, AR, RAP, REEX, RTF, RTDE, ACC, ACCA.

C07 excludes acts with `dateNoLongerInForce ≤ today` and any act whose work has a dated consolidation with files.

Core of C04:
```sparql
SELECT DISTINCT ?conso ?cw ?type ?flag ?dateApplicability ?dateEndApplicability ?created ?userFormat ?file ?fileModified WHERE {
 { SELECT ?cw (MAX(?d) AS ?dateApplicability) WHERE { ?x0 a jolux:Consolidation ; jolux:isMemberOf ?cw ;
     jolux:dateApplicability ?d ; jolux:isRealizedBy ?e0 . FILTER(?d <= "2026-10-16"^^xsd:date) } GROUP BY ?cw }
 ?conso a jolux:Consolidation ; jolux:isMemberOf ?cw ; jolux:dateApplicability ?dateApplicability ; jolux:isRealizedBy ?e .
 OPTIONAL{?conso jolux:typeDocument ?type} OPTIONAL{?conso jolux:inForceStatus ?flag}
 OPTIONAL{?conso jolux:dateEndApplicability ?dateEndApplicability} OPTIONAL{?conso dct:created ?created}
 ?e jolux:isEmbodiedBy ?m . ?m jolux:userFormat ?userFormat ; jolux:isExemplifiedBy ?file . OPTIONAL{?m dct:modified ?fileModified} }
```

**Pitfall.** Adding the base-act join, a GROUP BY with SAMPLE, and an EXISTS stub check to C04 in a single query hit the ~45 s timeout twice. Keep queries flat and join on the client side.

**Corpus size.** About 38,050 documents: 1,179 consolidated plus 36,872 original. The XML-bearing subset is about 32.4k. Total storage is **UNVERIFIED**.
- Samples: original GDPR XML 84,689 B, consolidated XML 140,421 B, Code du travail XML 4.13 MB.
- No file-size property exists in the RDF.
- A guess of 1–3 GB for XML+HTML is unverified.

Full metadata for the core scope is about 37k acts × ~44 triples, roughly 1.6M triples or ~340 MB of N-Triples (extrapolated from C12).

---
## 6. Incremental sync

**Watermarks that are verified to work:**
1. **Graph-level `dct:modified`** (C09): `GRAPH ?g { ?g dct:modified ?t } FILTER(?t >= W)` restricted to `/eli/etat/leg/`. From W=2026-09-01 it returned 439 graphs in 0.85 s, 12 of them false positives from malformed literals. Across all branches (C85): etat/adm 1,948, other 655, etat/leg 383, consolidations 39, codes/recueils 18. Superseded consolidations do bump it: `code/travail/20260701/graph` was modified 2026-07-27 when 20260726 appeared.
2. **New works:** `dct:created ≥ W` (Acts 698, Consolidations 55, Codes 14 since 2026-09-01). Also `jolux:publicationDate ≥ W` (698 Acts: PA 412, AMIN 136, RC 97, RGD 13, RMIN 11, LOI 8, …).
3. **Files:** Manifestation `dct:modified ≥ W` (3,413 manifestations). This property is present on 173,341 manifestations, with values up to 2026-10.
4. **Status and boundary sweep** (C10): acts whose `dateEntryInForce` or `dateNoLongerInForce` falls in [W, today] (79 leg rows). Consolidations whose `dateApplicability` falls in [W, today] (39). This is mandatory because status flips are silent.
5. **Impact fan-out** (C14): relations of acts published since W, counted as distinct new acts / distinct targets:

| Relation | New acts | Distinct targets |
|---|---|---|
| modifies | 43 | 42 |
| repeals | 143 | 144 |
| basedOn | 328 | 154 |
| cites | 45 | 206 |
| rectifies | 2 | 2 |
| transposes | 2 | 2 |

Use these to re-check targets' consolidations.

**Not usable:** The PROV log (`prov:generatedAtTime` on `…/prov/modifyEntity…`) **stops at 2022-02-07**.

**Late backfills.** Historic consolidations still appear late, for example `loi/1912/07/27/n1/consolide/19240716`, created 2026-09-10. Watermark on created/modified, never on dateApplicability.

---
## 7. Access to content files (VERIFIED, important)

`https://data.legilux.public.lu/robots.txt` contains:
```
User-agent: *
Disallow: /api/rss-adm.xml
Disallow: /eli/*
Disallow: /filestore/*
```
So automated bulk download of the `isExemplifiedBy` file URLs **is disallowed for crawlers**. The SPARQL endpoint is not disallowed.

Before reading robots.txt, I made 3 file GETs: 2 XML files plus 1 ranged request. After that I stopped.

**What the 3 requests showed:**
- `HEAD` gets 403. `GET` works and returns `Last-Modified`, `Accept-Ranges: bytes` (206 on Range) and `Access-Control-Allow-Origin: *`.
- The `http://` file URLs 302-redirect to `https://`.

**The website** (`legilux.public.lu`) is an Angular SPA.
- Its `/eli/...` paths return a 2,118-byte shell.
- The bundle reads `config.back_end.manifestation_prefix` and builds `filestore/...` URLs, so the browser uses the same filestore.
- It also calls `/zuul-proxy/casemates/api/...` and an Elasticsearch `_search` index named `data`. The details belong to another track (UNVERIFIED).

Permission from the SCL (Service central de législation) or an official dump is needed before ingesting full texts.
