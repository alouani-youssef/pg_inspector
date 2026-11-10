-- =============================================================================
-- pg_inspector mocks 30: crm schema  (data quality scenario)
--
-- A CRM imported from a legacy system: few constraints, so dirty data got in. Every
-- defect is planted DETERMINISTICALLY (modulo rules on the row number) so tests can
-- assert exact counts. Expected counts are listed at the bottom of this file.
--
-- Feeds: the investigate_data_quality prompt (NULLs, duplicates, orphans, unexpected
-- values, inconsistent timestamps, suspicious numbers), explore_database (reference
-- columns with no foreign key, a table without a primary key), generate_sql_query.
-- =============================================================================

\set ON_ERROR_STOP on

BEGIN;

CREATE SCHEMA crm;
COMMENT ON SCHEMA crm IS 'pg_inspector mocks: CRM imported from a legacy system (dirty data)';

-- -----------------------------------------------------------------------------
-- crm.accounts
-- -----------------------------------------------------------------------------
CREATE TABLE crm.accounts (
    id           bigint PRIMARY KEY,
    name         text        NOT NULL,
    domain       text,
    country_code char(2)     NOT NULL,
    industry     text        NOT NULL,
    created_at   timestamptz NOT NULL,
    updated_at   timestamptz NOT NULL
);
COMMENT ON TABLE  crm.accounts IS 'Customer companies';
COMMENT ON COLUMN crm.accounts.name IS 'Company name, should be unique (case-insensitive)';
COMMENT ON COLUMN crm.accounts.country_code IS 'ISO-3166 alpha-2, upper case (see public.countries)';
COMMENT ON COLUMN crm.accounts.industry IS 'One of: software, retail, finance, health';

-- 60 accounts.
INSERT INTO crm.accounts (id, name, domain, country_code, industry, created_at, updated_at)
SELECT
    g,
    CASE WHEN g % 11 = 0 THEN upper(format('Account %s', g - 1)) || ' '   -- near-duplicate name
         ELSE format('Account %s', g) END,
    CASE WHEN g % 12 = 0 THEN NULL ELSE format('account%s.example', g) END,
    CASE WHEN g % 20 = 0 THEN 'XX'                                         -- not a country
         WHEN g % 15 = 0 THEN 'ma'                                         -- lower case
         ELSE (ARRAY['MA','FR','US','DE','ES'])[1 + (g % 5)] END,
    CASE WHEN g % 13 = 0 THEN ''                                           -- empty string
         ELSE (ARRAY['software','retail','finance','health'])[1 + (g % 4)] END,
    timestamptz '2024-01-01 09:00:00+00' + (g * interval '1 day'),
    CASE WHEN g % 14 = 0                                                   -- updated before created
         THEN timestamptz '2024-01-01 09:00:00+00' + (g * interval '1 day') - interval '5 days'
         ELSE timestamptz '2024-01-01 09:00:00+00' + (g * interval '1 day') + interval '30 days' END
FROM generate_series(1, 60) AS g;

-- -----------------------------------------------------------------------------
-- crm.contacts  (account_id has NO foreign key)
-- -----------------------------------------------------------------------------
CREATE TABLE crm.contacts (
    id         bigint PRIMARY KEY,
    account_id bigint      NOT NULL,
    first_name text        NOT NULL,
    last_name  text        NOT NULL,
    email      text,
    phone      text,
    created_at timestamptz NOT NULL,
    deleted_at timestamptz
);
COMMENT ON TABLE  crm.contacts IS 'People at customer companies; soft-deleted through deleted_at';
COMMENT ON COLUMN crm.contacts.account_id IS 'The contact''s company (crm.accounts.id)';
COMMENT ON COLUMN crm.contacts.email IS 'Business email, should be unique (case-insensitive)';
COMMENT ON COLUMN crm.contacts.phone IS 'E.164 phone number, e.g. +212600000000';

CREATE INDEX contacts_account_idx ON crm.contacts (account_id);

