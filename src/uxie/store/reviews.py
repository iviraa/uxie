"""Per-(PR, head sha) review records. The conditional claim is what makes retries safe."""

from __future__ import annotations

import time
from decimal import Decimal
from typing import Any

import boto3
from botocore.exceptions import ClientError

LEASE_SECONDS = 900  # review worker: matches the Lambda timeout
VERIFY_LEASE_SECONDS = 100  # dispatcher: renewed by heartbeats; shorter than the 120s SQS
# visibility so a redelivered job always finds a dead worker's lease already expired


class Busy(Exception):
    """Another worker holds a live lease on this job; retry later instead of dropping it."""


class ReviewStore:
    def __init__(self, table: str) -> None:
        self._table = boto3.resource("dynamodb").Table(table)

    @staticmethod
    def key(repo: str, pr: int, sha: str) -> dict[str, str]:
        return {"PK": f"PR#{repo}#{pr}", "SK": f"SHA#{sha}"}

    @staticmethod
    def verify_key(repo: str, pr: int, sha: str, fingerprint: str) -> dict[str, str]:
        return {"PK": f"PR#{repo}#{pr}", "SK": f"VERIFY#{sha}#{fingerprint}"}

    def claim(self, repo: str, pr: int, sha: str, force: bool = False) -> bool:
        """True if this worker now owns the review of this sha."""
        return self._claim(self.key(repo, pr, sha), force)

    def claim_verify(self, repo: str, pr: int, sha: str, fingerprint: str) -> bool:
        """True if claimed; False if already finished; Busy if someone is working on it."""
        key = self.verify_key(repo, pr, sha, fingerprint)
        if self._claim(key, force=False, lease=VERIFY_LEASE_SECONDS):
            return True
        item = self._table.get_item(Key=key, ConsistentRead=True).get("Item") or {}
        if item.get("status") == "in_progress":
            raise Busy(f"{key['SK']} is being verified")
        return False

    def extend_verify(self, repo: str, pr: int, sha: str, fingerprint: str) -> None:
        """Heartbeat: keep our lease alive while the proof is still running."""
        now = int(time.time())
        self._table.update_item(
            Key=self.verify_key(repo, pr, sha, fingerprint),
            UpdateExpression="SET lease_until = :until",
            ConditionExpression="#s = :ip",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={":until": now + VERIFY_LEASE_SECONDS, ":ip": "in_progress"},
        )

    def finish_verify(
        self, repo: str, pr: int, sha: str, fingerprint: str, status: str, **fields: Any
    ) -> None:
        self._finish(self.verify_key(repo, pr, sha, fingerprint), status, **fields)

    def _claim(self, key: dict[str, str], force: bool, lease: int = LEASE_SECONDS) -> bool:
        now = int(time.time())
        # claimable: new, a crashed worker's expired lease, or a failed attempt. Finished jobs
        # are not (a redelivered message must not redo a review or post a second reply);
        # only force (/review) may redo a finished review.
        cond = "attribute_not_exists(PK) OR (#s = :ip AND lease_until < :now) OR #s = :failed"
        if force:
            cond += " OR #s IN (:done, :skipped)"
        values: dict[str, Any] = {":now": now, ":failed": "failed", ":ip": "in_progress"}
        if force:
            values |= {":done": "done", ":skipped": "skipped"}
        try:
            self._table.put_item(
                Item={
                    **key,
                    "status": "in_progress",
                    "lease_until": now + lease,
                    "started_at": now,
                },
                ConditionExpression=cond,
                ExpressionAttributeNames={"#s": "status"},
                ExpressionAttributeValues=values,
            )
            return True
        except ClientError as e:
            if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
                return False
            raise

    def finish(self, repo: str, pr: int, sha: str, status: str, **fields: Any) -> None:
        self._finish(self.key(repo, pr, sha), status, **fields)

    def _finish(self, key: dict[str, str], status: str, **fields: Any) -> None:
        names = {"#s": "status"}
        values: dict[str, Any] = {":s": status, ":zero": 0}
        sets = ["#s = :s", "lease_until = :zero"]
        for i, (k, v) in enumerate(fields.items()):
            names[f"#f{i}"] = k
            values[f":v{i}"] = _ddb(v)
            sets.append(f"#f{i} = :v{i}")
        self._table.update_item(
            Key=key,
            UpdateExpression="SET " + ", ".join(sets),
            ExpressionAttributeNames=names,
            ExpressionAttributeValues=values,
        )


def _ddb(v: Any) -> Any:
    if isinstance(v, float):
        return Decimal(str(v))
    if isinstance(v, dict):
        return {k: _ddb(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_ddb(x) for x in v]
    return v
