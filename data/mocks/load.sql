-- Loads the scenario mocks, in order, after data/db_init.sql.
--
-- Mounted by docker compose as /docker-entrypoint-initdb.d/02_mocks.sql (with this directory
-- at /mocks), so it runs only on the FIRST start of an empty volume. Re-seed with
-- `uv run test --reseed`. Add a new scenario file here to load it.

\set ON_ERROR_STOP on

\i /mocks/10_extensions.sql
\i /mocks/20_hr_security.sql
\i /mocks/30_crm_quality.sql
\i /mocks/40_analytics_performance.sql
