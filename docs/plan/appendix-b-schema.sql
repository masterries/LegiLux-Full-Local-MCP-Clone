-- =====================================================================================================
-- Appendix B — PostgreSQL DDL sketch for the LegiLux local clone
-- Target: PostgreSQL 18 + pgvector 0.8.7  (image pgvector/pgvector:0.8.7-pg18-trixie@sha256:9d9c9302…,
--         extended as lgx-db with db/tsearch/french_unaccent.stop, see PLAN.md §7)
-- Form:   a psql script.  Run on the BUILD side into a fresh database per release:
--           psql -v ON_ERROR_STOP=1 -v embed_dim=1024 -d legilux_build_<T> -f appendix-b-schema.sql
--         embed_dim is the per-release vector dimension (Mistral Embed = 1024). It only matters
--         when the embedding stage runs; with provider=none the column stays NULL.
-- Status: applied cleanly on pgvector/pgvector:0.8.7-pg18-trixie on 2026-10-16, incl. a fixture load,
--         the EXCLUDE constraint, the conditional HNSW index and a pg_dump -Fc -Z zstd:9 / pg_restore
--         round trip (PLAN.md Appendix E, check P4). Re-applied after the review revision (search filters,
--         text_md rule) with one test per filter parameter (check P11).
-- Sections: B.1 extensions/schemas · B.2 text search · B.3 enums · B.4 release + embedding metadata
--           B.5 reference texts · B.6 acts · B.7 versions · B.8 units/aliases · B.9 relations/recueils
--           B.10 chunks · B.11 change log + data quality · B.12 build-only schema · B.13 functions
--           B.14 search · B.15 post-load (conditional HNSW, invariants) · B.16 roles
-- =====================================================================================================
\set ON_ERROR_STOP on
\if :{?embed_dim}
\else
  \set embed_dim 1024
\endif

-- ----------------------------------------------------------------------------------------------- B.1
CREATE EXTENSION IF NOT EXISTS vector;      -- pgvector 0.8.7 (halfvec, HNSW, iterative scans)
CREATE EXTENSION IF NOT EXISTS unaccent;
CREATE EXTENSION IF NOT EXISTS pg_trgm;     -- fuzzy title / alias matching for the citation resolver
CREATE EXTENSION IF NOT EXISTS btree_gist;  -- EXCLUDE (act_id WITH =, text_valid WITH &&)

CREATE SCHEMA lgx;        -- legal data, shipped
CREATE SCHEMA lgx_meta;   -- release metadata + invariant views, shipped
CREATE SCHEMA build;      -- build-side state, NEVER shipped (pg_dump --exclude-schema=build)

-- ----------------------------------------------------------------------------------------------- B.2
-- fr_legal: unaccent, then Snowball French with an UNACCENTED stopword list. With the stock list,
-- accented stopwords leak through after unaccent (verified in the prototype). The file
-- french_unaccent.stop (= stock french.stop, unaccented, deduplicated) is baked into the lgx-db image
-- at $SHAREDIR/tsearch_data/.
CREATE TEXT SEARCH DICTIONARY lgx.fr_stem_ua (TEMPLATE = snowball, LANGUAGE = french, STOPWORDS = french_unaccent);
CREATE TEXT SEARCH CONFIGURATION lgx.fr_legal (COPY = pg_catalog.french);
ALTER TEXT SEARCH CONFIGURATION lgx.fr_legal
  ALTER MAPPING FOR hword, hword_part, word, asciiword, asciihword, hword_asciipart WITH public.unaccent, lgx.fr_stem_ua;
-- simple_ua: exact identifiers ('2016/679', 'l', '121-1', '1er', 'bis') for the exact-token branch
CREATE TEXT SEARCH CONFIGURATION lgx.simple_ua (COPY = pg_catalog.simple);
ALTER TEXT SEARCH CONFIGURATION lgx.simple_ua
  ALTER MAPPING FOR hword, hword_part, word, asciiword, asciihword, hword_asciipart WITH public.unaccent, simple;

CREATE FUNCTION lgx.f_unaccent(text) RETURNS text
  LANGUAGE sql IMMUTABLE PARALLEL SAFE STRICT
  RETURN public.unaccent('public.unaccent'::regdictionary, $1);

-- ----------------------------------------------------------------------------------------------- B.3
-- Mutually exclusive serving labels (PLAN.md §2.4). Future versions are a ROLE, not a label.
CREATE TYPE lgx.text_basis AS ENUM
  ('consolidated', 'stub_fallback', 'original_never_amended', 'original_possibly_outdated', 'metadata_only');
CREATE TYPE lgx.version_kind AS ENUM ('original', 'consolidated', 'code', 'code_recueil', 'recueil');
CREATE TYPE lgx.version_role AS ENUM ('current', 'future', 'fallback', 'history_text', 'metadata');

