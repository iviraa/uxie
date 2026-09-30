"""Which findings the sandbox can verify. Everything else is reviewed but not proven."""

from __future__ import annotations

from pathlib import PurePosixPath

VERIFIABLE_SUFFIXES = {".py"}


def verifiable(path: str) -> bool:
    return PurePosixPath(path).suffix in VERIFIABLE_SUFFIXES
