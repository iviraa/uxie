"""Conditional-write semantics against emulated DynamoDB (moto), not a hand-written fake."""

import time

import boto3
import pytest
from botocore.exceptions import ClientError
from moto import mock_aws

from uxie.store import reviews
from uxie.store.reviews import Busy, ReviewStore


@pytest.fixture
def store(monkeypatch):
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-2")
    with mock_aws():
        boto3.client("dynamodb").create_table(
            TableName="t",
            KeySchema=[
                {"AttributeName": "PK", "KeyType": "HASH"},
                {"AttributeName": "SK", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "PK", "AttributeType": "S"},
                {"AttributeName": "SK", "AttributeType": "S"},
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        yield ReviewStore("t")


def test_finished_review_is_not_reclaimed(store):
    assert store.claim("o/r", 1, "sha")
    store.finish("o/r", 1, "sha", "done")
    assert not store.claim("o/r", 1, "sha")  # redelivered webhook: no second review
    assert store.claim("o/r", 1, "sha", force=True)  # /review may redo it


def test_failed_review_can_be_retried(store):
    assert store.claim("o/r", 1, "sha")
    store.finish("o/r", 1, "sha", "failed")
    assert store.claim("o/r", 1, "sha")


def test_live_lease_blocks_and_expired_lease_is_reclaimed(store, monkeypatch):
    assert store.claim("o/r", 1, "sha")
    assert not store.claim("o/r", 1, "sha")  # someone is on it
    later = time.time() + reviews.LEASE_SECONDS + 1
    monkeypatch.setattr(reviews.time, "time", lambda: later)
    assert store.claim("o/r", 1, "sha")  # that worker crashed; take over


def test_verify_duplicate_vs_busy(store, monkeypatch):
    key = ("o/r", 1, "sha", "fp")
    assert store.claim_verify(*key)
    with pytest.raises(Busy):
        store.claim_verify(*key)  # live lease: leave the message for later
    store.extend_verify(*key)
    store.finish_verify(*key, "done", verdict="confirmed")
    assert store.claim_verify(*key) is False  # done: safe to drop the redelivery
    with pytest.raises(ClientError):
        store.extend_verify(*key)  # heartbeats can't revive a finished job


def test_verify_crash_recovery_after_short_lease(store, monkeypatch):
    key = ("o/r", 1, "sha", "fp")
    assert store.claim_verify(*key)
    later = time.time() + reviews.VERIFY_LEASE_SECONDS + 1
    monkeypatch.setattr(reviews.time, "time", lambda: later)
    assert store.claim_verify(*key)  # dispatcher died without heartbeats; recover in ~2 min
