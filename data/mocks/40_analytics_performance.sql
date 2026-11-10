-- =============================================================================
-- pg_inspector mocks 40: analytics schema  (performance scenario)
--
-- Big enough (~200k events) that plans and statistics mean something, with planted
-- problems for the investigate_database_performance prompt and the health resource:
--
--   * analytics.events.session_id is a foreign key with NO usable index: its only index
--     (events_session_id_key) is INVALID, left by a failed CREATE INDEX CONCURRENTLY.
--     Filtering or joining on session_id does a sequential scan of 200k rows.
--   * duplicate indexes:   events_occurred_at_idx == events_occurred_at_dup_idx
--   * overlapping indexes: events_type_idx is a prefix of events_type_occurred_idx
--   * never-scanned index: events_payload_gin
--   * dead tuples:         analytics.page_views has autovacuum disabled and 60% of its rows
--                          deleted (30k dead / 20k live), so health warns it needs VACUUM
--   * no primary key, everything typed as text: analytics.raw_imports
--   * declarative partitioning: analytics.daily_metrics (3 monthly partitions + default)
--   * pg_stat_statements history: the slow session_id lookup is run a few times below
--
-- Expected values are listed at the bottom of this file.
-- =============================================================================

\set ON_ERROR_STOP on

BEGIN;

CREATE SCHEMA analytics;
COMMENT ON SCHEMA analytics IS 'pg_inspector mocks: clickstream analytics (performance scenario)';

-- -----------------------------------------------------------------------------
-- analytics.sessions
-- -----------------------------------------------------------------------------
CREATE TABLE analytics.sessions (
    id          bigint PRIMARY KEY,
    customer_id bigint,
    started_at  timestamptz NOT NULL,
    device      text        NOT NULL,
    user_agent  text        NOT NULL
);
COMMENT ON TABLE  analytics.sessions IS 'Website visits';
COMMENT ON COLUMN analytics.sessions.customer_id IS 'public.customers.id when the visitor was logged in';

-- 20 000 sessions; every 3rd is anonymous.
INSERT INTO analytics.sessions (id, customer_id, started_at, device, user_agent)
SELECT
    g,
    CASE WHEN g % 3 = 0 THEN NULL ELSE 1 + (g % 200) END,
    timestamptz '2026-01-01 00:00:00+00' + (g * interval '3 minutes'),
    (ARRAY['desktop','mobile','tablet'])[1 + (g % 3)],
    format('MockBrowser/%s.0', 1 + (g % 12))
FROM generate_series(1, 20000) AS g;

-- -----------------------------------------------------------------------------
-- analytics.events  (FK without a usable index)
-- -----------------------------------------------------------------------------
CREATE TABLE analytics.events (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    session_id  bigint      NOT NULL REFERENCES analytics.sessions (id) ON DELETE CASCADE,
    event_type  text        NOT NULL,
    occurred_at timestamptz NOT NULL,
    payload     jsonb       NOT NULL DEFAULT '{}'::jsonb
);
COMMENT ON TABLE analytics.events IS 'Clickstream events, 10 per session';

-- 200 000 events: 10 per session.
INSERT INTO analytics.events (session_id, event_type, occurred_at, payload)
SELECT
    1 + ((g - 1) / 10),
    (ARRAY['page_view','page_view','page_view','click','click','search','add_to_cart','checkout'])[1 + (g % 8)],
    timestamptz '2026-01-01 00:00:00+00' + (((g - 1) / 10) * interval '3 minutes') + ((g % 10) * interval '15 seconds'),
    jsonb_build_object('path', format('/p/%s', g % 500), 'ms', (g * 7) % 900)
FROM generate_series(1, 200000) AS g;

CREATE INDEX events_occurred_at_idx     ON analytics.events (occurred_at);
CREATE INDEX events_occurred_at_dup_idx ON analytics.events (occurred_at);              -- duplicate
CREATE INDEX events_type_idx            ON analytics.events (event_type);               -- prefix of the next
CREATE INDEX events_type_occurred_idx   ON analytics.events (event_type, occurred_at);
CREATE INDEX events_payload_gin         ON analytics.events USING gin (payload);        -- never used

-- -----------------------------------------------------------------------------
-- analytics.page_views  (dead tuples, autovacuum disabled)
-- -----------------------------------------------------------------------------
CREATE TABLE analytics.page_views (
    id        bigint PRIMARY KEY,
    path      text        NOT NULL,
    viewed_at timestamptz NOT NULL
) WITH (autovacuum_enabled = false);
COMMENT ON TABLE analytics.page_views IS 'Rolled-up page views; a purge job deletes old rows (autovacuum is off: bloat)';

INSERT INTO analytics.page_views (id, path, viewed_at)
SELECT g, format('/p/%s', g % 500), timestamptz '2026-01-01 00:00:00+00' + (g * interval '1 minute')
FROM generate_series(1, 50000) AS g;

-- -----------------------------------------------------------------------------
-- analytics.raw_imports  (no primary key, no indexes, everything text)
-- -----------------------------------------------------------------------------
CREATE TABLE analytics.raw_imports (
    file_name   text,
    line_no     text,
    payload     text,
    imported_at text
);
COMMENT ON TABLE analytics.raw_imports IS 'Staging area for partner CSV files';

