#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
[[ "${GITHUB_ACTIONS:-}" == true && "${GITHUB_EVENT_NAME:-}" == schedule && "${GITHUB_REF:-}" == refs/heads/main && "${NIGHTLY_SUITE:-}" == aiuditor-installed ]] || {
  echo 'AIuditor PostgreSQL tests require the central scheduled main batch' >&2
  exit 2
}
: "${TEST_EVIDENCE_DIR:?Central nightly report directory is required}"
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
mkdir -p "$TEST_EVIDENCE_DIR"
export PYTHONPATH="$PWD/tests:$PWD/integration"
uv run --frozen "${run_options[@]}" pytest -q -p conftest integration --junitxml="$TEST_EVIDENCE_DIR/aiuditor-postgres.xml"
uv run --frozen "${run_options[@]}" python - "$TEST_EVIDENCE_DIR/aiuditor-postgres.xml" <<'PY'
import sys
from xml.etree import ElementTree as ET
root=ET.parse(sys.argv[1]).getroot()
assert list(root.iter('testcase')), 'No PostgreSQL cases ran'
assert not list(root.iter('skipped')), 'PostgreSQL qualification skipped cases'
PY
