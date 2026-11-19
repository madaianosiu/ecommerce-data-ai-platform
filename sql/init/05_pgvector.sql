-- Embedding-uri pentru textul recenziilor (Etapa 7), folosite pentru căutare semantică / RAG.
-- Tabelul NU e gestionat de dbt: îl umple src/rag/embed_reviews.py, iar dbt nu atinge
-- tabelele din core pe care nu le definește. Idempotent: poate fi rulat de mai multe ori.
--
-- Pe un volum nou rulează automat (docker-entrypoint-initdb.d, după 04_api_role.sql).
-- Pe o bază existentă:
--   docker compose exec -T db sh -c \
--     'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -f /docker-entrypoint-initdb.d/05_pgvector.sql'

CREATE EXTENSION IF NOT EXISTS vector;

-- Un rând per (review_id, model): același review_id apare pe mai multe comenzi în
-- fact_reviews, dar mereu cu același text, deci se calculează un singur embedding.
-- vector(384) = dimensiunea modelului intfloat/multilingual-e5-small; un model cu altă
-- dimensiune are nevoie de altă coloană sau de alt tabel.
CREATE TABLE IF NOT EXISTS core.review_embeddings (
    review_id    text         NOT NULL,
    comment_text text         NOT NULL,
    review_score smallint     NOT NULL,
    embedding    vector(384)  NOT NULL,
    model_name   text         NOT NULL,
    created_at   timestamptz  NOT NULL DEFAULT now(),
    PRIMARY KEY (review_id, model_name)
);

-- Index aproximativ pentru căutarea după similaritate cosinus (operatorul <=>).
CREATE INDEX IF NOT EXISTS review_embeddings_embedding_hnsw
    ON core.review_embeddings USING hnsw (embedding vector_cosine_ops);

-- API-ul citește doar din core prin rolul read-only api_reader (04_api_role.sql).
-- Privilegiile implicite din 04 acoperă deja tabelele noi create de același rol,
-- dar GRANT-ul explicit face fișierul corect și dacă tabelul a fost creat de alt rol.
DO $$
BEGIN
    IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'api_reader') THEN
        GRANT SELECT ON core.review_embeddings TO api_reader;
    ELSE
        RAISE WARNING 'role api_reader does not exist yet; run 04_api_role.sql first';
    END IF;
END
$$;
