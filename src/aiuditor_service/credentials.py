"""Hash-only credential refs. Never persist or return the raw secret."""

from __future__ import annotations

import hashlib


def credential_ref(raw: str) -> str:
    """SHA-256 hex of the full secret. Not bcrypt. Comparable locally."""

    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
