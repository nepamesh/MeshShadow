FROM python:3.12-slim

WORKDIR /app

# Install system deps for matplotlib
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Create data directories and non-root user
RUN mkdir -p /app/data/map_cache \
    && useradd -r -s /bin/false appuser \
    && chown -R appuser:appuser /app/data

USER appuser

EXPOSE 5000

ENV PYTHONUNBUFFERED=1
ENV DB_PATH=/app/data/meshprop.db
ENV CACHE_DIR=/app/data/map_cache

CMD ["python", "main.py"]
