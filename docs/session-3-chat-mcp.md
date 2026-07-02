# Session 3 — Chat Endpoint + Lead Capture MCP Server

**Project:** Automate This — SMB Automation Advisor  
**Repo:** `C:\Users\markm\Documents\Claude\Projects\Turing\rmerge-AE.2.5`  
**Date:** 2026-07-02  
**Goal:** Wire the RAG pipeline into a working `/chat` endpoint; build a FastMCP `capture_lead` server wrapping Twenty CRM; connect them via LLM tool calling.

---

## What Already Exists (Sessions 1–2)

```
backend/
  app/
    main.py              — FastAPI app; GET /health, POST /admin/ingest
    config.py            — Pydantic settings (get_settings() singleton)
    db.py                — AsyncSessionLocal, engine (asyncpg + SQLAlchemy)
    rag/
      ingest.py          — embed_text(), ingest_static_kb(), _upsert_document()
      retriever.py       — search_documents(query_embedding, top_k) → list[dict]
      live_ingester.py   — ingest_live_docs()
  knowledge_base/        — 6 static automation pattern .md files (already ingested)
  tests/
    test_health.py       — endpoint tests (39 passing)
    test_ingest.py
    test_rag_integration.py  — @pytest.mark.integration, needs live DB
  pyproject.toml         — uv project, pytest-asyncio mode=AUTO
init.sql                 — conversations + documents tables, ivfflat index
docker-compose.yml       — Postgres 16 + pgvector (or use existing twenty-db-1)
```

### DB schema (already in init.sql)

```sql
-- Conversation tracking
CREATE TABLE conversations (
    session_id  UUID        PRIMARY KEY,
    turn_count  INTEGER     NOT NULL DEFAULT 0,
    history     JSONB       NOT NULL DEFAULT '[]',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- RAG documents
CREATE TABLE documents (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    title        TEXT NOT NULL,
    content      TEXT NOT NULL,
    embedding    VECTOR(1536),
    metadata     JSONB NOT NULL DEFAULT '{}',
    content_hash TEXT UNIQUE,
    UNIQUE (title)
);
```

### Settings already in config.py

```python
chat_model: str = "openai/gpt-4o-mini"          # via OpenRouter
embedding_model: str = "openai/text-embedding-3-small"
max_turns_per_session: int = 8
max_input_chars: int = 600
max_output_tokens: int = 450
openrouter_api_key: str
openrouter_base_url: str = "https://openrouter.ai/api/v1"
database_url: str
admin_api_key: str
```

### Environment (.env)
- `DATABASE_URL=postgresql+asyncpg://automate:automate@localhost:5432/automate_this`  
  (running inside `twenty-db-1` container — do NOT start a second Postgres via docker-compose)
- `OPENROUTER_API_KEY=<key>`
- `ADMIN_API_KEY=localdev123`
- New in Session 3: `TWENTY_API_KEY=<key>`, `TWENTY_BASE_URL=http://localhost:3000`

### How to run locally
```bash
cd backend
uv run uvicorn app.main:app --reload --port 8000
```

### How to run tests
```bash
uv run pytest -m "not integration"          # 39 unit tests
uv run pytest -m integration -v             # 3 integration tests (needs live DB)
```

---

## Global Constraints

- **Package manager:** `uv` only — never `pip install`
- **Settings:** always `get_settings()` — never instantiate `Settings()` directly
- **Async everywhere:** all DB calls use `AsyncSessionLocal` + `await`
- **SQL params:** always `CAST(:x AS vector)` / `CAST(:x AS jsonb)` — never `::vector` (asyncpg incompatibility)
- **No ORM models:** raw `text()` SQL, same pattern as existing code
- **Test style:** `pytest-asyncio` with `asyncio_mode = "auto"` — no `@pytest.mark.asyncio` needed on individual tests (but still works if added)
- **Embeddings:** only `text-embedding-3-small` via OpenRouter (1536-dim). Never Ollama for embeddings.
- **LLM:** `openai/gpt-4o-mini` via OpenRouter (chat_model config)
- **YAGNI:** implement exactly what the tasks specify — no extra abstraction
- **Comments:** only when WHY is non-obvious

