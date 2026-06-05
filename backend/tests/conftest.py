import os

# Set required env vars BEFORE app modules are imported.
os.environ.setdefault("OPENROUTER_API_KEY", "test-key-for-ci")
os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+asyncpg://automate:automate@localhost:5432/automate_this",
)
os.environ.setdefault("ADMIN_API_KEY", "test-admin-key")
os.environ.setdefault("ALLOWED_ORIGINS", "http://localhost:3000")
