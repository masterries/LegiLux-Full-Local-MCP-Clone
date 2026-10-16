# content-formats

## Recommendations
- Use the Akoma Ntoso (CSD13 + scl:) XML from https://legilux.public.lu/filestore/... as the canonical full-text source. Find each file through SPARQL jolux:isExemplifiedBy rather than deriving the path from the ELI, because older acts point to whole Mémorial issue PDFs.
- Treat HTML only as a fallback or display copy. It exists for exactly the same documents as XML and adds noise (inline JS, hidden spans), so it does not improve coverage.
- Use PDF only when no XML exists: the 6 PDF-only codes (communal, education_nationale, environnement, environnement_annexes, route, securite_sociale), the PDF recueils and pre-2010 acts. Extract with pdftotext -layout and regex-segment by 'Art. …'. Add optional Tesseract (fra) OCR only for pages with no text layer, and store a source_format/quality flag.
- Select 'current' versions as follows. For each work, take the consolidation with inForceStatus=applicable that has an XML manifestation. If the applicable one has no files (113 placeholders, e.g. the Loi du 5 avril 1993 relative au secteur financier), fall back to the latest consolidation that has files, add the JO texts of later amending acts, and flag consolidation_stale. For in-force acts that have no consolidation, ingest the JO XML. Store the 29 not-yet-applicable versions separately as 'upcoming'.
- Take all document metadata (dates, in-force status, title, type, Mémorial, subjects, citations, transpositions) from SPARQL, not from the XML <meta>. The XML meta is a stale snapshot: empty publicationDate, docDate 2999-01-01, and a FRBRthis that points to /jo.
- Emit one DB row per <article> by walking book/part/title/chapter/section/subsection, and also parse attachments/attachment/doc/mainBody (annexes; create annex units when an annex has no articles). Never emit articles from inside <embeddedStructure> (quoted amending text). Store the article's ELI work/version URIs from scl:JOLUXWork when present.
- Build citations from article@id + num + paragraph/num + list labels (ol@scl:start/@scl:symbol or li@scl:symbol). Do not use paragraph@id, which is sequential and incomplete. Normalise num labels, e.g. 'Art. L. 121-1.' to 'L. 121-1'.
- Build text by concatenating text nodes without adding spaces, then normalising whitespace (sup/i/b are inline). Strip noteRef/mod markers. Keep <del> content flagged or dropped. Turn tables into markdown. Replace base64 <img> data URIs with '[image: alt]' before storing, since they cause up to 1.9 MB files.
- Store the modification apparatus: textualMod (type/source/destination), note@title 'A|pubdate|actdate|type|memNo' and note text, and the mod-start for= links. The LLM can then answer 'modifié par …' questions. In codes, classify parenthetical headings like '( L. 13 mai 2008 )' as provenance, not as headings.
- Normalise ref@href: relative '/eli/...' gets the prefix http://data.legilux.public.lu; keep http://data.europa.eu/eli/... as is. Store these as a citation graph (article → target ELI).
- Run incremental sync with the SPARQL named-graph change feed (GRAPH ?g { ?g dcterms:modified ?m } FILTER(?m > last_run)), then a conditional GET with If-Modified-Since on each file (304 when unchanged). Never send HEAD (it returns 403). Use Range: bytes=0-0 for size probes. Keep a delay of at least 1 second between requests and a descriptive User-Agent.
- Exclude from the clone: DOCX (robots.txt), PDF/A copies when XML exists, Mémorial B (/eli/etat/adm, robots-disallowed and mostly administrative or personal notices), non-applicable consolidations and no-longer-in-force acts (keep metadata only), and scl:index. Make RC (communal regulations: 21,612 in force, only 5,749 with XML) an optional ingestion profile.
- Store each version's source URL, Last-Modified, sha256 and graph_modified to make the airgapped DB auditable. Record the licence per manifestation (CC BY 4.0 vs licenceSCL) for attribution in MCP answers.
- Use the stdlib-only prototype bin/akn_parse.py in the scratchpad as the starting point for the ingestion parser. It already handles hierarchy paths, in-XML ELI, embeddedStructure exclusion and annexes.

