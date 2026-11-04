"""Class fit filter: weights + KV headroom must fit after OS reserve.

Rule of thumb from hypermesh-docs/models.md — not a vendor spec. Crawler
drops anything that cannot fit the class being soaked.
"""

from __future__ import annotations

from dataclasses import dataclass

# Unified memory on edge boxes. OS + agent + weights + KV share one pool.
KV_HEADROOM_GB = 1.5

# Conservative OS + agent reserve per public/lab class we actually seed.
OS_RESERVE_GB: dict[str, float] = {
    "nx-volume": 2.0,
    "agx-large": 4.0,
    "thor": 6.0,
}

CLASS_MEMORY_GB: dict[str, float] = {
    "nx-volume": 16.0,
    "agx-large": 64.0,
    "thor": 128.0,
}

GIB = 1024**3


@dataclass(frozen=True)
class FitResult:
    class_id: str
    fits: bool
    usable_gb: float
    needed_gb: float


def usable_memory_gb(class_id: str) -> float | None:
    memory = CLASS_MEMORY_GB.get(class_id)
    reserve = OS_RESERVE_GB.get(class_id)
    if memory is None or reserve is None:
        return None
    return max(0.0, memory - reserve)


def fits_class(class_id: str, size_bytes: int) -> FitResult | None:
    """True when file size + KV headroom fits usable RAM for the class."""

    usable = usable_memory_gb(class_id)
    if usable is None:
        return None
    if size_bytes < 0:
        size_bytes = 0
    needed = (size_bytes / GIB) + KV_HEADROOM_GB
    return FitResult(
        class_id=class_id,
        fits=needed <= usable + 1e-9,
        usable_gb=usable,
        needed_gb=needed,
    )


def classes_that_fit(size_bytes: int, *, class_ids: tuple[str, ...] | None = None) -> list[str]:
    ids = class_ids or tuple(CLASS_MEMORY_GB.keys())
    out: list[str] = []
    for class_id in ids:
        result = fits_class(class_id, size_bytes)
        if result is not None and result.fits:
            out.append(class_id)
    return out
