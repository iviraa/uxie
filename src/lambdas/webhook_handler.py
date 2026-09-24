"""API Gateway -> verify signature -> enqueue. Must answer GitHub within 10s."""

from __future__ import annotations

import base64
import json
import logging
from typing import Any

import boto3

from critic.common import config
from critic.common.logger import get_logger, log
from critic.github.webhook import route, verify_signature

logger = get_logger(__name__)
_sqs = boto3.client("sqs")


def lambda_handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
    raw = event.get("body") or ""
    body = base64.b64decode(raw) if event.get("isBase64Encoded") else raw.encode()

    secret = config.app_secret(config.app_secret_arn()).webhook_secret
    if not verify_signature(secret, body, headers.get("x-hub-signature-256")):
        log(logger, logging.WARNING, "bad webhook signature")
        return _resp(401, "bad signature")

    gh_event = headers.get("x-github-event", "")
    delivery = headers.get("x-github-delivery", "")
    if gh_event == "ping":
        return _resp(200, "pong")

    job = route(gh_event, json.loads(body))
    if job is None:
        return _resp(202, "ignored")

    # FIFO: one message group per PR keeps reviews of the same PR in order;
    # the delivery id dedupes GitHub's webhook retries for 5 minutes
    _sqs.send_message(
        QueueUrl=config.queue_url(),
        MessageBody=json.dumps(job.to_dict()),
        MessageGroupId=job.group_id,
        MessageDeduplicationId=delivery or f"{job.group_id}#{job.head_sha}",
    )
    log(logger, logging.INFO, "queued review", job=job.to_dict(), delivery=delivery)
    return _resp(202, "queued")


def _resp(status: int, message: str) -> dict[str, Any]:
    return {"statusCode": status, "body": json.dumps({"message": message})}
