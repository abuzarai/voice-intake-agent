# Use Python 3.11 slim image
FROM python:3.11-slim

# Install uv (standalone binary; pin for reproducibility)
COPY --from=ghcr.io/astral-sh/uv:0.5.14 /uv /uvx /bin/

# Set working directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Copy dependency files first for better caching
COPY pyproject.toml uv.lock ./

# Install Python dependencies from locked graph
RUN uv sync --frozen --no-dev --no-install-project

# Copy application code
COPY app/ ./app/

# Create non-root user for security
RUN useradd -m -u 1000 appuser && chown -R appuser:appuser /app
USER appuser

# Prefer the project venv (uv sync created .venv)
ENV PATH="/app/.venv/bin:${PATH}"

# Expose port (Cloud Run will set PORT env var)
EXPOSE 8000

# Health check (matches FastAPI /api/v1/health)
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/v1/health', timeout=2)" || exit 1

# Start application (Cloud Run sets PORT env var)
CMD ["sh", "-c", "python -m uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]