# syntax=docker/dockerfile:1
FROM python:3.13-alpine@sha256:9ef1a599f863231c3d91e941389bfdc4a2d6070557243d54df1a4e57015b960e AS builder
COPY --from=ghcr.io/astral-sh/uv:0.11.26@sha256:3d868e555f8f1dbc324afa005066cd11e1053fc4743b9808ca8025283e65efa5 /uv /usr/local/bin/uv
WORKDIR /app
ENV UV_HTTP_TIMEOUT=120
RUN apk add --no-cache git ca-certificates
COPY pyproject.toml uv.lock LICENSE NOTICE ./
COPY src src
RUN uv sync --frozen --no-dev --no-editable
RUN rm -rf /app/.venv/share/jupyter
RUN --network=none .venv/bin/python -c "import aiuditor_service.app"
FROM python:3.13-alpine@sha256:9ef1a599f863231c3d91e941389bfdc4a2d6070557243d54df1a4e57015b960e
WORKDIR /app
COPY --from=builder /app/.venv /app/.venv
COPY docker-entrypoint.sh /docker-entrypoint.sh
COPY LICENSE NOTICE /usr/share/licenses/aiuditor/
ARG SOURCE_SHA
ARG BUILT_AT
LABEL org.opencontainers.image.licenses="Apache-2.0" \
      org.opencontainers.image.source="https://github.com/hyperme-sh/aiuditor" \
      org.opencontainers.image.revision="${SOURCE_SHA}"
ENV AIUDITOR_SOURCE_SHA=${SOURCE_SHA} GIT_SHA=${SOURCE_SHA} BUILT_AT=${BUILT_AT} PYTHONDONTWRITEBYTECODE=1
USER 65532:65532
ENTRYPOINT ["/bin/sh", "/docker-entrypoint.sh"]
CMD ["/app/.venv/bin/python", "-m", "aiuditor_service.main"]
