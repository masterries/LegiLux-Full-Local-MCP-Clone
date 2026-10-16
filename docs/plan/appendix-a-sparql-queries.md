# Appendix A — SPARQL queries used by the pipeline

This appendix belongs to [PLAN.md](../PLAN.md) (§2.2, §6.7). Every query below ran live against `https://data.legilux.public.lu/sparqlendpoint` on 2026-10-16.

**Status markers**
- **[V]** — tested exactly as printed.
- **[V-adapted]** — the tested text with a trivial change (a projection or an `ORDER BY` removed). These are re-run in M1 and their row counts recorded before the pipeline relies on them.

**Source files**
- Tested query files: `research/sparql-jolux-model/catalog/C01–C16`, `research/gaps-and-reconciliation/q/*`, `research/scope-sizing/*.rq`, and the plan checks `plan/verify/q_manif.rq` and `plan/verify/w/*.rq`.
- In the repository the pipeline queries live in `sparql/` (Q01–Q18 and Q-MANIF) and `sparql/checks/` (probes and golden reproductions).

## A.0 Conventions (all queries)

**Request**
- `POST` to `https://data.legilux.public.lu/sparqlendpoint`, `application/x-www-form-urlencoded`, field `query`.
- `Accept: text/csv` for SELECT, `application/n-triples` for CONSTRUCT.
- User-Agent `LegiLux-Local-Clone/<version> (+mailto:<operator contact>; <=1 req/s)`.
- Sequential requests, ≥ 1.2 s apart.

**Query shape**
- **Flat and unsorted.** The store is a union default graph, so rows repeat; always use `DISTINCT`. Join on the client: server-side joins with GROUP BY and EXISTS hit the ≈ 45 s gateway timeout [V].
- **Do not use `ORDER BY` with `LIMIT`/`OFFSET` past 10,000 rows.** Virtuoso refuses it with error SR353 (A.5) [V]. `ORDER BY` without a LIMIT did work: C07 returned 36,875 sorted rows [V J10]. Bulk pulls still omit it.
- **Parse all datetimes on the client.** Some `dct:modified` literals are malformed (`…472Z+02:00`). `YEAR()` and `MAX()` on them fail or misbehave [V]. `YEAR()` on `jolux:dateDocument` (a plain `xsd:date`) works [V].
- **Never parse dates from URIs.** Example: `recueil/sites_monuments/20142209` [V].
- **Rewrite file URLs.** File identifiers `http://data.legilux.public.lu/filestore/…` are fetched as `https://legilux.public.lu/filestore/…`, after the robots check and the denylist check.

**Completeness and fallback**
- Every full pull is followed by a completeness check (Q17). The distinct subjects in the pull must equal `COUNT(DISTINCT …)`.
- If single-request pulls ever break (a row ceiling or a rate limit appears), the same SELECT is split:
  - by year of `jolux:dateDocument`, in windows of fewer than 10k rows (see Q07-fallback); or
  - by type; or
  - into `VALUES` batches of ≤ 500 IRIs sent by POST.

  `ORDER BY` is used only inside a partition of fewer than 10k rows.

Common prefixes, prepended to every query:

```sparql
PREFIX jolux: <http://data.legilux.public.lu/resource/ontology/jolux#>
PREFIX dct:   <http://purl.org/dc/terms/>
PREFIX xsd:   <http://www.w3.org/2001/XMLSchema#>
PREFIX skos:  <http://www.w3.org/2004/02/skos/core#>
PREFIX rdf:   <http://www.w3.org/1999/02/22-rdf-syntax-ns#>
```

## A.1 Overview and cadence

**Cadence key**
- **N** — every nightly run.
- **W** — weekly release build; every release build runs on fresh full pulls.
- **M** — monthly.
- **D** — on demand.

