# db-retrieval-design

## Recommendations
- Use one PostgreSQL 18 database as the only primary store. Default image: paradedb/paradedb:0.26.2-pg18 (pg_search BM25 with a French stemmer, plus pgvector). If AGPL is not acceptable, build from pgvector/pgvector:0.8.7-pg18-trixie and add pg_textsearch 1.5.0. Pin image digests and pull through a mirror or `docker save` to avoid Docker Hub rate limits.
- Store embeddings as halfvec(1024) (bge-m3 by default, Qwen3-Embedding-0.6B as alternative; both 1024-d with permissive licenses). Put a partial HNSW index on the in-force, non-repealed chunks (m=16, ef_construction=128); at query time set hnsw.ef_search=100 and hnsw.iterative_scan=relaxed_order.
- Compute embeddings once, on a GPU, on the connected side, and ship them inside the dump. At the airgapped site, embed only queries (about 50 ms on CPU) and rerank with TEI cpu-1.9.4. On CPU, rerank only the top ~20 candidates truncated to ~512 tokens, or give the reranker a GPU.
- Lexical branch: BM25 ranks on header and body. Keep native tsvector columns (fr_legal with unaccent and french_stem; simple_ua for exact tokens) to match identifiers such as '2016/679' or 'L. 121-1'. Ship a custom unaccented French stopword dictionary.
- Retrieval pipeline: citation parser for exact hits, then BM25 + dense + exact-token candidates, fused with RRF (k=60) in a single SQL query, then a cross-encoder reranker, then context assembly at article level with breadcrumbs, version date, text_basis and relations.
- One chunk per article, with a breadcrumb header and scl:indexterms. Split articles over ~1,500 tokens at <paragraph> boundaries and repeat the header. Never merge short articles. Do not embed preambles, parcel or table annexes, or repealed text.
- Keep only the current text. For each act use the current consolidation (dateEndApplicability IS NULL), or else the /jo original, and set text_basis to 'original_possibly_outdated' when the act has inbound jolux:modifies but no consolidation (about 738 in-force laws). Keep metadata-only rows for all versions.
- Resolve citations deterministically: ELI, then Mémorial reference, then code abbreviations in act_alias, then (type, date) candidates ranked by trigram similarity to the rest of the title, with a curated acronym list (CNPD, RGPD, CSSF, ...). Return the ambiguity instead of guessing: 74% of in-force laws share their date with another law.
- The MCP tools should return structured citation objects (act/article/version ELIs, consolidation date, status, text_basis, CC BY 4.0 attribution). Add a post-generation check that rejects citations not in the retrieved set and quotes that are not verbatim.
- Build a frozen, versioned evaluation set: about 500 auto-generated citation-resolution cases, 300–500 LLM-generated retrieval questions validated by a lawyer, and temporal/negative cases. Track Recall@k, MRR, nDCG, citation precision/recall and groundedness, and block a database release if they regress.
- Use pg_dump -Fc -Z zstd as the single transfer file, with an optional pg_basebackup volume tarball to avoid rebuilding HNSW on restore. Mount the PG18 volume at /var/lib/postgresql (PGDATA /var/lib/postgresql/18/docker).
- Treat RDF only as an optional sidecar: an Oxigraph 0.5.11 compose profile holding a filtered N-Triples export of current-act metadata, for SPARQL users. The MCP server should query SQL.

