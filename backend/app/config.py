from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # LLM
    openrouter_api_key: str
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    chat_model: str = "openai/gpt-4o-mini"
    embedding_model: str = "openai/text-embedding-3-small"

    # Database
    database_url: str

    # Security
    admin_api_key: str
    allowed_origins: str = "http://localhost:3000"

    # Rate limiting
    max_turns_per_session: int = 8
    max_input_chars: int = 600
    max_output_tokens: int = 450

    # Local dev Ollama
    ollama_base_url: str = "http://localhost:11434"
    ollama_embedding_model: str = "nomic-embed-text"

    # Twenty CRM (for capture_lead MCP tool)
    twenty_api_key: str = ""
    twenty_base_url: str = "http://localhost:3001"

    @property
    def allowed_origins_list(self) -> list[str]:
        return [o.strip() for o in self.allowed_origins.split(",")]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
