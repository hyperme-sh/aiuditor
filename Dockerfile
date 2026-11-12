# syntax=docker/dockerfile:1
FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim@sha256:531f855bda2c73cd6ef67d56b733b357cea384185b3022bd09f05e002cd144ca AS builder
WORKDIR /app
ENV UV_HTTP_TIMEOUT=120
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates && rm -rf /var/lib/apt/lists/*
COPY pyproject.toml uv.lock ./
COPY src src
RUN uv sync --frozen --no-dev --no-editable
RUN --network=none .venv/bin/python -c "import aiuditor_service.app"
FROM python:3.13-slim-bookworm@sha256:281d39ff02e3b389401586134ff9e6572954687822a58055ec07a60ded4e0b7f
WORKDIR /app
COPY --from=builder /app/.venv /app/.venv
COPY docker-entrypoint.sh /docker-entrypoint.sh
ARG SOURCE_SHA
ARG BUILT_AT
LABEL org.opencontainers.image.source="https://github.com/hyperme-sh/aiuditor" \
      org.opencontainers.image.revision="${SOURCE_SHA}"
ENV AIUDITOR_SOURCE_SHA=${SOURCE_SHA} GIT_SHA=${SOURCE_SHA} BUILT_AT=${BUILT_AT} PYTHONDONTWRITEBYTECODE=1
USER 65532:65532
ENTRYPOINT ["/bin/sh", "/docker-entrypoint.sh"]
CMD ["/app/.venv/bin/python", "-m", "aiuditor_service.main"]
