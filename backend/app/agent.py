"""LangGraph agent: explicit StateGraph replacing main.py's bounded 2-call loop.

Graph shape:  START -> agent -> (tool_calls? tools : END),  tools -> agent
History is owned by the checkpointer (thread_id = session_id). Per-turn
channels (sources, tools_called, token/cost counters) use overwrite reducers
and are reset by turn_input() each turn so checkpointed values from the
previous turn never leak into this turn's API response.
"""

import json
from typing import Annotated, TypedDict

from langchain_core.messages import AnyMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_openai import ChatOpenAI
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages

from app.config import get_settings
from app.cost_tracker import calculate_cost, estimate_embedding_tokens
from app.tools.roi import calculate_roi
from app.tools.search import search_automation_patterns
from mcp_server.server import capture_lead

# --- Tool schemas + system prompt: MOVED VERBATIM from app/main.py ---
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
# ---------------------------------------------------------------------


class AgentState(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]
    # Per-turn channels (overwrite reducer, reset by turn_input each turn):
    sources: list[dict]
    tools_called: list[str]
    input_tokens: int
    output_tokens: int
    cost_usd: float


def turn_input(message: str) -> dict:
    """Invoke input for one user turn — appends the message, resets per-turn channels."""
    return {
        "messages": [HumanMessage(content=message)],
        "sources": [],
        "tools_called": [],
        "input_tokens": 0,
        "output_tokens": 0,
        "cost_usd": 0.0,
    }


def _usage_tokens(ai_message) -> tuple[int, int]:
    """Extract (input_tokens, output_tokens) from an AIMessage's usage_metadata, defaulting to 0."""
    usage = getattr(ai_message, "usage_metadata", None) or {}
    return usage.get("input_tokens", 0), usage.get("output_tokens", 0)


async def agent_node(state: AgentState) -> dict:
    """One LLM call. System prompt prepended at call time (not checkpointed)."""
    settings = get_settings()
    llm = ChatOpenAI(
        model=settings.chat_model,
        api_key=settings.openrouter_api_key,
        base_url=settings.openrouter_base_url,
        max_tokens=settings.max_output_tokens,
    ).bind_tools([CALCULATE_ROI_TOOL, SEARCH_AUTOMATION_PATTERNS_TOOL, CAPTURE_LEAD_TOOL])

    ai_message = await llm.ainvoke([SystemMessage(content=SYSTEM_PROMPT)] + state["messages"])
    in_tok, out_tok = _usage_tokens(ai_message)
    return {
        "messages": [ai_message],
        "input_tokens": state["input_tokens"] + in_tok,
        "output_tokens": state["output_tokens"] + out_tok,
        "cost_usd": state["cost_usd"] + calculate_cost(settings.chat_model, in_tok, out_tok),
    }


async def tools_node(state: AgentState) -> dict:
    """Execute every tool call from the last AIMessage. Tool errors are fed back
    as ToolMessages so the LLM can recover — never raised (a malformed LLM tool
    call must not 500 the request; real bug, regression-tested)."""
    settings = get_settings()
    last = state["messages"][-1]
    tool_messages: list[ToolMessage] = []
    sources = state["sources"]
    tools_called = list(state["tools_called"])
    input_tokens = state["input_tokens"]
    cost_usd = state["cost_usd"]

    for tool_call in last.tool_calls:
        tool_name = tool_call["name"]
        tool_args = tool_call["args"]
        tools_called.append(tool_name)
        try:
            if tool_name == "calculate_roi":
                tool_result = calculate_roi(**tool_args)
            elif tool_name == "search_automation_patterns":
                search_result = await search_automation_patterns(**tool_args)
                sources = search_result["sources"]
                tool_result = search_result["formatted"]
                embedding_tokens = estimate_embedding_tokens(tool_args.get("task_description", ""))
                input_tokens += embedding_tokens
                cost_usd += calculate_cost(settings.embedding_model, embedding_tokens, 0)
            elif tool_name == "capture_lead":
                tool_result = await capture_lead(**tool_args)
            else:
                tool_result = {"status": "error", "detail": f"Unknown tool {tool_name}"}
        except Exception as e:
            tool_result = {"status": "error", "detail": f"{tool_name} failed: {e}"}

        tool_messages.append(
            ToolMessage(content=json.dumps(tool_result), tool_call_id=tool_call["id"])
        )

    return {
        "messages": tool_messages,
        "sources": sources,
        "tools_called": tools_called,
        "input_tokens": input_tokens,
        "cost_usd": cost_usd,
    }


def should_continue(state: AgentState) -> str:
    last = state["messages"][-1]
    return "tools" if getattr(last, "tool_calls", None) else END


def build_agent_graph(checkpointer):
    """Compile the agent graph. Checkpointer is injected: AsyncPostgresSaver in
    production (main.py lifespan), InMemorySaver in tests."""
    graph = StateGraph(AgentState)
    graph.add_node("agent", agent_node)
    graph.add_node("tools", tools_node)
    graph.add_edge(START, "agent")
    graph.add_conditional_edges("agent", should_continue, {"tools": "tools", END: END})
    graph.add_edge("tools", "agent")
    return graph.compile(checkpointer=checkpointer)