---

## Architecture for Session 3

```
User
  │
  ▼
POST /chat  (FastAPI, backend/app/main.py)
  │
  ├─ 1. Load/create conversation (DB: conversations table)
  ├─ 2. Enforce rate limit (turn_count < max_turns_per_session)
  ├─ 3. Validate input length (≤ max_input_chars)
  ├─ 4. embed_text(message) → query_embedding
  ├─ 5. search_documents(query_embedding, top_k=3) → context docs
  ├─ 6. Build prompt (system + context + history + user message)
  ├─ 7. Call OpenRouter LLM with tool definition for capture_lead
  │       ┌─ LLM replies with text → return to user
  │       └─ LLM calls capture_lead tool → execute → feed result back → second LLM call
  └─ 8. Persist updated history + increment turn_count
  
Response: { "reply": str, "sources": [...], "session_id": str, "turns_remaining": int }

FastMCP server (mcp_server/server.py) — separate process, port 8001
  └─ capture_lead(name, email, company, pain_point) → calls Twenty CRM REST API
```

---

## Task 1: `POST /chat` endpoint (no tool calling yet)

### What to build

Add `POST /chat` to `backend/app/main.py`.

**Request body:**
```python
class ChatRequest(BaseModel):
    session_id: str | None = None   # UUID string; None = start new session
    message: str
```

**Response body:**
```python
class ChatResponse(BaseModel):
    session_id: str
    reply: str
    sources: list[dict]             # [{title, similarity}, ...] top_k=3
    turns_remaining: int
```

**Flow:**

```python
@app.post("/chat", tags=["chat"])
async def chat(body: ChatRequest) -> ChatResponse:
    settings = get_settings()

    # 1. Input validation
    if len(body.message) > settings.max_input_chars:
        raise HTTPException(422, f"Message exceeds {settings.max_input_chars} chars")

    # 2. Session management
    session_id = body.session_id or str(uuid.uuid4())
    async with AsyncSessionLocal() as session:
        row = (await session.execute(
            text("SELECT turn_count, history FROM conversations WHERE session_id = :sid"),
            {"sid": session_id}
        )).fetchone()

        if row is None:
            turn_count = 0
            history = []
            await session.execute(
                text("INSERT INTO conversations (session_id, turn_count, history) VALUES (:sid, 0, '[]')"),
                {"sid": session_id}
            )
            await session.commit()
        else:
            turn_count = row.turn_count
            history = row.history  # already a list (asyncpg deserialises JSONB)

    # 3. Rate limiting
    if turn_count >= settings.max_turns_per_session:
        raise HTTPException(429, "Session turn limit reached")

    # 4. RAG retrieval
    from app.rag.ingest import embed_text
    from app.rag.retriever import search_documents
    query_embedding = await embed_text(body.message)
    docs = await search_documents(query_embedding, top_k=3)

    # 5. Build prompt
    context = "\n\n".join(
        f"### {d['title']}\n{d['content'][:800]}" for d in docs
    )
    system_prompt = (
        "You are 'Automate This', an SMB automation advisor. "
        "Use the context below to recommend automation solutions. "
        "Be concrete: name the tools, estimate hours saved per week, "
        "and suggest a first step the business owner can take today.\n\n"
        f"CONTEXT:\n{context}"
    )

    # 6. Call LLM
    from openai import AsyncOpenAI
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

    # 7. Persist conversation
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
            {"history": json.dumps(new_history), "sid": session_id}
        )
        await session.commit()

    sources = [{"title": d["title"], "similarity": float(d["similarity"])} for d in docs]
    return ChatResponse(
        session_id=session_id,
        reply=reply,
        sources=sources,
        turns_remaining=settings.max_turns_per_session - (turn_count + 1),
    )
```

**Imports to add to main.py:** `uuid`, `json`, `BaseModel` from pydantic, `ChatRequest`/`ChatResponse` models.

### TDD steps

Write tests FIRST in `backend/tests/test_chat.py`:

