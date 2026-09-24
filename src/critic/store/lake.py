"""Write review + finding rows into the github-logs data lake (Hive-partitioned JSON lines)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import boto3

PREFIX = "processed/critic"


def partition(when: datetime) -> str:
    return f"year={when:%Y}/month={when:%m}/day={when:%d}"


def review_rows(
    job: dict[str, Any], result: dict[str, Any], when: datetime
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    review_id = f"{job['repo']}#{job['pr_number']}#{job['head_sha']}"
    base = {"review_id": review_id, "repo": job["repo"], "pr_number": job["pr_number"]}
    review = {
        **base,
        "head_sha": job["head_sha"],
        "trigger": job["trigger"],
        "reviewed_at": when.isoformat(),
        "finding_count": len(result["findings"]),
        "raw_finding_count": result["stats"].get("raw", 0),
        "tool_calls": len(result["tool_calls"]),
        "input_tokens": result["usage"]["input_tokens"],
        "output_tokens": result["usage"]["output_tokens"],
        "cost_usd": result["cost_usd"],
        "latency_s": result["latency_s"],
        "model_id": result.get("model_id", ""),
        "mode": result.get("mode", ""),
    }
    findings = [
        {
            **base,
            **{k: f[k] for k in ("path", "line", "severity", "category", "title", "confidence")},
        }
        for f in result["findings"]
    ]
    return review, findings


class Lake:
    def __init__(self, bucket: str) -> None:
        self._bucket = bucket
        self._s3 = boto3.client("s3")

    def write_review(self, job: dict[str, Any], result: dict[str, Any]) -> None:
        now = datetime.now(UTC)
        review, findings = review_rows(job, result, now)
        name = review["review_id"].replace("/", "_").replace("#", "_")
        self._put(f"{PREFIX}/reviews/{partition(now)}/{name}.json", [review])
        if findings:
            self._put(f"{PREFIX}/findings/{partition(now)}/{name}.json", findings)

    def _put(self, key: str, rows: list[dict[str, Any]]) -> None:
        body = "\n".join(json.dumps(r, default=str) for r in rows) + "\n"
        self._s3.put_object(
            Bucket=self._bucket, Key=key, Body=body.encode(), ContentType="application/json"
        )
