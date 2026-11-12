#!/usr/bin/env bash
# Inspect a fresh root commit without changing repository refs or uploading files.
set -euo pipefail
cd "$(dirname "$0")/.."
scratch=$(mktemp -d)
trap 'rm -rf "$scratch"' EXIT
version=8.30.1
name="gitleaks_${version}_linux_x64.tar.gz"
curl --fail --silent --show-error --location \
  "https://github.com/gitleaks/gitleaks/releases/download/v${version}/${name}" -o "$scratch/$name"
printf '%s  %s\n' 551f6fc83ea457d62a0d98237cbad105af8d557003051f41f3e7ca7b3f2470eb "$scratch/$name" | sha256sum --check
mkdir "$scratch/tool" "$scratch/source"
tar -xzf "$scratch/$name" -C "$scratch/tool" gitleaks
git archive HEAD | tar -xf - -C "$scratch/source"
for required in AGENTS.md docs/release-policy.md LICENSE NOTICE; do test -s "$scratch/source/$required"; done
if rg -n 'self-hosted|debrunner|shared-local|upload-artifact|download-artifact' "$scratch/source/.github/workflows"; then
  echo 'Public CI must use hosted runners and container-only distribution' >&2
  exit 1
fi
"$scratch/tool/gitleaks" dir "$scratch/source" --redact --no-banner --max-archive-depth 3 --ignore-gitleaks-allow
# Fresh history exists only in this disposable directory. Never reset or push refs.
git -C "$scratch/source" init --quiet --initial-branch=main
git -C "$scratch/source" add .
git -C "$scratch/source" -c user.name='AIuditor maintainers' -c user.email='maintainers@example.invalid' \
  -c core.hooksPath=/dev/null commit --quiet -m 'Initial public source snapshot'
test "$(git -C "$scratch/source" rev-list --count HEAD)" = 1
"$scratch/tool/gitleaks" git "$scratch/source" --redact --no-banner --max-archive-depth 3 --ignore-gitleaks-allow
printf 'Public source and single-commit history scan passed for %s\n' "$(git rev-parse HEAD)"
