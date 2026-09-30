from pathlib import Path

import pytest

from tests.conftest import PATCH, FakeModel, tool_turn
from uxie.sandbox.spec import RunOutcome
from uxie.verify.dispatcher import Snapshots, VerifyRequest, handle
from uxie.verify.prover import ProverSettings

TEST = "from a import f\ndef test_f():\n    assert f([1, 2]) == 1\n"
FINDING = {
    "path": "a.py",
    "line": 4,
    "severity": "high",
    "category": "bug",
    "title": "t",
    "explanation": "e",
    "confidence": 0.9,
    "fingerprint": "fp1",
}


class FakeGH:
    def __init__(self, head="h1"):
        self.head, self.replies, self.downloads = head, [], []

    def get_pr(self, repo, n):
        return {"head": {"sha": self.head}}

    def list_pr_files(self, repo, n):
        return [{"filename": "a.py", "status": "modified", "patch": PATCH}]

    def download_snapshot(self, repo, sha, dest: Path):
        self.downloads.append(sha)
        root = dest / f"o-r-{sha}"
        root.mkdir(parents=True)
        (root / "a.py").write_text("def f(xs):\n    return xs[-1]\n")
        return root

    def reply_to_comment(self, repo, n, comment_id, body):
        self.replies.append((comment_id, body))


class FakeStore:
    def __init__(self, claimable=True):
        self.claimable, self.finished = claimable, []

    def claim_verify(self, repo, pr, sha, fp):
        return self.claimable

    def finish_verify(self, repo, pr, sha, fp, status, **fields):
        self.finished.append((status, fields))


class FakeExec:
    def __init__(self, head="failed", base="passed"):
        self.out = {"head": head, "base": base}

    def run(self, src, test_path, timeout_s, env=None, target_file=None):
        return RunOutcome(self.out[src.name], 1, 0.1, "AssertionError: x", "", [4])


def _req(**kw):
    base = dict(
        repo="o/r",
        pr_number=1,
        installation_id=7,
        base_sha="b1",
        head_sha="h1",
        title="t",
        body="",
        finding=FINDING,
        comment_id=99,
    )
    return VerifyRequest(**(base | kw))


def _handle(tmp_path, gh, store, ex, req=None):
    model = FakeModel([tool_turn("submit_test", {"test_code": TEST, "rationale": "r"})])
    return handle(
        req or _req(),
        gh,
        model,
        ex,
        None,
        Snapshots(tmp_path / "snaps"),
        store,
        tmp_path / "work",
        ProverSettings(max_attempts=1, max_tool_turns=1),
    )


def test_confirmed_replies_under_comment(tmp_path):
    gh, store = FakeGH(), FakeStore()
    assert _handle(tmp_path, gh, store, FakeExec()) == "confirmed"
    ((cid, body),) = gh.replies
    assert cid == 99 and body.startswith("✅")
    assert store.finished[-1][0] == "done" and store.finished[-1][1]["verdict"] == "confirmed"
    assert not list((tmp_path / "work").glob("*"))  # scratch cleaned


def test_inconclusive_is_quiet(tmp_path):
    gh = FakeGH()
    assert (
        _handle(tmp_path, gh, FakeStore(), FakeExec(head="failed", base="failed")) == "inconclusive"
    )
    assert gh.replies == []


def test_duplicate_and_stale(tmp_path):
    assert _handle(tmp_path, FakeGH(), FakeStore(claimable=False), FakeExec()) == "duplicate"
    store = FakeStore()
    assert _handle(tmp_path, FakeGH(head="newer"), store, FakeExec()) == "stale"
    assert store.finished == [("stale", {})]


def test_failure_is_recorded_and_raised(tmp_path):
    class Boom(FakeGH):
        def list_pr_files(self, repo, n):
            raise RuntimeError("github down")

    store = FakeStore()
    with pytest.raises(RuntimeError):
        _handle(tmp_path, Boom(), store, FakeExec())
    assert store.finished[-1][0] == "failed"


def test_snapshots_are_cached_and_pruned(tmp_path):
    gh, snaps = FakeGH(), Snapshots(tmp_path / "s", keep=2)
    a = snaps.get(gh, "o/r", "s1")
    assert snaps.get(gh, "o/r", "s1") == a and gh.downloads == ["s1"]
    snaps.get(gh, "o/r", "s2")
    snaps.get(gh, "o/r", "s3")
    assert not (tmp_path / "s" / "o__r" / "s1").exists()  # oldest evicted


def test_request_roundtrip():
    r = _req()
    assert VerifyRequest.from_json(r.to_json()) == r and r.fingerprint == "fp1"
