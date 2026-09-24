"""Read-only view of a repo snapshot on disk. Backs the agent's tools."""

from __future__ import annotations

import os
import re
from pathlib import Path

MAX_READ_LINES = 400
MAX_SEARCH_HITS = 40
MAX_FILE_BYTES = 1_000_000
SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", "dist", "build", "target"}


class Workspace:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()

    def _resolve(self, rel: str) -> Path:
        p = (self.root / rel.lstrip("/")).resolve()
        if p != self.root and self.root not in p.parents:  # block ../ escapes
            raise ValueError(f"path escapes workspace: {rel}")
        return p

    def read_text(self, rel: str) -> str | None:
        p = self._resolve(rel)
        if not p.is_file() or p.stat().st_size > MAX_FILE_BYTES:
            return None
        try:
            return p.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return None

    def read_file(self, rel: str, start: int = 1, end: int | None = None) -> str:
        text = self.read_text(rel)
        if text is None:
            return f"error: {rel} not found, binary, or too large"
        lines = text.splitlines()
        start = max(1, start)
        end = min(len(lines), end or start + MAX_READ_LINES - 1, start + MAX_READ_LINES - 1)
        body = "\n".join(f"{i:>5}  {lines[i - 1]}" for i in range(start, end + 1))
        more = f"\n... ({len(lines) - end} more lines)" if end < len(lines) else ""
        return f"{rel} lines {start}-{end} of {len(lines)}\n{body}{more}"

    def list_dir(self, rel: str = "") -> str:
        p = self._resolve(rel)
        if not p.is_dir():
            return f"error: {rel or '.'} is not a directory"
        entries = sorted(
            (e.name + "/" if e.is_dir() else e.name) for e in p.iterdir() if e.name not in SKIP_DIRS
        )
        return "\n".join(entries[:300]) or "(empty)"

    def search(self, pattern: str, glob: str | None = None) -> str:
        """Regex search across text files, like a tiny git grep."""
        try:
            rx = re.compile(pattern)
        except re.error as e:
            return f"error: bad regex: {e}"
        hits: list[str] = []
        for dirpath, dirnames, filenames in os.walk(self.root):
            dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
            for name in sorted(filenames):
                full = Path(dirpath) / name
                rel = str(full.relative_to(self.root))
                if glob and not full.match(glob):
                    continue
                text = self.read_text(rel)
                if text is None:
                    continue
                for i, line in enumerate(text.splitlines(), 1):
                    if rx.search(line):
                        hits.append(f"{rel}:{i}: {line.strip()[:200]}")
                        if len(hits) >= MAX_SEARCH_HITS:
                            return "\n".join(hits) + "\n... (truncated)"
        return "\n".join(hits) or "no matches"
