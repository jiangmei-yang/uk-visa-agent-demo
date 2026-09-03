FROM python:3.12-slim
WORKDIR /app
ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
RUN useradd --create-home --uid 10001 appuser \
    && pip install --no-cache-dir uv==0.12.5
COPY pyproject.toml uv.lock README.md /app/
RUN uv sync --frozen --no-dev --no-install-project
COPY . /app
RUN uv sync --frozen --no-dev \
    && mkdir -p /app/runtime \
    && chown -R appuser:appuser /app/runtime
USER appuser
EXPOSE 8000
HEALTHCHECK --interval=5s --timeout=3s --start-period=20s --retries=12 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2)"
CMD ["sh", "-c", "visa-agent demo --reset && exec visa-agent web --host 0.0.0.0 --port 8000"]
