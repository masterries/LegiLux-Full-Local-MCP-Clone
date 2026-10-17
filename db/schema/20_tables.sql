-- =====================================================================================================
-- Part 20: enums and tables (Appendix B.3–B.12).
-- psql variable embed_dim (default 1024) sets the halfvec dimension of lgx.chunk.embedding. The column
-- stays NULL unless the optional embedding stage runs (provider none = default = no vectors).
-- Deviations from Appendix B:
--   * lgx.warning_def gains `variants jsonb` (texts that replace the base text, e.g. W_STUB old_fallback;
--     Appendix D writes them as optional [ ]/{ } fragments).
--   * lgx.act.metadata_reason gains 'not_loaded': the selection chose a text for the act, but this build
--     holds none (a sample release, or a file that could not be fetched/parsed; reported by the gates).
--   * lgx.relation: the natural key (src_eli, rel_type, dst_eli, source) collapsed AKN edges (one row per
--     modifying act and target work, although a textualMod targets one article and an akn-ref belongs to one
--     citing unit: 6,452 sample edges -> 1,618). It now has a surrogate `relation_id`, a resolved
--     `dst_unit_id` (destination unit of a textualMod / note hint or an article ELI) and a unique key over
--     the endpoints incl. the units (NULLS NOT DISTINCT).
-- =====================================================================================================
\set ON_ERROR_STOP on
\if :{?embed_dim}
\else
  \set embed_dim 1024
\endif

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
CREATE TABLE lgx.embedding_model (
  embedding_set_id smallint PRIMARY KEY,
  provider         text NOT NULL CHECK (provider IN ('mistral-api', 'mistral-local', 'openai-compatible', 'tei')),
  model_id         text NOT NULL,               -- 'mistral-embed-2312' (pinned id, never the alias)
  model_version    text NOT NULL,               -- 'v23.12'
  model_aliases    text[] NOT NULL DEFAULT '{}',-- ids the runtime endpoint may report for the same model
  dim              int  NOT NULL CHECK (dim > 0),
  normalized       boolean NOT NULL,            -- Mistral: unit norm, so cosine = dot product
  distance         text NOT NULL DEFAULT 'cosine' CHECK (distance IN ('cosine', 'inner_product', 'l2')),
  vector_type      text NOT NULL DEFAULT 'halfvec' CHECK (vector_type IN ('halfvec', 'bit+halfvec')),
  doc_prefix       text NOT NULL DEFAULT '',    -- none for Mistral
  query_prefix     text NOT NULL DEFAULT '',    -- none for Mistral
  parity_threshold real NOT NULL DEFAULT 0.999 CHECK (parity_threshold > 0 AND parity_threshold <= 1),
  chunk_count      int  NOT NULL,
  created_at       timestamptz NOT NULL DEFAULT now()
);
-- Golden-vector probes: fixed texts + their full-precision (float4) document-side vectors.
-- At MCP start the runtime adapter embeds the same texts; every cosine must be >= parity_threshold.
CREATE TABLE lgx.embedding_probe (
  embedding_set_id smallint NOT NULL REFERENCES lgx.embedding_model ON DELETE CASCADE,
  probe_no         smallint NOT NULL,
  probe_text       text  NOT NULL,
  text_sha256      bytea NOT NULL,
  vector           real[] NOT NULL,
  PRIMARY KEY (embedding_set_id, probe_no)
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
CREATE TABLE lgx.warning_def (                  -- catalogue in PLAN.md §8.6 / Appendix D
  code     text PRIMARY KEY,
  severity text NOT NULL CHECK (severity IN ('info', 'medium', 'high')),
  text_fr  text NOT NULL, text_de text NOT NULL, text_en text NOT NULL,
  variants jsonb NOT NULL DEFAULT '{}'::jsonb   -- {variant: {fr, de, en}}; a variant replaces the base text
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
                                                   'temporary', 'robots', 'excluded_by_config', 'not_yet_text',
                                                   'not_loaded')),
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
  text_md          text,                        -- tables as Markdown
  text_sha256      bytea,
  is_repealed      boolean NOT NULL DEFAULT false,  -- style="del" / 'Abrogé(s) (…)' -> stub, never chunked
  repeal_note      text,
  is_modifying     boolean NOT NULL DEFAULT false,  -- amending-act article with <mod>/embeddedStructure
  contains_quoted_amendment boolean NOT NULL DEFAULT false,
  amended_by       text[] NOT NULL DEFAULT '{}',    -- textualMod / note sources (ELIs)
  index_terms      text[] NOT NULL DEFAULT '{}',
  page_from        int, page_to int,            -- PDF-derived units
  from_recueil_wid text,                        -- normalised wId when kept from an XML recueil
  UNIQUE (version_id, ordinal)
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
  relation_id  bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  src_eli      text NOT NULL,
  src_act_id   bigint REFERENCES lgx.act ON DELETE CASCADE,
  src_unit_id  bigint REFERENCES lgx.unit ON DELETE CASCADE,   -- citing unit (akn-ref)
  rel_type     text NOT NULL CHECK (rel_type IN ('modifies', 'repeals', 'based_on', 'cites', 'transposes',
                                                 'rectifies', 'refers_to')),
  dst_eli      text NOT NULL,                   -- may point outside the metadata scope (EU ELIs, adm acts)
  dst_act_id   bigint REFERENCES lgx.act ON DELETE SET NULL,
  dst_unit_id  bigint REFERENCES lgx.unit ON DELETE SET NULL,  -- resolved destination unit (hint / article ELI)
  dst_unit_hint text,                           -- 'art_5' from textualMod destination (raw, kept when unresolved)
  src_dei      date,                            -- for 'amending acts since text date'
  source       text NOT NULL CHECK (source IN ('sparql', 'akn-ref', 'akn-textualmod', 'akn-note')),
  CONSTRAINT relation_endpoints_key UNIQUE NULLS NOT DISTINCT
    (src_eli, rel_type, dst_eli, source, src_unit_id, dst_unit_id, dst_unit_hint)
);
CREATE INDEX relation_dst      ON lgx.relation (dst_act_id, rel_type);
CREATE INDEX relation_src      ON lgx.relation (src_act_id, rel_type);
CREATE INDEX relation_dst_unit ON lgx.relation (dst_unit_id) WHERE dst_unit_id IS NOT NULL;
CREATE INDEX relation_src_unit ON lgx.relation (src_unit_id) WHERE src_unit_id IS NOT NULL;

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
  body             text NOT NULL,
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

