"""Runtime configuration. The only module that reads os.environ."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from functools import lru_cache

import boto3


@dataclass(frozen=True)
class ReviewSettings:
    model_id: str
    mode: str  # baseline | context
    max_tool_turns: int
    min_confidence: float
    max_comments: int
    max_changed_lines: int
    price_in_per_mtok: float
    price_out_per_mtok: float
    region: str | None = None


@dataclass(frozen=True)
class AppSecret:
    app_id: str
    private_key: str
    webhook_secret: str


def review_settings() -> ReviewSettings:
    return ReviewSettings(
        model_id=_require("BEDROCK_MODEL_ID"),
        mode=os.environ.get("REVIEW_MODE", "context"),
        max_tool_turns=int(os.environ.get("MAX_TOOL_TURNS", "8")),
        min_confidence=float(os.environ.get("MIN_CONFIDENCE", "0.5")),
        max_comments=int(os.environ.get("MAX_COMMENTS", "10")),
        max_changed_lines=int(os.environ.get("MAX_CHANGED_LINES", "1500")),
        price_in_per_mtok=float(os.environ.get("PRICE_IN_PER_MTOK", "1.0")),
        price_out_per_mtok=float(os.environ.get("PRICE_OUT_PER_MTOK", "5.0")),
        region=os.environ.get("AWS_REGION"),
    )


def queue_url() -> str:
    return _require("REVIEW_QUEUE_URL")


@dataclass(frozen=True)
class DispatcherSettings:
    queue_url: str
    reviews_table: str
    app_secret_arn: str
    prover_model_id: str
    jobs_root: str
    sandbox_image: str
    region: str | None


def dispatcher_settings() -> DispatcherSettings:
    return DispatcherSettings(
        queue_url=_require("VERIFY_QUEUE_URL"),
        reviews_table=_require("REVIEWS_TABLE"),
        app_secret_arn=_require("APP_SECRET_ARN"),
        prover_model_id=_require("PROVER_MODEL_ID"),
        jobs_root=os.environ.get("JOBS_ROOT", "/jobs"),
        sandbox_image=os.environ.get("SANDBOX_IMAGE", "uxie-sandbox:dev"),
        region=os.environ.get("AWS_REGION"),
    )


def verify_queue_url() -> str | None:
    return os.environ.get("VERIFY_QUEUE_URL") or None  # unset: reviews aren't verified


def reviews_table() -> str:
    return _require("REVIEWS_TABLE")


def lake_bucket() -> str:
    return _require("LAKE_BUCKET")


def app_secret_arn() -> str:
    return _require("APP_SECRET_ARN")


def workspace_root() -> str:
    return os.environ.get("WORKSPACE_ROOT", "/tmp/uxie")


@lru_cache(maxsize=4)
def app_secret(secret_arn: str) -> AppSecret:
    """GitHub App credentials from Secrets Manager. Cached per warm Lambda."""
    raw = boto3.client("secretsmanager").get_secret_value(SecretId=secret_arn)["SecretString"]
    data = json.loads(raw)
    return AppSecret(
        app_id=str(data["app_id"]),
        private_key=str(data["private_key"]),
        webhook_secret=str(data["webhook_secret"]),
    )


def _require(key: str) -> str:
    value = os.environ.get(key)
    if not value:
        raise RuntimeError(f"missing required env var: {key}")
    return value
