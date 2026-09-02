FROM python:3.12-slim
WORKDIR /app
COPY . /app
RUN pip install --no-cache-dir uv==0.12.5 && uv sync --frozen --no-dev
ENV PATH="/app/.venv/bin:${PATH}"
EXPOSE 8000
HEALTHCHECK --interval=5s --timeout=3s --start-period=20s --retries=12 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2)"
CMD ["sh", "-c", "visa-agent demo --reset && exec visa-agent web --host 0.0.0.0 --port 8000"]
