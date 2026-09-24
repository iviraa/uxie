"""The review agent loop: diff in, validated findings out."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from critic.common.config import ReviewSettings
from critic.diff.patch import FilePatch
from critic.review import prompts, tools
from critic.review.findings import Finding, parse_findings, validate
from critic.review.model import ChatModel, Usage
from critic.review.workspace import Workspace

CACHE_POINT = {"cachePoint": {"type": "default"}}


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
    usage = Usage()
    mode = cfg.mode
    specs = (
        [tools.SUBMIT_REVIEW] if mode == "baseline" else [*tools.CONTEXT_TOOLS, tools.SUBMIT_REVIEW]
    )
    rendered = "\n\n".join(f.render() for f in pr.files)

    system: list[dict[str, Any]] = [{"text": prompts.system_prompt(mode)}]
    first: list[dict[str, Any]] = [{"text": prompts.user_prompt(pr.title, pr.body, rendered)}]
    if cache:
        system.append(CACHE_POINT)
        first.append(CACHE_POINT)
    messages: list[dict[str, Any]] = [{"role": "user", "content": first}]

    calls: list[dict[str, Any]] = []
    submitted: dict[str, Any] | None = None
    force = mode == "baseline"
    max_turns = 0 if mode == "baseline" else cfg.max_tool_turns

    for turn in range(max_turns + 2):  # +1 for forced submit, +1 for a nudge after end_turn
        if turn >= max_turns:
            force = True
        choice = {"tool": {"name": "submit_review"}} if force else {"auto": {}}
        resp = model.converse(system, messages, specs, choice)
        usage.add(resp.usage)
        messages.append({"role": "assistant", "content": resp.content})

        uses = resp.tool_uses()
        submitted = next((u["input"] for u in uses if u["name"] == "submit_review"), None)
        if submitted is not None:
            break
        if not uses:  # model answered in prose; make it use the tool
            messages.append({"role": "user", "content": [{"text": prompts.FORCE_SUBMIT}]})
            force = True
            continue

        results: list[dict[str, Any]] = []
        for u in uses:
            out = tools.dispatch(ws, u["name"], u.get("input") or {})
            calls.append({"name": u["name"], "input": u.get("input"), "chars": len(out)})
            results.append(
                {"toolResult": {"toolUseId": u["toolUseId"], "content": [{"text": out}]}}
            )
        if turn + 1 >= max_turns:
            results.append({"text": prompts.FORCE_SUBMIT})
        messages.append({"role": "user", "content": results})

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
