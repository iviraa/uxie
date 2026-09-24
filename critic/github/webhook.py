"""Webhook signature check and event routing (pure functions, no AWS)."""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import asdict, dataclass
from typing import Any

PR_ACTIONS = {"opened", "reopened", "synchronize", "ready_for_review"}
TRUSTED_ASSOCIATIONS = {"OWNER", "MEMBER", "COLLABORATOR"}
COMMAND = "/review"


@dataclass(frozen=True)
class ReviewJob:
    repo: str
    pr_number: int
    installation_id: int
    head_sha: str | None  # None when triggered by a comment; worker resolves it
    trigger: str
    comment_id: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def group_id(self) -> str:
        return f"{self.repo}#{self.pr_number}"


def verify_signature(secret: str, body: bytes, header: str | None) -> bool:
    if not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header.removeprefix("sha256="))


def route(event: str, payload: dict[str, Any]) -> ReviewJob | None:
    """Map a webhook to a review job, or None if we should ignore it."""
    inst = (payload.get("installation") or {}).get("id")
    repo = (payload.get("repository") or {}).get("full_name")
    if not inst or not repo:
        return None

    if event == "pull_request":
        pr = payload.get("pull_request") or {}
        if payload.get("action") not in PR_ACTIONS or pr.get("draft") or pr.get("state") != "open":
            return None
        return ReviewJob(repo, int(pr["number"]), int(inst), pr["head"]["sha"], payload["action"])

    if event == "issue_comment":
        issue = payload.get("issue") or {}
        comment = payload.get("comment") or {}
        if (
            payload.get("action") == "created"
            and "pull_request" in issue
            and issue.get("state") == "open"
            and comment.get("body", "").strip().lower().startswith(COMMAND)
            and comment.get("author_association") in TRUSTED_ASSOCIATIONS
        ):
            return ReviewJob(repo, int(issue["number"]), int(inst), None, "command", comment["id"])
    return None