-- ----------------------------------------------------------------------------------------------- B.4
CREATE TABLE lgx_meta.release (                 -- exactly one row per shipped database
  release_id       text PRIMARY KEY,            -- 'data-20261016'
  singleton        boolean NOT NULL DEFAULT true UNIQUE CHECK (singleton),
  sync_date        date NOT NULL,               -- T (SPARQL snapshot date)
  horizon          date GENERATED ALWAYS AS (sync_date + 365) STORED,  -- H: no status projection beyond
  built_at         timestamptz NOT NULL,
  schema_version   int  NOT NULL,
  rules_version    text NOT NULL,
  parser_version   text NOT NULL,
  pipeline_git_sha text NOT NULL,
  inputs_digest    text NOT NULL,               -- sha256 over SPARQL snapshots + robots files + config
  content_digest   text,
  config           jsonb NOT NULL,              -- scope.yaml + embedding.yaml as applied (no secrets)
  counts           jsonb NOT NULL,              -- per text_basis / flag / role
  gates            jsonb NOT NULL,              -- anomaly + eval gate results
  eval             jsonb
);

-- Embedding stage metadata. ZERO rows when provider=none (the shipped default until the user enables it).
-- One row when the release carries vectors (provider mistral; revised 2026-10-17: one provider, one startup probe
-- instead of golden-vector probes). The runtime embeds probe_text through the on-prem endpoint: the reported model id
-- must equal model_id, the vector length dim, and the cosine to probe_vector must be >= probe_min_cosine.
CREATE TABLE lgx.embedding_model (
  embedding_set_id smallint PRIMARY KEY,
  provider         text NOT NULL CHECK (provider IN ('mistral')),
  model_id         text NOT NULL,               -- the id the build-side API reported (e.g. 'mistral-embed')
  model_requested  text NOT NULL,               -- config/embedding.yaml `model`
  dim              int  NOT NULL CHECK (dim > 0 AND dim <= 4000),   -- halfvec HNSW limit
  normalized       boolean NOT NULL DEFAULT true,   -- Mistral: unit norm, so cosine = dot product
  probe_text       text NOT NULL,
  probe_vector     real[] NOT NULL,             -- full precision, as the build-side API returned it
  probe_min_cosine real NOT NULL DEFAULT 0.99 CHECK (probe_min_cosine > 0 AND probe_min_cosine <= 1),
  chunk_count      int  NOT NULL,
  created_at       timestamptz NOT NULL DEFAULT now(),
  CHECK (cardinality(probe_vector) = dim)
);

-- ----------------------------------------------------------------------------------------------- B.5
CREATE TABLE lgx.vocab (                        -- SKOS labels (resource-type, application-status, …)
  concept_uri text PRIMARY KEY,
  scheme      text NOT NULL,
  code        text,
  label_fr    text, label_de text, label_en text
);
CREATE TABLE lgx.legal_text (                   -- attribution, disclaimer, CC BY 4.0 legal code, NOTICE
  code text NOT NULL,                           -- 'attribution' | 'disclaimer' | 'cc_by_4_0' | 'notice'
  lang text NOT NULL CHECK (lang IN ('fr', 'de', 'en')),
  body text NOT NULL,
  PRIMARY KEY (code, lang)
);
CREATE TABLE lgx.warning_def (                  -- catalogue in PLAN.md §8.5 / Appendix D
  code     text PRIMARY KEY,
  severity text NOT NULL CHECK (severity IN ('info', 'medium', 'high')),
  text_fr  text NOT NULL, text_de text NOT NULL, text_en text NOT NULL
);

-- ----------------------------------------------------------------------------------------------- B.6
-- One row per jolux:ComplexWork in the metadata scope (Mémorial A minus RC communal regulations,
-- minus naturalisation laws, minus denylisted ELIs). Status is NEVER read from a stored boolean.
CREATE TABLE lgx.act (
  act_id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  eli_work          text NOT NULL UNIQUE,       -- http://data.legilux.public.lu/eli/etat/leg/loi/2018/08/01/a686
  eli_jo            text UNIQUE,                -- …/a686/jo  (NULL for recueils / code-recueils)
  doc_type          text NOT NULL,              -- LOI | RGD | CODE | CODE_RECUEIL | RECUEIL | Constitution | …
  date_document     date,
  publication_date  date,
  dei_min           date,                       -- earliest dateEntryInForce
  dei_count         smallint NOT NULL DEFAULT 0,-- >1 sets flag entry_dates_multiple (cause unverified)
  dnl               date,                       -- dateNoLongerInForce
  rdf_status        text,                       -- raw code; consulted ONLY for '-implicit' and undated repeal
  memorial_ref      text,                       -- 'Mémorial A n° 686 de 2018' (from jolux:isPartOf)
  historical_legal_id text,
  title             text,
  title_short       text,
  citation_title    text,                       -- « La référence à la présente loi peut se faire … »
  subjects          text[] NOT NULL DEFAULT '{}',
  institutions      text[] NOT NULL DEFAULT '{}',
  text_basis        lgx.text_basis NOT NULL,    -- label at T; runtime recomputes with lgx.serving_at()
  metadata_reason   text CHECK (metadata_reason IN ('repealed', 'repealed_base', 'type', 'licence', 'pdf_only',
                                                   'temporary', 'robots', 'excluded_by_config', 'not_yet_text')),
  flags             text[] NOT NULL DEFAULT '{}'
                    CHECK (flags <@ ARRAY['consolidation_lags', 'pdf_extracted', 'superseded_by_code',
                                          'consolidation_discontinued', 'base_legale_abrogee', 'acte_temporaire',
                                          'stub_no_text', 'entry_dates_multiple', 'is_amending_act',
                                          'old_act', 'licence_scl_source', 'status_flag_conflict']::text[]),
  basis_version_id  bigint,                     -- version whose text is served at T (FK added below)
  superseded_by_act_id bigint REFERENCES lgx.act, -- code-recueil member -> code work
  rank_prior        real NOT NULL DEFAULT 1.0,
  graph_modified    timestamptz,
  title_tsv tsvector GENERATED ALWAYS AS (
       setweight(to_tsvector('lgx.fr_legal'::regconfig, coalesce(title_short, '') || ' ' || coalesce(citation_title, '')), 'A')
    || setweight(to_tsvector('lgx.fr_legal'::regconfig, coalesce(title, '')), 'B')) STORED
);
CREATE INDEX act_type_date  ON lgx.act (doc_type, date_document);
CREATE INDEX act_title_tsv  ON lgx.act USING gin (title_tsv);
CREATE INDEX act_title_trgm ON lgx.act USING gin (lgx.f_unaccent(lower(title)) gin_trgm_ops);
CREATE INDEX act_memorial   ON lgx.act (memorial_ref);

