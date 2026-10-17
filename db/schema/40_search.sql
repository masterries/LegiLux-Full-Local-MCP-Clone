-- =====================================================================================================
-- Part 40: lexical / hybrid candidate generation with RRF fusion (Appendix B.14, PLAN §8.3).
-- Deviations from Appendix B:
--   * the exact-identifier branch honours p_doc_types (like the lexical and dense branches) and keeps its 20
--     best matches by ts_rank_cd instead of the 20 lowest chunk ids;
--   * (M1 loader stage) PLAN §8.3 applies EVERY tool filter to EVERY branch, but the sketch had no act filter,
--     no in_force_only and an unfiltered cited branch. Two optional trailing parameters were added (existing
--     6/7-argument calls keep working): p_act_ids (search_legislation filters.act_eli → act ids) and
--     p_in_force_only (default true: window contains as_of; false also admits stored windows that start
--     after as_of — future versions, not-yet-in-force acts, PLAN §7.3). The cited branch now honours the
--     window, p_doc_types and p_act_ids (it still ignores `searchable`: an explicit citation is answered).
--   * (M2 serving stage, "search API 2"; ADR docs/adr/0001-lexical-ranker.md) native FTS has no IDF: with an
--     OR query, ts_rank_cd lets frequent words (« code », « travail », « quel ») outrank the article that
--     carries the rare ones (« préavis »: 72 of 10,220 sample chunks). The lexical branch is now a BM25-like
--     ranker in SQL, with no new table and no extension:
--       score(d) = [ Σ_t idf(t) · w(t) · sat_tf(d, t) · (1 + header_weight · [t in header])
--                    + phrase_weight · Σ_(a,b adjacent in the query, ≤ 3 positions apart in d) (idf(a) + idf(b)) / 2 ]
--                  · (1 + act_boost · [d's act is named in the query])
--                  / ((1 − length_b) + length_b · tokens(d) / mean tokens of the candidate pool)
--     idf(t) = ln(1 + (N − df + 0.5) / (df + 0.5)), N = reltuples of lgx.chunk, df counted on the GIN index and
--     capped at df_cap (cost bound at full scale; all very frequent terms share the floor idf); sat_tf =
--     ts_rank({0,0,1,1}, tsv, t) (tf saturation, 1 → 0.61, 3 → 0.83, 9 → 0.94); w(t) = 1 for the user's words,
--     expansion_weight for server-side expansion terms (p_expansion). Candidates: the chunks matching the
--     selective terms (df ≤ pool_df_max and below df_cap; see search API 3 below if none is), the pool_cap best by
--     Σ idf of matched terms.
--     New optional trailing parameters: p_expansion (expansion text), p_boost_act_ids (acts the query names) and
--     p_opts (jsonb knobs, defaults below; {"ranker": "rank_cd"} restores the M1 ts_rank_cd branch, with the
--     M1 coverage column, for comparisons). New output columns: lex_score, coverage, phrase_hits, act_boosted.
--     Defaults (G2 v1, both halves agreeing; eval/reports/m2-baseline.md): header_weight 0.5, phrase_weight 0.5,
--     length_b 0.25, act_boost 1.0, expansion_weight 0.3, df_cap = pool_df_max 5000, pool_cap 1000, lex_limit 50.
--     Earlier calls (6–9 arguments, gates, package smoke, readyz canned query) are unchanged.
--   * ("search API 3", M2 review 2026-10-17) p_member_act_id: restrict every branch to one member act of a
--     code-recueil (its own original and the code units carrying that member_act_id) INSIDE the SQL, before the
--     lex_limit / pool caps — search_legislation filters.act_eli of a superseded member used to filter after the
--     LIMIT and lost most of the member's matches (PLAN §8.3: every filter on every branch). The branch gates
--     (ranker bm25 / rank_cd) test the parameter itself, a one-time filter, instead of a join with the options
--     CTE (the rank_cd branch used to scan and rank every matching chunk on every bm25 search); query_terms
--     counts each term's df once (MATERIALIZED); with no selective term the pool is the chunks holding all the
--     user's terms (AND), the OR of every term only when that is empty; exact identifiers matched in a chunk's
--     header (« Art. L. 124-3. ») rank before chunks that only cite them.
-- =====================================================================================================
\set ON_ERROR_STOP on

-- ---------------------------------------------------------------------------------------------- B.14
-- OR-query over fr_legal lexemes (AND is too strict for natural questions). Kept for the rank_cd branch.
CREATE FUNCTION lgx.or_query(q text) RETURNS tsquery
  LANGUAGE sql STABLE PARALLEL SAFE
  RETURN (SELECT NULLIF(string_agg(quote_literal(l), ' | '), '')::tsquery
            FROM unnest(tsvector_to_array(to_tsvector('lgx.fr_legal'::regconfig, q))) AS l);

CREATE FUNCTION lgx.chunk_window_ok(p_from date, p_to date, p_as_of date, p_in_force_only boolean)
  RETURNS boolean LANGUAGE sql IMMUTABLE PARALLEL SAFE
  RETURN (p_to IS NULL OR p_to > p_as_of) AND (p_from <= p_as_of OR NOT coalesce(p_in_force_only, true));

-- Query terms with their weight (1 = the user's words, p_expansion_weight = server-side expansion), first
-- position in the query (phrase pairs; NULL for expansion terms), document frequency and idf.
CREATE FUNCTION lgx.query_terms(p_query text, p_expansion text DEFAULT NULL,
                                p_expansion_weight double precision DEFAULT 0.3, p_df_cap int DEFAULT 5000)
  RETURNS TABLE (lexeme text, qweight double precision, qpos int, df bigint, idf double precision)
  LANGUAGE sql STABLE PARALLEL SAFE
  AS $$
  WITH t AS (
    SELECT u.lexeme, 1.0::float8 AS w, u.positions[1]::int AS pos
      FROM unnest(to_tsvector('lgx.fr_legal'::regconfig, coalesce(p_query, ''))) u
    UNION ALL
    SELECT u.lexeme, coalesce(p_expansion_weight, 0.3), NULL::int
      FROM unnest(to_tsvector('lgx.fr_legal'::regconfig, coalesce(p_expansion, ''))) u),
  g AS (SELECT t.lexeme, max(t.w) AS w, min(t.pos) AS pos FROM t GROUP BY t.lexeme),
  n AS (SELECT greatest(1.0, coalesce(nullif(greatest(c.reltuples, 0), 0), (SELECT count(*) FROM lgx.chunk)))::float8 AS n
          FROM pg_class c WHERE c.oid = 'lgx.chunk'::regclass),
  d AS MATERIALIZED (SELECT g.lexeme, g.w, g.pos,  -- once per term: the idf expression reads df twice
               (SELECT count(*) FROM (SELECT 1 FROM lgx.chunk c
                                       WHERE c.searchable AND c.tsv @@ quote_literal(g.lexeme)::tsquery
                                       LIMIT greatest(p_df_cap, 1)) z) AS df
          FROM g)
  SELECT d.lexeme, d.w, d.pos, d.df, ln(1.0 + (n.n - d.df + 0.5) / (d.df + 0.5)) FROM d, n $$;

-- Candidate generation + RRF fusion in one statement (PLAN.md §8.3). p_qvec NULL => lexical-only (the default
-- mode until vectors exist and the parity check passes). The reranker runs afterwards in the MCP server on the
-- returned candidates.
CREATE FUNCTION lgx.search_candidates(
    p_query text, p_qvec halfvec, p_as_of date, p_cited_units bigint[], p_exact tsquery,
    p_doc_types text[], p_limit int DEFAULT 40, p_act_ids bigint[] DEFAULT NULL, p_in_force_only boolean DEFAULT true,
    p_expansion text DEFAULT NULL, p_boost_act_ids bigint[] DEFAULT NULL, p_opts jsonb DEFAULT NULL,
    p_member_act_id bigint DEFAULT NULL)
  RETURNS TABLE (chunk_id bigint, score double precision, lex_rank bigint, dense_rank bigint, exact_hit boolean,
                 cited_hit boolean, lex_score double precision, coverage int, phrase_hits int, act_boosted boolean)
  LANGUAGE sql STABLE
  SET hnsw.ef_search = 100
  SET hnsw.iterative_scan = 'relaxed_order'
  AS $$
  WITH o AS (
    SELECT coalesce((p_opts->>'df_cap')::int, 5000) AS df_cap,
           coalesce((p_opts->>'pool_df_max')::int, 5000) AS pool_df_max,
           coalesce((p_opts->>'pool_cap')::int, 1000) AS pool_cap,
           coalesce((p_opts->>'header_weight')::float8, 0.5) AS hw,
           coalesce((p_opts->>'phrase_weight')::float8, 0.5) AS pw,
           coalesce((p_opts->>'length_b')::float8, 0.25) AS lb,
           coalesce((p_opts->>'act_boost')::float8, 1.0) AS ab,
           coalesce((p_opts->>'expansion_weight')::float8, 0.3) AS ew,
           coalesce((p_opts->>'lex_limit')::int, 50) AS lex_limit),
  -- the member restriction (search API 3): NULL = none; otherwise the member's own units and its code copies
  mem AS (SELECT u.unit_id FROM lgx.unit u WHERE p_member_act_id IS NOT NULL AND u.member_act_id = p_member_act_id),
  -- ------------------------------------------------------------------ bm25-like lexical branch
  qt AS (SELECT t.*, quote_literal(t.lexeme)::tsquery AS tq, (quote_literal(t.lexeme) || ':A')::tsquery AS tqa
           FROM o, lgx.query_terms(p_query, p_expansion, o.ew, o.df_cap) t
          WHERE coalesce(p_opts->>'ranker', 'bm25') = 'bm25' AND t.df > 0),
  sel AS (  -- selective terms (OR); none selective: all of the user's terms (AND), the OR of every term as fallback
    -- a df at the cap means « at least df_cap »: never selective (with df_cap = pool_df_max every capped term
    -- used to count as selective and the pool became every chunk matching any word of the question)
    SELECT (SELECT string_agg(qt.tq::text, ' | ')::tsquery FROM qt, o
             WHERE qt.df <= o.pool_df_max AND qt.df < o.df_cap) AS q_or,
           (SELECT string_agg(qt.tq::text, ' & ')::tsquery FROM qt WHERE qt.qpos IS NOT NULL) AS q_and,
           (SELECT string_agg(qt.tq::text, ' | ')::tsquery FROM qt) AS q_all),
  cand AS (  -- scalar subqueries: one-time filters / index conditions, the fallback runs only when needed
    SELECT c.chunk_id, c.act_id, c.tsv, c.n_tokens FROM lgx.chunk c
     WHERE c.searchable AND c.tsv @@ (SELECT coalesce(sel.q_or, sel.q_and) FROM sel)
       AND lgx.chunk_window_ok(c.valid_from, c.valid_to, p_as_of, p_in_force_only)
       AND (p_doc_types IS NULL OR c.doc_type = ANY (p_doc_types))
       AND (p_act_ids IS NULL OR c.act_id = ANY (p_act_ids))
       AND (p_member_act_id IS NULL OR c.act_id = p_member_act_id OR c.unit_id IN (SELECT unit_id FROM mem))
    UNION ALL
    SELECT c.chunk_id, c.act_id, c.tsv, c.n_tokens FROM lgx.chunk c
     WHERE (SELECT sel.q_or FROM sel) IS NULL
       AND NOT EXISTS (SELECT 1 FROM lgx.chunk c2
                        WHERE c2.searchable AND c2.tsv @@ (SELECT sel.q_and FROM sel)
                          AND lgx.chunk_window_ok(c2.valid_from, c2.valid_to, p_as_of, p_in_force_only)
                          AND (p_doc_types IS NULL OR c2.doc_type = ANY (p_doc_types))
                          AND (p_act_ids IS NULL OR c2.act_id = ANY (p_act_ids))
                          AND (p_member_act_id IS NULL OR c2.act_id = p_member_act_id
                               OR c2.unit_id IN (SELECT unit_id FROM mem)))
       AND c.searchable AND c.tsv @@ (SELECT sel.q_all FROM sel)
       AND lgx.chunk_window_ok(c.valid_from, c.valid_to, p_as_of, p_in_force_only)
       AND (p_doc_types IS NULL OR c.doc_type = ANY (p_doc_types))
       AND (p_act_ids IS NULL OR c.act_id = ANY (p_act_ids))
       AND (p_member_act_id IS NULL OR c.act_id = p_member_act_id OR c.unit_id IN (SELECT unit_id FROM mem))),
  pool AS (
    SELECT c.chunk_id, c.act_id, c.tsv, c.n_tokens FROM cand c
     ORDER BY (SELECT sum(qt.idf * qt.qweight) FROM qt WHERE c.tsv @@ qt.tq) DESC, c.chunk_id
     LIMIT (SELECT pool_cap FROM o)),
  pairs AS (  -- adjacent query terms (by position, stopword gaps included): proximity window of 3
    SELECT a.lexeme AS la, b.lexeme AS lb, (a.idf + b.idf) / 2 AS idf,
           (format('%s <1> %s | %s <2> %s | %s <3> %s', a.tq, b.tq, a.tq, b.tq, a.tq, b.tq))::tsquery AS pq
      FROM qt a JOIN LATERAL (SELECT * FROM qt b WHERE b.qpos > a.qpos ORDER BY b.qpos LIMIT 1) b ON true
     WHERE a.qpos IS NOT NULL),
  terms AS (
    SELECT p.chunk_id, p.act_id, p.n_tokens,
           sum(qt.idf * qt.qweight * ts_rank('{0,0,1,1}'::real[], p.tsv, qt.tq, 0)
               * (1.0 + o.hw * (p.tsv @@ qt.tqa)::int)) AS s,
           count(*) FILTER (WHERE qt.qpos IS NOT NULL) AS cov
      FROM pool p JOIN qt ON p.tsv @@ qt.tq, o
     GROUP BY p.chunk_id, p.act_id, p.n_tokens),
  phr AS (
    SELECT p.chunk_id, count(*) AS n, sum(pr.idf) AS s FROM pool p JOIN pairs pr ON p.tsv @@ pr.pq GROUP BY p.chunk_id),
  bm AS (
    SELECT t.chunk_id, t.cov, coalesce(ph.n, 0) AS ph, (p_boost_act_ids IS NOT NULL AND t.act_id = ANY (p_boost_act_ids)) AS boosted,
           (t.s + o.pw * coalesce(ph.s, 0))
             * (1.0 + o.ab * (p_boost_act_ids IS NOT NULL AND t.act_id = ANY (p_boost_act_ids))::int)
             / ((1.0 - o.lb) + o.lb * greatest(t.n_tokens, 1) / greatest((SELECT avg(n_tokens) FROM pool), 1)) AS s
      FROM terms t LEFT JOIN phr ph USING (chunk_id), o),
  lex_bm AS (
    SELECT bm.chunk_id, bm.s, bm.cov, bm.ph, bm.boosted, row_number() OVER (ORDER BY bm.s DESC, bm.chunk_id) AS r
      FROM bm ORDER BY bm.s DESC, bm.chunk_id LIMIT (SELECT lex_limit FROM o)),
  -- ------------------------------------------------------------------ legacy branch (ts_rank_cd, no IDF)
  q AS (SELECT lgx.or_query(p_query) AS tsq WHERE coalesce(p_opts->>'ranker', 'bm25') = 'rank_cd'),
  lex_cd AS (
    SELECT x.chunk_id, x.s,
           (SELECT count(*) FROM unnest(tsvector_to_array(to_tsvector('lgx.fr_legal'::regconfig, coalesce(p_query, ''))))
                AS l(lex) WHERE x.tsv @@ quote_literal(l.lex)::tsquery) AS cov,  -- the M1 coverage re-ordering key
           0::bigint AS ph, false AS boosted,
           row_number() OVER (ORDER BY x.s DESC, x.chunk_id) AS r FROM (
      SELECT c.chunk_id, c.tsv, ts_rank_cd(c.tsv, q.tsq, 32)::float8 AS s
        FROM lgx.chunk c, q
       WHERE coalesce(p_opts->>'ranker', 'bm25') = 'rank_cd' AND q.tsq IS NOT NULL AND c.searchable AND c.tsv @@ q.tsq
         AND lgx.chunk_window_ok(c.valid_from, c.valid_to, p_as_of, p_in_force_only)
         AND (p_doc_types IS NULL OR c.doc_type = ANY (p_doc_types))
         AND (p_act_ids IS NULL OR c.act_id = ANY (p_act_ids))
         AND (p_member_act_id IS NULL OR c.act_id = p_member_act_id OR c.unit_id IN (SELECT unit_id FROM mem))
       ORDER BY s DESC, c.chunk_id LIMIT 50) x),
  lex AS (SELECT * FROM lex_bm UNION ALL SELECT * FROM lex_cd),
  dense AS (
    SELECT y.chunk_id, row_number() OVER (ORDER BY y.dist, y.chunk_id) AS r FROM (
      SELECT c.chunk_id, c.embedding <=> p_qvec AS dist
        FROM lgx.chunk c
       WHERE p_qvec IS NOT NULL AND c.searchable AND c.embedding IS NOT NULL
         AND lgx.chunk_window_ok(c.valid_from, c.valid_to, p_as_of, p_in_force_only)
         AND (p_doc_types IS NULL OR c.doc_type = ANY (p_doc_types))
         AND (p_act_ids IS NULL OR c.act_id = ANY (p_act_ids))
         AND (p_member_act_id IS NULL OR c.act_id = p_member_act_id OR c.unit_id IN (SELECT unit_id FROM mem))
       ORDER BY c.embedding <=> p_qvec LIMIT 50) y),
  exact AS (  -- the identifier in the chunk's own header (the article itself) first, then by ts_rank_cd
    SELECT z.chunk_id, row_number() OVER (ORDER BY z.own DESC, z.s DESC, z.chunk_id) AS r FROM (
      SELECT c.chunk_id, ts_rank_cd(c.tsv_exact, p_exact) AS s,
             to_tsvector('lgx.simple_ua'::regconfig, c.header) @@ p_exact AS own
        FROM lgx.chunk c
       WHERE p_exact IS NOT NULL AND c.searchable AND c.tsv_exact @@ p_exact
         AND lgx.chunk_window_ok(c.valid_from, c.valid_to, p_as_of, p_in_force_only)
         AND (p_doc_types IS NULL OR c.doc_type = ANY (p_doc_types))
         AND (p_act_ids IS NULL OR c.act_id = ANY (p_act_ids))
         AND (p_member_act_id IS NULL OR c.act_id = p_member_act_id OR c.unit_id IN (SELECT unit_id FROM mem))
       ORDER BY own DESC, s DESC, c.chunk_id LIMIT 20) z),
  cited AS (
    SELECT c.chunk_id, 1::bigint AS r FROM lgx.chunk c
     WHERE c.unit_id = ANY (p_cited_units)
       AND lgx.chunk_window_ok(c.valid_from, c.valid_to, p_as_of, p_in_force_only)
       AND (p_doc_types IS NULL OR c.doc_type = ANY (p_doc_types))
       AND (p_act_ids IS NULL OR c.act_id = ANY (p_act_ids))
       AND (p_member_act_id IS NULL OR c.act_id = p_member_act_id OR c.unit_id IN (SELECT unit_id FROM mem))),
  fused AS (
    SELECT u.chunk_id, sum(u.w / (60.0 + u.r)) AS s,
           min(u.r) FILTER (WHERE u.src = 'lex') AS lex_rank, min(u.r) FILTER (WHERE u.src = 'dense') AS dense_rank,
           bool_or(u.src = 'exact') AS exact_hit, bool_or(u.src = 'cited') AS cited_hit
      FROM (SELECT chunk_id, r, 1.0 AS w, 'lex' AS src FROM lex
            UNION ALL SELECT chunk_id, r, 1.0, 'dense' FROM dense
            UNION ALL SELECT chunk_id, r, 2.0, 'exact' FROM exact
            UNION ALL SELECT chunk_id, r, 10.0, 'cited' FROM cited) u
     GROUP BY u.chunk_id)
  SELECT f.chunk_id, f.s * c.rank_prior, f.lex_rank, f.dense_rank, f.exact_hit, f.cited_hit,
         l.s, l.cov::int, l.ph::int, coalesce(l.boosted, false)
    FROM fused f JOIN lgx.chunk c USING (chunk_id) LEFT JOIN lex l USING (chunk_id)
   ORDER BY 2 DESC, 1 LIMIT p_limit $$;