| ID | Repo file | Source (tested) | Purpose | Result on 2026-10-16 | Cadence |
|---|---|---|---|---|---|
| Q01 | `q01_acts.rq` | GR `q/a1.rq` | Act table: act, work, type, raw status, dei, dnl, publication date | **151,253 rows** (151,250 distinct acts), 46,136,891 B, **5.18 s** [V] | W |
| Q02 | `q02_consolidations.rq` | GR `q/c1.rq` | All consolidation versions with window, flag, type, created, publication date | **4,719 rows**, 1,638,807 B, ≈ 1.3 s [V] | N |
| Q03 | `q03_conso_formats.rq` | GR `q/c2.rq` | Formats, licence and language per consolidation | 13,679 rows, 3,541,126 B [V] | N |
| Q04 | `q04_conso_base_acts.rq` | GR `q/c3.rq` | Base acts (status, type, dnl) of consolidated works | 1,370 rows, 386,788 B [V] | N |
| Q05 | `q05_rel_modifies_repeals.rq` | GR `q/m2.rq` | modifies/repeals edges with the source's dei | **47,428 rows**, 9,894,740 B [V] | W |
| Q06 | `q06_rel_other.rq` | plan check P2 | basedOn, cites, rectifies, transposes edges | **169,243 rows**, 32,998,420 B, 3.64 s [V] | W |
| Q07 | `q07_titles.rq` | plan check P3 | French expression titles of all `/eli/etat/leg/` acts (display, temporary-traffic rule, naturalisation rule, aliases) | **80,906 rows / acts**, 18,611,429 B, 2.94 s [V] | W |
| Q07-fallback | `q07_titles_by_year.rq` | scope-sizing `qa.rq` | The same in 10 year windows (≈ 8–9k rows each) | 80,922 rows in 10 chunks [V] | fallback |
| Q08 | `q08_memorial_refs.rq` | GR `q/histid.rq` | Mémorial issue (`jolux:isPartOf`) and `historicalLegalId` → « Mém. A n° … de … » | 71,455 rows, 10,136,001 B [V] | W |
| Q09 | `checks/q09_licence_crosstab.rq` | GR `q/l4.rq` | Licence × format for in-force LOI/RGD/AGD/RMIN (licence-distribution gate) | 72,742 rows → 28,703 acts, 14,182,255 B [V] | W |
| Q10 | `q10_vocabularies.construct.rq` | SJ `C03` | SKOS concepts of 6 schemes | **15,253 triples**, 2,422,346 B, 1.30 s [V] | W |
| Q11 | `q11_graph_watermark.rq` | SJ `C09` | Graphs with `dct:modified ≥ W` (W = last_run − 2 days) | 439 rows since 2026-09-01, 0.85 s; 12 malformed false positives [V] | N |
| Q12 | `q12_boundary_sweep.rq` | plan check P1 | Acts (dei, dnl) and consolidations (dA, dEnd) crossing [last_run, T + 60 d] | 261 rows (40 in leg), 22,452 B, 0.76 s for 2026-10-09..12-15 [V] | N |
| Q13 | `q13_new_relations.rq` | SJ `C14` | Relations of acts published since W (monitor) | modifies 43 → 42 targets; repeals 143 → 144; basedOn 328 → 154; cites 45 → 206; rectifies 2 → 2; transposes 2 → 2 [V] | N |
| Q14 | `checks/q14_stubs.rq` | SJ `C06` | Consolidations without expressions (stub gate) | 113 rows, 0.55 s [V] | N |
| Q15 | `checks/q15_codes.rq` | SJ `C13` | Applicable codes, code-recueils and recueils with titles (CODE/Constitution gate) | 52 rows, 0.7 s [V] | N |
| Q16 | `q16_construct_graphs.construct.rq` | SJ `C15` | Full refresh of the graphs of specific works | 2 graphs → 149 triples [V] | D |
| Q17 | `checks/q17_completeness.rq` | GR `q/classes_distinct.rq` | Completeness of the unsorted pulls | Act 151,250 / 151,250; Consolidation 4,719 / 4,719 [V] | with every Q01/Q02 pull |
| Q18 | `checks/q18_impact.rq` | GR `q/i3.rq` | `LegalResourceImpact` (hint only; partial coverage) | 379 "modifie", 5 "rectifie" [V] | W (report) |
| Q-MANIF | `q_manif.rq` | `plan/verify/q_manif.rq` | File URL + licence + format + `dct:modified` of selected works (VALUES ≤ 500, POST) | 5 works → 17 rows, 0.69 s [V] | N (changed works), M (all ≈ 26k candidates, ≈ 55 batches) |

**Request budget**

| Run | Requests | Data | Time at the endpoint [E] |
|---|---|---|---|
| Nightly | ≈ 8 SPARQL requests + Q-MANIF batches for changed works | ≈ 6 MB | < 10 s |
| Weekly | ≈ 12 full pulls | ≈ 140 MB | < 40 s |
| Monthly full Q-MANIF | ≈ 55 POSTs | ≈ 23 MB [E] | ≈ 1 min at 1 req/s |

