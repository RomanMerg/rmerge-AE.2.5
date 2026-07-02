from app.cost_tracker import calculate_cost, estimate_embedding_tokens


def test_calculate_cost_known_chat_model():
    # gpt-4o-mini: input 0.15/1M, output 0.60/1M
    cost = calculate_cost("openai/gpt-4o-mini", input_tokens=1_000_000, output_tokens=1_000_000)
    assert cost == 0.15 + 0.60


def test_calculate_cost_zero_tokens():
    assert calculate_cost("openai/gpt-4o-mini", 0, 0) == 0.0


def test_calculate_cost_unknown_model_returns_zero():
    assert calculate_cost("some/unknown-model", 1000, 1000) == 0.0


def test_calculate_cost_embedding_model_has_no_output_cost():
    # embeddings pricing has output=0.0, so output_tokens shouldn't affect cost
    cost = calculate_cost("openai/text-embedding-3-small", input_tokens=1_000_000, output_tokens=1_000_000)
    assert cost == 0.02


def test_estimate_embedding_tokens_basic():
    assert estimate_embedding_tokens("a" * 400) == 100


def test_estimate_embedding_tokens_minimum_one():
    assert estimate_embedding_tokens("") == 1
    assert estimate_embedding_tokens("hi") == 1