```python
# Test 1: new session created when session_id is None
# Test 2: existing session_id reuses the session (turn_count increments)
# Test 3: message > max_input_chars returns 422
# Test 4: turn_count >= max_turns_per_session returns 429
# Test 5: reply is returned from LLM
# Test 6: sources list has title and similarity keys
# Test 7: turns_remaining decrements correctly

# Mocks needed:
# - patch app.rag.ingest.embed_text (AsyncMock returning [0.0]*1536)
# - patch app.rag.retriever.search_documents (AsyncMock returning 3 fake docs)
# - patch openai.AsyncOpenAI (mock completions.create returning fake reply)
# - DB: use FastAPI TestClient with AsyncSessionLocal patched OR use httpx AsyncClient
```

Use `httpx.AsyncClient` with `ASGITransport` (already in conftest.py or add it).

### Acceptance criteria
- [ ] `POST /chat` with no session_id returns 200 with a new UUID `session_id`
- [ ] Second call with same session_id increments `turns_remaining` by -1
- [ ] Message > 600 chars returns 422
- [ ] After 8 turns, returns 429
- [ ] `sources` contains up to 3 items with `title` and `similarity` fields
- [ ] All 7 tests pass

---

## Task 2: FastMCP `capture_lead` server

### What to build

Create a standalone FastMCP server at `mcp_server/server.py`.

**Add to backend/pyproject.toml deps:**
```toml
"fastmcp>=2.0",
"httpx>=0.27",    # already present from live_ingester
```

**`mcp_server/__init__.py`** — empty

**`mcp_server/server.py`:**

```python
"""
FastMCP server exposing the capture_lead tool.
Wraps Twenty CRM REST API to create a Person record.

Run with:
    uv run python -m mcp_server.server
    
Or as MCP stdio server (for Gradio / Claude Desktop):
    uv run fastmcp run mcp_server/server.py
"""
import os
import httpx
from fastmcp import FastMCP

mcp = FastMCP("automate-this-lead-capture")

TWENTY_BASE_URL = os.getenv("TWENTY_BASE_URL", "http://localhost:3000")
TWENTY_API_KEY = os.getenv("TWENTY_API_KEY", "")


@mcp.tool()
async def capture_lead(
    name: str,
    email: str,
    company: str,
    pain_point: str,
) -> dict:
    """
    Save a prospective lead into Twenty CRM.

    Args:
        name: Full name of the contact (e.g. "Jane Smith")
        email: Business email address
        company: Company or trading name
        pain_point: The automation problem they described in the chat

    Returns:
        {"status": "created", "person_id": "<uuid>"} on success
        {"status": "error", "detail": "<msg>"} on failure
    """
    headers = {
        "Authorization": f"Bearer {TWENTY_API_KEY}",
        "Content-Type": "application/json",
    }
    first, *rest = name.strip().split(" ", 1)
    last = rest[0] if rest else ""

    payload = {
        "name": {"firstName": first, "lastName": last},
        "emails": {"primaryEmail": email},
        "company": {"name": company},
        "jobTitle": "SMB Owner",
    }

    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            resp = await client.post(
                f"{TWENTY_BASE_URL}/api/object/people",
                json=payload,
                headers=headers,
            )
            resp.raise_for_status()
            data = resp.json()
            person_id = data.get("data", {}).get("createPerson", {}).get("id", "unknown")
            
            # Also log the pain point as a note
            await client.post(
                f"{TWENTY_BASE_URL}/api/object/notes",
                json={
                    "title": f"Automation pain point — {company}",
                    "body": pain_point,
                    "noteTargets": [{"personId": person_id}] if person_id != "unknown" else [],
                },
                headers=headers,
            )
            return {"status": "created", "person_id": person_id}
        except httpx.HTTPStatusError as e:
            return {"status": "error", "detail": str(e)}
        except Exception as e:
            return {"status": "error", "detail": str(e)}


if __name__ == "__main__":
    mcp.run()
```

> **Twenty CRM API note:** The exact REST endpoint may differ depending on the Twenty CRM version.  
> Check `GET http://localhost:3000/api/object/people` with a Bearer token first.  
> If the REST path differs, inspect the network tab in Twenty CRM's UI or check the API docs at `http://localhost:3000/api`.  
> The API key is found in Twenty CRM → Settings → API → Generate Key.

### TDD steps

