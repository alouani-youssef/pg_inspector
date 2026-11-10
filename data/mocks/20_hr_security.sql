-- =============================================================================
-- pg_inspector mocks 20: hr schema + roles  (security audit scenario)
--
-- Feeds: the audit_database_security prompt, sensitive-column detection through the
-- dictionary resource, row-level security, SECURITY DEFINER functions.
--
-- Every person, ID, IBAN, hash and token below is fake and deterministic. The role
-- passwords are throwaway values for this local container only.
--
-- Planted findings (expected severity in brackets):
--   [Critical] hr.employees (national_id, salary, iban, birth_date) readable by PUBLIC
--   [Critical] hr.reset_password(): SECURITY DEFINER, owned by a superuser, no pinned
--              search_path, EXECUTE granted to PUBLIC (the default)
--   [High]     role legacy_admin: LOGIN + CREATEROLE + CREATEDB + BYPASSRLS, no expiry,
--              and owns hr.app_users (a login role owning objects)
--   [High]     role etl_loader: REPLICATION + member of pg_read_server_files and
--              pg_execute_server_program
--   [High]     role reporting: ALL on every hr table WITH GRANT OPTION; analyst inherits it
--   [Medium]   CREATE on schema public granted to PUBLIC
--   [Medium]   default privileges: future hr tables readable by PUBLIC
--   [Medium]   hr.app_users stores password_hash and api_token columns
--   [Low]      role intern: LOGIN with an expired VALID UNTIL
--   [Info]     hr.salary_reviews has row-level security with a policy
--   [Info]     hr.employee_directory(): SECURITY DEFINER done right (pinned search_path)
--   [Info]     inspector_ro: the least-privilege role to run this server with
-- =============================================================================

\set ON_ERROR_STOP on

BEGIN;

CREATE SCHEMA hr;
COMMENT ON SCHEMA hr IS 'pg_inspector mocks: personnel and payroll data (PII) for the security audit';

-- -----------------------------------------------------------------------------
-- Tables
-- -----------------------------------------------------------------------------
CREATE TABLE hr.departments (
    id          integer PRIMARY KEY,
    name        text NOT NULL UNIQUE,
    cost_center text NOT NULL
);
COMMENT ON TABLE hr.departments IS 'Organisational units';

INSERT INTO hr.departments (id, name, cost_center) VALUES
    (1, 'Engineering', 'CC-100'),
    (2, 'Sales',       'CC-200'),
    (3, 'Finance',     'CC-300'),
    (4, 'People',      'CC-400');

CREATE TABLE hr.employees (
    id             integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    department_id  integer       NOT NULL REFERENCES hr.departments (id),
    manager_id     integer       REFERENCES hr.employees (id),
    first_name     text          NOT NULL,
    last_name      text          NOT NULL,
    work_email     text          NOT NULL UNIQUE,
    personal_phone text,
    birth_date     date          NOT NULL,
    national_id    text          NOT NULL UNIQUE,
    iban           text,
    salary         numeric(10, 2) NOT NULL CHECK (salary > 0),
    hired_at       date          NOT NULL,
    terminated_at  date,
    CONSTRAINT employees_terminated_after_hired CHECK (terminated_at IS NULL OR terminated_at >= hired_at)
);
COMMENT ON TABLE  hr.employees IS 'Employees, including personal and payroll data';
COMMENT ON COLUMN hr.employees.personal_phone IS 'Private phone number (PII)';
COMMENT ON COLUMN hr.employees.birth_date IS 'Date of birth (PII)';
COMMENT ON COLUMN hr.employees.national_id IS 'Government-issued ID number (PII)';
COMMENT ON COLUMN hr.employees.iban IS 'Bank account for payroll';
COMMENT ON COLUMN hr.employees.salary IS 'Gross yearly salary in EUR (confidential)';

CREATE INDEX employees_department_idx ON hr.employees (department_id);
CREATE INDEX employees_manager_idx    ON hr.employees (manager_id);

-- 40 employees. #1 is the CEO; #2..#4 report to #1; everyone else to 2 + (g % 3).
-- Every 8th has left the company.
INSERT INTO hr.employees (department_id, manager_id, first_name, last_name, work_email,
                          personal_phone, birth_date, national_id, iban, salary, hired_at, terminated_at)
SELECT
    1 + (g % 4),
    CASE WHEN g = 1 THEN NULL WHEN g <= 4 THEN 1 ELSE 2 + (g % 3) END,
    (ARRAY['Amine','Julia','Karim','Nora','Paul','Salma','Hugo','Lina'])[1 + (g % 8)],
    (ARRAY['Idrissi','Moreau','Brown','Weber','Ruiz'])[1 + (g % 5)],
    format('employee%s@corp.example', g),
    CASE WHEN g % 6 = 0 THEN NULL ELSE format('+212 6%s', lpad((g * 7919)::text, 8, '0')) END,
    date '1970-01-01' + (g * 331),
    format('MOCK-ID-%s', lpad((g * 104729)::text, 9, '0')),
    CASE WHEN g % 10 = 0 THEN NULL ELSE format('MA00 MOCK %s', lpad(g::text, 16, '0')) END,
    (30000 + g * 1250)::numeric(10, 2),
    date '2015-01-05' + (g * 61),
    CASE WHEN g % 8 = 0 THEN date '2015-01-05' + (g * 61) + 400 END
FROM generate_series(1, 40) AS g;

