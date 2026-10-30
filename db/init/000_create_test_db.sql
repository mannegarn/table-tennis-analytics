-- 000: create the dedicated integration-test database.
--
-- Runs once on first container init, in autocommit against POSTGRES_DB.
-- CREATE DATABASE cannot run inside a transaction block, hence \gexec rather
-- than a DO block. Guarded so a re-run is a no-op.
--
-- For an already-initialised container (init scripts do not re-run):
--   psql -U postgres -c "CREATE DATABASE tt_stats_test;"

SELECT 'CREATE DATABASE tt_stats_test'
WHERE NOT EXISTS (
    SELECT FROM pg_database WHERE datname = 'tt_stats_test'
)\gexec
