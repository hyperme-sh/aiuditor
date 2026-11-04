#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
: "${TEST_DATABASE_URL:?Set a disposable administrative PostgreSQL database URL}"
scratch=$(mktemp -d)
trap 'rm -rf "$scratch"' EXIT
export PYTHONPATH="$PWD/tests:$PWD/integration"
uv run --frozen pytest -q -p conftest integration --junitxml="$scratch/postgres.xml"
uv run --frozen python - "$scratch/postgres.xml" <<'PY'
import sys
from xml.etree import ElementTree as ET
root=ET.parse(sys.argv[1]).getroot()
assert list(root.iter('testcase')), 'No PostgreSQL cases ran'
assert not list(root.iter('skipped')), 'PostgreSQL qualification skipped cases'
PY