CREATE TABLE lgx.act_alias (                    -- citation resolver: abbreviations, acronyms, short titles
  alias_norm text NOT NULL,                     -- normalised: lower + unaccent + collapsed spaces
  act_id     bigint NOT NULL REFERENCES lgx.act ON DELETE CASCADE,
  kind       text NOT NULL CHECK (kind IN ('short_title', 'citation_title', 'code_abbrev', 'acronym',
                                           'memorial', 'eli', 'curated')),
  reviewed   boolean NOT NULL DEFAULT false,    -- lawyer-reviewed (config/aliases.yaml)
  PRIMARY KEY (alias_norm, act_id)
);
CREATE INDEX act_alias_trgm ON lgx.act_alias USING gin (alias_norm gin_trgm_ops);

-- ----------------------------------------------------------------------------------------------- B.7
-- Metadata for ALL consolidation versions of works in scope (4,719 at T0) + the /jo originals.
-- Text is stored only for role current/future/fallback (and history_text if history.mode is enabled).
CREATE TABLE lgx.act_version (
  version_id       bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  act_id           bigint NOT NULL REFERENCES lgx.act ON DELETE CASCADE,
  eli_version      text NOT NULL UNIQUE,        -- …/a686/consolide/20251226 | …/code/civil/20251226 | …/a686/jo
  kind             lgx.version_kind NOT NULL,
  role             lgx.version_role NOT NULL,
  date_applicability     date,                  -- from SPARQL, never parsed from the URI
  date_end_applicability date,
  rdf_status       text,                        -- raw consolidation flag (informational)
  is_stub          boolean NOT NULL DEFAULT false,  -- version announced without files
  has_files        boolean NOT NULL DEFAULT false,
  has_text         boolean NOT NULL DEFAULT false,  -- text stored in lgx.unit
  text_valid       daterange,                   -- dates on which THIS stored text serves the act
  label            text,                        -- 'Version consolidée applicable au 26/12/2025'
  source_format    text CHECK (source_format IN ('xml', 'html', 'pdf')),
  source_url       text,                        -- https://legilux.public.lu/filestore/eli/etat/leg/…
  licence          text,                        -- jolux:license of the fetched manifestation
  rights           text DEFAULT 'Ministère d''État du Grand-Duché de Luxembourg – Service central de législation',
  legal_value      text,                        -- raw jolux:legalValue; semantics unverified, never displayed
  http_last_modified timestamptz,
  manif_modified   timestamptz,
  graph_modified   timestamptz,
  sha256           bytea,
  bytes            bigint,
  fetched_at       timestamptz,
  parser_version   text,
  created          timestamptz,
  CHECK (has_text = (text_valid IS NOT NULL)),
  CHECK (NOT has_text OR (source_url IS NOT NULL AND licence IS NOT NULL AND sha256 IS NOT NULL)),
  CHECK (NOT (has_text AND is_stub)),
  -- One stored text per act per date (legal-fidelity rule, applied to stored text windows)
  CONSTRAINT one_text_per_act_and_date EXCLUDE USING gist (act_id WITH =, text_valid WITH &&) WHERE (has_text)
);
CREATE INDEX act_version_act ON lgx.act_version (act_id, date_applicability);
ALTER TABLE lgx.act ADD CONSTRAINT act_basis_version_fk
  FOREIGN KEY (basis_version_id) REFERENCES lgx.act_version DEFERRABLE INITIALLY DEFERRED;

