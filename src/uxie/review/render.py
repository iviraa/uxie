"""Markdown for the GitHub review body and inline comments."""

from __future__ import annotations

import re
from typing import Any

from uxie.review.findings import Finding

_SEV_ICON = {"high": "🔴", "medium": "🟠", "low": "🟡"}
FOOTER = "<sub>reviewed by uxie</sub>"
REVIEW_MARK = "<!-- uxie-review -->"
_FP_RE = re.compile(r"<!-- uxie:([0-9a-f]{12}) -->")


def fingerprints_in(texts: list[str]) -> set[str]:
    return {m for t in texts for m in _FP_RE.findall(t)}


def is_own_review(body: str) -> bool:
    return REVIEW_MARK in body


def comment_body(f: Finding) -> str:
    parts = [f"{_SEV_ICON[f.severity]} **{f.title}** · `{f.category}`", "", f.explanation]
    if f.suggestion:
        parts += ["", f"**Suggestion:** {f.suggestion}"]
    if f.fingerprint:
        parts.append(f"<!-- uxie:{f.fingerprint} -->")
    return "\n".join(parts)


def inline_comments(findings: list[Finding]) -> list[dict[str, Any]]:
    return [
        {"path": f.path, "line": f.line, "side": "RIGHT", "body": comment_body(f)} for f in findings
    ]


def review_body(summary: str, findings: list[Finding], note: str = "") -> str:
    head = (
        f"**uxie review:** {len(findings)} finding(s)"
        if findings
        else ("**uxie review:** no defects found")
    )
    parts = [head, "", summary.strip()]
    if note:
        parts += ["", note]
    return "\n".join([*parts, "", FOOTER, REVIEW_MARK])
