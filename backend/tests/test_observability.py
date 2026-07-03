from unittest.mock import MagicMock, patch

from app import observability
from app.observability import get_langfuse_callbacks


def _settings_mock(public="", secret=""):
    s = MagicMock()
    s.langfuse_public_key = public
    s.langfuse_secret_key = secret
    s.langfuse_host = "https://cloud.langfuse.com"
    return s


def setup_function():
    observability._init_langfuse.cache_clear()


def test_no_keys_returns_empty_callbacks():
    with patch("app.observability.get_settings", return_value=_settings_mock()):
        assert get_langfuse_callbacks() == []


def test_keys_present_returns_callback_handler():
    with (
        patch("app.observability.get_settings", return_value=_settings_mock("pk", "sk")),
        patch("langfuse.Langfuse") as langfuse_cls,
        patch("langfuse.langchain.CallbackHandler") as handler_cls,
    ):
        callbacks = get_langfuse_callbacks()

    langfuse_cls.assert_called_once_with(
        public_key="pk", secret_key="sk", host="https://cloud.langfuse.com"
    )
    assert callbacks == [handler_cls.return_value]


def test_langfuse_client_initialised_once():
    with (
        patch("app.observability.get_settings", return_value=_settings_mock("pk", "sk")),
        patch("langfuse.Langfuse") as langfuse_cls,
        patch("langfuse.langchain.CallbackHandler"),
    ):
        get_langfuse_callbacks()
        get_langfuse_callbacks()

    langfuse_cls.assert_called_once()
