"""Finding model plus validation against the diff (the model's output is untrusted)."""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass
from typing import Any

from uxie.diff.patch import FilePatch
from uxie.review.tools import CATEGORIES, SEVERITIES

SNAP_DISTANCE = 3
_SEV_RANK = {"high": 0, "medium": 1, "low": 2}


@dataclass
class Finding:
    path: str
    line: int
    severity: str
    category: str
    title: str
    explanation: str
    confidence: float
    suggestion: str = ""
    snapped_from: int | None = None  # original line if we moved it onto the diff
    fingerprint: str = ""  # stable across pushes; used to avoid reposting

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def fingerprint(f: Finding, fp: FilePatch) -> str:
    """Hash of file, category and the flagged line's text (line numbers shift between pushes)."""
    text = next((ln.text for ln in fp.lines if ln.new_no == f.line), "")
    norm = " ".join(text.split())
    key = f"{f.path}|{f.category}|{norm}"
    return hashlib.sha1(key.encode()).hexdigest()[:12]


def parse_findings(raw: list[dict[str, Any]]) -> list[Finding]:
    out: list[Finding] = []
    for r in raw:
        try:
            out.append(
                Finding(
                    path=str(r["path"]).lstrip("/"),
                    line=int(r["line"]),
                    severity=r["severity"] if r.get("severity") in SEVERITIES else "medium",
                    category=r["category"] if r.get("category") in CATEGORIES else "bug",
                    title=str(r["title"]).strip()[:200],
                    explanation=str(r["explanation"]).strip()[:3000],
                    confidence=max(0.0, min(1.0, float(r.get("confidence", 0.5)))),
                    suggestion=str(r.get("suggestion") or "").strip()[:2000],
                )
            )
        except (KeyError, ValueError, TypeError):
            continue
    return out


def validate(
    findings: list[Finding],
    files: list[FilePatch],
    min_confidence: float,
    max_comments: int,
) -> tuple[list[Finding], dict[str, int]]:
    """Drop findings that GitHub can't anchor or that are low-signal. Returns stats too."""
    by_path = {f.path: f for f in files}
    stats = {"raw": len(findings), "bad_path": 0, "off_diff": 0, "low_conf": 0, "dupe": 0}
    kept: list[Finding] = []
    seen: set[tuple[str, int]] = set()
    for f in findings:
        fp = by_path.get(f.path)
        if fp is None:
            stats["bad_path"] += 1
            continue
        ok = fp.commentable()
        if f.line not in ok:
            near = [n for n in ok if abs(n - f.line) <= SNAP_DISTANCE]
            if not near:
                stats["off_diff"] += 1
                continue
            f.snapped_from, f.line = f.line, min(near, key=lambda n: abs(n - f.line))
        if f.confidence < min_confidence:
            stats["low_conf"] += 1
            continue
        if (f.path, f.line) in seen:
            stats["dupe"] += 1
            continue
        seen.add((f.path, f.line))
        f.fingerprint = fingerprint(f, fp)
        kept.append(f)
    kept.sort(key=lambda f: (_SEV_RANK[f.severity], -f.confidence))
    stats["capped"] = max(0, len(kept) - max_comments)
    return kept[:max_comments], stats
