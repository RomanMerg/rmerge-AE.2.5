"""Langfuse tracing — env-gated. No keys in settings -> everything is a no-op.

Langfuse v3 SDK: constructing Langfuse(...) once registers the singleton
client; CallbackHandler() then picks it up. Keys come from get_settings()
(pydantic .env), NOT os.environ — the SDK's own env-var lookup would miss
them because pydantic-settings never exports to os.environ.
"""

from functools import lru_cache

from app.config import get_settings


@lru_cache(maxsize=1)
def _init_langfuse():
    """Initialise the Langfuse client singleton once, or None when disabled."""
    settings = get_settings()
    if not (settings.langfuse_public_key and settings.langfuse_secret_key):
        return None
    import langfuse

    return langfuse.Langfuse(
        public_key=settings.langfuse_public_key,
        secret_key=settings.langfuse_secret_key,
        host=settings.langfuse_host,
    )


def get_langfuse_callbacks() -> list:
    """LangChain callbacks for graph config — [CallbackHandler] or [] when disabled."""
    if _init_langfuse() is None:
        return []
    import langfuse.langchain

    return [langfuse.langchain.CallbackHandler()]
