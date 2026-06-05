-- Enable pgvector extension (required before VECTOR columns can be created)
CREATE EXTENSION IF NOT EXISTS vector;

-- Conversation tracking and per-session rate limiting
CREATE TABLE IF NOT EXISTS conversations (
    session_id  UUID        PRIMARY KEY,
    turn_count  INTEGER     NOT NULL DEFAULT 0,
    history     JSONB       NOT NULL DEFAULT '[]',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- RAG document store (static patterns + live docs share this table)
CREATE TABLE IF NOT EXISTS documents (
    id           UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    title        TEXT        NOT NULL,
    content      TEXT        NOT NULL,
    embedding    VECTOR(1536),             -- dimensions match text-embedding-3-small
    metadata     JSONB       NOT NULL DEFAULT '{}',  -- source, fetched_at, use_case etc.
    content_hash TEXT        UNIQUE        -- sha256 of content; enables idempotent re-ingest
);

-- IVFFlat index for approximate cosine similarity search
-- lists=10 is appropriate for our small KB (~100 documents); increase when KB grows past 10k
CREATE INDEX IF NOT EXISTS documents_embedding_idx
    ON documents USING ivfflat (embedding vector_cosine_ops)
    WITH (lists = 10);