## A.2 Full-table pulls (selection inputs)

### Q01 — act table (GR `a1`) [V]
```sparql
SELECT DISTINCT ?a ?cw ?type ?st ?dei ?dnl ?pub WHERE {
 ?a a jolux:Act ; jolux:isMemberOf ?cw ; jolux:typeDocument ?type ; jolux:inForceStatus ?st .
 OPTIONAL { ?a jolux:dateEntryInForce ?dei } OPTIONAL { ?a jolux:dateNoLongerInForce ?dnl } OPTIONAL { ?a jolux:publicationDate ?pub } }
```
**Caveats**
- There are 3 more rows than distinct acts. `dateEntryInForce` has 151,252 values over 151,249 acts. The client keeps `dei_min`, `dei_count` and the flag `entry_dates_multiple` [V].
- Status is **recomputed from dates** (PLAN §2.2). `?st` is kept raw.
- The 50 `no-longer-in-force-implicit` acts have no dnl [V].

### Q02 — consolidation versions (GR `c1`) [V]
```sparql
SELECT DISTINCT ?c ?cw ?d ?end ?st ?type ?created ?pub WHERE {
 ?c a jolux:Consolidation ; jolux:isMemberOf ?cw ; jolux:dateApplicability ?d .
 OPTIONAL { ?c jolux:dateEndApplicability ?end } OPTIONAL { ?c jolux:inForceStatus ?st }
 OPTIONAL { ?c jolux:typeDocument ?type } OPTIONAL { ?c dct:created ?created } OPTIONAL { ?c jolux:publicationDate ?pub } }
```
Drives the window rule. Results [V]:
- 1,213 works have exactly one window-current version.
- 34 future versions over 18 works.
- 38 versions have no status and 55 have no type. The type of these comes from the base act.

### Q03 — consolidation formats, licence, language (GR `c2`) [V]
```sparql
SELECT DISTINCT ?c ?uf ?lic ?lang WHERE {
 ?c a jolux:Consolidation ; jolux:isRealizedBy ?e . OPTIONAL { ?e jolux:language ?lang }
 OPTIONAL { ?e jolux:isEmbodiedBy ?m . ?m jolux:userFormat ?uf . OPTIONAL { ?m jolux:license ?lic } } }
```
**Caveats**
- A consolidation with **no row here is a stub**. The window-current set has 49 stubs: 15 flagged applicable and 34 with no status [V].
- XML in window-current versions: 1,132. PDF-only: 32 [V].

### Q04 — base acts of consolidated works (GR `c3`) [V]
```sparql
SELECT DISTINCT ?cw ?act ?ast ?atype ?dnl WHERE {
 ?c a jolux:Consolidation ; jolux:isMemberOf ?cw . ?act a jolux:Act ; jolux:isMemberOf ?cw .
 OPTIONAL { ?act jolux:inForceStatus ?ast } OPTIONAL { ?act jolux:typeDocument ?atype } OPTIONAL { ?act jolux:dateNoLongerInForce ?dnl } }
```
**Caveats**
- 37 RECUEIL and 7 CODE_RECUEIL works have no base act [V].
- 35 window-current consolidations have a repealed base act [V].

### Q05 — modifies / repeals edges (GR `m2`) [V]
```sparql
SELECT DISTINCT ?src ?rel ?tgt ?dei WHERE {
 VALUES ?rel { jolux:modifies jolux:repeals }
 ?src ?rel ?tgt . OPTIONAL { ?src jolux:dateEntryInForce ?dei } }
```
**Caveats**
- Targets are `/jo` act URIs.
- 12,065 repealed Mémorial A acts have a named repealer [V].
- The RDF edges may be incomplete. They are cross-checked against AKN `ref@href` targets in amending acts (PLAN R4).

### Q06 — basedOn / cites / rectifies / transposes edges (plan check P2) [V]
```sparql
SELECT DISTINCT ?src ?rel ?tgt WHERE {
  VALUES ?rel { jolux:basedOn jolux:cites jolux:rectifies jolux:transposes }
  ?src a jolux:Act ; ?rel ?tgt . }
```
**Result:** basedOn 86,315, cites 77,981, transposes 3,354 and rectifies 1,593 rows. These match the predicate counts of the db track. 132,377 rows have a `/etat/leg/` source.

