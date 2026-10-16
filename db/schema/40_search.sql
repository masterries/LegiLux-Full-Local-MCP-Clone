-- =====================================================================================================
-- Part 40: lexical / hybrid candidate generation with RRF fusion (Appendix B.14, PLAN §8.3).
-- Deviation from Appendix B: the exact-identifier branch now honours p_doc_types (like the lexical and
-- dense branches) and keeps its 20 best matches by ts_rank_cd instead of the 20 lowest chunk ids.
-- =====================================================================================================
\set ON_ERROR_STOP on

-- ---------------------------------------------------------------------------------------------- B.14
-- OR-query over fr_legal lexemes (AND is too strict for natural questions).
CREATE FUNCTION lgx.or_query(q text) RETURNS tsquery
  LANGUAGE sql STABLE PARALLEL SAFE
  RETURN (SELECT NULLIF(string_agg(quote_literal(l), ' | '), '')::tsquery
            FROM unnest(tsvector_to_array(to_tsvector('lgx.fr_legal'::regconfig, q))) AS l);

-- Candidate generation + RRF fusion in one statement (PLAN.md §8.4). p_qvec NULL => lexical-only
-- (the default mode until vectors exist and the parity check passes). The reranker runs afterwards in
-- the MCP server on the returned candidates.
CREATE FUNCTION lgx.search_candidates(
    p_query text, p_qvec halfvec, p_as_of date, p_cited_units bigint[], p_exact tsquery,
    p_doc_types text[], p_limit int DEFAULT 40)
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
         AND c.valid_from <= p_as_of AND (c.valid_to IS NULL OR c.valid_to > p_as_of)
         AND (p_doc_types IS NULL OR c.doc_type = ANY (p_doc_types))
       ORDER BY s DESC LIMIT 50) x),
  dense AS (
    SELECT y.chunk_id, row_number() OVER (ORDER BY y.dist) AS r FROM (
      SELECT c.chunk_id, c.embedding <=> p_qvec AS dist
        FROM lgx.chunk c
       WHERE p_qvec IS NOT NULL AND c.searchable AND c.embedding IS NOT NULL
         AND c.valid_from <= p_as_of AND (c.valid_to IS NULL OR c.valid_to > p_as_of)
         AND (p_doc_types IS NULL OR c.doc_type = ANY (p_doc_types))
       ORDER BY c.embedding <=> p_qvec LIMIT 50) y),
  exact AS (
    SELECT z.chunk_id, row_number() OVER (ORDER BY z.s DESC, z.chunk_id) AS r FROM (
      SELECT c.chunk_id, ts_rank_cd(c.tsv_exact, p_exact) AS s
        FROM lgx.chunk c
       WHERE p_exact IS NOT NULL AND c.searchable AND c.tsv_exact @@ p_exact
         AND c.valid_from <= p_as_of AND (c.valid_to IS NULL OR c.valid_to > p_as_of)
         AND (p_doc_types IS NULL OR c.doc_type = ANY (p_doc_types))
       ORDER BY s DESC, c.chunk_id LIMIT 20) z),
  cited AS (
    SELECT c.chunk_id, 1::bigint AS r FROM lgx.chunk c
     WHERE c.unit_id = ANY (p_cited_units)
       AND c.valid_from <= p_as_of AND (c.valid_to IS NULL OR c.valid_to > p_as_of)),
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

