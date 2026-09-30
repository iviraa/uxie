"""Dispatcher process: long-poll the verify queue and prove one finding at a time.

Runs as a Deployment on the local k3s cluster; KEDA scales it from zero on queue depth.
"""

from __future__ import annotations

import logging
import signal
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import boto3

from uxie.common import config, telemetry
from uxie.common.logger import get_logger, log
from uxie.github.app_auth import installation_token
from uxie.github.client import GitHubClient
from uxie.review.model import BedrockModel
from uxie.sandbox.envs import Deps
from uxie.sandbox.k8s import K8sBuildRunner, K8sExecutor
from uxie.store.reviews import Busy, ReviewStore
from uxie.verify.dispatcher import Snapshots, handle
from uxie.verify.request import VerifyRequest

logger = get_logger(__name__)
VISIBILITY_S = 120  # short: a crashed pod's job comes back within ~2 minutes
HEARTBEAT_S = 45  # while a proof runs, keep extending the message and the lease


class _Heartbeat(threading.Thread):
    """Keeps a message invisible (and our DynamoDB lease alive) until the proof finishes."""

    def __init__(self, beat: Callable[[], None], every: float) -> None:
        super().__init__(daemon=True)
        self._beat, self._every, self._done = beat, every, threading.Event()

    def run(self) -> None:
        while not self._done.wait(self._every):
            try:
                self._beat()
            except Exception:
                logger.exception("heartbeat failed")  # the job may then be retried elsewhere

    def stop(self) -> None:
        self._done.set()
        self.join(timeout=5)


def serve(
    sqs: Any,
    queue_url: str,
    handle_one: Callable[[VerifyRequest], str],
    stop: threading.Event,
    extend_lease: Callable[[VerifyRequest], None] | None = None,
    heartbeat_s: float = HEARTBEAT_S,
) -> None:
    """Delete a message only after it's handled; failures reappear and later dead-letter."""
    while not stop.is_set():
        resp = sqs.receive_message(
            QueueUrl=queue_url,
            AttributeNames=["SentTimestamp"],
            MaxNumberOfMessages=1,
            WaitTimeSeconds=20,
            VisibilityTimeout=VISIBILITY_S,
        )
        for msg in resp.get("Messages", []):
            handle = msg["ReceiptHandle"]
            hb: _Heartbeat | None = None
            try:
                req = VerifyRequest.from_json(msg["Body"])

                def beat(req: VerifyRequest = req, handle: str = handle) -> None:
                    sqs.change_message_visibility(
                        QueueUrl=queue_url, ReceiptHandle=handle, VisibilityTimeout=VISIBILITY_S
                    )
                    if extend_lease:
                        extend_lease(req)

                hb = _Heartbeat(beat, heartbeat_s)
                hb.start()
                wait_ms = _wait_ms(msg.get("Attributes", {}))
                with (
                    telemetry.continued(req.trace),
                    telemetry.span("sqs.receive", **{"queue": "verify", "queue.wait_ms": wait_ms}),
                ):
                    status = handle_one(req)
                log(logger, logging.INFO, "job done", status=status, finding=req.fingerprint)
            except Busy:
                log(logger, logging.INFO, "job busy elsewhere; will retry", id=msg["MessageId"])
                continue  # don't delete: if that worker dies, this message comes back
            except Exception:
                logger.exception("verify failed", extra={"extras": {"id": msg["MessageId"]}})
                continue
            finally:
                if hb:
                    hb.stop()
            sqs.delete_message(QueueUrl=queue_url, ReceiptHandle=msg["ReceiptHandle"])


def _wait_ms(attrs: dict[str, str]) -> int | None:
    sent = attrs.get("SentTimestamp")
    return int(time.time() * 1000) - int(sent) if sent else None


def main() -> None:
    cfg = config.dispatcher_settings()
    otel = config.otel_settings()
    telemetry.setup(
        "uxie-dispatcher", otel.endpoint if otel else None, otel.headers if otel else None
    )
    secret = config.app_secret(cfg.app_secret_arn)
    jobs = Path(cfg.jobs_root)
    model = BedrockModel(cfg.prover_model_id, cfg.region)
    ex = K8sExecutor(jobs)  # each language brings its own sandbox image
    envs = Deps(jobs / "envs", runner=K8sBuildRunner(jobs))  # python, go, ...
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

    def extend_lease(req: VerifyRequest) -> None:
        store.extend_verify(req.repo, req.pr_number, req.head_sha, req.fingerprint)

    serve(
        boto3.client("sqs", region_name=cfg.region), cfg.queue_url, handle_one, stop, extend_lease
    )


if __name__ == "__main__":
    main()