**Caveats**
- Out-of-scope sources (Mémorial B) are dropped on the client. EU targets are kept as ELI strings.
- `base_legale_abrogee` = every basedOn target not live at T. This gives 1,586 RGD (golden G-b1 below).

### Q07 — French titles of Mémorial A acts (plan check P3) [V]
```sparql
SELECT DISTINCT ?a ?title WHERE {
  ?a a jolux:Act ; jolux:isRealizedBy ?e .
  ?e jolux:language <http://publications.europa.eu/resource/authority/language/FRA> ; jolux:title ?title .
  FILTER(STRSTARTS(STR(?a), "http://data.legilux.public.lu/eli/etat/leg/")) }
```
**Uses (client side)**
- The naturalisation rule (`(confér|accord)\w* la naturalisation` + lawyer review).
- The temporary-traffic rule ("réglementation temporaire de la circulation", no dnl).
- Display titles.

80,906 acts have a French title, out of 80,918 Mémorial A acts. The rest keep the type + date label.

**Q07-fallback** — scope-sizing `qa.rq`, run in 10 year windows (1617–1902 … 2021–2026, 8,925 … 4,269 rows) [V]:
```sparql
SELECT ?jo ?w ?t ?status ?d ?dnl ?title WHERE { ?jo a jolux:Act ; jolux:typeDocument ?t ; jolux:inForceStatus ?status ;
  jolux:isMemberOf ?w ; jolux:dateDocument ?d . OPTIONAL { ?jo jolux:dateNoLongerInForce ?dnl }
  OPTIONAL { ?jo jolux:isRealizedBy ?e . ?e jolux:title ?title }
  FILTER(CONTAINS(STR(?jo),"/eli/etat/leg/")) FILTER(YEAR(?d) >= %Y0% && YEAR(?d) <= %Y1%) }
```

### Q08 — Mémorial references (GR `histid`) [V]
```sparql
SELECT ?a ?h ?mem WHERE { ?a a jolux:Act ; jolux:historicalLegalId ?h . OPTIONAL { ?a jolux:isPartOf ?mem }
  FILTER(CONTAINS(STR(?a),"/eli/etat/leg/")) }
```
- `?mem` such as `…/eli/etat/leg/memorial/1997/a36` is rendered « Mémorial A n° 36 de 1997 ».
- Acts without `historicalLegalId` fall back to `jolux:isPartOf` from Q-MANIF/Q16 when needed.

### Q09 — licence cross-check (GR `l4`) [V]
```sparql
SELECT ?a ?uf ?lic WHERE {
 ?a a jolux:Act ; jolux:inForceStatus <http://data.legilux.public.lu/resource/authority/application-status/in-force> ;
    jolux:typeDocument ?t ; jolux:isRealizedBy ?e . ?e jolux:isEmbodiedBy ?m . ?m jolux:userFormat ?uf .
 FILTER(?t IN (<http://data.legilux.public.lu/resource/authority/resource-type/LOI>,<http://data.legilux.public.lu/resource/authority/resource-type/RGD>,
               <http://data.legilux.public.lu/resource/authority/resource-type/AGD>,<http://data.legilux.public.lu/resource/authority/resource-type/RMIN>))
 FILTER(?uf IN (<http://data.legilux.public.lu/resource/authority/user-format/html>,<http://data.legilux.public.lu/resource/authority/user-format/xml>,
                <http://data.legilux.public.lu/resource/authority/user-format/xml-akomantoso>,<http://data.legilux.public.lu/resource/authority/user-format/pdf>))
 OPTIONAL { ?m jolux:license ?lic } }
```
**Result:** 8,115 CC-BY XML; 15,502 CC-BY HTML only; 619 text only licenceSCL; 4,442 PDF-only licenceSCL; 25 PDF-only CC-BY [V].

**Use:** a weekly gate on the licence distribution. The per-file licence decision uses Q-MANIF.

### Q10 — vocabularies (SJ `C03`) [V]
```sparql
CONSTRUCT { ?c a skos:Concept ; skos:inScheme ?scheme ; skos:prefLabel ?l ; skos:notation ?n ; skos:broader ?b ; skos:exactMatch ?x }
WHERE {
  VALUES ?scheme {
    <http://data.legilux.public.lu/resource/authority/resource-type>
    <http://data.legilux.public.lu/resource/authority/application-status>
    <http://data.legilux.public.lu/resource/authority/legal-subject>
    <http://data.legilux.public.lu/resource/authority/legal-institution>
    <http://data.legilux.public.lu/resource/authority/user-format>
    <http://data.legilux.public.lu/resource/authority/license> }
  ?c skos:inScheme ?scheme .
  OPTIONAL { ?c skos:prefLabel ?l } OPTIONAL { ?c skos:notation ?n } OPTIONAL { ?c skos:broader ?b } OPTIONAL { ?c skos:exactMatch ?x } }
```
The `person` scheme is deliberately not exported (minimisation).

