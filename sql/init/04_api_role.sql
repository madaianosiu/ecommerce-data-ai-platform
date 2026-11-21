-- Read-only role for the data API (FastAPI): it can read ONLY the tables in schema core.
-- The password comes from the API_DB_PASSWORD environment variable (psql meta-command \getenv,
-- psql >= 15), so it never appears in this file. Idempotent: safe to run more than once.
--
-- Runs automatically on a new volume (docker-entrypoint-initdb.d). On an existing database:
--   dotenv -f .env run -- docker compose exec -T -e API_DB_PASSWORD db \
--     sh -c 'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -f /docker-entrypoint-initdb.d/04_api_role.sql'

\getenv api_password API_DB_PASSWORD
\if :{?api_password}
\else
    \echo 'WARNING: API_DB_PASSWORD is not set; role api_reader was NOT created.'
    \quit
\endif

SELECT 'CREATE ROLE api_reader'
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'api_reader')
\gexec

-- No administrative privileges; sessions are read-only by default and have a timeout.
ALTER ROLE api_reader WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION
    PASSWORD :'api_password';
ALTER ROLE api_reader SET default_transaction_read_only = on;
ALTER ROLE api_reader SET statement_timeout = '10s';

SELECT format('GRANT CONNECT ON DATABASE %I TO api_reader', current_database())
\gexec

-- Only core: USAGE on the schema + SELECT on its tables. Nothing on raw, staging or public.
REVOKE ALL ON SCHEMA raw, staging FROM api_reader;
REVOKE ALL ON ALL TABLES IN SCHEMA raw FROM api_reader;
REVOKE ALL ON ALL TABLES IN SCHEMA staging FROM api_reader;

GRANT USAGE ON SCHEMA core TO api_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA core TO api_reader;

-- dbt recreates the core tables on every `dbt run` (create + rename), and a new table does not
-- inherit the old GRANTs. Default privileges for tables created by the current role (the owner,
-- which runs dbt) automatically grant SELECT on the new tables too.
ALTER DEFAULT PRIVILEGES IN SCHEMA core GRANT SELECT ON TABLES TO api_reader;
