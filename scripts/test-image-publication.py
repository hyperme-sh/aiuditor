"""Exercise the workflow's actual registry-existence branch without a registry."""
import os
from pathlib import Path
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
source = (root / '.github/workflows/package.yml').read_text()
start = source.index('          if docker manifest inspect "$image"')
end = source.index('          docker tag "$local_image"', start)
block = source[start:end]
fake = 'docker() { printf "%s" "$LOOKUP_OUTPUT" >&2; return "$LOOKUP_CODE"; }\nimage=ghcr.io/example/runtime:test-fixture\n'
cases = [
    ('exists', 0, '', False),
    ('docker-missing', 1, 'no such manifest: ghcr.io/example/runtime:test-fixture\n', True),
    ('ghcr-missing', 1, 'manifest unknown\n', True),
    ('auth', 1, 'unauthorized: authentication required\n', False),
    ('network', 1, 'connection timed out\n', False),
    ('server', 1, '500 Internal Server Error\n', False),
    ('ambiguous', 1, 'manifest unknown\nconnection reset\n', False),
    ('empty', 1, '', False),
]
with tempfile.TemporaryDirectory() as directory:
    for name, code, output, allowed in cases:
        result = subprocess.run(['bash', '-euo', 'pipefail', '-c', fake + block],
            env=dict(os.environ, LOOKUP_OUTPUT=output, LOOKUP_CODE=str(code)),
            cwd=directory, capture_output=True, text=True)
        assert (result.returncode == 0) == allowed, name
print('Registry existence branch passed all 8 absence/error cases')
