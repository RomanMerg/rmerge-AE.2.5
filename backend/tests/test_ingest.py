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
