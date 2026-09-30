"""The review agent loop: diff in, validated findings out."""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
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


def _specialists(
    pr: PullRequest, ws: Workspace, model: ChatModel, cfg: ReviewSettings, cache: bool, t0: float
) -> ReviewResult:
    """Focused single-call reviewers in parallel; findings merged and deduplicated."""
    user = prompts.user_prompt(pr.title, pr.body, "\n\n".join(f.render() for f in pr.files))

    def one(focus: str) -> tuple[str, dict[str, Any] | None, Usage]:
        conv = Conversation(
            model,
            prompts.system_prompt("baseline", focus),
            user,
            [tools.SUBMIT_REVIEW],
            "submit_review",
            ws,
            prompts.FORCE_SUBMIT,
            cache,
        )
        return focus, conv.run(0, force=True), conv.usage

    with ThreadPoolExecutor(len(prompts.SPECIALISTS)) as pool:
        results = list(pool.map(one, prompts.SPECIALISTS))

    usage, raw, summary, stats = Usage(), [], "", {}
    for focus, sub, u in results:
        for k in (
            "input_tokens",
            "output_tokens",
            "cache_read_tokens",
            "cache_write_tokens",
            "calls",
        ):
            setattr(usage, k, getattr(usage, k) + getattr(u, k))
        found = (sub or {}).get("findings") or []
        stats[f"raw_{focus}"] = len(found) if isinstance(found, list) else 0
        raw += found if isinstance(found, list) else []
        summary = summary or str((sub or {}).get("summary") or "")
    # most confident first, so dedupe keeps the strongest version of a shared finding
    parsed = sorted(parse_findings(raw), key=lambda f: -f.confidence)
    kept, vstats = validate(parsed, pr.files, cfg.min_confidence, cfg.max_comments)
    cost = usage.cost_usd(cfg.price_in_per_mtok, cfg.price_out_per_mtok)
    err = None if any(sub is not None for _, sub, _ in results) else "no submit_review"
    return ReviewResult(summary, kept, vstats | stats, usage, cost, time.monotonic() - t0, [], err)


def review(
    pr: PullRequest,
    ws: Workspace,
    model: ChatModel,
    cfg: ReviewSettings,
    cache: bool = True,
) -> ReviewResult:
    t0 = time.monotonic()
    mode = cfg.mode
    if mode == "specialists":
        return _specialists(pr, ws, model, cfg, cache, t0)
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
