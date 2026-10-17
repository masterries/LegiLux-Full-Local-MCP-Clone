-- =====================================================================================================
-- Part 95: roles and grants (Appendix B.16). Re-run after every restore (dumps use --no-owner
-- --no-privileges). The role is cluster-level and created only once.
-- =====================================================================================================
\set ON_ERROR_STOP on

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
-- The build schema exists only in the build database (pg_dump --exclude-schema=build), so the REVOKE must not
-- fail on a restored release (deviation from Appendix B.16, found by the M1 restore test).
DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = 'build') THEN
    REVOKE ALL ON SCHEMA build FROM PUBLIC;
  END IF;
END $$;
GRANT USAGE ON SCHEMA lgx, lgx_meta TO lgx_ro;
GRANT SELECT ON ALL TABLES IN SCHEMA lgx, lgx_meta TO lgx_ro;
GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA lgx TO lgx_ro;
-- After restore + ANALYZE on the runtime host:  ALTER DATABASE legilux SET default_transaction_read_only = on;
