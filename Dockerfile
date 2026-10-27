FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.13.1 /uv /bin/uv
WORKDIR /app
ENV UV_COMPILE_BYTECODE=0 PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY app/ ./app/
RUN find /app/app -type d -exec chmod 755 {} + \
    && useradd --uid 10001 --no-create-home live
USER 10001:10001
ENV PATH="/app/.venv/bin:$PATH" HOME=/tmp
EXPOSE 8000
CMD ["python", "-m", "uvicorn", "app.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--no-access-log", "--log-level", "critical", "--timeout-graceful-shutdown", "45", "--no-proxy-headers"]
