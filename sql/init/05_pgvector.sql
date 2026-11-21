-- Embeddings of the review texts (Stage 7), used for semantic search / RAG.
-- The table is NOT managed by dbt: src/rag/embed_reviews.py fills it, and dbt does not touch
-- core tables it does not define. Idempotent: safe to run more than once.
--
-- Runs automatically on a new volume (docker-entrypoint-initdb.d, after 04_api_role.sql).
-- On an existing database:
--   docker compose exec -T db sh -c \
--     'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -f /docker-entrypoint-initdb.d/05_pgvector.sql'

CREATE EXTENSION IF NOT EXISTS vector;

-- One row per (review_id, model): the same review_id appears on several orders in
-- fact_reviews, but always with the same text, so only one embedding is computed.
-- vector(384) = the dimension of intfloat/multilingual-e5-small; a model with a different
-- dimension needs another column or another table.
CREATE TABLE IF NOT EXISTS core.review_embeddings (
    review_id    text         NOT NULL,
    comment_text text         NOT NULL,
    review_score smallint     NOT NULL,
    embedding    vector(384)  NOT NULL,
    model_name   text         NOT NULL,
    created_at   timestamptz  NOT NULL DEFAULT now(),
    PRIMARY KEY (review_id, model_name)
);

-- Approximate index for cosine-similarity search (the <=> operator).
CREATE INDEX IF NOT EXISTS review_embeddings_embedding_hnsw
    ON core.review_embeddings USING hnsw (embedding vector_cosine_ops);

-- The API reads only from core, through the read-only role api_reader (04_api_role.sql).
-- The default privileges from 04 already cover new tables created by the same role,
-- but the explicit GRANT keeps this file correct if the table was created by another role.
DO $$
BEGIN
    IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'api_reader') THEN
        GRANT SELECT ON core.review_embeddings TO api_reader;
    ELSE
        RAISE WARNING 'role api_reader does not exist yet; run 04_api_role.sql first';
    END IF;
END
$$;
