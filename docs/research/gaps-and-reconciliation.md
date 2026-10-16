# gaps-and-reconciliation

## Recommendations
- Current-version selection (replace every track's variant): for date T, take the consolidation with dateApplicability <= T < dateEndApplicability (or no end). There is exactly one per work today (1,213). Then (a) drop or flag it if every base act (the work's jolux:Act member) has dateNoLongerInForce <= T or status no-longer-in-force* (35 cases); (b) if it has no files (49 stubs), fall back to the latest version with files only when one exists (10 cases), labelled 'consolidation annoncée au <date>, texte non disponible'; otherwise use the JO original plus the amending-act list (39 cases); (c) never treat a version whose end date has passed with no successor as current (40 cases in the sparql-jolux-model rule). Do not use the 'applicable' flag (2 wrong, 34 missing) or 'no dateEndApplicability' (includes 18 future versions).
- Compute in-force status and the current version locally at query time from dates. Flags flip silently (266 of 269 recent act flips had no graph bump; consolidation flags flip on dateApplicability). Ship the next future consolidation versions (34 versions, 26 with files) so an aging airgapped DB switches versions by itself. Treat no-longer-in-force-implicit (50) as not in force.
- SPARQL paging: pull whole tables unsorted in one request (act table: 151,253 rows / 46 MB / 5 s; consolidations: 4,719 rows / 1.3 s). Use ORDER BY only within partitions of under 10k rows (SR353 beyond OFFSET+LIMIT 10,000). Correct the sparql-jolux-model recommendation of 10k–50k sorted pages.
- Content acquisition: fetch only https://legilux.public.lu/filestore/eli/etat/leg/... (allowed by that host's robots.txt). Never fetch data.legilux.public.lu/filestore or /eli, /eli/etat/adm/ (including the 15 current adm consolidations), *.docx, /reg_ue/ or /dir_ue/. Parse robots.txt at each run and turn its specific URIs (e.g. naturalisation law loi/2007/01/15/n2) into an ELI denylist that also applies to filestore paths. In parallel, write to SCL (info@legilux.public.lu) to confirm the bulk-fetch rate and the licenceSCL terms.
- Licence policy: current consolidations are all CC-BY, so XML is fine there. For in-force JO acts, 15,502 core acts have CC-BY HTML but only licenceSCL XML. Either get SCL to confirm the FAQ's CC-BY statement covers the XML, or build an HTML parser path for those acts (same art_N anchors). Store jolux:license per source file and show it in each citation. Cite the copyright exception as Art. 10, 8° (not Art. 8) and leave its scope (SCL editorial work, database right) to counsel.
- Treat code-recueils specially. XML code-recueils (fonction_publique, sante, recueils) compile member consolidations: deduplicate by article wId (/eli/.../art_X/consolide/YYYYMMDD), keep the member consolidation as canonical, keep only /jo-sourced articles from the recueil, and never use generated art_N… ids as anchors. PDF-only code-recueils (route 969 pages, environnement, environnement_annexes, securite_sociale, communal, education_nationale) are born-digital text and the only consolidated source for heavily amended acts (agd/1955/11/23/n1 modified 162×), so their PDF parsing is in scope for v1. Suppress the outdated JO originals of their member acts, or route users to the code text.
- Text-basis labels with expected volumes for the MCP warnings: consolidated (1,129); stub_fallback (10); original_never_amended (6,803 LOI / 8,445 RGD); original_possibly_outdated (763 LOI / 803 RGD amended but not covered by an up-to-date consolidation). Always return the list of amending acts (jolux:modifies inbound, entry date > base text date) with the latter. Add flags 'base légale abrogée' (1,586 RGD) and 'temporary act without end date' (663 traffic RGDs; also exclude these from default retrieval).
- Sync design: nightly (1) graph dct:modified watermark with client-side datetime parsing; (2) date-boundary sweep for dateEntryInForce, dateNoLongerInForce, dateApplicability and dateEndApplicability crossing [last_run, T+60d]; (3) full re-pull of the consolidation table each run (1.6 MB) and weekly re-pull of the act table. Use the Atom feed and RSS only as triggers: no consolidations, and they miss date flips. Consolidations are timely (median creation lag 0 days, p90 48), so weekly airgap bundles suffice.
- Citations: use the in-XML article ELIs (scl:JOLUXWork uriThis) for consolidations and codes. For JO originals (no article ELIs in the XML), cite act ELI + 'Art. N' and, if needed, a clearly marked derived URI. Never parse dates or versions from URIs (recueil/sites_monuments/20142209). The disclaimer should quote loi du 23 décembre 2016, Art. 5: only the published Journal officiel 'fait seul foi'.
- Embedding default: choose Qwen3-Embedding-0.6B (safetensors present, Apache-2.0, 1024-d) over bge-m3 for TEI offline serving, unless an offline TEI load test of bge-m3 (pytorch_model.bin only) passes. Pin the HF revision. Keep ParadeDB (AGPL) behind a compose profile pending legal sign-off. pg_textsearch is at v1.5.1.
- Re-base corpus sizing on one definition before the plan fixes hardware. The tracks' chunk estimates (100–200k vs 400k) assume different scopes and chunk sizes. Measure article counts and text bytes on a 1% stratified sample of the final selection (consolidations + chosen JO types) instead of extrapolating from codes.

