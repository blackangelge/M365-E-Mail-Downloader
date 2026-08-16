# syntax=docker/dockerfile:1

FROM python:3.12-slim AS builder

WORKDIR /build

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential libpq-dev \
    && rm -rf /var/lib/apt/lists/*

RUN python -m venv /venv
ENV PATH="/venv/bin:$PATH"

COPY pyproject.toml ./
# Kein Lockfile-Format vorgegeben: Dependencies werden aus pyproject.toml installiert.
RUN pip install --no-cache-dir --upgrade pip && pip install --no-cache-dir .


FROM python:3.12-slim AS runtime

RUN apt-get update && apt-get install -y --no-install-recommends \
        libpq5 curl \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 1000 appuser

COPY --from=builder /venv /venv
ENV PATH="/venv/bin:$PATH" PYTHONUNBUFFERED=1

WORKDIR /app
COPY app ./app
COPY migrations ./migrations
COPY alembic.ini ./
COPY scripts ./scripts

RUN mkdir -p /data/Download && chown -R appuser:appuser /app /data

USER appuser

EXPOSE 4000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -f http://localhost:4000/healthz || exit 1

ENTRYPOINT ["scripts/entrypoint.sh"]
