#!/bin/sh
set -eu
/app/.venv/bin/python -m controlplane_tenancy.startup
exec "$@"
