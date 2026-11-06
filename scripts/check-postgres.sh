#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
: "${TEST_DATABASE_URL:?Set a disposable administrative PostgreSQL database URL}"
run_options=()
case "${1:-}" in
  '') ;;
  --fixtures)
    python3 scripts/verify_dependency_fixtures.py "$PWD" >/dev/null
    .venv/bin/python scripts/verify_preparation_environment.py "$PWD"
    run_options=(--no-sync)
    ;;
  *) echo 'Usage: bash scripts/check-postgres.sh [--fixtures]' >&2; exit 2 ;;
esac
if (( $# > 1 )); then echo 'Unexpected arguments' >&2; exit 2; fi
scratch=$(mktemp -d)
trap 'rm -rf "$scratch"' EXIT
export PYTHONPATH="$PWD/tests:$PWD/integration"
uv run --frozen "${run_options[@]}" pytest -q -p conftest integration --junitxml="$scratch/postgres.xml"
uv run --frozen "${run_options[@]}" python - "$scratch/postgres.xml" <<'PY'
import sys
from xml.etree import ElementTree as ET
root=ET.parse(sys.argv[1]).getroot()
assert list(root.iter('testcase')), 'No PostgreSQL cases ran'
assert not list(root.iter('skipped')), 'PostgreSQL qualification skipped cases'
PY
