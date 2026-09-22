"""Hugging Face (+ allowlisted GGUF publisher) metadata crawl. No weight download."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol

import httpx

# Small allowlist of GGUF publishers on Hugging Face. Expand deliberately.
GGUF_PUBLISHER_ALLOWLIST: frozenset[str] = frozenset(
    {
        "bartowski",
        "thebloke",
        "quantfactory",
        "ggml-org",
        "lmstudio-community",
        "fyberlabs",
    }
)

_QUANT_RE = re.compile(
    r"(?i)(Q[2-8](?:_[A-Z0-9]+)?|IQ[1-4]_[A-Z0-9]+|F16|F32|BF16)"
)
_SHA_RE = re.compile(r"(?i)^sha256:([0-9a-f]{64})$")


@dataclass(frozen=True)
class ArtifactMeta:
    """One GGUF (or similar) file pin. Metadata only — never the weights."""

    repo: str
    revision: str
    file_name: str
    size_bytes: int
    license: str | None
    quant: str | None
    file_hash: str | None
    publisher: str

    @property
    def source_ref(self) -> str:
        return f"{self.repo}@{self.revision}/{self.file_name}"

    @property
    def catalog_id(self) -> str:
        stem = self.file_name.rsplit(".", 1)[0]
        raw = f"hf-{self.publisher}-{stem}".lower()
        cleaned = re.sub(r"[^a-z0-9._-]+", "-", raw).strip("-._")
        return (cleaned or "hf-artifact")[:64]


class MetadataSource(Protocol):
    async def list_artifacts(self) -> list[ArtifactMeta]: ...


def _publisher(repo: str) -> str:
    return (repo.split("/", 1)[0] or "").strip().lower()


def _quant_from_name(file_name: str) -> str | None:
    match = _QUANT_RE.search(file_name)
    return match.group(1).upper() if match else None


def _hash_from_lfs(oid: object) -> str | None:
    text = str(oid or "").strip()
    if not text:
        return None
    if _SHA_RE.match(text):
        return text.lower()
    if re.fullmatch(r"[0-9a-fA-F]{64}", text):
        return f"sha256:{text.lower()}"
    return None


@dataclass
class StaticMetadataSource:
    """Test / offline seed. Never hits the network."""

    artifacts: list[ArtifactMeta]

    async def list_artifacts(self) -> list[ArtifactMeta]:
        return list(self.artifacts)


class HuggingFaceMetadataSource:
    """List GGUF files from allowlisted HF repos. Metadata API only — no blob GET."""

    def __init__(
        self,
        repos: tuple[str, ...] | list[str],
        *,
        revision: str = "main",
        timeout_s: float = 20.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._repos = tuple(repos)
        self._revision = revision
        self._timeout = timeout_s
        self._client = client

    async def list_artifacts(self) -> list[ArtifactMeta]:
        out: list[ArtifactMeta] = []
        owns = self._client is None
        client = self._client or httpx.AsyncClient(timeout=self._timeout)
        try:
            for repo in self._repos:
                publisher = _publisher(repo)
                if publisher not in GGUF_PUBLISHER_ALLOWLIST:
                    continue
                license_name = await self._license(client, repo)
                tree = await self._tree(client, repo)
                for entry in tree:
                    if not isinstance(entry, dict):
                        continue
                    path = str(entry.get("path") or "")
                    if not path.lower().endswith(".gguf"):
                        continue
                    if entry.get("type") not in (None, "file"):
                        continue
                    size = int(entry.get("size") or 0)
                    lfs = entry.get("lfs") if isinstance(entry.get("lfs"), dict) else {}
                    file_hash = _hash_from_lfs(lfs.get("oid") or entry.get("oid"))
                    out.append(
                        ArtifactMeta(
                            repo=repo,
                            revision=self._revision,
                            file_name=path,
                            size_bytes=size,
                            license=license_name,
                            quant=_quant_from_name(path),
                            file_hash=file_hash,
                            publisher=publisher,
                        )
                    )
        finally:
            if owns:
                await client.aclose()
        return out

    async def _license(self, client: httpx.AsyncClient, repo: str) -> str | None:
        response = await client.get(f"https://huggingface.co/api/models/{repo}")
        if response.status_code >= 400:
            return None
        try:
            body = response.json()
        except ValueError:
            return None
        if not isinstance(body, dict):
            return None
        card = body.get("cardData") if isinstance(body.get("cardData"), dict) else {}
        lic = card.get("license") or body.get("license")
        return str(lic).strip().lower()[:64] if lic else None

    async def _tree(self, client: httpx.AsyncClient, repo: str) -> list:
        response = await client.get(
            f"https://huggingface.co/api/models/{repo}/tree/{self._revision}",
            params={"recursive": "true"},
        )
        if response.status_code >= 400:
            return []
        try:
            body = response.json()
        except ValueError:
            return []
        return body if isinstance(body, list) else []


def default_seed_repos() -> tuple[str, ...]:
    """Conservative starter set. Operators expand via AIUDITOR_CRAWL_REPOS."""

    return (
        "bartowski/Llama-3.1-8B-Instruct-GGUF",
        "bartowski/Qwen2.5-32B-Instruct-GGUF",
    )