-- ----------------------------------------------------------------------------------------------- B.8
CREATE TABLE lgx.unit (                          -- structural tree of stored text versions
  unit_id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  version_id       bigint NOT NULL REFERENCES lgx.act_version ON DELETE CASCADE,
  act_id           bigint NOT NULL REFERENCES lgx.act ON DELETE CASCADE,
  member_act_id    bigint REFERENCES lgx.act,   -- code-recueil member context (route, CSS …)
  parent_id        bigint REFERENCES lgx.unit ON DELETE CASCADE,
  unit_type        text NOT NULL CHECK (unit_type IN ('book', 'part', 'title', 'chapter', 'section', 'subsection',
                     'article', 'paragraph', 'alinea', 'point', 'annex', 'preamble', 'visa', 'page')),
  akn_id           text,                        -- 'art_l_121-1'; never generated recueil ids ('art_N10064')
  anchor           text NOT NULL,               -- AKN id path, HTML anchor, or 'p.345-346' for PDF
  eli_unit         text,                        -- in-XML article ELI (consolidations, codes)
  eli_unit_version text,                        -- …/art_l_121-1/20201101
  eli_unit_derived boolean NOT NULL DEFAULT false, -- JO originals: <work>/art_N minted locally, non-official
  unit_version_date date,                       -- 'rédaction applicable depuis' (INFERRED from the ELI)
  num_raw          text, num_norm text,         -- 'Art. L. 121-1.' / 'l121-1'
  heading          text,
  provenance       text,                        -- '( L. 13 mai 2008 )', marginal 'L. 27.7.92'
  breadcrumb       text,
  ordinal          int NOT NULL,
  depth            smallint NOT NULL,
  text_plain       text,                        -- verbatim basis for quotes; no <del> content
  text_md          text,                        -- ONLY for units with tables/lists (Markdown); NULL otherwise, renderers
                                                -- fall back to text_plain (minimisation: no second copy of plain text)
  text_sha256      bytea,
  is_repealed      boolean NOT NULL DEFAULT false,  -- style="del" / 'Abrogé(s) (…)' -> stub, never chunked
  repeal_note      text,
  is_modifying     boolean NOT NULL DEFAULT false,  -- amending-act article with <mod>/embeddedStructure
  contains_quoted_amendment boolean NOT NULL DEFAULT false,
  amended_by       text[] NOT NULL DEFAULT '{}',    -- textualMod / note sources (ELIs)
  index_terms      text[] NOT NULL DEFAULT '{}',
  page_from        int, page_to int,            -- PDF-derived units
  from_recueil_wid text,                        -- normalised wId when kept from an XML recueil
  UNIQUE (version_id, ordinal),
  CHECK (text_md IS NULL OR text_md <> text_plain)
);
CREATE INDEX unit_article ON lgx.unit (act_id, num_norm) WHERE unit_type = 'article';
CREATE INDEX unit_member  ON lgx.unit (member_act_id, num_norm) WHERE member_act_id IS NOT NULL;
CREATE INDEX unit_eli     ON lgx.unit (eli_unit);
CREATE INDEX unit_parent  ON lgx.unit (parent_id);

CREATE TABLE lgx.article_alias (                 -- 'Art. 9 et 10.' -> art_9_10 / art_9_et_10; '1er' -> 1
  act_id   bigint NOT NULL REFERENCES lgx.act ON DELETE CASCADE,
  num_norm text   NOT NULL,
  unit_id  bigint NOT NULL REFERENCES lgx.unit ON DELETE CASCADE,
  PRIMARY KEY (act_id, num_norm, unit_id)
);

-- ----------------------------------------------------------------------------------------------- B.9
CREATE TABLE lgx.relation (
  src_eli      text NOT NULL,
  src_act_id   bigint REFERENCES lgx.act ON DELETE CASCADE,
  src_unit_id  bigint REFERENCES lgx.unit ON DELETE CASCADE,
  rel_type     text NOT NULL CHECK (rel_type IN ('modifies', 'repeals', 'based_on', 'cites', 'transposes',
                                                 'rectifies', 'refers_to')),
  dst_eli      text NOT NULL,                   -- may point outside the metadata scope (EU ELIs, adm acts)
  dst_act_id   bigint REFERENCES lgx.act ON DELETE SET NULL,
  dst_unit_hint text,                           -- '#art_5' from textualMod destination
  src_dei      date,                            -- for 'amending acts since text date'
  source       text NOT NULL CHECK (source IN ('sparql', 'akn-ref', 'akn-textualmod', 'akn-note')),
  PRIMARY KEY (src_eli, rel_type, dst_eli, source)
);
CREATE INDEX relation_dst ON lgx.relation (dst_act_id, rel_type);
CREATE INDEX relation_src ON lgx.relation (src_act_id, rel_type);

CREATE TABLE lgx.recueil_member (                -- derived (wId / headings) + lawyer-reviewed routing
  recueil_version_id bigint NOT NULL REFERENCES lgx.act_version ON DELETE CASCADE,
  member_act_id      bigint NOT NULL REFERENCES lgx.act ON DELETE CASCADE,
  part_heading       text,                      -- 'Version consolidée applicable au 01/06/2026 : Loi du …'
  member_version_eli text,
  method             text NOT NULL CHECK (method IN ('wid', 'heading', 'curated')),
  reviewed_by        text,
  PRIMARY KEY (recueil_version_id, member_act_id)
);