## Risks / open questions
- pg_search is AGPL-3.0. Internal, unmodified use is usually fine, but the deploying organisation must confirm. The permissive fallback pg_textsearch was verified from its README only; it was not built or benchmarked here (GitHub release downloads were blocked in this sandbox).
- ParadeDB's own vector search inside its index is beta (docs say 'beta feature available in versions 0.25.0 and above'), so the design uses standard pgvector HNSW. The ParadeDB image ships pgvector 0.8.6, not 0.8.7; 0.8.7 only fixes an IVFFlat issue.
- Robots.txt and permission for fetching full text: data.legilux.public.lu disallows /filestore/*, while legilux.public.lu/filestore/ (which the website itself uses) is not disallowed. The ingestion track should confirm the policy, and possibly the polite rate, with the SCL. HEAD requests get 403, so change detection must rely on SPARQL dct:modified or ranged GETs.
- About 738 in-force laws were amended but have no current consolidation, so their stored text may be outdated. The answer layer must warn. Building our own consolidations is out of scope and risky.
- Corpus scope decisions (PA, RC, AMIN/AGD, temporary traffic RGDs, large annexes) change the size by an order of magnitude. The 100k–200k chunk and 2–5 GB estimates are inferred from small samples.
- CPU-only airgapped hardware makes cross-encoder reranking slow (10–17 s for 40 candidates measured). Decide on GPU availability, or rely on the LLM for final selection.
- Citation-parser gaps: an act named before the article, acronyms and informal names, and older citation styles (e.g. 'Mém. 1972, 1909'). These need an alias dictionary curated by a lawyer.
- The French stemmers over-stem in places (ParadeDB turned 'données' into 'don'), and with unaccent first, accented stopwords leak through. The effect on ranking quality should be measured in the evaluation.
- The RRF results come from a 363-article prototype with 3 questions, so they show how the pieces behave but are not statistically meaningful. The embedding model choice (bge-m3 vs Qwen3-0.6B vs e5-large) needs the full evaluation set.
- Article numbering differs between the ELI and AKN ids ('art_9_10' vs 'art_9_et_10', 'art_17--20'). Joining SPARQL article URIs to XML units needs normalisation, and the RDF article resources stop at 2021.
- Docker Hub rate limiting (429) was seen through the shared proxy. Air-gap bundle builds should use mirror.gcr.io or an authenticated pull and record digests.
- Possible evaluation data sources (e.g. public FAQ pages citing articles) have not been checked for license or terms of reuse.

## Verified facts
- pgvector latest is 0.8.7 (2026-10-01); Docker tags pgvector/pgvector:0.8.7-pg18-trixie (159 MB), 0.8.7-pg17-trixie (158 MB) and pg18/pg17 bookworm variants exist  
  _evidence:_ GET https://hub.docker.com/v2/repositories/pgvector/pgvector/tags?page_size=60&ordering=last_updated -> '0.8.7-pg18-trixie 2026-10-01 159 MB'; https://raw.githubusercontent.com/pgvector/pgvector/master/CHANGELOG.md -> '## 0.8.7 (2026-10-01) - Fixed buffer overflow with IVFFlat index build'
- pgvector HNSW can index vector up to 2,000 dims and halfvec up to 4,000 dims; iterative index scans (hnsw.iterative_scan = strict_order|relaxed_order) since 0.8.0; README recommends RRF or cross-encoder for hybrid  
  _evidence:_ pgvector README lines 252-253: '- `vector` - up to 2,000 dimensions / - `halfvec` - up to 4,000 dimensions'; line 487: 'SET hnsw.iterative_scan = strict_order;'; line 642: 'You can use Reciprocal Rank Fusion ... or a cross-encoder'
- ParadeDB latest image paradedb/paradedb:0.26.2-pg18 (354 MB, 2026-10-16) bundles pg_search 0.26.2, vector 0.8.6, postgis 3.6.4, pg_cron 1.6, pg_ivm 1.13 on PostgreSQL 18.6; pg_search is AGPL-3.0  
  _evidence:_ Docker Hub tags API: '0.26.2-pg18 2026-10-16 354 MB'; in container: select from pg_available_extensions -> 'pg_search|0.26.2|0.26.2', 'vector|0.8.6|0.8.6', 'postgis|3.6.4'; shared_preload_libraries 'pg_search,pg_cron,pg_stat_statements'; README: 'ParadeDB Community is licensed under the GNU Affero General Public License v3.0'
- ParadeDB 0.26 supports French Snowball stemming and ASCII folding; both access methods 'paradedb' and 'bm25' exist; key_field is deprecated  
  _evidence:_ SELECT 'Les données à caractère personnel'::pdb.simple('stemmer=french','ascii_folding=true','stopwords_language=french')::text[] -> {don,a,caracter,personnel}; SELECT amname FROM pg_am -> paradedb, bm25; CREATE INDEX ... WITH (key_field='chunk_id') -> 'WARNING: key_field is deprecated as of 0.26.0 and is a no-op'
- pg_textsearch (Timescale) provides BM25 for PostgreSQL 17/18 using PostgreSQL text search configs, under the PostgreSQL License; latest listed release v1.5.0  
  _evidence:_ https://raw.githubusercontent.com/timescale/pg_textsearch/main/README.md: 'pg_textsearch supports PostgreSQL 17 and 18', 'CREATE INDEX docs_idx ON documents USING bm25(content) WITH (text_config='english');', release table '[v1.5.0] | Background compaction, Boolean and phrase queries'; LICENSE: 'The PostgreSQL License'
- Official postgres images: 18.6 and 17.11 (2026-10-07); in PG18 images PGDATA is /var/lib/postgresql/18/docker and the VOLUME moved to /var/lib/postgresql  
  _evidence:_ Docker Hub tags library/postgres -> '18.6 2026-10-07 154 MB', '17.11 2026-10-07'; docker-library docs content.md: 'For 18 it is `/var/lib/postgresql/18/docker` ... The defined `VOLUME` was changed in 18 and above to `/var/lib/postgresql`'; in pgvector container: show data_directory -> /var/lib/postgresql/18/docker
- Alternative engine versions/licenses: Qdrant v1.19.2 (Apache-2.0), OpenSearch 3.9.0 (~1105 MB image, Apache-2.0), Elasticsearch 9.5.5 (AGPL/SSPL/ELv2 triple license), Weaviate 1.39.x (BSD-3 or Weaviate License), Oxigraph 0.5.11 on ghcr (MIT/Apache), QLever only rolling commit tags, stain/jena-fuseki last updated 2024-07-31  
  _evidence:_ Docker Hub tags: qdrant 'v1.19.2 2026-10-05 71 MB'; opensearch '3.9.0 2026-09-29 1105 MB'; elasticsearch '9.5.5 2026-10-13 854 MB'; stain/jena-fuseki 'latest 2024-07-31'; ghcr.io tags list oxigraph/oxigraph -> ['0.5.6',...,'0.5.11']; raw LICENSE files: elastic 'triple license under the GNU Affero General Public License v3.0 only, the Server Side Public License, v 1, and the Elastic License 2.0'; weaviate 'variously licensed under the BSD-3-Clause License, or the Weaviate License'
- Qdrant full-text index is for filtering only and does not affect BM25 ranking; ascii_folding added in v1.16.0  
  _evidence:_ WebFetch https://qdrant.tech/documentation/concepts/indexing/ -> 'A full-text index does not affect BM25 queries'; 'ASCII folding ... available as of v1.16.0'
- sqlite-vec is pre-v1; DuckDB VSS HNSW is experimental, must fit in RAM and is fully re-serialized on checkpoint; DuckDB FTS indexes are static snapshots unless incremental=true  
  _evidence:_ sqlite-vec VERSION file '0.1.10-alpha.4', README '_sqlite-vec is a pre-v1, so expect breaking changes!_'; duckdb-vss README: 'This is an experimental extension', 'The index itself is not buffer managed and must be able to fit into RAM memory', 'every time DuckDB performs a checkpoint the entire index will be serialized'; duckdb-fts README: 'By default, indexes are static snapshots ... use incremental = true'
- Hugging Face TEI latest is 1.9.4 (cpu-1.9.4, turing-1.9.4, hopper-1.9.4); it supports Qwen3 and XLM-RoBERTa embedding models and XLM-RoBERTa sequence-classification rerankers; local model dirs via --model-id /data/...  
  _evidence:_ ghcr.io/v2/huggingface/text-embeddings-inference/tags/list (paginated) -> cpu-1.9.4, 1.9.4, hopper-1.9.4; TEI README line 70-71 'supports ... XLM-RoBERTa ... Qwen3, and Gemma3'; line 103 'supports CamemBERT, and XLM-RoBERTa Sequence Classification models'; line 404 '--model-id /data/Qwen3-Embedding-0.6B'
- Model licenses and dimensions: BAAI/bge-m3 MIT (xlm-roberta, hidden 1024, max_pos 8194); BAAI/bge-reranker-v2-m3 Apache-2.0; Qwen/Qwen3-Embedding-0.6B Apache-2.0 (hidden 1024, max_pos 32768); intfloat/multilingual-e5-large MIT; jinaai/jina-embeddings-v3 cc-by-nc-4.0; google/embeddinggemma-300m gemma license, gated  
  _evidence:_ https://huggingface.co/api/models/<id> tags 'license:mit' / 'license:apache-2.0' / 'license:cc-by-nc-4.0' / 'license:gemma' gated manual; config.json: bge-m3 {'hidden_size': 1024, 'max_position_embeddings': 8194}; Qwen3-Embedding-0.6B {'hidden_size': 1024, 'max_position_embeddings': 32768}
- Predicates on jolux:Act and their counts (relations target /jo URIs)  
  _evidence:_ SPARQL: SELECT ?p (COUNT(*) AS ?n) WHERE { ?s a jolux:Act ; ?p ?o } GROUP BY ?p -> inForceStatus 151250, dateEntryInForce 151252, basedOn 86315, cites 77981, dateNoLongerInForce 34636, modifies 27420, repeals 19632, transposes 3354, rectifies 1593, consolidates 367; sample: .../rgd/2023/04/19/a214/jo jolux:modifies .../rgd/2012/12/26/n14/jo
- Act in-force status distribution and in-force acts by type  
  _evidence:_ SPARQL GROUP BY jolux:inForceStatus -> in-force 119980, no-longer-in-force 31162, not-yet-in-force 58, no-longer-in-force-implicit 50; in-force by typeDocument -> RC 33113, PA 31350, AMIN 9845, RGD 9747, LOI 8060, AGD 7620, A 5945, DIV 3522
- 4,719 consolidations, of which 1,214 are current (no dateEndApplicability); 15 current code/recueil versions including code/civil/20251226 and code/travail/20260726; consolidations use the application-status vocabulary 'applicable'/'not-applicable'  
  _evidence:_ SPARQL count -> all 4719 | current 1214 | distinctBase 1416; current codes query -> http://data.legilux.public.lu/eli/etat/leg/code/civil/20251226 | 2025-12-26 ... 15 rows; a686/consolide/20251226 jolux:inForceStatus .../application-status/applicable
- Of 8,060 in-force LOI, 1,231 are targets of jolux:modifies and only 493 of those have a current consolidation (~738 amended laws exist only as original text); 510 in-force LOI have a current consolidation  
  _evidence:_ SPARQL: inforce 8060 | modified 1231; join with ?c a jolux:Consolidation ; jolux:isMemberOf ?w FILTER NOT EXISTS {?c jolux:dateEndApplicability ?e} -> inforceLoiWithCurrentConsol 510, modifiedInforceLoiWithCurrentConsol 493
- Only 2,078 of the 8,060 in-force LOI have a unique dateDocument; up to 35 in-force laws share one date (20+ on 2018-08-01)  
  _evidence:_ SPARQL nested GROUP BY ?d -> k=1: 2078 dates/2078 acts; k=2: 714 dates/1428 acts; ... k=35: 1 date; query for dateDocument=2018-08-01 returned 20 laws incl. .../loi/2018/08/01/a686/jo and a689/jo
- Expressions are almost entirely French: FRA 263,155, ENG 103, DEU 14  
  _evidence:_ SPARQL SELECT ?l (COUNT(DISTINCT ?e)) WHERE { ?e a jolux:Expression ; jolux:language ?l } -> .../language/FRA 263155, ENG 103, DEU 14
- In-force LOI+RGD formats: html 16,820 acts, xml-akomantoso 11,665, xml 5,156; 987 lack XML/HTML (mostly 19th century)  
  _evidence:_ SPARQL over userFormat -> html 16820, xml-akomantoso 11665, xml 5156; FILTER NOT EXISTS xml/html grouped by decade -> 1850: 245, 1860: 176, ... 2010: 6 (sum 987)
- Article-level jolux:Article resources in RDF (58,673) only have dateApplicability years 2010-2021, so they are stale for current text  
  _evidence:_ SPARQL SELECT YEAR(?d) COUNT(DISTINCT ?a) over jolux:Article -> 2010:261, 2015:1583, 2016:2990, 2017:8930, 2018:2904, 2019:69, 2020:1268, 2021:78
- data.legilux.public.lu robots.txt disallows /eli/* and /filestore/*; the legilux SPA config sets manifestation_prefix https://legilux.public.lu, and files are served at https://legilux.public.lu/filestore/... (GET 200 application/xml; HEAD returns 403)  
  _evidence:_ curl https://data.legilux.public.lu/robots.txt -> 'Disallow: /eli/* Disallow: /filestore/*'; curl https://legilux.public.lu/api/settings -> '"manifestation_prefix":"https://legilux.public.lu"'; GET https://legilux.public.lu/filestore/eli/etat/leg/loi/2018/08/01/a686/consolide/20251226/fr/xml/eli-etat-leg-loi-2018-08-01-a686-consolide-20251226-fr-xml.xml -> 'HTTP/1.1 200, Content-Type: application/xml, Content-Length: 140421'; curl -I same host -> 403
- Legilux XML is Akoma Ntoso CSD13 with an scl namespace; articles have ids like art_1er, embed article-level ELIs, mark repealed articles with style="del" and <del>, carry modification notes/passiveRef, and codes carry scl:indexterms and scl:uri  
  _evidence:_ a686 XML: '<akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0/CSD13" xmlns:scl="http://www.scl.lu">', '<article id="art_1er">... uriThis http://data.legilux.public.lu/eli/etat/leg/loi/2018/08/01/a686/art_1er/20180820', '<article id="art_57" style="del">', '<note ... title="A|2025-12-22|2025-12-19|L|619">'; Code civil head: '<article id="art_6-1" scl:uri="/eli/etat/leg/code/civil/art_6-1"> <scl:indexterms>...<scl:label>Abus de droit</scl:label>' and '( <ref href="/eli/etat/leg/loi/1987/07/02/n1/jo">L. 2 juillet 1987</ref> )'
- Article numbering is irregular: 'Art. 9 et 10.' has ELI art_9_10 but AKN id art_9_et_10; 'Art. 17 à 20.' has ELI art_17--20; Code du travail uses 'Art. L. 121-1.' / id art_l_121-1  
  _evidence:_ Prototype query on unit/article_alias -> 'Art. 9 et 10. | http://data.legilux.public.lu/eli/etat/leg/code/civil/art_9_10', 'Art. 17 à 20. | .../code/civil/art_17--20'; travail head ids: ('article', 'art_l_121-1'), nums: 'Art. L. 121-1.'
- Sizes: Code civil XML 5,413,811 B; Code du travail XML 4,128,058 B; a686 consolidated 140,421 B XML -> 46,836 chars of live article text across 72 live articles (6 repealed; median 398, p90 1,342, max 3,516 chars); sample RGD outliers of 1.1-1.9 MB are protection-zone annexes  
  _evidence:_ Ranged GET -r 0-0 -> 'Content-Range: bytes 0-0/5413811' (civil/20251226), '0-0/4128058' (travail/20260726); akn_articles.py -> 'articles kept 72 repealed(style=del) 6 / chars: total 46836 median 398.5 p90 1342 max 3516'; titles of rgd/2018/10/02/a936 '...zones de protection autour du captage d’eau souterraine Heisdorf...'
- The whole Legilux RDF store holds ~13.3 million triples  
  _evidence:_ SPARQL SELECT (COUNT(*) AS ?n) WHERE { ?s ?p ?o } -> 13307204
- Prototype schema applies on PG 18.6 + pgvector 0.8.7; fr_legal FTS config keeps tokens such as '2016/679' and '1er'; accented stopwords leak after unaccent  
  _evidence:_ docker exec ... psql -f schema.sql -> SCHEMA_OK; to_tsvector('lgx.fr_legal','Les données à caractère personnel; résiliation ...; règlement (UE) 2016/679; 1er août 2018') -> "'2016/679':17 '1er':18 'a':3 'donne':2 'le':1 'resili':6 ..."
- In a 363-article prototype, BM25 (pg_search) + dense RRF ranked the expected article #1 where native ts_rank_cd + dense RRF ranked it #6; a cross-encoder rerank of the native list also moved it to #1  
  _evidence:_ pdb_hybrid.py: 'Peut-on engager sa responsabilité en exerçant un droit de manière excessive ?' -> '0.0328 bm25:1 dense:1 ... > Art. 6-1.'; query_proto.py native: Art. 6-1 absent from top 5; rerank_proto.py: 'rr= 1.54 rrf_rank= 6 ... > Art. 6-1.' next 'rr= -5.08'; same pattern for Art. L. 121-1 (rrf_rank 6 -> rerank 1; BM25 hybrid rank 1)
- CPU costs (4 vCPU Xeon 2.1 GHz): embedding 363 articles with multilingual-e5-large ONNX took ~4 min total; single query embedding 53 ms; bge-reranker-v2-m3-int8 reranking 40 candidates took 10.6-16.9 s  
  _evidence:_ load_proto.py run: 'real 3m59.138s' for 363 chunks; timing script -> 'avg query embed ms 53'; rerank_proto.py -> '(rerank 40 cands in 16.9s CPU)', '(rerank 40 cands in 10.6s CPU)'
- A partial HNSW index (WHERE in_force AND NOT is_repealed) is used by the planner for filtered ANN queries, with additional predicates applied as post-filter  
  _evidence:_ EXPLAIN SELECT chunk_id FROM chunk WHERE in_force AND NOT is_repealed AND doc_type='CODE' ORDER BY embedding <=> '<vec>'::halfvec(1024) LIMIT 50 -> 'Index Scan using chunk_emb_hnsw_live on chunk / Filter: (doc_type = 'CODE'::text)'
- Each halfvec(1024) value is 2,052 bytes; pg_dump -Fc -Z zstd:9 works on PG 18  
  _evidence:_ SELECT avg(pg_column_size(embedding)) -> 2052; pg_dump -U postgres -d legilux -Fc -Z zstd:9 -f /tmp/legilux.dump -> 1239840 bytes, pg_restore -l lists 102 entries
- Docker Hub returned 429 for anonymous pulls through the shared proxy; mirror.gcr.io served the same images (pgvector 0.8.7-pg18-trixie digest sha256:9d9c9302...)  
  _evidence:_ docker pull pgvector/pgvector:0.8.7-pg18-trixie -> 'unexpected status from HEAD request ... 429 Too Many Requests'; docker pull mirror.gcr.io/pgvector/pgvector:0.8.7-pg18-trixie -> 'Digest: sha256:9d9c930220cb9bf2f956d10a8f909cf9973d9672ca0278aef2c1e6facccad2e0'
- Laws contain an official short-citation article usable as an alias  
  _evidence:_ a686 consolidated Art. 77 text: 'La référence à la présente loi peut se faire sous une forme abrégée en recourant à l’intitulé suivant : « Loi du 1er août 2018 portant organisation de la Commission nationale pour la protection des données et du régime général sur la protection des données ».'

## Report

# Track: database choice and retrieval architecture (Legilux local clone with an MCP server)

## 1. Recommendation

**Use one PostgreSQL 18 database. It holds the relational model, the vectors and the lexical index.**

- **Default image:** `paradedb/paradedb:0.26.2-pg18`. It bundles `pg_search` 0.26.2 (BM25 on Tantivy, with a French Snowball stemmer and ASCII folding) and `vector` 0.8.6 (pgvector). I tested both in this sandbox.
- **If AGPL-3.0 is not acceptable:** build from `pgvector/pgvector:0.8.7-pg18-trixie` and add `pg_textsearch` v1.5.0 (PostgreSQL License). It does BM25 on top of Postgres text-search configs, so it can reuse the custom `fr_legal` config. I verified this from its README only; I did not build or test it here.
- **Minimal mode:** native Postgres full-text search only. In my test it was clearly the weakest lexical ranker.

Other components:
- **Embeddings:** stored as `halfvec(1024)`. Default model `BAAI/bge-m3` (MIT); alternative `Qwen/Qwen3-Embedding-0.6B` (Apache-2.0). Both are 1024-dimensional.
- **Vector index:** pgvector HNSW. I verified that the planner uses a *partial* HNSW index over in-force, non-repealed chunks.
- **Reranker:** cross-encoder `BAAI/bge-reranker-v2-m3` (Apache-2.0), served by `ghcr.io/huggingface/text-embeddings-inference:cpu-1.9.4` (a GPU tag can be used instead).
- **RDF:** no triple store as the main database. Optionally, an Oxigraph sidecar (`ghcr.io/oxigraph/oxigraph:0.5.11`) loaded with filtered metadata, behind a compose profile.

Why one Postgres:
- **Exact citation lookups** ("art. L. 121-1 C. trav.") become indexed SQL joins.
- **The amends/repeals graph** is plain relational edges, walked with recursive queries.
- **Hybrid ranking with RRF** is a single SQL statement.
- **Transfer to the airgapped site** is one `pg_dump -Fc -Z zstd` file, plus `docker save` of the images and the model folders. I tested `-Z zstd:9` on PG 18.6.
- **The corpus is small.** The whole Legilux RDF graph is about 13.3M triples, and the in-force text corpus is likely 100k–200k article chunks. A second search engine would add operating cost without adding capability.

## 2. Source-data facts that drive the schema (checked live)

All SPARQL queries went to `https://data.legilux.public.lu/sparqlendpoint` with prefix `jolux: <http://data.legilux.public.lu/resource/ontology/jolux#>`, at about 1 request per second.

**Act metadata**
- Predicates on `jolux:Act` (151,250 acts):
  - `inForceStatus`, `typeDocument`, `dateDocument`, `publicationDate`, `dateEntryInForce` (151,252), `dateNoLongerInForce` (34,636).
  - Relations: `basedOn` (86,315), `cites` (77,981), `modifies` (27,420), `repeals` (19,632), `transposes` (3,354), `rectifies` (1,593), `consolidates` (367).
  - Subjects: `subjectLevel1` / `subjectLevel2`. Other: `historicalLegalId` (95,998), `isPartOf` (the Mémorial).
- Relation targets are the `/jo` URIs. Example: `…/rgd/2023/04/19/a214/jo modifies …/rgd/2012/12/26/n14/jo`.
- Status counts: in-force 119,980; no-longer-in-force 31,162; not-yet-in-force 58; no-longer-in-force-implicit 50.
- In-force acts by type: RC 33,113; PA 31,350; AMIN 9,845; RGD 9,747; LOI 8,060; AGD 7,620; A 5,945. Type labels come from `skos:prefLabel` (e.g. PA = "publication administrative", RC = "règlement communal").
- Language: 263,155 of 263,317 expressions are French (FRA); 14 are German. The lexical index can therefore be French only, and the German/English questions are handled by multilingual embeddings.

**Versions and consolidations**
- There are 4,719 `jolux:Consolidation` resources. 1,214 of them are current, meaning they have no `dateEndApplicability`. 916 `jolux:Code` versions exist, of which **15 are current**, for example `…/eli/etat/leg/code/civil/20251226` and `…/code/travail/20260726`.
- Consolidations use a **different status vocabulary**: `application-status/applicable` or `not-applicable`, not `in-force`. To decide which version is current, use `dateEndApplicability IS NULL`.
- An act is a `ComplexWork` (`…/loi/2018/08/01/a686`) with members `…/a686/jo` (the original) and `…/a686/consolide/20251226`.
- **Text gap:** of 8,060 in-force LOI, 1,231 are targets of `jolux:modifies`, and only 493 of those have a current consolidation. About **738 amended laws exist only as their original text**. The schema needs a `text_basis` flag and the answer must warn the user in these cases.
- **The article-level resources in the RDF are stale.** The 58,673 `jolux:Article` resources only have `dateApplicability` years between 2010 and 2021. Current article structure must come from the XML.

**XML format**
- Formats available for in-force LOI+RGD: html for 16,820 acts, `xml-akomantoso` for 11,665, `xml` for 5,156. 987 of them, mostly from the 19th century, have neither XML nor HTML; treat these as metadata only.
- The format is Akoma Ntoso `http://docs.oasis-open.org/legaldocml/ns/akn/3.0/CSD13` with an `scl:` namespace. Details:
  - Hierarchy: `title/chapter/section/subsection/article/paragraph/alinea` for laws; codes add `book`.
  - Article IDs look like `art_1er` and `art_l_121-1`.
  - Article-level ELIs are embedded in the XML, e.g. `…/a686/art_1er` plus a versioned form `…/art_1er/20180820`.
  - Codes carry `scl:uri="/eli/etat/leg/code/civil/art_6-1"` and `<scl:indexterms>` (for example "Abus de droit").
  - Inline amendment provenance appears as `( <ref href="/eli/etat/leg/loi/1987/07/02/n1/jo">L. 2 juillet 1987</ref> )`.
  - Cross-references are relative ELIs, e.g. `<ref href="/eli/reg_ue/2016/679/jo">`.
- **Consolidated texts still contain repealed text.** Examples: `<article id="art_57" style="del">` and `<del>…</del>` (6 of 78 articles in a686). Textual forms also occur, such as "Art. 9 et 10. Abrogés ( L. 23 avril 1934 )". These must be excluded from embedding but kept as stub rows.
- **Article numbering is irregular**: "Art. 9 et 10." has ELI `art_9_10` but AKN id `art_9_et_10`; "Art. 17 à 20." has ELI `art_17--20`. An `article_alias` table is needed.
- Laws state their own short citation title. Art. 77 of a686 reads: "La référence à la présente loi peut se faire sous une forme abrégée … « Loi du 1er août 2018 portant organisation de la Commission nationale pour la protection des données … »". Store this as an alias.

**Ambiguity of dated citations**
- Only 2,078 of the 8,060 in-force LOI have a unique `dateDocument`. 714 dates are shared by 2 laws, and one date is shared by 35. For 2018-08-01 SPARQL returned 20 laws (limit 20), including a686 (the CNPD law) and a689 (data protection in criminal matters).
- So "loi du 1er août 2018, art. 5" cannot be resolved from the date alone. The parser must use the rest of the title.

**File sizes** (ranged GETs via `https://legilux.public.lu/filestore/…`)
- Code civil XML: 5,413,811 B. Code du travail: 4,128,058 B.
- 12 pseudo-random in-force LOI: 7.8–84 KB, median about 25 KB.
- 12 RGD: median about 12 KB, but two outliers of 1.1 and 1.9 MB. These are a water-catchment protection-zone regulation and a special-protection-zone designation, i.e. long annexes. Temporary traffic RGDs ("réglementation temporaire de la circulation") also show up as "in-force".
- The a686 consolidated XML is 140,421 B and yields 46,836 chars of live article text (72 live articles: median 398 chars, p90 1,342, max 3,516).

## 3. Comparison of options

| Option | Hybrid quality | Exact lookup / joins | Temporal + amendment graph | Docker operations | Airgap / backup | License | Verdict |
|---|---|---|---|---|---|---|---|
| **PG18 + pgvector + pg_search (ParadeDB 0.26.2)** | BM25 with French stemmer and ASCII folding, plus HNSW, fused with RRF in SQL (tested) | Native SQL, foreign keys, unique indexes | Tables and recursive CTEs | 1 container (354 MB image) | 1 dump file | PostgreSQL + **AGPL-3.0** (pg_search) | **Recommended** |
| PG18 + pgvector + pg_textsearch 1.5.0 | BM25 over PG text-search configs (can use `fr_legal`) | same | same | 1 container, custom build | same | PostgreSQL License | Fallback if AGPL is an issue (not tested here) |
| PG18 + pgvector + native FTS | `ts_rank_cd` has no IDF, so lexical ranking is weaker (shown in §8) | same | same | 154 MB image | same | PostgreSQL | Minimal mode / exact-token filter |
| OpenSearch 3.9.0 / Elasticsearch 9.5.5 | Strong BM25, kNN and hybrid pipelines; built-in French analyzer (from memory) | Weak; you denormalise | Done in the app | JVM; image ~1.1 GB / 854 MB; a relational store is still needed | Snapshot directories | Apache-2.0 / AGPL+SSPL+ELv2 | Too much for ~150k chunks; means two stores |
| Qdrant 1.19.2 or Weaviate 1.39 + Postgres | Dense + sparse vectors; Qdrant's full-text index is *filtering only* | Needs Postgres anyway | In Postgres | 2 stores to keep in sync | 2 backups | Apache-2.0 / BSD-3 + "Weaviate License" | Not justified |
| Triple store (Oxigraph 0.5.11, Jena Fuseki 5.x, QLever) | None (no BM25 or vectors) | SPARQL; exact ELI lookups work | Native RDF | One more container | RDF dump | MIT/Apache, Apache-2.0, Apache-2.0 | Optional metadata sidecar |
| SQLite + FTS5 + sqlite-vec | FTS5 bm25; French stemming not built in (from memory); sqlite-vec is pre-v1 ("0.1.10-alpha.4") | SQL | ok | No server | **Best single-file portability** | Public domain / MIT-Apache | Good as an extra export, not the main store |
| DuckDB + fts + vss | FTS has a French stemmer but the index is a "static snapshot" unless `incremental`; VSS HNSW is experimental and must fit in RAM | SQL | ok | Embedded; one writer at a time | Single file | MIT | Analytics only |

Notes:
- The Docker Hub `stain/jena-fuseki` image was last updated in 2024. Apache's own Fuseki needs a local Docker build.
- `adfreiburg/qlever` only publishes rolling `commit-*` and `latest` tags.

## 4. Deployment components

**Services** (all models loaded from mounted folders, nothing downloaded at runtime):
- `db`: `paradedb/paradedb:0.26.2-pg18`, or the custom `pgvector/pgvector:0.8.7-pg18-trixie` build.
  - PG 18 images use `PGDATA=/var/lib/postgresql/18/docker`, and the volume must be mounted at `/var/lib/postgresql` (from the official image docs; `show data_directory` confirmed it).
  - **Pin image digests.** For example, `pgvector/pgvector:0.8.7-pg18-trixie` is `sha256:9d9c930220cb9bf2f956d10a8f909cf9973d9672ca0278aef2c1e6facccad2e0`.
  - Docker Hub returned **429** through the shared proxy here; `mirror.gcr.io/...` worked. Build the airgap bundle with `docker save`.
- `embed`: `text-embeddings-inference:cpu-1.9.4 --model-id /models/bge-m3`. Only queries are embedded at the airgapped site.
- `rerank`: same TEI image with `--model-id /models/bge-reranker-v2-m3`. TEI supports XLM-RoBERTa sequence-classification rerankers.
- `mcp`: the MCP server (Python, `psycopg` 3). It is read-only, connecting with a `SELECT`-only role.
- Optional profile `rdf`: Oxigraph serving a filtered N-Triples export.

**Embedding is a build-time job.** On 4 vCPU (Xeon 2.1 GHz), multilingual-e5-large (ONNX via fastembed) embedded 363 articles in about 4 minutes, roughly 0.6 s per chunk. 150k chunks would therefore take more than 24 hours on CPU. Run the full embedding on a GPU on the connected side and ship the vectors inside the dump. Query embedding at runtime measured **53 ms per query** on CPU.

## 5. Schema sketch

The full tested file is `schema.sql`; it applied without errors on PG 18.6 + pgvector 0.8.7. Key parts:

```sql
CREATE EXTENSION vector; CREATE EXTENSION unaccent; CREATE EXTENSION pg_trgm;
CREATE TEXT SEARCH CONFIGURATION lgx.fr_legal (COPY = french);
ALTER TEXT SEARCH CONFIGURATION lgx.fr_legal ALTER MAPPING FOR hword,hword_part,word WITH unaccent, french_stem;
CREATE TEXT SEARCH CONFIGURATION lgx.simple_ua (COPY = simple);   -- exact tokens: 'l. 121-1', '2016/679', '1er', 'bis'
CREATE FUNCTION lgx.f_unaccent(text) RETURNS text LANGUAGE sql IMMUTABLE PARALLEL SAFE STRICT
  RETURN public.unaccent('public.unaccent'::regdictionary, $1);

CREATE TABLE vocab (concept_uri text PK, scheme text, code text, label_fr text, label_de text, label_en text);

CREATE TYPE text_basis AS ENUM ('consolidated','original','original_possibly_outdated','metadata_only');
CREATE TABLE act (                      -- one row per jolux:ComplexWork
  act_id bigint identity PK, eli_work text UNIQUE, eli_jo text UNIQUE, doc_type text, is_code bool,
  date_document date, publication_date date, date_entry_into_force date, date_no_longer_in_force date,
  in_force_status text, memorial text, historical_legal_id text,
  title text, title_short text, citation_title text, responsible_inst text[], subjects text[],
  language char(2), text_basis text_basis, current_version_id bigint,
  title_tsv tsvector GENERATED (A: title_short+citation_title, B: title), rdf_modified timestamptz, updated_at timestamptz);
CREATE INDEX ON act (doc_type, date_document);
CREATE INDEX ON act USING gin (lgx.f_unaccent(lower(title)) gin_trgm_ops);
CREATE TABLE act_alias (act_id, alias_norm, kind);   -- 'code du travail','c. trav.','ncpc','cnpd','mem a 686 2018', ELI forms

CREATE TABLE act_version (              -- metadata for ALL known versions; text only for is_current
  version_id PK, act_id FK, eli_version text UNIQUE, kind ('consolidated'|'original'|'code'),
  date_applicability date, date_end_applicability date, is_current bool, has_text bool,
  source_url text, source_format text, source_modified timestamptz, http_last_modified timestamptz,
  sha256 bytea, bytes int, fetched_at timestamptz, parser_version text,
  license text DEFAULT 'https://creativecommons.org/licenses/by/4.0/', rights text);
CREATE UNIQUE INDEX ON act_version (act_id) WHERE is_current;

CREATE TABLE unit (                     -- AKN tree of the current version
  unit_id PK, version_id FK, act_id FK, parent_id FK, unit_type text, akn_id text,
  eli_unit text, eli_unit_version text, unit_version_date date,
  num_raw text, num_norm text, heading text, ordinal int, depth smallint, breadcrumb text,
  text_plain text, is_repealed bool, is_modifying bool, amended_by text[], index_terms text[]);
CREATE INDEX ON unit (act_id, num_norm) WHERE unit_type='article';
CREATE TABLE article_alias (act_id, num_norm, unit_id, PK(act_id,num_norm,unit_id));  -- '9','10' -> "Art. 9 et 10."

CREATE TABLE relation (src_act_id, src_unit_id NULL, rel_type, dst_eli, dst_act_id NULL, dst_unit_hint, source);
  -- rel_type: modifies|repeals|based_on|cites|consolidates|transposes|rectifies|modified_temp_by|refers_to
  -- source: 'sparql' | 'akn-ref' | 'akn-note'

CREATE TABLE chunk (
  chunk_id PK, unit_id FK, act_id FK, version_id FK, part_no, part_count,
  header text, body text, n_tokens int,
  doc_type text, in_force bool, is_repealed bool, date_document date,   -- denormalised filters
  embedding halfvec(1024), embed_model text,
  tsv tsvector GENERATED (setweight(fr_legal(header),'A') || setweight(fr_legal(body),'B')),
  tsv_exact tsvector GENERATED (simple_ua(header||' '||body)));
CREATE INDEX ON chunk USING gin (tsv);  CREATE INDEX ON chunk USING gin (tsv_exact);
CREATE INDEX chunk_emb_hnsw_live ON chunk USING hnsw (embedding halfvec_cosine_ops)
  WITH (m=16, ef_construction=128) WHERE in_force AND NOT is_repealed;
-- ParadeDB variant (tested):
CREATE INDEX chunk_bm25 ON chunk USING bm25 (chunk_id,
  (header::pdb.simple('stemmer=french','ascii_folding=true')),
  (body::pdb.simple('stemmer=french','ascii_folding=true')));

CREATE TABLE sync_state (source PK, watermark timestamptz, last_run_at, last_status, details jsonb);
CREATE TABLE ingest_log (...);
CREATE VIEW attribution AS SELECT 'Source : Legilux … © Ministère d''État – SCL, licence CC BY 4.0. Copie locale ; texte non officiel …';
```

**Rules for keeping only current, relevant data**
- **Which text is loaded:** for each act, the text of the current consolidation if one exists (`dateEndApplicability IS NULL`), otherwise the `/jo` original.
  - Set `text_basis = 'original_possibly_outdated'` when an inbound `modifies` exists but there is no consolidation.
  - Keep earlier versions as metadata rows only (about 4.7k + 916 rows), so the system can say "wording applicable since 26/12/2025" and link to older versions online.
- **Repealed articles:** `<article style="del">` and articles whose text matches `^Abrogé` become stub units with `is_repealed`. They get no chunk, but they still answer questions like "art. 56 was repealed by Loi du 19 décembre 2025".
- **Content not embedded:** preamble and "visas" (kept in `unit` for display only); annexes made of tables or parcel lists (use a digit-ratio or length heuristic).
- **Act types and titles not loaded:** PA, RC and temporary traffic regulations (`RTC` and RGD titles matching "réglementation temporaire de la circulation"), unless they are in scope.
- **Amending laws:** articles of amending laws are marked `is_modifying` (they contain `<mod>` / `<embeddedStructure>`) and down-weighted, because their effect already appears in the consolidated target.
- **Embedding provenance:** store `embed_model` on every chunk. Re-embed when the model changes.

## 6. Retrieval pipeline

**Stage 0: query analysis and the citation parser**

The prototype is `citeparse.py`. It parsed 9 of 10 test strings correctly:
- `art. L. 121-1 C. trav.` → `{article:"L121-1", act:{code:"travail"}}`
- `Art. 5, paragraphe 2, de la loi du 1er août 2018 portant organisation …` → `{article:"5", paragraph:"2", act:{type:LOI, date:"2018-08-01", title_hint:"portant organisation …"}}`
- `articles 17 à 20 du Code civil` → a range
- `article 12bis de la loi modifiée du 4 décembre 1967 …` → "modifiée" is ignored
- ELI URLs
- `Mém. A n° 686 de 2018`
- `point 3°`

The one failure: when the act comes *before* the article ("règlement grand-ducal du 14 novembre 2013, art. 3, alinéa 2"), the parser does not attach it. A backward search is needed.

Resolution order:
1. ELI or Mémorial reference: exact match.
2. Code alias: `act_alias`.
3. (type, date) gives the candidates; rank them by trigram similarity between `title_hint` and the title.
   - Demo on the 20 laws of 2018-08-01: the hint "portant organisation de la CNPD …" ranked a686 first (0.309 vs 0.237).
   - The bare acronym "CNPD" scored 0.005, so it failed. A curated acronym list in `act_alias` is needed (CNPD, RGPD, CSSF, ADEM, …).
4. If several candidates are close, return the ambiguity to the LLM instead of guessing.

Article resolution then uses `article_alias(act_id, num_norm)`. The normaliser maps 1er→1, "L. 121-1"→L121-1, bis/ter suffixes, and "à" ranges.

**Stage 1: candidate generation** (run in parallel, all filtered to `in_force AND NOT is_repealed` unless the user asks about history):
- (a) BM25 with `body ||| $q OR header ||| $q`, top 50.
- (b) Dense: `ORDER BY embedding <=> $qvec LIMIT 50`, with `SET hnsw.ef_search=100; SET hnsw.iterative_scan=relaxed_order` for extra filters such as `doc_type` (verified to run as a post-filter on the partial index).
- (c) Exact-token matches through `tsv_exact @@ phraseto_tsquery('lgx.simple_ua', '2016/679')` for numbers and identifiers.
- (d) Units resolved by the citation parser, injected with a high weight.
- (e) Act-level title search (`act.title_tsv` / trigram) for questions like "which law governs X?".

**Stage 2: fusion with RRF** (k = 60), done in SQL:

```sql
WITH bm25 AS (SELECT chunk_id, RANK() OVER (ORDER BY pdb.score(chunk_id) DESC, chunk_id) r FROM lgx.chunk
              WHERE (body ||| $1 OR header ||| $1) AND in_force AND NOT is_repealed ORDER BY pdb.score(chunk_id) DESC LIMIT 50),
     dense AS (SELECT chunk_id, RANK() OVER (ORDER BY embedding <=> $2, chunk_id) r FROM lgx.chunk
               WHERE in_force AND NOT is_repealed ORDER BY embedding <=> $2 LIMIT 50),
     exact AS (SELECT chunk_id, 1 AS r FROM lgx.chunk WHERE unit_id = ANY($3)),
     fused AS (SELECT chunk_id, SUM(w/(60+r)) score FROM (
               SELECT chunk_id,r,1.0 w FROM bm25 UNION ALL SELECT chunk_id,r,1.0 FROM dense
               UNION ALL SELECT chunk_id,r,10.0 FROM exact) u GROUP BY chunk_id)
SELECT * FROM fused ORDER BY score DESC LIMIT 40;
```

**Stage 3: reranking.** Score the top 20–40 candidates with bge-reranker-v2-m3 on `header + body[:~512 tokens]` and keep the top 6–10.

**Stage 4: context assembly.**
- Expand each hit to the full article. When the article is very short, add its siblings or parent heading.
- Group the results by act.
- Attach act metadata: title, ELI, `text_basis`, consolidation date, in-force status, and `amended_by`.
- Collect `relation` edges (repealed/modified by, `based_on` regulations).
- Fit everything to a token budget.

**Chunking.**
- One chunk is one article, and the header is the breadcrumb: `"{short title} > Livre … > Titre … > Chapitre … > Section … > Art. X." + index_terms`.
- Articles longer than about 1,500 tokens are split at `<paragraph>` boundaries, repeating the header and adding "(suite, §n)". Splits never happen inside an alinéa.
- Short articles are never merged, so that each citation points at exactly one article.

**Stage 5: answering with citations.** MCP tools return structured citation objects:

```json
{"act_title":"Loi du 1er août 2018 portant organisation de la CNPD …","doc_type":"LOI",
 "act_eli":"http://data.legilux.public.lu/eli/etat/leg/loi/2018/08/01/a686/jo",
 "article":"Art. 49.","article_eli":"http://data.legilux.public.lu/eli/etat/leg/loi/2018/08/01/a686/art_49",
 "version_eli":".../a686/consolide/20251226","version_applicable_from":"2025-12-26",
 "text_basis":"consolidated","in_force_status":"in-force","breadcrumb":"Titre I > Chapitre 2 > Section XI Sanctions",
 "text":"…","amended_by":["/eli/etat/leg/loi/2025/12/19/a619/jo"],"attribution":"…CC BY 4.0…"}
```

The system prompt sets the citation format: « Art. 49, Loi du 1er août 2018 … (version consolidée applicable au 26/12/2025) — ELI ».

A post-generation check:
- Re-parses the citations in the answer.
- Rejects or flags any (act, article) pair that is not in the retrieved set.
- Checks that every quoted span is a substring of `unit.text_plain`.
- Forces a warning when `text_basis = 'original_possibly_outdated'` or the status is not in-force.

Tools to expose:
- `search_law(query, filters)`
- `get_article(citation | eli)`
- `get_act(eli | citation)` (table of contents plus metadata)
- `get_relations(eli, type, direction)`
- `resolve_citation(text)`
- `list_versions(eli)`

## 7. Handling French and Luxembourg legal language

- **Accents and stopwords:** the `fr_legal` config (unaccent + french_stem) works; "résiliation" becomes 'resili'. Because stopwords are checked *after* unaccent, unaccented "à" leaks through as 'a'. Ship a custom Snowball dictionary with an unaccented stopword file.
- **Exact tokens:** the `simple_ua` config keeps '2016/679', '1er', '121', '-1' and 'bis' as tokens.
- **ParadeDB stemming caveat:** `pdb.simple('stemmer=french','ascii_folding=true')` produced `{don,a,caracter,personnel}` for "Les données à caractère personnel". "données" is over-stemmed to "don"; acceptable for BM25, but evaluate it.
- **Other languages:** questions in German or English are handled by the multilingual embeddings. In the test, the German query "Wer ernennt die Mitglieder der Datenschutzkommission?" returned CNPD Art. 17 first through the dense branch alone. Optionally have the LLM rewrite the query into French keywords for the BM25 branch.

## 8. Prototype results (this sandbox, 363 articles)

Setup: Docker with pgvector 0.8.7-pg18 and ParadeDB 0.26.2-pg18. Data: a686 (consolidated) plus the first 400 KB of the Code civil and Code du travail XML. Embeddings: multilingual-e5-large. Treat these as anecdotal; they show how the pieces behave, not general quality.

| Question | Native FTS + dense, RRF | BM25 (pg_search) + dense, RRF | Native RRF, then bge-reranker-v2-m3-int8 |
|---|---|---|---|
| "Peut-on engager sa responsabilité en exerçant un droit de manière excessive ?" (expected: C. civ. Art. 6-1) | rank 6 | **rank 1** (bm25:1, dense:1) | **rank 1** (score 1.54 vs −5.08 for the next) |
| "Qui est considéré comme salarié et quel droit régit le contrat de travail ?" (expected: Art. L. 121-1) | rank 6 | **rank 1** | **rank 1** |
| "Quelles sont les sanctions que la CNPD peut prononcer ?" | Arts 49/48 at 1–2 | Arts 52/49/51/48 at 1–4 | – |

Other measurements:
- **Native ranking is weak.** The OR form of `ts_rank_cd` did not have Art. 6-1 in its top 5, while BM25 ranked it first (15.05). Postgres-native ranking has no IDF.
- **CPU reranking is slow:** 40 candidates took 10.6–16.9 s (int8 ONNX, 4 vCPU). On CPU, rerank only about 20 truncated candidates, or use a GPU.
- **Storage:** 2,052 B per `halfvec(1024)` value; the prototype dump was 1,239,840 B.

## 9. Evaluation plan

**Gold sets.** Build and freeze them on the connected side, and version them together with the database snapshot.
1. **Citation resolution, about 500 items, generated automatically from the database.** Sample units and render them in many citation styles: abbreviations, "1er", bis, ranges, dated acts with partial titles, acronyms. Metric: exact (act, unit) accuracy, plus the correct-abstain rate on ambiguous dates (74% of in-force laws share their date).
2. **Retrieval, 300–500 questions.** Sample articles in proportion to the corpus. An LLM writes 1–2 natural questions per article, including paraphrases without keywords and a German or English variant. A lawyer validates 30% or more and adds 50–100 real-world and multi-hop questions (law → implementing RGD via `based_on`).
3. **Temporal and negative cases:** repealed articles, not-yet-in-force acts, outdated originals, and out-of-scope questions (EU regulations, case law). The expected behaviour is a warning or abstention.

**Metrics.**
- Retrieval, at article level: Recall@5/10/20, MRR@10, nDCG@10, with partial credit when the act is right but the article is wrong.
- Citations: precision (cited unit exists and was retrieved) and recall against the gold units.
- Answer quality: groundedness, i.e. the share of claims supported by the cited text, judged by a local LLM and spot-checked by a lawyer; verbatim-quote validity; correct warning when `text_basis` is outdated.
- Latency p50/p95.

**Ablations:** native vs BM25 vs dense; with and without RRF; with and without the reranker; header breadcrumbs on or off; index terms on or off; chunk sizes; embedding models (bge-m3 vs Qwen3-0.6B vs e5-large).

**Release gate:** run the evaluation on every database rebuild, publish the metrics with the dump, and block a release if Recall@10 or citation accuracy drops.

## 10. Size and operations estimates (inferred, not measured)

- **Corpus:** in-force LOI (8,060) + RGD (9,747), with current consolidations replacing originals, plus 15 codes and optionally AGD/AMIN. That is roughly 150–250 MB of plain text and about 100k–200k article chunks.
- **Vectors:** 0.2–0.4 GB of `halfvec`, plus an HNSW index of similar size.
- **Database:** about 2–5 GB in total. The compressed `pg_dump` is about 1–2 GB; it excludes index data, so the HNSW index is rebuilt on restore.
- **Faster start option:** ship a physical volume snapshot (`pg_basebackup` tar) to skip the rebuild.
