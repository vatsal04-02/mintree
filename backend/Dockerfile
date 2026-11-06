# Backend image: API, Celery worker, outbox publisher and beat all use this image with different commands (see docker-compose.yml).
FROM python:3.13-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /srv/backend
COPY requirements.lock.txt .
RUN pip install -r requirements.lock.txt
COPY alembic.ini ./
COPY app ./app
COPY migrations ./migrations
COPY scripts ./scripts
RUN useradd --system --uid 10001 --home /srv mintree && mkdir -p /srv/storage && chown -R mintree /srv
USER mintree
ENV MINTREE_STORAGE_ROOT=/srv/storage
EXPOSE 8000
# Default: the API. 2 workers x pool (see docs/SCALABILITY_STRATEGY.md for the connection budget before changing either number).
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2", "--proxy-headers", "--forwarded-allow-ips", "*"]