-- ---------------------------------------------------------------------------------------------- B.10
-- Retrieval units. Validity windows are denormalised from the version/act dates at load time, so an
-- ageing offline database switches versions and drops repealed acts on the right day.
CREATE TABLE lgx.chunk (
  chunk_id         bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  unit_id          bigint NOT NULL REFERENCES lgx.unit ON DELETE CASCADE,
  act_id           bigint NOT NULL REFERENCES lgx.act,
  version_id       bigint NOT NULL REFERENCES lgx.act_version,
  part_no          smallint NOT NULL DEFAULT 1,
  part_count       smallint NOT NULL DEFAULT 1,
  header           text NOT NULL,               -- '{titre court} — {version} > Livre … > Art. X.' + index terms
  body             text NOT NULL,               -- unit text (or one part of it). Deliberate 2nd copy: the generated
                                                -- tsv columns must read in-row text; they are not dumped and are
                                                -- rebuilt at restore, so the dump carries no tsvectors (PLAN §4.3)
  n_tokens         int,
  doc_type         text NOT NULL,
  text_basis       lgx.text_basis NOT NULL,     -- label at T (display hint; runtime recomputes)
  valid_from       date NOT NULL,
  valid_to         date,                        -- exclusive; NULL = open
  searchable       boolean NOT NULL DEFAULT true,   -- false: superseded_by_code, temporary (if stored)
  rank_prior       real NOT NULL DEFAULT 1.0,
  content_sha256   bytea NOT NULL,              -- sha256(header || body); embedding-cache key component
  embedding_set_id smallint REFERENCES lgx.embedding_model,
  embedding        halfvec(:embed_dim),         -- NULL unless the (optional) embedding stage ran
  tsv tsvector GENERATED ALWAYS AS (
       setweight(to_tsvector('lgx.fr_legal'::regconfig, header), 'A')
    || setweight(to_tsvector('lgx.fr_legal'::regconfig, body), 'B')) STORED,
  tsv_exact tsvector GENERATED ALWAYS AS (to_tsvector('lgx.simple_ua'::regconfig, header || ' ' || body)) STORED,
  CHECK ((embedding IS NULL) = (embedding_set_id IS NULL)),
  CHECK (valid_to IS NULL OR valid_to > valid_from)
);
CREATE INDEX chunk_tsv   ON lgx.chunk USING gin (tsv)       WHERE searchable;
CREATE INDEX chunk_exact ON lgx.chunk USING gin (tsv_exact) WHERE searchable;
CREATE INDEX chunk_valid ON lgx.chunk (valid_from, valid_to) WHERE searchable;
CREATE INDEX chunk_unit  ON lgx.chunk (unit_id);
CREATE INDEX chunk_act   ON lgx.chunk (act_id);
-- The HNSW index is created in B.15 ONLY if vectors exist.

-- ---------------------------------------------------------------------------------------------- B.11
CREATE TABLE lgx.change_log (                    -- hashes only, never old text; 12 months rolling
  release_id     text NOT NULL,
  eli_unit       text NOT NULL,                 -- article ELI or '<version_eli>#<anchor>'
  change_type    text NOT NULL CHECK (change_type IN ('added', 'changed', 'repealed', 'removed', 'version_switch')),
  old_sha256     bytea,
  new_sha256     bytea,
  effective_date date,
  PRIMARY KEY (release_id, eli_unit, change_type)
);
CREATE TABLE lgx.data_quality_issue (            -- shown as W_DATA_QUALITY on cited entities
  entity_eli text NOT NULL,
  code       text NOT NULL,                     -- 'flag_date_conflict', 'uri_date_mismatch', 'malformed_wid', …
  detail     text,
  first_seen date NOT NULL,
  PRIMARY KEY (entity_eli, code)
);

-- ---------------------------------------------------------------------------------------------- B.12
-- Build-side only (excluded from the dump): fetch log and sync watermarks.
CREATE TABLE build.fetch_log (
  url text NOT NULL, fetched_at timestamptz NOT NULL, http_status int, bytes bigint, sha256 bytea,
  last_modified timestamptz, elapsed_ms int, PRIMARY KEY (url, fetched_at)
);
CREATE TABLE build.sync_state (key text PRIMARY KEY, value jsonb NOT NULL);   -- watermarks, robots sha256

-- ---------------------------------------------------------------------------------------------- B.13
-- Status computed from dates at query time (PLAN.md §2.2 step 1, §7.3). The Python twin lives in
-- lgx_core.rules; a parity test compares both on all acts × {T, T+1, T+30, T+90, T+365}.
CREATE FUNCTION lgx.act_status(p_dei date, p_dnl date, p_rdf_status text, p_as_of date)
  RETURNS text LANGUAGE sql IMMUTABLE PARALLEL SAFE
  RETURN CASE
    WHEN p_rdf_status = 'no-longer-in-force-implicit'              THEN 'no-longer-in-force'
    WHEN p_dnl IS NOT NULL AND p_dnl <= p_as_of                     THEN 'no-longer-in-force'
    WHEN p_dnl IS NULL AND p_rdf_status = 'no-longer-in-force'      THEN 'no-longer-in-force'
    WHEN p_dei IS NOT NULL AND p_dei > p_as_of                      THEN 'not-yet-in-force'
    ELSE 'in-force' END;