## Risks / open questions
- Terms of reuse are unresolved. The FAQ grants CC BY 4.0 for 'textes et métadonnées', but 30k+ in-force JO XML files are tagged 'Licence de réutilisation SCL', a licence with no published definition. The general site terms forbid redistribution 'sauf indication contraire', and the data host's robots.txt disallows /filestore/*. Written confirmation from SCL is the only clean answer. Until then, redistributing an airgapped bundle carries legal risk (UNVERIFIED legal assessment).
- 49 current consolidations are file-less stubs, 34 of them for over 6 months, including the Loi du 5 avril 1993 relative au secteur financier. For these works there is no current consolidated text anywhere in open data. Answers built from the original plus amending acts are inherently unreliable.
- 763 LOI and 803 RGD in force have possibly outdated stored text. The RDF modifies/repeals edges may themselves be incomplete. LegalResourceImpact covers only 384 impacts since 2023 and cannot serve as a completeness check.
- In-force flags are weak. 663 temporary traffic RGDs have no end date. 1,586 RGDs have all their legal bases repealed (whether they survive depends on transitional clauses: UNVERIFIED legal rule). Old acts back to 1617 are flagged in-force. Retrieval needs type and title rules plus down-weighting; the rules need lawyer review.
- Code-recueil handling depends on markup seen in only 1 MB of one XML code-recueil (fonction_publique). The wId pattern has malformed variants ('…/n2//consolide/…', '/art_X/jo'). PDF-only code-recueils have no wIds, so deduplication against member acts and article segmentation of 969-page PDFs are untested.
- Silent status flips mean any snapshot ages. If the MCP server reads stored flags instead of computing them from dates, an airgapped DB will misreport in-force status between bundles. Future versions announced after the bundle date cannot be anticipated.
- Unsorted large SPARQL pulls work today (151k rows / 46 MB in 5 s), but neither row ceilings nor rate limits are documented. A server-side change could break single-request pulls, so keep a partitioned fallback (by year/type, under 10k rows per sorted partition).
- Whether the website shows consolidations or texts beyond the RDF was not re-verified here. content-formats reported 4,718 consolidation/code/recueil URLs in the sitemaps vs 4,719 in RDF, which suggests parity. The internal Elasticsearch index (/elasticsearch/proxy) was deliberately not examined.
- Whether TEI 1.9.4 can load BAAI/bge-m3 offline from pytorch_model.bin is UNVERIFIED. The Qwen3.8 vLLM parser names and the other airgap-stack items flagged by that track remain UNVERIFIED.
- Copyright scope: Art. 10, 8° of the 2001 law (actes officiels) is verified text. Whether it covers SCL consolidations, XML markup, recueils, or the database (sui generis) right is a legal question for counsel (UNVERIFIED).

## Verified facts
- Sorted SPARQL results are hard-capped at OFFSET+LIMIT <= 10,000 rows  
  _evidence:_ POST https://data.legilux.public.lu/sparqlendpoint query 'SELECT ?s WHERE { ?s a jolux:Act } ORDER BY ?s LIMIT 10 OFFSET 10000' -> HTTP 500 'Virtuoso 22023 Error SR353: Sorted TOP clause specifies more then 10010 rows to sort. Only 10000 are allowed.'; 'ORDER BY ?s LIMIT 20000' -> same SR353; 'LIMIT 10 OFFSET 9990' -> HTTP 200
- Unsorted SPARQL results are not capped: the full act list or full act table comes back in one response  
  _evidence:_ 'SELECT ?s WHERE { ?s a jolux:Act } LIMIT 200000' -> HTTP 200, 10,066,663 B, 2.17 s, 151,251 lines incl. header (151,250 distinct); act table q a1 (7 columns) -> 46,136,891 B in 5.18 s, 151,253 rows
