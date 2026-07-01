import hashlib
import json
from pathlib import Path

from openai import AsyncOpenAI
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.config import get_settings
from app.db import AsyncSessionLocal


def compute_content_hash(content: str) -> str:
    return hashlib.sha256(content.encode()).hexdigest()


def load_kb_files(kb_dir: Path | None = None) -> list[dict]:
    if kb_dir is None:
        kb_dir = Path(__file__).parent.parent.parent / "knowledge_base"
    docs = []
    for md_file in sorted(kb_dir.glob("*.md")):
        content = md_file.read_text(encoding="utf-8")
        first_line = content.splitlines()[0] if content.strip() else ""
        title = first_line.lstrip("# ").strip() if first_line.startswith("#") else md_file.stem
        docs.append({"title": title, "content": content})
    return docs


async def embed_text(content: str) -> list[float]:
    settings = get_settings()
    client = AsyncOpenAI(
        api_key=settings.openrouter_api_key,
        base_url=settings.openrouter_base_url,
    )
    response = await client.embeddings.create(
        input=content,
        model=settings.embedding_model,
    )
    embedding = response.data[0].embedding
    if len(embedding) != 1536:
        raise ValueError(
            f"Expected 1536-dim embedding from text-embedding-3-small, got {len(embedding)}. "
            "Check EMBEDDING_MODEL config."
        )
    return embedding


async def _upsert_document(
    title: str,
    content: str,
    embedding: list[float],
    metadata: dict,
    content_hash: str,
) -> str:
    """Insert or update document. Returns 'inserted', 'updated', or 'skipped'."""
    vector_str = "[" + ",".join(str(v) for v in embedding) + "]"
    async with AsyncSessionLocal() as session:
        row = (
            await session.execute(
                text("SELECT id, content_hash FROM documents WHERE title = :title"),
                {"title": title},
            )
        ).fetchone()

        if row is None:
            try:
                await session.execute(
                    text(
                        "INSERT INTO documents (title, content, embedding, metadata, content_hash) "
                        "VALUES (:title, :content, CAST(:embedding AS vector), CAST(:metadata AS jsonb), :hash)"
                    ),
                    {
                        "title": title,
                        "content": content,
                        "embedding": vector_str,
                        "metadata": json.dumps(metadata),
                        "hash": content_hash,
                    },
                )
                await session.commit()
                return "inserted"
            except IntegrityError:
                await session.rollback()
                return "skipped"

        if row.content_hash == content_hash:
            return "skipped"

        await session.execute(
            text(
                "UPDATE documents SET content=:content, embedding=CAST(:embedding AS vector), "
                "metadata=CAST(:metadata AS jsonb), content_hash=:hash WHERE id=:id"
            ),
            {
                "content": content,
                "embedding": vector_str,
                "metadata": json.dumps(metadata),
                "hash": content_hash,
                "id": str(row.id),
            },
        )
        await session.commit()
        return "updated"


async def ingest_static_kb(kb_dir: Path | None = None) -> dict:
    """Embed and upsert all static KB markdown files. Returns ingestion summary."""
    docs = load_kb_files(kb_dir)
    ingested = skipped = 0

    for doc in docs:
        content_hash = compute_content_hash(doc["content"])
        embedding = await embed_text(doc["content"])
        status = await _upsert_document(
            title=doc["title"],
            content=doc["content"],
            embedding=embedding,
            metadata={"source": "static"},
            content_hash=content_hash,
        )
        if status == "skipped":
            skipped += 1
        else:
            ingested += 1

    return {"ingested": ingested, "skipped": skipped}
