# Session 2 — Knowledge Base + Ingestion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Populate the knowledge base with 6 curated automation pattern files, build an idempotent ingestion pipeline that embeds them into pgvector, add a live-docs ingester for external tool documentation, and expose a protected `/admin/ingest` endpoint.

**Architecture:** All documents (static KB files + live tool docs) live in a single `documents` table in pgvector, queried later by the agent's `search_automation_patterns` tool. Ingestion is always idempotent via sha256 `content_hash` upsert logic. Embeddings always use `text-embedding-3-small` via OpenRouter (1536-dim, matching the `VECTOR(1536)` schema). The retriever (`retriever.py`) uses direct async SQL with pgvector's `<=>` cosine operator — no LangChain PGVector class needed, avoiding schema conflicts with our existing `documents` table.

**Tech Stack:** Python 3.12, `openai` (AsyncOpenAI client for OpenRouter-compatible embeddings), `httpx` (live doc fetching), `beautifulsoup4` (HTML stripping), `tiktoken` (token counting + chunking), SQLAlchemy async (existing), asyncpg (existing), pytest + pytest-asyncio (existing)

**Key design decisions (don't reverse without reading the handover):**
- `VECTOR(1536)` in schema → only `text-embedding-3-small` (1536-dim) is valid. `nomic-embed-text` via Ollama is 768-dim and will NOT fit. Local dev requires an OpenRouter key.
- Static KB files are embedded whole (1 file = 1 document). They're ~300–600 tokens — no chunking needed.
- Live docs chunk at 800 tokens / 100-token overlap using `tiktoken`.
- Upsert logic: lookup by `title`, skip if same `content_hash`, update embedding if content changed, insert if new.
- Retriever uses raw async SQL, not `langchain-postgres` (which creates its own tables).

---

## File Map

| File | Action | Responsibility |
|------|--------|---------------|
| `backend/knowledge_base/.gitkeep` | Delete | Replaced by real files |
| `backend/knowledge_base/01-lead-capture.md` | Create | KB pattern: lead capture from forms to CRM |
| `backend/knowledge_base/02-email-parsing.md` | Create | KB pattern: email parsing to database |
| `backend/knowledge_base/03-invoice-generation.md` | Create | KB pattern: invoice generation from spreadsheet |
| `backend/knowledge_base/04-appointment-booking.md` | Create | KB pattern: appointment booking + confirmation |
| `backend/knowledge_base/05-client-onboarding.md` | Create | KB pattern: new client onboarding workflow |
| `backend/knowledge_base/06-social-media-scheduling.md` | Create | KB pattern: social media post scheduling |
| `backend/app/rag/__init__.py` | Create | Package marker |
| `backend/app/rag/ingest.py` | Create | Load MD files, compute sha256, embed via OpenAI, upsert |
| `backend/app/rag/retriever.py` | Create | Async cosine similarity search against `documents` table |
| `backend/app/rag/live_ingester.py` | Create | Fetch URLs, strip HTML, chunk 800/100 tokens, embed, upsert |
| `backend/app/main.py` | Modify | Add POST `/admin/ingest` endpoint |
| `backend/tests/test_ingest.py` | Create | Unit tests (mocked embeddings) + integration test (real DB) |
| `backend/pyproject.toml` | Modify | Add `openai`, `beautifulsoup4`, `tiktoken`; move `httpx` to prod |

---

## Task 1: Add dependencies + `rag/` package scaffold

**Files:**
- Modify: `backend/pyproject.toml`
- Create: `backend/app/rag/__init__.py`

- [ ] **Step 1: Add production dependencies**

Run from `backend/`:

```bash
uv add openai beautifulsoup4 tiktoken httpx
```

`httpx` was a dev-only dep (for tests); it now becomes a prod dep because `live_ingester.py` needs it at runtime. `uv add` adds to `[project].dependencies`.

- [ ] **Step 2: Verify pyproject.toml has all deps**

After `uv add`, `backend/pyproject.toml` `[project].dependencies` section must contain all four new packages. Run:

```bash
uv run python -c "import openai, bs4, tiktoken, httpx; print('all imports OK')"
```

Expected: `all imports OK`

- [ ] **Step 3: Create rag package marker**

```bash
mkdir -p backend/app/rag
touch backend/app/rag/__init__.py
```

- [ ] **Step 4: Run existing tests — confirm nothing broken**

```bash
cd backend
uv run pytest -v
```

Expected: all 13 existing tests pass.

- [ ] **Step 5: Commit**

```bash
git add backend/pyproject.toml backend/uv.lock backend/app/rag/__init__.py
git commit -m "chore: add openai, beautifulsoup4, tiktoken, httpx deps + rag package"
```

---

## Task 2: Write 6 static KB markdown files

**Files:**
- Delete: `backend/knowledge_base/.gitkeep`
- Create: `backend/knowledge_base/01-lead-capture.md` through `06-social-media-scheduling.md`

- [ ] **Step 1: Remove .gitkeep**

```bash
rm backend/knowledge_base/.gitkeep
```

- [ ] **Step 2: Create `backend/knowledge_base/01-lead-capture.md`**

```markdown
# Pattern: Lead Capture from Contact Forms → CRM/Spreadsheet

## Problem
A potential customer fills in a contact form on your website. You get an email notification. You open it, copy the name, email, phone, and message into a spreadsheet or CRM — manually, one by one. If you're busy, leads wait hours before being logged. If you're on holiday, they're lost.

This is the single most common manual bottleneck for service businesses: the gap between a lead arriving and it being tracked.

## Manual time cost (benchmark)
- 3–5 minutes per lead to open, copy, paste, and log
- 20 leads/month = 1–2 hours/month
- 50 leads/month = 3–4 hours/month
- Zero leads get missed when you're busy — except they do

## Automation approach
1. Contact form submits to a webhook URL (most form tools — Typeform, Tally, Gravity Forms — support this natively)
2. Webhook triggers your automation workflow immediately (sub-second)
3. Workflow normalises field names (different forms name fields differently)
4. Parallel actions: upsert lead into CRM + append row to Google Sheets tracking log
5. Optional: send internal Slack/email notification to the sales person
6. Optional: send auto-reply to the lead confirming receipt

The result: every lead is in your CRM and tracking sheet within 5 seconds of submission, 24/7, with zero manual effort.

## Recommended tools
- **n8n** (self-hosted): best for full control, no per-operation pricing, works with any webhook
- **Make.com**: good visual builder, free tier handles 1,000 operations/month
- **Zapier**: easiest setup, but per-task pricing adds up above 100 leads/month
- **Twenty CRM**: open-source, integrates natively with n8n — good for privacy-conscious businesses

## n8n node outline
```
Webhook (POST) 
  → Set node (normalise: first_name, last_name, email, phone, message, source)
  → [Parallel branch 1] Google Sheets: Append Row
  → [Parallel branch 2] Twenty CRM: Create Person + Create Opportunity
  → [Optional] Gmail: Send auto-reply to lead
  → [Optional] Slack: Post notification to #leads channel
```

Webhook URL is generated by n8n and pasted into your form tool's webhook settings.

## ROI benchmark
At 20 leads/month, 4 min/lead manual time:
- Time saved: 1.3 hours/month
- At €20/hr: **€27/month saved**
- n8n self-hosted cost: ~€5/month (VPS) → payback in 1 month
- At 50 leads/month: **€67/month saved** — payback in days

The ROI is modest at low volume. The real win is reliability: no leads fall through the cracks when you're busy.
```

- [ ] **Step 3: Create `backend/knowledge_base/02-email-parsing.md`**

```markdown
# Pattern: Email Parsing → Extract Data, Log to Database

## Problem
Emails arrive containing structured data: order confirmations, booking requests, supplier invoices, support tickets, form submissions forwarded by other systems. Someone manually opens each email, reads it, copies the relevant fields into a spreadsheet, database, or other system.

This is error-prone (copy-paste mistakes), slow (2–5 min per email), and creates a bottleneck when volume spikes.

## Manual time cost (benchmark)
- 2–5 minutes per email to open, read, extract, paste
- 50 emails/week = 2–4 hours/week = 8–16 hours/month
- At €20/hr: €160–320/month in labour cost
- Higher-volume inboxes (200+ emails/week) can justify a €500/month automation budget

## Automation approach
1. Email trigger monitors an inbox (Gmail, IMAP, Outlook)
2. Filter: only process emails matching subject/sender pattern
3. Extract fields using one of:
   - **Regex/code**: for highly structured, consistent formats (order numbers, amounts)
   - **AI extraction**: for freeform text where field positions vary (use GPT-4o-mini, cost ~€0.001/email)
4. Write extracted data to target system: Postgres, Google Sheets, Airtable, or CRM
5. Mark email as processed (label, archive, or forward)

For order confirmations or supplier invoices with consistent formats, regex is fast and free. For freeform customer emails, a short AI prompt extracting 3–5 fields costs fractions of a cent.

## Recommended tools
- **n8n**: Email Trigger node + Code node (regex or call OpenRouter API) + database node
- **Make.com**: Email module + Text Parser or OpenAI module
- **Zapier + Formatter**: works for simple extractions; struggles with AI extraction without Zapier's premium AI tier

## n8n node outline
```
Email Trigger (IMAP / Gmail)
  → IF node (filter: subject contains "Order" or sender = orders@supplier.com)
  → Code node (extract: order_id, amount, customer_name, date — regex or AI call)
  → Postgres: Insert Row (or Google Sheets: Append Row)
  → Gmail: Add Label "processed"
```

For AI extraction, the Code node calls OpenRouter with a prompt like:
"Extract order_id, total_amount, customer_email from this email. Return JSON."

## ROI benchmark
At 50 emails/week, 3 min/email manual:
- Time saved: 2.5 hours/week = 10 hours/month
- At €20/hr: **€200/month saved**
- Automation cost: €10–30/month (n8n VPS + minimal AI API costs)
- Payback: first month

Break-even volume: ~10 emails/week at €20/hr. Below that, manual is fine.
```

- [ ] **Step 4: Create `backend/knowledge_base/03-invoice-generation.md`**

```markdown
# Pattern: Invoice/Bill Generation from Spreadsheet Rows

## Problem
At the end of the week or month, you open a spreadsheet tracking completed jobs, hours, or deliverables. For each row, you manually create an invoice in your billing tool (or worse, a Word template), fill in the client name, line items, and total, then email it as a PDF.

This is 10–20 minutes per invoice, done under time pressure at billing time, and easy to get wrong.

## Manual time cost (benchmark)
- 10–20 minutes per invoice (open tracker, create invoice, fill fields, PDF, email)
- 20 invoices/month = 3–7 hours/month
- At €25/hr: €75–175/month in labour cost, every month, forever

## Automation approach
1. Trigger: scheduled (first of month), or row-change in spreadsheet (status = "ready to invoice")
2. Read new/approved rows from Google Sheets or Airtable
3. Group by client (one invoice per client, multiple line items)
4. Generate invoice: either via API (Stripe, QuickBooks, Xero, FreeAgent) or generate HTML/PDF directly
5. Email PDF to client with standard message
6. Mark rows as "invoiced" in the tracker

The most reliable approach is integrating with your existing invoicing tool's API. If you don't use one, n8n can generate and email a clean HTML invoice converted to PDF.

## Recommended tools
- **n8n**: Cron → Google Sheets → HTTP Request (billing API) → Gmail
- **Make.com**: Scheduler → Sheets → Stripe/Xero module → Email
- **Zapier**: works well if your billing tool has a Zap (Stripe, FreshBooks, Wave)
- **Stripe**: if you already use Stripe, their invoicing API is excellent and free per invoice

## n8n node outline
```
Cron (1st of month, 9am)
  → Google Sheets: Get Rows (filter: status = "approved", invoiced = false)
  → Split In Batches (group by client_email)
  → Aggregate node (sum line items per client)
  → HTTP Request: POST to billing API (create invoice)
       OR Code node: generate invoice HTML
       → HTML to PDF conversion (external service or Puppeteer)
  → Gmail: Send invoice PDF to client
  → Google Sheets: Update Row (invoiced = true, invoice_id = ...)
```

## ROI benchmark
At 20 invoices/month, 15 min/invoice:
- Time saved: 5 hours/month
- At €25/hr: **€125/month saved**
- Setup cost: 2–4 hours one-time (€50–100 at consultant rates, or DIY)
- Automation running cost: €5–10/month
- Payback: first billing cycle

Unusually high ROI because invoicing is high-stakes (late invoices = late payments) and monthly recurring.
```

- [ ] **Step 5: Create `backend/knowledge_base/04-appointment-booking.md`**

```markdown
# Pattern: Appointment Booking → Calendar + Confirmation Email

## Problem
A client wants to book a meeting or appointment. This triggers a back-and-forth: "Are you free Tuesday?" "No, how about Thursday?" "Works for me, what time?" Then you manually add it to your calendar, then manually send a confirmation email with the meeting link, address, or preparation instructions.

For high-booking businesses (consultants, clinics, coaches, tradespeople), this is 15–30 minutes per booking of pure admin.

## Manual time cost (benchmark)
- 15–30 minutes per booking including back-and-forth and follow-up
- 20 bookings/month = 5–10 hours/month
- At €20/hr: €100–200/month
- Also: no-show rate is higher when there's no automated reminder

## Automation approach
1. Client books via self-serve tool (Calendly, Cal.com, TidyCal) — they see your real availability
2. On booking confirmation, webhook fires
3. Automation creates/updates Google Calendar event with all details
4. Sends confirmation email to client (template with meeting details, preparation instructions, address/link)
5. Sends reminder email 24h and 1h before appointment
6. Optional: adds client to CRM, creates follow-up task after appointment

The self-serve booking tool eliminates the back-and-forth entirely. The automation handles the admin downstream.

## Recommended tools
- **Calendly** (booking tool) + **n8n** or **Make.com** (automation) — most common setup
- **Cal.com** (open-source Calendly alternative): works same way, self-hostable
- **TidyCal**: cheaper Calendly alternative, supports webhooks
- **Twenty CRM**: can be auto-updated with booking data via n8n

## n8n node outline
```
Webhook (Calendly / Cal.com booking.created event)
  → Set node (extract: attendee_name, email, start_time, end_time, meeting_type)
  → Google Calendar: Create Event (add client as attendee)
  → Gmail: Send confirmation (template with meeting details + preparation notes)
  → Wait node (trigger 24h before start_time)
  → Gmail: Send reminder
  → [Optional] Twenty CRM: Create/Update Contact + Create Activity
```

For the reminder, use n8n's Wait node set to a calculated time (start_time minus 24 hours).

## ROI benchmark
At 20 bookings/month, 20 min/booking saved:
- Time saved: 6.7 hours/month
- At €20/hr: **€134/month saved**
- Also: ~20% reduction in no-shows from automated reminders (estimated value: 4 bookings/year recovered)
- Calendly free tier covers up to 1 meeting type; Cal.com is fully free self-hosted
- Total cost: €5–15/month → payback in weeks
```

- [ ] **Step 6: Create `backend/knowledge_base/05-client-onboarding.md`**

```markdown
# Pattern: New Client Onboarding (Welcome Email, Folder Creation, CRM Entry)

## Problem
A new client signs a contract or pays a deposit. Now you need to: send a welcome email with next steps and their portal login, create a project folder in Google Drive (with the right subfolder structure), add them to your CRM, assign them to a project management tool, and possibly send a questionnaire.

This takes 45–90 minutes per client and happens when you're most excited (just closed a deal) but also most busy. Steps get skipped.

## Manual time cost (benchmark)
- 45–90 minutes per new client across all onboarding steps
- 4 new clients/month = 3–6 hours/month
- At €30/hr (higher rate because this is high-trust, client-facing work): €90–180/month
- Risk: missed steps damage the first impression with a new client

## Automation approach
1. Trigger: contract signed (PandaDoc/DocuSign webhook), payment received (Stripe webhook), or manual trigger via CRM deal stage change
2. All downstream steps run in parallel:
   - Create Google Drive folder from template structure
   - Set folder sharing permissions for client email
   - Send welcome email (template with project timeline, portal link, first steps)
   - Create CRM contact + project record
   - Create project in project management tool (Notion, Linear, Asana)
   - Send onboarding questionnaire (Typeform)
3. Internal notification to you with summary of what was set up

The most important insight: these steps are independent and can all run simultaneously. What takes 90 minutes manually takes 30 seconds automated.

## Recommended tools
- **n8n**: best for multi-system integrations; handles Google Drive, Gmail, CRM, Typeform natively
- **Make.com**: visual builder, good for teams who want visibility into the workflow
- **Zapier**: works but multi-step + parallel branches require their premium tier
- **Trigger via**: PandaDoc, DocuSign, Stripe, or a simple form (Tally/Typeform) that you fill yourself

## n8n node outline
```
Webhook (contract signed or Stripe payment.succeeded)
  → Set node (extract: client_name, client_email, project_name, start_date)
  → [Parallel branch 1] Google Drive: Copy Template Folder → Rename → Share with client_email
  → [Parallel branch 2] Gmail: Send welcome email (template)
  → [Parallel branch 3] Twenty CRM: Create Person + Create Opportunity (status: active)
  → [Parallel branch 4] Notion/Linear: Create Project with client details
  → [Parallel branch 5] Typeform: Create response collector or send invite link
  → Wait for all branches (Merge node)
  → Gmail: Send internal summary to yourself
```

## ROI benchmark
At 4 new clients/month, 75 min/client:
- Time saved: 5 hours/month
- At €30/hr: **€150/month saved**
- One-time setup: 3–5 hours
- Running cost: €5–10/month
- Payback: first month
- Hidden benefit: consistent, professional onboarding increases perceived value — hard to price but real
```

- [ ] **Step 7: Create `backend/knowledge_base/06-social-media-scheduling.md`**

```markdown
# Pattern: Social Media Post Scheduling

## Problem
Consistent social media presence requires posting at regular intervals. Most small businesses either post inconsistently (when they remember) or spend 2–4 hours/week manually composing and publishing to each platform. Platforms have different formats, character limits, and optimal posting times.

The manual approach also means everything stops when you're on holiday, sick, or just busy with client work.

## Manual time cost (benchmark)
- Writing + formatting + posting to 2–3 platforms: 30–60 min per post
- 3 posts/week across 2 platforms = 3–6 hours/week = 12–24 hours/month
- At €20/hr: €240–480/month in time cost
- Opportunity cost: consistent posting drives organic reach; inconsistency tanks algorithm performance

## Automation approach
1. Maintain a content calendar in Google Sheets or Notion (title, text, image URL, scheduled date, platforms)
2. Scheduled trigger checks for posts due today
3. For each post: retrieve text and image, format for each platform
4. Publish via platform APIs (Twitter/X, LinkedIn, Facebook, Instagram via Meta API)
5. Mark as posted in the calendar
6. Optional: pull performance metrics back into the sheet 48h later

This separates content creation (your creative work, done in batches) from publishing (a mechanical task the automation handles).

## Recommended tools
- **n8n**: best for direct API access to all major platforms without per-post fees
- **Make.com**: good alternative, has native social media modules
- **Buffer/Hootsuite**: dedicated scheduling tools (simpler setup but ongoing per-post costs)
- **Zapier**: works but per-task pricing makes high-volume posting expensive
- Note: Instagram and TikTok have restrictive API terms for automation — verify before building

## n8n node outline
```
Cron (daily, 8am)
  → Google Sheets: Get Rows (filter: publish_date = today, status = "approved")
  → Loop over each post:
      → [Branch: Twitter/X] HTTP Request: POST to Twitter API v2
      → [Branch: LinkedIn] HTTP Request: POST to LinkedIn API
      → [Branch: Facebook] Facebook Graph API node
      → Google Sheets: Update Row (status = "posted", posted_at = timestamp)
  → [Optional, 48h later] Cron → fetch engagement metrics → update sheet
```

Image handling: store images in Google Drive or an S3 bucket; include the public URL in the sheet. n8n fetches the image as binary data and includes it in the API request.

## ROI benchmark
At 3 posts/week × 2 platforms, 45 min/post manual:
- Time saved: 2.25 hours/week = 9 hours/month
- At €20/hr: **€180/month saved**
- n8n self-hosted cost: €5–10/month shared with other workflows
- Payback: less than 2 weeks
- Additional benefit: batch content creation is cognitively cheaper than daily context-switching
```

- [ ] **Step 8: Commit**

```bash
git add backend/knowledge_base/
git rm backend/knowledge_base/.gitkeep 2>/dev/null || true
git commit -m "feat: add 6 static automation pattern KB files"
```

---

## Task 3: `rag/ingest.py` — static KB ingestion (TDD)

**Files:**
- Create: `backend/app/rag/ingest.py`
- Create: `backend/tests/test_ingest.py` (unit tests only in this task)

The ingest pipeline: for each MD file in `knowledge_base/`, compute sha256 of content, get embedding via OpenAI API, upsert to `documents` table (skip if same hash, update if hash changed, insert if new).

- [ ] **Step 1: Write the failing unit tests**

Create `backend/tests/test_ingest.py`:

```python
import hashlib
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# --- Unit tests (no DB, no API) ---

def test_compute_content_hash_is_sha256():
    from app.rag.ingest import compute_content_hash

    content = "Hello, world!"
    expected = hashlib.sha256(content.encode()).hexdigest()
    assert compute_content_hash(content) == expected


def test_compute_content_hash_same_input_same_output():
    from app.rag.ingest import compute_content_hash

    content = "deterministic content"
    assert compute_content_hash(content) == compute_content_hash(content)


def test_compute_content_hash_different_inputs_differ():
    from app.rag.ingest import compute_content_hash

    assert compute_content_hash("a") != compute_content_hash("b")


def test_load_kb_files_returns_list_of_dicts(tmp_path):
    """load_kb_files reads .md files and returns title + content dicts."""
    from app.rag.ingest import load_kb_files

    # Create two test MD files in tmp_path
    (tmp_path / "01-test-a.md").write_text("# Pattern: Test A\n\nContent A.")
    (tmp_path / "02-test-b.md").write_text("# Pattern: Test B\n\nContent B.")

    docs = load_kb_files(tmp_path)

    assert len(docs) == 2
    titles = [d["title"] for d in docs]
    assert "Pattern: Test A" in titles
    assert "Pattern: Test B" in titles


def test_load_kb_files_title_extracted_from_h1(tmp_path):
    """Title is the text of the first H1 heading (strip '# ')."""
    from app.rag.ingest import load_kb_files

    (tmp_path / "01-test.md").write_text("# Pattern: My Pattern\n\nSome content here.")
    docs = load_kb_files(tmp_path)
    assert docs[0]["title"] == "Pattern: My Pattern"


def test_load_kb_files_content_is_full_file_text(tmp_path):
    """content field is the full markdown text."""
    from app.rag.ingest import load_kb_files

    text = "# Pattern: Full\n\n## Problem\nSome problem here."
    (tmp_path / "01-full.md").write_text(text)
    docs = load_kb_files(tmp_path)
    assert docs[0]["content"] == text


def test_load_kb_files_empty_dir_returns_empty_list(tmp_path):
    from app.rag.ingest import load_kb_files

    docs = load_kb_files(tmp_path)
    assert docs == []


@pytest.mark.asyncio
async def test_ingest_static_kb_calls_embed_for_each_new_doc():
    """ingest_static_kb calls embed_text once per document when none are in DB yet."""
    from app.rag import ingest

    fake_docs = [
        {"title": "Pattern: A", "content": "content A"},
        {"title": "Pattern: B", "content": "content B"},
    ]
    fake_embedding = [0.1] * 1536

    with (
        patch.object(ingest, "load_kb_files", return_value=fake_docs),
        patch.object(ingest, "embed_text", new=AsyncMock(return_value=fake_embedding)) as mock_embed,
        patch.object(ingest, "_upsert_document", new=AsyncMock()) as mock_upsert,
    ):
        result = await ingest.ingest_static_kb()

    assert mock_embed.call_count == 2
    assert mock_upsert.call_count == 2
    assert result["ingested"] == 2


@pytest.mark.asyncio
async def test_ingest_static_kb_returns_summary_dict():
    from app.rag import ingest

    fake_docs = [{"title": "Pattern: X", "content": "content X"}]

    with (
        patch.object(ingest, "load_kb_files", return_value=fake_docs),
        patch.object(ingest, "embed_text", new=AsyncMock(return_value=[0.0] * 1536)),
        patch.object(ingest, "_upsert_document", new=AsyncMock()),
    ):
        result = await ingest.ingest_static_kb()

    assert "ingested" in result
    assert "skipped" in result
    assert isinstance(result["ingested"], int)
    assert isinstance(result["skipped"], int)
```

- [ ] **Step 2: Run tests — expect ImportError**

```bash
cd backend
uv run pytest tests/test_ingest.py -v
```

Expected: `ModuleNotFoundError: No module named 'app.rag.ingest'`

- [ ] **Step 3: Create `backend/app/rag/ingest.py`**

```python
import hashlib
import json
from pathlib import Path

from openai import AsyncOpenAI
from sqlalchemy import text

from app.config import get_settings
from app.db import AsyncSessionLocal


def compute_content_hash(content: str) -> str:
    return hashlib.sha256(content.encode()).hexdigest()


def load_kb_files(kb_dir: Path | None = None) -> list[dict]:
    if kb_dir is None:
        kb_dir = Path(__file__).parent.parent.parent.parent / "knowledge_base"
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
    return response.data[0].embedding


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
            await session.execute(
                text(
                    "INSERT INTO documents (title, content, embedding, metadata, content_hash) "
                    "VALUES (:title, :content, :embedding::vector, :metadata::jsonb, :hash)"
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

        if row.content_hash == content_hash:
            return "skipped"

        await session.execute(
            text(
                "UPDATE documents SET content=:content, embedding=:embedding::vector, "
                "metadata=:metadata::jsonb, content_hash=:hash WHERE id=:id"
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
```

- [ ] **Step 4: Run unit tests — expect PASS**

```bash
uv run pytest tests/test_ingest.py -v -k "not integration"
```

Expected: all unit tests pass (the `integration` marker tests don't exist yet, so all current tests run).

- [ ] **Step 5: Commit**

```bash
git add backend/app/rag/ingest.py backend/tests/test_ingest.py
git commit -m "feat: add rag/ingest.py — static KB ingestion with sha256 idempotency"
```

---

## Task 4: `rag/retriever.py` — cosine similarity search (TDD)

**Files:**
- Create: `backend/app/rag/retriever.py`
- Modify: `backend/tests/test_ingest.py` (add retriever unit tests)

The retriever executes a pgvector cosine similarity query against the `documents` table and returns the top-k results with similarity scores.

- [ ] **Step 1: Add retriever unit tests to `backend/tests/test_ingest.py`**

Append to the end of `backend/tests/test_ingest.py`:

```python
# --- Retriever unit tests ---

@pytest.mark.asyncio
async def test_search_documents_returns_list():
    """search_documents returns a list (may be empty if DB is empty or unavailable)."""
    from app.rag.retriever import search_documents

    query_embedding = [0.1] * 1536

    with patch("app.rag.retriever.AsyncSessionLocal") as mock_session_cls:
        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_session_cls.return_value = mock_session

        mock_result = MagicMock()
        mock_result.mappings.return_value.all.return_value = []
        mock_session.execute = AsyncMock(return_value=mock_result)

        results = await search_documents(query_embedding, top_k=2)

    assert isinstance(results, list)


@pytest.mark.asyncio
async def test_search_documents_passes_correct_top_k():
    """search_documents passes top_k to the SQL query."""
    from app.rag.retriever import search_documents

    query_embedding = [0.0] * 1536

    with patch("app.rag.retriever.AsyncSessionLocal") as mock_session_cls:
        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_session_cls.return_value = mock_session

        mock_result = MagicMock()
        mock_result.mappings.return_value.all.return_value = []
        mock_session.execute = AsyncMock(return_value=mock_result)

        await search_documents(query_embedding, top_k=5)

    call_kwargs = mock_session.execute.call_args[0][1]
    assert call_kwargs["top_k"] == 5


@pytest.mark.asyncio
async def test_search_documents_result_has_expected_keys():
    """Each result dict has title, content, metadata, similarity keys."""
    from app.rag.retriever import search_documents

    query_embedding = [0.1] * 1536
    fake_row = {"title": "T", "content": "C", "metadata": {}, "similarity": 0.9}

    with patch("app.rag.retriever.AsyncSessionLocal") as mock_session_cls:
        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_session_cls.return_value = mock_session

        mock_result = MagicMock()
        mock_result.mappings.return_value.all.return_value = [fake_row]
        mock_session.execute = AsyncMock(return_value=mock_result)

        results = await search_documents(query_embedding, top_k=1)

    assert len(results) == 1
    assert set(results[0].keys()) == {"title", "content", "metadata", "similarity"}
```

- [ ] **Step 2: Run tests — expect ImportError**

```bash
uv run pytest tests/test_ingest.py -v -k "search_documents"
```

Expected: `ModuleNotFoundError: No module named 'app.rag.retriever'`

- [ ] **Step 3: Create `backend/app/rag/retriever.py`**

```python
from sqlalchemy import text

from app.db import AsyncSessionLocal


async def search_documents(query_embedding: list[float], top_k: int = 2) -> list[dict]:
    """Return the top_k documents most similar to query_embedding (cosine similarity)."""
    vector_str = "[" + ",".join(str(v) for v in query_embedding) + "]"
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            text(
                "SELECT title, content, metadata, "
                "1 - (embedding <=> :embedding::vector) AS similarity "
                "FROM documents "
                "ORDER BY embedding <=> :embedding::vector "
                "LIMIT :top_k"
            ),
            {"embedding": vector_str, "top_k": top_k},
        )
        rows = result.mappings().all()
        return [dict(row) for row in rows]
```

- [ ] **Step 4: Run retriever unit tests — expect PASS**

```bash
uv run pytest tests/test_ingest.py -v
```

Expected: all tests pass (unit tests for ingest + retriever).

- [ ] **Step 5: Commit**

```bash
git add backend/app/rag/retriever.py backend/tests/test_ingest.py
git commit -m "feat: add rag/retriever.py — async cosine similarity search via pgvector"
```

---

## Task 5: `rag/live_ingester.py` — live docs ingestion (TDD)

**Files:**
- Create: `backend/app/rag/live_ingester.py`
- Modify: `backend/tests/test_ingest.py` (add live ingester tests)

The live ingester fetches documentation pages from 5 external tools, strips HTML nav/footer, chunks at 800 tokens / 100 overlap, then embeds and upserts each chunk. Source URLs are hardcoded (per handover §3: "prefer hardcoding over env vars").

- [ ] **Step 1: Add live ingester tests to `backend/tests/test_ingest.py`**

Append to the end of `backend/tests/test_ingest.py`:

```python
# --- Live ingester unit tests ---

def test_chunk_text_single_chunk_when_below_limit():
    """Text shorter than max_tokens is returned as a single chunk."""
    from app.rag.live_ingester import chunk_text

    short_text = "This is a short text."
    chunks = chunk_text(short_text, max_tokens=800, overlap=100)
    assert len(chunks) == 1
    assert chunks[0] == short_text


def test_chunk_text_splits_long_text():
    """Text exceeding max_tokens is split into multiple chunks."""
    from app.rag.live_ingester import chunk_text

    # Generate ~1600 tokens worth of text (approx 1 token per 4 chars, so ~6400 chars)
    long_text = "word " * 2000  # ~2000 tokens
    chunks = chunk_text(long_text, max_tokens=800, overlap=100)
    assert len(chunks) >= 2


def test_chunk_text_chunks_overlap():
    """Successive chunks share overlap_tokens tokens at their boundary."""
    from app.rag.live_ingester import chunk_text

    long_text = "word " * 2000
    chunks = chunk_text(long_text, max_tokens=800, overlap=100)
    # The end of chunk[0] should share content with the start of chunk[1]
    # (not a perfect test but confirms chunks are not disjoint)
    assert len(chunks) >= 2
    # Verify total unique content is less than sum of chunk sizes (overlap exists)
    total_chars = sum(len(c) for c in chunks)
    original_chars = len(long_text)
    assert total_chars > original_chars  # overlap means total > original


def test_strip_html_removes_nav_and_footer():
    from app.rag.live_ingester import strip_html

    html = """
    <html><body>
    <nav>Nav content here</nav>
    <main><p>Useful content here</p></main>
    <footer>Footer stuff</footer>
    </body></html>
    """
    result = strip_html(html)
    assert "Nav content here" not in result
    assert "Footer stuff" not in result
    assert "Useful content here" in result


def test_strip_html_returns_plain_text():
    from app.rag.live_ingester import strip_html

    html = "<p>Hello <b>world</b></p>"
    result = strip_html(html)
    assert "<" not in result
    assert "Hello" in result
    assert "world" in result


@pytest.mark.asyncio
async def test_ingest_live_docs_fetches_each_source():
    """ingest_live_docs makes an HTTP GET for each configured source URL."""
    from app.rag import live_ingester

    fake_html = "<html><body><main><p>" + ("word " * 100) + "</p></main></body></html>"
    source_count = len(live_ingester.LIVE_SOURCES)

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.text = fake_html

    with (
        patch.object(live_ingester, "embed_text", new=AsyncMock(return_value=[0.1] * 1536)),
        patch.object(live_ingester, "_upsert_document", new=AsyncMock(return_value="inserted")),
        patch("httpx.AsyncClient") as mock_client_cls,
    ):
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client_cls.return_value = mock_client

        result = await live_ingester.ingest_live_docs()

    assert mock_client.get.call_count == source_count
    assert "ingested" in result
    assert "skipped" in result
    assert "errors" in result


@pytest.mark.asyncio
async def test_ingest_live_docs_handles_fetch_error_gracefully():
    """A network error on one source does not abort the whole run."""
    from app.rag import live_ingester

    with (
        patch.object(live_ingester, "embed_text", new=AsyncMock(return_value=[0.1] * 1536)),
        patch.object(live_ingester, "_upsert_document", new=AsyncMock(return_value="inserted")),
        patch("httpx.AsyncClient") as mock_client_cls,
    ):
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(side_effect=Exception("network error"))
        mock_client_cls.return_value = mock_client

        result = await live_ingester.ingest_live_docs()

    assert result["errors"] > 0
    assert result["ingested"] == 0
```

- [ ] **Step 2: Run tests — expect ImportError**

```bash
uv run pytest tests/test_ingest.py -v -k "live_ingester or chunk_text or strip_html"
```

Expected: `ModuleNotFoundError: No module named 'app.rag.live_ingester'`

- [ ] **Step 3: Create `backend/app/rag/live_ingester.py`**

```python
import json
import logging
from datetime import UTC, datetime

import httpx
import tiktoken
from bs4 import BeautifulSoup

from app.rag.ingest import compute_content_hash, embed_text, _upsert_document

logger = logging.getLogger(__name__)

# Documentation pages to index. One URL per entry; title is used as document title prefix.
LIVE_SOURCES: list[dict] = [
    {
        "url": "https://docs.n8n.io/integrations/",
        "title_prefix": "n8n Docs: Integrations Index",
        "tool": "n8n",
    },
    {
        "url": "https://docs.n8n.io/integrations/builtin/core-nodes/",
        "title_prefix": "n8n Docs: Core Nodes Reference",
        "tool": "n8n",
    },
    {
        "url": "https://docs.twenty.com/start/getting-started-with-twenty",
        "title_prefix": "Twenty CRM Docs: Getting Started",
        "tool": "twenty_crm",
    },
    {
        "url": "https://docs.twenty.com/developers/webhooks",
        "title_prefix": "Twenty CRM Docs: Webhooks & Automation",
        "tool": "twenty_crm",
    },
    {
        "url": "https://www.make.com/en/help/scenarios/creating-a-scenario",
        "title_prefix": "Make.com Docs: Creating a Scenario",
        "tool": "make",
    },
    {
        "url": "https://help.zapier.com/hc/en-us/articles/8496197478157",
        "title_prefix": "Zapier Docs: Create Zaps",
        "tool": "zapier",
    },
    {
        "url": "https://docs.lovable.dev/introduction",
        "title_prefix": "Lovable Docs: Introduction",
        "tool": "lovable",
    },
]


def strip_html(html: str) -> str:
    """Remove nav, footer, header, script, and style tags; return plain text."""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["nav", "footer", "header", "script", "style", "aside"]):
        tag.decompose()
    return soup.get_text(separator=" ", strip=True)


def chunk_text(text: str, max_tokens: int = 800, overlap: int = 100) -> list[str]:
    """Split text into chunks of max_tokens with overlap tokens between chunks."""
    enc = tiktoken.get_encoding("cl100k_base")
    tokens = enc.encode(text)

    if len(tokens) <= max_tokens:
        return [text]

    chunks = []
    start = 0
    while start < len(tokens):
        end = min(start + max_tokens, len(tokens))
        chunk_tokens = tokens[start:end]
        chunks.append(enc.decode(chunk_tokens))
        if end == len(tokens):
            break
        start = end - overlap

    return chunks


async def ingest_live_docs() -> dict:
    """Fetch, chunk, embed, and upsert live documentation pages."""
    ingested = skipped = errors = 0
    fetched_at = datetime.now(UTC).isoformat()

    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        for source in LIVE_SOURCES:
            try:
                response = await client.get(source["url"])
                response.raise_for_status()
                plain_text = strip_html(response.text)

                if len(plain_text.strip()) < 100:
                    logger.warning("Skipping %s — too little text after stripping", source["url"])
                    errors += 1
                    continue

                chunks = chunk_text(plain_text, max_tokens=800, overlap=100)

                for i, chunk in enumerate(chunks):
                    title = f"{source['title_prefix']} (chunk {i + 1}/{len(chunks)})"
                    content_hash = compute_content_hash(chunk)
                    embedding = await embed_text(chunk)
                    metadata = {
                        "source": "live",
                        "tool": source["tool"],
                        "url": source["url"],
                        "fetched_at": fetched_at,
                        "chunk_index": i,
                        "total_chunks": len(chunks),
                    }
                    status = await _upsert_document(
                        title=title,
                        content=chunk,
                        embedding=embedding,
                        metadata=metadata,
                        content_hash=content_hash,
                    )
                    if status == "skipped":
                        skipped += 1
                    else:
                        ingested += 1

            except Exception as exc:
                logger.error("Failed to ingest %s: %s", source["url"], exc)
                errors += 1

    return {"ingested": ingested, "skipped": skipped, "errors": errors}
```

- [ ] **Step 4: Run live ingester tests — expect PASS**

```bash
uv run pytest tests/test_ingest.py -v
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add backend/app/rag/live_ingester.py backend/tests/test_ingest.py
git commit -m "feat: add rag/live_ingester.py — fetch, chunk, embed live tool docs"
```

---

## Task 6: `/admin/ingest` endpoint (TDD)

**Files:**
- Modify: `backend/app/main.py`
- Modify: `backend/tests/test_health.py` (add admin endpoint tests)

The endpoint accepts `X-Admin-Key` header and `?source=static|live` query param. Defaults to `static` if param omitted.

- [ ] **Step 1: Write failing tests**

Append to the end of `backend/tests/test_health.py`:

```python
# --- /admin/ingest endpoint tests ---

from unittest.mock import AsyncMock, patch


@pytest.mark.asyncio
async def test_admin_ingest_requires_api_key():
    """POST /admin/ingest without X-Admin-Key returns 401."""
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post("/admin/ingest")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_admin_ingest_rejects_wrong_key():
    """POST /admin/ingest with wrong key returns 403."""
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post("/admin/ingest", headers={"X-Admin-Key": "wrong-key"})
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_admin_ingest_static_calls_ingest_static_kb():
    """POST /admin/ingest?source=static with correct key calls ingest_static_kb."""
    from app.config import get_settings

    correct_key = get_settings().admin_api_key
    mock_result = {"ingested": 6, "skipped": 0}

    with patch("app.main.ingest_static_kb", new=AsyncMock(return_value=mock_result)):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/admin/ingest?source=static",
                headers={"X-Admin-Key": correct_key},
            )

    assert response.status_code == 200
    data = response.json()
    assert data["source"] == "static"
    assert data["result"]["ingested"] == 6


@pytest.mark.asyncio
async def test_admin_ingest_defaults_to_static():
    """POST /admin/ingest without ?source param defaults to static mode."""
    from app.config import get_settings

    correct_key = get_settings().admin_api_key

    with patch("app.main.ingest_static_kb", new=AsyncMock(return_value={"ingested": 0, "skipped": 6})):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/admin/ingest",
                headers={"X-Admin-Key": correct_key},
            )

    assert response.status_code == 200
    assert response.json()["source"] == "static"


@pytest.mark.asyncio
async def test_admin_ingest_live_calls_ingest_live_docs():
    """POST /admin/ingest?source=live with correct key calls ingest_live_docs."""
    from app.config import get_settings

    correct_key = get_settings().admin_api_key
    mock_result = {"ingested": 14, "skipped": 0, "errors": 0}

    with patch("app.main.ingest_live_docs", new=AsyncMock(return_value=mock_result)):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/admin/ingest?source=live",
                headers={"X-Admin-Key": correct_key},
            )

    assert response.status_code == 200
    data = response.json()
    assert data["source"] == "live"
    assert data["result"]["ingested"] == 14


@pytest.mark.asyncio
async def test_admin_ingest_invalid_source_returns_422():
    """POST /admin/ingest?source=bogus returns 422 (invalid query param value)."""
    from app.config import get_settings

    correct_key = get_settings().admin_api_key

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/admin/ingest?source=bogus",
            headers={"X-Admin-Key": correct_key},
        )

    assert response.status_code == 422
```

- [ ] **Step 2: Run tests — expect failure (endpoint not yet created)**

```bash
uv run pytest tests/test_health.py -v -k "admin_ingest"
```

Expected: tests fail with 404 or ImportError (the endpoint doesn't exist yet).

- [ ] **Step 3: Update `backend/app/main.py`**

Replace the full contents of `backend/app/main.py`:

```python
from typing import Literal

from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.rag.ingest import ingest_static_kb
from app.rag.live_ingester import ingest_live_docs

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
```

- [ ] **Step 4: Run all admin ingest tests — expect PASS**

```bash
uv run pytest tests/test_health.py -v
```

Expected: all tests pass (health + admin ingest).

- [ ] **Step 5: Commit**

```bash
git add backend/app/main.py backend/tests/test_health.py
git commit -m "feat: add /admin/ingest endpoint with X-Admin-Key auth and static/live modes"
```

---

## Task 7: Integration test — ingest doc, query back, similarity > 0.7

**Files:**
- Create: `backend/tests/test_rag_integration.py`

This test requires a live Postgres with pgvector. It inserts a known embedding vector directly (bypassing the OpenAI API), then queries it via the retriever, and verifies the similarity score exceeds 0.7.

**Requires:** Docker Postgres running (`docker compose up -d postgres`). Skip if unavailable.

- [ ] **Step 1: Check if Docker Postgres is running before writing the test**

```bash
docker ps --filter "name=rmerge-ae-postgres-1" --format "{{.Names}}: {{.Status}}"
```

If not running, start it (do NOT run `docker compose up -d` blindly — check first):

```bash
docker ps -a
```

If `rmerge-ae-postgres-1` exists but is stopped, start only postgres:

```bash
docker compose start postgres
```

If it doesn't exist at all (fresh machine), start it:

```bash
docker compose up -d postgres
```

Wait for it to be healthy:

```bash
docker compose ps
```

Expected: `rmerge-ae-postgres-1` shows `running (healthy)`.

- [ ] **Step 2: Add `pytest.ini_options` markers to `backend/pyproject.toml`**

The existing `[tool.pytest.ini_options]` section in `backend/pyproject.toml` looks like:

```toml
[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
```

Update it to:

```toml
[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
markers = [
    "integration: requires live Postgres with pgvector (docker compose up -d postgres)",
]
```

- [ ] **Step 3: Create `backend/tests/test_rag_integration.py`**

```python
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
                "VALUES (:title, :content, :embedding::vector, :metadata::jsonb, :hash)"
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
                    "VALUES (:title, :content, :embedding::vector, :metadata::jsonb, :hash)"
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
    from pathlib import Path
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
```

- [ ] **Step 4: Run integration tests — expect PASS**

```bash
cd backend
uv run pytest tests/test_rag_integration.py -v -m integration
```

Expected output:
```
tests/test_rag_integration.py::test_ingest_and_retrieve_similarity_above_threshold PASSED
tests/test_rag_integration.py::test_retriever_returns_most_similar_first PASSED
tests/test_rag_integration.py::test_upsert_idempotency_skips_same_content PASSED

3 passed
```

If you see `similarity = -1.0` or strange values, the `<=>` operator result is the distance (0 = identical). The retriever computes `1 - distance` — verify this is correct in `retriever.py`.

- [ ] **Step 5: Run full unit test suite — confirm no regressions**

```bash
uv run pytest -v -m "not integration"
```

Expected: all existing tests pass (13 original + new unit tests from tasks 3–6).

- [ ] **Step 6: Commit**

```bash
git add backend/tests/test_rag_integration.py backend/pyproject.toml
git commit -m "test: add RAG integration tests — ingest + retrieval + similarity threshold"
```

---

## Task 8: Final test run + session wrap-up

- [ ] **Step 1: Run full unit test suite**

```bash
cd backend
uv run pytest -v -m "not integration"
```

All tests must pass. Fix any failures before proceeding.

- [ ] **Step 2: Run integration tests (requires Docker Postgres)**

```bash
uv run pytest tests/test_rag_integration.py -v -m integration
```

All 3 must pass.

- [ ] **Step 3: Verify the knowledge_base directory has 6 files**

```bash
ls backend/knowledge_base/
```

Expected: 6 `.md` files, no `.gitkeep`.

- [ ] **Step 4: Verify the rag package structure**

```bash
ls backend/app/rag/
```

Expected: `__init__.py`, `ingest.py`, `live_ingester.py`, `retriever.py`

- [ ] **Step 5: Smoke test the admin endpoint**

Start the server (in a separate terminal):

```bash
cd backend
uv run uvicorn app.main:app --reload --port 8000
```

Check that the `/admin/ingest` endpoint appears in Swagger:
Open `http://localhost:8000/docs` — the endpoint should be listed under the `admin` tag.

Verify 401 without key:
```bash
curl -s -o /dev/null -w "%{http_code}" -X POST http://localhost:8000/admin/ingest
```
Expected: `401`

- [ ] **Step 6: Manual task for Roman — call `/admin/ingest` and verify DB**

This step is for you to do manually after the session:

```bash
# Set your actual admin key (from backend/.env)
ADMIN_KEY="your_admin_api_key_here"

# Ingest static KB
curl -X POST "http://localhost:8000/admin/ingest?source=static" \
  -H "X-Admin-Key: $ADMIN_KEY" \
  -H "Content-Type: application/json"
```

Expected response: `{"source": "static", "result": {"ingested": 6, "skipped": 0}}`

Then verify documents appear in the database:

```bash
docker compose exec postgres psql -U automate -d automate_this \
  -c "SELECT title, metadata->>'source' as source FROM documents ORDER BY title;"
```

Expected: 6 rows with `source = static`.

---

## Spec Self-Review

**Spec §5.1 — 6 static KB files:** ✅ Tasks 2 creates all 6 with exact template structure  
**Spec §5.2 — live ingester:** ✅ Task 5 implements `live_ingester.py` with all 5 sources  
**Spec §5.2 — 800/100 token chunking:** ✅ `chunk_text()` in `live_ingester.py`  
**Spec §5.2 — `metadata.source="live"` + `fetched_at`:** ✅ metadata dict in `_upsert_document` call  
**Spec §5 — static and live indistinguishable to agent:** ✅ both go into same `documents` table  
**Spec §3.1 — `/admin/ingest` with API key:** ✅ Task 6 uses `X-Admin-Key` header  
**Spec §3.1 — `?source=live|static`:** ✅ FastAPI `Literal` query param  
**Spec §11 — retriever.py:** ✅ Task 4 creates `retriever.py` with `search_documents()`  
**Spec §11 — unit test similarity > 0.7:** ✅ Task 7 integration test  
**Spec §6 — idempotent via content_hash:** ✅ `_upsert_document()` logic  
**Handover §2.3 — `VECTOR(1536)` constraint:** ✅ always use `text-embedding-3-small`, documented  
**Handover §4 — Docker pre-flight:** ✅ Task 7 Step 1 checks `docker ps` before any compose commands  

**Architectural decisions documented in plan header** — embedding model, chunking strategy, retriever approach — ✅