-- The consolidation version whose window contains as_of (may be a file-less stub).
CREATE FUNCTION lgx.version_as_of(p_act bigint, p_as_of date)
  RETURNS bigint LANGUAGE sql STABLE PARALLEL SAFE
  RETURN (SELECT v.version_id FROM lgx.act_version v
           WHERE v.act_id = p_act AND v.kind <> 'original'
             AND v.date_applicability <= p_as_of
             AND (v.date_end_applicability IS NULL OR v.date_end_applicability > p_as_of)
           ORDER BY v.date_applicability DESC LIMIT 1);

-- The stored text that serves the act on as_of (at most one, guaranteed by the EXCLUDE constraint).
CREATE FUNCTION lgx.text_version_as_of(p_act bigint, p_as_of date)
  RETURNS bigint LANGUAGE sql STABLE PARALLEL SAFE
  RETURN (SELECT v.version_id FROM lgx.act_version v
           WHERE v.act_id = p_act AND v.has_text AND v.text_valid @> p_as_of);

-- Serving decision for one act on one date: status, window version, served text, label, flags,
-- next announced version. Warnings are assembled from this row by lgx_core.warnings (Python).
CREATE FUNCTION lgx.serving_at(p_act bigint, p_as_of date)
  RETURNS TABLE (act_id bigint, as_of date, status text, window_version_id bigint, text_version_id bigint,
                 text_basis lgx.text_basis, flags text[], next_version_date date, next_version_has_text boolean)
  LANGUAGE sql STABLE PARALLEL SAFE AS $$
  SELECT a.act_id, p_as_of,
         lgx.act_status(a.dei_min, a.dnl, a.rdf_status, p_as_of),
         w.version_id, t.version_id,
         CASE
           WHEN t.version_id IS NULL                       THEN 'metadata_only'
           WHEN t.kind <> 'original' AND w.version_id IS NOT DISTINCT FROM t.version_id THEN 'consolidated'
           WHEN t.kind <> 'original'                       THEN 'stub_fallback'  -- window version is a stub / unknown
           WHEN a.text_basis = 'original_never_amended'
                AND NOT EXISTS (SELECT 1 FROM lgx.relation r
                                 WHERE r.dst_act_id = a.act_id AND r.rel_type IN ('modifies', 'repeals')
                                   AND r.source = 'sparql' AND r.src_dei <= p_as_of)
                                                           THEN 'original_never_amended'
           ELSE 'original_possibly_outdated' END::lgx.text_basis,
         a.flags, n.date_applicability, n.has_text
    FROM lgx.act a
    LEFT JOIN lgx.act_version w ON w.version_id = lgx.version_as_of(a.act_id, p_as_of)
    LEFT JOIN lgx.act_version t ON t.version_id = lgx.text_version_as_of(a.act_id, p_as_of)
    LEFT JOIN LATERAL (SELECT v.date_applicability, v.has_text FROM lgx.act_version v
                        WHERE v.act_id = a.act_id AND v.kind <> 'original' AND v.date_applicability > p_as_of
                        ORDER BY v.date_applicability LIMIT 1) n ON true
   WHERE a.act_id = p_act $$;

-- Amending / repealing acts in force after the stored text's date (mandatory list for
-- original_possibly_outdated and stub_fallback).
CREATE FUNCTION lgx.amending_acts_since(p_act bigint, p_since date, p_as_of date)
  RETURNS TABLE (src_eli text, rel_type text, src_dei date, title text)
  LANGUAGE sql STABLE PARALLEL SAFE AS $$
  SELECT DISTINCT r.src_eli, r.rel_type, r.src_dei, s.title
    FROM lgx.relation r LEFT JOIN lgx.act s ON s.act_id = r.src_act_id
   WHERE r.dst_act_id = p_act AND r.rel_type IN ('modifies', 'repeals') AND r.source = 'sparql'
     AND r.src_dei > p_since AND r.src_dei <= p_as_of
   ORDER BY r.src_dei $$;

-- Set-returning "view at a given date" for operators and tests: every act with its computed status.
CREATE FUNCTION lgx.acts_as_of(p_as_of date)
  RETURNS TABLE (act_id bigint, eli_work text, doc_type text, status text, text_basis_at_t lgx.text_basis)
  LANGUAGE sql STABLE PARALLEL SAFE AS $$
  SELECT a.act_id, a.eli_work, a.doc_type, lgx.act_status(a.dei_min, a.dnl, a.rdf_status, p_as_of), a.text_basis
    FROM lgx.act a $$;
CREATE VIEW lgx.v_act_status_today AS             -- convenience; tools always pass an explicit as_of
  SELECT * FROM lgx.acts_as_of(current_date);

