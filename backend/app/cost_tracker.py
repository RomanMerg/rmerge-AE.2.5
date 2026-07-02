MODEL_PRICING = {
    # USD per 1M tokens. Verify against https://openrouter.ai/models periodically — these drift.
    "openai/gpt-4o-mini": {"input": 0.15, "output": 0.60},
    "openai/text-embedding-3-small": {"input": 0.02, "output": 0.0},
}


def calculate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """Estimate USD cost for a single API call. Unknown models cost 0.0."""
    pricing = MODEL_PRICING.get(model, {"input": 0.0, "output": 0.0})
    input_cost = (input_tokens / 1_000_000) * pricing["input"]
    output_cost = (output_tokens / 1_000_000) * pricing["output"]
    return input_cost + output_cost


def estimate_embedding_tokens(text: str) -> int:
    """Rough token estimate (~4 chars/token) — embed_text() doesn't expose real usage."""
    return max(1, len(text) // 4)
