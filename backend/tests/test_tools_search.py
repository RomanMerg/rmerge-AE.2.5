from unittest.mock import AsyncMock, patch

import pytest


def _fake_docs(n=3):
    return [
        {"title": f"Doc {i}", "content": f"content {i}" * 20, "metadata": {}, "similarity": 0.9 - i * 0.1}
        for i in range(n)
    ]


@pytest.mark.asyncio
async def test_search_automation_patterns_returns_formatted_and_sources():
    from app.tools.search import search_automation_patterns

    with (
        patch("app.tools.search.embed_text", new=AsyncMock(return_value=[0.0] * 1536)),
        patch("app.tools.search.search_documents", new=AsyncMock(return_value=_fake_docs(3))),
    ):
        result = await search_automation_patterns("How do I automate invoicing?")

    assert set(result.keys()) == {"formatted", "sources"}
    assert len(result["sources"]) == 3
    for source in result["sources"]:
        assert set(source.keys()) == {"title", "similarity"}


@pytest.mark.asyncio
async def test_search_automation_patterns_formatted_contains_doc_titles():
    from app.tools.search import search_automation_patterns

    with (
        patch("app.tools.search.embed_text", new=AsyncMock(return_value=[0.0] * 1536)),
        patch("app.tools.search.search_documents", new=AsyncMock(return_value=_fake_docs(2))),
    ):
        result = await search_automation_patterns("Automate onboarding")

    assert "Doc 0" in result["formatted"]
    assert "Doc 1" in result["formatted"]


@pytest.mark.asyncio
async def test_search_automation_patterns_calls_embed_text_with_task_description():
    from app.tools.search import search_automation_patterns

    with (
        patch("app.tools.search.embed_text", new=AsyncMock(return_value=[0.0] * 1536)) as mock_embed,
        patch("app.tools.search.search_documents", new=AsyncMock(return_value=[])),
    ):
        await search_automation_patterns("Automate lead capture")

    mock_embed.assert_called_once_with("Automate lead capture")


@pytest.mark.asyncio
async def test_search_automation_patterns_empty_results_returns_empty_lists():
    from app.tools.search import search_automation_patterns

    with (
        patch("app.tools.search.embed_text", new=AsyncMock(return_value=[0.0] * 1536)),
        patch("app.tools.search.search_documents", new=AsyncMock(return_value=[])),
    ):
        result = await search_automation_patterns("Something obscure")

    assert result["formatted"] == ""
    assert result["sources"] == []
