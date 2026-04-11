FROM ghcr.io/astral-sh/uv:python3.11-bookworm-slim

WORKDIR /app

# Keep installs reproducible and avoid symlink surprises inside containers.
ENV UV_COMPILE_BYTECODE=1
ENV UV_LINK_MODE=copy

# Install dependencies from the project metadata and lockfile.
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
COPY server.py ./
RUN uv sync --frozen --no-dev --extra server

# Expose port
EXPOSE 5011

# Run server
ENV WEB_CONCURRENCY=4
CMD ["sh", "-c", "uv run gunicorn server:app -k uvicorn.workers.UvicornWorker --bind ${HOST:-0.0.0.0}:${PORT:-8000} --workers ${WEB_CONCURRENCY:-4}"]
