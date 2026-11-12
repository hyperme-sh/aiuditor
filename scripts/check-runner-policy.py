"""Refuse unconditional private runners or a changed hosted-only public runner policy."""
from pathlib import Path
import re
import sys

SELECTOR = 'ubuntu-24.04'


def check(root):
    selectors = []
    for workflow in (Path(root) / '.github/workflows').glob('*.yml'):
        text = workflow.read_text()
        for line in text.splitlines():
            if re.match(r'\s*runs-on:', line):
                value = line.split(':', 1)[1].strip()
                if value != SELECTOR:
                    raise ValueError(f'{workflow.name}: public runner must be ubuntu-24.04')
                selectors.append(value)
        if re.search(r'^\s+uses:.*\.github/workflows/', text, re.M):
            raise ValueError(f'{workflow.name}: reusable runner policy needs explicit review')
    if not selectors:
        raise ValueError('No runner selectors found')


if __name__ == '__main__':
    check(sys.argv[1] if len(sys.argv) > 1 else Path(__file__).resolve().parents[1])
    print('Public hosted-only runner selection verified')
