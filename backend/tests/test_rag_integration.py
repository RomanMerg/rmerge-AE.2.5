"""
Integration tests for RAG ingestion + retrieval.

Requires live Postgres with pgvector running:
  docker compose up -d postgres

Run with:
  uv run pytest tests/test_rag_integration.py -v -m integration

Skip from normal test run:
  uv run pytest -v -m "not integration"
"""

import json
import uuid

import pytest
from sqlalchemy import text

from app.db import AsyncSessionLocal


async def _db_available() -> bool:
    """Return True if the test DB is reachable."""
    try:
        async with AsyncSessionLocal() as session:
            await session.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
async def skip_if_no_db():
    if not await _db_available():
        pytest.skip("Postgres not available — run: docker compose up -d postgres")
    yield
    # Dispose the connection pool so asyncpg coroutine warnings from one test
    # do not leave the pool in a broken state that causes the next test to skip.
    from app.db import engine
    await engine.dispose()


@pytest.fixture
async def clean_test_docs():
    """Remove any documents with title starting 'Integration Test:' before and after each test."""
    async with AsyncSessionLocal() as session:
        await session.execute(
            text("DELETE FROM documents WHERE title LIKE 'Integration Test:%'")
        )
        await session.commit()
    yield
    async with AsyncSessionLocal() as session:
        await session.execute(
            text("DELETE FROM documents WHERE title LIKE 'Integration Test:%'")
        )
        await session.commit()


@pytest.mark.asyncio
async def test_ingest_and_retrieve_similarity_above_threshold(clean_test_docs):
    """
    Insert a document with a known 1536-dim vector, then query with the same vector.
    The cosine similarity of a vector with itself is 1.0, which must exceed 0.7.
    """
    from app.db import AsyncSessionLocal
    from app.rag.retriever import search_documents

    # A simple unit vector (norm = 1.0 for clean cosine math)
    embedding = [0.0] * 1536
    embedding[0] = 1.0  # unit vector in dimension 0
    vector_str = "[" + ",".join(str(v) for v in embedding) + "]"

    doc_title = "Integration Test: Unit Vector Doc"
    doc_content = "This is a test document for integration testing the retriever."
    content_hash = str(uuid.uuid4())  # unique hash so no conflict

    async with AsyncSessionLocal() as session:
        await session.execute(
            text(
                "INSERT INTO documents (title, content, embedding, metadata, content_hash) "
                "VALUES (:title, :content, CAST(:embedding AS vector), CAST(:metadata AS jsonb), :hash)"
            ),
            {
                "title": doc_title,
                "content": doc_content,
                "embedding": vector_str,
                "metadata": json.dumps({"source": "integration_test"}),
                "hash": content_hash,
            },
        )
        await session.commit()

    # Query with the same vector — cosine similarity should be 1.0
    results = await search_documents(embedding, top_k=1)

    assert len(results) >= 1, "Expected at least one result from retriever"
    top_result = results[0]
    assert top_result["title"] == doc_title
    assert top_result["similarity"] > 0.7, (
        f"Expected similarity > 0.7, got {top_result['similarity']}"
    )


@pytest.mark.asyncio
async def test_retriever_returns_most_similar_first(clean_test_docs):
    """When two documents are inserted, the one most similar to the query comes first."""
    from app.rag.retriever import search_documents

    # Vector pointing strongly in dimension 0
    embedding_a = [0.0] * 1536
    embedding_a[0] = 1.0

    # Vector pointing strongly in dimension 1 (less similar to embedding_a)
    embedding_b = [0.0] * 1536
    embedding_b[1] = 1.0

    # Query is identical to embedding_a
    query = list(embedding_a)

    async with AsyncSessionLocal() as session:
        for title, emb in [("Integration Test: Doc A", embedding_a), ("Integration Test: Doc B", embedding_b)]:
            vec_str = "[" + ",".join(str(v) for v in emb) + "]"
            await session.execute(
                text(
                    "INSERT INTO documents (title, content, embedding, metadata, content_hash) "
                    "VALUES (:title, :content, CAST(:embedding AS vector), CAST(:metadata AS jsonb), :hash)"
                ),
                {
                    "title": title,
                    "content": f"Content for {title}",
                    "embedding": vec_str,
                    "metadata": json.dumps({"source": "integration_test"}),
                    "hash": str(uuid.uuid4()),
                },
            )
        await session.commit()

    results = await search_documents(query, top_k=2)

    assert len(results) >= 2
    titles = [r["title"] for r in results[:2]]
    assert titles[0] == "Integration Test: Doc A", (
        f"Expected Doc A (similarity=1.0) first, got: {titles}"
    )
    assert results[0]["similarity"] > results[1]["similarity"]


@pytest.mark.asyncio
async def test_upsert_idempotency_skips_same_content(clean_test_docs):
    """Calling ingest_static_kb twice does not create duplicate documents."""
    from unittest.mock import AsyncMock, patch

    from app.rag import ingest

    fake_embedding = [0.0] * 1536
    fake_embedding[0] = 0.5

    tmp_content = "# Pattern: Idempotency Test\n\nContent that won't change."

    with (
        patch.object(ingest, "load_kb_files", return_value=[
            {"title": "Integration Test: Idempotency", "content": tmp_content}
        ]),
        patch.object(ingest, "embed_text", new=AsyncMock(return_value=fake_embedding)),
    ):
        result1 = await ingest.ingest_static_kb()
        result2 = await ingest.ingest_static_kb()

    assert result1["ingested"] == 1
    assert result2["skipped"] == 1, "Second run with same content should be skipped"

    # Verify only 1 row in DB
    async with AsyncSessionLocal() as session:
        count = (
            await session.execute(
                text("SELECT COUNT(*) FROM documents WHERE title = 'Integration Test: Idempotency'")
            )
        ).scalar()
    assert count == 1, f"Expected 1 row, found {count}"
