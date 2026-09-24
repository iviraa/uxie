"""Decide which changed files are worth reviewing."""

from __future__ import annotations

import fnmatch

from critic.diff.patch import FilePatch

SKIP_GLOBS = (
    "*.lock",
    "*lock.json",
    "*.lockb",
    "go.sum",
    "Cargo.lock",
    "poetry.lock",
    "uv.lock",
    "*.min.js",
    "*.min.css",
    "*.map",
    "*.snap",
    "*.svg",
    "*.png",
    "*.jpg",
    "*.gif",
    "*.ico",
    "*.pdf",
    "*.ipynb",
    "dist/*",
    "build/*",
    "vendor/*",
    "node_modules/*",
    "*_pb2.py",
    "*.pb.go",
    "*.generated.*",
    "*/migrations/*.py",
)


def is_reviewable(fp: FilePatch) -> bool:
    if fp.status == "removed" or not fp.lines:
        return False
    return not any(fnmatch.fnmatch(fp.path, g) for g in SKIP_GLOBS)


def select(files: list[FilePatch]) -> list[FilePatch]:
    return [f for f in files if is_reviewable(f)]


def changed_lines(files: list[FilePatch]) -> int:
    return sum(f.added + f.removed for f in files)
