import json
import time
import uuid
from contextlib import asynccontextmanager
from typing import Literal

import structlog
from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.errors import GraphRecursionError
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool
from pydantic import BaseModel
from sqlalchemy import text

from app.agent import build_agent_graph, turn_input
from app.config import get_settings
from app.db import AsyncSessionLocal
from app.logging_config import configure_logging
from app.observability import get_langfuse_callbacks
from app.rag.ingest import ingest_static_kb
from app.rag.live_ingester import ingest_live_docs
from app.rate_limit import check_ip_rate_limit, get_client_ip

logger = structlog.get_logger()

FALLBACK_REPLY = "Could you tell me more about what you're looking to automate?"
RECURSION_FALLBACK_REPLY = (
    "That one took more steps than I allow myself — could you rephrase or split the question?"
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: logging + LangGraph Postgres checkpointer + compiled agent graph.
    ASGITransport in unit tests never runs this — tests set app.state.agent_graph directly."""
    configure_logging()
    settings = get_settings()
    pool = AsyncConnectionPool(
        conninfo=settings.database_url_psycopg,
        max_size=5,
        open=False,
        kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row},
    )
    await pool.open()
    checkpointer = AsyncPostgresSaver(pool)
    await checkpointer.setup()  # idempotent — creates checkpoint tables on first run
    app.state.agent_graph = build_agent_graph(checkpointer)
    logger.info("startup_complete", checkpointer="AsyncPostgresSaver")
    yield
    await pool.close()


app = FastAPI(
    title="Automate This API",
    description="SMB automation advisor — AI-powered consulting chatbot backend",
    version="0.2.0",
    lifespan=lifespan,
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
    sources: list[dict]  # [{title, similarity}, ...] — populated only if search_automation_patterns was called
    turns_remaining: int
    tokens_used: int
    cost_usd: float


async def _get_or_create_turn_count(session_id: str) -> int:
    async with AsyncSessionLocal() as session:
        row = (
            await session.execute(
                text("SELECT turn_count FROM conversations WHERE session_id = :sid"),
                {"sid": session_id},
            )
        ).fetchone()
        if row is None:
            await session.execute(
                text("INSERT INTO conversations (session_id, turn_count, history) VALUES (:sid, 0, '[]')"),
                {"sid": session_id},
            )
            await session.commit()
            return 0
        return row.turn_count


async def _increment_turn(session_id: str) -> None:
    async with AsyncSessionLocal() as session:
        await session.execute(
            text(
                "UPDATE conversations SET turn_count = turn_count + 1, "
                "updated_at = NOW() WHERE session_id = :sid"
            ),
            {"sid": session_id},
        )
        await session.commit()


@app.post("/chat", tags=["chat"])
async def chat(body: ChatRequest, request: Request) -> ChatResponse:
    """Multi-turn chat via the LangGraph agent. History lives in LangGraph
    checkpoints (thread_id = session_id); conversations table only counts turns."""
    settings = get_settings()

    if len(body.message) > settings.max_input_chars:
        raise HTTPException(422, f"Message exceeds {settings.max_input_chars} chars")

    await check_ip_rate_limit(get_client_ip(request))

    session_id = body.session_id or str(uuid.uuid4())
    turn_count = await _get_or_create_turn_count(session_id)
    if turn_count >= settings.max_turns_per_session:
        raise HTTPException(429, "Session turn limit reached")

    graph = app.state.agent_graph
    config = {
        "configurable": {"thread_id": session_id},
        "recursion_limit": 10,
        "callbacks": get_langfuse_callbacks(),
        "metadata": {"langfuse_session_id": session_id},
    }

    started = time.perf_counter()
    try:
        result = await graph.ainvoke(turn_input(body.message), config)
        reply = result["messages"][-1].content or FALLBACK_REPLY
    except GraphRecursionError:
        result = {"sources": [], "tools_called": [], "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}
        reply = RECURSION_FALLBACK_REPLY
    latency_ms = round((time.perf_counter() - started) * 1000)

    await _increment_turn(session_id)

    tokens_used = result["input_tokens"] + result["output_tokens"]
    turns_remaining = settings.max_turns_per_session - (turn_count + 1)
    logger.info(
        "chat_turn",
        session_id=session_id,
        tools_called=result["tools_called"],
        tokens_used=tokens_used,
        cost_usd=round(result["cost_usd"], 6),
        latency_ms=latency_ms,
        turns_remaining=turns_remaining,
    )

    return ChatResponse(
        session_id=session_id,
        reply=reply,
        sources=result["sources"],
        turns_remaining=turns_remaining,
        tokens_used=tokens_used,
        cost_usd=round(result["cost_usd"], 6),
    )


@app.post("/chat/stream", tags=["chat"])
async def chat_stream(body: ChatRequest, request: Request) -> StreamingResponse:
    """SSE variant of /chat for the Next.js frontend: streams LLM tokens as
    `token` events, then one `done` event with the full ChatResponse payload.
    All request-rejection paths (422/429) fire BEFORE streaming starts, as
    plain HTTP errors — once the stream is open only SSE events come back."""
    settings = get_settings()

    if len(body.message) > settings.max_input_chars:
        raise HTTPException(422, f"Message exceeds {settings.max_input_chars} chars")

    await check_ip_rate_limit(get_client_ip(request))

    session_id = body.session_id or str(uuid.uuid4())
    turn_count = await _get_or_create_turn_count(session_id)
    if turn_count >= settings.max_turns_per_session:
        raise HTTPException(429, "Session turn limit reached")

    graph = app.state.agent_graph
    config = {
        "configurable": {"thread_id": session_id},
        "recursion_limit": 10,
        "callbacks": get_langfuse_callbacks(),
        "metadata": {"langfuse_session_id": session_id},
    }

    async def event_stream():
        started = time.perf_counter()
        try:
            try:
                async for chunk, metadata in graph.astream(
                    turn_input(body.message), config, stream_mode="messages"
                ):
                    # Only surface assistant text (agent node); tool results and
                    # tool-call argument deltas are not user-facing tokens.
                    if (
                        metadata.get("langgraph_node") == "agent"
                        and isinstance(chunk.content, str)
                        and chunk.content
                    ):
                        yield f"event: token\ndata: {json.dumps({'content': chunk.content})}\n\n"

                state = await graph.aget_state(config)
                values = state.values
                reply = values["messages"][-1].content or FALLBACK_REPLY
                await _increment_turn(session_id)

                tokens_used = values["input_tokens"] + values["output_tokens"]
                turns_remaining = settings.max_turns_per_session - (turn_count + 1)
                logger.info(
                    "chat_turn",
                    session_id=session_id,
                    tools_called=values["tools_called"],
                    tokens_used=tokens_used,
                    cost_usd=round(values["cost_usd"], 6),
                    latency_ms=round((time.perf_counter() - started) * 1000),
                    turns_remaining=turns_remaining,
                    streamed=True,
                )
                payload = ChatResponse(
                    session_id=session_id,
                    reply=reply,
                    sources=values["sources"],
                    turns_remaining=turns_remaining,
                    tokens_used=tokens_used,
                    cost_usd=round(values["cost_usd"], 6),
                ).model_dump()
                yield f"event: done\ndata: {json.dumps(payload)}\n\n"
            except GraphRecursionError:
                await _increment_turn(session_id)
                turns_remaining = settings.max_turns_per_session - (turn_count + 1)
                logger.info(
                    "chat_turn",
                    session_id=session_id,
                    tools_called=[],
                    tokens_used=0,
                    cost_usd=0.0,
                    latency_ms=round((time.perf_counter() - started) * 1000),
                    turns_remaining=turns_remaining,
                    streamed=True,
                )
                payload = ChatResponse(
                    session_id=session_id,
                    reply=RECURSION_FALLBACK_REPLY,
                    sources=[],
                    turns_remaining=turns_remaining,
                    tokens_used=0,
                    cost_usd=0.0,
                ).model_dump()
                yield f"event: done\ndata: {json.dumps(payload)}\n\n"
        except Exception as e:  # noqa: BLE001 — stream is already open; must emit SSE, not raise
            logger.error("chat_stream_error", session_id=session_id, error=str(e))
            yield f"event: error\ndata: {json.dumps({'detail': 'The advisor hit a snag mid-reply — please retry.'})}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")