INSERT INTO analytics.raw_imports (file_name, line_no, payload, imported_at)
SELECT
    format('partner_%s.csv', 1 + (g % 3)),
    g::text,
    format('{"sku": "SKU-%s", "qty": %s}', lpad((1 + g % 50)::text, 4, '0'), 1 + g % 9),
    to_char(timestamptz '2026-02-01 00:00:00+00' + (g * interval '1 minute'), 'DD/MM/YYYY HH24:MI')
FROM generate_series(1, 1000) AS g;

-- -----------------------------------------------------------------------------
-- analytics.daily_metrics  (range-partitioned by day)
-- -----------------------------------------------------------------------------
CREATE TABLE analytics.daily_metrics (
    day      date    NOT NULL,
    metric   text    NOT NULL,
    value    numeric NOT NULL,
    PRIMARY KEY (day, metric)
) PARTITION BY RANGE (day);
COMMENT ON TABLE analytics.daily_metrics IS 'Daily KPIs, one partition per month';

CREATE TABLE analytics.daily_metrics_2026_01 PARTITION OF analytics.daily_metrics
    FOR VALUES FROM ('2026-01-01') TO ('2026-02-01');
CREATE TABLE analytics.daily_metrics_2026_02 PARTITION OF analytics.daily_metrics
    FOR VALUES FROM ('2026-02-01') TO ('2026-03-01');
CREATE TABLE analytics.daily_metrics_2026_03 PARTITION OF analytics.daily_metrics
    FOR VALUES FROM ('2026-03-01') TO ('2026-04-01');
CREATE TABLE analytics.daily_metrics_default PARTITION OF analytics.daily_metrics DEFAULT;

-- 100 days (2026-01-01 .. 2026-04-10) x 3 metrics = 300 rows; the last 10 days land in DEFAULT.
INSERT INTO analytics.daily_metrics (day, metric, value)
SELECT date '2026-01-01' + d, m, (d + 1) * (CASE m WHEN 'sessions' THEN 200 WHEN 'orders' THEN 7 ELSE 350 END)
FROM generate_series(0, 99) AS d
CROSS JOIN unnest(ARRAY['sessions', 'orders', 'revenue']) AS m;

-- -----------------------------------------------------------------------------
-- analytics.session_summary  (materialized view)
-- -----------------------------------------------------------------------------
CREATE MATERIALIZED VIEW analytics.session_summary AS
SELECT s.device, count(DISTINCT s.id) AS sessions, count(e.id) AS events
FROM analytics.sessions s
JOIN analytics.events e ON e.session_id = s.id
GROUP BY s.device;
COMMENT ON MATERIALIZED VIEW analytics.session_summary IS 'Sessions and events per device';

COMMIT;

-- -----------------------------------------------------------------------------
-- Planted problems that cannot run inside a transaction block
-- -----------------------------------------------------------------------------

-- The purge job: 30 000 of 50 000 page views deleted, never vacuumed.
DELETE FROM analytics.page_views WHERE id % 5 IN (0, 1, 2);
-- Publish the DELETE's counters now, or the ANALYZE below is overwritten by them (live = 0).
SELECT pg_stat_force_next_flush();

-- A unique index built CONCURRENTLY over duplicate values fails and is left INVALID.
-- The ERROR printed here is expected.
\set ON_ERROR_STOP off
CREATE UNIQUE INDEX CONCURRENTLY events_session_id_key ON analytics.events (session_id);
\set ON_ERROR_STOP on
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_index WHERE indexrelid = 'analytics.events_session_id_key'::regclass
                   AND NOT indisvalid) THEN
        RAISE EXCEPTION 'expected analytics.events_session_id_key to be left invalid';
    END IF;
END;
$$;

GRANT USAGE ON SCHEMA analytics TO reporting;
GRANT SELECT ON ALL TABLES IN SCHEMA analytics TO reporting;

ANALYZE analytics.sessions, analytics.events, analytics.page_views, analytics.raw_imports,
        analytics.daily_metrics, analytics.session_summary;

-- Workload history for pg_stat_statements / pg_stat_user_tables: the slow lookup by
-- session_id (sequential scan) and a cheap lookup by primary key.
SELECT count(*) FROM analytics.events WHERE session_id = 4242;
SELECT count(*) FROM analytics.events WHERE session_id = 1717;
SELECT count(*) FROM analytics.events WHERE session_id = 9001;
SELECT e.event_type, count(*) FROM analytics.events e JOIN analytics.sessions s ON s.id = e.session_id
WHERE s.customer_id = 42 GROUP BY e.event_type;
SELECT * FROM analytics.sessions WHERE id = 42;

-- =============================================================================
-- Expected values for tests (tests/mocks/test_scenarios.py)
-- -----------------------------------------------------------------------------
--   analytics.sessions       : 20 000
--   analytics.events         : 200 000   (10 per session)
--   analytics.page_views     : 20 000 live, 30 000 dead (autovacuum off)
--   analytics.raw_imports    : 1 000, no primary key, no indexes
--   analytics.daily_metrics  : 300 (partitioned: 2026_01, 2026_02, 2026_03, default; 30 in default)
--   analytics.session_summary: 3 rows (desktop, mobile, tablet)
--   invalid index            : analytics.events_session_id_key
--   duplicate indexes        : events_occurred_at_idx, events_occurred_at_dup_idx
-- =============================================================================
