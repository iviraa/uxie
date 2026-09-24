"""Unified-diff parsing: hunks, commentable lines, and a numbered view for the model."""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field

_HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


@dataclass(frozen=True)
class DiffLine:
    kind: str  # "+", "-", " "
    text: str
    old_no: int | None
    new_no: int | None


@dataclass
class FilePatch:
    path: str
    status: str  # added | modified | removed | renamed
    lines: list[DiffLine] = field(default_factory=list)
    hunk_starts: list[int] = field(default_factory=list)  # indexes into lines

    @property
    def added(self) -> int:
        return sum(1 for ln in self.lines if ln.kind == "+")

    @property
    def removed(self) -> int:
        return sum(1 for ln in self.lines if ln.kind == "-")

    def commentable(self) -> set[int]:
        """New-file line numbers GitHub accepts a RIGHT-side review comment on."""
        return {ln.new_no for ln in self.lines if ln.new_no is not None}

    def added_lines(self) -> set[int]:
        return {ln.new_no for ln in self.lines if ln.kind == "+" and ln.new_no is not None}

    def render(self) -> str:
        """Numbered diff: new-file line numbers let the model cite exact lines."""
        out = [f"### {self.path} ({self.status})"]
        starts = set(self.hunk_starts)
        for i, ln in enumerate(self.lines):
            if i in starts and i:
                out.append("   ...")
            num = f"{ln.new_no:>5}" if ln.new_no is not None else "     "
            out.append(f"{num} {ln.kind} {ln.text}")
        return "\n".join(out)


def parse_patch(path: str, patch: str, status: str = "modified") -> FilePatch:
    fp = FilePatch(path=path, status=status)
    old_no = new_no = 0
    in_hunk = False
    for raw in patch.splitlines():
        m = _HUNK_RE.match(raw)
        if m:
            old_no, new_no = int(m.group(1)), int(m.group(3))
            fp.hunk_starts.append(len(fp.lines))
            in_hunk = True
            continue
        if not in_hunk or raw.startswith("\\"):  # "\ No newline at end of file"
            continue
        kind, text = (raw[0], raw[1:]) if raw else (" ", "")
        if kind == "+":
            fp.lines.append(DiffLine("+", text, None, new_no))
            new_no += 1
        elif kind == "-":
            fp.lines.append(DiffLine("-", text, old_no, None))
            old_no += 1
        else:
            fp.lines.append(DiffLine(" ", text, old_no, new_no))
            old_no += 1
            new_no += 1
    return fp


def make_patch(old: str, new: str, context: int = 3) -> str:
    """GitHub-style patch body (hunks only, no ---/+++ headers)."""
    diff = difflib.unified_diff(old.splitlines(), new.splitlines(), lineterm="", n=context)
    return "\n".join(line for line in diff if not line.startswith(("---", "+++")))