CREATE TABLE hr.app_users (
    id            integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    employee_id   integer NOT NULL UNIQUE REFERENCES hr.employees (id) ON DELETE CASCADE,
    username      text    NOT NULL UNIQUE,
    password_hash text    NOT NULL,
    api_token     text,
    is_admin      boolean NOT NULL DEFAULT false,
    last_login_at timestamptz
);
COMMENT ON TABLE  hr.app_users IS 'Back-office logins for employees';
COMMENT ON COLUMN hr.app_users.password_hash IS 'bcrypt hash of the login password (secret)';
COMMENT ON COLUMN hr.app_users.api_token IS 'Personal API token (secret)';

-- 25 logins (employees 1..25); employees 1..3 are admins; every 5th has an API token.
INSERT INTO hr.app_users (employee_id, username, password_hash, api_token, is_admin, last_login_at)
SELECT
    g,
    format('emp%s', g),
    '$2b$12$MOCK' || md5('password-' || g),
    CASE WHEN g % 5 = 0 THEN 'tok_mock_' || md5('token-' || g) END,
    g <= 3,
    timestamptz '2026-01-01 08:00:00+00' + (g * interval '3 days')
FROM generate_series(1, 25) AS g;

CREATE TABLE hr.salary_reviews (
    id          integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    employee_id integer       NOT NULL REFERENCES hr.employees (id),
    reviewer_id integer       NOT NULL REFERENCES hr.employees (id),
    reviewed_on date          NOT NULL,
    old_salary  numeric(10, 2) NOT NULL,
    new_salary  numeric(10, 2) NOT NULL
);
COMMENT ON TABLE hr.salary_reviews IS 'Salary changes; rows visible only to their reviewer (RLS)';

-- One review for each of employees 5..40, reviewed by their manager.
INSERT INTO hr.salary_reviews (employee_id, reviewer_id, reviewed_on, old_salary, new_salary)
SELECT e.id, e.manager_id, date '2026-03-01' + e.id, e.salary - 1000, e.salary
FROM hr.employees e
WHERE e.id >= 5;

ALTER TABLE hr.salary_reviews ENABLE ROW LEVEL SECURITY;
CREATE POLICY salary_reviews_by_reviewer ON hr.salary_reviews
    FOR SELECT
    USING (reviewer_id = nullif(current_setting('app.employee_id', true), '')::integer);

-- -----------------------------------------------------------------------------
-- Functions
-- -----------------------------------------------------------------------------
-- BAD: runs as its (superuser) owner, resolves names through the caller's search_path,
-- and is executable by PUBLIC.
CREATE FUNCTION hr.reset_password(p_username text, p_password_hash text) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER AS $$
BEGIN
    UPDATE hr.app_users SET password_hash = p_password_hash WHERE username = p_username;
    RETURN FOUND;
END;
$$;
COMMENT ON FUNCTION hr.reset_password(text, text) IS 'Self-service password reset (legacy)';

-- GOOD: SECURITY DEFINER with a pinned search_path, exposing non-sensitive columns only.
CREATE FUNCTION hr.employee_directory()
RETURNS TABLE (id integer, full_name text, department text, work_email text)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, hr AS $$
    SELECT e.id, e.first_name || ' ' || e.last_name, d.name, e.work_email
    FROM hr.employees e
    JOIN hr.departments d ON d.id = e.department_id
    WHERE e.terminated_at IS NULL;
$$;
REVOKE ALL ON FUNCTION hr.employee_directory() FROM PUBLIC;
COMMENT ON FUNCTION hr.employee_directory() IS 'Company directory without personal data';

COMMIT;

-- -----------------------------------------------------------------------------
-- Roles (cluster-wide). Throwaway passwords: local mock container only.
-- -----------------------------------------------------------------------------

-- The role this server should run as: reads everything, writes nothing.
CREATE ROLE inspector_ro LOGIN PASSWORD 'mock-inspector-ro' CONNECTION LIMIT 10;
GRANT pg_read_all_data, pg_read_all_stats TO inspector_ro;
COMMENT ON ROLE inspector_ro IS 'Least-privilege role for pg_inspector (read-only)';

-- Application role: CRUD on crm (granted in 30_crm_quality.sql) and nothing in hr.
CREATE ROLE app_rw LOGIN PASSWORD 'mock-app-rw';

-- Over-privileged reporting group and a member that inherits it.
CREATE ROLE reporting NOLOGIN;
GRANT USAGE ON SCHEMA hr TO reporting;
GRANT ALL ON ALL TABLES IN SCHEMA hr TO reporting WITH GRANT OPTION;
CREATE ROLE analyst LOGIN PASSWORD 'mock-analyst' IN ROLE reporting;

-- Legacy administrator that should not exist any more.
CREATE ROLE legacy_admin LOGIN CREATEROLE CREATEDB BYPASSRLS PASSWORD 'mock-legacy-admin';
ALTER TABLE hr.app_users OWNER TO legacy_admin;

-- ETL account with server-file and program access.
CREATE ROLE etl_loader LOGIN REPLICATION PASSWORD 'mock-etl-loader';
GRANT pg_read_server_files, pg_execute_server_program TO etl_loader;

-- Expired account that was never dropped.
CREATE ROLE intern LOGIN PASSWORD 'mock-intern' VALID UNTIL '2025-12-31 23:59:59+00';

-- Public access mistakes.
GRANT USAGE ON SCHEMA hr TO PUBLIC;
GRANT SELECT ON hr.employees TO PUBLIC;
GRANT CREATE ON SCHEMA public TO PUBLIC;
ALTER DEFAULT PRIVILEGES IN SCHEMA hr GRANT SELECT ON TABLES TO PUBLIC;

ANALYZE hr.departments, hr.employees, hr.app_users, hr.salary_reviews;