Write tests in `backend/tests/test_mcp_server.py`:

```python
# Test 1: capture_lead returns {"status": "created", "person_id": ...} on 201
# Test 2: capture_lead returns {"status": "error", ...} on HTTP 4xx
# Test 3: capture_lead returns {"status": "error", ...} on network failure
# Test 4: name is split correctly ("Jane Smith" → firstName="Jane", lastName="Smith")
# Test 5: single-word name handled ("Cher" → firstName="Cher", lastName="")

# Mock httpx.AsyncClient.post to avoid hitting real Twenty CRM
```

### Acceptance criteria
- [ ] `mcp_server/server.py` runs without error: `uv run python -m mcp_server.server`
- [ ] All 5 unit tests pass (mocked HTTP)
- [ ] `fastmcp` is listed in `pyproject.toml` deps

---

## Task 3: Wire `capture_lead` into the chat endpoint

### What to build

Extend `POST /chat` to pass the `capture_lead` tool definition to the LLM (OpenAI function calling format) and execute it when the LLM calls it.

**Tool definition (add to the LLM call in main.py):**

```python
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
                "name":       {"type": "string", "description": "Full name"},
                "email":      {"type": "string", "description": "Email address"},
                "company":    {"type": "string", "description": "Business name"},
                "pain_point": {"type": "string", "description": "The automation problem they described"},
            },
            "required": ["name", "email", "company", "pain_point"],
        },
    },
}
```

**Extend the LLM call:**

```python
response = await client.chat.completions.create(
    model=settings.chat_model,
    messages=messages,
    max_tokens=settings.max_output_tokens,
    tools=[CAPTURE_LEAD_TOOL],
    tool_choice="auto",
)

choice = response.choices[0]

if choice.finish_reason == "tool_calls":
    tool_call = choice.message.tool_calls[0]
    args = json.loads(tool_call.function.arguments)
    
    # Import and call the MCP tool function directly (same process)
    from mcp_server.server import capture_lead
    tool_result = await capture_lead(**args)
    
    # Feed result back to LLM for a natural language reply
    messages.append(choice.message)  # assistant message with tool_calls
    messages.append({
        "role": "tool",
        "tool_call_id": tool_call.id,
        "content": json.dumps(tool_result),
    })
    second_response = await client.chat.completions.create(
        model=settings.chat_model,
        messages=messages,
        max_tokens=settings.max_output_tokens,
    )
    reply = second_response.choices[0].message.content
else:
    reply = choice.message.content
```

> **Note:** We import `capture_lead` directly from `mcp_server.server` (same Python process) rather than connecting to the MCP server over a socket. The MCP server is separately runnable for Claude Desktop / Gradio integration — this import is for the API handler. Both are valid: the function is the same, the protocol wrapping is what FastMCP adds.

### TDD steps

Add to `backend/tests/test_chat.py`:

```python
# Test 8: when LLM returns tool_call for capture_lead,
#          the tool is called with correct args and a second LLM call is made
# Test 9: tool result {"status": "created"} leads to natural-language reply confirming lead saved
# Test 10: tool result {"status": "error"} leads to graceful reply (not 500)

# Mocks needed: patch capture_lead in mcp_server.server, patch both LLM calls
```

### Acceptance criteria
- [ ] All 10 chat tests pass
- [ ] Manual test: sending "My name is Jane Smith, email jane@example.com, I run a plumbing company and spend 3 hours a week on invoices" causes LLM to call `capture_lead` (verify via log or mock assertion)

---

## Task 4: Gradio frontend (optional but recommended for submission demo)

### What to build

A simple Gradio chat interface that calls the `/chat` endpoint.

**File:** `frontend/app.py`

**Add to backend/pyproject.toml (or separate frontend/pyproject.toml):**
```toml
"gradio>=4.0",
```

