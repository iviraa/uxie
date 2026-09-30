"""SQS -> review one PR. Failures return to the queue and land in the DLQ after retries."""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

import boto3

from uxie import pipeline
from uxie.common import config, telemetry
from uxie.common.logger import get_logger, log
from uxie.github.app_auth import installation_token
from uxie.github.client import GitHubClient
from uxie.github.webhook import ReviewJob
from uxie.review.model import BedrockModel
from uxie.store.lake import Lake
from uxie.store.reviews import ReviewStore
from uxie.verify.request import VerifyRequest

logger = get_logger(__name__)
_otel = config.otel_settings()
telemetry.setup(
    "uxie-review-worker", _otel.endpoint if _otel else None, _otel.headers if _otel else None
)


def lambda_handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    failures: list[dict[str, str]] = []
    for record in event.get("Records", []):
        try:
            headers = {
                k: v["stringValue"]
                for k, v in record.get("messageAttributes", {}).items()
                if "stringValue" in v
            }
            sent = record.get("attributes", {}).get("SentTimestamp")
            wait_ms = int(time.time() * 1000) - int(sent) if sent else None
            with (
                telemetry.continued(headers),  # child of the webhook's trace
                telemetry.span("sqs.receive", **{"queue": "review", "queue.wait_ms": wait_ms}),
            ):
                _handle(ReviewJob(**json.loads(record["body"])))
        except Exception:
            logger.exception("review failed", extra={"extras": {"message_id": record["messageId"]}})
            failures.append({"itemIdentifier": record["messageId"]})
    telemetry.flush()
    return {"batchItemFailures": failures}


def _handle(job: ReviewJob) -> None:
    secret = config.app_secret(config.app_secret_arn())
    token = installation_token(secret.app_id, secret.private_key, job.installation_id)
    cfg = config.review_settings()
    verify_url = config.verify_queue_url()
    enqueue = None
    if verify_url:
        sqs = boto3.client("sqs")

        def enqueue(req: VerifyRequest) -> None:
            sqs.send_message(QueueUrl=verify_url, MessageBody=req.to_json())

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
            enqueue,
        )
    log(logger, logging.INFO, "job finished", status=status, job=job.to_dict())
