-- =====================================================================================================
-- Part 30: status functions, convenience views and build invariants (Appendix B.13 + B.15 view).
-- Python twins live in lgx_core.rules (status.py, serving.py); a parity test compares both.
-- Deviation from Appendix B: the invariant view is created with the schema (not in post-load) so the
-- loader and the tests can query it on an empty database.
-- =====================================================================================================
\set ON_ERROR_STOP on

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

