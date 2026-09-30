"""Verify worker: pull a finding off SQS, prove it in the sandbox, reply on the PR comment."""

from __future__ import annotations

import logging
import shutil
import uuid
from pathlib import Path
from typing import Any, Protocol

from uxie.common.logger import get_logger, log
from uxie.diff import filters
from uxie.github.client import GitHubClient
from uxie.pipeline import files_from_api
from uxie.review.findings import Finding
from uxie.review.model import ChatModel
from uxie.review.reviewer import PullRequest
from uxie.review.workspace import Workspace
from uxie.sandbox.executor import Executor
from uxie.verify.prover import ProverSettings, prove
from uxie.verify.render import verdict_reply
from uxie.verify.request import VerifyRequest

logger = get_logger(__name__)


class VerifyStore(Protocol):
    def claim_verify(self, repo: str, pr: int, sha: str, fingerprint: str) -> bool: ...

    def finish_verify(
        self, repo: str, pr: int, sha: str, fingerprint: str, status: str, **fields: Any
    ) -> None: ...


class EnvSource(Protocol):
    def ensure(self, repo_root: Path, exclude_newer: str | None = None) -> Path | None: ...


class Snapshots:
    """Extracted repo tarballs under root/<repo>/<sha>, reused across findings of one PR."""

    def __init__(self, root: Path, keep: int = 40) -> None:
        self.root, self.keep = root, keep

    def get(self, gh: GitHubClient, repo: str, sha: str) -> Path:
        dest = self.root / repo.replace("/", "__") / sha
        done = dest / ".ok"
        if done.exists():
            return Path(done.read_text())
        shutil.rmtree(dest, ignore_errors=True)
        snap = gh.download_snapshot(repo, sha, dest)
        done.write_text(str(snap))
        self._prune()
        return snap

    def _prune(self) -> None:
        marks = sorted(self.root.glob("*/*/.ok"), key=lambda p: p.stat().st_mtime)
        for old in marks[: max(0, len(marks) - self.keep)]:
            shutil.rmtree(old.parent, ignore_errors=True)


def handle(
    req: VerifyRequest,
    gh: GitHubClient,
    model: ChatModel,
    ex: Executor,
    envs: EnvSource | None,
    snaps: Snapshots,
    store: VerifyStore,
    work_root: Path,
    cfg: ProverSettings | None = None,
) -> str:
    """Returns the verdict, or duplicate | stale."""
    key = (req.repo, req.pr_number, req.head_sha, req.fingerprint)
    if not store.claim_verify(*key):
        return "duplicate"
    work = work_root / f"verify-{uuid.uuid4().hex[:10]}"
    try:
        pr = gh.get_pr(req.repo, req.pr_number)
        if pr["head"]["sha"] != req.head_sha:  # a newer push will be reviewed and verified
            store.finish_verify(*key, "stale")
            return "stale"
        files = filters.select(files_from_api(gh.list_pr_files(req.repo, req.pr_number)))
        head_root = snaps.get(gh, req.repo, req.head_sha)
        base_root = snaps.get(gh, req.repo, req.base_sha)
        env = envs.ensure(head_root) if envs else None
        proof = prove(
            Finding(**req.finding),
            PullRequest(req.title, req.body, files),
            Workspace(head_root),
            base_root,
            head_root,
            model,
            ex,
            work,
            cfg,
            env,
        )
        reply = verdict_reply(proof)
        if reply and req.comment_id:
            gh.reply_to_comment(req.repo, req.pr_number, req.comment_id, reply)
        store.finish_verify(
            *key,
            "done",
            verdict=proof.verdict,
            attempts=proof.attempts,
            test_code=proof.test_code,
            note=proof.note,
            replied=bool(reply and req.comment_id),
        )
        log(
            logger,
            logging.INFO,
            "verified",
            repo=req.repo,
            pr=req.pr_number,
            finding=req.fingerprint,
            verdict=proof.verdict,
            attempts=proof.attempts,
        )
        return proof.verdict
    except Exception as e:
        store.finish_verify(*key, "failed", error=str(e)[:500])
        raise
    finally:
        shutil.rmtree(work, ignore_errors=True)
