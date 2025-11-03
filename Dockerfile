
FROM python:3.11-slim

WORKDIR /app

# Ensure Python output is unbuffered for immediate log visibility in Celery workers
ENV PYTHONUNBUFFERED=1

# Install system dependencies
RUN apt-get update && apt-get install -y \
    postgresql-client \
    libpq-dev \
    gcc \
    g++ \
    libmagic1 \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first for better caching
COPY requirements.txt .
ENV PIP_DEFAULT_TIMEOUT=120
RUN pip install --no-cache-dir --default-timeout=120 -r requirements.txt

# Copy application code
COPY . .

# Create non-root user (will be used if privilege drop works)
RUN useradd -u 1000 -m appuser && chown -R appuser:appuser /app

# Create necessary directories
RUN mkdir -p /app/data/uploads /app/data/vector_db && chown -R appuser:appuser /app/data

# Compile translations (.po -> .mo)
RUN pybabel compile -d app/translations -f || true

EXPOSE 5000

# Use plain gevent worker; flask-sock + simple-websocket handles handshake itself.
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "-k", "gevent", "--workers", "2", "--worker-connections", "1000", "--timeout", "120", "wsgi:app"]