-- ---------------------------------------------------------------------------------------------- B.14
-- OR-query over fr_legal lexemes (AND is too strict for natural questions).
CREATE FUNCTION lgx.or_query(q text) RETURNS tsquery
  LANGUAGE sql STABLE PARALLEL SAFE
  RETURN (SELECT NULLIF(string_agg(quote_literal(l), ' | '), '')::tsquery
            FROM unnest(tsvector_to_array(to_tsvector('lgx.fr_legal'::regconfig, q))) AS l);

-- Candidate generation + RRF fusion in one statement (PLAN.md §8.3). p_qvec NULL => lexical-only
-- (the default mode until vectors exist and the startup probe passes). The reranker runs afterwards in
-- the MCP server on the returned candidates. Tool filters map 1:1 (Appendix D, D.2.1) and apply to EVERY branch:
--   filters.doc_types -> p_doc_types; filters.act_eli -> p_act_ids (resolved by the server);
--   filters.in_force_only = false -> p_include_future = true (also stored future versions and not-yet-in-force
--   acts, i.e. chunks with valid_from > as_of; text whose window ended before as_of is never returned).
CREATE FUNCTION lgx.search_candidates(
    p_query text, p_qvec halfvec, p_as_of date, p_cited_units bigint[], p_exact tsquery,
    p_doc_types text[], p_limit int DEFAULT 40,
    p_act_ids bigint[] DEFAULT NULL, p_include_future boolean DEFAULT false)
  RETURNS TABLE (chunk_id bigint, score double precision, lex_rank bigint, dense_rank bigint, exact_hit boolean,
                 cited_hit boolean)
  LANGUAGE sql STABLE
  SET hnsw.ef_search = 100
  SET hnsw.iterative_scan = 'relaxed_order'
  AS $$
  WITH q AS (SELECT lgx.or_query(p_query) AS tsq),
  lex AS (
    SELECT x.chunk_id, row_number() OVER (ORDER BY x.s DESC) AS r FROM (
      SELECT c.chunk_id, ts_rank_cd(c.tsv, q.tsq, 32) AS s
        FROM lgx.chunk c, q
       WHERE q.tsq IS NOT NULL AND c.searchable AND c.tsv @@ q.tsq
         AND (c.valid_from <= p_as_of OR p_include_future) AND (c.valid_to IS NULL OR c.valid_to > p_as_of)
         AND (p_doc_types IS NULL OR c.doc_type = ANY (p_doc_types))
         AND (p_act_ids IS NULL OR c.act_id = ANY (p_act_ids))
       ORDER BY s DESC LIMIT 50) x),
  dense AS (
    SELECT y.chunk_id, row_number() OVER (ORDER BY y.dist) AS r FROM (
      SELECT c.chunk_id, c.embedding <=> p_qvec AS dist
        FROM lgx.chunk c
       WHERE p_qvec IS NOT NULL AND c.searchable AND c.embedding IS NOT NULL
         AND (c.valid_from <= p_as_of OR p_include_future) AND (c.valid_to IS NULL OR c.valid_to > p_as_of)
         AND (p_doc_types IS NULL OR c.doc_type = ANY (p_doc_types))
         AND (p_act_ids IS NULL OR c.act_id = ANY (p_act_ids))
       ORDER BY c.embedding <=> p_qvec LIMIT 50) y),
  exact AS (
    SELECT c.chunk_id, row_number() OVER (ORDER BY c.chunk_id) AS r
      FROM lgx.chunk c
     WHERE p_exact IS NOT NULL AND c.searchable AND c.tsv_exact @@ p_exact
       AND (c.valid_from <= p_as_of OR p_include_future) AND (c.valid_to IS NULL OR c.valid_to > p_as_of)
       AND (p_doc_types IS NULL OR c.doc_type = ANY (p_doc_types))
       AND (p_act_ids IS NULL OR c.act_id = ANY (p_act_ids))
     LIMIT 20),
  cited AS (
    SELECT c.chunk_id, 1::bigint AS r FROM lgx.chunk c
     WHERE c.unit_id = ANY (p_cited_units)
       AND (c.valid_from <= p_as_of OR p_include_future) AND (c.valid_to IS NULL OR c.valid_to > p_as_of)
       AND (p_doc_types IS NULL OR c.doc_type = ANY (p_doc_types))
       AND (p_act_ids IS NULL OR c.act_id = ANY (p_act_ids))),
  fused AS (
    SELECT u.chunk_id, sum(u.w / (60.0 + u.r)) AS s,
           min(u.r) FILTER (WHERE u.src = 'lex') AS lex_rank, min(u.r) FILTER (WHERE u.src = 'dense') AS dense_rank,
           bool_or(u.src = 'exact') AS exact_hit, bool_or(u.src = 'cited') AS cited_hit
      FROM (SELECT chunk_id, r, 1.0 AS w, 'lex' AS src FROM lex
            UNION ALL SELECT chunk_id, r, 1.0, 'dense' FROM dense
            UNION ALL SELECT chunk_id, r, 2.0, 'exact' FROM exact
            UNION ALL SELECT chunk_id, r, 10.0, 'cited' FROM cited) u
     GROUP BY u.chunk_id)
  SELECT f.chunk_id, f.s * c.rank_prior, f.lex_rank, f.dense_rank, f.exact_hit, f.cited_hit
    FROM fused f JOIN lgx.chunk c USING (chunk_id)
   ORDER BY 2 DESC LIMIT p_limit $$;

