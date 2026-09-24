"""Live review of one GitHub PR: fetch, review, post, record."""

from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Any

import httpx

from uxie.common.config import ReviewSettings
from uxie.common.logger import get_logger, log
from uxie.diff import filters
from uxie.diff.patch import FilePatch, parse_patch
from uxie.github.client import GitHubClient
from uxie.github.webhook import ReviewJob
from uxie.review import render
from uxie.review.model import ChatModel
from uxie.review.reviewer import PullRequest, review
from uxie.review.workspace import Workspace
from uxie.store.lake import Lake
from uxie.store.reviews import ReviewStore

logger = get_logger(__name__)


def files_from_api(raw: list[dict[str, Any]]) -> list[FilePatch]:
    # GitHub omits "patch" for binary and very large files; those can't be reviewed inline
    return [
        parse_patch(f["filename"], f["patch"], f.get("status", "modified"))
        for f in raw
        if f.get("patch")
    ]


def run(
    job: ReviewJob,
    gh: GitHubClient,
    model: ChatModel,
    cfg: ReviewSettings,
    store: ReviewStore,
    lake: Lake | None,
    workdir: Path,
) -> str:
    """Returns a status string: done | skipped | stale | duplicate."""
    pr = gh.get_pr(job.repo, job.pr_number)
    head = pr["head"]["sha"]
    if job.head_sha and job.head_sha != head:
        log(logger, logging.INFO, "stale job; newer push will be reviewed", job=job.to_dict())
        return "stale"
    if pr.get("draft") or pr.get("state") != "open":
        return "skipped"

    force = job.trigger == "command"
    if not store.claim(job.repo, job.pr_number, head, force=force):
        log(logger, logging.INFO, "already reviewed or in progress", job=job.to_dict())
        return "duplicate"

    job_d = {**job.to_dict(), "head_sha": head}
    try:
        files = filters.select(files_from_api(gh.list_pr_files(job.repo, job.pr_number)))
        size = filters.changed_lines(files)
        if not files or size > cfg.max_changed_lines:
            reason = "no reviewable files" if not files else f"diff too large ({size} lines)"
            store.finish(job.repo, job.pr_number, head, "skipped", reason=reason)
            if force:
                gh.create_review(
                    job.repo, job.pr_number, head, render.review_body(f"Skipped: {reason}.", []), []
                )
            return "skipped"

        shutil.rmtree(workdir, ignore_errors=True)
        root = gh.download_snapshot(job.repo, head, workdir)
        result = review(
            PullRequest(pr.get("title") or "", pr.get("body") or "", files),
            Workspace(root),
            model,
            cfg,
        )
        if result.error:
            raise RuntimeError(result.error)

        review_id = _post(gh, job, head, result.summary, result.findings)
        out = result.to_dict() | {"model_id": cfg.model_id, "mode": cfg.mode}
        store.finish(
            job.repo,
            job.pr_number,
            head,
            "done",
            review_id=review_id,
            finding_count=len(result.findings),
            cost_usd=result.cost_usd,
            latency_s=result.latency_s,
            findings=[f.to_dict() for f in result.findings],
            model_id=cfg.model_id,
        )
        if lake:
            lake.write_review(job_d, out)
        log(
            logger,
            logging.INFO,
            "review posted",
            job=job_d,
            findings=len(result.findings),
            cost_usd=result.cost_usd,
            latency_s=result.latency_s,
            stats=result.stats,
        )
        return "done"
    except Exception as e:
        store.finish(job.repo, job.pr_number, head, "failed", error=str(e)[:500])
        raise
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def _post(gh: GitHubClient, job: ReviewJob, head: str, summary: str, findings: list) -> int:
    try:
        resp = gh.create_review(
            job.repo,
            job.pr_number,
            head,
            render.review_body(summary, findings),
            render.inline_comments(findings),
        )
    except httpx.HTTPStatusError as e:
        if e.response.status_code != 422 or not findings:
            raise
        # GitHub rejected an anchor; fall back to listing findings in the body
        listed = "\n".join(f"- `{f.path}:{f.line}` {render.comment_body(f)}" for f in findings)
        resp = gh.create_review(
            job.repo, job.pr_number, head, render.review_body(summary, findings, listed), []
        )
    return int(resp["id"])
