FROM python:3.12-slim

# Install uv — pinned to a specific version for reproducible builds
COPY --from=ghcr.io/astral-sh/uv:0.11.19 /uv /usr/local/bin/uv

WORKDIR /app

# Copy dependency manifests first so Docker re-uses this layer on code-only changes
COPY pyproject.toml uv.lock* ./

# Install production dependencies only (no pytest, httpx, etc.)
RUN uv sync --no-dev --frozen

# Copy application source
COPY app/ ./app/

# Create non-root user and set ownership of app directory
RUN adduser --disabled-password --gecos "" appuser && \
    chown -R appuser:appuser /app

USER appuser

EXPOSE 8000

CMD ["uv", "run", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