-- ---------------------------------------------------------------------------------------------- B.15
-- Post-load, run by `lgx load` after COPY. Never shipped as a separate step: the dump carries the DDL.
ANALYZE;

-- Conditional HNSW: only when the optional embedding stage produced vectors (default: no vectors).
-- halfvec HNSW supports up to 4,000 dims; lgx.embedding_model.dim is checked against it (Mistral Embed: 1024).
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM lgx.chunk WHERE embedding IS NOT NULL) THEN
    EXECUTE 'CREATE INDEX chunk_hnsw ON lgx.chunk USING hnsw (embedding halfvec_cosine_ops) '
         || 'WITH (m = 16, ef_construction = 128) WHERE searchable AND embedding IS NOT NULL';
  END IF;
END $$;

-- Build invariants: the load fails unless every row of this view has n = 0.
CREATE VIEW lgx_meta.invariant_violations AS
  SELECT 'chunk_of_repealed_unit' AS check_name, count(*) AS n
    FROM lgx.chunk c JOIN lgx.unit u USING (unit_id) WHERE u.is_repealed
  UNION ALL SELECT 'chunk_of_modifying_article', count(*)
    FROM lgx.chunk c JOIN lgx.unit u USING (unit_id) WHERE u.is_modifying
  UNION ALL SELECT 'text_version_without_cc_by_licence', count(*)
    FROM lgx.act_version WHERE has_text AND licence IS DISTINCT FROM 'http://creativecommons.org/licenses/by/4.0/'
  UNION ALL SELECT 'adm_branch_present', count(*)
    FROM lgx.act WHERE eli_work LIKE 'http://data.legilux.public.lu/eli/etat/adm/%'
  UNION ALL SELECT 'consolidated_basis_without_text', count(*)
    FROM lgx.act a JOIN lgx.act_version v ON v.version_id = a.basis_version_id
   WHERE a.text_basis = 'consolidated' AND NOT v.has_text
  UNION ALL SELECT 'possibly_outdated_without_amending_relation', count(*)
    FROM lgx.act a
   WHERE a.text_basis = 'original_possibly_outdated'
     AND NOT EXISTS (SELECT 1 FROM lgx.relation r WHERE r.dst_act_id = a.act_id AND r.rel_type IN ('modifies', 'repeals'))
     AND NOT EXISTS (SELECT 1 FROM lgx.act_version v WHERE v.act_id = a.act_id AND v.kind <> 'original')
  UNION ALL SELECT 'vector_without_embedding_model', count(*)
    FROM lgx.chunk c WHERE c.embedding IS NOT NULL
     AND NOT EXISTS (SELECT 1 FROM lgx.embedding_model m WHERE m.embedding_set_id = c.embedding_set_id)
  UNION ALL SELECT 'embedding_model_dim_mismatch', count(*)
    FROM lgx.chunk c JOIN lgx.embedding_model m USING (embedding_set_id) WHERE vector_dims(c.embedding) <> m.dim
  UNION ALL SELECT 'missing_legal_texts', 4 - count(DISTINCT code)
    FROM lgx.legal_text WHERE code IN ('attribution', 'disclaimer', 'cc_by_4_0', 'notice') AND lang = 'fr'
  UNION ALL SELECT 'release_row_missing', 1 - count(*) FROM lgx_meta.release;
-- Also enforced by the loader (Python, needs the robots/GDPR denylist which is NOT shipped):
--   no denylisted ELI in act/relation; selection counts within the anomaly-gate tolerances.

-- ---------------------------------------------------------------------------------------------- B.16
-- Roles are cluster-level. `lgx install` creates them once; `lgx update-data` re-applies the GRANTs
-- after each restore (dumps are made with --no-owner --no-privileges).
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'lgx_ro') THEN
    CREATE ROLE lgx_ro LOGIN CONNECTION LIMIT 40;   -- password set from the Docker secret by `lgx install`
  END IF;
END $$;
ALTER ROLE lgx_ro SET default_transaction_read_only = on;
ALTER ROLE lgx_ro SET statement_timeout = '5s';
ALTER ROLE lgx_ro SET idle_in_transaction_session_timeout = '10s';
REVOKE ALL ON SCHEMA build FROM PUBLIC;
GRANT USAGE ON SCHEMA lgx, lgx_meta TO lgx_ro;
GRANT SELECT ON ALL TABLES IN SCHEMA lgx, lgx_meta TO lgx_ro;
GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA lgx TO lgx_ro;
-- After restore + ANALYZE on the runtime host:  ALTER DATABASE legilux SET default_transaction_read_only = on;
