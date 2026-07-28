# PrismPipe — gnu/glibc (python:slim). Embed Bedd via multi-stage FROM.
# Default: published GHCR image. Compose passes BEDD_IMAGE (see x-bedd-build-args).
# Do not retag a local build as ghcr.io/team-deepiri/bedd:* — that shadows pulls.
ARG BEDD_IMAGE=ghcr.io/team-deepiri/bedd:0.8
FROM ${BEDD_IMAGE} AS bedd

FROM python:3.11-slim

WORKDIR /app

# Bedd runtime (Bun-style) — glibc binary for debian/python:slim
COPY --from=bedd /usr/local/bin/bedd /usr/local/bin/bedd
COPY --from=bedd /opt/bedd/skills /opt/bedd/skills
ENV BEDD_SKILLS_DIR=/opt/bedd/skills
ENV BEDD_BUS_URL=redis://redis:6379
ENV BEDD_DLQ_STREAM=bedd.dlq

COPY pyproject.toml poetry.lock requirements.txt README.md ./
COPY src ./src
COPY migrations ./migrations
COPY server.py .

RUN pip install --no-cache-dir -r requirements.txt \
    && pip install --no-cache-dir -e ".[all]" \
    && pip install --no-cache-dir "fastapi>=0.110" "uvicorn[standard]>=0.27" "gunicorn>=22" \
    && bedd help >/dev/null

EXPOSE 5011

ENV WEB_CONCURRENCY=1
# Single worker until ComputationGraph is shared across processes (in-memory dedup).
# Override via compose/env only after Redis-backed computation sharing lands.
ENV GUNICORN_TIMEOUT=120
ENV PORT=5011
ENV HOST=0.0.0.0
CMD ["sh", "-c", "gunicorn server:app -k uvicorn.workers.UvicornWorker --bind ${HOST:-0.0.0.0}:${PORT:-5011} --workers ${WEB_CONCURRENCY:-1} --timeout ${GUNICORN_TIMEOUT:-120}"]
