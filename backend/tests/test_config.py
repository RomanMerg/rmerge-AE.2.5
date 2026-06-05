import pytest

from app.config import Settings, get_settings


def test_settings_has_openrouter_api_key():
    assert get_settings().openrouter_api_key == "test-key-for-ci"


def test_settings_has_database_url():
    assert "postgresql" in get_settings().database_url


def test_settings_default_chat_model():
    assert get_settings().chat_model == "openai/gpt-4o-mini"


def test_settings_allowed_origins_list_is_a_list():
    origins = get_settings().allowed_origins_list
    assert isinstance(origins, list)
    assert len(origins) >= 1


def test_settings_max_turns_default():
    assert get_settings().max_turns_per_session == 8


def test_settings_type_coercion_max_turns():
    """Pydantic must coerce string env var to int."""
    import os
    os.environ["MAX_TURNS_PER_SESSION"] = "15"
    get_settings.cache_clear()
    try:
        s = get_settings()
        assert s.max_turns_per_session == 15
        assert isinstance(s.max_turns_per_session, int)
    finally:
        del os.environ["MAX_TURNS_PER_SESSION"]
        get_settings.cache_clear()


def test_settings_allowed_origins_list_multi_value():
    """allowed_origins_list must split comma-separated string and strip whitespace."""
    import os
    os.environ["ALLOWED_ORIGINS"] = "http://a.com, http://b.com"
    get_settings.cache_clear()
    try:
        s = get_settings()
        assert s.allowed_origins_list == ["http://a.com", "http://b.com"]
    finally:
        del os.environ["ALLOWED_ORIGINS"]
        get_settings.cache_clear()


def test_settings_missing_required_field_raises():
    """Settings must raise ValidationError when a required field is absent."""
    import os
    from pydantic import ValidationError
    key = os.environ.pop("OPENROUTER_API_KEY", None)
    get_settings.cache_clear()
    try:
        with pytest.raises(ValidationError):
            Settings()
    finally:
        if key is not None:
            os.environ["OPENROUTER_API_KEY"] = key
        get_settings.cache_clear()


def test_db_module_imports_and_engine_exists():
    """Engine construction from a valid DATABASE_URL format must not raise."""
    from app.db import AsyncSessionLocal, engine  # noqa: F401 — import is the assertion

    assert engine is not None
    assert AsyncSessionLocal is not None
