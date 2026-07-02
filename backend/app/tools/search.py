from app.rag.ingest import embed_text
from app.rag.retriever import search_documents


async def search_automation_patterns(task_description: str) -> dict:
    """Search the knowledge base for automation patterns relevant to a task.

    Returns `formatted` (context text for the LLM) and `sources` (structured
    title + similarity pairs for the API response).
    """
    query_embedding = await embed_text(task_description)
    docs = await search_documents(query_embedding, top_k=3)

    formatted = "\n\n".join(f"### {d['title']}\n{d['content'][:800]}" for d in docs)
    sources = [{"title": d["title"], "similarity": float(d["similarity"])} for d in docs]

    return {"formatted": formatted, "sources": sources}
