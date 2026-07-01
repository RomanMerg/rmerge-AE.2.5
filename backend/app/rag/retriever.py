from sqlalchemy import text

from app.db import AsyncSessionLocal


async def search_documents(query_embedding: list[float], top_k: int = 2) -> list[dict]:
    """Return the top_k documents most similar to query_embedding (cosine similarity)."""
    vector_str = "[" + ",".join(str(v) for v in query_embedding) + "]"
    async with AsyncSessionLocal() as session:
        # Probe all ivfflat lists to guarantee full recall on small datasets.
        # In production with many docs, lower this to ~10% of the lists value for speed.
        await session.execute(text("SET LOCAL ivfflat.probes = 10"))
        result = await session.execute(
            text(
                "SELECT title, content, metadata, "
                "1 - (embedding <=> CAST(:embedding AS vector)) AS similarity "
                "FROM documents "
                "ORDER BY embedding <=> CAST(:embedding AS vector) "
                "LIMIT :top_k"
            ),
            {"embedding": vector_str, "top_k": top_k},
        )
        rows = result.mappings().all()
        return [dict(row) for row in rows]
