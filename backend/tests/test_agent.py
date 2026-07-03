"""Unit tests for the LangGraph agent graph — LLM mocked, InMemorySaver checkpointer."""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from app.agent import build_agent_graph, turn_input


def _make_ai_message(content=None, tool_calls=None, usage_metadata=None):
    """Minimal AIMessage stand-in: .content, .tool_calls, .usage_metadata.
    Uses a real AIMessage so add_messages/checkpointing serialization works."""
    from langchain_core.messages import AIMessage

    return AIMessage(
        content=content or "",
        tool_calls=tool_calls or [],
        usage_metadata=usage_metadata
        if usage_metadata is not None
        else {"input_tokens": 10, "output_tokens": 20, "total_tokens": 30},
    )


def _graph_and_config():
    graph = build_agent_graph(InMemorySaver())
    config = {"configurable": {"thread_id": str(uuid.uuid4())}, "recursion_limit": 10}
    return graph, config


def _patch_llm(side_effect):
    """Patch app.agent.ChatOpenAI so .bind_tools().ainvoke() yields side_effect in order."""
    mock_cls = patch("app.agent.ChatOpenAI").start()
    mock_cls.return_value.bind_tools.return_value.ainvoke = AsyncMock(side_effect=side_effect)
    return mock_cls


@pytest.fixture(autouse=True)
def _stop_patches():
    yield
    patch.stopall()


async def test_no_tool_calls_returns_reply_and_zero_sources():
    _patch_llm([_make_ai_message(content="Just tell me more about your process.")])
    graph, config = _graph_and_config()

    result = await graph.ainvoke(turn_input("hi"), config)

    assert result["messages"][-1].content == "Just tell me more about your process."
    assert result["sources"] == []
    assert result["tools_called"] == []
    assert result["input_tokens"] == 10
    assert result["output_tokens"] == 20


async def test_roi_tool_round_trip():
    tool_call = {
        "name": "calculate_roi",
        "args": {"hours_saved_per_week": 5, "hourly_rate": 30, "setup_cost": 1000},
        "id": "call_1",
    }
    _patch_llm([
        _make_ai_message(tool_calls=[tool_call]),
        _make_ai_message(content="You'd save 7800/year."),
    ])
    graph, config = _graph_and_config()

    result = await graph.ainvoke(turn_input("5h/week at 30/h, setup 1000"), config)

    assert result["messages"][-1].content == "You'd save 7800/year."
    assert result["tools_called"] == ["calculate_roi"]
    # tokens from both LLM calls accumulated
    assert result["input_tokens"] == 20
    assert result["output_tokens"] == 40


async def test_search_tool_populates_sources_and_embedding_tokens():
    fake_sources = [{"title": "Pattern A", "similarity": 0.9}]
    tool_call = {
        "name": "search_automation_patterns",
        "args": {"task_description": "x" * 40},
        "id": "call_2",
    }
    _patch_llm([
        _make_ai_message(tool_calls=[tool_call]),
        _make_ai_message(content="Based on Pattern A, use n8n."),
    ])
    graph, config = _graph_and_config()

    with patch(
        "app.agent.search_automation_patterns",
        new=AsyncMock(return_value={"formatted": "### Pattern A\n...", "sources": fake_sources}),
    ):
        result = await graph.ainvoke(turn_input("what to automate?"), config)

    assert result["sources"] == fake_sources
    # 10+10 LLM input + 40/4=10 estimated embedding tokens
    assert result["input_tokens"] == 30


async def test_multi_round_tool_loop():
    """The new capability vs the old 2-call loop: LLM can request tools twice in one turn."""
    call_a = {"name": "calculate_roi", "args": {"hours_saved_per_week": 2, "hourly_rate": 50, "setup_cost": 500}, "id": "a"}
    call_b = {"name": "calculate_roi", "args": {"hours_saved_per_week": 4, "hourly_rate": 50, "setup_cost": 500}, "id": "b"}
    _patch_llm([
        _make_ai_message(tool_calls=[call_a]),
        _make_ai_message(tool_calls=[call_b]),
        _make_ai_message(content="Comparing both scenarios: ..."),
    ])
    graph, config = _graph_and_config()

    result = await graph.ainvoke(turn_input("compare 2h vs 4h saved"), config)

    assert result["messages"][-1].content == "Comparing both scenarios: ..."
    assert result["tools_called"] == ["calculate_roi", "calculate_roi"]


async def test_malformed_tool_args_do_not_crash():
    """Missing/None required arg from the LLM -> error fed back as ToolMessage, not an exception."""
    bad_call = {
        "name": "calculate_roi",
        "args": {"hours_saved_per_week": 3, "hourly_rate": None, "setup_cost": 200},
        "id": "bad",
    }
    _patch_llm([
        _make_ai_message(tool_calls=[bad_call]),
        _make_ai_message(content="Could you share your hourly rate?"),
    ])
    graph, config = _graph_and_config()

    result = await graph.ainvoke(turn_input("3h a week on posts"), config)

    assert result["messages"][-1].content == "Could you share your hourly rate?"


async def test_unknown_tool_returns_error_result():
    ghost_call = {"name": "ghost_tool", "args": {}, "id": "g"}
    _patch_llm([
        _make_ai_message(tool_calls=[ghost_call]),
        _make_ai_message(content="Sorry, let me try again."),
    ])
    graph, config = _graph_and_config()

    result = await graph.ainvoke(turn_input("hi"), config)

    tool_msgs = [m for m in result["messages"] if m.type == "tool"]
    assert "Unknown tool" in tool_msgs[-1].content


async def test_second_turn_sees_first_turn_history_and_resets_counters():
    """Checkpointer memory: turn 2 on the same thread_id includes turn 1 messages,
    but per-turn counters reset."""
    llm_mock = _patch_llm([
        _make_ai_message(content="First reply."),
        _make_ai_message(content="Second reply."),
    ])
    graph, config = _graph_and_config()

    r1 = await graph.ainvoke(turn_input("first question"), config)
    r2 = await graph.ainvoke(turn_input("second question"), config)

    # per-turn counters reset between turns (not cumulative across the session)
    assert r1["input_tokens"] == 10 and r2["input_tokens"] == 10
    # the second LLM call received the full prior history (system + h1 + a1 + h2 = 4 messages)
    second_call_messages = llm_mock.return_value.bind_tools.return_value.ainvoke.call_args_list[1].args[0]
    contents = [m.content for m in second_call_messages]
    assert "first question" in contents
    assert "First reply." in contents
    assert "second question" in contents


async def test_capture_lead_dispatched():
    lead_call = {
        "name": "capture_lead",
        "args": {"name": "Jane", "email": "j@x.com", "company": "X", "pain_point": "invoicing"},
        "id": "l",
    }
    _patch_llm([
        _make_ai_message(tool_calls=[lead_call]),
        _make_ai_message(content="Saved, thanks Jane."),
    ])
    graph, config = _graph_and_config()

    with patch("app.agent.capture_lead", new=AsyncMock(return_value={"status": "created", "person_id": "1"})) as lead_mock:
        result = await graph.ainvoke(turn_input("Jane, j@x.com, X, invoicing"), config)

    lead_mock.assert_awaited_once_with(name="Jane", email="j@x.com", company="X", pain_point="invoicing")
    assert result["tools_called"] == ["capture_lead"]
