"""Run a command with credentials restricted to the lock's exact private repos."""
import base64
import os
from pathlib import Path
import subprocess
import sys
import tomllib
from urllib.parse import urlsplit

root = Path(__file__).resolve().parents[1]
env = os.environ.copy()
token = env.pop("DEPENDENCY_READ_TOKEN", "")
secret = Path("/run/secrets/dependency_read_token")
if not token and secret.exists():
    token = secret.read_text().strip()
if token:
    urls = set()
    for package in tomllib.loads((root / "uv.lock").read_text())["package"]:
        source = package.get("source", {}).get("git")
        if source:
            url = urlsplit(source)
            if url.scheme != "https" or url.netloc != "github.com" or not url.path.startswith("/hyperme-sh/"):
                raise SystemExit("Unapproved dependency Git origin")
            urls.add("https://github.com" + url.path)
    header = base64.b64encode(("x-access-token:" + token).encode()).decode()
    if env.get("GITHUB_ACTIONS"):
        print("::add-mask::" + header, flush=True)
    env["GIT_CONFIG_COUNT"] = str(len(urls))
    for i, url in enumerate(sorted(urls)):
        env[f"GIT_CONFIG_KEY_{i}"] = f"http.{url}.extraheader"
        env[f"GIT_CONFIG_VALUE_{i}"] = "AUTHORIZATION: basic " + header
raise SystemExit(subprocess.call(sys.argv[1:], cwd=root, env=env))
