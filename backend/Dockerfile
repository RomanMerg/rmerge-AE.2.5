FROM python:3.12-slim

# Install uv — Astral's fast Python package manager
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

# Copy dependency manifests first so Docker re-uses this layer on code-only changes
COPY pyproject.toml uv.lock* ./

# Install production dependencies only (no pytest, httpx, etc.)
RUN uv sync --no-dev --frozen

# Copy application source
COPY app/ ./app/

EXPOSE 8000

CMD ["uv", "run", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
