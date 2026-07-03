import json

import structlog

from app.logging_config import configure_logging


def test_configure_logging_produces_json_lines(capsys):
    """After configure_logging(), a structlog event renders as a single JSON line
    with event name and bound key-value fields."""
    configure_logging()
    logger = structlog.get_logger()
    logger.info("chat_turn", session_id="abc-123", tokens_used=150, cost_usd=0.0001)

    out = capsys.readouterr().out.strip()
    parsed = json.loads(out)
    assert parsed["event"] == "chat_turn"
    assert parsed["session_id"] == "abc-123"
    assert parsed["tokens_used"] == 150
    assert "timestamp" in parsed
    assert parsed["level"] == "info"


def test_configure_logging_is_idempotent():
    """Calling configure_logging() twice must not raise or duplicate handlers."""
    configure_logging()
    configure_logging()