-- 500 contacts.
INSERT INTO crm.contacts (id, account_id, first_name, last_name, email, phone, created_at, deleted_at)
SELECT
    g,
    CASE WHEN g % 25 = 0 THEN 1000 + g ELSE 1 + (g % 60) END,              -- orphans
    CASE WHEN g % 100 = 7 THEN '   '                                       -- blank
         ELSE (ARRAY['Rania','Tom','Aya','Lucas','Hiba','Max','Zineb','Leo'])[1 + (g % 8)] END,
    (ARRAY['Tazi','Petit','Clark','Fischer','Navarro','Chraibi'])[1 + (g % 6)],
    CASE WHEN g % 20 = 0 THEN NULL                                         -- missing
         WHEN g % 50 = 2 THEN upper(format('contact%s@example.com', g - 1)) -- case duplicate
         WHEN g % 40 = 7 THEN format('contact%s_at_example.com', g)        -- malformed
         ELSE format('contact%s@example.com', g) END,
    CASE WHEN g % 9 = 0 THEN NULL
         WHEN g % 3 = 0 THEN format('+2126%s', lpad(g::text, 8, '0'))      -- E.164
         WHEN g % 3 = 1 THEN format('06-%s', lpad(g::text, 8, '0'))        -- local
         ELSE format('(06) %s', lpad(g::text, 8, '0')) END,                -- formatted
    CASE WHEN g % 125 = 0 THEN timestamptz '2031-01-01 00:00:00+00' + (g * interval '1 hour')  -- future
         ELSE timestamptz '2025-01-01 00:00:00+00' + (g * interval '12 hours') END,
    CASE WHEN g % 30 = 0 THEN timestamptz '2025-01-01 00:00:00+00' + (g * interval '12 hours') + interval '10 days' END
FROM generate_series(1, 500) AS g;

-- -----------------------------------------------------------------------------
-- crm.opportunities  (account_id / contact_id have NO foreign key, stage has NO check)
-- -----------------------------------------------------------------------------
CREATE TABLE crm.opportunities (
    id          bigint PRIMARY KEY,
    account_id  bigint         NOT NULL,
    contact_id  bigint,
    stage       text           NOT NULL,
    amount      numeric(12, 2) NOT NULL,
    currency    char(3),
    probability integer,
    created_at  timestamptz    NOT NULL,
    closed_at   timestamptz
);
COMMENT ON TABLE  crm.opportunities IS 'Sales pipeline';
COMMENT ON COLUMN crm.opportunities.stage IS 'One of: prospecting, qualification, proposal, won, lost';
COMMENT ON COLUMN crm.opportunities.amount IS 'Expected deal value, in currency';
COMMENT ON COLUMN crm.opportunities.currency IS 'ISO-4217 code, upper case';
COMMENT ON COLUMN crm.opportunities.probability IS 'Win probability in percent (0-100)';
COMMENT ON COLUMN crm.opportunities.closed_at IS 'Set when stage becomes won or lost';

CREATE INDEX opportunities_account_idx ON crm.opportunities (account_id);

-- 1000 opportunities.
INSERT INTO crm.opportunities (id, account_id, contact_id, stage, amount, currency, probability, created_at, closed_at)
SELECT
    g,
    CASE WHEN g % 111 = 0 THEN 9000 + g ELSE 1 + (g % 60) END,              -- orphans
    CASE WHEN g % 7 = 0 THEN NULL ELSE 1 + (g % 500) END,
    CASE WHEN g % 40 = 0  THEN 'Won'                                       -- off-vocabulary
         WHEN g % 40 = 20 THEN 'closed-won'                                -- off-vocabulary
         ELSE (ARRAY['prospecting','qualification','proposal','won','lost'])[1 + (g % 5)] END,
    CASE WHEN g % 100 = 0  THEN -(1000 + (g * 37) % 5000)                  -- negative
         WHEN g % 100 = 50 THEN 0                                          -- zero
         WHEN g % 250 = 3  THEN 9999999.00                                 -- outlier
         ELSE 1000 + (g * 37) % 5000 END,
    CASE WHEN g % 33 = 0 THEN NULL
         WHEN g % 17 = 0 THEN 'eur'                                        -- lower case
         ELSE 'EUR' END,
    CASE WHEN g % 200 = 5 THEN 150 ELSE (g * 13) % 101 END,               -- > 100
    timestamptz '2025-02-01 00:00:00+00' + (g * interval '8 hours'),
    CASE WHEN g % 90 = 0                                                   -- closed before created
         THEN timestamptz '2025-02-01 00:00:00+00' + (g * interval '8 hours') - interval '3 days'
         WHEN g % 5 IN (3, 4)
         THEN timestamptz '2025-02-01 00:00:00+00' + (g * interval '8 hours') + interval '20 days' END
