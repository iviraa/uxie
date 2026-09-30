"""Per-(PR, head sha) review records. The conditional claim is what makes retries safe."""

from __future__ import annotations

import time
from decimal import Decimal
from typing import Any

import boto3
from botocore.exceptions import ClientError

LEASE_SECONDS = 900  # matches the worker timeout


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
        return self._claim(self.verify_key(repo, pr, sha, fingerprint), force=False)

    def finish_verify(
        self, repo: str, pr: int, sha: str, fingerprint: str, status: str, **fields: Any
    ) -> None:
        self._finish(self.verify_key(repo, pr, sha, fingerprint), status, **fields)

    def _claim(self, key: dict[str, str], force: bool) -> bool:
        now = int(time.time())
        # a fresh sha, a crashed worker's expired lease, or a failed attempt can be claimed;
        # force (/review command) may also redo a finished review
        cond = "attribute_not_exists(PK) OR lease_until < :now OR #s = :failed"
        if force:
            cond += " OR #s IN (:done, :skipped)"
        values: dict[str, Any] = {":now": now, ":failed": "failed"}
        if force:
            values |= {":done": "done", ":skipped": "skipped"}
        try:
            self._table.put_item(
                Item={
                    **key,
                    "status": "in_progress",
                    "lease_until": now + LEASE_SECONDS,
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
