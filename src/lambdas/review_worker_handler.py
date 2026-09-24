"""SQS -> review one PR. Failures return to the queue and land in the DLQ after retries."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from critic import pipeline
from critic.common import config
from critic.common.logger import get_logger, log
from critic.github.app_auth import installation_token
from critic.github.client import GitHubClient
from critic.github.webhook import ReviewJob
from critic.review.model import BedrockModel
from critic.store.lake import Lake
from critic.store.reviews import ReviewStore

logger = get_logger(__name__)


def lambda_handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    failures: list[dict[str, str]] = []
    for record in event.get("Records", []):
        try:
            _handle(ReviewJob(**json.loads(record["body"])))
        except Exception:
            logger.exception("review failed", extra={"extras": {"message_id": record["messageId"]}})
            failures.append({"itemIdentifier": record["messageId"]})
    return {"batchItemFailures": failures}


def _handle(job: ReviewJob) -> None:
    secret = config.app_secret(config.app_secret_arn())
    token = installation_token(secret.app_id, secret.private_key, job.installation_id)
    cfg = config.review_settings()
    with GitHubClient(token) as gh:
        if job.comment_id:
            gh.add_reaction(job.repo, job.comment_id, "eyes")  # ack the /review command
        status = pipeline.run(
            job,
            gh,
            BedrockModel(cfg.model_id, cfg.region),
            cfg,
            ReviewStore(config.reviews_table()),
            Lake(config.lake_bucket()),
            Path(config.workspace_root()) / f"{job.pr_number}",
        )
    log(logger, logging.INFO, "job finished", status=status, job=job.to_dict())
