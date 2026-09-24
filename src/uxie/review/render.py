"""Markdown for the GitHub review body and inline comments."""

from __future__ import annotations

from typing import Any

from uxie.review.findings import Finding

_SEV_ICON = {"high": "🔴", "medium": "🟠", "low": "🟡"}
FOOTER = "<sub>reviewed by uxie</sub>"


def comment_body(f: Finding) -> str:
    parts = [f"{_SEV_ICON[f.severity]} **{f.title}** · `{f.category}`", "", f.explanation]
    if f.suggestion:
        parts += ["", f"**Suggestion:** {f.suggestion}"]
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
    return "\n".join([*parts, "", FOOTER])
