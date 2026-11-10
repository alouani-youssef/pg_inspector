-- =============================================================================
-- pg_inspector mocks 10: extensions
--
-- Installed in their own schema so they do not add objects to `public`, whose contents
-- the tests assert exactly. `pg_stat_statements` needs `shared_preload_libraries`, set by
-- the `command` of the db service in docker-compose.yaml.
--
-- Feeds: the overview resource (extensions), investigate_database_performance
-- (pg_stat_statements ordered by total_exec_time), trigram index in crm.
-- =============================================================================

\set ON_ERROR_STOP on

CREATE SCHEMA extensions;
COMMENT ON SCHEMA extensions IS 'pg_inspector mocks: extensions, kept out of public';

CREATE EXTENSION pg_stat_statements SCHEMA extensions;
CREATE EXTENSION pg_trgm            SCHEMA extensions;
CREATE EXTENSION citext             SCHEMA extensions;

-- Let unqualified names (pg_stat_statements, citext, gin_trgm_ops) resolve in new sessions.
ALTER DATABASE :"DBNAME" SET search_path = "$user", public, extensions;
SET search_path = "$user", public, extensions;