```python
"""
Gradio chat frontend for Automate This.
Run with: uv run python frontend/app.py
"""
import uuid
import httpx
import gradio as gr

API_URL = "http://localhost:8000/chat"
session_id = str(uuid.uuid4())


def chat(message: str, history: list) -> tuple[str, list]:
    global session_id
    try:
        resp = httpx.post(
            API_URL,
            json={"session_id": session_id, "message": message},
            timeout=30.0,
        )
        data = resp.json()
        if resp.status_code == 429:
            return "Session limit reached. Please refresh to start a new conversation.", history
        if resp.status_code != 200:
            return f"Error {resp.status_code}: {data.get('detail', 'Unknown error')}", history

        reply = data["reply"]
        turns_left = data.get("turns_remaining", "?")
        sources = data.get("sources", [])

        # Append source citations to reply
        if sources:
            citations = "\n\n**Sources used:**\n" + "\n".join(
                f"- {s['title']} (similarity: {s['similarity']:.2f})" for s in sources
            )
            reply += citations

        reply += f"\n\n*{turns_left} turns remaining in this session.*"
        history.append((message, reply))
        return "", history
    except Exception as e:
        return f"Connection error: {e}", history


with gr.Blocks(title="Automate This — SMB Advisor") as demo:
    gr.Markdown("# Automate This\nDescribe a repetitive task your business does manually. I'll tell you how to automate it.")
    chatbot = gr.Chatbot(height=500)
    msg = gr.Textbox(placeholder="e.g. I spend 3 hours a week chasing unpaid invoices by email...", label="Your message")
    clear = gr.Button("New conversation")

    msg.submit(chat, [msg, chatbot], [msg, chatbot])
    clear.click(lambda: (str(uuid.uuid4()), []), outputs=[gr.State(), chatbot])

if __name__ == "__main__":
    demo.launch(server_port=7860)
```

**Run:**
```bash
uv run python frontend/app.py
# Open http://localhost:7860
```

### Acceptance criteria
- [ ] Gradio UI loads at `http://localhost:7860`
- [ ] Chat works end-to-end (message → reply with sources shown)
- [ ] "New conversation" resets the session

---

## Task 5: Final test run + verification

### Steps

1. Run full unit suite — must be ≥ 39 + new tests:
   ```bash
   cd backend
   uv run pytest -m "not integration" -v
   ```

2. Run integration tests (needs live DB):
   ```bash
   uv run pytest -m integration -v
   ```

3. Smoke test the full flow manually:
   ```bash
   # Start the API
   uv run uvicorn app.main:app --reload --port 8000

   # New chat session
   curl -X POST http://localhost:8000/chat \
     -H "Content-Type: application/json" \
     -d '{"message": "I spend 4 hours a week manually creating invoices in Word and emailing them"}'

   # Second turn (use session_id from response above)
   curl -X POST http://localhost:8000/chat \
     -H "Content-Type: application/json" \
     -d '{"session_id": "<id>", "message": "My name is Jane Smith, email jane@acme.com, I run Acme Plumbing"}'
   ```

4. Commit all changes, push to `https://github.com/RomanMerg/rmerge-AE.2.5`.

---

## New .env variables needed

Add to `backend/.env` (and `backend/.env.example`):

```bash
# Twenty CRM (for capture_lead MCP tool)
TWENTY_API_KEY=your_twenty_crm_api_key_here
TWENTY_BASE_URL=http://localhost:3000
```

**How to get the Twenty CRM API key:**
1. Open Twenty CRM (usually at `http://localhost:3000`)
2. Settings → API → Generate new key
3. Copy the token into `.env`

---

## Summary of files to create/modify

| File | Action |
|---|---|
| `backend/app/main.py` | Add `POST /chat`, `ChatRequest`, `ChatResponse`, `CAPTURE_LEAD_TOOL` |
| `backend/mcp_server/__init__.py` | Create (empty) |
| `backend/mcp_server/server.py` | Create FastMCP server |
| `backend/tests/test_chat.py` | Create (10+ tests) |
| `backend/tests/test_mcp_server.py` | Create (5+ tests) |
| `backend/pyproject.toml` | Add `fastmcp>=2.0` dep |
| `backend/.env.example` | Add TWENTY_API_KEY, TWENTY_BASE_URL |
| `frontend/app.py` | Create Gradio UI (optional) |

**Do NOT modify:** `rag/ingest.py`, `rag/retriever.py`, `rag/live_ingester.py`, `init.sql`, `docker-compose.yml`.
