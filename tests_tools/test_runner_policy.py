import importlib.util
from pathlib import Path
import pytest

spec = importlib.util.spec_from_file_location('runner_policy', Path(__file__).parents[1] / 'scripts/check-runner-policy.py')
policy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(policy)


def test_repository_runner_policy():
    policy.check(Path(__file__).parents[1])


@pytest.mark.parametrize('selector', [
    '[self-hosted, linux, x64, debrunner]', 'ubuntu-latest',
    "${{ github.event.repository.private && 'self-hosted' || 'ubuntu-24.04' }}",
    'self-hosted',
])
def test_unconditional_or_wrong_visibility_runners_are_refused(tmp_path, selector):
    directory = tmp_path / '.github/workflows'
    directory.mkdir(parents=True)
    (directory / 'ci.yml').write_text('jobs:\n  check:\n    runs-on: ' + selector + '\n')
    with pytest.raises(ValueError, match='public runner'):
        policy.check(tmp_path)
