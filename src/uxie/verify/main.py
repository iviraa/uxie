"""Dispatcher process: long-poll the verify queue and prove one finding at a time.

Runs as a Deployment on the local k3s cluster; KEDA scales it from zero on queue depth.
"""

from __future__ import annotations

import logging
import signal
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

import boto3

from uxie.common import config, telemetry
from uxie.common.logger import get_logger, log
from uxie.github.app_auth import installation_token
from uxie.github.client import GitHubClient
from uxie.review.model import BedrockModel
from uxie.sandbox.envs import EnvBuilder
from uxie.sandbox.k8s import K8sBuildRunner, K8sExecutor
from uxie.store.reviews import ReviewStore
from uxie.verify.dispatcher import Snapshots, handle
from uxie.verify.request import VerifyRequest

logger = get_logger(__name__)
VISIBILITY_S = 1800  # matches the queue; a proof must finish before it reappears


def serve(
    sqs: Any, queue_url: str, handle_one: Callable[[VerifyRequest], str], stop: threading.Event
) -> None:
    """Delete a message only after it's handled; failures reappear and later dead-letter."""
    while not stop.is_set():
        resp = sqs.receive_message(
            QueueUrl=queue_url,
            MaxNumberOfMessages=1,
            WaitTimeSeconds=20,
            VisibilityTimeout=VISIBILITY_S,
        )
        for msg in resp.get("Messages", []):
            try:
                req = VerifyRequest.from_json(msg["Body"])
                status = handle_one(req)
                log(logger, logging.INFO, "job done", status=status, finding=req.fingerprint)
            except Exception:
                logger.exception("verify failed", extra={"extras": {"id": msg["MessageId"]}})
                continue
            sqs.delete_message(QueueUrl=queue_url, ReceiptHandle=msg["ReceiptHandle"])


def main() -> None:
    cfg = config.dispatcher_settings()
    otel = config.otel_settings()
    telemetry.setup(
        "uxie-dispatcher", otel.endpoint if otel else None, otel.headers if otel else None
    )
    secret = config.app_secret(cfg.app_secret_arn)
    jobs = Path(cfg.jobs_root)
    model = BedrockModel(cfg.prover_model_id, cfg.region)
    ex = K8sExecutor(jobs, image=cfg.sandbox_image)
    envs = EnvBuilder(jobs / "envs", runner=K8sBuildRunner(jobs))
    snaps = Snapshots(jobs / "snapshots")
    store = ReviewStore(cfg.reviews_table)

    def handle_one(req: VerifyRequest) -> str:
        token = installation_token(secret.app_id, secret.private_key, req.installation_id)
        with GitHubClient(token) as gh:
            return handle(req, gh, model, ex, envs, snaps, store, jobs / "work")

    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):  # finish the current job, then exit
        signal.signal(sig, lambda *_: stop.set())
    log(logger, logging.INFO, "dispatcher started", queue=cfg.queue_url)
    serve(boto3.client("sqs", region_name=cfg.region), cfg.queue_url, handle_one, stop)


if __name__ == "__main__":
    main()