### Q-MANIF — manifestations of selected works (VALUES ≤ 500, POST) [V]
```sparql
SELECT DISTINCT ?w ?uf ?lic ?file ?mod WHERE {
  VALUES ?w {
    <http://data.legilux.public.lu/eli/etat/leg/loi/2018/08/01/a686/consolide/20251226>
    <http://data.legilux.public.lu/eli/etat/leg/code/route/20260612>
    <http://data.legilux.public.lu/eli/etat/leg/loi/2023/04/19/a203/jo>
    <http://data.legilux.public.lu/eli/etat/leg/rgd/2015/06/25/n12/jo>
    <http://data.legilux.public.lu/eli/etat/leg/rgd/2003/01/24/n2/jo> }      # production: ≤ 500 version/act IRIs per POST
  ?w jolux:isRealizedBy ?e .
  ?e jolux:language <http://publications.europa.eu/resource/authority/language/FRA> ; jolux:isEmbodiedBy ?m .
  ?m jolux:userFormat ?uf ; jolux:isExemplifiedBy ?file .
  OPTIONAL { ?m jolux:license ?lic }
  OPTIONAL { ?m dct:modified ?mod } }
```
**Result:** 17 rows in 0.69 s.

**Caveats**
- `rgd/2015/06/25/n12/jo` and `rgd/2003/01/24/n2/jo` have CC-BY **only on DOCX**, which robots.txt disallows. Both become `metadata_only` (reason `licence`) [V J10].
- Older PDF manifestations point to **whole Mémorial issues** (e.g. `memorial/2003/a19`). These are never fetched as act text in v1.
- The licence-aware picker is: CC-BY XML → CC-BY HTML → CC-BY born-digital PDF (allowlisted code/consolidation PDFs only) → metadata-only.
- The batch size of 500 was used by prior art. The 5-IRI test shows the shape; M1 records the row count and time of a 500-IRI batch.

## A.3 Incremental queries (nightly)

### Q11 — graph watermark (SJ `C09`, [V-adapted]: the tested `BIND(IRI(REPLACE(…)))` projection is removed)
```sparql
SELECT ?g ?modified WHERE {
  GRAPH ?g { ?g dct:modified ?modified }
  FILTER(?modified >= "2026-09-01T00:00:00Z"^^xsd:dateTime)        # W = last_run − 2 days
  FILTER(STRSTARTS(STR(?g), "http://data.legilux.public.lu/eli/etat/leg/")) }
```
**Client side**
- Strip a trailing `/graph` to get the work or version IRI. Server-side `REPLACE` with a regex returned its input unchanged [V J10].
- Parse `?modified` and drop malformed literals. There were 12 of 439 in the test.
- Works whose graph changed but none of whose pulled columns changed are refreshed with Q16 and logged.
- The tested query also had `ORDER BY ?modified` with no LIMIT. That is not capped, but it is dropped here as unnecessary.

### Q12 — date-boundary sweep, acts and consolidations (plan check P1) [V]
```sparql
SELECT DISTINCT ?x ?kind ?d WHERE {
  { ?x a jolux:Act ; jolux:dateNoLongerInForce ?d . BIND("dnl" AS ?kind) } UNION
  { ?x a jolux:Act ; jolux:dateEntryInForce ?d . BIND("dei" AS ?kind) } UNION
  { ?x a jolux:Consolidation ; jolux:dateApplicability ?d . BIND("da" AS ?kind) } UNION
  { ?x a jolux:Consolidation ; jolux:dateEndApplicability ?d . BIND("dea" AS ?kind) }
  FILTER(?d >= "2026-10-09"^^xsd:date && ?d <= "2026-12-15"^^xsd:date) }   # [last_run, T + 60 d]
```
**Result:** 261 rows. In leg: da 8, dea 9, dei 17, dnl 6. The rest are adm rows, which the client drops.

