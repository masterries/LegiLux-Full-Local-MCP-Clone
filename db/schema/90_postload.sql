-- =====================================================================================================
-- Part 90: post-load (Appendix B.15). Run by `lgx load` after COPY (and harmless on an empty database).
-- Idempotent: safe to run again after re-loading chunks.
-- Deviations from Appendix B: CREATE INDEX IF NOT EXISTS (re-runnable), and an explicit error when
-- vectors exist without an lgx.embedding_model row (the sketch would hit format('%s', NULL)).
-- =====================================================================================================
\set ON_ERROR_STOP on

ANALYZE;

-- Conditional HNSW: only when the optional embedding stage produced vectors (default: no vectors).
-- halfvec HNSW supports up to 4,000 dims (Mistral Embed 1024 fits). For a >4,000-dim model (option B,
-- e5-mistral-7b-instruct, 4096-d) index binary_quantize(embedding)::bit(N) with bit_hamming_ops and
-- re-rank the candidates with the full halfvec (pgvector README pattern).
DO $$
DECLARE v_dim int;
BEGIN
  IF EXISTS (SELECT 1 FROM lgx.chunk WHERE embedding IS NOT NULL) THEN
    SELECT dim INTO v_dim FROM lgx.embedding_model ORDER BY embedding_set_id DESC LIMIT 1;
    IF v_dim IS NULL THEN
      RAISE EXCEPTION 'lgx.chunk has vectors but lgx.embedding_model is empty';
    ELSIF v_dim <= 4000 THEN
      EXECUTE 'CREATE INDEX IF NOT EXISTS chunk_hnsw ON lgx.chunk USING hnsw (embedding halfvec_cosine_ops) '
           || 'WITH (m = 16, ef_construction = 128) WHERE searchable AND embedding IS NOT NULL';
    ELSE
      EXECUTE format('CREATE INDEX IF NOT EXISTS chunk_hnsw_bq ON lgx.chunk USING hnsw '
           || '((binary_quantize(embedding)::bit(%s)) bit_hamming_ops) WHERE searchable AND embedding IS NOT NULL', v_dim);
    END IF;
  END IF;
END $$;
