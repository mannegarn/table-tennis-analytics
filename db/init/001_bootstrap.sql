-- bootstrap local postgres database
--
-- Runs once when container starts as superuser inside POSTGRES_DB.
-- Files here execute in filename order against the database named by
-- POSTGRES_DB, so this file must stay environment-agnostic.
--

DO $$
BEGIN
    EXECUTE format(
        'ALTER DATABASE %I SET timezone TO ''UTC''',
        current_database()
    );
END
$$;

--  add text similiarty extension if needed for fuzzy matching / name reconciliation later?
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- Ensure the schemas exist before migrations run.
CREATE SCHEMA IF NOT EXISTS public;
CREATE SCHEMA IF NOT EXISTS ingest;
CREATE SCHEMA IF NOT EXISTS analytics;
