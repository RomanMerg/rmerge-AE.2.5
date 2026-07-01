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
