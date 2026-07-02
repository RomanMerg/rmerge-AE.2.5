import json
import uuid
from typing import Literal

from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from openai import AsyncOpenAI
from pydantic import BaseModel
from sqlalchemy import text

from app.config import get_settings
from app.db import AsyncSessionLocal
from app.rag.ingest import embed_text, ingest_static_kb
from app.rag.live_ingester import ingest_live_docs
from app.rag.retriever import search_documents

app = FastAPI(
    title="Automate This API",
    description="SMB automation advisor — AI-powered consulting chatbot backend",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().allowed_origins_list,
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "X-Admin-Key"],
)


@app.get("/health", tags=["meta"])
async def health() -> dict:
    """Uptime check — no auth required. Render calls this to verify the service is up."""
    return {"status": "ok", "version": app.version}


@app.post("/admin/ingest", tags=["admin"])
async def admin_ingest(
    source: Literal["static", "live"] = Query(default="static"),
    x_admin_key: str | None = Header(default=None),
) -> dict:
    """Ingest knowledge base documents into pgvector.

    - source=static (default): embeds the 6 curated KB markdown files
    - source=live: fetches and embeds live docs from n8n, Twenty CRM, Make, Zapier, Lovable
    """
    if x_admin_key is None:
        raise HTTPException(status_code=401, detail="X-Admin-Key header required")
    if x_admin_key != get_settings().admin_api_key:
        raise HTTPException(status_code=403, detail="Invalid admin key")

    if source == "static":
        result = await ingest_static_kb()
    else:
        result = await ingest_live_docs()

    return {"source": source, "result": result}


class ChatRequest(BaseModel):
    session_id: str | None = None  # UUID string; None = start new session
    message: str


class ChatResponse(BaseModel):
    session_id: str
    reply: str
    sources: list[dict]  # [{title, similarity}, ...] top_k=3
    turns_remaining: int


@app.post("/chat", tags=["chat"])
async def chat(body: ChatRequest) -> ChatResponse:
    """Single-turn RAG chat over the SMB automation knowledge base (no tool calling yet)."""
    settings = get_settings()

    if len(body.message) > settings.max_input_chars:
        raise HTTPException(422, f"Message exceeds {settings.max_input_chars} chars")

    session_id = body.session_id or str(uuid.uuid4())
    async with AsyncSessionLocal() as session:
        row = (
            await session.execute(
                text("SELECT turn_count, history FROM conversations WHERE session_id = :sid"),
                {"sid": session_id},
            )
        ).fetchone()

        if row is None:
            turn_count = 0
            history = []
            await session.execute(
                text("INSERT INTO conversations (session_id, turn_count, history) VALUES (:sid, 0, '[]')"),
                {"sid": session_id},
            )
            await session.commit()
        else:
            turn_count = row.turn_count
            history = row.history  # already a list (asyncpg deserialises JSONB)

    if turn_count >= settings.max_turns_per_session:
        raise HTTPException(429, "Session turn limit reached")

    query_embedding = await embed_text(body.message)
    docs = await search_documents(query_embedding, top_k=3)

    context = "\n\n".join(f"### {d['title']}\n{d['content'][:800]}" for d in docs)
    system_prompt = (
        "You are 'Automate This', an SMB automation advisor. "
        "Use the context below to recommend automation solutions. "
        "Be concrete: name the tools, estimate hours saved per week, "
        "and suggest a first step the business owner can take today.\n\n"
        f"CONTEXT:\n{context}"
    )

    client = AsyncOpenAI(
        api_key=settings.openrouter_api_key,
        base_url=settings.openrouter_base_url,
    )
    messages = [{"role": "system", "content": system_prompt}]
    messages += history
    messages.append({"role": "user", "content": body.message})

    response = await client.chat.completions.create(
        model=settings.chat_model,
        messages=messages,
        max_tokens=settings.max_output_tokens,
    )
    reply = response.choices[0].message.content

    new_history = history + [
        {"role": "user", "content": body.message},
        {"role": "assistant", "content": reply},
    ]
    async with AsyncSessionLocal() as session:
        await session.execute(
            text(
                "UPDATE conversations SET turn_count = turn_count + 1, "
                "history = CAST(:history AS jsonb) WHERE session_id = :sid"
            ),
            {"history": json.dumps(new_history), "sid": session_id},
        )
        await session.commit()

    sources = [{"title": d["title"], "similarity": float(d["similarity"])} for d in docs]
    return ChatResponse(
        session_id=session_id,
        reply=reply,
        sources=sources,
        turns_remaining=settings.max_turns_per_session - (turn_count + 1),
    )