**Why it exists**
- Status flips are **silent**. 266 of the 269 acts that ceased between 2026-09-01 and 10-16 had no graph bump [V].
- Consolidation flags flip on `dateApplicability`, and versions are often pre-published [V].
- The runtime computes status from dates anyway. This sweep (a) catches metadata edits that **move** dates, and (b) feeds `RELEASE_NOTES.md` ("changements de statut attendus dans les 60 jours").

**Fallback:** the four branches run as separate queries, i.e. the tested C10 pattern for acts plus its consolidation analogue.

### Q13 — relations of new acts (SJ `C14`) [V]
```sparql
SELECT ?rel (COUNT(DISTINCT ?new) AS ?newActs) (COUNT(DISTINCT ?target) AS ?affected) WHERE {
  VALUES ?rel { jolux:modifies jolux:repeals jolux:rectifies jolux:basedOn jolux:cites jolux:transposes }
  ?new a jolux:Act ; jolux:publicationDate ?pd ; ?rel ?target .
  FILTER(?pd >= "2026-09-01"^^xsd:date) } GROUP BY ?rel
```
This is a monitor. Its non-aggregated form (`SELECT DISTINCT ?new ?rel ?target`) lists the targets whose `text_basis` may change. For example, a new `modifies` edge turns `original_never_amended` into `original_possibly_outdated`. The full edge tables are re-pulled weekly.

### Q14 — stub consolidations (SJ `C06`) [V]
```sparql
SELECT DISTINCT ?stub ?cw ?d ?flag WHERE {
  ?stub a jolux:Consolidation ; jolux:isMemberOf ?cw ; jolux:dateApplicability ?d .
  OPTIONAL { ?stub jolux:inForceStatus ?flag }
  FILTER NOT EXISTS { ?stub jolux:isRealizedBy ?e } }
```
**Gate:** stubs may not grow by more than 20 in one run.

### Q15 — applicable codes and recueils (SJ `C13`) [V]
```sparql
SELECT DISTINCT ?c ?type ?d ?title WHERE {
  ?c a jolux:Consolidation ; jolux:inForceStatus <http://data.legilux.public.lu/resource/authority/application-status/applicable> ;
     jolux:typeDocument ?type ; jolux:dateApplicability ?d .
  FILTER(?type IN (<http://data.legilux.public.lu/resource/authority/resource-type/RECUEIL>,
                   <http://data.legilux.public.lu/resource/authority/resource-type/CODE_RECUEIL>,
                   <http://data.legilux.public.lu/resource/authority/resource-type/CODE>))
  OPTIONAL { ?c jolux:isRealizedBy/jolux:title ?title } } ORDER BY ?type ?c
```
Fewer than 100 rows, so the sort is safe. This query relies on the flag, but only as a **cross-check**: every CODE and the Constitution must have stored text, or the gate fails.

### Q16 — CONSTRUCT of selected graphs (SJ `C15`) [V]
```sparql
CONSTRUCT { ?s ?p ?o } WHERE { VALUES ?g { <http://data.legilux.public.lu/eli/etat/leg/loi/2018/08/01/a686/jo/graph>
                                           <http://data.legilux.public.lu/eli/etat/leg/loi/2018/08/01/a686/consolide/20251226/graph> }
                               GRAPH ?g { ?s ?p ?o } }
```
DESCRIBE is broken (SP031) and CONSTRUCT works [V].

### Q17 — completeness check (GR `classes_distinct`) [V]
```sparql
SELECT ?c (COUNT(?s) AS ?rows) (COUNT(DISTINCT ?s) AS ?distinct) WHERE {
 VALUES ?c { jolux:Act jolux:Consolidation }
 ?s a ?c } GROUP BY ?c
```
**Gate:** the distinct subjects in Q01/Q02 must equal `?distinct` (151,250 / 4,719 at T0). Otherwise the build switches to partitioned pulls.

**Caution:** `jolux:Article` returns 58,673 rows but only 15,770 distinct subjects. That graph is stale and is never used [V].

### Q18 — LegalResourceImpact (GR `i3`) [V], hint only
```sparql
SELECT ?type ?lbl (COUNT(DISTINCT ?s) AS ?n) (MIN(?p) AS ?minPub) (MAX(?p) AS ?maxPub) (SUM(IF(BOUND(?cb),1,0)) AS ?consolidated) WHERE {
 ?s a jolux:LegalResourceImpact ; jolux:legalResourceImpactHasType ?type ; jolux:publicationDate ?p .
 OPTIONAL { ?type skos:prefLabel ?lbl } OPTIONAL { ?s jolux:impactConsolidatedBy ?cb } } GROUP BY ?type ?lbl
```
The result covers only 384 impacts since 2023-07. It is never used as a completeness check.

