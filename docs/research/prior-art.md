# prior-art

## Recommendations
- Use the official SPARQL endpoint (https://data.legilux.public.lu/sparqlendpoint) as the only discovery and metadata channel. Port SFHAJJI/lex's jolux:Consolidation catalogue, manifestation, licence, subject and owl:sameAs queries to Python, keeping page size 5000 and sorted paging under 10,000 rows, or use VALUES batches over POST.
- Download content files only from https://legilux.public.lu/filestore/... (rewrite the http://data.legilux.public.lu/filestore/ identifiers), never from data.legilux.public.lu/filestore or /eli. Fetch sequentially at about 1 req/s with a descriptive User-Agent, retries on 429/5xx, ETag/conditional GET and a 32 MB cap.
- Define 'current' as consolidations with no dateEndApplicability (1,214 today). Store future-dated or not-yet-applicable ones flagged, not as current. Add a second, rule-based branch for in-force LOI/RGD originals that were never consolidated (about 10k candidates) that excludes amending acts (jolux:modifies) and one-shot or temporary acts; agree the rule with the scope track.
- Write our own lxml parser for the CSD13 AKN dialect. Take identity from SPARQL, not FRBRthis. Use article id plus the scl:jolux uriThis article ELI as the chunk key and citation. Drop or flag any element with style="del" and all <del> content. Keep <passiveModifications>/<textualMod> sources as 'modified by' citations and <ref href> ELIs as cross-reference edges. Use recover mode for malformed files.
- Store XML only. Skip PDF, DOCX and HTML and all superseded versions to meet the 'no unnecessary data'. For the 91 current consolidations without XML, keep metadata and a source link with an explicit 'text unavailable' state instead of OCR-ing PDFs.
- Use SFHAJJI/lex-articles (61.6 MB parquet, CC-BY-4.0) once, outside the air gap, as a test oracle for our extractor (article counts, anchors, text similarity). Do not depend on it at runtime: one maintainer, last updated 2026-08-15. Treat legalize-lu, Ansvar, the endomorphosis/justicedao HF sets and lu-eli-mcp output as non-authoritative.
- Use the samusafe/gpassoni Fedlex RAG patterns for retrieval: Postgres + pgvector + FTS (or ParadeDB BM25) with bge-m3 embeddings, bge-reranker-v2-m3, RRF fusion, one-hop ELI cross-reference expansion, a deterministic citation verifier that keeps only citations present in retrieved chunks, and refusal when nothing is retrieved.
- Design the MCP tools after mindful-bio/mcp-fedlex and lex: every result carries a provenance block (ELI, consolidation URI, dateApplicability or valid_as_of, legilux.public.lu source URL, CC-BY attribution 'Ministère d'État – Service central de législation'), plus a coverage() tool that states known gaps. Tools are read-only and results are treated as data.
- For incremental sync, query SPARQL for consolidations by dcterms:created and for <uri>/graph nodes by dcterms:modified since the last run. Use data.legilux.public.lu/api/rss-leg.xml and rss-draft.xml only as cheap triggers. Adopt lex's anomaly gate: abort the update if the work count drops by more than 5%.
- Ship the JOLux ontology (jolux.ttl v6.11 from sparnatural-legilux-casemates) and the Legilux SKOS vocabularies (resource-type, application-status, legal-subject labels, all French) in the offline image, so the local LLM and maintainers can interpret metadata codes.
- Do not reuse yet-sparql-mcp-server (AGPL or commercial), cobalt or bluebell (LGPL, and they target AKN 3.0 final or text→AKN), or any code path that dereferences data.legilux.public.lu/eli. Fedlex SPARQL queries must be translated: there is no ConsolidationAbstract in Legilux, and the status vocabulary differs.

## Risks / open questions
- Scope gap: about 7.5k LOI and 9.2k RGD are flagged in-force but have no consolidation (about 5.0k and 5.5k when amending acts are excluded). Which of these are 'current and relevant' needs a deterministic rule, for example dropping temporary traffic RGDs and acts that only authorise purchases or budgets. Without it the corpus either misses real law or bloats.
- The 10,000-row sorted-result ceiling on Virtuoso is REPORTED by SFHAJJI/lex and cyanheads, not tested by me. Page conservatively.
- Filestore HEAD returning 403 and the duplicate-attribute malformed XML are REPORTED by others, not verified. The ~31 MB maximum file size is REPORTED by lex.
- I verified the repealed-article markup (style="del" / <del>) on one consolidation only. Other markers (inserted text, notes, annexes, tables in large codes) need a broader sample from the parsing track.
- The legilux.public.lu robots.txt does not disallow /filestore today, and lex relies on that. A future robots change could block content download, so the pipeline should check robots.txt before each run.
- 38 current consolidations have no inForceStatus and 55 works have no typeDocument. Decide how to label these in answers.
- Multilingual expressions exist (the Constitution has de/fr/lb; the lex-corpus manifest lists de/en/fr/lb). Current XML is FRA only per my query, but non-French expressions may exist without XML. Decide whether to keep FR only.
- The licence is CC-BY-4.0 per the manifestation triples and the dataset (VERIFIED), but Ansvar's sources.yml argues text reuse rests on an exception in the copyright law of 18 April 2001 (Art. 10, 8°). Attribution must travel into every MCP response. Have counsel confirm if the clone is redistributed.
- lex-articles and lex-corpus show the maintainer's nightly job stopped updating after 2026-08-15 (the live catalogue is 4,719 versus their 4,656). Any plan to seed from them must reconcile against live SPARQL.
- I did not test Fedlex endpoint behaviour or Fedlex AKN details (eId versus id, namespace). Statements about Fedlex differences beyond the absent ConsolidationAbstract are REPORTED or UNVERIFIED.
- The internal Elasticsearch backing the Legilux SPA search is disallowed (/search) and undocumented. We cannot reuse official full-text ranking, so local search quality depends entirely on our own index.

## Verified facts
- data.legilux.public.lu robots.txt disallows /eli/* and /filestore/* (and /api/rss-adm.xml), so RDF dereference and filestore downloads on the data host are off-limits  
  _evidence:_ curl https://data.legilux.public.lu/robots.txt -> 200 text/plain: 'User-agent: *\nDisallow: /api/rss-adm.xml\nDisallow: /eli/*\nDisallow: /filestore/*'
- legilux.public.lu robots.txt does not disallow /filestore, and the consolidated AKN XML is downloadable from the main host  
  _evidence:_ curl https://legilux.public.lu/robots.txt (Disallow list: /publications-regroupees, /api/rss-adm.xml, /eli/etat/adm/, /reg_ue/, /dir_ue/, /search, /*.svg, /*.docx + specific URIs). GET https://legilux.public.lu/filestore/eli/etat/leg/loi/2018/08/01/a686/consolide/20251226/fr/xml/eli-etat-leg-loi-2018-08-01-a686-consolide-20251226-fr-xml.xml -> 200 application/xml 140421B
- The SPARQL endpoint accepts both GET and POST (form-encoded); /sparql is only an Angular SPA shell  
  _evidence:_ POST --data-urlencode query='...SELECT (COUNT(?c) AS ?n) WHERE { ?c a jolux:Consolidation }' https://data.legilux.public.lu/sparqlendpoint -> HTTP 200 application/sparql-results+json, n=4719. GET https://data.legilux.public.lu/sparql?query=... -> 200 text/html 698B containing <title>Casemates</title><app-root>
- There are 4,719 jolux:Consolidation resources across 1,416 works; 1,214 consolidations (1,213 works) have no dateEndApplicability  
  _evidence:_ SPARQL: SELECT (COUNT(?c) AS ?n) (COUNT(DISTINCT ?w) AS ?works) WHERE { ?c a jolux:Consolidation ; jolux:isMemberOf ?w } -> n=4719 | works=1416; same with FILTER NOT EXISTS { ?c jolux:dateEndApplicability ?e } -> n=1214 | works=1213
- Among current (open-ended) consolidations: 1,162 applicable, 14 not-yet-applicable, 38 without inForceStatus; 34 consolidations have dateApplicability after 2026-10-16  
  _evidence:_ SPARQL GROUP BY ?status over open-ended consolidations -> applicable 1162, (none) 38, not-yet-applicable 14; SELECT COUNT where dateApplicability > "2026-10-16"^^xsd:date -> 34
- Current consolidations by type (distinct works): RGD 541, LOI 494, untyped 55, RECUEIL 37, AGD 16, RMIN 14, CODE_RECUEIL 8, RGC 7, AMIN 7, CODE 6, PA 6  
  _evidence:_ SPARQL SELECT ?type (COUNT(DISTINCT ?w)) ... FILTER NOT EXISTS dateEndApplicability GROUP BY ?type
- 1,123 current consolidations have a French XML manifestation; 91 have no XML at all  
  _evidence:_ SPARQL with ?m jolux:userFormat <.../user-format/xml> GROUP BY ?lang -> lang=.../language/FRA withXml=1123; FILTER NOT EXISTS xml manifestation -> noXml=91
- Legilux has no jolux:ConsolidationAbstract class (unlike Fedlex); jolux:Code=916, ComplexWork=151,631, Article=58,673  
  _evidence:_ SPARQL SELECT ?c (COUNT(?s)) WHERE { ?s a ?c . FILTER(... CONTAINS Consol|Complex|Article|Code) } GROUP BY ?c -> Consolidation 4719, Code 916, ComplexWork 151631, Article 58673 (no ConsolidationAbstract row)
- Each consolidation's own triples include dcterms:created, publicationDate, dateApplicability, inForceStatus, isMemberOf and typeDocument, and its /graph node carries dcterms:modified, so both can drive incremental sync  
  _evidence:_ SPARQL SELECT ?p ?o WHERE { <http://data.legilux.public.lu/eli/etat/leg/loi/2018/08/01/a686/consolide/20251226> ?p ?o } -> dcterms:created 2026-01-08T09:59:17.373+01:00, publicationDate 2026-01-08, dateApplicability 2025-12-26, inForceStatus .../application-status/applicable, isMemberOf .../a686; <.../20251226/graph> dcterms:modified 2026-04-23T18:38:48.622+02:00
- 24 consolidations were created in the last 30 days (small incremental delta)  
  _evidence:_ SPARQL COUNT ?c a jolux:Consolidation ; dcterms:created ?cr FILTER(?cr >= "2026-09-16T00:00:00"^^xsd:dateTime) -> n=24
- Consolidation manifestations carry a CC-BY-4.0 jolux:license and use the http://data.legilux.public.lu/filestore/... identifier pattern for xml/pdf/docx/html  
  _evidence:_ SPARQL on isMemberOf <.../loi/2018/08/01/a686> open-ended consolidation -> file=http://data.legilux.public.lu/filestore/eli/etat/leg/loi/2018/08/01/a686/consolide/20251226/fr/xml/eli-etat-leg-loi-2018-08-01-a686-consolide-20251226-fr-xml.xml | lic=http://creativecommons.org/licenses/by/4.0/ (also pdf, docx, html rows)
- Consolidated AKN uses the CSD13 draft namespace with the scl extension, id= attributes (not eId), and per-article JOLux metadata with article-level ELIs  
  _evidence:_ Downloaded sample root: <akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0/CSD13" xmlns:scl="http://www.scl.lu"><act contains="originalVersion">; article id="art_2" contains <scl:jolux scl:name="uriThis">http://data.legilux.public.lu/eli/etat/leg/loi/2018/08/01/a686/art_2/20180820</scl:jolux> and dateApplicability 2018-08-20; 78 articles
- Consolidated AKN keeps repealed provisions, marked with style="del" and <del>, and links the amending act via passiveModifications/textualMod  
  _evidence:_ a686 consolide 20251226 XML: <article id="art_56" style="del"> ... <del>Art. 56.</del> ... <del>Il est créé une administration dénommée « Commissariat du Gouvernement ...</del>; 14 style=del elements, 30 <del>; <textualMod id="pm1" type="substitution"><source href="/eli/etat/leg/loi/2025/12/19/a619/jo"/>
- The consolidated XML's FRBR metadata points to the original /jo work, not the consolidation URI  
  _evidence:_ Same file: <FRBRWork><FRBRthis value="http://data.legilux.public.lu/eli/etat/leg/loi/2018/08/01/a686/jo"/> and <act contains="originalVersion"> despite being the consolide/20251226 manifestation
- legalize-lu (HEAD d1228d0) shows the repealed Art. 56 of loi 2018-08-01 a686 as live text, so its HEAD is not a reliable current-law source  
  _evidence:_ git clone --depth 1 legalize-dev/legalize-lu; lu/leg-loi-2018-08-01-a686.md line 428: '##### Art. 56.' followed by 'Il est créé une administration dénommée « Commissariat du Gouvernement à la protection des données auprès de l’État »...', frontmatter last_updated: "2018-08-01"
- Act-level inForceStatus counts: LOI in-force 8,060 / no-longer 1,199 / not-yet 6; RGD in-force 9,747 / no-longer 5,630 / not-yet 2  
  _evidence:_ SPARQL SELECT ?type ?st (COUNT(DISTINCT ?act)) WHERE { VALUES ?type {LOI RGD} ?act a jolux:Act ; jolux:typeDocument ?type . OPTIONAL { ?act jolux:inForceStatus ?st } } GROUP BY ?type ?st
- 7,533 LOI and 9,184 RGD are flagged in-force but have no consolidation in their ComplexWork; excluding acts with jolux:modifies, 4,965 LOI and 5,499 RGD remain  
  _evidence:_ SPARQL ... jolux:inForceStatus <.../in-force> ; jolux:isMemberOf ?cw . FILTER NOT EXISTS { ?c a jolux:Consolidation ; jolux:isMemberOf ?cw } GROUP BY ?type -> LOI 7533, RGD 9184; adding FILTER NOT EXISTS { ?act jolux:modifies ?m } -> LOI 4965, RGD 5499
- Allowed RSS feeds exist on the data host: rss-leg.xml (Mémorial A legislation), rss-draft.xml (draft laws), rss.xml (combined); each returns 50 items  
  _evidence:_ GET https://data.legilux.public.lu/api/rss-leg.xml -> 200 31816B, 50 items, newest 2026-10-15 'Règlement ministériel du 1er octobre 2026 ...' http://data.legilux.public.lu/eli/etat/leg/rmin/2026/10/01/a495/jo, oldest 2026-08-31; rss-draft.xml -> 200 38408B, items like http://data.legilux.public.lu/eli/dl/pl/2026/74; rss.xml -> 200 26166B. Guessed legilux.public.lu/api/rss-leg.xml -> 404 JSON
- The legilux.public.lu front end is an Angular SPA backed by an internal Elasticsearch _search API (not a public API; /search is disallowed)  
  _evidence:_ GET https://legilux.public.lu/ -> 2118B shell with <app-root>; main-es2015.3e682525a3b5c44f5589.js (2,044,191B) contains 'baseUrl}/${e.index}/_search' and the application-status vocabulary list (applicable, in-force, indeterminate-status, no-longer-in-force, no-longer-in-force-implicit, not-applicable, not-yet-applicable)
- The data.public.lu Legilux dataset (62c83bfd9794ec8e47b5bc68) is cc-by, frequency continuous, and lists only 'Technical documentation' and 'SPARQL endpoint' resources (no bulk dump); it has exactly 2 reuses  
  _evidence:_ GET https://data.public.lu/api/1/datasets/62c83bfd9794ec8e47b5bc68/ -> license cc-by, frequency continuous, resources: https://data.legilux.public.lu/ (html), https://data.legilux.public.lu/sparql (sparql). GET /api/1/reuses/?dataset=... -> total 2: 'Lex: Luxembourg law as it stood on any date' (law.soufien.lu), 'Lux.memorial' (www.lux.memorial)
- SFHAJJI/lex-articles publishes a 61.6 MB per-provision parquet (CC-BY-4.0) covering 1,334 LU works / 3,168 versions / 96,793 anchors, last modified 2026-08-15  
  _evidence:_ curl -sSI -L https://github.com/SFHAJJI/lex-articles/releases/latest/download/lu-legilux-provisions.parquet -> 200 Content-Length: 61644220, Last-Modified: Sat, 15 Aug 2026 08:43:42 GMT; raw catalog.json (1,102,328B) -> Counter lu-legilux 1334, eu-eurlex 1250; lu derived_versions 3168 anchors 96793; LICENSE 'Attribution 4.0 International'
- SFHAJJI/lex-corpus-lu-legilux manifest: 1,402 works, 4,656 versions, 3,167 expressions with text / 1,493 without; licence CC-BY-4.0; last commit 2026-08-15  
  _evidence:_ git clone --filter=blob:none; manifest.json: "works": 1402, "versions": 4656, "expressions_with_text": 3167, "expressions_without_text": 1493; git log -1 -> c087f91 Sat Aug 15 00:35:34 2026; GitHub search shows license cc-by-4.0, size 491747
- SFHAJJI/lex (Apache-2.0) queries jolux:Consolidation via POST with a 1500 ms pause, caps sorted paging at 10,000 rows, and fetches bodies from legilux.public.lu/filestore  
  _evidence:_ src/Lex.Sources.Legilux/SparqlClient.cs: 'internal const int SortedTopMaximum = 10_000;', '_pause = pause ?? TimeSpan.FromMilliseconds(1500)', HttpMethod.Post FormUrlEncodedContent; LegiluxAdapter.cs: catalogue query '?c a jolux:Consolidation ; jolux:isMemberOf ?work ; jolux:dateApplicability ?from' pageSize 5000, maximumRows 20_000; 'https://legilux.public.lu' + uri.AbsolutePath rewrite; BodyCapBytes = 32 MB
- matematicsolutions/lu-eli-mcp (Apache-2.0, PyPI 0.3.3) fetches via RDF dereference on data.legilux.public.lu/{eli} and the data-host filestore, and wrongly states there is no HTTP SPARQL  
  _evidence:_ client.py: DEFAULT_BASE_URL = 'https://data.legilux.public.lu'; get_rdf -> f'{self.base_url}/{eli_path}' Accept application/rdf+xml; DISCOVERY.md: 'No HTTP SPARQL: POST /sparql returns 405, GET /sparql?query= returns the SPA'; PyPI JSON lu-eli-mcp 0.3.3 uploaded 2026-08-24
- Ansvar-Systems/Luxembourg-law-mcp (Apache-2.0, archived) ingests only original jo texts of LOI and RGD, with no consolidation handling, from the data-host filestore  
  _evidence:_ scripts/ingest.ts DOC_TYPES = ['LOI','RGD']; query '?act a jolux:Act ; ... jolux:userFormat <.../user-format/xml>'; grep -i consolid in scripts/ and src/tools -> no matches; fetcher.ts buildXmlUrl -> https://data.legilux.public.lu/filestore/...; COVERAGE.md 4,551 laws, 36,014 provisions
- The JOLux ontology v6.11 (11/6/2024) is available as a 747 KB Turtle file in sparna-git/sparnatural-legilux-casemates and states it was extended from 2019 for Switzerland  
  _evidence:_ jolux/jolux.ttl (747164B): owl:Ontology with rdfs:label 'Version 6.11 11/6/2024' and rdfs:comment '...Conçue initialement pour le Luxembourg entre 2015 et 2019, l'ontologie a été enrichie à partir de 2019 pour couvrir aussi les besoins de la Suisse.'; 258 owl:Class matches; config.ttl has 49 sh:NodeShape
- The HF dataset endomorphosis/ipfs_luxembourg_laws (CC-BY-4.0) is built from pdftotext of Mémorial PDFs and wrongly says the SPARQL endpoint is an Angular shell  
  _evidence:_ https://huggingface.co/datasets/endomorphosis/ipfs_luxembourg_laws/raw/main/README.md: 'Source: data.legilux RDF + JO/Mémorial PDFs (pdftotext). SPARQL UI is an Angular shell, unused.' Laws 1,354, Articles 38,335, snapshot 2026-09-03
- Library and model licences and versions for the candidate stack  
  _evidence:_ PyPI JSON: mcp 2.3.0 (2026-10-02, MIT); fastmcp 4.1.0 (Apache); rdflib 7.6.0 (BSD-3); lxml 6.1.3 (BSD-3); bluebell-akn 5.0.0 (LGPLv3+); cobalt 9.0.1 (LGPLv3+); legi 0.6 (CC0). HF API: BAAI/bge-m3 license mit; BAAI/bge-reranker-v2-m3 apache-2.0; OrdalieTech/Solon-embeddings-large-0.1 mit; intfloat/multilingual-e5-large mit; maastrichtlawtech/bsard cc-by-nc-sa-4.0; maastrichtlawtech/lleqa cc-by-nc-sa-4.0 gated manual
- Current Docker image tags for candidate infrastructure  
  _evidence:_ hub.docker.com/v2 tags: pgvector/pgvector 0.8.7-pg17-trixie / 0.8.7-pg18-trixie (2026-10-01); qdrant/qdrant v1.19.2 (2026-10-05); ollama/ollama 0.40.5 (2026-10-15); paradedb/paradedb 0.26.2-pg18 (2026-10-16)
- samusafe/swiss-legal-rag (MIT) is a local-first Fedlex RAG reference using docker compose, PostgreSQL+pgvector, FTS, RRF, a cross-encoder reranker and Ollama bge-m3 + qwen2.5:3b-instruct  
  _evidence:_ raw README.md: 'Combines pgvector cosine search, PostgreSQL full-text search, reciprocal-rank fusion, and a cross-encoder reranker'; 'docker compose up -d ... ollama pull bge-m3; ollama pull qwen2.5:3b-instruct'; License MIT

## Report

# Prior art and reusable components for the LegiLux local MCP clone

Research date: 2026-10-16. Live checks were rate-limited to about 1 request per 1.2 s, used a descriptive User-Agent, followed robots.txt and fetched one sample per kind. GitHub repositories were read with `git clone --depth 1 --filter=blob:none` and raw.githubusercontent.com, because the GitHub MCP/API was scoped to this repo only. The scratch area is `<research-scratch>/prior-art/`, with `bin/sparql.sh` (the polite SPARQL helper), `repos/`, `readmes/` and `dl/`.

Labels used below:
- **VERIFIED**: I ran it or read it myself today.
- **REPORTED**: another project says so and I did not re-test it.
- **UNVERIFIED**: from memory.

---

## 0. Summary

1. **Someone has already solved most of the Legilux ingestion problem: SFHAJJI's "Lex" stack.** Its parts:
   - `SFHAJJI/lex`: engine, Apache-2.0, .NET 10.
   - `SFHAJJI/lex-corpus-lu-legilux`: verbatim Akoma Ntoso (AKN) evidence, CC-BY-4.0.
   - `SFHAJJI/lex-articles`: per-article JSON, Markdown and Parquet, CC-BY-4.0.

   It uses the right source boundary (`jolux:Consolidation`), the right fetch host (`legilux.public.lu/filestore`, which robots.txt permits) and correct paging limits. We should copy its SPARQL queries and its schema ideas. We should not depend on its artefacts at runtime: one maintainer, last data commit 2026-08-15, and index builds are "redistribution reserved".
2. **Most other Legilux tools break robots.txt or get "current law" wrong.**
   - `data.legilux.public.lu/robots.txt` disallows `/eli/*` and `/filestore/*` (VERIFIED). lu-eli-mcp, Ansvar, legalize-pipeline and clarvia all fetch from `data.legilux.public.lu/filestore` or dereference `/eli`.
   - Ansvar indexes only the original `/jo` texts of LOI and RGD. That is not the law in force.
3. **Consolidated AKN keeps repealed text in the file** (VERIFIED). Repealed articles carry `style="del"` and their text is wrapped in `<del>`. A naive extractor serves repealed text as current law. legalize-lu does exactly this for Art. 56 of the 2018 CNPD law.
4. **Fedlex (Switzerland) uses the same JOLux ontology**, but the instance data differs. Legilux has no `jolux:ConsolidationAbstract` (VERIFIED: 0 instances) and its status vocabulary is different. Fedlex projects are useful for architecture patterns (local RAG, hybrid search, citation verification, MCP tool design), not as drop-in queries.
5. **There is no official REST API or bulk dump.** What exists:
   - the SPARQL endpoint (GET and POST both work);
   - per-file downloads from the filestore;
   - three allowed RSS feeds on `data.legilux.public.lu/api/` (`rss-leg.xml`, `rss-draft.xml`, `rss.xml`; `rss-adm.xml` is disallowed).

   For incremental sync, the consolidation's `dcterms:created` and the `<uri>/graph` node's `dcterms:modified` are better signals than RSS.

---

## 1. Legilux-specific assets (ranked by usefulness)

### 1.1 SFHAJJI/lex (engine). Rating: high (learn from it, maybe borrow code)
- **Repo and maturity:** https://github.com/SFHAJJI/lex, Apache-2.0 (LICENSE checked). C#/.NET 10, about 632 files, HEAD 9337935 (2026-09-03).
- **What it does:** builds SQLite FTS5/BM25 indexes, with optional hybrid search using a pinned local encoder. Serves read-only MCP tools (`Lex.Mcp.Stdio`, `Lex.Web`). Hosted at law.soufien.lu/mcp.
- **Legilux adapter** (`src/Lex.Sources.Legilux/LegiluxAdapter.cs`, `SparqlClient.cs`), VERIFIED by reading:
  - Endpoint `https://data.legilux.public.lu/sparqlendpoint`; POST form-encoded, sequential, 1500 ms pause, 4 retry attempts, 120 s body timeout, User-Agent `Lex/0.1 (+https://github.com/SFHAJJI/lex)`.
  - Catalogue query (page size 5000, hard cap 20,000 rows):
    ```sparql
    SELECT ?c ?work ?type ?from ?to ?status ?pub ?expr ?lang ?title ?titleShort WHERE {
      ?c a jolux:Consolidation ; jolux:isMemberOf ?work ; jolux:dateApplicability ?from .
      OPTIONAL { ?c jolux:typeDocument ?type } OPTIONAL { ?c jolux:dateEndApplicability ?to }
      OPTIONAL { ?c jolux:inForceStatus ?status } OPTIONAL { ?c jolux:publicationDate ?pub }
      OPTIONAL { ?c jolux:isRealizedBy ?expr . OPTIONAL { ?expr jolux:language ?lang }
                 OPTIONAL { ?expr jolux:title ?title } OPTIONAL { ?expr jolux:titleShort ?titleShort } }
    } ORDER BY ?c ?expr LIMIT {limit} OFFSET {offset}
    ```
  - Manifestation query: `?c a jolux:Consolidation ; jolux:isRealizedBy ?expr . ?expr jolux:isEmbodiedBy ?m . ?m jolux:userFormat ?fmt ; jolux:isExemplifiedBy ?file` with `VALUES ?fmt {…/user-format/xml …/user-format/pdf}`.
  - Licence query: a `VALUES ?m {…}` batch with `jolux:license`.
  - Subject query uses `jolux:subjectLevel1/2` with SKOS labels; identity query uses `owl:sameAs`.
  - It rewrites `http://data.legilux.public.lu/filestore/...` to `https://legilux.public.lu/filestore/...` (the robots-permitted host).
  - Body cap is 32 MB. The comment says the largest file is about 31 MB (`rgd-2016-04-27-n4` versions), with others at 9.2 and 10.4 MB.
  - `SortedTopMaximum = 10_000`: "The Legilux Virtuoso endpoint cannot verify a sorted result beyond 10000 rows; use a bounded VALUES batch" (REPORTED limit).
- **Docs worth reading:** `docs/luxembourg-scope.md`, `docs/legilux-subject-taxonomy.md` and `docs/known-defects.md`. Points they make:
  - The `Act` class is not a safe ingestion boundary.
  - Subject labels exist in French only.
  - There is one malformed-XML defect (a duplicate `scl:cols-nb` attribute).
  - Some consolidations have empty provisions.
  - The Constitution has de/fr/lb expressions, which caused a title-language bug.
- **How we would use it:** port its queries and paging rules to Python, and adopt its ideas: an anomaly gate (abort if the work count drops by more than 5%), a determinism guard, and refusing to fabricate text. The stack is too heavy to adopt whole (.NET, DirectML embeddings, signed Azure artefacts).

### 1.2 SFHAJJI/lex-articles (derived dataset). Rating: high (bootstrap and test oracle, not the source of truth)
- **Repo:** https://github.com/SFHAJJI/lex-articles, CC-BY-4.0 data, `lex-articles/1` schema (SCHEMA.md).
- **Format:** per-provision JSON/Markdown with publisher article ELIs, `article_valid_from` and `md_span`, plus `history.json` per anchor.
- **Size and coverage (VERIFIED):**
  - Release asset `lu-legilux-provisions.parquet` is 61,644,220 bytes, Last-Modified 2026-08-15 (HTTP HEAD via the releases/latest redirect).
  - `catalog.json` is 1,102,328 bytes: 1,334 LU works, 3,168 derived LU versions, 96,793 LU anchors (plus 1,250 EU works).
- **How we would use it:**
  - Download once, outside the air gap, as a cross-check or golden set for our own AKN extractor (article counts, anchors, text hashes).
  - Possibly use it as a quick-start seed filtered to the latest version per work.
  - Its provision schema (anchor, ELI, path, valid_from, text_md) is a good model for our `provisions` table.
- **Caveats:**
  - It includes every historical version, so we must filter.
  - It is stale relative to live data: 4,656 versions versus 4,719 live consolidations today.
  - It covers consolidations only. Original acts that were never consolidated are absent.

### 1.3 SFHAJJI/lex-corpus-lu-legilux (verbatim evidence). Rating: medium
- **Repo:** https://github.com/SFHAJJI/lex-corpus-lu-legilux, CC-BY-4.0, NOTICE with a three-layer attribution.
- **Size and coverage (VERIFIED):** GitHub reports size 491,747 KB; 9,249 files.
- **Manifest (VERIFIED):**
  - 1,402 works, 4,656 versions, 4,660 expressions, of which 3,167 have text and 1,493 do not.
  - Languages de/en/fr/lb; validity range 1849-03-14 to 2030-09-15.
  - Last commit 2026-08-15, although the README says it runs nightly.
- **Layout:** `works/<slug>/versions/<valid_from>--<sha256>/{meta.json, fr.xml | fr.pdf-memorial/...}`. It includes Mémorial PDFs, which we want to avoid storing.
- **How we would use it:** as a reference for the identity scheme and the metadata JSON. We do not need its data, since it carries every version and PDFs.

### 1.4 matematicsolutions/lu-eli-mcp. Rating: low-medium (small, clean Python reference; data path breaks robots.txt)
- **Repo and package:** https://github.com/matematicsolutions/lu-eli-mcp, Apache-2.0, PyPI `lu-eli-mcp` 0.3.3 (2026-08-24), HEAD 14a9017 (2026-09-24). Its Dockerfile is `FROM python:3.11-slim` + `pip install lu-eli-mcp`.
- **Tools:** `lu_get_act(eli)`, `lu_get_text(eli, language, file_format)`, `lu_coverage()`.
- **Mechanism:** `GET https://data.legilux.public.lu/{eli}` with `Accept: application/rdf+xml`, then filestore download. `data.legilux.public.lu/robots.txt` disallows both (VERIFIED), so we must not copy this access path.
- **Wrong claim:** its DISCOVERY.md says "No HTTP SPARQL". It probed `/sparql` (an SPA) instead of `/sparqlendpoint`.
- **Reusable pieces:**
  - `citations.py`: a stdlib ElementTree jolux RDF/XML parser and an ELI normaliser.
  - Fixtures `act_loi_2018.rdf` (11.7 KB) and `act_loi_2018_text.xml`.
  - The citation contract (`eli_uri`, `human_readable_citation`, `source_url`).

### 1.5 Ansvar-Systems/Luxembourg-law-mcp. Rating: low (anti-pattern for "current law"; useful TypeScript MCP scaffolding)
- **Repo:** https://github.com/Ansvar-Systems/Luxembourg-law-mcp, Apache-2.0, **archived**, HEAD bd41a25 (2026-07-07). Fork at pepe57/Luxembourg-law-mcp.
- **Design:** SQLite FTS5 (about 69 MB), 4,551 acts and 36,014 provisions.
- **Discovery query** (`scripts/ingest.ts`): `?act a jolux:Act ; jolux:typeDocument …/LOI|RGD ; jolux:isRealizedBy ?expr … ?manifest jolux:userFormat …/user-format/xml`, paged 5000 with OFFSET.
- **Problems:**
  - It ingests only the original `/jo` text and has no consolidation handling (grep for "consolid" finds nothing in scripts).
  - It marks everything `in_force`.
  - It fetches from `data.legilux.public.lu/filestore`, which robots.txt disallows.
- **Reusable piece:** the AKN parser (`scripts/lib/parser.ts`, fast-xml-parser, isArray list for `article/alinea/paragraph/p/li/scl:jolux`). It shows the structure we must handle.

### 1.6 legalize-dev/legalize-lu and legalize-pipeline. Rating: low-medium
- **Repos:** https://github.com/legalize-dev/legalize-lu (data CC-BY-4.0, updated 2026-10-12, HEAD d1228d0, 19,798 Markdown files: 12,449 RGD, 7,308 LOI, Constitution). The pipeline is https://github.com/legalize-dev/legalize-pipeline (MIT, Python).
- **Design:** one Markdown file per norm and one git commit per consolidation.
- **Useful notes:** `research/RESEARCH-LU.md` has good AKN element inventory notes: `ref`, `note`, `mod`, `textualMod`, `passiveModifications`, `embeddedStructure`, no tables or images. It also has SPARQL patterns.
- **Wrong claims:**
  - It says "GET only". POST works (VERIFIED).
  - It uses the `data.legilux.public.lu/filestore` host, which robots.txt disallows.
- **Current-law risk (VERIFIED):** `lu/leg-loi-2018-08-01-a686.md` at HEAD still shows **Art. 56** as live text, with frontmatter `last_updated: "2018-08-01"`. The live consolidation `…/a686/consolide/20251226` marks Art. 56 `style="del"`, repealed by loi 2025-12-19 a619.
- **Use:** only as a cross-check.

### 1.7 sparna-git/sparnatural-legilux-casemates. Rating: medium (schema documentation for the offline LLM)
- **Repo:** https://github.com/sparna-git/sparnatural-legilux-casemates, HEAD a47bff9 (2025-07-08). No licence file seen (UNVERIFIED).
- **Contents (VERIFIED):**
  - `jolux/jolux.ttl` (747,164 bytes): the full Legilux **JOLux ontology v6.11 (11/6/2024)**, about 258 `owl:Class` and about 988 property declarations. The `rdfs:comment` states it was "enrichie à partir de 2019 pour couvrir aussi les besoins de la Suisse" (extended from 2019 to cover Swiss needs too).
  - `config.ttl`: 49 SHACL NodeShapes of the Legilux graph, including treaty, draft, memorial and vocabulary shapes.
  - Example queries.
- **How we would use it:** ship `jolux.ttl` and a trimmed SHACL summary into the air-gapped image so the LLM and developers can understand fields. Optionally load it into a local triple store for a "raw SPARQL" tool.

### 1.8 Smaller and lower-value items
- **amirdaraee/luxembourg-mcp** (MIT, PyPI `luxembourg-mcp`): 44 tools. `search_legislation` is a live `FILTER(CONTAINS(LCASE(STR(?title)), …))` SPARQL title search. Fine as a pattern for a "remote fallback" tool; useless offline.
- **clarvia-org/clarvia-graph** `lex/countries/lu/CASEMATES.md`: good operational notes.
  - Codes use `eli/etat/leg/code/<name>/<YYYYMMDD>` without `/consolide/`.
  - Filestore HEAD returns 403 (REPORTED).
  - Both `eli:` namespaces appear.
  - HTML manifestation CSS hooks: `div#art_N`, `p.richtext_num_article`.
  - It claims POST is unreliable. My one POST with `Accept: application/sparql-results+json` worked.
- **worldwidelaw/legal-sources** `sources/LU/LegalDatabase`: fetches HTML manifestations and extracts text. A simpler but lossier approach.
- **yet-market/yet-sparql-mcp-server**: generic SPARQL MCP configured for Legilux. AGPL-3.0 or commercial licence, so avoid.
- **Hugging Face datasets:**
  - `endomorphosis/ipfs_luxembourg_laws` (CC-BY-4.0, snapshot 2026-09-03): 1,354 instruments and 38,335 articles, but text comes from **pdftotext of Mémorial PDFs**. Its README wrongly says "SPARQL endpoint is an Angular app-root shell".
  - `justicedao/ipfs_luxembourg_laws_ir` (license "other"): BM25, vectors and graph parquet built from it.
  - `ehoai/lu-legal-legilux`: README only, no data.
  - None are suitable as a source of truth.
- **data.public.lu reuses** of dataset 62c83bfd9794ec8e47b5bc68 (VERIFIED via API): exactly two, "Lex" (law.soufien.lu) and "Lux.memorial" (www.lux.memorial, "IA consultant directement le Journal officiel", a commercial app; its code was not found).
- **cgilois/https-legilux.public.lu-rss-memorial**: an empty repo (size 1).
- **dataspace4health-lu/edc-mdc** `app/services/legilux/client.py`: an httpx POST SPARQL client. More evidence that POST works.
- **SFHAJJI/energy-copilot** `probes/legilux_applicability_probe.py`: a probe for in-force acts that are not consolidated. Its query has a bug: it joins `?cons jolux:isMemberOf ?act` against the `/jo` act instead of the ComplexWork.

---

## 2. Official Legilux interfaces (all VERIFIED live today)

| Interface | URL | Status, robots.txt and notes |
|---|---|---|
| SPARQL (Virtuoso) | `https://data.legilux.public.lu/sparqlendpoint` | GET `?query=` and POST form-encoded both return `application/sparql-results+json`; 0.4–1.5 s for aggregate queries; allowed by robots.txt. |
| SPARQL UI | `https://data.legilux.public.lu/sparql` | 698-byte Angular shell (`<title>Casemates</title><app-root>`). Not an API. |
| Content files | `https://legilux.public.lu/filestore/eli/.../consolide/YYYYMMDD/fr/xml/eli-...-fr-xml.xml` | Sample a686 consolidation: 200, `application/xml`, 140,421 B. Allowed on the main host. The same path on `data.legilux.public.lu` is disallowed (`Disallow: /filestore/*`). |
| RDF dereference | `data.legilux.public.lu/eli/...` | `Disallow: /eli/*`. Do not use; use SPARQL. |
| RSS | `https://data.legilux.public.lu/api/rss-leg.xml` | 200, 31,816 B, 50 items, 2026-08-31 to 2026-10-15. Covers Mémorial A legislation (`/jo` URIs). |
| RSS | `https://data.legilux.public.lu/api/rss-draft.xml` | 200, 38,408 B, 50 items. Draft laws (`eli/dl/pl/...`). |
| RSS | `https://data.legilux.public.lu/api/rss.xml` | 200, 26,166 B. Combined feed, dominated by Mémorial B. |
| RSS | `rss-adm.xml` | Disallowed on both hosts. Not used. |
| Front-end search | `legilux.public.lu/search` | Internal Elasticsearch (`${baseUrl}/${index}/_search` in `main-es2015.*.js`). `Disallow: /search`. Do not use. |
| Vocabularies | `http://data.legilux.public.lu/vocabulaires` | Listed in the dataset description; SKOS also available via SPARQL. |
| Dataset | data.public.lu dataset `62c83bfd9794ec8e47b5bc68` | Licence `cc-by`, frequency `continuous`. Resources are only "Technical documentation" and "SPARQL endpoint". No bulk dump. |

There is no bulk dump and no REST API. Guessed `legilux.public.lu/api/rss*.xml` paths return 404 JSON (VERIFIED). The real feeds are on the data host.

**Incremental-sync signals (VERIFIED on `…/a686/consolide/20251226`):**
- The consolidation node has `dcterms:created 2026-01-08T09:59:17`, `jolux:publicationDate 2026-01-08`, `jolux:dateApplicability 2025-12-26`, `inForceStatus …/applicable`, `isMemberOf …/a686` and `typeDocument LOI`.
- The `…/consolide/20251226/graph` node has `dcterms:modified 2026-04-23T18:38:48`.
- 24 consolidations had `dcterms:created >= 2026-09-16`, so the 30-day delta is small.

---

## 3. Live corpus numbers that matter for "current and relevant" scope (VERIFIED SPARQL)

| Query | Result |
|---|---|
| `jolux:Consolidation` (with `isMemberOf`) | 4,719 consolidations, 1,416 works |
| … with no `dateEndApplicability` (current) | 1,214 consolidations, 1,213 works |
| Status of those current consolidations | applicable 1,162; not-yet-applicable 14; **no status 38** |
| `dateApplicability > 2026-10-16` | 34 (future-dated) |
| Current consolidations by `typeDocument` (works) | RGD 541, LOI 494, **none 55**, RECUEIL 37, AGD 16, RMIN 14, CODE_RECUEIL 8, RGC 7, AMIN 7, CODE 6, PA 6, others ≤4 |
| Current consolidations with FR XML | 1,123 (all `language/FRA`); **91 have no XML manifestation** |
| Other classes | `jolux:Code` 916, `jolux:ComplexWork` 151,631, `jolux:Article` 58,673; `ConsolidationAbstract` absent |
| LOI acts by `inForceStatus` | in-force 8,060; no-longer-in-force 1,199; not-yet-in-force 6 |
| RGD acts by `inForceStatus` | in-force 9,747; no-longer 5,630; not-yet 2 |
| In-force LOI/RGD whose ComplexWork has **no** consolidation | LOI 7,533, RGD 9,184 |
| … of which not themselves amending acts (no `jolux:modifies`) | LOI 4,965, RGD 5,499 |

So the consolidated catalogue (about 1,213 current works) is the core. The plan still needs a rule for the roughly 10k in-force LOI/RGD that were never consolidated. Many are one-shot acts (property purchases, temporary traffic RGDs), but some are real current law. SFHAJJI's scope doc reaches the same conclusion.

---

## 4. Consolidated AKN facts our parser must handle (VERIFIED on one 140 KB sample)

- **Namespace:** `http://docs.oasis-open.org/legaldocml/ns/akn/3.0/CSD13` plus `xmlns:scl="http://www.scl.lu"`. This is the 2013 draft namespace, not AKN 3.0 final, so generic AKN libraries such as cobalt may not match. Elements use `id=` (for example `art_2`, `art_1er`), not `eId`.
- **Metadata block:** `<act contains="originalVersion">` with FRBRthis = `…/a686/jo`, even inside the consolidated file. Take identity from SPARQL, not from the file.
- **Per-article metadata:** each `<article>` embeds `scl:JOLUXWork` with the article ELI (`…/a686/art_2`), the article version ELI (`…/art_2/20180820`), `dateApplicability` and `rdf:type jolux:Article`. This gives article-level ELI citations without any extra request.
- **Repealed text:** 14 elements have `style="del"`, and there are 30 `<del>` elements and `<mod class="mod-start" for="#pm1"/>` markers. `<passiveModifications><textualMod id="pm1" type="substitution"><source href="/eli/etat/leg/loi/2025/12/19/a619/jo"/>` gives the amending act. There are 81 `<ref href=…ELI>` cross-references. **Filter `del` content and keep the `textualMod` sources as "modified by" citations.**
- **Element counts:** article 78, alinea 124, paragraph 33, chapter 11, section 13, embeddedText 21, note 1.

---

## 5. Comparable projects in other jurisdictions

**Fedlex (Switzerland, JOLux):**
- **swiss/fedlex-jolux**: the official JOLux documentation (Sphinx). Concept docs transfer directly.
- **swiss/fedlex-sparql**, **bequrios/fedlex**: SPARQL tutorial notebooks.
- **malkreide/fedlex-mcp** (MIT, Python FastMCP, 12 tools; live SPARQL, no cache). It uses `jolux:ConsolidationAbstract` and `/0 /1 /3` status codes, so its queries need translating.
- **mindful-bio/mcp-fedlex** (Apache-2.0, Rust, 40 tools, docker compose with Redis, provenance block on every tool result, `valid_as_of`). The best model for MCP tool design and point-in-time citations.
- **OpenHelvetia/mcp-fedlex-oh** (Rust, "bitemporal citation loop").
- **samusafe/swiss-legal-rag** (MIT): the closest reference architecture for our air-gapped stack.
  - Docker Compose with PostgreSQL + pgvector, PostgreSQL FTS, reciprocal-rank fusion and a cross-encoder reranker.
  - Ollama `bge-m3` + `qwen2.5:3b-instruct`.
  - Refuses when nothing is retrieved; `/ready` checks DB, models and a non-empty corpus; a 33-question gold set.
- **gpassoni/swiss-legal-rag** (MIT): Qdrant dense (`BAAI/bge-m3`) + sparse BM25, RRF, `bge-reranker-v2-m3`, one-hop cross-reference expansion, and a **deterministic citation verifier** that keeps only citations the model was shown.
- **mkubicek/fedlex-parser**: a small AKN article extractor (heading context per article).
- **droid-f/fedlex**, **benjamin-arfa/swiss-law**: git mirrors.

**Other jurisdictions:**
- **France:** `pylegifrance/mcp-server-legifrance` (MIT, 65 stars; PISTE OAuth). `Legilibre/legi.py` (PyPI `legi` 0.6, CC0) builds SQLite from the DILA LEGI bulk dumps and links versions; it is a good design reference for a "versioned law into SQLite" schema. `matematicsolutions/fr-eli-mcp`.
- **EU:** `cyanheads/eur-lex-mcp-server` (Apache-2.0; CELLAR SPARQL; notes the same "sorted results stop at row 10,000" Virtuoso limit), `matematicsolutions/mcp-eu-sparql`, and `matematicsolutions/mcp-eu-compliance` (offline SQLite FTS5 with verbatim EUR-Lex). Relevant later if we add the EU directives that LU laws transpose.
- **Germany:** `kmein/gesetze`, `QuantLaw/gesetze-im-internet` (daily git archives), `klein-business/legal-text-mcp-de` (canonical IDs and provenance), `Steffen-W/gesetzessuche`.
- **Netherlands and Belgium:** `Ansvar-Systems/Dutch-law-mcp` and `Belgian-law-mcp` (both archived), and `PatrickB-Private/bwb2md`.

---

## 6. Parsing, retrieval and evaluation components

| Component | Licence / version (VERIFIED on PyPI, HF or Docker Hub) | Verdict |
|---|---|---|
| `lxml` 6.1.3 | BSD-3 | Use it for CSD13 AKN, with recover mode as a fallback for the duplicate-attribute defect. |
| `rdflib` 7.6.0 | BSD-3 | Optional, for loading the ontology or SKOS offline. |
| `SPARQLWrapper` 2.0.0 (2022) | W3C | Not needed; plain httpx is enough. |
| `mcp` 2.3.0 (2026-10-02) | MIT | The official Python SDK for our MCP server. |
| `fastmcp` 4.1.0 | Apache-2.0 | Alternative to the official SDK. |
| `bluebell-akn` 5.0.0 | LGPL-3+ | Plain text → AKN. **Not needed**; we already have AKN. |
| `cobalt` 9.0.1 | LGPL-3+ | AKN/FRBR helpers that assume AKN3 final. **Low fit.** |
| `BAAI/bge-m3` | MIT | Multilingual dense + sparse; handles fr/de. Recommended. |
| `BAAI/bge-reranker-v2-m3` | Apache-2.0 | Recommended reranker. |
| `OrdalieTech/Solon-embeddings-large-0.1` | MIT | French-specialised alternative. |
| `intfloat/multilingual-e5-large` | MIT | Alternative. |
| `maastrichtlawtech/bsard` | CC-BY-NC-SA-4.0 | French (Belgian) statutory-article retrieval benchmark. Internal evaluation only. |
| `maastrichtlawtech/lleqa` | Gated | Same caveats as BSARD. |
| `pgvector/pgvector:0.8.7-pg17-trixie` / `pg18` | Docker Hub tag 2026-10-01 | One-container option: SQL + FTS + vectors. |
| `paradedb/paradedb:0.26.2-pg18` | 2026-10-16 | Postgres with a BM25 extension. |
| `qdrant/qdrant:v1.19.2` | 2026-10-05 | Separate vector-store option. |
| `ollama/ollama:0.40.5` | 2026-10-15 | Local LLM and embeddings runtime. |

**Citation parsing:** I found no maintained French or Luxembourg legal-citation parser. The best citation source is structural: `<ref href="…/eli/…">` in AKN, article ELIs from `scl:jolux uriThis`, and `textualMod` sources. A small regex layer is only needed for user queries such as "art. 5 de la loi du 1er août 2018".

---

## 7. Pitfalls and lessons learned

1. **robots.txt (VERIFIED):** `data.legilux.public.lu` disallows `/eli/*`, `/filestore/*` and `/api/rss-adm.xml`. Fetch content from `legilux.public.lu/filestore/...`. Several open-source tools get this wrong.
2. **`/sparql` vs `/sparqlendpoint` (VERIFIED):** `/sparql` is an SPA. Mixing them up led lu-eli-mcp and the endomorphosis dataset to claim "no SPARQL".
3. **GET vs POST (VERIFIED):** projects disagree, but both worked for me. Prefer GET for short queries; use POST for long `VALUES` batches to avoid URL length limits of about 8 KB (REPORTED).
4. **Virtuoso paging (REPORTED by lex and cyanheads):** do not page a sorted result past 10,000 rows. Use `ORDER BY` with pages under 10k, or `VALUES` batches.
5. **The original act is not current law:** Ansvar's approach. Always prefer the current consolidation. Fall back to the original only when no consolidation exists.
6. **Repealed text inside consolidated XML (VERIFIED):** see section 4. legalize-lu shows the repealed Art. 56 as live text.
7. **Gaps and oddities in consolidation metadata (VERIFIED):** 38 current consolidations have no status, 55 works have no `typeDocument`, 34 versions are future-dated (keep them, flagged "not yet applicable") and 91 current ones have no XML (PDF only). Do not OCR them; store metadata and a link.
8. **The Act-level `in-force` flag is coarse (VERIFIED):** it is true for about 8k LOI, including exhausted amending laws. Combine it with consolidation membership, `jolux:modifies` and type rules.
9. **URI shapes:** codes use `eli/etat/leg/code/<name>/<YYYYMMDD>`; laws use `…/consolide/YYYYMMDD`. Identifiers are `http://`; fetch over `https://`.
10. **Files and XML quality (REPORTED by lex):** files can reach about 31 MB; there is one malformed-XML case; some old consolidations yield empty provisions; Constitution expressions exist in de/fr/lb (pick fr as the dominant language); subject labels are French only.
11. **Filestore HEAD returns 403 (REPORTED by clarvia):** use GET with a conditional or ETag.
12. **Third-party snapshots go stale:** lex-corpus and lex-articles were last updated 2026-08-15 although they claim "nightly". Always rebuild from the official endpoint, and use third-party data only for validation.

---

## 8. Proposed reuse map

| Need | Reuse |
|---|---|
| Discovery queries | Port lex's Consolidation, manifestation, licence, subject and sameAs queries. Add our own "current only" filter (`FILTER NOT EXISTS { ?c jolux:dateEndApplicability ?e }`, or `dateEndApplicability >= today`) and a branch for unconsolidated in-force originals. |
| Fetch | Write our own polite fetcher: about 1 req/s, sequential, retries on 429/5xx, `legilux.public.lu/filestore` host, ETag cache, 32 MB cap. |
| Parse | Write our own lxml CSD13 parser. Check its output against the lex-articles parquet (article counts, anchors, text) and the lu-eli-mcp/Ansvar fixtures. |
| Store and retrieve | Follow samusafe/gpassoni: Postgres + pgvector + FTS (or ParadeDB), bge-m3, bge-reranker-v2-m3, RRF, citation verifier, refusal when nothing is retrieved. |
| MCP design | Follow mindful-bio and lex: a provenance block (ELI, `valid_as_of`, source URL, attribution), `coverage()` stating gaps, read-only, and tool results treated as data. |
| Schema docs offline | Ship sparnatural's `jolux.ttl` v6.11 and the vocabulary SKOS. |
| Change detection | SPARQL on the consolidation's `dcterms:created` and the `<uri>/graph` node's `dcterms:modified`, plus `rss-leg.xml` and `rss-draft.xml` as triggers. |