## Risks / open questions
- Some key laws have no current consolidated text: the Loi du 5 avril 1993 relative au secteur financier has had only placeholders since 2025. For these, answers must be rebuilt from the original act plus the amending acts, which is less reliable. How widespread this is beyond the 113 placeholders and 47 applicable versions without XML was not fully measured.
- Six major current codes are PDF-only, including the Code de la sécurité sociale (838 pages) and the Code de la route (35.5 MB). Parsing them into articles with a regex is error-prone and has not been validated end to end.
- Many pre-2000 acts exist only as whole Mémorial issue PDFs. Some pages are scans without OCR (e.g. Mémorial A 1995 n°98, pages 3+), 19th-century issues are bilingual two-column OCR, and there is no page-range metadata per act except memorialPage in some XML meta. Segmenting an issue into acts is an open problem.
- Volume of in-force JO XML: the 20-file sample had a median of 8.6 KB but a mean of 181 KB because of base64 images. The full download of about 42k files could be several GB and take about 12+ hours at 1 req/s (UNVERIFIED extrapolation). This needs a resumable job and maybe a policy that prioritises LOI/RGD.
- The XML schema is a CTIE-specific variant of Akoma Ntoso CSD13 with no public XSD found (urn:fontoxml:akomantoso.xsd is an editor URN). Markup differs between the JO, consolidation and code pipelines: hierarchy ID styles, scl:subject2 attributes, headings used for provenance. The parser needs tests across many samples, not just the 6 used here.
- No public article-level deep link was verified. Legilux pages are a JavaScript SPA, so whether https://legilux.public.lu/eli/...#art_x scrolls to the article is UNVERIFIED. Article ELI URIs such as .../code/travail/art_l_121-1 do 303 to the SPA.
- Using the SPARQL graph change feed at scale (pagination, endpoint timeouts and result limits) was not tested beyond LIMIT 25. Virtuoso-like aggregate quirks were seen: an OPTIONAL+BIND count gave 38,122 instead of 5,384.
- Which SPARQL predicate (jolux:modifies, passiveRef, or the textualMod source in the XML) most completely lists the amending acts after the last available consolidation is unverified.
- Licensing is mixed: 149,106 manifestations carry 'Licence de réutilisation SCL' rather than CC BY 4.0. Its exact terms need legal review before redistributing an offline copy.
- Judicial decisions (/eli/jdec/...) are metadata-only in Legilux, and their texts are hosted elsewhere (e.g. https://ja.public.lu/54597C.pdf). Including case law needs a separate source decision.
- The SPA's Elasticsearch proxy (https://legilux.public.lu/elasticsearch/proxy) might index the consolidated full text, including versions with no filestore files. It was not explored, because it is an internal site API, and it should not be relied on without permission.

## Verified facts
- legilux.public.lu /eli/... URLs (any manifestation suffix) return only the Angular SPA shell, not content  
  _evidence:_ GET https://legilux.public.lu/eli/etat/leg/loi/2026/02/25/a88/jo{,/fr,/fr/html,/fr/xml,/fr/pdf,/de/html} -> all '200 text/html;charset=UTF-8 size=1010' (gzip), body contains '<app-root></app-root>' and 'celui-ci n'est disponible qu'avec un navigateur supportant javascript'
- Actual files live in the filestore and are listed in SPARQL via jolux:isEmbodiedBy/jolux:isExemplifiedBy  
  _evidence:_ SPARQL on <.../loi/2026/02/25/a88/jo/fr> jolux:isEmbodiedBy ?m . ?m jolux:isExemplifiedBy ?f -> http://data.legilux.public.lu/filestore/eli/etat/leg/loi/2026/02/25/a88/jo/fr/xml/eli-etat-leg-loi-2026-02-25-a88-jo-fr-xml.xml (also pdfa, html, docx); GET https://legilux.public.lu/filestore/.../eli-etat-leg-loi-2026-02-25-a88-jo-fr-xml.xml -> 200 application/xml 33278 B
- XML format is Akoma Ntoso 3.0 CSD13 namespace extended with a CTIE/SCL namespace  
  _evidence:_ a88.xml root: <akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0/CSD13" xmlns:scl="http://www.scl.lu"><act contains="originalVersion" name="" scl:page-proof="many">; Code du travail adds xsi:noNamespaceSchemaLocation="urn:fontoxml:akomantoso.xsd"
- Articles are marked <article id=...> with <num>, <heading>, <paragraph><num>, <alinea><content><p>, lists <ol scl:start scl:symbol>  
  _evidence:_ a88.xml: <article id="art_2"><num>Art. 2.</num><heading><b>TVP UE</b></heading><paragraph><num>(1)</num><alinea><content><p>Le TVP UE est un titre de voyage...; <ol scl:start="1" scl:symbol="1°">
- Consolidated and code XML embed article-level ELI URIs (work + dated version) inside each article  
  _evidence:_ cons2013.xml: <article id="art_1er"><scl:JOLUXWork>...uriThis http://data.legilux.public.lu/eli/etat/leg/loi/2013/07/12/n1/art_1er ... uriThis .../art_1er/20260416 ... dateApplicability 2026-04-16; travail.xml art_l_121-1 -> .../code/travail/art_l_121-1/20201101
- Article-level RDF is not present in SPARQL for ordinary consolidated laws; jolux:Article instances exist only for some codes  
  _evidence:_ SELECT ?p ?o WHERE { <.../loi/2013/07/12/n1/art_1er/20260416> ?p ?o } -> 0 rows; GROUP BY isMemberOf of jolux:Article -> loi/1804/03/21/n1 7091, loi/1996/08/11/n1/jo 2794, loi/1808/11/17/n1 1775, loi/1879/06/18/n1 1384, code/travail 1126 ...
- Consolidated XML contains modification apparatus: passiveModifications/textualMod, lifecycle eventRef, mod-start/mod-end markers, modification notes with Mémorial info in note@title  
  _evidence:_ cons2013.xml: <textualMod id="pm1" incomplete="false" type="insertion"><source href="/eli/etat/leg/loi/2015/07/23/n5/jo"/><destination href="#art_5"/>; <mod class="mod-start" for="#pm51" style="li"/>; <note id="M1" marker="1" refersTo="pm1" title="A|2015-07-31|2015-07-23|L|149">; counts: 140 textualMod (79 insertion, 52 replacement, 9 substitution)
- embeddedStructure contains quoted articles of other acts (id-less <article>) which must not be indexed as articles of the act  
  _evidence:_ cons2013.xml: 288 <article> elements, 59 without id, located at act/body/chapter/section/article/alinea/content/p/embeddedStructure[/part/chapter|/section/subsection]; 85 articles contain embedded structures
- Paragraph ids are sequential non-ELI ids and incomplete  
  _evidence:_ cons2013.xml paragraph ids 'paragraph_66','paragraph_67',...; 243 of 426 <paragraph> have an id
- XML metadata for JO texts is a stale pre-publication snapshot; SPARQL holds authoritative dates/status  
  _evidence:_ a88.xml: <scl:jolux scl:name="publicationDate"/> (empty), dateEntryInForce empty, <docDate date="2999-01-01">; SPARQL for .../a88/jo: publicationDate 2026-02-26, dateEntryInForce 2026-03-02, inForceStatus in-force; HTML JSON-LD says legislationLegalForce NotInForce
- Some JO XML files embed base64 PNG images, inflating size  
  _evidence:_ GET .../rgd/2018/10/02/a936/jo/fr/xml/eli-etat-leg-rgd-2018-10-02-a936-jo-fr-xml.xml -> 1,900,411 B for 7 articles; contains <img alt="image 1" scl:img-height="15" src="data:image/png;base64,iVBORw0KGgo...">
- HTML manifestation is XSLT output with matching anchors, schema.org JSON-LD and noisy modification widgets  
  _evidence:_ cons2013.html: <div id="art_2" class="richtext_article"><a name="art_2">; <div class="richtext_paragraph richtext_paragraph" id="paragraph_66">; <span id="link_pm2"><span style="font-style: italic; display: none;" id="ref_text_art_2"> (L du 15 mars 2016)</span>; a88.html head: <script type="application/ld+json">{"@type":"Legislation","@id":"http://data.legilux.public.lu/eli/etat/leg/loi/2026/02/25/a88/jo"...
- Filestore HTTP behaviour: Last-Modified, no ETag, Range supported, conditional GET 304, HEAD forbidden, XML not gzipped, HTML gzipped  
  _evidence:_ a88.xml headers: Last-Modified: Thu, 26 Feb 2026 15:45:05 GMT; X-Content-Size: 33278; X-Robots-Tag: noindex, nofollow; Cache-Control: no-cache; no ETag. curl -I -> 403 Forbidden. -r 0-99 -> 206 Content-Range: bytes 0-99/33278. If-Modified-Since: Thu, 26 Feb 2026 15:45:05 GMT -> 304. XML with Accept-Encoding: gzip -> no Content-Encoding, 16664 B on wire; cons2013.html Content-Encoding: gzip, X-Content-Size 1831526, 167966 B on wire
- data.legilux.public.lu ELI URIs do content negotiation: HTML -> 303 to legilux.public.lu, RDF -> 303 to SPARQL CONSTRUCT over a per-resource named graph  
  _evidence:_ GET https://data.legilux.public.lu/eli/etat/leg/loi/2026/02/25/a88/jo -> 303 Location: https://legilux.public.lu/eli/...; with Accept: text/turtle -> 303 Location: https://data.legilux.public.lu/sparqlendpoint?query=CONSTRUCT { ?s ?p ?o. } WHERE { GRAPH <http://data.legilux.public.lu/eli/etat/leg/loi/2026/02/25/a88/jo/graph> {...} }; following it -> 200 text/turtle 5522 B incl. 'ns2:graph dcterms:modified "2026-02-26T15:46:41.777+02:00"'
- Per-resource named graphs carry dcterms:modified usable as a change feed  
  _evidence:_ SPARQL SELECT ?g ?m WHERE { GRAPH ?g { ?g dcterms:modified ?m } FILTER(?m >= "2026-10-14T00:00:00Z"^^xsd:dateTime) } ORDER BY DESC(?m) LIMIT 25 -> http://data.legilux.public.lu/eli/etat/traite/2026/3/graph | 2026-10-16T20:00:05.123+02:00; .../eli/dl/ppl/2026/294/graph | 2026-10-16T17:04:34.194+02:00; ...
- Consolidations expose validity windows and status; exactly one 'applicable' per work, plus future versions  
  _evidence:_ SPARQL versions of loi/2013/07/12/n1: consolide/20260416 2026-04-16..2027-04-16 applicable; consolide/20270416 2027-04-16 (no status); earlier ones not-applicable. Global: applicable 1181 (1181 works), not-applicable 3471, not-yet-applicable 29, none 38
- 1,134 of 1,181 applicable consolidations have XML; the rest are PDF-only or placeholders without files  
  _evidence:_ SPARQL COUNT applicable with FILTER EXISTS format XML -> 1134; NOT EXISTS list includes code/securite_sociale/20260101 (pdf), code/route/20260612 (pdf), code/environnement/20261003 (pdf), code/communal/20260801 (pdf), code/education_nationale/20260915 (pdf), code/environnement_annexes/20260314 (pdf), loi/1993/04/05/n1/consolide/20260711 (no formats)
- Placeholder consolidations without files exist, including all recent versions of the Loi du 5 avril 1993 (secteur financier)  
  _evidence:_ GRAPH <.../loi/1993/04/05/n1/consolide/20260711/graph> has only type/isMemberOf/inForceStatus applicable/dateApplicability 2026-07-11/dateEndApplicability 2027-01-11, no isRealizedBy; versions 20250101..20270111 all have 0 manifestations; only consolide/20031001 and 19991022 have /fr/pdf; filestore guess .../consolide/20260711/fr/xml/... -> 404 application/json. 4,606 of 4,719 consolidations have manifestations
- Current code versions and formats  
  _evidence:_ SPARQL jolux:Code applicable: civil/20251226, commerce/20260803, consommation/20260927, fonction_publique/20261011, penal/20251219, procedure_civile/20251219, procedure_penale/20260607, sante/20260810, travail/20260726, recueil/logement/20260104 -> docx,html,pdf,xml; communal, education_nationale, environnement, environnement_annexes, route, securite_sociale -> pdf only
- Code file sizes  
  _evidence:_ Range GET 0-0: code/travail/20260726 xml 4,128,058 B, html 4,242,544 B, pdf 2,317,228 B; code/civil/20251226 xml 5,413,811 B, html 3,012,461 B, pdf 1,642,012 B; code/securite_sociale/20260101 pdf 5,059,985 B; code/route/20260612 pdf 35,507,629 B
- Code XML structure and size: Code du travail 1,196 articles with livre/titre/chapitre/section ids and index terms; Code civil 2,301 articles  
  _evidence:_ travail.xml: <section id="livre_premier.titre_ii.chapitre_premier.section_1"><article id="art_l_121-1">...<scl:indexterm><scl:label>Contrat de travail</scl:label>...<num>Art. L. 121-1.</num><heading>(<ref href="/eli/etat/leg/loi/2008/05/13/n1/jo">L. 13 mai 2008</ref>)</heading>; books livre_premier..livre_vi; civil.xml article ids art_1er..art_2281 (2301), <article id="art_1382" scl:uri="/eli/etat/leg/code/civil/art_1382">
- XML/HTML availability by era for /eli/etat/leg/ acts (XML count = HTML count where tested)  
  _evidence:_ SPARQL COUNT by decade (total vs FILTER EXISTS XML): 1900s 2167/1056, 1950s 2529/1787, 1980s 5983/3157, 1990s 9403/3529, 2000s 12653/7343, 2010s 14163/14123, 2020s 5384/5384; LOI-only query showed identical XML and HTML counts per decade
- In-force acts and XML coverage  
  _evidence:_ SPARQL: in-force 64,536, no-longer-in-force 16,320; in-force with XML 42,359; LOI 8,033 in force / 7,131 with XML; RGD 9,734/9,649; RC 21,612/5,749
- Pre-2000 acts without XML point to a whole Mémorial issue PDF; quality varies (text, OCR, or image-only)  
  _evidence:_ rgd/1972/11/29/n1/jo/fr/pdf isExemplifiedBy .../memorial/1972/a72/fr/pdf/eli-etat-leg-memorial-1972-a72-fr-pdf.pdf (171,366 B, 16 p, 54,087 chars text). memorial/1995/a98 (8,383,965 B, 225 p, DigiPath): pages 3+ CCITT 200dpi images, pdftotext 1 char/page. memorial/1895/a7 (764,235 B, 20 p): Creator ABBYY FineReader, 43,641 chars, bilingual 'Memorial des Großherzogthums Luxemburg'
- PDF-only codes are text-based PDFs  
  _evidence:_ code/securite_sociale/20260101 PDF: Creator Adobe Illustrator CS5.1, Producer Acrobat Distiller 22.0, 838 pages, pdftotext 3,176,654 chars
- Texts are essentially French only  
  _evidence:_ SPARQL expressions by jolux:language: FRA 263,155; ENG 103; DEU 14; LTZ 2; others 1-5 (treaties). DEU/LTZ: .../constitution/1868/10/17/n1/consolide/20230701/de and /lb; current constitution consolide/20260829 has only /fr
- Sitemaps: 11 files, 100,075 URLs, work-level URIs only, no Mémorial B  
  _evidence:_ sitemap-index.xml (1,200 B) lists sitemap-1..11 lastmod 2026-10-16; sitemaps 1-10 have 10,000 URLs each (~1.58-1.69 MB), sitemap-11 75 URLs; categories: 86,376 .../jo, 4,718 consolidation/code/recueil, 7,784 /eli/dl/..., 1,183 /eli/etat/traite/..., 12 editorial; lastmod year 2022: 67,638 entries
- Sample sizes of current consolidated XML and in-force JO XML  
  _evidence:_ Range GET 0-0 on 20 MD5-ordered applicable consolidation XMLs: min 12,397, median 46,147, mean 61,601, max 168,971 B. 20 in-force JO XMLs (2000+): min 6,355, median 8,629, mean 181,241, max 1,900,411 B
- Two licences in use  
  _evidence:_ SPARQL count by jolux:license: http://creativecommons.org/licenses/by/4.0/ 350,336; http://data.legilux.public.lu/resource/authority/license/licenceSCL 149,106 (skos:prefLabel 'Licence de réutilisation SCL')
- SPA public config reveals manifestation prefix and backends  
  _evidence:_ GET https://legilux.public.lu/api/settings -> {"file_base_uri":"http://data.legilux.public.lu","elastic_url":"https://legilux.public.lu/elasticsearch/proxy","allowed_manifestation_langs":["de","en","fr","lb"],"manifestation_prefix":"https://legilux.public.lu",...}
- Old retro-converted XML includes Mémorial page metadata  
  _evidence:_ loi/1978/07/14/n1/jo XML (16,664 B): scl:jolux memorialPage 1050, memorialSequence 1; clean <article id="art_1er"><num>Art. 1<sup>er</sup>.</num><alinea><content><p>...
- Prototype parser extracts article units correctly from all sample types  
  _evidence:_ bin/akn_parse.py: a88 11 articles; cons2013 229 articles (229 with in-XML ELI, 85 containing quoted embedded structures, 453,842 chars); travail 1,196 articles incl. annex (1,179 with ELI); civil 2,301 (all with ELI)

## Report

# Track: Content manifestations and document formats on legilux.public.lu

Live tests ran on 2026-10-16 between 17:57 and 18:10 UTC. I sent about 110 requests in total, at most about 1 request per second, with the User-Agent `LegiLux-research-bot/0.1 (format research; low volume; contact ...)`. I did not fetch any robots-disallowed path or any `.docx` file. Scratch files are in `<research-scratch>/content-formats/`. Downloads are in `dl/`. The helper scripts and the prototype parser are in `bin/`, with the parser at `bin/akn_parse.py`.

## TL;DR

1. **Ingest Akoma Ntoso XML from the filestore.** The canonical format is the Akoma Ntoso 3.0 (CSD13) XML that CTIE/SCL serves from `https://legilux.public.lu/filestore/eli/...`. Find each file through SPARQL (`jolux:isEmbodiedBy` → `jolux:isExemplifiedBy`) rather than guessing the path. HTML covers the same documents (same coverage numbers) but is noisy. Use the text layer of the PDF only when there is no XML.
2. **The `/eli/...` URLs on legilux.public.lu are not content.** Every URL returns the same 2 KB Angular shell, whatever the suffix (`/fr/xml`, `/fr/pdf`, `/de/html` …).
3. **XML has good article-level markup but some traps.**
   - Good:
     - `<article id="art_1er|art_4-1|art_7bis|art_l_121-1|annexe_1_art_2">`, with `<num>`, `<heading>`, `<paragraph><num>(1)</num>`, `<alinea>`, `<ol scl:start scl:symbol>`, `<ref href="/eli/...">`.
     - Consolidations and codes also carry article-level ELI URIs inside the XML (`scl:JOLUXWork/uriThis` = `.../art_2` and `.../art_2/20260403`).
     - Each article has a modification apparatus: `<mod class="mod-start" for="#pmN"/>`, `<noteRef href="#MN"/>`, `<notes scl:type="modification">`, `<passiveModifications><textualMod>`.
   - Traps:
     - `<embeddedStructure>` holds quoted articles of *other* acts and must not be indexed as articles of this act.
     - Paragraph IDs are not ELI subdivisions.
     - Some JO files embed base64 PNG images (up to 1.9 MB).
     - XML metadata is a stale snapshot; take metadata from SPARQL.
4. **"Current" selection works through SPARQL, with one catch.** Use `jolux:Consolidation` + `jolux:inForceStatus <.../applicable>` together with `dateApplicability`/`dateEndApplicability`. However, 113 consolidations are placeholders with no files (shown on the site as "consolidation in progress"), and these include current versions of the *Loi du 5 avril 1993 relative au secteur financier*. Six current "codes" are PDF-only: communal, education_nationale, environnement, environnement_annexes, route, securite_sociale.
5. **Change detection.** Every resource lives in its own named graph `<…/graph>` that carries `dcterms:modified`. One SPARQL query returns everything changed since time T. The filestore supports `If-Modified-Since` → 304 and `Range`, but it answers HEAD with 403 and has no ETag.

---

## 1. URI patterns and what each one returns (verified)

### 1.1 Identifier URIs (ELI)

| Kind | Pattern (verified example) |
|---|---|
| Act, original JO publication (Work/LegalResource) | `http://data.legilux.public.lu/eli/etat/leg/loi/2026/02/25/a88/jo` (`a` = Mémorial A act number since 2016; `nN` = sequence for older acts, e.g. `.../loi/2013/07/12/n1/jo`) |
| Abstract act (all versions) | `.../eli/etat/leg/loi/2013/07/12/n1` (target of `jolux:isMemberOf`) |
| Consolidated version | `.../eli/etat/leg/loi/2013/07/12/n1/consolide/20260416` (date = `dateApplicability`) |
| Code version | `.../eli/etat/leg/code/travail/20260726`; `code/civil/20251226` (17 code slugs in the sitemap) |
| Recueil version | `.../eli/etat/leg/recueil/douanes_accises/20220221` (38 slugs) |
| Article in consolidation/code | `.../eli/etat/leg/code/travail/art_l_121-1` (work) and `.../art_l_121-1/20201101` (article version) |
| Mémorial A issue | `.../eli/etat/leg/memorial/2026/a88` (`jolux:Memorial`, `memorialNumber 88`, `memorialYear 2026`); older issues e.g. `.../memorial/1995/a98` |
| Expression | `<work>/fr` (also `/de`, `/lb`, `/en` for a few treaties and the 2023 Constitution) |
| Manifestation | `<work>/fr/{xml,html,pdf,pdfa,docx}` |
| File (item) | `http://data.legilux.public.lu/filestore/eli/etat/leg/loi/2026/02/25/a88/jo/fr/xml/eli-etat-leg-loi-2026-02-25-a88-jo-fr-xml.xml` |
| Named graph | `<work>/graph` (e.g. `.../loi/2026/02/25/a88/jo/graph`) |

The file-name rule is: path = `filestore/<eli path>/<lang>/<fmt>/` and file name = `<eli path with / replaced by ->-<lang>-<fmt>.<ext>`. For pre-2000s PDF-only acts, however, the manifestation points to the **whole Mémorial issue PDF**. For example, `rgd/1972/11/29/n1/jo/fr/pdf` → `filestore/eli/etat/leg/memorial/1972/a72/fr/pdf/eli-etat-leg-memorial-1972-a72-fr-pdf.pdf`. So you cannot derive file paths from the ELI; always read `isExemplifiedBy`.

### 1.2 HTTP behaviour per host

- **`https://legilux.public.lu/eli/...`** (any suffix) → `200 text/html`, 1010 B gzip (2118 B), with the body `<app-root></app-root>` (Angular SPA). Tested on `/jo`, `/jo/fr`, `/jo/fr/html`, `/jo/fr/xml`, `/jo/fr/pdf` and `/jo/de/html` for loi 2026/02/25/a88: all identical. **Do not crawl these.**
- **`http://data.legilux.public.lu/eli/...`** → `302` to https.
- **`https://data.legilux.public.lu/eli/...`** → `303`:
  - default/HTML: `Location: https://legilux.public.lu/eli/etat/leg/loi/2026/02/25/a88/jo`
  - `Accept: text/turtle` or `application/rdf+xml`: `Location: https://data.legilux.public.lu/sparqlendpoint?query=CONSTRUCT { ?s ?p ?o. } WHERE { GRAPH <http://data.legilux.public.lu/eli/etat/leg/loi/2026/02/25/a88/jo/graph> { ?s ?p ?o. } }`. Following it returned `200 text/turtle`, 5,522 B, 94 lines, including `ns2:graph dcterms:modified "2026-02-26T15:46:41.777+02:00"`.
  - Article URIs resolve the same way: `https://data.legilux.public.lu/eli/etat/leg/code/travail/art_l_121-1` → 303 → `https://legilux.public.lu/eli/etat/leg/code/travail/art_l_121-1`.
- **`https://legilux.public.lu/filestore/...`** (and the identical `https://data.legilux.public.lu/filestore/...`; `http://data...` gives 302):
  - Headers: `Last-Modified` present, **no ETag**, `Cache-Control: no-cache`, `X-Content-Size: <bytes>`, `X-Robots-Tag: noindex, nofollow`, `Accept-Ranges: bytes`, `Access-Control-Allow-Origin: *`.
  - `Range: bytes=0-0` → `206` with `Content-Range: bytes 0-0/<total>`, which gives a cheap size probe.
  - `If-Modified-Since: Thu, 26 Feb 2026 15:45:05 GMT` → **304**.
  - **HEAD → 403 Forbidden.** This is WAF/Apache behaviour, so use a Range GET instead.
  - **gzip:** `text/html` is gzip-encoded (a consolidated HTML of 1,831,526 B went over the wire as 167,966 B). `application/xml` is **never** compressed, even with `Accept-Encoding: gzip` (16,664 B on the wire = Content-Length).
  - Default curl UA → 200; empty UA → 200. No 429 or throttling across about 110 requests at ≤1 req/s.
- The SPA config at `https://legilux.public.lu/api/settings` shows `"manifestation_prefix": "https://legilux.public.lu"`, `"file_base_uri": "http://data.legilux.public.lu"`, `"elastic_url": "https://legilux.public.lu/elasticsearch/proxy"`, `"allowed_manifestation_langs": ["de","en","fr","lb"]`. The bundle function `getFileUrl()` rewrites `http://data.legilux.public.lu/filestore/...` to `https://legilux.public.lu/filestore/...`.
- Legacy file URIs (`jolux:previousIsExemplifiedBy`), e.g. `https://data.legilux.public.lu/file/eli-etat-leg-memorial-1972-72-fr-pdf.pdf`, return 301.

### 1.3 Manifestation formats in the triple store

`SELECT ?uf (COUNT(?m) AS ?n) WHERE { ?m a jolux:Manifestation . OPTIONAL { ?m jolux:userFormat ?uf } } GROUP BY ?uf` returned:

| userFormat | count |
|---|---|
| pdf | 154,216 |
| pdfa | 108,657 |
| docx | 99,168 |
| html | 99,162 |
| xml-akomantoso | 63,839 |
| xml | 35,323 |
| doc | 4 |

`xml` and `xml-akomantoso` are the same AKN format with the same file naming; the label differs by ingestion era. Licences: `cc-by/4.0` on 350,336 manifestations, and `licenceSCL` ("Licence de réutilisation SCL") on 149,106, mostly older PDFs.

---

## 2. XML: vocabulary and markup (verified)

**Root (all samples):** `<akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0/CSD13" xmlns:scl="http://www.scl.lu">`. The Code du travail adds `xsi:noNamespaceSchemaLocation="urn:fontoxml:akomantoso.xsd"` (FontoXML editor). This is **Akoma Ntoso 3.0 CSD13** (a pre-1.0 committee draft namespace, not the final OASIS `akn/3.0` namespace), extended by a CTIE/SCL namespace `scl:` with these elements:

- `scl:JOLUXWork`, `scl:JOLUXLegalResource`, `scl:JOLUXExpression`, `scl:JOLUXManifestation`, `scl:jolux scl:name="…"`, `scl:rdf`
- `scl:indexterms/scl:indexterm/scl:label`
- `scl:num`, `scl:articles`, `scl:index`

and these attributes: `scl:symbol`, `scl:start`, `scl:separator`, `scl:style-format`, `scl:verify`, `scl:page-proof`, `scl:uri`, `scl:subject2`.

### 2.1 (a) Recent law, JO form: loi 2026/02/25/a88

The file is 33,278 B. Its elements: p 76, content 66, alinea 65, num 38, li 36, paragraph 27, ref 26, article 11, heading 11, ol 8, plus preface/longTitle, preamble/formula, conclusions/signature, attachments/attachment/doc/mainBody, and components/component/interstitial (the "Doc. parl." footer). Excerpt:

```xml
<article id="art_2">
<num>Art. 2.</num>
<heading><b>TVP UE</b></heading>
<paragraph>
<num>(1)</num>
<alinea><content><p>Le TVP UE est un titre de voyage qui est délivré par le ministre ...</p></content></alinea>
</paragraph>
...
<ol scl:start="1" scl:symbol="1°"><li>« <ref href="/eli/etat/leg/agd/1923/06/29/n1/jo" id="8c58d440-...">arrêté grand-ducal du 29 juin 1923</ref> » : ...</li>
```

**The metadata inside the JO XML is a pre-publication snapshot.** It contains `<scl:jolux scl:name="publicationDate"/>` (empty), an empty `dateEntryInForce`, empty `FRBRthis`/`FRBRuri` values, and `<docDate date="2999-01-01">`. SPARQL for the same act gives `publicationDate 2026-02-26` and `dateEntryInForce 2026-03-02`. The HTML JSON-LD says `"legislationLegalForce": {"@id":"https://schema.org/NotInForce"}`, while SPARQL says `inForceStatus in-force`.

The annex here (`attachment_1`) is **alinea-only, with no `<article>`**, so a parser that only collects articles loses it.

### 2.2 (b) Consolidated text: loi 2013/07/12/n1 (AIFM law), `consolide/20260416`

The XML is 1,080,499 B with about 524k characters of body text.

- **Meta:** `FRBRWork/FRBRthis` = `.../loi/2013/07/12/n1/jo` (not the consolidation URI), and `act@contains="originalVersion"` even though the text is consolidated.
- `scl:JOLUXLegalResource` holds `dateApplicability 2026-04-16` and `dateEndApplicability 2027-04-16`.
- `<lifecycle>` holds `eventRef` elements (`type="generation"|"amendment"`, with dates).
- `<analysis><passiveModifications><textualMod id="pm1" type="insertion|replacement|substitution"><source href="/eli/etat/leg/loi/2015/07/23/n5/jo"/><destination href="#art_5"/>`. This file has 140 of them (79 insertion, 52 replacement, 9 substitution).
- `<references><passiveRef href=… showAs="Loi du 1er juillet 2024 …"/>`.

Body hierarchy: `chapter id="chapitre_1er"` › `section id="chapitre_3.section_1"` › `article id="art_1er"`. Excerpt:

```xml
<article id="art_1er">
<scl:JOLUXWork><scl:JOLUXComplexWork><scl:jolux scl:name="uriThis">http://data.legilux.public.lu/eli/etat/leg/loi/2013/07/12/n1/art_1er</scl:jolux></scl:JOLUXComplexWork>
<scl:JOLUXLegalResource><scl:jolux scl:name="uriThis">http://data.legilux.public.lu/eli/etat/leg/loi/2013/07/12/n1/art_1er/20260416</scl:jolux>
<scl:jolux scl:lg="fra" scl:name="title">Art. 1er.</scl:jolux><scl:jolux scl:name="dateApplicability">2026-04-16</scl:jolux>
<scl:rdf scl:name="type">http://data.legilux.public.lu/resource/ontology/jolux#Article</scl:rdf></scl:JOLUXLegalResource></scl:JOLUXWork>
<num>Art. 1<sup>er</sup>.</num>
<heading> <b> <i>Définitions</i> </b> </heading>
...<li scl:symbol="(2-1)"><mod class="mod-start" for="#pm51" style="li"/>« AEAPP » : ... <mod class="mod-end" for="#pm51"/><noteRef href="#M51" marker="51"/></li>
```

Notes:

```xml
<notes scl:type="modification" source="#rp11">
<note id="M1" marker="1" refersTo="pm1" title="A|2015-07-31|2015-07-23|L|149">
<content><p><ref href="http://data.legilux.public.lu/eli/etat/leg/loi/2015/07/23/n5/jo">Loi du 23 juillet 2015 portant: ...</ref></p></content></note></notes>
```

The `note@title` is `Mémorial series | publication date | act date | type | Mémorial number`.

Repealed items stay in the text as `<li style="del"><mod class="mod-start" .../><del>…</del>`.

**ID stability:**

- Article IDs are stable and readable (`art_4-1`, `art_7bis`, `art_15-2`).
- Paragraph IDs are sequential (`paragraph_66` …), and only 243 of 426 `<paragraph>` elements have an ID.
- **59 of 288 `<article>` elements have no ID.** They sit under `alinea/content/p/embeddedStructure` (quoted amending text of other acts), and 85 articles contain such structures.

### 2.3 (c) Large codes

**Code du travail `code/travail/20260726`.** The XML is 4,128,058 B, with 1,196 articles (`art_l_010-1` … `annexe_1_art_6`), 6 `book` (`livre_premier` … `livre_vi`), 40 `title`, 149 `chapter`, 269 `section`, 3,830 `scl:indexterm`, 41 notes and 1 table. Article and heading example:

```xml
<section id="livre_premier.titre_ii.chapitre_premier.section_1">
<article id="art_l_121-1"> <scl:JOLUXWork> … uriThis …/code/travail/art_l_121-1/20201101 … dateApplicability 2020-11-01 …</scl:JOLUXWork>
<scl:indexterms><scl:indexterm><scl:label>Contrat de travail</scl:label></scl:indexterm>…</scl:indexterms>
<num>Art. L. 121-1.</num>
<heading>( <ref href="/eli/etat/leg/loi/2008/05/13/n1/jo">L. 13 mai 2008</ref> )</heading>
```

In the codes, `<heading>` usually holds the **provenance** ("(L. 13 mai 2008)"), not a subject title.

**Code civil `code/civil/20251226`.** The XML is 5,413,811 B, with 2,301 articles (`art_1er` … `art_2281`), 13,084 index terms, no notes, no `<book>` IDs, and `chapter id="chapter_75" scl:subject2=… scl:subject2lbl="délit"`. Articles also carry `scl:uri="/eli/etat/leg/code/civil/art_1382"`.

The HTML and PDF of the same version are 3,012,461 B and 1,642,012 B.

Article-level RDF is only partial in SPARQL. `?a a jolux:Article` grouped by `isMemberOf` gives:

| Member work | Articles |
|---|---|
| loi/1804/03/21/n1 (Code civil) | 7,091 |
| loi/1996/08/11/n1/jo (NCPC) | 2,794 |
| loi/1808/11/17/n1 | 1,775 |
| loi/1879/06/18/n1 (Code pénal) | 1,384 |
| code/travail | 1,126 |
| … | … |

`.../loi/2013/07/12/n1/art_1er/20260416` has **no triples**, so article granularity must come from the XML.

### 2.4 Old XML (retro-converted)

Loi 1978/07/14/n1 (16,664 B) has clean `<article id="art_1er"><num>Art. 1<sup>er</sup>.</num><alinea><content><p>…`, with no paragraphs. Its meta includes `memorialPage 1050` and `memorialSequence 1`, which supports "Mém. A n°…, p. 1050" citations.

### 2.5 Images and size outliers

RGD 2018/10/02/a936 is a 1,900,411 B XML for 7 articles, because of `<img alt="image 1" src="data:image/png;base64,iVBORw0K…">`.

### 2.6 Prototype parser check (verified on downloaded samples)

`bin/akn_parse.py` uses the stdlib ElementTree. It walks book/part/title/chapter/section/subsection, emits one row per `<article>`, does not descend into `embeddedStructure`, and also walks `attachments`. Results:

| File | Articles | With in-XML article ELI | Text characters |
|---|---|---|---|
| a88 | 11 | 0 | 12,546 |
| cons2013 | 229 | 229 | 453,842 (85 contain quoted structures) |
| travail | 1,196 | 1,179 | 1,521,885 |
| civil | 2,301 | 2,301 | 750,290 |

Text must be built by **concatenating text nodes without inserted spaces**, then normalising whitespace. Otherwise `Art. 1<sup>er</sup>` becomes "1 er".

---

## 3. HTML manifestation (verified)

HTML is XHTML generated by XSLT from the same AKN source, with `xmlns:akoma` and `xmlns:scl` declared and Antenna House/xalan namespaces leaking in.

- **Head:** a `<script type="application/ld+json">` with schema.org `Legislation` (`@id`, `legislationDate`, `datePublished`, `legislationLegalForce`, `isPartOf`, `citation`, `legislationTransposes`, `keywords`).
- **Body:** `div id="art_2" class="richtext_article"` with `<a name="art_2">`, `div class="richtext_paragraph" id="paragraph_66"`, `richtext_alinea`, `richtext_num_article` and `richtext_heading_article`. Lists are `<table class="richtext_ol">` with `<td class="richtext_numLI">`.
- **Modification markers:** `span id="mod_marker_start_mod…" title="Loi du 3 mars 2026 …"`, `sup id="mod_supStart_…"`, `span id="link_pm2"` with hidden "(L du 15 mars 2016)", inline `onmouseover="highlightMod(...)"` and many `display:none` spans.
- **Anchors match the XML IDs** (`art_*`, `chapitre_*`, `paragraph_*`).
- **Text volume** of cons2013: HTML 507k characters vs XML body 524k characters, i.e. equivalent text with more noise.
- **Coverage:** in every probe, HTML existed exactly where XML existed (LOI by decade: identical counts).

**Verdict:** HTML is a fallback or display copy only. It has no advantage over the XML except gzip on the wire.

---

## 4. Coverage by era and format (verified with SPARQL)

### 4.1 Original JO acts

These are all `jolux:Act` under `/eli/etat/leg/` (86,376 in total = the JO entries in the sitemap). The XML count uses `FILTER EXISTS { ?w jolux:isRealizedBy/jolux:isEmbodiedBy/jolux:format <…/file-type/XML> }`.

| Decade | Acts | With XML |
|---|---|---|
| 1840s | 1,499 | 190 |
| 1900s | 2,167 | 1,056 |
| 1950s | 2,529 | 1,787 |
| 1980s | 5,983 | 3,157 |
| 1990s | 9,403 | 3,529 |
| 2000s | 12,653 | 7,343 |
| 2010s | 14,163 | 14,123 |
| 2020s | 5,384 | 5,384 |

By in-force status: in-force 64,536 (42,359 with XML), no-longer-in-force 16,320.

| In-force type | In force | With XML |
|---|---|---|
| LOI | 8,033 | 7,131 |
| RGD | 9,734 | 9,649 |
| RC (communal regs) | 21,612 | 5,749 |
| AGD | 4,327 | 4,056 |
| A | 5,314 | 3,681 |
| RMIN | 3,240 | 3,036 |

### 4.2 Consolidations and codes

There are 4,719 consolidations over 1,416 works, of which 4,606 have files. By `inForceStatus`:

| Status | Count |
|---|---|
| applicable | 1,181 (one per work) |
| not-applicable | 3,471 |
| not-yet-applicable | 29 |
| none | 38 |

Of the 1,181 applicable consolidations, **1,134 have XML**. The 47 without XML are:

- **PDF-only codes:** communal/20260801, education_nationale/20260915, environnement/20261003, environnement_annexes/20260314, route/20260612 (35,507,629 B PDF), securite_sociale/20260101 (5,059,985 B PDF).
- **PDF-only recueils.**
- **Old PDF-only consolidations.**
- **Placeholders with no file at all.** Example: `loi/1993/04/05/n1/consolide/20260711` is applicable, but every version from 20250101 onward has 0 manifestations, and the filestore URL guess returns 404. The last versions with text are 19991022 and 20031001 (PDF). The SPA bundle has `consolidation_in_progress` / `isFakeConsolidation` labels for these.

Current codes **with** XML+HTML+PDF: civil, commerce, consommation, fonction_publique, penal, procedure_civile, procedure_penale, sante, travail, and recueil/logement.

### 4.3 PDFs

- **Modern (2017+):** PDF/A, born digital. a88: Antenna House XSL Formatter, 6 pages, 207,518 B, 21,345 text characters.
- **Pre-2000 issue PDFs (per Mémorial issue, not per act):**
  - 1972 A72: Acrobat Distiller, 16 pages, 171,366 B, 54,087 text characters (text-based).
  - 1998 A72: 26 pages, 357,005 B, 53,753 characters (text-based).
  - 1995 A98: DigiPath, 225 pages, 8,383,965 B. Only pages 1–2 have text; pages 3+ are CCITT 200 dpi scans **with no OCR** (7,593 characters for 225 pages).
  - 1895 A7: ABBYY FineReader OCR over 200 dpi scans, 20 pages, 764,235 B, 43,641 characters, **bilingual two-column FR/DE** layout.
- **PDF-only codes:** text-based. Code de la sécurité sociale: 838 pages, 3,176,654 characters, extracts cleanly with `pdftotext -layout`.

### 4.4 Languages

`SELECT ?lang (COUNT(?e) AS ?n) WHERE { ?e a jolux:Expression ; jolux:language ?lang } GROUP BY ?lang` gives FRA 263,155, ENG 103, DEU 14, LTZ 2, and about 30 other languages with 1–5 each (treaty texts `/eli/etat/traite/YYYY/N/doc/tab/1/xx`). The 2023 Constitution has `/de` and `/lb` expressions. The current Constitution `consolide/20260829` has only `/fr`. 19th-century Mémorial PDFs contain German parallel text. **Plan for French only**, with the language as a column.

---

## 5. Size estimates

These come from 1-byte Range GETs on pseudo-random samples (`ORDER BY MD5(STR(?c))`).

- **20 current consolidated XMLs:** min 12,397, median 46,147, mean 61,601, max 168,971 B.
  - Estimate: 1,134 × ~62 KB ≈ 70 MB, plus the XML codes (civil 5.4 MB, travail 4.1 MB …). Total about **100–150 MB of raw XML** for all current consolidated law (estimate).
- **20 in-force JO XMLs (2000+):** min 6,355, median 8,629, mean 181,241, max 1,900,411 B. The outliers come from base64 images.
  - All 42,359 in-force acts with XML could be several GB raw (UNVERIFIED extrapolation from a skewed sample). Stripping images on ingest should cut the stored size by about an order of magnitude (UNVERIFIED).
- **Excluding RC** (communal regs, 21,612 in force) reduces the volume further.

---

## 6. Sitemaps (verified)

- The index (1,200 B) lists `sitemap-1..11.xml`, all with `<lastmod>2026-10-16</lastmod>`.
- Sitemaps 1–10 hold exactly 10,000 URLs each (1.58–1.69 MB). Sitemap 11 has 75 URLs (11,859 B). **Total 100,075.**
- **Content:**
  - 86,376 JO work URLs (`…/jo`)
  - 4,718 consolidation/code/recueil URLs
  - 7,784 parliamentary dossiers `/eli/dl/{pl,pr,ppl,ppc}/YYYY/N`
  - 1,183 treaties `/eli/etat/traite/...`
  - 12 editorial pages
- **Excluded:** Mémorial B (`/eli/etat/adm/`, robots-disallowed), manifestation or file URLs, and judicial decisions.
- **`lastmod`:** 67,638 entries carry 2022 (bulk migration). The files are roughly ordered by lastmod, and sitemap-11 holds 2026-10-12…15.
- **Verdict:** the sitemap is usable as a coarse discovery list, but **worse than SPARQL** for change detection. The graph query below showed 2026-10-16 changes that the sitemaps did not yet contain.

Change-feed query (returned 25 rows, newest `http://data.legilux.public.lu/eli/etat/traite/2026/3/graph | 2026-10-16T20:00:05.123+02:00`, and also `/eli/dl/...` and `/eli/jdec/juris_admin/court/2026/54597/graph`):

```sparql
PREFIX dcterms: <http://purl.org/dc/terms/>
SELECT ?g ?m WHERE { GRAPH ?g { ?g dcterms:modified ?m } FILTER(?m >= "2026-10-14T00:00:00Z"^^xsd:dateTime) } ORDER BY DESC(?m) LIMIT 25
```

---

## 7. Recommended ingestion design (format + parsing)

### 7.1 Selection ("current and relevant only")

1. **Works with consolidations.** For each work, take the consolidation that is `inForceStatus applicable` **and** has an XML manifestation.
   - If the applicable version has no file (placeholder), take the latest version that has files, and also ingest the JO XML of the amending acts published after it. The amending acts come from `jolux:modifies`/`passiveRef` (UNVERIFIED which predicate is most complete). Mark the document `consolidation_stale=true`.
   - Optionally keep the 29 `not-yet-applicable` versions as "upcoming law".
2. **In-force JO acts without any consolidation.** Take the JO XML, or if there is none, the PDF text layer. For whole-issue PDFs, segment by act using the issue's sommaire. There is no page-range metadata, except `memorialPage` in older XML meta.
3. **The six PDF-only codes and the PDF recueils.** Parse the PDF text with `pdftotext -layout` plus a regex on `^Art\. ([LR]\. )?\d+[-\w]*\.` and heading regexes. Mark them `source_format=pdf`.
4. **Skip:**
   - not-applicable consolidations and no-longer-in-force acts (keep a metadata row only, for "abrogé" answers)
   - DOCX (robots)
   - PDF/A when XML exists
   - Mémorial B
   - `scl:index`
   - base64 images (replace with `[image: alt]`)

### 7.2 Element → DB field mapping

| Source | DB field |
|---|---|
| SPARQL work URI (`/jo`, `/consolide/D`, `/code/x/D`) | `document.eli_version` (PK); `jolux:isMemberOf` → `document.eli_work` |
| SPARQL `jolux:title` (expression) or `preface/longTitle` | `document.title` (the consolidation longTitle is prefixed "Version consolidée applicable au 16/04/2026 : …") |
| SPARQL `typeDocument`, `dateDocument`, `publicationDate`, `dateEntryInForce`, `inForceStatus`, `dateApplicability`, `dateEndApplicability`, `isPartOf` (Mémorial), `responsibilityOf`, `subjectLevel1/2`, `cites`, `transposes`, `draft`, `basedOn`, `license` | `document.*` (**do not** take these from the XML meta) |
| `isExemplifiedBy` URL, `Last-Modified`, sha256, `<graph> dcterms:modified` | `document.source_url`, `source_last_modified`, `content_hash`, `graph_modified` |
| `book/part/title/chapter/section/subsection` @id, `num`, `heading` | `structure_node(id, kind, num, heading, parent_id, ord)`; article `path` (breadcrumb) |
| `article@id` | `article.xml_id` (stable anchor; HTML has the same ID; URL `https://legilux.public.lu/eli/...#art_x` UNVERIFIED) |
| `article/scl:JOLUXWork//scl:jolux[@scl:name='uriThis']` [1], [2], `dateApplicability` | `article.eli_work`, `article.eli_version`, `article.date_applicability` (consolidations and codes only; JO texts have none) |
| `article/num` | `article.num_label` ("Art. L. 121-1.") → `article.num_norm` ("L. 121-1") |
| `article/heading` | `article.heading`; if it matches `^\(\s*(L\.|R\.|Règl|A\.)…\)$`, store it in `article.provenance` |
| `scl:indexterms//scl:label` | `article.keywords[]` |
| `paragraph/num` ("(1)"), `alinea` order, `ol@scl:start` + `@scl:symbol` ("1°", "a)", "1.", "i)", "dash", "custom") or `li@scl:symbol` ("(2-1)", "2bis°") | `unit(level=paragraph/alinea/point, label)`, giving citations like "art. 2, par. (1), al. 2, point 3°". `paragraph@id` is stored but **not** used as an identifier |
| text (concatenated text nodes, `del` flagged, `noteRef`/`mod` stripped) | `article.text` + `article.text_md` (tables → markdown) → chunked for embeddings, mostly at article level and split by paragraph above about 2k tokens |
| `ref@href` (relative `/eli/...` → prefix `http://data.legilux.public.lu`; `http://data.europa.eu/eli/...` kept) | `reference(from_article, to_eli, anchor_text)` |
| `mod[@class='mod-start']@for` → `textualMod(@type, source@href, destination@href)`; `noteRef@href` → `note(@id, @marker, @refersTo, @title, text, ref)` | `modification(article, type, modifying_act_eli, mem_series, pub_date, act_date, mem_no)` from `note@title` "A\|2015-07-31\|2015-07-23\|L\|149" |
| `lifecycle/eventRef` | `document_event(date, type, source)` |
| `attachments/attachment/doc/mainBody` | annex units. Use articles if present, otherwise split on bold `ANNEXE …` paragraphs |
| `embeddedStructure` subtree | stays inside the parent article text as quoted text, flagged `contains_quoted_amendment`; **never** emitted as articles |

### 7.3 Sync loop

Run nightly or weekly:

1. Query SPARQL graphs with `dcterms:modified > last_run`.
2. Re-query their manifestations.
3. Do a conditional GET (`If-Modified-Since`) on each file, which returns 304 when nothing changed.
4. Re-parse the changed files.

Never use HEAD (403). Use `Range: bytes=0-0` for size checks.

At 1 request per second, the initial pull of about 1,200 consolidations + codes takes about 25 minutes. The pull of about 42k in-force JO XMLs takes about 12 hours, so run it as a one-time resumable job.

## 8. Things observed but out of scope or open

- **Judicial decisions** (`/eli/jdec/juris_admin/court/2026/54597`) are metadata-only in Legilux. They have `owl:sameAs https://ja.public.lu/54597C.pdf` and an ECLI.
- **SPARQL aggregate pitfall:** a `COUNT(DISTINCT)` with OPTIONAL+BIND returned 38,122 for 2020s acts, while the plain count is 5,384. Use FILTER EXISTS style queries for counts.
- **Elasticsearch proxy:** the SPA's search backend (`/elasticsearch/proxy`) was not explored, since it is the site's internal API.
