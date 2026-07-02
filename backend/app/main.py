import json
import uuid
from typing import Literal

from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_openai import ChatOpenAI
from pydantic import BaseModel
from sqlalchemy import text

from app.config import get_settings
from app.db import AsyncSessionLocal
from app.rag.ingest import ingest_static_kb
from app.rag.live_ingester import ingest_live_docs
from app.tools.roi import calculate_roi
from app.tools.search import search_automation_patterns
from mcp_server.server import capture_lead

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
    sources: list[dict]  # [{title, similarity}, ...] — populated only if search_automation_patterns was called
    turns_remaining: int


CALCULATE_ROI_TOOL = {
    "type": "function",
    "function": {
        "name": "calculate_roi",
        "description": (
            "Calculate the ROI of automating a manual task. Call this when the user states "
            "how many hours per week a task takes and you know or can reasonably estimate "
            "their hourly rate and a rough automation setup cost."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "hours_saved_per_week": {"type": "number", "description": "Hours per week the automation would save"},
                "hourly_rate": {"type": "number", "description": "The business owner's hourly rate in EUR"},
                "setup_cost": {"type": "number", "description": "One-time cost to build/set up the automation in EUR"},
            },
            "required": ["hours_saved_per_week", "hourly_rate", "setup_cost"],
        },
    },
}

SEARCH_AUTOMATION_PATTERNS_TOOL = {
    "type": "function",
    "function": {
        "name": "search_automation_patterns",
        "description": (
            "Search the knowledge base for automation patterns relevant to a task the user "
            "described. Call this before recommending a specific automation approach so your "
            "answer is grounded in real patterns rather than guessed."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "task_description": {
                    "type": "string",
                    "description": "The manual task or business process to find automation patterns for",
                },
            },
            "required": ["task_description"],
        },
    },
}

CAPTURE_LEAD_TOOL = {
    "type": "function",
    "function": {
        "name": "capture_lead",
        "description": (
            "Save the user's contact details into the CRM so the team can follow up. "
            "Only call this when the user has explicitly provided their name AND email."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Full name"},
                "email": {"type": "string", "description": "Email address"},
                "company": {"type": "string", "description": "Business name"},
                "pain_point": {"type": "string", "description": "The automation problem they described"},
            },
            "required": ["name", "email", "company", "pain_point"],
        },
    },
}

SYSTEM_PROMPT = (
    "You are 'Automate This', an SMB automation advisor. You have three tools available: "
    "search_automation_patterns to ground your advice in real automation patterns, "
    "calculate_roi to estimate payback time and savings once you know hours saved, hourly "
    "rate, and setup cost, and capture_lead to save the user's contact details once they've "
    "explicitly given you their name AND email. Be concrete: name the tools, estimate hours "
    "saved per week, and suggest a first step the business owner can take today."
)


@app.post("/chat", tags=["chat"])
async def chat(body: ChatRequest) -> ChatResponse:
    """Multi-turn chat with tool calling (search_automation_patterns, calculate_roi, capture_lead) via LangChain."""
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

    llm = ChatOpenAI(
        model=settings.chat_model,
        api_key=settings.openrouter_api_key,
        base_url=settings.openrouter_base_url,
        max_tokens=settings.max_output_tokens,
    ).bind_tools([CALCULATE_ROI_TOOL, SEARCH_AUTOMATION_PATTERNS_TOOL, CAPTURE_LEAD_TOOL])

    messages = [SystemMessage(content=SYSTEM_PROMPT)]
    for turn in history:
        if turn["role"] == "user":
            messages.append(HumanMessage(content=turn["content"]))
        else:
            messages.append(AIMessage(content=turn["content"]))
    messages.append(HumanMessage(content=body.message))

    ai_message = await llm.ainvoke(messages)
    sources: list[dict] = []

    if ai_message.tool_calls:
        messages.append(ai_message)
        for tool_call in ai_message.tool_calls:
            tool_name = tool_call["name"]
            tool_args = tool_call["args"]

            if tool_name == "calculate_roi":
                tool_result = calculate_roi(**tool_args)
            elif tool_name == "search_automation_patterns":
                search_result = await search_automation_patterns(**tool_args)
                sources = search_result["sources"]
                tool_result = search_result["formatted"]
            elif tool_name == "capture_lead":
                tool_result = await capture_lead(**tool_args)
            else:
                tool_result = {"status": "error", "detail": f"Unknown tool {tool_name}"}

            messages.append(
                ToolMessage(content=json.dumps(tool_result), tool_call_id=tool_call["id"])
            )

        final_message = await llm.ainvoke(messages)
        reply = final_message.content
    else:
        reply = ai_message.content

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

    return ChatResponse(
        session_id=session_id,
        reply=reply,
        sources=sources,
        turns_remaining=settings.max_turns_per_session - (turn_count + 1),
    )