## A.4 Golden-number reproductions (frozen 2026-10-16 snapshot; tests only, not production)

The outputs of these queries (`research/gaps-and-reconciliation/out/*`) form the **frozen snapshot**. The M1 selection tests assert the golden numbers in PLAN §11.5 against it.

| ID | Source | What it reproduces |
|---|---|---|
| G-b1 | GR `q/b1.rq`: `SELECT DISTINCT ?a ?b WHERE { ?a jolux:typeDocument <…/resource-type/RGD> ; jolux:inForceStatus <…/application-status/in-force> ; jolux:basedOn ?b . }` | 13,702 rows / 9,361 RGD → 1,586 with every legal base repealed [V] |
| G-tt1 | GR `q/tt1.rq` (in-force RGD titles + dateDocument + dnl) | 9,747 rows → 663 temporary traffic RGDs, all without dnl [V] |
| G-m1 | GR `q/m1.rq` (edges into consolidated works + source dei/pub/type) | 7,693 rows → 13 lagging of 1,129 usable [V] |
| G-natur | GR `q/natur.rq` (LOI titles with "naturalisation" + "conférant\|accordant", grouped by status) | 511 in force [V] |
| G-natur2 | GR `q/natur2.rq` ("naturalisation" without those words) | 460 in force, e.g. « … qui accorde la naturalisation au sieur Theisen … » [V]; goes to lawyer review |
| G-t08 | GR `q/t08.rq` (acts with dnl in 2026-09-01..12-31 × status × graph-modified before/after dnl) | 266 / 3 past; 341 future still in force [V] |
| G-f1 | GR `q/f1.rq` (consolidations with dA in 2026-09-20..10-16 + graph modified) | Pre-publication, e.g. `rgd/2011/02/27/n10/consolide/20261011` (graph 2026-10-08) [V] |
| G-legalvalue | GR `q/legalvalue.rq` | officiel: 3,190 consolidation / 113,209 Mémorial A manifestations; definitif 8,660 [V] |

## A.5 Endpoint probes (regression checks, run in M1 and then quarterly)

| Probe | Query | Expected [V] |
|---|---|---|
| `cap_sorted_off10k` | `SELECT ?a WHERE { ?a a jolux:Act } ORDER BY ?a LIMIT 10000 OFFSET 10000` | HTTP 500 `Virtuoso 22023 Error SR353: Sorted TOP clause specifies more then 20000 rows to sort. Only 10000 are allowed.` |
| `cap_sorted_lim20k` | `… ORDER BY ?a LIMIT 20000` | SR353 |
| `cap_offset_9990` | `… ORDER BY ?s LIMIT 10 OFFSET 9990` | HTTP 200 |
| `cap_unsorted` | `SELECT ?a WHERE { ?a a jolux:Act } LIMIT 20000` | 20,000 rows (1,328,868 B). With `LIMIT 200000`: 151,250 distinct rows, 10,066,663 B, 2.17 s |

If a probe changes, the pipeline switches to partitioned pulls and opens an issue.

## A.6 Reference queries (superseded; kept for regression only)

- **SJ `C04`** — latest consolidation with files and `dA ≤ T`, per work: 5,122 rows / 1,375 consolidations / 1.56 s [V].
  - **Not the current rule.** After dropping repealed base acts it yields 1,179 = 1,129 usable + 10 stub fallbacks + **40 discontinued** consolidations, which must not be presented as current.
- **SJ `C07`** — in-force core-scope acts without a usable consolidation: **36,875 rows / 36,872 acts / 31,291,340 B / 5.7 s**, run with `ORDER BY ?act` and **no LIMIT** [V J10].
  - Its 34-type list is broader than the plan's 18 text types.
  - It flags 5,697 scan-only acts (Tier 2).
  - It is replaced by client-side selection over Q01 + Q02 + Q-MANIF.
- **SJ `C10`** (act-only boundary sweep, 79 leg rows for 2026-09-01..10-16) and **SJ `C11`** (change summary, 6 reasons, 0.41 s) — superseded by Q12 and the snapshot diff.
