-- Rol read-only pentru data API (FastAPI): poate citi DOAR tabelele din schema core.
-- Parola vine din variabila de mediu API_DB_PASSWORD (meta-comanda psql \getenv, psql >= 15),
-- deci nu apare în acest fișier. Idempotent: poate fi rulat de mai multe ori.
--
-- Pe un volum nou rulează automat (docker-entrypoint-initdb.d). Pe o bază existentă:
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

-- Fără privilegii administrative; sesiunile sunt read-only implicit și au timeout.
ALTER ROLE api_reader WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION
    PASSWORD :'api_password';
ALTER ROLE api_reader SET default_transaction_read_only = on;
ALTER ROLE api_reader SET statement_timeout = '10s';

SELECT format('GRANT CONNECT ON DATABASE %I TO api_reader', current_database())
\gexec

-- Doar core: USAGE pe schemă + SELECT pe tabele. Nimic pe raw, staging sau public.
REVOKE ALL ON SCHEMA raw, staging FROM api_reader;
REVOKE ALL ON ALL TABLES IN SCHEMA raw FROM api_reader;
REVOKE ALL ON ALL TABLES IN SCHEMA staging FROM api_reader;

GRANT USAGE ON SCHEMA core TO api_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA core TO api_reader;

-- dbt recreează tabelele core la fiecare `dbt run` (create + rename), iar un tabel nou nu
-- moștenește GRANT-urile vechi. Privilegiile implicite pentru tabelele create de rolul curent
-- (proprietarul, cel care rulează dbt) le acordă automat SELECT și pe tabelele noi.
ALTER DEFAULT PRIVILEGES IN SCHEMA core GRANT SELECT ON TABLES TO api_reader;
