-- =====================================================================================================
-- Part 10: text search (Appendix B.2).
-- fr_legal: unaccent, then Snowball French with an UNACCENTED stopword list. With the stock list,
-- accented stopwords leak through after unaccent (verified in the prototype). french_unaccent.stop
-- (= stock french.stop, unaccented, deduplicated; db/tsearch/) is baked into the lgx-db image at
-- $SHAREDIR/tsearch_data/ and must also exist on every server that restores a dump.
-- =====================================================================================================
\set ON_ERROR_STOP on

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
