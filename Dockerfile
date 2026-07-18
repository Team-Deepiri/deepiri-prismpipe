FROM python:3.11-slim

WORKDIR /app


# Bedd runtime (Bun-style) — glibc binary
ARG BEDD_IMAGE=ghcr.io/team-deepiri/bedd:0.6
COPY --from=${BEDD_IMAGE} /usr/local/bin/bedd /usr/local/bin/bedd
COPY --from=${BEDD_IMAGE} /opt/bedd/skills /opt/bedd/skills
ENV BEDD_SKILLS_DIR=/opt/bedd/skills


# Install dependencies
COPY pyproject.toml .
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN pip install --no-cache-dir -e ".[all]"

# Copy server
COPY server.py .

# Expose port
EXPOSE 5011

# Run server
ENV WEB_CONCURRENCY=4
CMD ["sh", "-c", "gunicorn server:app -k uvicorn.workers.UvicornWorker --bind ${HOST:-0.0.0.0}:${PORT:-8000} --workers ${WEB_CONCURRENCY:-4}"]