- legilux.public.lu robots.txt does not disallow /filestore, while data.legilux.public.lu disallows /eli/* and /filestore/*; the main host also lists specific takedown URIs incl. a naturalisation law  
  _evidence:_ GET https://legilux.public.lu/robots.txt (1,249 B): 'Disallow: /publications-regroupees … /eli/etat/adm/ … /reg_ue/ … /dir_ue/ … /search … /*.docx … /eli/etat/leg/loi/2007/01/15/n2/jo … /eli/etat/leg/memorial/2007/8/fr/pdf'; GET https://data.legilux.public.lu/robots.txt: 'Disallow: /api/rss-adm.xml Disallow: /eli/* Disallow: /filestore/*'; SPARQL title of loi/2007/01/15/n2/jo -> 'Lois du 15 janvier 2007 conférant la naturalisation.'
- Exactly one consolidation per work covers today by date window (1,213); flag-based 'applicable' gives 1,181, of which 2 are wrong (end date long past); the 34 window-current versions without a flag are all file-less stubs  
  _evidence:_ q c1 (4,719 rows) + c2 formats + bin/recon.py: 'W window covers today 1213 works 1213', 'A flag applicable 1181', 'A&W 1179 A-W 2 W-A 34'; A-W = agd/1927/05/04/n1/consolide/19300129 (end 2002-01-01), loi/1992/06/17/n1/consolide/20090616 (end 2010-02-23); 'W formats xml 1132, none 49, pdf-only 32'; 'A formats xml 1134, pdf-only 32, none 15'
- 'No dateEndApplicability' is not the right definition of current: it includes 18 future versions and misses 17 current versions that have a scheduled end date  
  _evidence:_ bin/recon.py: 'B open-ended 1214', 'B-W 18 Counter({(not-yet-applicable, future): 14, (no status, future): 4})', 'W-B 17'
- 35 window-current consolidations belong to base acts that are no longer in force; usable current consolidations (window + files + base not repealed) = 1,129 (1,097 XML, 32 PDF-only)  
  _evidence:_ q c3 base-act status + bin/recon.py: 'W base status in-force 1134, no base act (RECUEIL) 37, no-longer-in-force 35, no base act (CODE_RECUEIL) 7'; e.g. etat/leg/loi/1999/05/31/n8/consolide/20180918 base dnl 2026-08-01; 'usable current consolidations … 1129 Counter({xml: 1097, pdf-only: 32})'
- sparql-jolux-model's 1,179 'current' = 1,129 usable + 10 fallbacks for stubs + 40 discontinued consolidations (ended, no successor, base act still in force)  
  _evidence:_ bin/recon.py: 'D latest-with-files<=today 1375'; 'D-W with base ok 50 have newer (stub) version 10 discontinued (no newer version at all) 40'; example etat/leg/loi/1922/03/05/n1/consolide/19650531 end 2004-06-21
- 49 current stubs exist with no files; 39 have no older version with files; 34 have been applicable for over 6 months  
  _evidence:_ bin/recon.py 'W without files 49 … with older fallback having files 10 no fallback at all 39'; q s1 graph dct:modified for stubs -> 110 in 2026, 3 in 2025; 'current stubs applicable for >6 months 34', oldest loi/2009/06/05/n8/consolide/20250616
- Act in-force status flips silently at dateNoLongerInForce, without updating the graph dct:modified  
  _evidence:_ q t08: acts with dnl 2026-09-01..2026-10-16 -> no-longer-in-force with graph-mod-before-dnl 266, graph-mod-on/after-dnl 3; dnl 2026-10-17..12-31 -> in-force 341
- Consolidation applicable flags also flip silently on dateApplicability, and versions are often pre-published  
  _evidence:_ q f1: etat/leg/rgd/2011/02/27/n10/consolide/20261011 dateApplicability 2026-10-11 status applicable graph dct:modified 2026-10-08T13:58:50.834+02:00; loi/2017/04/24/a479/consolide/20261002 graph 2026-09-29
- Current consolidations are timely: only 13 of 1,129 usable ones have an amending or repealing act in force after their dateApplicability; codes and the Constitution have 0  
  _evidence:_ q m1 (7,693 modifies/repeals edges into consolidated works) + bin/lag.py: 'up to date 1116, has pending amendment(s) 13'; per-code lines e.g. 'CODE etat/leg/code/travail/20260726 2026-07-26 pending 0'
- Consolidation creation lag in 2026 has a median of 0 days (p90 48), and 67 works got a new version in the last 90 days  
  _evidence:_ bin/recon data: '2026-applicable versions with files 279 created minus dateApplicability: min -663 p10 -10 median 0 p90 48 … share created before/at applicability 0.52'; 'works changed (new version) in last 90 days 67'
- In-force LOI/RGD text-basis breakdown: 721 LOI and 764 RGD were modified but never consolidated; including stubs, ended and lagging consolidations, 763 LOI and 803 RGD have possibly outdated text  
  _evidence:_ q m2 (47,428 modifies/repeals edges) + q a1 + bin/acts.py: ('LOI','modified; never consolidated') 721; stub 18; only outdated/ended 16; lags 8; up to date 467; never modified 6803; RGD 764/19/17/3/486/8445
- jolux:Article has 58,673 type rows but 15,770 distinct resources; ComplexWork has 151,631 rows but 151,580 distinct  
  _evidence:_ 'SELECT (COUNT(*) AS ?n) (COUNT(DISTINCT ?a) AS ?nd) WHERE { ?a a jolux:Article }' -> 58673,15770; same for jolux:ComplexWork -> 151631,151580
- In-force LOI = 8,060 total = 8,033 in /etat/leg/ + 27 in /etat/adm/; RGD 9,747 = 9,734 + 13  
  _evidence:_ q k2 GROUP BY type -> LOI 8060, RGD 9747; bin/acts.py split -> ('RGD','leg') 9734, ('LOI','leg') 8033, ('LOI','adm') 27, ('RGD','adm') 13; e.g. http://data.legilux.public.lu/eli/etat/adm/loi/1997/01/24/n2/jo
- Code de la sécurité sociale is typed CODE_RECUEIL (member of base act loi/1925/12/17/n1); there are 7 CODE and 9 CODE_RECUEIL works and 15 current code versions  
  _evidence:_ bin/recon code listing: 'loi/1925/12/17/n1 {code: 11, consolide: 1} {CODE_RECUEIL: 11} current: code/securite_sociale/20260101 [pdf]'; CODE works civil/commerce/procedure_penale/penal/travail/consommation/procedure_civile; code/cooperation_judiciaire current: None
- Code de la route member acts have no individual consolidation and are heavily amended; the Code de la route PDF is born-digital (969 pages, text layer)  
  _evidence:_ bin/recon: works loi/1955/02/14/n3 and agd/1955/11/23/n1 -> [] consolidations; q m2 counts: loi/1955/02/14/n3/jo modified-by 42, agd/1955/11/23/n1/jo modified-by 162; Range GET bytes 0-524287 of https://legilux.public.lu/filestore/eli/etat/leg/code/route/20260612/fr/pdf/eli-etat-leg-code-route-20260612-fr-pdf.pdf -> 206, '/Linearized 1/L 35507629 … /N 969', 120 /Font, 6 /ToUnicode, 10,203 Tj/TJ ops in 94 decompressed streams
- XML code-recueils compile member consolidations: articles carry wId article ELIs of member consolidations and generated, unstable ids  
  _evidence:_ Range GET bytes 0-1048575 of …/code/fonction_publique/20261011/fr/xml/…xml (total 6,550,354 B): xmlns:recueil="http://www.scl.lu/recueil"; '<article wId="/eli/etat/leg/constitution/1868/10/17/n1/art_11/consolide/20260829" id="art_N10064">'; part heading 'Version consolidée applicable au 01/06/2026 : Loi du 16 avril 1979 fixant le statut général des fonctionnaires de l'Etat.'; 404 of 478 wIds = same version as the member's own current consolidation, 74 /jo or malformed '…/n2//consolide/20260717'
- RDF has no links from recueils or code-recueils to their member acts  
  _evidence:_ q r1 outgoing predicates of RECUEIL/CODE_RECUEIL consolidations: version, isMemberOf, isRealizedBy, typeDocument, dct:modified, responsibilityOf, personInCharge, rdf:type, publicationDate, dateEndApplicability, inForceStatus, dateApplicability, urgentEvent, dct:created; q r2 inbound: prov:specializationOf 493, jolux:impactConsolidatedBy 1
- For in-force LOI/RGD/AGD/RMIN, CC-BY XML exists for only 8,115 acts, while 15,502 acts have XML only under licenceSCL but HTML under CC-BY  
  _evidence:_ q l4 per-act crosstab -> 28,703 acts: 'CC-BY XML' 8115, 'CC-BY HTML only (XML SCL or absent)' 15502, 'PDF under licenceSCL only' 4442, 'text only under licenceSCL' 619, 'CC-BY PDF only' 25; q l1 in-force /etat/leg/: xml-akomantoso licenceSCL 30,452 acts (pub 1623-11-20..2016-12-28), html CC-BY 38,924
- The licenceSCL concept has no definition or terms in the vocabulary  
  _evidence:_ q l2 'SELECT DISTINCT ?p ?o WHERE { <…/license/licenceSCL> ?p ?o }' -> only rdf:type skos:Concept, dct:created 2016-01-01T00:00:00Z, euvoc:status CURRENT, inScheme, prefLabel 'Licence de réutilisation SCL', topConceptOf, owl:deprecated false
- The Casemates FAQ grants CC BY 4.0 for texts and metadata; the general site terms restrict reuse 'sauf indication contraire'  
  _evidence:_ GET https://legilux.public.lu/api/assets/faq/questions/12-introduction.html (2,441 B): 'Les textes et les métadonnées sont mis à disposition selon la licence d’utilisation http://creativecommons.org/licenses/by/4.0/ qui autorise leur réutilisation pour des utilisations commerciales ou non.'; …/14-utilisation.html (6,718 B): 'Sauf indication contraire, l’usager est autorisé à … les documents ne peuvent être utilisés qu'à titre personnel … ne peuvent être diffusés en dehors de ce site.'
- The official-acts copyright exception is Art. 10, 8° of the amended loi du 18 avril 2001, not Art. 8  
  _evidence:_ GET https://legilux.public.lu/filestore/eli/etat/leg/loi/2001/04/18/n2/consolide/20220409/fr/xml/eli-etat-leg-loi-2001-04-18-n2-consolide-20220409-fr-xml.xml -> 200, 483,074 B; art_10: 'Lorsque l'œuvre … a été licitement rendue accessible au public, l'auteur ne peut interdire: …' then '<li scl:symbol="8°">les actes officiels de l'autorité et leur traduction officielle, ainsi que les discours prononcés dans les assemblées délibérantes…'
- Only the published Journal officiel is authentic (relevant to disclaimers)  
  _evidence:_ SPARQL found http://data.legilux.public.lu/eli/etat/leg/loi/2016/12/23/n25/jo ('Loi du 23 décembre 2016 concernant le Journal officiel du Grand-Duché de Luxembourg.', in-force); GET its filestore XML (13,589 B) art_5: 'Le Journal officiel est publié sous forme électronique … Le Journal officiel publié fait seul foi et produit des effets juridiques.'
- JO original XML carries only document-level ELIs, with no article-level ELIs  
  _evidence:_ loi/2016/12/23/n25/jo XML: 2 JOLUXWork blocks, uriThis values …/loi/2016/12/23/n25, …/n25/jo, …/jo/fr, …/jo/fr/xml, …/jo/fr/pdf; 9 <article id="art_1er"…"art_9"> without uriThis; <docDate date="2999-01-01">
- No current national act has German text: DEU/LTZ expressions are only the 2023-07-01 Constitution version and treaty documents  
  _evidence:_ q g1: LTZ 2 -> constitution/1868/10/17/n1/consolide/20230701 | traite/2019/36/doc/tab/1; DEU 14 -> constitution …/consolide/20230701 plus 13 /eli/etat/traite/YYYY/N/doc/tab/1 (1992/29 … 2023/107)
- The ELI Atom update feed exists but contains no consolidations; its leg entries are mostly metadata edits of old acts that mirror graph dct:modified  
  _evidence:_ GET https://legilux.public.lu/pillar4/eli-update-feed.atom -> 200 application/atom+xml 463,579 B; 1,439 entries 2026-08-17T00:09:41Z..2026-10-15T16:59:40Z; adm 836, leg 603, consolide 0; 70 leg entries from 2026, rest older (e.g. 1872-1874: 68); q t07 on 531 old leg ids -> all graph dct:modified >= 2026-08-15 (in-force 356; no-longer-in-force with old dnl 174)
- RSS feed for Mémorial A is on the data host (50 items)  
  _evidence:_ GET https://data.legilux.public.lu/api/rss-leg.xml -> 200 31,816 B, 50 items, newest link http://data.legilux.public.lu/eli/etat/leg/rmin/2026/10/01/a495/jo, pubDates 2026-10-15 … 2026-08-31
- 663 in-force RGDs are temporary traffic regulations with no end date; 1,586 in-force RGDs have all their national legal bases repealed  
  _evidence:_ q tt1 (9,747 in-force RGD titles): 'temporary traffic' 663 (2020s 497, 2010s 117, 2000s 49), 'with dnl 0'; amending-only titles 3,123; q b1 basedOn (9,361 RGD with basedOn) -> 'all national bases repealed' 1586, 'at least one base in force' 7651
- A partial structured amendment model exists (jolux:LegalResourceImpact) but covers only 384 impacts since 2023-07  
  _evidence:_ q i1 -> LegalResourceImpact 384 (CONTAINS scan took 8.9 s); q i3 -> impact-type/1 'modifie' 379 (pub 2023-07-27..2026-08-03, 138 with impactConsolidatedBy), impact-type/2 'rectifie' 5
- One consolidation URI carries a date different from its dateApplicability (an invalid date)  
  _evidence:_ bin/recon.py: 'uri-date != dateApplicability 1 [http://data.legilux.public.lu/eli/etat/leg/recueil/sites_monuments/20142209]'
- Stack spot-checks: mcp 2.3.0 is latest; Open WebUI 0.12.3 pins mcp==1.27.2; bge-m3 has no safetensors; Qwen3-Embedding-0.6B has safetensors; pg_textsearch latest tag is v1.5.1  
  _evidence:_ curl https://pypi.org/pypi/mcp/json -> 'mcp latest 2.3.0 2026-10-02T22:06:52'; https://pypi.org/pypi/open-webui/0.12.3/json requires_dist -> ['mcp==1.27.2']; https://huggingface.co/api/models/BAAI/bge-m3 siblings -> ['onnx/model.onnx','pytorch_model.bin'] sha 5617a9f6…; Qwen/Qwen3-Embedding-0.6B -> ['model.safetensors'] sha 97b0c614…, license apache-2.0; git ls-remote --tags https://github.com/timescale/pg_textsearch -> … v1.5.0, v1.5.1

## Report

# Completeness critic and gap filler: reconciliation of the 6 tracks (live checks, 2026-10-16)

**How the checks were run**
- About 45 SPARQL requests to `https://data.legilux.public.lu/sparqlendpoint`, all POST and form-encoded.
- About 12 other HTTP GETs: robots.txt for both hosts, the Atom feed, RSS, two FAQ pages, 2 small XML files, and 3 ranged PDF/XML probes.
- Every request used UA `LegiLux-Local-Clone-Research/0.1 (gap-check; ~1 req/s; small samples)`, with a 1.2 s gap between requests.

**Where the files are**
- Scripts, queries and raw outputs: `<research-scratch>/gaps-and-reconciliation/`.
  - `bin/q.sh` is the polite SPARQL helper.
  - `bin/recon.py` reconciles the consolidation counts.
  - `bin/lag.py` measures consolidation lag.
  - `bin/acts.py` checks status and amendments.
  - `q/*.rq` holds the exact queries; `out/*` holds the results.

Anything not checked live is marked **UNVERIFIED**.

---
## 1. Contradictions between tracks, resolved live

### 1.1 Row caps and paging (sparql-jolux-model and scope-sizing are both partly wrong)
- **Sorted queries are capped at OFFSET+LIMIT ≤ 10,000.**
  - `SELECT ?s WHERE {?s a jolux:Act} ORDER BY ?s LIMIT 10 OFFSET 10000` → HTTP 500: `Virtuoso 22023 Error SR353: Sorted TOP clause specifies more then 10010 rows to sort. Only 10000 are allowed.`
  - `ORDER BY ?s LIMIT 20000` → the same SR353 error.
  - `LIMIT 10 OFFSET 9990` → 200.
  - So sparql-jolux-model's advice to "page with ORDER BY + LIMIT/OFFSET in 10k–50k chunks" is **wrong** past row 10,000.
- **Unsorted queries are not capped.**
  - `SELECT ?s WHERE {?s a jolux:Act} LIMIT 200000` → 151,250 distinct rows, 10,066,663 B, 2.17 s.
  - My full act status table (a1: 7 columns) returned 151,253 rows, 46 MB, in 5.2 s.
  - So scope-sizing's claim that "results are capped at 10,000 rows" is **wrong** for unsorted queries.
- **Rule for the plan:**
  - Pull whole tables unsorted in one request (all are ≤ 151k rows), or partition by year, type or `VALUES` batches.
  - Use ORDER BY only inside partitions of under 10k rows.
  - Do not rely on unsorted OFFSET paging being stable; it is not guaranteed.

### 1.2 Can full texts be fetched? (sparql-jolux-model says "blocked", the other tracks say "allowed")
Both robots.txt files were fetched live:
- `https://data.legilux.public.lu/robots.txt` → `Disallow: /api/rss-adm.xml`, `/eli/*`, `/filestore/*`.
- `https://legilux.public.lu/robots.txt` disallows only:
  - `/publications-regroupees`, `/api/rss-adm.xml`, `/eli/etat/adm/`, `/reg_ue/`, `/dir_ue/`, `/search`, `/*.svg`, `/*.docx`;
  - plus 9 specific URIs, including `/eli/etat/leg/loi/2007/01/15/n2/jo` (title from SPARQL: "Lois du 15 janvier 2007 conférant la naturalisation."), two PA notices and `/eli/etat/leg/memorial/2007/8/fr/pdf`.

**Resolution**
- Fetching `https://legilux.public.lu/filestore/eli/etat/leg/...` is **not disallowed**. It is the SPA's own `manifestation_prefix`. The "main blocker" in sparql-jolux-model is overstated.
- Nuance 1 — use terms: the use-terms FAQ (`/api/assets/faq/questions/14-utilisation.html`, fetched live) is restrictive "Sauf indication contraire" ("les documents ne peuvent être diffusés en dehors de ce site"). The Casemates FAQ (`12-introduction.html`, fetched live) is that "indication contraire": "Les textes et les métadonnées sont mis à disposition selon la licence d'utilisation http://creativecommons.org/licenses/by/4.0/".
- Nuance 2 — the specific robots URIs act as a **personal-data takedown list**. Apply them as an ELI denylist to filestore paths too, even though the paths differ.
- Nuance 3 — `/eli/etat/adm/` is disallowed. 15 current consolidations live under `/eli/etat/adm/` (PA 5, AGD 3, AMIN 3, AGC 2, RI 1, untyped 1). Skip them.

### 1.3 How many consolidations are "current"? (1,181 / 1,213 / 1,214 / 1,179 / 1,123 / 1,134)
I pulled all 4,719 consolidations, their formats and their base-act status in three unsorted queries (`out/c1`, `c2`, `c3`), then joined them in `bin/recon.py`. The definitions differ as follows:

| Definition | Count | Notes |
|---|---|---|
| A. flag `inForceStatus applicable` | 1,181 (1 per work) | 2 are flag errors: `agd/1927/05/04/n1/consolide/19300129` (end 2002-01-01) and `loi/1992/06/17/n1/consolide/20090616` (end 2010-02-23) |
| B. no `dateEndApplicability` (db track, prior-art) | 1,214 | **Wrong rule.** It includes 18 future versions (14 not-yet-applicable, 4 status-less) and misses 17 current versions that already have a scheduled end date |
| **W. window: dateApplicability ≤ today < dateEndApplicability (or no end)** | **1,213, exactly one per work** | A∩W = 1,179. W−A = 34, all status-less **and** file-less stubs |
| D. latest version with files and dateApplicability ≤ today (sparql-jolux-model C04) | 1,375 → 1,179 after dropping repealed base acts | That 1,179 = 1,129 usable W + 10 stub fallbacks + **40 discontinued consolidations** (end date passed, no successor version, base act still in force, e.g. `loi/1922/03/05/n1/consolide/19650531` ended 2004). These 40 must not be presented as current |

**What W contains**
- Formats: 1,132 with XML, 32 PDF-only, 49 with no files. The 49 are 15 flagged applicable plus 34 status-less.
- Base act status:
  - 1,134 in force;
  - **35 whose base act is no-longer-in-force but whose consolidation was never closed**, e.g. `loi/1999/05/31/n8/consolide/20180918` (base repealed 2026-08-01);
  - 44 recueils and code-recueils with no base act.

**Final usable set** (W, has files, base not repealed): **1,129** consolidations (1,097 XML, 32 PDF-only), 1,118 of them in `/etat/leg/`.

**The 49 current stubs**
- Only 10 have an older version with files to fall back on. For example, `loi/1993/04/05/n1` (secteur financier) falls back to 2003-10-01, which is useless.
- 39 have no version with files at all.
- 34 have been "applicable" for more than 6 months without files (oldest: `loi/2009/06/05/n8/consolide/20250616`). Stub graphs were last modified in a 2026-04-14 batch. Do not expect files to appear soon.

**Reconciling the other tracks' numbers**
- prior-art's "1,123 with FR XML / 91 without" and scope-sizing's "1,127 / 32 / 55" apply to the B set (no end date).
- content-formats' "1,134 of 1,181 applicable have XML" is consistent: A breaks down into 1,134 XML, 32 PDF-only, 15 none.

### 1.4 Other count mismatches
- **jolux:Article:** 58,673 type rows vs **15,770 distinct** (`SELECT (COUNT(*)) (COUNT(DISTINCT ?a))` → `58673,15770`). scope-sizing, prior-art and db quote the duplicated figure, which comes from the union default graph.
- **ComplexWork:** 151,631 rows vs **151,580 distinct**.
- **In-force LOI:** 8,060 = 8,033 in `/etat/leg/` + 27 in `/etat/adm/`. In-force RGD: 9,747 = 9,734 + 13.
- **Amended in-force LOI without a usable current consolidation** (721 vs ~738). From `bin/acts.py` over all 47,428 modifies/repeals edges (`out/m2`) and the 151,250-act table (`out/a1`):

  | In-force LOI in `/etat/leg/` | Count |
  |---|---|
  | never modified | 6,803 |
  | modified, consolidation up to date | 467 |
  | modified, never consolidated | **721** |
  | modified, only ended consolidations | 16 |
  | modified, current consolidation is a stub | 18 |
  | modified, consolidation lags | 8 |

  - RGD: 8,445 / 486 / **764** / 17 / 19 / 3.
  - So **763 LOI and 803 RGD** would be served with possibly outdated text. Both tracks' figures are partial views of this.

### 1.5 Which codes exist, and of what type
`bin/recon.py` code listing:
- **7 CODE works**, each a member of its base act: civil = loi/1804/03/21/n1, commerce = loi/1807/09/15/n2, procédure pénale = loi/1808/11/17/n1, pénal = loi/1879/06/18/n1, travail = loi/2006/07/31/n2, consommation = loi/2011/04/08/n2, procédure civile = rgd/1998/08/03/n4.
- **9 CODE_RECUEIL works:** communal, cooperation_judiciaire (no current version), education_nationale, environnement, environnement_annexes, fonction_publique, route, santé, **and sécurité sociale**.
  - Sécurité sociale is typed CODE_RECUEIL, not CODE, even though it is a member of base act loi/1925/12/17/n1. sparql-jolux-model's base-act table puts it among the codes.
- So there are **15 current code versions**: the 7 CODEs plus 8 CODE_RECUEILs.

### 1.6 Licence: XML-first (content-formats) vs HTML-first (scope-sizing)
Per-act cross-tab for in-force LOI/RGD/AGD/RMIN (`out/l4`, 28,703 acts):

| Licence situation | Acts |
|---|---|
| CC-BY XML | 8,115 |
| **XML only under licenceSCL but HTML under CC-BY** | **15,502** |
| Text only under licenceSCL | 619 |
| PDF only, licenceSCL | 4,442 |
| PDF only, CC-BY | 25 |

The licenceSCL concept has no definition in the vocabulary. Its only properties are prefLabel "Licence de réutilisation SCL", created 2016-01-01 and status CURRENT (`out/l2`).

So for 15.5k core acts the CC-BY copy is the HTML. Two ways forward:
- (a) Get SCL's confirmation that XML may be reused under CC-BY (the FAQ says "textes et métadonnées" are CC-BY), or
- (b) parse HTML for those acts. Same anchors and text per content-formats; `div id="art_N"`.

Current consolidations are all CC-BY.

**Copyright exception.** scope-sizing cites "art. 8" of the 2001 copyright law. That is **wrong**. The current consolidation `loi/2001/04/18/n2/consolide/20220409` (fetched, 483,074 B), **Art. 10, `<li scl:symbol="8°">`**, reads: "les actes officiels de l'autorité et leur traduction officielle, ainsi que les discours prononcés dans les assemblées délibérantes…". Whether this also covers SCL's editorial consolidations or markup, or the database (sui generis) right, is **UNVERIFIED legal interpretation**.

### 1.7 Infrastructure claims spot-checked
- PyPI `mcp` latest is 2.3.0 (2026-10-02T22:06:52). `open-webui 0.12.3` requires `mcp==1.27.2`. Both confirmed.
- `BAAI/bge-m3` has only `onnx/model.onnx` and `pytorch_model.bin`, no safetensors (HF API sha 5617a9f6…). `Qwen/Qwen3-Embedding-0.6B` has `model.safetensors` (sha 97b0c614…, apache-2.0).
  - The db track's default (bge-m3 on TEI) therefore carries load risk. Prefer Qwen3-Embedding-0.6B unless an offline TEI load test of bge-m3 passes.
- `git ls-remote` of timescale/pg_textsearch shows tags up to **v1.5.1**, not 1.5.0 as the db track says.
- ParadeDB is AGPL; the db track defaults to it while the airgap track advises against it. This is a product/legal decision. Ship the permissive default (pgvector + native FTS, or pg_textsearch) and put ParadeDB behind a compose profile.

---
## 2. Gaps no track answered (answered live)

### 2.1 Status flips are date-driven and silent, for acts and consolidations
- **Acts (q `t08`).** Acts with dateNoLongerInForce between 2026-09-01 and today:
  - 266 are now no-longer-in-force with a graph `dct:modified` **before** the end date;
  - only 3 have a graph modified on or after it.
- **Acts with an end date between 2026-10-17 and 12-31:** 341 are still `in-force`. So status is flipped after the date with no graph bump.
- **Consolidations (`out/f1`).** Example: `rgd/2011/02/27/n10/consolide/20261011` has graph modified 2026-10-08 and is now `applicable` from 2026-10-11. Flags flip silently at dateApplicability, and versions are often pre-published.
- **Dates vs flags (`bin/acts.py`).** Dates agree with flags except for:
  - 1 in-force act whose end date has passed (`adm/amin/2023/08/29/b3075`);
  - 4 not-yet-in-force acts whose entry date has passed;
  - 3 no-longer-in-force acts with no past end date;
  - 50 `no-longer-in-force-implicit` acts with no dateNoLongerInForce.
- **Implication for the airgap:** compute in-force status and the "current version" **at query time from dates**: `dei ≤ asof AND (dnl IS NULL OR dnl > asof)`, plus the consolidation window. Treat `-implicit` as not in force. Ship the next future version (34 future versions over 18 works, 26 of them with files) so an aging offline DB still switches versions correctly.

### 2.2 How fresh are consolidations?
- **Missed amendments (`bin/lag.py`).** Of the 1,129 usable current consolidations, only **13** have a modifying or repealing act in force after their dateApplicability. 12 of these lag by more than a year (legacy, e.g. `loi/1862/12/09/n1/consolide/19761227`) and one by 14 days. All 15 current codes and the Constitution have 0 pending amendments.
- **Creation lag.** For 279 versions with files applicable in 2026, `created − dateApplicability` has median **0 days**; 52% are created on or before the applicability date; p10 is −10 and p90 is +48.
- **Cadence.** New versions per applicability month: 4–80 (e.g. 2026-01: 80, 2026-09: 29). 67 works got a new version in the last 90 days. Weekly data bundles are more than sufficient.

### 2.3 Code-recueils: what they contain and how to deduplicate them
**XML code-recueils are compilations of member consolidations.** I ranged-fetched the first 1 MB of `code/fonction_publique/20261011` XML (total 6,550,354 B):
- It uses an extra namespace `xmlns:recueil="http://www.scl.lu/recueil"`.
- `part` headings read "Version consolidée applicable au 01/06/2026 : Loi du 16 avril 1979 fixant le statut général…".
- Articles carry `wId="/eli/etat/leg/constitution/1868/10/17/n1/art_11/consolide/20260829"` but have **generated ids** `id="art_N10064"`, which are unstable.
- 404 of 478 articles point to **the same version** as the member's own current consolidation.
- The other 74 are `/art_X/jo` original-text articles or malformed `…/n2//consolide/20260717`.
- **Rule:** deduplicate by wId, keep the member consolidation as canonical, keep only the `/jo`-sourced articles from the recueil, and never use `art_N…` ids as anchors.

**PDF-only code-recueils are the only consolidated source for major areas.**
- Code de la route members `loi/1955/02/14/n3` (modified 42×) and `agd/1955/11/23/n1` (modified 162×) have **no individual consolidation**.
- Their JO originals would be badly outdated, so PDF parsing of the code-recueils is required, not optional.
- Code de la route PDF probe (`Range: bytes=0-524287` of the 35,507,629 B file):
  - `/Linearized … /N 969` (969 pages), InDesign PieceInfo;
  - 120 `/Font` entries with `/ToUnicode`;
  - 10,203 Tj/TJ operators in the first 94 streams.
  - So it is a born-digital text layer; no OCR is needed.
- RDF has no links from recueils to their members (`out/r1`/`r2`): outgoing predicates are only isMemberOf, isRealizedBy, typeDocument, etc.

### 2.4 Article-level ELIs
- **Consolidations and codes:** they embed `scl:JOLUXWork/uriThis` article ELIs, as other tracks showed. The `…/art_X/YYYYMMDD` form gives the date that article's wording last changed, which supports "en vigueur dans cette rédaction depuis le …" (inference).
- **JO originals:** only document-level ELIs. `loi/2016/12/23/n25/jo` XML (13,589 B) has 2 JOLUXWork blocks with uriThis …/n25, …/n25/jo, …/fr, …/fr/xml, …/fr/pdf, and none per article.
- **Citations for unconsolidated acts:** cite act ELI + "Art. N". A minted `<work>/art_N` is non-official (UNVERIFIED that it resolves).
- **Never parse dates from URIs:** `recueil/sites_monuments/20142209` has URI date ≠ dateApplicability (an invalid date).

### 2.5 German and Luxembourgish texts
`out/g1`:
- DEU expressions (14) = `constitution/…/consolide/20230701` plus 13 treaty documents (`/eli/etat/traite/YYYY/N/doc/tab/1`).
- LTZ (2) = that same Constitution version plus one treaty.
- No current national act has a German text. Plan FR-only content, with multilingual embeddings for DE/EN questions.

### 2.6 What "current" means for RGDs
`out/tt1`, `out/b1`. Of the 9,747 in-force RGDs:
- **663 are temporary traffic RGDs** ("réglementation temporaire de la circulation"; 497 from the 2020s) with **no dateNoLongerInForce**, so they look in force forever;
- 3,123 have amending-only titles;
- 164 concern protection zones;
- **1,586 have all their national legal bases (`jolux:basedOn`) no longer in force.** Under LU practice they often survive through transitional clauses (UNVERIFIED legal rule). Flag "base légale abrogée" rather than dropping them.

### 2.7 Incremental feeds
- **ELI Atom feed:** `https://legilux.public.lu/pillar4/eli-update-feed.atom` returned 200, 463,579 B. It has 1,439 entries (2026-08-17T00:09:41Z → 2026-10-15T16:59:40Z): 836 adm, 603 leg, **0 consolidations**.
  - 533 of the 603 leg entries are pre-2025 acts.
  - All 531 checked match a graph `dct:modified` ≥ 2026-08-15 (`out/t07`); 174 are retro-curated repeals (old end date).
  - So the feed mirrors graph edits. It misses consolidations and silent date flips.
- **RSS:** `https://data.legilux.public.lu/api/rss-leg.xml` has 50 items (2026-08-31 to 10-15).
- **SPARQL stays primary.**
  - Run the graph watermark (with client-side datetime parsing) and a daily date-boundary sweep.
  - Re-pull the 46 MB unsorted act table weekly (5 s).
  - Re-pull the 4,719-row consolidation table each run (1.6 MB, 1.3 s).
- **LegalResourceImpact:** I also found `jolux:LegalResourceImpact` (`out/i1`–`i3`).
  - 384 impacts: 379 "modifie", 5 "rectifie". Published 2023-07-27 → 2026-08-03; 140 carry `impactConsolidatedBy`.
  - Coverage is partial, so use it only as a hint.

### 2.8 Legal disclaimer wording (verified)
Loi du 23 décembre 2016 concernant le Journal officiel (`loi/2016/12/23/n25/jo` XML, fetched), Art. 5: "Le Journal officiel publié fait seul foi et produit des effets juridiques." The MCP answer template should therefore say that consolidated texts and the local copy are informational and that only the published JO is authentic.
