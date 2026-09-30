"""Live review of one GitHub PR: fetch, review, post, record."""

from __future__ import annotations

import logging
import shutil
from collections.abc import Callable
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
from uxie.review.findings import Finding
from uxie.review.model import ChatModel
from uxie.review.reviewer import PullRequest, review
from uxie.review.workspace import Workspace
from uxie.store.lake import Lake
from uxie.store.reviews import ReviewStore
from uxie.verify.request import VerifyRequest

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
    enqueue: Callable[[VerifyRequest], None] | None = None,
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

        # re-reviews only post what's new; an explicit /review always gets a reply
        own = [
            r.get("body") or ""
            for r in gh.list_reviews(job.repo, job.pr_number)
            if render.is_own_review(r.get("body") or "")
        ]
        comments = [c.get("body") or "" for c in gh.list_review_comments(job.repo, job.pr_number)]
        seen = render.fingerprints_in(comments + own)  # bodies hold fingerprints after a 422
        fresh = [f for f in result.findings if f.fingerprint not in seen]
        repeats = len(result.findings) - len(fresh)
        reviewed_before = bool(own)
        review_id = 0
        if fresh or force or not reviewed_before:
            note = f"{repeats} earlier finding(s) still apply." if repeats else ""
            review_id = _post(gh, job, head, result.summary, fresh, note)
            if enqueue and review_id and fresh:
                _enqueue_verifies(gh, job, pr, head, fresh, review_id, enqueue)
        out = result.to_dict() | {"model_id": cfg.model_id, "mode": cfg.mode}
        store.finish(
            job.repo,
            job.pr_number,
            head,
            "done",
            review_id=review_id,
            finding_count=len(result.findings),
            new_finding_count=len(fresh),
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


def _enqueue_verifies(
    gh: GitHubClient,
    job: ReviewJob,
    pr: dict[str, Any],
    head: str,
    findings: list[Finding],
    review_id: int,
    enqueue: Callable[[VerifyRequest], None],
) -> None:
    """One verify job per posted finding, pointing at its inline comment for the reply."""
    posted = gh.list_comments_for_review(job.repo, job.pr_number, review_id)
    by_fp = {fp: c["id"] for c in posted for fp in render.fingerprints_in([c.get("body") or ""])}
    for f in findings:
        enqueue(
            VerifyRequest(
                repo=job.repo,
                pr_number=job.pr_number,
                installation_id=job.installation_id,
                base_sha=pr["base"]["sha"],
                head_sha=head,
                title=pr.get("title") or "",
                body=pr.get("body") or "",
                finding=f.to_dict(),
                comment_id=by_fp.get(f.fingerprint),
            )
        )


def _post(
    gh: GitHubClient, job: ReviewJob, head: str, summary: str, findings: list, note: str = ""
) -> int:
    try:
        resp = gh.create_review(
            job.repo,
            job.pr_number,
            head,
            render.review_body(summary, findings, note),
            render.inline_comments(findings),
        )
    except httpx.HTTPStatusError as e:
        if e.response.status_code != 422 or not findings:
            raise
        # GitHub rejected an anchor; fall back to listing findings in the body
        listed = "\n".join(f"- `{f.path}:{f.line}` {render.comment_body(f)}" for f in findings)
        listed = f"{note}\n\n{listed}" if note else listed
        resp = gh.create_review(
            job.repo, job.pr_number, head, render.review_body(summary, findings, listed), []
        )
    return int(resp["id"])
