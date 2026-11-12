"""Exercise moving-container publication conditions without a registry."""
import os
from pathlib import Path
import subprocess
import tempfile
root = Path(__file__).resolve().parents[1]
source = (root / '.github/workflows/package.yml').read_text()
# Execute the actual channel step: stale queued builds cannot publish, and a
# source/config mismatch cannot advance even when its commit is current main.
start = source.index('          current=$(gh api')
end = len(source)
channel_block = source[start:end]
channel_fake = r'''
gh() { printf '%s\n' "$LATEST_HEAD"; }
docker() {
  case "$1 $2" in
    'image inspect')
      if [[ "$*" == *org.opencontainers.image.revision* ]]; then printf '%s\n' "$IMAGE_REVISION";
      else printf '%s\n' "$IMAGE_ID"; fi ;;
    'push '*) printf 'push\n' >> "$CALLS" ;;
    *) : ;;
  esac
}
'''
with tempfile.TemporaryDirectory() as directory:
    for name, latest, revision, image_id, success, pushed in [
        ('superseded', 'b'*40, 'a'*40, 'sha256:'+'c'*64, True, False),
        ('current-qualified', 'a'*40, 'a'*40, 'sha256:'+'c'*64, True, True),
        ('wrong-revision', 'a'*40, 'b'*40, 'sha256:'+'c'*64, False, False),
        ('wrong-config', 'a'*40, 'a'*40, 'sha256:'+'d'*64, False, False),
    ]:
        calls = Path(directory)/('calls-'+name)
        result = subprocess.run(['bash','-euo','pipefail','-c',channel_fake+channel_block],
            env=dict(os.environ,LATEST_HEAD=latest,IMAGE_REVISION=revision,IMAGE_ID=image_id,
                     GITHUB_SHA='a'*40,GITHUB_REPOSITORY='example/plane',QUALIFIED_REF='plane:ci-1-1',
                     QUALIFIED_ID='sha256:'+'c'*64,RUNNER_TEMP=directory,GH_TOKEN='fixture-only',
                     GITHUB_ACTOR='fixture',GITHUB_STEP_SUMMARY=str(Path(directory)/'summary'),CALLS=str(calls)),
            cwd=directory,capture_output=True,text=True)
        assert (result.returncode==0)==success,(name,result.stderr)
        assert calls.exists()==pushed,name
print('Moving TEST channel passed current/stale/source/config cases')
