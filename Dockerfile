# PrismPipe — gnu/glibc (python:slim).
FROM python:3.11-slim

WORKDIR /app

COPY pyproject.toml poetry.lock requirements.txt README.md ./
COPY src ./src
COPY migrations ./migrations
COPY server.py .

RUN pip install --no-cache-dir -r requirements.txt \
    && pip install --no-cache-dir -e ".[all]"

EXPOSE 5011

ENV WEB_CONCURRENCY=2
# Redis-backed ComputationGraph shares warm hits across workers (COMPUTATION_REDIS_URL/REDIS_URL).
# Override down to 1 only if Redis is unavailable.
ENV GUNICORN_TIMEOUT=120
ENV COMPUTATION_CACHE_TTL_S=30
ENV PORT=5011
ENV HOST=0.0.0.0
CMD ["sh", "-c", "gunicorn server:app -k uvicorn.workers.UvicornWorker --bind ${HOST:-0.0.0.0}:${PORT:-5011} --workers ${WEB_CONCURRENCY:-2} --timeout ${GUNICORN_TIMEOUT:-120}"]