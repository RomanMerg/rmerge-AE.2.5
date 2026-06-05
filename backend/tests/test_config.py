from app.config import settings


def test_settings_has_openrouter_api_key():
    assert settings.openrouter_api_key == "test-key-for-ci"


def test_settings_has_database_url():
    assert "postgresql" in settings.database_url


def test_settings_default_chat_model():
    assert settings.chat_model == "openai/gpt-4o-mini"


def test_settings_allowed_origins_list_is_a_list():
    origins = settings.allowed_origins_list
    assert isinstance(origins, list)
    assert len(origins) >= 1


def test_settings_max_turns_default():
    assert settings.max_turns_per_session == 8
