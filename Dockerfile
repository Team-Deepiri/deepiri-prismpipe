FROM python:3.11-slim

WORKDIR /app

# Install dependencies
COPY pyproject.toml .
RUN pip install --no-cache-dir -e ".[all]"

# Copy server
COPY server.py .

# Expose port
EXPOSE 5011

# Run server
CMD ["python", "server.py"]
