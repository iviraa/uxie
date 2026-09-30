"""Runtime configuration. The only module that reads os.environ."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from functools import lru_cache
from urllib.parse import unquote

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
    post_confidence: float | None = None  # below this, findings wait for a sandbox proof
    focuses: tuple[str, ...] | None = None  # which specialists run (None = all)


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
        focuses=tuple(os.environ["SPECIALISTS"].split(","))
        if os.environ.get("SPECIALISTS")
        else None,  # e.g. "logic,edges,contracts,behavior" to turn on the behavior reviewer
        post_confidence=float(os.environ["POST_CONFIDENCE"])
        if os.environ.get("POST_CONFIDENCE")
        else None,
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


@dataclass(frozen=True)
class OtelSettings:
    endpoint: str  # e.g. https://otlp-gateway-prod-us-east-2.grafana.net/otlp
    headers: dict[str, str]


def otel_settings() -> OtelSettings | None:
    """OTLP export target from a Secrets Manager secret (the auth header is a credential)."""
    arn = os.environ.get("OTEL_SECRET_ARN")
    if not arn:
        return None
    raw = boto3.client("secretsmanager").get_secret_value(SecretId=arn)["SecretString"]
    data = json.loads(raw)
    # same format as OTEL_EXPORTER_OTLP_HEADERS: comma-separated k=v, values URL-encoded
    headers = {
        k.strip(): unquote(v.strip())
        for k, v in (h.split("=", 1) for h in str(data.get("headers", "")).split(",") if "=" in h)
    }
    return OtelSettings(endpoint=str(data["endpoint"]).rstrip("/"), headers=headers)


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
