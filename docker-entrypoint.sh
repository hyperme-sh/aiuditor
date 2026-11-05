#!/bin/sh
set -eu
/app/.venv/bin/python -m aiuditor_service.schema_startup
exec "$@"
