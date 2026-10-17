-- =====================================================================================================
-- Part 90: post-load (Appendix B.15). Run by `lgx load` after COPY (and harmless on an empty database).
-- Idempotent: safe to run again after re-loading chunks.
-- Deviations from Appendix B: CREATE INDEX IF NOT EXISTS (re-runnable), and an explicit error when
-- vectors exist without an lgx.embedding_model row (the sketch would hit format('%s', NULL)).
-- =====================================================================================================
\set ON_ERROR_STOP on

ANALYZE;

-- Conditional HNSW: only when the optional embedding stage produced vectors (default: no vectors).
-- halfvec HNSW supports up to 4,000 dims (lgx.embedding_model.dim is checked against it; Mistral Embed: 1024).
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM lgx.chunk WHERE embedding IS NOT NULL) THEN
    IF NOT EXISTS (SELECT 1 FROM lgx.embedding_model) THEN
      RAISE EXCEPTION 'lgx.chunk has vectors but lgx.embedding_model is empty';
    END IF;
    EXECUTE 'CREATE INDEX IF NOT EXISTS chunk_hnsw ON lgx.chunk USING hnsw (embedding halfvec_cosine_ops) '
         || 'WITH (m = 16, ef_construction = 128) WHERE searchable AND embedding IS NOT NULL';
  END IF;
END $$;
