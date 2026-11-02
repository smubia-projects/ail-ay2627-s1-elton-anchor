# Anchor's API for Cloud Run (AI Lodge demo): FastAPI only, the pages are on Vercel (web/, build.js).
# Copies only what the API imports: web/'s API modules and the scripts/ modules the answering route
# shares with the eval (prompts, checker, number check, router, chunker). The eval pipeline, its
# models (Docling, the reranker) and the course files stay out of the image.
FROM python:3.13-slim

WORKDIR /app

COPY requirements-web.txt .
RUN pip install --no-cache-dir -r requirements-web.txt

# scripts/chunk.py counts tokens with cl100k_base: fetch it at build time, not on the first upload.
ENV TIKTOKEN_CACHE_DIR=/app/.tiktoken
RUN python -c "import tiktoken; tiktoken.get_encoding('cl100k_base')"

COPY scripts/openai_client.py scripts/answering.py scripts/pictures.py scripts/checker.py \
     scripts/number_check.py scripts/calc.py scripts/router.py scripts/chunk.py scripts/
COPY web/app.py web/db.py web/ingest.py web/pipeline.py web/partial_json.py web/retrieval.py \
     web/rate_limit.py web/rate_limit_engine.py web/

RUN useradd -m -u 1001 appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 8080
ENV PYTHONUNBUFFERED=1

HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/api/health', timeout=5)" || exit 1

CMD exec uvicorn app:app --app-dir web --host 0.0.0.0 --port ${PORT:-8080}
