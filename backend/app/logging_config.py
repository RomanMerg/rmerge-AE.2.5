"""Structured JSON logging via structlog.

One JSON object per line to stdout — greppable locally, parseable by any
log aggregator later (Render, Grafana Loki, etc.). Call configure_logging()
once at app startup; it is idempotent.
"""

import logging

import structlog


def configure_logging() -> None:
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        cache_logger_on_first_use=False,  # keep False so tests/capsys see reconfiguration
    )