FROM generate_series(1, 1000) AS g;

-- -----------------------------------------------------------------------------
-- crm.leads  (NO primary key, exact duplicate rows, trigram search index)
-- -----------------------------------------------------------------------------
CREATE TABLE crm.leads (
    email       text,
    company     text,
    source      text,
    score       integer,
    captured_at timestamptz
);
COMMENT ON TABLE crm.leads IS 'Raw web-form leads, loaded nightly by a CSV import';

CREATE INDEX leads_company_trgm ON crm.leads USING gin (company extensions.gin_trgm_ops);

-- 200 leads, then the first 15 imported a second time (exact duplicates) => 215 rows.
INSERT INTO crm.leads (email, company, source, score, captured_at)
SELECT
    format('lead%s@prospect.example', g),
    format('Prospect %s %s', (ARRAY['Labs','Group','Systems','Studio'])[1 + (g % 4)], g),
    (ARRAY['webinar','website','referral','ads'])[1 + (g % 4)],
    (g * 17) % 100,
    timestamptz '2026-01-01 00:00:00+00' + (g * interval '2 hours')
FROM generate_series(1, 200) AS g;

INSERT INTO crm.leads
SELECT * FROM crm.leads
WHERE email IN (SELECT format('lead%s@prospect.example', g) FROM generate_series(1, 15) AS g);

-- -----------------------------------------------------------------------------
-- crm.pipeline_by_stage  (view)
-- -----------------------------------------------------------------------------
CREATE VIEW crm.pipeline_by_stage AS
SELECT o.stage, count(*) AS opportunities, sum(o.amount) AS total_amount
FROM crm.opportunities o
GROUP BY o.stage;
COMMENT ON VIEW crm.pipeline_by_stage IS 'Opportunity count and value per stage';

COMMIT;

GRANT USAGE ON SCHEMA crm TO app_rw;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA crm TO app_rw;

ANALYZE crm.accounts, crm.contacts, crm.opportunities, crm.leads;

-- =============================================================================
-- Expected values for tests (tests/mocks/test_scenarios.py)
-- -----------------------------------------------------------------------------
--   crm.accounts      : 60 rows
--       near-duplicate names (lower(trim(name)))       : 5 pairs (ids 11,22,33,44,55)
--       domain IS NULL                                 : 5
--       country_code 'XX'                              : 3     lower case: 3
--       industry = ''                                  : 4
--       updated_at < created_at                        : 4
--   crm.contacts      : 500 rows
--       account_id with no crm.accounts row            : 20
--       email IS NULL                                  : 25
--       duplicate emails (lower(email))                : 10 pairs
--       malformed email (no '@')                       : 13
--       blank first_name (trim = '')                   : 5
--       created_at in the future                       : 4
--       phone IS NULL                                  : 55    soft-deleted: 16
--   crm.opportunities : 1000 rows
--       account_id with no crm.accounts row            : 9
--       stage outside the documented values            : 50   ('Won' 25, 'closed-won' 25)
--       amount < 0                                     : 10    = 0: 10    = 9999999: 4
--       currency IS NULL                               : 30    lower case: 57
--       probability > 100                              : 5
--       closed_at < created_at                         : 11
--   crm.leads         : 215 rows, no primary key, 15 exact duplicate rows
-- =============================================================================
