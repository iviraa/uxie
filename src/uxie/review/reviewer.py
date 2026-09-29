"""The review agent loop: diff in, validated findings out."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from uxie.common.config import ReviewSettings
from uxie.diff.patch import FilePatch
from uxie.review import prompts, tools
from uxie.review.agent import Conversation
from uxie.review.findings import Finding, parse_findings, validate
from uxie.review.model import ChatModel, Usage
from uxie.review.workspace import Workspace


@dataclass
class PullRequest:
    title: str
    body: str
    files: list[FilePatch]


@dataclass
class ReviewResult:
    summary: str
    findings: list[Finding]
    stats: dict[str, int]
    usage: Usage
    cost_usd: float
    latency_s: float
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "summary": self.summary,
            "findings": [f.to_dict() for f in self.findings],
            "stats": self.stats,
            "usage": vars(self.usage),
            "cost_usd": round(self.cost_usd, 6),
            "latency_s": round(self.latency_s, 2),
            "tool_calls": self.tool_calls,
            "error": self.error,
        }


def review(
    pr: PullRequest,
    ws: Workspace,
    model: ChatModel,
    cfg: ReviewSettings,
    cache: bool = True,
) -> ReviewResult:
    t0 = time.monotonic()
    mode = cfg.mode
    specs = (
        [tools.SUBMIT_REVIEW] if mode == "baseline" else [*tools.CONTEXT_TOOLS, tools.SUBMIT_REVIEW]
    )
    rendered = "\n\n".join(f.render() for f in pr.files)

    conv = Conversation(
        model,
        prompts.system_prompt(mode),
        prompts.user_prompt(pr.title, pr.body, rendered),
        specs,
        "submit_review",
        ws,
        prompts.FORCE_SUBMIT,
        cache,
    )
    max_turns = 0 if mode == "baseline" else cfg.max_tool_turns
    submitted = conv.run(max_turns, force=mode == "baseline")
    usage, calls = conv.usage, conv.calls

    cost = usage.cost_usd(cfg.price_in_per_mtok, cfg.price_out_per_mtok)
    latency = time.monotonic() - t0
    if submitted is None:
        return ReviewResult("", [], {"raw": 0}, usage, cost, latency, calls, "no submit_review")

    raw = submitted.get("findings") or []
    kept, stats = validate(
        parse_findings(raw if isinstance(raw, list) else []),
        pr.files,
        cfg.min_confidence,
        cfg.max_comments,
    )
    return ReviewResult(
        str(submitted.get("summary") or ""), kept, stats, usage, cost, latency, calls
    )
