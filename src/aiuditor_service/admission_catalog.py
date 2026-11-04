"""Admission catalog id helper. Shared by promote hop and tests."""

from __future__ import annotations

import re

_CATALOG_ID_RE = re.compile(r"[^a-z0-9._-]+")


def admission_catalog_id(*, intended_class: str, file_hash: str) -> str:
    digest = (file_hash or "").strip().lower().removeprefix("sha256:")
    raw = f"upload-{intended_class}-{digest[:16]}".lower()
    cleaned = _CATALOG_ID_RE.sub("-", raw).strip("-._")
    return (cleaned or "upload-artifact")[:64]
